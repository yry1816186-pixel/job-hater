-- 0004 — 投递标签（用户自由分类：内推/海投/优先级/城市意向…）
-- 语义：纯用户标注事实，不参与状态机；空数组即无标签。
ALTER TABLE applications ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
