"""分层匹配引擎（§8）：

  A. Eligibility Gate   —— 硬性约束，全部来自用户 preset，可配置，透明拒绝
  B. Retrieval/Relevance—— FTS BM25 + 结构化信号，岗位-画像相关性
  C. Personalized Rank  —— 加权维度分，权重/阈值是用户数据而非代码常量
  D. (LLM Deep Review)  —— 可选增强层，由 AI Provider 提供；永远不能推翻 A 层事实

铁律：
- 不预设立场：国企≠更好、专业对口≠优先、薪资高≠更好、应届≠不能投社招——
  这些都是 Gate/维度的用户配置，不是系统价值判断。
- 每个结果必须可解释：结论 + 依据 + 不确定性，三者齐全。
- 同输入同输出：纯函数 + engine_version 版本化，结果可复现可回放。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from jobhater import textproc as tp
from jobhater.db.connection import transaction
from jobhater.domain.models import DimensionScore, GateOutcome, JobPosting, MatchOutcome
from jobhater.services.profile import degree_rank

ENGINE_VERSION = "2.0.0"

# 维度默认权重与结论阈值——"数据层默认"而非代码铁律：用户在 preset.weights 可全量覆盖。
DEFAULT_WEIGHTS: dict[str, float] = {
    "skill_match": 0.30,
    "experience_relevance": 0.25,
    "location_fit": 0.15,
    "salary_fit": 0.15,
    "recency": 0.10,
    "feedback": 0.05,
}
DEFAULT_VERDICTS: dict[str, float] = {"recommend": 75, "consider": 55}

# 反馈事件的排序影响（数据驱动，可查看可重置）。惩罚默认强于奖励（机会成本不对称）。
FEEDBACK_WEIGHTS: dict[str, float] = {
    "interested": 1.0,
    "not_interested": -1.5,
    "too_far": -1.0,
    "low_pay": -1.0,
    "bad_industry": -1.5,
    "bad_company": -2.0,
    "skill_mismatch": -1.0,
    "applied": 0.5,
    "rejected": -0.5,
    "got_interview": 1.0,
    "got_offer": 1.0,
}

_GRAD_YEAR_RE_STR = r"(20\d{2})\s*届"


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%f.%f")[:-3] + "Z"


def _job_text(job: JobPosting) -> str:
    parts = [
        job.title, job.employer_name, job.department or "", job.description or "",
        job.responsibilities or "", " ".join(job.keywords), job.city or "",
        job.experience_required_text or "",
    ]
    return " ".join(p for p in parts if p).lower()


# ========== A. Eligibility Gates ==========


def eligibility_gates(
    job: JobPosting,
    profile_view: dict,
    preset,
) -> list[GateOutcome]:
    """逐条评估硬性 Gate。passed=False 即淘汰；全部结论（含通过）都保留供展示。"""
    out: list[GateOutcome] = []
    text = _job_text(job)

    def add(code: str, passed: bool, detail: str) -> None:
        out.append(GateOutcome(code=code, passed=passed, detail=detail))

    gates_cfg: dict = preset.gates or {}

    # 招聘批次类型（unknown 不淘汰——数据不足不是候选人的错，标注不确定性）
    if preset.employment_types:
        rt = job.recruitment_type.value
        if rt == "unknown":
            add("recruitment_type", True, "信源未提供批次类型，无法核验（不淘汰，建议人工确认）")
        elif rt not in preset.employment_types:
            add("recruitment_type", False, f"岗位为 {rt}，偏好为 {preset.employment_types}")
        else:
            add("recruitment_type", True, f"批次类型 {rt} 符合偏好")

    # 经验年限
    if (
        preset.max_experience_years_required is not None
        and job.experience_required_min is not None
        and job.experience_required_min > preset.max_experience_years_required
    ):
        add(
            "experience_over_max", False,
            f"岗位要求 ≥{job.experience_required_min:g} 年经验，"
            f"偏好上限 {preset.max_experience_years_required:g} 年",
        )
    elif job.experience_required_min is not None:
        add("experience_over_max", True, f"岗位要求 ≥{job.experience_required_min:g} 年经验，在可接受范围")

    # 学历（双方都可判定时才硬判；缺数据 → 通过+不确定性）
    job_edu = degree_rank(job.education_required)
    my_edu = max(
        (degree_rank(e.get("degree")) or 0 for e in profile_view.get("educations", [])),
        default=0,
    )
    if job_edu is not None:
        if my_edu == 0:
            add("education", True, "岗位要求学历但画像未填学历，无法核验（不淘汰，建议补全画像）")
        elif job_edu > my_edu:
            add("education", False,
                f"岗位要求 {job.education_required}，画像最高学历不足（gate 可在偏好中关闭）")
        else:
            add("education", True, "学历满足要求")

    # 城市/远程
    if preset.target_cities:
        city = job.city or ""
        remote = job.work_mode.value == "remote"
        hit = any(c and (c in city or city and city in c) for c in preset.target_cities)
        if hit:
            add("city", True, f"{city} 在目标城市 {preset.target_cities} 内")
        elif remote and preset.remote_ok:
            add("city", True, "岗位支持远程，符合远程偏好")
        else:
            add("city", False, f"{city or '城市未知'} 不在目标城市 {preset.target_cities} 内")

    # 薪资底线
    if preset.salary_min_k is not None:
        if job.salary_max_k is None:
            if not preset.accept_incomplete_salary:
                add("salary_floor", False, "薪资未标注且偏好要求明确薪资")
            else:
                add("salary_floor", True, "薪资未标注（按偏好放行，面试前需确认）")
        elif job.salary_max_k < preset.salary_min_k:
            add("salary_floor", False,
                f"薪资上限 {job.salary_max_k:g}K 低于底线 {preset.salary_min_k:g}K")
        else:
            add("salary_floor", True, f"薪资上限 {job.salary_max_k:g}K ≥ 底线 {preset.salary_min_k:g}K")

    # 排除项
    if any(tp.norm_key(e) == tp.norm_key(job.employer_name) for e in preset.exclude_employers):
        add("excluded_employer", False, f"{job.employer_name} 在排除企业名单中")
    if preset.exclude_keywords:
        hits = tp.hit_words(text, preset.exclude_keywords)
        if hits:
            add("excluded_keyword", False, f"命中排除关键词：{hits[:5]}")

    # 可选 Gate（默认关，用户开启）
    flags = (job.extras or {}).get("flags", {})
    if gates_cfg.get("exclude_headhunter") and flags.get("headhunter_signal"):
        add("headhunter", False, "检测到猎头/RPO 信号")
    if gates_cfg.get("exclude_outsourcing") and flags.get("outsourcing_signal"):
        add("outsourcing", False, "检测到外包/驻场信号")
    if job.status.value == "expired":
        add("expired", False, "岗位已过截止日期")
    elif gates_cfg.get("exclude_expired") is False:
        pass  # 用户显式关闭过期 gate 时，过期岗位仍参与（详情页仍会展示过期状态）

    # 毕业年份（校招定向批次：JD 明确写了届数且与用户不符 → 淘汰）
    if preset.graduation_year and job.recruitment_type.value == "campus":
        import re

        years = {int(y) for y in re.findall(_GRAD_YEAR_RE_STR, text)}
        if years and preset.graduation_year not in years:
            add("graduation_year", False,
                f"岗位面向 {sorted(years)} 届，与你的 {preset.graduation_year} 届不符")

    return out


# ========== B. Relevance（检索信号） ==========


def _bm25_scores(
    con: sqlite3.Connection, terms: list[str], limit: int = 500
) -> dict[int, float]:
    """对 FTS 查询返回 {rowid: 归一化 bm25 信号 0-100}。无词项时返回空。"""
    expr = tp.fts_query(terms)
    if not expr.strip():
        return {}
    try:
        rows = con.execute(
            "SELECT rowid, rank FROM job_postings_fts WHERE job_postings_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    # fts5 rank = -bm25（越小越相关）。转正后按最大值归一。
    raw = {r["rowid"]: -r["rank"] for r in rows}
    mx = max(raw.values())
    if mx <= 0:
        return {k: 0.0 for k in raw}
    return {k: 100.0 * v / mx for k, v in raw.items()}


# ========== C. 维度评分 ==========


def _core_skills(profile_view: dict, top: int = 10) -> list[dict]:
    skills = list(profile_view.get("skills", []))
    skills.sort(key=lambda s: (s.get("level") or 0, s.get("years") or 0), reverse=True)
    return skills[:top]


def dimension_scores(
    job: JobPosting,
    profile_view: dict,
    preset,
    relevance: float | None,
    employer_feedback_sum: float | None,
) -> tuple[dict[str, DimensionScore], dict]:
    """返回 (维度分, 匹配证据)。每个维度：分数+依据+不确定性。"""
    text = _job_text(job)
    dims: dict[str, DimensionScore] = {}
    evidence: dict = {}

    # --- skill_match：核心技能在 JD 中的覆盖 ---
    core = _core_skills(profile_view)
    if not core:
        dims["skill_match"] = DimensionScore(
            score=50, reasons=["画像未填技能，按中性分50"], uncertainty="画像技能缺失"
        )
    else:
        matched, missing = [], []
        for s in core:
            words = [s["name"]] + list(s.get("aliases") or [])
            if tp.hit_words(text, words):
                matched.append(s["name"])
            else:
                missing.append(s["name"])
        evidence["matched_skills"] = matched
        evidence["unmatched_core_skills"] = missing
        if not matched:
            dims["skill_match"] = DimensionScore(
                score=30, reasons=[f"核心技能无一出现在 JD（缺口：{missing[:6]}）"],
                uncertainty=None,
            )
        else:
            score = round(100 * len(matched) / len(core))
            score = max(score, 30)  # 有命中时保底30，避免长尾技能稀释
            dims["skill_match"] = DimensionScore(
                score=score,
                reasons=[f"核心技能命中 {len(matched)}/{len(core)}：{matched[:8]}"],
            )
        if relevance is not None:
            note = f"检索相关性(BM25归一) {relevance:.0f}/100"
            dims["skill_match"].reasons.append(note)

    # --- experience_relevance：经历/项目标签与目标角色 ---
    tags: set[str] = set()
    for e in profile_view.get("experiences", []):
        tags.update(t.lower() for t in (e.get("tags") or []))
        if e.get("title"):
            tags.add(str(e["title"]).lower())
    for p in profile_view.get("projects", []):
        tags.update(t.lower() for t in (p.get("tags") or []))
    role_hits = []
    for role in preset.target_roles or []:
        if role.lower() in text or tp.title_similarity(role, job.title) >= 0.3:
            role_hits.append(role)
    tag_list = sorted(tags)
    tag_hits = [t for t in tag_list if t and t in text]
    if not tag_list and not preset.target_roles:
        dims["experience_relevance"] = DimensionScore(
            score=50, reasons=["画像无经历标签且偏好无目标角色，按中性分50"],
            uncertainty="经历信号缺失",
        )
    else:
        denom = max(len(tag_list), 1)
        score = round(100 * len(tag_hits) / denom)
        score = max(score, 20)
        if role_hits:
            score = min(100, score + 20)
        reasons = []
        if tag_hits:
            reasons.append(f"经历/项目标签命中 {len(tag_hits)}/{len(tag_list)}：{tag_hits[:6]}")
        if role_hits:
            reasons.append(f"目标角色信号：{role_hits[:3]}")
        if not reasons:
            reasons.append("画像经历标签与 JD 无直接交集")
        dims["experience_relevance"] = DimensionScore(score=score, reasons=reasons)
        evidence["tag_hits"] = tag_hits
        evidence["role_hits"] = role_hits

    # --- location_fit ---
    if not preset.target_cities:
        dims["location_fit"] = DimensionScore(score=60, reasons=["未设置目标城市，按中性分60"])
    else:
        city = job.city or ""
        if any(c in city or (city and city in c) for c in preset.target_cities):
            dims["location_fit"] = DimensionScore(score=100, reasons=[f"{city} 在目标城市内"])
        elif job.work_mode.value == "remote" and preset.remote_ok:
            dims["location_fit"] = DimensionScore(score=90, reasons=["支持远程"])
        elif city:
            dims["location_fit"] = DimensionScore(score=30, reasons=[f"{city} 不在目标城市内"])
        else:
            dims["location_fit"] = DimensionScore(
                score=60, reasons=["城市未知"], uncertainty="地点数据缺失"
            )

    # --- salary_fit ---
    if preset.salary_min_k is None:
        dims["salary_fit"] = DimensionScore(score=60, reasons=["未设置薪资期望，按中性分60"])
    elif job.salary_max_k is None:
        dims["salary_fit"] = DimensionScore(
            score=60, reasons=["薪资未标注"], uncertainty="薪资数据缺失，面试前确认"
        )
    elif job.salary_max_k >= preset.salary_min_k:
        dims["salary_fit"] = DimensionScore(
            score=100,
            reasons=[f"上限 {job.salary_max_k:g}K ≥ 期望 {preset.salary_min_k:g}K"],
        )
    else:
        ratio = job.salary_max_k / preset.salary_min_k
        dims["salary_fit"] = DimensionScore(
            score=round(60 * ratio),
            reasons=[f"上限 {job.salary_max_k:g}K < 期望 {preset.salary_min_k:g}K，按比例给分"],
        )

    # --- recency ---
    ref = job.published_at or job.last_seen_at
    if ref and ref[:10].isdigit() and len(ref) >= 10:
        try:
            days = (dt.date.today() - dt.date.fromisoformat(ref[:10])).days
        except ValueError:
            days = None
    else:
        days = None
    if days is None:
        dims["recency"] = DimensionScore(score=50, reasons=["发布时间未知"], uncertainty="时效数据缺失")
    elif days <= 7:
        dims["recency"] = DimensionScore(score=100, reasons=[f"{days} 天内发布"])
    elif days <= 30:
        dims["recency"] = DimensionScore(score=round(100 - (days - 7) * (30 / 23)), reasons=[f"{days} 天前发布"])
    elif days <= 90:
        dims["recency"] = DimensionScore(score=round(70 - (days - 30) * (30 / 60)), reasons=[f"{days} 天前发布"])
    else:
        dims["recency"] = DimensionScore(score=20, reasons=[f"{days} 天前发布，信息可能过时"])

    # --- feedback ---
    if employer_feedback_sum is None:
        dims["feedback"] = DimensionScore(score=50, reasons=["暂无对该雇主的反馈记录"])
    else:
        adj = max(-20.0, min(20.0, employer_feedback_sum)) * 2.5
        dims["feedback"] = DimensionScore(
            score=round(50 + adj),
            reasons=[f"历史反馈净分 {employer_feedback_sum:+g}（可查看可重置）"],
        )

    return dims, evidence


def combine(
    dims: dict[str, DimensionScore], weights_cfg: dict
) -> tuple[float | None, str | None]:
    """加权合成总分与结论。权重缺失的维度不参与（而不是按0分惩罚）。"""
    weights = {**DEFAULT_WEIGHTS, **(weights_cfg or {})}
    used = {k: v for k, v in weights.items() if k in dims and isinstance(v, (int, float)) and v != 0}
    if not used:
        return None, None
    total_w = sum(used.values())
    total = sum(dims[k].score * w for k, w in used.items()) / total_w
    verdicts = {**DEFAULT_VERDICTS, **(weights_cfg or {}).get("verdicts", {})}
    if total >= verdicts["recommend"]:
        label = "推荐"
    elif total >= verdicts["consider"]:
        label = "可考虑"
    else:
        label = "暂缓"
    return round(total, 1), label


# ========== 服务封装（持久化 + 批量） ==========


class MatchService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def evaluate(
        self,
        job: JobPosting,
        profile_view: dict,
        preset,
        *,
        relevance: float | None = None,
        feedback_sum: float | None = None,
    ) -> MatchOutcome:
        gates = eligibility_gates(job, profile_view, preset)
        eligible = all(g.passed for g in gates)
        dims, evidence = dimension_scores(job, profile_view, preset, relevance, feedback_sum)
        rank_score, verdict = combine(dims, preset.weights or {})
        uncertainty_count = sum(1 for d in dims.values() if d.uncertainty)
        needs_review = (
            uncertainty_count >= 2
            or bool((job.extras or {}).get("near_dup_of"))
        )
        rel = None if relevance is None else round(relevance, 1)
        return MatchOutcome(
            job_id=job.id,
            profile_id=profile_view["profile"]["id"],
            preset_id=getattr(preset, "id", None),
            engine_version=ENGINE_VERSION,
            eligible=eligible,
            gate_reasons=gates,
            relevance_score=rel,
            rank_score=rank_score,
            verdict=verdict if eligible else "不符合硬性条件",
            dims=dims,
            evidence=evidence,
            needs_review=needs_review,
        )

    def persist(self, outcome: MatchOutcome) -> None:
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO match_results (
                     id, job_id, profile_id, preset_id, engine_version, eligible,
                     gate_reasons_json, relevance_score, rank_score, verdict,
                     dims_json, evidence_json, needs_review
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"mt_{outcome.job_id}_{outcome.profile_id}_{dt.datetime.now(dt.UTC).strftime('%H%M%S%f')}",
                    outcome.job_id, outcome.profile_id, outcome.preset_id,
                    outcome.engine_version, outcome.eligible,
                    json.dumps([g.model_dump() for g in outcome.gate_reasons], ensure_ascii=False),
                    outcome.relevance_score, outcome.rank_score, outcome.verdict,
                    json.dumps({k: v.model_dump() for k, v in outcome.dims.items()}, ensure_ascii=False),
                    json.dumps(outcome.evidence, ensure_ascii=False),
                    outcome.needs_review,
                ),
            )

    def rank_jobs(
        self,
        profile_view: dict,
        preset,
        jobs: list[JobPosting],
    ) -> list[MatchOutcome]:
        """批量评估 + 持久化，按（eligible 优先、rank_score 降序）返回。"""
        # 反馈聚合：employer_name → 净分
        profile_id = profile_view["profile"]["id"]
        fb_rows = self.con.execute(
            """SELECT j.employer_name AS name, f.kind AS kind FROM feedback_events f
                 JOIN job_postings j ON j.id = f.job_id
                 WHERE f.profile_id=? AND j.employer_name != ''""",
            (profile_id,),
        ).fetchall()
        fb_by_employer: dict[str, float] = {}
        for r in fb_rows:
            fb_by_employer[r["name"]] = (
                fb_by_employer.get(r["name"], 0.0) + FEEDBACK_WEIGHTS.get(r["kind"], 0.0)
            )
        # BM25 相关性（一次查询批量取）
        terms = list(preset.target_roles or []) + [
            s["name"] for s in _core_skills(profile_view)
        ]
        bm25 = _bm25_scores(self.con, terms, limit=2000)
        rowid_by_id = {
            r["id"]: r["rowid"]
            for r in self.con.execute("SELECT id, rowid FROM job_postings").fetchall()
        }
        outcomes = []
        for job in jobs:
            rel = bm25.get(rowid_by_id.get(job.id, -1))
            fb = fb_by_employer.get(job.employer_name)
            outcome = self.evaluate(job, profile_view, preset, relevance=rel, feedback_sum=fb)
            self.persist(outcome)
            outcomes.append(outcome)
        outcomes.sort(key=lambda o: (not o.eligible, -(o.rank_score or 0)))
        return outcomes

    def latest_for_job(self, job_id: str, profile_id: str) -> MatchOutcome | None:
        row = self.con.execute(
            """SELECT * FROM match_results WHERE job_id=? AND profile_id=?
                 AND engine_version=? ORDER BY computed_at DESC LIMIT 1""",
            (job_id, profile_id, ENGINE_VERSION),
        ).fetchone()
        if not row:
            return None
        return MatchOutcome(
            job_id=row["job_id"], profile_id=row["profile_id"], preset_id=row["preset_id"],
            engine_version=row["engine_version"], eligible=bool(row["eligible"]),
            gate_reasons=[GateOutcome(**g) for g in json.loads(row["gate_reasons_json"])],
            relevance_score=row["relevance_score"], rank_score=row["rank_score"],
            verdict=row["verdict"],
            dims={k: DimensionScore(**v) for k, v in json.loads(row["dims_json"]).items()},
            evidence=json.loads(row["evidence_json"]),
            needs_review=bool(row["needs_review"]),
        )
