-- B3 判活阶梯：逐岗位存活判定账本。
-- 保守判死立场（career-ops liveness 同款设计）：只有确定性信号（官方 404/410、
-- ATS 业务下线码、静态页硬关键词）才落 expired；429/5xx/超时/模糊一律 uncertain。
-- verdict 与 code 分开：code 是机器可读依据（http_gone / tencent_e1005 / …），
-- evidence 是人读证据摘要（含命中片段），误判可回溯可撤销。
-- 岗位状态本体仍写 job_postings.status（经 JobService.set_status），
-- 本表只承担「何时查过、凭什么判」的审计与复查调度（避免重复探测）。
CREATE TABLE job_liveness_log (
    job_id      TEXT PRIMARY KEY REFERENCES job_postings(id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL DEFAULT '',    -- active | expired | uncertain
    code        TEXT NOT NULL DEFAULT '',    -- 判定依据机器码
    evidence    TEXT NOT NULL DEFAULT '',    -- 人读证据摘要（命中片段）
    checked_at  TEXT
);
CREATE INDEX idx_liveness_verdict ON job_liveness_log(verdict);
