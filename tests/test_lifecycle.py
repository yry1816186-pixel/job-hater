"""生命周期测试：状态机强制、诚实投递语义、面试、Offer 比较、反馈闭环。"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService, FeedbackService, LifecycleError
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    apply_all(tmp_path / "lc.db")
    con = connect(tmp_path / "lc.db")
    ps, js = ProfileService(con), JobService(con)
    profile = ps.create_profile("测试")
    js.ingest([{
        "title": "后端开发工程师", "company": "云图信息", "city": "杭州",
        "salary": "20-35K", "description": "Java 微服务开发",
    }], source_id="t")
    job = js.search("")[0]
    yield con, profile.id, job.id
    con.close()


def test_state_machine_rejects_illegal_transitions(env):
    con, pid, jid = env
    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    with pytest.raises(LifecycleError, match="非法状态转移"):
        apps.transition(app, "offer")  # discovered → offer 不允许（投后状态必须经确认门）
    # applied_confirmed 只能经用户确认门：通用转移即使在 ready_to_apply 也拒绝
    apps.transition(app, "saved")
    apps.transition(app, "preparing")
    apps.transition(app, "materials_ready")
    apps.transition(app, "ready_to_apply")
    with pytest.raises(LifecycleError, match="确认已投递"):
        apps.transition(app, "applied_confirmed")
    final = apps.confirm_applied(app, channel="官网")
    assert final["status"] == "applied_confirmed" and final["applied_at"]


def test_preparation_funnel_allows_forward_skips(env):
    """准备漏斗允许向前跳步（语义蕴含，审计流记录实际 from→to）。"""
    con, pid, jid = env
    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    final = apps.transition(app, "ready_to_apply", note="万事俱备")
    assert final["status"] == "ready_to_apply"
    evs = apps.events(app)
    assert evs[-1]["payload_json"].find("discovered") >= 0  # 审计流保留真实起点


def test_confirm_applied_is_user_gate(env):
    con, pid, jid = env
    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    final = apps.confirm_applied(app, channel="官网")
    assert final["status"] == "applied_confirmed"
    evs = apps.events(app)
    assert any(e["kind"] == "status_change" and "user_confirmed" in (e["payload_json"] or "") for e in evs)
    # 幂等
    again = apps.confirm_applied(app)
    assert again["status"] == "applied_confirmed"


def test_same_job_single_application_and_events_audit(env):
    con, pid, jid = env
    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    with pytest.raises(LifecycleError, match="唯一"):
        apps.create(jid, pid)
    apps.transition(app, "saved")
    apps.transition(app, "shortlisted", note="匹配分高")
    evs = apps.events(app)
    kinds = [e["kind"] for e in evs]
    assert kinds == ["created", "status_change", "status_change"]


def test_interview_and_offer_flow(env):
    con, pid, jid = env
    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    apps.confirm_applied(app)
    iv = apps.schedule_interview(app, round=1, kind="technical", scheduled_at="2026-10-01T10:00:00Z")
    apps.finish_interview(iv.id, outcome="pass")
    apps.transition(app, "interviewing")
    offer = apps.add_offer(app, base_salary_k=25, salary_months=16, city="杭州",
                           benefits=["五险一金", "餐补"], custom_dimensions={"通勤": "地铁30分钟"})
    offers = apps.list_offers(pid)
    assert len(offers) == 1 and offers[0]["salary_months"] == 16
    assert offers[0]["job_title"] == "后端开发工程师"  # 列表自带岗位名（可辨识）
    cmp = apps.compare_offers([offer.id])
    assert cmp[0]["annual_base_k"] == 400.0
    assert cmp[0]["job_title"] == "后端开发工程师"
    apps.set_offer_status(offer.id, "accepted")
    with pytest.raises(LifecycleError):
        apps.set_offer_status(offer.id, "bogus")


def test_feedback_loop_viewable_and_resettable(env):
    con, pid, jid = env
    fb = FeedbackService(con)
    fb.record(pid, "interested", job_id=jid)
    fb.record(pid, "low_pay", job_id=jid, note="低于预期")
    hist = fb.history(pid)
    assert len(hist) == 2 and hist[0]["kind"] == "low_pay"
    assert fb.reset(pid) == 2
    assert fb.history(pid) == []
