"""领域枚举。存库为字符串值；新增值只增不删（旧数据永远可读）。"""
from __future__ import annotations

from enum import StrEnum


class ExperienceKind(StrEnum):
    INTERNSHIP = "internship"
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    FREELANCE = "freelance"
    PROJECT = "project"


class EvidenceSourceKind(StrEnum):
    FILE = "file"
    PASTE = "paste"
    AI_EXTRACTED = "ai_extracted"
    MANUAL = "manual"


class FactType(StrEnum):
    EDUCATION = "education"
    EXPERIENCE = "experience"
    PROJECT = "project"
    SKILL = "skill"
    AWARD = "award"
    CERTIFICATION = "certification"
    NUMBER = "number"
    OTHER = "other"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    CONTRACT = "contract"


class RecruitmentType(StrEnum):
    """岗位面向的招聘批次类型。unknown 表示信源未提供，由推断规则标注置信度。"""
    CAMPUS = "campus"
    SOCIAL = "social"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class WorkMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"
    REMOVED = "removed"
    REJECTED = "rejected"  # 入库过滤拒绝（透明留档）


class SourceAdapterKind(StrEnum):
    MANUAL_PASTE = "manual_paste"      # 粘贴 JD/URL
    FILE_IMPORT = "file_import"        # JSON/CSV 文件导入
    API_FETCH = "api_fetch"            # 官方公开 API 拉取
    BROWSER_BRIDGE = "browser_bridge"  # 扩展/书签脚本推送（预留）


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    SAVED = "saved"
    SHORTLISTED = "shortlisted"
    PREPARING = "preparing"
    MATERIALS_READY = "materials_ready"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED_CONFIRMED = "applied_confirmed"
    ASSESSMENT = "assessment"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"


# 状态机：允许的转移。
# 设计原则（诚实语义优先，操作摩擦最小化）：
# 1. 准备漏斗（discovered→saved→shortlisted→preparing→materials_ready→ready_to_apply）
#    允许向前跳步——后置状态在语义上蕴含前置步骤已完成，审计流记录实际 from→to；
# 2. applied_confirmed 不在转移表中：只能经 confirm_applied 的用户确认门进入
#    （事件带 user_confirmed 标记），通用 transition 一律拒绝；
# 3. 投后状态（assessment/interviewing/offer/rejected）只能从 applied_confirmed 链到达，
#    防止伪造生命周期（未确认投递直接 offer）。
APPLICATION_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DISCOVERED: {
        ApplicationStatus.SAVED, ApplicationStatus.SHORTLISTED,
        ApplicationStatus.PREPARING, ApplicationStatus.MATERIALS_READY,
        ApplicationStatus.READY_TO_APPLY,
        ApplicationStatus.REJECTED, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.SAVED: {
        ApplicationStatus.SHORTLISTED, ApplicationStatus.PREPARING,
        ApplicationStatus.MATERIALS_READY, ApplicationStatus.READY_TO_APPLY,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.SHORTLISTED: {
        ApplicationStatus.PREPARING, ApplicationStatus.MATERIALS_READY,
        ApplicationStatus.READY_TO_APPLY,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.PREPARING: {
        ApplicationStatus.MATERIALS_READY, ApplicationStatus.READY_TO_APPLY,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.MATERIALS_READY: {
        ApplicationStatus.READY_TO_APPLY, ApplicationStatus.PREPARING,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.READY_TO_APPLY: {
        # applied_confirmed 只经 confirm_applied（用户确认门），不在此表
        ApplicationStatus.PREPARING,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.APPLIED_CONFIRMED: {
        ApplicationStatus.ASSESSMENT, ApplicationStatus.INTERVIEWING,
        ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.ASSESSMENT: {
        ApplicationStatus.INTERVIEWING, ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.INTERVIEWING: {
        ApplicationStatus.OFFER, ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.CLOSED,
    },
    ApplicationStatus.OFFER: {ApplicationStatus.CLOSED},
    ApplicationStatus.REJECTED: {ApplicationStatus.CLOSED},
    ApplicationStatus.WITHDRAWN: {ApplicationStatus.CLOSED},
    ApplicationStatus.CLOSED: set(),
}


class FeedbackKind(StrEnum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    TOO_FAR = "too_far"
    LOW_PAY = "low_pay"
    BAD_INDUSTRY = "bad_industry"
    BAD_COMPANY = "bad_company"
    SKILL_MISMATCH = "skill_mismatch"
    APPLIED = "applied"
    REJECTED = "rejected"
    GOT_INTERVIEW = "got_interview"
    GOT_OFFER = "got_offer"


class CompanyType(StrEnum):
    SOE = "soe"                # 国企/央企
    INSTITUTE = "institute"    # 科研院所/事业单位
    PRIVATE = "private"
    FOREIGN = "foreign"
    STARTUP = "startup"
    UNKNOWN = "unknown"
