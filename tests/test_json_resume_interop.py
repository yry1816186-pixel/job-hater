"""JSON Resume 开放标准互操作测试：导出形状、导入映射、往返保真、幂等。"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeService


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "jr.db"
    apply_all(db)
    con = connect(db)
    ps = ProfileService(con)
    p = ps.create_profile("小约", "3年后端")
    ps.add_skill(p.id, name="Go", level=4, years=3)
    ps.add_experience(p.id, employer="某云", title="后端工程师", tags=["go"],
                      description="高并发网关")
    ps.add_education(p.id, school="华中科技大学", degree="本科", major="软件工程")
    ps.add_project(p.id, name="网关重构", role="负责人", description="性能提升3倍")
    rs = ResumeService(con)
    rid, vid = rs.build_master_from_profile(p.id)
    yield con, ps, rs, p.id, vid
    con.close()


def test_export_uses_official_schema_keys(env):
    _, _, rs, _, vid = env
    doc = rs.export_json_resume(vid)
    assert doc["$schema"].startswith("https://raw.githubusercontent.com/jsonresume")
    assert doc["basics"]["name"] == "小约"
    assert doc["work"][0]["name"] == "某云" and doc["work"][0]["position"] == "后端工程师"
    assert doc["education"][0]["institution"] == "华中科技大学"
    assert doc["education"][0]["studyType"] == "本科" and doc["education"][0]["area"] == "软件工程"
    assert doc["skills"][0]["name"] == "Go"
    assert doc["_meta"]["generator"] == "job-hater"


def test_export_preserves_evidence_extensions(env):
    """证据引用按下划线扩展保留——溯源主张不因导出丢失。"""
    _, _, rs, _, vid = env
    doc = rs.export_json_resume(vid)
    for w in doc["work"]:
        assert "_evidence_ids" in w


def test_import_maps_and_counts(env):
    con, ps, rs, pid, _ = env
    doc = {
        "basics": {"name": "外部名字", "label": "AI产品转型", "summary": "五年经验"},
        "skills": [{"name": "Figma", "keywords": ["figma.com"]}],
        "work": [{"name": "外部公司", "position": "产品经理", "startDate": "2024-01-01"}],
        "education": [{"institution": "新大学", "area": "设计", "studyType": "硕士"}],
        "projects": [{"name": "外部项目", "role": "PM"}],
    }
    counts = rs.import_json_resume(pid, doc)
    assert counts["skills"] == 1 and counts["experiences"] == 1
    assert counts["educations"] == 1 and counts["projects"] == 1
    # headline 原有值不覆盖（原「3年后端」非空）
    assert counts["headline_filled"] == 0
    # 已有技能不重复导入
    again = rs.import_json_resume(pid, doc)
    assert again["skills"] == 0 and again["experiences"] == 0


def test_roundtrip_export_import_export(env):
    """导出 → 新画像导入 → 从新画像生成再导出：核心实体全部存活。"""
    con, ps, rs, pid, vid = env
    doc = rs.export_json_resume(vid)
    p2 = ps.create_profile("迁移者")
    rs.import_json_resume(p2.id, doc)
    rid2, vid2 = rs.build_master_from_profile(p2.id, "迁移后主简历")
    doc2 = rs.export_json_resume(vid2)
    assert doc2["basics"]["name"] == "迁移者"  # 名字是画像身份，不随简历迁移
    assert doc2["work"][0]["name"] == "某云"
    assert doc2["education"][0]["institution"] == "华中科技大学"
    assert any(s["name"] == "Go" for s in doc2["skills"])
    assert any(p["name"] == "网关重构" for p in doc2["projects"])


def test_import_rejects_non_resume(env):
    from jobhater.services.resume import ResumeError

    _, _, rs, pid, _ = env
    with pytest.raises(ResumeError):
        rs.import_json_resume("prof_不存在", {"basics": {"name": "x"}})
