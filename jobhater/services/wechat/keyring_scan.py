"""微信进程内存密钥检索（Windows，Weixin.exe 4.x）。

原理：WCDB 把每个库的 32 字节 AES 密钥缓存在微信进程内存（明文或 hex 字符串
形态）。本模块枚举全部可读内存区域：
1. 先收集 ``x'<64/96hex>'`` 与裸 64-hex / UTF-16 hex 字符串候选；
2. 再对 32 字节滑窗做熵预筛（numpy，逐字节对齐）；
3. 候选统一经 ``decrypt.quick_screen``（AES 首块特征）筛查，
   ``decrypt.verify_key``（HMAC-SHA512 终审）确认——无假阳性。

密钥只在内存中流转：找到后立即返回/用于解密，绝不写入磁盘。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from jobhater.services.wechat.decrypt import quick_screen, verify_key

KDF_ITER = 256_000


def verify_as_password(passphrase: bytes, page1: bytes) -> bytes | None:
    """密码式验证（新版 WCDB：内存存 passphrase，每库 PBKDF2 派生 enc_key）。

    返回派生出的 enc_key（可用于直接解密），不匹配返回 None。
    成本 ≈ 一次 256000 轮 PBKDF2-SHA512（约 0.2s），调用方须先做候选筛减。
    """
    salt = page1[:16]
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, 32)
    if verify_key(enc_key, page1):
        return enc_key
    return None

_api_cache: dict[str, ctypes.WinDLL] = {}


def _api() -> tuple[ctypes.WinDLL, ctypes.WinDLL] | None:
    """惰性加载并配置 kernel32/psapi 签名（仅 Windows）。"""
    import os

    if os.name != "nt":
        return None
    if "k" in _api_cache:
        return _api_cache["k"], _api_cache["p"]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    p = ctypes.WinDLL("psapi", use_last_error=True)
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.OpenProcess.restype = wt.HANDLE
    k.CloseHandle.argtypes = [wt.HANDLE]
    k.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k.ReadProcessMemory.restype = wt.BOOL
    p.EnumProcesses.argtypes = [ctypes.POINTER(ctypes.c_uint32), wt.DWORD, ctypes.POINTER(wt.DWORD)]
    p.EnumProcesses.restype = wt.BOOL
    p.GetModuleBaseNameW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
    p.GetModuleBaseNameW.restype = wt.DWORD
    _api_cache["k"] = k
    _api_cache["p"] = p
    return k, p


class _MBI(ctypes.Structure):
    """64 位 MEMORY_BASIC_INFORMATION（sizeof == 48，字段顺序错一位全盘错位）。"""

    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.USHORT),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


_READABLE_PROTECT = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
_CHUNK = 32 * 1024 * 1024


def weixin_pids() -> list[tuple[int, int]]:
    """[(pid, 内存KB)]，按内存降序（主进程优先）。非 Windows/未运行返回空。"""
    api = _api()
    if api is None:
        return []
    k, p = api
    arr = (ctypes.c_uint32 * 2048)()
    needed = wt.DWORD(0)
    if not p.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return []
    out: list[tuple[int, int]] = []
    buf = ctypes.create_unicode_buffer(260)
    for pid in arr[: needed.value // 4]:
        if not pid:
            continue
        h = k.OpenProcess(0x0410, False, pid)
        if not h:
            continue
        try:
            if p.GetModuleBaseNameW(h, None, buf, 260) and buf.value.lower() in ("weixin.exe", "wechat.exe"):
                out.append((pid, 0))
        finally:
            k.CloseHandle(h)
    return out


def _regions(k: ctypes.WinDLL, h: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    addr = 0
    mbi = _MBI()
    while addr <= 0x7FFFFFFEFFFF:
        if k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if mbi.State == 0x1000 and mbi.Protect in _READABLE_PROTECT and mbi.RegionSize > 0:
            out.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return out


def _read(k: ctypes.WinDLL, h: int, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    if not k.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        if n.value == 0:
            return b""
    return buf.raw[: n.value]


_HEX64 = re.compile(rb"[0-9a-fA-F]{64}")
_HEX64_UTF16 = re.compile(rb"(?:[0-9a-fA-F]\x00){64}")


def _hex_candidates(data: bytes) -> Iterator[bytes]:
    """内存块中的 hex 字符串候选 → 32 字节密钥候选。"""
    seen: set[bytes] = set()
    for m in _HEX64.finditer(data):
        seen.add(m.group()[:64])
    for m in _HEX64_UTF16.finditer(data):
        seen.add(bytes(m.group()[0::2])[:64])
    for hx in seen:
        try:
            yield bytes.fromhex(hx.decode("ascii"))
        except ValueError:
            continue


def _raw_candidates(data: bytes) -> Iterator[bytes]:
    """逐字节滑窗熵预筛（≥22 个不同字节值）后的原始 32 字节候选。"""
    if len(data) < 32:
        return
    import numpy as np  # noqa: PLC0415（可选依赖: 缺失时 raw 扫描降级为 hex-only）

    arr = np.frombuffer(data, dtype=np.uint8)
    n = len(arr) - 31
    wins = np.lib.stride_tricks.as_strided(arr, shape=(n, 32), strides=(1, 1))
    s = np.sort(wins, axis=1)
    uniq = (np.diff(s, axis=1) != 0).sum(axis=1) + 1
    for i in np.nonzero(uniq >= 22)[0]:
        off = int(i)
        yield bytes(arr[off : off + 32])


# ---- 结构定位候选（社区方法: 密钥容器布局签名 → 指针解引用）----
# 布局A: [8B ptr][8B 0][8B len=32][8B cap=47]（WeChatDataAnalysis 的 YARA 规则）
_STUB_A = b"\x00" * 10 + (32).to_bytes(8, "little") + (47).to_bytes(8, "little")
# 布局B: MSVC std::string [8B ptr 末2字节零][8B size=32] 紧邻
_STUB_B = b"\x00\x00" + (32).to_bytes(8, "little")


def _deref_targets(data: bytes, ptr_reader: Callable[[int], bytes]) -> Iterator[int]:
    """从两种布局签名解出密钥指针。``ptr_reader(addr)->32B`` 由调用方提供。"""
    for stub, back in ((_STUB_A, 6), (_STUB_B, 6)):
        pos = data.find(stub)
        while pos >= 0:
            ptr_off = pos - back
            if ptr_off >= 0:
                ptr = int.from_bytes(data[ptr_off : ptr_off + 8], "little")
                if 0x10000 < ptr < 0x7FFF_FFFF_FFFF:
                    yield ptr
            pos = data.find(stub, pos + 1)


def _struct_candidates(data: bytes, read_at: Callable[[int], bytes]) -> Iterator[bytes]:
    """布局签名定位的指针解引用候选（ passphrase 形态，需配 DLL XOR 验证）。"""
    for ptr in _deref_targets(data, read_at):
        kb = read_at(ptr)
        if len(kb) == 32 and _looks_like_secret(kb):
            yield kb


def _looks_like_secret(key: bytes) -> bool:
    """密码学随机 32B 的快速统计筛（区分普通文本/指针串）。"""
    return len(set(key)) >= 15 and sum(32 <= b <= 126 for b in key) <= 24


# ---- Weixin.dll 内的 XOR 混淆密钥（4×mov rdx,imm64 汇编模式）----
_MOV_RDX_PATTERN = re.compile(
    b"^\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\x85\xC0",
    re.DOTALL,
)


def dll_xor_keys(install_path: str | None = None) -> list[bytes]:
    """从 Weixin.dll 提取 XOR 混淆密钥候选（每个 32B）。

    安装目录自动发现（注册表 → 常见位置 → 版本子目录内最大的 Weixin.dll）。
    读取失败/找不到返回空列表（密码验证退化为直通模式）。
    """
    dll = _locate_weixin_dll(install_path)
    if dll is None:
        return []
    try:
        data = Path(dll).read_bytes()
    except OSError:
        return []
    keys: list[bytes] = []
    offset = 0
    while True:
        idx = data.find(b"\x48\xBA", offset)
        if idx == -1:
            break
        m = _MOV_RDX_PATTERN.match(data[idx : idx + 85])
        if m:
            k = m.group(1) + m.group(2) + m.group(3) + m.group(4)
            if k not in keys:
                keys.append(k)
            offset = idx + len(m.group(0))
        else:
            offset = idx + 1
    return keys


def _locate_weixin_dll(install_path: str | None) -> str | None:
    import os

    root = install_path
    if root is None:
        root = _default_wechat_install()
    if not root or not os.path.isdir(root):
        return None
    best: tuple[int, str] | None = None
    for d in os.listdir(root):
        cand = os.path.join(root, d, "Weixin.dll")
        if os.path.isfile(cand):
            size = os.path.getsize(cand)
            if best is None or size > best[0]:
                best = (size, cand)
    return best[1] if best else None


def _default_wechat_install() -> str | None:
    import os

    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            p = os.path.join(base, "Tencent", "Weixin")
            if os.path.isdir(p):
                return p
    return None


@dataclass
class ScanProgress:
    """进度快照（前端轮询用）。"""

    phase: str = "idle"  # idle/scanning/done/failed
    scanned_bytes: int = 0
    total_bytes: int = 0
    pids: list[int] = field(default_factory=list)
    candidates: int = 0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase, "scanned_bytes": self.scanned_bytes,
            "total_bytes": self.total_bytes, "pids": self.pids,
            "candidates": self.candidates,
        }


def find_key(
    page1: bytes | list[bytes],
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> bytes | None:
    """在所有 Weixin 进程内存中检索能解开任一目标库（page1 列表）的密钥。

    多锚点：每库密钥独立缓存，单一库的 key 可能不在内存；传入全部核心库的
    第一页可显著提高命中面。密钥命中即返回。找不到返回 None（不抛异常）。

    高级通道：环境变量 ``JOBHATER_WECHAT_KEY``（64 位 hex）优先验证——供
    已知密钥的用户跳过内存扫描；密钥仍只在内存中使用。两种形态都试：
    直通式（某库 enc_key）优先（验证便宜），密码式（passphrase，每库
    PBKDF2 派生）回退。该通道不依赖微信进程与平台，可离线使用。
    """
    anchors = [page1] if isinstance(page1, (bytes, bytearray)) else list(page1)
    if not anchors:
        return None
    import os

    env_key = os.environ.get("JOBHATER_WECHAT_KEY", "").strip()
    if len(env_key) == 64:
        try:
            cand = bytes.fromhex(env_key)
        except ValueError:
            cand = b""
        if cand:
            if any(quick_screen(cand, p1) and verify_key(cand, p1) for p1 in anchors):
                return cand
            for p1 in anchors:
                ek = verify_as_password(cand, p1)
                if ek is not None:
                    return ek
    api = _api()
    if api is None:
        return None
    k, _p = api
    targets = weixin_pids()
    prog = ScanProgress(phase="scanning", pids=[pid for pid, _ in targets])
    if progress:
        progress(prog)

    def _match(cand: bytes) -> bool:
        return any(quick_screen(cand, p1) and verify_key(cand, p1) for p1 in anchors)

    # DLL XOR 混淆密钥（新版 WCDB 密码式验证所需; 提取失败退化为直通）
    xor_keys = dll_xor_keys()

    def _match_password(pw: bytes) -> bytes | None:
        """密码式: 直通 + XOR 混淆两种形态 × 全锚点。返回 enc_key。"""
        forms = [pw] + [bytes(a ^ b for a, b in zip(pw, xk, strict=True)) for xk in xor_keys]
        for form in forms:
            for p1 in anchors:
                ek = verify_as_password(form, p1)
                if ek is not None:
                    return ek
        return None

    for pid, _mem in targets:
        h = k.OpenProcess(0x0410, False, pid)
        if not h:
            continue
        try:
            regions = _regions(k, h)
            prog.total_bytes += sum(s for _, s in regions)
            for base, size in regions:
                if stop_check and stop_check():
                    prog.phase = "idle"
                    if progress:
                        progress(prog)
                    return None
                for off in range(0, size, _CHUNK):
                    data = _read(k, h, base + off, min(_CHUNK, size - off))
                    if not data:
                        continue
                    prog.scanned_bytes += len(data)
                    for cand in _hex_candidates(data):
                        prog.candidates += 1
                        if _match(cand):
                            prog.phase = "done"
                            if progress:
                                progress(prog)
                            return cand
                    # 结构定位候选 → 密码式验证（新版 WCDB; 已熵筛）
                    proc_handle = h
                    for pw in _struct_candidates(data, lambda a, _h=proc_handle: _read(k, _h, a, 32)):
                            prog.candidates += 1
                            ek = _match_password(pw)
                            if ek is not None:
                                prog.phase = "done"
                                if progress:
                                    progress(prog)
                                return ek
                    for cand in _raw_candidates(data):
                        prog.candidates += 1
                        if _match(cand):
                            prog.phase = "done"
                            if progress:
                                progress(prog)
                            return cand
                if progress:
                    progress(prog)
        finally:
            k.CloseHandle(h)
    prog.phase = "done"
    if progress:
        progress(prog)
    return None
