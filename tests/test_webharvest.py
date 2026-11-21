"""网页采集引擎（v3.8 webharvest）测试：三级提取/放大器/安全闸/幂等。

立场对齐：SSRF 私网零请求；robots 遵守；反爬如实降级；绝不臆造字段。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobhater import config
from jobhater.db import apply_all, connect
from jobhater.services.webharvest import (
    MAX_DEPTH,
    WebHarvestService,
    extract_page,
    related_links,
)

FIX = Path(__file__).parent / "fixtures" / "webharvest_liepin.html"

# 结构与真实猎聘详情页一致（选择器/标题格式/header 区），内容虚构
FIXTURE_HTML = """<!doctype html><html><head><title>【武汉 新媒体运营招聘】-星尘文创（虚构）招聘信息-猎聘</title></head>
<body><section class="job-detail-header-box">
<span class="job-title">新媒体运营</span>
<span class="job-salary">8-12k</span>
</section>
<section class="job-detail-company-box"></section>
<section class="job-intro-container"><dl class="paragraph"><dt>职位介绍</dt>
<dd data-selector="job-intro-content">岗位职责
1. 负责公司新媒体矩阵的日常运营，包括内容策划、文案撰写、排版编辑与数据复盘。
2. 策划原创图文与短视频选题，打造贴合品牌调性的内容，提升粉丝粘性。
任职要求
1. 本科及以上学历，新闻传播、市场营销相关专业优先。
2. 一年以上新媒体运营经验，熟悉公众号与短视频平台。</dd></dl></section>
<div class="related"><a href="/job/1111111111.shtml">相关岗位一</a>
<a href="/job/2222222222.shtml">相关岗位二</a>
<a href="https://other.example.com/job/3333333333.shtml">外站（应排除）</a></div>
</body></html>"""

JSONLD_HTML = """<html><head><title>测试</title></head><body>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"行政专员","description":"负责办公用品管理、会议组织、文件收发与日常行政事务支持，维护办公环境与供应商对接，工作细致稳定。",
"hiringOrganization":{"name":"示例行政服务公司（虚构）"},
"jobLocation":{"address":{"addressRegion":"成都","addressLocality":"高新区"}},
"baseSalary":{"@type":"MonetaryAmount","currency":"CNY","value":{"@type":"QuantitativeValue","minValue":5,"maxValue":8,"unitText":"K"}},
"datePosted":"2026-11-20"}</script></body></html>"""

GENERIC_HTML = """<html><head><title>产品运营专员招聘-启明科技（虚构）</title>
<meta property="og:title" content="产品运营专员招聘-启明科技（虚构）"></head><body>
<div>启明科技招聘产品运营专员，负责用户增长与活动运营。</div>
<h2>岗位职责</h2><p>1. 负责产品日常运营与用户增长策略执行，策划线上活动并复盘数据效果。</p>
<p>2. 跟踪核心指标，输出运营周报，协同产品与设计优化用户体验。</p>
<h2>任职要求</h2><p>1. 本科及以上学历，一年以上互联网运营经验。</p></body></html>"""


@pytest.fixture()
def env(tmp_path):
    config.set_data_dir(tmp_path)
    apply_all()
    con = connect()
    yield con, tmp_path
    con.close()
    config.set_data_dir(None)


def _svc(con=None, transport=None, sleep=None, **kw):
    return WebHarvestService(con, transport=transport,
                             sleep=sleep or (lambda s: None), **kw)


# ---------- 提取三级 ----------


class TestExtractors:
    def test_liepin_adapter(self):
        raw, method, review = extract_page(FIXTURE_HTML, "https://www.liepin.com/job/123.shtml")
        assert method == "liepin" and review is False
        assert raw["title"] == "新媒体运营"
        assert raw["company"] == "星尘文创（虚构）"
        assert raw["city"] == "武汉" and raw["salary"] == "8-12k"
        assert "岗位职责" in raw["description"] and "任职要求" in raw["description"]

    def test_liepin_city_echo_stripped(self):
        """城市过滤页标题的公司段带城市回声（集团武汉→集团），按捕获城市剥离。"""
        html = FIXTURE_HTML.replace(
            "【武汉 新媒体运营招聘】-星尘文创（虚构）招聘信息",
            "【武汉 新媒体运营招聘】-星尘文创（虚构）武汉招聘信息")
        raw, method, _ = extract_page(html, "https://www.liepin.com/job/1.shtml")
        assert method == "liepin" and raw["company"] == "星尘文创（虚构）"

    def test_jsonld_jobposting(self):
        raw, method, review = extract_page(JSONLD_HTML, "https://careers.example.com/job/9")
        assert method == "jsonld"
        assert raw["title"] == "行政专员"
        assert raw["company"] == "示例行政服务公司（虚构）"
        assert raw["city"] == "成都"
        assert raw["salary"] == "5-8K"
        assert raw["published_at"] == "2026-11-20"

    def test_jsonld_salary_absolute_month_normalized(self):
        """unitText=MONTH 的绝对元薪资→规范化「元/月」（parse_salary 可解析），
        非 CNY 币种如实保留原样（不换汇不臆造）。"""
        posting = {"@type": "JobPosting", "title": "数据专员",
                   "description": "负责业务数据的日常整理、核对与报表输出，维护数据台账，"
                                  "配合各部门完成取数需求并保障口径一致。",
                   "hiringOrganization": {"name": "示例数据服务公司（虚构）"},
                   "baseSalary": {"value": {"minValue": 9000.0, "maxValue": 15000.0,
                                            "unitText": "MONTH"}}}
        html = ('<script type="application/ld+json">'
                + json.dumps(posting, ensure_ascii=False) + "</script>")
        raw, method, _ = extract_page(html, "https://x.example.com/a")
        assert method == "jsonld" and raw["salary"] == "9000-15000元/月"
        posting["baseSalary"]["currency"] = "USD"
        posting["baseSalary"]["value"] = {"minValue": 90000, "maxValue": 120000,
                                          "unitText": "YEAR"}
        html2 = ('<script type="application/ld+json">'
                 + json.dumps(posting, ensure_ascii=False) + "</script>")
        raw2, _, _ = extract_page(html2, "https://x.example.com/b")
        assert raw2["salary"] == "90000-120000USD"

    def test_jsonld_graph_container(self):
        """@graph 包裹的 JobPosting（部分站点的嵌套写法）也能命中。"""
        posting = {"@type": "JobPosting", "title": "行政专员",
                   "description": "负责办公用品管理、会议组织、文件收发与日常行政事务支持，对接供应商并维护办公环境，工作细致稳定。",
                   "hiringOrganization": {"name": "示例行政服务公司（虚构）"}}
        html = ('<script type="application/ld+json">'
                + json.dumps({"@context": "https://schema.org",
                              "@graph": [{"@type": "WebPage"}, posting]},
                             ensure_ascii=False)
                + "</script>")
        raw, method, _ = extract_page(html, "https://x.example.com/a")
        assert method == "jsonld" and raw["title"] == "行政专员"

    def test_generic_marks_needs_review(self):
        raw, method, review = extract_page(GENERIC_HTML, "https://hr.example.com/p/1.html")
        assert method == "generic" and review is True
        assert raw["title"] == "产品运营专员"
        assert raw["company"] == "启明科技（虚构）"
        assert "岗位职责" in raw["description"]

    def test_generic_seo_underscore_title(self):
        """「岗位名招聘_公司名招聘-平台名」(高校人才网等 SEO 站):
        公司必须是下划线第二段, 不能让横线规则把平台名误当公司名。"""
        html = ('<html><head><title>财务部会计招聘_山东省科创集团有限公司招聘-'
                '高校人才网|高才网</title></head><body>'
                '<h2>岗位职责</h2><p>负责集团日常会计核算、报表编制与税务申报，'
                '配合审计并维护财务系统数据准确性。</p>'
                '<h2>任职要求</h2><p>本科及以上，会计相关专业，两年以上经验。</p></body></html>')
        raw, method, review = extract_page(html, "https://www.gaoxiaojob.com/job/detail/1.html")
        assert method == "generic" and review is True
        assert raw["title"] == "财务部会计"
        assert raw["company"] == "山东省科创集团有限公司"
        assert "高校人才网" not in (raw["company"] or "")
        assert "岗位职责" in raw["description"]
        # 公司段尾部装饰字符（「研究所#招聘」）一并剥离
        html2 = ('<html><head><title>会计岗招聘_农业农村部食物与营养发展研究所#招聘-'
                 '高校人才网|高才网</title></head><body>'
                 '<h2>岗位职责</h2><p>负责研究所日常会计核算与资金管理，编制月度报表，'
                 '配合年度审计并管理科研经费台账，开展预算执行分析与财务档案维护。</p></body></html>')
        raw2, _, _ = extract_page(html2, "https://www.gaoxiaojob.com/job/detail/2.html")
        assert raw2["company"] == "农业农村部食物与营养发展研究所"

    def test_extraction_failure_is_honest(self):
        raw, why, _ = extract_page("<html><body>登录后查看详情</body></html>",
                                   "https://www.example.com/x")
        assert raw is None and why


class TestRelatedLinks:
    def test_liepin_pattern_excludes_cross_host(self):
        links = related_links(FIXTURE_HTML, "https://www.liepin.com/job/123.shtml")
        assert len(links) == 2
        assert all("liepin.com" in link for link in links)

    def test_generic_pattern_for_unknown_sites(self):
        html = '<a href="/position_detail/abc.html">职</a><a href="/news/x.html">闻</a>'
        links = related_links(html, "https://careers.sample.cn/list")
        assert len(links) == 1 and "position_detail" in links[0]


# ---------- 安全闸与礼貌抓取 ----------


class TestSafety:
    def test_ssrf_private_url_zero_requests(self, env):
        con, _ = env

        def _boom(request: httpx.Request):  # pragma: no cover
            raise AssertionError("私网 URL 不应发出任何请求")

        svc = _svc(con, transport=httpx.MockTransport(_boom))
        out = svc.harvest(["http://192.168.1.3/job/1.shtml"], ingest=False)
        svc.close()
        assert out["pages_fetched"] == 0
        assert any("ssrf_blocked" in d["reason"] for d in out["degraded"] + out["failed"])

    def test_robots_disallow_degrades(self, env):
        con, _ = env
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /job/\n")
            return httpx.Response(200, text=FIXTURE_HTML, headers={"content-type": "text/html; charset=utf-8"})

        svc = _svc(con, transport=httpx.MockTransport(handler))
        out = svc.harvest(["https://www.liepin.com/job/123.shtml"], ingest=False)
        svc.close()
        assert out["pages_fetched"] == 0
        assert any("robots" in d["reason"] for d in out["degraded"])

    def test_robots_403_blocks_whole_host(self, env):
        con, _ = env

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(403)
            raise AssertionError("robots 403 后不应抓任何页面")  # pragma: no cover

        svc = _svc(con, transport=httpx.MockTransport(handler))
        out = svc.harvest(["https://www.liepin.com/job/1.shtml"], ingest=False)
        svc.close()
        assert out["pages_fetched"] == 0
        assert any("403" in d["reason"] for d in out["degraded"])

    def test_per_host_rate_limit_sleeps(self, env):
        con, _ = env
        waits: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text=FIXTURE_HTML, headers={"content-type": "text/html; charset=utf-8"})

        svc = _svc(con, transport=httpx.MockTransport(handler), sleep=waits.append)
        out = svc.harvest(["https://www.liepin.com/job/123.shtml"], ingest=False)
        svc.close()
        # 种子页带 2 条相关链接，depth=1 → 全部跟进（放大器）；
        # robots.txt + 3 页同主机连续命中：限速对第 2+ 次命中生效
        assert out["pages_fetched"] == 3
        assert any(w > 0 for w in waits)


# ---------- 放大器 / 幂等 / 入库 ----------


class TestHarvestFlow:
    def _handler(self, pages: dict[str, str], calls: list[str]):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            body = pages.get(request.url.path)
            if body is None:
                return httpx.Response(404)
            return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

        return handler

    def test_depth_amplifier_and_caps(self, env):
        con, _ = env
        calls: list[str] = []
        pages = {
            "/job/000.shtml": FIXTURE_HTML,
            "/job/1111111111.shtml": FIXTURE_HTML,
            "/job/2222222222.shtml": FIXTURE_HTML,
        }
        svc = _svc(con, transport=httpx.MockTransport(self._handler(pages, calls)))
        out = svc.harvest(["https://www.liepin.com/job/000.shtml"], depth=1, ingest=False)
        svc.close()
        assert out["pages_fetched"] == 3  # 种子 + 2 条相关
        assert out["extracted"] == 3

        calls.clear()
        svc = _svc(con, transport=httpx.MockTransport(self._handler(pages, calls)))
        out = svc.harvest(["https://www.liepin.com/job/000.shtml"], depth=1, max_pages=2, ingest=False)
        svc.close()
        assert out["pages_fetched"] == 2  # max_pages 硬顶

    def test_depth_beyond_max_is_clamped(self, env):
        con, _ = env
        svc = _svc(con)
        assert MAX_DEPTH == 2
        svc.close()

    def test_idempotent_skip_existing(self, env):
        con, _ = env
        svc = _svc(con, existing_urls={"https://www.liepin.com/job/123.shtml"})
        out = svc.harvest(["https://www.liepin.com/job/123.shtml"], ingest=False)
        svc.close()
        assert out["skipped_existing"] == 1 and out["pages_fetched"] == 0

    def test_ingest_roundtrip_with_provenance(self, env):
        con, _ = env
        calls: list[str] = []
        svc = _svc(con, transport=httpx.MockTransport(self._handler(
            {"/job/000.shtml": FIXTURE_HTML}, calls)))
        out = svc.harvest(["https://www.liepin.com/job/000.shtml"], depth=0)
        svc.close()
        assert out["ingest"]["added"] == 1
        assert "www.liepin.com" in out["ingest"]["by_source"]
        row = con.execute(
            "SELECT source_id, title, salary_text, city FROM job_postings"
        ).fetchone()
        assert row["source_id"] == "www.liepin.com"
        assert row["title"] == "新媒体运营" and row["salary_text"] == "8-12k"

    def test_reingest_skips_and_reports(self, env):
        """同一种子重跑：全跳过，库不翻倍（幂等承诺）。"""
        con, _ = env
        calls: list[str] = []
        svc = _svc(con, transport=httpx.MockTransport(self._handler(
            {"/job/000.shtml": FIXTURE_HTML}, calls)))
        svc.harvest(["https://www.liepin.com/job/000.shtml"], depth=0)
        calls.clear()
        out = svc.harvest(["https://www.liepin.com/job/000.shtml"], depth=0)
        svc.close()
        assert out["skipped_existing"] == 1
        assert con.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"] == 1


# ---------- MCP / CLI 出口 ----------


class TestSurfacing:
    def test_mcp_harvest_and_preview(self, env, monkeypatch):
        con, tmp_path = env
        con.close()  # MCP 工具内部自建连接
        calls: list[str] = []
        transport = httpx.MockTransport(self._handler_static(calls))
        real_client_init = httpx.Client.__init__

        def patched_init(self, *a, **kw):
            kw["transport"] = transport
            real_client_init(self, *a, **kw)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)
        from jobhater import mcp_server as ms

        config.set_data_dir(tmp_path)
        try:
            prev = json.loads(ms.jobs_fetch_preview("https://www.liepin.com/job/000.shtml"))
            assert prev["ok"] is True and prev["method"] == "liepin"
            assert prev["related_links"] == 2

            r = json.loads(ms.jobs_harvest(
                json.dumps(["https://www.liepin.com/job/000.shtml"]), dry_run=True))
            assert r["dry_run"] is True and r["extracted"] == 3  # 种子+2 相关

            r2 = json.loads(ms.jobs_harvest(
                json.dumps(["https://www.liepin.com/job/000.shtml"])))
            # 3 页同内容 → 去重引擎如实工作：收 3 入 1 去重 2
            assert r2["ingest"]["received"] == 3 and r2["ingest"]["added"] == 1
            assert r2["ingest"]["deduped_exact"] + r2["ingest"]["deduped_near"] == 2
        finally:
            config.set_data_dir(None)

    @staticmethod
    def _handler_static(calls: list[str]):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text=FIXTURE_HTML, headers={"content-type": "text/html; charset=utf-8"})

        return handler

    def test_cli_harvest_requires_seed(self, tmp_path, capsys):
        from jobhater.cli import main

        config.set_data_dir(tmp_path)
        try:
            assert main(["harvest"]) == 2
            assert "种子" in capsys.readouterr().err
        finally:
            config.set_data_dir(None)
