#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""store.py — 统一本地JSON存储层（Campus-Job-Agent 数据契约）

所有个人数据、岗位数据、投递台账均存储在 data/ 下，纯本地，无任何云端上传逻辑。
写入采用「临时文件 + 原子替换」，避免中断导致数据损坏。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PATHS = {
    "profile": DATA / "profile" / "profile.json",
    "jobs": DATA / "jobs" / "jobs.json",
    "applications": DATA / "applications.json",
    "config": DATA / "config.json",
    "out": DATA / "out",
}


def load(name: str, default: Any = None) -> Any:
    """读取一个 JSON 数据文件；不存在时返回 default（并按需给出可写的默认结构）。

    文件损坏时不静默重建（会丢数据），而是给出可操作的修复指引。
    """
    path = PATHS[name]
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"数据文件不存在: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"数据文件损坏: {path}（{e}）\n"
            f"修复方式：备份该文件（mv {path} {path}.corrupt），然后删除或手工修复后重试。\n"
            f"注意：先备份再操作，不要直接删除——里面可能有你的投递记录。"
        ) from e


def save(name: str, obj: Any) -> Path:
    """原子写入一个 JSON 数据文件。"""
    path = PATHS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子替换
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def ensure_defaults() -> None:
    """初始化缺失的默认数据文件（幂等）。"""
    if not PATHS["jobs"].exists():
        save("jobs", {"schema_version": "1.0", "jobs": []})
    if not PATHS["applications"].exists():
        save("applications", {
            "schema_version": "1.0",
            "applications": [],      # 投递台账：一条 = 一个岗位的投递生命周期
            "daily_log": {},         # {"2026-09-20": {"boss": {"count": 3, "last_message_at": "..."}}}
            "blacklist": [],         # 已投递/明确不去的企业（规范化名称），避免重复投递
        })
    if not PATHS["config"].exists():
        save("config", {
            "schema_version": "1.0",
            "risk": {
                "daily_apply_cap_per_platform": 25,   # 单平台每日投递上限
                "min_message_interval_sec": 30,       # 单条消息最小间隔
                "sensitive_hours": ["22:00-08:00"],   # 平台风控敏感时段，禁止自动投递
            },
            "filter": {
                "max_published_age_days": 30,         # 超过此天数的岗位视为过期
                "exclude_headhunter": True,           # 排除猎头岗
                "exclude_outsourcing": True,          # 排除外包岗
                "exclude_experience_gte_years": 1,    # 排除要求≥1年经验的岗位
            },
            "campus_mode": True,                      # 全局校招模式
        })
