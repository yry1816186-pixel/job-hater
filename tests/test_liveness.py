"""B3 判活阶梯测试：保守性是第一断言——误判死（expired）会埋掉真岗，严格劣于 uncertain。

fixture 与 test_jd_enrich 同源（career-ops 文档化响应形态）；判死/判活/不确定
三类路径每个 provider 至少一例，传输层故障（429/5xx/超时）必须落 uncertain。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobhater.db import apply_all, connect
from jobhater.services.liveness import LivenessService, classify_body

FIX = Path(__file__).parent / "fixtures" / "enrich"


def _fixture(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def mock_transport(**overrides):
    """与 test_jd_enrich 同款离线回放；overrides 覆写单路由模拟死岗/限流/故障。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, responder in overrides.items():
            if pattern in url:
                return responder(request)
        if "/position/detail" in url:
            return httpx.Response(200, json=_fixture("alibaba.json"))
        if "post/ByPostId" in url:
            return httpx.Response(200, json=_fixture("tencent.json"))
        if "getJobDetail" in url:
            return httpx.Response(200, json=_fixture("meituan.json"))
        if "boards-api.greenhouse.io" in url:
            return httpx.Response(200, json=_fixture("greenhouse.json"))
        if "lever.co/v0/postings" in url:
            return httpx.Response(200, json=_fixture("lever.json"))
        if "posting-api/job-board" in url:
            return httpx.Response(200, json=_fixture("ashby.json"))
        if "wday/cxs" in url:
            return httpx.Response(200, json=_fixture("workday.json"))
        if "talent.baidu.com" in url:
            return httpx.Response(200, text=(FIX / "baidu.html").read_text(encoding="utf-8"))
        return httpx.Response(200, text="<html><body>普通页面内容，无下线标记。" * 10 + "</body></html>")

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


def _job_id(con, title: str) -> str:
    return con.execute("SELECT id FROM job_postings WHERE title = ?", (title,)).fetchone()["id"]


# ---------- Tier2 正文分类（保守词表 + guard） ----------


class TestClassify:
    def test_zh_hard_markers(self):
        for text in ("很抱歉，该职位已关闭。去看看其他机会", "该岗位已下线，欢迎关注后续招聘"):
            v = classify_body(text + "更多推荐岗位持续更新中，欢迎浏览全部在招职位。" * 3)
            assert v.verdict == "expired", text

    def test_en_hard_markers(self):
        v = classify_body("This job is no longer accepting applications. " + "padding " * 20)
        assert v.verdict == "expired" and v.code == "no_longer_accepting"
        v2 = classify_body("Sorry, this position has been filled. " + "padding " * 20)
        assert v2.verdict == "expired" and v2.code == "has_been_filled"

    def test_filled_guard_application_form(self):
        """「申请表填好后…」不是死信号——career-ops 用 lookbehind guard，Python 侧同判。"""
        v = classify_body(
            "Once the application form has been filled out, you will receive a confirmation. " * 3
        )
        assert v.verdict == "uncertain"

    def test_closed_compound_guard(self):
        """closed-loop/closed-form 是复合词，不是关闭。"""
        v = classify_body("This role is closed-loop control of the platform. " * 5)
        assert v.verdict == "uncertain"

    def test_is_closed_plain(self):
        v = classify_body("This role is closed. " + "padding " * 20)
        assert v.verdict == "expired" and v.code == "is_closed"

    def test_insufficient_content(self):
        assert classify_body("").verdict == "uncertain"
        assert classify_body("职位已关闭").code == "insufficient_content"  # 死词但正文过短 → 不定论

    def test_live_page_uncertain(self):
        v = classify_body("岗位职责：负责产品体验设计，与团队协作完成日常需求。" * 5)
        assert v.verdict == "uncertain" and v.code == "no_death_marker"


# ---------- Tier1 provider 判活（死/活/不确定三态） ----------


class TestTier1:
    def _svc(self, con, **overrides) -> LivenessService:
        return LivenessService(con, transport=mock_transport(**overrides), sleep=lambda s: None)

    def test_alibaba_active_no_flip(self, con):
        _seed(con, [("用户体验设计师", "阿里巴巴",
                     "https://talent.alibaba.com/off-campus/position-detail?positionId=12345678")])
        out = self._svc(con).probe(_job_id(con, "用户体验设计师"))
        assert out["verdict"] == "active" and out["status_changed"] is False
        row = con.execute("SELECT status FROM job_postings WHERE title='用户体验设计师'").fetchone()
        assert row["status"] == "active"

    def test_alibaba_dead_content_null_flips_expired(self, con):
        _seed(con, [("阿里死岗", "阿里巴巴",
                     "https://talent.alibaba.com/off-campus/position-detail?positionId=12345678")])
        svc = self._svc(con, **{"/position/detail": lambda r: httpx.Response(
            200, json={"success": True, "content": None}
        )})
        out = svc.probe(_job_id(con, "阿里死岗"))
        assert out["verdict"] == "expired" and out["code"] == "alibaba_content_null"
        assert out["status_changed"] is True
        row = con.execute(
            "SELECT status, reject_reason FROM job_postings WHERE title='阿里死岗'"
        ).fetchone()
        assert row["status"] == "expired" and "liveness:" in row["reject_reason"]
        log = dict(con.execute(
            "SELECT verdict, code FROM job_liveness_log WHERE job_id=?",
            (_job_id(con, "阿里死岗"),),
        ).fetchone())
        assert log["verdict"] == "expired" and log["code"] == "alibaba_content_null"

    def test_tencent_e1005_expired(self, con):
        _seed(con, [("腾讯死岗", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890")])
        svc = self._svc(con, **{"post/ByPostId": lambda r: httpx.Response(
            200, json={"Code": 500, "Data": "E1005"}
        )})
        out = svc.probe(_job_id(con, "腾讯死岗"))
        assert out["verdict"] == "expired" and out["code"] == "tencent_e1005"

    def test_meituan_jobstatus_001_expired(self, con):
        _seed(con, [("美团死岗", "美团", "https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234")])
        svc = self._svc(con, **{"getJobDetail": lambda r: httpx.Response(
            200, json={"status": 1, "data": {"jobUnionId": "1000234", "jobStatus": "001"}}
        )})
        out = svc.probe(_job_id(con, "美团死岗"))
        assert out["verdict"] == "expired" and out["code"] == "meituan_status_001"

    def test_greenhouse_404_expired(self, con):
        _seed(con, [("GH死岗", "Stripe", "https://job-boards.greenhouse.io/stripe/jobs/1234567")])
        svc = self._svc(con, **{"boards-api.greenhouse.io": lambda r: httpx.Response(404)})
        out = svc.probe(_job_id(con, "GH死岗"))
        assert out["verdict"] == "expired" and out["code"] == "http_gone"

    def test_lever_404_not_authoritative(self, con):
        """Lever API 404 对机密岗是假阴性（career-ops 实测）——必须 uncertain，不判死。"""
        _seed(con, [("Lever岗", "Acme", "https://jobs.lever.co/acme/1f2e3d4c-5b6a-7890-abcd-ef1234567890")])
        svc = self._svc(con, **{"lever.co/v0/postings": lambda r: httpx.Response(404)})
        out = svc.probe(_job_id(con, "Lever岗"))
        assert out["verdict"] == "uncertain" and "404" in out["code"]
        row = con.execute("SELECT status FROM job_postings WHERE title='Lever岗'").fetchone()
        assert row["status"] == "active"

    def test_workday_s22_withdrawn_expired(self, con):
        _seed(con, [("WD死岗", "NVIDIA",
                     "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Program-Manager_R12345")])
        svc = self._svc(con, **{"wday/cxs": lambda r: httpx.Response(
            403, json={"httpStatus": 403, "errorCode": "S22"}
        )})
        out = svc.probe(_job_id(con, "WD死岗"))
        assert out["verdict"] == "expired" and out["code"] == "workday_api_withdrawn"

    def test_workday_waf_403_uncertain(self, con):
        _seed(con, [("WAF岗", "NVIDIA",
                     "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Program-Manager_R12345")])
        svc = self._svc(con, **{"wday/cxs": lambda r: httpx.Response(403, text="<html>blocked</html>")})
        out = svc.probe(_job_id(con, "WAF岗"))
        assert out["verdict"] == "uncertain"

    def test_ashby_unlisted_expired(self, con):
        _seed(con, [("Ashby死岗", "Linear",
                     "https://jobs.ashbyhq.com/linear/deadbeef-0000-0000-0000-000000000000")])
        out = self._svc(con).probe(_job_id(con, "Ashby死岗"))
        assert out["verdict"] == "expired" and out["code"] == "ashby_api_unlisted"

    def test_transport_500_uncertain_no_flip(self, con):
        """429/5xx/超时绝不判死——判活服务对传输故障的立场。"""
        _seed(con, [("故障岗", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890")])
        for status in (429, 500, 502):
            svc = self._svc(con, **{"post/ByPostId": lambda r, s=status: httpx.Response(s)})
            out = svc.probe(_job_id(con, "故障岗"))
            assert out["verdict"] == "uncertain", f"HTTP {status} 必须不确定"
        row = con.execute("SELECT status FROM job_postings WHERE title='故障岗'").fetchone()
        assert row["status"] == "active"

    def test_search_mode_url_uncertain(self, con):
        """列表页 URL（无岗位 ID）不适用详情判活——如实 uncertain，不猜。"""
        _seed(con, [("列表岗", "阿里巴巴", "https://talent.alibaba.com/off-campus/position-list?keywords=x")])
        out = self._svc(con).probe(_job_id(con, "列表岗"))
        assert out["verdict"] == "uncertain" and out["code"] == "search_url_no_id"


# ---------- Tier2 静态页判活 ----------


class TestTier2:
    def _svc(self, con, **overrides) -> LivenessService:
        return LivenessService(con, transport=mock_transport(**overrides), sleep=lambda s: None)

    def test_static_zh_marker_expired(self, con):
        # zhiye 无 check() → 落 Tier2 静态 GET canonical URL
        _seed(con, [("北森死岗", "保利",
                     "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        dead_page = "<html><body>很抱歉，您查看的职位已关闭。" + "更多推荐岗位 " * 20 + "</body></html>"
        svc = self._svc(con, **{"polycareer.zhiye.com": lambda r: httpx.Response(200, text=dead_page)})
        out = svc.probe(_job_id(con, "北森死岗"))
        assert out["verdict"] == "expired" and out["code"] == "zh_position_closed"

    def test_static_404_expired(self, con):
        _seed(con, [("静态404岗", "保利",
                     "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        svc = self._svc(con, **{"polycareer.zhiye.com": lambda r: httpx.Response(404)})
        out = svc.probe(_job_id(con, "静态404岗"))
        assert out["verdict"] == "expired" and out["code"] == "http_gone"

    def test_static_spa_shell_uncertain(self, con):
        """JS 渲染 SPA 只回导航/页脚 → 正文不足 → uncertain（如实降级）。"""
        _seed(con, [("SPA岗", "保利",
                     "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        svc = self._svc(con, **{"polycareer.zhiye.com": lambda r: httpx.Response(
            200, text="<html><body><nav>首页 职位 关于我们</nav></body></html>"
        )})
        out = svc.probe(_job_id(con, "SPA岗"))
        assert out["verdict"] == "uncertain" and out["code"] == "insufficient_content"

    def test_static_cross_host_redirect_uncertain(self, con):
        _seed(con, [("跳转岗", "保利",
                     "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80")])
        svc = self._svc(con, **{"polycareer.zhiye.com": lambda r: httpx.Response(
            302, headers={"Location": "https://evil.io/steal"}
        )})
        out = svc.probe(_job_id(con, "跳转岗"))
        assert out["verdict"] == "uncertain" and out["code"] == "redirect_off_host"

    def test_probe_guards(self, con):
        _seed(con, [("无链接岗", "某公司", "")])
        svc = self._svc(con)
        assert svc.probe("nonexistent")["ok"] is False
        assert svc.probe(_job_id(con, "无链接岗"))["error"] == "该岗位没有官网链接，无法判活"


# ---------- 批量与统计 ----------


class TestRunAndStats:
    def _svc(self, con, **overrides) -> LivenessService:
        return LivenessService(con, transport=mock_transport(**overrides), sleep=lambda s: None)

    def test_run_batch_and_expired_skipped(self, con):
        rows = [
            ("活岗A", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890"),
            ("活岗B", "阿里巴巴", "https://talent.alibaba.com/off-campus/position-detail?positionId=12345678"),
            ("死岗C", "美团", "https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234"),
        ]
        _seed(con, rows)
        svc = self._svc(con, **{"getJobDetail": lambda r: httpx.Response(
            200, json={"status": 1, "data": {"jobUnionId": "1000234", "jobStatus": "001"}}
        )})
        out = svc.run(limit=10)
        assert out["checked"] == 3
        assert out["counts"] == {"active": 2, "expired": 1, "uncertain": 0}
        assert out["expired_jobs"][0]["title"] == "死岗C"
        # 死岗已 expired → 第二轮不再复查（省请求）；其余照常
        out2 = svc.run(limit=10)
        assert out2["checked"] == 2 and out2["counts"]["expired"] == 0
        st = svc.stats()
        assert st["active_with_url"] == 2  # 死岗已离开 active
        assert st["verdicts"].get("expired") == 1

    def test_stats_never_checked(self, con):
        _seed(con, [("待查岗", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890")])
        st = self._svc(con).stats()
        assert st["never_checked"] == 1 and st["active_with_url"] == 1


# ---------- API 端点 ----------


def test_liveness_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobhater import config
    from jobhater.api import create_app
    from jobhater.db import connect

    config.set_data_dir(tmp_path)
    orig_init = LivenessService.__init__

    def init_offline(self, con, **kw):
        kw.setdefault("transport", mock_transport())
        kw.setdefault("sleep", lambda s: None)
        orig_init(self, con, **kw)

    monkeypatch.setattr(LivenessService, "__init__", init_offline)
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/liveness/stats").json()["never_checked"] == 0
        con = connect(config.db_path())
        try:
            _seed(con, [
                ("API活岗", "腾讯", "https://careers.tencent.com/jobdesc.html?postId=1234567890"),
                ("API美团岗", "美团", "https://zhaopin.meituan.com/web/position/detail?jobUnionId=1000234"),
            ])
        finally:
            con.close()
        r = c.post("/api/jobs/" + _api_job_id(c, "API活岗") + "/liveness")
        assert r.status_code == 200 and r.json()["verdict"] == "active", r.text
        r2 = c.post("/api/liveness/run", json={"limit": 10})
        assert r2.status_code == 200 and r2.json()["checked"] == 2, r2.text
        st = c.get("/api/liveness/stats").json()
        assert st["verdicts"].get("active") == 2
        r404 = c.post("/api/jobs/nonexistent/liveness")
        assert r404.status_code == 404
    config.set_data_dir(None)


def _api_job_id(client, title: str) -> str:
    items = client.get("/api/jobs", params={"limit": 50}).json()["items"]
    return [j for j in items if j["title"] == title][0]["id"]
