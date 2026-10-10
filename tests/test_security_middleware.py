"""Host/Origin 安全中间件对抗性测试（BC-001 DNS rebinding + BC-002 跨站写）。

以攻击者视角固化 SEC-001/SEC-002 的闭合语义：
- 恶意 Host（rebinding 域名、后缀伪装、缺失/重复 Host 头）→ 403；
- 回环白名单任意端口/大小写/IPv6 字面量 → 放行；
- /api/* 写方法携带恶意 Origin（含 null、子域伪装、未知 scheme）→ 403；
  回环 http(s) 源、浏览器扩展源（chrome-extension/moz-extension）、
  无 Origin 的非浏览器客户端（curl/CLI/MCP/pytest）→ 放行；
- multipart 隐藏表单跨站 POST /api/backup/restore（SEC-002 攻击链）在中间件层截断。
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app
from jobhater.api.security import HostOriginMiddleware

EVIL_ORIGIN = {"Origin": "http://evil.com"}


@pytest.fixture()
def client(tmp_path):
    """干净数据目录 + 走完整中间件栈的 TestClient（conftest 已统一回环 base_url）。"""
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as c:
        yield c
    config.set_data_dir(None)


# ---------- BC-001：Host 白名单（DNS rebinding 闭合） ----------


def test_host_evil_domain_rejected(client):
    """DNS rebinding 主载荷：攻击者域名作 Host 读取本地 API → 403。"""
    r = client.get("/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 403
    assert r.json() == {"detail": "host not allowed"}


def test_host_read_path_backup_download_also_blocked(client):
    """PII 外泄路径 GET /api/backup/download 同样被 Host 校验拦截。"""
    r = client.get("/api/backup/download", headers={"Host": "evil.com"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8787",
        "127.0.0.1:9999",  # --port 自定义：端口无关
        "127.0.0.1",
        "localhost:8787",
        "localhost:1",
        "LOCALHOST:8787",  # 大小写归一化
        "[::1]:8787",  # IPv6 字面量（方括号内冒号不误切端口）
        "[::1]:65535",
        "localhost.:8787",  # DNS 末尾分隔点与裸名语义等价
    ],
)
def test_host_loopback_any_port_allowed(client, host):
    r = client.get("/api/health", headers={"Host": host})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1.evil.com",  # 后缀伪装
        "localhost.evil.com",
        "evil.com:8787",  # 端口混淆（主机名仍是 evil.com）
        "evil.127.0.0.1",
        "rebind.attacker.example",
    ],
)
def test_host_lookalike_rejected(client, host):
    r = client.get("/api/health", headers={"Host": host})
    assert r.status_code == 403


def test_rejection_logged(client, caplog):
    """拒绝事件必须留痕（security.reject），供 BC-009 结构化日志消费。"""
    with caplog.at_level(logging.WARNING, logger="jobhater.security"):
        client.get("/api/health", headers={"Host": "evil.com"})
    recs = [r for r in caplog.records if r.getMessage() == "security.reject"]
    assert recs, "security.reject 未记录"
    assert recs[0].reason == "host_not_allowed"
    assert recs[0].host == "evil.com"


# ---------- BC-001：原始 ASGI 层畸形 Host（TestClient 无法构造） ----------


def _call_middleware(headers: list[tuple[bytes, bytes]]) -> tuple[int | None, bool]:
    """直接以构造 scope 调中间件，返回 (响应状态码, 是否到达内层 app)。"""
    reached = {"inner": False}

    async def inner(scope, receive, send):
        reached["inner"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run() -> int | None:
        status: list[int] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": headers,
        }
        await HostOriginMiddleware(inner)(scope, receive, send)
        return status[0] if status else None

    return asyncio.run(run()), reached["inner"]


def test_missing_host_header_rejected():
    status, reached = _call_middleware([])
    assert status == 403
    assert reached is False


def test_duplicate_host_header_rejected():
    """双 Host 头属于歧义请求（请求走私面），一律拒绝。"""
    status, reached = _call_middleware(
        [(b"host", b"127.0.0.1:8787"), (b"host", b"evil.com")]
    )
    assert status == 403
    assert reached is False


# ---------- BC-001：白名单经环境变量追加（局域网部署口径） ----------


def test_env_extra_hosts_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHATER_ALLOWED_HOSTS", "192.168.1.50, NAS.local:8787")
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as c:
        assert c.get("/api/health", headers={"Host": "192.168.1.50:8787"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "nas.local:9000"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "evil.com"}).status_code == 403
    config.set_data_dir(None)


# ---------- BC-002：Origin 校验（跨站写闭合） ----------


def test_write_with_evil_origin_rejected(client):
    r = client.post("/api/demo/seed", headers=EVIL_ORIGIN)
    assert r.status_code == 403
    assert r.json() == {"detail": "origin not allowed"}


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8787",  # 同源回环
        "http://localhost:9999",  # 端口无关
        "http://127.0.0.1:8787",
        "https://127.0.0.1:8443",
        "http://[::1]:8787",  # IPv6 回环源
        "chrome-extension://abcdef1234567890abcdef1234567890",  # 本仓浏览器助手扩展
        "moz-extension://1234abcd-12ab-34cd-56ef-1234567890ab",
        "Chrome-Extension://ABCDEF1234567890ABCDEF",  # scheme 大小写归一化
    ],
)
def test_write_with_trusted_origin_allowed(client, origin):
    r = client.post("/api/demo/seed", headers={"Origin": origin})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.com",
        "https://localhost.evil.com",  # 子域伪装
        "https://sub.evil.com:8787",
        "null",  # 沙箱 iframe/重定向来源：一律拒绝
        "",  # 空 Origin 头
        "ftp://127.0.0.1",  # 非浏览器 http(s)/扩展 scheme
        "javascript:alert(1)",
        "http://127.0.0.1.evil.com",
    ],
)
def test_write_with_malicious_origin_rejected(client, origin):
    r = client.post("/api/demo/seed", headers={"Origin": origin})
    assert r.status_code == 403


def test_write_without_origin_allowed(client):
    """无 Origin = 非浏览器客户端（curl/CLI/MCP/pytest）→ 放行。"""
    r = client.post("/api/demo/seed")
    assert r.status_code == 200


def test_csrf_restore_multipart_attack_chain_blocked(client):
    """SEC-002 攻击链闭合验证：恶意网页隐藏表单 multipart 提交 restore → 403。"""
    r = client.post(
        "/api/backup/restore",
        files={"file": ("jobhater-backup.db", b"SQLite format 3\x00forged", "application/octet-stream")},
        headers=EVIL_ORIGIN,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "origin not allowed"}


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
def test_all_write_methods_guarded(client, method):
    r = getattr(client, method)("/api/demo/clear", headers=EVIL_ORIGIN)
    assert r.status_code == 403


def test_rejection_precedes_routing(client):
    """中间件先于路由：伪造 Origin 打未知路径也应 403 而非 404。"""
    r = client.post("/api/nonexistent-endpoint", headers=EVIL_ORIGIN)
    assert r.status_code == 403


def test_get_with_evil_origin_passes_origin_rule(client):
    """读方法不在 Origin 规则覆盖面：跨站读由 BC-001 Host 校验 + 浏览器 SOP 拦截。"""
    r = client.get("/api/health", headers=EVIL_ORIGIN)
    assert r.status_code == 200
