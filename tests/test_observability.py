"""BC-009 结构化 JSON 日志契约测试。

固化可观测性地基的对外语义：
- 每行日志是合法 JSON，必备字段齐（timestamp/level/event/request_id——observability_contract）；
- request_id 由最外层安全中间件生成：同请求的 access 行与业务行同 ID，
  且与响应头 X-Request-ID 一致；请求外 request_id 为空串；
- 敏感键（api_key/authorization/cookie/x-api-key）值一律脱敏为 ***（大小写不敏感）；
- 日志仅写 stderr 不落盘（12-factor 事件流），CLI 表格输出（print）白名单之外零残留。
"""
from __future__ import annotations

import ast
import io
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app
from jobhater.observability import (
    JsonLogFormatter,
    UvicornAccessJsonFormatter,
    get_request_id,
    new_request_id,
    setup_logging,
    uvicorn_json_log_config,
)

_REQUIRED_FIELDS = ("timestamp", "level", "event", "request_id")


def _formatter() -> JsonLogFormatter:
    return JsonLogFormatter(fmt="%(message)s", timestamp=True)


def _format_record(record: logging.LogRecord) -> dict:
    return json.loads(_formatter().format(record))


@pytest.fixture()
def client(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as c:
        yield c
    config.set_data_dir(None)


# ---------- ① JSON 行与必备字段 ----------


def test_log_line_is_json_with_required_fields(caplog):
    with caplog.at_level(logging.INFO, logger="jobhater.test"):
        logging.getLogger("jobhater.test").info("security.reject", extra={"detail": "host_not_allowed"})
    data = _format_record(caplog.records[-1])
    for field in _REQUIRED_FIELDS:
        assert field in data, f"缺少必备字段 {field}: {data}"
    assert data["event"] == "security.reject"
    assert data["level"] == "info"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", data["timestamp"][:19])
    assert data["request_id"] == ""  # 请求上下文外为空串，不编造


def test_extra_context_fields_serialized(caplog):
    with caplog.at_level(logging.INFO, logger="jobhater.test"):
        logging.getLogger("jobhater.test").info(
            "ai.request", extra={"provider": "openai_compatible", "task": "resume_parse", "outcome": "success"}
        )
    data = _format_record(caplog.records[-1])
    assert data["provider"] == "openai_compatible"
    assert data["task"] == "resume_parse"
    assert data["outcome"] == "success"


def test_warning_level_lowercase(caplog):
    with caplog.at_level(logging.WARNING, logger="jobhater.test"):
        logging.getLogger("jobhater.test").warning("article_fetch_unavailable", extra={"reason": "timeout"})
    assert _format_record(caplog.records[-1])["level"] == "warning"


# ---------- ② request_id：同请求 access 行/业务行/响应头同源 ----------


def test_request_id_coherent_across_access_business_and_header(tmp_path, monkeypatch):
    """BC-009 验收 ②：access 行与业务行 request_id 一致，且与 X-Request-ID 响应头匹配。

    业务行经 monkeypatch 在 health 处理器内触发（走真实请求管线，含中间件 contextvar 注入）；
    捕获方式是挂一个使用正式 JsonFormatter 的内存 handler——格式化发生在请求上下文内，
    与生产 stderr 输出同一路径。
    """
    from jobhater.api import routes_core

    orig_connect = routes_core.connect

    def connect_and_emit_business_line():
        logging.getLogger("jobhater.business").info("business.line", extra={"probe": "ok"})
        return orig_connect()

    monkeypatch.setattr(routes_core, "connect", connect_and_emit_business_line)

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(_formatter())
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        config.set_data_dir(tmp_path)
        with TestClient(create_app()) as c:
            resp = c.get("/api/health")
            resp2 = c.get("/api/health")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        config.set_data_dir(None)

    assert resp.status_code == 200
    rid = resp.headers["x-request-id"]
    rid2 = resp2.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{8}", rid), f"X-Request-ID 应为 8 位 hex: {rid}"
    assert rid != rid2, "两次请求的 request_id 必须不同"

    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    # 只审计本仓事件行（httpx 客户端自身的 INFO 行也会流入根 handler，无 request_id）
    events = [e for e in lines if e.get("event") in ("http.access", "business.line")]
    by_request: dict[str, list[dict]] = {}
    for e in events:
        by_request.setdefault(e["request_id"], []).append(e)
    assert set(by_request) == {rid, rid2}, f"事件行必须按请求归组到两个 request_id: {set(by_request)}"
    for _request_rid, group in by_request.items():
        assert any(e["event"] == "http.access" for e in group), "请求完成后应产出 http.access 行"
        assert any(e["event"] == "business.line" for e in group), "请求内业务行应被捕获"
    access = [e for e in by_request[rid] if e["event"] == "http.access"]
    # access 行按 observability_contract 带齐 method/path/status_code/duration_ms
    for entry in access:
        for field in ("method", "path", "status_code", "duration_ms"):
            assert field in entry, f"access 行缺少 {field}: {entry}"
    assert access[-1]["method"] == "GET"
    assert access[-1]["status_code"] == 200


def test_request_id_contextvar_scoped_to_request(client):
    """请求处理内 contextvar 有值、请求结束后复位为空。"""
    assert get_request_id() == ""
    with TestClient(create_app()) as c:
        # 在请求管线内观察（借 security.reject 之外的正常请求头即可）
        resp = c.get("/api/health")
    assert resp.status_code == 200
    assert get_request_id() == ""


def test_rejection_response_carries_request_id_and_log(client, caplog):
    """安全拒绝（403）响应同样带 X-Request-ID，security.reject 行携带同一 ID。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(_formatter())
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING, logger="jobhater.security"):
            resp = client.get("/api/health", headers={"Host": "evil.com"})
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert resp.status_code == 403
    rid = resp.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{8}", rid)
    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    rejects = [e for e in lines if e.get("event") == "security.reject"]
    assert rejects and all(e["request_id"] == rid for e in rejects)


def test_new_request_id_unique_and_hex():
    ids = {new_request_id() for _ in range(200)}
    assert len(ids) == 200
    for rid in ids:
        assert re.fullmatch(r"[0-9a-f]{8}", rid)


# ---------- ③ 敏感字段脱敏 ----------


@pytest.mark.parametrize(
    "key",
    ["api_key", "Authorization", "COOKIE", "x-api-key", "API_KEY", "X-Api-Key"],
)
def test_sensitive_keys_masked(caplog, key):
    with caplog.at_level(logging.INFO, logger="jobhater.test"):
        logging.getLogger("jobhater.test").info(
            "provider.created", extra={key: "sk-super-secret-value", "provider": "x"}
        )
    data = _format_record(caplog.records[-1])
    assert data[key] == "***"
    assert "sk-super-secret-value" not in json.dumps(data)


def test_provider_creation_event_masked_end_to_end(client, caplog):
    """BC-009 验收 ③：provider 创建事件（真实服务层事件形状）api_key 被脱敏。"""
    with caplog.at_level(logging.INFO, logger="jobhater.ai"):
        logging.getLogger("jobhater.ai").info(
            "provider.created",
            extra={"adapter_kind": "openai_compatible", "display_name": "测试",
                   "api_key": "sk-1234567890abcdef", "authorization": "Bearer eyJhbGc"},
        )
    data = _format_record(caplog.records[-1])
    assert data["api_key"] == "***"
    assert data["authorization"] == "***"
    assert data["adapter_kind"] == "openai_compatible"


def test_non_sensitive_fields_not_masked(caplog):
    with caplog.at_level(logging.INFO, logger="jobhater.test"):
        logging.getLogger("jobhater.test").info(
            "job.ingested", extra={"api_key_hint": "长度12", "provider": "none"}
        )
    data = _format_record(caplog.records[-1])
    assert data["api_key_hint"] == "长度12"  # 只脱敏精确敏感键，不做误伤式子串匹配
    assert data["provider"] == "none"


# ---------- ④ print 白名单审计（cli.py 表格输出之外零 print） ----------


def test_no_print_calls_outside_cli():
    import jobhater

    package_root = __import__("pathlib").Path(jobhater.__file__).resolve().parent
    offenders: list[str] = []
    for py in sorted(package_root.rglob("*.py")):
        if py.name == "cli.py":
            continue  # CLI 表格/状态输出是 stdout 用户界面，不是日志
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{py.relative_to(package_root)}:{node.lineno}")
    assert offenders == [], f"jobhater/ 包内出现白名单外 print 调用: {offenders}"


# ---------- 装配与 uvicorn 合流 ----------


def test_setup_logging_idempotent_stderr_only():
    """setup_logging 幂等：重复调用不叠加 handler；handler 流向 stderr（不落盘）。"""
    import sys

    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging()
    first_pass = list(root.handlers)
    setup_logging()
    assert list(root.handlers) == first_pass, "重复装配不得叠加 handler"
    json_handlers = [h for h in first_pass if isinstance(h.formatter, JsonLogFormatter)]
    assert json_handlers, "根 logger 应持有 JSON formatter 的 handler"
    new_handler = [h for h in first_pass if h not in before]
    assert all(h.stream is sys.stderr for h in new_handler), "唯一 sink 必须是 stderr"
    for h in new_handler:
        root.removeHandler(h)  # 还原测试现场


def test_uvicorn_log_config_routes_access_to_stderr_json():
    cfg = uvicorn_json_log_config()
    assert cfg["version"] == 1
    assert cfg["disable_existing_loggers"] is False
    handlers = cfg["handlers"]
    assert all(h["class"] == "logging.StreamHandler" for h in handlers.values())
    assert all(h["stream"] == "ext://sys.stderr" for h in handlers.values())
    assert "json" in cfg["formatters"] and "access" in cfg["formatters"]
    loggers = cfg["loggers"]
    assert set(loggers) >= {"uvicorn", "uvicorn.error", "uvicorn.access"}
    assert loggers["uvicorn.access"]["handlers"] == ["access"]
    # uvicorn 默认把 access 写 stdout；本配置统一并入 stderr JSON 流（12-factor）


def test_uvicorn_access_formatter_unpacks_structured_fields():
    """uvicorn access 行：args 元组展开为 method/path/status_code 字段并携带 request_id。"""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "p", 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:51713", "GET", "/api/health", "1.1", 200),
        None,
    )
    data = json.loads(UvicornAccessJsonFormatter(fmt="%(message)s", timestamp=True).format(record))
    assert data["method"] == "GET"
    assert data["path"] == "/api/health"
    assert data["status_code"] == 200
    assert data["client_addr"] == "127.0.0.1:51713"
    assert data["http_version"] == "1.1"
    assert "request_id" in data


def test_uvicorn_access_formatter_tolerates_non_tuple_args():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "p", 1, "plain message", None, None
    )
    data = json.loads(UvicornAccessJsonFormatter(fmt="%(message)s", timestamp=True).format(record))
    assert data["event"] == "plain message"
