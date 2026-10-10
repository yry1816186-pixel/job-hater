"""画像域端点：画像/偏好(preset)/证据/简历版本（含 JSON Resume 互通与导入）。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from jobhater.api.deps import _err, get_con
from jobhater.api.schemas import (
    AwardIn,
    CertificationIn,
    EducationIn,
    EvidenceConfirmIn,
    EvidenceIn,
    ExperienceIn,
    PresetIn,
    PresetPatch,
    ProfileIn,
    ProfilePatchIn,
    ProjectIn,
    SkillIn,
)
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeError, ResumeService


def register(app: FastAPI) -> None:
    # ---------- 画像 ----------

    @app.post("/api/profiles")
    def create_profile(body: ProfileIn, con=Depends(get_con)):
        return ProfileService(con).create_profile(
            body.display_name, body.headline, phone=body.phone, email=body.email
        ).model_dump()

    @app.get("/api/profiles")
    def list_profiles(con=Depends(get_con)):
        return [p.model_dump() for p in ProfileService(con).list_profiles()]

    @app.get("/api/profiles/{profile_id}")
    def get_profile(profile_id: str, con=Depends(get_con)):
        ps = ProfileService(con)
        p = ps.get_profile(profile_id)
        if not p:
            raise HTTPException(404, "profile 不存在")
        return ps.match_view(profile_id)

    @app.patch("/api/profiles/{profile_id}")
    def patch_profile(profile_id: str, body: ProfilePatchIn, con=Depends(get_con)):
        try:
            return ProfileService(con).update_profile(
                profile_id, display_name=body.display_name, headline=body.headline,
                summary=body.summary, phone=body.phone, email=body.email,
            ).model_dump()
        except KeyError:
            raise HTTPException(404, "profile 不存在") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/profiles/{profile_id}/educations")
    def add_education(profile_id: str, body: EducationIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_education(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/experiences")
    def add_experience(profile_id: str, body: ExperienceIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_experience(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/projects")
    def add_project(profile_id: str, body: ProjectIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_project(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/skills")
    def add_skill(profile_id: str, body: SkillIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_skill(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.delete("/api/profiles/{profile_id}/skills/{skill_id}")
    def delete_skill(profile_id: str, skill_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_skill(profile_id, skill_id)
            return {"deleted": skill_id}
        except Exception as e:
            raise _err(e) from e

    @app.delete("/api/profiles/{profile_id}/educations/{education_id}")
    def delete_education(profile_id: str, education_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_education(profile_id, education_id)
            return {"deleted": education_id}
        except Exception as e:
            raise _err(e) from e

    @app.delete("/api/profiles/{profile_id}/experiences/{experience_id}")
    def delete_experience(profile_id: str, experience_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_experience(profile_id, experience_id)
            return {"deleted": experience_id}
        except Exception as e:
            raise _err(e) from e

    @app.delete("/api/profiles/{profile_id}/projects/{project_id}")
    def delete_project(profile_id: str, project_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_project(profile_id, project_id)
            return {"deleted": project_id}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/awards")
    def add_award(profile_id: str, body: AwardIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_award(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/certifications")
    def add_certification(profile_id: str, body: CertificationIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_certification(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/profiles/{profile_id}/evidence")
    def add_evidence(profile_id: str, body: EvidenceIn, con=Depends(get_con)):
        try:
            return ProfileService(con).add_evidence(
                profile_id, body.original_text,
                normalized_fact=body.normalized_fact, fact_type=body.fact_type,
                source_kind=body.source_kind, source_ref=body.source_ref,
                section=body.section, confidence=body.confidence,
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/profiles/{profile_id}/evidence")
    def list_evidence(profile_id: str, only_confirmed: bool = False, con=Depends(get_con)):
        return [
            ev.model_dump()
            for ev in ProfileService(con).list_evidence(profile_id, only_confirmed)
        ]

    @app.post("/api/profiles/{profile_id}/evidence/confirm")
    def confirm_evidence(profile_id: str, body: EvidenceConfirmIn, con=Depends(get_con)):
        n = ProfileService(con).confirm_evidence(profile_id, body.evidence_ids)
        return {"confirmed": n}

    @app.delete("/api/profiles/{profile_id}/evidence/{evidence_id}")
    def delete_evidence(profile_id: str, evidence_id: str, con=Depends(get_con)):
        try:
            ProfileService(con).delete_evidence(profile_id, evidence_id)
            return {"deleted": evidence_id}
        except Exception as e:
            raise _err(e) from e

    # ---------- 偏好 ----------

    @app.post("/api/profiles/{profile_id}/presets")
    def create_preset(profile_id: str, body: PresetIn, con=Depends(get_con)):
        try:
            return ProfileService(con).create_preset(profile_id, **body.model_dump()).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/profiles/{profile_id}/presets")
    def list_presets(profile_id: str, con=Depends(get_con)):
        return [p.model_dump() for p in ProfileService(con).list_presets(profile_id)]

    @app.patch("/api/presets/{preset_id}")
    def patch_preset(preset_id: str, body: PresetPatch, con=Depends(get_con)):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            return ProfileService(con).update_preset(preset_id, **changes).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/presets/{preset_id}/activate")
    def activate_preset(preset_id: str, con=Depends(get_con)):
        ps = ProfileService(con)
        preset = ps.get_preset(preset_id)
        if not preset:
            raise HTTPException(404, "preset 不存在")
        ps.activate_preset(preset.profile_id, preset_id)
        return {"ok": True}

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str, con=Depends(get_con)):
        try:
            ps = ProfileService(con)
            preset = ps.get_preset(preset_id)
            if not preset:
                raise HTTPException(404, "preset 不存在")
            ps.delete_preset(preset.profile_id, preset_id)
            return {"deleted": preset_id}
        except HTTPException:
            raise
        except Exception as e:
            raise _err(e) from e

    # ---------- 简历 ----------

    @app.post("/api/resumes/generate")
    def generate_master(body: dict, con=Depends(get_con)):
        try:
            rs = ResumeService(con)
            rid, vid = rs.build_master_from_profile(body["profile_id"])
            report = rs.factcheck(vid)
            return {"resume_id": rid, "version_id": vid, "factcheck": report}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/resumes")
    def list_resumes(profile_id: str, con=Depends(get_con)):
        return ResumeService(con).list_resumes(profile_id)

    @app.get("/api/resumes/{resume_id}/versions")
    def list_versions(resume_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).list_versions(resume_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}")
    def get_version(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).get_version(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.post("/api/resumes/{resume_id}/versions")
    def new_version(resume_id: str, body: dict, con=Depends(get_con)):
        try:
            rs = ResumeService(con)
            vid = rs.commit_version(
                resume_id, body.get("sections") or {},
                bullets_provenance=body.get("bullets_provenance"),
                note=body.get("note"),
            )
            return {"version_id": vid, "factcheck": rs.factcheck(vid)}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/resume-versions/{version_id}/factcheck")
    def run_factcheck(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).factcheck(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.post("/api/resume-versions/{version_id}/final")
    def mark_final(version_id: str, con=Depends(get_con)):
        try:
            return ResumeService(con).mark_final(version_id)
        except ResumeError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}/export")
    def export_version(
        version_id: str, fmt: str = "md", template: str = "classic", con=Depends(get_con)
    ):
        if fmt not in ("md", "json", "json-resume", "html", "pdf", "docx"):
            raise HTTPException(422, "fmt ∈ md/json/json-resume/html/pdf/docx")
        if template not in ("classic", "compact"):
            raise HTTPException(422, "template ∈ classic/compact")
        try:
            path = ResumeService(con).export_file(version_id, fmt, template=template)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e
        if fmt in ("md", "html", "json", "json-resume"):
            return PlainTextResponse(
                path.read_text(encoding="utf-8"),
                media_type="text/plain; charset=utf-8",
            )
        return FileResponse(path, filename=path.name)

    @app.get("/api/resume-versions/{version_id}/json-resume")
    def get_json_resume(version_id: str, con=Depends(get_con)):
        """JSON Resume 开放标准视图（结构化 JSON 响应，非文件下载）。"""
        try:
            return ResumeService(con).export_json_resume(version_id)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/profiles/{profile_id}/import/json-resume")
    def import_json_resume(profile_id: str, body: dict, con=Depends(get_con)):
        """从 JSON Resume 标准文件导入画像簇（技能/经历/教育/项目）。
        导入与手工建档同级——同样要经证据确认与 factcheck，无免检特权。
        body 即标准 resume JSON（或 {"resume": {...}} 包裹）。"""
        data = body.get("resume") if isinstance(body.get("resume"), dict) else body
        if not isinstance(data, dict) or not (
            data.get("basics") or data.get("work") or data.get("skills")
            or data.get("education") or data.get("projects")
        ):
            raise HTTPException(422, "不是可识别的 JSON Resume 文档（缺 basics/work/skills/education/projects）")
        try:
            return ResumeService(con).import_json_resume(profile_id, data)
        except ResumeError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise _err(e) from e

    @app.get("/api/resume-versions/{version_id}/diff")
    def diff_versions(version_id: str, against: str, con=Depends(get_con)):
        try:
            return ResumeService(con).diff_versions(against, version_id)
        except ResumeError as e:
            raise _err(e) from e
