"""微信本地数据源测试：解密往返 / 消息解析 / 招聘识别金样 / 服务编排 / API。"""
from __future__ import annotations

import secrets
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from wcdb_fixture import build_wcdb_style_db  # noqa: E402

from jobhater.services.wechat import recruit as rc  # noqa: E402
from jobhater.services.wechat.decrypt import (  # noqa: E402
    decrypt_database,
    encrypt_database_for_test,
    quick_screen,
    verify_key,
)

# ==================== 解密器 ====================


@pytest.fixture()
def enc_pair(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """(明文WCDB库, 加密库, 密钥) — 手造 reserved=80 夹具全程 sqlite 可读。"""
    plain = tmp_path / "wc.db"
    build_wcdb_style_db(plain, 50)
    key = secrets.token_bytes(32)
    enc = tmp_path / "wc.enc.db"
    encrypt_database_for_test(plain, enc, key, secrets.token_bytes(16))
    return plain, enc, key


def _read_table(db: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT id, name FROM t ORDER BY id").fetchall()
    finally:
        con.close()


def test_encrypted_file_has_no_sqlite_magic(enc_pair):
    _plain, enc, _key = enc_pair
    assert enc.read_bytes()[:16] != b"SQLite format 3\x00"


def test_verify_and_screen_accept_only_right_key(enc_pair):
    _plain, enc, key = enc_pair
    page1 = enc.read_bytes()[:4096]
    assert verify_key(key, page1)
    assert quick_screen(key, page1)
    for _ in range(20):
        bad = secrets.token_bytes(32)
        assert not verify_key(bad, page1)
        assert not quick_screen(bad, page1)


def test_decrypt_roundtrip_sqlite_readable(enc_pair):
    _plain, enc, key = enc_pair
    out = enc.with_suffix(".dec.db")
    r = decrypt_database(enc, out, key)
    assert r.ok and r.error is None
    assert _read_table(out)[:3] == [(1, "name1"), (2, "name2"), (3, "name3")]
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_decrypt_rejects_wrong_key(enc_pair):
    _plain, enc, _key = enc_pair
    r = decrypt_database(enc, enc.with_suffix(".bad.db"), secrets.token_bytes(32))
    assert not r.ok and "HMAC" in (r.error or "")


def test_decrypt_rejects_tampered_page1(enc_pair):
    _plain, enc, key = enc_pair
    tam = bytearray(enc.read_bytes())
    tam[100] ^= 0x01
    tam_path = enc.with_suffix(".tam.db")
    tam_path.write_bytes(bytes(tam))
    r = decrypt_database(tam_path, enc.with_suffix(".tam.dec.db"), key)
    assert not r.ok


# ==================== 招聘识别（金样） ====================

POS_CAMPUS = """【2027届校招】字节跳动后端开发工程师
招聘：字节跳动
岗位：后端开发工程师（Golang/Python）
Base：北京/上海
薪资：25-50k·15薪
要求：2027届本科及以上，计算机相关专业
职责：
1. 负责核心服务开发
2. 参与架构设计
投递：https://job.bytedance.com/campus"""

POS_INTERN = "急招！腾讯日常实习—数据分析实习生，base深圳，要求2027届本科，一周4天到岗，简历发 hr@example.com，可转正"

POS_GROUP_JD = """华为2027届校园招聘启动啦！
岗位名称：算法工程师
工作地点：杭州
薪资范围：30k-45k
任职要求：
- 2027届硕士起
- 熟悉机器学习
公司：华为技术有限公司
截止10月30日"""

NEG_CHAT = "今天天气不错，中午吃什么"
NEG_SEEKER = "求问有大佬内推吗？我的简历可以帮忙看看吗？怎么投递呀"
NEG_NEWS = "新闻：某公司宣布裁员百分之十，股价下跌"


@pytest.mark.parametrize(
    ("text", "cohort", "kind"),
    [
        (POS_CAMPUS, 2027, "campus"),
        (POS_INTERN, 2027, "intern"),
        (POS_GROUP_JD, 2027, "campus"),
    ],
)
def test_recruit_positives(text: str, cohort: int, kind: str):
    hit = rc.analyze(text)
    assert hit is not None, f"应为招聘信息: {text[:30]}"
    assert hit.confidence >= 0.5
    assert hit.cohort == cohort
    assert hit.kind == kind
    assert hit.is_recruiter_side


@pytest.mark.parametrize("text", [NEG_CHAT, NEG_SEEKER, NEG_NEWS])
def test_recruit_negatives(text: str):
    hit = rc.analyze(text)
    assert hit is None or (hit.confidence < 0.5 and not hit.is_recruiter_side) or hit.confidence < 0.35


def test_recruit_fields_extraction():
    hit = rc.analyze(POS_CAMPUS)
    assert hit is not None
    assert hit.company and "字节" in hit.company
    assert hit.title and "后端" in hit.title
    assert "北京" in hit.cities
    assert hit.salary and "25" in hit.salary
    assert hit.apply_method and "job.bytedance.com" in hit.apply_method
    assert any("意图词" in e for e in hit.evidence)


def test_recruit_deadline():
    hit = rc.analyze(POS_GROUP_JD)
    assert hit is not None
    assert hit.deadline is not None and "10" in hit.deadline


def test_recruit_short_or_empty():
    assert rc.analyze("") is None
    assert rc.analyze("招") is None


def test_recruit_cohort_other_year():
    text = POS_CAMPUS.replace("2027", "2026")
    hit = rc.analyze(text)
    assert hit is not None and hit.cohort == 2026


# ==================== 消息解析（合成库） ====================


def _mk_msg_db(path: Path, talker: str, rows: list[tuple], with_name2id: dict[int, str] | None = None) -> None:
    """构造一个含 Msg_md5(talker) 表的明文库。rows: (local_type, sender_id, create_time, content)。"""
    import hashlib

    con = sqlite3.connect(path)
    con.execute(
        f"""CREATE TABLE Msg_{hashlib.md5(talker.encode()).hexdigest()} (
        local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER,
        sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, status INTEGER,
        message_content TEXT, packed_info_data BLOB)"""
    )
    con.executemany(
        f"INSERT INTO Msg_{hashlib.md5(talker.encode()).hexdigest()} "
        "(local_type, real_sender_id, create_time, status, message_content) VALUES (?,?,?,?,?)",
        rows,
    )
    if with_name2id:
        con.execute("CREATE TABLE IF NOT EXISTS Name2Id(id INTEGER PRIMARY KEY, username TEXT)")
        con.executemany("INSERT OR REPLACE INTO Name2Id(id, username) VALUES (?,?)", list(with_name2id.items()))
    con.commit()
    con.close()


def _mk_contact_db(path: Path, contacts: list[tuple[str, str, int]]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE contact(username TEXT, alias TEXT, remark TEXT, nick_name TEXT, local_type INTEGER)")
    con.executemany("INSERT INTO contact VALUES (?,?,?,?,?)", contacts)
    con.commit()
    con.close()


def _zstd(data: bytes) -> bytes:
    import zstandard

    return zstandard.ZstdCompressor().compress(data)


def test_parser_roundtrip(tmp_path: Path):
    room = "room1@chatroom"
    friend = "wxid_friend"
    msg_db = tmp_path / "message_0.db"
    _mk_msg_db(
        msg_db,
        room,
        [
            (1, 7, 1700000000, 0, "wxid_alice:\n【2027校招】阿里招聘后端工程师 base杭州 25-40k"),
            (1, 8, 1700000060, 0, _zstd("wxid_bob:\n美团2027届校招内推，产品经理岗，北京".encode())),
            (3, 7, 1700000100, 0, "wxid_alice:\n[图片]"),  # 图片应被过滤
        ],
        with_name2id={7: "wxid_alice", 8: "wxid_bob"},
    )
    _mk_msg_db(msg_db, friend, [(1, 3, 1700000200, 0, "蔚来急招2027届算法实习生，上海，投递 hr@nio.com")], with_name2id={3: friend})
    contact_db = tmp_path / "contact.db"
    _mk_contact_db(
        contact_db,
        [(room, "", "求职互助群", "群昵称", 2), (friend, "", "备注朋友", "朋友昵称", 1), ("wxid_alice", "", "", "Alice", 1), ("wxid_bob", "", "", "Bob", 1)],
    )
    from jobhater.services.wechat.parser import iter_messages, load_contacts, msg_table_name

    assert msg_table_name(room).startswith("Msg_")
    contacts = load_contacts(contact_db)
    assert contacts[room]["name"] == "求职互助群"  # 备注优先
    msgs = list(iter_messages([msg_db], contacts, self_wxid="wxid_me"))
    assert len(msgs) == 3  # 图片被过滤
    room_msgs = [m for m in msgs if m.talker == room]
    assert all(m.is_chatroom for m in room_msgs)
    # 群聊 sender 前缀剥离
    alice = next(m for m in room_msgs if "阿里" in m.content)
    assert alice.sender == "wxid_alice" and alice.sender_name == "Alice"
    assert alice.content.startswith("【2027校招】")
    # zstd 解压
    bob = next(m for m in room_msgs if "美团" in m.content)
    assert bob.content.startswith("美团") and bob.sender == "wxid_bob"
    # 私聊
    private = next(m for m in msgs if m.talker == friend)
    assert private.talker_name == "备注朋友"
    assert not private.is_chatroom


def test_parser_skips_unknown_tables(tmp_path: Path):
    """不在 contact 名单里的 Msg_ 表（无名会话）应被跳过而非崩溃。"""
    msg_db = tmp_path / "message_0.db"
    _mk_msg_db(msg_db, "ghost@chatroom", [(1, 1, 1700000000, 0, "x:\n内容")])
    from jobhater.services.wechat.parser import iter_messages

    assert list(iter_messages([msg_db], {})) == []


# ==================== 服务层 analyze（burst 合并） ====================


class _Msg:
    """测试用消息替身：字段与 WeChatMessage 对齐。"""

    def __init__(self, talker: str, sender: str, t: int, content: str):
        from jobhater.services.wechat.parser import WeChatMessage

        self.m = WeChatMessage(
            talker=talker, talker_name=talker, is_chatroom=True,
            sender=sender, sender_name=sender, is_self=False,
            msg_type=1, create_time=t, content=content,
        )


def test_service_analyze_burst_merge(tmp_path: Path):
    from jobhater.services.wechat import parser as wx_parser
    from jobhater.services.wechat.service import ScanState, WeChatService

    svc = WeChatService()
    state = ScanState()
    msgs = [
        wx_parser.WeChatMessage("g@chatroom", "群", True, "hr01", "HR小王", False, 1, 1000, "【2027届校招】米哈游招聘"),
        wx_parser.WeChatMessage("g@chatroom", "群", True, "hr01", "HR小王", False, 1, 1010, "岗位：游戏策划"),
        wx_parser.WeChatMessage("g@chatroom", "群", True, "hr01", "HR小王", False, 1, 1020, "Base：上海 薪资：20-35k"),
        wx_parser.WeChatMessage("g@chatroom", "群", True, "u9", "路人", False, 1, 1030, "哈哈哈谢谢分享"),
    ]
    # 三条单发都不够置信，但合并后是完整 JD —— mock iter_messages 注入消息流
    from unittest.mock import patch

    import jobhater.services.wechat.service as svc_mod

    with patch.object(svc_mod.wx_parser, "iter_messages", return_value=iter(msgs)):
        hits = svc._analyze([], {}, "me", 0.35, state)
    merged = [h for h in hits if h["merged"]]
    assert merged, "三条连发 JD 应被合并识别"
    assert "米哈游" in merged[0]["source_text"] or "游戏策划" in merged[0]["source_text"]
    assert state.messages_analyzed == 4


def test_service_results_and_status_idle(tmp_path):
    from jobhater import config

    config.set_data_dir(tmp_path)
    from jobhater.services.wechat.service import WeChatService

    svc = WeChatService()
    assert svc.results() is None
    assert svc.status()["phase"] == "idle"
    assert svc.status()["running"] is False
    config.set_data_dir(None)


# ==================== API ====================


def test_api_wechat_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobhater import config

    config.set_data_dir(tmp_path)
    from jobhater.api.app import create_app

    with TestClient(create_app()) as client:  # with 触发 lifespan（自动迁移）
        # env 端点应可用（结果依赖本机是否装微信，不硬断言）
        r = client.get("/api/wechat/env")
        assert r.status_code == 200
        assert "blockers" in r.json()
        # results 空态
        r = client.get("/api/wechat/results")
        assert r.status_code == 200 and r.json()["hits"] == []
        # import 无结果 → 404
        r = client.post("/api/wechat/import", json={"min_confidence": 0.5})
        assert r.status_code == 404
    config.set_data_dir(None)


def test_api_wechat_import_with_fake_result(tmp_path, monkeypatch):
    """注入伪扫描结果 → 导入应走 ingest 链成功入库。"""
    import json
    import time as _time

    from fastapi.testclient import TestClient

    from jobhater import config

    config.set_data_dir(tmp_path)
    from jobhater.api.app import create_app
    from jobhater.services.wechat.service import WeChatService

    svc = WeChatService()
    result = {
        "generated_at": _time.time(),
        "account": "wxid_test",
        "stats": {},
        "hits": [
            {
                "company": "测试科技有限公司", "title": "后端工程师", "cities": ["北京"],
                "salary": "20-40k", "education": "本科", "cohort": 2027, "kind": "campus",
                "deadline": None, "apply_method": None, "confidence": 0.9,
                "evidence": [], "is_recruiter_side": True,
                "talker": "g@chatroom", "talker_name": "求职群", "sender_name": "HR",
                "create_time": 1700000000, "time_str": "2023-11-15 06:13",
                "source_text": "【2027届校招】测试科技有限公司招聘后端工程师 base北京 20-40k",
                "merged": False,
            }
        ],
    }
    svc.result_path().write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    # 同 data_dir 下 app 内单例会读到同一 result 文件；with 触发 lifespan（自动迁移）
    with TestClient(create_app()) as client:
        r = client.get("/api/wechat/results")
        assert r.status_code == 200 and len(r.json()["hits"]) == 1

        r = client.post("/api/wechat/import", json={"min_confidence": 0.5, "cohort": 2027})
        assert r.status_code == 200
        body = r.json()
        assert body["added"] >= 1, body

        # 入库后 jobs 可搜到
        r = client.get("/api/jobs", params={"q": "后端工程师"})
        assert r.status_code == 200
    config.set_data_dir(None)
