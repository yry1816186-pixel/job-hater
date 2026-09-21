"""确定性材料生成层测试：岗位定制简历/求职信/打招呼话术/面试题库/提升计划。

覆盖：正常生成与落库、JD 相关度重排且证据引用原样保留、题库只引用真实画像条目、
不承诺 JD 独有能力（Kafka 不进话术/求职信主张）、画像为空时的诚实降级
（生成型报错、分析型空降级+说明）、缺口只认 keywords 结构化字段。
"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.materials import MaterialsError, MaterialsService
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeService

JD = {
    "title": "后端开发工程师", "company": "云图信息", "city": "杭州",
    "salary": "20-35K",
    "description": "岗位职责：\n负责 Java 微服务后端开发，参与高并发订单系统设计\n"
                   "任职要求：\n熟悉 MySQL 与 Redis，有 Kafka 使用经验优先",
    "keywords": ["Java", "Redis", "Kafka"],
}


@pytest.fixture()
def env(tmp_path):
    apply_all(tmp_path / "m.db")
    con = connect(tmp_path / "m.db")
    ps, js = ProfileService(con), JobService(con)
    p = ps.create_profile("小陈", headline="2027届 · 后端开发")
    ev_java = ps.add_evidence(p.id, "参与订单服务改造，接口平均耗时下降 30%，QPS 从 120 提升到 450",
                              fact_type="experience")
    ev_pipe = ps.add_evidence(p.id, "搭建每周业务数据报表 ETL 流程", fact_type="experience")
    ev_proj = ps.add_evidence(p.id, "使用 Spring Boot 与 Redis 开发校园二手交易平台",
                              fact_type="project")
    # 画像原序：数据实习在前（sort_order=0），Java 实习在后——重排后应反转
    ps.add_experience(p.id, employer="某数据公司", title="数据实习生",
                      kind="internship", start_date="2026-03", end_date="2026-06",
                      description="负责数据报表 ETL 流程搭建", tags=["Python", "ETL"],
                      evidence_ids=[ev_pipe.id], sort_order=0)
    ps.add_experience(p.id, employer="某电商平台", title="后端开发实习生",
                      kind="internship", start_date="2026-06", end_date="2026-09",
                      description="参与订单服务改造，接口平均耗时下降 30%", tags=["Java", "微服务"],
                      evidence_ids=[ev_java.id], sort_order=1)
    ps.add_project(p.id, name="校园二手书交易平台",
                   description="基于 Spring Boot 与 Redis 实现商品与订单模块",
                   tags=["Spring Boot", "Redis"], evidence_ids=[ev_proj.id])
    ps.add_skill(p.id, "Excel", level=2)
    ps.add_skill(p.id, "Java", level=4, aliases=["java"])
    ps.add_skill(p.id, "MySQL", level=3)
    ps.confirm_evidence(p.id, [ev_java.id, ev_pipe.id, ev_proj.id])
    stats = js.ingest([JD], source_id="t")
    assert stats.added == 1
    job = js.search("")[0]
    yield con, p.id, job.id, (ev_java.id, ev_pipe.id, ev_proj.id)
    con.close()


@pytest.fixture()
def empty_env(tmp_path):
    apply_all(tmp_path / "e.db")
    con = connect(tmp_path / "e.db")
    ps, js = ProfileService(con), JobService(con)
    p = ps.create_profile("空白")
    js.ingest([JD], source_id="t")
    job = js.search("")[0]
    yield con, p.id, job.id
    con.close()


def test_build_job_resume_reorders_and_keeps_evidence(env):
    con, pid, jid, evs = env
    out = MaterialsService(con).build_job_resume(pid, jid)
    assert out["factcheck"]["passed"], out["factcheck"]
    rs = ResumeService(con)
    ver = rs.get_version(out["version_id"])
    row = con.execute("SELECT * FROM resumes WHERE id=?", (out["resume_id"],)).fetchone()
    assert row["kind"] == "job_specific" and row["job_id"] == jid
    # 重排：JD 高相关的 Java 实习排到第一（画像原序里它是第二个）
    assert [w["position"] for w in ver["sections"]["work"]] == ["后端开发实习生", "数据实习生"]
    # 只重排不改写：bullet 文本与证据引用原样保留
    assert ver["sections"]["work"][0]["summary"] == "参与订单服务改造，接口平均耗时下降 30%"
    assert ver["sections"]["work"][0]["evidence_ids"] == [evs[0]]
    assert ver["sections"]["work"][1]["evidence_ids"] == [evs[1]]
    assert ver["sections"]["projects"][0]["evidence_ids"] == [evs[2]]
    assert ver["sections"]["skills"][-1]["name"] == "Excel"  # 零相关技能沉底
    assert all(p["rewrite_kind"] == "reordering" for p in ver["bullets_provenance"])
    # 相关度透明可查：每条都有 id/name/score，且重排后单调不增
    ps = ProfileService(con)
    exp_by_title = {e.title: e.id for e in ps.list_experiences(pid)}
    work_rel = out["relevance"]["work"]
    assert [x["id"] for x in work_rel] == [
        exp_by_title["后端开发实习生"], exp_by_title["数据实习生"],
    ]
    scores = [x["score"] for x in work_rel]
    assert scores == sorted(scores, reverse=True)


def test_cover_letter_persisted_with_anchors_and_selfcheck(env):
    con, pid, jid, (ev_java, _evp, ev_proj) = env
    ms = MaterialsService(con)
    # 先建岗位简历 → 求职信应自动挂到该（画像,岗位）的最新 job_specific 版本
    ms.build_job_resume(pid, jid)
    letter = ms.build_cover_letter(pid, jid)
    assert letter.id.startswith("cl_") and letter.job_id == jid
    assert letter.resume_version_id is not None
    md = letter.content_md
    for head in ("为什么是我", "为什么这家", "期待"):
        assert head in md
    # 每个论据带证据锚点
    assert f"[ev:{ev_java}]" in md and f"[ev:{ev_proj}]" in md
    # 不把 JD 独有的 Kafka 当成自己的能力写进主张
    why_me = md.split("**为什么是我**")[1].split("**为什么这家**")[0]
    assert "Kafka" not in why_me
    # 自检通过并落库（cover_letters 此前全仓库无写入方）
    assert letter.factcheck_report is not None and letter.factcheck_report["passed"]
    row = con.execute("SELECT * FROM cover_letters WHERE id=?", (letter.id,)).fetchone()
    assert row is not None and row["content_md"] == md


def test_cover_letter_jd_overlap_and_quote_fallback(env, tmp_path):
    con, pid, _jid, _evs = env
    ms = MaterialsService(con)
    # keywords 与画像有交集 → 回扣真实条目
    letter = ms.build_cover_letter(pid, _jid)
    assert "岗位要求的「Java」" in letter.content_md
    # keywords 为空 → JD 原文摘录降级，不臆测
    js = JobService(con)
    js.ingest([{"title": "运营专员", "company": "另一家", "description": "负责用户增长运营"}],
              source_id="t2")
    job2 = next(j for j in js.search("") if j.title == "运营专员")
    letter2 = ms.build_cover_letter(pid, job2.id)
    assert "原文要点" in letter2.content_md and "> 负责" in letter2.content_md


def test_greeting_grounds_everything_in_profile(env):
    con, pid, jid, _evs = env
    g = MaterialsService(con).build_greeting(pid, jid)
    text = g["greeting"]
    assert "后端开发工程师" in text and "小陈" in text
    assert "Java" in text            # 画像有且 JD 提到 → 可以说
    assert "Kafka" not in text       # JD 独有 → 不承诺
    assert 40 <= g["length"] <= 160
    # 首要论据来自最高相关度的真实条目（本画像下项目/经历都可能登顶）
    assert g["sources"]["project_id"] or g["sources"]["experience_id"]
    assert g["sources"]["skill_ids"] and g["sources"]["headline_used"] is True


def test_interview_questions_only_reference_real_entries(env):
    con, pid, jid, _evs = env
    ps = ProfileService(con)
    iq = MaterialsService(con).build_interview_questions(pid, jid)
    real_ids = {e.id for e in ps.list_experiences(pid)}
    real_ids |= {p.id for p in ps.list_projects(pid)}
    real_ids |= {s.id for s in ps.list_skills(pid)}
    secs = iq["sections"]
    # 深挖 + 技术题的锚点必须是真实画像条目
    anchored = secs["project_deep_dive"] + secs["technical_basics"]
    assert anchored
    for item in anchored:
        for a in item["anchors"]:
            assert a["id"] in real_ids
    assert any("Kafka" in q["question"] for q in secs["reverse_questions"])
    assert len(secs["behavioral"]) == 3
    assert all(q["anchors"] == [] for q in secs["behavioral"])
    # 技能题带自评一致性提示
    java_q = next(q for q in secs["technical_basics"] if q["anchors"][0]["name"] == "Java")
    assert java_q["note"] and "level=4" in java_q["note"]


def test_upskill_plan_gaps_and_integrity(env):
    con, pid, jid, _evs = env
    up = MaterialsService(con).build_upskill_plan(pid, jid)
    assert [m["name"] for m in up["matched_skills"]] == ["Java", "Redis"]
    assert [g["name"] for g in up["gaps"]] == ["Kafka"]  # 画像无 Kafka → 唯一缺口
    gap = up["gaps"][0]
    assert len(gap["steps"]) == 4 and gap["milestone"]
    assert set(up["phases"]) == {"14d", "30d", "60d"}
    assert any("Kafka" in s for s in up["phases"]["14d"])
    assert any("Kafka" in s for s in up["phases"]["60d"])
    assert "真实" in up["integrity_note"] and "简历" in up["integrity_note"]


def test_empty_profile_generators_raise_analyzers_degrade(empty_env):
    con, pid, jid = empty_env
    ms = MaterialsService(con)
    # 生成型：画像为空 → 明确报错，绝不编造
    with pytest.raises(MaterialsError, match="请先补充画像"):
        ms.build_job_resume(pid, jid)
    with pytest.raises(MaterialsError, match="请先补充画像"):
        ms.build_cover_letter(pid, jid)
    with pytest.raises(MaterialsError, match="请先补充画像"):
        ms.build_greeting(pid, jid)
    # 分析型：空降级 + 诚实说明
    iq = ms.build_interview_questions(pid, jid)
    assert iq["sections"]["project_deep_dive"] == []
    assert iq["sections"]["technical_basics"] == []
    assert len(iq["sections"]["behavioral"]) == 3
    assert any("画像为空" in n for n in iq["notes"])
    up = ms.build_upskill_plan(pid, jid)
    assert [g["name"] for g in up["gaps"]] == ["Java", "Redis", "Kafka"]  # 画像空 → 全为缺口
    assert up["matched_skills"] == []


def test_upskill_without_keywords_degrades_honestly(env):
    con, pid, _jid, _evs = env
    js = JobService(con)
    js.ingest([{"title": "运营专员", "company": "另一家", "description": "负责用户增长运营"}],
              source_id="t2")
    job2 = next(j for j in js.search("") if j.title == "运营专员")
    up = MaterialsService(con).build_upskill_plan(pid, job2.id)
    assert up["gaps"] == [] and up["matched_skills"] == []
    assert up["phases"] == {"14d": [], "30d": [], "60d": []}
    assert any("keywords" in n for n in up["notes"])


def test_missing_profile_or_job_raises(env):
    con, pid, jid, _evs = env
    ms = MaterialsService(con)
    with pytest.raises(MaterialsError, match="画像不存在"):
        ms.build_greeting("prof_nope", jid)
    with pytest.raises(MaterialsError, match="岗位不存在"):
        ms.build_greeting(pid, "job_nope")
    with pytest.raises(MaterialsError, match="岗位不存在"):
        ms.build_interview_questions(pid, "job_nope")
