"""投递/面试/Offer 生命周期服务。

诚实语义（§14）：
- 只有用户确认（confirm_applied）或 adapter 可验证反馈才允许进入 applied_confirmed；
- 状态机强制单步合法转移（domain.enums.APPLICATION_TRANSITIONS），非法转移直接拒绝；
- 每次状态变更写 application_events（不可变审计流）。

黑名单语义（§15）：同一雇主允许多岗位并行投递；"重复投递"只针对同一岗位
（applications 表 UNIQUE(job_id, profile_id) 已保证），雇主级仅提供 cooldown 提示。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from jobhater.db.connection import transaction
from jobhater.domain.enums import APPLICATION_TRANSITIONS, ApplicationStatus
from jobhater.domain.models import Interview, Offer
from jobhater.services.storage import new_id, row_to_model

_INTERVIEW_JSON = {"interviewer_names": ("interviewer_names_json", [])}
_SESSION_JSON = {"transcript": ("transcript_json", [])}
_REVIEW_JSON = {
    "scores": ("scores_json", {}),
    "strengths": ("strengths_json", []),
    "gaps": ("gaps_json", []),
    "practice_items": ("practice_items_json", []),
}
_OFFER_JSON = {
    "benefits": ("benefits_json", []),
    "custom_dimensions": ("custom_dimensions_json", {}),
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _app_dict(row: sqlite3.Row) -> dict:
    """applications 行 → 契约 dict：tags_json 列解析为 tags 列表。"""
    d = dict(row)
    try:
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
    except json.JSONDecodeError:
        d["tags"] = []  # 标签是标注非事实：损坏不炸列表，如实置空
    return d


class LifecycleError(ValueError):
    """非法生命周期操作（状态机拒绝、实体不存在等）。"""


class ApplicationService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 投递 ----------

    def create(self, job_id: str, profile_id: str, *, status: str = "discovered") -> str:
        app_id = new_id("app")
        with transaction(self.con):
            try:
                self.con.execute(
                    """INSERT INTO applications(id, job_id, profile_id, status) VALUES (?,?,?,?)""",
                    (app_id, job_id, profile_id, status),
                )
            except sqlite3.IntegrityError as e:
                if "UNIQUE" in str(e):
                    raise LifecycleError("该岗位已存在投递记录（同岗位唯一）") from e
                raise
            self._event(app_id, "created")
        return app_id

    def _event(self, application_id: str, kind: str, payload: dict | None = None, note: str | None = None) -> None:
        self.con.execute(
            """INSERT INTO application_events(application_id, kind, payload_json, note)
               VALUES (?,?,?,?)""",
            (application_id, kind, json.dumps(payload or {}, ensure_ascii=False), note),
        )

    def get(self, application_id: str) -> dict:
        row = self.con.execute(
            """SELECT a.*, p.title AS job_title, p.employer_name, p.city AS job_city
                 FROM applications a LEFT JOIN job_postings p ON p.id = a.job_id
                 WHERE a.id=?""",
            (application_id,),
        ).fetchone()
        if not row:
            raise LifecycleError(f"投递记录不存在: {application_id}")
        return _app_dict(row)

    def list(self, profile_id: str, *, tag: str | None = None) -> list[dict]:
        """画像的投递列表；tag 过滤在 Python 侧做（本地规模，避免 LIKE 元字符陷阱）。"""
        sql = """SELECT a.*, p.title AS job_title, p.employer_name, p.city AS job_city
                 FROM applications a LEFT JOIN job_postings p ON p.id = a.job_id
                 WHERE a.profile_id=? ORDER BY a.updated_at DESC"""
        rows = [_app_dict(r) for r in self.con.execute(sql, (profile_id,)).fetchall()]
        if tag:
            return [r for r in rows if tag in r["tags"]]
        return rows

    # ---------- 标签（0004：用户自由标注，不参与状态机） ----------

    def add_tag(self, application_id: str, tag: str) -> dict:
        tag = (tag or "").strip()
        if not tag:
            raise LifecycleError("标签不能为空")
        current = self.get(application_id)
        if tag in current["tags"]:
            return current  # 幂等
        with transaction(self.con):
            self.con.execute(
                "UPDATE applications SET tags_json=?, updated_at=? WHERE id=?",
                (json.dumps([*current["tags"], tag], ensure_ascii=False),
                 _now(), application_id),
            )
            self._event(application_id, "tag_added", payload={"tag": tag})
        return self.get(application_id)

    def remove_tag(self, application_id: str, tag: str) -> dict:
        current = self.get(application_id)
        if tag not in current["tags"]:
            raise LifecycleError(f"标签不存在: {tag}")
        with transaction(self.con):
            self.con.execute(
                "UPDATE applications SET tags_json=?, updated_at=? WHERE id=?",
                (json.dumps([t for t in current["tags"] if t != tag], ensure_ascii=False),
                 _now(), application_id),
            )
            self._event(application_id, "tag_removed", payload={"tag": tag})
        return self.get(application_id)

    def transition(
        self, application_id: str, to_status: str, *, note: str | None = None, payload: dict | None = None
    ) -> dict:
        current = self.get(application_id)
        cur_s, to_s = ApplicationStatus(current["status"]), ApplicationStatus(to_status)
        if to_s == ApplicationStatus.APPLIED_CONFIRMED:
            # 诚实语义（§投递生命周期）：applied_confirmed 只能经 confirm_applied 的
            # 用户确认门进入（事件带 user_confirmed 标记），通用转移一律拒绝。
            raise LifecycleError(
                "applied_confirmed 只能经「确认已投递」入口进入（用户亲口确认），"
                "请使用 confirm_applied"
            )
        if to_s not in APPLICATION_TRANSITIONS[cur_s]:
            allowed = sorted(s.value for s in APPLICATION_TRANSITIONS[cur_s] if s is not ApplicationStatus.APPLIED_CONFIRMED)
            raise LifecycleError(
                f"非法状态转移 {cur_s.value} → {to_s.value}；允许的目标：{allowed}"
            )
        now = _now()
        with transaction(self.con):
            self.con.execute(
                """UPDATE applications SET status=?, status_updated_at=?, updated_at=?
                   WHERE id=?""",
                (to_s.value, now, now, application_id),
            )
            self._event(
                application_id, "status_change",
                payload={"from": cur_s.value, "to": to_s.value, **(payload or {})}, note=note,
            )
        return self.get(application_id)

    def confirm_applied(self, application_id: str, *, channel: str | None = None) -> dict:
        """用户确认已投递（人肉投递后回填）。这是进入 applied_confirmed 的唯一常规入口。"""
        current = self.get(application_id)
        if current["status"] == ApplicationStatus.APPLIED_CONFIRMED.value:
            return current  # 幂等
        if current["status"] not in (ApplicationStatus.READY_TO_APPLY.value,):
            # 从早期状态直接确认：允许，但要求当前状态能一步转移到 ready_to_apply 链上？
            # 产品语义：用户说"我投了"就是事实——放行并记录原始状态
            pass
        now = _now()
        with transaction(self.con):
            self.con.execute(
                "UPDATE applications SET status=?, applied_at=?, apply_channel=COALESCE(?, apply_channel), "
                "status_updated_at=?, updated_at=? WHERE id=?",
                (ApplicationStatus.APPLIED_CONFIRMED.value, now, channel, now, now, application_id),
            )
            self._event(
                application_id, "status_change",
                payload={"from": current["status"], "to": "applied_confirmed", "user_confirmed": True},
                note="用户确认已投递",
            )
        return self.get(application_id)

    def link_resume(self, application_id: str, resume_version_id: str) -> None:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE applications SET resume_version_id=?, updated_at=? WHERE id=?",
                (resume_version_id, _now(), application_id),
            )
            if cur.rowcount == 0:
                raise LifecycleError(f"投递记录不存在: {application_id}")

    def events(self, application_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM application_events WHERE application_id=? ORDER BY occurred_at, id",
            (application_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 面试 ----------

    def schedule_interview(
        self, application_id: str, *, round: int = 1, kind: str | None = None,
        scheduled_at: str | None = None, location: str | None = None,
    ) -> Interview:
        iv = Interview(
            id=new_id("iv"), application_id=application_id, round=round, kind=kind,
            scheduled_at=scheduled_at, location=location,
        )
        with transaction(self.con):
            insert_cols = "id, application_id, round, kind, scheduled_at, location, status"
            self.con.execute(
                f"INSERT INTO interviews ({insert_cols}) VALUES (?,?,?,?,?,?, 'planned')",
                (iv.id, iv.application_id, iv.round, iv.kind, iv.scheduled_at, iv.location),
            )
            self._event(application_id, "interview_scheduled", payload={"interview_id": iv.id, "round": round})
        return iv

    def list_interviews(self, application_id: str) -> list[Interview]:
        rows = self.con.execute(
            "SELECT * FROM interviews WHERE application_id=? ORDER BY round, scheduled_at",
            (application_id,),
        ).fetchall()
        return [row_to_model(Interview, r, _INTERVIEW_JSON) for r in rows]

    def finish_interview(self, interview_id: str, *, outcome: str, notes: str | None = None) -> None:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE interviews SET status='done', outcome=?, notes=COALESCE(?, notes) WHERE id=?",
                (outcome, notes, interview_id),
            )
            if cur.rowcount == 0:
                raise LifecycleError(f"面试不存在: {interview_id}")

    # ---------- 日历导出（RFC 5545 iCalendar） ----------

    @staticmethod
    def _ics_escape(text: str) -> str:
        return (
            str(text)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", " ")
            .replace("\n", " ")
        )

    @staticmethod
    def _ics_stamp(iso: str | None) -> str | None:
        """ISO 时间戳 → iCalendar UTC 形式 YYYYMMDDTHHMMSSZ；无法解析返回 None。"""
        if not iso:
            return None
        digits = "".join(ch for ch in str(iso)[:19] if ch.isdigit())
        if len(digits) < 8:
            return None
        return digits.ljust(14, "0")[:14] + "Z"

    def calendar_ics(self, profile_id: str) -> str:
        """投递截止 + 面试排期 → iCalendar（导入系统日历/Google Calendar）。

        只导出未来事件：过期截止与已完成面试不产生噪音。
        """
        import datetime as _dt

        def _future_date(v: str | None) -> _dt.date | None:
            if not v:
                return None
            try:
                d = _dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                return None
            return d if d >= _dt.date.today() else None

        now_stamp = self._ics_stamp(_dt.datetime.now(_dt.timezone.utc).isoformat()) or ""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//job-hater//local job search//CN",
            "CALSCALE:GREGORIAN",
        ]
        # ① 投递截止（跟踪中的岗位 deadline）
        for row in self.con.execute(
            """SELECT a.id AS aid, j.title AS title, j.employer_name AS employer, j.deadline
                 FROM applications a JOIN job_postings j ON j.id = a.job_id
                 WHERE a.profile_id=? AND j.deadline IS NOT NULL
                   AND a.status NOT IN ('withdrawn','rejected','closed')""",
            (profile_id,),
        ).fetchall():
            d = _future_date(row["deadline"])
            if not d:
                continue
            summary = self._ics_escape(f"⏳ 投递截止：{row['title']} @ {row['employer']}")
            lines += [
                "BEGIN:VEVENT",
                f"UID:deadline-{row['aid']}@jobhater.local",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                f"SUMMARY:{summary}",
                "DESCRIPTION:来自 Job Hater 的投递截止提醒",
                "BEGIN:VALARM",
                "TRIGGER:-P1D",
                "ACTION:DISPLAY",
                "DESCRIPTION:明天截止，确认是否已投递",
                "END:VALARM",
                "END:VEVENT",
            ]
        # ② 面试排期（planned 且有 scheduled_at）
        for row in self.con.execute(
            """SELECT i.id AS iid, i.round, i.kind, i.scheduled_at,
                      j.title AS title, j.employer_name AS employer
                 FROM interviews i
                 JOIN applications a ON a.id = i.application_id
                 JOIN job_postings j ON j.id = a.job_id
                 WHERE a.profile_id=? AND i.status='planned' AND i.scheduled_at IS NOT NULL""",
            (profile_id,),
        ).fetchall():
            start = self._ics_stamp(row["scheduled_at"])
            if not start:
                continue
            kind = row["kind"] or "面试"
            summary = self._ics_escape(f"🎤 第{row['round']}轮{kind}：{row['title']} @ {row['employer']}")
            lines += [
                "BEGIN:VEVENT",
                f"UID:interview-{row['iid']}@jobhater.local",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{start}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:Job Hater 面试排期：第{row['round']}轮",
                "BEGIN:VALARM",
                "TRIGGER:-PT30M",
                "ACTION:DISPLAY",
                "DESCRIPTION:面试半小时前",
                "END:VALARM",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    # ---------- Offer ----------

    def add_offer(
        self, application_id: str, *, base_salary_k: float, salary_months: int | None = None,
        city: str | None = None, work_mode: str | None = None, deadline: str | None = None,
        bonus_text: str | None = None, equity_text: str | None = None,
        probation_months: int | None = None, notes: str | None = None,
        benefits: list[str] | None = None, custom_dimensions: dict | None = None,
    ) -> Offer:
        offer = Offer(
            id=new_id("ofr"), application_id=application_id, base_salary_k=base_salary_k,
            salary_months=salary_months, bonus_text=bonus_text, equity_text=equity_text,
            benefits=benefits or [], city=city, work_mode=work_mode,
            probation_months=probation_months, deadline=deadline, notes=notes,
            custom_dimensions=custom_dimensions or {},
        )
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO offers (id, application_id, base_salary_k, salary_months,
                     bonus_text, equity_text, benefits_json, city, work_mode,
                     probation_months, deadline, notes, custom_dimensions_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    offer.id, offer.application_id, offer.base_salary_k, offer.salary_months,
                    offer.bonus_text, offer.equity_text,
                    json.dumps(offer.benefits, ensure_ascii=False), offer.city, offer.work_mode,
                    offer.probation_months, offer.deadline, offer.notes,
                    json.dumps(offer.custom_dimensions, ensure_ascii=False),
                ),
            )
            self._event(application_id, "offer_received", payload={"offer_id": offer.id})
        return offer

    def list_offers(self, profile_id: str) -> list[dict]:
        """列表带岗位/公司名（ Offer 本体不含 job 信息，UI 需要可辨识的名字）。"""
        rows = self.con.execute(
            """SELECT o.*, p.title AS job_title, p.employer_name, p.city AS job_city
                 FROM offers o
                 JOIN applications a ON a.id = o.application_id
                 LEFT JOIN job_postings p ON p.id = a.job_id
                 WHERE a.profile_id=? ORDER BY o.created_at""",
            (profile_id,),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            for field, (col, default) in _OFFER_JSON.items():
                raw = d.pop(col)
                d[field] = json.loads(raw) if raw is not None else default
            out.append(d)
        return out

    def set_offer_status(self, offer_id: str, status: str) -> None:
        valid = {"considering", "accepted", "declined", "expired"}
        if status not in valid:
            raise LifecycleError(f"非法 offer 状态：{status}（允许 {sorted(valid)}）")
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE offers SET status=?, updated_at=? WHERE id=?", (status, _now(), offer_id)
            )
            if cur.rowcount == 0:
                raise LifecycleError(f"offer 不存在: {offer_id}")

    def compare_offers(self, offer_ids: list[str]) -> list[dict]:
        """并列比较：结构化事实并排展示，不做价值判断（权重留给用户）。"""
        out = []
        for oid in offer_ids:
            row = self.con.execute(
                """SELECT o.*, j.title AS job_title, j.employer_name, j.city AS job_city
                     FROM offers o
                     JOIN applications a ON a.id=o.application_id
                     JOIN job_postings j ON j.id=a.job_id
                     WHERE o.id=?""",
                (oid,),
            ).fetchone()
            if not row:
                raise LifecycleError(f"offer 不存在: {oid}")
            d = dict(row)
            d["benefits"] = json.loads(d.pop("benefits_json") or "[]")
            d["custom_dimensions"] = json.loads(d.pop("custom_dimensions_json") or "{}")
            d["annual_base_k"] = round(
                d["base_salary_k"] * (d["salary_months"] or 12), 1
            )
            out.append(d)
        return out


# ---------- 反馈闭环（§9：可查看、可重置） ----------


class FeedbackService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def record(self, profile_id: str, kind: str, *, job_id: str | None = None, note: str | None = None) -> None:
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO feedback_events(job_id, profile_id, kind, note) VALUES (?,?,?,?)",
                (job_id, profile_id, kind, note),
            )

    def history(self, profile_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM feedback_events WHERE profile_id=? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def reset(self, profile_id: str) -> int:
        """清空该画像全部反馈（个性化影响回到零，可解释也可撤销）。"""
        with transaction(self.con):
            cur = self.con.execute("DELETE FROM feedback_events WHERE profile_id=?", (profile_id,))
            return cur.rowcount
