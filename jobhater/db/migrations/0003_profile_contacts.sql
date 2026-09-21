-- 0003 — 画像增加联系方式（简历 basics 的法定字段；v1 identity.phone/email 的迁移去处）
ALTER TABLE candidate_profiles ADD COLUMN phone TEXT;
ALTER TABLE candidate_profiles ADD COLUMN email TEXT;
