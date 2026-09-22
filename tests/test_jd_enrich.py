"""B1 JD 补全引擎测试：fixture 回放（真实响应形状）+ URL 解析安全 + 163 防串岗 + 断点续跑。

fixture 来源：2026-09-23 五家官方接口真实响应（正文截短保结构），
捕获脚本见会话记录；形状漂移时重捕即可，测试语义不变。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobhater.db import apply_all, connect
from jobhater.services.enrich import EnrichmentEngine, provider_for_url
from jobhater.services.enrich.providers import (
    BaiduProvider,
    BytePortalProvider,
    MihoyoProvider,
    Netease163Provider,
    WechatArticleProvider,
    ZhiyeProvider,
)

FIX = Path(__file__).parent / "fixtures" / "enrich"


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def mock_transport(**overrides):
    """按 URL 分发 fixture 的离线回放传输层；overrides 允许单测覆写个别路由。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, responder in overrides.items():
            if pattern in url:
                return responder(request)
        if "/api/v1/job/posts/" in url:
            return httpx.Response(200, json=json.loads(_fixture("byteportal.json")))
        if "GetJobAdInfo" in url:
            return httpx.Response(200, json=json.loads(_fixture("zhiye.json")))
        if "ats-portal/v1/job/info" in url:
            return httpx.Response(200, json=json.loads(_fixture("mihoyo.json")))
        if "talent.baidu.com" in url:
            return httpx.Response(200, text=_fixture("baidu.html"))
        if "getJobList" in url:
            return httpx.Response(200, json=json.loads(_fixture("netease163.json")))
        return httpx.Response(404, text="no fixture route")

    return httpx.MockTransport(handler)


@pytest.fixture
def con(tmp_path):
    apply_all(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def _seed(con, rows):
    from jobhater.services.jobs import JobService

    JobService(con).ingest(
        [{"title": t, "company": comp, "url": u} for t, comp, u in rows],
        source_id="test",
    )


# ---------- URL 解析：可补全形态识别 + 域校验（防 SSRF/张冠李戴的第一道闸） ----------


class TestParse:
    def test_byteportal_detail_and_portal_type(self):
        p = BytePortalProvider()
        ref = p.parse("https://jobs.bytedance.com/campus/position/7628896431840807173/detail")
        assert ref and ref.job_key == "7628896431840807173" and ref.extra["portal_type"] == 3
        ref2 = p.parse("https://xiaomi.jobs.f.mioffice.cn/campus/position/7686764793517001010/detail")
        assert ref2 and ref2.extra["portal_type"] == 6

    def test_byteportal_list_page_rejected(self):
        # 列表页（position 后无数字段）不可补全——防止把列表当详情
        assert BytePortalProvider().parse(
            "https://jobs.bytedance.com/campus/position?keywords=AI&current=1"
        ) is None

    def test_evil_host_rejected(self):
        # 点号边界：仿冒域不得命中
        assert provider_for_url("https://evil-jobs.bytedance.com.attacker.io/position/123/detail") is None
        assert provider_for_url("https://zhiye.com.evil.io/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80") is None

    def test_zhiye_uuid_only(self):
        p = ZhiyeProvider()
        assert p.parse("https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")
        assert p.parse("https://mengniu.zhiye.com/job/detail?jobAdId=not-a-uuid") is None

    def test_mihoyo_hash_route(self):
        ref = MihoyoProvider().parse("https://jobs.mihoyo.com/#/campus/position/7399")
        assert ref and ref.job_key == "7399"
        assert MihoyoProvider().parse("https://jobs.mihoyo.com/#/campus/position") is None

    def test_baidu_forms(self):
        p = BaiduProvider()
        assert p.parse("https://talent.baidu.com/jobs/detail/GRADUATE/79e85217-8204-4a5f-9b05-03b2bb2597bd")
        assert p.parse("https://talent.baidu.com/jobs/detail?jobId=96a406ef-e29d-4745-aa93-0eee3aed3c69")
        assert p.parse("https://talent.baidu.com/jobs/detail?jobId=12345") is None

    def test_netease_takes_project_id(self):
        ref = Netease163Provider().parse("https://campus.163.com/app/job/position?id=103")
        assert ref and ref.job_key == "103"
        assert Netease163Provider().parse("https://campus.163.com/post.html") is None

    def test_wechat_host(self):
        assert WechatArticleProvider().parse("https://mp.weixin.qq.com/s/abc123")
        assert WechatArticleProvider().parse("https://mp.weixin.qq.com.evil.io/s/abc") is None


# ---------- 单 provider fetch 回放 ----------


class TestFetch:
    def test_byteportal_roundtrip(self):
        ref = BytePortalProvider().parse(
            "https://jobs.bytedance.com/campus/position/7628896431840807173/detail"
        )
        with httpx.Client(transport=mock_transport()) as c:
            res = BytePortalProvider().fetch(ref, "", c)
        assert res.ok and "任职要求" in res.jd_text and res.api_title

    def test_zhiye_roundtrip(self):
        ref = ZhiyeProvider().parse(
            "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80"
        )
        with httpx.Client(transport=mock_transport()) as c:
            res = ZhiyeProvider().fetch(ref, "", c)
        assert res.ok and res.jd_text

    def test_mihoyo_roundtrip(self):
        ref = MihoyoProvider().parse("https://jobs.mihoyo.com/#/campus/position/7399")
        with httpx.Client(transport=mock_transport()) as c:
            res = MihoyoProvider().fetch(ref, "", c)
        assert res.ok and res.jd_text and res.api_title

    def test_baidu_ssr_roundtrip(self):
        ref = BaiduProvider().parse(
            "https://talent.baidu.com/jobs/detail/GRADUATE/79e85217-8204-4a5f-9b05-03b2bb2597bd"
        )
        with httpx.Client(transport=mock_transport()) as c:
            res = BaiduProvider().fetch(ref, "", c)
        assert res.ok and len(res.jd_text) > 50

    def test_baidu_cross_host_redirect_rejected(self):
        ref = BaiduProvider().parse(
            "https://talent.baidu.com/jobs/detail/GRADUATE/79e85217-8204-4a5f-9b05-03b2bb2597bd"
        )
        t = mock_transport(**{"talent.baidu.com": lambda r: httpx.Response(
            302, headers={"Location": "https://evil.io/steal"}
        )})
        with httpx.Client(transport=t) as c:
            res = BaiduProvider().fetch(ref, "", c)
        assert not res.ok and "跨域重定向" in res.error

    def test_netease_title_match_and_guard(self):
        p = Netease163Provider()
        ref = p.parse("https://campus.163.com/app/job/position?id=103")
        with httpx.Client(transport=mock_transport()) as c:
            exact = p.fetch(ref, "营销推广专员（网易智邮）", c)
            miss = p.fetch(ref, "完全不相关的岗位名称", c)
            fuzzy = p.fetch(ref, "营销推广专员", c)  # 6 字符包含 + 比例过线 → fuzzy，可审计
            short = p.fetch(ref, "营销专员", c)      # 4 字符包含 → 拒绝（防串岗）
        assert exact.ok and exact.match_mode == "exact"
        assert not miss.ok and miss.error == "no_match"
        assert fuzzy.ok and fuzzy.match_mode == "fuzzy"
        assert not short.ok and short.error == "no_match"

    def test_netease_exact_beats_fuzzy_across_pages(self):
        """多页批次：精确命中优先，fuzzy 不抢跑（翻页全程节流由引擎层保证）。"""
        p = Netease163Provider()
        ref = p.parse("https://campus.163.com/app/job/position?id=103")
        calls = {"page": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("currentPage", "1"))
            calls["page"] = max(calls["page"], page)
            base = json.loads(_fixture("netease163.json"))
            if page == 1:
                base = json.loads(_fixture("netease163.json"))
                no_exact = [it for it in base["data"]["list"]
                            if it.get("positionName") != "营销推广专员（网易智邮）"]
                filler = [dict(no_exact[0])]  # 复制非精确项填充至满页
                base["data"]["list"] = (no_exact + filler * 100)[:99] + [dict(
                    positionName="营销推广专员", positionDescription="职责A", positionRequirement="",
                )]  # 满页 100 项：只有 fuzzy 候选，逼出翻页
            return httpx.Response(200, json=base)

        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            res = p.fetch(ref, "营销推广专员（网易智邮）", c)
        assert res.ok and res.match_mode == "exact" and calls["page"] == 2

    def test_netease_api_error_is_failed_not_no_match(self):
        """业务码非 200 是接口故障——账本语义不得与「标题不匹配」混淆。"""
        p = Netease163Provider()
        ref = p.parse("https://campus.163.com/app/job/position?id=103")
        t = mock_transport(**{"getJobList": lambda r: httpx.Response(
            200, json={"code": 500, "msg": "boom", "data": None}
        )})
        with httpx.Client(transport=t) as c:
            res = p.fetch(ref, "营销推广专员（网易智邮）", c)
        assert not res.ok and "code=500" in res.error and res.error != "no_match"

    def test_byteportal_api_error_shape(self):
        ref = BytePortalProvider().parse(
            "https://jobs.bytedance.com/campus/position/7628896431840807173/detail"
        )
        t = mock_transport(**{"/api/v1/job/posts/": lambda r: httpx.Response(
            200, json={"code": 403, "message": "forbidden"}
        )})
        with httpx.Client(transport=t) as c:
            res = BytePortalProvider().fetch(ref, "", c)
        assert not res.ok and "code=403" in res.error

    def test_baidu_404_sentinel(self):
        ref = BaiduProvider().parse(
            "https://talent.baidu.com/jobs/detail?jobId=96a406ef-e29d-4745-aa93-0eee3aed3c69"
        )
        t = mock_transport(**{"jobId": lambda r: httpx.Response(
            302, headers={"Location": "/jobs/404"}
        )})
        with httpx.Client(transport=t) as c:
            res = BaiduProvider().fetch(ref, "", c)
        assert not res.ok and "岗位已下线" in res.error

    def test_wechat_provider_via_monkeypatch(self, monkeypatch):
        from jobhater.services.wechat import article_fetch

        class _Art:
            text = "岗位：AI 产品实习生。要求：本科在读……"
            title = "某公众号文章"

        monkeypatch.setattr(article_fetch, "fetch_articles", lambda urls, **k: {urls[0]: _Art()})
        p = WechatArticleProvider()
        ref = p.parse("https://mp.weixin.qq.com/s/abc123")
        res = p.fetch(ref, "", None)
        assert res.ok and "AI 产品实习生" in res.jd_text


# ---------- 引擎：落库 / 断点续跑 / 重试上限 / 统计 ----------


class TestEngine:
    ROWS = [
        ("广告大模型研究员", "字节跳动", "https://jobs.bytedance.com/campus/position/7628896431840807173/detail"),
        ("公关策略专员", "小米", "https://xiaomi.jobs.f.mioffice.cn/campus/position/7686764793517001010/detail"),
        ("工程造价岗", "保利", "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80"),
        ("营销推广专员（网易智邮）", "网易", "https://campus.163.com/app/job/position?id=103"),
        ("无关岗位", "网易", "https://campus.163.com/app/job/position?id=103"),
        ("未知站岗位", "某公司", "https://jobs.example.com/position/1"),  # 无 provider → 不选入
    ]

    def test_run_writes_jd_and_log(self, con):
        _seed(con, self.ROWS)
        # 记录补全前 search_text——断言刷新逻辑真实生效（而非入库即非空的装饰性断言）
        before = con.execute(
            "SELECT search_text FROM job_postings WHERE title = '广告大模型研究员'"
        ).fetchone()["search_text"]
        out = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None).run(limit=50)
        assert out["total_ok"] == 4
        assert out["total_no_match"] == 1
        row = con.execute(
            "SELECT description, search_text FROM job_postings WHERE title = '广告大模型研究员'"
        ).fetchone()
        assert row["description"] and "任职要求" in row["description"]
        # JD 独有 token 确实进入了 FTS 索引文本（且相对入库态发生了变化）
        from jobhater import textproc as tp

        jd_tokens = tp.tokenize_for_fts(row["description"])
        assert row["search_text"] != before
        assert any(t and t in row["search_text"] for t in jd_tokens[:20])
        log = dict(con.execute(
            "SELECT provider, COUNT(*) n FROM jd_enrich_log WHERE status='ok' GROUP BY provider"
        ).fetchall())
        assert log.get("byteportal") == 2 and log.get("zhiye") == 1 and log.get("netease163") == 1
        # 163 审计字段：api_title/match_mode 落账，串岗可追溯
        audit = con.execute(
            "SELECT api_title, match_mode FROM jd_enrich_log WHERE provider='netease163' AND status='ok'"
        ).fetchone()
        assert audit["api_title"] and audit["match_mode"] == "exact"
        st = EnrichmentEngine(con).stats()
        assert st["with_jd"] == 4 and st["active_jobs"] == 6

    def test_resume_after_interrupt(self, con):
        """断点续跑：limit=2 跑一半（模拟杀进程），再跑处理剩余——状态全在库内。"""
        _seed(con, self.ROWS)
        eng = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None)
        first = eng.run(limit=2)
        assert first["total_ok"] + first["total_no_match"] == 2  # no_match 也占名额
        second = eng.run(limit=50)
        assert second["total_ok"] + first["total_ok"] == 4
        third = eng.run(limit=50)  # 全部 ok 后不再重复处理
        assert third["total_ok"] == 0

    def test_attempts_cap_stops_retry(self, con):
        _seed(con, [("工程造价岗", "保利", "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        eng = EnrichmentEngine(
            con,
            transport=mock_transport(**{"GetJobAdInfo": lambda r: httpx.Response(500, text="boom")}),
            sleep=lambda s: None,
        )
        for _ in range(3):
            assert eng.run(limit=10)["total_failed"] == 1
        assert eng.run(limit=10)["total_failed"] == 0  # attempts=3 后退出待办
        assert con.execute("SELECT attempts, status FROM jd_enrich_log").fetchone()["attempts"] == 3

    def test_enrich_one_guards(self, con):
        _seed(con, [
            ("未知站岗位", "某公司", "https://jobs.example.com/position/1"),
            ("无链接岗位", "某公司", ""),  # 空 url 入库即 canonical_url=NULL
        ])
        no_url = con.execute("SELECT id FROM job_postings WHERE title='无链接岗位'").fetchone()["id"]
        unk = con.execute("SELECT id FROM job_postings WHERE title='未知站岗位'").fetchone()["id"]
        eng = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None)
        assert eng.enrich_one(no_url)["error"] == "该岗位没有官网链接"
        assert "暂不支持" in eng.enrich_one(unk)["error"]

    def test_enrich_one_success_and_attempts_cap(self, con):
        _seed(con, [("广告大模型研究员", "字节跳动",
                     "https://jobs.bytedance.com/campus/position/7628896431840807173/detail")])
        jid = con.execute("SELECT id FROM job_postings WHERE title='广告大模型研究员'").fetchone()["id"]
        eng = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None)
        out = eng.enrich_one(jid)  # 成功路径
        assert out["ok"] and out["jd_chars"] > 0 and out["provider"] == "byteportal"
        assert "已有 JD 正文" in eng.enrich_one(jid)["error"]  # 幂等
        # 失败岗位：3 次后单条端点也拒绝重试（防刷官方接口）
        _seed(con, [("死岗", "保利", "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        dead = con.execute("SELECT id FROM job_postings WHERE title='死岗'").fetchone()["id"]
        failing = EnrichmentEngine(
            con,
            transport=mock_transport(**{"GetJobAdInfo": lambda r: httpx.Response(500, text="boom")}),
            sleep=lambda s: None,
        )
        for _ in range(3):
            assert not failing.enrich_one(dead)["ok"]
        r = failing.enrich_one(dead)
        assert not r["ok"] and "不再重试" in r["error"]

    def test_baidu_relative_redirect_resolved(self):
        """SSR 302 的 Location 常为相对路径——必须 urljoin 解析（真实翻车点）。"""
        ref = BaiduProvider().parse(
            "https://talent.baidu.com/jobs/detail?jobId=96a406ef-e29d-4745-aa93-0eee3aed3c69"
        )
        t = mock_transport(**{
            "jobId": lambda r: httpx.Response(302, headers={"Location": "/jobs/list"}),
            "talent.baidu.com/jobs/list": lambda r: httpx.Response(
                200, text=_fixture("baidu.html")
            ),
        })
        with httpx.Client(transport=t) as c:
            res = BaiduProvider().fetch(ref, "", c)
        assert res.ok and res.jd_text

    def test_unparseable_rows_do_not_eat_fetch_quota(self, con):
        """大量更新的不可解析行（列表页/短链）不得把可直读旧行挤出本次运行。"""
        rows = [("小米短链A", "小米", "https://xiaomi.jobs.f.mioffice.cn/s/dF19SuoRgkw"),
                ("小米短链B", "小米", "https://xiaomi.jobs.f.mioffice.cn/toptalent")]
        rows += [(f"字节岗{i}", "字节跳动",
                  f"https://jobs.bytedance.com/campus/position/76288964318408071{i}/detail") for i in range(3)]
        _seed(con, rows)
        out = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None).run(limit=2)
        assert out["providers"]["byteportal"]["ok"] == 2  # 名额全部给了可直读行
        assert out["providers"]["byteportal"]["failed"] == 2  # 短链只记账不占名额

    def test_run_unknown_provider_rejected(self, con):
        with pytest.raises(ValueError):
            EnrichmentEngine(con).run(limit=1, provider_id="nope")

    def test_provider_scoped_run_not_starved(self, con):
        """定向运行不得被其他域的新行饿死：host 预过滤必须推进到 LIMIT 之前。"""
        rows = [("字节岗", "字节跳动", "https://jobs.bytedance.com/campus/position/1111111111111111111/detail")]
        rows += [(f"zhiye岗{i}", "保利", "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80") for i in range(5)]
        _seed(con, rows)
        out = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None).run(
            limit=1, provider_id="byteportal"
        )
        assert out["providers"].get("byteportal", {}).get("ok") == 1


# ---------- API 端点（TestClient 全链：路由→引擎→库） ----------


def test_enrichment_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobhater import config
    from jobhater.api import create_app
    from jobhater.db import connect

    config.set_data_dir(tmp_path)
    # 离线回放：给引擎注入 MockTransport（API 层无法直传，包装构造器）
    orig_init = EnrichmentEngine.__init__

    def init_offline(self, con, **kw):
        kw.setdefault("transport", mock_transport())
        kw.setdefault("sleep", lambda s: None)
        orig_init(self, con, **kw)

    monkeypatch.setattr(EnrichmentEngine, "__init__", init_offline)
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/enrichment/stats").json()["active_jobs"] == 0
        con = connect(config.db_path())
        try:
            _seed(con, TestEngine.ROWS)
        finally:
            con.close()
        r = c.post("/api/enrichment/run", json={"limit": 50})
        assert r.status_code == 200 and r.json()["total_ok"] == 4, r.text
        assert "重新运行匹配" in r.json()["match_stale_hint"]
        st = c.get("/api/enrichment/stats").json()
        assert st["with_jd"] == 4
        job = [j for j in c.get("/api/jobs", params={"limit": 10}).json()["items"]
               if j["title"] == "广告大模型研究员"][0]
        # 已补全的岗位再单条补全 → 拒绝（幂等保护）
        r2 = c.post(f"/api/jobs/{job['id']}/enrich")
        assert r2.status_code == 200 and "已有 JD 正文" in r2.json()["error"]
        r3 = c.post("/api/enrichment/run", json={"provider": "nope"})
        assert r3.status_code == 422
    config.set_data_dir(None)
