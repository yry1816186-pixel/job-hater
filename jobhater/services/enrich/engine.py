"""JD 补全引擎（v4.0 B1）：让 89% 无正文的存量岗变成全文库。

设计要点：
- **断点续跑的真相源在库内**：pending = active + 有 url + 无正文 + 未达重试上限
  （jd_enrich_log.attempts < 3）。进程随时可杀（验收含杀进程重跑），重启自动续。
- **节流在请求层**：_ThrottledClient 包装每个 httpx 请求——同一官方域任意两次
  请求（含 163 翻页、百度跳转链）间隔 ≥ HOST_MIN_INTERVAL_S；时间戳存模块级
  map，同一进程内跨 engine 实例生效（API 服务连续请求也受节流保护）。
  并发多进程运行仍可能双倍请求：本工具按单实例串行设计，账本 upsert 保证不损坏。
- **透明账本**：每次尝试写 jd_enrich_log（provider/status/attempts/last_error/
  api_title/match_mode）。no_match（163 标题对不上）与 failed（网络/接口）分开
  统计；fuzzy 命中单独可辨——串岗可审计、可回滚。
- 匹配重算保持显式动作（仓库既有决策）：run 结果带 match_stale 提示，由
  用户/前端触发 /api/match/run。
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from jobhater.db.connection import transaction
from jobhater.services.enrich.providers import (
    PROVIDERS,
    JdProvider,
    JdResult,
    host_filter_clause,
    provider_for_url,
)

HOST_MIN_INTERVAL_S = 1.2
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_S = 10.0
MAX_JD_CHARS = 20000  # 微信文章等长文截断，防单条撑爆 FTS

# 模块级节流时间戳：同一进程内跨 engine 实例共享（API 服务连续请求同样受保护）
_HOST_LAST_HIT: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _ThrottledClient:
    """httpx.Client 的节流包装：每个请求前按目标 host 强制最小间隔。

    providers 拿到的是本对象（结构上与 httpx.Client 同为 get/post），
    多请求 fetch（163 翻页/百度跳转）内部也受节流——对官方接口的礼貌承诺
    不因 fetch 内部循环而失效。
    """

    def __init__(self, inner: httpx.Client, sleep: Callable[[float], None],
                 min_interval_s: float) -> None:
        self._inner = inner
        self._sleep = sleep
        self._min_interval = min_interval_s

    def _pace(self, url: str) -> None:
        host = urlsplit(url).netloc
        if not host:
            return
        last = _HOST_LAST_HIT.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self._min_interval - (now - last)
            if wait > 0:
                self._sleep(wait)
        _HOST_LAST_HIT[host] = time.monotonic()

    def get(self, url: str, *, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None):
        self._pace(url)
        return self._inner.get(url, params=params, headers=headers)

    def post(self, url: str, *, json: dict[str, Any],
             headers: dict[str, str] | None = None):
        self._pace(url)
        return self._inner.post(url, json=json, headers=headers)


class EnrichmentEngine:
    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_s: float = HOST_MIN_INTERVAL_S,
    ) -> None:
        self.con = con
        self._transport = transport
        self._sleep = sleep
        self._min_interval = min_interval_s

    # ---------- 对外主入口 ----------

    def _client(self) -> _ThrottledClient:
        inner = httpx.Client(
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": "Mozilla/5.0"},  # 各 provider 按需覆盖
        )
        return _ThrottledClient(inner, self._sleep, self._min_interval)

    def run(self, *, limit: int = 50, provider_id: str | None = None) -> dict[str, Any]:
        """批量补全。返回逐 provider 统计；单次运行对每岗只尝试一次
        （重试靠下次 run，attempts 计数防无限刷）。"""
        limit = max(1, limit)
        providers = self._select_providers(provider_id)
        clause, args = host_filter_clause(providers)
        sql = (
            "SELECT j.id, j.title, j.employer_name, j.canonical_url AS url, "
            "       COALESCE(l.attempts, 0) AS attempts "
            "FROM job_postings j "
            "LEFT JOIN jd_enrich_log l ON l.job_id = j.id "
            f"WHERE j.canonical_url IS NOT NULL AND COALESCE(TRIM(j.description), '') = '' "
            f"AND j.status = 'active' AND COALESCE(l.status, '') <> 'ok' "
            f"AND COALESCE(l.attempts, 0) < ? AND ({clause}) "
            f"ORDER BY j.rowid DESC LIMIT ?"
        )
        rows = self.con.execute(sql, [MAX_ATTEMPTS, *args, limit * 6 + 30]).fetchall()

        stats: dict[str, dict[str, int]] = {}
        by_provider = self._dispatch(rows, providers)
        client = self._client()
        try:
            fetched = 0  # 真正发起网络请求的名额（limit 只约束网络，不约束记账）
            for provider, bucket in by_provider.items():
                s = stats.setdefault(
                    provider.id, {"ok": 0, "failed": 0, "no_match": 0, "jd_chars": 0, "fuzzy_ok": 0}
                )
                for row in bucket:
                    ref = provider.parse(row["url"] or "")
                    if ref is None:
                        # host 命中但 URL 形态不可解析（列表页/短链）：只记账不占网络名额，
                        # 防止大量新行把可直读的旧行挤出本次运行（超采 + 免费记账双保险）
                        self._record(row["id"], provider.id, "failed", "URL 形态不可解析（列表页/无岗位 ID）")
                        s["failed"] += 1
                        continue
                    if fetched >= limit:
                        continue
                    fetched += 1
                    outcome = self._enrich_one(provider, row, client, ref)
                    s[outcome] += 1
                    if outcome == "ok":
                        s["jd_chars"] += self._last_jd_chars
                        if self._last_match_mode == "fuzzy":
                            s["fuzzy_ok"] += 1
        finally:
            client._inner.close()
        return {
            "providers": stats,
            "total_ok": sum(v["ok"] for v in stats.values()),
            "total_failed": sum(v["failed"] for v in stats.values()),
            "total_no_match": sum(v["no_match"] for v in stats.values()),
            "total_fuzzy_ok": sum(v["fuzzy_ok"] for v in stats.values()),
        }

    def enrich_one(self, job_id: str) -> dict[str, Any]:
        """单岗位补全（详情页「拉取 JD 原文」入口）。受 attempts 上限约束。"""
        row = self.con.execute(
            "SELECT id, title, employer_name, canonical_url AS url FROM job_postings WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "job 不存在"}
        if not (row["url"] or "").strip():
            return {"ok": False, "error": "该岗位没有官网链接"}
        if self.con.execute(
            "SELECT 1 FROM job_postings WHERE id = ? AND COALESCE(TRIM(description), '') <> ''",
            (job_id,),
        ).fetchone():
            return {"ok": False, "error": "已有 JD 正文（无需补全）"}
        log_row = self.con.execute(
            "SELECT attempts, status FROM jd_enrich_log WHERE job_id = ?", (job_id,)
        ).fetchone()
        if log_row is not None and log_row["attempts"] >= MAX_ATTEMPTS and log_row["status"] != "ok":
            return {
                "ok": False,
                "error": f"已尝试 {log_row['attempts']} 次未成功（岗位可能已下线），"
                         "不再重试以免刷官方接口",
            }
        provider = provider_for_url(row["url"])
        if provider is None:
            return {"ok": False, "error": "该链接域名暂不支持自动补全"}
        client = self._client()
        try:
            outcome = self._enrich_one(provider, row, client)
        finally:
            client._inner.close()
        return {
            "ok": outcome == "ok",
            "provider": provider.id,
            "jd_chars": self._last_jd_chars if outcome == "ok" else 0,
            "match_mode": self._last_match_mode if outcome == "ok" else "",
            "error": "" if outcome == "ok" else f"补全未成功（{outcome}），详见 jd_enrich_log",
        }

    def stats(self) -> dict[str, Any]:
        """覆盖率与逐 provider 账本（验收指标的数据源）。"""
        active = self.con.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN COALESCE(TRIM(description),'') <> '' THEN 1 ELSE 0 END) d "
            "FROM job_postings WHERE status = 'active'"
        ).fetchone()
        by_provider = {}
        for r in self.con.execute(
            "SELECT provider, status, SUM(CASE WHEN match_mode='fuzzy' THEN 1 ELSE 0 END) fuzzy, "
            "COUNT(*) n FROM jd_enrich_log GROUP BY provider, status"
        ):
            p = by_provider.setdefault(r["provider"] or "(none)", {})
            p[f"{r['status'] or '(pending)'}"] = r["n"]
            if r["fuzzy"]:
                p["fuzzy_ok"] = r["fuzzy"]
        total = active["n"] or 0
        covered = active["d"] or 0
        return {
            "active_jobs": total,
            "with_jd": covered,
            "coverage_pct": round(covered * 100.0 / total, 1) if total else 0.0,
            "log": by_provider,
        }

    # ---------- 内部 ----------

    def _select_providers(self, provider_id: str | None) -> list[JdProvider]:
        if provider_id is None:
            return list(PROVIDERS)
        for p in PROVIDERS:
            if p.id == provider_id:
                return [p]
        raise ValueError(f"未知 provider: {provider_id}")

    def _dispatch(
        self, rows: list[sqlite3.Row], providers: list[JdProvider]
    ) -> dict[JdProvider, list[sqlite3.Row]]:
        out: dict[JdProvider, list[sqlite3.Row]] = {}
        for row in rows:
            p = provider_for_url(row["url"] or "")
            if p is None or p not in providers:
                continue  # host 预过滤命中但 URL 形态不可解析：留给未来 provider
            out.setdefault(p, []).append(row)
        return out

    _last_jd_chars = 0   # _enrich_one 的传出通道（避免每岗一个 tuple 返回）
    _last_match_mode = ""

    def _enrich_one(
        self, provider: JdProvider, row: sqlite3.Row, client: _ThrottledClient, ref=None
    ) -> str:
        url = row["url"]
        ref = ref if ref is not None else provider.parse(url)
        if ref is None:
            self._record(row["id"], provider.id, "failed", "URL 形态不可解析（列表页/无岗位 ID）")
            return "failed"
        try:
            res: JdResult = provider.fetch(ref, row["title"] or "", client)
        except httpx.HTTPStatusError as e:
            self._record(row["id"], provider.id, "failed", f"HTTP {e.response.status_code}")
            return "failed"
        except (httpx.RequestError, ValueError) as e:
            self._record(row["id"], provider.id, "failed", f"{type(e).__name__}: {str(e)[:120]}")
            return "failed"
        except Exception as e:  # provider 内部结构假设被破坏也要留痕，不静默
            self._record(row["id"], provider.id, "failed", f"意外错误 {type(e).__name__}: {str(e)[:120]}")
            return "failed"

        jd = (res.jd_text or "").strip()[:MAX_JD_CHARS]
        if res.ok and jd:
            now = _now_iso()
            with transaction(self.con):
                self.con.execute(
                    "UPDATE job_postings SET description = ?, fetched_at = ? WHERE id = ?",
                    (jd, now, row["id"]),
                )
                self._refresh_search_text(row["id"])
                self._record(
                    row["id"], provider.id, "ok", "",
                    enriched_at=now, jd_chars=len(jd),
                    api_title=res.api_title or "", match_mode=res.match_mode or "",
                )
            self._last_jd_chars = len(jd)
            self._last_match_mode = res.match_mode or ""
            return "ok"
        status = "no_match" if res.error == "no_match" else "failed"
        self._record(
            row["id"], provider.id, status, res.error[:200],
            api_title=res.api_title or "", match_mode=res.match_mode or "",
        )
        return status

    def _refresh_search_text(self, job_id: str) -> None:
        """补全正文后重建 FTS 索引文本。构造式与入库主路径 jobs._store 对齐：
        title + employer + description + keywords + city（此前缺后两者，
        会把补全行的 keywords/city 检索 token 挤掉——对抗评审 P2-2）。
        """
        row = self.con.execute(
            "SELECT title, employer_name, description, keywords_json, city "
            "FROM job_postings WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return
        import json as _json

        from jobhater import textproc as tp

        try:
            keywords = " ".join(_json.loads(row["keywords_json"] or "[]"))
        except (ValueError, TypeError):
            keywords = ""
        self.con.execute(
            "UPDATE job_postings SET search_text = ? WHERE id = ?",
            (
                tp.tokenize_for_fts(
                    f"{row['title']} {row['employer_name']} {row['description'] or ''} "
                    f"{keywords} {row['city'] or ''}"
                ),
                job_id,
            ),
        )

    def _record(
        self,
        job_id: str,
        provider_id: str,
        status: str,
        error: str = "",
        *,
        enriched_at: str = "",
        jd_chars: int = 0,
        api_title: str = "",
        match_mode: str = "",
    ) -> None:
        self.con.execute(
            """
            INSERT INTO jd_enrich_log(job_id, provider, status, attempts, last_error,
                                     enriched_at, jd_chars, api_title, match_mode)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                provider = excluded.provider,
                status = excluded.status,
                attempts = jd_enrich_log.attempts + 1,
                last_error = excluded.last_error,
                enriched_at = CASE WHEN excluded.status = 'ok' THEN excluded.enriched_at
                                   ELSE jd_enrich_log.enriched_at END,
                jd_chars = excluded.jd_chars,
                api_title = excluded.api_title,
                match_mode = excluded.match_mode
            """,
            (job_id, provider_id, status, error, enriched_at, jd_chars, api_title, match_mode),
        )
