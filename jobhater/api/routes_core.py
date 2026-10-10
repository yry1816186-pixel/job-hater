"""应用级核心端点：健康检查/设置/示例数据/统计/面试练习器/CSV 导出/备份恢复。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

import csv
import io
import logging
import os
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from jobhater import config
from jobhater.api.deps import _err, get_con
from jobhater.api.schemas import (
    AIReviewIn,
    InterviewSessionIn,
    SelfReviewIn,
    SettingIn,
    TurnIn,
)
from jobhater.db import connect, migration_files
from jobhater.metrics import METRICS_CONTENT_TYPE, prometheus_text
from jobhater.services.ai import EgressNotAcknowledged
from jobhater.services.backup import BackupService
from jobhater.services.demo import DemoService
from jobhater.services.interview_kit import InterviewKitService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService
from jobhater.services.settings import SettingsService
from jobhater.services.stats import StatsService

logger = logging.getLogger("jobhater.health")


def _csv_response(header: list[str], rows: list[dict], filename: str) -> Response:
    """CSV 导出（UTF-8 + BOM：Excel 直接打开中文不乱码；逗号/引号/换行按 RFC 4180 转义）。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([
            ";".join(v) if isinstance((v := r.get(k)), list) else v
            for k in header
        ])
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health():
        """深度探活（BC-008）：DB 连通+SELECT 1 / schema 版本一致 / data_dir 可写。

        任一失败 → 503 + checks 明细（compose healthcheck 与前端错误横幅消费）；
        锁竞争时快速失败（busy_timeout 收紧到 40ms，单次检查预算 < 50ms）。
        """
        checks: dict[str, str] = {"db": "fail", "schema": "fail", "data_dir": "fail"}
        con = None
        try:
            con = connect()
            con.execute("PRAGMA busy_timeout=40")
            con.execute("SELECT 1").fetchone()
            checks["db"] = "ok"
            expected = {v for v, _, _ in migration_files()}
            applied = {int(r["version"]) for r in con.execute("SELECT version FROM schema_migrations")}
            checks["schema"] = "ok" if applied == expected else "fail"
        except (sqlite3.Error, OSError) as e:
            logger.warning("health.check_db_failed", extra={"error": str(e)})
        finally:
            if con is not None:
                con.close()
        try:
            checks["data_dir"] = "ok" if os.access(config.data_dir(), os.W_OK) else "fail"
        except OSError as e:
            logger.warning("health.check_data_dir_failed", extra={"error": str(e)})
        if any(v != "ok" for v in checks.values()):
            return JSONResponse(status_code=503, content={"ok": False, "checks": checks})
        return {"ok": True, "version": app.version, "checks": checks}

    # ---------- 进程内自监控指标（BC-010） ----------

    def _metrics() -> Response:
        """Prometheus 文本暴露（version=0.0.4）：内存累计快照，不持久化不外发。

        经 BC-001/002 中间件同等防护（仅本机回环 Host 可读）；/metrics 为
        Prometheus 惯例路径、/api/metrics 为本仓 API 命名空间路径（同一处理器）。
        """
        return Response(content=prometheus_text(), media_type=METRICS_CONTENT_TYPE)

    app.add_api_route("/metrics", _metrics, methods=["GET"])
    app.add_api_route("/api/metrics", _metrics, methods=["GET"])

    # ---------- 用户设置（本地偏好 KV） ----------

    @app.get("/api/settings")
    def list_settings(con=Depends(get_con)):
        return SettingsService(con).list()

    @app.get("/api/settings/{key}")
    def get_setting(key: str, con=Depends(get_con)):
        return {"key": key, "value": SettingsService(con).get(key)}

    @app.put("/api/settings/{key}")
    def put_setting(key: str, body: SettingIn, con=Depends(get_con)):
        SettingsService(con).set(key, body.value)
        return {"key": key, "value": body.value}

    @app.delete("/api/settings/{key}")
    def delete_setting(key: str, con=Depends(get_con)):
        return {"deleted": SettingsService(con).delete(key)}

    # ---------- 示例数据 ----------

    @app.get("/api/demo/status")
    def demo_status(con=Depends(get_con)):
        return DemoService(con).status()

    @app.post("/api/demo/seed")
    def demo_seed(con=Depends(get_con)):
        return DemoService(con).seed()

    @app.post("/api/demo/clear")
    def demo_clear(con=Depends(get_con)):
        return DemoService(con).clear()

    # ---------- 统计与洞察 ----------

    @app.get("/api/stats/overview")
    def stats_overview(profile_id: str | None = None, con=Depends(get_con)):
        return StatsService(con).overview(profile_id)

    @app.get("/api/stats/salary")
    def stats_salary(city: str | None = None, con=Depends(get_con)):
        return StatsService(con).salary_insights(city=city)

    # ---------- 模拟面试练习器 ----------

    @app.get("/api/applications/{app_id}/interview-sessions")
    def list_iv_sessions(app_id: str, con=Depends(get_con)):
        return [s.model_dump() for s in InterviewKitService(con).list_sessions(app_id)]

    @app.post("/api/interview-sessions")
    def create_iv_session(body: InterviewSessionIn, con=Depends(get_con)):
        try:
            return InterviewKitService(con).create_session(
                body.interview_id, mode=body.mode, persona=body.persona,
                difficulty=body.difficulty,
            ).model_dump()
        except ValueError as e:
            raise _err(e) from e

    @app.get("/api/interview-sessions/{session_id}")
    def get_iv_session(session_id: str, con=Depends(get_con)):
        try:
            return InterviewKitService(con).get_session(session_id).model_dump()
        except ValueError as e:
            raise _err(e) from e

    @app.post("/api/interview-sessions/{session_id}/turns")
    def add_iv_turn(session_id: str, body: TurnIn, con=Depends(get_con)):
        try:
            return InterviewKitService(con).add_turn(
                session_id, body.role, body.content
            ).model_dump()
        except ValueError as e:
            raise _err(e) from e

    @app.post("/api/interview-sessions/{session_id}/end")
    def end_iv_session(session_id: str, con=Depends(get_con)):
        try:
            return InterviewKitService(con).end_session(session_id).model_dump()
        except ValueError as e:
            raise _err(e) from e

    @app.get("/api/interview-sessions/{session_id}/stats")
    def iv_session_stats(session_id: str, con=Depends(get_con)):
        try:
            return InterviewKitService(con).stats(session_id)
        except ValueError as e:
            raise _err(e) from e

    @app.get("/api/interview-sessions/{session_id}/reviews")
    def list_iv_reviews(session_id: str, con=Depends(get_con)):
        return [r.model_dump() for r in InterviewKitService(con).list_reviews(session_id)]

    @app.post("/api/interview-sessions/{session_id}/reviews/self")
    def iv_review_self(session_id: str, body: SelfReviewIn, con=Depends(get_con)):
        try:
            return InterviewKitService(con).review_self(
                session_id, scores=body.scores, strengths=body.strengths,
                gaps=body.gaps, practice_items=body.practice_items,
            ).model_dump()
        except ValueError as e:
            raise _err(e) from e

    @app.post("/api/interview-sessions/{session_id}/reviews/ai")
    def iv_review_ai(session_id: str, body: AIReviewIn, con=Depends(get_con)):
        """AI 面试复盘（远程 opt-in，428 语义同 /api/ai/complete）。"""
        try:
            return InterviewKitService(con).review_ai(session_id, ack_egress=body.ack_egress)
        except EgressNotAcknowledged as e:
            raise HTTPException(428, detail={"disclosure": str(e), "task": "interview_review"}) from e
        except ValueError as e:
            raise _err(e) from e

    # ---------- CSV 导出（Excel 兼容，带 BOM） ----------

    @app.get("/api/export/applications.csv")
    def export_applications_csv(profile_id: str, con=Depends(get_con)):
        rows = ApplicationService(con).list(profile_id)
        header = ["id", "status", "job_title", "employer_name", "job_city",
                  "applied_at", "apply_channel", "tags", "notes", "created_at", "updated_at"]
        return _csv_response(header, rows, "applications.csv")

    @app.get("/api/export/jobs.csv")
    def export_jobs_csv(con=Depends(get_con)):
        jobs = JobService(con).search("", limit=10000)
        rows = [{
            "id": j.id, "title": j.title, "employer_name": j.employer_name, "city": j.city,
            "salary_text": j.salary_text, "deadline": j.deadline,
            "published_at": j.published_at, "status": j.status.value,
            "canonical_url": j.canonical_url,
        } for j in jobs]
        header = ["id", "title", "employer_name", "city", "salary_text", "deadline",
                  "published_at", "status", "canonical_url"]
        return _csv_response(header, rows, "jobs.csv")

    # ---------- 全量备份与恢复 ----------

    @app.get("/api/backup/download")
    def backup_download(con=Depends(get_con)):
        data, filename = BackupService(con, config.db_path()).snapshot()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/backup/restore")
    async def backup_restore(file: UploadFile, con=Depends(get_con)):
        data = await file.read()
        try:
            return BackupService(con, config.db_path()).restore(data)
        except ValueError as e:
            raise _err(e) from e
