"""应用路径与全局配置解析。

数据目录解析顺序（先命中先用）：
1. 环境变量 ``JOBHATER_DATA``
2. 当前工作目录下的 ``.jobhater/``（仓库内开发模式）
3. 用户主目录 ``~/.jobhater/``（普通用户默认）

所有路径惰性解析，测试中可用 ``set_data_dir`` 注入临时目录。
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "JOBHATER_DATA"


class _DataDir:
    """可注入的数据目录持有者（模块级单例的测试友好替代）。"""

    def __init__(self, override: Path | None = None) -> None:
        self._override = override

    def resolve(self) -> Path:
        if self._override is not None:
            return self._override
        env = os.environ.get(DATA_DIR_ENV)
        if env:
            return Path(env).expanduser().resolve()
        cwd_local = Path.cwd() / ".jobhater"
        if cwd_local.exists():
            return cwd_local
        return Path.home() / ".jobhater"


_holder = _DataDir()


def set_data_dir(path: Path | str | None) -> None:
    """注入/清除数据目录（测试与多实例场景）。"""
    _holder._override = Path(path).expanduser().resolve() if path is not None else None


def data_dir() -> Path:
    """业务数据根目录（数据库、导入快照、生成材料均在其下）。"""
    d = _holder.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "jobhater.db"


def snapshots_dir() -> Path:
    d = data_dir() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_root() -> Path:
    """源码仓库根（定位打包的 migrations 等资源）。"""
    return Path(__file__).resolve().parent.parent
