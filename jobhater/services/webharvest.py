"""网页采集引擎（v3.8）：种子 URL → 放大抓取 → 三级提取 → raw jobs。

设计立场（v3.7 插件实战验证的产物——提取管线从 agent 手写脚本升级为引擎能力）：
- 全专业全地域无偏向：适配器按「站点结构」适配（猎聘 DOM / JSON-LD 通用标准 /
  通用启发式），不按方向、地域、行业做任何过滤——方向判断永远留给用户的偏好。
- 绝不臆造：三级提取全失败 → fail 如实返回（与 paste_parse 的诚实拒绝同语义）；
  通用启发式路径产出的 raw 一律标 needs_review。
- 安全：出站前 netguard.guard_url 预检（私网/非 http 零请求发出）；robots.txt
  遵守（每主机缓存）；per-host 限速复用 enrich 的 _ThrottledClient（1.2s）；
  UA 与引擎其他抓取面一致（providers._UA）；绝不规避反检测——被拦/反爬一律
  记入 degraded 如实返回。
- 可持续：抓取前按 canonical_url 查库跳过已入库页（幂等重跑不翻倍）；
  depth ≤ 2、max_pages 硬顶；串行礼貌节奏（40 页约 1 分钟）。

与 SourceAdapter 的关系：不硬套该 Protocol——本服务是 URL 种子驱动的采集
（用户/agent 给种子），不是官方接口拉取；产出仍走 JobService.ingest 统一规范。
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from jobhater.services.enrich.engine import (
    HOST_MIN_INTERVAL_S,
    REQUEST_TIMEOUT_S,
    _ThrottledClient,
)
from jobhater.services.enrich.providers import _UA
from jobhater.services.netguard import guard_url

MAX_DEPTH = 2
DEFAULT_MAX_PAGES = 50
BODY_MAX_BYTES = 3 * 1024 * 1024
MIN_BODY_CHARS = 60
# JSON-LD 是站点主动内嵌的结构化数据（非抓取推测），正文可信下限低于散文抓取
MIN_JSONLD_DESC_CHARS = 30


# ---------- HTML 工具 ----------


class _TextOut(HTMLParser):
    """轻量正文抽取：去 script/style，保留块级换行。"""

    _BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "section", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{2,}", "\n", "".join(self.parts))


def _html_text(html: str) -> str:
    p = _TextOut()
    try:
        p.feed(html)
    except Exception:  # 畸形 HTML：尽力而为
        pass
    return p.text()


def _strip_tags(fragment: str) -> str:
    txt = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", fragment)
    txt = re.sub(r"<[^>]+>", "", txt)
    return re.sub(r"\n{2,}", "\n", re.sub(r"[ \t\u3000]+", " ", txt)).strip()


# ---------- 站点适配器（按结构，不按方向/地域） ----------


def _extract_liepin(html: str, url: str) -> tuple[dict | None, str]:
    """猎聘详情页（2026-11 LIVE 验证：正文 data-selector="job-intro-content"，
    三要素在 <title>【城市 岗位名招聘】-公司名…，薪资在 detail-header 区。"""
    m = re.search(r'data-selector="job-intro-content"[^>]*>(.*?)</dd>', html, re.S)
    if not m:
        return None, "liepin: 无 job-intro-content"
    body = _strip_tags(m.group(1))
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    title_page = _strip_tags(t.group(1)) if t else ""
    city = title = company = salary = ""
    m2 = re.match(r"【(.+?)\s+(.+?)招聘】-(.+?)招聘信息", title_page)
    if m2:
        city, title, company = (s.strip() for s in m2.groups())
        # 城市过滤页的公司段常带城市回声（「湖北富思特集团武汉」）——按捕获城市剥离
        if city and company.endswith(city):
            company = company[: -len(city)].strip() or company
    head = re.search(r'class="job-detail-header-box"(.*?)job-detail-company-box', html, re.S)
    if head:
        s = re.search(r'class="job-salary">([^<]+)<', head.group(1))
        if s:
            salary = s.group(1).strip()
    if not title or not company:
        return None, "liepin: 标题三要素解析失败"
    if len(body) < MIN_BODY_CHARS:
        return None, "liepin: 正文过短"
    return {
        "title": title, "company": company, "city": city or None,
        "salary": salary or None, "description": body[:4000], "url": url,
    }, "liepin"


def _extract_jsonld(html: str, url: str) -> tuple[dict | None, str]:
    """schema.org/JobPosting（通用标准——大量招聘站为搜索引擎索引而内嵌）。"""
    out = None
    for m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            graph = node.get("@graph") if isinstance(node, dict) else None
            candidates2 = (graph or []) + [node]
            for n in candidates2:
                if not isinstance(n, dict):
                    continue
                if "JobPosting" not in str(n.get("@type", "")):
                    continue
                out = n
                break
            if out:
                break
        if out:
            break
    if not out:
        return None, "jsonld: 无 JobPosting"
    title = _strip_tags(str(out.get("title") or out.get("name") or ""))
    org = out.get("hiringOrganization") or {}
    company = _strip_tags(str(org.get("name", "")) if isinstance(org, dict) else str(org))
    desc = _strip_tags(str(out.get("description") or ""))
    city = ""
    loc = out.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc]
    for loc_item in locs:
        if isinstance(loc_item, dict):
            addr = loc_item.get("address") or {}
            if isinstance(addr, dict):
                city = str(addr.get("addressRegion") or addr.get("addressLocality") or "")
                if city:
                    break
    salary = ""
    bs = out.get("baseSalary") or {}
    if isinstance(bs, dict):
        val = bs.get("value") or {}
        unit_raw = str(bs.get("unitText") or val.get("unitText") or "").strip()
        currency = str(
            out.get("currency") or bs.get("currency") or val.get("currency") or ""
        ).strip().upper()
        # schema.org 标准单位→中文薪资单位（parse_salary 可解析）；其余（K 等）原样透传
        unit_txt = {"MONTH": "元/月", "YEAR": "元/年", "WEEK": "元/周",
                    "DAY": "元/天", "HOUR": "元/时"}.get(unit_raw.upper(), unit_raw)
        if currency and currency not in ("CNY", "RMB"):
            # 非 CNY：如实保留币种（不换汇、不臆造）——解析器不识别→None，诚实降级
            unit_txt = currency
        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        lo_v, hi_v = _num(val.get("minValue")), _num(val.get("maxValue"))
        if lo_v is not None or hi_v is not None:
            lo_v = lo_v if lo_v is not None else hi_v
            hi_v = hi_v if hi_v is not None else lo_v
            salary = (f"{lo_v:g}-{hi_v:g}{unit_txt}" if hi_v != lo_v
                      else f"{lo_v:g}{unit_txt}").strip()
    if not title or not company:
        return None, "jsonld: 缺 title/company"
    if len(desc) < MIN_JSONLD_DESC_CHARS:
        return None, "jsonld: 正文过短"
    raw = {"title": title, "company": company, "city": city or None,
           "salary": salary or None, "description": desc[:4000], "url": url}
    if out.get("datePosted"):
        raw["published_at"] = str(out["datePosted"])
    return raw, "jsonld"


_JOB_SECTIONS = re.compile(
    r"(岗位职责|工作职责|职位描述|任职要求|岗位要求|职位职责|工作内容|job description)", re.I
)


def _extract_generic(html: str, url: str) -> tuple[dict | None, str]:
    """通用启发式：title 标签三要素/裸标题 + 正文段落。产出标 needs_review。"""
    text = _html_text(html)
    title = company = city = ""
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    if t:
        tp = _strip_tags(t.group(1))
        m = re.match(r"【(.+?)\s+(.+?)招聘】-(.+?)(?:招聘|人才网|信息)", tp)
        if m:
            city, title, company = (s.strip() for s in m.groups())
        else:
            # 国内 SEO 站常见格式「岗位名招聘_公司名招聘-平台名」(高校人才网等):
            # 下划线前段是岗位、第二段才是公司——必须先于宽泛的横线规则匹配,
            # 否则横线规则会把平台名误当公司名(2026-11-21 LIVE 实测暴露)
            m0 = re.match(r"(.{2,40}?)招聘[_·](.{2,40}?)招聘", tp)
            if m0:
                title = m0.group(1).strip()
                # 部分站点的公司段带 #/| 等装饰尾巴(「研究所#招聘」)——剥掉再入库
                company = m0.group(2).strip().rstrip("#|、丨")[:60] or company
            else:
                m2 = re.match(r"(.{2,40}?)招聘[信息\s]*[-–—|]\s*(.+)$", tp)
                if m2:
                    title = m2.group(1).strip()
                    company = m2.group(2).strip()[:60]
    # og 标签兜底
    if not title:
        m = re.search(r'property="og:title"[^>]*content="([^"]+)"', html)
        if m:
            title = _strip_tags(m.group(1))[:60]
    if not title or len(text) < MIN_BODY_CHARS:
        return None, "generic: 标题或正文不足"
    # 正文截取：从第一个岗位段关键词起到文本尾部（宁多勿少，needs_review 交用户）
    m = _JOB_SECTIONS.search(text)
    body = text[m.start():].strip() if m else text[:4000].strip()
    if len(body) < MIN_BODY_CHARS:
        return None, "generic: 正文过短"
    return {
        "title": title, "company": company or None, "city": city or None,
        "description": body[:4000], "url": url, "needs_review": True,
    }, "generic"


_ADAPTERS: list[tuple[str, Callable[[str, str], tuple[dict | None, str]]]] = [
    ("liepin", _extract_liepin),
]
# 适配器之外的兜底链（对任何站点生效——全行业无偏向的关键）
_FALLBACKS = [_extract_jsonld, _extract_generic]


def extract_page(html: str, url: str) -> tuple[dict | None, str, bool]:
    """三级提取。返回 (raw, 方法名, needs_review)。全败返回 (None, 原因, False)。"""
    host = urlsplit(url).hostname or ""
    for name, fn in _ADAPTERS:
        if name in host:
            raw, why = fn(html, url)
            if raw:
                return raw, name, False
            last_why = why
            break
    else:
        last_why = "无匹配站点适配器"
    for fn in _FALLBACKS:
        raw, why = fn(html, url)
        if raw:
            return raw, fn.__name__.replace("_extract_", ""), raw.pop("needs_review", False)
    return None, f"{last_why}; 兜底失败", False


# 详情链接模式：先站点级（精确），后通用兜底（job 型路径）
_LINK_PATTERNS = {
    "liepin": re.compile(r'href="(/job/\d+\.shtml)"'),
}
_GENERIC_LINK = re.compile(
    r'href="([^"#?]+(?:job|jobs|position|position_detail|zhaopin|recruit|career)[^"#?]*/[^"#?]+\.(?:s?html|htm))"',
    re.I,
)


def related_links(html: str, url: str, cap: int = 60) -> list[str]:
    host = urlsplit(url).hostname or ""
    links: list[str] = []
    for pat in (_LINK_PATTERNS.get(host.split(".")[-2] if host.count(".") >= 1 else "", None)
                or _LINK_PATTERNS.get(host, None),
                _GENERIC_LINK):
        if pat is None:
            continue
        for m in pat.finditer(html):
            href = m.group(1)
            full = urljoin(url, href)
            if urlsplit(full).hostname == host and full not in links:
                links.append(full)
        if links:
            break  # 命中站点级模式就不再用泛化兜底（防误收）
    return links[:cap]


# ---------- robots.txt（每主机缓存，遵守 Disallow） ----------


class _RobotsCache:
    def __init__(self, client: _ThrottledClient, sleep: Callable[[float], None]):
        self._client = client
        self._cache: dict[str, tuple[bool, list[str], str]] = {}

    def allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._cache:
            note = ""
            prefixes: list[str] = []
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            try:
                r = self._client.get(robots_url, headers={"User-Agent": _UA})
                if r.status_code == 200:
                    ua_scope = None
                    for line in r.text.splitlines():
                        line = line.split("#")[0].strip()
                        low = line.lower()
                        if low.startswith("user-agent:"):
                            ua_scope = line.split(":", 1)[1].strip()
                        elif low.startswith("disallow:") and ua_scope == "*":
                            p = line.split(":", 1)[1].strip()
                            if p:
                                prefixes.append(p)
                elif r.status_code in (401, 403):
                    note = "robots 返回 403，按全站不抓处理"
                    prefixes = ["/"]
                else:
                    note = f"robots 状态 {r.status_code}，视为允许"
            except httpx.HTTPError as e:
                note = f"robots 不可达（{type(e).__name__}），视为允许"
            self._cache[host] = (not prefixes, prefixes, note)
        ok, prefixes, note = self._cache[host]
        if not ok:
            path = parts.path or "/"
            if any(path.startswith(p) for p in prefixes):
                return False, note or "robots Disallow"
            return True, note
        return True, note


# ---------- 服务 ----------


@dataclass
class HarvestReport:
    pages_fetched: int = 0
    links_found: int = 0
    skipped_existing: int = 0
    raw_jobs: list[dict] = field(default_factory=list)
    methods: dict[str, int] = field(default_factory=dict)
    failed: list[dict] = field(default_factory=list)   # 抓到了但提取不出（含原因）
    degraded: list[dict] = field(default_factory=list)  # 未抓（robots/反爬/SSRF/网络）

    def as_dict(self) -> dict:
        return {
            "pages_fetched": self.pages_fetched,
            "links_found": self.links_found,
            "skipped_existing": self.skipped_existing,
            "methods": self.methods,
            "failed": self.failed,
            "degraded": self.degraded,
            "extracted": len(self.raw_jobs),
        }


class WebHarvestService:
    def __init__(self, con=None, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 existing_urls: set[str] | None = None):
        self.con = con
        self._sleep = sleep
        inner = httpx.Client(
            timeout=REQUEST_TIMEOUT_S, follow_redirects=True, transport=transport,
            headers={"User-Agent": _UA},
        )
        self._client = _ThrottledClient(inner, self._sleep, HOST_MIN_INTERVAL_S)
        self._robots = _RobotsCache(self._client, self._sleep)
        self._existing = existing_urls or set()

    def close(self) -> None:
        self._client._inner.close()

    # ---- 单页 ----

    def _fetch(self, url: str) -> tuple[str | None, str]:
        """返回 (html, "") 或 (None, 降级/失败原因)。SSRF 闸先行：零请求发出。"""
        blocked = guard_url(url)
        if blocked:
            return None, f"ssrf_blocked: {blocked}"
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return None, "degraded: 仅支持 http/https"
        ok, note = self._robots.allowed(url)
        if not ok:
            return None, f"degraded: robots 不允许（{note}）"
        try:
            r = self._client.get(url, headers={"User-Agent": _UA})
        except httpx.HTTPError as e:
            return None, f"degraded: 网络错误（{type(e).__name__}）"
        if r.status_code != 200:
            return None, f"degraded: http_{r.status_code}（如实记录，不规避）"
        ctype = r.headers.get("content-type", "")
        if ctype and "html" not in ctype:
            return None, f"degraded: 非 HTML（{ctype.split(';')[0]}）"
        if len(r.content) > BODY_MAX_BYTES:
            return None, "degraded: 响应超 3MB 上限"
        return r.text, ""

    def preview(self, url: str) -> dict:
        """单页提取预览（只读不落库）——agent 批量前先验质量。"""
        html, why = self._fetch(url)
        if html is None:
            return {"ok": False, "url": url, "reason": why}
        raw, method, needs_review = extract_page(html, url)
        out: dict = {"ok": raw is not None, "url": url, "method": method,
                     "related_links": len(related_links(html, url))}
        if raw:
            out["job"] = raw
            out["needs_review"] = needs_review
        else:
            out["reason"] = method
        return out

    # ---- 批量（放大器） ----

    def harvest(self, seeds: list[str], *, depth: int = 1, max_pages: int = DEFAULT_MAX_PAGES,
                ingest: bool = True, source_of: Callable[[str], str] | None = None) -> dict:
        """BFS：种子 → 提取 → 收集同域 job 链接（depth 跳）→ 入库（可选）。
        幂等：已在库的 URL 直接跳过（skipped_existing），可反复重跑。"""
        depth = max(0, min(depth, MAX_DEPTH))
        max_pages = max(1, min(max_pages, 500))
        rep = HarvestReport()
        queue: list[tuple[str, int]] = []
        seen: set[str] = set()
        for u in seeds:
            u = u.strip()
            if u and u not in seen:
                seen.add(u)
                queue.append((u, 0))
        raws_by_source: dict[str, list[dict]] = {}
        while queue and rep.pages_fetched < max_pages:
            url, d = queue.pop(0)
            host = urlsplit(url).hostname or "unknown"
            if (self.con is not None or self._existing) and self._exists(url):
                rep.skipped_existing += 1
                continue
            html, why = self._fetch(url)
            if html is None:
                (rep.degraded if why.startswith("degraded") or why.startswith("ssrf")
                 else rep.failed).append({"url": url, "reason": why})
                continue
            rep.pages_fetched += 1
            raw, method, needs_review = extract_page(html, url)
            if raw:
                if needs_review:
                    raw["needs_review"] = True
                rep.methods[method] = rep.methods.get(method, 0) + 1
                raws_by_source.setdefault(source_of(host) if source_of else host, []).append(raw)
                rep.raw_jobs.append(raw)
            else:
                rep.failed.append({"url": url, "reason": method})
            if d < depth:
                for link in related_links(html, url):
                    if link not in seen:
                        seen.add(link)
                        queue.append((link, d + 1))
        rep.links_found = max(0, len(seen) - rep.pages_fetched - rep.skipped_existing)
        out = rep.as_dict()
        if ingest and self.con is not None:
            out["ingest"] = self._ingest(raws_by_source)
        else:
            out["jobs"] = rep.raw_jobs
        return out

    def _exists(self, url: str) -> bool:
        if url in self._existing:
            return True
        if self.con is None:
            return False
        row = self.con.execute(
            "SELECT 1 FROM job_postings WHERE canonical_url=? LIMIT 1", (url,)
        ).fetchone()
        return row is not None

    def _ingest(self, raws_by_source: dict[str, list[dict]]) -> dict:
        from jobhater.services.jobs import JobService

        js = JobService(self.con)
        total = {"received": 0, "added": 0, "deduped_exact": 0, "deduped_near": 0,
                 "enriched": 0, "rejected": 0, "by_source": {}}
        for source_id, raws in raws_by_source.items():
            js.ensure_source(source_id, "api_fetch", f"网页采集 {source_id}")
            st = js.ingest(raws, source_id=source_id)
            d = st.model_dump()
            total["by_source"][source_id] = {
                k: d[k] for k in ("received", "added", "deduped_exact",
                                  "deduped_near", "enriched", "rejected")
            }
            for k in total:
                if k != "by_source" and k in d:
                    total[k] += d[k]
        return total
