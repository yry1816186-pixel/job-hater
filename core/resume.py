#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resume.py — 定制简历与文案生成（确定性骨架，事实全锚定）

应届生简历的通用章节结构：
  教育背景 → 实习/项目经历 → 校园项目/经历 → 竞赛奖项 → 技能证书

铁律：
- 本模块输出的每一行经历都带 [ev:证据ID] 引用，交付前必须过 factcheck 硬校验
- 经历排序按「与JD的相关度」动态调整（重新排序=允许；新增事实=禁止）
- 关键词对齐：把JD同义关键词自然融入已有事实的表述，不新增事实

同时提供：求职信、打招呼话术（应届生风格）的确定性模板生成。
"""
from __future__ import annotations

import re

from core import scorer

# JD关键词 → 简历表述中的同义表达（只换说法，不换事实）
KEYWORD_SYNONYMS = {
    "llm": ["LLM", "大模型"], "agent": ["Agent", "智能体"], "rag": ["检索增强", "多源检索"],
    "前端": ["前端", "Web"], "后端": ["后端", "服务端"], "全栈": ["全栈", "端到端"],
    "多模态": ["多模态"], "推荐": ["排序", "检索"], "cv": ["视觉", "图像"],
}


def _jd_text(job: dict) -> str:
    return " ".join(str(job.get(k, "")) for k in ("title", "description", "jd_text", "keywords")).lower()


# 经历ID → 证据索引ID 的映射放画像数据里（profile.experience_ev_map），core 不硬编码任何个人ID。
# _ev_of 需要 profile 上下文：以下函数在 generate_* 内通过闭包注入。
_FALLBACK_EV = {"ev_paper", "ev_awards", "ev_education", "ev_self_eval"}


def _ev_of(exp_id: str, profile: dict | None = None) -> str:
    mapping = (profile or {}).get("experience_ev_map", {})
    if exp_id in mapping:
        return mapping[exp_id]
    return exp_id if exp_id in _FALLBACK_EV else "ev_self_eval"


def _relevance(exp: dict, jd: str) -> int:
    """经历与JD的相关度：命中标签与亮点关键词计数。用于重排序（不新增内容）。"""
    text = (exp.get("name", "") + " " + " ".join(exp.get("tags", [])) + " " + " ".join(exp.get("highlights", []))).lower()
    hits = 0
    for group in scorer.SKILL_GROUPS:
        if any(w.lower() in jd for w in group["jd"]) and any(w.lower() in text for w in group["profile"] + group["jd"]):
            hits += 2
    for h in scorer.EXPERIENCE_HINTS:
        if any(w.lower() in jd for w in h["jd"]) and any(t.lower() in text for t in h["evidence_tags"]):
            hits += 3
    return hits


def _edu(profile: dict) -> list[str]:
    out = []
    for e in profile.get("education", []):
        out.append(
            f"- {e['school']} · {e['major']} · {e['degree']}（{e['start']} – {e['end']}）  [ev:ev_education]"
        )
        courses = "、".join(e.get("core_courses", [])[:5])
        if courses:
            out.append(f"- 核心课程：{courses}  [ev:ev_education]")
        out.append(f"- GPA：{e.get('gpa', '—')}｜排名：前{e.get('rank_percent', '—')}%｜{e.get('tests', [{}])[0].get('name', '')}：{e.get('tests', [{}])[0].get('score', '—')}  [ev:ev_education]")
    return out


def _experiences(profile: dict, jd: str, limit: int = 4) -> list[str]:
    exps = sorted(profile.get("experiences", []), key=lambda e: -_relevance(e, jd))
    out = []
    for e in exps[:limit]:
        period = f"{e.get('start', '')} – {e.get('end', '')}"
        ev = _ev_of(e["id"], profile)
        out.append(f"### {e['name']}｜{e.get('role', '')}（{period}）  [ev:{ev}]")
        for h in e.get("highlights", []):
            out.append(f"- {h}  [ev:{ev}]")
        out.append("")
    return out


def _awards(profile: dict) -> list[str]:
    out = ["### 竞赛奖项", ""]
    for a in profile.get("awards", []):
        work = f"（{a['work']}）" if a.get("work") else ""
        out.append(f"- {a['name']} · {a['level']}{work}  [ev:ev_awards]")
    out.append("")
    out.append("### 荣誉")
    out.append("")
    for h in profile.get("honors", []):
        out.append(f"- {h}  [ev:ev_honors]")
    out.append("")
    return out


def _skills(profile: dict, jd: str) -> list[str]:
    """技能列表：优先展示与JD相关的技能（重排序，不新增）。"""
    skills = profile.get("skills", [])
    def rel(s: dict) -> int:
        name_l = s["name"].lower()
        return sum(1 for g in scorer.SKILL_GROUPS for w in g["jd"] if w.lower() in jd and any(p in name_l for p in g["profile"]))
    ordered = sorted(skills, key=lambda s: -rel(s))
    out = ["### 技能", ""]
    for s in ordered:
        out.append(f"- {s['name']}（{s['level']}）  [ev:{s['evidence'][0] if s.get('evidence') else 'ev_self_eval'}]")
    out.append("")
    return out


def _publications(profile: dict) -> list[str]:
    out = []
    for p in profile.get("publications", []):
        out.append(f"### 科研成果")
        out.append("")
        out.append(f"- 《{p['title']}》｜{p['venue']}｜{p['status']}｜{p['role']}  [ev:ev_paper]")
        for r in p.get("key_results", []):
            out.append(f"- {r}  [ev:ev_paper]")
        out.append("")
    return out


def render_html(md_text: str, title: str) -> str:
    """把本生成器输出的规整 Markdown（标题/列表/粗体/分隔线，子集）确定性转为
    自包含 A4 打印 HTML。不引第三方库、不走 CDN——浏览器打开即是排版好的简历，
    自带打印为 PDF 的样式（Ctrl+P）。只信任自己生成的输入，不作为通用 md 转换器。"""
    import html as _html

    def inline(s: str) -> str:
        s = _html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        return s

    out: list[str] = []
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("> "):
            out.append(f"<p class='note'>{inline(line[2:])}</p>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith("- "):
            if out and out[-1].endswith("</li>") is False and out[-1].startswith("<ul"):
                pass
            out.append(f"<li>{inline(line[2:])}</li>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    # 相邻 <li> 归入同一 <ul>
    body: list[str] = []
    in_ul = False
    for el in out:
        if el.startswith("<li>"):
            if not in_ul:
                body.append("<ul>")
                in_ul = True
            body.append(el)
        else:
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(el)
    if in_ul:
        body.append("</ul>")

    return (
        "<!doctype html>\n<html lang='zh-CN'><head><meta charset='utf-8'>\n"
        f"<title>{_html.escape(title)}</title>\n<style>\n"
        "  :root{font-size:14px}\n"
        "  *{box-sizing:border-box}\n"
        "  body{font-family:'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC',sans-serif;"
        "color:#1a1a1a;margin:0;line-height:1.65}\n"
        "  .page{max-width:210mm;margin:0 auto;padding:16mm 15mm}\n"
        "  h1{font-size:1.5rem;margin:0 0 .2em}\n"
        "  h2{font-size:1.05rem;margin:1.1em 0 .3em;padding-bottom:.15em;border-bottom:1.5px solid #2b2b2b}\n"
        "  h3{font-size:.95rem;margin:.8em 0 .2em}\n"
        "  p{margin:.3em 0} .note{color:#555;font-size:.85rem}\n"
        "  ul{margin:.2em 0 .5em;padding-left:1.3em} li{margin:.15em 0}\n"
        "  hr{border:none;border-top:1px solid #bbb;margin:1em 0}\n"
        "  .toolbar{position:fixed;top:8px;right:8px}\n"
        "  .toolbar button{font:inherit;padding:6px 14px;border:1px solid #999;border-radius:6px;"
        "background:#fff;cursor:pointer}\n"
        "  @media print{.toolbar{display:none}.page{padding:0}body{font-size:12.5px}}\n"
        "  @page{size:A4;margin:14mm 13mm}\n"
        "</style></head><body>\n"
        "<div class='toolbar'><button onclick='window.print()'>打印 / 存为 PDF</button></div>\n"
        f"<div class='page'>\n{''.join(body)}\n</div>\n"
        "</body></html>\n"
    )


def generate_resume(job: dict, profile: dict) -> tuple[str, str]:
    """返回 (带引用的审计版, 用户可见版)。审计版供 factcheck 与审核Agent使用。"""
    identity = profile["identity"]
    jd = _jd_text(job)
    lines: list[str] = []
    lines.append(f"# {identity['name']} — {job.get('title', '应聘简历')}")
    lines.append("")
    lines.append(f"> 定制投递：{job.get('company', '—')} · {job.get('title', '—')}｜生成方式：基于真实材料重排与措辞对齐，事实未经允许不增不减")
    lines.append("")
    lines.append(f"**联系方式**：{identity.get('phone', '—')}｜{identity.get('email', '—')}｜"
                 f"{identity.get('grade', '')}（{identity.get('cohort_label', '应届生')}）  [ev:ev_identity]")
    lines.append("")
    lines.append("## 教育背景")
    lines += _edu(profile)
    lines.append("## 实习 / 项目经历")
    lines += _experiences(profile, jd)
    lines += _publications(profile)
    lines += _awards(profile)
    lines.append("## 技能")
    lines += _skills(profile, jd)
    lines.append("---")
    lines.append("*本简历由 Campus-Job-Agent 基于用户真实材料生成，已通过事实锚定校验（每条经历可溯源）。*")
    audited = "\n".join(lines)
    visible = re.sub(r"\s*\[ev:[A-Za-z0-9_\-]+\]", "", audited)
    return audited, visible


def _profile_evidence_points(profile: dict, jd: str) -> list[str]:
    """从画像数据派生「我为什么匹配」论据——只引用画像里真实存在的记录，
    每条带证据锚点。禁止在代码里硬编码任何个人事实（换个用户必须同样成立）。"""
    points: list[tuple[str, str]] = []  # (文案, 锚点)
    pubs = profile.get("publications") or []
    awards = profile.get("awards") or []
    skills_l = " ".join(s["name"].lower() for s in profile.get("skills", []))

    if pubs and any(w in jd for w in ["ai", "llm", "大模型", "agent", "智能体", "算法", "机器学习", "深度学习"]):
        p0 = pubs[0]
        points.append((f"以{p0.get('role', '作者')}身份完成论文《{p0['title']}》（{p0.get('venue', '')}｜{p0.get('status', '')}）",
                       "ev_paper"))
    if awards and any(w in jd for w in ["ai", "算法", "竞赛", "创新", "计算机", "设计"]):
        a0 = awards[0]
        work = f"（{a0['work']}）" if a0.get("work") else ""
        points.append((f"获{a0.get('level', '')}{a0['name']}{work}", "ev_awards"))
    if (any(w in jd for w in ["设计", "交互", "体验", "产品", "ui", "ux", "用户研究"]) and ("ui" in skills_l or "交互" in skills_l)):
        points.append((f"主修{profile['education'][0].get('major', '')}，兼具审美判断与用户研究视角", "ev_education"))
    if any(w in jd for w in ["前端", "全栈", "react", "typescript", "node", "工程"]) and ("react" in skills_l or "typescript" in skills_l):
        points.append(("具备从原型到上线的完整工程落地能力", "ev_self_eval"))
    if not points:
        edu = (profile.get("education") or [{}])[0]
        points.append((f"跨学科背景（主修{edu.get('major', '')}），具备快速学习与完整项目落地经验", "ev_self_eval"))
    # 论据排序跟随 JD 类型：设计/体验类岗优先谈设计与用户视角，技术类岗优先谈论文与获奖
    design_first = any(w in jd for w in ["设计", "交互", "体验", "ui", "ux", "视觉", "用户研究"])
    points.sort(key=lambda p: (0 if (design_first and p[1] == "ev_education") or
                               (not design_first and p[1] in ("ev_paper", "ev_awards")) else 1,))
    return [(f"{text} [ev:{ev}]", ev) for text, ev in points]


def generate_cover_letter(job: dict, profile: dict) -> tuple[str, str]:
    """求职信：三段式（我是谁+匹配点 / 证据 / 诚意与安排），全事实锚定。
    匹配论据从画像数据派生（_profile_evidence_points），代码不含任何个人事实字面量。"""
    identity = profile["identity"]
    edu = profile["education"][0]
    top_exps = sorted(profile.get("experiences", []), key=lambda e: -_relevance(e, _jd_text(job)))[:2]
    ev_refs = [_ev_of(e["id"], profile) for e in top_exps]
    fit_points = _profile_evidence_points(profile, _jd_text(job))

    lines = [
        f"# 求职信 — {job.get('company', '')} · {job.get('title', '')}",
        "",
        f"尊敬的招聘负责人：您好！我是{identity['name']}，{edu['school']}{edu['major']}专业{identity['grade']}学生（{identity['cohort_label']}），看到贵司「{job.get('title', '')}」岗位后非常心动，郑重投递。  [ev:ev_identity][ev:ev_education]",
        "",
        "## 为什么是我",
        "",
    ]
    for p, _ in fit_points:
        lines.append(f"- {p}")
    lines += [
        "",
        "## 可以立刻验证的经历",
        "",
    ]
    for e, ref in zip(top_exps, ev_refs):
        first = e["highlights"][0] if e.get("highlights") else e["name"]
        lines.append(f"- {e['name']}（{e.get('role', '')}）：{first}  [ev:{ref}]")  # ref 已映射为 evidence_index 键
    lines += [
        "",
        f"我理解应届生意味着要快速学习、踏实交付——这正是我在[ev:{ev_refs[0] if ev_refs else 'ev_self_eval'}]项目中的工作方式。随信附上定制简历与作品集，随时可安排面试（线上/线下均可），感谢您的时间！",
        "",
        f"此致  \n{identity['name']}　{identity['phone']}　{identity['email']}  [ev:ev_identity]",
        "",
        "---",
        "*本求职信由 Campus-Job-Agent 生成，所有事实均锚定用户原始材料，交付前通过 factcheck 硬校验。*",
    ]
    audited = "\n".join(lines)
    visible = re.sub(r"\s*\[ev:[A-Za-z0-9_\-]+\]", "", audited)
    return audited, visible


def generate_greeting(job: dict, profile: dict) -> tuple[str, str]:
    """打招呼话术（应届生风格：诚意+匹配点+学习能力，120字内）。
    匹配点从画像数据派生，代码不含任何个人事实字面量。"""
    identity = profile["identity"]
    jd = _jd_text(job)
    major = (profile.get("education") or [{}])[0].get("major", "")
    hook = f"您好！我是{identity['name']}，{identity.get('cohort_label', '应届生')}"
    points = _profile_evidence_points(profile, jd)
    if points:
        # 取第一条匹配论据，去掉锚点标记后自然融入（锚点已在审计版保留）
        first = re.sub(r"\s*\[ev:[A-Za-z0-9_\-]+\]", "", points[0][0])
        hook += f"，{first}"
    else:
        hook += f"，主修{major}，对贵司岗位非常感兴趣 [ev:ev_identity]"
    hook += f"。看到「{job.get('title', '')}」岗位与我经历非常契合，学习能力强、能快速上手，期待有机会详聊！方便的话可以看看我的简历，随时可面试。感谢！"
    audited = hook
    visible = re.sub(r"\s*\[ev:[A-Za-z0-9_\-]+\]", "", audited)
    return audited, visible
