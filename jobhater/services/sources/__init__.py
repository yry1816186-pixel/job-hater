"""信源适配器：契约 + 注册表 + 内置实现。"""
from jobhater.services.sources.base import REGISTRY, AdapterRegistry, HealthReport, SourceAdapter
from jobhater.services.sources.paste import PasteAdapter, parse_jd_text

REGISTRY.register(PasteAdapter())

__all__ = [
    "REGISTRY",
    "AdapterRegistry",
    "HealthReport",
    "SourceAdapter",
    "PasteAdapter",
    "parse_jd_text",
]
