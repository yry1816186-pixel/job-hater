"""微信本地数据源检测（仅 Windows + 微信 4.x 桌面版）。

探测链（全部只读，自动发现，零手工配置）：
1. 注册表 ``HKCU\\Software\\Tencent\\Weixin``（安装路径/版本）；
2. ``%APPDATA%\\Tencent\\xwechat\\config\\*.ini`` 内容即数据根（如 ``D:\\微信chat``）；
3. 常见位置兜底扫描（Documents / 用户目录 / 各盘根的 ``xwechat_files``）；
4. 账号目录 = 数据根下 ``xwechat_files/wxid_*_<hash>``；
5. Weixin.exe 运行进程枚举（密钥提取的可用性前提）。

任何一步失败都不抛异常——返回的 :class:`WeChatEnv` 携带每步的检查明细，
UI 据此给出「缺什么、怎么补」的可行动指引。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass, field
from pathlib import Path

WIN = os.name == "nt"


@dataclass
class WeChatAccount:
    """一个已在本机登录过的微信账号（数据目录级）。"""

    key: str  # 目录名，如 wxid_btxzomoll4s422_07ed
    wxid: str  # 纯 wxid 部分
    root: Path  # 账号数据目录（含 db_storage）
    message_dbs: list[Path] = field(default_factory=list)
    biz_dbs: list[Path] = field(default_factory=list)  # 公众号文章库
    contact_dbs: list[Path] = field(default_factory=list)
    session_dbs: list[Path] = field(default_factory=list)
    total_db_bytes: int = 0


@dataclass
class WeChatEnv:
    """检测快照：platform_ok / data_root / accounts / processes / blockers。"""

    platform_ok: bool = WIN
    installed: bool = False
    install_path: str | None = None
    version: str | None = None
    data_root: Path | None = None
    data_root_source: str | None = None  # 通过哪条探测链找到的
    accounts: list[WeChatAccount] = field(default_factory=list)
    weixin_pids: list[int] = field(default_factory=list)
    checks: list[dict[str, object]] = field(default_factory=list)  # 每步检查明细

    @property
    def blockers(self) -> list[str]:
        """阻塞自动扫描的原因（面向用户可读）。

        基于 self.platform_ok 而非直接读环境：快照对象须自洽——
        detect() 真实构造时 platform_ok=WIN，行为不变；测试/离线场景
        构造的假想环境也能正确表达「平台 OK」。
        """
        out: list[str] = []
        if not self.platform_ok:
            out.append("当前不是 Windows 系统（微信本地库解密仅支持 Windows 微信 4.x 桌面版）")
        if self.platform_ok and not self.installed:
            out.append("未检测到微信 4.x（Weixin）安装")
        if self.platform_ok and self.installed and not self.accounts:
            out.append("找到微信但未发现本地账号数据（可能从未在此设备登录）")
        if self.platform_ok and self.accounts and not self.weixin_pids:
            out.append("微信未运行——密钥提取需要已登录的微信进程，请先打开微信并登录")
        return out


# ---------------------------------------------------------------- 注册表 / ini 探测

_REG_PATH = r"Software\Tencent\Weixin"


def _read_registry() -> tuple[str | None, str | None]:
    """读 HKCU\\Software\\Tencent\\Weixin 的 InstallPath / Version（微信 4.x 写入处）。"""
    if not WIN:
        return None, None
    import winreg  # noqa: PLC0415（仅 Windows，延迟导入避免跨平台加载失败）

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            install = winreg.QueryValueEx(k, "InstallPath")[0] if _has_value(k, "InstallPath") else None
            version = winreg.QueryValueEx(k, "Version")[0] if _has_value(k, "Version") else None
            return install, version
    except OSError:
        return None, None


def _has_value(key: object, name: str) -> bool:
    import winreg  # noqa: PLC0415

    try:
        winreg.QueryValueEx(key, name)  # type: ignore[arg-type]
        return True
    except OSError:
        return False


def _version_to_str(raw: object) -> str | None:
    """注册表 Version 是微信自定义 DWORD，不可靠；仅在能对齐 a.b.c.d 时使用。"""
    if raw is None:
        return None
    try:
        v = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(raw)
    parts = [(v >> s) & m for s, m in ((28, 0xF), (24, 0xF), (16, 0xFF), (0, 0xFFFF))]
    # 合理域检查：major 应在 1..9（微信注册表值常为脏数据, 如 0xF2 开头）
    if not 1 <= parts[0] <= 9:
        return None
    return ".".join(str(p) for p in parts)


def _exe_file_version(install_path: str | None) -> str | None:
    """读 Weixin.exe 的文件版本（最可靠：4.1.13.12）。"""
    if not install_path or not WIN:
        return None
    exe = Path(install_path) / "Weixin.exe"
    if not exe.is_file():
        return None
    try:
        import ctypes.wintypes as _wt  # noqa: PLC0415

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(exe), 0, size, data):
            return None
        val = ctypes.c_void_p()
        length = _wt.UINT(0)
        if not ctypes.windll.version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(val), ctypes.byref(length)):
            return None
        # Translation 是 (lang16, charset16) 双字，拼成 080404b0 形式的块名
        pair = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint32)).contents.value
        block = f"\\StringFileInfo\\{pair & 0xFFFF:04x}{pair >> 16:04x}\\ProductVersion"
        if not ctypes.windll.version.VerQueryValueW(data, block, ctypes.byref(val), ctypes.byref(length)):
            return None
        s = ctypes.wstring_at(val.value or 0, length.value - 1) if val.value else ""
        return s or None
    except OSError:
        return None


def _ini_candidates() -> list[tuple[Path, str]]:
    """%APPDATA%\\Tencent\\xwechat\\config\\*.ini 每个文件内容都是一个数据根路径。"""
    out: list[tuple[Path, str]] = []
    conf = Path(os.environ.get("APPDATA", "")) / "Tencent" / "xwechat" / "config"
    if not conf.is_dir():
        return out
    for ini in sorted(conf.glob("*.ini")):
        try:
            text = ini.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text and len(text) < 120 and not text.startswith("["):
            out.append((Path(text), f"配置文件 {ini.name}"))
    return out


def _common_roots() -> list[tuple[Path, str]]:
    """常见位置兜底（Documents / 用户目录 / 各盘根下的 xwechat_files 或其父目录）。"""
    out: list[tuple[Path, str]] = []
    home = Path.home()
    docs = Path(os.environ.get("USERPROFILE", str(home))) / "Documents"
    seen: set[Path] = set()
    candidates = [docs / "xwechat_files", home / "xwechat_files"]
    # 各盘根：xwechat_files 直接在根，或「数据目录/xwechat_files」（用户自定义目录）
    if WIN:
        for letter in "CDEFGH":
            root = Path(f"{letter}:/")
            if root.is_dir():
                candidates.append(root / "xwechat_files")
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c.is_dir():
            out.append((c, f"常见位置 {c}"))
    return out


def _find_data_root() -> tuple[Path | None, str | None]:
    """数据根 = 含 xwechat_files 的目录。返回 (data_root, 来源说明)。"""
    # 1) ini 指定路径（最权威：微信自己写的）
    for raw, src in _ini_candidates():
        # ini 内容可能是「xwechat_files 的父目录」也可能直接就是 xwechat_files
        if raw.name == "xwechat_files" and raw.is_dir():
            return raw.parent, src
        child = raw / "xwechat_files"
        if child.is_dir():
            return raw, src
    # 2) 常见位置
    for xf, src in _common_roots():
        if xf.is_dir():
            return xf.parent, src
    return None, None


# ---------------------------------------------------------------- 账号目录解析


def _scan_account(root: Path) -> WeChatAccount:
    """枚举一个账号目录下的核心数据库（只统计 .db，忽略 -wal/-shm/material 副本）。"""
    acc = WeChatAccount(key=root.name, wxid=root.name.split("_2")[0] if "_2" in root.name else root.name, root=root)
    storage = root / "db_storage"
    if not storage.is_dir():
        return acc
    buckets = {
        "message": (storage / "message", "message_dbs"),
        "biz": (storage / "message", "biz_dbs"),
        "contact": (storage / "contact", "contact_dbs"),
        "session": (storage / "session", "session_dbs"),
    }
    for folder_name, attr in buckets.values():
        folder = folder_name if isinstance(folder_name, Path) else storage / folder_name
        if not folder.is_dir():
            continue
        target: list[Path] = getattr(acc, attr)
        for db in sorted(folder.glob("*.db")):
            if db.name.endswith(("-wal", "-shm")):
                continue
            if attr == "biz_dbs" and not db.name.startswith("biz_message"):
                continue
            if attr == "message_dbs" and db.name.startswith(("biz_message", "media")):
                continue
            try:
                acc.total_db_bytes += db.stat().st_size
                target.append(db)
            except OSError:
                continue
    return acc


# ---------------------------------------------------------------- 进程枚举

_k32: ctypes.WinDLL | None = None
_psapi: ctypes.WinDLL | None = None


def _load_windows_api() -> bool:
    global _k32, _psapi
    if not WIN:
        return False
    if _k32 is not None:
        return True
    try:
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _psapi = ctypes.WinDLL("psapi", use_last_error=True)
        assert _k32 is not None and _psapi is not None  # noqa: S101（类型收窄）
        _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        _k32.OpenProcess.restype = wt.HANDLE
        _k32.CloseHandle.argtypes = [wt.HANDLE]
        _psapi.EnumProcesses.argtypes = [ctypes.POINTER(ctypes.c_uint32), wt.DWORD, ctypes.POINTER(wt.DWORD)]
        _psapi.EnumProcesses.restype = wt.BOOL
        _psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
        _psapi.GetModuleBaseNameW.restype = wt.DWORD
        return True
    except OSError:
        return False


def find_weixin_pids() -> list[int]:
    """枚举正在运行的 Weixin.exe（密钥扫描目标）。失败返回空列表，不抛。"""
    if not _load_windows_api():
        return []
    assert _psapi is not None and _k32 is not None  # noqa: S101（load 成功即非空）
    arr = (ctypes.c_uint32 * 2048)()
    needed = wt.DWORD(0)
    if not _psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return []
    out: list[int] = []
    buf = ctypes.create_unicode_buffer(260)
    for pid in arr[: needed.value // 4]:
        if not pid:
            continue
        h = _k32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
        if not h:
            continue
        try:
            if _psapi.GetModuleBaseNameW(h, None, buf, 260):
                if buf.value.lower() in ("weixin.exe", "wechat.exe"):
                    out.append(pid)
        finally:
            _k32.CloseHandle(h)
    return sorted(out)


# ---------------------------------------------------------------- 主入口


def detect() -> WeChatEnv:
    """完整环境检测（幂等、只读、任何失败降级为 blocker 文案）。"""
    env = WeChatEnv()
    install, version_raw = _read_registry()
    if install:
        env.installed = Path(install).is_dir()
        env.install_path = install
        env.version = _exe_file_version(install) or _version_to_str(version_raw)
        env.checks.append({"step": "registry", "ok": env.installed, "detail": install})
    else:
        # 注册表没有 ≠ 没装：旧版/绿色版场景靠进程或数据目录兜底判断
        env.checks.append({"step": "registry", "ok": False, "detail": "未写入注册表"})

    root, src = _find_data_root()
    if root is not None:
        env.data_root = root
        env.data_root_source = src
        env.installed = True
        xf = root / "xwechat_files"
        for d in sorted(xf.iterdir()) if xf.is_dir() else []:
            if d.is_dir() and d.name.startswith("wxid_") and (d / "db_storage").is_dir():
                env.accounts.append(_scan_account(d))
        env.checks.append(
            {"step": "data_root", "ok": True, "detail": f"{src} → {root}，账号 {len(env.accounts)} 个"}
        )
    else:
        env.checks.append({"step": "data_root", "ok": False, "detail": "常见位置未发现 xwechat_files"})

    env.weixin_pids = find_weixin_pids() if WIN else []
    env.checks.append(
        {"step": "weixin_process", "ok": bool(env.weixin_pids), "detail": f"pids={env.weixin_pids}"}
    )
    return env
