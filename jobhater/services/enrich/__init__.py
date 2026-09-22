"""JD 补全子系统（v4.0 B1）：URL → 官方接口 → JD 原文落库。"""
from jobhater.services.enrich.engine import EnrichmentEngine
from jobhater.services.enrich.providers import PROVIDERS, provider_for_url

__all__ = ["EnrichmentEngine", "PROVIDERS", "provider_for_url"]
