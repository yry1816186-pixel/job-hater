"""微信服务编排离线 E2E：合成加密库 + mock 环境/密钥 → 全链路（解密→解析→识别→结果）。"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))


from jobhater.services.wechat.service import WeChatService  # noqa: E402


def _build_account_env(root: Path, key: bytes) -> object:
    """构造假账号目录：加密的 message_0.db + contact.db，返回 mock detect() 结果。

    库用 wcdb_fixture 手造（reserved=80、cell 全在 [0:4016]），否则加密往返
    会截断页尾 cell。
    """
    from wcdb_fixture import build_wcdb_db

    from jobhater.services.wechat.decrypt import encrypt_database_for_test
    from jobhater.services.wechat.detect import WeChatAccount, WeChatEnv

    acc_dir = root / "wxid_fake_0011"
    msg_dir = acc_dir / "db_storage" / "message"
    ct_dir = acc_dir / "db_storage" / "contact"
    msg_dir.mkdir(parents=True)
    ct_dir.mkdir(parents=True)

    room, friend = "room@chatroom", "wxid_hr"
    now = int(time.time())
    good = "【2027届校招】腾讯招聘后端开发工程师 base深圳/杭州 薪资25-45k 简历投递 hr@tencent.com"
    bytedance = "同学你好，我们字节跳动2027届秋招内推开启，算法工程师岗位，北京，30-60k，链接 https://ref.bytedance.com"
    msg_cols = "(local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, status INTEGER, message_content TEXT, packed_info_data BLOB)"
    build_wcdb_db(
        root / "plain_msg.db",
        [
            (
                f"Msg_{hashlib.md5(room.encode()).hexdigest()}",
                f"CREATE TABLE Msg_{hashlib.md5(room.encode()).hexdigest()}{msg_cols}",
                [[None, 0, 1, 0, 5, now, 4, f"wxid_hr:\n{good}", None]],
            ),
            (
                f"Msg_{hashlib.md5(friend.encode()).hexdigest()}",
                f"CREATE TABLE Msg_{hashlib.md5(friend.encode()).hexdigest()}{msg_cols}",
                [[None, 0, 1, 0, 6, now - 60, 4, bytedance, None]],
            ),
            ("Name2Id", "CREATE TABLE Name2Id(id INTEGER PRIMARY KEY, username TEXT)", [[None, "wxid_hr"], [None, "wxid_friend"]]),
        ],
    )
    encrypt_database_for_test(root / "plain_msg.db", msg_dir / "message_0.db", key, secrets.token_bytes(16))

    build_wcdb_db(
        root / "plain_ct.db",
        [
            (
                "contact",
                "CREATE TABLE contact(username TEXT, alias TEXT, remark TEXT, nick_name TEXT, local_type INTEGER)",
                [
                    [room, "", "秋招信息群", "群", 2],
                    [friend, "", "", "招聘HR小李", 1],
                    ["wxid_hr", "", "", "HR", 1],
                ],
            )
        ],
    )
    encrypt_database_for_test(root / "plain_ct.db", ct_dir / "contact.db", key, secrets.token_bytes(16))

    account = WeChatAccount(
        key=acc_dir.name, wxid="wxid_fake", root=acc_dir,
        message_dbs=[msg_dir / "message_0.db"], contact_dbs=[ct_dir / "contact.db"],
        total_db_bytes=1000,
    )
    env = WeChatEnv(installed=True, data_root=root, accounts=[account], weixin_pids=[1])
    return env


def test_scan_pipeline_offline(tmp_path: Path):
    import pytest

    # 合成库需 pycryptodome 加密（可选依赖 jobhater[wechat]），缺失时降级跳过
    pytest.importorskip("Crypto", reason="pycryptodome 未安装（jobhater[wechat] 可选依赖）")
    from jobhater import config

    config.set_data_dir(tmp_path / "data")
    key = secrets.token_bytes(32)
    env = _build_account_env(tmp_path / "wxroot", key)
    svc = WeChatService()

    with (
        patch("jobhater.services.wechat.service.detect_env", return_value=env),
        patch("jobhater.services.wechat.service.find_key", return_value=key),
    ):
        status = svc.start_scan()
        assert status["phase"] in {"detecting", "extracting_key", "decrypting", "parsing", "analyzing", "done"}, f"error={status.get('error')!r}"
        # 等后台线程结束（离线合成数据, 秒级）
        if svc._thread is not None:
            svc._thread.join(timeout=60)
        final = svc.status()
    assert final["phase"] == "done", final
    assert final["error"] is None
    assert final["stats"]["dbs_decrypted"] == 2
    assert final["stats"]["messages_analyzed"] >= 2

    res = svc.results()
    assert res is not None
    companies = [h["company"] for h in res["hits"]]
    assert any(c and "腾讯" in c for c in companies), companies
    assert any(c and "字节" in c for c in companies), companies
    cohorts = {h["cohort"] for h in res["hits"]}
    assert 2027 in cohorts
    # 密钥绝不落盘
    raw = json.dumps(res)
    assert key.hex() not in raw
    # 解密产物在数据目录下且可清除（先取路径再 purge：work_dir() 调用会自建目录）
    assert svc.decrypted_dir().exists()
    wd = svc.work_dir()
    svc.purge()
    assert not wd.exists() and not svc.result_path().exists()
    config.set_data_dir(None)
