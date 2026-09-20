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
