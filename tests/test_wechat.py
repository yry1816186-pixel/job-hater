"""微信本地数据源测试：解密往返 / 消息解析 / 招聘识别金样 / 服务编排 / API。"""
from __future__ import annotations

import importlib.util
import secrets
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# pycryptodome 是可选 extras（jobhater[wechat]）：产品代码缺失时优雅降级，
# 加密往返类测试同样跳过；识别/解析/API 测试不依赖它照常运行
needs_aes = pytest.mark.skipif(
    importlib.util.find_spec("Crypto") is None,
    reason="pycryptodome 未安装（可选依赖 jobhater[wechat]）",
)

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
    pytest.importorskip("Crypto", reason="pycryptodome 未安装（jobhater[wechat] 可选依赖）")
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


def test_parser_flagged_local_type_and_appmsg(tmp_path: Path):
    """v4 低位掩码解码 + 49 型 appmsg XML 文本化（实测暴露的两类漏源）。"""
    room = "room@chatroom"
    msg_db = tmp_path / "message_0.db"
    # 21474836481 = 5×2^32+1（带 flag 的文本）；244813135921 低位=49（flag 变体卡片）；4294967345 纯 49
    appmsg_xml = (
        'wxid_alice:\n<?xml version="1.0"?>\n<msg><appmsg><title>'
        "【字节跳动】2027届校园招聘正式启动</title><des>面向2027届毕业生，技术/产品/运营岗"
        '</des><url>https://jobs.bytedance.com/campus</url></appmsg></msg>'
    )
    flagged_card = 'wxid_alice:\n<msg><appmsg><title>搜狐畅游2027暑期实习</title></appmsg></msg>'
    _mk_msg_db(
        msg_db,
        room,
        [
            (21474836481, 7, 1700000000, 0, "wxid_alice:\n联想2027届秋招启动，供应链岗"),
            (4294967345, 7, 1700000060, 0, appmsg_xml),
            (244813135921, 7, 1700000120, 0, flagged_card),
            (3, 7, 1700000100, 0, "wxid_alice:\n[图片]"),  # 低位=3 过滤
        ],
        with_name2id={7: "wxid_alice"},
    )
    contact_db = tmp_path / "contact.db"
    _mk_contact_db(contact_db, [(room, "", "测试群", "", 2), ("wxid_alice", "", "", "Alice", 1)])
    from jobhater.services.wechat.parser import iter_messages, load_contacts

    msgs = list(iter_messages([msg_db], load_contacts(contact_db)))
    assert len(msgs) == 3, [m.content for m in msgs]
    text = next(m for m in msgs if "联想" in m.content)
    assert text.msg_type == 1 and "联想" in text.content
    card = next(m for m in msgs if "字节" in m.content)
    assert card.msg_type == 49
    assert card.content.startswith("【字节跳动】2027届校园招聘正式启动")
    assert "面向2027届毕业生" in card.content and "jobs.bytedance.com" in card.content
    assert "<appmsg>" not in card.content  # XML 已文本化
    flagged = next(m for m in msgs if "搜狐畅游" in m.content)
    assert flagged.msg_type == 49 and flagged.content == "搜狐畅游2027暑期实习"


def test_appmsg_to_text_edge_cases():
    """非 XML 原样返回；视频号 ##token## 噪声标题过滤；实体反转义；CDATA 剥离。"""
    from jobhater.services.wechat.parser import appmsg_to_text

    assert appmsg_to_text("普通文本不走提取") == "普通文本不走提取"
    noisy = "<msg><appmsg><title>##X4oZ6vla##我在快手看的视频</title><des>分享</des></appmsg></msg>"
    assert "##" not in appmsg_to_text(noisy)
    esc = '<msg><appmsg><title>A&amp;B公司 &lt;2027校招&gt;</title></appmsg></msg>'
    assert "A&B公司 <2027校招>" in appmsg_to_text(esc)
    cdata = "<msg><appmsg><title><![CDATA[招聘 | 中兴通讯2027届未来领军人才招聘]]></title><des><![CDATA[]]></des></appmsg></msg>"
    got = appmsg_to_text(cdata)
    assert got == "招聘 | 中兴通讯2027届未来领军人才招聘", got


def test_recruit_card_title_company():
    """公众号卡片标题模式：「招聘丨公司NNNN届…」抽出公司（去重键不塌缩的前提）。"""
    from jobhater.services.wechat.recruit import analyze, extract_company

    c, src = extract_company("招聘丨宇树科技2027届校园招聘正式启动")
    assert c == "宇树科技" and src == "卡片标题模式"
    c2, _ = extract_company("实习 | 特斯拉2027届T-STAR实习生项目正式启动")
    assert c2 == "特斯拉"
    # 正常文本不受影响（品牌/后缀路径优先级不变）
    c3, s3 = extract_company("联想2027届秋招全面启动！网申开启")
    assert c3 == "联想" and s3 == "企业名词典"
    # 端到端：卡片标题消息应产出含公司与届别的命中
    hit = analyze("招聘 | 平安银行2027届暑期实习生招聘启动\nhttps://mp.weixin.qq.com/s/abc")
    assert hit is not None and hit.company == "平安银行" and hit.cohort == 2027


# ==================== 图片 OCR 通道（cache 明文图） ====================


def test_ocr_parse_jsonl(tmp_path):
    """worker JSONL 解析：正常行/坏行/BOM/空文本过滤。"""
    from jobhater.services.wechat.ocr import parse_ocr_jsonl

    f = tmp_path / "o.jsonl"
    f.write_bytes(
        b"\xef\xbb\xbf"  # BOM
        b'{"file": "a.jpg", "size": 100, "mtime": "2026-09-01", "text": "\xe9\xa1\xba\xe4\xb8\xb0\xe9\x9b\x86\xe5\x9b\xa22027\xe5\xb1\x8a\xe6\xa0\xa1\xe6\x8b\x9b\xe8\x81\x98"}\n'
        b'{"file": "b.jpg", "text": ""}\n'
        b"not-json\n"
    )
    hits = parse_ocr_jsonl(f)
    assert len(hits) == 1 and hits[0].mtime == "2026-09-01" and "2027" in hits[0].text


def test_ocr_ocr_text_into_recruit():
    """OCR 文本（字间空格已被 worker 压缩）应能进识别引擎产出命中。"""
    from jobhater.services.wechat.recruit import analyze

    text = "顺丰集团2027届全球校园招聘一切由你创造 网申通道已开启 扫码投递"
    hit = analyze(text)
    assert hit is not None and hit.company == "顺丰" and hit.cohort == 2027


# ==================== 公众号文章正文深挖 ====================


def test_article_extract_and_collect():
    """SSR 正文提取 + URL 收集（去重/置信排序/限量）。"""
    from jobhater.services.wechat.article_fetch import collect_urls, extract_article

    doc = (
        "<html><title>字节跳动2027届校招</title><body>"
        '<div id="js_content"><p>【招聘岗位】后端/前端/算法</p>'
        "<p>工作地点：北京 上海</p><br>薪资 25-45k</div></body></html>"
    )
    t, body = extract_article(doc)
    assert "字节跳动" in t and "后端/前端/算法" in body and "北京" in body

    hits = [
        {"apply_method": "链接 https://mp.weixin.qq.com/s/abc", "confidence": 0.9},
        {"apply_method": "链接 https://mp.weixin.qq.com/s/abc", "confidence": 0.5},  # 重复 URL
        {"apply_method": "链接 https://example.com/other", "confidence": 1.0},  # 非公众号
        {"apply_method": "链接 https://mp.weixin.qq.com/s/xyz", "confidence": 0.4},
    ]
    urls = collect_urls(hits, limit=1)
    assert urls == ["https://mp.weixin.qq.com/s/abc"], urls  # 高置信优先 + 限量
    assert len(collect_urls(hits)) == 2


def test_article_fetch_offline_no_urls(monkeypatch):
    """空 URL 列表不发起网络请求（fetch_articles 短路）。"""
    from jobhater.services.wechat import article_fetch as af

    called = []
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: called.append(1))
    assert af.fetch_articles([]) == {}
    assert called == []


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


# ==================== 密码式验证 / 结构定位 / DLL XOR（社区方法产品化） ====================


@needs_aes
def test_verify_as_password_roundtrip(tmp_path):
    """passphrase → PBKDF2 派生 enc_key 加密 → 密码式验证应还原 enc_key。"""
    import hashlib
    import secrets as _secrets

    from jobhater.services.wechat.decrypt import encrypt_database_for_test
    from jobhater.services.wechat.keyring_scan import KDF_ITER, verify_as_password

    plain = tmp_path / "p.db"
    build_wcdb_style_db(plain, 10)
    pw = _secrets.token_bytes(32)
    salt = _secrets.token_bytes(16)
    enc_key = hashlib.pbkdf2_hmac("sha512", pw, salt, KDF_ITER, 32)
    enc = tmp_path / "p.enc.db"
    encrypt_database_for_test(plain, enc, enc_key, salt)

    page1 = enc.read_bytes()[:4096]
    got = verify_as_password(pw, page1)
    assert got == enc_key, "正确 passphrase 应还原 enc_key"
    assert verify_as_password(_secrets.token_bytes(32), page1) is None


@needs_aes
def test_find_key_env_password_fallback(tmp_path, monkeypatch):
    """JOBHATER_WECHAT_KEY 传 passphrase 时应走密码式回退并返回派生 enc_key；
    传错误值不得命中（且不进入内存扫描——微信不在场也能用）。"""
    import hashlib
    import secrets as _secrets

    from jobhater.services.wechat.decrypt import encrypt_database_for_test
    from jobhater.services.wechat.keyring_scan import KDF_ITER, find_key

    plain = tmp_path / "e.db"
    build_wcdb_style_db(plain, 6)
    pw = _secrets.token_bytes(32)
    salt = _secrets.token_bytes(16)
    enc_key = hashlib.pbkdf2_hmac("sha512", pw, salt, KDF_ITER, 32)
    enc = tmp_path / "e.enc.db"
    encrypt_database_for_test(plain, enc, enc_key, salt)
    page1 = enc.read_bytes()[:4096]

    monkeypatch.setenv("JOBHATER_WECHAT_KEY", pw.hex())
    assert find_key(page1) == enc_key

    # 负例：错误 passphrase 不命中；打桩跳过内存扫描保证测试快且确定
    import jobhater.services.wechat.keyring_scan as ksm

    monkeypatch.setattr(ksm, "weixin_pids", lambda: [])
    monkeypatch.setenv("JOBHATER_WECHAT_KEY", _secrets.token_bytes(32).hex())
    assert find_key(page1) is None


# ==================== 实测反馈修复：升学噪声 / 品牌子串 / title 吞链接 ====================


def test_recruit_edu_admission_suppressed():
    """推免/保研招生通知应标记 kind=edu 且置信度被压到导入线（0.5）以下。"""
    from jobhater.services.wechat.recruit import analyze

    txt = (
        "【目标院校资讯】院校名称：中国科学院大学\n"
        "通知名称：中国科学院自动化研究所关于接收2027级推荐免试研究生的工作安排\n"
        "面向2027届本科毕业生，预推免报名即将开始\n"
        "通知类型：招生信息\n宣讲会时间另行通知\n联系方式：yzs@ucas.ac.cn\n截止：2026-09-13"
    )
    hit = analyze(txt)
    assert hit is not None, "升学通知仍应可见（雷达页展示）"
    assert hit.kind == "edu"
    assert hit.confidence < 0.5, f"升学通知置信度应 <0.5，得 {hit.confidence}"
    assert any("升学" in e for e in hit.evidence)


def test_recruit_normal_campus_not_edu():
    """真校招 JD 不得被升学规则误伤。"""
    from jobhater.services.wechat.recruit import analyze

    txt = (
        "联想2027届秋招全面启动！\n网申-测评-面试-offer\n"
        "一、公司简介：联想是全球智能设备领导厂商\n"
        "二、招募岗位：技术、产品与项目、设计、市场与销售、职能、供应链\n"
        "三、福利待遇：午餐加班餐补贴40/天\n投递：https://talent.lenovo.com.cn"
    )
    hit = analyze(txt)
    assert hit is not None and hit.kind == "campus"
    assert hit.confidence >= 0.5


def test_recruit_brand_longest_match():
    """「京东方」不得被子串「京东」抢先命中。"""
    from jobhater.services.wechat.recruit import extract_company

    company, src = extract_company("京东方科技集团(BOE)2027届秋招宣讲-南京站")
    assert company == "京东方", f"应最长匹配京东方，得 {company}"
    assert src == "企业名词典"
    company2, _ = extract_company("京东2027届校招启动，投递简历")
    assert company2 == "京东"


def test_recruit_title_rejects_url():
    """标签值是链接时不当作岗位名（回退词典或置空）。"""
    from jobhater.services.wechat.recruit import extract_title

    t, src = extract_title("职位：https://www.kdocs.cn/l/abc123 请填写")
    assert t is None or "http" not in t
    t2, _ = extract_title("职位：数据分析师。薪资：面议")
    assert t2 == "数据分析师"


def test_recruit_group_name_not_company():
    """群名（含「群」字）不得当公司名；真实企业发送者兜底仍可用。"""
    from jobhater.services.wechat.recruit import extract_company

    c1, _ = extract_company("急招数据分析师，坐标北京，薪资15-25K", sender_name="艺术学院2023级脆皮本科生群")
    assert c1 is None, f"群名不得成为公司，得 {c1}"
    c2, _ = extract_company("急招数据分析师，坐标北京", sender_name="长城证券招聘")
    assert c2 == "长城证券"
    c3, _ = extract_company("搜狐畅游2026春招+2027暑期实习同步开启")
    assert c3 == "搜狐畅游", f"应命中搜狐畅游，得 {c3}"


def test_looks_like_secret_filter():
    import secrets as _secrets

    from jobhater.services.wechat.keyring_scan import _looks_like_secret

    assert _looks_like_secret(_secrets.token_bytes(32))
    assert not _looks_like_secret(b"hello world this is plain text!!!")  # 可打印过多
    assert not _looks_like_secret(b"\x00" * 32)  # 全零
    assert not _looks_like_secret(b"ab" * 16)  # 字节种类过少


def test_dll_xor_keys_pattern(tmp_path):
    """合成含 4×mov rdx,imm64 模式的伪 DLL → 应提取出拼接的 32B。"""
    from jobhater.services.wechat.keyring_scan import dll_xor_keys

    key_parts = [b"AAAABBBB", b"CCCCDDDD", b"EEEEFFFF", b"GGGGHHHH"]
    blob = (
        b"\x90" * 64
        + b"\x48\xBA" + key_parts[0] + b"\x90\x90\x90"
        + b"\x48\xBA" + key_parts[1] + b"\x90\x90\x90\x90"
        + b"\x48\xBA" + key_parts[2] + b"\x90\x90\x90"
        + b"\x48\xBA" + key_parts[3] + b"\x90\x90\x90\x90"
        + b"\x48\x85\xC0"
        + b"\x00" * 128
    )
    fake_root = tmp_path / "4.9.9.9"
    fake_root.mkdir()
    (fake_root / "Weixin.dll").write_bytes(blob)
    keys = dll_xor_keys(str(tmp_path))
    assert b"".join(key_parts) in keys


def test_struct_candidates_layouts():
    """两种布局签名都能解引用出候选。"""
    from jobhater.services.wechat.keyring_scan import _struct_candidates

    secret = bytes(range(32))
    fake_heap = {0x50000: secret}

    def reader(addr: int) -> bytes:
        return fake_heap.get(addr, b"")

    # 布局A: [ptr][0×8][32][47]
    mem_a = (0x50000).to_bytes(8, "little") + b"\x00" * 8 + (32).to_bytes(8, "little") + (47).to_bytes(8, "little")
    assert secret in list(_struct_candidates(mem_a, reader))
    # 布局B: [ptr 末2字节零][size=32] 紧邻
    mem_b = (0x50000).to_bytes(8, "little") + (32).to_bytes(8, "little") + b"junk"
    assert secret in list(_struct_candidates(mem_b, reader))
    # 无签名 → 无候选
    assert list(_struct_candidates(b"\x00" * 64, reader)) == []
