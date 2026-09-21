"""HTTP 路由：参数绑定 + 错误映射，业务规则全部在 services 层。"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from jobhater.db import connect
from jobhater.services.ai import EGRESS_DISCLOSURES, AIService
from jobhater.services.backup import BackupService
from jobhater.services.contacts import ContactsService
from jobhater.services.interview_kit import InterviewKitService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import (
    ApplicationService,
    FeedbackService,
    LifecycleError,
)
from jobhater.services.matching import MatchService
from jobhater.services.profile import ProfileService
from jobhater.services.reminders import RemindersService
from jobhater.services.settings import SettingsService
from jobhater.services.stats import StatsService


def get_con():
    con = connect()
    try:
        yield con
    finally:
        con.close()


# ---------- 请求模型 ----------


class ProfileIn(BaseModel):
    display_name: str
    headline: str | None = None
    phone: str | None = None
    email: str | None = None


class ProfilePatchIn(BaseModel):
    headline: str | None = None
    phone: str | None = None
    email: str | None = None


class EducationIn(BaseModel):
    school: str
    degree: str | None = None
    major: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    gpa: float | None = None
    gpa_note: str | None = None


class ExperienceIn(BaseModel):
    employer: str
    title: str
    kind: str = "full_time"
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class ProjectIn(BaseModel):
    name: str
    role: str | None = None
    url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class SkillIn(BaseModel):
    name: str
    category: str | None = None
    level: int | None = Field(default=None, ge=0, le=5)
    years: float | None = None
    aliases: list[str] = Field(default_factory=list)
    note: str | None = None


class EvidenceIn(BaseModel):
    original_text: str
    normalized_fact: str | None = None
    fact_type: str | None = None
    source_kind: str = "manual"
    source_ref: str | None = None
    section: str | None = None
    confidence: float = 1.0


class EvidenceConfirmIn(BaseModel):
    evidence_ids: list[str]


class PresetIn(BaseModel):
    name: str
    employment_types: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    target_cities: list[str] = Field(default_factory=list)
    remote_ok: bool = False
    salary_min_k: float | None = None
    exclude_employers: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    max_experience_years_required: float | None = None
    min_education: str | None = None
    graduation_year: int | None = None
    accept_incomplete_salary: bool = True
    weights: dict = Field(default_factory=dict)
    gates: dict = Field(default_factory=dict)


class PresetPatch(BaseModel):
    """部分更新：None 字段不修改。"""
    name: str | None = None
    employment_types: list[str] | None = None
    target_roles: list[str] | None = None
    target_cities: list[str] | None = None
    remote_ok: bool | None = None
    salary_min_k: float | None = None
    exclude_employers: list[str] | None = None
    exclude_keywords: list[str] | None = None
    max_experience_years_required: float | None = None
    graduation_year: int | None = None
    accept_incomplete_salary: bool | None = None
    weights: dict | None = None
    gates: dict | None = None


class JobsImportIn(BaseModel):
    jobs: list[dict[str, Any]]
    source_id: str = "manual"


class PasteIn(BaseModel):
    text: str
    url: str | None = None
    save: bool = False  # true=解析并直接入库；false=只出草稿供用户确认


class MatchRunIn(BaseModel):
    profile_id: str
    preset_id: str | None = None
    query: str = ""
    statuses: list[str] = Field(default_factory=lambda: ["active"])
    limit: int = Field(200, ge=1, le=2000)


class ApplicationIn(BaseModel):
    job_id: str
    profile_id: str
    status: str = "discovered"


class TransitionIn(BaseModel):
    status: str
    note: str | None = None


class ConfirmAppliedIn(BaseModel):
    channel: str | None = None


class InterviewIn(BaseModel):
    round: int = 1
    kind: str | None = None
    scheduled_at: str | None = None
    location: str | None = None


class OfferIn(BaseModel):
    application_id: str
    base_salary_k: float = Field(ge=0)
    salary_months: int | None = Field(default=None, ge=0, le=36)
    city: str | None = None
    work_mode: str | None = None
    deadline: str | None = None
    bonus_text: str | None = None
    equity_text: str | None = None
    probation_months: int | None = None
    notes: str | None = None
    benefits: list[str] = Field(default_factory=list)
    custom_dimensions: dict = Field(default_factory=dict)


class OfferCompareIn(BaseModel):
    offer_ids: list[str]


class OfferStatusIn(BaseModel):
    status: str


class FeedbackIn(BaseModel):
    profile_id: str
    kind: str
    job_id: str | None = None
    note: str | None = None


class AIProviderIn(BaseModel):
    adapter_kind: str
    display_name: str
    base_url: str | None = None
    model: str
    timeout_s: int = 60
    max_retries: int = 1
    cost_note: str | None = None
    enabled: bool = False


class APIKeyIn(BaseModel):
    api_key: str


class EnabledIn(BaseModel):
    enabled: bool


class AICompleteIn(BaseModel):
    """AI 任务执行（远程 opt-in 唯一入口）。ack_egress=False 时返回 428 + 披露文本，
    调用方必须先向用户展示披露并取得确认后重试。"""
    task: str
    system: str
    user: str
    ack_egress: bool = False
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    json_schema: dict | None = None


class AwardIn(BaseModel):
    name: str
    issuer: str | None = None
    date: str | None = None
    level: str | None = None
    description: str | None = None


class CertificationIn(BaseModel):
    name: str
    issuer: str | None = None
    issue_date: str | None = None
    expire_date: str | None = None
    credential_id: str | None = None


class InterviewFinishIn(BaseModel):
    outcome: str  # passed / failed / pending / unknown
    notes: str | None = None


class LinkResumeIn(BaseModel):
    resume_version_id: str


class JobStatusIn(BaseModel):
    status: str  # active / expired / archived / rejected
    reason: str | None = None


class ProfileIdIn(BaseModel):
    profile_id: str


class ContactIn(BaseModel):
    name: str
    application_id: str | None = None
    employer_id: str | None = None
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    note: str | None = None


class ContactPatchIn(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    note: str | None = None


class ReminderIn(BaseModel):
    owner_kind: str  # application / interview / offer
    owner_id: str
    due_at: str  # YYYY-MM-DD 或完整 ISO
    title: str
    kind: str | None = None


class ReminderDoneIn(BaseModel):
    done: bool


class SettingIn(BaseModel):
    value: Any  # 任意 JSON；各键的形状契约见 SettingsService.KNOWN_KEYS


class InterviewSessionIn(BaseModel):
    interview_id: str
    mode: str = "mock"
    persona: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)


class TurnIn(BaseModel):
    role: str  # interviewer / candidate
    content: str


class SelfReviewIn(BaseModel):
    scores: dict[str, float]
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    practice_items: list[str] = Field(default_factory=list)


class AIReviewIn(BaseModel):
    ack_egress: bool = False


def _err(e: Exception) -> HTTPException:
    if isinstance(e, (KeyError, LifecycleError)):
        return HTTPException(status_code=404 if isinstance(e, KeyError) else 422, detail=str(e).strip("'\""))
    if isinstance(e, sqlite3.IntegrityError):
        return HTTPException(status_code=409, detail=f"数据冲突：{e}")
    return HTTPException(status_code=500, detail=str(e))


def _csv_response(header: list[str], rows: list[dict], filename: str) -> Response:
    """CSV 导出（UTF-8 + BOM：Excel 直接打开中文不乱码；逗号/引号/换行按 RFC 4180 转义）。"""
    import csv
    import io

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


def register_routes(app: FastAPI) -> None:
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    # ---------- 画像 ----------

    @app.post("/api/profiles")
    def create_profile(body: ProfileIn, con=Depends(get_con)):
        return ProfileService(con).create_profile(
            body.display_name, body.headline, phone=body.phone, email=body.email
        ).model_dump()

    @app.get("/api/profiles")
    def list_profiles(con=Depends(get_con)):
        return [p.model_dump() for p in ProfileService(con).list_profiles()]

    @app.get("/api/profiles/{profile_id}")
    def get_profile(profile_id: str, con=Depends(get_con)):
        ps = ProfileService(con)
        p = ps.get_profile(profile_id)
        if not p:
            raise HTTPException(404, "profile 不存在")
        return ps.match_view(profile_id)

    @app.patch("/api/profiles/{profile_id}")
    def patch_profile(profile_id: str, body: ProfilePatchIn, con=Depends(get_con)):
        try:
            return ProfileService(con).update_profile(
                profile_id, headline=body.headline, phone=body.phone, email=body.email
            ).model_dump()
        except KeyError:
            raise HTTPException(404, "profile 不存在") from None

    @app.post("/api/profiles/{profile_id}/educations")
    def add_education(profile_id: str, body: EducationIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_education(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/experiences")
    def add_experience(profile_id: str, body: ExperienceIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_experience(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/projects")
    def add_project(profile_id: str, body: ProjectIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_project(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/skills")
    def add_skill(profile_id: str, body: SkillIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_skill(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.delete("/api/profiles/{profile_id}/skills/{skill_id}")
    def delete_skill(profile_id: str, skill_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_skill(profile_id, skill_id)
            return {"deleted": skill_id}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/awards")
    def add_award(profile_id: str, body: AwardIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_award(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/certifications")
    def add_certification(profile_id: str, body: CertificationIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_certification(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/evidence")
    def add_evidence(profile_id: str, body: EvidenceIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_evidence(
                profile_id, body.original_text,
                normalized_fact=body.normalized_fact, fact_type=body.fact_type,
                source_kind=body.source_kind, source_ref=body.source_ref,
                section=body.section, confidence=body.confidence,
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/profiles/{profile_id}/evidence")
    def list_evidence(profile_id: str, only_confirmed: bool = False, con=Depends(get_con)):
        return [
            ev.model_dump()
            for ev in ProfileService(con).list_evidence(profile_id, only_confirmed)
        ]

    @app.post("/api/profiles/{profile_id}/evidence/confirm")
    def confirm_evidence(profile_id: str, body: EvidenceConfirmIn, con=Depends(get_con)):
        n = ProfileService(con).confirm_evidence(profile_id, body.evidence_ids)
        return {"confirmed": n}

    @app.delete("/api/profiles/{profile_id}/evidence/{evidence_id}")
    def delete_evidence(profile_id: str, evidence_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_evidence(profile_id, evidence_id)
            return {"deleted": evidence_id}
        except Exception as e:
            raise _err(e) from e

    # ---------- 偏好 ----------

    @app.post("/api/profiles/{profile_id}/presets")
    def create_preset(profile_id: str, body: PresetIn, con=Depends(get_con)):
        try:
            return ProfileService(con).create_preset(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/profiles/{profile_id}/presets")
    def list_presets(profile_id: str, con=Depends(get_con)):
        return [p.model_dump() for p in ProfileService(con).list_presets(profile_id)]

    @app.patch("/api/presets/{preset_id}")
    def patch_preset(preset_id: str, body: PresetPatch, con=Depends(get_con)):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            return ProfileService(con).update_preset(preset_id, **changes).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/presets/{preset_id}/activate")
    def activate_preset(preset_id: str, con=Depends(get_con)):
        ps = ProfileService(con)
        preset = ps.get_preset(preset_id)
        if not preset:
            raise HTTPException(404, "preset 不存在")
        ps.activate_preset(preset.profile_id, preset_id)
        return {"ok": True}

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str, con=Depends(get_con)):
        try:
            ps = ProfileService(con)
            preset = ps.get_preset(preset_id)
            if not preset:
                raise HTTPException(404, "preset 不存在")
            ps.delete_preset(preset.profile_id, preset_id)
            return {"deleted": preset_id}
        except HTTPException:
            raise
        except Exception as e:
            raise _err(e) from e

    # ---------- 岗位 ----------

    @app.post("/api/jobs/import")
    def import_jobs(body: JobsImportIn, con=Depends(get_con)):
        stats = JobService(con).ingest(body.jobs, source_id=body.source_id)
        return stats.model_dump()

    @app.post("/api/import/paste")
    def import_paste(body: PasteIn, con=Depends(get_con)):
        """§7 通用入口：粘贴任意 JD 文本 → 结构化草稿（默认不落库，用户确认后保存）。"""
        from jobhater.services.sources import parse_jd_text

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
        )
        out = {
            "total": svc.count_filtered(
                q,
                cities=[city] if city else None,
                recruitment_types=[recruitment_type] if recruitment_type else None,
                statuses=[status] if status else None,
                near_dup_only=near_dup_only,
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
        from fastapi.responses import Response

        ics = ApplicationService(con).calendar_ics(profile_id)
        return Response(
            content=ics,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="jobhater.ics"'},
        )

    # ---------- 确定性材料生成（本地零 AI 依赖：v1 能力回归） ----------

    @app.post("/api/jobs/{job_id}/materials/resume")
    def materials_resume(job_id: str, body: ProfileIdIn, con=Depends(get_con)):
        from jobhater.services.materials import MaterialsError, MaterialsService

        try:
            return MaterialsService(con).build_job_resume(body.profile_id, job_id)
        except MaterialsError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.post("/api/jobs/{job_id}/materials/cover-letter")
    def materials_cover_letter(job_id: str, body: ProfileIdIn, con=Depends(get_con)):
        from jobhater.services.materials import MaterialsError, MaterialsService
        from jobhater.services.resume import strip_citations

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
        from jobhater.services.materials import MaterialsError, MaterialsService

        try:
            return MaterialsService(con).build_greeting(body.profile_id, job_id)
        except MaterialsError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/jobs/{job_id}/materials/interview-questions")
    def materials_interview_questions(job_id: str, profile_id: str, con=Depends(get_con)):
        from jobhater.services.materials import MaterialsService

        try:
            return MaterialsService(con).build_interview_questions(profile_id, job_id)
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/jobs/{job_id}/materials/upskill-plan")
    def materials_upskill_plan(job_id: str, profile_id: str, con=Depends(get_con)):
        from jobhater.services.materials import MaterialsService

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

    # ---------- 投递生命周期 ----------

    @app.post("/api/applications")
    def create_application(body: ApplicationIn, con=Depends(get_con)):
        try:
            app_id = ApplicationService(con).create(
                body.job_id, body.profile_id, status=body.status
            )
            return {"id": app_id}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications")
    def list_applications(profile_id: str, con=Depends(get_con)):
        return ApplicationService(con).list(profile_id)

    @app.get("/api/applications/{app_id}")
    def get_application(app_id: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).get(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/resume")
    def link_resume(app_id: str, body: LinkResumeIn, con=Depends(get_con)):
        try:
            ApplicationService(con).link_resume(app_id, body.resume_version_id)
            return ApplicationService(con).get(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/transition")
    def transition(app_id: str, body: TransitionIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).transition(app_id, body.status, note=body.note)
        except LifecycleError as e:
            raise _err(e) from e  # 状态机语义错误：422 并保留完整信息
        except ValueError as e:
            # 非法状态串（ApplicationStatus 枚举外）：422 且不回显内部异常文本
            raise HTTPException(422, f"非法投递状态：{body.status}") from e
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/confirm-applied")
    def confirm_applied(app_id: str, body: ConfirmAppliedIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).confirm_applied(app_id, channel=body.channel)
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications/{app_id}/events")
    def app_events(app_id: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).events(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/interviews")
    def schedule_interview(app_id: str, body: InterviewIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).schedule_interview(
                app_id, **body.model_dump()
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications/{app_id}/interviews")
    def list_interviews(app_id: str, con=Depends(get_con)):
        return [i.model_dump() for i in ApplicationService(con).list_interviews(app_id)]

    @app.post("/api/interviews/{interview_id}/finish")
    def finish_interview(interview_id: str, body: InterviewFinishIn, con=Depends(get_con)):
        try:
            ApplicationService(con).finish_interview(
                interview_id, outcome=body.outcome, notes=body.notes
            )
            return {"ok": True, "interview_id": interview_id, "outcome": body.outcome}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/offers")
    def add_offer(body: OfferIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).add_offer(
                body.application_id, **body.model_dump(exclude={"application_id"})
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/offers")
    def list_offers(profile_id: str, con=Depends(get_con)):
        return ApplicationService(con).list_offers(profile_id)

    @app.post("/api/offers/compare")
    def compare_offers(body: OfferCompareIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).compare_offers(body.offer_ids)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/offers/{offer_id}/status")
    def set_offer_status(offer_id: str, body: OfferStatusIn, con=Depends(get_con)):
        try:
            ApplicationService(con).set_offer_status(offer_id, body.status)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    # ---------- 简历 ----------

    from jobhater.services.resume import (  # 此导入在函数内：避免模块级循环依赖
        ResumeError,
        ResumeService,
    )

    @app.post("/api/resumes/generate")
    def generate_master(body: dict, con=Depends(get_con)):
        try:
            rs = ResumeService(con)
            rid, vid = rs.build_master_from_profile(body["profile_id"])
            report = rs.factcheck(vid)
            return {"resume_id": rid, "version_id": vid, "factcheck": report}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/resumes")
    def list_resumes(profile_id: str, con=Depends(get_con)):
        return ResumeService(con).list_resumes(profile_id)

    @app.get("/api/resumes/{resume_id}/versions")
    def list_versions(resume_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).list_versions(resume_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}")
    def get_version(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).get_version(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.post("/api/resumes/{resume_id}/versions")
    def new_version(resume_id: str, body: dict, con=Depends(get_con)):
        try:
            rs = ResumeService(con)
            vid = rs.commit_version(
                resume_id, body.get("sections") or {},
                bullets_provenance=body.get("bullets_provenance"),
                note=body.get("note"),
            )
            return {"version_id": vid, "factcheck": rs.factcheck(vid)}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/resume-versions/{version_id}/factcheck")
    def run_factcheck(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).factcheck(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.post("/api/resume-versions/{version_id}/final")
    def mark_final(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).mark_final(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}/export")
    def export_version(
        version_id: str, fmt: str = "md", template: str = "classic", con=Depends(get_con)
    ):
        if fmt not in ("md", "json", "json-resume", "html", "pdf", "docx"):
            raise HTTPException(422, "fmt ∈ md/json/json-resume/html/pdf/docx")
        if template not in ("classic", "compact"):
            raise HTTPException(422, "template ∈ classic/compact")
        try:
            path = ResumeService(con).export_file(version_id, fmt, template=template)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e
        from fastapi.responses import FileResponse, PlainTextResponse

        if fmt in ("md", "html", "json", "json-resume"):
            return PlainTextResponse(
                path.read_text(encoding="utf-8"),
                media_type="text/plain; charset=utf-8",
            )
        return FileResponse(path, filename=path.name)

    @app.get("/api/resume-versions/{version_id}/json-resume")
    def get_json_resume(version_id: str, con=Depends(get_con)):
        """JSON Resume 开放标准视图（结构化 JSON 响应，非文件下载）。"""
        try:
            return ResumeService(con).export_json_resume(version_id)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/profiles/{profile_id}/import/json-resume")
    def import_json_resume(profile_id: str, body: dict, con=Depends(get_con)):
        """从 JSON Resume 标准文件导入画像簇（技能/经历/教育/项目）。
        导入与手工建档同级——同样要经证据确认与 factcheck，无免检特权。
        body 即标准 resume JSON（或 {"resume": {...}} 包裹）。"""
        from jobhater.services.resume import ResumeError

        data = body.get("resume") if isinstance(body.get("resume"), dict) else body
        if not isinstance(data, dict) or not (
            data.get("basics") or data.get("work") or data.get("skills")
            or data.get("education") or data.get("projects")
        ):
            raise HTTPException(422, "不是可识别的 JSON Resume 文档（缺 basics/work/skills/education/projects）")
        try:
            return ResumeService(con).import_json_resume(profile_id, data)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}/diff")
    def diff_versions(version_id: str, against: str, con=Depends(get_con)):
        try:
            return ResumeService(con).diff_versions(against, version_id)
        except ResumeError as e:
            raise _err(e) from e

    # ---------- 反馈 ----------

    @app.post("/api/feedback")
    def record_feedback(body: FeedbackIn, con=Depends(get_con)):
        FeedbackService(con).record(body.profile_id, body.kind, job_id=body.job_id, note=body.note)
        return {"ok": True}

    @app.get("/api/feedback")
    def feedback_history(profile_id: str, con=Depends(get_con)):
        return FeedbackService(con).history(profile_id)

    @app.delete("/api/feedback")
    def reset_feedback(profile_id: str, con=Depends(get_con)):
        return {"deleted": FeedbackService(con).reset(profile_id)}

    # ---------- AI ----------

    @app.get("/api/ai/providers")
    def list_ai_providers(con=Depends(get_con)):
        return AIService(con).list_providers()

    @app.post("/api/ai/providers")
    def add_ai_provider(body: AIProviderIn, con=Depends(get_con)):
        pid = AIService(con).add_provider(
            adapter_kind=body.adapter_kind, display_name=body.display_name,
            base_url=body.base_url, model=body.model, timeout_s=body.timeout_s,
            max_retries=body.max_retries, cost_note=body.cost_note, enabled=body.enabled,
        )
        return {"id": pid}

    @app.post("/api/ai/providers/{provider_id}/key")
    def set_api_key(provider_id: str, body: APIKeyIn, con=Depends(get_con)):
        try:
            AIService(con).set_api_key(provider_id, body.api_key)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/ai/providers/{provider_id}/enabled")
    def set_ai_enabled(provider_id: str, body: EnabledIn, con=Depends(get_con)):
        try:
            AIService(con).set_enabled(provider_id, body.enabled)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/ai/egress")
    def ai_egress(task: str, con=Depends(get_con)):
        if task not in EGRESS_DISCLOSURES:
            raise HTTPException(422, f"未知任务类型，可选：{sorted(EGRESS_DISCLOSURES)}")
        return {"task": task, "disclosure": AIService(con).egress_disclosure(task)}

    @app.get("/api/ai/tasks")
    def ai_tasks(con=Depends(get_con)):
        """任务目录：label + 当前是否可用 + 各自出境披露。"""
        return AIService(con).list_tasks()

    @app.post("/api/ai/complete")
    def ai_complete(body: AICompleteIn, con=Depends(get_con)):
        """AI 任务执行（远程 opt-in 唯一入口）。

        - 本地模式 → 200 {executed:false, reason:"local_mode"}（诚实状态，非错误）
        - 未确认披露 → 428 {disclosure}（调用方先展示、用户确认后带 ack_egress 重试）
        """
        from jobhater.services.ai import EgressNotAcknowledged

        try:
            return AIService(con).run_task(
                body.task, body.system, body.user,
                ack_egress=body.ack_egress, max_tokens=body.max_tokens,
                temperature=body.temperature, json_schema=body.json_schema,
            )
        except EgressNotAcknowledged as e:
            raise HTTPException(428, detail={"disclosure": str(e), "task": body.task}) from e
        except KeyError as e:
            raise _err(e) from e
        except Exception as e:
            raise _err(e) from e

    # ================= 联系人（轻量 CRM） =================

    @app.get("/api/contacts")
    def list_contacts(
        application_id: str | None = None, employer_id: str | None = None,
        q: str | None = None, con=Depends(get_con),
    ):
        return ContactsService(con).list(
            application_id=application_id, employer_id=employer_id, q=q
        )

    @app.post("/api/contacts")
    def add_contact(body: ContactIn, con=Depends(get_con)):
        try:
            return ContactsService(con).add(
                body.name, application_id=body.application_id, employer_id=body.employer_id,
                role=body.role, phone=body.phone, email=body.email,
                wechat=body.wechat, note=body.note,
            )
        except ValueError as e:
            raise _err(e) from e

    @app.get("/api/contacts/{contact_id}")
    def get_contact(contact_id: int, con=Depends(get_con)):
        c = ContactsService(con).get(contact_id)
        if c is None:
            raise HTTPException(404, f"联系人不存在: {contact_id}")
        return c

    @app.patch("/api/contacts/{contact_id}")
    def patch_contact(contact_id: int, body: ContactPatchIn, con=Depends(get_con)):
        try:
            return ContactsService(con).update(
                contact_id, name=body.name, role=body.role, phone=body.phone,
                email=body.email, wechat=body.wechat, note=body.note,
            )
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/contacts/{contact_id}")
    def delete_contact(contact_id: int, con=Depends(get_con)):
        try:
            ContactsService(con).delete(contact_id)
            return {"ok": True}
        except ValueError as e:
            raise _err(e) from e

    # ================= 提醒与跟进建议 =================

    @app.get("/api/reminders")
    def list_reminders(
        include_done: bool = False, done: bool = False,
        owner_kind: str | None = None, owner_id: str | None = None,
        con=Depends(get_con),
    ):
        return RemindersService(con).list(
            done=done, include_done=include_done, owner_kind=owner_kind, owner_id=owner_id
        )

    @app.get("/api/reminders/suggestions")
    def reminder_suggestions(profile_id: str | None = None, con=Depends(get_con)):
        """确定性规则推导的跟进建议（不落库，采纳后 POST 成为真实提醒）。"""
        return RemindersService(con).suggestions(profile_id=profile_id)

    @app.post("/api/reminders")
    def add_reminder(body: ReminderIn, con=Depends(get_con)):
        try:
            return RemindersService(con).create(
                body.owner_kind, body.owner_id, body.due_at, body.title, kind=body.kind
            )
        except ValueError as e:
            raise _err(e) from e

    @app.patch("/api/reminders/{reminder_id}")
    def patch_reminder(reminder_id: int, body: ReminderDoneIn, con=Depends(get_con)):
        try:
            return RemindersService(con).set_done(reminder_id, body.done)
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/reminders/{reminder_id}")
    def delete_reminder(reminder_id: int, con=Depends(get_con)):
        try:
            RemindersService(con).delete(reminder_id)
            return {"ok": True}
        except ValueError as e:
            raise _err(e) from e

    # ================= 用户设置（本地偏好 KV） =================

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

    # ================= 模拟面试练习器 =================

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
        from jobhater.services.ai import EgressNotAcknowledged

        try:
            return InterviewKitService(con).review_ai(session_id, ack_egress=body.ack_egress)
        except EgressNotAcknowledged as e:
            raise HTTPException(428, detail={"disclosure": str(e), "task": "interview_review"}) from e
        except ValueError as e:
            raise _err(e) from e

    # ================= 统计与洞察 =================

    @app.get("/api/stats/overview")
    def stats_overview(profile_id: str | None = None, con=Depends(get_con)):
        return StatsService(con).overview(profile_id)

    @app.get("/api/stats/salary")
    def stats_salary(city: str | None = None, con=Depends(get_con)):
        return StatsService(con).salary_insights(city=city)

    # ================= ATS 简历-JD 匹配报告（确定性） =================

    @app.get("/api/jobs/{job_id}/ats-scan")
    def ats_scan(job_id: str, resume_version_id: str = Query(alias="resume_version_id"), con=Depends(get_con)):
        from jobhater.services.ats_scan import ATSScanService

        try:
            return ATSScanService(con).scan(job_id, resume_version_id)
        except ValueError as e:
            raise _err(e) from e

    # ================= 投递标签（0004） =================

    @app.post("/api/applications/{app_id}/tags/{tag}")
    def add_app_tag(app_id: str, tag: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).add_tag(app_id, tag)
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/applications/{app_id}/tags/{tag}")
    def remove_app_tag(app_id: str, tag: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).remove_tag(app_id, tag)
        except ValueError as e:
            raise _err(e) from e

    # ================= CSV 导出（Excel 兼容，带 BOM） =================

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

    # ================= 全量备份与恢复 =================

    @app.get("/api/backup/download")
    def backup_download(con=Depends(get_con)):
        from jobhater.config import db_path

        data, filename = BackupService(con, db_path()).snapshot()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/backup/restore")
    async def backup_restore(file: UploadFile, con=Depends(get_con)):
        from jobhater.config import db_path

        data = await file.read()
        try:
            return BackupService(con, db_path()).restore(data)
        except ValueError as e:
            raise _err(e) from e
