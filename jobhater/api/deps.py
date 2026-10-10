"""API 装配层公共依赖：数据库连接与通用错误映射（BC-007 自 routes.py 纯搬移）。"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import HTTPException, Request, UploadFile

from jobhater.db import connect
from jobhater.services.lifecycle import LifecycleError

logger = logging.getLogger("jobhater.api")

# SEC-007：Content-Length 预检给 multipart 框架开销（boundary/文件名/头部）留的余量——
# 预检只拦明显超限，精确上限由分块累计兜底
_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_UPLOAD_CHUNK_BYTES = 1 << 20


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
    # SEC-004：500 不回显内部异常文本（文件路径/SQL/第三方端点等敏感细节），
    # 完整堆栈经 logging.exception 落 stderr（BC-009 JSON 流），保留 4xx 语义化文本
    logger.exception("api.unhandled_error", extra={"error_type": type(e).__name__})
    return HTTPException(status_code=500, detail="服务器内部错误")


async def read_upload(request: Request, file: UploadFile, max_bytes: int) -> bytes:
    """读取上传文件体（SEC-007）：Content-Length 预检短路 + 分块累计硬上限。

    预检命中（含 multipart 余量）直接 413 且不读 body——恶意/误操作的大文件
    不再先全量进内存后校验；Content-Length 缺席或说谎时由分块累计兜底。
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + _MULTIPART_OVERHEAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小上限（{max_bytes // (1 << 20)}MB），请精简后重试",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小上限（{max_bytes // (1 << 20)}MB），请精简后重试",
            )
        chunks.append(chunk)
    return b"".join(chunks)
