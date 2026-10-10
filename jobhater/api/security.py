"""纯 ASGI Host/Origin 安全中间件（BC-001 DNS rebinding + BC-002 跨站写）。

闭合 Phase 1 诊断两条 high 级安全问题：
- SEC-001（CWE-350/306）：本地 API 无 Host 校验，攻击者域名重绑定到 127.0.0.1
  即可绕过浏览器同源策略整库读取 PII、盗用 keyring 中的 AI key。
  → 全部 HTTP 请求的 Host 主机名必须 ∈ 回环白名单（端口无关，兼容
  ``job-hater serve --port`` 自定义部署）；缺失/重复 Host 头同样拒绝。
- SEC-002（CWE-352）：multipart/无体 POST 端点（如 /api/backup/restore 原位
  覆盖本地库）可被恶意网页以 simple request 跨站直接触发。→ ``/api/*`` 写方法
  （POST/PATCH/PUT/DELETE）携带 Origin 时仅放行回环 http(s) 源与浏览器扩展源
  （chrome-extension/moz-extension：本仓扩展经 manifest host_permissions 仅通信
  127.0.0.1/localhost，残留风险=用户自装恶意扩展，属其既有 localhost 权限面，
  见 SECURITY.md）；无 Origin 的非浏览器客户端（curl/CLI/MCP/pytest）放行——
  跨站写必带 Origin，跨站读已由 Host 校验 + 浏览器 SOP 拦截。

白名单追加：环境变量 ``JOBHATER_ALLOWED_HOSTS`` 逗号分隔（局域网部署用户自担，
README/SECURITY.md 说明）。实现参照 Starlette TrustedHostMiddleware 的 Host 白名单
与 OWASP CSRF Prevention Cheat Sheet 的 Origin 校验，按本产品端口无关 loopback
语义适配；仅用标准库，不引入新依赖。
"""
from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from jobhater.observability import bind_request_id, get_request_id, unbind_request_id

logger = logging.getLogger("jobhater.security")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ALLOWED_HOSTS_ENV = "JOBHATER_ALLOWED_HOSTS"
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_EXTENSION_SCHEMES = frozenset({"chrome-extension", "moz-extension"})


def _host_only(host: str) -> str:
    """Host 头取主机名：小写、剥端口、剥末尾 DNS 分隔点；IPv6 字面量 ``[::1]:8787``
    方括号内冒号不参与端口切分。尾点（``localhost.``）与裸名语义等价，归一化剥除，
    防御对精确匹配白名单的尾点绕过变体。
    """
    h = host.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end != -1 else h
    hostname = h.rsplit(":", 1)[0] if ":" in h else h
    return hostname.rstrip(".")


def _allowed_hosts() -> frozenset[str]:
    """回环白名单 ∪ 环境变量追加项（每项同样经主机名归一化）。"""
    extra = os.environ.get(_ALLOWED_HOSTS_ENV, "")
    return _LOOPBACK_HOSTS | {_host_only(h) for h in extra.split(",") if h.strip()}


def _origin_allowed(origin: str, allowed_hosts: frozenset[str]) -> bool:
    """Origin 放行判定：回环 http(s) 源（端口任意）或浏览器扩展源；其余（含 null）拒绝。"""
    parts = urlsplit(origin.strip())
    scheme = parts.scheme.lower()
    if scheme in _EXTENSION_SCHEMES:
        return True
    if scheme in ("http", "https"):
        return parts.hostname in allowed_hosts
    return False


async def _forbidden(send: Send, detail: str, **context: str) -> None:
    logger.warning("security.reject", extra={"detail": detail, **context})
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HostOriginMiddleware:
    """Host/Origin 双校验（create_app 最先装配，全部入站 HTTP 请求先行过检）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.allowed_hosts = _allowed_hosts()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # BC-009：request_id 在最外层（本中间件）生成——安全拒绝行、access 行、
        # 业务行全部同 ID 串联；经 contextvar 向内传播，随 X-Request-ID 响应头外显。
        token = bind_request_id()

        async def send_with_request_id(message: Send) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", get_request_id().encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self._check_and_forward(scope, receive, send_with_request_id)
        finally:
            unbind_request_id(token)

    async def _check_and_forward(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        headers = scope.get("headers") or []
        method = scope.get("method", "")
        path = scope.get("path", "")
        hosts = [v.decode("latin-1") for k, v in headers if k == b"host"]
        if len(hosts) != 1 or _host_only(hosts[0]) not in self.allowed_hosts:
            await _forbidden(
                send,
                "host not allowed",
                reason="host_not_allowed",
                host=hosts[0] if hosts else "",
                method=method,
                path=path,
            )
            return
        if method.upper() in _WRITE_METHODS and path.startswith("/api/"):
            origins = [v.decode("latin-1") for k, v in headers if k == b"origin"]
            bad = [o for o in origins if not _origin_allowed(o, self.allowed_hosts)]
            if bad:
                await _forbidden(
                    send,
                    "origin not allowed",
                    reason="origin_not_allowed",
                    origin=bad[0],
                    method=method,
                    path=path,
                )
                return
        await self.app(scope, receive, send)
