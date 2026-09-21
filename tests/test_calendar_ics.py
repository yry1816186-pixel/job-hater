"""ICS 日历导出测试：RFC 5545 形状、截止/面试两类事件、过期过滤、转义。"""
from __future__ import annotations

import datetime as dt

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "ics.db"
    apply_all(db)
    con = connect(db)
    ps = ProfileService(con)
    p = ps.create_profile("日历测试")
    yield con, ps, JobService(con), ApplicationService(con), p.id
    con.close()


def _deadline(offset_days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=offset_days)).isoformat()


def test_ics_shape_and_both_event_kinds(env):
    con, ps, js, apps, pid = env
    js.ingest([
        {"title": "后端岗", "company": "有截止公司", "city": "上海", "salary": "20-30K",
         "description": "Go 后端", "deadline": _deadline(3)},
    ], source_id="t")
    job = js.search("")[0]
    app_id = apps.create(job.id, pid)
    apps.transition(app_id, "ready_to_apply")
    apps.confirm_applied(app_id)
    apps.schedule_interview(
        app_id, round=2, kind="技术面",
        scheduled_at=f"{_deadline(2)}T10:00:00Z",
    )
    ics = apps.calendar_ics(pid)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "VERSION:2.0" in ics and "PRODID:-//job-hater" in ics
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "\r\n" in ics  # RFC 5545 要求 CRLF
    assert "UID:deadline-" in ics and "TRIGGER:-P1D" in ics
    assert "UID:interview-" in ics and "第2轮技术面" in ics
    assert "TRIGGER:-PT30M" in ics
    # VALUE=DATE 形式的全天截止事件
    assert f"DTSTART;VALUE=DATE:{_deadline(3).replace('-', '')}" in ics


def test_ics_filters_past_and_closed(env):
    con, ps, js, apps, pid = env
    js.ingest([
        {"title": "过期岗", "company": "过期", "city": "上海", "description": "x",
         "deadline": "2020-01-01"},
        {"title": "活跃岗", "company": "活跃", "city": "上海", "description": "y",
         "deadline": _deadline(5)},
    ], source_id="t")
    jobs = {j.title: j for j in js.search("")}
    a_old = apps.create(jobs["过期岗"].id, pid)
    apps.create(jobs["活跃岗"].id, pid)
    apps.transition(a_old, "closed")  # 已关闭：即使有截止也不提醒
    ics = apps.calendar_ics(pid)
    assert "UID:deadline-" in ics and "活跃岗" in ics
    assert "过期岗" not in ics


def test_ics_escapes_special_chars(env):
    con, ps, js, apps, pid = env
    js.ingest([
        {"title": "后端;负责人,方向", "company": "公司\\名字", "city": "上海",
         "description": "x", "deadline": _deadline(1)},
    ], source_id="t")
    job = js.search("")[0]
    apps.create(job.id, pid)
    ics = apps.calendar_ics(pid)
    assert "后端\\;负责人\\,方向" in ics
    assert "公司\\\\名字" in ics


def test_ics_empty_profile_clean_calendar(env):
    _, _, _, apps, pid = env
    ics = apps.calendar_ics(pid)
    assert ics.count("BEGIN:VEVENT") == 0
