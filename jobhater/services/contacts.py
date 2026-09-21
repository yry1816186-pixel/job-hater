"""联系人（求职版轻量 CRM）。

诚实语义：
- 联系人是用户手工维护的事实记录（姓名/角色/联系方式/备注），系统不做任何推断；
- 可挂在投递（application）或雇主（employer）上，也可独立存在；
- 挂载对象不存在时明确报错（404 语义），不静默忽略；
- 删除即物理删除——本地数据，用户全权。
"""
from __future__ import annotations

import sqlite3

from jobhater.db.connection import transaction


class ContactError(ValueError):
    """非法联系人操作（挂载对象不存在、字段缺失等）。"""


# 列表返回嵌入上下文：投递 → 岗位/雇主名；雇主 → 雇主规范名
_LIST_SQL = """
SELECT c.*, p.title AS job_title, p.employer_name,
       e.canonical_name AS employer_name_direct
FROM contacts c
LEFT JOIN applications a ON a.id = c.application_id
LEFT JOIN job_postings p ON p.id = a.job_id
LEFT JOIN employers e ON e.id = c.employer_id
"""


def _ensure_exists(con: sqlite3.Connection, table: str, row_id: str, label: str) -> None:
    row = con.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone()  # noqa: S608
    if not row:
        raise ContactError(f"{label}不存在: {row_id}")


class ContactsService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def add(
        self, name: str, *, application_id: str | None = None, employer_id: str | None = None,
        role: str | None = None, phone: str | None = None, email: str | None = None,
        wechat: str | None = None, note: str | None = None,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ContactError("联系人姓名不能为空")
        with transaction(self.con):
            if application_id:
                _ensure_exists(self.con, "applications", application_id, "投递")
            if employer_id:
                _ensure_exists(self.con, "employers", employer_id, "雇主")
            cur = self.con.execute(
                """INSERT INTO contacts(application_id, employer_id, name, role, phone, email, wechat, note)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (application_id, employer_id, name, role, phone, email, wechat, note),
            )
            if cur.lastrowid is None:  # INSERT 成功必有值；收窄供类型检查
                raise ContactError("写入联系人失败（未取得自增 ID）")
            new_id = cur.lastrowid
        return self.get(new_id)  # type: ignore[return-value]

    def get(self, contact_id: int) -> dict | None:
        row = self.con.execute(
            _LIST_SQL + " WHERE c.id=?", (contact_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update(
        self, contact_id: int, *, name: str | None = None, role: str | None = None,
        phone: str | None = None, email: str | None = None, wechat: str | None = None,
        note: str | None = None,
    ) -> dict:
        """部分更新；传 None 的字段保持原值（清空请传空串）。"""
        current = self.con.execute(
            "SELECT * FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        if not current:
            raise ContactError(f"联系人不存在: {contact_id}")
        new_name = (name or "").strip() or current["name"]  # 姓名不允许被更新成空
        updates = {
            "role": role, "phone": phone, "email": email, "wechat": wechat, "note": note,
        }
        changed = {k: v for k, v in updates.items() if v is not None}
        with transaction(self.con):
            self.con.execute(
                "UPDATE contacts SET name=?, "
                + ",".join(f"{k}=?" for k in changed)
                + " WHERE id=?",
                (new_name, *changed.values(), contact_id),
            )
        return self.get(contact_id)  # type: ignore[return-value]

    def delete(self, contact_id: int) -> None:
        with transaction(self.con):
            cur = self.con.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
            if cur.rowcount == 0:
                raise ContactError(f"联系人不存在: {contact_id}")

    def list(
        self, *, application_id: str | None = None, employer_id: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        """按挂载对象过滤；q 对姓名/角色做包含匹配（大小写不敏感）。"""
        where: list[str] = []
        params: list = []
        if application_id:
            where.append("c.application_id=?")
            params.append(application_id)
        if employer_id:
            where.append("(c.employer_id=? OR e.id=?)")  # 直挂雇主 或 经投递间接关联
            params.extend([employer_id, employer_id])
        if q:
            where.append("(c.name LIKE ? OR c.role LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        sql = _LIST_SQL
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.id DESC"
        rows = self.con.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Contact 模型契约内嵌上下文字段（job_title/employer_name_direct → 统一 employer_name）
    d["employer_name"] = d.pop("employer_name_direct") or d.get("employer_name") or ""
    if "job_title" not in d:
        d["job_title"] = None
    return d


__all__ = ["ContactsService", "ContactError"]
