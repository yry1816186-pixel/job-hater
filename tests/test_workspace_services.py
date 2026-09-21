"""工作台服务测试：联系人 CRM、提醒与建议、设置 KV、模拟面试练习器、统计、备份恢复。

全部为确定性断言：不 mock、不跳过——每个服务的行为契约由这些测试锁定。
"""
from __future__ import annotations

import datetime as dt
import sqlite3 as sq
import tempfile
from pathlib import Path

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.backup import BackupError, BackupService
from jobhater.services.contacts import ContactError, ContactsService
from jobhater.services.interview_kit import InterviewKitError, InterviewKitService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService
from jobhater.services.profile import ProfileService
from jobhater.services.reminders import ReminderError, RemindersService
from jobhater.services.settings import SettingsService
from jobhater.services.stats import StatsService


@pytest.fixture()
def env(tmp_path):
    """完整闭环：画像 + 3 个岗位（带薪资/截止日期）+ 投递。"""
    apply_all(tmp_path / "ws.db")
    con = connect(tmp_path / "ws.db")
    ps, js = ProfileService(con), JobService(con)
    profile = ps.create_profile("测试者", phone="13800000000", email="t@ex.com")
    js.ingest([
        {
            "title": "后端开发工程师", "company": "云图信息", "city": "杭州",
            "salary": "20-35K·16薪", "description": "Java 微服务开发，熟悉分布式",
            "deadline": (dt.date.today() + dt.timedelta(days=5)).isoformat(),
        },
        {"title": "数据开发工程师", "company": "星河数据", "city": "杭州",
         "salary": "22-33K·15薪", "description": "数据仓库 Spark"},
        {"title": "前端开发工程师", "company": "极光互动", "city": "上海",
         "salary": "18-30K", "description": "React TypeScript"},
    ], source_id="t")
    jobs = js.search("")
    assert len(jobs) >= 3
    by_title = {j.title: j.id for j in jobs}
    yield con, profile.id, (
        by_title["后端开发工程师"], by_title["数据开发工程师"], by_title["前端开发工程师"],
    ), tmp_path / "ws.db"
    con.close()


# ---------- 联系人 ----------

def test_contact_crud_and_embedded_context(env):
    con, pid, (jid, jid2, _), _ = env
    app = ApplicationService(con).create(jid, pid)
    cs = ContactsService(con)
    c = cs.add("张HR", application_id=app, role="HR", phone="139", wechat="zhr")
    assert c["name"] == "张HR" and c["job_title"] == "后端开发工程师"
    assert c["employer_name"] == "云图信息"
    upd = cs.update(c["id"], note="内推联系人")
    assert upd["note"] == "内推联系人" and upd["role"] == "HR"  # 部分更新不动其他字段
    assert cs.list(application_id=app)[0]["id"] == c["id"]
    assert cs.list(q="张")[0]["id"] == c["id"]
    cs.delete(c["id"])
    assert cs.list() == []


def test_contact_rejects_missing_anchor_and_empty_name(env):
    con, _, (jid, _, _), _ = env
    with pytest.raises(ContactError, match="不存在"):
        ContactsService(con).add("张HR", application_id="app_nope")
    with pytest.raises(ContactError, match="姓名不能为空"):
        ContactsService(con).add("   ")


# ---------- 提醒 ----------

def test_reminder_crud_with_owner_context(env):
    con, pid, (jid, jid2, _), _ = env
    apps = ApplicationService(con)
    app = apps.confirm_applied(apps.create(jid, pid), channel="官网")
    rs = RemindersService(con)
    r = rs.create("application", app["id"], dt.date.today().isoformat(), "询问进度", kind="followup")
    assert r["owner_title"] == "后端开发工程师" and r["owner_employer"] == "云图信息"
    assert rs.list()[0]["id"] == r["id"]
    done = rs.set_done(r["id"], True)
    assert done["done"] is True
    assert rs.list() == []  # 默认只看未完成
    assert rs.list(include_done=True)[0]["id"] == r["id"]


def test_reminder_validation(env):
    con, pid, (jid, jid2, _), _ = env
    rs = RemindersService(con)
    app = ApplicationService(con).create(jid, pid)
    with pytest.raises(ReminderError, match="owner_kind"):
        rs.create("job", app, "2026-01-01", "x")
    with pytest.raises(ReminderError, match="不存在"):
        rs.create("application", "app_missing", "2026-01-01", "x")
    with pytest.raises(ReminderError, match="due_at"):
        rs.create("application", app, "not-a-date", "x")


def test_reminder_suggestions_deterministic(env):
    """停滞投递 → 跟进建议；采纳后不再重复建议（防轰炸）。"""
    con, pid, (jid, jid2, _), _ = env
    apps = ApplicationService(con)
    app = apps.confirm_applied(apps.create(jid, pid), channel="官网")
    # 伪造 updated_at 为 10 天前（规则输入）
    con.execute(
        "UPDATE applications SET updated_at=? WHERE id=?",
        ((dt.date.today() - dt.timedelta(days=10)).isoformat(), app["id"]),
    )
    con.commit()
    rs = RemindersService(con)
    sug = rs.suggestions()
    followups = [s for s in sug if s["kind"] == "followup"]
    assert len(followups) == 1 and "云图信息" in followups[0]["title"]
    # 采纳 → 落库后同 owner+kind 不再建议
    rs.create("application", app["id"], followups[0]["due_at"], followups[0]["title"], kind="followup")
    assert [s for s in rs.suggestions() if s["kind"] == "followup"] == []


def test_reminder_interview_prep_suggestion(env):
    con, pid, (jid, jid2, _), _ = env
    apps = ApplicationService(con)
    app = apps.confirm_applied(apps.create(jid, pid), channel="官网")
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    apps.schedule_interview(app["id"], scheduled_at=f"{tomorrow}T10:00:00", kind="technical")
    sug = RemindersService(con).suggestions()
    assert any(s["kind"] == "interview_prep" for s in sug)


# ---------- 设置 ----------

def test_settings_kv_roundtrip_and_corrupt_tolerance(env):
    con, _, (jid, _, _), _ = env
    ss = SettingsService(con)
    assert ss.get("theme") is None
    ss.set("theme", "dark")
    ss.set("saved_searches", [{"name": "杭州后端", "params": {"q": "后端", "city": "杭州"}}])
    assert ss.get("theme") == "dark"
    assert ss.list()["saved_searches"][0]["name"] == "杭州后端"
    # 单键损坏不砸全局：直接写坏 JSON，get 回退默认值
    con.execute(
        "UPDATE user_settings SET value_json='{broken' WHERE key='theme'"
    )
    con.commit()
    assert ss.get("theme", "light") == "light"
    assert ss.delete("theme") is True and ss.delete("theme") is False


# ---------- 模拟面试练习器 ----------

def _session_with_turns(con, pid, jid):
    apps = ApplicationService(con)
    app_id = apps.create(jid, pid)
    apps.confirm_applied(app_id, channel="官网")
    iv = apps.schedule_interview(
        app_id, scheduled_at=f"{dt.date.today().isoformat()}T15:00:00", kind="technical"
    )
    kit = InterviewKitService(con)
    s = kit.create_session(iv.id, persona="严肃的技术负责人", difficulty=3)
    kit.add_turn(s.id, "interviewer", "讲一个你做过的分布式项目")
    kit.add_turn(s.id, "candidate", "我做过订单微服务拆分，QPS 从 2000 提升到 8000，"
                  "过程中引入了分库分表和消息削峰。")
    kit.add_turn(s.id, "interviewer", "分库分片键怎么选的？")
    return kit, s.id, iv


def test_interview_session_lifecycle_and_stats(env):
    con, pid, (jid, jid2, _), _ = env
    kit, sid, iv = _session_with_turns(con, pid, jid)
    stats = kit.stats(sid)
    assert stats["questions"] == 2 and stats["answers"] == 1
    assert stats["unanswered_trailing"] == 1  # 最后一问未答
    assert stats["answer_chars"]["max"] >= stats["answer_chars"]["min"] > 0
    ended = kit.end_session(sid)
    assert ended.ended_at
    with pytest.raises(InterviewKitError, match="已结束"):
        kit.add_turn(sid, "candidate", "补答")
    assert len(kit.list_sessions(ApplicationService(con).list(pid)[0]["id"])) == 1


def test_interview_session_validation(env):
    con, pid, (jid, jid2, _), _ = env
    kit = InterviewKitService(con)
    with pytest.raises(InterviewKitError, match="面试不存在"):
        kit.create_session("iv_missing")
    apps = ApplicationService(con)
    app = apps.confirm_applied(apps.create(jid, pid), channel="官网")
    iv = apps.schedule_interview(app["id"])
    s = kit.create_session(iv.id, mode="real_record")
    with pytest.raises(InterviewKitError, match="role"):
        kit.add_turn(s.id, "observer", "旁听")
    with pytest.raises(InterviewKitError, match="内容不能为空"):
        kit.add_turn(s.id, "candidate", "  ")


def test_review_self_deterministic_and_ai_local_mode(env):
    con, pid, (jid, jid2, _), _ = env
    kit, sid, _ = _session_with_turns(con, pid, jid)
    r = kit.review_self(
        sid, scores={"structure": 7, "clarity": 8}, strengths=["有量化数据"],
        gaps=["没答完最后一问"],
    )
    assert r.ai_generated is False and r.scores["structure"] == 7.0
    reviews = kit.list_reviews(sid)
    assert len(reviews) == 1
    # 本地模式（未启用 provider）：AI 复盘诚实降级，不落库、不抛错
    out = kit.review_ai(sid, ack_egress=True)
    assert out["executed"] is False and out["reason"] == "local_mode"
    assert len(kit.list_reviews(sid)) == 1


# ---------- 统计 ----------

def test_stats_overview_funnel_uses_applied_at_fact(env):
    """已投/面试/offer 全部用事实表判据（applied_at/interviews/offers 存在性）。"""
    con, pid, (jid, jid2, _), _ = env
    apps = ApplicationService(con)
    a1 = apps.confirm_applied(apps.create(jid, pid), channel="官网")
    apps.confirm_applied(apps.create(jid2, pid), channel="官网")  # 第二个岗位也确认已投
    ov = StatsService(con).overview(pid)
    assert ov["total"] == 2
    assert ov["funnel"]["applied"] == 2  # 两份 confirm_applied 各写一次 applied_at
    assert ov["funnel"]["interviewed"] == 0
    apps.schedule_interview(a1["id"])
    assert StatsService(con).overview(pid)["funnel"]["interviewed"] == 1


def test_stats_weekly_and_health(env):
    con, pid, (jid, jid2, _), _ = env
    apps = ApplicationService(con)
    apps.create(jid, pid)  # 未投（discovered）→ 岗位截止日计入"投前截止预警"
    st = StatsService(con)
    weekly = st.weekly(pid)
    assert weekly and weekly[-1]["applications"] >= 1  # 本周有新投递
    h = st.health(pid)
    assert h["upcoming_interviews_7d"] == 0 and h["deadlines_7d"] == 1  # 岗位截止日在 5 天后


def test_stats_salary_insights_honest_sample_note(env):
    con, pid, (jid, jid2, _), _ = env
    JobService(con).ingest([
        {"title": "Go 后端", "company": "B公司", "city": "杭州", "salary": "25-30K·16薪",
         "description": "Go 微服务"},
        {"title": "Java 后端", "company": "C公司", "city": "北京", "salary": "30-45K",
         "description": "Java"},
    ], source_id="t")
    si = StatsService(con).salary_insights()
    assert si["sample_size"] >= 3  # fixture 岗位 + 2 个新岗位
    assert "非市场行情" in si["note"]
    cities = {c["city"] for c in si["by_city"]}
    assert {"杭州", "北京"} <= cities


# ---------- 备份恢复 ----------

def test_backup_snapshot_restore_roundtrip(env):
    con, pid, (jid, _, _), db_path = env
    ApplicationService(con).create(jid, pid)
    svc = BackupService(con, db_path)
    data, filename = svc.snapshot()
    assert data.startswith(b"SQLite format 3\x00") and "jobhater_backup_" in filename
    # 破坏当前库（删画像），恢复后数据完整回归
    con.execute("DELETE FROM applications")
    con.execute("DELETE FROM candidate_profiles")
    con.commit()
    report = svc.restore(data)
    assert report["ok"] is True and report["counts"]["applications"] >= 1
    assert ProfileService(con).get_profile(pid) is not None


def test_backup_rejects_garbage_and_foreign_db(env):
    con, _, _, db_path = env
    svc = BackupService(con, db_path)
    with pytest.raises(BackupError, match="SQLite"):
        svc.restore(b"this is not a database at all")
    # 一个合法 SQLite 但不是 jobhater 库
    with tempfile.TemporaryDirectory() as td:
        foreign = Path(td) / "f.db"
        fcon = sq.connect(foreign)
        fcon.execute("CREATE TABLE foo(x)")
        fcon.commit()
        fcon.close()
        with pytest.raises(BackupError, match="不是 jobhater 备份|核心表"):
            svc.restore(foreign.read_bytes())
