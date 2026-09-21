"""v1 (Campus-Job-Agent JSON) → v2 (SQLite) 一次性迁移工具。

输入：旧 data/ 目录（profile/jobs/applications 三份 JSON）。
输出：新库中的画像+证据+偏好+岗位（经统一 ingest 路径，去重照常生效）。

语义映射中如实降级的点（在结果里明说，不假装无损）：
- v1 skills.level 是文本（了解/熟练/精通）→ 数值 1/3/4；
- v1 blacklist 语义是"已投递防重复"（§15 已废除）→ 不迁移为 user_blocked，仅记录数量；
- v1 jobs.status=rejected 是旧校招规则的拒绝 → 保留 rejected 状态与原因，透明可查；
- v1 的 skills.evidence / experience_ev_map → evidence 表（source_kind=paste）。

v1 字段映射说明（profile.json 实际 schema）：
- experience 条目的 name/highlights 无 v2 专用列 → 合并写入 description（内容不丢，结构降级）；
- experience 的证据链接在顶层 experience_ev_map（不在条目内）→ 经 ev_id_map 映射到 evidence_ids；
- education.gpa 形如 "3.8/5.0" → 拆出数值 gpa，原文留 gpa_note；
- evidence_index 文本的类目前缀（"教育背景："等，v1 系统固定书写约定）→ fact_type 分类；
- awards 条目的 level/work/year → Award.level/description/date。
"""
from __future__ import annotations

import json
from pathlib import Path

from jobhater.db import apply_all, connect
from jobhater.domain.enums import EvidenceSourceKind, FactType
from jobhater.services.jobs import JobService
from jobhater.services.profile import ProfileService

_LEVEL_MAP = {"了解": 1, "熟悉": 2, "熟练": 3, "掌握": 3, "精通": 4, "专家": 5}

# v1 evidence_index 文本的类目前缀（v1 系统生成时的固定书写约定）→ FactType
_V1_EVIDENCE_PREFIX_FACT_TYPE: list[tuple[str, FactType]] = [
    ("教育背景", FactType.EDUCATION),
    ("科研经历", FactType.EXPERIENCE),
    ("项目经验", FactType.PROJECT),
    ("竞赛获奖", FactType.AWARD),
    ("荣誉", FactType.AWARD),
    ("校园经历", FactType.EXPERIENCE),
    ("技能与自我评价", FactType.SKILL),
]


def _infer_fact_type(text: str) -> FactType | None:
    for prefix, ft in _V1_EVIDENCE_PREFIX_FACT_TYPE:
        if text.startswith(prefix):
            return ft
    return None


def _parse_gpa(v) -> tuple[float | None, str | None]:
    """v1 gpa 形如 "3.8/5.0" → (3.8, "3.8/5.0")；纯数字 → (float, None)。"""
    s = str(v or "").strip()
    if not s:
        return None, None
    head = s.split("/", 1)[0].strip()
    try:
        return float(head), s
    except ValueError:
        return None, s


def _date(v) -> str | None:
    if not v:
        return None
    s = str(v).strip().replace(".", "-").replace("/", "-")
    return s[:10] if len(s) >= 7 else None


def migrate(old_data_dir: Path | str, db_path: Path | str | None = None) -> dict:
    old = Path(old_data_dir)
    profile_file = old / "profile" / "profile.json"
    jobs_file = old / "jobs" / "jobs.json"
    apps_file = old / "applications.json"
    report: dict = {"profile": None, "jobs": None, "notes": []}

    con = connect(db_path) if db_path else connect()
    try:
        apply_all(db_path) if db_path else apply_all()
        ps = ProfileService(con)

        # ---------- 画像 ----------
        if profile_file.exists():
            v1p = json.loads(profile_file.read_text(encoding="utf-8"))
            identity = v1p.get("identity", {})
            name = identity.get("name") or identity.get("display_name") or "迁移用户"
            profile = ps.create_profile(
                name, headline=identity.get("headline"),
                phone=identity.get("phone"), email=identity.get("email"),
            )
            pid = profile.id

            ev_id_map: dict[str, str] = {}
            ev_index: dict[str, str] = v1p.get("evidence_index", {})
            for old_ev_id, text in ev_index.items():
                if old_ev_id.startswith("_"):  # _source_files 等是元数据键，不是证据
                    continue
                ev = ps.add_evidence(
                    pid, str(text), source_kind=EvidenceSourceKind.PASTE,
                    normalized_fact=str(text)[:200], user_confirmed=True,
                    fact_type=_infer_fact_type(str(text)),
                )
                ev_id_map[old_ev_id] = ev.id
            report["profile"] = {
                "id": pid, "name": name,
                "evidence_migrated": len(ev_id_map),
            }

            for e in v1p.get("education", []):
                school = e.get("school", "")
                gpa_val, gpa_note = _parse_gpa(e.get("gpa"))
                edu_ev = [
                    ev_id_map[old_id]
                    for old_id, text in ev_index.items()
                    if old_id in ev_id_map and school and school in str(text)
                ]
                ps.add_education(
                    pid, school=school,
                    degree=e.get("degree"), major=e.get("major"),
                    start_date=_date(e.get("start")), end_date=_date(e.get("end")),
                    gpa=gpa_val, gpa_note=gpa_note,
                    evidence_ids=edu_ev,
                )
            for s in v1p.get("skills", []):
                ps.add_skill(
                    pid, s.get("name", ""),
                    level=_LEVEL_MAP.get(str(s.get("level", "")), None),
                    aliases=[], evidence_ids=[ev_id_map[x] for x in s.get("evidence", []) if x in ev_id_map],
                )
            ev_map_v1: dict[str, str] = v1p.get("experience_ev_map", {})
            for x in v1p.get("experiences", []):
                # v1 条目自带 name + highlights（正文亮点），v2 无专用列 → 合并进 description
                desc_parts: list[str] = []
                if x.get("name"):
                    desc_parts.append(str(x["name"]))
                for h in x.get("highlights", []) or []:
                    desc_parts.append(str(h))
                if x.get("description"):
                    desc_parts.append(str(x["description"]))
                old_ev_ids = list(x.get("evidence", []))
                mapped = ev_map_v1.get(x.get("id"))
                if mapped and mapped not in old_ev_ids:
                    old_ev_ids.append(mapped)
                ps.add_experience(
                    pid, employer=x.get("employer", x.get("org", "")),
                    title=x.get("title", x.get("role", "")),
                    kind=x.get("kind", "internship" if x.get("is_internship") else "full_time"),
                    start_date=_date(x.get("start")), end_date=_date(x.get("end")),
                    description="\n".join(desc_parts) or None,
                    tags=list(x.get("tags", [])),
                    evidence_ids=[ev_id_map[t] for t in old_ev_ids if t in ev_id_map],
                )
            for a in v1p.get("awards", []) + v1p.get("honors", []):
                if isinstance(a, dict):
                    year = a.get("year")
                    ps.add_award(
                        pid, name=a.get("name", a.get("title", "")),
                        issuer=a.get("issuer"), date=_date(a.get("date")) or (str(year) if year else None),
                        level=a.get("level"), description=a.get("work"),
                    )
            prefs = v1p.get("preferences", {})
            if prefs:
                ps.create_preset(
                    pid, "迁移的求职偏好（v1）",
                    target_roles=list(prefs.get("target_roles", [])),
                    target_cities=list(prefs.get("target_cities", [])),
                    salary_min_k=prefs.get("salary_min_k"),
                    remote_ok=bool(prefs.get("remote_ok", False)),
                )
                ps.activate_preset(pid, ps.list_presets(pid)[0].id)

        # ---------- 岗位 ----------
        if jobs_file.exists():
            v1j = json.loads(jobs_file.read_text(encoding="utf-8"))
            raw_jobs = []
            rejected_ids: set[str] = set()
            rejected_reasons: dict[str, str] = {}
            for j in v1j.get("jobs", []):
                old_id = str(j.get("id") or "")
                raw = {
                    "title": j.get("title"), "company": j.get("company"),
                    "city": j.get("city"), "salary": j.get("salary"),
                    "salary_min_k": j.get("salary_min_k"), "salary_max_k": j.get("salary_max_k"),
                    "experience_required": j.get("experience_required"),
                    "education_required": j.get("education_required"),
                    "description": j.get("description"),
                    "keywords": j.get("keywords") or [],
                    "url": j.get("url"), "published_at": j.get("published_at"),
                    "deadline": j.get("deadline"),
                    "source_job_id": old_id or None,
                }
                if j.get("status") == "rejected":
                    rejected_ids.add(old_id)
                    rejected_reasons[old_id] = j.get("reject_reason") or "v1 过滤规则拒绝"
                raw_jobs.append(raw)
            js = JobService(con)
            stats = js.ingest(raw_jobs, source_id="v1_migration")
            # v1 拒绝的岗位按原状态标注（透明可查）
            if rejected_ids:
                from jobhater.db.connection import transaction

                with transaction(con):
                    for r in con.execute(
                        "SELECT id, source_job_id FROM job_postings WHERE source_id='v1_migration'"
                    ).fetchall():
                        sid = r["source_job_id"]
                        if sid in rejected_ids:
                            con.execute(
                                "UPDATE job_postings SET status='rejected', reject_reason=? WHERE id=?",
                                (f"v1迁移保留：{rejected_reasons.get(sid, '')}", r["id"]),
                            )
            report["jobs"] = stats.model_dump()
            report["jobs"]["v1_rejected_preserved"] = len(rejected_ids)

        # ---------- 投递台账 ----------
        if apps_file.exists():
            v1a = json.loads(apps_file.read_text(encoding="utf-8"))
            n_apps = len(v1a.get("applications", []))
            n_black = len(v1a.get("blacklist", []))
            report["notes"].append(
                f"v1 投递记录 {n_apps} 条未迁移（v2 语义重建，请重新建立投递跟踪）；"
                f"v1 blacklist {n_black} 条不迁移（'已投递拉黑公司'语义已废除，见 §15）"
            )
        return report
    finally:
        con.close()
