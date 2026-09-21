"""Resume 服务测试：master 生成、版本化、factcheck 对抗、diff、多格式导出降级。"""
from __future__ import annotations

import pytest

from jobhater import config
from jobhater.db import apply_all, connect
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeError, ResumeService


@pytest.fixture()
def env(tmp_path):
    config.set_data_dir(tmp_path)
    apply_all(tmp_path / "r.db")
    con = connect(tmp_path / "r.db")
    ps = ProfileService(con)
    p = ps.create_profile("小林", headline="2027届 · 设计×AI")
    ev1 = ps.add_evidence(p.id, "实习期间主导3场用户访谈，满意度提升12%",
                          fact_type="experience")
    ev2 = ps.add_evidence(p.id, "2025 校级设计竞赛一等奖", fact_type="award")
    ps.add_education(p.id, school="南京某大学", degree="本科", major="环境设计",
                     end_date="2027-06")
    ps.add_experience(p.id, employer="某互联网公司", title="产品实习生",
                      kind="internship", start_date="2026-06", end_date="2026-09",
                      description="负责某功能改版，用户满意度提升 12%",
                      tags=["产品设计"], evidence_ids=[ev1.id])
    ps.add_skill(p.id, "Figma", level=3, aliases=["figma"])
    ps.confirm_evidence(p.id, [ev1.id, ev2.id])
    yield con, p.id, (ev1.id, ev2.id)
    config.set_data_dir(None)
    con.close()


def test_master_build_and_versions(env):
    con, pid, _ = env
    rs = ResumeService(con)
    rid, vid1 = rs.build_master_from_profile(pid)
    v1 = rs.get_version(vid1)
    assert v1["version"] == 1
    assert v1["sections"]["work"][0]["position"] == "产品实习生"
    assert v1["sections"]["skills"][0]["name"] == "Figma"
    # 修改 → 新版本（不可变历史）
    sections = v1["sections"]
    sections["basics"]["summary"] = "专注 AI 产品设计的应届生"
    vid2 = rs.commit_version(rid, sections, parent_version_id=vid1, note="加摘要")
    assert rs.get_version(vid2)["version"] == 2
    diff = rs.diff_versions(vid1, vid2)
    assert not diff["added"] and not diff["removed"] and not diff["changed"]  # 摘要不属 bullet


def test_factcheck_adversarial(env):
    con, pid, (ev1, ev2) = env
    rs = ResumeService(con)
    rid, vid1 = rs.build_master_from_profile(pid)
    base = rs.get_version(vid1)["sections"]
    # 场景1：编造数字（12→47，证据里没有47）
    bad = json_copy(base)
    bad["work"][0]["highlights"] = [f"负责改版，用户满意度提升 47% [ev:{ev1}]"]
    vid_bad = rs.commit_version(rid, bad, bullets_provenance=[
        {"path": "work[0].highlights[0]", "text": bad["work"][0]["highlights"][0],
         "evidence_ids": [ev1], "rewrite_kind": "factual_rewrite"}])
    report = rs.factcheck(vid_bad)
    assert not report["passed"]
    assert any(i["type"] == "unverified_number" for i in report["issues"])
    with pytest.raises(ResumeError, match="真实性校验未通过"):
        rs.mark_final(vid_bad)

    # 场景2：数字有据 → 通过并可定稿
    good = json_copy(base)
    good["work"][0]["highlights"] = [f"负责改版，用户满意度提升 12% [ev:{ev1}]"]
    vid_good = rs.commit_version(rid, good, bullets_provenance=[
        {"path": "work[0].highlights[0]", "text": good["work"][0]["highlights"][0],
         "evidence_ids": [ev1], "rewrite_kind": "factual_rewrite"}])
    report = rs.factcheck(vid_good)
    assert report["passed"], report["issues"]
    assert rs.mark_final(vid_good)["ok"] is True

    # 场景3：无引用条目
    nocite = json_copy(base)
    nocite["work"][0]["highlights"] = ["独立完成整个产品线从0到1的搭建"]
    vid_nc = rs.commit_version(rid, nocite, bullets_provenance=[
        {"path": "work[0].highlights[0]", "text": nocite["work"][0]["highlights"][0],
         "evidence_ids": [], "rewrite_kind": "emphasis"}])
    report = rs.factcheck(vid_nc)
    # 条目级证据覆盖子条目，但「独立完成/从0到1」强主张在证据原文中不存在 → 必须被拦
    assert any(i["type"] == "unverified_claim" for i in report["issues"])

    # 场景4：unsupported 改写 → 直接阻断
    unsup = json_copy(base)
    unsup["work"][0]["highlights"] = [f"搭建了千万级营收体系 [ev:{ev1}]"]
    vid_un = rs.commit_version(rid, unsup, bullets_provenance=[
        {"path": "work[0].highlights[0]", "text": unsup["work"][0]["highlights"][0],
         "evidence_ids": [ev1], "rewrite_kind": "unsupported"}])
    report = rs.factcheck(vid_un)
    assert any(i["type"] in ("unsupported_claim", "commercial_risk") for i in report["issues"])


def json_copy(d):
    import json

    return json.loads(json.dumps(d, ensure_ascii=False))


def test_renders_and_export_degradation(env, tmp_path):
    con, pid, _ = env
    rs = ResumeService(con)
    _, vid = rs.build_master_from_profile(pid)
    md = rs.render_markdown(vid)
    assert "产品实习生" in md and "## 技能" in md
    html = rs.render_html(vid)
    assert "<h1>小林</h1>" in html and "产品实习生" in html
    # XSS 转义：画像文本含尖括号时不注入
    con2 = con
    from jobhater.services.profile import ProfileService as PS

    p2 = PS(con2).create_profile("<script>alert(1)</script>")
    rid2, vid2 = ResumeService(con2).build_master_from_profile(p2.id)
    html2 = ResumeService(con2).render_html(vid2)
    assert "<script>" not in html2 and "&lt;script&gt;" in html2

    p_md = rs.export_file(vid, "md")
    assert p_md.exists() and p_md.suffix == ".md"
    p_html = rs.export_file(vid, "html")
    assert p_html.exists()
    p_json = rs.export_file(vid, "json")
    assert p_json.exists()
    # 可选依赖缺失时的诚实降级
    try:
        import docx  # noqa: F401

        have_docx = True
    except ImportError:
        have_docx = False
    if not have_docx:
        with pytest.raises(ResumeError, match="python-docx"):
            rs.export_file(vid, "docx")
    try:
        import playwright  # noqa: F401

        have_pw = True
    except ImportError:
        have_pw = False
    if not have_pw:
        with pytest.raises(ResumeError, match="playwright"):
            rs.export_file(vid, "pdf")
