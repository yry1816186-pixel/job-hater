-- B1 对抗评审 P0-1 修复：补全审计字段。
-- api_title = 官方接口返回的岗位名（163 标题匹配的可追溯证据）；
-- match_mode = exact | fuzzy | ''（直读型 provider 无匹配语义）。
-- 串岗可审计可回滚：fuzzy 命中与 exact 命中在账本层面可区分。
ALTER TABLE jd_enrich_log ADD COLUMN api_title TEXT NOT NULL DEFAULT '';
ALTER TABLE jd_enrich_log ADD COLUMN match_mode TEXT NOT NULL DEFAULT '';
