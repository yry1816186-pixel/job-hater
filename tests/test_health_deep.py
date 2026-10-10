"""/api/health 深度探活测试（BC-008）。

健康语义从静态 {ok, version} 深化为三项真实检查（compose healthcheck /
前端错误横幅消费）：db 连通 + SELECT 1、schema 版本与代码一致、data_dir 可写；
任一失败 → 503 + checks 明细，全部通过 → 200。单次检查预算 < 50ms。
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app
from jobhater.db import connect


@pytest.fixture()
def client(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as c:  # lifespan: 启动即迁移
        yield c
    config.set_data_dir(None)


def test_health_ok_with_three_checks(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"]
    assert body["checks"] == {"db": "ok", "schema": "ok", "data_dir": "ok"}


def test_health_db_failure_returns_503(client, monkeypatch):
    def _broken_connect(path=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("jobhater.api.routes.connect", _broken_connect)
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["checks"]["db"] == "fail"
    assert "detail" not in body  # 顶层契约体，而非 FastException detail 包装


def test_health_schema_drift_returns_503(client):
    con = connect()
    try:
        con.execute(
            "DELETE FROM schema_migrations WHERE version ="
            " (SELECT MAX(version) FROM schema_migrations)"
        )
    finally:
        con.close()
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["checks"]["schema"] == "fail"
    assert body["checks"]["db"] == "ok"  # 连通性不受 schema 漂移影响


def test_health_data_dir_not_writable_returns_503(client, monkeypatch):
    monkeypatch.setattr("jobhater.api.routes.os.access", lambda p, mode: False)
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["checks"]["data_dir"] == "fail"
    assert body["checks"]["db"] == "ok"


def test_health_check_budget_under_50ms(client):
    client.get("/api/health")  # 预热（线程池/文件缓存），预算针对稳态单次检查
    t0 = time.perf_counter()
    r = client.get("/api/health")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 50, f"探活耗时 {elapsed_ms:.1f}ms 超预算"
