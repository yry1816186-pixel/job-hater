"""JD 补全 provider 注册表（v4.0 B1）。

每个 provider 负责一类**官方招聘站**的「URL → 岗位标识 → JD 原文」。
全部端点 2026-09-23 活体验证（复现：python scripts/probe_jd_apis.py）：

- byteportal（字节系门户框架，同源覆盖 jobs.bytedance.com / *.f.mioffice.cn / *.jobs.feishu.cn）：
  GET {origin}/api/v1/job/posts/{id}?portal_type=N&with_recommend=false
  → data.job_post_detail.description / requirement
- zhiye（北森）：GET {origin}/api/JobAd/GetJobAdInfo → Data.Duty / Data.Require
- mihoyo：POST ats.openout.mihoyo.com/ats-portal/v1/job/info
  {"id","channelDetailIds":[1],"hireType":1} → data.description / data.jobRequire
- baidu：talent.baidu.com SSR HTML，post-content-title__/desc__ 块对（职责/要求）
- netease163：URL 的 id 是**批次 projectId 非岗位 ID**——走列表 API + 标题匹配
- wechat：复用公众号深挖通道 fetch_articles（requests 缺失时如实降级）

B2 增补（v3.2，career-ops MIT 移植，见 providers_cn.py / providers_ats.py）：
- alibaba / tencent / meituan：详情 URL 直读 + 列表 URL 搜索反查（163 模式），
  同时实现 check() 供判活 Tier1（官方业务下线码：content:null / E1005 / jobStatus 001）
- greenhouse / lever / ashby / workday：国际 ATS 单岗详情端点（外企在华岗），
  check() 判活含 Lever 404 假阴性与 Workday 403 S22 撤回码的显式建模

安全立场（与仓库合规红线一致）：
- 只请求注册表内的官方域（host 后缀精确到点号边界），不抓商业招聘板；
- 详情 API 一律免浏览器、免签名、免登录；ID 注入前字符集校验；
- 引擎层 follow_redirects=False；百度 SSR 的同域跳转在 provider 内显式限界。
"""
from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


@dataclass
class JdResult:
    ok: bool
    jd_text: str = ""
    api_title: str | None = None
    error: str = ""
    match_mode: str = ""  # exact | fuzzy | ""（直读型 provider 无匹配语义）


@dataclass
class LivenessVerdict:
    """判活结果（B3，保守立场：拿不准就是 uncertain，绝不猜死）。

    career-ops liveness 同款设计：误判死（expired）会经 dedup 过滤永久埋掉真岗，
    严格劣于多一次 uncertain——所以 expired 只允许出自确定性信号（官方 404/410、
    ATS 业务下线码、静态页硬关键词），code 记录机器依据、evidence 记录人读证据。
    """

    verdict: str  # active | expired | uncertain
    code: str = ""
    evidence: str = ""


@dataclass
class JobRef:
    job_key: str
    origin: str
    extra: dict[str, Any] = field(default_factory=dict)


class HttpLike(Protocol):
    """引擎传入的 httpx.Client（follow_redirects=False，10s 超时）。"""

    def get(self, url: str, *, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None) -> Any: ...

    def post(self, url: str, *, json: dict[str, Any],
             headers: dict[str, str] | None = None) -> Any: ...


class JdProvider(Protocol):
    id: str
    hosts: tuple[str, ...]

    def parse(self, url: str) -> JobRef | None: ...

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult: ...


def _host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _host_allowed(host: str, suffix: str) -> bool:
    """后缀匹配必须落在点号边界：jobs.bytedance.com ✓ / xjobs.bytedance.com.evil.com ✗"""
    return host == suffix or host.endswith("." + suffix)


def _clean(text: Any) -> str:
    if not text:
        return ""
    return str(text).strip()


def _join_jd(desc: str, requirement: str) -> str:
    parts = [p for p in (_clean(desc), _clean(requirement)) if p]
    if len(parts) == 2:
        return f"{parts[0]}\n任职要求：\n{parts[1]}"
    return "".join(parts)


# ---------- 字节系门户（字节/小米/飞书招聘板同一框架） ----------

_DIGITS = re.compile(r"^\d{5,25}$")


class BytePortalProvider:
    id = "byteportal"
    hosts = ("jobs.bytedance.com", "f.mioffice.cn", "jobs.feishu.cn")

    _DETAIL = re.compile(r"/position/(\d{5,25})(?:/|$|\?)")

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        if not any(_host_allowed(host, s) for s in self.hosts):
            return None
        m = self._DETAIL.search(urlsplit(url).path)
        if not m:
            return None  # 列表/落地页（无岗位 ID）不属于本 provider 可补全形态
        key = m.group(1)
        if not _DIGITS.match(key):
            return None
        scheme = urlsplit(url).scheme or "https"
        return JobRef(key, f"{scheme}://{host}", extra={"portal_type": 3 if host.endswith("bytedance.com") else 6})

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = http.get(
            f"{ref.origin}/api/v1/job/posts/{ref.job_key}",
            params={"portal_type": ref.extra["portal_type"], "with_recommend": "false"},
            headers={"User-Agent": _UA, "Referer": ref.origin + "/"},
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != 0:
            return JdResult(False, error=f"code={payload.get('code')}")
        detail = (payload.get("data") or {}).get("job_post_detail") or {}
        jd = _join_jd(detail.get("description"), detail.get("requirement"))
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(detail.get("title")))


# ---------- 北森 zhiye ----------

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ZhiyeProvider:
    id = "zhiye"
    hosts = ("zhiye.com",)

    _AD = re.compile(r"jobAdId=([0-9a-f-]{36})", re.I)

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        if not _host_allowed(host, "zhiye.com"):
            return None
        m = self._AD.search(url)
        if not m:
            return None
        key = m.group(1).lower()
        if not _UUID.match(key):
            return None
        scheme = urlsplit(url).scheme or "https"
        return JobRef(key, f"{scheme}://{host}")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = http.get(
            f"{ref.origin}/api/JobAd/GetJobAdInfo",
            params={
                "jobAdId": ref.job_key,
                "category": "job",
                "displayFields": '["Duty","Require"]',
            },
            headers={"User-Agent": _UA, "Referer": ref.origin + "/"},
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("Code") != 200:
            return JdResult(False, error=f"Code={payload.get('Code')} {payload.get('Message')}")
        data = payload.get("Data") or {}
        jd = _join_jd(data.get("Duty"), data.get("Require"))
        if not jd:
            return JdResult(False, error="Duty/Require 均为空（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(data.get("JobAdName")) or None)


# ---------- 米哈游 ----------


class MihoyoProvider:
    id = "mihoyo"
    hosts = ("jobs.mihoyo.com",)
    _API = "https://ats.openout.mihoyo.com/ats-portal/v1/job/info"

    _POS = re.compile(r"campus/position/(\d{3,12})")

    def parse(self, url: str) -> JobRef | None:
        if not _host_allowed(_host_of(url), "jobs.mihoyo.com"):
            return None
        # 岗位号在 hash 路由里（#/campus/position/7399），对整串 URL 匹配
        m = self._POS.search(url)
        if not m:
            return None
        return JobRef(m.group(1), "https://jobs.mihoyo.com")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        r = http.post(
            self._API,
            json={"id": ref.job_key, "channelDetailIds": [1], "hireType": 1},
            headers={
                "User-Agent": _UA,
                "Origin": "https://jobs.mihoyo.com",
                "Referer": "https://jobs.mihoyo.com/",
            },
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != 0:
            return JdResult(False, error=f"code={payload.get('code')} {payload.get('message')}")
        data = payload.get("data") or {}
        jd = _join_jd(data.get("description"), data.get("jobRequire"))
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(data.get("title")))


# ---------- 百度（SSR HTML 直读） ----------

_BAIDU_MARK = re.compile(r'post-content-(title|desc)__[A-Za-z0-9_]+">')
_TAG = re.compile(r"<[^>]+>")


class BaiduProvider:
    id = "baidu"
    hosts = ("talent.baidu.com",)
    _MAX_HOPS = 3

    _ID = re.compile(
        r"jobs/detail/(?:GRADUATE/)?([0-9a-f-]{36})|jobId=([0-9a-f-]{36})", re.I
    )

    def parse(self, url: str) -> JobRef | None:
        if not _host_allowed(_host_of(url), "talent.baidu.com"):
            return None
        m = self._ID.search(url)
        key = (m.group(1) or m.group(2)).lower() if m else ""
        if not _UUID.match(key):
            return None
        parts = urlsplit(url)
        # 重建为 https + 校验过的 host，保留 path+query（jobId 在 query 里，丢了就 404）
        safe = urlunsplit(("https", "talent.baidu.com", parts.path, parts.query, ""))
        return JobRef(key, "https://talent.baidu.com", extra={"url": safe})

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        # SSR 页可能带同域跳转；显式限界：最多 3 跳，相对 Location 用 urljoin 解析，
        # host 不得离开 talent.baidu.com
        url = ref.extra["url"]
        resp = None
        for _ in range(self._MAX_HOPS):
            resp = http.get(url, headers={"User-Agent": _UA})
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            loc = resp.headers.get("location", "")
            resolved = urljoin(url, loc)  # 相对路径（'/jobs/x'）必须解析成绝对 URL
            if _host_of(resolved) != "talent.baidu.com":
                return JdResult(False, error=f"拒绝跨域重定向: {loc[:80]}")
            if "/jobs/404" in resolved:
                return JdResult(False, error="岗位已下线（官方 404）")
            url = resolved
        else:
            return JdResult(False, error="重定向次数超限")
        resp.raise_for_status()
        # 标记分段提取：每个 post-content-*(title|desc)__ 块的文本延伸到下一个标记为止，
        # 而非停在第一个 </div>——真实富文本 JD 的嵌套 div 不再被截断
        marks = list(_BAIDU_MARK.finditer(resp.text))
        texts = []
        for i, m in enumerate(marks):
            if m.group(1) != "desc":
                continue
            end = marks[i + 1].start() if i + 1 < len(marks) else len(resp.text)
            chunk = resp.text[m.end():end]
            text = _clean(html_mod.unescape(_TAG.sub("", chunk)))
            if text and len(text) > 10:
                texts.append(text)
        jd = "\n".join(texts)
        if not jd:
            return JdResult(False, error="SSR 页未解析到 JD 块（结构变更或岗位下线）")
        return JdResult(True, jd_text=jd)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """B3 判活 Tier1：官方 /jobs/404 哨兵与裸 404 是确定性下线，其余保守。"""
        try:
            res = self.fetch(ref, "", http)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (404, 410):
                return LivenessVerdict("expired", code="http_gone", evidence=f"HTTP {code}")
            return LivenessVerdict("uncertain", code=f"http_{code}", evidence=f"HTTP {code}")
        except (httpx.RequestError, ValueError) as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if res.ok:
            return LivenessVerdict("active", code="baidu_ssr_ok", evidence="SSR JD 块可读")
        if "岗位已下线" in res.error:
            return LivenessVerdict("expired", code="baidu_404_sentinel", evidence=res.error)
        return LivenessVerdict("uncertain", code="baidu_unparsed", evidence=res.error[:120])


# ---------- 网易 163（URL id 是批次 → 列表 API + 标题匹配） ----------

_TITLE_NOISE = re.compile(r"[\s（）()【】\[\]·\-—/|、,，.。：:；;+＋]+")


def _norm_title(s: str) -> str:
    return _TITLE_NOISE.sub("", s).lower()


# 包含匹配的最低门槛：短于它的标题（如「工程师」「营销专员」）会在批次列表里
# 命中大量无关岗位（对抗评审 P0-1 实测复现），只允许精确等价
_FUZZY_MIN_CHARS = 6
_FUZZY_MIN_RATIO = 0.6


def _title_match_mode(want: str, got: str) -> str | None:
    """分层标题匹配：exact 优先；包含匹配须双方长度与相似比同时过线。"""
    if not want or not got:
        return None
    if want == got:
        return "exact"
    shorter, longer = (want, got) if len(want) <= len(got) else (got, want)
    if len(shorter) >= _FUZZY_MIN_CHARS and len(shorter) / len(longer) >= _FUZZY_MIN_RATIO:
        return "fuzzy" if shorter in longer else None
    return None


class Netease163Provider:
    id = "netease163"
    hosts = ("campus.163.com",)
    _MAX_PAGES = 10

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        if not (_host_allowed(host, "campus.163.com") or _host_allowed(host, "campus.game.163.com")):
            return None
        pid = (parse_qs(urlsplit(url).query).get("id") or [None])[0]
        if not pid or not pid.isdigit() or not (1 <= len(pid) <= 10):
            return None
        return JobRef(pid, f"https://{host}")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        want = _norm_title(job_title)
        if not want:
            return JdResult(False, error="本地岗位缺少标题，无法匹配")
        for page in range(1, self._MAX_PAGES + 1):
            r = http.get(
                f"{ref.origin}/api/campuspc/position/getJobList",
                params={"pageSize": 100, "currentPage": page, "projectId": ref.job_key},
                headers={"User-Agent": _UA, "Referer": ref.origin + "/"},
            )
            r.raise_for_status()
            payload = r.json()
            # 对齐其余 provider：HTTP 200 但业务码非 200 是接口故障，不是「标题不匹配」
            if payload.get("code") not in (200, 0):
                return JdResult(False, error=f"code={payload.get('code')} {payload.get('msg') or ''}"[:150])
            items = ((payload.get("data") or {}).get("list")) or []
            fuzzy_hit: tuple[dict, str] | None = None
            for it in items:
                got = _norm_title(str(it.get("positionName") or ""))
                mode = _title_match_mode(want, got)
                if mode == "exact":
                    jd = _join_jd(it.get("positionDescription"), it.get("positionRequirement"))
                    if jd:
                        return JdResult(True, jd_text=jd, api_title=_clean(it.get("positionName")),
                                        match_mode="exact")
                    return JdResult(False, error="匹配岗位正文为空")
                if mode == "fuzzy" and fuzzy_hit is None:
                    fuzzy_hit = (it, got)
            if len(items) < 100:
                break
        if fuzzy_hit is not None:
            it, _ = fuzzy_hit
            jd = _join_jd(it.get("positionDescription"), it.get("positionRequirement"))
            if jd:
                return JdResult(True, jd_text=jd, api_title=_clean(it.get("positionName")),
                                match_mode="fuzzy")
        return JdResult(False, error="no_match")


# ---------- 微信公众号文章（复用深挖通道） ----------


class WechatArticleProvider:
    id = "wechat"
    hosts = ("mp.weixin.qq.com",)

    def parse(self, url: str) -> JobRef | None:
        if not _host_allowed(_host_of(url), "mp.weixin.qq.com"):
            return None
        return JobRef(url, "https://mp.weixin.qq.com")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        # requests 属可选依赖（wechat 组）：缺失时深挖通道如实降级，不静默假装成功
        try:
            from jobhater.services.wechat.article_fetch import fetch_articles
        except ImportError as e:
            return JdResult(False, error=f"深挖通道不可用（缺可选依赖 requests）: {e}")
        arts = fetch_articles([ref.job_key])
        art = arts.get(ref.job_key)
        if art is None or not (art.text or "").strip():
            return JdResult(False, error="文章正文为空或抓取失败")
        return JdResult(True, jd_text=art.text.strip(), api_title=art.title or None)


# B2 增补的七个 provider 在子模块实现（career-ops 移植）。import 必须放在本模块
# 全部公共定义之后：子模块反向 import 本模块的名字（JdResult/_join_jd 等），此时
# 本模块已注册进 sys.modules，循环引用安全（E402 因此豁免）。
from jobhater.services.enrich.providers_ats import AshbyProvider as _AshbyProvider  # noqa: E402
from jobhater.services.enrich.providers_ats import (
    GreenhouseProvider as _GreenhouseProvider,  # noqa: E402
)
from jobhater.services.enrich.providers_ats import LeverProvider as _LeverProvider  # noqa: E402
from jobhater.services.enrich.providers_ats import WorkdayProvider as _WorkdayProvider  # noqa: E402
from jobhater.services.enrich.providers_cn import AlibabaProvider as _AlibabaProvider  # noqa: E402
from jobhater.services.enrich.providers_cn import MeituanProvider as _MeituanProvider  # noqa: E402
from jobhater.services.enrich.providers_cn import TencentProvider as _TencentProvider  # noqa: E402

PROVIDERS: list[JdProvider] = [
    BytePortalProvider(),
    ZhiyeProvider(),
    MihoyoProvider(),
    BaiduProvider(),
    Netease163Provider(),
    WechatArticleProvider(),
    _AlibabaProvider(),
    _TencentProvider(),
    _MeituanProvider(),
    _GreenhouseProvider(),
    _LeverProvider(),
    _AshbyProvider(),
    _WorkdayProvider(),
]


def provider_for_url(url: str) -> JdProvider | None:
    host = _host_of(url)
    for p in PROVIDERS:
        if any(_host_allowed(host, s) for s in p.hosts):
            return p
    return None


def host_filter_clause(providers: list[JdProvider] | None = None) -> tuple[str, list[str]]:
    """生成 pending 查询的 host 预过滤 SQL（LIKE 参数列表）。

    providers=None 时覆盖全部注册域；定向运行时只圈定目标 provider 的域——
    预过滤必须发生在 LIMIT 之前，否则新行被其他域占满时定向运行会饿死。
    预过滤只是缩小扫描面；真正的域校验始终在 provider.parse 的点号边界判定。
    """
    conds = []
    args: list[str] = []
    for p in (providers if providers is not None else PROVIDERS):
        for h in p.hosts:
            conds.append("j.canonical_url LIKE '%' || ? || '/%'")
            args.append(h)
    return " OR ".join(conds), args
