"""v3.5 发现层测试：QQ 解密能力层往返 + 收件箱表格映射/双解析/服务流。

QQ 侧只测**可离线验证的能力**（解密造库往返、环境探测目录逻辑）——
消息表结构解析在真实密钥可用前不存在，不写臆测代码（反剧场立场）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobhater import config
from jobhater.db import apply_all, connect

try:
    from Crypto.Cipher import AES  # noqa: F401

    _HAS_AES = True
except ImportError:
    _HAS_AES = False

needs_aes = pytest.mark.skipif(not _HAS_AES, reason="pycryptodome 未安装")


# ---------- QQ 解密往返 ----------


@needs_aes
def test_qq_encrypt_decrypt_roundtrip(tmp_path):
    import secrets

    from jobhater.services.qq import decrypt as dq

    plain = tmp_path / "p.db"
    con = sqlite3.connect(plain)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO t(name) VALUES (?)", [("岗位" + str(i),) for i in range(50)])
    con.commit()
    con.close()

    ek = secrets.token_bytes(32)
    enc = tmp_path / "p_enc.db"
    dq.encrypt_database_for_test(plain, enc, ek)

    # magic 头 + 解密往返 + 可打开且数据完整
    assert dq.looks_like_qq_db(enc)
    anchor = dq.read_anchor(enc)
    assert dq.check_key(ek, anchor)
    wrong = dq.derive_key(b"not-the-passphrase", enc)
    assert wrong is None  # 错误 passphrase 全组合不命中
    out = tmp_path / "p_out.db"
    assert dq.decrypt_database(enc, out, ek)
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    names = [r[0] for r in con.execute("SELECT name FROM t LIMIT 3")]
    con.close()
    assert n == 50 and names[0] == "岗位0"


def test_qq_detect_finds_env_dir(tmp_path, monkeypatch):
    import jobhater.services.qq.detect as dt  # 模块本体（__init__ 里的 detect 是函数）

    tf = tmp_path / "Tencent Files"
    uin = tf / "12345678"
    nt_db = uin / "nt_qq" / "nt_db"
    nt_db.mkdir(parents=True)
    (nt_db / "nt_msg.db").write_bytes(b"x" * 100)
    # 环境隔离：只探测试目录（真机 Documents/小米云盘的账号不得混入断言）
    monkeypatch.setattr(dt, "_candidate_roots", lambda: [tf])
    monkeypatch.setattr(dt, "_qq_pids", lambda: [])
    env = dt.detect()
    assert env.platform_ok or not env.accounts  # 非 Windows 如实无账号
    if env.platform_ok:
        assert len(env.accounts) == 1 and env.accounts[0].uin == "12345678"
        assert env.accounts[0].msg_dbs == ["nt_msg.db"]
        # QQ 未跑：blockers 分层如实（OCR 通道不受影响的口径）
        assert any("QQ 未运行" in b for b in env.blockers)
        d = env.to_dict()
        assert d["accounts"][0]["uin"] == "12345678"


# ---------- 收件箱：表格列头映射 ----------


def _write_csv(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8-sig")


def test_inbox_csv_header_mapping(tmp_path, monkeypatch):
    from jobhater.services.inbox import InboxService

    config.set_data_dir(tmp_path)
    try:
        inbox = InboxService.inbox_dir()
        _write_csv(inbox / "内推表.csv",
                   "序号,公司名称,岗位,城市,薪资范围,投递链接,截止日期\n"
                   "1,字节跳动,产品经理,北京,25-40K,https://jobs.bytedance.com/campus/position/1/detail,2026-12-31\n"
                   "2,美团,视觉设计师,上海,20-30K,https://zhaopin.meituan.com/web/position/detail?jobUnionId=9,2026-12-15\n"
                   "3,,无公司行,杭州,,,,\n")
        svc = InboxService()
        out = svc.scan()
        assert out["files"] == 1
        items = svc.results()["items"]
        assert len(items) == 3  # 三行都进暂存；无公司行 company 空（待人工补）
        assert items[0]["company"] == "字节跳动" and items[0]["title"] == "产品经理"
        assert items[0]["city"] == "北京" and "25-40K" in items[0]["salary"]
        assert items[0]["url"].startswith("https://jobs.bytedance.com")
        assert items[0]["deadline"] == "2026-12-31"
        assert items[2].get("company") == "" and out["needs_review"] == 1
        # 原件已移 processed/
        assert not (inbox / "内推表.csv").exists()
        assert (inbox / "processed" / "内推表.csv").exists()
    finally:
        config.set_data_dir(None)


def test_inbox_csv_gb18030_and_header_offset(tmp_path):
    from jobhater.services.inbox import InboxService

    config.set_data_dir(tmp_path)
    try:
        inbox = InboxService.inbox_dir()
        body = ("某学校2027届岗位汇总\n"
                "备注：老师转发\n"
                "单位,招聘职位,工作地点,待遇,网址\n"
                "国家电网,电气工程师,南京,面议,https://example.com/1\n")
        (inbox / "g.csv").write_bytes(body.encode("gb18030"))
        items = InboxService().scan() and InboxService().results()["items"]
        # 表头在第 3 行（前两行是标题/备注）——自动定位后仍应映射成功
        assert items and items[0]["company"] == "国家电网"
        assert items[0]["title"] == "电气工程师"
    finally:
        config.set_data_dir(None)


def test_inbox_xlsx_mapping_or_skip(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    from jobhater.services.inbox import InboxService

    config.set_data_dir(tmp_path)
    try:
        inbox = InboxService.inbox_dir()
        wb = Workbook()
        ws = wb.active
        ws.append(["公司", "岗位名称", "城市", "月薪"])
        ws.append(["阿里巴巴", "用户体验设计师", "杭州", "25K"])
        wb.save(inbox / "t.xlsx")
        items = InboxService().scan() and InboxService().results()["items"]
        assert items and items[0]["company"] == "阿里巴巴"
        assert items[0]["salary"] == "25K"
    finally:
        config.set_data_dir(None)


# ---------- 收件箱：文本双解析 + 图片 OCR 通道（mock） ----------


def test_inbox_text_dual_parse(tmp_path):
    from jobhater.services.inbox import InboxService

    config.set_data_dir(tmp_path)
    try:
        inbox = InboxService.inbox_dir()
        (inbox / "jd.txt").write_text(
            "【招聘】腾讯 2027届校招 产品经理\n"
            "工作地点：深圳\n"
            "薪资：20-35K·16薪\n"
            "职责：负责腾讯会议产品规划。\n"
            "要求：本科及以上。\n"
            "投递链接 https://join.qq.com/post.html?x=1\n"
            "截止时间：2026-12-31",
            encoding="utf-8",
        )
        svc = InboxService()
        svc.scan()
        items = svc.results()["items"]
        assert len(items) == 1
        it = items[0]
        assert it["company"] == "腾讯" and "产品经理" in it["title"]
        assert it["city"] == "深圳" and "20-35K" in it["salary"]
        assert it.get("url", "").startswith("https://join.qq.com")
        assert it.get("deadline") == "2026-12-31"
        assert it.get("confidence", 0) > 0.35
    finally:
        config.set_data_dir(None)


def test_inbox_image_ocr_channel(tmp_path, monkeypatch):
    from jobhater.services.inbox import InboxService
    from jobhater.services.wechat import ocr as wx_ocr

    config.set_data_dir(tmp_path)
    try:
        inbox = InboxService.inbox_dir()
        img = inbox / "poster.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20000)  # 假图：只测通道接线

        def fake_run_ocr(d, timeout_s=600):
            assert Path(d) != inbox  # 必须走临时目录（processed/ 不重扫）
            text = ("【实习】抖音集团 数据分析实习生 base 上海 300-400元/天 "
                    "截止2026-12-20 链接 https://example.com/j/9")
            return [wx_ocr.OcrHit(file=str(Path(d) / "poster.png"), mtime="x", text=text)]

        monkeypatch.setattr("jobhater.services.wechat.ocr.run_ocr", fake_run_ocr)
        svc = InboxService()
        svc.scan()
        items = svc.results()["items"]
        assert len(items) == 1
        # 标题提取对海报体例不保证（如实落「未识别标题」兜底），断言落在确定的字段上
        assert items[0].get("source_file") == "poster.png"
        assert items[0].get("description") and "数据分析" in items[0]["description"]
    finally:
        config.set_data_dir(None)


# ---------- 收件箱：入库（勾选式，缺公司拒收） ----------


def test_inbox_import_selected(tmp_path):
    from jobhater.services.inbox import InboxService

    config.set_data_dir(tmp_path)
    try:
        apply_all(config.db_path())
        con = connect(config.db_path())
        try:
            res_path = InboxService.result_path()
            res_path.parent.mkdir(parents=True, exist_ok=True)
            res_path.write_text(json.dumps({
                "generated_at": 1,
                "files": [],
                "items": [
                    {"title": "岗位A", "company": "公司A", "city": "北京"},
                    {"title": "岗位B", "company": "", "city": "上海"},  # 缺公司→拒收
                    {"title": "岗位C", "company": "公司C", "city": "杭州"},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            svc = InboxService(con=con)
            out = svc.import_items([0, 1, 2])
            assert out["imported"] == 2 and out["requested"] == 3
            n = con.execute("SELECT COUNT(*) FROM job_postings WHERE source_id='inbox'").fetchone()[0]
            assert n == 2
        finally:
            con.close()
    finally:
        config.set_data_dir(None)


# ---------- API ----------


def test_inbox_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobhater.api import create_app

    config.set_data_dir(tmp_path)
    try:
        # 注入暂存（API 测试惯用手法：与微信雷达测试同构）
        from jobhater.services.inbox import InboxService

        res_path = InboxService.result_path()
        res_path.parent.mkdir(parents=True, exist_ok=True)
        res_path.write_text(json.dumps({
            "generated_at": 1, "files": [{"file": "a.csv", "kind": "table", "items": 1}],
            "items": [{"title": "端点岗", "company": "端点公司", "city": "南京"}],
        }, ensure_ascii=False), encoding="utf-8")

        app = create_app()
        with TestClient(app) as c:
            st = c.get("/api/inbox/status").json()
            assert st["phase"] in ("idle", "done")
            r = c.get("/api/inbox/results").json()
            assert len(r["items"]) == 1
            r2 = c.post("/api/inbox/scan")
            assert r2.status_code == 200 and r2.json()["running"] in (True, False)
            imp = c.post("/api/inbox/import", json={"indices": [0]}).json()
            assert imp["imported"] == 1
            jobs = c.get("/api/jobs", params={"limit": 10}).json()["items"]
            assert any(j["title"] == "端点岗" for j in jobs)
    finally:
        config.set_data_dir(None)
