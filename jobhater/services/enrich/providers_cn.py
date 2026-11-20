"""B2 中国大厂官方源 provider：阿里巴巴 / 腾讯 / 美团——JD 补全与判活双用途。

端点知识来源：career-ops（MIT，github.com/career-ops-hq/career-ops）providers/
{alibaba,tencent,meituan}.mjs 与 liveness-api.mjs，其 README 标注 2026-07/2026-11
活体验证；本文件按其规格移植为 Python，fixture 按 career-ops 文档化响应形态构造。

三源共同形态：
- **详情 URL 直读**（主路径）：URL 自带岗位 ID（positionId / postId / jobUnionId）
  → 单次官方详情接口拿到 JD 全文，零标题匹配；
- **列表 URL 搜索反查**（163 模式推广）：URL 只是列表/落地页（存量岗常见形态）
  → 以本地岗位标题调官方搜索接口，exact 优先 / fuzzy 须过闸（≥6 字符且比例 ≥0.6），
  api_title+match_mode 落账可审计——串岗防线与 Netease163Provider 同构。
- **判活 check()**：详情接口同时回答「岗位还在不在」（阿里 content:null /
  腾讯 E1005 / 美团 jobStatus 001 = 确定性下线信号），供 LivenessService Tier1 使用。

安全立场（与 B1 一致）：host 点号边界后缀匹配；ID 字符集校验后才入 URL/请求体；
引擎层 follow_redirects=False + per-host 节流；阿里详情接口的无状态 CSRF 对
（XSRF-TOKEN cookie 与 x-xsrf-token header 同值自铸）按 career-ops 实测语义使用，
不涉及任何登录态。
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from jobhater.services.enrich.providers import (
    _UA,
    HttpLike,
    JdResult,
    JobRef,
    LivenessVerdict,
    _clean,
    _host_allowed,
    _host_of,
    _join_jd,
    _norm_title,
    _title_match_mode,
)

# 搜索反查的礼貌上限：标题查询结果通常 ≤2 页，5 页硬顶防异常响应拖长节流队列
_SEARCH_MAX_PAGES = 5
_SEARCH_PAGE_SIZE = 100


def _search_match(
    items: list[dict[str, Any]],
    want: str,
    extract: Any,
) -> JdResult:
    """163 同构的分层标题匹配：页内 exact 立即返回，fuzzy 记住待翻页结束。

    extract(item) -> (api_title, jd_text)；exact 命中但正文为空按「匹配岗位正文为空」
    记失败（不下调到 fuzzy——串岗风险大于重试收益）。
    """
    fuzzy_item = None
    fuzzy_title = ""
    for it in items:
        api_title = _clean(extract(it)[0])
        mode = _title_match_mode(want, _norm_title(api_title))
        if mode == "exact":
            jd = extract(it)[1]
            if jd:
                return JdResult(True, jd_text=jd, api_title=api_title, match_mode="exact")
            return JdResult(False, error="匹配岗位正文为空", api_title=api_title)
        if mode == "fuzzy" and fuzzy_item is None:
            fuzzy_item, fuzzy_title = it, api_title
    if fuzzy_item is not None:
        jd = extract(fuzzy_item)[1]
        if jd:
            return JdResult(True, jd_text=jd, api_title=fuzzy_title, match_mode="fuzzy")
    return JdResult(False, error="no_match")


# ---------- 阿里巴巴（talent.alibaba.com / campus-talent.alibaba.com） ----------


class AlibabaProvider:
    id = "alibaba"
    hosts = ("talent.alibaba.com", "campus-talent.alibaba.com")

    # LIVE 发现（2026-11-20，真实库 989 条存量全在 campus 子域）：channel 按 URL
    # 所属站点分流——campus 岗 ID 走 group_official_site 会拿到 content=null（假死），
    # 必须用 campus_official_site；两个子域 API 同构（/position/detail|search）。
    _CHANNELS = {
        "talent.alibaba.com": "group_official_site",
        "campus-talent.alibaba.com": "campus_official_site",
    }

    # 详情 URL：…/position-detail?positionId={1-15 位数字}（campus/off-campus 同形）
    _ID = re.compile(r"positionId=(\d{1,15})(?:&|$)")

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        channel = next((c for h, c in self._CHANNELS.items() if _host_allowed(host, h)), None)
        if channel is None:
            return None
        origin = f"https://{host}"
        m = self._ID.search(url)
        if m:
            return JobRef(m.group(1), origin, extra={"channel": channel})
        # 列表/落地页：host 命中但无岗位 ID → 搜索反查模式（标题驱动）
        return JobRef("", origin, extra={"mode": "search", "channel": channel})

    @staticmethod
    def _xsrf_headers(origin: str) -> dict[str, str]:
        # 阿里招聘站详情/搜索接口是无状态双提交 CSRF 过滤（非登录墙）：
        # 自铸一对同值 token 即可（career-ops 2026-07 实测：UUID 对被接受）
        token = uuid.uuid4().hex
        return {
            "User-Agent": _UA,
            "Referer": origin + "/",
            "Cookie": f"XSRF-TOKEN={token}",
            "x-xsrf-token": token,
        }

    def _detail(self, ref: JobRef, http: HttpLike) -> JdResult:
        r = http.post(
            f"{ref.origin}/position/detail",
            json={"id": int(ref.job_key), "channel": ref.extra["channel"]},
            headers=self._xsrf_headers(ref.origin),
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("success") is not True:
            return JdResult(False, error=f"接口返回 success={payload.get('success')!r} "
                                         f"{str(payload.get('message') or payload.get('errorCode') or '')[:80]}")
        content = payload.get("content")
        if not isinstance(content, dict):
            # career-ops 判定：success=true 且 content=null = 岗位已下线/不存在
            return JdResult(False, error="岗位已下线（官方返回 content 为空）")
        jd = _join_jd(content.get("description"), content.get("requirement"))
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(content.get("name")) or None)

    def _search(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        want = _norm_title(job_title)
        if not want:
            return JdResult(False, error="本地岗位缺少标题，无法匹配")
        for page in range(1, _SEARCH_MAX_PAGES + 1):
            r = http.post(
                f"{ref.origin}/position/search",
                json={
                    "channel": ref.extra["channel"], "language": "zh",
                    "batchId": "", "categories": "", "deptCodes": [],
                    "key": job_title, "pageIndex": page, "pageSize": _SEARCH_PAGE_SIZE,
                    "regions": "", "subCategories": "",
                },
                headers=self._xsrf_headers(ref.origin),
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("success") is not True:
                return JdResult(False, error=f"接口返回 success={payload.get('success')!r}"[:120])
            content = payload.get("content")
            if not isinstance(content, dict):
                return JdResult(False, error=f"搜索响应缺 content 对象（得到 {type(content).__name__}）")
            items = content.get("datas")
            # LIVE 发现（2026-11-20）：campus 域空结果时 datas=null（社招域是 []），
            # 且 content 带 totalCount/currentPage/pageSize——null+分页键在场 = 合法
            # 空结果（no_match 语义），只有连分页键都没有才算结构漂移（failed 语义）
            if items is None and not any(k in content for k in ("totalCount", "currentPage", "pageSize")):
                return JdResult(False, error=f"搜索响应缺 content.datas（得到键：{sorted(content)[:5]}）")
            items = items or []
            res = _search_match(
                items, want,
                lambda it: (it.get("name") or "",
                            _join_jd(it.get("description"), it.get("requirement"))),
            )
            if res.ok or res.error != "no_match":
                return res
            if len(items) < _SEARCH_PAGE_SIZE:
                break
        return JdResult(False, error="no_match")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        if ref.extra.get("mode") == "search":
            return self._search(ref, job_title, http)
        return self._detail(ref, http)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """Tier1 判活：success=true + content=null = 确定性下线（career-ops 实测语义）。"""
        if ref.extra.get("mode") == "search" or not ref.job_key:
            return LivenessVerdict("uncertain", code="search_url_no_id",
                                   evidence="列表页 URL 无岗位 ID，详情判活不适用")
        try:
            r = http.post(
                f"{ref.origin}/position/detail",
                json={"id": int(ref.job_key), "channel": ref.extra["channel"]},
                headers=self._xsrf_headers(ref.origin),
            )
        except Exception as e:  # 判活绝不因传输故障猜死
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code != 200:
            return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                                   evidence=f"HTTP {r.status_code}")
        payload = r.json()
        if payload.get("success") is True and payload.get("content") is None:
            return LivenessVerdict("expired", code="alibaba_content_null",
                                   evidence="success=true 且 content=null")
        if payload.get("success") is True and isinstance(payload.get("content"), dict):
            return LivenessVerdict("active", code="alibaba_detail_ok",
                                   evidence=f"content.name={payload['content'].get('name')!r}")
        return LivenessVerdict("uncertain", code="alibaba_shape_unknown",
                               evidence=f"success={payload.get('success')!r}")


# ---------- 腾讯（careers.tencent.com） ----------


class TencentProvider:
    id = "tencent"
    hosts = ("careers.tencent.com", "join.qq.com")
    _API = "https://careers.tencent.com"

    # 详情 URL：…/jobdesc.html?postId={1-15 位数字}
    _ID = re.compile(r"postId=(\d{1,15})(?:&|$)")

    def parse(self, url: str) -> JobRef | None:
        host = _host_of(url)
        if not _host_allowed(host, "careers.tencent.com"):
            # join.qq.com = 腾讯招聘投递站（真实库 634 条存量在此，URL 为
            # resume.html?k=令牌 形态，无 postId）——只开放搜索反查，不做详情直读
            if _host_allowed(host, "join.qq.com"):
                return JobRef("", self._API, extra={"mode": "search"})
            return None
        m = self._ID.search(url)
        if m:
            return JobRef(m.group(1), self._API)
        return JobRef("", self._API, extra={"mode": "search"})

    def _detail(self, ref: JobRef, http: HttpLike) -> JdResult:
        r = http.get(
            f"{self._API}/tencentcareer/api/post/ByPostId",
            params={"postId": ref.job_key, "language": "zh-cn",
                    "timestamp": int(time.time() * 1000)},
            headers={"User-Agent": _UA, "Referer": f"{self._API}/"},
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("Code") == 500:
            return JdResult(False, error=f"岗位已下线（业务码 E1005: {payload.get('Data')}）"
                                         if payload.get("Data") == "E1005"
                                         else f"Code=500 Data={payload.get('Data')}")
        if payload.get("Code") != 200:
            return JdResult(False, error=f"Code={payload.get('Code')} {str(payload.get('Msg') or '')[:80]}")
        data = payload.get("Data") or {}
        jd = _join_jd(data.get("Responsibility"), data.get("Requirement"))
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(data.get("RecruitPostName")) or None)

    def _search(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        want = _norm_title(job_title)
        if not want:
            return JdResult(False, error="本地岗位缺少标题，无法匹配")
        for page in range(1, _SEARCH_MAX_PAGES + 1):
            r = http.get(
                f"{self._API}/tencentcareer/api/post/Query",
                params={"keyword": job_title, "pageIndex": page,
                        "pageSize": _SEARCH_PAGE_SIZE, "language": "zh-cn",
                        "timestamp": int(time.time() * 1000)},
                headers={"User-Agent": _UA, "Referer": f"{self._API}/"},
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("Code") != 200:
                return JdResult(False, error=f"Code={payload.get('Code')} {str(payload.get('Msg') or '')[:80]}")
            data = payload.get("Data") or {}
            posts = data.get("Posts")
            # LIVE 发现（2026-11-20）：空结果时 Posts=null 而 Count 键在场——
            # null+统计键在场 = 合法空结果（no_match），无统计键才算结构漂移
            if posts is None and "Count" not in data:
                return JdResult(False, error=f"搜索响应缺 Data.Posts（得到键：{sorted(data)[:5]}）")
            posts = posts or []
            res = _search_match(
                posts, want,
                lambda it: (it.get("RecruitPostName") or "",
                            _join_jd(it.get("Responsibility"), it.get("Requirement"))),
            )
            if res.ok or res.error != "no_match":
                return res
            if len(posts) < _SEARCH_PAGE_SIZE:
                break
        return JdResult(False, error="no_match")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        if ref.extra.get("mode") == "search":
            return self._search(ref, job_title, http)
        return self._detail(ref, http)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """Tier1 判活：Code 500 + Data E1005 = 官方下线码（career-ops 实测语义）。"""
        if ref.extra.get("mode") == "search" or not ref.job_key:
            return LivenessVerdict("uncertain", code="search_url_no_id",
                                   evidence="列表页 URL 无岗位 ID，详情判活不适用")
        try:
            r = http.get(
                f"{self._API}/tencentcareer/api/post/ByPostId",
                params={"postId": ref.job_key, "language": "zh-cn",
                        "timestamp": int(time.time() * 1000)},
                headers={"User-Agent": _UA, "Referer": f"{self._API}/"},
            )
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code != 200:
            return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                                   evidence=f"HTTP {r.status_code}")
        payload = r.json()
        if payload.get("Code") == 200 and (payload.get("Data") or {}).get("PostId"):
            return LivenessVerdict("active", code="tencent_byid_ok",
                                   evidence=f"RecruitPostName={(payload['Data']).get('RecruitPostName')!r}")
        if payload.get("Code") == 500 and payload.get("Data") == "E1005":
            return LivenessVerdict("expired", code="tencent_e1005",
                                   evidence="Code=500 Data=E1005（官方下线码）")
        return LivenessVerdict("uncertain", code="tencent_shape_unknown",
                               evidence=f"Code={payload.get('Code')} Data={str(payload.get('Data'))[:60]}")


# ---------- 美团（zhaopin.meituan.com） ----------


class MeituanProvider:
    id = "meituan"
    hosts = ("zhaopin.meituan.com",)
    _API = "https://zhaopin.meituan.com/api/official"

    # 详情 URL：…/web/position/detail?jobUnionId={1-15 位数字}
    _ID = re.compile(r"jobUnionId=(\d{1,15})(?:&|$)")

    def parse(self, url: str) -> JobRef | None:
        if not _host_allowed(_host_of(url), "zhaopin.meituan.com"):
            return None
        m = self._ID.search(url)
        if m:
            return JobRef(m.group(1), self._API)
        return JobRef("", self._API, extra={"mode": "search"})

    def _detail(self, ref: JobRef, http: HttpLike) -> JdResult:
        r = http.post(
            f"{self._API}/job/getJobDetail",
            json={"jobUnionId": ref.job_key, "jobShareType": "1"},
            headers={"User-Agent": _UA, "Referer": "https://zhaopin.meituan.com/"},
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != 1:
            # status=0 = 岗位从未存在或请求方式不对——不下「已下线」结论（career-ops 同判）
            return JdResult(False, error=f"status={payload.get('status')} {str(payload.get('msg') or '')[:80]}")
        data = payload.get("data") or {}
        if str(data.get("jobUnionId") or "") != ref.job_key:
            return JdResult(False, error="详情响应岗位 ID 不一致（可能已下线）")
        job_status = str(data.get("jobStatus") or "")
        if job_status == "001":
            return JdResult(False, error="岗位已下线（jobStatus=001）")
        jd = _join_jd(data.get("jobDuty"), data.get("jobRequirement"))
        if not jd:
            return JdResult(False, error="响应无正文字段（岗位可能已下线）")
        return JdResult(True, jd_text=jd, api_title=_clean(data.get("name")) or None)

    def _search(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        want = _norm_title(job_title)
        if not want:
            return JdResult(False, error="本地岗位缺少标题，无法匹配")
        for page in range(1, _SEARCH_MAX_PAGES + 1):
            r = http.post(
                f"{self._API}/job/getJobList",
                json={
                    "page": {"pageNo": page, "pageSize": _SEARCH_PAGE_SIZE},
                    "keywords": job_title, "jobShareType": "1",
                    "jobType": [{"code": "3", "subCode": []}],
                    "cityList": [], "department": [],
                    "jfJgList": [], "typeCode": [], "specialCode": [],
                },
                headers={"User-Agent": _UA, "Referer": "https://zhaopin.meituan.com/"},
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("status") != 1:
                return JdResult(False, error=f"status={payload.get('status')} {str(payload.get('msg') or '')[:80]}")
            data = payload.get("data") or {}
            items = data.get("list")
            # 同阿里/腾讯：空结果容器可为 null，分页对象在场即合法（no_match 语义）
            if items is None and "page" not in data:
                return JdResult(False, error=f"搜索响应缺 data.list（得到键：{sorted(data)[:5]}）")
            items = items or []
            res = _search_match(
                items, want,
                lambda it: (it.get("name") or "",
                            _join_jd(it.get("jobDuty"), it.get("jobRequirement"))),
            )
            if res.ok or res.error != "no_match":
                return res
            # 美团列表接口偶发对限流返回空表（career-ops EMPTY_RETRIES 实测）——
            # 搜索反查场景交给引擎 attempts 上限重试，不在 provider 内自旋
            if len(items) < _SEARCH_PAGE_SIZE:
                break
        return JdResult(False, error="no_match")

    def fetch(self, ref: JobRef, job_title: str, http: HttpLike) -> JdResult:
        if ref.extra.get("mode") == "search":
            return self._search(ref, job_title, http)
        return self._detail(ref, http)

    def check(self, ref: JobRef, http: HttpLike) -> LivenessVerdict:
        """Tier1 判活：status=1 + jobStatus 000=在招 / 001=下线（career-ops 实测语义）。"""
        if ref.extra.get("mode") == "search" or not ref.job_key:
            return LivenessVerdict("uncertain", code="search_url_no_id",
                                   evidence="列表页 URL 无岗位 ID，详情判活不适用")
        try:
            r = http.post(
                f"{self._API}/job/getJobDetail",
                json={"jobUnionId": ref.job_key, "jobShareType": "1"},
                headers={"User-Agent": _UA, "Referer": "https://zhaopin.meituan.com/"},
            )
        except Exception as e:
            return LivenessVerdict("uncertain", code="transport_error", evidence=str(e)[:120])
        if r.status_code != 200:
            return LivenessVerdict("uncertain", code=f"http_{r.status_code}",
                                   evidence=f"HTTP {r.status_code}")
        payload = r.json()
        if payload.get("status") != 1:
            return LivenessVerdict("uncertain", code="meituan_status_0",
                                   evidence=f"status={payload.get('status')}（从未存在或接口异常，均不下死结论）")
        data = payload.get("data") or {}
        if str(data.get("jobUnionId") or "") != ref.job_key:
            return LivenessVerdict("uncertain", code="meituan_id_mismatch",
                                   evidence=f"响应 jobUnionId={data.get('jobUnionId')!r} 与请求不符")
        job_status = str(data.get("jobStatus") or "")
        if job_status == "001":
            return LivenessVerdict("expired", code="meituan_status_001",
                                   evidence="jobStatus=001（官方下线码）")
        if job_status == "000":
            return LivenessVerdict("active", code="meituan_status_000",
                                   evidence=f"name={data.get('name')!r}")
        return LivenessVerdict("uncertain", code="meituan_status_unknown",
                               evidence=f"jobStatus={job_status!r}")
