"""Schema 迁移运行器。

- 迁移脚本为包内 ``db/migrations/NNNN_*.sql``，按版本号升序执行；
- 版本记录于 ``schema_migrations`` 表（同时写 ``PRAGMA user_version`` 便于快速探测）；
- 每个脚本包裹在显式 ``BEGIN…COMMIT`` 中经 ``executescript`` 执行（触发器体内的
  分号只有 executescript 能正确处理）；失败即回滚，不留半套 schema；
- 禁止修改已发布的迁移脚本——变更一律追加新脚本。
"""
from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

from jobhater.db.connection import connect

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
)
"""


def migration_files() -> list[tuple[int, str, str]]:
    """[(version, name, sql)] 按版本升序。迁移目录随包分发。"""
    out: list[tuple[int, str, str]] = []
    traversable = resources.files("jobhater") / "db" / "migrations"
    for res in traversable.iterdir():
        name = res.name
        if not name.endswith(".sql"):
            continue
        m = re.match(r"^(\d{4})_", name)
        if not m:
            raise ValueError(f"迁移文件命名必须为 NNNN_描述.sql: {name}")
        out.append((int(m.group(1)), name, res.read_text(encoding="utf-8")))
    out.sort(key=lambda t: t[0])
    if len({v for v, _, _ in out}) != len(out):
        raise ValueError("迁移版本号重复")
    return out


def current_version(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def apply_all(path: Path | str | None = None) -> int:
    """把所有未应用的迁移按序执行，返回应用条数。幂等。"""
    con = connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        applied = 0
        for version, name, sql in migration_files():
            if version <= current_version(con):
                continue
            try:
                # 显式事务包裹，保证单脚本原子性；executescript 会先提交悬挂事务，
                # 因此这里不与 transaction() 混用。
                con.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
            except BaseException:
                con.rollback()
                raise
            con.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)", (version, name)
            )
            con.execute(f"PRAGMA user_version={version}")
            applied += 1
        return applied
    finally:
        con.close()
