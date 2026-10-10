"""进程内 Prometheus 最小指标集（BC-010）。

立场与边界：
- prometheus-client 官方实现，专用 CollectorRegistry（不挂在默认 registry 上，
  避免 gc/platform 采集器混入——保持「最小 5 指标」的产品口径）；
- 内存累计、不持久化、不外发：/metrics 端点仅本机回环 Host 可读（BC-001/002
  中间件同等防护），是本机自监控，不改变「无遥测上报」立场；
- http_requests_total / http_request_duration_seconds 由 ObservabilityMiddleware
  在请求完成时记录；path 用路由模板（``/api/jobs/{job_id}``）而非实际路径作标签，
  避免 label 基数爆炸；ai/wechat/ingest 三类由对应 service 层埋点。

指标清单（observability_contract.key_metrics）：
- http_requests_total{method, path_template, status}          —— HTTP 请求计数
- http_request_duration_seconds (histogram)                   —— HTTP 耗时分布
- ai_provider_requests_total{provider, task, outcome}         —— AI Provider 调用
- wechat_scan_duration_seconds{phase} (histogram)             —— 微信扫描各阶段耗时
- job_ingest_total{source}                                    —— 入库岗位计数（去重后新增）
"""
from __future__ import annotations

from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.exposition import CONTENT_TYPE_PLAIN_0_0_4

REGISTRY = CollectorRegistry()

# 规格 observability_contract 锁定经典 0.0.4 文本格式（prometheus-client ≥0.26 的
# CONTENT_TYPE_LATEST 已指向新版 1.0.0 格式，显式取 0.0.4 常量保持兼容面稳定）
METRICS_CONTENT_TYPE = CONTENT_TYPE_PLAIN_0_0_4

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "path_template", "status"],
    registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    registry=REGISTRY,
)
ai_provider_requests_total = Counter(
    "ai_provider_requests_total",
    "AI Provider 调用总数",
    ["provider", "task", "outcome"],
    registry=REGISTRY,
)
wechat_scan_duration_seconds = Histogram(
    "wechat_scan_duration_seconds",
    "微信扫描各阶段耗时（秒）",
    ["phase"],
    registry=REGISTRY,
)
job_ingest_total = Counter(
    "job_ingest_total",
    "入库岗位总数（去重后新增）",
    ["source"],
    registry=REGISTRY,
)


def path_template_for(scope: dict[str, Any]) -> str:
    """ASGI scope → 路由模板标签。

    Starlette 在路由命中后把 route 对象写回 scope（``scope["route"].path`` 即
    ``/api/jobs/{job_id}`` 模板）；未命中任何路由（无 SPA 兜底的 404）返回
    有界常量 "unmatched"，保证标签值不随请求路径发散。
    """
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return "unmatched"


def record_http_request(method: str, path_template: str, status: int, duration_s: float) -> None:
    """单次 HTTP 请求观测落账（中间件在响应完成后调用；线程安全）。"""
    http_requests_total.labels(
        method=method, path_template=path_template, status=str(status)
    ).inc()
    http_request_duration_seconds.observe(duration_s)


def prometheus_text() -> bytes:
    """Prometheus 文本暴露格式（version=0.0.4）快照。"""
    return generate_latest(REGISTRY)
