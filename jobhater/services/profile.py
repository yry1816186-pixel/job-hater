"""画像服务：候选人档案、证据、求职偏好（preset）。

UI / CLI / MCP 共用的唯一实现。证据先行：任何 AI 提取的事实先入 evidence，
经用户确认（Candidate Facts Review）后才被简历生成引用。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from jobhater.db.connection import transaction
from jobhater.domain.enums import EvidenceSourceKind
from jobhater.domain.models import (
    Award,
    CandidateProfile,
    Certification,
    Education,
    Evidence,
    Experience,
    Project,
    SearchPreset,
    Skill,
)
from jobhater.services.storage import (
    JsonFieldMap,
    insert_model,
    model_to_row,
    new_id,
    row_to_model,
)

_EDU_JSON: JsonFieldMap = {"detail": ("detail_json", {}), "evidence_ids": ("evidence_ids_json", [])}
_EXP_JSON: JsonFieldMap = {"tags": ("tags_json", []), "evidence_ids": ("evidence_ids_json", [])}
_SKILL_JSON: JsonFieldMap = {
    "aliases": ("aliases_json", []),
    "evidence_ids": ("evidence_ids_json", []),
}
_AWARD_JSON: JsonFieldMap = {"evidence_ids": ("evidence_ids_json", [])}
_CERT_JSON: JsonFieldMap = {"evidence_ids": ("evidence_ids_json", [])}
_PRESET_JSON: JsonFieldMap = {
    "employment_types": ("employment_types_json", []),
    "target_roles": ("target_roles_json", []),
    "target_cities": ("target_cities_json", []),
    "exclude_employers": ("exclude_employers_json", []),
    "exclude_industries": ("exclude_industries_json", []),
    "exclude_keywords": ("exclude_keywords_json", []),
    "weights": ("weights_json", {}),
    "gates": ("gates_json", {}),
}


class ProfileService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 画像 ----------

    def create_profile(self, display_name: str, headline: str | None = None) -> CandidateProfile:
        profile = CandidateProfile(
            id=new_id("prof"), display_name=display_name, headline=headline
        )
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO candidate_profiles(id, display_name, headline) VALUES (?,?,?)",
                (profile.id, profile.display_name, profile.headline),
            )
        return profile

    def get_profile(self, profile_id: str) -> CandidateProfile | None:
        row = self.con.execute(
            "SELECT * FROM candidate_profiles WHERE id=?", (profile_id,)
        ).fetchone()
        return row_to_model(CandidateProfile, row, {}) if row else None

    def list_profiles(self) -> list[CandidateProfile]:
        rows = self.con.execute(
            "SELECT * FROM candidate_profiles ORDER BY created_at"
        ).fetchall()
        return [row_to_model(CandidateProfile, r, {}) for r in rows]

    # ---------- 画像子实体 ----------

    def _add_child(self, model, table: str, json_fields: JsonFieldMap) -> None:
        with transaction(self.con):
            insert_model(self.con, table, model, json_fields)

    def add_education(self, profile_id: str, **kw) -> Education:
        edu = Education(id=new_id("edu"), profile_id=profile_id, **kw)
        self._add_child(edu, "educations", _EDU_JSON)
        return edu

    def add_experience(self, profile_id: str, **kw) -> Experience:
        exp = Experience(id=new_id("exp"), profile_id=profile_id, **kw)
        self._add_child(exp, "experiences", _EXP_JSON)
        return exp

    def add_project(self, profile_id: str, **kw) -> Project:
        proj = Project(id=new_id("prj"), profile_id=profile_id, **kw)
        self._add_child(proj, "projects", _EXP_JSON)
        return proj

    def add_skill(self, profile_id: str, name: str, **kw) -> Skill:
        skill = Skill(id=new_id("skl"), profile_id=profile_id, name=name, **kw)
        self._add_child(skill, "skills", _SKILL_JSON)
        return skill

    def add_award(self, profile_id: str, **kw) -> Award:
        award = Award(id=new_id("awd"), profile_id=profile_id, **kw)
        self._add_child(award, "awards", _AWARD_JSON)
        return award

    def add_certification(self, profile_id: str, **kw) -> Certification:
        cert = Certification(id=new_id("crt"), profile_id=profile_id, **kw)
        self._add_child(cert, "certifications", _CERT_JSON)
        return cert

    def list_educations(self, profile_id: str) -> list[Education]:
        rows = self.con.execute(
            "SELECT * FROM educations WHERE profile_id=? ORDER BY sort_order, end_date DESC",
            (profile_id,),
        ).fetchall()
        return [row_to_model(Education, r, _EDU_JSON) for r in rows]

    def list_experiences(self, profile_id: str) -> list[Experience]:
        rows = self.con.execute(
            "SELECT * FROM experiences WHERE profile_id=? ORDER BY sort_order, end_date DESC",
            (profile_id,),
        ).fetchall()
        return [row_to_model(Experience, r, _EXP_JSON) for r in rows]

    def list_projects(self, profile_id: str) -> list[Project]:
        rows = self.con.execute(
            "SELECT * FROM projects WHERE profile_id=? ORDER BY sort_order, end_date DESC",
            (profile_id,),
        ).fetchall()
        return [row_to_model(Project, r, _EXP_JSON) for r in rows]

    def list_skills(self, profile_id: str) -> list[Skill]:
        rows = self.con.execute(
            "SELECT * FROM skills WHERE profile_id=? ORDER BY name", (profile_id,)
        ).fetchall()
        return [row_to_model(Skill, r, _SKILL_JSON) for r in rows]

    # ---------- 证据 ----------

    def add_evidence(
        self,
        profile_id: str,
        original_text: str,
        *,
        source_kind: EvidenceSourceKind = EvidenceSourceKind.MANUAL,
        normalized_fact: str | None = None,
        fact_type=None,
        source_ref: str | None = None,
        doc_hash: str | None = None,
        section: str | None = None,
        confidence: float = 1.0,
    ) -> Evidence:
        ev = Evidence(
            id=new_id("ev"),
            profile_id=profile_id,
            source_kind=source_kind,
            source_ref=source_ref,
            doc_hash=doc_hash,
            section=section,
            original_text=original_text,
            normalized_fact=normalized_fact,
            fact_type=fact_type,
            confidence=confidence,
        )
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO evidence(id, profile_id, source_kind, source_ref, doc_hash,
                     section, original_text, normalized_fact, fact_type, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    ev.id, ev.profile_id, ev.source_kind.value, ev.source_ref, ev.doc_hash,
                    ev.section, ev.original_text, ev.normalized_fact,
                    ev.fact_type.value if ev.fact_type else None, ev.confidence,
                ),
            )
        return ev

    def list_evidence(self, profile_id: str, only_confirmed: bool = False) -> list[Evidence]:
        sql = "SELECT * FROM evidence WHERE profile_id=?"
        if only_confirmed:
            sql += " AND user_confirmed=1"
        rows = self.con.execute(sql + " ORDER BY created_at", (profile_id,)).fetchall()
        return [row_to_model(Evidence, r, {}) for r in rows]

    def confirm_evidence(self, profile_id: str, evidence_ids: list[str]) -> int:
        """用户在事实复核中确认证据。返回确认条数（不存在的 id 如实不计）。"""
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE evidence SET user_confirmed=1 WHERE profile_id=? AND id IN "
                f"({','.join('?' * len(evidence_ids))})",
                (profile_id, *evidence_ids),
            )
            return cur.rowcount

    # ---------- 求职偏好 ----------

    def create_preset(self, profile_id: str, name: str, **kw) -> SearchPreset:
        preset = SearchPreset(id=new_id("pst"), profile_id=profile_id, name=name, **kw)
        with transaction(self.con):
            insert_model(self.con, "search_presets", preset, _PRESET_JSON)
        return preset

    def update_preset(self, preset_id: str, **kw) -> SearchPreset:
        """部分更新：仅传入的字段被修改。"""
        current = self.get_preset(preset_id)
        if current is None:
            raise KeyError(f"preset 不存在: {preset_id}")
        merged = current.model_copy(update=kw)
        row = model_to_row(merged, _PRESET_JSON)
        row["updated_at"] = _now_sql()
        with transaction(self.con):
            self.con.execute(
                "UPDATE search_presets SET "
                + ",".join(f"{k}=?" for k in row)
                + " WHERE id=?",
                (*row.values(), preset_id),
            )
        return merged

    def get_preset(self, preset_id: str) -> SearchPreset | None:
        row = self.con.execute(
            "SELECT * FROM search_presets WHERE id=?", (preset_id,)
        ).fetchone()
        return row_to_model(SearchPreset, row, _PRESET_JSON) if row else None

    def list_presets(self, profile_id: str) -> list[SearchPreset]:
        rows = self.con.execute(
            "SELECT * FROM search_presets WHERE profile_id=? ORDER BY created_at", (profile_id,)
        ).fetchall()
        return [row_to_model(SearchPreset, r, _PRESET_JSON) for r in rows]

    def active_preset(self, profile_id: str) -> SearchPreset | None:
        row = self.con.execute(
            "SELECT * FROM search_presets WHERE profile_id=? AND is_active=1", (profile_id,)
        ).fetchone()
        if row:
            return row_to_model(SearchPreset, row, _PRESET_JSON)
        presets = self.list_presets(profile_id)
        return presets[0] if presets else None

    def activate_preset(self, profile_id: str, preset_id: str) -> None:
        with transaction(self.con):
            self.con.execute(
                "UPDATE search_presets SET is_active=0 WHERE profile_id=?", (profile_id,)
            )
            cur = self.con.execute(
                "UPDATE search_presets SET is_active=1 WHERE profile_id=? AND id=?",
                (profile_id, preset_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"preset 不存在: {preset_id}")

    # ---------- 匹配输入视图 ----------

    def match_view(self, profile_id: str) -> dict:
        """打包匹配引擎所需的画像数据（一次查询，纯只读）。"""
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"profile 不存在: {profile_id}")
        return {
            "profile": profile.model_dump(),
            "educations": [e.model_dump() for e in self.list_educations(profile_id)],
            "experiences": [e.model_dump() for e in self.list_experiences(profile_id)],
            "projects": [p.model_dump() for p in self.list_projects(profile_id)],
            "skills": [s.model_dump() for s in self.list_skills(profile_id)],
        }


# profile_degree_rank 供匹配引擎的学历 Gate 使用（学位的保守序，未列出的返回 None）
_DEGREE_RANK = {"大专": 1, "专科": 1, "本科": 2, "学士": 2, "硕士": 3, "研究生": 3, "博士": 4}


def _now_sql() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def degree_rank(degree: str | None) -> int | None:
    if not degree:
        return None
    for key, rank in _DEGREE_RANK.items():
        if key in degree:
            return rank
    return None
