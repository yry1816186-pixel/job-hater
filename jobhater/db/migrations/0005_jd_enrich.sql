-- B1 JD 补全引擎：逐岗位补全状态账本。
-- 断点续跑的真相源在库内（pending = active + 有 url + 无正文 + 未达重试上限），
-- 进程随时可杀，重跑自动从剩余待办继续；attempts 上限防死循环刷官方接口。
CREATE TABLE jd_enrich_log (
    job_id      TEXT PRIMARY KEY REFERENCES job_postings(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',    -- ok | failed | no_match
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    enriched_at TEXT,
    jd_chars    INTEGER NOT NULL DEFAULT 0
);
