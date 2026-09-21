"""岗位服务测试：导入规范化、分层去重、快照、检索、健康上报。"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService


@pytest.fixture()
def svc(tmp_path):
    apply_all(tmp_path / "t.db")
    con = connect(tmp_path / "t.db")
    yield JobService(con)
    con.close()


RAW_A = {
    "title": "机器学习工程师",
    "company": "示例智能科技（杭州）有限公司",
    "city": "杭州",
    "salary": "20-35K·16薪",
    "experience_required": "1-3年",
    "education_required": "本科",
    "url": "https://example.com/job/1",
    "description": "负责推荐算法研发，要求熟悉深度学习、大规模数据处理与 A/B 实验。",
    "keywords": ["算法", "推荐"],
}
RAW_A_DUP_SOURCE_ID = {**RAW_A, "source_job_id": "1001"}
RAW_B_SAME_COMPANY_NEAR_TITLE = {
    **RAW_A,
    "title": "机器学习工程师（急招）",
    "source_job_id": "1002",
}
RAW_C_OTHER = {
    "title": "机械设计师",
    "company": "重工集团有限公司",
    "city": "徐州",
    "salary": "8-12K",
    "description": "负责机械结构设计与有限元仿真分析。",
}


def test_ingest_normalization(svc):
    stats = svc.ingest([RAW_A, RAW_C_OTHER])
    assert stats.added == 2 and stats.rejected == 0
    job = svc.search("机器学习")[0]
    assert job.salary_min_k == 20 and job.salary_max_k == 35 and job.salary_months == 16
    assert job.experience_required_min == 1.0
    assert job.education_required == "本科"
    assert job.city == "杭州"
    assert job.recruitment_type.value in ("campus", "social", "internship", "unknown")
    # 快照保真
    row = svc.con.execute(
        "SELECT raw_json FROM source_snapshots WHERE job_id=?", (job.id,)
    ).fetchone()
    assert "示例智能科技" in row["raw_json"]


def test_dedup_layers(svc):
    # L1：同源同 source_job_id
    s1 = svc.ingest([RAW_A_DUP_SOURCE_ID])
    s2 = svc.ingest([RAW_A_DUP_SOURCE_ID])
    assert s1.added == 1 and s2.deduped_exact == 1
    # L2：跨源同 dedupe_key（不同 source）
    s3 = svc.ingest([RAW_A], source_id="web_paste")
    assert s3.deduped_exact == 1
    # L3：近似标题 → 标记 near_dup_of 入库
    s4 = svc.ingest([RAW_B_SAME_COMPANY_NEAR_TITLE])
    assert s4.deduped_near == 1
    flagged = [j for j in svc.search("", limit=10) if j.extras.get("near_dup_of")]
    assert len(flagged) == 1


def test_enrich_missing_description(svc):
    thin = {k: v for k, v in RAW_A.items() if k != "description"}
    full = dict(RAW_A, source_job_id="1009")
    svc.ingest([thin])
    stats = svc.ingest([full])
    assert stats.enriched == 1
    job = svc.search("机器学习")[0]
    assert "推荐算法" in (job.description or "")


def test_placeholder_and_broken_rejected(svc):
    stats = svc.ingest(
        [
            {"title": "（填写：岗位名）", "company": "某公司"},
            {"title": "", "company": ""},
            {"company": "没有标题的公司"},
        ]
    )
    assert stats.rejected == 3 and stats.added == 0


def test_structured_filters_and_near_dup_view(svc):
    svc.ingest([RAW_A, RAW_C_OTHER])
    hangzhou = svc.search("", cities=["杭州"])
    assert len(hangzhou) == 1 and hangzhou[0].city == "杭州"
    mech = svc.search("机械")
    assert len(mech) == 1 and mech[0].title == "机械设计师"


def test_source_health_isolation(svc):
    svc.ensure_source("wenke", "api_fetch", "官方接口源")
    svc.record_source_health("wenke", ok=False, message="超时")
    svc.record_source_health("wenke", ok=False, message="超时")
    src = svc.list_sources()[0]
    assert src.health_status == "degraded" and src.consecutive_failures == 2
    svc.record_source_health("wenke", ok=True)
    src = [s for s in svc.list_sources() if s.id == "wenke"][0]
    assert src.health_status == "ok" and src.consecutive_failures == 0
