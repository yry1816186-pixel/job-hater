"""匹配引擎 v2.1 特性锁定：同义组、硬域封顶、薪资倒挂、词法-语义交叉校验、发布超龄过期。"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.matching import (
    MatchService,
    _match_words_for_skill,
    dimension_scores,
)
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    apply_all(tmp_path / "m21.db")
    con = connect(tmp_path / "m21.db")
    yield con
    con.close()


def _preset(ps: ProfileService, pid: str, **kw):
    defaults = dict(name="测试偏好", target_roles=["后端开发"], target_cities=["上海"],
                    salary_min_k=20)
    defaults.update(kw)
    return ps.create_preset(pid, **defaults)


def test_skill_synonym_groups_expand_match_words():
    """用户技能 go 的匹配词集应含内置同义 golang/go语言；用户别名与之并集。"""
    words = _match_words_for_skill(
        {"name": "Go", "aliases": ["grpc"], "level": 3, "years": 2}
    )
    lowered = {w.lower() for w in words}
    assert {"go", "grpc", "golang", "go语言"} <= lowered


def test_hard_domain_words_cap_coincidental_hits(env):
    """JD 是芯片岗但词面撞上「后端」：skill/experience 封顶50并带人工复核提示。"""
    ps, js = ProfileService(env), JobService(env)
    p = ps.create_profile("硬件转码者")
    ps.add_skill(p.id, name="后端开发", level=4, years=3, aliases=["backend"])
    ps.add_experience(p.id, employer="某公司", title="后端开发", tags=["后端", "服务"])
    pst = _preset(ps, p.id)
    js.ingest([{
        "title": "IC后端设计工程师", "company": "芯原股份", "city": "上海",
        "description": "芯片后端设计，集成电路物理实现，FPGA验证，半导体工艺",
    }], source_id="t")
    job = js.search("")[0]
    dims, evidence = dimension_scores(job, ps.match_view(p.id), pst, None, None)
    assert dims["skill_match"].score <= 50
    assert dims["experience_relevance"].score <= 50
    assert evidence.get("hard_domain_flag")
    assert any("巧合" in r for r in dims["skill_match"].reasons)


def test_salary_inversion_flagged_not_gated(env):
    """上海岗 12K 达到用户底线 10K（gate 通过）但低于城市参考线 15K → evidence 旗标。"""
    ps, js = ProfileService(env), JobService(env)
    p = ps.create_profile("低预期用户")
    pst = _preset(ps, p.id, salary_min_k=10)
    js.ingest([{
        "title": "运营专员", "company": "小公司", "city": "上海", "salary": "9-12K",
    }], source_id="t")
    job = js.search("")[0]
    svc = MatchService(env)
    outcome = svc.evaluate(job, ps.match_view(p.id), pst)
    assert outcome.eligible  # 不是 gate
    assert outcome.evidence.get("salary_inversion", {}).get("city") == "上海"
    assert any("倒挂" in r for r in outcome.dims["salary_fit"].reasons)


def test_published_age_expiry_fallback(env):
    """无 deadline 但发布超 60 天的岗位 → 过期兜底。"""
    js = JobService(env)
    js.ingest([{
        "title": "老岗位", "company": "旧公司", "city": "北京",
        "published_at": "2025-01-01",  # 远超 60 天
    }], source_id="t")
    job = js.search("")[0]
    assert job.status.value == "expired"


def test_lexical_semantic_conflict_flags_review(env):
    """JD 堆砌命中词但整体语境不相关 → 词法-语义冲突旗标。"""
    ps, js = ProfileService(env), JobService(env)
    p = ps.create_profile("python")
    ps.add_skill(p.id, name="Python", level=4, years=3)
    ps.add_skill(p.id, name="测试", level=3, years=2)
    pst = _preset(ps, p.id)
    js.ingest([{
        "title": "医疗器械注册专员", "company": "医械公司", "city": "上海",
        "description": "负责医疗器械注册申报，临床试验资料整理，Python脚本辅助数据处理，"
                       "测试报告审核，与药监部门沟通，制药行业法规合规",
    }], source_id="t")
    job = js.search("")[0]
    dims, evidence = dimension_scores(job, ps.match_view(p.id), pst, None, None)
    # 医疗器械语境与 python 开发画像：要么硬域封顶，要么交叉校验冲突，至少其一
    flagged = (
        evidence.get("hard_domain_flag") is not None
        or evidence.get("lexical_semantic_conflict") is not None
        or dims["skill_match"].uncertainty is not None
    )
    assert flagged
