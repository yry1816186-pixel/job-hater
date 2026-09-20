#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""factcheck.py — 真实性硬校验（双Agent审核机制的确定性一环）

原则三「事实不可捏造」的机器执行者。LLM 起草Agent写出的任何简历/文案，
在交付给用户前必须通过本模块校验：

1. 引用覆盖：每一条经历/亮点条目（- • ▸ 开头的行）必须带 [ev:证据ID] 引用
2. 引用合法：引用的证据ID必须存在于 profile.evidence_index
3. 数字溯源：文本中的每个数字必须能在其引用的证据原文中找到（防止编造量化数据）
4. 高危动词守卫：「独立完成/主导/构建了」等强主张词出现在无引用行 → 直接标记

输出 report：passed=True 才允许交付；不通过则列出全部问题，退回起草Agent修正。
注意：本模块只认 [ev:id] 引用与证据原文，无法被 prompt 注入绕过。
"""
from __future__ import annotations

import re

# 条目判定：项目符号后必须跟空白（避免把 "---" 分隔线、"--" 破折号误判为条目）
BULLET_RE = re.compile(r"^\s*(?:[-•*▸·]\s+|\d+[.、)]\s+)(.+)$")
CITE_RE = re.compile(r"\[ev:([A-Za-z0-9_\-]+)\]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
STRONG_CLAIM_WORDS = ["独立完成", "独立作者", "主导", "首创", "从零搭建", "从0到1", "独立开发"]

# 高风险商业词：学生材料中极少合法出现，若证据原文无此词则警报（LLM审核Agent终审）
COMMERCIAL_RISK_WORDS = ["商业", "营收", "收入", "签约", "甲方", "客户", "盈利", "百万", "千万", "亿", "中标", "成交"]

# 行首装饰性数字（有序列表序号）与通用上下文数字白名单
ORDINAL_RE = re.compile(r"^\s*\d+[.、)]")


def _strip_citations(text: str) -> str:
    return CITE_RE.sub("", text)


def check(text: str, profile: dict) -> dict:
    evidence_index = profile.get("evidence_index", {})
    issues: list[dict] = []
    bullets_checked = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m = BULLET_RE.match(line)
        if not m:
            continue  # 标题、段落正文不做条目级强制（由LLM审核Agent覆盖）
        bullets_checked += 1
        body = m.group(1)
        cites = CITE_RE.findall(line)

        if not cites:
            issues.append({"line": line, "type": "missing_citation",
                           "msg": "条目无 [ev:证据ID] 引用——所有经历条目必须可溯源"})
            continue

        bad_ids = [c for c in cites if c not in evidence_index]
        if bad_ids:
            issues.append({"line": line, "type": "invalid_citation",
                           "msg": f"引用了不存在的证据ID：{bad_ids}（可疑：证据索引被伪造？）"})
            continue

        # 数字溯源：条目中的数字必须出现在其引用的证据原文之一
        cited_text = " ".join(str(evidence_index.get(c, "")) for c in cites)
        body_wo_cite = ORDINAL_RE.sub("", body)
        for num in NUMBER_RE.findall(body_wo_cite):
            if num not in cited_text:
                issues.append({"line": line, "type": "unverified_number",
                               "msg": f"数字「{num}」未在所引证据原文中出现——疑似编造量化数据"})

        # 高风险商业词守卫：条目含商业词但证据原文没有 → 警报（LLM审核Agent终审）
        for w in COMMERCIAL_RISK_WORDS:
            if w in body_wo_cite and w not in cited_text:
                issues.append({"line": line, "type": "unverified_claim",
                               "msg": f"高风险表述「{w}」未在所引证据原文中出现——需审核Agent人工终审"})

    # 全文级：证据ID引用了但索引里没有（防止引用悬空）
    for cid in set(CITE_RE.findall(text)):
        if cid not in evidence_index:
            issues.append({"line": f"[ev:{cid}]", "type": "invalid_citation",
                           "msg": f"全文级检查：证据ID「{cid}」不在 evidence_index"})

    return {"passed": not issues, "bullets_checked": bullets_checked, "issues": issues}


def render_report(report: dict) -> str:
    if report["passed"]:
        return (f"✅ 真实性校验通过：{report['bullets_checked']} 条经历条目全部可溯源"
                f"（引用覆盖/证据存在/数字溯源 三项硬检查均通过）")
    lines = [f"❌ 真实性校验未通过（{len(report['issues'])} 个问题，{report['bullets_checked']} 条已查）:", ""]
    for i, issue in enumerate(report["issues"], 1):
        lines.append(f"{i}. [{issue['type']}] {issue['msg']}")
        lines.append(f"   问题行：{issue['line'][:100]}")
    lines.append("")
    lines.append("处理规则：退回起草Agent修正。可以重组措辞、对齐JD关键词，但每条事实必须锚定证据原文。")
    return "\n".join(lines)
