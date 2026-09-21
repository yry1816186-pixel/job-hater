"""求职漏斗与洞察统计（确定性 SQL 聚合，无 AI）。

诚实语义：
- 全部数字来自本地库的直接计数/分位数，不做任何预测或推断；
- 「已投」= applied/applied_confirmed（用户确认语义见 lifecycle）；
- 转化率分母为 0 时显示 null（不用 0% 冒充"0 转化"）；
- 薪资洞察只统计 min/max 双全的岗位，样本数随行标注——样本小不构成"行情"。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

_APPLIED_STATUSES = ("applied_confirmed", "assessment", "interviewing", "offer")
_IN_PROGRESS_STATUSES = ("applied_confirmed", "assessment", "interviewing")
_PRE_APPLY_STATUSES = (
    "discovered", "saved", "shortlisted", "preparing", "materials_ready", "ready_to_apply",
)
_WEEKS_DEFAULT = 12
_CITY_TOP_N = 12


def _percentile(sorted_vals: list[float], p: float) -> float:
    """最近邻法分位数（p∈[0,100]）。空列表由调用方排除。"""
    if not sorted_vals:
        return 0.0
    idx = round((len(sorted_vals) - 1) * p / 100)
    return sorted_vals[idx]


class StatsService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 总览 ----------

    def overview(self, profile_id: str | None = None) -> dict:
        cond, params = self._profile_cond(profile_id)
        status_rows = self.con.execute(
            f"SELECT status, COUNT(*) AS n FROM applications {cond}"
            " GROUP BY status ORDER BY n DESC",
            params,
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in status_rows}
        total = sum(by_status.values())

        # 「已投」的最诚实判据：applied_at 只由用户确认门（confirm_applied）写入
        applied = self._scalar(
            "SELECT COUNT(*) FROM applications WHERE applied_at IS NOT NULL"
            + (" AND profile_id=?" if profile_id else ""),
            ((profile_id,) if profile_id else ()),
        )
        # 进入面试/拿 offer 用事实表计数（任一轮面试/任一 offer 记录），不依赖状态列
        p_cond = " AND a.profile_id=?" if profile_id else ""
        p_params: tuple = ((profile_id,) if profile_id else ())
        interviewed = self._scalar(
            "SELECT COUNT(DISTINCT a.id) FROM applications a"
            " JOIN interviews i ON i.application_id=a.id WHERE 1=1" + p_cond,
            p_params,
        )
        offered = self._scalar(
            "SELECT COUNT(DISTINCT a.id) FROM applications a"
            " JOIN offers o ON o.application_id=a.id WHERE 1=1" + p_cond,
            p_params,
        )
        funnel = {
            "discovered": total,
            "applied": applied,
            "interviewed": interviewed,
            "offered": offered,
            "applied_rate": round(applied / total * 100, 1) if total else None,
            "interview_rate": round(interviewed / applied * 100, 1) if applied else None,
            "offer_rate": round(offered / interviewed * 100, 1) if interviewed else None,
        }
        return {
            "total": total,
            "by_status": by_status,
            "funnel": funnel,
            "weekly": self.weekly(profile_id),
            "health": self.health(profile_id),
            "sources": self.source_effectiveness(profile_id),
            "top_employers": self.top_employers(profile_id),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }

    def weekly(self, profile_id: str | None = None, weeks: int = _WEEKS_DEFAULT) -> list[dict]:
        """按 ISO 周聚合：新投递/面试安排/事件数。"""
        start = dt.date.today() - dt.timedelta(days=weeks * 7)
        p = profile_id
        apps = self.con.execute(
            "SELECT created_at FROM applications WHERE created_at >= ?"
            + (" AND profile_id=?" if p else ""),
            ((start.isoformat(), p) if p else (start.isoformat(),)),
        ).fetchall()
        ivs = self.con.execute(
            """SELECT i.scheduled_at FROM interviews i JOIN applications a ON a.id=i.application_id
               WHERE i.scheduled_at >= ?""" + (" AND a.profile_id=?" if p else ""),
            ((start.isoformat(), p) if p else (start.isoformat(),)),
        ).fetchall()
        events = self.con.execute(
            """SELECT e.occurred_at FROM application_events e
               JOIN applications a ON a.id=e.application_id
               WHERE e.occurred_at >= ?""" + (" AND a.profile_id=?" if p else ""),
            ((start.isoformat(), p) if p else (start.isoformat(),)),
        ).fetchall()
        buckets: dict[str, dict] = {}
        for r in apps:
            k = _iso_week_key(r["created_at"])
            if k:
                buckets.setdefault(k, _empty_week(k))["applications"] += 1
        for r in ivs:
            k = _iso_week_key(r["scheduled_at"])
            if k:
                buckets.setdefault(k, _empty_week(k))["interviews"] += 1
        for r in events:
            k = _iso_week_key(r["occurred_at"])
            if k:
                buckets.setdefault(k, _empty_week(k))["events"] += 1
        return [buckets[k] for k in sorted(buckets)]

    def health(self, profile_id: str | None = None) -> dict:
        """漏斗健康度：停滞投递/临近面试/待决策 Offer/临近截止岗位。"""
        today = dt.date.today()
        week_later = (today + dt.timedelta(days=7)).isoformat()
        stale_params = (*_IN_PROGRESS_STATUSES, 7) + ((profile_id,) if profile_id else ())
        stale = self._scalar(
            f"""SELECT COUNT(*) FROM applications a
                WHERE a.status IN ({','.join('?' * len(_IN_PROGRESS_STATUSES))})
                AND julianday('now') - julianday(a.updated_at) > ?
                {"AND a.profile_id=?" if profile_id else ""}""",
            stale_params,
        )
        upcoming = self._scalar(
            """SELECT COUNT(*) FROM interviews i JOIN applications a ON a.id=i.application_id
               WHERE i.status='planned' AND substr(i.scheduled_at,1,10) BETWEEN ? AND ?"""
            + (" AND a.profile_id=?" if profile_id else ""),
            ((today.isoformat(), week_later, profile_id) if profile_id
             else (today.isoformat(), week_later)),
        )
        pending_offers = self._scalar(
            "SELECT COUNT(*) FROM offers o JOIN applications a ON a.id=o.application_id"
            " WHERE o.status='considering'" + (" AND a.profile_id=?" if profile_id else ""),
            ((profile_id,) if profile_id else ()),
        )
        deadline_params = (
            *_PRE_APPLY_STATUSES, today.isoformat(), week_later,
        ) + ((profile_id,) if profile_id else ())
        deadlines = self._scalar(
            f"""SELECT COUNT(*) FROM applications a JOIN job_postings p ON p.id=a.job_id
                WHERE a.status IN ({','.join('?' * len(_PRE_APPLY_STATUSES))})
                AND p.deadline IS NOT NULL AND substr(p.deadline,1,10) BETWEEN ? AND ?
                {"AND a.profile_id=?" if profile_id else ""}""",
            deadline_params,
        )
        return {
            "stale_applications": stale,
            "upcoming_interviews_7d": upcoming,
            "pending_offers": pending_offers,
            "deadlines_7d": deadlines,
        }

    def source_effectiveness(self, profile_id: str | None = None) -> list[dict]:
        """每个来源：岗位数 / 由此投递数 / 进入面试数。"""
        p_cond = (" AND a.profile_id=?" if profile_id else "")
        params: tuple = ((profile_id,) if profile_id else ())
        rows = self.con.execute(
            f"""
            SELECT s.id, s.display_name AS name,
                   (SELECT COUNT(*) FROM job_postings p2 WHERE p2.source_id=s.id) AS jobs,
                   COUNT(DISTINCT a.id) AS applications,
                   COUNT(DISTINCT i.id) AS interviews
            FROM job_sources s
            LEFT JOIN job_postings p ON p.source_id=s.id
            LEFT JOIN applications a ON a.job_id=p.id
            LEFT JOIN interviews i ON i.application_id=a.id
            WHERE 1=1{p_cond}
            GROUP BY s.id, s.display_name
            HAVING jobs > 0 OR applications > 0
            ORDER BY jobs DESC
            """,
            params,
        ).fetchall()
        return [
            {"source": r["id"], "name": r["name"], "jobs": r["jobs"],
             "applications": r["applications"], "interviews": r["interviews"]}
            for r in rows
        ]

    def top_employers(self, profile_id: str | None = None, limit: int = 10) -> list[dict]:
        cond, params = self._profile_cond(profile_id)
        rows = self.con.execute(
            f"""SELECT p.employer_name AS employer, COUNT(*) AS n,
                       SUM(CASE WHEN a.status IN ('applied','applied_confirmed','screening','interview','offer_stage','closed') THEN 1 ELSE 0 END) AS progressed
                FROM applications a JOIN job_postings p ON p.id=a.job_id {cond}
                GROUP BY p.employer_name ORDER BY n DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 薪资洞察 ----------

    def salary_insights(self, *, city: str | None = None) -> dict:
        """已入库岗位的月薪中位区间分位数（仅 min/max 双全样本）。

        注意：这是"本地库岗位样本"的分位数，不是市场行情——样本数随行标注，
        样本 < 5 的分组前端应显式提示不具参考性。
        """
        where = " WHERE salary_min_k IS NOT NULL AND salary_max_k IS NOT NULL"
        params: list = []
        if city:
            where += " AND city LIKE ?"
            params.append(f"%{city}%")
        rows = self.con.execute(
            f"""SELECT city, employer_name, title,
                       (salary_min_k+salary_max_k)/2.0 AS mid,
                       salary_months, recruitment_type
                FROM job_postings{where} AND status != 'expired'""",
            params,
        ).fetchall()
        samples = [dict(r) for r in rows]
        overall = _percentiles([s["mid"] for s in samples])
        by_city: dict[str, list[float]] = {}
        for s in samples:
            if s["city"]:
                by_city.setdefault(s["city"], []).append(s["mid"])
        cities = [
            {"city": c, "count": len(v), **_percentiles(sorted(v))}
            for c, v in sorted(by_city.items(), key=lambda kv: -len(kv[1]))[:_CITY_TOP_N]
        ]
        return {
            "sample_size": len(samples),
            "note": "基于本地已入库岗位（min/max 双全）的分位数，非市场行情；样本<5不具参考性",
            "overall": {**overall, "count": len(samples)},
            "by_city": cities,
        }

    # ---------- 内部 ----------

    @staticmethod
    def _profile_cond(profile_id: str | None) -> tuple[str, tuple]:
        if profile_id:
            return "WHERE profile_id=?", (profile_id,)
        return "", ()

    def _scalar(self, sql: str, params: tuple) -> int:
        row = self.con.execute(sql, params).fetchone()
        return int(row[0]) if row else 0


def _empty_week(key: str) -> dict:
    return {"week": key, "applications": 0, "interviews": 0, "events": 0}


def _iso_week_key(v: str | None) -> str | None:
    if not v:
        return None
    try:
        d = dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _percentiles(vals: list[float]) -> dict:
    s = sorted(vals)
    return {
        "p25": round(_percentile(s, 25), 1),
        "p50": round(_percentile(s, 50), 1),
        "p75": round(_percentile(s, 75), 1),
    }


__all__ = ["StatsService"]
