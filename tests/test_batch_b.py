"""B 批次测试：ATS 匹配报告、投递标签、CSV 导出、双简历模板。"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api.app import create_app
from jobhater.db import apply_all, connect
from jobhater.services.ats_scan import ATSScanError, ATSScanService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import LifecycleError
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeService


@pytest.fixture()
def env(tmp_path, monkeypatch):
    config.set_data_dir(tmp_path)  # CSV 导出走 API 默认连接，需隔离到 tmp_path
    apply_all(tmp_path / "ats.db")
    con = connect(tmp_path / "ats.db")
    ps, js = ProfileService(con), JobService(con)
    profile = ps.create_profile("测试者", headline="Java 后端", phone="13800000000", email="t@ex.com")
    ps.add_skill(profile.id, "Java", level=4)
    ps.add_skill(profile.id, "MySQL", level=3)
    ps.add_education(profile.id, school="浙江大学", degree="本科", major="计算机",
                     start_date="2023-09", end_date="2027-06")
    js.ingest([{
        "title": "Java 后端开发工程师", "company": "云图信息", "city": "杭州",
        "salary": "20-35K",
        "description": "负责微服务系统设计与开发。",
        "responsibilities": "熟悉 Java、Spring Boot、MySQL、Redis；具备良好沟通能力与团队合作精神。",
        "keywords": ["Java", "MySQL", "Spring Boot"],
    }], source_id="t")
    job = {j.title: j for j in js.search("")}["Java 后端开发工程师"]
    rid, vid = ResumeService(con).build_master_from_profile(profile.id)
    yield con, profile.id, job.id, vid
    con.close()
    config.set_data_dir(None)


# ---------- ATS 报告 ----------

def test_ats_report_structure_and_math(env):
    con, pid, jid, vid = env
    rep = ATSScanService(con).scan(jid, vid)
    # 结构契约
    for key in ("score", "target", "band", "coverage", "parseability", "keywords", "advisory", "methodology"):
        assert key in rep
    kw = rep["keywords"]
    # Java/MySQL 在画像技能里 → 命中；Redis 只在 JD 出现 → 硬缺失
    matched_terms = [e["term"] for e in kw["matched"]]
    assert "Java" in matched_terms and "MySQL" in matched_terms
    assert "redis" in [t.lower() for t in kw["hard_missing"]]
    # 证据链：每个命中词带分节 + 摘录 + JD 计数（竞品均不提供）
    java_entry = next(e for e in kw["matched"] if e["term"] == "Java")
    assert java_entry["hits"][0]["section"] and java_entry["hits"][0]["snippet"]
    assert java_entry["jd_count"] >= 1
    # 计分透明度声明存在
    assert rep["methodology"]["counted"] and rep["methodology"]["advisory_only"]
    # 分数 = 覆盖(70 满分) + 可解析(30 满分)，不超界
    assert 0 <= rep["score"] <= 100
    assert rep["coverage"]["score"] <= 70 and rep["parseability"]["score"] <= 30


def test_ats_report_format_checks_catch_thin_resume(env):
    """薄简历：联系方式齐但分节不足 → 可解析性扣分 + fix 建议给出动作。"""
    con, pid, jid, vid = env
    rep = ATSScanService(con).scan(jid, vid)
    checks = {c["item"]: c for c in rep["parseability"]["checks"]}
    contact = next(c for k, c in checks.items() if k.startswith("联系方式"))
    assert contact["ok"] is True  # fixture 画像带 phone/email
    sections = next(c for k, c in checks.items() if k.startswith("标准分节"))
    assert sections["ok"] is False  # 只有 education 一节
    assert "fix" in sections and sections["fix"]


def test_ats_report_stuffing_warning(env):
    """技能词只出现在 skills 分节、经历/项目零支撑 → 黄牌（不计分，反堆砌立场）。"""
    con, pid, jid, vid = env
    rep = ATSScanService(con).scan(jid, vid)
    warnings = [a for a in rep["advisory"] if a["kind"] == "stuffing_warning"]
    assert warnings and "Java" in warnings[0]["title"]


def test_ats_report_rejects_unknown_ids(env):
    con, pid, jid, vid = env
    with pytest.raises(ATSScanError, match="岗位不存在"):
        ATSScanService(con).scan("job_nope", vid)
    with pytest.raises(ATSScanError, match="简历版本不存在"):
        ATSScanService(con).scan(jid, "rv_nope")


# ---------- 投递标签 ----------

def test_application_tags_crud_and_filter(env):
    con, pid, jid, _ = env
    from jobhater.services.lifecycle import ApplicationService

    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    tagged = apps.add_tag(app, "内推")
    assert tagged["tags"] == ["内推"]
    again = apps.add_tag(app, "内推")  # 幂等
    assert again["tags"] == ["内推"]
    apps.add_tag(app, "优先")
    assert [r["id"] for r in apps.list(pid, tag="内推")] == [app]
    assert apps.list(pid, tag="不存在") == []
    removed = apps.remove_tag(app, "内推")
    assert removed["tags"] == ["优先"]
    with pytest.raises(LifecycleError, match="标签不存在"):
        apps.remove_tag(app, "内推")
    with pytest.raises(LifecycleError, match="标签不能为空"):
        apps.add_tag(app, "  ")


# ---------- CSV 导出（API 层，Excel BOM） ----------

def test_csv_export_endpoints(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as client:  # with 触发 lifespan（自动迁移）
        pid = client.post("/api/profiles", json={"display_name": "测试者"}).json()["id"]
        client.post("/api/jobs/import", json={"jobs": [{
            "title": "Java 后端开发工程师", "company": "云图信息", "city": "杭州",
            "salary": "20-35K", "description": "Java 微服务",
        }]})
        jid = client.get("/api/jobs").json()["items"][0]["id"]
        app_id = client.post("/api/applications", json={
            "job_id": jid, "profile_id": pid,
        }).json()["id"]
        client.post(f"/api/applications/{app_id}/tags/内推")

        r = client.get(f"/api/export/applications.csv?profile_id={pid}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert r.content.startswith(b"\xef\xbb\xbf")  # Excel 中文 BOM
        rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
        assert rows and rows[0]["job_title"] == "Java 后端开发工程师"
        assert rows[0]["tags"] == "内推"

        r2 = client.get("/api/export/jobs.csv")
        assert r2.status_code == 200
        jrows = list(csv.DictReader(io.StringIO(r2.content.decode("utf-8-sig"))))
        assert any(j["title"] == "Java 后端开发工程师" for j in jrows)
    config.set_data_dir(None)


# ---------- 双模板 ----------

def test_resume_compact_template(env):
    con, pid, jid, vid = env
    rs = ResumeService(con)
    classic = rs.render_html(vid)
    compact = rs.render_html(vid, template="compact")
    assert compact != classic  # 版式确实不同（一页致密 vs 标准衬线）
    assert "浙江大学" in compact  # 内容一致（教育节都在）
    assert "浙江大学" in classic


def test_export_route_template_validation(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(create_app()) as client:
        pid = client.post("/api/profiles", json={"display_name": "t"}).json()["id"]
        con = connect()
        rid, vid = ResumeService(con).build_master_from_profile(pid)
        ok = client.get(f"/api/resume-versions/{vid}/export?fmt=html&template=compact")
        assert ok.status_code == 200 and "浙江大学" not in ok.text  # 画像无教育，正文为空壳
        bad = client.get(f"/api/resume-versions/{vid}/export?fmt=html&template=fancy")
        assert bad.status_code == 422
        con.close()
    config.set_data_dir(None)
