"""CLI 冒烟：doctor / import / match 与同一服务层行为一致。"""
from __future__ import annotations

import json

from jobhater import config
from jobhater.cli import main


def _run(tmp_path, *argv: str) -> int:
    config.set_data_dir(tmp_path)
    try:
        return main(list(argv))
    finally:
        config.set_data_dir(None)


def test_doctor_import_match(tmp_path, capsys):
    assert _run(tmp_path, "doctor") == 0
    assert "数据库就绪" in capsys.readouterr().out

    f = tmp_path / "jobs.json"
    f.write_text(json.dumps([
        {"title": "算法工程师", "company": "示例科技", "city": "北京",
         "salary": "20-30K", "description": "机器学习推荐算法"},
    ], ensure_ascii=False), encoding="utf-8")
    assert _run(tmp_path, "import", str(f), "--source", "manual") == 0
    out = capsys.readouterr().out
    assert "入库 1" in out

    # 建画像+偏好（直接走服务层，CLI 不重复业务逻辑）
    from jobhater.db import connect
    from jobhater.services.profile import ProfileService

    config.set_data_dir(tmp_path)
    con = connect()
    ps = ProfileService(con)
    p = ps.create_profile("CLI测试")
    ps.add_skill(p.id, "机器学习", aliases=["ml"])
    ps.create_preset(p.id, "默认", target_roles=["算法"], target_cities=["北京"])
    con.close()
    config.set_data_dir(None)

    assert _run(tmp_path, "match", "--profile", p.id) == 0
    out = capsys.readouterr().out
    assert "算法工程师" in out and "评估 1 岗" in out


def test_profile_and_preset_verbs(tmp_path, capsys):
    """v3.7：CLI 无 MCP 也能闭环——profile create(简历) → preset → match。"""
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "王小明\n求职意向：新媒体运营\n\n教育背景\n临江大学 新闻学专业 本科"
        " 2021.09-2025.06\n\n技能\n熟悉公众号排版、剪映、数据分析\n", encoding="utf-8",
    )
    assert _run(tmp_path, "profile", "list") == 0
    assert "还没有画像" in capsys.readouterr().out  # 空库给可行动指引

    assert _run(tmp_path, "profile", "create", "--resume-file", str(resume)) == 0
    out = capsys.readouterr().out
    assert "画像已建立" in out

    # 缺姓名的报错路径（首行超 4 字不落入裸姓名启发式）
    empty = tmp_path / "empty.txt"
    empty.write_text("求职材料若干页，均为项目与课程描述，无姓名行。", encoding="utf-8")
    assert _run(tmp_path, "profile", "create", "--resume-file", str(empty)) == 2

    config.set_data_dir(tmp_path)
    from jobhater.db import connect
    con = connect()
    pid = con.execute("SELECT id FROM candidate_profiles LIMIT 1").fetchone()["id"]
    con.close()
    config.set_data_dir(None)

    assert _run(
        tmp_path, "preset", "--profile", pid, "--role", "新媒体运营",
        "--city", "上海", "--type", "social", "--salary-min", "8",
    ) == 0
    assert "偏好" in capsys.readouterr().out
    assert _run(tmp_path, "preset", "--profile", pid, "--type", "bogus") == 2  # 非法类型拒
    assert _run(tmp_path, "preset", "--profile", "prof_x", "--role", "x") == 2  # 画像不存在

    f = tmp_path / "job.json"
    f.write_text(json.dumps([
        {"title": "新媒体运营专员", "company": "示例传媒", "city": "上海",
         "salary": "9-13K", "description": "公众号排版 剪映 数据分析",
         "recruitment_type": "social"},
    ], ensure_ascii=False), encoding="utf-8")
    assert _run(tmp_path, "import", str(f)) == 0
    assert _run(tmp_path, "match", "--profile", pid) == 0
    out = capsys.readouterr().out
    assert "新媒体运营专员" in out
