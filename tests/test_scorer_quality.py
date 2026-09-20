#!/usr/bin/env python3
"""test_scorer_quality.py — 评分质量回归测试（真实数据暴露的缺陷，逐个锁死）

背景：2026-09-20 真实库（13k+ 岗位）实测暴露三类误判——
  ①「IC后端」命中画像技能「Go / MySQL 后端」→ IC岗技能维100分（词法巧合）
  ②裸词「设计」把 IC设计/机械设计 岗判成设计艺术对口岗（专业对口100分）
  ③短ASCII词子串误报（quick 含 ui / golang 含 go）
每个用例对应一处修复，防止回归。运行：python3 tests/test_scorer_quality.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ingest, scorer  # noqa: E402

# 合成画像：环境设计×AI 跨学科（结构与真实画像同构，不依赖真实个人数据）
PROFILE = {
    "identity": {"name": "测试用户", "cohort_label": "2027届应届生"},
    "education": [{"school": "某大学", "major": "环境设计", "degree": "本科"}],
    "skills": [
        {"name": "Python", "level": "熟练", "evidence": ["ev1"]},
        {"name": "Go / MySQL 后端", "level": "了解", "evidence": ["ev1"]},
        {"name": "UI/UX 与人机交互设计", "level": "熟练", "evidence": ["ev1"]},
    ],
    "experiences": [{"id": "exp_1", "type": "项目", "name": "多模态应用", "tags": ["多模态大模型", "UI/UX 与人机交互设计"]}],
    "publications": [],
    "awards": [],
    "preferences": {"target_roles": ["交互设计"], "target_cities": ["上海"], "salary_min_k": 12},
    "evidence_index": {"ev1": "测试夹具证据"},
}


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(("✅" if cond else "❌") + f" {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def test_ic_false_friend() -> bool:
    job = {"id": "t_ic", "title": "IC设计  IC验证 IC后端  IC版图（27届提前批）", "company": "某微电子",
           "city": "北京", "description": "", "keywords": [], "extras": {}}
    r = scorer.score_job(job, PROFILE)
    ok = check("IC岗：技能维封顶≤50", r["dims"]["skill"]["score"] <= 50, str(r["dims"]["skill"]["score"]))
    ok &= check("IC岗：经历维封顶≤50", r["dims"]["experience"]["score"] <= 50)
    ok &= check("IC岗：专业对口≤30（不再误判设计类）", r["dims"]["major_fit"]["score"] <= 30, str(r["dims"]["major_fit"]["score"]))
    ok &= check("IC岗：总分不再进 A/B 档", r["total"] < 65, f"total={r['total']} {r['verdict']}")
    ok &= check("IC岗：降档原因如实可解释", any("封顶50" in x for x in r["dims"]["skill"]["reasons"]))
    return ok


def test_design_variants() -> bool:
    job = {"id": "t_des", "title": "【2027校招】视觉设计师（UED）", "company": "某公司", "city": "上海", "description": "", "keywords": []}
    r = scorer.score_job(job, PROFILE)
    ok = check("视觉设计师：专业对口100", r["dims"]["major_fit"]["score"] == 100)
    mech = {"id": "t_mech", "title": "机械结构设计工程师", "company": "某公司", "city": "上海", "description": "", "keywords": []}
    rm = scorer.score_job(mech, PROFILE)
    ok &= check("机械结构设计：不算设计艺术对口（≤30）", rm["dims"]["major_fit"]["score"] <= 30, str(rm["dims"]["major_fit"]["score"]))
    return ok


def test_boundary_hits() -> bool:
    ok = check("边界：'quick' 不误报 ui", scorer._hit("quick scan", ["ui"]) == [])
    ok &= check("边界：'ui/ux 设计' 正常命中 ui", scorer._hit("ui/ux 设计", ["ui"]) == ["ui"])
    ok &= check("边界：'golang' 不误报裸 go", scorer._hit("golang 开发", ["go"]) == [])
    ok &= check("边界：'go语言' 正常命中", scorer._hit("会 go语言 优先", ["go语言"]) == ["go语言"])
    ok &= check("边界：'c++' 命中不被字母数字邻接破坏", scorer._hit("熟悉c++编程", ["c++"]) == ["c++"])
    return ok


def test_ingest_cleanliness() -> bool:
    raw = {"title": "多模态\n大模型\n产品经理", "company": "某公司", "description": "第一行\n第二行", "extras": {"lead": True}}
    job = ingest.normalize(raw, "test")
    ok = check("清洗：标题换行折叠为空格", "\n" not in job["title"] and job["title"] == "多模态 大模型 产品经理")
    ok &= check("清洗：描述保留结构但去首尾空白", job["description"] == "第一行\n第二行")
    ok &= check("契约：extras.lead 线索标记透传", job["extras"].get("lead") is True)
    r1 = scorer.score_job(job, PROFILE)
    r2 = scorer.score_job(job, PROFILE)
    ok &= check("决定论：同输入同分数", r1["total"] == r2["total"])
    return ok


def test_semantic_advisory() -> bool:
    job = {"id": "t_sem", "title": "AI交互设计师（2027校招）", "company": "某公司", "city": "上海",
           "salary": "18-25K",
           "description": "负责大模型产品的交互设计与用户研究，熟悉LLM/Agent技术边界，UI/UX 经验优先。",
           "keywords": []}
    r = scorer.score_job(job, PROFILE)
    ok = check("语义：无 semantic_sim 时不产生复核提示", not any("语义复核" in f for f in r["flags"]))
    job2 = dict(job, extras={"semantic_sim": 0.35})
    r2 = scorer.score_job(job2, PROFILE)
    ok &= check("语义：词法高分+语义0.35 → 人工复核旗标", any("语义复核" in f for f in r2["flags"]), str(r2["flags"]))
    ok &= check("语义：旗标不改变总分", r2["total"] == r["total"])
    job3 = dict(job, extras={"semantic_sim": 0.75})
    r3 = scorer.score_job(job3, PROFILE)
    ok &= check("语义：语义0.75 正常 → 无复核提示", not any("语义复核" in f for f in r3["flags"]))
    return ok


def main() -> int:
    results = [test_ic_false_friend(), test_design_variants(), test_boundary_hits(), test_ingest_cleanliness(),
               test_semantic_advisory()]
    failed = results.count(False)
    if failed:
        print(f"\n{failed} 组用例未通过")
        return 1
    print("\nALL TESTS PASSED (5 suites: IC误报 / 设计变体 / 词边界 / 清洗与线索标记 / 语义复核旗标)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
