"""Phase B 逻辑/架构批次测试（LOG-004/005/007/008 + ARCH-003/004）。

- LOG-004：AI Provider 仅 5xx/429 重试；4xx 与响应形状错误直抛，不空耗重试；
- LOG-005：DISCOVERED 可直达 WITHDRAWN（放弃不需要前置仪式）；
- LOG-007：wenke.py 文件头具备 Copyright + MIT + 准确来源 URL（非省略占位符）；
- LOG-008/ARCH-004：「最近一次匹配」统一 computed_at DESC, id DESC 判定，
  检索排序子查询同序；get_job 单次查询；_now() 收敛至 services/storage.py；
- ARCH-003：JSON 列序列化统一走 storage 映射契约（row_to_dict/insert_model）。
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

# ==================== LOG-004：Provider 重试策略 ====================


def _provider_with_transport(provider, handler):
    """把 provider 的 httpx.Client 换成 MockTransport（官方测试模式）。"""
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_openai_compat_4xx_not_retried():
    from jobhater.services.ai import AIError, OpenAICompatProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    p = _provider_with_transport(
        OpenAICompatProvider(base_url="http://t.local", model="m", api_key="k", max_retries=3),
        handler,
    )
    with pytest.raises(AIError):
        p.complete(TaskRequest(task="resume_parse", system="s", user="u"))
    assert calls["n"] == 1, "4xx 不可重试：鉴权/参数错误重放只会空耗配额"


def test_openai_compat_429_retried_until_exhausted():
    from jobhater.services.ai import AIError, OpenAICompatProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    p = _provider_with_transport(
        OpenAICompatProvider(base_url="http://t.local", model="m", api_key="k", max_retries=3),
        handler,
    )
    with pytest.raises(AIError):
        p.complete(TaskRequest(task="resume_parse", system="s", user="u"))
    assert calls["n"] == 4, "429 属可重试：耗尽 max_retries+1 次后才放弃"


def test_openai_compat_5xx_retried_until_exhausted():
    from jobhater.services.ai import AIError, OpenAICompatProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="upstream down")

    p = _provider_with_transport(
        OpenAICompatProvider(base_url="http://t.local", model="m", api_key="k", max_retries=3),
        handler,
    )
    with pytest.raises(AIError):
        p.complete(TaskRequest(task="resume_parse", system="s", user="u"))
    assert calls["n"] == 4, "5xx 属可重试：耗尽 max_retries+1 次后才放弃"


def test_openai_compat_5xx_then_success_recovers():
    from jobhater.services.ai import OpenAICompatProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="transient")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    p = _provider_with_transport(
        OpenAICompatProvider(base_url="http://t.local", model="m", api_key="k", max_retries=2),
        handler,
    )
    assert p.complete(TaskRequest(task="resume_parse", system="s", user="u")) == "ok"
    assert calls["n"] == 2, "瞬时 5xx 重试一次即恢复"


def test_openai_compat_bad_response_shape_not_retried():
    from jobhater.services.ai import AIError, OpenAICompatProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"unexpected": "shape"})  # 缺 choices → KeyError

    p = _provider_with_transport(
        OpenAICompatProvider(base_url="http://t.local", model="m", api_key="k", max_retries=3),
        handler,
    )
    with pytest.raises(AIError):
        p.complete(TaskRequest(task="resume_parse", system="s", user="u"))
    assert calls["n"] == 1, "响应形状错误重放必得同一形状：不重试"


def test_anthropic_4xx_not_retried():
    from jobhater.services.ai import AIError, AnthropicProvider, TaskRequest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    p = _provider_with_transport(
        AnthropicProvider(base_url="http://t.local", model="m", api_key="k", max_retries=3),
        handler,
    )
    with pytest.raises(AIError):
        p.complete(TaskRequest(task="resume_parse", system="s", user="u"))
    assert calls["n"] == 1


# ==================== LOG-005：DISCOVERED → WITHDRAWN ====================


def test_discovered_can_withdraw_directly(env):
    con, pid, jid = env
    from jobhater.domain.enums import APPLICATION_TRANSITIONS, ApplicationStatus
    from jobhater.services.lifecycle import ApplicationService

    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    out = apps.transition(app, "withdrawn")
    assert out["status"] == "withdrawn"
    # 转移矩阵与实现同步（LOG-005：放弃不该有前置仪式）
    assert ApplicationStatus.WITHDRAWN in APPLICATION_TRANSITIONS[ApplicationStatus.DISCOVERED]


# ==================== LOG-007：wenke.py 头部署名合规 ====================


def test_wenke_header_carries_copyright_and_accurate_url():
    src = (
        Path(__file__).resolve().parent.parent / "jobhater" / "services" / "sources" / "wenke.py"
    ).read_text(encoding="utf-8")
    header = src[: src.index('"""', 3) + 3]  # 模块 docstring 即声明载体
    assert "Copyright" in header, "MIT 要求随 substantial portion 保留版权声明"
    assert "wenke-radar" in header and "MIT" in header
    assert "https://github.com/andylove-me/wenke-radar" in header, "来源 URL 必须是准确地址"
    assert ".../wenke-radar" not in header, "省略占位符 URL 不满足署名要求"


# ==================== LOG-008/ARCH-004：latest 口径统一 ====================


def _insert_match(con, *, mid, job_id, profile_id, computed_at, rank_score, eligible=1):
    from jobhater.services.matching import ENGINE_VERSION

    con.execute(
        """INSERT INTO match_results (id, job_id, profile_id, engine_version,
             computed_at, eligible, rank_score)
           VALUES (?,?,?,?,?,?,?)""",
        (mid, job_id, profile_id, ENGINE_VERSION, computed_at, eligible, rank_score),
    )


def test_latest_for_job_prefers_computed_at_over_max_id(env):
    """同一 (job, profile) 两行：computed_at 较新行的 id 字典序更小——
    旧 MAX(id) 口径会选错行，统一口径必须选 computed_at 最新者。"""
    con, pid, jid = env
    from jobhater.services.matching import MatchService

    _insert_match(con, mid="mt_zzz_old", job_id=jid, profile_id=pid,
                  computed_at="2026-01-01T00:00:00.000Z", rank_score=99.0)
    _insert_match(con, mid="mt_aaa_new", job_id=jid, profile_id=pid,
                  computed_at="2026-06-01T00:00:00.000Z", rank_score=1.0)
    m = MatchService(con).latest_for_job(jid, pid)
    # MatchOutcome 不携带行 id：以新行独有的 rank_score 判定取到了哪行
    assert m is not None and m.rank_score == 1.0


def test_ranked_search_join_uses_same_latest_row(two_jobs_env):
    """检索排序的关联子查询与 latest_for_job 同序（ARCH-004）：
    两岗位各两行匹配——按「最新行」口径 B(99) 应排在 A(1) 前；
    旧 MAX(id) 口径会取旧行得 A(99) > B(1)，顺序相反。"""
    con, pid, jobs = two_jobs_env
    from jobhater.services.jobs import JobService

    a, b = jobs["岗位A"], jobs["岗位B"]
    # 岗位A：旧行 rank 99，最新行 rank 1；岗位B：旧行 rank 1，最新行 rank 99
    _insert_match(con, mid="mt_zzz_a", job_id=a.id, profile_id=pid,
                  computed_at="2026-01-01T00:00:00.000Z", rank_score=99.0)
    _insert_match(con, mid="mt_aaa_a", job_id=a.id, profile_id=pid,
                  computed_at="2026-06-01T00:00:00.000Z", rank_score=1.0)
    _insert_match(con, mid="mt_yyy_b", job_id=b.id, profile_id=pid,
                  computed_at="2026-01-01T00:00:00.000Z", rank_score=1.0)
    _insert_match(con, mid="mt_bbb_b", job_id=b.id, profile_id=pid,
                  computed_at="2026-06-01T00:00:00.000Z", rank_score=99.0)
    out = JobService(con).search("", ranked_profile_id=pid, limit=10)
    assert [j.title for j in out] == ["岗位B", "岗位A"]


def test_get_job_queries_latest_match_once(tmp_path, monkeypatch):
    """LOG-008：get_job 带画像查询时 latest_for_job 只调一次（不再判空+取值双查）。"""
    from fastapi.testclient import TestClient

    from jobhater import config
    from jobhater.api.app import create_app
    from jobhater.services.matching import MatchService

    config.set_data_dir(tmp_path)
    calls = {"n": 0}
    orig = MatchService.latest_for_job

    def _counting(self, job_id, profile_id):
        calls["n"] += 1
        return orig(self, job_id, profile_id)

    monkeypatch.setattr(MatchService, "latest_for_job", _counting)
    try:
        with TestClient(create_app()) as client:
            pid = client.post("/api/profiles", json={"display_name": "计数"}).json()["id"]
            assert client.post(
                "/api/jobs/import", json={"jobs": [{"title": "t", "company": "c"}]}
            ).json()["added"] == 1
            job_id = client.get("/api/jobs").json()["items"][0]["id"]
            # 有匹配行时才会执行取值分支：旧实现「判空+取值」双查在此暴露
            from jobhater.db import connect

            con = connect()
            try:
                _insert_match(con, mid="mt_once", job_id=job_id, profile_id=pid,
                              computed_at="2026-06-01T00:00:00.000Z", rank_score=42.0)
                con.commit()
            finally:
                con.close()
            r = client.get(f"/api/jobs/{job_id}", params={"profile_id": pid})
        assert r.status_code == 200, r.text
        assert r.json()["match"]["rank_score"] == 42.0  # 命中刚插入的行
        assert calls["n"] == 1, "判空+取值双查是浪费：单次查询后复用结果"
    finally:
        config.set_data_dir(None)


# ==================== ARCH-003：JSON 列走 storage 映射契约 ====================


def test_application_list_tolerates_corrupt_tags(env):
    """tags 是标注非事实：tags_json 损坏不炸列表、如实置空（语义保留）。"""
    con, pid, jid = env
    from jobhater.services.lifecycle import ApplicationService

    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    con.execute("UPDATE applications SET tags_json='{corrupt' WHERE id=?", (app,))
    assert apps.list(pid)[0]["tags"] == []
    assert apps.get(app)["tags"] == []


def test_offer_roundtrip_via_storage_mapping(env):
    """add_offer 经 insert_model 映射契约写入；list/compare 经 row_to_dict 读回。"""
    con, pid, jid = env
    from jobhater.services.lifecycle import ApplicationService

    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    offer = apps.add_offer(app, base_salary_k=20.0, salary_months=15,
                           benefits=["餐补"], custom_dimensions={"通勤": "地铁"})
    listed = apps.list_offers(pid)
    assert listed[0]["id"] == offer.id
    assert listed[0]["benefits"] == ["餐补"]
    assert listed[0]["custom_dimensions"] == {"通勤": "地铁"}
    cmp = apps.compare_offers([offer.id])
    assert cmp[0]["annual_base_k"] == 300.0
    assert cmp[0]["benefits"] == ["餐补"]


def test_offer_defaults_survive_mapping_insert(env):
    """可选字段缺席时 DB DEFAULT 生效（NOT NULL DEFAULT 列不可显式 NULL 击穿）。"""
    con, pid, jid = env
    from jobhater.services.lifecycle import ApplicationService

    apps = ApplicationService(con)
    app = apps.create(jid, pid)
    offer = apps.add_offer(app, base_salary_k=15.0)
    row = apps.list_offers(pid)[0]
    assert row["id"] == offer.id
    assert row["benefits"] == [] and row["custom_dimensions"] == {}


# ==================== ARCH-004：_now() 收敛 storage 单一定义 ====================


def test_now_iso_single_source_and_format():
    import jobhater.services.storage as storage_mod

    services_dir = Path(__file__).resolve().parent.parent / "jobhater" / "services"
    offenders = [
        p.name for p in services_dir.glob("*.py")
        if "def _now(" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"_now() 复制体残留：{offenders}"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", storage_mod.now_iso()), (
        "统一时间戳格式：秒 + 毫秒 + Z（曾发现 matching.py 复制体漂移为缺秒格式）"
    )


# ==================== fixtures ====================


@pytest.fixture()
def env(tmp_path):
    from jobhater.db import apply_all, connect
    from jobhater.services.jobs import JobService
    from jobhater.services.profile import ProfileService

    apply_all(tmp_path / "arch.db")
    con = connect(tmp_path / "arch.db")
    ps, js = ProfileService(con), JobService(con)
    profile = ps.create_profile("测试")
    js.ingest([{
        "title": "后端开发工程师", "company": "云图信息", "city": "杭州",
        "salary": "20-35K", "description": "Java 微服务开发",
    }], source_id="t")
    job = js.search("")[0]
    yield con, profile.id, job.id
    con.close()


@pytest.fixture()
def two_jobs_env(tmp_path):
    from jobhater.db import apply_all, connect
    from jobhater.services.jobs import JobService
    from jobhater.services.profile import ProfileService

    apply_all(tmp_path / "arch2.db")
    con = connect(tmp_path / "arch2.db")
    ps, js = ProfileService(con), JobService(con)
    profile = ps.create_profile("排序")
    js.ingest([
        {"title": "岗位A", "company": "甲公司", "description": "a"},
        {"title": "岗位B", "company": "乙公司", "description": "b"},
    ], source_id="t")
    jobs = {j.title: j for j in js.search("")}
    yield con, profile.id, jobs
    con.close()
