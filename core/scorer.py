#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scorer.py — 五维匹配评分引擎（2027届应届生秋招定向）

评分铁律：
1. 权重固定：技能匹配35% + 实习/项目经历25% + 地点与薪资20% + 企业与发展15% + 专业对口5%
2. 工作年限不参与打分（权重为0），改由过滤层硬性排除≥1年经验岗位
3. 每个维度有明确打分锚点（本文件常量表），同一输入永远得到同一分数——可复现、可解释
4. 应届生加分项独立计分（不进权重，封顶+10）：国企央企/落户政策/校招培养体系
5. 风险旗标独立输出：薪资倒挂风险、薪资未标注、技能缺口
"""
from __future__ import annotations

import re
from typing import Any

WEIGHTS = {
    "skill": 0.35,       # 技能匹配度
    "experience": 0.25,  # 实习/项目经历
    "location_salary": 0.20,
    "company_growth": 0.15,
    "major_fit": 0.05,
}

# 技能同义组：JD 中出现的写法 → 用户画像中对应技能组。
# 每组列出画像技能名（profile.skills[].name 的关键词）与 JD 侧触发词。
SKILL_GROUPS = [
    {"profile": ["python"], "jd": ["python", "pytorch", "机器学习", "深度学习"]},
    {"profile": ["pytorch"], "jd": ["pytorch", "torch", "深度学习框架"]},
    {"profile": ["llm", "agent", "大模型"], "jd": ["llm", "大模型", "aigc", "agent", "智能体", "gpt", "prompt", "rag", "多模态"]},
    {"profile": ["typescript", "node"], "jd": ["typescript", "node", "nodejs", "javascript", "全栈"]},
    {"profile": ["react", "前端"], "jd": ["react", "前端", "vue", "h5", "web"]},
    {"profile": ["go", "mysql", "后端"], "jd": ["go语言", "golang", "mysql", "后端", "服务端"]},
    {"profile": ["android", "kotlin", "移动端"], "jd": ["android", "kotlin", "移动端", "app开发", "客户端"]},
    {"profile": ["部署", "优化", "量化", "lora"], "jd": ["模型部署", "推理优化", "量化", "lora", "微调", "fine-tune", "sft"]},
    {"profile": ["检索", "排序", "rrf", "rerank"], "jd": ["检索", "搜索", "排序", "rerank", "向量", "embedding", "faiss", "推荐"]},
    {"profile": ["ui", "ux", "交互", "人机交互"], "jd": ["ui", "ux", "交互设计", "体验设计", "视觉设计", "用户研究", "人机交互"]},
    {"profile": ["计算机辅助设计", "环境设计", "空间"], "jd": ["空间设计", "环境设计", "室内", "景观", "展示设计", "建筑装饰"]},
    {"profile": ["产品"], "jd": ["产品经理", "产品助理", "产品设计", "需求分析", "产品策划"]},
    {"profile": ["数据分析"], "jd": ["数据分析", "sql", "ab实验", "数据敏感"]},
    {"profile": ["java"], "jd": ["java", "spring", "jvm", "kafka"]},
    {"profile": ["c++"], "jd": ["c++", "cpp"]},
    {"profile": ["docker", "k8s", "云"], "jd": ["docker", "k8s", "kubernetes", "云原生", "aws", "azure", "阿里云", "腾讯云"]},
    {"profile": ["spark", "大数据"], "jd": ["spark", "hadoop", "flink", "大数据", "数据仓库", "hive"]},
]

# 经历匹配锚点：JD 领域触发词 → 用户画像证据（experiences.tags / publications）
# 注意：触发词用可靠信号词（如"视觉设计"而非裸"设计"），避免 IC设计/机械设计 误触 UI/UX 证据
EXPERIENCE_HINTS = [
    {"jd": ["llm", "大模型", "aigc", "agent", "智能体", "多模态", "多模态大模型"], "evidence_tags": ["多模态大模型", "Agent 编排", "VLM", "AI Scientist"]},
    {"jd": ["算法", "机器学习", "深度学习", "cv", "计算机视觉"], "evidence_tags": ["多模态大模型", "CLIP", "FAISS", "VLM"]},
    {"jd": ["ai", "人工智能", "ai4s", "ai scientist"], "evidence_tags": ["AI Scientist", "AI4S", "Agent 编排"]},
    {"jd": ["全栈", "前端", "web", "react", "typescript"], "evidence_tags": ["全栈", "可信工程"]},
    {"jd": ["科研", "研究", "论文", "学术"], "evidence_tags": ["AI Scientist", "Agent 编排"]},
    {"jd": ["交互设计", "视觉设计", "体验设计", "ui", "ux", "用户研究"], "evidence_tags": ["UI/UX 与人机交互设计", "组织协调"]},
    {"jd": ["产品", "策划"], "evidence_tags": ["UI/UX 与人机交互设计"]},
    {"jd": ["检索", "搜索", "推荐", "知识"], "evidence_tags": ["信息检索与排序（RRF、Listwise Rerank）"]},
]

# 企业类型锚点
SOE_WORDS = ["国企", "央企", "事业单位", "研究院", "研究所", "科学院", "国有", "国资"]
STATE_OWNED_BONUS_WORDS = ["落户", "户口", "人才补贴", "安家费", "住房补贴"]
TRAINING_WORDS = ["管培生", "培养体系", "培养计划", "校招专项", "英才计划", "管培", "rotation", "轮岗", "菁英", "领航"]
OUTSOURCING_WORDS = ["外包", "驻场", "人力服务", "劳务派遣", "resource outsourcing"]
HEADHUNTER_WORDS = ["猎头", "rpo", "招聘顾问", "人力资源服务"]

# 设计类岗位的可靠信号（禁用裸词"设计"：IC设计/结构设计/机械设计都不是环境设计的对口岗）
DESIGN_WORDS = ["视觉设计", "交互设计", "体验设计", "用户研究", "ui", "ux", "ued",
                "平面设计", "工业设计", "环境设计", "空间设计", "展示设计", "艺术设计", "美术", "动画"]
# 硬件/芯片等强领域信号：命中时技能/经历两维封顶50（画像技能域未覆盖该领域，如实降档而非假装匹配）
HARD_DOMAIN_WORDS = ["ic设计", "ic验证", "ic后端", "ic版图", "芯片", "版图", "模拟电路", "数字电路",
                     "电路设计", "硬件工程师", "机械设计", "机械工程师", "结构设计", "模具",
                     "仿真工程师", "工艺工程师", "材料研发", "土建", "施工"]

# 薪资倒挂启发式：校招岗位薪资上限低于该城市社招同级别保守均值过多 → 提示风险。
# 保守常数表（K/月，应届技术/设计类参考下限），仅作提示，不作为否决依据。
CITY_SALARY_FLOOR_K = {"南京": 12, "上海": 15, "北京": 15, "杭州": 14, "深圳": 15, "广州": 13, "成都": 10, "武汉": 10, "西安": 9, "默认": 10}

VERDICTS = [(80, "A·强烈推荐"), (65, "B·推荐"), (50, "C·可考虑"), (0, "D·暂缓")]


def _text(job: dict) -> str:
    parts = [str(job.get(k, "")) for k in ("title", "company", "description", "jd_text", "keywords", "department")]
    parts += [str(k) for k in (job.get("keywords") or [])]
    parts += [str(t) for t in (job.get("tags") or [])]
    return " ".join(parts).lower()


def _hit(text: str, words: list[str]) -> list[str]:
    """信号词命中。短 ASCII 词（ui/ux/go/cv/ai 等）必须整词命中：
    子串匹配会把 quick 当成 ui、django 当成 go——这类误报在真实数据里出现过。"""
    hits = []
    for w in words:
        lw = w.lower()
        if lw.isascii() and len(lw) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(lw)}(?![a-z0-9])", text):
                hits.append(w)
        elif lw in text:
            hits.append(w)
    return hits


def _parse_salary(job: dict) -> tuple[float | None, float | None]:
    """salary_min_k / salary_max_k（千元/月）。支持结构化字段与常见文本格式。"""
    lo, hi = job.get("salary_min_k"), job.get("salary_max_k")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return float(lo), float(hi)
    raw = str(job.get("salary") or job.get("salary_text") or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~–—至]\s*(\d+(?:\.\d+)?)\s*[kK千]", raw)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*[-~–—至]\s*(\d+(?:\.\d+)?)\s*万", raw)
        if m:
            return float(m.group(1)) * 10, float(m.group(2)) * 10
        return None, None
    return float(m.group(1)), float(m.group(2))


def _parse_city(job: dict) -> str | None:
    for key in ("city", "location", "work_city"):
        v = job.get(key)
        if v:
            return str(v)
    return None


def _hard_domain_cap(dims: dict, job_text: str) -> list[str]:
    """硬件/芯片等强领域命中时，把技能/经历两维封顶50，并如实写明原因。
    动机：画像技能域与该领域无重叠时，词法巧合（如「IC后端」命中「后端」）会制造虚假高分。
    封顶而非否决：岗位若确有软件/算法子方向，评分仍可进入 C 档被看见。"""
    hard = _hit(job_text, HARD_DOMAIN_WORDS)
    if not hard:
        return []
    note = (f"岗位核心领域命中硬件/芯片类信号 {hard[:3]}，画像技能域未覆盖，"
            f"技能/经历两维封顶50（如实降档，不猜测可迁移性）")
    for k in ("skill", "experience"):
        if dims[k]["score"] > 50:
            dims[k]["score"] = 50
            dims[k]["reasons"].append(note)
    return [note]


def score_skill(job_text: str, profile: dict) -> dict:
    """技能匹配（35%）。锚点：JD 命中的同义组中，画像有证据支撑的比例。"""
    jd_groups = [g for g in SKILL_GROUPS if _hit(job_text, g["jd"])]
    if not jd_groups:
        return {"score": 50, "reasons": ["JD 未检出明确技能关键词，按中性分50（不可解释性优先于猜测）"]}
    matched, unmatched = [], []
    for g in jd_groups:
        if _hit(" ".join(s["name"].lower() for s in profile.get("skills", [])), g["profile"]):
            matched.append(g["jd"][0])
        else:
            unmatched.append(g["jd"][0])
    score = round(100 * len(matched) / len(jd_groups))
    score = max(score, 30)  # 命中过 JD 技能组但全不匹配时，保底30而非0，避免误杀
    reasons = [f"JD 技能组命中 {len(matched)}/{len(jd_groups)}：匹配 {matched[:8]}"]
    if unmatched:
        reasons.append(f"技能缺口：{unmatched[:8]}")
    return {"score": score, "reasons": reasons, "matched": matched, "unmatched": unmatched}


def score_experience(job_text: str, profile: dict) -> dict:
    """实习/项目经历（25%）。锚点：JD 领域触发词对应画像证据标签的覆盖度。"""
    hints = [h for h in EXPERIENCE_HINTS if _hit(job_text, h["jd"])]
    if not hints:
        return {"score": 50, "reasons": ["JD 未检出可对应的经历领域，按中性分50"]}
    all_tags = set()
    for e in profile.get("experiences", []):
        all_tags.update(t.lower() for t in e.get("tags", []))
    for p in profile.get("publications", []):
        all_tags.update(w.lower() for w in ["论文", "科研", "研究", "ei", "多模态", "深度学习", "机器学习"])
    covered = [h["evidence_tags"][0] for h in hints if any(t.lower() in all_tags for t in h["evidence_tags"])]
    score = round(100 * len(covered) / len(hints))
    reasons = [f"JD 经历领域命中 {len(hints)} 项，画像证据覆盖 {len(covered)} 项：{covered[:6]}"]
    if score < 60:
        reasons.append("部分领域缺少直接项目证据，建议以学习能力+可迁移经历补强（不可编造）")
    return {"score": score, "reasons": reasons}


def score_location_salary(job: dict, job_text: str, profile: dict) -> dict:
    """地点与薪资（20%）。锚点：城市=期望城市100/远程90/其他30/未标注60；薪资=达标100/不足按比例/未标注60。"""
    reasons, scores = [], []
    city = _parse_city(job)
    prefs = profile.get("preferences", {}).get("target_cities", [])
    if not city:
        city_score, city_reason = 60, "城市未标注，按中性分60"
    elif any(c in city for c in prefs):
        city_score = 100
        city_reason = f"{city} 在期望城市 {prefs} 内"
    elif "远程" in job_text or "remote" in job_text:
        city_score, city_reason = 90, "支持远程"
    else:
        city_score, city_reason = 30, f"{city} 不在期望城市内"
    scores.append(city_score)
    reasons.append(city_reason)

    lo, hi = _parse_salary(job)
    want = profile.get("preferences", {}).get("salary_min_k", 0)
    if hi is None:
        sal_score, sal_reason = 60, "薪资未标注，按中性分60"
    elif hi >= want:
        sal_score, sal_reason = 100, f"薪资上限 {hi:g}K ≥ 期望下限 {want}K"
    else:
        sal_score = round(60 * hi / want) if want else 60
        sal_reason = f"薪资上限 {hi:g}K < 期望下限 {want}K，按比例给分"
    scores.append(sal_score)
    reasons.append(sal_reason)
    return {"score": round(sum(scores) / len(scores)), "reasons": reasons}


def score_company_growth(job: dict, job_text: str) -> dict:
    """企业与发展（15%）。锚点：国企央企/科研院所100 → 知名企业80 → 一般60 → 存疑40。"""
    reasons = []
    soe = _hit(job_text, SOE_WORDS)
    if soe:
        score = 100
        reasons.append(f"国企/央企/科研院所属性（命中：{soe[:3]}），稳定与发展兼得")
    else:
        known = _hit(job_text, ["上市", "独角兽", "500强", "知名", "头部", "独角兽企业"])
        if known:
            score = 80
            reasons.append(f"企业知名度信号：{known[:3]}")
        else:
            score = 60
            reasons.append("无明确企业类型信号，按一般企业60")
    training = _hit(job_text, TRAINING_WORDS)
    if training:
        reasons.append(f"校招培养体系信号：{training[:4]}")
    return {"score": score, "reasons": reasons}


def score_major_fit(job: dict, job_text: str, profile: dict) -> dict:
    """专业对口（5%）。锚点：不限100 / 设计艺术类100 / 硬件芯片类30 / 计算机·AI类70（跨学科有证据）/ 强限定其他20。
    设计类判定用 DESIGN_WORDS 可靠信号词——裸词「设计」会把 IC设计/机械设计 误判为设计艺术岗。"""
    major = profile.get("education", [{}])[0].get("major", "")
    if _hit(job_text, ["专业不限", "不限专业", "专业无关", "all majors"]):
        return {"score": 100, "reasons": ["专业不限"]}
    if _hit(job_text, DESIGN_WORDS):
        return {"score": 100, "reasons": [f"设计与艺术类岗位，与主修专业「{major}」直接对口"]}
    if _hit(job_text, ["仅限", "只限", "相关专业"]) and "设计" not in major:
        return {"score": 20, "reasons": ["岗位对专业有强限定，跨专业风险高"]}
    if _hit(job_text, HARD_DOMAIN_WORDS):
        return {"score": 30, "reasons": ["硬件/芯片/机械等强限定领域，与主修专业跨度大；若含软件子方向请在详情页单独确认"]}
    if _hit(job_text, ["计算机", "软件工程", "人工智能", "电子信息", "通信工程", "自动化", "数学"]):
        reasons = [f"计算机/泛IT类岗位，主修「{major}」非直接对口；但画像有 AI 项目与 EI 论文等跨学科硬证据"]
        return {"score": 70, "reasons": reasons}
    return {"score": 80, "reasons": ["未见明确专业限定，默认较开放"]}


def bonuses_and_flags(job: dict, job_text: str, dims: dict) -> tuple[list[tuple[str, int]], list[str]]:
    """应届生加分项（封顶+10）与风险旗标。加分为 (名称, 分值) 二元组，避免脆弱的字符串解析。"""
    bonuses: list[tuple[str, int]] = []
    flags: list[str] = []
    if _hit(job_text, SOE_WORDS):
        bonuses.append(("国企/央企/事业单位", 5))
    if _hit(job_text, STATE_OWNED_BONUS_WORDS):
        bonuses.append(("落户/人才政策", 3))
    if _hit(job_text, TRAINING_WORDS):
        bonuses.append(("校招培养体系", 3))
    if dims["skill"].get("unmatched"):
        flags.append("⚠️ 技能缺口：" + "、".join(dims["skill"]["unmatched"][:6]) + "（须如实面对，禁止在简历中编造）")
    lo, hi = _parse_salary(job)
    city = _parse_city(job)
    floor = CITY_SALARY_FLOOR_K.get(city or "默认", CITY_SALARY_FLOOR_K["默认"])
    if hi is not None and hi < floor:
        flags.append(f"⚠️ 薪资倒挂风险：上限{hi:g}K 低于{city or '同级别城市'}保守参考线{floor}K")
    if hi is None:
        flags.append("⚠️ 薪资未标注，需面试前确认")
    return bonuses, flags


def score_job(job: dict, profile: dict) -> dict:
    """主入口：返回可解释的完整评分结果（纯函数，可复现）。"""
    text = _text(job)
    dims = {
        "skill": score_skill(text, profile),
        "experience": score_experience(text, profile),
        "location_salary": score_location_salary(job, text, profile),
        "company_growth": score_company_growth(job, text),
        "major_fit": score_major_fit(job, text, profile),
    }
    total = sum(dims[k]["score"] * WEIGHTS[k] for k in WEIGHTS)
    bonuses, flags = bonuses_and_flags(job, text, dims)
    hard_notes = _hard_domain_cap(dims, text)
    if hard_notes:  # 封顶后重算总分（bonuses 照常独立，不参与封顶）
        total = sum(dims[k]["score"] * WEIGHTS[k] for k in WEIGHTS)
    # 语义相似度参考（可选增强，semantic_enrich 写入）：不参与权重，只在
    # 词法高分但语义明显偏低时提示人工复核——两种信号打架时诚实说出来。
    sem = (job.get("extras") or {}).get("semantic_sim")
    if isinstance(sem, (int, float)) and total >= 65 and sem < 0.4:
        flags.append(f"⚠️ 语义复核提示：向量相似度 {sem:.0%} 明显偏低而词法评分较高（可能关键词堆砌或领域错位），建议人工复核")
    total = min(100.0, total + min(10, sum(points for _, points in bonuses)))
    verdict = next(label for threshold, label in VERDICTS if total >= threshold)
    return {
        "job_id": job.get("id"),
        "total": round(total, 1),
        "verdict": verdict,
        "dims": {
            k: {
                "score": dims[k]["score"],
                "weight": WEIGHTS[k],
                "weighted": round(dims[k]["score"] * WEIGHTS[k], 1),
                "reasons": dims[k]["reasons"],
            }
            for k in WEIGHTS
        },
        "bonus": bonuses,
        "flags": flags,
    }
