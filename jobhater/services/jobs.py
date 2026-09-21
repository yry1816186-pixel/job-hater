"""岗位服务：导入（规范化+分层去重+快照）、检索、状态。

去重层次（§5）：
  L1 (source_id, source_job_id) 精确命中 —— 同源同岗
  L2 dedupe_key（规范化雇主|标题） —— 跨源同岗
  L3 近似去重（同雇主 + 标题 bigram Jaccard ≥ 0.7）—— 存疑入库，标记人工复核
  L4 内容指纹（规范化描述 sha1）—— 同文异题，标记 canonical_of
入库拒绝只针对结构性损坏（无标题/无雇主/模板占位符）；一切"用户偏好"性质的
过滤（经验/城市/薪资）属于匹配引擎的 Eligibility Gate，不属于数据层。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from jobhater import textproc as tp
from jobhater.db.connection import transaction
from jobhater.domain.enums import JobStatus, RecruitmentType, WorkMode
from jobhater.domain.models import IngestStats, JobPosting, JobSource
from jobhater.services.storage import (
    JsonFieldMap,
    insert_model,
    loads,
    new_id,
    row_to_model,
)

_JOB_JSON: JsonFieldMap = {
    "requirements": ("requirements_json", []),
    "keywords": ("keywords_json", []),
    "extras": ("extras_json", {}),
}
_SRC_JSON: JsonFieldMap = {
    "config": ("config_json", {}),
    "rate_policy": ("rate_policy_json", {}),
}

NEAR_DUP_TITLE_THRESHOLD = 0.7
_PLACEHOLDER_MARKERS = ("（填写：", "（必填）", "【填写", "<填写")

_CAMPUS_WORDS = [
    "校招", "校园招聘", "应届", "管培生", "trainee", "campus", "在校生",
    "毕业生", "秋招", "春招", "宣讲会", "菁英", "英才计划",
]
_INTERNSHIP_WORDS = ["实习", "intern", "日常实习", "暑期实习", "internship"]
_SOCIAL_WORDS = ["社招", "社会招聘", "资深", "高级", "专家", "负责人"]
_REMOTE_WORDS = ["远程", "remote", "居家办公", "work from home", "wfh"]
_HYBRID_WORDS = ["混合办公", "hybrid", " hybrid"]
_HEADHUNTER_WORDS = ["猎头", "rpo", "招聘顾问", "人力资源服务"]
_OUTSOURCING_WORDS = ["外包", "驻场", "劳务派遣", "人力服务"]

# 无 deadline 岗位的发布超龄过期窗口（天）。数据常量：校招周期通常 1-2 月，
# 超过 60 天未更新的岗位信息大概率已过时；宁可早标过期（可手工恢复）不可误导。
PUBLISHED_MAX_AGE_DAYS = 60


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class JobService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self._emp_cache: dict[str, str] | None = None

    # ---------- 信源 ----------

    def ensure_source(
        self,
        source_id: str,
        adapter_kind: str,
        display_name: str,
        *,
        config: dict | None = None,
        rate_policy: dict | None = None,
    ) -> JobSource:
        row = self.con.execute("SELECT * FROM job_sources WHERE id=?", (source_id,)).fetchone()
        if row:
            return row_to_model(JobSource, row, _SRC_JSON)
        src = JobSource(
            id=source_id,
            adapter_kind=adapter_kind,  # type: ignore[arg-type]
            display_name=display_name,
            config=config or {},
            rate_policy=rate_policy or {},
        )
        with transaction(self.con):
            insert_model(self.con, "job_sources", src, _SRC_JSON)
        return src

    def list_sources(self) -> list[JobSource]:
        rows = self.con.execute("SELECT * FROM job_sources ORDER BY id").fetchall()
        return [row_to_model(JobSource, r, _SRC_JSON) for r in rows]

    def record_source_health(
        self, source_id: str, *, ok: bool, message: str | None = None
    ) -> None:
        """Adapter 调用后上报健康。单源失败只影响自身状态（故障隔离）。"""
        now = _now()
        with transaction(self.con):
            if ok:
                self.con.execute(
                    """UPDATE job_sources SET health_status='ok', health_message=?,
                         last_success_at=?, last_attempt_at=?, consecutive_failures=0,
                         updated_at=? WHERE id=?""",
                    (message, now, now, now, source_id),
                )
            else:
                self.con.execute(
                    """UPDATE job_sources SET
                         health_status=CASE WHEN consecutive_failures+1>=3 THEN 'down' ELSE 'degraded' END,
                         health_message=?, last_attempt_at=?, consecutive_failures=consecutive_failures+1,
                         updated_at=? WHERE id=?""",
                    (message, now, now, source_id),
                )

    # ---------- 雇主 ----------

    def _employer_cache(self) -> dict[str, str]:
        """规范化名/别名 → 雇主id 的进程内缓存（首次或失效后全量装载一次）。"""
        if self._emp_cache is None:
            cache: dict[str, str] = {}
            for r in self.con.execute(
                "SELECT id, canonical_name, aliases_json FROM employers"
            ).fetchall():
                cache[tp.norm_key(r["canonical_name"])] = r["id"]
                for a in loads(r["aliases_json"], []):
                    cache.setdefault(tp.norm_key(a), r["id"])
            self._emp_cache = cache
        return self._emp_cache

    def _resolve_employer(self, company: str) -> str:
        """按规范化名/别名找雇主，找不到则建档（type=unknown，不猜）。返回雇主 id。"""
        norm = tp.norm_key(company)
        cache = self._employer_cache()
        hit = cache.get(norm)
        if hit:
            return hit
        emp_id = new_id("emp")
        # 在调用方（ingest 批量）事务内执行
        self.con.execute(
            "INSERT INTO employers(id, canonical_name, aliases_json) VALUES (?,?,?)",
            (emp_id, company.strip(), "[]"),
        )
        cache[norm] = emp_id
        return emp_id

    # ---------- 导入主链 ----------

    def ingest(self, raw_jobs: list[dict], source_id: str = "manual") -> IngestStats:
        """批量导入：单事务批量写入（§29 ingestion batch 化）。

        去重判定全部走预装载的内存索引（一次 SELECT 装载），避免逐行查询的 O(n²)。
        每条去向透明可查；任一行写入异常 → 整批回滚并抛出（导入是原子操作）。
        """
        self.ensure_source(source_id, "manual_paste", source_id)
        stats = IngestStats()
        now = _now()
        # ---- 预装载去重索引（各一次全量 SELECT） ----
        existing_by_key: dict[str, tuple[str, bool]] = {}
        src_pairs: set[tuple[str, str]] = set()
        content_first: dict[str, str] = {}
        near_titles: dict[str, list[tuple[str, str]]] = {}
        for r in self.con.execute(
            "SELECT id, dedupe_key, source_id, source_job_id, content_hash, "
            "title, employer_name, description FROM job_postings"
        ).fetchall():
            existing_by_key.setdefault(r["dedupe_key"], (r["id"], bool((r["description"] or "").strip())))
            if r["source_job_id"]:
                src_pairs.add((r["source_id"], r["source_job_id"]))
            content_first.setdefault(r["content_hash"], r["id"])
            near_titles.setdefault(tp.norm_key(r["employer_name"]), []).append((r["id"], r["title"]))

        seen_keys: set[str] = set()
        with transaction(self.con):
            for raw in raw_jobs:
                stats.received += 1
                head = f"{raw.get('title', '')}{raw.get('company', '')}{raw.get('employer', '')}"
                if any(marker in head for marker in _PLACEHOLDER_MARKERS):
                    stats.rejected += 1
                    stats.details.append(
                        {"title": raw.get("title"), "company": raw.get("company"),
                         "result": "拒绝：模板占位符未填写"}
                    )
                    continue
                job = self.normalize(raw, source_id, fetched_at=now)
                if job is None:
                    stats.rejected += 1
                    stats.details.append(
                        {"title": raw.get("title"), "company": raw.get("company"),
                         "result": "拒绝：缺少标题或公司名（清洗失败）"}
                    )
                    continue
                key = job.dedupe_key
                if key in seen_keys:
                    stats.deduped_exact += 1
                    stats.details.append(
                        {"title": job.title, "company": job.employer_name, "result": "批内精确去重"}
                    )
                    continue
                seen_keys.add(key)
                outcome = self._store(
                    job, raw, now, existing_by_key, src_pairs, content_first, near_titles
                )
                stats.details.append(
                    {"id": job.id, "title": job.title, "company": job.employer_name, "result": outcome}
                )
                if outcome.startswith("入库"):
                    stats.added += 1
                elif "补全" in outcome:
                    stats.enriched += 1
                elif outcome.startswith("近似去重"):
                    stats.deduped_near += 1
                elif outcome.startswith("去重"):
                    stats.deduped_exact += 1
        return stats

    def normalize(
        self, raw: dict, source_id: str, fetched_at: str | None = None
    ) -> JobPosting | None:
        """原始条目 → 统一岗位契约。缺字段如实留空（None），绝不臆造；
        推断类字段（批次类型/办公模式）把依据写进 extras，可审计。"""
        title = " ".join(str(raw.get("title", "")).split())
        company = str(
            raw.get("company") or raw.get("employer") or raw.get("employer_name")
            or raw.get("company_name") or ""
        ).strip()
        if not title or not company:
            return None
        desc = tp.clean_whitespace(
            str(raw.get("description") or raw.get("jd_text") or raw.get("content") or "")
        )
        text = f"{title} {company} {desc}".lower()
        extras: dict = {"inferences": []}

        recruitment, rec_conf = _infer_recruitment(text, raw)
        if recruitment != RecruitmentType.UNKNOWN:
            extras["inferences"].append(
                {"field": "recruitment_type", "value": recruitment.value, "confidence": rec_conf}
            )
        work_mode = _infer_work_mode(text)
        exp_text = str(
            raw.get("experience_required") or raw.get("experience") or ""
        ).strip()
        exp_years = tp.parse_experience_years(exp_text or None)
        if exp_years is None and desc:
            exp_years = tp.parse_experience_years(desc)
            if exp_years is not None:
                extras["inferences"].append(
                    {"field": "experience_required_min", "value": exp_years,
                     "source": "description_scan"}
                )
        sal_text = str(
            raw.get("salary") or raw.get("salary_text") or ""
        ).strip()
        sal_min, sal_max, sal_months = tp.parse_salary(sal_text)
        city = str(raw.get("city") or raw.get("location") or "").strip() or None
        flags = _factual_flags(text)
        if flags:
            extras["flags"] = flags

        employer_name = company
        content_basis = tp.norm_key(company) + "|" + tp.norm_key(title) + "|" + tp.norm_key(
            (desc or "")[:200]
        )
        job = JobPosting(
            id=new_id("job"),
            employer_name=employer_name,
            source_id=source_id,
            source_job_id=str(raw.get("source_job_id") or raw.get("id") or "").strip() or None,
            canonical_url=str(raw.get("url") or raw.get("canonical_url") or "").strip() or None,
            title=title,
            department=str(raw.get("department") or "").strip() or None,
            recruiter_name=str(raw.get("recruiter_name") or raw.get("hr_name") or "").strip() or None,
            city=city,
            district=str(raw.get("district") or "").strip() or None,
            work_mode=work_mode,
            employment_type=str(raw.get("employment_type") or "").strip() or None,
            recruitment_type=recruitment,
            experience_required_min=exp_years,
            experience_required_text=exp_text or None,
            education_required=str(
                raw.get("education_required") or raw.get("education") or ""
            ).strip() or None,
            salary_min_k=sal_min,
            salary_max_k=sal_max,
            salary_months=sal_months,
            salary_text=sal_text or None,
            description=desc or None,
            responsibilities=_join_lines(raw.get("responsibilities")),
            keywords=[str(k) for k in (raw.get("keywords") or [])],
            published_at=_iso_date(raw.get("published_at")),
            deadline=_iso_date(raw.get("deadline")),
            fetched_at=fetched_at,
            status=JobStatus.ACTIVE,
            source_confidence=float(raw.get("source_confidence", 1.0)),
            content_hash=tp.sha1_hex(content_basis),
            dedupe_key=tp.norm_key(company) + "|" + tp.norm_key(title),
            extras=extras,
        )
        return job

    # ---------- 存储与去重 ----------

    def _store(
        self,
        job: JobPosting,
        raw: dict,
        now: str,
        existing_by_key: dict[str, tuple[str, bool]],
        src_pairs: set[tuple[str, str]],
        content_first: dict[str, str],
        near_titles: dict[str, list[tuple[str, str]]],
    ) -> str:
        """返回去向描述。在 ingest 的批量事务内执行——所有写操作不再各自开事务。
        去重索引（调用方预装载）随写入原地更新，保证批内后续行可见。"""
        # L1: 同源同 ID
        if job.source_job_id and (job.source_id, job.source_job_id) in src_pairs:
            row = self.con.execute(
                "SELECT id, description FROM job_postings WHERE source_id=? AND source_job_id=?",
                (job.source_id, job.source_job_id),
            ).fetchone()
            if row:
                self._snapshot(row["id"], job, raw, now)
                if not (row["description"] or "").strip() and job.description:
                    self._enrich(row["id"], job, now)
                    return "去重并补全JD（同源同ID）"
                return "去重跳过（同源同ID）"
        # L2: 跨源 dedupe_key
        hit = existing_by_key.get(job.dedupe_key)
        if hit:
            job_id, has_desc = hit
            self._snapshot(job_id, job, raw, now)
            if not has_desc and job.description:
                self._enrich(job_id, job, now)
                existing_by_key[job.dedupe_key] = (job_id, True)
                return "去重并补全JD（跨源同岗）"
            return "去重跳过（跨源同岗）"
        # L3: 近似去重（同雇主 + 标题相似，内存索引）
        emp_norm = tp.norm_key(job.employer_name)
        near = None
        for other_id, other_title in near_titles.get(emp_norm, []):
            if tp.title_similarity(job.title, other_title) >= NEAR_DUP_TITLE_THRESHOLD:
                near = other_id
                break
        if near:
            job.extras = {**job.extras, "near_dup_of": near}
        # L4: 内容指纹
        same_content = content_first.get(job.content_hash)
        if same_content and not job.extras.get("near_dup_of"):
            job.extras = {**job.extras, "canonical_of": same_content}
        # 过期事实检测（数据事实，非用户偏好）
        if _is_expired(job):
            job.status = JobStatus.EXPIRED
        employer_id = self._resolve_employer(job.employer_name)
        search_text = tp.tokenize_for_fts(
            f"{job.title} {job.employer_name} {job.description or ''} "
            f"{' '.join(job.keywords)} {job.city or ''}"
        )
        cols = [
            "id", "employer_id", "source_id", "source_job_id", "canonical_url", "title",
            "department", "recruiter_name", "city", "district", "work_mode", "employment_type",
            "recruitment_type", "experience_required_min", "experience_required_text",
            "education_required", "salary_min_k", "salary_max_k", "salary_months", "salary_text",
            "description", "responsibilities", "keywords_json", "published_at", "deadline",
            "fetched_at", "status", "source_confidence", "content_hash", "dedupe_key",
            "search_text", "extras_json", "employer_name",
        ]
        vals = (
            job.id, employer_id, job.source_id, job.source_job_id, job.canonical_url,
            job.title, job.department, job.recruiter_name, job.city, job.district,
            job.work_mode.value, job.employment_type, job.recruitment_type.value,
            job.experience_required_min, job.experience_required_text,
            job.education_required, job.salary_min_k, job.salary_max_k,
            job.salary_months, job.salary_text, job.description, job.responsibilities,
            json.dumps(job.keywords, ensure_ascii=False),
            job.published_at, job.deadline, job.fetched_at, job.status.value,
            job.source_confidence, job.content_hash, job.dedupe_key, search_text,
            json.dumps(job.extras, ensure_ascii=False), job.employer_name,
        )
        assert len(cols) == len(vals), f"列/值不匹配: {len(cols)} vs {len(vals)}"
        self.con.execute(
            f"INSERT INTO job_postings ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            vals,
        )
        # 索引原地更新（批内后续行可见）
        existing_by_key[job.dedupe_key] = (job.id, bool((job.description or "").strip()))
        if job.source_job_id:
            src_pairs.add((job.source_id, job.source_job_id))
        content_first.setdefault(job.content_hash, job.id)
        near_titles.setdefault(emp_norm, []).append((job.id, job.title))
        self._snapshot(job.id, job, raw, now)
        if job.extras.get("near_dup_of"):
            return f"近似去重标记入库（near_dup_of={job.extras['near_dup_of']}）"
        if job.extras.get("canonical_of"):
            return f"同文异题标记入库（canonical_of={job.extras['canonical_of']}）"
        if job.status == JobStatus.EXPIRED:
            return "入库（标记为已过期）"
        return "入库"

    def _snapshot(self, job_id: str, job: JobPosting, raw: dict, now: str) -> None:
        """在调用方事务内写入原始快照（不在本方法内开事务）。"""
        raw_json = json.dumps(raw, ensure_ascii=False, default=str)
        self.con.execute(
            """INSERT INTO source_snapshots(job_id, source_id, source_job_id, url, raw_hash, raw_json, fetched_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                job_id, job.source_id, job.source_job_id, job.canonical_url,
                tp.sha1_hex(raw_json), raw_json, now,
            ),
        )

    def _enrich(self, job_id: str, job: JobPosting, now: str) -> None:
        self.con.execute(
            "UPDATE job_postings SET description=?, search_text=?, last_seen_at=? WHERE id=?",
            (
                job.description,
                tp.tokenize_for_fts(f"{job.title} {job.employer_name} {job.description or ''}"),
                now, job_id,
            ),
        )

    # ---------- 查询 ----------

    def get(self, job_id: str) -> JobPosting | None:
        row = self.con.execute("SELECT * FROM job_postings WHERE id=?", (job_id,)).fetchone()
        return row_to_model(JobPosting, row, _JOB_JSON) if row else None

    def _search_conditions(
        self,
        query: str,
        cities: list[str] | None,
        recruitment_types: list[str] | None,
        statuses: list[str] | None,
        near_dup_only: bool,
    ) -> tuple[bool, list[str], list]:
        """构造 search 与 count_filtered 共用的 WHERE 片段。返回 (join_fts, where, args)。"""
        where: list[str] = []
        args: list = []
        join_fts = bool(query.strip())
        if join_fts:
            where.append("job_postings_fts MATCH ?")
            args.append(tp.fts_query([query]))
        if statuses:
            where.append(f"p.status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        else:
            where.append("p.status IN ('active','expired')")
        if cities:
            where.append(
                "(" + " OR ".join("p.city LIKE ?" for _ in cities) + ")"
            )
            args.extend(f"%{c}%" for c in cities)
        if recruitment_types:
            where.append(f"p.recruitment_type IN ({','.join('?' * len(recruitment_types))})")
            args.extend(recruitment_types)
        if near_dup_only:
            where.append("p.extras_json LIKE '%\"near_dup_of\"%'")
        return join_fts, where, args

    def search(
        self,
        query: str = "",
        *,
        cities: list[str] | None = None,
        recruitment_types: list[str] | None = None,
        statuses: list[str] | None = None,
        near_dup_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        ranked_profile_id: str | None = None,
    ) -> list[JobPosting]:
        """检索：FTS（中文预分词）+ 结构化过滤。query 为空时按时间倒序列举。

        ranked_profile_id 非空时改按该画像最近一次匹配结果排序：
        合格优先 → rank_score → 检索相关性 → 入库时间。岗位未参与匹配
        （无匹配记录）排最后，不隐藏——排序是呈现顺序，不是过滤。"""
        join_fts, where, args = self._search_conditions(
            query, cities, recruitment_types, statuses, near_dup_only
        )
        join_match = ""
        order = " ORDER BY p.last_seen_at DESC"
        if ranked_profile_id:
            # 每岗取该画像最近一次匹配（match_results 追加写，MAX(id) 即最新）
            join_match = (
                " LEFT JOIN match_results m ON m.job_id = p.id AND m.profile_id = ?"
                " AND m.id = (SELECT MAX(m2.id) FROM match_results m2"
                "             WHERE m2.job_id = p.id AND m2.profile_id = ?)"
            )
            args = [ranked_profile_id, ranked_profile_id, *args]
            order = (
                " ORDER BY COALESCE(m.eligible, 0) DESC, COALESCE(m.rank_score, 0) DESC,"
                " COALESCE(m.relevance_score, 0) DESC, p.last_seen_at DESC"
            )
        elif join_fts:
            order = " ORDER BY rank"
        sql = (
            "SELECT p.* FROM job_postings p"
            + (" JOIN job_postings_fts ON job_postings_fts.rowid = p.rowid" if join_fts else "")
            + join_match
            + (" WHERE " + " AND ".join(where) if where else "")
            + order
            + " LIMIT ? OFFSET ?"
        )
        rows = self.con.execute(sql, (*args, limit, offset)).fetchall()
        return [row_to_model(JobPosting, r, _JOB_JSON) for r in rows]

    def count_filtered(
        self,
        query: str = "",
        *,
        cities: list[str] | None = None,
        recruitment_types: list[str] | None = None,
        statuses: list[str] | None = None,
        near_dup_only: bool = False,
    ) -> int:
        """与 search() 同口径的过滤计数（分页总数不失真）。"""
        join_fts, where, args = self._search_conditions(
            query, cities, recruitment_types, statuses, near_dup_only
        )
        sql = (
            "SELECT COUNT(*) AS c FROM job_postings p"
            + (" JOIN job_postings_fts ON job_postings_fts.rowid = p.rowid" if join_fts else "")
            + (" WHERE " + " AND ".join(where) if where else "")
        )
        return int(self.con.execute(sql, args).fetchone()["c"])

    def count(self, statuses: list[str] | None = None) -> int:
        if statuses:
            q = f"SELECT COUNT(*) AS c FROM job_postings WHERE status IN ({','.join('?' * len(statuses))})"
            row = self.con.execute(q, statuses).fetchone()
        else:
            row = self.con.execute("SELECT COUNT(*) AS c FROM job_postings").fetchone()
        return int(row["c"])

    def set_status(self, job_id: str, status: str, reason: str | None = None) -> None:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE job_postings SET status=?, reject_reason=? WHERE id=?",
                (status, reason, job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"job 不存在: {job_id}")


# ---------- 推断器（全部把依据写入 extras.inferences，可审计可反驳） ----------


def _infer_recruitment(text: str, raw: dict) -> tuple[RecruitmentType, float]:
    if tp.hit_words(text, _INTERNSHIP_WORDS):
        return RecruitmentType.INTERNSHIP, 0.95
    if tp.hit_words(text, _CAMPUS_WORDS):
        return RecruitmentType.CAMPUS, 0.9
    if tp.hit_words(text, _SOCIAL_WORDS):
        return RecruitmentType.SOCIAL, 0.7
    exp = tp.parse_experience_years(str(raw.get("experience_required") or ""))
    if exp is not None and exp >= 1:
        return RecruitmentType.SOCIAL, 0.5
    return RecruitmentType.UNKNOWN, 0.0


def _infer_work_mode(text: str) -> WorkMode:
    if tp.hit_words(text, _REMOTE_WORDS):
        return WorkMode.REMOTE
    if tp.hit_words(text, _HYBRID_WORDS):
        return WorkMode.HYBRID
    return WorkMode.UNKNOWN


def _factual_flags(text: str) -> dict:
    """事实性旗标（非用户偏好）：猎头/外包信号。供 Gate 与 UI 提示。"""
    flags: dict = {}
    if tp.hit_words(text, _HEADHUNTER_WORDS):
        flags["headhunter_signal"] = True
    if tp.hit_words(text, _OUTSOURCING_WORDS):
        flags["outsourcing_signal"] = True
    return flags


def _is_expired(job: JobPosting) -> bool:
    today = _today()
    if job.deadline and job.deadline < today:
        return True
    # 发布超龄兜底（v1 经验保留）：无 deadline 的岗位不会自然过期，
    # 超过保守窗口的按过期处理，避免陈旧岗位长期占据列表。窗口是数据常量非偏好。
    if not job.deadline and job.published_at:
        pub = _iso_date(job.published_at)
        if pub:
            try:
                age = (dt.date.today() - dt.date.fromisoformat(pub)).days
            except ValueError:
                return False
            if age > PUBLISHED_MAX_AGE_DAYS:
                return True
    return False


def _iso_date(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()[:10]
    try:
        dt.date.fromisoformat(s)
        return s
    except ValueError:
        return None


def _join_lines(v) -> str | None:
    """list → 换行拼接文本；str 直通；空 → None。"""
    if v is None:
        return None
    if isinstance(v, list):
        joined = "\n".join(str(x).strip() for x in v if str(x).strip())
        return joined or None
    s = str(v).strip()
    return s or None
