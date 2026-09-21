"""FastAPI 应用工厂：路由见 routes.py（UI/CLI/MCP 共享同一服务层）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from jobhater.db import apply_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_all()  # 启动即迁移（幂等）
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Job Hater", version="2.2.0", lifespan=lifespan)
    from jobhater.api.routes import register_routes  # 延迟导入避免环

    register_routes(app)
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
