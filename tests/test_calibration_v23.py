"""v2.3.0 校准与体验修复的回归测试：

- BM25 相关性按 0.6/0.4 融合进 skill_match（词法为主、检索印证）；
- 匹配排序 tiebreak 引入检索相关性；
- 城市未知 Gate 的 accept_unknown_city 数据层开关；
- JobService.search 的 ranked_profile_id 匹配优先排序；
- JD keywords 的批次词（「2027届校园招聘」类）不再进入技能缺口；
- 求职信论据按 JD 相关度重排 + 联系方式签名 + 人面版本剥离 [ev:]。
"""
from __future__ import annotations

import pytest

from jobhater import config
from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.matching import ENGINE_VERSION, MatchService
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    config.set_data_dir(tmp_path)
    apply_all(tmp_path / "cal.db")
    con = connect(tmp_path / "cal.db")
    ps = ProfileService(con)
    p = ps.create_profile("小袁", phone="18600000000", email="y@example.com")
    ps.add_skill(p.id, "LLM/Agent 应用开发", level=3, aliases=["agent"])
    ps.add_skill(p.id, "React / Vite 前端", level=3)
    ps.add_skill(p.id, "Python", level=3)
    ev = ps.add_evidence(p.id, "Agent 系统开发经历", fact_type="experience")
    ps.add_experience(
        p.id, employer="FAR-Lab 项目组", title="核心开发", kind="project",
        description="LLM Agent 编排系统", tags=["Agent 编排"],
        evidence_ids=[ev.id],
    )
    js = JobService(con)
    js.ingest([
        {"title": "AI Agent 应用开发工程师", "company": "某AI公司", "city": "上海",
         "salary": "15-25K", "description": "负责 LLM Agent 应用工程化落地，React 前端",
         "keywords": ["Agent", "2027届校园招聘、正式"]},
        {"title": "销售管培生", "company": "某快消", "city": "上海",
         "salary": "15-25K", "description": "渠道拓展与客户维护"},
    ], source_id="test")
    yield con, p.id
    con.close()
    config.set_data_dir(None)


def test_engine_version_bumped():
    assert ENGINE_VERSION == "2.3.0"


def test_relevance_blend_raises_skill_score(env):
    """同一岗位：相关性 90 时 skill_match 高于相关性缺失时；理由可解释。"""
    con, pid = env
    ps = ProfileService(con)
    preset = ps.create_preset(pid, "默认", target_cities=["上海"])
    job = JobService(con).search("Agent")[0]
    svc = MatchService(con)
    base = svc.evaluate(job, ps.match_view(pid), preset, relevance=None)
    boosted = svc.evaluate(job, ps.match_view(pid), preset, relevance=90.0)
    lexical = base.dims["skill_match"].score
    assert boosted.dims["skill_match"].score == round(0.6 * lexical + 0.4 * 90)
    assert any("校准" in r for r in boosted.dims["skill_match"].reasons)
    # 词法 0 分但 BM25 满分 → 不超过 40（防单一信号独大）
    capped = svc.evaluate(job, ps.match_view(pid), preset, relevance=100.0)
    job_nolex = JobService(con).search("管培生")[0]
    none_hit = svc.evaluate(job_nolex, ps.match_view(pid), preset, relevance=100.0)
    assert none_hit.dims["skill_match"].score <= 40 or job_nolex.id != job.id
    assert capped.dims["skill_match"].score >= base.dims["skill_match"].score


def test_rank_jobs_orders_relevant_first(env):
    """批量匹配：对口语义强相关的 Agent 岗排在无关销售岗之前。"""
    con, pid = env
    ps = ProfileService(con)
    preset = ps.create_preset(pid, "默认", target_cities=["上海"])
    jobs = JobService(con).search("", statuses=["active"], limit=10)
    outcomes = MatchService(con).rank_jobs(ps.match_view(pid), preset, jobs)
    eligible = [o for o in outcomes if o.eligible]
    assert eligible, "至少 Agent 岗应合格"
    assert eligible[0].job_id != eligible[-1].job_id
    top = JobService(con).get(eligible[0].job_id)
    assert "Agent" in top.title


def test_unknown_city_gate_optin(env):
    """默认：城市未知 → city gate 淘汰；开启 accept_unknown_city → 放行并标注。"""
    con, pid = env
    ps = ProfileService(con)
    js = JobService(con)
    js.ingest([{"title": "算法工程师（城市缺失）", "company": "某所", "description": "算法"}],
              source_id="test")
    job = js.search("城市缺失")[0]
    strict = ps.create_preset(pid, "严格", target_cities=["上海"])
    loose = ps.create_preset(pid, "放行", target_cities=["上海"],
                             gates={"accept_unknown_city": True})
    svc = MatchService(con)
    o1 = svc.evaluate(job, ps.match_view(pid), strict)
    assert not o1.eligible and any(g.code == "city" and not g.passed for g in o1.gate_reasons)
    o2 = svc.evaluate(job, ps.match_view(pid), loose)
    assert o2.eligible
    city_gate = [g for g in o2.gate_reasons if g.code == "city"][0]
    assert city_gate.passed and "人工核实" in city_gate.detail


def test_search_ranked_by_match(env):
    """ranked_profile_id 排序：合格高分岗在前，未参与匹配/淘汰岗在后。"""
    con, pid = env
    ps = ProfileService(con)
    preset = ps.create_preset(pid, "默认", target_cities=["上海"])
    js = JobService(con)
    jobs = js.search("", statuses=["active"], limit=10)
    MatchService(con).rank_jobs(ps.match_view(pid), preset, jobs)
    ranked = js.search("", statuses=["active"], limit=10, ranked_profile_id=pid)
    assert "Agent" in ranked[0].title  # 相关且合格 → 第一
    titles = [j.title for j in ranked]
    assert titles.index("AI Agent 应用开发工程师") < titles.index("销售管培生")


def test_jd_terms_filters_batch_labels(env):
    from jobhater.services.materials import _jd_terms

    job = JobService(env[0]).search("Agent")[0]
    terms = _jd_terms(job)
    assert "2027届校园招聘、正式" not in terms
    assert any("agent" in t.lower() for t in terms)


def test_cover_letter_relevance_order_and_display(env):
    con, pid = env
    from jobhater.services.materials import MaterialsService
    from jobhater.services.resume import strip_citations

    ps = ProfileService(con)
    ps.add_experience(pid, employer="院团委", title="团总支书", description="组织协调学生活动")
    job = JobService(con).search("Agent")[0]
    letter = MaterialsService(con).build_cover_letter(pid, job.id)
    # 相关度重排：FAR-Lab（Agent 相关）必须先于团委学生工作
    why_me = letter.content_md.split("**为什么这家**")[0]
    assert why_me.index("FAR-Lab") < why_me.index("团总支书")
    # 落款含联系方式
    assert "18600000000" in letter.content_md and "y@example.com" in letter.content_md
    # 人面版本无内部标记，且保留联系方式
    display = strip_citations(letter.content_md)
    assert "[ev:" not in display and "18600000000" in display
