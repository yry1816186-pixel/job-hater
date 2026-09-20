#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job_agent_mcp.py — Campus-Job-Agent 本地 MCP Server（stdio，零第三方依赖）

实现 MCP 2024-11-05 协议的最小可用子集：initialize / tools/list / tools/call。
数据不出本机：所有工具直接读写 data/ 下的本地JSON。

在 Claude Code 中的接入（项目级 .mcp.json 已配置）：
  python3 mcp/job_agent_mcp.py

手动测试：
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 mcp/job_agent_mcp.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ingest, interview, risk, scorer, store, upskill  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"


def tool_get_profile_summary(_: dict) -> str:
    store.ensure_defaults()
    p = store.load("profile")
    edu = p["education"][0]
    return json.dumps({
        "name": p["identity"]["name"],
        "cohort": p["identity"]["cohort_label"],
        "school_major": f"{edu['school']} · {edu['major']}",
        "target_roles": p["preferences"]["target_roles"],
        "target_cities": p["preferences"]["target_cities"],
        "skills_count": len(p.get("skills", [])),
        "evidence_anchors": len(p.get("evidence_index", {})) - 1,
        "note": "所有简历生成必须引用 evidence_index，禁止编造",
    }, ensure_ascii=False)


def tool_search_jobs(args: dict) -> str:
    store.ensure_defaults()
    profile = store.load("profile")
    jobs = ingest.list_active_jobs()
    min_score = float(args.get("min_score", 50))
    top = int(args.get("top", 10))
    keyword = args.get("keyword")
    results = []
    for j in jobs:
        if keyword and keyword.lower() not in json.dumps(j, ensure_ascii=False).lower():
            continue
        s = scorer.score_job(j, profile)
        if s["total"] >= min_score:
            results.append({"id": j["id"], "title": j.get("title"), "company": j.get("company"),
                            "city": j.get("city"), "score": s["total"], "verdict": s["verdict"]})
    results.sort(key=lambda x: -x["score"])
    return json.dumps({"count": len(results), "jobs": results[:top]}, ensure_ascii=False)


def tool_score_job_text(args: dict) -> str:
    """即时评分：把粘贴的JD文本打分（不落库）。"""
    store.ensure_defaults()
    profile = store.load("profile")
    job = {
        "id": "adhoc", "title": args.get("title", "粘贴的JD"),
        "company": args.get("company", "未知公司"), "city": args.get("city"),
        "description": args.get("description", ""), "salary": args.get("salary"),
        "experience_required": args.get("experience_required"),
    }
    flags = ingest.detect_flags(job, store.load("config"))
    keep, reason = ingest.campus_filter(job, flags, store.load("config"))
    s = scorer.score_job(job, profile)
    if not keep:
        s["verdict"] = f"🚫 校招过滤未通过（{reason}）——不建议投递，分数仅供参考"
    return json.dumps({"score": s, "campus_filter": {"passed": keep, "reason": reason},
                       "hints": "评分基于确定性锚点；投递材料生成请使用 apply 流程（含真实性校验）"},
                      ensure_ascii=False)


def tool_pipeline_summary(_: dict) -> str:
    store.ensure_defaults()
    app = store.load("applications")
    apps = app.get("applications", [])
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.get("status", "applied")] = by_status.get(a.get("status", "applied"), 0) + 1
    db = store.load("jobs")
    return json.dumps({
        "applications": by_status,
        "blacklist_size": len(app.get("blacklist", [])),
        "jobs_total": len(db.get("jobs", [])),
        "jobs_active": len([j for j in db.get("jobs", []) if j.get("status") != "rejected"]),
    }, ensure_ascii=False)


def tool_add_job(args: dict) -> str:
    stats = ingest.ingest_jobs([args], source_platform=args.get("source_platform", "mcp"))
    d = stats["details"][0] if stats["details"] else {}
    return json.dumps({"result": d}, ensure_ascii=False)


def tool_interview_questions(args: dict) -> str:
    store.ensure_defaults()
    profile = store.load("profile")
    db = store.load("jobs")
    jid = args.get("job_id")
    job = next((j for j in db["jobs"] if j["id"] == jid), None) if jid else None
    if job is None:
        job = {"company": args.get("company", "目标公司"), "title": args.get("title", "目标岗位"),
               "description": args.get("description", "")}
    return json.dumps(interview.build_question_set(job, profile), ensure_ascii=False)


def tool_upskill_plan(args: dict) -> str:
    store.ensure_defaults()
    profile = store.load("profile")
    db = store.load("jobs")
    jid = args.get("job_id")
    job = next((j for j in db["jobs"] if j["id"] == jid), None) if jid else None
    if job is None:
        job = {"company": "目标方向", "title": args.get("title", "通用冲刺"),
               "description": args.get("description", ""), "keywords": []}
    return upskill.render_plan(upskill.analyze(job, profile))


def tool_get_job(args: dict) -> str:
    """单岗完整详情（含 JD 全文与评分依据），供对话式查看。"""
    store.ensure_defaults()
    db = store.load("jobs")
    jid = str(args.get("job_id", ""))
    job = next((j for j in db["jobs"] if j["id"] == jid or j["id"].startswith(jid)), None)
    if job is None:
        return json.dumps({"error": f"未找到岗位 {jid}，先 search_jobs 查看列表"}, ensure_ascii=False)
    profile = store.load("profile")
    s = scorer.score_job(job, profile)
    return json.dumps({"job": {k: job.get(k) for k in
                               ("id", "title", "company", "city", "salary", "url",
                                "description", "published_at", "deadline", "keywords",
                                "extras", "status")},
                       "score": s}, ensure_ascii=False)


def tool_fetch_sources(args: dict) -> str:
    """触发信源采集（等价于 CLI fetch）：wenke 官方接口 / xiaozhao 种子 / 渠道线索。"""
    import subprocess
    root = ROOT
    env = str(args.get("env", "all"))
    venv_py = root / ".venv-sources" / "bin" / "python"
    bridge = root / "adapters" / "sources" / "wenke_bridge.py"
    lines = []
    if env in ("all", "wenke"):
        if venv_py.exists():
            r = subprocess.run([str(venv_py), str(bridge)], capture_output=True, text=True, timeout=1800)
            lines.append(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[:300])
            feeds = root / "data" / "feeds" / "wenke.json"
            if feeds.exists() and r.returncode == 0:
                raw = ingest.load_json_file(str(feeds))
                st = ingest.ingest_jobs(raw, source_platform="official")
                lines.append(f"入库 {st['added']}｜去重 {st['deduped']}｜补全JD {st.get('enriched', 0)}｜拒绝 {st['rejected']}")
        else:
            return json.dumps({"error": "采集底座未安装：bash adapters/sources/setup_env.sh"}, ensure_ascii=False)
    if env in ("all", "xiaozhao"):
        r = subprocess.run([sys.executable, str(root / "adapters" / "sources" / "import_xiaozhao_seed.py")],
                           capture_output=True, text=True, timeout=300)
        lines.append((r.stdout.strip().splitlines() or [""])[-1])
    if env in ("all", "leads"):
        r = subprocess.run([sys.executable, str(root / "adapters" / "sources" / "jobradar_leads.py")],
                           capture_output=True, text=True, timeout=120)
        lines.append((r.stdout.strip().splitlines() or [""])[-1])
    return json.dumps({"results": lines}, ensure_ascii=False)


TOOLS = [
    {"name": "get_profile_summary", "description": "获取本地用户画像摘要（2027届应届生）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_jobs", "description": "搜索本地岗位库并按五维评分排序",
     "inputSchema": {"type": "object", "properties": {
         "keyword": {"type": "string"}, "min_score": {"type": "number"}, "top": {"type": "integer"}}}},
    {"name": "score_job_text", "description": "即时评分一段粘贴的JD文本（不落库）：返回五维分数+校招过滤结论",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "company": {"type": "string"}, "city": {"type": "string"},
         "salary": {"type": "string"}, "experience_required": {"type": "string"},
         "description": {"type": "string"}}, "required": ["description"]}},
    {"name": "add_job", "description": "向本地岗位库添加一个岗位（自动清洗/去重/校招过滤）",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "company": {"type": "string"}, "description": {"type": "string"},
         "city": {"type": "string"}, "salary": {"type": "string"}, "url": {"type": "string"},
         "source_platform": {"type": "string"}}, "required": ["title", "company"]}},
    {"name": "get_job", "description": "查看单个岗位的完整详情（JD全文+五维评分依据）",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"}}, "required": ["job_id"]}},
    {"name": "fetch_sources", "description": "触发信源采集入库（wenke官方接口/校招种子/渠道线索，等价 CLI fetch）",
     "inputSchema": {"type": "object", "properties": {
         "env": {"type": "string", "enum": ["all", "wenke", "xiaozhao", "leads"]}}}},
    {"name": "pipeline_summary", "description": "投递进度统计（按状态分组+黑名单+岗位库存量）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "interview_questions", "description": "基于岗位与画像生成面试题库（含证据锚点）",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "company": {"type": "string"}, "title": {"type": "string"},
         "description": {"type": "string"}}}},
    {"name": "upskill_plan", "description": "技能缺口分析与秋招冲刺学习计划",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}}}},
]

DISPATCH = {
    "get_profile_summary": tool_get_profile_summary,
    "search_jobs": tool_search_jobs,
    "score_job_text": tool_score_job_text,
    "add_job": tool_add_job,
    "get_job": tool_get_job,
    "fetch_sources": tool_fetch_sources,
    "pipeline_summary": tool_pipeline_summary,
    "interview_questions": tool_interview_questions,
    "upskill_plan": tool_upskill_plan,
}


def respond(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "campus-job-agent", "version": "1.0.0"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        fn = DISPATCH.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown tool {name}"}}
        try:
            result = fn(params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": result}]}}
        except Exception as e:  # 错误要友好、可定位，不静默
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = respond(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
