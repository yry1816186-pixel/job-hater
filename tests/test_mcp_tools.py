"""MCP server 工具层测试：不经 stdio，直接调用注册的函数实现。"""
from __future__ import annotations

import json

import pytest

from jobhater import config
from jobhater.db import apply_all, connect
from jobhater.services.profile import ProfileService


@pytest.fixture()
def env(tmp_path):
    config.set_data_dir(tmp_path)
    apply_all()  # 用默认 db 路径——与 MCP 工具内部的 connect() 一致
    con = connect()
    ps = ProfileService(con)
    p = ps.create_profile("MCP测试")
    ps.add_skill(p.id, "机器学习", aliases=["ml"])
    ps.create_preset(p.id, "默认", target_roles=["算法"], target_cities=["北京"])
    con.close()
    yield tmp_path, p.id
    config.set_data_dir(None)


def test_mcp_tool_roundtrip(env):
    from jobhater import mcp_server as ms

    tmp, pid = env
    # 导入
    r = json.loads(ms.jobs_import(json.dumps([
        {"title": "算法工程师", "company": "示例科技", "city": "北京", "salary": "20-30K",
         "description": "机器学习推荐算法"}
    ])))
    assert r["added"] == 1
    # 检索
    found = json.loads(ms.jobs_search("算法"))
    assert found[0]["title"] == "算法工程师"
    jid = found[0]["id"]
    # 匹配
    ranked = json.loads(ms.match_run(pid))
    assert ranked[0]["job_id"] == jid and ranked[0]["eligible"] is True
    # 详情带匹配解释
    detail = json.loads(ms.jobs_get(jid, profile_id=pid))
    assert detail["match"]["dims"]["skill_match"]["score"] >= 60
    # 画像
    prof = json.loads(ms.profile_get(pid))
    assert prof["profile"]["display_name"] == "MCP测试"
    # 投递 + 状态机拒绝伪造
    from jobhater.services.lifecycle import ApplicationService

    con = connect()
    app = ApplicationService(con).create(jid, pid)
    con.close()
    apps = json.loads(ms.applications_list(pid))
    assert apps[0]["id"] == app
    denied = json.loads(ms.applications_update(app, "applied_confirmed"))
    assert "不允许" in denied["error"] or "用户" in denied["error"]
    ok = json.loads(ms.applications_update(app, "saved"))
    assert ok["status"] == "saved"


def test_mcp_resumes_prepare(env):
    from jobhater import mcp_server as ms

    tmp, pid = env
    r = json.loads(ms.resumes_prepare(pid))
    assert "version_id" in r and "factcheck" in r


_RESUME = """李小白
电话 13800001111 · 邮箱 libai@example.com
求职意向：数据分析师

教育背景
临江大学 统计学专业 本科 2021.09 - 2025.06

技能
熟练使用 Python、SQL、Tableau、Excel

工作经历
2023.06 - 2023.09 某电商公司 数据分析实习生
- 负责销售日报与活动复盘
"""


def test_mcp_agent_onboarding_loop(tmp_path):
    """v3.7 插件闭环：profile_list 空 → profile_create(简历文本) → preset_set → 匹配可用。"""
    from jobhater import mcp_server as ms

    config.set_data_dir(tmp_path)
    apply_all()
    try:
        empty = json.loads(ms.profile_list())
        assert "hint" in empty  # 空库给可行动指引

        created = json.loads(ms.profile_create(resume_text=_RESUME))
        assert created["profile_id"].startswith("prof")
        assert created["display_name"] == "李小白"
        assert created["imported"]["skills"] >= 3  # Python/SQL/Tableau/Excel 至少三样
        assert created["imported"]["educations"] == 1

        listed = json.loads(ms.profile_list())
        assert listed[0]["id"] == created["profile_id"]
        assert listed[0]["skills"] >= 3

        preset = json.loads(ms.preset_set(
            created["profile_id"],
            target_roles_json='["数据分析"]',
            target_cities_json='["杭州"]',
            employment_types_json='["social", "bogus_type"]',
            salary_min_k=12,
        ))
        assert preset["employment_types"] == ["social"]
        assert preset["ignored_types"] == ["bogus_type"]  # 非法类型如实退回不静默吞
        assert preset["salary_min_k"] == 12

        # 更新路径：再次 preset_set 不改名、字段合并
        again = json.loads(ms.preset_set(
            created["profile_id"], target_cities_json='["杭州", "上海"]',
        ))
        assert again["name"] == "求职偏好" and again["target_cities"] == ["杭州", "上海"]
        assert again["target_roles"] == ["数据分析"]  # 未传字段保持

        # 导入一条对口岗 → 匹配闭环
        ms.jobs_import(json.dumps([
            {"title": "数据分析师", "company": "示例电商", "city": "杭州",
             "salary": "15-25K", "description": "Python SQL Tableau 报表分析",
             "recruitment_type": "social"},
        ]))
        ranked = json.loads(ms.match_run(created["profile_id"]))
        assert ranked and ranked[0]["eligible"] is True
    finally:
        config.set_data_dir(None)


def test_mcp_profile_create_requires_name(tmp_path):
    from jobhater import mcp_server as ms

    config.set_data_dir(tmp_path)
    apply_all()
    try:
        r = json.loads(ms.profile_create())  # 既无姓名也无简历
        assert "error" in r
    finally:
        config.set_data_dir(None)


def test_mcp_sources_fetch_dry_run(tmp_path, monkeypatch):
    """sources_fetch dry_run：只健康检查不入库（不真打外网——mock 适配器）。"""
    import jobhater.services.sources as srcs
    from jobhater import mcp_server as ms

    class _FakeReport:
        ok = True
        message = "模拟健康"

    class _FakeAdapter:
        def __init__(self, companies=None): ...
        def produce(self):
            return [{"title": "模拟岗", "company": "模拟公司"}]
        last_fetch_report = {"模拟公司": _FakeReport()}
        def health_check(self):
            return _FakeReport()

    # mcp_server 函数内 from jobhater.services.sources import WenkeAdapter → 补丁包属性
    monkeypatch.setattr(srcs, "WenkeAdapter", _FakeAdapter)

    config.set_data_dir(tmp_path)
    apply_all()
    try:
        r = json.loads(ms.sources_fetch(dry_run=True))
        assert r["dry_run"] is True and r["fetched"] == 1
        r2 = json.loads(ms.sources_fetch())
        assert r2["added"] == 1 and r2["report"]["模拟公司"]["ok"] is True
    finally:
        config.set_data_dir(None)
