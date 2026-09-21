"""确定性材料生成层：岗位定制简历、求职信、打招呼话术、面试题库、技能提升计划。

v1→v2 重建补齐的「无 AI 材料层」：
- 零 AI / 零网络 / 零新依赖：全部输出由用户画像条目 + JD 结构化字段经本地规则派生；
- 证据先行：凡引用画像内容的论据都携带 evidence_ids 锚点；条目不存在就不生成
  （宁缺勿造）。画像为空时，生成型方法（简历/求职信/打招呼）诚实报错，
  分析型方法（题库/提升计划）返回空结构并附说明；
- 相关度 = 条目文本片段与 JD 文本的字符 bigram Jaccard（textproc.title_similarity），
  只用于重排与择优，绝不改写事实——每个 bullet 的 evidence_ids 原样保留；
- JD 的「要求技能」只取 keywords 结构化字段（机器可读、可审计）。JD 正文中的
  技能无法在不引入词表/模型的前提下可靠枚举，keywords 缺失时如实降级并说明；
- 诚信规则：提升计划明确「真实完成后才能写进简历」。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field

from jobhater import textproc as tp
from jobhater.db.connection import transaction
from jobhater.domain.models import (
    CoverLetter,
    Education,
    Experience,
    JobPosting,
    Project,
    Skill,
)
from jobhater.services.jobs import JobService
from jobhater.services.profile import ProfileService
from jobhater.services.resume import (
    CITE_RE,
    COMMERCIAL_RISK_WORDS,
    NUMBER_RE,
    STRONG_CLAIM_WORDS,
    ResumeService,
)
from jobhater.services.storage import JsonFieldMap, new_id, row_to_model

_CL_JSON: JsonFieldMap = {"factcheck_report": ("factcheck_report_json", None)}

# 行为面通用题：不依赖画像条目，不构成任何事实主张
_BEHAVIORAL_QUESTIONS = [
    "讲一段你与他人协作达成目标的经历，按「情境-任务-行动-结果（STAR）」组织作答。",
    "说一次你收到负面反馈的经历：当时如何回应，之后做了什么改变？",
    "描述一次多任务并行的经历：你如何排序优先级、保证交付？",
]

# JD 关键词中的通用词（语言学停用词，与任何用户无关），不作为技能缺口信号
_GENERIC_TERMS = {
    "熟悉", "掌握", "了解", "优先", "能力", "经验", "良好",
    "相关", "以上", "岗位职责", "任职要求", "加分",
}

INTEGRITY_NOTE = (
    "诚信规则：以下为学习路径建议。对应能力真实掌握、产出真实完成后，才可写入简历"
    "或向面试官主张——本工具不会把未掌握的技能写进任何材料。"
)

GREETING_MAX_CHARS = 150  # 打招呼话术的组装预算（含开头结尾，约 120 字目标的上限）


class MaterialsError(ValueError):
    """材料生成失败（画像/岗位不存在或数据不足）。信息面向用户，可直接展示。"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clip(s: str | None, limit: int) -> str:
    t = tp.clean_whitespace(s or "")
    if len(t) <= limit:
        return t
    return t[:limit].rstrip() + "…"


def _first_clause(s: str | None, limit: int) -> str:
    """取第一句（按中英文句读切分）并截断——引用用户原文，不越过句界改写。"""
    t = tp.clean_whitespace(s or "")
    head = re.split(r"[。！？；;\n]", t, maxsplit=1)[0].strip()
    return _clip(head or t, limit)


def _jd_text(job: JobPosting) -> str:
    """JD 全文信号：标题/雇主/部门/描述/职责/结构化要求与关键词。"""
    parts = [
        job.title, job.employer_name, job.department or "", job.description or "",
        job.responsibilities or "", " ".join(job.requirements), " ".join(job.keywords),
    ]
    return " ".join(p for p in parts if p and p.strip())


def _jd_hits(text: str, terms: list[str]) -> bool:
    """技能词是否出现在 JD 文本。ASCII 词一律整词边界匹配——比 textproc.hit_words
    更严（后者仅约束 ≤3 字符的 ASCII 词），避免 Java 误命中 JavaScript 一类假阳性。"""
    lowered = (text or "").lower()
    for t in terms:
        lt = t.lower().strip()
        if not lt:
            continue
        if lt.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(lt)}(?![a-z0-9])", lowered):
                return True
        elif lt in lowered:
            return True
    return False


def _jd_terms(job: JobPosting) -> list[str]:
    """机器可读的 JD 技能要求信号：keywords 结构化字段（去通用词、去重、保序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for k in job.keywords:
        t = k.strip()
        if len(t) >= 2 and t not in seen and t not in _GENERIC_TERMS:
            seen.add(t)
            out.append(t)
    return out


@dataclass
class _Entry:
    """画像条目的生成视角统一封装：只暴露生成所需字段，杜绝越界编造。"""

    kind: str                # experience / project / skill
    id: str
    name: str                # 展示名
    detail: str              # 主描述文本（可空）
    fields: list[str]        # 参与相关度计算与缺口比对的文本片段
    match_terms: list[str]   # 参与「是否出现在 JD」判断的词（技能名+别名；条目为名称+标签）
    evidence_ids: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # employer/title/role/level 等结构化补充


@dataclass
class _Context:
    """一次材料生成的全部输入（一次装载，全程只读）。"""

    profile_id: str
    display_name: str
    headline: str
    summary: str
    job: JobPosting
    jd_text: str
    experiences: list[_Entry]
    projects: list[_Entry]
    skills: list[_Entry]
    educations: list[Education]
    awards: list[dict]

    @property
    def has_content(self) -> bool:
        return bool(self.experiences or self.projects or self.skills)

    @property
    def claims(self) -> list[_Entry]:
        """可支撑「为什么是我」的经历/项目条目（技能不算经历论据）。"""
        return self.experiences + self.projects


def _relevance(entry: _Entry, jd_text: str) -> float:
    """条目与 JD 的相关度：各文本片段与 JD 的字符 bigram Jaccard 取最大值。
    绝对值受 JD 长度稀释，仅用于相对排序，不做任何绝对含义解读。"""
    scores = [tp.title_similarity(f, jd_text) for f in entry.fields if f and f.strip()]
    return round(max(scores), 4) if scores else 0.0


def _reorder(entries: list[_Entry], jd_text: str) -> list[tuple[_Entry, float]]:
    """按 JD 相关度稳定重排：同分保持画像原序（sorted 稳定性），只排序不改写。"""
    scored = [(e, _relevance(e, jd_text)) for e in entries]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


class MaterialsService:
    """确定性材料生成：数据只来自画像与 JD，规则只重排/摘录/提问，不虚构。"""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self._ps = ProfileService(con)
        self._js = JobService(con)
        self._rs = ResumeService(con)

    # ---------- 内部装载 ----------

    @staticmethod
    def _entry_of_experience(e: Experience) -> _Entry:
        return _Entry(
            kind="experience", id=e.id, name=f"{e.title} · {e.employer}",
            detail=e.description or "",
            fields=[e.title, e.employer, e.description or "", *e.tags],
            match_terms=[e.title, *e.tags],
            evidence_ids=list(e.evidence_ids),
            meta={"employer": e.employer, "title": e.title},
        )

    @staticmethod
    def _entry_of_project(p: Project) -> _Entry:
        return _Entry(
            kind="project", id=p.id, name=p.name, detail=p.description or "",
            fields=[p.name, p.role or "", p.description or "", *p.tags],
            match_terms=[p.name, *p.tags],
            evidence_ids=list(p.evidence_ids),
            meta={"role": p.role or ""},
        )

    @staticmethod
    def _entry_of_skill(s: Skill) -> _Entry:
        return _Entry(
            kind="skill", id=s.id, name=s.name, detail=s.note or "",
            fields=[s.name, *s.aliases],
            match_terms=[s.name, *s.aliases],
            evidence_ids=list(s.evidence_ids),
            meta={"level": s.level},
        )

    def _load_context(self, profile_id: str, job_id: str) -> _Context:
        profile = self._ps.get_profile(profile_id)
        if profile is None:
            raise MaterialsError(f"画像不存在: {profile_id}")
        job = self._js.get(job_id)
        if job is None:
            raise MaterialsError(f"岗位不存在: {job_id}")
        awards = [
            {"title": ev.normalized_fact or ev.original_text[:60], "evidence_ids": [ev.id]}
            for ev in self._ps.list_evidence(profile_id, only_confirmed=True)
            if ev.fact_type is not None and ev.fact_type.value == "award"
        ]
        return _Context(
            profile_id=profile_id,
            display_name=profile.display_name,
            headline=profile.headline or "",
            summary=profile.summary or "",
            job=job,
            jd_text=_jd_text(job),
            experiences=[self._entry_of_experience(e) for e in self._ps.list_experiences(profile_id)],
            projects=[self._entry_of_project(p) for p in self._ps.list_projects(profile_id)],
            skills=[self._entry_of_skill(s) for s in self._ps.list_skills(profile_id)],
            educations=self._ps.list_educations(profile_id),
            awards=awards,
        )

    def _skill_split(self, ctx: _Context) -> tuple[list[str], list[tuple[str, int]]]:
        """JD 关键词二分：(画像已覆盖, 缺口)。缺口按 JD 出现次数降序（稳定保序）。"""
        haystack = " ".join(" ".join(e.fields) for e in ctx.experiences + ctx.projects + ctx.skills)
        jd_lower = ctx.jd_text.lower()
        matched: list[str] = []
        gaps: list[tuple[str, int]] = []
        for term in _jd_terms(ctx.job):
            if tp.hit_words(haystack, [term]):
                matched.append(term)
            else:
                gaps.append((term, jd_lower.count(term.lower())))
        gaps.sort(key=lambda pair: pair[1], reverse=True)
        return matched, gaps

    def _latest_job_version(self, profile_id: str, job_id: str) -> str | None:
        """该（画像,岗位）下 job_specific 简历的最新版本 id；没有则 None。"""
        row = self.con.execute(
            """SELECT rv.id FROM resumes r JOIN resume_versions rv ON rv.resume_id = r.id
               WHERE r.profile_id=? AND r.job_id=? AND r.kind='job_specific'
               ORDER BY rv.version DESC LIMIT 1""",
            (profile_id, job_id),
        ).fetchone()
        return row["id"] if row else None

    # ---------- 1. 岗位定制简历 ----------

    def build_job_resume(self, profile_id: str, job_id: str) -> dict:
        """以 master 的证据规则为基础，按 JD 相关度重排经历/项目/技能生成 job_specific 版本。

        只重排不改写：所有 bullet 文本与 evidence_ids 与 master 同源；provenance 的
        rewrite_kind 统一为 reordering。生成后立即走既有 factcheck 路径。
        """
        ctx = self._load_context(profile_id, job_id)
        if not ctx.has_content:
            raise MaterialsError("画像中没有经历/项目/技能条目，无法生成岗位定制简历——请先补充画像")
        jd = ctx.jd_text
        work_scored = _reorder(ctx.experiences, jd)
        proj_scored = _reorder(ctx.projects, jd)
        skill_scored = _reorder(ctx.skills, jd)

        exp_by_id = {e.id: e for e in self._ps.list_experiences(profile_id)}
        prj_by_id = {p.id: p for p in self._ps.list_projects(profile_id)}
        skl_by_id = {s.id: s for s in self._ps.list_skills(profile_id)}
        sections: dict = {
            "basics": {"name": ctx.display_name, "label": ctx.headline, "summary": ctx.summary},
            "work": [
                {
                    "name": e.employer,
                    "position": e.title,
                    "startDate": e.start_date or "",
                    "endDate": e.end_date or ("至今" if e.is_current else ""),
                    "summary": e.description or "",
                    "highlights": [],
                    "evidence_ids": list(e.evidence_ids),
                }
                for e in (exp_by_id[entry.id] for entry, _ in work_scored)
            ],
            "education": [
                {
                    "school": e.school, "degree": e.degree or "", "major": e.major or "",
                    "startDate": e.start_date or "", "endDate": e.end_date or "",
                    "score": (f"GPA {e.gpa}" if e.gpa else "") + (f"（{e.gpa_note}）" if e.gpa_note else ""),
                    "evidence_ids": list(e.evidence_ids),
                }
                for e in ctx.educations
            ],
            "projects": [
                {
                    "name": p.name, "role": p.role or "", "url": p.url or "",
                    "startDate": p.start_date or "", "endDate": p.end_date or "",
                    "description": p.description or "",
                    "evidence_ids": list(p.evidence_ids),
                }
                for p in (prj_by_id[entry.id] for entry, _ in proj_scored)
            ],
            "skills": [
                {"name": s.name, "level": s.level or 0, "keywords": list(s.aliases)}
                for s in (skl_by_id[entry.id] for entry, _ in skill_scored)
            ],
            "awards": [
                {"title": a["title"], "date": "", "evidence_ids": list(a["evidence_ids"])}
                for a in ctx.awards
            ],
        }
        # 只重排 → 每条 bullet 的 provenance 记 reordering，evidence_ids 原样跟随
        prov: list[dict] = []
        for i, (entry, _score) in enumerate(work_scored):
            text = sections["work"][i]["summary"]
            if text:
                prov.append({"path": f"work[{i}].summary", "text": text,
                             "evidence_ids": list(entry.evidence_ids), "rewrite_kind": "reordering"})
        for i, (entry, _score) in enumerate(proj_scored):
            desc = sections["projects"][i]["description"]
            if desc:
                prov.append({"path": f"projects[{i}].description", "text": desc,
                             "evidence_ids": list(entry.evidence_ids), "rewrite_kind": "reordering"})

        name = f"岗位定制：{ctx.job.title} · {ctx.job.employer_name}"
        rid = self._rs.create_resume(profile_id, name, kind="job_specific", job_id=job_id)
        vid = self._rs.commit_version(rid, sections, bullets_provenance=prov,
                                      note=f"按 JD 相关度重排（job_id={job_id}）")
        report = self._rs.factcheck(vid)
        return {
            "resume_id": rid,
            "version_id": vid,
            "profile_id": profile_id,
            "job_id": job_id,
            "relevance": {
                "work": [{"id": e.id, "name": e.name, "score": s} for e, s in work_scored],
                "projects": [{"id": e.id, "name": e.name, "score": s} for e, s in proj_scored],
                "skills": [{"id": e.id, "name": e.name, "score": s} for e, s in skill_scored],
            },
            "factcheck": report,
        }

    # ---------- 2. 求职信 ----------

    def build_cover_letter(self, profile_id: str, job_id: str) -> CoverLetter:
        """三段式求职信（为什么我/为什么这家/期待），写入 cover_letters 并返回模型。

        为什么我：只从画像经历/项目派生，每个论据带 [ev:*] 证据锚点；
        为什么这家：JD keywords 与画像条目的真实交集回扣，无交集时原文摘录 JD 要点；
        期待：固定礼貌收尾，不承诺任何画像/JD 之外的事实。
        生成后跑确定性自检（引用存在性/数字溯源/强主张覆盖），报告随行落库。
        """
        ctx = self._load_context(profile_id, job_id)
        if not ctx.claims:
            raise MaterialsError(
                "画像中没有经历/项目条目，无法生成求职信的「为什么是我」论据——请先补充画像"
            )
        job = ctx.job
        evidence_map = {ev.id: ev.original_text for ev in self._ps.list_evidence(profile_id)}

        lines: list[str] = [
            f"# 求职信：{job.title}（{job.employer_name}）", "", "您好：", "",
            "**为什么是我**", "",
        ]
        for e in ctx.experiences[:3]:
            anchors = "".join(f" [ev:{i}]" for i in e.evidence_ids)
            lines.append(f"- {e.meta['employer']}｜{e.meta['title']}：{_clip(e.detail, 80)}{anchors}")
        for p in ctx.projects[:2]:
            anchors = "".join(f" [ev:{i}]" for i in p.evidence_ids)
            role = f"（{p.meta['role']}）" if p.meta.get("role") else ""
            lines.append(f"- 项目「{p.name}」{role}：{_clip(p.detail, 80)}{anchors}")
        jd_hit_skills = [s for s in ctx.skills if _jd_hits(ctx.jd_text, s.match_terms)][:4]
        if jd_hit_skills:
            lines.append(f"- 技能：{'、'.join(s.name for s in jd_hit_skills)}（均见于岗位 JD）")

        lines += ["", "**为什么这家**", ""]
        overlap: list[tuple[str, _Entry]] = []
        for term in _jd_terms(job):
            for e in ctx.claims:
                if tp.hit_words(" ".join(e.fields), [term]):
                    overlap.append((term, e))
                    break
        if overlap:
            for term, e in overlap[:4]:
                anchors = "".join(f" [ev:{i}]" for i in e.evidence_ids)
                lines.append(f"- 岗位要求的「{term}」，对应我在「{e.name}」的经历{anchors}")
        else:
            jd_lines = [ln.strip() for ln in (job.description or "").splitlines() if ln.strip()][:3]
            if jd_lines:
                lines.append("以下为岗位 JD 的原文要点（未经改写摘录）：")
                lines += [f"> {ln}" for ln in jd_lines]
            else:
                lines.append("（该岗位暂无可摘录的 JD 正文——建议投递前进一步了解岗位方向，本信不作臆测。）")

        lines += [
            "", "**期待**", "",
            "如方向合适，期待有机会当面介绍上述经历的细节；也欢迎先沟通团队更具体的需求。感谢您的时间。",
        ]
        content_md = "\n".join(lines) + "\n"

        report = self._factcheck_text(content_md, evidence_map)
        resume_version_id = self._latest_job_version(profile_id, job_id)
        cid = new_id("cl")
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO cover_letters
                     (id, profile_id, job_id, resume_version_id, content_md, factcheck_report_json)
                   VALUES (?,?,?,?,?,?)""",
                (cid, profile_id, job_id, resume_version_id, content_md,
                 json.dumps(report, ensure_ascii=False)),
            )
        row = self.con.execute("SELECT * FROM cover_letters WHERE id=?", (cid,)).fetchone()
        return row_to_model(CoverLetter, row, _CL_JSON)

    def _factcheck_text(self, text: str, evidence: dict[str, str]) -> dict:
        """求职信的确定性自检（简历 factcheck 同规则族的轻量版）：只校验主张行
        （"- " 开头）；JD 摘录行（"> "）与标题/结尾不参与。规则：引用必须存在、
        数字必须溯源到所引证据原文、强主张与商业高风险词必须被证据覆盖。"""
        issues: list[dict] = []
        claim_lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
        for ln in claim_lines:
            loc = {"line": _clip(ln, 40)}
            cites = CITE_RE.findall(ln)
            bad = [c for c in cites if c not in evidence]
            if bad:
                issues.append({**loc, "type": "invalid_citation",
                               "msg": f"引用了不存在的证据ID：{bad}"})
                continue
            cited = " ".join(evidence[c] for c in cites)
            body = CITE_RE.sub("", ln)
            nums = NUMBER_RE.findall(body)
            if nums and not cites:
                issues.append({**loc, "type": "missing_citation",
                               "msg": f"包含数字 {nums} 但无证据引用"})
            else:
                for num in nums:
                    if num not in cited:
                        issues.append({**loc, "type": "unverified_number",
                                       "msg": f"数字「{num}」未在所引证据原文中出现"})
            for w in STRONG_CLAIM_WORDS + COMMERCIAL_RISK_WORDS:
                if w in body and w not in cited:
                    issues.append({**loc, "type": "unverified_claim",
                                   "msg": f"表述「{w}」未在证据原文中出现"})
        return {"passed": not issues, "lines_checked": len(claim_lines),
                "issues": issues, "checked_at": _now()}

    # ---------- 3. 打招呼话术 ----------

    def build_greeting(self, profile_id: str, job_id: str) -> dict:
        """平台首消息话术（约 120 字）。每句话都锚定画像或 JD：
        技能子句只列「画像有且 JD 提到」的技能；经历子句引用真实条目首句。
        组装超预算时按「技能 → 经历」顺序丢弃子句，绝不截断事实。"""
        ctx = self._load_context(profile_id, job_id)
        if not ctx.has_content:
            raise MaterialsError("画像中没有经历/项目/技能条目，无法生成打招呼话术——请先补充画像")
        job = ctx.job
        sources: dict = {"experience_id": None, "project_id": None, "skill_ids": [],
                         "headline_used": bool(ctx.headline)}

        opening = f"您好！我对贵司「{job.title}」岗位很感兴趣。"
        identity = (
            f"我是{ctx.display_name}（{ctx.headline}）。"
            if ctx.headline else f"我是{ctx.display_name}。"
        )

        claim_clause = ""
        for entry, _score in _reorder(ctx.claims, ctx.jd_text):
            if not entry.detail:
                continue
            excerpt = _first_clause(entry.detail, 40)
            if entry.kind == "experience":
                claim_clause = (
                    f"我曾在{entry.meta['employer']}担任{entry.meta['title']}，{excerpt}。"
                )
            else:
                claim_clause = f"我做过「{entry.name}」：{excerpt}。"
            sources[f"{entry.kind}_id"] = entry.id
            break

        matched = [s for s in ctx.skills if _jd_hits(ctx.jd_text, s.match_terms)][:4]
        skill_clause = ""
        if matched:
            skill_clause = f"熟悉{'、'.join(s.name for s in matched)}，与岗位要求方向一致。"
            sources["skill_ids"] = [s.id for s in matched]

        closing = "期待能进一步沟通，我可以随时发送简历，谢谢！"
        body = opening + identity
        for clause in (claim_clause, skill_clause):
            if clause and len(body + clause + closing) <= GREETING_MAX_CHARS:
                body += clause
        greeting = body + closing
        return {"greeting": greeting, "length": len(greeting), "sources": sources}

    # ---------- 4. 面试题库 ----------

    def build_interview_questions(self, profile_id: str, job_id: str) -> dict:
        """四板块题库。防编造约束：题目只能引用真实画像条目（anchors 带来源 id）；
        画像为空时项目/技术板块留空并说明，仅保留通用行为面题与 JD 缺口反问。"""
        ctx = self._load_context(profile_id, job_id)
        deep: list[dict] = []
        for entry, _score in _reorder(ctx.claims, ctx.jd_text)[:4]:
            anchors = [{"kind": entry.kind, "id": entry.id, "name": entry.name}]
            basis = "来自画像经历条目" if entry.kind == "experience" else "来自画像项目条目"
            qs = [f"请具体介绍「{entry.name}」：你负责的部分、协作方式与最终产出是什么？"]
            if entry.detail:
                qs.append(f"你提到「{_first_clause(entry.detail, 36)}」——"
                          "当时最大的难点是什么，如何解决的？")
            qs.append(f"复盘「{entry.name}」：如果重来一次，哪个环节你会换一种做法？")
            for q in qs:
                deep.append({"question": q, "anchors": anchors, "basis": basis})
        matched = [s for s in ctx.skills if _jd_hits(ctx.jd_text, s.match_terms)][:6]
        tech: list[dict] = []
        for s in matched:
            level = s.meta.get("level")
            note = (f"画像自评 level={level}/5，请准备与自评一致的表述"
                    if level is not None else None)
            tech.append({
                "question": f"结合真实经历说明「{s.name}」：你在什么场景用过、解决过什么问题？",
                "anchors": [{"kind": "skill", "id": s.id, "name": s.name}],
                "note": note,
            })
        behavioral = [
            {"question": q, "anchors": [], "basis": "通用行为面题（STAR 结构），不依赖画像条目"}
            for q in _BEHAVIORAL_QUESTIONS
        ]
        _matched_terms, gaps = self._skill_split(ctx)
        reverse = [
            {
                "question": f"关于 JD 提到的「{name}」：团队目前的选型与实践现状是怎样的？"
                            "新人入职后在这块承担什么职责？",
                "anchors": [{"kind": "jd_gap", "name": name}],
            }
            for name, _occ in gaps[:4]
        ]
        notes: list[str] = []
        if not ctx.has_content:
            notes.append("画像为空：项目深挖与技术基础题无法生成（不编造条目），"
                         "仅提供通用行为面题与基于 JD 关键词的反问建议。")
        elif ctx.skills and not matched:
            notes.append("画像技能无一出现在 JD 文本：技术基础题留空，缺口见反问与提升计划。")
        if not _jd_terms(ctx.job):
            notes.append("岗位未提供 keywords 结构化字段：反问/缺口信号有限，"
                         "建议补全 JD 关键词后重新生成。")
        return {
            "profile_id": profile_id,
            "job_id": job_id,
            "sections": {
                "project_deep_dive": deep,
                "technical_basics": tech,
                "behavioral": behavioral,
                "reverse_questions": reverse,
            },
            "notes": notes,
        }

    # ---------- 5. 技能提升计划 ----------

    def build_upskill_plan(self, profile_id: str, job_id: str) -> dict:
        """JD keywords 与画像技能/经历的缺口分析 + 14/30/60 天冲刺路径模板。
        缺口只认 keywords 结构化字段（正文技能枚举需要词表/模型，本层不臆测）；
        文案明确诚信规则：真实完成后才能写进简历。"""
        ctx = self._load_context(profile_id, job_id)
        terms = _jd_terms(ctx.job)
        matched_terms, gaps = self._skill_split(ctx)

        claims = ctx.claims + ctx.skills
        matched: list[dict] = []
        for term in matched_terms:
            via = [
                {"kind": e.kind, "id": e.id, "name": e.name}
                for e in claims if tp.hit_words(" ".join(e.fields), [term])
            ][:2]
            matched.append({"name": term, "via": via})

        gap_items: list[dict] = []
        for name, occ in gaps:
            steps = [
                f"通读{name}的官方入门文档或一门入门课，写下：它解决什么问题、核心概念清单",
                f"做一个{name}的最小练习（真实可运行，不追求上线），把核心概念各用一次",
                f"对照本岗位 JD 中{name}的使用场景，把练习改造得更接近真实用途，输出复盘笔记",
                f"在{name}相关的社区回答问题或发一篇总结，用外部反馈检验理解",
            ]
            gap_items.append({
                "name": name,
                "occurrences_in_jd": occ,
                "steps": steps,
                "milestone": f"能脱稿讲清{name}是什么、何时该用/不该用，并展示练习产出",
            })

        phases: dict[str, list[str]] = {"14d": [], "30d": [], "60d": []}
        for name, _occ in gaps[:2]:
            phases["14d"].append(f"【{name}】走完学习路径第 1 步（入门文档/课程），建立概念清单")
        for name, _occ in gaps[:3]:
            phases["30d"].append(f"【{name}】完成第 1-3 步：入门 → 最小练习 → 对照 JD 场景改造并复盘")
        for item in gap_items:
            phases["60d"].append(f"【{item['name']}】完成全部 4 步，里程碑：{item['milestone']}")
        if gap_items:
            phases["60d"].append("对照 JD 逐条复盘：真实掌握的更新进画像；尚未掌握的保持诚实留白")

        notes: list[str] = []
        if not terms:
            notes.append("岗位未提供 keywords 结构化字段：无法可靠识别技能缺口"
                         "（JD 正文的技能枚举需要词表或模型，本层不做臆测）——"
                         "请补全 JD 关键词后重新生成。")
        elif not matched_terms:
            notes.append("JD 关键词与画像零重合：请确认 JD 关键词是否准确，或补充画像技能/经历。")
        return {
            "profile_id": profile_id,
            "job_id": job_id,
            "integrity_note": INTEGRITY_NOTE,
            "matched_skills": matched,
            "gaps": gap_items,
            "phases": phases,
            "notes": notes,
        }
