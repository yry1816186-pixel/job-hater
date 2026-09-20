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
