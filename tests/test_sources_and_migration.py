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
    assert "公司" in draft["needs_review_fields"]  # 用户直接阅读：中文标签


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


# ---------- v1 真实 schema（name/highlights/experience_ev_map）迁移回归 ----------


@pytest.fixture()
def real_v1_data(tmp_path):
    """v1 (Campus-Job-Agent) profile.json 的实际字段形态：条目用 name/org/role/highlights，
    证据链接在顶层 experience_ev_map，evidence_index 含 _source_files 元数据键。"""
    d = tmp_path / "old_real"
    (d / "profile").mkdir(parents=True)
    (d / "jobs").mkdir()
    (d / "profile" / "profile.json").write_text(json.dumps({
        "identity": {"name": "小袁", "phone": "18600000000", "email": "xiaoy@example.com"},
        "education": [{"school": "南京航空航天大学", "major": "环境设计", "degree": "本科",
                        "start": "2023.09", "end": "2027.06", "gpa": "3.8/5.0"}],
        "skills": [{"name": "Python", "level": "熟练", "evidence": ["ev_self"]}],
        "experiences": [
            {"id": "exp_proj", "type": "project", "name": "真研系统", "role": "核心开发",
             "org": "挑战杯项目", "start": "2026.06", "end": "至今",
             "highlights": ["构建11阶段闭环", "RRF+Listwise Rerank 融合检索"],
             "tags": ["AI4S"]},
        ],
        "awards": [{"name": "全国大学生计算机设计大赛", "level": "国家级一等奖",
                     "work": "智能摄影APP", "year": 2026}],
        "preferences": {"target_roles": ["AI产品经理"], "target_cities": ["南京"]},
        "evidence_index": {
            "ev_edu": "教育背景：南京航空航天大学 环境设计 本科",
            "ev_proj": "项目经验：《真研系统》RRF+Listwise Rerank 融合检索",
            "ev_self": "技能与自我评价：熟练Python",
            "_source_files": ["data/profile/raw/简历.pdf"],
        },
        "experience_ev_map": {"exp_proj": "ev_proj"},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "jobs" / "jobs.json").write_text(json.dumps({"jobs": []}), encoding="utf-8")
    (d / "applications.json").write_text(
        json.dumps({"applications": [], "daily_log": {}, "blacklist": []}), encoding="utf-8")
    return d


def test_migration_real_v1_schema_keeps_content_and_evidence(real_v1_data, tmp_path):
    config.set_data_dir(tmp_path / "new_real")
    try:
        report = migrate(real_v1_data)
        pid = report["profile"]["id"]
        con = connect()
        try:
            from jobhater.services.profile import ProfileService

            ps = ProfileService(con)
            # 联系方式随 identity 迁移（简历 basics 法定字段）
            prof = ps.get_profile(pid)
            assert prof.phone == "18600000000" and prof.email == "xiaoy@example.com"
            # 经历：name+highlights 不丢（并入 description），证据经 experience_ev_map 链接
            exps = ps.list_experiences(pid)
            assert len(exps) == 1
            assert exps[0].employer == "挑战杯项目" and exps[0].title == "核心开发"
            assert "真研系统" in (exps[0].description or "")
            assert "构建11阶段闭环" in (exps[0].description or "")
            assert len(exps[0].evidence_ids) == 1
            # 教育：gpa 数值拆出 + 学校名命中的证据自动挂链
            edus = ps.list_educations(pid)
            assert edus[0].gpa == 3.8 and edus[0].gpa_note == "3.8/5.0"
            assert len(edus[0].evidence_ids) == 1
            # 获奖：level/work/year 不丢（直接查表：ProfileService 暂无 list_awards）
            awd = con.execute(
                "SELECT name, level, date, description FROM awards WHERE profile_id=?", (pid,)
            ).fetchall()
            assert awd[0]["level"] == "国家级一等奖"
            assert awd[0]["description"] == "智能摄影APP"
            assert awd[0]["date"] == "2026"
            # 证据：前缀分类生效；_source_files 不混入
            evs = ps.list_evidence(pid)
            assert len(evs) == 3
            assert not any("简历.pdf" in (e.original_text or "") for e in evs)
            ft = {e.normalized_fact[:4]: e.fact_type.value if e.fact_type else None for e in evs}
            assert ft["教育背景"] == "education"
            assert ft["项目经验"] == "project"
            assert ft["技能与自"] == "skill"
        finally:
            con.close()
    finally:
        config.set_data_dir(None)
