"""对抗性边缘测试（SECURITY.md v2 配套套件）。

定位：以攻击者视角固化既有服务的**诚实语义**与安全边界：
- 出境披露门（ack_egress → 428 + 披露文本）、本地模式默认不出境；
- API key 全链路不入库/不入响应/不落盘；
- 粘贴导入对超长/空白/注入样字符的健壮性（内容是数据不是指令）；
- 状态机诚实语义（applied_confirmed 用户确认门、同岗唯一约束的并发竞争）；
- Offer 数值边界、SQLite/FTS 注入面。

已知 bug 的处理方式：发现的真实缺陷**不在服务代码里修**（主 agent 决策），
而是在此用 ``xfail(strict=True)`` 固化**期望行为**——当前失败（套件仍绿，计 xfailed），
服务修复后会 XPASS 并报错，提醒把标记摘掉让测试转正。对应编号见 SECURITY.md
「已知残留风险」表。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app
from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService, LifecycleError
from jobhater.services.profile import ProfileService
from jobhater.services.sources import parse_jd_text

try:
    import jieba  # noqa: F401

    HAVE_JIEBA = True
except ImportError:
    HAVE_JIEBA = False

_FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
HAVE_FRONTEND_DIST = _FRONTEND_INDEX.exists()

FAKE_KEY = "sk-adversarial-not-a-secret"


@pytest.fixture()
def client(tmp_path):
    """干净数据目录 + TestClient。raise_server_exceptions=False：
    服务端 500 以真实状态码返回而非在测试里 re-raise（对抗测试要断言状态码）。"""
    config.set_data_dir(tmp_path)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    config.set_data_dir(None)


@pytest.fixture()
def remote_ai_client(client, monkeypatch):
    """已启用远程 provider 的客户端（假 keyring；远程调用打桩，绝不发真实网络请求）。
    base_url 指向不可达端口：万一桩失效，连接会立刻失败而非挂起。"""
    import jobhater.services.ai as ai_mod

    store: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def set_password(service: str, user: str, pw: str) -> None:
            store[(service, user)] = pw

        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            return store.get((service, user))

    monkeypatch.setattr(ai_mod, "HAVE_KEYRING", True)
    monkeypatch.setattr(ai_mod, "keyring", FakeKeyring)
    pid = client.post("/api/ai/providers", json={
        "adapter_kind": "openai_compatible", "display_name": "对抗测试源",
        "base_url": "http://127.0.0.1:1/v1", "model": "adversarial-model",
    }).json()["id"]
    client.post(f"/api/ai/providers/{pid}/key", json={"api_key": FAKE_KEY})
    assert client.post(f"/api/ai/providers/{pid}/enabled", json={"enabled": True}).json()["ok"]
    # ack 通过后的远程调用以桩替换：断言的是门与路由语义，不是 HTTP 客户端
    monkeypatch.setattr(
        ai_mod.OpenAICompatProvider, "complete",
        lambda self, req: f"stub-result:{req.task}",
    )
    return client


@pytest.fixture()
def svc_env(tmp_path):
    """服务层直连环境（无 HTTP），参照 tests/test_lifecycle.py 的 env fixture。"""
    db = tmp_path / "adv.db"
    apply_all(db)
    con = connect(db)
    pid = ProfileService(con).create_profile("对抗测试").id
    js = JobService(con)
    js.ingest([{
        "title": "对抗测试工程师", "company": "边界科技",
        "description": "安全边界验证岗位描述",
    }], source_id="adv")
    job = js.search("")[0]
    yield con, pid, job.id, db
    con.close()


def _seed_application(c: TestClient) -> str:
    """经 API 准备 画像+岗位+投递，返回 application_id。"""
    pid = c.post("/api/profiles", json={"display_name": "对抗测试", "headline": "红队"}).json()["id"]
    saved = c.post("/api/import/paste", json={
        "text": "职位：对抗测试工程师\n公司：边界科技\n薪资：20-30K", "save": True,
    }).json()["saved"]
    assert saved["added"] == 1, str(saved)
    jid = c.get("/api/jobs").json()["items"][0]["id"]
    return c.post("/api/applications", json={"job_id": jid, "profile_id": pid}).json()["id"]


# ---------- 出境披露门（ack_egress） ----------


def test_egress_gate_returns_428_with_disclosure(remote_ai_client):
    c = remote_ai_client
    r = c.post("/api/ai/complete", json={
        "task": "job_deep_review", "system": "s", "user": "岗位JD全文", "ack_egress": False,
    })
    assert r.status_code == 428
    detail = r.json()["detail"]
    assert detail["task"] == "job_deep_review"
    # 披露文本必须随 428 返回，调用方据此向用户展示后才能带 ack 重试
    assert "发送" in detail["disclosure"]
    assert "画像" in detail["disclosure"]
    # 错误路径绝不回显 key
    assert "sk-adversarial" not in r.text


def test_egress_ack_then_execute_via_stubbed_provider(remote_ai_client):
    c = remote_ai_client
    r = c.post("/api/ai/complete", json={
        "task": "resume_rewrite", "system": "s", "user": "u", "ack_egress": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is True
    assert body["result"] == "stub-result:resume_rewrite"


def test_local_mode_needs_no_ack_and_discloses_honestly(client):
    """本地模式不出境：不要求 ack（不是错误），披露如实为「不会发送任何数据」。"""
    c = client
    r = c.post("/api/ai/complete", json={
        "task": "cover_letter", "system": "s", "user": "u", "ack_egress": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is False and body["reason"] == "local_mode"
    disclosure = c.get("/api/ai/egress", params={"task": "cover_letter"}).json()["disclosure"]
    assert disclosure == "本地模式：不会发送任何数据。"


def test_unknown_task_404_on_complete_and_422_on_egress(client):
    c = client
    r = c.post("/api/ai/complete", json={
        "task": "no_such_task", "system": "s", "user": "u", "ack_egress": True,
    })
    assert r.status_code == 404
    assert "未知任务类型" in r.json()["detail"]
    r2 = c.get("/api/ai/egress", params={"task": "no_such_task"})
    assert r2.status_code == 422


# ---------- API key 保证链 ----------


def test_api_key_never_touches_responses_or_disk(remote_ai_client, tmp_path):
    c = remote_ai_client
    r = c.get("/api/ai/providers")
    assert r.status_code == 200
    providers = r.json()
    assert providers[0]["has_api_key"] is True
    # 列表响应只含布尔，绝无 key 本体
    assert FAKE_KEY not in r.text
    # 数据目录（SQLite 及全部文件）字节层面都不含 key：key 只在 OS keyring
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert FAKE_KEY.encode("utf-8") not in p.read_bytes(), f"key 泄漏到文件: {p.name}"


# ---------- 粘贴导入边界 ----------


def test_paste_huge_text_parsed_verbatim(client):
    c = client
    filler = "负责核心模块的职责描述，长期迭代。" * 20000
    text = f"职位：超长文本测试\n公司：长文公司\n{filler}"
    r = c.post("/api/import/paste", json={"text": text})
    assert r.status_code == 200
    draft = r.json()["draft"]
    assert len(draft["description"]) > 100_000
    assert filler[:50] in draft["description"]  # 正文保真，不截断不臆造


def test_paste_injection_samples_stored_verbatim_as_data(client):
    """注入样字符：脚本标签/SQL 片段一律原样进结构化字段与正文（是数据不是指令）。"""
    c = client
    evil = ("职位：前端工程师\n公司：<script>alert(1)</script>\n"
            "薪资：15-25K；'; DROP TABLE job_postings;--")
    r = c.post("/api/import/paste", json={"text": evil})
    assert r.status_code == 200
    draft = r.json()["draft"]
    assert draft["company"] == "<script>alert(1)</script>"
    assert "<script>" in draft["description"]
    assert "DROP TABLE" in draft["description"]
    # save=True 直接入库同样不炸，存储层只存文本
    r2 = c.post("/api/import/paste", json={"text": evil, "save": True})
    assert r2.status_code == 200
    assert r2.json()["saved"]["added"] == 1
    assert c.get("/api/jobs").status_code == 200  # 库仍健康


def test_paste_blank_rejected_honestly_at_service_layer():
    """服务层语义：空白输入是显式 ValueError（诚实拒绝），不是崩溃。"""
    with pytest.raises(ValueError, match="JD 文本为空"):
        parse_jd_text("   \n\t  ")


def test_paste_blank_api_returns_422(client):
    """纯空白粘贴 → 422 诚实拒绝（ValueError 在路由层映射，不 500）。"""
    r = client.post("/api/import/paste", json={"text": "   \n  "})
    assert r.status_code == 422


def test_paste_save_requires_title_and_company(client):
    c = client
    r = c.post("/api/import/paste", json={"text": "就是一段没有公司标签的正文", "save": True})
    assert r.status_code == 422
    assert "title" in r.json()["detail"] and "company" in r.json()["detail"]


# ---------- 状态机：非法转移 / 并发唯一 / 确认门 ----------


def test_transition_rejects_illegal_jump_and_applied_confirmed(svc_env):
    con, pid, jid, _db = svc_env
    apps = ApplicationService(con)
    app_id = apps.create(jid, pid)
    with pytest.raises(LifecycleError, match="非法状态转移"):
        apps.transition(app_id, "offer")  # discovered → offer：投后必须经确认门
    for s in ("saved", "preparing", "materials_ready", "ready_to_apply"):
        apps.transition(app_id, s)
    with pytest.raises(LifecycleError, match="确认已投递"):
        apps.transition(app_id, "applied_confirmed")  # 唯一入口是 confirm_applied


def test_concurrent_same_job_duplicate_application(svc_env):
    """并发同岗重复投递：UNIQUE(job_id, profile_id) 恰好放行一个，另一个诚实拒绝。"""
    con, pid, jid, db = svc_env
    con2 = connect(db)
    try:
        barrier = threading.Barrier(2)
        results: list[str] = []

        def _create(apps: ApplicationService) -> None:
            barrier.wait()
            try:
                results.append(apps.create(jid, pid))
            except LifecycleError as e:
                results.append(f"LifecycleError:{e}")

        t1 = threading.Thread(target=_create, args=(ApplicationService(con),))
        t2 = threading.Thread(target=_create, args=(ApplicationService(con2),))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        ok = [r for r in results if r.startswith("app_")]
        dup = [r for r in results if r.startswith("LifecycleError")]
        assert len(ok) == 1 and len(dup) == 1, str(results)
        assert "唯一" in dup[0]
    finally:
        con2.close()


def test_duplicate_application_via_api_422(client):
    c = client
    _seed_application(c)
    jid = c.get("/api/jobs").json()["items"][0]["id"]
    pid = c.get("/api/profiles").json()[0]["id"]
    r = c.post("/api/applications", json={"job_id": jid, "profile_id": pid})
    assert r.status_code == 422
    assert "唯一" in r.json()["detail"]


def test_illegal_transition_via_api_422(client):
    c = client
    app_id = _seed_application(c)
    r = c.post(f"/api/applications/{app_id}/transition", json={"status": "offer"})
    assert r.status_code == 422
    assert "非法状态转移" in r.json()["detail"]


def test_transition_invalid_status_returns_422_without_internals(client):
    """非法状态串 → 422，且不回显内部枚举异常文本。"""
    c = client
    app_id = _seed_application(c)
    r = c.post(f"/api/applications/{app_id}/transition", json={"status": "bogus_status"})
    assert r.status_code == 422
    assert "ApplicationStatus" not in r.text  # 内部实现细节不外泄


# ---------- Offer 数值边界 ----------


def test_offer_negative_salary_rejected(client):
    """负薪资/超范围月数在请求模型层被拒（ge=0/le=36）；非数字同样 422。"""
    c = client
    app_id = _seed_application(c)
    neg = c.post("/api/offers", json={"application_id": app_id, "base_salary_k": -5})
    assert neg.status_code == 422
    months = c.post("/api/offers", json={"application_id": app_id, "base_salary_k": 20, "salary_months": 99})
    assert months.status_code == 422
    bad = c.post("/api/offers", json={"application_id": app_id, "base_salary_k": "abc"})
    assert bad.status_code == 422


# ---------- SQLite/FTS 注入面 ----------


def test_search_hostile_terms_stay_parameterized(client):
    """搜索词含单引号/分号/SQL 关键字：参数化 + FTS 逐 token 引号包裹，正常返回空集；
    库不被破坏。半角双引号是已知缺陷（见下一条 xfail）。"""
    c = client
    seed = c.post("/api/import/paste", json={
        "text": "职位：注入面测试工程师\n公司：参数化公司\n职责：什么都不做", "save": True,
    })
    assert seed.status_code == 200 and seed.json()["saved"]["added"] == 1
    hostile = ["' OR '1'='1", "a;b;c", "x;y'z", "' ; DELETE FROM job_postings; --"]
    for q in hostile:
        r = c.get("/api/jobs", params={"q": q})
        assert r.status_code == 200, f"q={q!r} → {r.status_code} {r.text[:120]}"
        assert set(r.json()) == {"total", "items"}
    # 表未被破坏：不带过滤仍能列出种子岗位
    assert c.get("/api/jobs").json()["total"] == 1


@pytest.mark.skipif(not HAVE_JIEBA, reason="无 jieba 时走字符 bigram 路径，不触发该路径")
def test_search_with_double_quote_must_not_crash(client):
    """搜索词含半角双引号：token 内引号成对转义，不产生 fts5 语法错误。"""
    r = client.get("/api/jobs", params={"q": '他说"你好"'})
    assert r.status_code == 200


# ---------- SPA 静态回退路径穿越（已知bug#1） ----------


@pytest.mark.skipif(not HAVE_FRONTEND_DIST, reason="未构建 frontend/dist 时 SPA 回退路由不挂载")
def test_spa_fallback_must_not_escape_dist(client):
    """路径穿越防护：..%2f 编码变体不得读到 dist 之外的文件。"""
    r = client.get("/..%2f..%2fpyproject.toml")
    assert r.status_code == 404 or "build-system" not in r.text
