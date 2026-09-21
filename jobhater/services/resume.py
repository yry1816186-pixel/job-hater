"""简历服务：master → version → 岗位定制版，证据溯源贯穿全程。

设计（§10/§11）：
- sections 采用 JSON Resume 兼容结构（basics/work/education/projects/skills/awards），
  兼容生态工具链（schema 校验器/主题）；
- 每条 bullet 的 provenance 记录 {证据ID, 改写类别}；改写类别六档：
  verbatim / factual_rewrite / emphasis / reordering / keyword_alignment / unsupported；
- factcheck 是确定性守门（v1 factcheck 的泛化移植）：引用覆盖/证据存在/数字溯源/
  高危主张核验/unsupported 阻断——不过闸不标记 final；
- 导出：Markdown/JSON 原生；HTML 自包含可打印；PDF 经 playwright（可选），
  缺失时降级为"浏览器打印"路径并如实说明；DOCX 经 python-docx（可选）。
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sqlite3
from pathlib import Path

from jobhater import config
from jobhater.db.connection import transaction
from jobhater.services.storage import new_id

# ---- factcheck 规则常量（与 v1 对齐，泛化到任意用户） ----
CITE_RE = re.compile(r"\[ev:([A-Za-z0-9_\-]+)\]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
STRONG_CLAIM_WORDS = ["独立完成", "独立作者", "主导", "首创", "从零搭建", "从0到1", "独立开发", "唯一负责人"]
COMMERCIAL_RISK_WORDS = ["商业", "营收", "收入", "签约", "甲方", "客户", "盈利", "百万", "千万", "亿", "中标", "成交"]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ResumeError(ValueError):
    pass


class ResumeService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 简历与版本 ----------

    def create_resume(self, profile_id: str, name: str, *, kind: str = "master",
                      job_id: str | None = None) -> str:
        rid = new_id("rsm")
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO resumes(id, profile_id, name, kind, job_id) VALUES (?,?,?,?,?)",
                (rid, profile_id, name, kind, job_id),
            )
        return rid

    def build_master_from_profile(self, profile_id: str, name: str = "主简历") -> tuple[str, str]:
        """从画像簇自动生成 v1：每条 bullet 溯源到画像条目的 evidence_ids。"""
        from jobhater.services.profile import ProfileService

        ps = ProfileService(self.con)
        profile = ps.get_profile(profile_id)
        if profile is None:
            raise ResumeError(f"画像不存在: {profile_id}")
        educations = ps.list_educations(profile_id)
        experiences = ps.list_experiences(profile_id)
        projects = ps.list_projects(profile_id)
        skills = ps.list_skills(profile_id)
        awards = ps.list_evidence(profile_id, only_confirmed=True)

        sections: dict = {
            "basics": {
                "name": profile.display_name,
                "label": profile.headline or "",
                "summary": profile.summary or "",
                "phone": profile.phone or "",
                "email": profile.email or "",
            },
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
                for e in experiences
            ],
            "education": [
                {
                    "school": e.school, "degree": e.degree or "", "major": e.major or "",
                    "startDate": e.start_date or "", "endDate": e.end_date or "",
                    "score": (f"GPA {e.gpa}" if e.gpa else "") + (f"（{e.gpa_note}）" if e.gpa_note else ""),
                    "evidence_ids": list(e.evidence_ids),
                }
                for e in educations
            ],
            "projects": [
                {
                    "name": p.name, "role": p.role or "", "url": p.url or "",
                    "startDate": p.start_date or "", "endDate": p.end_date or "",
                    "description": p.description or "",
                    "evidence_ids": list(p.evidence_ids),
                }
                for p in projects
            ],
            "skills": [
                {"name": s.name, "level": s.level or 0, "keywords": list(s.aliases)}
                for s in skills
            ],
            "awards": [
                {"title": ev.normalized_fact or ev.original_text[:60],
                 "date": "", "evidence_ids": [ev.id]}
                for ev in awards if ev.fact_type and ev.fact_type.value == "award"
            ],
        }
        rid = self.create_resume(profile_id, name)
        vid = self.commit_version(rid, sections, note="从画像自动生成 v1")
        return rid, vid

    def commit_version(
        self, resume_id: str, sections: dict, *,
        bullets_provenance: list[dict] | None = None, note: str | None = None,
        parent_version_id: str | None = None,
    ) -> str:
        """提交新版本（版本号自增，不可变——修改=新版本）。"""
        with transaction(self.con):
            row = self.con.execute(
                "SELECT current_version FROM resumes WHERE id=?", (resume_id,)
            ).fetchone()
            if not row:
                raise ResumeError(f"简历不存在: {resume_id}")
            version = row["current_version"] + 1
            vid = new_id("rv")
            self.con.execute(
                """INSERT INTO resume_versions (id, resume_id, version, sections_json,
                     bullets_provenance_json, parent_version_id, note)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    vid, resume_id, version,
                    json.dumps(sections, ensure_ascii=False),
                    json.dumps(bullets_provenance or [], ensure_ascii=False),
                    parent_version_id, note,
                ),
            )
            self.con.execute(
                "UPDATE resumes SET current_version=?, updated_at=? WHERE id=?",
                (version, _now(), resume_id),
            )
        return vid

    def get_version(self, version_id: str) -> dict:
        row = self.con.execute(
            "SELECT * FROM resume_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not row:
            raise ResumeError(f"版本不存在: {version_id}")
        d = dict(row)
        d["sections"] = json.loads(d.pop("sections_json"))
        d["bullets_provenance"] = json.loads(d.pop("bullets_provenance_json") or "[]")
        d["factcheck_report"] = json.loads(d.pop("factcheck_report_json")) if d.get("factcheck_report_json") else None
        return d

    def latest_version(self, resume_id: str) -> dict | None:
        row = self.con.execute(
            "SELECT id FROM resume_versions WHERE resume_id=? ORDER BY version DESC LIMIT 1",
            (resume_id,),
        ).fetchone()
        return self.get_version(row["id"]) if row else None

    def list_versions(self, resume_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT id, version, note, created_at, factcheck_report_json IS NOT NULL AS checked "
            "FROM resume_versions WHERE resume_id=? ORDER BY version DESC",
            (resume_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_resumes(self, profile_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM resumes WHERE profile_id=? AND status != 'archived' ORDER BY updated_at DESC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- factcheck（确定性守门） ----------

    def factcheck(self, version_id: str) -> dict:
        """对版本全文做真实性硬校验并回写报告。passed=False 禁止标记 final。"""
        version = self.get_version(version_id)
        profile_id_row = self.con.execute(
            "SELECT profile_id FROM resumes WHERE id=?", (version["resume_id"],)
        ).fetchone()
        profile_id = profile_id_row["profile_id"]
        evidence: dict[str, str] = {
            r["id"]: r["original_text"]
            for r in self.con.execute(
                "SELECT id, original_text FROM evidence WHERE profile_id=?", (profile_id,)
            ).fetchall()
        }
        bullets = self._extract_bullets(version["sections"])
        prov_by_text = {b["text"]: b for b in version["bullets_provenance"]}
        issues: list[dict] = []

        for path, text, item_evidence in bullets:
            prov = prov_by_text.get(text)
            cites = CITE_RE.findall(text)
            inline_ev = list(item_evidence) + (prov.get("evidence_ids", []) if prov else [])
            cited_text = " ".join(evidence.get(c, "") for c in cites + inline_ev)

            if not (cites or inline_ev):
                issues.append({"path": path, "type": "missing_citation",
                               "msg": "条目无证据引用——所有经历条目必须可溯源"})
                continue
            bad = [c for c in cites + inline_ev if c not in evidence]
            if bad:
                issues.append({"path": path, "type": "invalid_citation",
                               "msg": f"引用了不存在的证据ID：{bad}"})
                continue
            if prov and prov.get("rewrite_kind") == "unsupported":
                issues.append({"path": path, "type": "unsupported_claim",
                               "msg": "改写类别为 unsupported（AI 新增无证据主张）——禁止入终稿"})
            body = CITE_RE.sub("", text)
            for num in NUMBER_RE.findall(body):
                if num not in cited_text:
                    issues.append({"path": path, "type": "unverified_number",
                                   "msg": f"数字「{num}」未在所引证据原文中出现"})
            for w in STRONG_CLAIM_WORDS:
                if w in body and w not in cited_text:
                    issues.append({"path": path, "type": "unverified_claim",
                                   "msg": f"强主张「{w}」未在证据原文中出现"})
            for w in COMMERCIAL_RISK_WORDS:
                if w in body and w not in cited_text:
                    issues.append({"path": path, "type": "commercial_risk",
                                   "msg": f"高风险表述「{w}」未在证据原文中出现（学生材料需终审）"})
        report = {"passed": not issues, "bullets_checked": len(bullets),
                  "issues": issues, "checked_at": _now()}
        with transaction(self.con):
            self.con.execute(
                "UPDATE resume_versions SET factcheck_report_json=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), version_id),
            )
        return report

    def mark_final(self, version_id: str) -> dict:
        report = self.con.execute(
            "SELECT factcheck_report_json FROM resume_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not report:
            raise ResumeError(f"版本不存在: {version_id}")
        parsed = json.loads(report["factcheck_report_json"]) if report["factcheck_report_json"] else None
        if not parsed or not parsed.get("passed"):
            raise ResumeError(
                "真实性校验未通过（或未运行），不能标记为 final——先运行 factcheck 并修复问题"
            )
        with transaction(self.con):
            self.con.execute(
                "UPDATE resumes SET status='final', updated_at=? WHERE id="
                "(SELECT resume_id FROM resume_versions WHERE id=?)",
                (_now(), version_id),
            )
        return {"ok": True}

    @staticmethod
    def _extract_bullets(sections: dict) -> list[tuple[str, str, list[str]]]:
        """(路径, 文本, 条目级证据ID)：work/projects 的 summary 与 highlights、
        education 的 score、awards 的 title。条目自带的 evidence_ids 视为内联引用。"""
        out: list[tuple[str, str, list[str]]] = []
        for i, w in enumerate(sections.get("work") or []):
            ev = list(w.get("evidence_ids") or [])
            if w.get("summary"):
                out.append((f"work[{i}].summary", str(w["summary"]), ev))
            for j, h in enumerate(w.get("highlights") or []):
                out.append((f"work[{i}].highlights[{j}]", str(h), ev))
        for i, p in enumerate(sections.get("projects") or []):
            if p.get("description"):
                out.append((f"projects[{i}].description", str(p["description"]),
                            list(p.get("evidence_ids") or [])))
        for i, e in enumerate(sections.get("education") or []):
            if e.get("score"):
                out.append((f"education[{i}].score", str(e["score"]),
                            list(e.get("evidence_ids") or [])))
        for i, a in enumerate(sections.get("awards") or []):
            if a.get("title"):
                out.append((f"awards[{i}].title", str(a["title"]),
                            list(a.get("evidence_ids") or [])))
        return out

    # ---------- 版本对比 ----------

    def diff_versions(self, version_a: str, version_b: str) -> dict:
        """结构化 diff：bullet 级增删改。"""
        va, vb = self.get_version(version_a), self.get_version(version_b)
        ba = {k: v for k, v, _ in self._extract_bullets(va["sections"])}
        bb = {k: v for k, v, _ in self._extract_bullets(vb["sections"])}
        added = [{"path": k, "text": v} for k, v in bb.items() if k not in ba]
        removed = [{"path": k, "text": v} for k, v in ba.items() if k not in bb]
        changed = []
        for k in set(ba) & set(bb):
            if ba[k] != bb[k]:
                changed.append({"path": k, "from": ba[k], "to": bb[k],
                                "similarity": round(
                                    difflib.SequenceMatcher(None, ba[k], bb[k]).ratio(), 3)})
        return {"added": added, "removed": removed, "changed": changed}

    # ---------- 导出 ----------

    def render_markdown(self, version_id: str) -> str:
        s = self.get_version(version_id)["sections"]
        b = s.get("basics") or {}
        lines = [f"# {b.get('name', '')}"]
        if b.get("label"):
            lines.append(f"*{b['label']}*")
        contact = " ｜ ".join(x for x in (b.get("email"), b.get("phone")) if x)
        if contact:
            lines.append(contact)
        lines.append("")
        if b.get("summary"):
            lines += [strip_citations(b["summary"]), ""]
        if s.get("work"):
            lines.append("## 工作与实习经历")
            for w in s["work"]:
                dates = f'{w.get("startDate", "")} – {w.get("endDate", "")}'.strip(" –")
                lines.append(f"### {w.get('position', '')} · {w.get('name', '')}　{dates}")
                # summary 可能含多行（迁移合并/用户粘贴的完整段落）→ 逐行成 bullet，与 highlights 同形
                for ln in str(w.get("summary") or "").splitlines():
                    if ln.strip():
                        lines.append(f"- {strip_citations(ln)}")
                for h in w.get("highlights") or []:
                    lines.append(f"- {strip_citations(h)}")
                lines.append("")
        if s.get("projects"):
            lines.append("## 项目经历")
            for p in s["projects"]:
                lines.append(f"### {p.get('name', '')}" + (f"（{p['role']}）" if p.get("role") else ""))
                for ln in str(p.get("description") or "").splitlines():
                    if ln.strip():
                        lines.append(f"- {strip_citations(ln)}")
                lines.append("")
        if s.get("education"):
            lines.append("## 教育背景")
            for e in s["education"]:
                lines.append(
                    f"- {e.get('school', '')}　{e.get('degree', '')}　{e.get('major', '')}"
                    + (f"　{e['score']}" if e.get("score") else "")
                )
            lines.append("")
        if s.get("skills"):
            lines.append("## 技能")
            lines.append("、".join(sk.get("name", "") for sk in s["skills"]))
            lines.append("")
        if s.get("awards"):
            lines.append("## 荣誉奖项")
            for a in s["awards"]:
                lines.append(f"- {a.get('title', '')}" + (f"　{a['date']}" if a.get("date") else ""))
        return "\n".join(lines).strip() + "\n"

    def render_html(self, version_id: str, *, template: str = "classic") -> str:
        """自包含可打印 HTML。template: classic（衬线标题标准版）/ compact（一页致密版）。"""
        s = self.get_version(version_id)["sections"]
        b = s.get("basics") or {}
        md_blocks: list[str] = []

        def h2(t: str) -> None:
            md_blocks.append(f"<h2>{_esc(t)}</h2>")

        if b.get("summary"):
            md_blocks.append(f"<p class='summary'>{_esc(strip_citations(b['summary']))}</p>")
        if s.get("work"):
            h2("工作与实习经历")
            for w in s["work"]:
                dates = f'{_esc(w.get("startDate", ""))} – {_esc(w.get("endDate", ""))}'
                md_blocks.append(
                    f"<div class='item'><div class='item-head'>"
                    f"<span class='item-title'>{_esc(w.get('position', ''))}</span>"
                    f"<span class='item-sub'>{_esc(w.get('name', ''))}</span>"
                    f"<span class='item-date'>{dates}</span></div>"
                    # summary 可能含多行（迁移合并/粘贴段落）→ 与 highlights 同样逐行成列表项
                    + "".join(
                        f"<li>{_esc(strip_citations(ln))}</li>"
                        for ln in str(w.get("summary") or "").splitlines() if ln.strip()
                    )
                    + "".join(f"<li>{_esc(strip_citations(x))}</li>" for x in (w.get("highlights") or []))
                    + "</div>"
                )
        if s.get("projects"):
            h2("项目经历")
            for p in s["projects"]:
                md_blocks.append(
                    f"<div class='item'><div class='item-head'>"
                    f"<span class='item-title'>{_esc(p.get('name', ''))}</span>"
                    f"<span class='item-sub'>{_esc(p.get('role', ''))}</span></div>"
                    + "".join(
                        f"<li>{_esc(strip_citations(ln))}</li>"
                        for ln in str(p.get("description") or "").splitlines() if ln.strip()
                    )
                    + "</div>"
                )
        if s.get("education"):
            h2("教育背景")
            for e in s["education"]:
                md_blocks.append(
                    f"<div class='item'><div class='item-head'>"
                    f"<span class='item-title'>{_esc(e.get('school', ''))}</span>"
                    f"<span class='item-sub'>{_esc(e.get('degree', ''))} {_esc(e.get('major', ''))}</span>"
                    f"<span class='item-date'>{_esc(e.get('startDate', ''))} – {_esc(e.get('endDate', ''))}</span></div>"
                    + (f"<p>{_esc(e['score'])}</p>" if e.get("score") else "")
                    + "</div>"
                )
        if s.get("skills"):
            h2("技能")
            md_blocks.append(
                "<p class='skills'>" + "、".join(_esc(sk.get("name", "")) for sk in s["skills"]) + "</p>"
            )
        if s.get("awards"):
            h2("荣誉奖项")
            md_blocks.append(
                "<ul>" + "".join(
                    f"<li>{_esc(a.get('title', ''))}" + (f"　{_esc(a['date'])}" if a.get("date") else "") + "</li>"
                    for a in s["awards"]
                ) + "</ul>"
            )
        body = "\n".join(md_blocks)
        contact = " ｜ ".join(x for x in (b.get("email"), b.get("phone")) if x)
        wrapper = COMPACT_TEMPLATE if template == "compact" else HTML_TEMPLATE
        return wrapper.format(
            name=_esc(b.get("name", "")), label=_esc(b.get("label", "")),
            contact=_esc(contact), body=body,
        )

    # ---------- JSON Resume 开放标准互操作（https://jsonresume.org/schema） ----------

    def export_json_resume(self, version_id: str) -> dict:
        """内部 sections → JSON Resume 标准。证据引用（evidence_ids）按下划线
        扩展属性保留——标准允许自定义属性，溯源是本系统的核心主张，不丢弃。"""
        v = self.get_version(version_id)
        s = v["sections"]
        basics = s.get("basics") or {}
        out: dict = {
            "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
            "basics": {
                "name": basics.get("name", ""),
                "label": basics.get("label", ""),
                "summary": strip_citations(basics.get("summary", "") or ""),
                "email": basics.get("email", "") or "",
                "phone": basics.get("phone", "") or "",
            },
            "work": [
                {
                    "name": w.get("name", ""),
                    "position": w.get("position", ""),
                    "startDate": w.get("startDate", "") or "",
                    "endDate": w.get("endDate", "") or "",
                    "summary": strip_citations(w.get("summary", "") or ""),
                    "highlights": [strip_citations(h) for h in (w.get("highlights") or [])],
                    "_evidence_ids": list(w.get("evidence_ids") or []),
                }
                for w in s.get("work") or []
            ],
            # 教育：官方键 institution/area/studyType；内部叫 school/major/degree
            "education": [
                {
                    "institution": e.get("school", ""),
                    "area": e.get("major", "") or "",
                    "studyType": e.get("degree", "") or "",
                    "startDate": e.get("startDate", "") or "",
                    "endDate": e.get("endDate", "") or "",
                    "score": e.get("score", "") or "",
                }
                for e in s.get("education") or []
            ],
            "projects": [
                {
                    "name": p.get("name", ""),
                    "role": p.get("role", "") or "",
                    "url": p.get("url", "") or "",
                    "description": strip_citations(p.get("description", "") or ""),
                    "startDate": p.get("startDate", "") or "",
                    "endDate": p.get("endDate", "") or "",
                    "_evidence_ids": list(p.get("evidence_ids") or []),
                }
                for p in s.get("projects") or []
            ],
            "skills": [
                {"name": k.get("name", ""), "level": str(k.get("level", "")),
                 "keywords": list(k.get("keywords") or [])}
                for k in s.get("skills") or []
            ],
            "awards": [
                {"title": a.get("title", ""), "date": a.get("date", "") or "",
                 "_evidence_ids": list(a.get("evidence_ids") or [])}
                for a in s.get("awards") or []
            ],
            "_meta": {
                "generator": "job-hater",
                "resume_id": v["resume_id"],
                "version_id": v["id"],
                "version": v["version"],
            },
        }
        return out

    def import_json_resume(self, profile_id: str, data: dict) -> dict:
        """JSON Resume 标准 → 画像簇（技能/经历/教育/项目）。只取可核实的事实字段；
        同名实体跳过（重复导入幂等）；返回各簇导入计数。

        信任边界：导入的是「用户自己提供的材料」，与手工建档同级——同样要经
        证据确认与 factcheck 才能进入定稿简历，导入不产生任何免检特权。
        """
        from jobhater.services.profile import ProfileService

        ps = ProfileService(self.con)
        if ps.get_profile(profile_id) is None:
            raise ResumeError(f"画像不存在: {profile_id}")
        counts = {"skills": 0, "experiences": 0, "educations": 0, "projects": 0, "headline_filled": 0}

        def _d(v: str | None) -> str | None:
            s = str(v or "").strip()
            return s or None

        existing_skills = {s.name for s in ps.list_skills(profile_id)}
        for sk in data.get("skills") or []:
            name = _d(sk.get("name"))
            if not name or name in existing_skills:
                continue
            ps.add_skill(
                profile_id, name=name,
                aliases=[str(k) for k in (sk.get("keywords") or []) if str(k).strip() and str(k) != name],
            )
            existing_skills.add(name)
            counts["skills"] += 1

        existing_exp = {(e.employer, e.title) for e in ps.list_experiences(profile_id)}
        for w in data.get("work") or []:
            employer, title = _d(w.get("name")), _d(w.get("position"))
            if not employer or not title or (employer, title) in existing_exp:
                continue
            ps.add_experience(
                profile_id, employer=employer, title=title,
                start_date=_d(w.get("startDate")), end_date=_d(w.get("endDate")),
                description=_d(w.get("summary")) or None,
                is_current=str(w.get("endDate") or "").strip() in ("", "至今", "Present", "present"),
            )
            existing_exp.add((employer, title))
            counts["experiences"] += 1

        existing_edu = {e.school for e in ps.list_educations(profile_id)}
        for e in data.get("education") or []:
            school = _d(e.get("institution") or e.get("school"))
            if not school or school in existing_edu:
                continue
            ps.add_education(
                profile_id, school=school,
                degree=_d(e.get("studyType") or e.get("degree")),
                major=_d(e.get("area") or e.get("major")),
                start_date=_d(e.get("startDate")), end_date=_d(e.get("endDate")),
            )
            existing_edu.add(school)
            counts["educations"] += 1

        existing_prj = {p.name for p in ps.list_projects(profile_id)}
        for p in data.get("projects") or []:
            name = _d(p.get("name"))
            if not name or name in existing_prj:
                continue
            ps.add_project(
                profile_id, name=name, role=_d(p.get("role")),
                url=_d(p.get("url")), description=_d(p.get("description")) or None,
            )
            existing_prj.add(name)
            counts["projects"] += 1

        basics = data.get("basics") or {}
        profile = ps.get_profile(profile_id)
        if not profile.headline:
            label = _d(basics.get("label")) or _d(basics.get("summary"))
            if label:
                ps.update_profile_headline(profile_id, label[:120])
                counts["headline_filled"] = 1
        return counts

    def export_file(self, version_id: str, fmt: str, *, template: str = "classic") -> Path:
        """导出到 exports 目录。fmt: md/json/html/pdf/docx/json-resume；html 系可选
        template=compact（一页致密版）。可选依赖缺失时如实报错。"""
        out_dir = config.exports_dir()
        version = self.get_version(version_id)
        base = f"resume_{version['version']}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if fmt == "md":
            p = out_dir / f"{base}.md"
            p.write_text(self.render_markdown(version_id), encoding="utf-8")
            return p
        if fmt == "json":
            p = out_dir / f"{base}.json"
            p.write_text(
                json.dumps(version["sections"], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return p
        if fmt == "json-resume":
            p = out_dir / f"{base}.jsonresume.json"
            p.write_text(
                json.dumps(self.export_json_resume(version_id), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return p
        if fmt == "html":
            p = out_dir / (f"{base}_{template}.html" if template != "classic" else f"{base}.html")
            p.write_text(self.render_html(version_id, template=template), encoding="utf-8")
            return p
        if fmt == "pdf":
            html_path = out_dir / f"{base}.html"
            html_path.write_text(self.render_html(version_id, template=template), encoding="utf-8")
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:
                raise ResumeError(
                    "PDF 导出需要 playwright（可选依赖）：pip install jobhater[pdf] 并执行 "
                    "playwright install chromium。已生成可打印 HTML 作为降级："
                    f"{html_path}（浏览器打开 → 打印 → 另存为 PDF）"
                ) from e
            pdf_path = out_dir / f"{base}.pdf"
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                page.goto(html_path.as_uri())
                page.pdf(path=str(pdf_path), format="A4",
                         margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
                browser.close()
            return pdf_path
        if fmt == "docx":
            try:
                import docx
            except ImportError as e:
                raise ResumeError(
                    "DOCX 导出需要 python-docx（可选依赖）：pip install jobhater[docx]"
                ) from e
            p = out_dir / f"{base}.docx"
            doc = docx.Document()
            md_lines = self.render_markdown(version_id).splitlines()
            for line in md_lines:
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=0)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=1)
                elif line.startswith("### "):
                    doc.add_heading(line[4:], level=2)
                elif line.startswith("- "):
                    doc.add_paragraph(line[2:], style="List Bullet")
                elif line.strip():
                    doc.add_paragraph(line)
            doc.save(str(p))
            return p
        raise ResumeError(f"不支持的导出格式：{fmt}")


def _esc(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def strip_citations(text: str) -> str:
    """人面渲染/导出时移除内部引用标记 [ev:...]——溯源保留在 bullets_provenance
    与 evidence_ids 扩展字段里，不出现在交给 HR 的文本中。"""
    return CITE_RE.sub("", str(text)).strip()


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{name} - 简历</title>
<style>
  :root {{ --ink: #1a1a1a; --muted: #555; --line: #d8d4cc; --accent: #8a5a2b; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
         color: var(--ink); font-size: 10.5pt; line-height: 1.55; max-width: 210mm;
         margin: 0 auto; padding: 10mm 2mm; }}
  header {{ text-align: center; border-bottom: 2.5pt solid var(--ink); padding-bottom: 8pt;
            margin-bottom: 10pt; }}
  h1 {{ font-family: "Source Han Serif SC", "Noto Serif CJK SC", "SimSun", serif;
       font-size: 22pt; letter-spacing: 2pt; }}
  .label {{ color: var(--muted); margin-top: 2pt; font-size: 11pt; }}
  .contact {{ color: var(--muted); margin-top: 3pt; font-size: 10pt; }}
  h2 {{ font-size: 12.5pt; letter-spacing: 1pt; color: var(--accent);
       border-bottom: 0.8pt solid var(--line); padding-bottom: 2pt; margin: 12pt 0 6pt; }}
  .item {{ margin-bottom: 8pt; page-break-inside: avoid; }}
  .item-head {{ display: flex; align-items: baseline; gap: 8pt; flex-wrap: wrap; }}
  .item-title {{ font-weight: 700; }}
  .item-sub {{ color: var(--muted); flex: 1; }}
  .item-date {{ color: var(--muted); font-size: 9.5pt; white-space: nowrap; }}
  p {{ margin-top: 3pt; }}
  .summary {{ text-align: center; color: var(--muted); }}
  .skills {{ margin-top: 3pt; }}
  li {{ margin: 2pt 0 2pt 14pt; }}
  @media print {{ body {{ padding: 0; }} }}
</style>
</head>
<body>
<header><h1>{name}</h1><div class="label">{label}</div><div class="contact">{contact}</div></header>
{body}
</body>
</html>
"""

# compact：一页致密模板（无衬线、窄行距、单行条目）——经历多/严格控制一页时用。
# 与 classic 共享 body 构造，仅版式不同；同样纯文本+标准分节，ATS 可解析。
COMPACT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{name} - 简历</title>
<style>
  :root {{ --ink: #111; --muted: #555; --line: #bbb; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         color: var(--ink); font-size: 9.5pt; line-height: 1.38; max-width: 200mm;
         margin: 0 auto; padding: 8mm 2mm; }}
  header {{ display: flex; align-items: baseline; gap: 10pt; flex-wrap: wrap;
            border-bottom: 1.6pt solid var(--ink); padding-bottom: 4pt; margin-bottom: 7pt; }}
  h1 {{ font-size: 15pt; letter-spacing: 1pt; }}
  .label {{ color: var(--muted); font-size: 10pt; }}
  .contact {{ color: var(--muted); font-size: 9pt; margin-left: auto; }}
  h2 {{ font-size: 10.5pt; letter-spacing: 1pt; margin: 8pt 0 4pt;
       border-bottom: 0.6pt solid var(--line); padding-bottom: 1pt; }}
  .item {{ margin-bottom: 5pt; page-break-inside: avoid; }}
  .item-head {{ display: flex; align-items: baseline; gap: 6pt; flex-wrap: wrap; }}
  .item-title {{ font-weight: 700; }}
  .item-sub {{ color: var(--muted); flex: 1; }}
  .item-date {{ color: var(--muted); font-size: 8.5pt; white-space: nowrap; }}
  p {{ margin-top: 2pt; }}
  .summary {{ color: var(--muted); }}
  .skills {{ margin-top: 2pt; }}
  li {{ margin: 1pt 0 1pt 12pt; }}
  @media print {{ body {{ padding: 0; }} }}
</style>
</head>
<body>
<header><h1>{name}</h1><span class="label">{label}</span><span class="contact">{contact}</span></header>
{body}
</body>
</html>
"""
