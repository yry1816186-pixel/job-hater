"""QQ NT 桌面版数据库解密（v3.5，配方经 2026-10-14 实战验证）。

文件格式（与微信 WCDB 不同，两段式）：
- 第 1 页（4096B）= **protobuf 填充头**（非加密）："QQ_NT DB" magic + 128 位
  hex rand + "1.1.0.1" + "HMAC_SHA1" + 写入时间戳 varint 等；
- 第 2 页起 = SQLCipher 体：AES-256-CBC，页 4096，reserve=48（页尾 48B 中
  [4048:4064]=IV），密文页 2 的首块解出 "SQLite format 3\\x00" 即验证 oracle。

密钥推导（p25c 实测结论，纠正了早期记忆的两处错误）：
- header varint@196 = **写入时间戳而非 KDF 迭代数**（早期按 1.79e9 跑了一小时
  全败后实测判定）；
- KDF = PBKDF2-SHA512(passphrase, salt, iters, 32)，iters ∈ {4000, 256000}
  双假设（iOS 同源格式 4000 / SQLCipher4 默认 256000），salt 候选三个：
  header rand 二进制前 16B / header[48:64] / 首个数据页前 16B；
- raw 32B enc_key 也可能直接躺在内存里（通道 B，最快）。

与微信雷达同款铁律：**密钥只在内存流转，绝不落盘**（mission 脚本曾缓存
qq_key.bin，产品化移除该行为）；解密产物明文库写 data_dir/qq/decrypted/。
可选依赖 pycryptodome（extras 组 [qq]），缺失时在真正解密处如实报错。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

try:
    from Crypto.Cipher import AES

    _HAS_AES = True
except ImportError:  # 缺可选依赖时类可导入，解密动作处才报错（微信 decrypt 同款降级）
    AES = None
    _HAS_AES = False

PAGE = 4096
RESERVE = 48
KDF_ITERS = (4000, 256000)
SQLITE_HDR = b"SQLite format 3\x00"
QQ_MAGIC = b"QQ_NT DB"


class QQDecryptError(RuntimeError):
    """用户可读的解密错误（含安装指引）。"""


def _require_aes() -> None:
    if not _HAS_AES:
        raise QQDecryptError("解密需要 pycryptodome：pip install 'jobhater[qq]'")


def read_header(db_path: Path) -> bytes:
    """protobuf 头页（文件前 4096B，明文）。"""
    with open(db_path, "rb") as f:
        return f.read(PAGE)


def read_anchor(db_path: Path) -> bytes:
    """锚点页 = 第 2 页（首个 SQLCipher 数据页，明文 SQLite 头在此）。"""
    with open(db_path, "rb") as f:
        f.seek(PAGE)
        return f.read(PAGE)


def salt_candidates(db_path: Path, anchor: bytes | None = None) -> list[bytes]:
    header = read_header(db_path)
    anchor = anchor or read_anchor(db_path)
    try:
        rand = bytes.fromhex(header[47:175].decode())
    except (ValueError, UnicodeDecodeError):
        return [anchor[:16]]
    return [rand[:16], header[48:64], anchor[:16]]


def check_key(ek: bytes, anchor: bytes) -> bool:
    """验证 oracle：锚点页首块 AES-CBC(iv=页尾-48 处) 解密 == SQLite 头。"""
    _require_aes()
    iv = anchor[PAGE - RESERVE : PAGE - RESERVE + 16]
    return AES.new(ek, AES.MODE_CBC, iv).decrypt(anchor[:16]) == SQLITE_HDR


def derive_key(passphrase: bytes, db_path: Path,
               salts: list[bytes] | None = None) -> bytes | None:
    """passphrase（内存扫出的 hex 串二进制）→ 三 salt × 双 iter 全组合推导验证。"""
    _require_aes()
    anchor = read_anchor(db_path)
    for salt in (salts if salts is not None else salt_candidates(db_path, anchor)):
        for it in KDF_ITERS:
            ek = hashlib.pbkdf2_hmac("sha512", passphrase, salt, it, 32)
            if check_key(ek, anchor):
                return ek
    return None


def looks_like_qq_db(db_path: Path) -> bool:
    """magic 快筛：头页含 QQ_NT DB 标记（detect 层据此认库，不读全文件）。"""
    try:
        with open(db_path, "rb") as f:
            return f.read(64).find(QQ_MAGIC) >= 0
    except OSError:
        return False


# 测试造库器的页尾暂存标记（真 QQ 头页不会出现）：SQLite 单元格从页尾向下
# 生长，普通库（reserved=0）的数据会占据 [4048:4096]，与 SQLCipher 的
# reserve 区冲突——造库器把每页尾 48B 暂存进合成头空闲区，解密侧见标记还原。
# 真 QQ 库的明文头声明 reserved=48，数据永不入尾区，走正常路径（尾区=IV/HMAC
# 垃圾，SQLite 自行忽略）。
_STASH_MARKER = b"JHTAILSTASH01"
_STASH_OFF = 176
_STASH_LEN_OFF = 190
_STASH_BASE = 256


def _stash_from_header(header: bytes, n_pages: int) -> dict[int, bytes]:
    if header[_STASH_OFF : _STASH_OFF + len(_STASH_MARKER)] != _STASH_MARKER:
        return {}
    stash = {}
    for i in range(n_pages):
        chunk = header[_STASH_BASE + 48 * i : _STASH_BASE + 48 * (i + 1)]
        if len(chunk) == 48:
            stash[i] = chunk
    return stash


def decrypt_database(src: Path, out: Path, ek: bytes) -> bool:
    """整库解密：跳过头页，逐页 AES-CBC（iv=[4048:4064]）。

    reserve 区（页尾 48B，真库=IV+HMAC）原样保留——明文库头声明 reserved=48
    时 SQLite 会忽略该区；测试造库（见 _STASH_MARKER）则从头部暂存还原页尾。
    原子写（tmp+replace），解密后立即用只读 sqlite 校验可打开且含表。
    """
    _require_aes()
    data = src.read_bytes()
    body = data[PAGE:]
    n = len(body) // PAGE
    stash = _stash_from_header(data[:PAGE], n)
    buf = bytearray()
    for i in range(n):
        pg = body[i * PAGE : (i + 1) * PAGE]
        iv = pg[PAGE - RESERVE : PAGE - RESERVE + 16]
        tail = stash.get(i, pg[PAGE - RESERVE :])
        buf += AES.new(ek, AES.MODE_CBC, iv).decrypt(pg[: PAGE - RESERVE]) + tail
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(bytes(buf))
    os.replace(tmp, out)
    try:
        con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            tabs = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            con.close()
        return len(tabs) > 0
    except sqlite3.DatabaseError:
        return False


def encrypt_database_for_test(plain: Path, out: Path, ek: bytes,
                              header: bytes | None = None) -> None:
    """测试造库：普通 SQLite 文件 → QQ NT 形态（protobuf 头页 + SQLCipher 体）。

    页尾 reserve 区放 IV(16B)+原页尾原样保留——保证对任意普通 SQLite 文件
    （reserved=0，页尾可能有数据）解密往返无损；真库此区为 HMAC，解密路径
    不校验 HMAC（与 p25c 一致），保留与否不影响正确性。
    """
    import secrets

    _require_aes()
    data = plain.read_bytes()
    n = (len(data) + PAGE - 1) // PAGE
    if header is None:
        # 显式定长布局（避免 bytearray 切片赋值错位）：magic/rand 对齐真头，
        # 空闲区放页尾暂存（marker+页数+逐页 48B）
        header = bytearray(PAGE)
        header[0:8] = QQ_MAGIC
        header[47:175] = secrets.token_hex(64).encode()
        header[_STASH_OFF : _STASH_OFF + len(_STASH_MARKER)] = _STASH_MARKER
        header[_STASH_LEN_OFF : _STASH_LEN_OFF + 2] = n.to_bytes(2, "little")
        for i in range(n):
            tail = data[i * PAGE + PAGE - RESERVE : (i + 1) * PAGE]
            header[_STASH_BASE + 48 * i : _STASH_BASE + 48 * (i + 1)] = \
                tail.ljust(RESERVE, b"\x00")
    body = bytearray()
    for i in range(n):
        pg = data[i * PAGE : (i + 1) * PAGE]
        pg = pg + b"\x00" * (PAGE - len(pg))
        iv = secrets.token_bytes(16)
        body += AES.new(ek, AES.MODE_CBC, iv).encrypt(pg[: PAGE - RESERVE]) + iv + secrets.token_bytes(RESERVE - 16)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(header) + bytes(body))
