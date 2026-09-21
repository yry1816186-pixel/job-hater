"""仓储序列化助手：模型字段 ↔ 表列的机械映射。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from pydantic import BaseModel

# json_fields 形如 {模型字段: (表列名, 列为空时的默认值)}
JsonFieldMap = dict[str, tuple[str, Any]]


def new_id(prefix: str) -> str:
    """短 uuid（带前缀，人可读且全局唯一）。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # JSON 列损坏不静默：抛给上层决定（数据完整性优先于可用性）
        raise ValueError(f"JSON 列内容损坏: {s[:100]!r}") from None


def model_to_row(model: BaseModel, json_fields: JsonFieldMap) -> dict[str, Any]:
    """模型 → 列字典；json 字段序列化进对应列，其余直通。"""
    data = model.model_dump()
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k in json_fields:
            out[json_fields[k][0]] = dumps(v)
        else:
            out[k] = v
    return out


def insert_model(
    con: sqlite3.Connection,
    table: str,
    model: BaseModel,
    json_fields: JsonFieldMap,
    *,
    drop_none: bool = True,
) -> None:
    """把模型插入表。drop_none=True 时剔除值为 None 的列，让 DB 端 DEFAULT 生效
    （避免显式 NULL 击穿 NOT NULL DEFAULT 列）。调用方自行包裹事务。"""
    row = {k: v for k, v in model_to_row(model, json_fields).items() if v is not None or not drop_none}
    cols = ",".join(row)
    con.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def row_to_model(cls: type[BaseModel], row: sqlite3.Row, json_fields: JsonFieldMap) -> BaseModel:
    """行 → 模型；json 列反序列化到模型字段（显式默认值，无推断）。
    表中的额外列（如 search_text 等存储细节）不进入模型——模型即契约。"""
    data: dict[str, Any] = dict(row)
    for model_field, (column, default) in json_fields.items():
        if column in data:
            raw = data.pop(column)
            data[model_field] = loads(raw, default)
    allowed = set(cls.model_fields)
    return cls.model_validate({k: v for k, v in data.items() if k in allowed})
