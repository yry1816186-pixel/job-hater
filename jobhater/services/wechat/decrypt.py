"""微信 4.x 数据库解密（SQLCipher 4 变体，Windows 桌面版）。

格式事实（逆向社区公开结论，已在本机真实库上验证）：
- 每库独立 32 字节 ``enc_key``（WCDB 缓存于微信进程内存）；
- AES-256-CBC，页 4096；页 1 前 16 字节为 salt；
- 每页尾部 reserve=80：IV(16) 在 ``page[4016:4032]``，HMAC-SHA512(64) 在 ``page[4032:4096]``；
- 密文范围：页 1 为 ``page[16:4016]``，其余页 ``page[0:4016]``；
- 解密页 1 后拼回 ``SQLite format 3\\x00`` 魔数；
- 验签：``mac_key = PBKDF2-HMAC-SHA512(enc_key, salt^0x3a, 2)``，
  ``HMAC-SHA512(mac_key, page[16:4032] || LE32(pgno))``。

安全边界：密钥只在本机进程内存中检索，绝不落盘明文；解密产物默认写入
jobhater 数据目录下的独立子目录，用户可一键清除。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:  # 可选依赖缺失：功能降级为明确报错，不阻断整个应用
    AES = None  # type: ignore[assignment]


def _require_aes() -> None:
    if AES is None:
        raise RuntimeError("缺少可选依赖 pycryptodome：请运行 pip install 'jobhater[wechat]' 后重试")

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
RESERVE_SZ = 80  # IV(16) + HMAC-SHA512(64)
IV_OFF = PAGE_SZ - RESERVE_SZ
HMAC_OFF = PAGE_SZ - 64
SQLITE_HDR = b"SQLite format 3\x00"
# SQLite 头 [16:24] 解密后特征：page_size=0x1000、写/读版本、reserved=80、0x40 0x20 0x20
_PLAINTEXT_SIG = bytes([0x10, 0x00])


@dataclass
class DecryptResult:
    """单库解密结果。"""

    path: Path
    out_path: Path | None
    ok: bool
    pages: int = 0
    error: str | None = None


def read_salt(db_path: Path) -> bytes:
    """读库 salt（页 1 前 16 字节）。"""
    with open(db_path, "rb") as f:
        return f.read(SALT_SZ)


def verify_key(enc_key: bytes, page1: bytes) -> bool:
    """用页 1 的 HMAC-SHA512 终审密钥（无假阳性）。"""
    if AES is None or len(page1) < PAGE_SZ:
        return False
    salt = page1[:SALT_SZ]
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, bytes(b ^ 0x3A for b in salt), 2, KEY_SZ)
    h = hmac.new(mac_key, page1[SALT_SZ : IV_OFF + 16], hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == page1[HMAC_OFF:PAGE_SZ]


def quick_screen(enc_key: bytes, page1: bytes) -> bool:
    """密钥候选快速筛查：解密页 1 首块比对 SQLite 头特征（比 PBKDF2 快一个量级）。

    byte20(reserved) 接受 0（标准 SQLite，测试合成库）或 80（WCDB 真实库）；
    筛查只做候选缩减，正确性由 :func:`verify_key` 的 HMAC 终审保证。
    """
    if AES is None or len(page1) < PAGE_SZ:
        return False
    pt = AES.new(enc_key, AES.MODE_ECB).decrypt(page1[SALT_SZ : SALT_SZ + 16])
    b = bytes(x ^ y for x, y in zip(pt, page1[IV_OFF : IV_OFF + 16], strict=True))
    return b[:2] == _PLAINTEXT_SIG and b[2] in (1, 2) and b[3] in (1, 2) and b[4] in (0, RESERVE_SZ) and b[5:8] == b"\x40\x20\x20"


def decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    """解密单页为标准 SQLite 页（保留 80 字节 reserve 区，页 1 恢复魔数）。"""
    iv = page[IV_OFF : IV_OFF + 16]
    if pgno == 1:
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        body = cipher.decrypt(page[SALT_SZ : PAGE_SZ - RESERVE_SZ])
        return SQLITE_HDR + body + b"\x00" * RESERVE_SZ
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    body = cipher.decrypt(page[: PAGE_SZ - RESERVE_SZ])
    return body + b"\x00" * RESERVE_SZ


def decrypt_database(db_path: Path, out_path: Path, enc_key: bytes) -> DecryptResult:
    """整库解密为明文 SQLite 文件（先 HMAC 验证，失败即拒）。"""
    _require_aes()
    size = db_path.stat().st_size
    if size < PAGE_SZ:
        return DecryptResult(db_path, None, False, error="文件小于一页")
    with open(db_path, "rb") as f:
        page1 = f.read(PAGE_SZ)
    if not verify_key(enc_key, page1):
        return DecryptResult(db_path, None, False, error="密钥验证失败（HMAC 不匹配）")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_pages = size // PAGE_SZ
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(db_path, "rb") as fin, open(tmp, "wb") as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                page = page + b"\x00" * (PAGE_SZ - len(page)) if page else b""
                if not page:
                    break
            fout.write(decrypt_page(enc_key, page, pgno))
    os.replace(tmp, out_path)
    return DecryptResult(db_path, out_path, True, pages=total_pages)


def encrypt_database_for_test(plain_path: Path, out_path: Path, enc_key: bytes, salt: bytes) -> None:
    """测试辅助：按微信 4.x 格式把明文 SQLite 加密（合成测试库用，非产品路径）。"""
    _require_aes()
    assert len(enc_key) == KEY_SZ and len(salt) == SALT_SZ
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, KEY_SZ)
    data = plain_path.read_bytes()
    if len(data) % PAGE_SZ:
        data = data + b"\x00" * (PAGE_SZ - len(data) % PAGE_SZ)
    with open(out_path, "wb") as f:
        for pgno in range(1, len(data) // PAGE_SZ + 1):
            page = bytearray(data[(pgno - 1) * PAGE_SZ : pgno * PAGE_SZ])
            if pgno == 1:
                body = bytes(page[SALT_SZ : PAGE_SZ - RESERVE_SZ])
            else:
                body = bytes(page[: PAGE_SZ - RESERVE_SZ])
            iv = os.urandom(16)
            ct = AES.new(enc_key, AES.MODE_CBC, iv).encrypt(body)
            out = bytearray(PAGE_SZ)
            start = SALT_SZ if pgno == 1 else 0
            if pgno == 1:
                out[:SALT_SZ] = salt
            out[start : PAGE_SZ - RESERVE_SZ] = ct
            out[IV_OFF : IV_OFF + 16] = iv
            h = hmac.new(mac_key, bytes(out[start : IV_OFF + 16]), hashlib.sha512)
            h.update(struct.pack("<I", pgno))
            out[HMAC_OFF:PAGE_SZ] = h.digest()
            f.write(out)
