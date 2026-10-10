"""岗位域端点：岗位/导入/信源/日历/材料生成/匹配/ATS 报告。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Response

from jobhater.api.deps import _err, get_con
from jobhater.api.schemas import (
    JobsImportIn,
    JobStatusIn,
    MatchRunIn,
    PasteIn,
    ProfileIdIn,
)
from jobhater.services.ats_scan import ATSScanService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService
from jobhater.services.matching import MatchService
from jobhater.services.materials import MaterialsError, MaterialsService
from jobhater.services.profile import ProfileService
from jobhater.services.resume import strip_citations
from jobhater.services.sources import parse_jd_text


def register(app: FastAPI) -> None:
    # ---------- 岗位 ----------

    @app.post("/api/jobs/import")
    def import_jobs(body: JobsImportIn, con=Depends(get_con)):
        try:
            stats = JobService(con).ingest(body.jobs, source_id=body.source_id)
            return stats.model_dump()
        except Exception as e:
            # SEC-004：未预期异常走 _err → 500 通用文案 + logging.exception 落 stderr
            raise _err(e) from e

    @app.post("/api/import/paste")
    def import_paste(body: PasteIn, con=Depends(get_con)):
        """§7 通用入口：粘贴任意 JD 文本 → 结构化草稿（默认不落库，用户确认后保存）。"""
        try:
            draft = parse_jd_text(body.text, url=body.url)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        saved = None
        if body.save:
            if not draft.get("title") or not draft.get("company"):
                raise HTTPException(
                    422, "直接入库需要 title 与 company；请以草稿模式确认补全后再保存"
                )
            raw = {k: v for k, v in draft.items()
                   if k not in ("parse_notes", "needs_review_fields")}
            stats = JobService(con).ingest([raw], source_id="manual")
            saved = stats.model_dump()
        return {"draft": draft, "saved": saved}

    @app.get("/api/jobs")
    def search_jobs(
        q: str = "", city: str | None = None, recruitment_type: str | None = None,
        status: str | None = None, near_dup_only: bool = False,
        sort: str = "recent", profile_id: str | None = None,
        since: str | None = None,
        graduation_year: int | None = Query(None, ge=2000, le=2100),
        source_id: str | None = None,
        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
        con=Depends(get_con),
    ):
        if sort not in ("recent", "match"):
            raise HTTPException(422, "sort 仅支持 recent | match")
        if sort == "match" and not profile_id:
            raise HTTPException(422, "按匹配排序需带 profile_id")
        svc = JobService(con)
        jobs = svc.search(
            q,
            cities=[city] if city else None,
            recruitment_types=[recruitment_type] if recruitment_type else None,
            statuses=[status] if status else None,
            near_dup_only=near_dup_only,
            limit=limit,
            offset=offset,
            ranked_profile_id=profile_id if sort == "match" else None,
            since=since,
            graduation_year=graduation_year,
            source_id=source_id,
        )
        out = {
            "total": svc.count_filtered(
                q,
                cities=[city] if city else None,
                recruitment_types=[recruitment_type] if recruitment_type else None,
                statuses=[status] if status else None,
                near_dup_only=near_dup_only,
                since=since,
                graduation_year=graduation_year,
                source_id=source_id,
            ),
            "items": [j.model_dump() for j in jobs],
        }
        if sort == "match" and profile_id:
            # 用已存的最近一次匹配结果展示分数（engine 版本一致），不在此重复计算——
            # 匹配是显式动作（总览「重新匹配排序」/ /api/match/run），翻页只读结果
            ms = MatchService(con)
            out["match_by_id"] = {
                j.id: m.model_dump()
                for j in jobs
                if (m := ms.latest_for_job(j.id, profile_id)) is not None
            }
            out["match_stale_hint"] = (
                "部分岗位还没有匹配结果（先在总览页运行匹配）——无结果的岗位按入库时间排在后面。"
                if len(out["match_by_id"]) < len(jobs) else ""
            )
        return out

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, profile_id: str | None = None, con=Depends(get_con)):
        job = JobService(con).get(job_id)
        if not job:
            raise HTTPException(404, "job 不存在")
        out = job.model_dump()
        if profile_id:
            out["match"] = (
                MatchService(con)
                .latest_for_job(job_id, profile_id)
                .model_dump() if MatchService(con).latest_for_job(job_id, profile_id) else None
            )
        return out

    @app.patch("/api/jobs/{job_id}/status")
    def set_job_status(job_id: str, body: JobStatusIn, con=Depends(get_con)):
        try:
            JobService(con).set_status(job_id, body.status, body.reason)
            return JobService(con).get(job_id).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/sources")
    def list_sources(con=Depends(get_con)):
        return [s.model_dump() for s in JobService(con).list_sources()]

    @app.get("/api/calendar/ics")
    def calendar_ics(profile_id: str, con=Depends(get_con)):
        """投递截止+面试排期 → iCalendar（RFC 5545，导入系统/Google 日历）。"""
        ics = ApplicationService(con).calendar_ics(profile_id)
        return Response(
            content=ics,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="jobhater.ics"'},
        )

    # ---------- 确定性材料生成（本地零 AI 依赖：v1 能力回归） ----------

    @app.post("/api/jobs/{job_id}/materials/resume")
    def materials_resume(job_id: str, body: ProfileIdIn, con=Depends(get_con)):
        try:
            return MaterialsService(con).build_job_resume(body.profile_id, job_id)
        except MaterialsError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.post("/api/jobs/{job_id}/materials/cover-letter")
    def materials_cover_letter(job_id: str, body: ProfileIdIn, con=Depends(get_con)):
        try:
            letter = MaterialsService(con).build_cover_letter(body.profile_id, job_id)
            # content_md 保留 [ev:*] 锚点（审计/自检用）；content_display 为可直接投递的人面版本
            out = letter.model_dump()
            out["content_display"] = strip_citations(out["content_md"])
            return out
        except MaterialsError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.post("/api/jobs/{job_id}/materials/greeting")
    def materials_greeting(job_id: str, body: ProfileIdIn, con=Depends(get_con)):
        try:
            return MaterialsService(con).build_greeting(body.profile_id, job_id)
        except MaterialsError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/jobs/{job_id}/materials/interview-questions")
    def materials_interview_questions(job_id: str, profile_id: str, con=Depends(get_con)):
        try:
            return MaterialsService(con).build_interview_questions(profile_id, job_id)
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/jobs/{job_id}/materials/upskill-plan")
    def materials_upskill_plan(job_id: str, profile_id: str, con=Depends(get_con)):
        try:
            return MaterialsService(con).build_upskill_plan(profile_id, job_id)
        except KeyError as e:
            raise _err(e) from e

    # ---------- 匹配 ----------

    @app.post("/api/match/run")
    def run_match(body: MatchRunIn, con=Depends(get_con)):
        ps = ProfileService(con)
        preset = (
            ps.get_preset(body.preset_id) if body.preset_id else ps.active_preset(body.profile_id)
        )
        if preset is None:
            raise HTTPException(422, "该画像没有求职偏好（preset），请先创建")
        jobs = JobService(con).search(
            body.query, statuses=body.statuses or None, limit=body.limit
        )
        outcomes = MatchService(con).rank_jobs(ps.match_view(body.profile_id), preset, jobs)
        return {
            "preset": preset.name,
            "evaluated": len(outcomes),
            "eligible": sum(1 for o in outcomes if o.eligible),
            "results": [o.model_dump() for o in outcomes[:100]],
        }

    # ---------- ATS 简历-JD 匹配报告（确定性） ----------

    @app.get("/api/jobs/{job_id}/ats-scan")
    def ats_scan(job_id: str, resume_version_id: str = Query(alias="resume_version_id"), con=Depends(get_con)):
        try:
            return ATSScanService(con).scan(job_id, resume_version_id)
        except ValueError as e:
            raise _err(e) from e
