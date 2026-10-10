"""微信本地数据源端点（环境检测/扫描/结果/导入/清除）。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from jobhater.api.deps import get_con
from jobhater.api.schemas import WeChatImportIn, WeChatScanIn
from jobhater.services.jobs import JobService


def register(app: FastAPI) -> None:
    from jobhater.services.wechat.detect import detect as detect_env
    from jobhater.services.wechat.service import WeChatService

    _wx = WeChatService()  # 进程级单例：持有后台扫描线程与状态

    @app.get("/api/wechat/env")
    def wechat_env():
        """环境检测：微信安装/账号数据/进程状态 + 阻塞项指引（只读、秒级）。"""
        env = detect_env()
        return {
            "platform_ok": env.platform_ok,
            "installed": env.installed,
            "version": env.version,
            "data_root": str(env.data_root) if env.data_root else None,
            "data_root_source": env.data_root_source,
            "accounts": [
                {
                    "wxid": a.wxid,
                    "message_dbs": len(a.message_dbs),
                    "total_db_mb": round(a.total_db_bytes / 1e6, 1),
                }
                for a in env.accounts
            ],
            "weixin_running": bool(env.weixin_pids),
            "blockers": env.blockers,
        }

    @app.post("/api/wechat/scan")
    def wechat_scan(inp: WeChatScanIn):
        """启动后台全流程扫描（检测→密钥→解密→解析→识别）。幂等：已在跑返回状态。"""
        return _wx.start_scan(min_confidence=inp.min_confidence)

    @app.get("/api/wechat/status")
    def wechat_status():
        return _wx.status()

    @app.post("/api/wechat/stop")
    def wechat_stop():
        _wx.stop_scan()
        return _wx.status()

    @app.get("/api/wechat/results")
    def wechat_results(
        cohort: int | None = None,
        kind: str | None = None,
        min_confidence: float = 0.0,
        q: str | None = None,
    ):
        """读取上次扫描结果（可按届别/类型/置信度/关键词过滤，前端本地翻页）。"""
        res = _wx.results()
        if res is None:
            return {"hits": [], "stats": None, "generated_at": None}
        hits = res.get("hits", [])
        if cohort is not None:
            hits = [h for h in hits if h.get("cohort") == cohort]
        if kind:
            hits = [h for h in hits if h.get("kind") == kind]
        if min_confidence:
            hits = [h for h in hits if h.get("confidence", 0) >= min_confidence]
        if q:
            ql = q.lower()
            hits = [
                h
                for h in hits
                if ql in (h.get("title") or "").lower()
                or ql in (h.get("company") or "").lower()
                or ql in (h.get("source_text") or "").lower()
                or ql in (h.get("talker_name") or "").lower()
            ]
        return {
            "hits": hits,
            "stats": res.get("stats"),
            "generated_at": res.get("generated_at"),
            "total": len(res.get("hits", [])),
        }

    @app.post("/api/wechat/import")
    def wechat_import(inp: WeChatImportIn, con=Depends(get_con)):
        """把选中命中导入岗位库（走统一 ingest 链：去重/规范化/FTS）。"""
        res = _wx.results()
        if res is None:
            raise HTTPException(404, "尚无扫描结果，请先扫描")
        hits = res.get("hits", [])
        if inp.cohort is not None:
            hits = [h for h in hits if h.get("cohort") == inp.cohort]
        hits = [h for h in hits if h.get("confidence", 0) >= inp.min_confidence]
        if inp.indices:
            wanted = set(inp.indices)
            hits = [h for i, h in enumerate(hits) if i in wanted]
        raw_jobs = []
        for h in hits:
            url = None
            am = h.get("apply_method") or ""
            if am.startswith("链接 "):
                url = am[3:].strip() or None
            raw_jobs.append(
                {
                    "title": h.get("title") or f"{(h.get('company') or '未知公司')}招聘（微信）",
                    "company": h.get("company") or h.get("talker_name") or "未知公司",
                    "city": (h.get("cities") or [None])[0],
                    "salary": h.get("salary"),
                    "education": h.get("education"),
                    "description": h.get("source_text"),
                    "url": url,
                    "deadline": h.get("deadline"),
                    "graduation_year": h.get("cohort"),
                }
            )
        if not raw_jobs:
            return {"added": 0, "deduped_exact": 0, "deduped_near": 0, "enriched": 0, "rejected": 0, "message": "没有符合条件的命中"}
        stats = JobService(con).ingest(raw_jobs, source_id="wechat")
        out = {
            "added": stats.added, "deduped_exact": stats.deduped_exact,
            "deduped_near": stats.deduped_near, "enriched": stats.enriched,
            "rejected": stats.rejected,
        }
        out["message"] = f"新增 {stats.added}，去重跳过 {stats.deduped_exact + stats.deduped_near}"
        return out

    @app.delete("/api/wechat/data")
    def wechat_purge():
        """清除解密产物与结果缓存（不动微信本体数据）。"""
        _wx.purge()
        return {"ok": True}
