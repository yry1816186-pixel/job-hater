"""FastAPI 应用工厂：路由见 routes.py（UI/CLI/MCP 共享同一服务层）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from jobhater.db import apply_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_all()  # 启动即迁移（幂等）
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Job Hater", version="2.0.0a1", lifespan=lifespan)
    from jobhater.api.routes import register_routes  # 延迟导入避免环

    register_routes(app)
    return app


app = create_app()
