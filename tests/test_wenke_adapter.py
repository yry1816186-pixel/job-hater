"""wenke 适配器测试：契约、解析（离线 fixture）、真实网络 smoke（可跳过）。"""
from __future__ import annotations

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.sources.wenke import (
    FETCHERS,
    WenkeAdapter,
    fetch_mihoyo,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_mihoyo_parses_pages_and_locations():
    """离线：用 wenke-radar 文档中的接口形状构造响应，验证分页终止与字段映射。"""
    pages = [
        {"success": True, "data": {"list": [
            {"id": 1001, "title": "游戏客户端开发", "addressDetailList": [
                {"addressDetail": "上海"}], "projectName": "原神", "jobNature": "全职"},
        ], "total": 1}},
    ]
    calls = []

    def get(url, **kw):
        calls.append(("GET", url))
        return _Resp({})

    def post(url, **kw):
        calls.append(("POST", url))
        return _Resp(pages[0])

    raw = fetch_mihoyo(get, post)
    assert len(raw) == 1
    assert raw[0]["source_job_id"] == "1001"
    assert raw[0]["company"] == "米哈游"
    assert raw[0]["city"] == "上海"
    assert raw[0]["url"].endswith("/campus/position/1001")
    assert "原神" in raw[0]["keywords"]
    # 单页满足 total → 只调用一次
    assert sum(1 for m, _ in calls if m == "POST") == 1


def test_adapter_contract_and_isolation():
    """契约：capabilities/rate_policy/produce 可用；未知公司名 → fail closed 不炸。"""
    a = WenkeAdapter(companies=["不存在的公司"])
    results = list(a.produce())
    assert results == []
    assert a.last_fetch_report["不存在的公司"].ok is False
    assert a.health_check().ok is False
    a2 = WenkeAdapter()
    assert a2.capabilities()["requires_login"] is False
    assert a2.rate_policy()["suggested_frequency"] == "daily"


def test_fetchers_registered():
    assert set(FETCHERS) >= {"米哈游", "百度", "网易"}


def test_live_smoke_single_company(tmp_path):
    """真实网络 smoke：米哈游官方接口获取 ≥1 条并全链入库。
    无外网/接口变更时如实 SKIP（不是 pass）——CI 与本地都诚实。"""
    import httpx

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            probe = client.get("https://jobs.mihoyo.com/", headers={
                "Referer": "https://jobs.mihoyo.com/"})
            if probe.status_code >= 400:
                pytest.skip(f"米哈游官网不可达（HTTP {probe.status_code}）")
    except httpx.HTTPError as e:
        pytest.skip(f"外网不可达：{e}")

    from jobhater import config

    config.set_data_dir(tmp_path)
    try:
        apply_all(tmp_path / "w.db")
        con = connect(tmp_path / "w.db")
        try:
            adapter = WenkeAdapter(companies=["米哈游"])
            raw = list(adapter.produce())
            report = adapter.last_fetch_report["米哈游"]
            if not report.ok:
                pytest.skip(f"官方接口当前不可用（fail closed）：{report.message}")
            assert raw, "接口成功但零条岗位"
            stats = JobService(con).ingest(raw, source_id="wenke")
            assert stats.added >= 1
            # 检索通路真实可用：任取一条已入库岗位的关键词能检回自己
            sample = raw[0]
            term = sample["title"][:2]
            hits = JobService(con).search(term, statuses=["active"])
            assert any(h.title == sample["title"] for h in hits), \
                f"以「{term}」检索应命中已入库的「{sample['title']}」"
            print(f"live smoke: 获取 {len(raw)} 条，入库 {stats.added}")
        finally:
            con.close()
    finally:
        config.set_data_dir(None)
