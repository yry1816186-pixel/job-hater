"""B2 国际 ATS provider：Greenhouse / Lever / Ashby / Workday——外企在华岗的 JD 补全与判活。

端点知识来源：career-ops（MIT，github.com/career-ops-hq/career-ops）providers/
{greenhouse,lever,ashby,workday}.mjs 与 liveness-api.mjs（2026-11 活体验证），
按其规格移植为 Python，只移植**单岗位详情端点**（B2 补全场景；整板扫描分页机器
不在本批次）。

四源共同形态：canonical URL 自带 board/slug + 岗位 ID → 换算到各家**固定 API host**
的单岗端点直读。SSRF 防线（career-ops liveness-api 同构 + B1 既有立场）：
- API host 一律来自代码内固定常量，绝不取自输入 URL 的 host（Workday 的
  tenant host 是唯一例外，且经锚定正则校验）；
- URL 段入参前过严格字符集（SAFE_SEG：字母数字._- 与 %20，禁 .. 与 %2F）；
- 引擎层 follow_redirects=False。

判活 check()（Tier1，保守立场）：
- 200 = 在招（单岗端点 200 本身即在招证明，Ashby 除外——board 级端点须确认本岗仍在列）；
- 404/410 = 下线（Lever 除外——机密岗 API 404 而页面活着，career-ops 实测假阴性）；
- 其余（403/WAF/5xx/超时）一律 uncertain，绝不猜死。
"""
from __future__ import annotations

import html as html_mod
import re
from typing import Any
from urllib.parse import urlsplit

from jobhater.services.enrich.providers import (
    _UA,
    HttpLike,
    JdResult,
    JobRef,
    LivenessVerdict,
    _clean,
    _host_allowed,
    _host_of,
)

# career-ops SAFE_SEGMENT 同款：ID/slug 段允许的字符集（%20 是 Ashby 带空格 slug 的例外）。
# 单独拒绝 ..（点号在字符集内，靠字符集挡不住路径穿越——career-ops 注释明确此坑）
_SAFE_SEG = re.compile(r"^(?:[A-Za-z0-9._-]|%20)+$")


def _safe_seg(seg: str) -> bool:
    return bool(seg) and ".." not in seg and _SAFE_SEG.match(seg) is not None


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\xa0]+")


def _html_to_text(raw: Any) -> str:
    """HTML 正文 → 纯文本：两遍实体解码夹标签剥离（career-ops 双编码语义）。

    顺序是关键：Greenhouse content 是实体转义的标记（&lt;p&gt;…），必须先解码出
    真标签再剥离，否则残留 &lt;p&gt; 字面量；第二遍解码收尾 &amp;nbsp; 类嵌套实体。
    """
    if not raw:
        return ""
    text = html_mod.unescape(str(raw))   # 第一遍：&lt;p&gt; → <p>
    text = _TAG.sub("", text)            # 剥真实标签
    text = html_mod.unescape(text)       # 第二遍：&amp;rsquo; 等嵌套实体
    text = _WS.sub(" ", text)            # 行内空白折叠
    return text.strip()


# ---------- Greenhouse（job-boards[.eu].greenhouse.io/{board}/jobs/{id}） ----------


class GreenhouseProvider:
    id = "greenhouse"
    hosts = ("greenhouse.io",)
    _API = "https://boards-api.greenhouse.io/v1/boards"

    # /{board}/jobs/{id}：board 段 + 数字 ID（嵌入板 ?gh_jid= 等罕见形态不在 B2 范围）
    _PATH = re.compile(r"^/([a-z0-9][a-z0-9-]{0,80})/jobs/(\d{1,15})/?$", re.I)

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        # 只认官方板域名；boards-api 自身（API URL 被存成 canonical）也放行
        if not (_host_allowed(host, "job-boards.greenhouse.io")
                or _host_allowed(host, "job-boards.eu.greenhouse.io")
                or _host_allowed(host, "boards.greenhouse.io")
                or _host_allowed(host, "boards.eu.greenhouse.io")
                or _host_allowed(host, "boards-api.greenhouse.io")):
            return None
        m = self._PATH.match(urlsplit_path(url))
        if not m:
            return None
        board, job_id = m.group(1), m.group(2)
        if not _safe_seg(board):
            return None
        return JobRef(job_id, self._API, extra={"board": board})

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = http.get(
            f"{self._API}/{ref.extra['board']}/jobs/{ref.job_key}",
            headers={"User-Agent": _UA},
        )
        r.raise_for_status()
        payload = r.json()
        # content 是双重编码 HTML（实体转义的标记）——两遍解码后去标签
        jd = _html_to_text(html_mod.unescape(str(payload.get("content") or "")))
        if not jd:
            return JdResult(False, error="响应无 content 正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(payload.get("title")) or None)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        try:
            r = http.get(
                f"{self._API}/{ref.extra['board']}/jobs/{ref.job_key}",
                headers={"User-Agent": _UA},
            )
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code in (404, 410):
            return LivenessVerdict("expired", code="http_gone",
                                   evidence=f"HTTP {r.status_code}（单岗端点确定性下线信号）")
        if r.status_code == 200:
            return LivenessVerdict("active", code="greenhouse_detail_ok",
                                   evidence=f"title={r.json().get('title')!r}")
        return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                               evidence=f"HTTP {r.status_code}")


# ---------- Lever（jobs.[eu.]lever.co/{board}/{id}） ----------


class LeverProvider:
    id = "lever"
    hosts = ("lever.co",)

    _PATH = re.compile(r"^/([A-Za-z0-9._-]{1,80})/([0-9a-fA-F-]{8,64})/?$")

    def parse(self, url: str) -> JobRef | None:
        parts = urlsplit_parts(url)
        host = parts[0]
        eu = host == "jobs.eu.lever.co"
        if host != "jobs.lever.co" and not eu:
            return None
        m = self._PATH.match(parts[1])
        if not m:
            return None
        slug, job_id = m.group(1), m.group(2)
        if not (_safe_seg(slug) and _safe_seg(job_id)):
            return None
        api = "https://api.eu.lever.co" if eu else "https://api.lever.co"
        return JobRef(job_id, api, extra={"slug": slug})

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = http.get(
            f"{ref.origin}/v0/postings/{ref.extra['slug']}/{ref.job_key}",
            headers={"User-Agent": _UA},
        )
        r.raise_for_status()
        payload = r.json()
        chunks = [
            _clean(payload.get("descriptionPlain")) or _html_to_text(payload.get("description")),
        ]
        for sec in payload.get("lists") or []:
            head = _clean(sec.get("text"))
            body = _html_to_text(sec.get("content"))
            if head and body:
                chunks.append(f"{head}\n{body}")
            elif body:
                chunks.append(body)
        extra = _clean(payload.get("additionalPlain"))
        if extra:
            chunks.append(extra)
        jd = "\n\n".join(c for c in chunks if c)
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(payload.get("text")) or None)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """Lever API 404 ≠ 下线：机密/内部岗 API 404 而页面活着（career-ops 实测）→ uncertain。"""
        try:
            r = http.get(
                f"{ref.origin}/v0/postings/{ref.extra['slug']}/{ref.job_key}",
                headers={"User-Agent": _UA},
            )
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code == 200:
            return LivenessVerdict("active", code="lever_detail_ok",
                                   evidence=f"text={r.json().get('text')!r}")
        if r.status_code in (404, 410):
            return LivenessVerdict("uncertain", code="lever_404_not_authoritative",
                                   evidence="Lever API 404 对机密岗是假阴性，不判死")
        return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                               evidence=f"HTTP {r.status_code}")


# ---------- Ashby（jobs.ashbyhq.com/{org}/{jobId}） ----------


class AshbyProvider:
    id = "ashby"
    hosts = ("ashbyhq.com",)
    _API = "https://api.ashbyhq.com/posting-api/job-board"

    _PATH = re.compile(r"^/([A-Za-z0-9._-]{1,80})/([A-Za-z0-9._-]{8,64})/?$")

    def parse(self, url: str) -> JobRef | None:
        parts = urlsplit_parts(url)
        if parts[0] != "jobs.ashbyhq.com":
            return None
        m = self._PATH.match(parts[1])
        if not m:
            return None
        org, job_id = m.group(1), m.group(2)
        if not (_safe_seg(org) and _safe_seg(job_id)):
            return None
        return JobRef(job_id, self._API, extra={"org": org})

    def _board(self, ref: JobRef, http: HttpLike):
        """board 级单文档（无分页）。返回 (payload, error) 二元组，绝不抛。"""
        r = http.get(
            f"{self._API}/{ref.extra['org']}",
            params={"includeCompensation": "true"},
            headers={"User-Agent": _UA},
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        payload = r.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return None, "board 响应缺 jobs 数组（结构变更或板不存在）"
        return payload, ""

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        payload, err = self._board(ref, http)
        if payload is None:
            return JdResult(False, error=err)
        for j in payload["jobs"]:
            # Ashby 页面 JS 渲染——静态 GET 只见导航/页脚（career-ops 实测），
            # API 是权威源；ID 大小写不敏感比对（同 career-ops classifyAshbyBoard）
            if str(j.get("id") or "").lower() == ref.job_key.lower():
                jd = _clean(j.get("descriptionPlain")) or _html_to_text(j.get("description"))
                if not jd:
                    return JdResult(False, error="匹配岗位正文为空")
                return JdResult(True, jd_text=jd, api_title=_clean(j.get("title")) or None)
        return JdResult(False, error="no_match")

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """board 200 只证明板存在；本岗仍在列=active，板上无此岗=expired（API 权威）。"""
        try:
            r = http.get(
                f"{self._API}/{ref.extra['org']}",
                params={"includeCompensation": "true"},
                headers={"User-Agent": _UA},
            )
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code == 404:
            # board 404 ≠ 岗位下线：可能是 embed-only 组织（career-ops api404Authoritative=false）
            return LivenessVerdict("uncertain", code="ashby_board_404",
                                   evidence="board 404（可能是 embed-only 组织），不判死")
        if r.status_code != 200:
            return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                                   evidence=f"HTTP {r.status_code}")
        try:
            payload = r.json()
            jobs = payload.get("jobs") if isinstance(payload, dict) else None
        except ValueError:
            return LivenessVerdict("uncertain", code="ashby_shape_unknown", evidence="响应非 JSON")
        if not isinstance(jobs, list):
            return LivenessVerdict("uncertain", code="ashby_shape_unknown", evidence="board 响应缺 jobs")
        for j in jobs:
            if str(j.get("id") or "").lower() == ref.job_key.lower():
                if j.get("isListed") is False:
                    return LivenessVerdict("expired", code="ashby_api_unlisted",
                                           evidence="本岗在板上但 isListed=false")
                return LivenessVerdict("active", code="ashby_api_ok",
                                       evidence=f"title={j.get('title')!r}")
        return LivenessVerdict("expired", code="ashby_api_unlisted",
                               evidence=f"board 200 但本岗（{ref.job_key}）不在 {len(jobs)} 条在列名单中")


# ---------- Workday（{tenant}.{shard}.myworkdayjobs.com/{site}/job/{path}） ----------

# Workday URL 三形态（career-ops 顺序：CXS → SITE → CAREERS，先精确后宽）：
#  1) {tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}[/job/{path}]（API URL 本身）
#  2) {instance}.myworkdaysite.com/{instance}/recruiting/{tenant}/{site}/job/{path}
#  3) {tenant}.{shard}.myworkdayjobs.com/[locale/]{site}/job/{path}
_WD_CXS = re.compile(
    r"^https://([\w-]+)\.(wd[\w-]*)\.myworkdayjobs\.com/wday/cxs/([\w-]+)/([^/?#]+)(/job/.+)?/?$",
    re.I,
)
_WD_SITE = re.compile(
    r"^https://([\w-]+)\.myworkdaysite\.com/[\w-]+/recruiting/([\w-]+)/([^/?#]+)/job/(.+?)/?$",
    re.I,
)
_WD_CAREERS = re.compile(
    r"^https://([\w-]+)\.(wd[\w-]*)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/job/(.+?)/?$",
    re.I,
)


class WorkdayProvider:
    id = "workday"
    hosts = ("myworkdayjobs.com", "myworkdaysite.com")

    def parse(self, url: str) -> JobRef | None:
        url = url.strip()
        # 形态 1：CXS API URL 本身（{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}[/job/...]）
        m = _WD_CXS.match(url)
        if m:
            tenant, shard, site = m.group(1), m.group(2), m.group(3)
            host = f"{tenant}.{shard}.myworkdayjobs.com"
            return self._make_ref(host, tenant, site, m.group(5) or "")
        # 形态 2：{instance}.myworkdaysite.com/{instance}/recruiting/{tenant}/{site}/job/{path}
        m = _WD_SITE.match(url)
        if m:
            instance, tenant, site = m.group(1), m.group(2), m.group(3)
            # myworkdaysite 的 CXS 端点同 host 同形（career-ops：same CXS shape）
            return self._make_ref(f"{instance}.myworkdaysite.com", tenant, site, f"/job/{m.group(4)}")
        # 形态 3：{tenant}.{shard}.myworkdayjobs.com/[locale/]{site}/job/{path}
        m = _WD_CAREERS.match(url)
        if m:
            tenant, shard, site = m.group(1), m.group(2), m.group(3)
            host = f"{tenant}.{shard}.myworkdayjobs.com"
            return self._make_ref(host, tenant, site, f"/job/{m.group(4)}")
        return None

    @staticmethod
    def _make_ref(host: str, tenant: str, site: str, job_path: str) -> JobRef | None:
        # externalPath 各段（/job/Toronto-ON-CAN/Title_R123 形态）逐段字符集校验
        for seg in job_path.split("/"):
            if seg and not _safe_seg(seg):
                return None
        return JobRef(
            job_path.rstrip("/"),
            f"https://{host}",
            extra={"tenant": tenant, "site": site, "cxs": f"/wday/cxs/{tenant}/{site}"},
        )

    def _detail(self, ref: JobRef, http: HttpLike):
        # WAF 关键头（career-ops 实测：部分租户缺这些头直接 500）
        r = http.get(
            f"{ref.origin}{ref.extra['cxs']}{ref.job_key}",
            headers={
                "User-Agent": _UA,
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"{ref.origin}/{ref.extra['site']}/",
            },
        )
        return r

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = self._detail(ref, http)
        r.raise_for_status()
        payload = r.json()
        info = payload.get("jobPostingInfo") or {}
        jd = _html_to_text(info.get("jobDescription"))
        if not jd:
            return JdResult(False, error="响应无 jobPostingInfo.jobDescription（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(info.get("title")) or None)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """403 + errorCode S22 = 岗位撤回（权威）；其他 403 = WAF（不确定）。"""
        try:
            r = self._detail(ref, http)
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code == 200:
            try:
                title = (r.json().get("jobPostingInfo") or {}).get("title")
            except ValueError:
                title = None
            return LivenessVerdict("active", code="workday_detail_ok", evidence=f"title={title!r}")
        if r.status_code in (404, 410):
            return LivenessVerdict("expired", code="http_gone", evidence=f"HTTP {r.status_code}")
        if r.status_code == 403:
            try:
                body = r.json()
                if body.get("httpStatus") == 403 and body.get("errorCode") == "S22":
                    return LivenessVerdict("expired", code="workday_api_withdrawn",
                                           evidence="403 + errorCode=S22（官方撤回码）")
                return LivenessVerdict("uncertain", code="workday_waf_403",
                                       evidence="403 无 S22（WAF 拦截），不判死")
            except ValueError:
                return LivenessVerdict("uncertain", code="workday_waf_403", evidence="403 响应非 JSON")
        return LivenessVerdict("uncertain", code=f"http_{r.status_code}", evidence=f"HTTP {r.status_code}")


# ---------- URL 拆解小助手（本模块内统一，避免多处重复 urlsplit 样板） ----------


def urlsplit_path(url: str) -> str:
    return urlsplit(url).path or "/"


def urlsplit_parts(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return (parts.netloc or "").lower(), parts.path or "/"
