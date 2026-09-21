"""多 persona 匹配测试（§27/§36）：同一产品必须服务完全不同的求职者。

反回归锚点：
- 应届设计×AI、3年Java后端、机械工程师 三个 persona 各自得到合理结果；
- 任何人都不再是"默认用户"——一个 persona 的高分岗对另一个应是低分或不合格；
- 硬性淘汰必须给出透明原因；同输入结果可复现。
"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.matching import MatchService, eligibility_gates
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    apply_all(tmp_path / "m.db")
    con = connect(tmp_path / "m.db")
    yield con
    con.close()


JOBS = [
    {  # 校招 AI 产品设计岗
        "title": "AI产品设计培训生（2027届校招）",
        "company": "星河网络科技",
        "city": "南京",
        "salary": "12-18K·15薪",
        "description": "2027届校园招聘。负责AI产品设计与人机交互，要求了解 Figma、交互设计、"
                       "用户研究，熟悉 AIGC 工具者优先。专业不限。",
    },
    {  # 社招 Java 后端（3-5年）
        "title": "Java高级开发工程师",
        "company": "云图信息",
        "city": "杭州",
        "salary": "25-40K·16薪",
        "experience_required": "3-5年",
        "description": "负责电商核心系统微服务开发。要求精通 Java、Spring Cloud、MySQL、"
                       "Redis、Kafka，有高并发系统经验。",
    },
    {  # 机械设计岗
        "title": "机械设计工程师",
        "company": "中原重工集团",
        "city": "洛阳",
        "salary": "10-15K",
        "description": "负责矿山机械结构设计与有限元仿真，要求机械类专业，熟练使用 SolidWorks、"
                       "AutoCAD。",
    },
    {  # 运营岗（供市场 persona）
        "title": "内容运营专员",
        "company": "拾光文化",
        "city": "上海",
        "salary": "8-13K",
        "description": "负责新媒体内容策划与用户增长，要求有公众号/小红书运营经验，数据敏感。",
    },
]


def _persona_campus_design(svc: ProfileService):
    p = svc.create_profile("小林", headline="2027届本科 · 设计×AI")
    svc.add_education(p.id, school="南京某大学", degree="本科", major="环境设计", end_date="2027-06")
    for name, al in [("Figma", ["figma"]), ("交互设计", ["ux", "交互"]),
                     ("用户研究", ["用研"]), ("AIGC工具", ["aigc", "stable diffusion"])]:
        svc.add_skill(p.id, name, aliases=al, level=3)
    svc.add_experience(p.id, employer="某互联网公司", title="产品实习生", kind="internship",
                       tags=["产品设计", "用户研究"], start_date="2026-06", end_date="2026-09")
    pst = svc.create_preset(
        p.id, "2027秋招", employment_types=["campus", "internship"],
        target_roles=["产品设计", "AI产品"], target_cities=["南京", "上海"],
        salary_min_k=10, graduation_year=2027, max_experience_years_required=0,
    )
    return p, pst


def _persona_java_backend(svc: ProfileService):
    p = svc.create_profile("老王", headline="3年 Java 后端")
    svc.add_education(p.id, school="武汉某理工", degree="本科", major="计算机", end_date="2022-06")
    for name, al, lv in [("Java", ["java", "jvm"], 4), ("Spring", ["spring cloud", "spring"], 4),
                         ("MySQL", ["mysql"], 4), ("Redis", ["redis"], 3), ("Kafka", ["kafka"], 3)]:
        svc.add_skill(p.id, name, aliases=al, level=lv, years=3)
    svc.add_experience(p.id, employer="某电商", title="Java后端开发", kind="full_time",
                       tags=["微服务", "高并发", "电商"], start_date="2022-07")
    pst = svc.create_preset(
        p.id, "社招后端", employment_types=["social"], target_roles=["Java后端", "后端开发"],
        target_cities=["杭州", "深圳"], salary_min_k=25, max_experience_years_required=None,
    )
    return p, pst


def _persona_mechanical(svc: ProfileService):
    p = svc.create_profile("阿中原", headline="机械工程应届硕士")
    svc.add_education(p.id, school="洛阳某工大", degree="硕士", major="机械工程", end_date="2026-06")
    for name in ["SolidWorks", "AutoCAD", "有限元仿真"]:
        svc.add_skill(p.id, name, level=4)
    pst = svc.create_preset(
        p.id, "机械方向", target_roles=["机械设计"], target_cities=["洛阳", "郑州"],
        salary_min_k=8, graduation_year=2026,
    )
    return p, pst


def test_campus_design_persona(env):
    ps, js = ProfileService(env), JobService(env)
    p, pst = _persona_campus_design(ps)
    js.ingest(JOBS, source_id="test")
    jobs = js.search("", statuses=["active"], limit=20)
    view = ps.match_view(p.id)
    outcomes = MatchService(env).rank_jobs(view, pst, jobs)
    by_title = {o.job_id: o for o in outcomes}
    title_by_id = {j.id: j.title for j in jobs}
    scored = {title_by_id[oid]: o for oid, o in by_title.items()}

    ai = scored["AI产品设计培训生（2027届校招）"]
    assert ai.eligible, [g for g in ai.gate_reasons if not g.passed]
    assert ai.rank_score >= 60
    assert "Figma" in str(ai.evidence.get("matched_skills")) or ai.dims["skill_match"].score > 30

    java = scored["Java高级开发工程师"]
    assert not java.eligible
    reasons = [g.code for g in java.gate_reasons if not g.passed]
    assert "experience_over_max" in reasons or "recruitment_type" in reasons, reasons

    mech = scored["机械设计工程师"]
    assert not mech.eligible  # 城市不在目标 + 技能域完全不同
    assert any(g.code == "city" for g in mech.gate_reasons if not g.passed)


def test_java_backend_persona(env):
    ps, js = ProfileService(env), JobService(env)
    p, pst = _persona_java_backend(ps)
    js.ingest(JOBS, source_id="test")
    jobs = js.search("", statuses=["active"], limit=20)
    outcomes = MatchService(env).rank_jobs(ps.match_view(p.id), pst, jobs)
    title_by_id = {j.id: j.title for j in jobs}
    scored = {title_by_id[o.job_id]: o for o in outcomes}

    java = scored["Java高级开发工程师"]
    assert java.eligible, [g for g in java.gate_reasons if not g.passed]
    assert java.rank_score >= 70
    assert java.dims["skill_match"].score >= 60
    assert java.verdict in ("强烈推荐", "推荐", "可考虑")  # 四档结论（v2.1）

    campus = scored["AI产品设计培训生（2027届校招）"]
    assert not campus.eligible  # 社招偏好：校招批次不合格（透明原因）
    assert any(g.code == "recruitment_type" for g in campus.gate_reasons if not g.passed)


def test_mechanical_persona(env):
    ps, js = ProfileService(env), JobService(env)
    p, pst = _persona_mechanical(ps)
    js.ingest(JOBS, source_id="test")
    jobs = js.search("", statuses=["active"], limit=20)
    outcomes = MatchService(env).rank_jobs(ps.match_view(p.id), pst, jobs)
    title_by_id = {j.id: j.title for j in jobs}
    scored = {title_by_id[o.job_id]: o for o in outcomes}
    mech = scored["机械设计工程师"]
    assert mech.eligible and mech.rank_score >= 60
    assert mech.dims["skill_match"].score >= 60


def test_reproducibility_and_transparency(env):
    """同输入两次评估结果一致；被淘汰岗位有透明 gate 原因；结果可回读。"""
    ps, js = ProfileService(env), JobService(env)
    p, pst = _persona_java_backend(ps)
    js.ingest(JOBS, source_id="test")
    jobs = js.search("", statuses=["active"], limit=20)
    ms = MatchService(env)
    view = ps.match_view(p.id)
    o1 = ms.evaluate(jobs[0], view, pst)
    o2 = ms.evaluate(jobs[0], view, pst)
    assert o1.rank_score == o2.rank_score and o1.eligible == o2.eligible

    ms.rank_jobs(view, pst, jobs)
    back = ms.latest_for_job(jobs[0].id, p.id)
    assert back is not None and back.rank_score == o1.rank_score
    # 每个结果都有依据：dims 非空且每个维度有 reasons
    assert back.dims and all(d.reasons for d in back.dims.values())


def test_graduation_year_gate(env):
    ps, js = ProfileService(env), JobService(env)
    p, pst = _persona_campus_design(ps)
    js.ingest([JOBS[0]], source_id="test")
    job = js.search("")[0]
    # 2026 届用户投 2027 届批次 → 淘汰
    pst26 = ps.create_preset(p.id, "2026届", graduation_year=2026)
    gates = eligibility_gates(job, ps.match_view(p.id), pst26)
    assert any(not g.passed and g.code == "graduation_year" for g in gates)
    # 2027 届用户 → 通过
    gates27 = eligibility_gates(job, ps.match_view(p.id), pst)
    assert all(g.passed for g in gates27), [g for g in gates27 if not g.passed]
