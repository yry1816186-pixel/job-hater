#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upskill.py — 技能缺口分析与秋招冲刺学习计划

原则三同样适用：学习计划建议「学会后如何真实地证据化」（做出真项目才能写上简历），
绝不建议「简历上先写上再说」。
"""
from __future__ import annotations

from core import scorer
from core.resume import _jd_text

# 缺口技能组 → 学习路径建议（14/30/60天三档）
LEARNING_PATHS = {
    "docker": ["Docker核心概念（镜像/容器/卷）+ 官方get-started", "把现有项目容器化（写Dockerfile+compose）", "多服务编排 + CI里跑容器化测试"],
    "k8s": ["K8s架构与核心对象", "minikube/kind本地集群部署自己的项目", "Helm打包 + 探针/滚动更新实践"],
    "java": ["Java SE核心 + 集合/并发", "Spring Boot写一个CRUD+鉴权小项目", "参与开源issue或把项目发布到GitHub收star"],
    "vue": ["Vue3组合式API官方教程", "把简历数据做成一个Vue看板页面（真实可用）", "组件化+路由+状态管理重构"],
    "sql": ["SQL基础查询与索引", "LeetCode数据库题30道", "给自己项目加一个真实的数据统计查询"],
    "数据分析": ["pandas/numpy速成", "用真实招聘数据做一次清洗+可视化分析", "把分析结论写成一篇可展示的报告"],
    "spark": ["Spark核心概念（RDD/DataFrame）", "本地数据集ETL练习", "把一个批处理任务迁移到Spark并对比性能"],
    "测试": ["pytest基础+给核心引擎写单测", "覆盖率工具+边界用例", "给项目加CI自动测试"],
    "english": ["每天30min技术文档阅读（官方docs）", "整理一份英文技术词汇表", "英文面试常见问答练习"],
}
DEFAULT_PATH = ["官方文档+入门教程建立框架", "做一个能拿得出手的小项目（真实完成）", "把项目沉淀成简历可写的证据（代码/文档/数据）"]


def analyze(job: dict, profile: dict) -> dict:
    jd = _jd_text(job)
    skill = scorer.score_skill(jd, profile)
    unmatched = skill.get("unmatched", [])
    matched = skill.get("matched", [])
    gaps = []
    for g in scorer.SKILL_GROUPS:
        if any(w.lower() in jd for w in g["jd"]) and not any(p in " ".join(s["name"].lower() for s in profile.get("skills", [])) for p in g["profile"]):
            label = g["jd"][0]
            key = next((k for k in LEARNING_PATHS if k in label.lower() or k in g["profile"]), None)
            gaps.append({"gap": label, "evidence": "JD要求但画像无对应技能证据", "path": LEARNING_PATHS.get(key, DEFAULT_PATH)})
    plan = {
        "job": f"{job.get('company', '')} · {job.get('title', '')}",
        "strength": matched,
        "gaps": gaps,
        "sprint": {
            "14天": "只补第一项缺口的入门档，同时集中投递现有A类岗位（学习与投递并行，绝不停投）",
            "30天": "前两项缺口各推进到「动手做小项目」档，产出可展示的成果",
            "60天": "全部缺口推进到有真实产出物，每个缺口都要有可验证的成果再写入简历",
        },
        "rule": "铁律：任何技能只有在真实完成后才能写入简历。学习产出=新证据，届时通过 /profile 追加到 evidence_index。",
    }
    return plan


def render_plan(plan: dict) -> str:
    lines = [f"# 秋招冲刺学习计划 — {plan['job']}", ""]
    lines.append(f"**已有优势**：{'、'.join(plan['strength']) if plan['strength'] else '（待首轮投递反馈后分析）'}")
    lines.append("")
    if not plan["gaps"]:
        lines.append("✅ 未检出技能缺口：JD 要求的技能方向与画像证据基本对齐。保持投递节奏即可。")
    else:
        lines.append(f"**技能缺口（{len(plan['gaps'])}项）**：")
        for g in plan["gaps"]:
            lines.append(f"- {g['gap']}：{g['evidence']}")
            for i, step in enumerate(g["path"], 1):
                lines.append(f"  {i}. {step}")
    lines.append("")
    lines.append("## 冲刺节奏")
    for k, v in plan["sprint"].items():
        lines.append(f"- **{k}**：{v}")
    lines.append("")
    lines.append(f"> {plan['rule']}")
    return "\n".join(lines)
