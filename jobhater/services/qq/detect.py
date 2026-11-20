"""QQ NT 环境探测（只读，Windows）。

与微信 detect 同款职责：给出「数据在哪、QQ 在不在跑、什么挡住了我们」。
QQ 特有实况（2026-11-20 实测）：QQ NT 在本机可能已卸载，但历史数据仍在
（常见于目录重定向到云盘，如 D:\\小米云盘\\文档\\Tencent Files）——此时
消息库解密通道天然 blocked（密钥只在当时的 QQ 进程内存里），图片 OCR
通道（nt_data 明文图）依然完整可用。blockers 如实分层报告。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MSG_DBS = ("nt_msg.db", "guild_msg.db")


@dataclass
class QQAccount:
    uin: str
    root: Path                    # {uin}/ 目录
    nt_db: Path                   # nt_qq/nt_db
    nt_data: Path | None          # nt_qq/nt_data（明文图片/文件）
    msg_dbs: list[str] = field(default_factory=list)
    db_mb: float = 0.0
    image_files: int = 0

    def to_dict(self) -> dict:
        return {
            "uin": self.uin,
            "root": str(self.root),
            "msg_dbs": self.msg_dbs,
            "db_mb": round(self.db_mb, 1),
            "image_files": self.image_files,
        }


@dataclass
class QQEnv:
    platform_ok: bool
    data_roots: list[str] = field(default_factory=list)   # 探测到的 Tencent Files 根
    accounts: list[QQAccount] = field(default_factory=list)
    qq_running: bool = False
    qq_install_hint: str = ""

    @property
    def blockers(self) -> list[str]:
        out = []
        if not self.platform_ok:
            out.append("仅支持 Windows（QQ 数据目录与 OCR 引擎均为 Windows 侧）")
        if not self.accounts:
            out.append("未发现 QQ NT 数据目录（默认 Documents\\Tencent Files、"
                       "云盘重定向位、或设 JOBHATER_QQ_DIR 指向 Tencent Files 根）")
        elif not self.qq_running:
            out.append("QQ 未运行：消息库解密通道不可用（密钥在 QQ 进程内存）。"
                       "图片 OCR 通道不受影响。")
        return out

    def to_dict(self) -> dict:
        return {
            "platform_ok": self.platform_ok,
            "data_roots": self.data_roots,
            "accounts": [a.to_dict() for a in self.accounts],
            "qq_running": self.qq_running,
            "blockers": self.blockers,
        }


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("JOBHATER_QQ_DIR")
    if env:
        roots.append(Path(env))
    home = Path.home()
    roots.append(home / "Documents" / "Tencent Files")
    # 云盘重定向常见位（实测本机命中 D:\小米云盘\文档\Tencent Files）
    for drive in ("D:", "E:"):
        for cloud in ("小米云盘", "百度网盘", "坚果云", "OneDrive"):
            p = Path(drive) / cloud / "文档" / "Tencent Files"
            if p.is_dir():
                roots.append(p)
    return roots


def _count_images(nd: Path | None) -> int:
    if not nd or not nd.is_dir():
        return 0
    n = 0
    for f in nd.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg") and f.stat().st_size > 10_000:
            n += 1
            if n >= 20_000:  # 防御性上限：超大目录不计瘫探测
                break
    return n


def _qq_pids() -> list[int]:
    if os.name != "nt":
        return []
    try:
        import ctypes

        psapi = ctypes.WinDLL("psapi")
        k32 = ctypes.WinDLL("kernel32")
        arr = (ctypes.c_uint32 * 2048)()
        needed = ctypes.c_uint32(0)
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        out: list[int] = []
        buf = ctypes.create_unicode_buffer(260)
        for pid in arr[: needed.value // 4]:
            if not pid:
                continue
            h = k32.OpenProcess(0x0410, False, pid)
            if not h:
                continue
            try:
                if psapi.GetModuleBaseNameW(h, None, buf, 260) and buf.value.lower() == "qq.exe":
                    out.append(pid)
            finally:
                k32.CloseHandle(h)
        return out
    except Exception:
        return []


def detect() -> QQEnv:
    env = QQEnv(platform_ok=os.name == "nt")
    if not env.platform_ok:
        return env
    for root in _candidate_roots():
        if not root.is_dir():
            continue
        env.data_roots.append(str(root))
        for uin_dir in sorted(root.iterdir()):
            if not (uin_dir.is_dir() and uin_dir.name.isdigit()):
                continue
            nt_qq = uin_dir / "nt_qq"
            nt_db = nt_qq / "nt_db"
            if not nt_db.is_dir():
                continue
            dbs = [n for n in MSG_DBS if (nt_db / n).exists()]
            if not dbs:
                continue
            db_mb = sum((nt_db / n).stat().st_size for n in dbs) / 1_048_576
            env.accounts.append(QQAccount(
                uin=uin_dir.name, root=uin_dir, nt_db=nt_db,
                nt_data=nt_qq / "nt_data" if (nt_qq / "nt_data").is_dir() else None,
                msg_dbs=dbs, db_mb=round(db_mb, 1),
                image_files=_count_images(nt_qq / "nt_data"),
            ))
    env.qq_running = bool(_qq_pids())
    return env
