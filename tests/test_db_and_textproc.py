"""数据层与文本层基础测试：迁移原子性/幂等性、FTS 中文检索、解析器。"""
from __future__ import annotations

import sqlite3

import pytest

from jobhater import textproc as tp
from jobhater.db import apply_all, connect, current_version, migration_files


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test.db"
    apply_all(path)
    con = connect(path)
    yield con
    con.close()


def test_migrations_apply_and_idempotent(tmp_path):
    path = tmp_path / "m.db"
    first = apply_all(path)
    assert first == len(migration_files())
    assert apply_all(path) == 0  # 幂等
    con = connect(path)
    try:
        assert current_version(con) > 0
        tables = {
            r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # §3 实体逐一点名（FTS 影子表除外）
        expected = {
            "candidate_profiles", "educations", "experiences", "projects", "skills",
            "awards", "certifications", "evidence", "search_presets",
            "employers", "job_sources", "job_postings", "source_snapshots",
            "match_results", "resumes", "resume_versions", "cover_letters",
            "applications", "application_events", "contacts",
            "interviews", "interview_sessions", "interview_reviews",
            "offers", "reminders", "feedback_events", "user_settings", "ai_providers",
        }
        missing = expected - tables
        assert not missing, f"缺表: {missing}"
    finally:
        con.close()


def test_migration_failure_leaves_no_partial_schema(tmp_path, monkeypatch):
    """半途失败的迁移必须整体回滚（不留半套 schema）。"""
    from jobhater.db import migrations as mig

    files = migration_files()
    assert files, "至少存在 0001"
    bad = files[0][2] + "\nCREATE TABLE broken_after (id TEXT PRIMARY KEY);\nCREATE SYNTAX ERROR HERE;"
    monkeypatch.setattr(mig, "migration_files", lambda: [(files[0][0], files[0][1], bad)])
    path = tmp_path / "bad.db"
    with pytest.raises(sqlite3.OperationalError):
        mig.apply_all(path)
    con = connect(path)
    try:
        tables = {
            r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "broken_after" not in tables
        assert "candidate_profiles" not in tables  # 同一脚本内全部回滚
        assert current_version(con) == 0
    finally:
        con.close()


def _insert_job(con, *, title="", company="", desc="", dedupe="k1", search=None):
    con.execute(
        "INSERT OR IGNORE INTO job_sources(id, adapter_kind, display_name) VALUES ('manual','manual_paste','手动导入')"
    )
    con.execute(
        """INSERT INTO job_postings(id, employer_id, source_id, title, dedupe_key,
               content_hash, search_text, description)
           VALUES (?, NULL, 'manual', ?, ?, 'h', ?, ?)""",
        (f"j-{dedupe}", title, dedupe, search if search is not None else tp.tokenize_for_fts(
            f"{title} {company} {desc}"), desc),
    )


def test_fts_chinese_search_roundtrip(db):
    """中文岗位经预分词后必须可被中文词/子串命中（无 jieba 环境下靠 bigram 兜底）。"""
    _insert_job(
        db,
        title="机器学习工程师",
        company="示例科技",
        desc="负责推荐系统算法研发，需要深度学习与大规模数据处理经验",
        dedupe="ml1",
    )
    _insert_job(
        db, title="机械设计师", company="重工集团", desc="负责机械结构设计与仿真", dedupe="mech1",
    )
    db.commit()
    for q in ["机器学习", "深度学习", "算法", "机械"]:
        expr = tp.fts_query([q])
        rows = db.execute(
            "SELECT title FROM job_postings_fts JOIN job_postings ON job_postings.rowid = job_postings_fts.rowid "
            "WHERE job_postings_fts MATCH ? ORDER BY rank",
            (expr,),
        ).fetchall()
        assert rows, f"查询「{q}」不应落空（expr={expr}）"
    # 区分度：机械查询不应命中机器学习岗
    rows = db.execute(
        "SELECT p.title FROM job_postings_fts f JOIN job_postings p ON p.rowid = f.rowid "
        "WHERE job_postings_fts MATCH ?",
        (tp.fts_query(["机械设计"]),),
    ).fetchall()
    assert [r[0] for r in rows] == ["机械设计师"]


def test_fts_delete_trigger_consistency(db):
    _insert_job(db, title="后端工程师", dedupe="be1")
    db.commit()
    db.execute("DELETE FROM job_postings WHERE dedupe_key='be1'")
    db.commit()
    n = db.execute("SELECT COUNT(*) AS c FROM job_postings_fts").fetchone()["c"]
    assert n == 0, "删除岗位后 FTS 必须同步清空"


# ---------- textproc ----------


def test_norm_key_and_similarity():
    assert tp.norm_key("腾讯科技（深圳）有限公司") == tp.norm_key("腾讯科技深圳有限公司")
    assert tp.title_similarity("高级Java开发工程师", "JAVA开发工程师（高级）") > 0.5
    assert tp.title_similarity("产品经理", "Java开发工程师") < 0.2


def test_parse_salary_variants():
    assert tp.parse_salary("15-25K·16薪") == (15.0, 25.0, 16)
    assert tp.parse_salary("1.5万-3万") == (15.0, 30.0, None)
    assert tp.parse_salary("300-500元/天") == (6.5, 10.9, None)
    assert tp.parse_salary("面议") == (None, None, None)
    assert tp.parse_salary("") == (None, None, None)


def test_parse_experience_years():
    assert tp.parse_experience_years("3-5年") == 3.0
    assert tp.parse_experience_years("经验不限") == 0.0
    assert tp.parse_experience_years("5年以上") == 5.0
    assert tp.parse_experience_years("2026届毕业生") == 0.0  # NO_EXP_WORDS 命中
    assert tp.parse_experience_years("本科") is None
    assert tp.parse_experience_years("99年工作经验") is None  # 荒谬值防护


def test_hit_words_ascii_boundary():
    assert tp.hit_words("需要 go 语言", ["go"]) == ["go"]
    assert tp.hit_words("良好的good编码习惯", ["go"]) == []  # go 不得从 good 内部误报
    assert tp.hit_words("熟悉 CI/CD 流程", ["CI"]) == ["CI"]
    assert "机器学习" in tp.hit_words("扎实的机器学习能力", ["机器学习"])
