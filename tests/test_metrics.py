"""BC-010 /metrics 最小指标集契约测试。

固化「内存累计、不外发」的本机自监控语义：
- GET /metrics（与 /api/metrics 别名）暴露 Prometheus 文本格式 0.0.4，
  含 5 个业务指标与 # TYPE 注释行；
- 端点经 BC-001/002 中间件同等防护：非回环 Host → 403；
- http_requests_total 按 path 模板（而非实际路径）打标签，防 label 基数爆炸；
- 专用 registry：不混入 prometheus-client 默认的 gc/platform 采集器；
- service 层埋点：job_ingest_total{source} / ai_provider_requests_total{provider,task,outcome}
  / wechat_scan_duration_seconds{phase} 各自真实驱动。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app
from jobhater.db import apply_all, connect
from jobhater.metrics import REGISTRY
from jobhater.services.ai import AIError, AIService
from jobhater.services.jobs import JobService

RAW_A = {
    "title": "后端开发工程师",
    "company": "指标测试科技有限公司",
    "description": "负责服务端开发与性能优化。",
}
RAW_B = {
    "title": "前端开发工程师",
    "company": "另一家网络股份有限公司",
    "description": "负责 Web 前端开发。",
}


@pytest.fixture()
def client(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as c:
        yield c
    config.set_data_dir(None)


def _counter(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------- 端点契约 ----------


def test_metrics_endpoint_exposition_format(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE" in r.text
    for metric in (
        "http_requests_total",
        "http_request_duration_seconds",
        "ai_provider_requests_total",
        "wechat_scan_duration_seconds",
        "job_ingest_total",
    ):
        assert metric in r.text, f"/metrics 缺少指标 {metric}"


def test_api_metrics_alias_same_surface(client):
    """规格 data_contract 以 /api/metrics 登记、BC-010 以 /metrics 验收：双路径同处理器。"""
    r = client.get("/api/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "http_requests_total" in r.text


def test_metrics_host_guard_rejects_evil_host(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app(), base_url="http://evil.com") as c:
        r = c.get("/metrics")
    config.set_data_dir(None)
    assert r.status_code == 403
    assert r.json() == {"detail": "host not allowed"}


def test_dedicated_registry_no_collector_pollution(client):
    """专用 CollectorRegistry：不携带 prometheus-client 默认 gc/platform 采集器。"""
    r = client.get("/metrics")
    assert "python_gc_" not in r.text
    assert "process_" not in r.text


# ---------- http 指标 ----------


def test_http_requests_total_increments_per_request(client):
    before = _counter(
        "http_requests_total", {"method": "GET", "path_template": "/api/health", "status": "200"}
    )
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
    after = _counter(
        "http_requests_total", {"method": "GET", "path_template": "/api/health", "status": "200"}
    )
    assert after - before >= 3


def test_path_label_uses_route_template_not_raw_path(client):
    """参数化路由以模板计数：任意 job_id 不产生新标签值（基数爆炸防线）。"""
    before = _counter(
        "http_requests_total",
        {"method": "GET", "path_template": "/api/jobs/{job_id}", "status": "404"},
    )
    for _ in range(3):
        client.get("/api/jobs/whatever-nonexistent-id")
    after = _counter(
        "http_requests_total",
        {"method": "GET", "path_template": "/api/jobs/{job_id}", "status": "404"},
    )
    assert after - before == 3


def test_unmatched_path_falls_back_to_bounded_label(client):
    client.get("/definitely-not-a-route")
    assert _counter(
        "http_requests_total", {"method": "GET", "path_template": "unmatched", "status": "404"}
    ) >= 1


# ---------- service 层埋点 ----------


def test_job_ingest_total_counts_added_by_source(tmp_path):
    apply_all(tmp_path / "t.db")
    con = connect(tmp_path / "t.db")
    try:
        svc = JobService(con)
        before = _counter("job_ingest_total", {"source": "metrics-test"})
        stats = svc.ingest([dict(RAW_A), dict(RAW_B)], source_id="metrics-test")
        assert stats.added == 2
        assert _counter("job_ingest_total", {"source": "metrics-test"}) - before == 2
        # 重复导入全量去重：不重复计数
        stats2 = svc.ingest([dict(RAW_A)], source_id="metrics-test")
        assert stats2.added == 0
        assert _counter("job_ingest_total", {"source": "metrics-test"}) - before == 2
    finally:
        con.close()


def test_ai_provider_requests_total_success_and_error(tmp_path, monkeypatch):
    apply_all(tmp_path / "t.db")
    con = connect(tmp_path / "t.db")
    try:
        svc = AIService(con)

        class StubProvider:
            adapter_kind = "stub_adapter"

            def complete(self, req):
                return "ok"

        monkeypatch.setattr(AIService, "active_provider", lambda self: StubProvider())
        before_ok = _counter(
            "ai_provider_requests_total",
            {"provider": "stub_adapter", "task": "job_deep_review", "outcome": "success"},
        )
        out = svc.run_task("job_deep_review", "s", "u", ack_egress=True)
        assert out["executed"] is True
        assert (
            _counter(
                "ai_provider_requests_total",
                {"provider": "stub_adapter", "task": "job_deep_review", "outcome": "success"},
            )
            - before_ok
            == 1
        )

        class BoomProvider:
            adapter_kind = "boom_adapter"

            def complete(self, req):
                raise AIError("upstream 500")

        monkeypatch.setattr(AIService, "active_provider", lambda self: BoomProvider())
        before_err = _counter(
            "ai_provider_requests_total",
            {"provider": "boom_adapter", "task": "job_deep_review", "outcome": "error"},
        )
        with pytest.raises(AIError):
            svc.run_task("job_deep_review", "s", "u", ack_egress=True)
        assert (
            _counter(
                "ai_provider_requests_total",
                {"provider": "boom_adapter", "task": "job_deep_review", "outcome": "error"},
            )
            - before_err
            == 1
        )
    finally:
        con.close()


def test_wechat_scan_phase_timer_observes_histogram():
    from jobhater.services.wechat.service import scan_phase

    before = _counter("wechat_scan_duration_seconds_count", {"phase": "parsing"})
    with scan_phase("parsing"):
        time.sleep(0.005)
    after = _counter("wechat_scan_duration_seconds_count", {"phase": "parsing"})
    assert after - before == 1
    # 失败/中断路径同样落账（finally 语义：部分耗时也可见）
    before_fail = _counter("wechat_scan_duration_seconds_count", {"phase": "decrypting"})
    with pytest.raises(RuntimeError):
        with scan_phase("decrypting"):
            raise RuntimeError("scan aborted")
    assert _counter("wechat_scan_duration_seconds_count", {"phase": "decrypting"}) - before_fail == 1
