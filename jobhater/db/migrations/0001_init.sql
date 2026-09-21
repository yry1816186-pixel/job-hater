-- 0001_init.sql — Job Hater v2 初始 schema
-- 设计约定：
--   * 主键 TEXT uuid（跨机器合并安全）；高频追加型事件表用 INTEGER 自增；
--   * 时间戳一律 ISO8601 UTC 文本（strftime('%Y-%m-%dT%H:%M:%fZ','now')）；
--   * 可空即"未知/未采集"，绝不以空串或占位值伪装；
--   * JSON 列（后缀 _json）存结构化扩展，核心可查询字段一律提升为列；
--   * ON DELETE 视语义：强从属（画像的学历等）级联，弱引用（岗位→雇主）置空或 RESTRICT。

-- ========== 画像与证据 ==========

CREATE TABLE candidate_profiles (
    id          TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    headline    TEXT,
    summary     TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE educations (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    school      TEXT NOT NULL,
    degree      TEXT,                -- 本科/硕士/博士/大专/高中及以下（自由文本，规范枚举在 domain 层校验）
    major       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    is_current  INTEGER NOT NULL DEFAULT 0,
    gpa         REAL,
    gpa_note    TEXT,                -- 如 "专业前10%"（有证据才写）
    detail_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_educations_profile ON educations(profile_id);

CREATE TABLE experiences (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    employer    TEXT NOT NULL,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'full_time',  -- internship/full_time/part_time/freelance/project
    start_date  TEXT,
    end_date    TEXT,
    is_current  INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    tags_json   TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_experiences_profile ON experiences(profile_id);

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT,
    url         TEXT,
    start_date  TEXT,
    end_date    TEXT,
    description TEXT,
    tags_json   TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_projects_profile ON projects(profile_id);

CREATE TABLE skills (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    category    TEXT,                -- technical/design/product/language/soft/other
    level       INTEGER,             -- 0-5 自评；NULL=未评
    years       REAL,
    note        TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(profile_id, name)
);
CREATE INDEX idx_skills_profile ON skills(profile_id);

CREATE TABLE awards (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    issuer      TEXT,
    date        TEXT,
    level       TEXT,                -- 国家级/省级/校级/行业奖项等
    description TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_awards_profile ON awards(profile_id);

CREATE TABLE certifications (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    issuer      TEXT,
    issue_date  TEXT,
    expire_date TEXT,
    credential_id TEXT,
    url         TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_certifications_profile ON certifications(profile_id);

-- 证据表：候选人事实的唯一真相源。AI 生成的每条经历必须能指回这里。
CREATE TABLE evidence (
    id              TEXT PRIMARY KEY,   -- 形如 ev_<8位>
    profile_id      TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    source_kind     TEXT NOT NULL,      -- file/paste/ai_extracted/manual
    source_ref      TEXT,               -- 相对 data 目录的文件路径或文档标识
    doc_hash        TEXT,               -- 来源文档内容 hash（防篡改/对账）
    section         TEXT,               -- 页码/章节/行号等定位
    original_text   TEXT NOT NULL,      -- 原文逐字（不清洗）
    normalized_fact TEXT,               -- 规范化后的事实陈述
    fact_type       TEXT,               -- education/experience/project/skill/award/number/other
    confidence      REAL NOT NULL DEFAULT 1.0,
    user_confirmed  INTEGER NOT NULL DEFAULT 0,  -- 用户在 Candidate Facts Review 中确认过
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES evidence(id) ON DELETE SET NULL
);
CREATE INDEX idx_evidence_profile ON evidence(profile_id);
CREATE INDEX idx_evidence_confirmation ON evidence(profile_id, user_confirmed);

-- ========== 求职偏好（preset 化，无任何默认硬编码用户假设） ==========

CREATE TABLE search_presets (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,          -- 如 "2027秋招-产品设计"、"Java后端社招"
    is_active       INTEGER NOT NULL DEFAULT 0,
    -- 目标
    employment_types_json TEXT NOT NULL DEFAULT '[]',  -- internship/campus/social
    target_roles_json     TEXT NOT NULL DEFAULT '[]',  -- 角色关键词列表
    target_cities_json    TEXT NOT NULL DEFAULT '[]',
    remote_ok       INTEGER NOT NULL DEFAULT 0,
    salary_min_k    REAL,                   -- 期望月薪下限（千元）
    -- 硬性排除（Eligibility Gate 的数据源）
    exclude_employers_json  TEXT NOT NULL DEFAULT '[]',
    exclude_industries_json TEXT NOT NULL DEFAULT '[]',
    exclude_keywords_json   TEXT NOT NULL DEFAULT '[]',
    max_experience_years_required REAL,    -- 岗位要求的经验上限容忍度；NULL=不限
    min_education   TEXT,                  -- 岗位学历要求低于此仍可投；NULL=不限
    graduation_year INTEGER,               -- 用户毕业年份（校招批次判定用）
    accept_incomplete_salary INTEGER NOT NULL DEFAULT 1, -- 薪资未标注是否仍入围
    -- 排序权重（Personalized Ranking 的数据源；全部可调，默认在 domain 层给中性值）
    weights_json    TEXT NOT NULL DEFAULT '{}',
    gates_json      TEXT NOT NULL DEFAULT '{}',   -- 额外 gate 开关（headhunter/outsourcing/过期…）
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(profile_id, name)
);
CREATE INDEX idx_presets_profile ON search_presets(profile_id, is_active);

-- ========== 雇主与岗位 ==========

CREATE TABLE employers (
    id            TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    company_type  TEXT,               -- soe/private/foreign/startup/institute/unknown
    industry      TEXT,
    size          TEXT,
    website       TEXT,
    notes         TEXT,
    user_blocked  INTEGER NOT NULL DEFAULT 0,   -- 用户明确屏蔽（区别于 cooldown）
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_employers_name ON employers(canonical_name);
CREATE INDEX idx_employers_blocked ON employers(user_blocked);

CREATE TABLE job_sources (
    id            TEXT PRIMARY KEY,   -- 稳定标识，如 "manual"、"paste"、"wenke"
    adapter_kind  TEXT NOT NULL,      -- manual/browser_paste/api_fetch/file_import
    display_name  TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    -- 健康与故障隔离（单源失败不得拖死全局）
    health_status TEXT NOT NULL DEFAULT 'unknown',  -- ok/degraded/down/unknown
    health_message TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    config_json   TEXT NOT NULL DEFAULT '{}',
    rate_policy_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE job_postings (
    id            TEXT PRIMARY KEY,
    employer_id   TEXT REFERENCES employers(id) ON DELETE SET NULL,
    source_id     TEXT NOT NULL REFERENCES job_sources(id),
    source_job_id TEXT,               -- 信源侧岗位 ID（可能无）
    canonical_url TEXT,
    title         TEXT NOT NULL,
    department    TEXT,
    recruiter_name TEXT,              -- BOSS 类平台的 HR 名称（如采集到）
    city          TEXT,
    district      TEXT,
    work_mode     TEXT,               -- onsite/hybrid/remote/unknown
    employment_type TEXT,             -- full_time/part_time/internship/contract
    recruitment_type TEXT,            -- campus/social/internship/unknown
    experience_required_min REAL,
    experience_required_max REAL,
    experience_required_text TEXT,
    education_required TEXT,
    salary_min_k   REAL,
    salary_max_k   REAL,
    salary_months  INTEGER,           -- 12/13/14/16 薪等
    salary_text    TEXT,              -- 原文薪资表述（保真）
    description    TEXT,              -- JD 全文（保真，含原文换行）
    responsibilities TEXT,
    requirements_json TEXT NOT NULL DEFAULT '[]',  -- 结构化要求列表（抽取产物，置信度在 extras）
    keywords_json  TEXT NOT NULL DEFAULT '[]',
    published_at   TEXT,
    deadline       TEXT,
    first_seen_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    fetched_at     TEXT,
    status         TEXT NOT NULL DEFAULT 'active',  -- active/expired/closed/removed/rejected
    reject_reason  TEXT,               -- status=rejected 时的透明原因
    source_confidence REAL NOT NULL DEFAULT 1.0,    -- 信源结构化可信度 0-1
    content_hash   TEXT NOT NULL,      -- 归一化内容的 sha1，分层去重第一层
    dedupe_key     TEXT NOT NULL,      -- 规范化 employer|title 层级去重键
    search_text    TEXT NOT NULL DEFAULT '',  -- 预分词检索文本（触发器同步进 FTS）
    extras_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_jobs_employer ON job_postings(employer_id);
CREATE INDEX idx_jobs_source ON job_postings(source_id, source_job_id);
CREATE UNIQUE INDEX idx_jobs_source_native ON job_postings(source_id, source_job_id)
    WHERE source_job_id IS NOT NULL;
CREATE UNIQUE INDEX idx_jobs_dedupe ON job_postings(dedupe_key);
CREATE INDEX idx_jobs_status ON job_postings(status, last_seen_at);
CREATE INDEX idx_jobs_city ON job_postings(city);
CREATE INDEX idx_jobs_recruitment ON job_postings(recruitment_type);

-- 全文检索：search_text 为预分词的空格分隔 token（textproc.tokenize_for_fts，
-- 中文 jieba 可选增强、字符 bigram 兜底），unicode61 对空格分隔 token 无语言偏好。
-- external-content 模式：FTS 不重复存储，触发器维护一致性。
CREATE VIRTUAL TABLE job_postings_fts USING fts5(
    search_text,
    content='job_postings',
    tokenize='unicode61'
);
CREATE TRIGGER jobs_fts_ai AFTER INSERT ON job_postings BEGIN
    INSERT INTO job_postings_fts(rowid, search_text) VALUES (NEW.rowid, NEW.search_text);
END;
CREATE TRIGGER jobs_fts_ad AFTER DELETE ON job_postings BEGIN
    INSERT INTO job_postings_fts(job_postings_fts, rowid, search_text)
    VALUES ('delete', OLD.rowid, OLD.search_text);
END;
CREATE TRIGGER jobs_fts_au AFTER UPDATE OF search_text ON job_postings BEGIN
    INSERT INTO job_postings_fts(job_postings_fts, rowid, search_text)
    VALUES ('delete', OLD.rowid, OLD.search_text);
    INSERT INTO job_postings_fts(rowid, search_text) VALUES (NEW.rowid, NEW.search_text);
END;

CREATE TABLE source_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL REFERENCES job_sources(id),
    source_job_id TEXT,
    url         TEXT,
    raw_hash    TEXT NOT NULL,
    raw_json    TEXT NOT NULL,        -- 信源原始条目（保真快照）
    fetched_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_snapshots_job ON source_snapshots(job_id);

-- ========== 匹配结果（可复现、可解释） ==========

CREATE TABLE match_results (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    preset_id   TEXT REFERENCES search_presets(id) ON DELETE SET NULL,
    engine_version TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    eligible    INTEGER NOT NULL,           -- 是否通过全部硬性 Gate
    gate_reasons_json TEXT NOT NULL DEFAULT '[]',  -- 未通过的 gate 及原因（透明）
    relevance_score REAL,                   -- 检索相关性 0-100（BM25+结构信号）
    rank_score  REAL,                       -- 个性化排序总分 0-100
    verdict     TEXT,                       -- 用户可读结论
    dims_json   TEXT NOT NULL DEFAULT '{}', -- 各维度分数+依据+不确定性
    evidence_json TEXT NOT NULL DEFAULT '{}', -- matched/unmatched 技能、命中证据
    needs_review INTEGER NOT NULL DEFAULT 0, -- 信号冲突等需人工复核
    UNIQUE(job_id, profile_id, preset_id, engine_version, computed_at)
);
CREATE INDEX idx_match_job ON match_results(job_id);
CREATE INDEX idx_match_profile ON match_results(profile_id, preset_id, computed_at);

-- ========== 简历 ==========

CREATE TABLE resumes (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,            -- "主简历" / "XX公司-产品岗"
    kind        TEXT NOT NULL DEFAULT 'master',  -- master/job_specific
    job_id      TEXT REFERENCES job_postings(id) ON DELETE SET NULL,  -- job_specific 时指向岗位
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft/final/archived
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_resumes_profile ON resumes(profile_id);

-- 版本化内容：结构化 sections（basics/education/work/projects/skills/awards…），
-- 兼容 JSON Resume 字段命名，附 provenance：每个 bullet 的 evidence_ids。
CREATE TABLE resume_versions (
    id          TEXT PRIMARY KEY,
    resume_id   TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    sections_json TEXT NOT NULL DEFAULT '{}',
    bullets_provenance_json TEXT NOT NULL DEFAULT '[]',  -- [{path, text, evidence_ids, rewrite_kind}]
    factcheck_report_json TEXT,
    parent_version_id TEXT REFERENCES resume_versions(id) ON DELETE SET NULL,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(resume_id, version)
);
CREATE INDEX idx_resume_versions ON resume_versions(resume_id, version);

CREATE TABLE cover_letters (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    job_id      TEXT REFERENCES job_postings(id) ON DELETE SET NULL,
    resume_version_id TEXT REFERENCES resume_versions(id) ON DELETE SET NULL,
    content_md  TEXT NOT NULL,
    factcheck_report_json TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ========== 投递生命周期 ==========

CREATE TABLE applications (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES job_postings(id),
    profile_id  TEXT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    resume_version_id TEXT REFERENCES resume_versions(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'discovered',
    -- discovered/saved/shortlisted/preparing/materials_ready/ready_to_apply/
    -- applied_confirmed/assessment/interviewing/offer/rejected/withdrawn/closed
    status_updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    applied_at  TEXT,               -- 仅用户确认投递后写入（诚实语义）
    apply_channel TEXT,             -- 官网/内推/BOSS手动 等（记录，不代发）
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(job_id, profile_id)
);
CREATE INDEX idx_applications_status ON applications(profile_id, status);

CREATE TABLE application_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,      -- status_change/note/interview_scheduled/offer_received/…
    payload_json TEXT NOT NULL DEFAULT '{}',
    note        TEXT,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_app_events ON application_events(application_id, occurred_at);

CREATE TABLE contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
    employer_id TEXT REFERENCES employers(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    role        TEXT,
    phone       TEXT,
    email       TEXT,
    wechat      TEXT,
    note        TEXT
);
CREATE INDEX idx_contacts_app ON contacts(application_id);

-- ========== 面试 ==========

CREATE TABLE interviews (
    id          TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    round       INTEGER NOT NULL DEFAULT 1,
    kind        TEXT,               -- behavioral/technical/case/hr/group/final
    scheduled_at TEXT,
    duration_min INTEGER,
    location    TEXT,               -- 现场/线上（会议链接）
    interviewer_names_json TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'planned',  -- planned/done/cancelled
    outcome     TEXT,               -- pass/fail/pending
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_interviews_app ON interviews(application_id);

CREATE TABLE interview_sessions (
    id          TEXT PRIMARY KEY,
    interview_id TEXT REFERENCES interviews(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL DEFAULT 'mock',   -- mock/real_record
    persona     TEXT,               -- 面试官角色设定
    difficulty  INTEGER,            -- 1-5
    started_at  TEXT,
    ended_at    TEXT,
    transcript_json TEXT NOT NULL DEFAULT '[]'  -- [{role, content, at}]
);
CREATE INDEX idx_sessions_interview ON interview_sessions(interview_id);

CREATE TABLE interview_reviews (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    overall     REAL,
    scores_json TEXT NOT NULL DEFAULT '{}',  -- structure/clarity/technical/evidence_consistency…
    strengths_json TEXT NOT NULL DEFAULT '[]',
    gaps_json   TEXT NOT NULL DEFAULT '[]',
    practice_items_json TEXT NOT NULL DEFAULT '[]',
    ai_generated INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ========== Offer 与决策 ==========

CREATE TABLE offers (
    id          TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    employer_id TEXT REFERENCES employers(id) ON DELETE SET NULL,
    base_salary_k REAL NOT NULL,    -- 月基本薪资（千元）
    salary_months INTEGER,
    bonus_text  TEXT,
    equity_text TEXT,
    benefits_json TEXT NOT NULL DEFAULT '[]',
    city        TEXT,
    work_mode   TEXT,
    probation_months INTEGER,
    deadline    TEXT,               -- 接受期限
    status      TEXT NOT NULL DEFAULT 'considering',  -- considering/accepted/declined/expired
    custom_dimensions_json TEXT NOT NULL DEFAULT '{}',  -- 用户自定义比较维度 {名: 值}
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_offers_application ON offers(application_id);

CREATE TABLE reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_kind  TEXT NOT NULL,      -- application/interview/offer
    owner_id    TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    kind        TEXT,               -- followup/deadline/interview_prep/…
    title       TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_reminders_due ON reminders(done, due_at);

-- ========== 反馈闭环（可查看、可重置） ==========

CREATE TABLE feedback_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT REFERENCES job_postings(id) ON DELETE CASCADE,
    profile_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- interested/not_interested/too_far/low_pay/bad_industry/
                                    -- bad_company/skill_mismatch/applied/rejected/got_interview/got_offer
    weight      REAL NOT NULL DEFAULT 1.0,   -- 惩罚类反馈可为负
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_feedback_profile ON feedback_events(profile_id, created_at);

-- ========== 设置与 AI Provider ==========

CREATE TABLE user_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- API key 永不落库：此处只存 keyring 引用名（jobhater/<provider_id>）。
CREATE TABLE ai_providers (
    id          TEXT PRIMARY KEY,
    adapter_kind TEXT NOT NULL,     -- openai_compatible/anthropic/ollama/none
    display_name TEXT NOT NULL,
    base_url    TEXT,
    model       TEXT NOT NULL,
    api_key_ref TEXT,               -- OS keyring 服务名；本地模型为 NULL
    enabled     INTEGER NOT NULL DEFAULT 0,   -- 默认 opt-in（隐私：远程 AI 需用户显式启用）
    capabilities_json TEXT NOT NULL DEFAULT '{}', -- context_window/structured_output/vision/embedding…
    timeout_s   INTEGER NOT NULL DEFAULT 60,
    max_retries INTEGER NOT NULL DEFAULT 1,
    cost_note   TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
