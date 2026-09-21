"""SQLite 连接管理：WAL、外键、事务上下文。

约定：
- 所有连接 ``PRAGMA journal_mode=WAL`` + ``foreign_keys=ON`` + ``busy_timeout``；
- 写操作一律经 :func:`transaction` 上下文（BEGIN IMMEDIATE，避免升级死锁）；
- ``row_factory=sqlite3.Row``，服务层用列名访问，禁止位置索引。
"""
from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from jobhater import config

BUSY_TIMEOUT_MS = 10_000


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """打开（必要时创建）数据库并应用基础 PRAGMA。调用方负责 close。"""
    p = Path(path) if path is not None else config.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：关闭隐式事务，事务一律由调用方经 transaction() 显式管理
    # check_same_thread=False：FastAPI 同步依赖的 setup/teardown（含 close）可能
    # 落在不同 threadpool 线程。每连接单请求独占、线程间仅顺序传递、无并发共享，
    # 放开 Python 层线程亲和检查是安全且必要的（否则间歇性 ProgrammingError）。
    con = sqlite3.connect(
        str(p),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextlib.contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """写事务上下文：正常提交，异常回滚。嵌套调用会直接报错（SQLite 不支持嵌套 BEGIN）。"""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.rollback()
        raise
    else:
        con.commit()
