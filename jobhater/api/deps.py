"""API 装配层公共依赖：数据库连接与通用错误映射（BC-007 自 routes.py 纯搬移）。"""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException

from jobhater.db import connect
from jobhater.services.lifecycle import LifecycleError


def get_con():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def _err(e: Exception) -> HTTPException:
    if isinstance(e, (KeyError, LifecycleError)):
        return HTTPException(status_code=404 if isinstance(e, KeyError) else 422, detail=str(e).strip("'\""))
    if isinstance(e, sqlite3.IntegrityError):
        return HTTPException(status_code=409, detail=f"数据冲突：{e}")
    return HTTPException(status_code=500, detail=str(e))
