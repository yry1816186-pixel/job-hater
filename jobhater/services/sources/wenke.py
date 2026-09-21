"""wenke 官方招聘接口适配器。

抓取器移植自 wenke-radar（MIT，https://github.com/.../wenke-radar，
见 docs/THIRD_PARTY_NOTICES.md），改写为本产品 SourceAdapter 契约：
- 产出 raw dict（本系统统一 ingest 契约），不再使用其私有 JobItem；
- 逐公司故障隔离：单公司失败只记健康状态，不拖垮整批；
- 限速：官方接口礼貌抓取（每源分页间隔），每日一跑的建议写入 rate_policy。

只访问各公司招聘官网的公开接口（无需登录/无验证码）。若某源开始要求
登录或出现验证码——fail closed，如实报 degraded/down，绝不绕过。
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from jobhater.services.sources.base import HealthReport

PAGE_INTERVAL_S = 1.2  # 分页间隔：礼貌抓取
MAX_PAGES = 30
TIMEOUT_S = 15


def _ms_to_date(ts) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        s = str(ts)
        return s[:10] if len(s) >= 8 else None


# ---------- 各公司抓取器：session → list[raw dict] ----------


def fetch_mihoyo(get: Callable, post: Callable) -> list[dict[str, Any]]:
    """米哈游校招：POST ats.openout.mihoyo.com/ats-portal/v1/job/list（渠道1=校招）。"""
    url = "https://ats.openout.mihoyo.com/ats-portal/v1/job/list"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://jobs.mihoyo.com",
        "Referer": "https://jobs.mihoyo.com/",
    }
    out, page, page_size = [], 1, 50
    while page <= MAX_PAGES:
        data = post(url, json={"channelDetailIds": [1], "pageNo": page, "pageSize": page_size},
                    headers=headers).json()
        if not data.get("success"):
            break
        items = (data.get("data") or {}).get("list") or []
        if not items:
            break
        out.extend(items)
        total = (data.get("data") or {}).get("total") or 0
        if (total and len(out) >= total) or len(items) < page_size:
            break
        page += 1
        time.sleep(PAGE_INTERVAL_S)
    raw = []
    for it in out:
        jid = str(it.get("id") or "")
        addr = "、".join(
            a.get("addressDetail") or "" for a in (it.get("addressDetailList") or [])
            if isinstance(a, dict)
        )
        raw.append({
            "source_job_id": jid,
            "title": (it.get("title") or "").strip(),
            "company": "米哈游",
            "city": addr or None,
            "keywords": [p for p in (it.get("projectName"), it.get("jobNature")) if p],
            "url": f"https://jobs.mihoyo.com/#/campus/position/{jid}",
        })
    return raw


def fetch_baidu(get: Callable, post: Callable) -> list[dict[str, Any]]:
    """百度校招：先 GET 列表页拿 Cookie，再 POST getPostListNew（form）。"""
    host = "https://talent.baidu.com"
    get(f"{host}/jobs/list", headers={"Referer": host})
    url = f"{host}/httservice/getPostListNew"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{host}/jobs/list",
        "Origin": host,
    }
    out, page, page_size = [], 1, 20  # 百度接口 pageSize 上限约 20
    while page <= MAX_PAGES:
        data = post(url, data={"recruitType": "GRADUATE", "pageSize": page_size,
                               "curPage": page, "keyWord": ""},
                    headers=headers).json()
        if data.get("status") != "ok":
            break
        d = data.get("data") or {}
        items = d.get("list") or []
        if not items:
            break
        out.extend(items)
        total = int(d.get("total") or 0)
        if (total and len(out) >= total) or len(items) < page_size:
            break
        page += 1
        time.sleep(PAGE_INTERVAL_S)
    raw = []
    for it in out:
        raw.append({
            "source_job_id": str(it.get("postId") or it.get("jobId") or ""),
            "title": (it.get("name") or "").strip(),
            "company": "百度",
            "keywords": [it.get("orgName")] if it.get("orgName") else [],
            "url": f"{host}/jobs/detail?jobId={it.get('jobId', '')}",
            "published_at": _ms_to_date(it.get("publishDate")),
        })
    return raw


def fetch_netease(get: Callable, post: Callable) -> list[dict[str, Any]]:
    """网易校招：先发现全部在招项目，再逐项目翻页。新校招季免改码。"""
    import re

    nav = get(
        "https://campus.163.com/api/campuspc/project/navigation/list", timeout=TIMEOUT_S
    ).json()
    project_re = re.compile(r"https://(campus(?:\.game)?\.163\.com)/app/job/position\?id=(\d+)")
    projects: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(nodes):
        for node in nodes or []:
            m = project_re.match(node.get("link") or "")
            if m:
                key = (m.group(1), m.group(2))
                if key not in seen:
                    seen.add(key)
                    projects.append((m.group(1), m.group(2), node.get("title") or ""))
            walk(node.get("children"))

    walk(nav.get("data"))
    out = []
    for domain, project_id, project_name in projects:
        page = 1
        while page <= MAX_PAGES:
            url = (
                f"https://{domain}/api/campuspc/position/getJobList"
                f"?pageSize=20&currentPage={page}&projectId={project_id}"
            )
            try:
                data = get(url, timeout=TIMEOUT_S).json()
            except Exception:  # 单项目失败不拖垮其它项目（wenke 同款策略）
                break
            items = (data.get("data") or {}).get("list") or []
            if not items:
                break
            for it in items:
                out.append({
                    "source_job_id": str(it.get("postId") or it.get("id") or ""),
                    "title": (it.get("name") or it.get("title") or "").strip(),
                    "company": "网易",
                    "city": (it.get("workPlaceName") or "").replace("-", "") or None,
                    "keywords": [project_name] if project_name else [],
                    "url": f"https://{domain}/app/job/position?id={project_id}",
                    "published_at": _ms_to_date(it.get("publishTime") or it.get("startTime")),
                })
            if len(items) < 20:
                break
            page += 1
            time.sleep(PAGE_INTERVAL_S)
    return out


FETCHERS: dict[str, Callable[[Callable, Callable], list[dict[str, Any]]]] = {
    "米哈游": fetch_mihoyo,
    "百度": fetch_baidu,
    "网易": fetch_netease,
}


class WenkeAdapter:
    """官方公开接口聚合信源（移植自 wenke-radar，MIT）。"""

    id = "wenke"
    display_name = "官方招聘接口（wenke）"

    def __init__(self, companies: list[str] | None = None) -> None:
        self.companies = companies or list(FETCHERS)

    def capabilities(self) -> dict:
        return {"search": True, "fetch_detail": False, "needs_browser": False,
                "requires_login": False, "companies": list(FETCHERS)}

    def rate_policy(self) -> dict:
        return {"min_interval_s": PAGE_INTERVAL_S, "suggested_frequency": "daily"}

    def produce(self, query: dict | None = None) -> Iterable[dict[str, Any]]:
        """逐公司抓取；每公司的成败通过 last_fetch_report 暴露给运行器记健康。"""
        import httpx

        self.last_fetch_report: dict[str, HealthReport] = {}
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
            for name in self.companies:
                fn = FETCHERS.get(name)
                if fn is None:
                    self.last_fetch_report[name] = HealthReport(
                        ok=False, message=f"未知公司抓取器：{name}")
                    continue
                try:
                    raw = fn(client.get, client.post)
                    self.last_fetch_report[name] = HealthReport(
                        ok=True, message=f"获取 {len(raw)} 条",
                        detail={"count": len(raw)})
                    yield from raw
                except Exception as e:
                    # fail closed：登录墙/验证码/网络异常都如实暴露，绝不绕过
                    self.last_fetch_report[name] = HealthReport(
                        ok=False, message=f"{type(e).__name__}: {e}")
                    continue

    def health_check(self) -> HealthReport:
        reports = getattr(self, "last_fetch_report", {})
        if not reports:
            return HealthReport(ok=True, message="尚未运行抓取")
        ok_n = sum(1 for r in reports.values() if r.ok)
        return HealthReport(
            ok=ok_n > 0,
            message=f"{ok_n}/{len(reports)} 公司源成功",
            detail={k: v.message for k, v in reports.items()},
        )
