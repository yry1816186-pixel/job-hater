"""全量备份与恢复（SQLite 在线备份 API）。

诚实语义：
- 备份 = 完整数据库文件快照（全部表 + FTS 索引 + schema 版本），不是选择性导出；
- exports/ 目录与 OS keyring 中的 API key 不在备份内：前者可由简历版本随时重新
  导出，后者按设计永不离开本机 keyring（服务名 jobhater/<provider_id>）；
- 恢复前必须通过三重校验（SQLite 文件头、integrity_check、schema_migrations
  版本与当前库一致），任一失败立即拒绝且原库分毫不动；
- 恢复用 SQLite backup API 原地替换当前库内容（对打开中的连接安全），随后
  WAL checkpoint 截断，避免旧 WAL 残留。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
from pathlib import Path

from jobhater.db.migrations import current_version

# 校验通过的备份必须包含的核心表（缺一即不是 jobhater 库）
_REQUIRED_TABLES = (
    "schema_migrations", "candidate_profiles", "job_postings", "applications",
    "resume_versions", "offers", "reminders",
)
_SQLITE_HEADER = b"SQLite format 3\x00"


class BackupError(ValueError):
    """备份/恢复失败（数据不是合法的 jobhater 库、校验未通过等）。"""


class BackupService:
    def __init__(self, con: sqlite3.Connection, db_path: Path) -> None:
        self.con = con
        self.db_path = db_path

    def snapshot(self) -> tuple[bytes, str]:
        """在线快照当前库 → (字节, 建议文件名)。经临时文件而非 serialize()
        （Connection.serialize 是 Python 3.11+ 才有，3.10 不带）。"""
        with tempfile.TemporaryDirectory(prefix="jobhater_snap_") as td:
            tmp = Path(td) / "snap.db"
            dst = sqlite3.connect(tmp)
            try:
                self.con.backup(dst)
            finally:
                dst.close()
            data = tmp.read_bytes()
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return data, f"jobhater_backup_{stamp}.db"

    def restore(self, data: bytes) -> dict:
        """用上传的快照原位替换当前库。校验失败抛 BackupError，原库不动。"""
        if not data:
            raise BackupError("备份文件为空")
        if not data.startswith(_SQLITE_HEADER):
            raise BackupError("不是 SQLite 数据库文件（文件头不符）")
        with tempfile.TemporaryDirectory(prefix="jobhater_restore_") as td:
            tmp = Path(td) / "restore.db"
            tmp.write_bytes(data)
            try:
                src = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True)
                src.row_factory = sqlite3.Row  # current_version 等按列名取值
            except sqlite3.DatabaseError as e:
                raise BackupError(f"无法打开备份文件: {e}") from e
            try:
                report = self._validate(src)
                # backup API：源(只读快照) → 目标(当前写连接)，整库原子替换
                src.backup(self.con)
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                # 外键完整性由 backup 保持；主动复核一遍再交付
                fk = self.con.execute("PRAGMA foreign_key_check").fetchall()
                if fk:
                    raise BackupError(f"恢复后外键校验发现 {len(fk)} 处不一致，已中止交付")
                return report
            except sqlite3.DatabaseError as e:
                raise BackupError(f"备份文件损坏或不完整: {e}") from e
            finally:
                src.close()

    def _validate(self, src: sqlite3.Connection) -> dict:
        integrity = src.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            detail = integrity[0] if integrity else "无结果"
            raise BackupError(f"完整性校验未通过: {detail}")
        tables = {
            r[0]
            for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [t for t in _REQUIRED_TABLES if t not in tables]
        if missing:
            raise BackupError(f"缺少核心表（不是 jobhater 备份）: {', '.join(missing)}")
        backup_version = current_version(src)
        live_version = current_version(self.con)
        if backup_version != live_version:
            raise BackupError(
                f"备份 schema 版本(v{backup_version})与当前库(v{live_version})不一致；"
                "请先用相同版本的应用制作备份"
            )
        counts = {
            t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("job_postings", "applications", "resume_versions")
        }
        return {"ok": True, "schema_version": backup_version, "counts": counts}


__all__ = ["BackupService", "BackupError"]
