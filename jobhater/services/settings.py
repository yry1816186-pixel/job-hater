"""用户设置（key-value JSON 存储）。

诚实语义：
- 值是任意 JSON（前端契约各自负责 schema 校验，服务层只保证读写原子）；
- 已知键集中列出，未知键保留不删（向前兼容：旧版本写的键不因升级消失）；
- 主题/保存的搜索/引导完成态等 UI 偏好都在这里——永不外发。
"""
from __future__ import annotations

import json
import sqlite3

from jobhater.db.connection import transaction

# 已知键与用途（文档即代码；新增键在此登记）
KNOWN_KEYS: dict[str, str] = {
    "theme": "主题偏好：'light' | 'dark' | 'system'",
    "saved_searches": "岗位页保存的搜索：[{name, params}]（params 为 /api/jobs 的查询参数）",
    "jobs_last_seen_at": "岗位列表上次查看时间（ISO），用于『新增 N 条』角标",
    "onboarding_done": "新手引导完成标记：bool",
    "board_grouping": "看板分组偏好：'stage'（按状态列）",
    "dashboard_widgets": "首页仪表盘启用的组件列表",
}


class SettingsService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def get(self, key: str, default: object = None) -> object:
        row = self.con.execute(
            "SELECT value_json FROM user_settings WHERE key=?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            # 单键损坏不砸全局：返回默认值（设置是可丢偏好，非事实数据）
            return default

    def set(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO user_settings(key, value_json) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (key, encoded),
            )

    def delete(self, key: str) -> bool:
        with transaction(self.con):
            cur = self.con.execute("DELETE FROM user_settings WHERE key=?", (key,))
            return cur.rowcount > 0

    def list(self) -> dict:
        rows = self.con.execute("SELECT key, value_json FROM user_settings").fetchall()
        out: dict = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value_json"])
            except json.JSONDecodeError:
                continue  # 同 get：损坏单键跳过，不砸列表
        return out


__all__ = ["SettingsService", "KNOWN_KEYS"]
