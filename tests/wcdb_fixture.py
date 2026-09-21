"""测试夹具：手工构造 WCDB 风格（reserved=80、usable=4016）的明文 SQLite 库。

标准 sqlite3 无法设置 reserved_bytes；而 cell 恰从 usable 末尾向下生长，
普通库经微信格式加密往返必然截断 [4016:4096] 的 cell。本夹具按 SQLite
文件格式规范逐字节构造页，保证全部数据只落在 [0:4016]，可被
``encrypt_database_for_test`` / ``decrypt_database`` 忠实往返。
"""
from __future__ import annotations

import struct
from pathlib import Path

PAGE = 4096
USABLE = PAGE - 80  # reserved=80（WCDB/SQLCipher 特征）


def _varint(n: int) -> bytes:
    """SQLite varint：1-9 字节，每字节 7 位组，高位组在前（大端）。"""
    if n < 0:
        raise ValueError("varint 不支持负数")
    groups: list[int] = [n & 0x7F]
    n >>= 7
    while n:
        groups.append(n & 0x7F)
        n >>= 7
    out = bytearray()
    for g in reversed(groups[1:]):
        out.append(g | 0x80)
    out.append(groups[0])
    return bytes(out)


def _record(values: list[object]) -> bytes:
    """SQLite record：header(varint len + serial types) + body。支持 None/int/str。"""
    serials: list[bytes] = []
    body = bytearray()
    for v in values:
        if v is None:
            serials.append(b"\x00")
        elif isinstance(v, int):
            if -128 <= v <= 127:
                serials.append(b"\x01")
                body += v.to_bytes(1, "big", signed=True)
            elif -32768 <= v <= 32767:
                serials.append(b"\x02")
                body += v.to_bytes(2, "big", signed=True)
            elif -8388608 <= v <= 8388607:
                serials.append(b"\x03")
                body += v.to_bytes(3, "big", signed=True)
            elif -(2**31) <= v <= 2**31 - 1:
                serials.append(b"\x04")
                body += v.to_bytes(4, "big", signed=True)
            else:
                serials.append(b"\x06")
                body += v.to_bytes(8, "big", signed=True)
        elif isinstance(v, str):
            data = v.encode("utf-8")
            serials.append(_varint(13 + 2 * len(data)))
            body += data
        else:
            raise TypeError(f"不支持的类型: {type(v)}")
    head_body = b"".join(serials)
    head = _varint(len(head_body) + 1) + head_body
    return head + bytes(body)


def _leaf_page(cells: list[bytes], *, base_offset: int = 0) -> tuple[bytearray, list[int]]:
    """构造表叶子页：cells 从 usable(4016) 向前摆放；返回 (页, cell绝对偏移列表按键序)。"""
    page = bytearray(PAGE)
    content_end = USABLE
    pointers: list[int] = []
    for cell in cells:
        content_end -= len(cell)
        if content_end < base_offset + 8 + 2 * len(cells):
            raise ValueError("页空间不足：测试数据需控制在单页内")
        page[content_end : content_end + len(cell)] = cell
        pointers.append(content_end)
    page[base_offset] = 0x0D
    page[base_offset + 1 : base_offset + 3] = (0).to_bytes(2, "big")
    page[base_offset + 3 : base_offset + 5] = len(cells).to_bytes(2, "big")
    page[base_offset + 5 : base_offset + 7] = (min(pointers) if pointers else 0).to_bytes(2, "big")
    page[base_offset + 7] = 0
    for i, p in enumerate(pointers):
        off = base_offset + 8 + 2 * i
        page[off : off + 2] = p.to_bytes(2, "big")
    return page, pointers


def _file_header(page1: bytearray, n_pages: int) -> None:
    h = page1
    h[0:16] = b"SQLite format 3\x00"
    h[16:18] = (4096).to_bytes(2, "big")
    h[18] = 1
    h[19] = 1
    h[20] = 80  # reserved → usable 4016
    h[21], h[22], h[23] = 64, 32, 32
    h[24:28] = (1).to_bytes(4, "big")  # change counter
    h[28:32] = n_pages.to_bytes(4, "big")
    h[32:36] = (0).to_bytes(4, "big")
    h[36:40] = (0).to_bytes(4, "big")
    h[40:44] = (1).to_bytes(4, "big")  # schema cookie
    h[44:48] = (4).to_bytes(4, "big")  # schema format
    h[48:52] = (0).to_bytes(4, "big")
    h[52:56] = (0).to_bytes(4, "big")
    h[56:60] = (1).to_bytes(4, "big")  # utf-8
    h[60:64] = (0).to_bytes(4, "big")
    h[64:68] = (0).to_bytes(4, "big")
    h[68:72] = (0).to_bytes(4, "big")
    h[92:96] = (1).to_bytes(4, "big")  # version-valid-for == change counter
    h[96:100] = struct.pack(">I", 3045000)


def build_wcdb_db(
    path: Path,
    tables: list[tuple[str, str, list[list[object]]]],
) -> None:
    """构造多表 WCDB 风格库。

    ``tables``: [(表名, 建表SQL, 行数据(值列表, 首列应为 INTEGER PRIMARY KEY 的 rowid 或 None))]。
    每表一个叶子页（第 2 页起）；行数据必须能装进单页 usable 区。
    """
    # 先为每张表构造叶子页
    pages: list[bytearray] = []
    master_cells: list[bytes] = []
    root = 2
    for name, sql, rows in tables:
        cells = []
        for i, row in enumerate(rows, start=1):
            rec_values = list(row)
            if rec_values and rec_values[0] is None:
                rec_values[0] = i  # INTEGER PRIMARY KEY 别名：由 rowid 承担
            payload = _record(rec_values)
            cells.append(_varint(len(payload)) + _varint(i) + payload)
        page, _ptrs = _leaf_page(cells)
        pages.append(page)
        master_cells.append(_record(["table", name, name, root, sql]) + b"")
        root += 1
    # master cell 需带 payload 长度与 rowid 前缀
    wrapped = []
    for i, cell in enumerate(master_cells, start=1):
        wrapped.append(_varint(len(cell)) + _varint(i) + cell)
    page1, _ = _leaf_page(wrapped, base_offset=100)
    _file_header(page1, 1 + len(pages))
    path.write_bytes(b"".join(bytes(p) for p in [page1, *pages]))


def build_wcdb_style_db(path: Path, rows: int = 50) -> None:
    """单表便捷入口：表 t(id INTEGER PRIMARY KEY, name TEXT)（历史兼容）。"""
    build_wcdb_db(
        path,
        [("t", "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)", [[None, f"name{i}"] for i in range(1, rows + 1)])],
    )
