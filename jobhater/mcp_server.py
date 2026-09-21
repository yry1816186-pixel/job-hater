"""Job Hater MCP server（官方 SDK，stdio）。

Agent 接口层（§24）：每个工具都是 services 层的薄封装——与 Web UI / CLI
完全同一实现，无平行逻辑。修改型工具的 side-effect 在工具描述中声明。

运行：job-hater-mcp（console script）或 python -m jobhater.mcp_server
"""
from __future__ import annotations

import json

# mcp 1.x 的 FastMCP 在 2.x 更名为 MCPServer（.tool/.run/instructions API 面兼容，
# 见官方迁移指南 https://py.sdk.modelcontextprotocol.io/v2/migration）。
# 双路 import 使本包同时支持 1.x 与 2.x 运行时。
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP

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
def match_run(profile_id: str, limit: int = 0) -> str:
    """对画像运行匹配排序（写 match_results 历史；limit=0 评估全部在库岗位——默认。
    返回前 30 条，含 gate 原因、匹配分、结论档与检索相关性）。"""
    con = _con()
    try:
        ps = ProfileService(con)
        preset = ps.active_preset(profile_id)
        if preset is None:
            return json.dumps({"error": "画像没有求职偏好(preset)"}, ensure_ascii=False)
        jobs = JobService(con).search("", statuses=["active"], limit=limit or 10**9)
        outcomes = MatchService(con).rank_jobs(ps.match_view(profile_id), preset, jobs)
        return json.dumps(
            [
                {
                    "job_id": o.job_id, "eligible": o.eligible,
                    "rank_score": o.rank_score, "verdict": o.verdict,
                    "relevance": o.relevance_score,
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


@mcp.tool()
def applications_create(job_id: str, profile_id: str) -> str:
    """为岗位建立投递跟踪（写库，初始 discovered）。只建跟踪不投递——投递永远由用户完成。"""
    con = _con()
    try:
        app_id = ApplicationService(con).create(job_id, profile_id)
        return json.dumps({"id": app_id, "status": "discovered"}, ensure_ascii=False)
    except LifecycleError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def paste_parse(jd_text: str, url: str = "") -> str:
    """粘贴 JD 原文 → 结构化草稿（只读，不落库）。字段抽取依据在 parse_notes 里。"""
    from jobhater.services.sources import parse_jd_text

    try:
        draft = parse_jd_text(jd_text, url=url or None)
        return json.dumps(draft, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def preset_get(profile_id: str) -> str:
    """读取画像当前生效的求职偏好（匹配的全部依据）。只读。"""
    con = _con()
    try:
        preset = ProfileService(con).active_preset(profile_id)
        if preset is None:
            return json.dumps({"error": "画像没有求职偏好(preset)"}, ensure_ascii=False)
        return preset.model_dump_json()
    finally:
        con.close()


@mcp.tool()
def ai_egress_disclosure(task: str) -> str:
    """读取指定 AI 任务的出境数据披露（agent 代用户操作前必须原样呈现该文本）。只读。"""
    con = _con()
    try:
        from jobhater.services.ai import AIService

        return json.dumps(
            {"task": task, "disclosure": AIService(con).egress_disclosure(task)},
            ensure_ascii=False,
        )
    except KeyError as e:
        return json.dumps({"error": f"未知任务类型: {e}"}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def materials_interview_questions(profile_id: str, job_id: str) -> str:
    """生成面试题库（只读分析，不落库；题目只引用画像真实条目）。"""
    con = _con()
    try:
        from jobhater.services.materials import MaterialsService

        out = MaterialsService(con).build_interview_questions(profile_id, job_id)
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def stats_overview(profile_id: str = "") -> str:
    """求职漏斗与洞察（确定性聚合）：漏斗转化/周活动/健康度/渠道效果/Top雇主。"""
    con = _con()
    try:
        from jobhater.services.stats import StatsService

        out = StatsService(con).overview(profile_id or None)
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def stats_salary(city: str = "") -> str:
    """薪资分位洞察（本地库样本 p25/p50/p75；样本数随行，<5 不具参考性）。"""
    con = _con()
    try:
        from jobhater.services.stats import StatsService

        out = StatsService(con).salary_insights(city=city or None)
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def ats_scan(job_id: str, resume_version_id: str) -> str:
    """ATS 简历-JD 匹配报告（确定性）：分数/硬软技能覆盖/缺失词/证据链/反堆砌提醒。"""
    con = _con()
    try:
        from jobhater.services.ats_scan import ATSScanService

        out = ATSScanService(con).scan(job_id, resume_version_id)
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool()
def reminders_list(profile_id: str = "", include_done: bool = False) -> str:
    """提醒列表（可选含已完成）+ 确定性跟进建议（建议不落库）。"""
    con = _con()
    try:
        from jobhater.services.reminders import RemindersService

        rs = RemindersService(con)
        out = {
            "reminders": rs.list(include_done=include_done),
            "suggestions": rs.suggestions(profile_id=profile_id or None),
        }
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        con.close()


def main() -> None:
    apply_all()
    mcp.run()


if __name__ == "__main__":
    main()
