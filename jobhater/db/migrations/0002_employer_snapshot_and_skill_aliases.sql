-- 0002 — 岗位表增加雇主名快照列；技能表增加用户可维护同义词（替代 v1 代码级全局词表）
ALTER TABLE job_postings ADD COLUMN employer_name TEXT NOT NULL DEFAULT '';
ALTER TABLE skills ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]';
