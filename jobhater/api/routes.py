"""HTTP 路由：参数绑定 + 错误映射，业务规则全部在 services 层。"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from jobhater.db import connect
from jobhater.services.ai import EGRESS_DISCLOSURES, AIService
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import (
    ApplicationService,
    FeedbackService,
    LifecycleError,
)
from jobhater.services.matching import MatchService
from jobhater.services.profile import ProfileService


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
    base_salary_k: float
    salary_months: int | None = None
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


def _err(e: Exception) -> HTTPException:
    if isinstance(e, (KeyError, LifecycleError)):
        return HTTPException(status_code=404 if isinstance(e, KeyError) else 422, detail=str(e).strip("'\""))
    if isinstance(e, sqlite3.IntegrityError):
        return HTTPException(status_code=409, detail=f"数据冲突：{e}")
    return HTTPException(status_code=500, detail=str(e))


def register_routes(app: FastAPI) -> None:
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    # ---------- 画像 ----------

    @app.post("/api/profiles")
    def create_profile(body: ProfileIn, con=Depends(get_con)):
        return ProfileService(con).create_profile(body.display_name, body.headline).model_dump()

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

    # ---------- 岗位 ----------

    @app.post("/api/jobs/import")
    def import_jobs(body: JobsImportIn, con=Depends(get_con)):
        stats = JobService(con).ingest(body.jobs, source_id=body.source_id)
        return stats.model_dump()

    @app.get("/api/jobs")
    def search_jobs(
        q: str = "", city: str | None = None, recruitment_type: str | None = None,
        status: str | None = None, near_dup_only: bool = False,
        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
        con=Depends(get_con),
    ):
        svc = JobService(con)
        jobs = svc.search(
            q,
            cities=[city] if city else None,
            recruitment_types=[recruitment_type] if recruitment_type else None,
            statuses=[status] if status else None,
            near_dup_only=near_dup_only,
            limit=limit,
            offset=offset,
        )
        return {
            "total": svc.count(),
            "items": [j.model_dump() for j in jobs],
        }

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

    @app.get("/api/sources")
    def list_sources(con=Depends(get_con)):
        return [s.model_dump() for s in JobService(con).list_sources()]

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

    @app.post("/api/applications/{app_id}/transition")
    def transition(app_id: str, body: TransitionIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).transition(app_id, body.status, note=body.note)
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
        return [o.model_dump() for o in ApplicationService(con).list_offers(profile_id)]

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
