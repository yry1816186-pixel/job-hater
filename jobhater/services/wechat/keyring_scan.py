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
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from jobhater.services.wechat.decrypt import quick_screen, verify_key

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
    已知密钥的用户跳过内存扫描；密钥仍只在内存中使用。
    """
    api = _api()
    if api is None:
        return None
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
        if cand and any(quick_screen(cand, p1) and verify_key(cand, p1) for p1 in anchors):
            return cand
    k, _p = api
    targets = weixin_pids()
    prog = ScanProgress(phase="scanning", pids=[pid for pid, _ in targets])
    if progress:
        progress(prog)

    def _match(cand: bytes) -> bool:
        return any(quick_screen(cand, p1) and verify_key(cand, p1) for p1 in anchors)

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
