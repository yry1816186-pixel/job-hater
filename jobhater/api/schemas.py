"""API 请求模型（Pydantic）：各资源端点的参数形状契约（BC-007 自 routes.py 纯搬移）。

按 FastAPI Bigger Applications 模式集中放置；类定义逐字保持，
OpenAPI components 由此生成（拆分前后快照必须零差异）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProfileIn(BaseModel):
    display_name: str
    headline: str | None = None
    phone: str | None = None
    email: str | None = None


class ProfilePatchIn(BaseModel):
    display_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    phone: str | None = None
    email: str | None = None


class ResumeTextInput(BaseModel):
    text: str


class ResumeAIParseIn(BaseModel):
    text: str
    ack_egress: bool = False


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


class WeChatScanIn(BaseModel):
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)


class WeChatImportIn(BaseModel):
    """BC-006：hit_ids（内容寻址键，sha1(talker|create_time|title|company[:60])[:16]）
    取代 indices——序号在两端过滤差异/结果刷新时漂移导错行；空 = 按过滤条件全部导入。"""
    hit_ids: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    cohort: int | None = None  # 只导指定届别（如 2027）
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
