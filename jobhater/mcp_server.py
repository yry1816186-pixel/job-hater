"""Job Hater MCP server（官方 SDK，stdio）。

Agent 接口层（§24）：每个工具都是 services 层的薄封装——与 Web UI / CLI
完全同一实现，无平行逻辑。修改型工具的 side-effect 在工具描述中声明。

运行：job-hater-mcp（console script）或 python -m jobhater.mcp_server
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.lifecycle import ApplicationService, LifecycleError
from jobhater.services.matching import MatchService
from jobhater.services.profile import ProfileService
from jobhater.services.resume import ResumeService

mcp = FastMCP(
    "job-hater",
    instructions=(
        "本地求职管理系统。工具返回结构化 JSON；匹配结果包含 结论+依据+不确定性，"
        "请原样呈现，不要把分数曲解为录用概率。修改型工具会写本地数据库。"
    ),
)


def _con():
    return connect()


@mcp.tool()
def profile_get(profile_id: str) -> str:
    """读取画像（含教育/经历/项目/技能簇）。只读。"""
    con = _con()
    try:
        return json.dumps(ProfileService(con).match_view(profile_id), ensure_ascii=False, default=str)
    finally:
        con.close()


@mcp.tool()
def jobs_search(query: str = "", city: str = "", limit: int = 20) -> str:
    """岗位检索（中文全文+结构化过滤）。只读。"""
    con = _con()
    try:
        jobs = JobService(con).search(
            query, cities=[city] if city else None, limit=min(limit, 100)
        )
        return json.dumps(
            [
                {
                    "id": j.id, "title": j.title, "company": j.employer_name,
                    "city": j.city, "salary": j.salary_text, "recruitment_type": j.recruitment_type.value,
                    "url": j.canonical_url,
                }
                for j in jobs
            ],
            ensure_ascii=False,
        )
    finally:
        con.close()


@mcp.tool()
def jobs_get(job_id: str, profile_id: str = "") -> str:
    """岗位详情（JD 原文+结构化字段；给 profile_id 时附带最新匹配解释）。只读。"""
    con = _con()
    try:
        js = JobService(con)
        job = js.get(job_id)
        if not job:
            return json.dumps({"error": "job 不存在"}, ensure_ascii=False)
        out = job.model_dump()
        if profile_id:
            m = MatchService(con).latest_for_job(job_id, profile_id)
            if m:
                out["match"] = m.model_dump()
        return json.dumps(out, ensure_ascii=False, default=str)
    finally:
        con.close()


@mcp.tool()
def jobs_import(raw_jobs_json: str, source_id: str = "mcp") -> str:
    """导入岗位（写库）。入参为岗位 raw JSON 数组字符串；返回每条去向（入库/去重/拒绝）。"""
    con = _con()
    try:
        raw = json.loads(raw_jobs_json)
        if not isinstance(raw, list):
            return json.dumps({"error": "需要 JSON 数组"}, ensure_ascii=False)
        stats = JobService(con).ingest(raw, source_id=source_id)
        return stats.model_dump_json()
    finally:
        con.close()


@mcp.tool()
def match_run(profile_id: str, limit: int = 50) -> str:
    """对画像运行匹配排序（写 match_results 历史；结果含 gate 原因与维度依据）。"""
    con = _con()
    try:
        ps = ProfileService(con)
        preset = ps.active_preset(profile_id)
        if preset is None:
            return json.dumps({"error": "画像没有求职偏好(preset)"}, ensure_ascii=False)
        jobs = JobService(con).search("", statuses=["active"], limit=limit)
        outcomes = MatchService(con).rank_jobs(ps.match_view(profile_id), preset, jobs)
        return json.dumps(
            [
                {
                    "job_id": o.job_id, "eligible": o.eligible,
                    "rank_score": o.rank_score, "verdict": o.verdict,
                    "failed_gates": [g.detail for g in o.gate_reasons if not g.passed],
                }
                for o in outcomes[:30]
            ],
            ensure_ascii=False,
        )
    finally:
        con.close()


@mcp.tool()
def resumes_list(profile_id: str) -> str:
    """列出画像的简历与版本。只读。"""
    con = _con()
    try:
        rs = ResumeService(con)
        out = []
        for r in rs.list_resumes(profile_id):
            out.append({**r, "versions": rs.list_versions(r["id"])})
        return json.dumps(out, ensure_ascii=False, default=str)
    finally:
        con.close()


@mcp.tool()
def resumes_prepare(profile_id: str, name: str = "主简历") -> str:
    """从画像生成/更新主简历并运行事实校验（写库）。返回版本号与校验报告。"""
    con = _con()
    try:
        rs = ResumeService(con)
        rid, vid = rs.build_master_from_profile(profile_id, name)
        report = rs.factcheck(vid)
        return json.dumps(
            {"resume_id": rid, "version_id": vid, "factcheck": report}, ensure_ascii=False
        )
    finally:
        con.close()


@mcp.tool()
def applications_list(profile_id: str) -> str:
    """列出投递跟踪（状态/时间线）。只读。"""
    con = _con()
    try:
        return json.dumps(ApplicationService(con).list(profile_id), ensure_ascii=False, default=str)
    finally:
        con.close()


@mcp.tool()
def applications_update(application_id: str, status: str, note: str = "") -> str:
    """推进投递状态（写库，状态机强制）。注意：applied_confirmed 只能经用户确认，
    本工具不允许直接伪造投递成功。"""
    con = _con()
    try:
        if status == "applied_confirmed":
            return json.dumps(
                {"error": "已投递状态必须由用户在 UI/CLI 确认（诚实语义），MCP 不代确认"},
                ensure_ascii=False,
            )
        return json.dumps(
            ApplicationService(con).transition(application_id, status, note=note or None),
            ensure_ascii=False,
            default=str,
        )
    except LifecycleError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def offers_compare(offer_ids_json: str) -> str:
    """Offer 并排比较（只读，结构化事实，无价值判断）。入参为 offer id JSON 数组。"""
    con = _con()
    try:
        ids = json.loads(offer_ids_json)
        return json.dumps(
            ApplicationService(con).compare_offers(ids), ensure_ascii=False, default=str
        )
    except LifecycleError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


def main() -> None:
    apply_all()
    mcp.run()


if __name__ == "__main__":
    main()
