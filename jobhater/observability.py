"""结构化 JSON 日志地基（BC-009）。

立场与边界：
- stdlib logging + python-json-logger（社区标准 JsonFormatter），每行一个 JSON 对象；
- 唯一 sink 是 stderr（12-factor 事件流）——不落盘、不外发，维持 SECURITY.md
  「日志不写文件、无遥测」的隐私承诺；docker logs / journalctl 直接可读；
- request_id 用 contextvars（asyncio 任务上下文自动传播）：在最外层安全中间件
  （HostOriginMiddleware）生成并注入 X-Request-ID 响应头——access 行、业务行、
  安全拒绝行全部同 ID 串联，请求结束自动复位（contextvar 不跨请求泄漏）；
- 敏感键脱敏：api_key/authorization/cookie/x-api-key（大小写不敏感、精确键名匹配）
  的值一律输出 "***"——格式化层兜底，即使业务代码误把密钥塞进 extra 也不会泄漏；
- uvicorn access log 经 uvicorn_json_log_config 并入同一 JSON 流（cli.py serve 消费），
  uvicorn 默认写 stdout 的行为在本配置下统一改为 stderr。

选型依据见规格 trade-off T3：不引入 structlog（对本规模是多余 DSL），
不加文件 sink（隐私立场），日志量小无 processor 管道需求。
"""
from __future__ import annotations

import contextvars
import logging
import sys
import time
import uuid
from typing import Any

from pythonjsonlogger.json import JsonFormatter
from starlette.types import ASGIApp, Receive, Scope, Send

from jobhater.metrics import path_template_for, record_http_request

# ---------- request_id（contextvar） ----------

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jobhater_request_id", default=""
)


def get_request_id() -> str:
    """当前请求上下文的 request_id；请求外返回空串（不编造占位值）。"""
    return _request_id_var.get()


def new_request_id() -> str:
    """8 位 hex 短 ID（uuid4 截断）：本地单进程排障够用，行宽友好。"""
    return uuid.uuid4().hex[:8]


def bind_request_id() -> contextvars.Token:
    """在最外层中间件绑定新 request_id，返回 token 供请求结束复位。"""
    return _request_id_var.set(new_request_id())


def unbind_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


# ---------- 敏感键脱敏 ----------

_SENSITIVE_KEYS = frozenset({"api_key", "authorization", "cookie", "x-api-key"})
_MASK = "***"


def _mask_sensitive(log_data: dict[str, Any]) -> None:
    """精确键名匹配（大小写不敏感）：api_key_hint 这类相邻键不受误伤。"""
    for key in list(log_data):
        if str(key).lower() in _SENSITIVE_KEYS:
            log_data[key] = _MASK


# ---------- Formatter ----------


class JsonLogFormatter(JsonFormatter):
    """应用统一 JSON 行格式：{timestamp, level, event, request_id, ...extras}。"""

    def add_fields(
        self, log_data: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        _mask_sensitive(log_data)
        # message → event（事件名是本仓日志约定的一等字段，业务行以事件名检索）
        log_data["event"] = log_data.pop("message", record.getMessage())
        log_data.setdefault("level", record.levelname.lower())
        log_data["request_id"] = get_request_id()


class UvicornAccessJsonFormatter(JsonLogFormatter):
    """uvicorn access 行 JSON 化：args 五元组展开为结构化字段。

    uvicorn 以 ``logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method,
    full_path, http_version, status_code)`` 记访问日志；本 formatter 把该元组
    展开进 JSON 字段，request_id 由 contextvar 注入（uvicorn 在请求任务内
    发出 access 记录，与应用行同上下文）。
    """

    _ACCESS_ARGS_LEN = 5

    def add_fields(
        self, log_data: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        args = record.__dict__.get("args")
        unpacked: dict[str, Any] | None = None
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) == self._ACCESS_ARGS_LEN:
            client_addr, method, full_path, http_version, status_code = args
            unpacked = {
                "client_addr": client_addr,
                "method": method,
                "path": full_path,
                "http_version": http_version,
                "status_code": status_code,
            }
        super().add_fields(log_data, record, message_dict)
        if unpacked is not None:
            log_data.update(unpacked)


# ---------- 装配 ----------


def make_json_formatter() -> JsonLogFormatter:
    return JsonLogFormatter(fmt="%(message)s", timestamp=True)


def make_access_formatter() -> UvicornAccessJsonFormatter:
    return UvicornAccessJsonFormatter(fmt="%(message)s", timestamp=True)


def setup_logging(level: int = logging.INFO) -> None:
    """进程级 JSON 日志装配（幂等）：根 logger 挂 stderr JSON handler。

    等价于 ``logging.basicConfig(stream=sys.stderr)`` 换 JSON formatter，但
    显式 addHandler 以兼容 pytest 等已给根 logger 装 handler 的运行环境
    （basicConfig 在 root 已有 handler 时是 no-op）。绝不注册文件 sink。
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler.formatter, JsonLogFormatter):
            return  # 已装配（幂等）
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(make_json_formatter())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)


def uvicorn_json_log_config() -> dict[str, Any]:
    """uvicorn log_config（dictConfig 形状）：uvicorn.* 三类 logger 全部走 stderr JSON。

    覆盖 uvicorn 默认配置的两处差异：default formatter 换 JSON、access 从 stdout
    改 stderr——整个进程的日志输出收敛为单一 JSON 流。formatter 经 "()" 工厂
    引用（dictConfig 标准机制，避免在配置 dict 里内联类路径带参数的限制）。
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "jobhater.observability.make_json_formatter"},
            "access": {"()": "jobhater.observability.make_access_formatter"},
        },
        "handlers": {
            "default": {
                "formatter": "json",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }


# ---------- access 日志 + HTTP 计时中间件（与 metrics.py 共同消费计时） ----------

logger = logging.getLogger("jobhater.http")


class ObservabilityMiddleware:
    """HTTP 观测中间件：access 结构化日志行 + http_requests_total / 耗时直方图。

    装配在 HostOriginMiddleware 内层（security 保持最外层）：未过安全校验的
    请求只产生 security.reject 行、不进入本层——既避免攻击者以任意路径灌爆
    path_template 标签基数，也让拒绝语义单一归属安全层。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.perf_counter()
        status_box = {"status": 0}

        async def send_wrapped(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_box["status"] = message["status"]
            await send(message)

        status = 0
        try:
            await self.app(scope, receive, send_wrapped)
            status = status_box["status"]
        except BaseException:
            # 未处理异常由更外层 ServerErrorMiddleware 落 500；此处按 500 记观测
            status = 500
            raise
        finally:
            duration_s = time.perf_counter() - start
            template = path_template_for(scope)
            record_http_request(method, template, status, duration_s)
            logger.info(
                "http.access",
                extra={
                    "method": method,
                    "path": path,
                    "path_template": template,
                    "status_code": status,
                    "duration_ms": round(duration_s * 1000, 2),
                },
            )
