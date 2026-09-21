"""SourceAdapter 正式契约（§5）。

所有岗位信源实现本协议；产出 raw dict 统一经 JobService.ingest 规范化入库，
单一去重/快照/FTS 路径——adapter 之间故障隔离（单源失败只记健康状态）。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class HealthReport:
    ok: bool
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    """岗位信源适配器。

    最小必须实现：id / display_name / capabilities / produce（产出原始条目）。
    拉取型源实现 produce(query)；导入型源（粘贴/文件）由用户动作直接触发，
    produce 返回空即可。健康上报由运行器统一写 job_sources 表。
    """

    id: str
    display_name: str

    def capabilities(self) -> dict[str, Any]:
        """声明能力：{search: bool, fetch_detail: bool, needs_browser: bool, ...}"""

    def rate_policy(self) -> dict[str, Any]:
        """限速策略：{min_interval_s, daily_cap, ...}；无限制返回空 dict。"""

    def produce(self, query: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
        """产出原始岗位条目（未规范化的 raw dict）。"""
        ...


class AdapterRegistry:
    """进程内注册表；按 id 取 adapter（未注册 → KeyError，如实报错）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.id in self._adapters:
            raise ValueError(f"adapter id 重复注册: {adapter.id}")
        self._adapters[adapter.id] = adapter

    def get(self, adapter_id: str) -> SourceAdapter:
        return self._adapters[adapter_id]

    def all(self) -> list[SourceAdapter]:
        return list(self._adapters.values())


REGISTRY = AdapterRegistry()
