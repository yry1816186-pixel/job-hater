"""API 加固测试（Phase B low 批）：

- SEC-004：500 分支通用文案 + 完整异常经结构化日志落 stderr（不泄露内部细节）；
- SEC-007：上传端点读取前 Content-Length 预检 + 分块累计硬上限（413）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

# ==================== SEC-004：500 通用文案 + 异常可观测 ====================


def test_internal_error_returns_generic_detail_and_logs(tmp_path, monkeypatch, caplog):
    """SEC-004：500 detail 固定通用文案；完整堆栈经 logging.exception 进 JSON 日志流。"""
    from fastapi.testclient import TestClient

    import jobhater.services.jobs as jobs_mod
    from jobhater import config
    from jobhater.api.app import create_app

    config.set_data_dir(tmp_path)

    def _boom(self, raw_jobs, source_id="manual"):
        raise RuntimeError("内部细节：C:/Users/secret/path 与 SELECT * FROM 的 SQL 片段")

    monkeypatch.setattr(jobs_mod.JobService, "ingest", _boom)
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            with caplog.at_level("ERROR", logger="jobhater.api"):
                r = client.post("/api/jobs/import", json={"jobs": [{"title": "t", "company": "c"}]})
        assert r.status_code == 500
        assert r.json()["detail"] == "服务器内部错误"
        assert "secret" not in r.text and "SELECT" not in r.text
        recs = [x for x in caplog.records if x.getMessage() == "api.unhandled_error"]
        assert recs, "未捕获异常必须留下结构化错误日志"
        assert recs[0].error_type == "RuntimeError"
        assert recs[0].exc_info, "logging.exception 语义：日志携带完整堆栈"
    finally:
        config.set_data_dir(None)


# ==================== SEC-007：上传大小上限（预检 + 分块硬限） ====================


class _FakeUpload:
    """UploadFile 替身：按序吐 chunk，记录是否被读取。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.read_called = False

    async def read(self, n: int = -1) -> bytes:
        self.read_called = True
        return self._chunks.pop(0) if self._chunks else b""


class _FakeRequest:
    def __init__(self, content_length: str | None):
        self.headers = {"content-length": content_length} if content_length else {}


def test_read_upload_chunked_cap_413():
    """SEC-007：Content-Length 缺席/说谎时分块累计仍硬限——超限 413。"""
    from jobhater.api.deps import read_upload

    fake = _FakeUpload([b"x" * 8, b"y" * 8])  # 16B > 10B 上限
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_upload(_FakeRequest(None), fake, 10))
    assert ei.value.status_code == 413
    assert fake.read_called


def test_read_upload_content_length_precheck_short_circuits():
    """SEC-007：Content-Length 超限（含 multipart 余量）直接 413——不读 body。"""
    from jobhater.api.deps import read_upload

    fake = _FakeUpload([b"x" * 8])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_upload(_FakeRequest(str(1 << 20)), fake, 10))
    assert ei.value.status_code == 413
    assert fake.read_called is False, "预检命中时不得读入 body"


def test_read_upload_within_limit_passthrough():
    from jobhater.api.deps import read_upload

    fake = _FakeUpload([b"abc", b"de"])
    data = asyncio.run(read_upload(_FakeRequest("1024"), fake, 10))
    assert data == b"abcde"


def test_parse_resume_oversize_rejected_413_before_parse(tmp_path):
    """SEC-007：简历上传超 10MB → 413（读取期生效，无需等待解析）。"""
    from fastapi.testclient import TestClient

    from jobhater import config
    from jobhater.api.app import create_app

    config.set_data_dir(tmp_path)
    try:
        with TestClient(create_app()) as client:
            r = client.post(
                "/api/onboarding/parse-resume",
                files={"file": ("big.txt", b"x" * (10 * 1024 * 1024 + 1), "text/plain")},
            )
        assert r.status_code == 413
        assert "10MB" in r.json()["detail"]
    finally:
        config.set_data_dir(None)


# ==================== SEC-005：扩展 popup 组装面 ====================


def test_extension_popup_no_innerhtml():
    """SEC-005：popup 对页面可控数据（抓取 URL/错误消息/表单 label）一律
    createElement+textContent 组装——innerHTML 0 命中。"""
    src = (Path(__file__).resolve().parent.parent / "extension" / "popup" / "popup.js").read_text(
        encoding="utf-8"
    )
    assert "innerHTML" not in src
