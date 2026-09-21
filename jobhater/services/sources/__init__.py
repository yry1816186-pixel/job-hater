"""信源适配器：契约 + 注册表 + 内置实现。"""
from jobhater.services.sources.base import REGISTRY, AdapterRegistry, HealthReport, SourceAdapter
from jobhater.services.sources.paste import PasteAdapter, parse_jd_text
from jobhater.services.sources.wenke import WenkeAdapter

REGISTRY.register(PasteAdapter())
REGISTRY.register(WenkeAdapter())

__all__ = [
    "REGISTRY",
    "AdapterRegistry",
    "HealthReport",
    "SourceAdapter",
    "PasteAdapter",
    "WenkeAdapter",
    "parse_jd_text",
]
