"""判活阶梯（v4 B3）：投递前零成本确认岗位存活，死岗标 expired。

移植自 career-ops liveness-{core,api}.mjs 的设计（MIT），适配本仓库三层结构：

- **Tier1 provider API 判活**（零 token、零浏览器）：URL 命中带 check() 的
  JD provider（alibaba/tencent/meituan/greenhouse/lever/ashby/workday/baidu）
  → 官方详情接口直接回答在招/下线。业务下线码（E1005、jobStatus 001、
  content:null、404/410、Workday S22）= 确定性 expired。
- **Tier2 静态 HTML 判活**：无 check() 的 provider（zhiye/byteportal 等 SPA 站
  常见）或任意 URL → GET canonical URL（同域跳转限界）读硬关键词
  （中英双语保守词表，源自 career-ops HARD_EXPIRED_PATTERNS 语义 + 中文对应）。
  JS 渲染站读不到正文 → uncertain（如实降级，本批次不含浏览器层）。

保守立场（career-ops 同款，写在最前）：**误判死严格劣于多一次 uncertain**——
expired 会把真岗从检索/匹配里埋掉。所以：
- 只有确定性信号才 expired：官方 404/410、ATS 业务下线码、静态页硬关键词；
- 403/429/5xx/超时/结构不明/正文不足 → uncertain；
- 判死只动 status='active' 的岗（不复活已 expired 的岗——复活是用户手动动作）。

状态落库走 job_postings.status（经与 JobService.set_status 同款 UPDATE），
凭据写 job_liveness_log（verdict/code/evidence/checked_at），复查调度以
checked_at 为准：没查过的最优先，uncertain 次之（站点可能恢复）。
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from jobhater.db.connection import transaction
from jobhater.services.enrich.engine import (
    HOST_MIN_INTERVAL_S,
    REQUEST_TIMEOUT_S,
    _ThrottledClient,
)
from jobhater.services.enrich.providers import (
    _UA,
    LivenessVerdict,
    _host_of,
    provider_for_url,
)

DEFAULT_LIMIT = 20
_MAX_HOPS = 3  # Tier2 同域跳转限界（与 BaiduProvider 同款语义）

# ---------- Tier2 静态页硬关键词（中英双语，保守词表） ----------
#
# 选词原则：只收「死页独有、活页正文不会出现」的短语。career-ops 的教训逐条吸收：
# - has been filled 必须排除 application/form 上下文与 filled out（活页文案
#   "once the application form has been filled…" 不是死信号）；
# - is closed 拒绝 closed-loop/closed-form 等复合词（真实 JD 写过 closed-loop control）；
# - 截止类模糊词（"投递截止"）一律不收——活页天天挂截止日期。
_ZH_EXPIRED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"职位已关闭|岗位已关闭|该职位已关闭"), "zh_position_closed"),
    (re.compile(r"职位已下线|岗位已下线|该职位已下线"), "zh_position_offline"),
    (re.compile(r"职位已失效|岗位已失效"), "zh_position_invalid"),
    (re.compile(r"已停止招聘|招聘已停止|招聘已结束|职位已结束"), "zh_recruit_ended"),
    (re.compile(r"职位不存在|岗位不存在"), "zh_position_not_found"),
]
_EN_EXPIRED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"no longer accepting applications", re.I), "no_longer_accepting"),
    (re.compile(r"(?:this )?(?:job|position|role) (?:is )?no longer (?:available|open)", re.I), "no_longer_open"),
    (re.compile(r"(?:this )?job (?:listing )?has expired", re.I), "job_expired"),
    (re.compile(r"job (?:listing )?not found", re.I), "job_not_found"),
    (re.compile(r"this (?:job|role|position)(?: listing)? is closed(?![-\w])", re.I), "is_closed"),
]
_FILLED = re.compile(r"has been filled\b(?!\s+out)", re.I)
_FILLED_SUBJECT = re.compile(r"\b(job|jobs|position|role|posting|opening|vacancy|requisition|req|listing)\b", re.I)


def _filled_dead(text: str) -> str | None:
    """「has been filled」带 career-ops 双 guard 的中文实现（re 不支持变宽后顾断言）。

    主语必须是岗位词且出现在前方 60 字符内；前方 25 字符出现 application/form
    则豁免（填的是表不是岗）。
    """
    for m in _FILLED.finditer(text):
        start = max(0, m.start() - 60)
        window = text[start:m.start()]
        if "application" in window[-25:].lower() or "form" in window[-25:].lower():
            continue
        if _FILLED_SUBJECT.search(window):
            return m.group(0)
    return None


def classify_body(text: str) -> LivenessVerdict:
    """Tier2 正文分类：硬关键词命中 → expired；否则 uncertain（正文不足也是 uncertain）。"""
    if not text or len(text.strip()) < 60:
        return LivenessVerdict("uncertain", code="insufficient_content",
                               evidence=f"正文仅 {len(text or '')} 字符（JS 渲染站或空页）")
    for pat, code in _ZH_EXPIRED:
        m = pat.search(text)
        if m:
            return LivenessVerdict("expired", code=code, evidence=_snippet(text, m.start()))
    for pat, code in _EN_EXPIRED:
        m = pat.search(text)
        if m:
            return LivenessVerdict("expired", code=code, evidence=_snippet(text, m.start()))
    hit = _filled_dead(text)
    if hit:
        return LivenessVerdict("expired", code="has_been_filled", evidence=hit)
    return LivenessVerdict("uncertain", code="no_death_marker",
                           evidence="页面可读但无下线标记（未证死，保守不定论）")


def _snippet(text: str, pos: int, width: int = 48) -> str:
    lo, hi = max(0, pos - width), min(len(text), pos + width)
    return text[lo:hi].replace("\n", " ").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LivenessService:
    """判活服务：probe 单岗 / run 批量 / stats 统计。注入 seam 与引擎同款。"""

    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.con = con
        self._transport = transport
        self._sleep = sleep

    # ---------- 对外主入口 ----------

    def probe(self, job_id: str) -> dict[str, object]:
        row = self.con.execute(
            "SELECT id, title, status, canonical_url AS url FROM job_postings WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "job 不存在"}
        if not (row["url"] or "").strip():
            return {"ok": False, "error": "该岗位没有官网链接，无法判活"}
        client = self._client()
        try:
            verdict = self._probe_url(row["url"], client)
        finally:
            client._inner.close()
        status_changed = self._apply(row, verdict)
        return {
            "ok": True,
            "job_id": job_id,
            "title": row["title"],
            "verdict": verdict.verdict,
            "code": verdict.code,
            "evidence": verdict.evidence[:200],
            "status_changed": status_changed,
        }

    def run(self, *, limit: int = DEFAULT_LIMIT) -> dict[str, object]:
        """批量判活：没查过的最优先，uncertain 次之（老 checked_at 先复查）。"""
        limit = max(1, limit)
        rows = self.con.execute(
            "SELECT j.id, j.title, j.status, j.canonical_url AS url "
            "FROM job_postings j LEFT JOIN job_liveness_log l ON l.job_id = j.id "
            "WHERE j.status = 'active' AND j.canonical_url IS NOT NULL "
            "AND COALESCE(l.verdict, '') <> 'expired' "
            "ORDER BY CASE WHEN l.job_id IS NULL THEN 0 ELSE 1 END, l.checked_at ASC, j.rowid DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        counts = {"active": 0, "expired": 0, "uncertain": 0}
        expired_jobs: list[dict[str, str]] = []
        client = self._client()
        try:
            for row in rows:
                verdict = self._probe_url(row["url"] or "", client)
                self._apply(row, verdict)
                counts[verdict.verdict] += 1
                if verdict.verdict == "expired":
                    expired_jobs.append(
                        {"job_id": row["id"], "title": row["title"], "code": verdict.code}
                    )
        finally:
            client._inner.close()
        return {"checked": len(rows), "counts": counts, "expired_jobs": expired_jobs}

    def stats(self) -> dict[str, object]:
        total = self.con.execute(
            "SELECT COUNT(*) n FROM job_postings WHERE status = 'active' AND canonical_url IS NOT NULL"
        ).fetchone()["n"]
        by_verdict = {
            r["verdict"]: r["n"]
            for r in self.con.execute(
                "SELECT verdict, COUNT(*) n FROM job_liveness_log GROUP BY verdict"
            )
        }
        never = total - sum(by_verdict.values())
        return {
            "active_with_url": total,
            "never_checked": never,
            "verdicts": by_verdict,
        }

    # ---------- 内部 ----------

    def _client(self) -> _ThrottledClient:
        inner = httpx.Client(
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": _UA},
        )
        return _ThrottledClient(inner, self._sleep, HOST_MIN_INTERVAL_S)

    def _probe_url(self, url: str, client: _ThrottledClient) -> LivenessVerdict:
        provider = provider_for_url(url)
        ref = provider.parse(url) if provider else None
        check = getattr(provider, "check", None)
        if provider is not None and ref is not None and callable(check):
            try:
                return check(ref, client)
            except Exception as e:  # provider check 任何意外都不得判死
                return LivenessVerdict("uncertain", code="check_error", evidence=str(e)[:120])
        # Tier1 不适用（无 provider / 无 check() / URL 不可解析）→ Tier2 静态页
        return self._probe_static(url, client)

    def _probe_static(self, url: str, client: _ThrottledClient) -> LivenessVerdict:
        """Tier2：GET canonical URL（同域跳转 ≤3 跳），404/410 或硬关键词 → expired。"""
        host = _host_of(url)
        if not host:
            return LivenessVerdict("uncertain", code="bad_url", evidence="URL 无 host")
        current = url
        try:
            for _ in range(_MAX_HOPS):
                resp = client.get(current, headers={"User-Agent": _UA})
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                resolved = urljoin(current, resp.headers.get("location", ""))
                if not resolved or _host_of(resolved) != host:
                    return LivenessVerdict("uncertain", code="redirect_off_host",
                                           evidence=f"跳转离开原域：{resolved[:80]}")
                current = resolved
            else:
                return LivenessVerdict("uncertain", code="too_many_hops", evidence="同域跳转超限")
        except Exception as e:  # 传输层任何故障都不判死（httpx.get 默认不抛状态码异常）
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if resp.status_code in (404, 410):
            return LivenessVerdict("expired", code="http_gone", evidence=f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            return LivenessVerdict("uncertain", code=f"http_{resp.status_code}",
                                   evidence=f"HTTP {resp.status_code}（不判死）")
        return classify_body(resp.text)

    def _apply(self, row: sqlite3.Row, verdict: LivenessVerdict) -> bool:
        """凭据落账本；expired 且岗位仍 active 时才写 status（不复活已死岗）。"""
        changed = False
        reason = f"liveness: {verdict.code} {verdict.evidence[:120]}".strip()
        with transaction(self.con):
            if verdict.verdict == "expired" and row["status"] == "active":
                self.con.execute(
                    "UPDATE job_postings SET status = 'expired', reject_reason = ? WHERE id = ?",
                    (reason, row["id"]),
                )
                changed = True
            self.con.execute(
                """
                INSERT INTO job_liveness_log(job_id, verdict, code, evidence, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    verdict = excluded.verdict,
                    code = excluded.code,
                    evidence = excluded.evidence,
                    checked_at = excluded.checked_at
                """,
                (row["id"], verdict.verdict, verdict.code, verdict.evidence[:300], _now_iso()),
            )
        return changed
