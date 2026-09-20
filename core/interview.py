#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interview.py — 面试题库与模拟面试骨架

输出结构化题库（四大板块），每题带「证据锚点」与「回答思路要点」，
LLM（SKILL.md 流程）据此展开成完整答案——答案仍受 factcheck 证据约束：
凡是经历类回答，必须锚定 evidence_index，禁止现场编造新的经历细节。
"""
from __future__ import annotations

from core import scorer
from core.resume import _ev_of, _jd_text, _relevance


def build_question_set(job: dict, profile: dict) -> dict:
    jd = _jd_text(job)
    skills = scorer.score_skill(jd, profile)
    matched = skills.get("matched", [])
    unmatched = skills.get("unmatched", [])
    top_exps = sorted(profile.get("experiences", []), key=lambda e: -_relevance(e, jd))[:2]

    qs: dict = {"job": f"{job.get('company', '')} · {job.get('title', '')}", "sections": []}

    sec1 = {"name": "一、项目深挖（必考，围绕你简历中最相关的经历）", "questions": []}
    for e in top_exps:
        ev = _ev_of(e["id"], profile)
        sec1["questions"].append({
            "q": f"请用2分钟介绍「{e['name']}」中你个人负责的部分，以及最难的坎怎么迈过去的？",
            "anchor": f"[ev:{ev}]",
            "hints": ["STAR结构：背景→任务→行动→量化结果", "难点选真实卡壳点（不要挑简单事），突出排查过程", "数字必须与证据原文一致，不可放大"],
        })
        sec1["questions"].append({
            "q": f"如果重做「{e['name']}」，你会改进哪个技术决策？为什么？",
            "anchor": f"[ev:{ev}]",
            "hints": ["展示反思能力，选一个真实trade-off（如端侧时延vs精度）", "结合岗位JD的技术栈谈改进方向"],
        })
    qs["sections"].append(sec1)

    sec2 = {"name": "二、技术基础（按JD技能关键词命中生成）", "questions": []}
    for m in matched[:5]:
        sec2["questions"].append({
            "q": f"岗位要求「{m}」相关能力——请结合你做过的项目讲一个该技术的实际用法与踩坑。",
            "anchor": "[ev:ev_self_eval]",
            "hints": ["每个概念配一个你自己项目的例子", "不会的概念坦诚说学习计划，不要硬编"],
        })
    qs["sections"].append(sec2)

    # 行为面问题从画像数据派生素材，代码不含任何个人事实字面量
    major = (profile.get("education") or [{}])[0].get("major", "")
    evid_bits = []
    if profile.get("publications"):
        evid_bits.append("论文")
    if profile.get("awards"):
        evid_bits.append("获奖项目")
    if profile.get("experiences"):
        evid_bits.append("项目经历")
    campus_exps = [e.get("name", "") for e in profile.get("experiences", [])
                   if any(k in str(e.get("type", "")) for k in ("校园", "学生", "志愿", "社团", "干部"))]
    evidence_hint = "、".join(evid_bits) or "画像中的真实经历"
    campus_hint = f"可用的学生经历素材：{'、'.join(campus_exps[:2])}" if campus_exps else "若无学生职务，用一次真实组织/协调经历作答，不夸大职权"

    sec3 = {"name": "三、行为面与文化匹配（应届生高频）", "questions": [
        {"q": f"你主修{major or '（画像未填专业）'}，为什么投这个岗位？（按实际岗位方向说明动机与差异化优势）", "anchor": "[ev:ev_education][ev:ev_self_eval]", "hints": ["跨学科/专业差异是差异化优势不是短板", f"用证据链支撑：{evidence_hint}"]},
        {"q": "讲一次你在团队里推动事情落地的经历。", "anchor": "[ev:ev_self_eval]", "hints": [campus_hint, "突出协调与结果，不夸大职权"]},
        {"q": "秋招季时间紧张，你如何安排学习与投递优先级？", "anchor": "[ev:ev_self_eval]", "hints": ["展示你的真实排期方法（如：评分排序→集中投高优→按缺口学习）"]},
    ]}
    qs["sections"].append(sec3)

    sec4 = {"name": "四、反问环节（你问面试官的问题，体现诚意）", "questions": [
        {"q": "这个岗位入职后前三个月的期望产出是什么？", "anchor": "", "hints": ["展示结果导向"]},
        {"q": "团队里应届生的成长路径大概什么样？（若有校招培养体系必问）", "anchor": "", "hints": ["呼应JD中的培养体系关键词"]},
    ]}
    if unmatched:
        sec4["questions"].append({"q": "（技能缺口主动权）关于岗位要求但我还在深入的「" + "、".join(unmatched[:2]) + "」，想请教团队内部的知识沉淀机制是怎样的？", "anchor": "", "hints": ["把缺口转化为学习诚意，绝不谎称已精通"]})
    qs["sections"].append(sec4)
    return qs


def render_question_set(qs: dict) -> str:
    lines = [f"# 模拟面试题库 — {qs['job']}", ""]
    for sec in qs["sections"]:
        lines.append(f"## {sec['name']}")
        lines.append("")
        for i, item in enumerate(sec["questions"], 1):
            lines.append(f"{i}. **{item['q']}**")
            if item.get("anchor"):
                lines.append(f"   - 证据锚点：{item['anchor']}（回答中的经历细节必须与此一致）")
            if item.get("hints"):
                for h in item["hints"]:
                    lines.append(f"   - 思路：{h}")
        lines.append("")
    lines.append("---")
    lines.append("*使用方式：/interview 进入模拟面试时，Claude 逐题扮演面试官追问；你的回答中的经历细节会被对照证据锚点校验，防止练习时形成编造习惯。*")
    return "\n".join(lines)
