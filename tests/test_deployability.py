"""可部署性硬化（v3.6）回归测试。

覆盖本批次四条防线：
1. 本机 API 安全线：Host 白名单（DNS rebinding）+ Origin 白名单（跨源写）；
2. 请求体/上传限额：超大 body 在读入前被 413 拒绝；
3. 前端 dist 解析：JOBHATER_DIST 显式声明 / 未构建时引导页（不再静默 404）；
4. 出站 SSRF 闸（netguard）：私网/环回/链路本地 URL 不发请求。
另含：纯标点搜索不 500、wheel 非 py 资源在包内、收件箱超限文件跳过。
"""
from __future__ import annotations

import importlib.resources
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from jobhater import __version__, config
from jobhater.api import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config.set_data_dir(tmp_path)
    monkeypatch.delenv("JOBHATER_DIST", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c
    config.set_data_dir(None)


# ---------- 1. 本机 API 安全线 ----------


class TestLocalOnlyGuard:
    def test_health_reports_version(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["version"] == __version__

    def test_evil_host_rejected(self, client):
        """DNS rebinding 防线：非白名单 Host 一律 400（含 GET 读路径）。"""
        r = client.get("/api/health", headers={"Host": "evil.example.com"})
        assert r.status_code == 400
        assert "JOBHATER_ALLOWED_HOSTS" in r.text

    def test_ipv6_loopback_host_allowed(self, client):
        r = client.get("/api/health", headers={"Host": "[::1]:8787"})
        assert r.status_code == 200

    def test_cross_site_write_rejected(self, client):
        """跨源简单请求写：恶意网页 Origin 的 POST/DELETE 一律 403。"""
        for path in ("/api/demo/seed", "/api/inbox/scan"):
            r = client.post(path, headers={"Origin": "http://evil.example.com"})
            assert r.status_code == 403, (path, r.status_code)

    def test_extension_and_dev_origins_allowed(self, client):
        """扩展（chrome-extension://）与本机开发端口（vite 代理）放行。"""
        r1 = client.post("/api/demo/seed", headers={"Origin": "chrome-extension://abcdefg"})
        assert r1.status_code == 200
        r2 = client.post("/api/demo/seed", headers={"Origin": "http://localhost:5173"})
        assert r2.status_code == 200

    def test_same_origin_lan_deploy_allowed(self, tmp_path, monkeypatch):
        """局域网部署：JOBHATER_ALLOWED_HOSTS 放开 Host 后，同源 Origin 的写放行。"""
        config.set_data_dir(tmp_path)
        monkeypatch.setenv("JOBHATER_ALLOWED_HOSTS", "nas.local")
        app = create_app()
        with TestClient(app) as c:
            r = c.post("/api/demo/seed",
                       headers={"Host": "nas.local:8787", "Origin": "http://nas.local:8787"})
            assert r.status_code == 200
            r2 = c.get("/api/health", headers={"Host": "nas.local"})
            assert r2.status_code == 200
            r3 = c.get("/api/health", headers={"Host": "evil.example.com"})
            assert r3.status_code == 400
        config.set_data_dir(None)

    def test_origin_prefix_bypass_rejected(self, client):
        """``http://127.0.0.1.evil.com`` 前缀伪装不放行（主机名精确匹配）。"""
        r = client.post("/api/demo/seed",
                        headers={"Origin": "http://127.0.0.1.evil.com"})
        assert r.status_code == 403

    def test_private_ip_host_allowed_by_default(self, client):
        """局域网 IP 直连开箱即用（v3.7）：私网/环回 IP 字面量 Host 默认放行。"""
        for host in ("192.168.1.5:8787", "10.0.0.8:8787", "172.20.1.4"):
            r = client.get("/api/health", headers={"Host": host})
            assert r.status_code == 200, (host, r.status_code)

    def test_public_domain_host_still_rejected(self, client):
        """私网 IP 放行不等于全开：公网域名 Host 仍走白名单（DNS rebinding 面是域名）。"""
        r = client.get("/api/health", headers={"Host": "careers.somecompany.com"})
        assert r.status_code == 400

    def test_guard_env_disables_middleware(self, tmp_path, monkeypatch):
        """JOBHATER_GUARD=0 逃生口：有外层防护的部署可整体关闭本机安全线。"""
        config.set_data_dir(tmp_path)
        monkeypatch.setenv("JOBHATER_GUARD", "0")
        app = create_app()
        with TestClient(app) as c:
            assert c.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 200
        config.set_data_dir(None)


# ---------- 2. 请求体限额 ----------


class TestBodyLimits:
    def test_paste_over_2mb_rejected(self, client):
        r = client.post("/api/import/paste", json={"text": "岗" * (2 * 1024 * 1024 + 128)})
        assert r.status_code == 413

    def test_normal_paste_still_works(self, client):
        r = client.post("/api/import/paste", json={"text": "招聘后端工程师，负责服务端开发。"})
        assert r.status_code == 200


# ---------- 3. dist 解析与引导页 ----------


class TestDistResolution:
    def _fake_dist(self, tmp_path):
        dist = tmp_path / "fakedist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<html><body><div id="root">FAKE_SPA</div></body></html>', encoding="utf-8")
        (dist / "assets" / "app.js").write_text("// ok", encoding="utf-8")
        return dist

    def test_env_dist_override_serves_spa(self, tmp_path, monkeypatch):
        config.set_data_dir(tmp_path)
        monkeypatch.setenv("JOBHATER_DIST", str(self._fake_dist(tmp_path)))
        with TestClient(create_app()) as c:
            assert "FAKE_SPA" in c.get("/").text
            assert c.get("/assets/app.js").status_code == 200
        config.set_data_dir(None)

    def test_missing_dist_returns_guide_page(self, tmp_path, monkeypatch):
        """未构建前端：``/`` 返回 200 引导页（修复前是静默 404 JSON）。"""
        config.set_data_dir(tmp_path)
        monkeypatch.setenv("JOBHATER_DIST", str(tmp_path / "not-built"))
        with TestClient(create_app()) as c:
            r = c.get("/")
            assert r.status_code == 200
            assert "前端" in r.text and "npm run build" in r.text
        config.set_data_dir(None)


# ---------- 4. 出站 SSRF 闸 ----------


class TestNetguard:
    def test_literal_private_ips_blocked(self):
        from jobhater.services.netguard import guard_url

        for u in (
            "http://127.0.0.1:8787/x",
            "http://169.254.169.254/latest/meta-data",  # 云元数据
            "http://192.168.1.1/admin",
            "http://10.0.0.5/internal",
            "http://172.16.0.9/",
            "http://[::1]/",
            "http://[fe80::1]/",
        ):
            assert guard_url(u) is not None, u

    def test_non_http_schemes_blocked(self):
        from jobhater.services.netguard import guard_url

        assert guard_url("file:///etc/passwd") is not None
        assert guard_url("ftp://example.com/x") is not None
        assert guard_url("not a url") is not None

    def test_public_host_allowed(self, monkeypatch):
        from jobhater.services import netguard

        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ])
        netguard._dns_cache.clear()
        assert netguard.guard_url("https://careers.example.com/jobs/1") is None

    def test_dns_decision_cached(self, monkeypatch):
        """v3.7 性能：同一主机名重复 URL 只解析一次 DNS（判活大池不再双倍解析）。"""
        from jobhater.services import netguard

        calls = []

        def fake_getaddrinfo(host, port):
            calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        netguard._dns_cache.clear()
        assert netguard.guard_url("https://careers.example.com/jobs/1") is None
        assert netguard.guard_url("https://careers.example.com/jobs/2") is None
        assert netguard.guard_url("https://careers.example.com/other") is None
        assert len(calls) == 1, calls

    def test_liveness_private_url_never_fetches(self, tmp_path):
        """判活 Tier2：私网 URL → uncertain+url_blocked，且一个请求都不发。"""
        from jobhater.db import apply_all, connect
        from jobhater.services.jobs import JobService
        from jobhater.services.liveness import LivenessService

        apply_all(tmp_path / "t.db")
        con = connect(tmp_path / "t.db")
        try:
            JobService(con).ingest(
                [{"title": "内网岗", "company": "测试公司",
                  "url": "http://192.168.0.10/jobs/1"}], source_id="test")
            jid = con.execute("SELECT id FROM job_postings WHERE title='内网岗'").fetchone()["id"]

            def _boom(request: httpx.Request) -> httpx.Response:  # pragma: no cover
                raise AssertionError("私网 URL 不应发出任何请求")

            svc = LivenessService(con, transport=httpx.MockTransport(_boom),
                                  sleep=lambda s: None)
            out = svc.probe(jid)
            assert out["verdict"] == "uncertain" and out["code"] == "url_blocked"
        finally:
            con.close()

    def test_wx_article_url_strict_matching(self):
        """公众号白名单：netloc 精确匹配，子串伪装 URL 不进深挖队列。"""
        from jobhater.services.wechat.article_fetch import _is_wx_article_url

        assert _is_wx_article_url("https://mp.weixin.qq.com/s/abc123") is True
        assert _is_wx_article_url("http://mp.weixin.qq.com/s?__biz=1") is True
        assert _is_wx_article_url("http://169.254.169.254/#mp.weixin.qq.com/s") is False
        assert _is_wx_article_url("https://evil.com/mp.weixin.qq.com/s") is False
        assert _is_wx_article_url("https://mp.weixin.qq.com/mp/appmsg/home") is False


# ---------- 5. 搜索健壮性与打包资源 ----------


class TestRobustness:
    @pytest.mark.parametrize("q", [";;;", '"', "。。。", "、、、"])
    def test_punctuation_only_query_no_500(self, client, q):
        """纯标点查询经分词后无 token：零结果（修复前 FTS5 空 MATCH 语法错误 → 500）。"""
        r = client.get("/api/jobs", params={"q": q})
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_package_resources_shipped(self):
        """wheel/安装产物必须带非 py 资源：迁移 SQL 与 OCR PowerShell worker。"""
        pkg = importlib.resources.files("jobhater")
        migs = list((pkg / "db" / "migrations").iterdir())
        assert migs, "migrations 未随包分发"
        assert (pkg / "services" / "wechat" / "ocr_worker.ps1").is_file(), \
            "ocr_worker.ps1 未随包分发（Windows OCR 通道将静默失效）"

    def test_inbox_oversize_file_skipped(self, tmp_path):
        """收件箱大小闸：>10MB 文件跳过记账且留在原处，小文件正常解析。"""
        from jobhater.services.inbox import InboxService

        config.set_data_dir(tmp_path)
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        big = inbox / "big.csv"
        with open(big, "wb") as f:
            f.truncate(11 * 1024 * 1024)  # 稀疏文件：瞬时创建
        small = inbox / "small.csv"
        small.write_text("公司,岗位\n测试公司,后端工程师", encoding="utf-8-sig")

        out = InboxService().scan()
        assert out["files"] == 1  # 只处理了小文件
        assert big.exists(), "超限文件应保留原处"
        assert (inbox / "processed" / "small.csv").exists()
        res = json.loads((tmp_path / "inbox" / "inbox_result.json").read_text(encoding="utf-8"))
        kinds = {n["kind"] for n in res["files"]}
        assert "skipped_oversize" in kinds
        config.set_data_dir(None)
