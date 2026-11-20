"""FastAPI 应用工厂：路由见 routes.py（UI/CLI/MCP 共享同一服务层）。"""
from __future__ import annotations

import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from jobhater import __version__
from jobhater.db import apply_all

logger = logging.getLogger("jobhater")

# 前端未构建时的引导页（200 返回，替代此前的静默 404）。
_GUIDE_HTML = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Hater · 前端未构建</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#222;line-height:1.7}}
code,pre{{background:#f5f5f5;border-radius:6px}}pre{{padding:12px;overflow-x:auto}}
h1{{font-size:1.5rem}}.ok{{color:#0a7d32;font-weight:600}}
</style></head><body>
<h1>API 服务运行正常<span class="ok">（v{__version__}）</span>，但 Web 界面尚未构建</h1>
<p>后端与命令行功能可用；浏览器界面需要先构建前端（一次性，约 1-2 分钟）：</p>
<pre>cd frontend
npm install
npm run build</pre>
<p>或者改用 Docker 部署（自动构建前端，无需本机 Node）：</p>
<pre>docker compose up --build</pre>
<p>构建完成后刷新本页即可。详见仓库 README「快速开始」。</p>
</body></html>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_all()  # 启动即迁移（幂等）
    yield


# ---------- 本机 API 安全线（v3.6） ----------

# Host 白名单默认值：回环 + testserver（Starlette TestClient 约定）。
_DEFAULT_HOSTS = ["127.0.0.1", "localhost", "*.localhost", "[::1]", "testserver"]
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def _hostname_of(host_header: str) -> str:
    """从 Host 头（可能带端口/IPv6 方括号）提取小写主机名；非法返回空串。"""
    try:
        return (urlsplit("//" + host_header).hostname or "").lower()
    except ValueError:
        return ""


def _host_allowed(host_header: str, allowed: list[str]) -> bool:
    hostname = _hostname_of(host_header)
    if not hostname:
        return False
    # 私网/环回 IP 字面量默认放行：局域网设备用 IP 直连开箱即用。
    # DNS rebinding 的攻击面是域名 Host（恶意域名的 Host 头是域名本身），
    # IP 字面量不构成该威胁——域名仍须白名单。
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local):
        return True
    for pat in allowed:
        pat = pat.lower().strip("[]")
        if pat.startswith("*."):
            suffix = pat[1:]  # 如 ".localhost"
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
        elif pat in (hostname, host_header.lower()):
            return True
    return False


def _origin_allowed(origin: str, host_header: str) -> bool:
    """写请求的 Origin 白名单：本机页面（任意端口，含 vite 开发代理）、
    浏览器扩展（chrome/moz-extension），以及与已过白名单 Host 同名的同源部署。"""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme in ("chrome-extension", "moz-extension"):
        return True
    host = (parts.hostname or "").lower()
    if host in _LOOPBACK_NAMES:
        return True
    # 局域网/自定义域名部署：Origin 主机 == 请求 Host 主机（Host 已过白名单）即视为同源
    return bool(host) and host == _hostname_of(host_header)


class _LocalOnlyMiddleware(BaseHTTPMiddleware):
    """两道闸（默认只服务本机，见 SECURITY.md）：

    1. Host 白名单——封 DNS rebinding：恶意域名 A 记录指向 127.0.0.1 后，
       浏览器同源读 /api/backup/download 拿全库 PII 的路径被 Host 校验拦断；
    2. Origin 白名单——封跨源简单请求写：任意网页 POST /api/demo/seed、
       /api/inbox/scan 等无 body/简单 body 端点（无预检）的触发被拒绝。
    """

    def __init__(self, app, allowed_hosts: list[str]):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "")
        if not _host_allowed(host, self.allowed_hosts):
            logger.warning("拒绝非白名单 Host 请求：%r", host)
            return JSONResponse(
                {"detail": f"拒绝访问：Host {host} 不在白名单。"
                           f"局域网/自定义域名部署请设置 JOBHATER_ALLOWED_HOSTS"},
                status_code=400,
            )
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin != "null"
            and not _origin_allowed(origin, host)
        ):
            logger.warning("拒绝跨源写请求：origin=%r path=%r", origin, request.url.path)
            return JSONResponse(
                {"detail": "跨源请求被拒绝：仅允许本机页面与浏览器扩展调用 API"},
                status_code=403,
            )
        return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="Job Hater", version=__version__, lifespan=lifespan)
    from jobhater.api.routes import register_routes  # 延迟导入避免环

    # JOBHATER_GUARD=0 一键关闭 Host/Origin 安全线（仅当有外层防护/反向代理时使用）。
    guard = os.environ.get("JOBHATER_GUARD", "1").strip().lower() not in ("", "0", "false", "off", "no")
    if guard:
        extra_hosts = [
            h.strip() for h in os.environ.get("JOBHATER_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
        app.add_middleware(_LocalOnlyMiddleware, allowed_hosts=[*_DEFAULT_HOSTS, *extra_hosts])
    else:
        logger.warning("JOBHATER_GUARD=0：Host/Origin 安全线已关闭——仅当有外层防护时这样部署")
    register_routes(app)
    _mount_spa(app)
    return app


def _resolve_dist() -> Path | None:
    """定位前端构建产物。

    顺序：env JOBHATER_DIST（Docker 镜像/自定义布局显式指定）
    → 仓库源码布局 frontend/dist（pip install -e 开发场景）。
    找不到返回 None（由 _mount_spa 挂引导页并打 WARNING，绝不静默）。
    """
    env = os.environ.get("JOBHATER_DIST")
    if env:
        p = Path(env)
        if (p / "index.html").exists():
            return p
        logger.warning("JOBHATER_DIST=%s 下未找到 index.html，Web 界面不可用", env)
        return None
    repo = (Path(__file__).resolve().parent.parent.parent / "frontend" / "dist").resolve()
    if (repo / "index.html").exists():
        return repo
    logger.warning(
        "未找到前端构建产物（frontend/dist）：Web 界面不可用，仅 API/CLI 可用。"
        "构建方法见 README「快速开始」，或设置 JOBHATER_DIST 指向已有 dist 目录。"
    )
    return None


def _mount_spa(app: FastAPI) -> None:
    """挂载前端构建产物（frontend/dist）。存在时单进程即可用：API + UI 同源。"""
    dist = _resolve_dist()
    if dist is None:
        @app.get("/{full_path:path}", include_in_schema=False)
        def guide(full_path: str):
            # API 路由已先注册（精确匹配优先）；落到这里的 GET 一律给引导页。
            return HTMLResponse(_GUIDE_HTML)

        return
    index = dist / "index.html"
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
