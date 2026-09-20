"""领域模型（Pydantic v2）。

与 db/migrations/0001_init.sql 一一对应；`*_json` 列在模型侧展开为原生
list/dict，由仓储层负责序列化。时间戳字符串（ISO8601 UTC）直通不解析——
展示层按需格式化，领域层不引入 datetime 对象的可变性。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jobhater.domain.enums import (
    CompanyType,
    EvidenceSourceKind,
    FactType,
    JobStatus,
    RecruitmentType,
    SourceAdapterKind,
    WorkMode,
)


class _Model(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# ========== 画像簇 ==========


class CandidateProfile(_Model):
    id: str
    display_name: str
    headline: str | None = None
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Education(_Model):
    id: str
    profile_id: str
    school: str
    degree: str | None = None
    major: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    gpa: float | None = None
    gpa_note: str | None = None
    detail: dict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0


class Experience(_Model):
    id: str
    profile_id: str
    employer: str
    title: str
    kind: str = "full_time"
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0


class Project(_Model):
    id: str
    profile_id: str
    name: str
    role: str | None = None
    url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0


class Skill(_Model):
    id: str
    profile_id: str
    name: str
    category: str | None = None
    level: int | None = Field(default=None, ge=0, le=5)
    years: float | None = None
    note: str | None = None
    # 同义词由用户/别名包提供——不再有代码级全局词表
    aliases: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Award(_Model):
    id: str
    profile_id: str
    name: str
    issuer: str | None = None
    date: str | None = None
    level: str | None = None
    description: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Certification(_Model):
    id: str
    profile_id: str
    name: str
    issuer: str | None = None
    issue_date: str | None = None
    expire_date: str | None = None
    credential_id: str | None = None
    url: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Evidence(_Model):
    id: str
    profile_id: str
    source_kind: EvidenceSourceKind
    source_ref: str | None = None
    doc_hash: str | None = None
    section: str | None = None
    original_text: str
    normalized_fact: str | None = None
    fact_type: FactType | None = None
    confidence: float = 1.0
    user_confirmed: bool = False
    created_at: str | None = None
    superseded_by: str | None = None


class SearchPreset(_Model):
    """求职偏好。除 id/profile_id/name 外全部可空——空=不限，由 Gate 解释。"""

    id: str
    profile_id: str
    name: str
    is_active: bool = False
    employment_types: list[str] = Field(default_factory=list)  # internship/campus/social
    target_roles: list[str] = Field(default_factory=list)
    target_cities: list[str] = Field(default_factory=list)
    remote_ok: bool = False
    salary_min_k: float | None = None
    exclude_employers: list[str] = Field(default_factory=list)
    exclude_industries: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    max_experience_years_required: float | None = None
    min_education: str | None = None
    graduation_year: int | None = None
    accept_incomplete_salary: bool = True
    weights: dict = Field(default_factory=dict)
    gates: dict = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


# ========== 雇主/信源/岗位簇 ==========


class Employer(_Model):
    id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    company_type: CompanyType | None = None
    industry: str | None = None
    size: str | None = None
    website: str | None = None
    notes: str | None = None
    user_blocked: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class JobSource(_Model):
    id: str
    adapter_kind: SourceAdapterKind
    display_name: str
    enabled: bool = True
    health_status: str = "unknown"
    health_message: str | None = None
    last_success_at: str | None = None
    last_attempt_at: str | None = None
    consecutive_failures: int = 0
    config: dict = Field(default_factory=dict)
    rate_policy: dict = Field(default_factory=dict)


class JobPosting(_Model):
    id: str
    employer_id: str | None = None
    employer_name: str = ""  # 冗余快照列（入库时写入，避免展示必查雇主表）
    source_id: str
    source_job_id: str | None = None
    canonical_url: str | None = None
    title: str
    department: str | None = None
    recruiter_name: str | None = None
    city: str | None = None
    district: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    employment_type: str | None = None
    recruitment_type: RecruitmentType = RecruitmentType.UNKNOWN
    experience_required_min: float | None = None
    experience_required_max: float | None = None
    experience_required_text: str | None = None
    education_required: str | None = None
    salary_min_k: float | None = None
    salary_max_k: float | None = None
    salary_months: int | None = None
    salary_text: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    requirements: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    published_at: str | None = None
    deadline: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    fetched_at: str | None = None
    status: JobStatus = JobStatus.ACTIVE
    reject_reason: str | None = None
    source_confidence: float = 1.0
    content_hash: str = ""
    dedupe_key: str = ""
    extras: dict = Field(default_factory=dict)


class IngestStats(_Model):
    """一次导入的透明统计：每条去向可查。"""
    received: int = 0
    added: int = 0
    deduped_exact: int = 0
    deduped_near: int = 0
    enriched: int = 0
    rejected: int = 0
    details: list[dict] = Field(default_factory=list)


# ========== 匹配结果 ==========


class GateOutcome(_Model):
    code: str                 # 如 experience_over_max / city_not_in_targets / blocked_employer
    passed: bool
    detail: str               # 人可读的原因（含数据）


class DimensionScore(_Model):
    """一个排序维度的完整可解释结构：分数+依据+不确定性。"""
    score: float
    reasons: list[str] = Field(default_factory=list)
    uncertainty: str | None = None  # 数据缺失/信号冲突时的诚实标注


class MatchOutcome(_Model):
    """单岗位匹配的完整结果（写入 match_results 前的内存形态）。"""
    job_id: str
    profile_id: str
    preset_id: str | None
    engine_version: str
    eligible: bool
    gate_reasons: list[GateOutcome]
    relevance_score: float | None
    rank_score: float | None
    verdict: str | None
    dims: dict[str, DimensionScore]
    evidence: dict = Field(default_factory=dict)  # matched/unmatched skills、命中证据
    needs_review: bool = False


# ========== 投递生命周期 ==========


class Application(_Model):
    id: str
    job_id: str
    profile_id: str
    resume_version_id: str | None = None
    status: str = "discovered"
    status_updated_at: str | None = None
    applied_at: str | None = None
    apply_channel: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ApplicationEvent(_Model):
    id: int
    application_id: str
    kind: str
    payload: dict = Field(default_factory=dict)
    note: str | None = None
    occurred_at: str | None = None


class Interview(_Model):
    id: str
    application_id: str
    round: int = 1
    kind: str | None = None  # behavioral/technical/case/hr/group/final
    scheduled_at: str | None = None
    duration_min: int | None = None
    location: str | None = None
    interviewer_names: list[str] = Field(default_factory=list)
    status: str = "planned"  # planned/done/cancelled
    outcome: str | None = None  # pass/fail/pending
    notes: str | None = None
    created_at: str | None = None


class InterviewSession(_Model):
    id: str
    interview_id: str | None = None
    mode: str = "mock"  # mock/real_record
    persona: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    started_at: str | None = None
    ended_at: str | None = None
    transcript: list[dict] = Field(default_factory=list)


class InterviewReview(_Model):
    id: str
    session_id: str
    overall: float | None = None
    scores: dict = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    practice_items: list[str] = Field(default_factory=list)
    ai_generated: bool = False
    created_at: str | None = None


class Offer(_Model):
    id: str
    application_id: str
    employer_id: str | None = None
    base_salary_k: float
    salary_months: int | None = None
    bonus_text: str | None = None
    equity_text: str | None = None
    benefits: list[str] = Field(default_factory=list)
    city: str | None = None
    work_mode: str | None = None
    probation_months: int | None = None
    deadline: str | None = None
    status: str = "considering"  # considering/accepted/declined/expired
    custom_dimensions: dict = Field(default_factory=dict)
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Reminder(_Model):
    id: int
    owner_kind: str  # application/interview/offer
    owner_id: str
    due_at: str
    kind: str | None = None
    title: str
    done: bool = False
    created_at: str | None = None


# ========== 简历 ==========


class Resume(_Model):
    id: str
    profile_id: str
    name: str
    kind: str = "master"  # master/job_specific
    job_id: str | None = None
    status: str = "draft"  # draft/final/archived
    current_version: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class BulletProvenance(_Model):
    """一条 bullet 的溯源：改写类别 + 支撑证据。rewrite_kind 取值见 ResumeService。"""
    path: str                     # sections 内的定位（如 work[0].bullets[2]）
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    rewrite_kind: str = "verbatim"  # verbatim/factual_rewrite/emphasis/reordering/keyword_alignment/unsupported


class ResumeVersion(_Model):
    id: str
    resume_id: str
    version: int
    sections: dict = Field(default_factory=dict)  # JSON Resume 兼容结构
    bullets_provenance: list[BulletProvenance] = Field(default_factory=list)
    factcheck_report: dict | None = None
    parent_version_id: str | None = None
    note: str | None = None
    created_at: str | None = None


class CoverLetter(_Model):
    id: str
    profile_id: str
    job_id: str | None = None
    resume_version_id: str | None = None
    content_md: str
    factcheck_report: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None
