"""粘贴解析器 + v1迁移工具测试。"""
from __future__ import annotations

import json

import pytest

from jobhater import config
from jobhater.db import connect
from jobhater.migrate_v1 import migrate
from jobhater.services.sources import REGISTRY, parse_jd_text

JD_SAMPLE = """职位：AI产品设计师（2027届校招）
公司：星河网络科技有限公司
城市：南京
薪资：12-18K·15薪
经验：应届生
学历：本科

岗位职责：
1. 负责AI产品的交互设计与用户研究；
2. 参与AIGC功能的产品定义与落地。
任职要求：
- 熟悉 Figma 等设计工具；
- 对大模型应用有热情，专业不限。
投递链接：https://jobs.example.com/apply/12345
"""


def test_parse_jd_text_full_labels():
    draft = parse_jd_text(JD_SAMPLE)
    assert draft["title"] == "AI产品设计师（2027届校招）"
    assert draft["company"] == "星河网络科技有限公司"
    assert draft["city"] == "南京"
    assert draft["salary"] == "12-18K·15薪"
    assert draft["education"] == "本科"
    assert draft["url"] == "https://jobs.example.com/apply/12345"
    assert "来自文本标签" in " ".join(draft["parse_notes"])
    assert draft["needs_review_fields"] == []


def test_parse_jd_text_unlabeled_fallbacks():
    text = "我们正在招一位机器学习工程师，base杭州，要求3年以上经验，硕士学历，薪资40-60K。"
    draft = parse_jd_text(text)
    assert draft.get("city") == "杭州"
    assert draft.get("education") == "硕士"
    assert "40-60K" in (draft.get("salary") or "")
    assert "company" not in draft or not draft.get("company")
    assert "company" in draft["needs_review_fields"]


def test_parse_jd_text_empty_rejected():
    with pytest.raises(ValueError, match="为空"):
        parse_jd_text("   \n  ")


def test_registry_has_paste_adapter():
    a = REGISTRY.get("manual")
    assert a.capabilities()["trigger"] == "user_paste"
    assert a.health_check().ok


# ---------- v1 迁移 ----------


@pytest.fixture()
def old_data(tmp_path):
    d = tmp_path / "old"
    (d / "profile").mkdir(parents=True)
    (d / "jobs").mkdir()
    (d / "profile" / "profile.json").write_text(json.dumps({
        "identity": {"name": "小林", "headline": "2027届·设计×AI"},
        "education": [{"school": "南京某大学", "major": "环境设计", "degree": "本科",
                        "start": "2023.09", "end": "2027.06", "gpa": "3.8/5.0"}],
        "skills": [{"name": "Python", "level": "熟练", "evidence": ["ev_paper"]}],
        "experiences": [{"employer": "某公司", "title": "产品实习生", "is_internship": True,
                          "start": "2026.06", "end": "2026.09", "description": "改版",
                          "tags": ["产品设计"], "evidence": ["ev_paper"]}],
        "publications": [], "awards": [{"name": "校级一等奖", "date": "2025.05"}],
        "honors": [],
        "preferences": {"target_roles": ["产品设计"], "target_cities": ["南京"],
                         "salary_min_k": 12},
        "evidence_index": {"ev_paper": "发表论文一篇，被引3次"},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "jobs" / "jobs.json").write_text(json.dumps({
        "schema_version": "1.0",
        "jobs": [
            {"id": "abc123", "title": "AI产品设计师", "company": "星河科技", "city": "南京",
             "salary": "12-18K", "description": "校招产品设计", "status": "new",
             "url": "https://x.com/1"},
            {"id": "def456", "title": "Java高级工程师", "company": "云图信息", "city": "杭州",
             "salary": "30-50K", "description": "社招", "status": "rejected",
             "reject_reason": "要求≥3年经验"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "applications.json").write_text(json.dumps({
        "applications": [], "daily_log": {}, "blacklist": ["星河科技"],
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_migration_end_to_end(old_data, tmp_path):
    config.set_data_dir(tmp_path / "new")
    try:
        report = migrate(old_data)
        # 画像与证据
        p = report["profile"]
        assert p["name"] == "小林" and p["evidence_migrated"] == 1
        con = connect()
        try:
            from jobhater.services.jobs import JobService
            from jobhater.services.profile import ProfileService

            ps = ProfileService(con)
            skills = ps.list_skills(p["id"])
            assert skills[0].name == "Python" and skills[0].level == 3
            assert skills[0].evidence_ids  # 证据映射迁移成功
            preset = ps.active_preset(p["id"])
            assert preset.name.startswith("迁移") and preset.salary_min_k == 12
            # 岗位：1 入库 + 1 保留拒绝
            js = JobService(con)
            active = js.search("", statuses=["active"])
            rejected = js.search("", statuses=["rejected"])
            assert len(active) == 1 and active[0].title == "AI产品设计师"
            assert len(rejected) == 1 and "v1迁移保留" in (rejected[0].reject_reason or "")
            # blacklist 未变成 user_blocked
            blocked = con.execute(
                "SELECT COUNT(*) AS c FROM employers WHERE user_blocked=1"
            ).fetchone()["c"]
            assert blocked == 0
        finally:
            con.close()
        assert any("blacklist" in n for n in report["notes"])
    finally:
        config.set_data_dir(None)


def test_migration_idempotent_rerun(old_data, tmp_path):
    config.set_data_dir(tmp_path / "new2")
    try:
        migrate(old_data)
        report2 = migrate(old_data)
        assert report2["jobs"]["added"] == 0  # 二次迁移：全部去重
        assert report2["jobs"]["deduped_exact"] == 2
    finally:
        config.set_data_dir(None)
