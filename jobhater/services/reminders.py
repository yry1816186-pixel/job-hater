"""提醒与跟进建议。

诚实语义：
- 手动提醒是用户事实（due_at/title/kind），可完成/撤销/删除；
- 「建议」是确定性规则推导（不是 AI）：数据满足条件才出现，条件消失即消失；
- 建议不落库——用户采纳（POST /api/reminders）后才成为真实提醒；
- 已存在同 owner+kind 的未完成提醒时不再重复建议（防轰炸）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from jobhater.db.connection import transaction

# 建议规则的确定性参数（数据层默认；调参即改这里）
FOLLOWUP_STALE_DAYS = 7  # 投递后 N 天无任何事件 → 建议跟进
INTERVIEW_PREP_WINDOW_DAYS = 3  # 面试前 N 天内 → 建议完成准备
OFFER_DECISION_WINDOW_DAYS = 7  # Offer 截止前 N 天内 → 建议决策

_OWNER_KINDS = ("application", "interview", "offer")
_IN_PROGRESS_APP_STATUSES = ("applied_confirmed", "assessment", "interviewing")


class ReminderError(ValueError):
    """非法提醒操作（owner 不存在、due_at 不合法等）。"""


def _parse_due(v: str) -> dt.date:
    """接受 YYYY-MM-DD 或完整 ISO 时间；日期非法即拒绝。"""
    s = (v or "").strip()
    if not s:
        raise ReminderError("due_at 不能为空")
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        raise ReminderError(f"due_at 不是合法日期: {v!r}") from None


def _fmt_due(d: dt.date) -> str:
    return d.isoformat()


class RemindersService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 手动提醒 CRUD ----------

    def create(
        self, owner_kind: str, owner_id: str, due_at: str, title: str,
        *, kind: str | None = None,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ReminderError("提醒标题不能为空")
        if owner_kind not in _OWNER_KINDS:
            raise ReminderError(f"owner_kind 必须是 {'/'.join(_OWNER_KINDS)}: {owner_kind!r}")
        due = _parse_due(due_at)
        tables = {"application": "applications", "interview": "interviews", "offer": "offers"}
        with transaction(self.con):
            if not self.con.execute(
                f"SELECT 1 FROM {tables[owner_kind]} WHERE id=?", (owner_id,)  # noqa: S608
            ).fetchone():
                raise ReminderError(f"{owner_kind} 不存在: {owner_id}")
            cur = self.con.execute(
                """INSERT INTO reminders(owner_kind, owner_id, due_at, kind, title)
                   VALUES (?,?,?,?,?)""",
                (owner_kind, owner_id, _fmt_due(due), kind, title),
            )
            if cur.lastrowid is None:  # INSERT 成功必有值；收窄供类型检查
                raise ReminderError("写入提醒失败（未取得自增 ID）")
            new_id = cur.lastrowid
        return self.get(new_id)  # type: ignore[return-value]

    def get(self, reminder_id: int) -> dict | None:
        row = self.con.execute(_LIST_SQL + " WHERE r.id=?", (reminder_id,)).fetchone()
        return _embed(row) if row else None

    def list(
        self, *, done: bool = False, include_done: bool = False, owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> list[dict]:
        where = [] if include_done else ["r.done=?"]
        params: list = [] if include_done else [int(done)]
        if owner_kind:
            where.append("r.owner_kind=?")
            params.append(owner_kind)
        if owner_id:
            where.append("r.owner_id=?")
            params.append(owner_id)
        sql = _LIST_SQL
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.done ASC, r.due_at ASC, r.id ASC"
        rows = self.con.execute(sql, params).fetchall()
        return [_embed(r) for r in rows]

    def set_done(self, reminder_id: int, done: bool) -> dict:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE reminders SET done=? WHERE id=?", (int(done), reminder_id)
            )
            if cur.rowcount == 0:
                raise ReminderError(f"提醒不存在: {reminder_id}")
        return self.get(reminder_id)  # type: ignore[return-value]

    def delete(self, reminder_id: int) -> None:
        with transaction(self.con):
            cur = self.con.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
            if cur.rowcount == 0:
                raise ReminderError(f"提醒不存在: {reminder_id}")

    # ---------- 确定性建议 ----------

    def suggestions(self, *, profile_id: str | None = None) -> list[dict]:
        """规则推导的跟进建议。不落库；每条可直接作为 create() 的入参。"""
        out: list[dict] = []
        out.extend(self._stale_application_suggestions(profile_id))
        out.extend(self._interview_prep_suggestions(profile_id))
        out.extend(self._offer_deadline_suggestions(profile_id))
        out.sort(key=lambda s: s["due_at"])
        return out

    def _has_open_reminder(self, owner_kind: str, owner_id: str, kind: str) -> bool:
        return bool(
            self.con.execute(
                "SELECT 1 FROM reminders WHERE owner_kind=? AND owner_id=? AND kind=? AND done=0",
                (owner_kind, owner_id, kind),
            ).fetchone()
        )

    def _stale_application_suggestions(self, profile_id: str | None) -> list[dict]:
        today = dt.date.today()
        cutoff = (today - dt.timedelta(days=FOLLOWUP_STALE_DAYS)).isoformat()
        sql = """
        SELECT a.id, a.status, a.updated_at, p.title, p.employer_name
        FROM applications a JOIN job_postings p ON p.id = a.job_id
        WHERE a.status IN (?,?,?)
        """
        params: list[str] = list(_IN_PROGRESS_APP_STATUSES)
        if profile_id:
            sql += " AND a.profile_id=?"
            params.append(profile_id)
        rows = self.con.execute(sql, params).fetchall()  # noqa: S608
        out: list[dict] = []
        for r in rows:
            ref = (r["updated_at"] or "")[:10]
            if not ref or ref > cutoff:
                continue  # 近期有动静（updated_at 由状态/事件维护）→ 不建议
            if self._has_open_reminder("application", r["id"], "followup"):
                continue
            stale_days = max((today - dt.date.fromisoformat(ref)).days, FOLLOWUP_STALE_DAYS)
            out.append({
                "kind": "followup",
                "owner_kind": "application",
                "owner_id": r["id"],
                "title": f"跟进：{r['employer_name']} · {r['title']}（已 {stale_days} 天无进展）",
                "due_at": _fmt_due(today),
                "reason": f"投递处于 {r['status']} 状态且 {stale_days} 天无任何更新",
            })
        return out

    def _interview_prep_suggestions(self, profile_id: str | None) -> list[dict]:
        today = dt.date.today()
        horizon = (today + dt.timedelta(days=INTERVIEW_PREP_WINDOW_DAYS)).isoformat()
        sql = """
        SELECT i.id, i.scheduled_at, i.round, p.title, p.employer_name
        FROM interviews i
        JOIN applications a ON a.id = i.application_id
        JOIN job_postings p ON p.id = a.job_id
        WHERE i.status='planned' AND i.scheduled_at IS NOT NULL
          AND substr(i.scheduled_at,1,10) BETWEEN ? AND ?
        """
        params: list[str] = [today.isoformat(), horizon]
        if profile_id:
            sql += " AND a.profile_id=?"
            params.append(profile_id)
        rows = self.con.execute(sql, params).fetchall()  # noqa: S608
        out: list[dict] = []
        for r in rows:
            if self._has_open_reminder("interview", r["id"], "interview_prep"):
                continue
            d = dt.date.fromisoformat(r["scheduled_at"][:10])
            left = (d - today).days
            out.append({
                "kind": "interview_prep",
                "owner_kind": "interview",
                "owner_id": r["id"],
                "title": f"面试准备：{r['employer_name']} · {r['title']} 第{r['round']}轮"
                         + ("（就是今天！）" if left == 0 else f"（还有 {left} 天）"),
                "due_at": _fmt_due(today),
                "reason": "面试在准备窗口内，建议过一遍面试题清单与材料工坊",
            })
        return out

    def _offer_deadline_suggestions(self, profile_id: str | None) -> list[dict]:
        today = dt.date.today()
        horizon = (today + dt.timedelta(days=OFFER_DECISION_WINDOW_DAYS)).isoformat()
        sql = """
        SELECT o.id, o.deadline, o.base_salary_k, p.employer_name, p.title
        FROM offers o
        JOIN applications a ON a.id = o.application_id
        JOIN job_postings p ON p.id = a.job_id
        WHERE o.status='considering' AND o.deadline IS NOT NULL
          AND substr(o.deadline,1,10) BETWEEN ? AND ?
        """
        params: list[str] = [today.isoformat(), horizon]
        if profile_id:
            sql += " AND a.profile_id=?"
            params.append(profile_id)
        rows = self.con.execute(sql, params).fetchall()  # noqa: S608
        out: list[dict] = []
        for r in rows:
            if self._has_open_reminder("offer", r["id"], "deadline"):
                continue
            out.append({
                "kind": "deadline",
                "owner_kind": "offer",
                "owner_id": r["id"],
                "title": f"Offer 决策：{r['employer_name']} · {r['title']}"
                         f"（{r['base_salary_k']}k，截止 {r['deadline'][:10]}）",
                "due_at": _fmt_due(min(today, dt.date.fromisoformat(r["deadline"][:10]))),
                "reason": "Offer 处于考虑中且决策期限临近，建议用对比表完成决策",
            })
        return out


# 列表嵌入 owner 上下文：三类 owner 各自经 application → 岗位，COALESCE 取第一个命中的
_LIST_SQL = """
SELECT r.*,
       COALESCE(p1.title, p2.title, p3.title) AS owner_title,
       COALESCE(p1.employer_name, p2.employer_name, p3.employer_name) AS owner_employer,
       i.round AS owner_round, o.base_salary_k AS owner_salary_k
FROM reminders r
LEFT JOIN applications a1 ON r.owner_kind='application' AND a1.id = r.owner_id
LEFT JOIN job_postings p1 ON p1.id = a1.job_id
LEFT JOIN interviews i ON r.owner_kind='interview' AND i.id = r.owner_id
LEFT JOIN applications a2 ON a2.id = i.application_id
LEFT JOIN job_postings p2 ON p2.id = a2.job_id
LEFT JOIN offers o ON r.owner_kind='offer' AND o.id = r.owner_id
LEFT JOIN applications a3 ON a3.id = o.application_id
LEFT JOIN job_postings p3 ON p3.id = a3.job_id
"""


def _embed(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["done"] = bool(d["done"])
    return d


__all__ = ["RemindersService", "ReminderError"]
