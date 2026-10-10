"""FastAPI 应用工厂：路由按资源分模块注册（UI/CLI/MCP 共享同一服务层）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from jobhater.api.security import HostOriginMiddleware
from jobhater.db import apply_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_all()  # 启动即迁移（幂等）
    from jobhater.services.wechat.service import WeChatService

    # ARCH-002：WeChatService 生命周期挂 app.state——lifespan 启动时创建、
    # 关闭时 stop_scan 收尾后台线程；路由处理器经 request.app.state.wechat 访问，
    # 不再在路由注册期构造进程级单例。
    app.state.wechat = WeChatService()
    try:
        yield
    finally:
        app.state.wechat.stop_scan()


def create_app() -> FastAPI:
    app = FastAPI(title="Job Hater", version="3.1.0", lifespan=lifespan)
    app.add_middleware(HostOriginMiddleware)  # BC-001/002：Host/Origin 安全校验最先装配
    from jobhater.api.routes_ai import register as register_ai  # 延迟导入避免环
    from jobhater.api.routes_core import register as register_core
    from jobhater.api.routes_jobs import register as register_jobs
    from jobhater.api.routes_lifecycle import register as register_lifecycle
    from jobhater.api.routes_profiles import register as register_profiles
    from jobhater.api.routes_wechat import register as register_wechat

    # BC-007：巨石 routes.py 按资源拆 6 模块，各模块 register(app) 顺序注册
    # （端点 URL/方法/schema/错误语义纯搬移，OpenAPI 快照前后零差异）
    register_core(app)
    register_profiles(app)
    register_jobs(app)
    register_lifecycle(app)
    register_ai(app)
    register_wechat(app)
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """挂载前端构建产物（frontend/dist）。存在时单进程即可用：API + UI 同源。"""
    dist = (Path(__file__).resolve().parent.parent.parent / "frontend" / "dist").resolve()
    index = dist / "index.html"
    if not index.exists():
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # 路径穿越防护：解析后必须仍在 dist 内（含 %2e%2e 编码绕过变体）
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and target.is_relative_to(dist):
            return FileResponse(target)
        if not target.is_relative_to(dist):
            raise HTTPException(404, "not found")
        return FileResponse(index)


app = create_app()
