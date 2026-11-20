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
from jobhater.services.enrich.providers_ats import (
    AshbyProvider,
    GreenhouseProvider,
    LeverProvider,
    WorkdayProvider,
)
from jobhater.services.enrich.providers_cn import (
    AlibabaProvider,
    MeituanProvider,
    TencentProvider,
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
        # ---- B2 新 provider（career-ops 移植）----
        if "/position/detail" in url:  # 阿里详情（注意先于美团 getJobDetail 判定无重叠）
            return httpx.Response(200, json=json.loads(_fixture("alibaba.json")))
        if "/position/search" in url:  # 阿里搜索反查
            return httpx.Response(200, json=json.loads(_fixture("alibaba_search.json")))
        if "post/ByPostId" in url:  # 腾讯单岗
            return httpx.Response(200, json=json.loads(_fixture("tencent.json")))
        if "post/Query" in url:  # 腾讯搜索反查
            return httpx.Response(200, json=json.loads(_fixture("tencent_query.json")))
        if "getJobDetail" in url:  # 美团单岗
            return httpx.Response(200, json=json.loads(_fixture("meituan.json")))
        if "official/job/getJobList" in url:  # 美团列表（必须先于网易的泛 getJobList 路由）
            return httpx.Response(200, json=json.loads(_fixture("meituan_list.json")))
        if "boards-api.greenhouse.io" in url:
            return httpx.Response(200, json=json.loads(_fixture("greenhouse.json")))
        if "lever.co/v0/postings" in url:
            return httpx.Response(200, json=json.loads(_fixture("lever.json")))
        if "posting-api/job-board" in url:
            return httpx.Response(200, json=json.loads(_fixture("ashby.json")))
        if "wday/cxs" in url:
            return httpx.Response(200, json=json.loads(_fixture("workday.json")))
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

    # ---- B2 新 provider：URL 形态识别（career-ops 移植） ----

    def test_alibaba_detail_and_search_modes(self):
        p = AlibabaProvider()
        # LIVE 发现（2026-11-20）：存量 989 条在 campus 子域，channel 必须分流
        # （campus ID 走 group_official_site 会拿到 content=null 假死）
        ref = p.parse("https://talent.alibaba.com/off-campus/position-detail?positionId=12345678")
        assert ref and ref.job_key == "12345678"
        assert ref.origin == "https://talent.alibaba.com"
        assert ref.extra["channel"] == "group_official_site"
        cref = p.parse("https://campus-talent.alibaba.com/campus/position/detail?positionId=199907740040")
        assert cref and cref.origin == "https://campus-talent.alibaba.com"
        assert cref.extra["channel"] == "campus_official_site"
        # 列表/落地页 → 搜索反查模式（163 模式推广：URL 无 ID 时标题驱动）
        sref = p.parse("https://talent.alibaba.com/off-campus/position-list?keywords=%E8%AE%BE%E8%AE%A1")
        assert sref and sref.extra.get("mode") == "search"
        # 详情形 URL 但 ID 非数字（脏数据）→ 也走搜索反查（标题兜底比丢弃更可用）
        dirty = p.parse("https://talent.alibaba.com/position-detail?positionId=abc")
        assert dirty and dirty.extra.get("mode") == "search"
        assert p.parse("https://talent.alibaba.com.evil.io/off-campus/position-detail?positionId=1") is None

    def test_tencent_detail_and_search_modes(self):
        p = TencentProvider()
        ref = p.parse("https://careers.tencent.com/jobdesc.html?postId=1234567890")
        assert ref and ref.job_key == "1234567890"
        assert p.parse("https://careers.tencent.com/careers.html").extra.get("mode") == "search"
        # join.qq.com = 腾讯投递站（真实库 634 条 resume.html?k=令牌 存量）→ 搜索反查
        join = p.parse("https://join.qq.com/resume.html?k=ykYyrMbYqDCz")
        assert join and join.extra.get("mode") == "search" and join.origin.endswith("careers.tencent.com")
        assert p.parse("https://join.qq.com.evil.io/resume.html?k=x") is None
        assert p.parse("https://evil.io/jobdesc.html?postId=1") is None

    def test_meituan_detail_and_search_modes(self):
        p = MeituanProvider()
        ref = p.parse("https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234")
        assert ref and ref.job_key == "1000234"
        assert p.parse("https://zhaopin.meituan.com/web/position/list").extra.get("mode") == "search"

    def test_greenhouse_forms(self):
        p = GreenhouseProvider()
        ref = p.parse("https://job-boards.greenhouse.io/stripe/jobs/1234567")
        assert ref and ref.job_key == "1234567" and ref.extra["board"] == "stripe"
        assert GreenhouseProvider().parse("https://boards.eu.greenhouse.io/acme/jobs/42")
        assert p.parse("https://greenhouse.io.evil.io/stripe/jobs/1") is None
        assert p.parse("https://job-boards.greenhouse.io/stripe/jobs/abc") is None

    def test_lever_forms(self):
        p = LeverProvider()
        ref = p.parse("https://jobs.lever.co/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890")
        assert ref and ref.extra["slug"] == "acme" and ref.origin.endswith("api.lever.co")
        assert LeverProvider().parse("https://jobs.eu.lever.co/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890")
        assert p.parse("https://jobs.lever.co/acme/1f2e3d4c/apply") is None  # 尾段多余路径不认
        assert p.parse("https://lever.co.evil.io/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890") is None

    def test_ashby_forms(self):
        p = AshbyProvider()
        ref = p.parse("https://jobs.ashbyhq.com/linear/8b9a2c1d-3e4f-5a6b-7c8d-9e0f1a2b3c4d")
        assert ref and ref.extra["org"] == "linear"
        assert p.parse("https://www.ashbyhq.com/linear/8b9a2c1d") is None  # host 必须精确到 jobs 子域
        assert p.parse("https://jobs.ashbyhq.com/linear/../evil/1") is None  # 路径穿越拒绝

    def test_workday_forms(self):
        p = WorkdayProvider()
        # careers 形态（含 locale 前缀）
        ref = p.parse("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Program-Manager_R12345")
        assert ref and ref.extra["tenant"] == "nvidia" and ref.extra["site"] == "NVIDIAExternalCareerSite"
        assert ref.job_key == "/job/US-CA-Santa-Clara/Program-Manager_R12345"
        # CXS API 形态（API URL 被存成 canonical 的罕见情形）
        cxs = p.parse("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Program-Manager_R12345")
        assert cxs and cxs.job_key.endswith("Program-Manager_R12345")
        # myworkdaysite 形态
        site = p.parse("https://acme.myworkdaysite.com/acme/recruiting/acme/ACME/job/Shanghai/Designer_R99")
        assert site and site.origin == "https://acme.myworkdaysite.com"
        assert p.parse("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/../../../evil") is None
        assert p.parse("https://nvidia.wd5.myworkdayjobs.com/other/path") is None


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

    # ---- B2 新 provider fetch 回放（fixture 按 career-ops 文档化响应形态构造） ----

    def test_alibaba_detail_and_dead(self):
        p = AlibabaProvider()
        ref = p.parse("https://talent.alibaba.com/off-campus/position-detail?positionId=12345678")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and "淘天集团" in res.jd_text and res.api_title == "用户体验设计师-淘天集团"
        # success=true + content=null = 官方确定性下线（career-ops 实测语义）
        t = mock_transport(**{"/position/detail": lambda r: httpx.Response(
            200, json={"success": True, "content": None}
        )})
        with httpx.Client(transport=t) as c:
            dead = p.fetch(ref, "", c)
        assert not dead.ok and "岗位已下线" in dead.error

    def test_alibaba_search_fallback_exact_fuzzy_guard(self):
        """列表 URL 搜索反查：exact 命中；fuzzy 过闸；短标题拒绝（163 同构防线）。"""
        p = AlibabaProvider()
        ref = p.parse("https://talent.alibaba.com/off-campus/position-list?keywords=design")
        with httpx.Client(transport=mock_transport()) as c:
            exact = p.fetch(ref, "用户体验设计师-淘天集团", c)
            fuzzy = p.fetch(ref, "用户体验设计师", c)
            short = p.fetch(ref, "设计师", c)
            miss = p.fetch(ref, "完全不相关岗位", c)
        assert exact.ok and exact.match_mode == "exact" and exact.api_title
        assert fuzzy.ok and fuzzy.match_mode == "fuzzy"
        assert not short.ok and short.error == "no_match"
        assert not miss.ok and miss.error == "no_match"
        # LIVE 发现（2026-11-20）：campus 域空结果 datas=null + 分页键在场 →
        # 合法空结果（no_match），不是结构故障（failed）
        t_null = mock_transport(**{"/position/search": lambda r: httpx.Response(
            200, json={"success": True, "content": {"totalCount": 0, "currentPage": 1, "pageSize": 100, "datas": None}}
        )})
        with httpx.Client(transport=t_null) as c:
            empty = p.fetch(ref, "任何标题", c)
        assert not empty.ok and empty.error == "no_match"

    def test_tencent_detail_e1005_and_search(self):
        p = TencentProvider()
        ref = p.parse("https://careers.tencent.com/jobdesc.html?postId=1234567890")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and "腾讯新闻" in res.jd_text and res.api_title and "CDG" in res.api_title
        # Code=500 + Data=E1005 = 官方下线码
        t = mock_transport(**{"post/ByPostId": lambda r: httpx.Response(
            200, json={"Code": 500, "Data": "E1005"}
        )})
        with httpx.Client(transport=t) as c:
            dead = p.fetch(ref, "", c)
        assert not dead.ok and "E1005" in dead.error
        # 列表 URL 搜索反查
        sref = p.parse("https://careers.tencent.com/careers.html")
        with httpx.Client(transport=mock_transport()) as c:
            s = p.fetch(sref, "27586-视觉设计师（CDG）", c)
        assert s.ok and s.match_mode == "exact"

    def test_meituan_detail_status001_and_search(self):
        p = MeituanProvider()
        ref = p.parse("https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and "美团优选" in res.jd_text and res.api_title == "视觉设计师"
        # jobStatus=001 = 官方下线码
        t = mock_transport(**{"getJobDetail": lambda r: httpx.Response(
            200, json={"status": 1, "data": {"jobUnionId": "1000234", "name": "x", "jobStatus": "001"}}
        )})
        with httpx.Client(transport=t) as c:
            dead = p.fetch(ref, "", c)
        assert not dead.ok and "jobStatus=001" in dead.error
        sref = p.parse("https://zhaopin.meituan.com/web/position/list")
        with httpx.Client(transport=mock_transport()) as c:
            s = p.fetch(sref, "视觉设计师", c)
        assert s.ok and s.match_mode == "exact"

    def test_greenhouse_double_encoded_content(self):
        """Greenhouse content 是实体转义的 HTML——两遍解码后必须无残留标签。"""
        p = GreenhouseProvider()
        ref = p.parse("https://job-boards.greenhouse.io/stripe/jobs/1234567")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and res.api_title == "Product Designer, Payments"
        assert "<" not in res.jd_text and "&lt;" not in res.jd_text
        assert "Payments design team" in res.jd_text and "payment onboarding" in res.jd_text

    def test_lever_roundtrip_composition(self):
        p = LeverProvider()
        ref = p.parse("https://jobs.lever.co/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and res.api_title == "Product Designer (China)"
        # descriptionPlain + lists 段落拼接
        assert "China market surfaces" in res.jd_text and "Requirements" in res.jd_text
        assert "Figma and design systems" in res.jd_text

    def test_ashby_board_lookup_case_insensitive(self):
        p = AshbyProvider()
        # URL 里 id 大小写与 board 响应不同 → 大小写不敏感比对（career-ops 同判）
        ref = p.parse("https://jobs.ashbyhq.com/linear/8B9A2C1D-3E4F-5A6B-7C8D-9E0F1A2B3C4D")
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and res.api_title == "Senior Product Designer"
        assert "China-facing surfaces" in res.jd_text
        miss_ref = p.parse("https://jobs.ashbyhq.com/linear/deadbeef-0000-0000-0000-000000000000")
        with httpx.Client(transport=mock_transport()) as c:
            miss = p.fetch(miss_ref, "", c)
        assert not miss.ok and miss.error == "no_match"

    def test_workday_html_description(self):
        p = WorkdayProvider()
        ref = p.parse(
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
            "US-CA-Santa-Clara/Program-Manager_R12345"
        )
        with httpx.Client(transport=mock_transport()) as c:
            res = p.fetch(ref, "", c)
        assert res.ok and res.api_title and "Program Manager" in res.api_title
        assert "<" not in res.jd_text and "Developer Programs" in res.jd_text
        assert "program management" in res.jd_text


# ---------- 引擎：落库 / 断点续跑 / 重试上限 / 统计 ----------


class TestEngine:
    ROWS = [
        ("广告大模型研究员", "字节跳动", "https://jobs.bytedance.com/campus/position/7628896431840807173/detail"),
        ("公关策略专员", "小米", "https://xiaomi.jobs.f.mioffice.cn/campus/position/7686764793517001010/detail"),
        ("工程造价岗", "保利", "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80"),
        ("营销推广专员（网易智邮）", "网易", "https://campus.163.com/app/job/position?id=103"),
        ("无关岗位", "网易", "https://campus.163.com/app/job/position?id=103"),
        ("未知站岗位", "某公司", "https://jobs.example.com/position/1"),  # 无 provider → 不选入
        # ---- B2 新 provider 行（批量/续跑/attempts 测试免费覆盖新 provider） ----
        ("用户体验设计师", "阿里巴巴", "https://talent.alibaba.com/off-campus/position-detail?positionId=12345678"),
        ("腾讯新闻视觉设计师", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890"),
        ("美团优选视觉设计师", "美团", "https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234"),
        ("Payments Product Designer", "Stripe", "https://job-boards.greenhouse.io/stripe/jobs/1234567"),
        ("China Product Designer", "Acme", "https://jobs.lever.co/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890"),
        ("Senior Product Designer", "Linear", "https://jobs.ashbyhq.com/linear/8b9a2c1d-3e4f-5a6b-7c8d-9e0f1a2b3c4d"),
        ("Program Manager", "NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Program-Manager_R12345"),
    ]

    def test_run_writes_jd_and_log(self, con):
        _seed(con, self.ROWS)
        # 记录补全前 search_text——断言刷新逻辑真实生效（而非入库即非空的装饰性断言）
        before = con.execute(
            "SELECT search_text FROM job_postings WHERE title = '广告大模型研究员'"
        ).fetchone()["search_text"]
        out = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None).run(limit=50)
        assert out["total_ok"] == 11
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
        # B2 七个新 provider 各自 ok 一条（13 行 - 6 旧行 = 7 新行全 ok）
        for pid in ("alibaba", "tencent", "meituan", "greenhouse", "lever", "ashby", "workday"):
            assert log.get(pid) == 1, f"{pid}: {log.get(pid)}"
        # 163 审计字段：api_title/match_mode 落账，串岗可追溯
        audit = con.execute(
            "SELECT api_title, match_mode FROM jd_enrich_log WHERE provider='netease163' AND status='ok'"
        ).fetchone()
        assert audit["api_title"] and audit["match_mode"] == "exact"
        st = EnrichmentEngine(con).stats()
        assert st["with_jd"] == 11 and st["active_jobs"] == 13

    def test_resume_after_interrupt(self, con):
        """断点续跑：limit=2 跑一半（模拟杀进程），再跑处理剩余——状态全在库内。"""
        _seed(con, self.ROWS)
        eng = EnrichmentEngine(con, transport=mock_transport(), sleep=lambda s: None)
        first = eng.run(limit=2)
        assert first["total_ok"] + first["total_no_match"] == 2  # no_match 也占名额
        second = eng.run(limit=50)
        assert second["total_ok"] + first["total_ok"] == 11
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
        assert r.status_code == 200 and r.json()["total_ok"] == 11, r.text
        assert "重新运行匹配" in r.json()["match_stale_hint"]
        st = c.get("/api/enrichment/stats").json()
        assert st["with_jd"] == 11
        job = [j for j in c.get("/api/jobs", params={"limit": 50}).json()["items"]
               if j["title"] == "广告大模型研究员"][0]
        # 已补全的岗位再单条补全 → 拒绝（幂等保护）
        r2 = c.post(f"/api/jobs/{job['id']}/enrich")
        assert r2.status_code == 200 and "已有 JD 正文" in r2.json()["error"]
        r3 = c.post("/api/enrichment/run", json={"provider": "nope"})
        assert r3.status_code == 422
    config.set_data_dir(None)
