"""简历 ↔ JD ATS 匹配报告（确定性规则引擎；对标 Jobscan，以证据链与反堆砌超越）。

计分透明度（回应竞品"黑盒"批评——哪些计入、哪些仅建议，全部声明）：

计入总分（0-100）：
- 关键词覆盖 70 分 = 硬技能 45 + 软技能 10 + 其他重要词 15
  （每个关键词按 JD 内权重计：出现在标题 ×3、任职要求 ×2、正文 ×1、频次 ×min(count,3)）
- 可解析性 30 分 = 联系方式完整 10（basics.email 与 phone 均非空）
                 + 标准分节 10（work/education/projects/skills 至少 3 个非空）
                 + 日期一致性 10（全部 YYYY-MM 或 YYYY-MM-DD，无混写/非标格式）

仅建议（不计分，如实标注 advisory）：
- 量化成果占比（含数字的 bullet 比例）
- 职位名对齐（basics.label / 最近 work.position 与 JD 标题的词面重合）
- 关键词堆砌黄牌（技能词只出现在 skills 分节、经历/项目零支撑）

诚实语义：
- 「命中」= 关键词本身或其同义组任一写法出现在简历任一分节；
- 证据链 = 每个命中词给出简历原文摘录 + 所在分节 + JD 内出现次数（竞品均不提供）；
- 「缺失」不暗示要堆进简历——反堆砌提示与 factcheck 立场一致：不教用户作弊；
- 分数是可解析性与词汇覆盖的机械度量，不是"简历好坏"的判断。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import jieba.posseg as pseg

from jobhater.services.matching import SKILL_SYNONYM_GROUPS

# 行业共识的目标线（Jobscan 官方建议 75-80%）；我们取 75 为"达标"
TARGET_SCORE = 75

# 软技能词表（数据层；中英并收，命中即归入"软技能"组）
SOFT_SKILL_WORDS: list[str] = [
    "沟通", "协作", "团队合作", "团队协作", "抗压", "学习能力", "责任心", "主动",
    "主动性", "领导力", "细心", "严谨", "自驱", "自驱力", "跨部门", "表达", "解决问题",
    "逻辑思维", "创新", "好奇心", "同理心", "适应能力", "时间管理", "执行力",
    "communication", "teamwork", "leadership", "ownership", "collaboration",
    "proactive", "adaptability", "problem-solving",
]

# 语言学停用词 + 校招批次/流程词（不构成技能信号）
_STOPWORDS: set[str] = {
    "我们", "你们", "岗位", "职位", "工作", "职责", "要求", "任职", "资格", "优先",
    "具备", "熟悉", "掌握", "了解", "负责", "参与", "相关", "以上", "以下", "以及",
    "公司", "团队", "部门", "业务", "项目", "经验", "能力", "学历", "专业", "应届",
    "毕业", "招聘", "校园", "实习生", "全职", "兼职", "正式", "员工", "福利", "薪资",
    "待遇", "五险", "一金", "工作地", "地点", "城市", "简历", "投递", "面试", "offer",
    "岗位描述", "加分", "项一", "有一", "良好", "较强", "优秀", "扎实", "基本", "常用",
    "之一", "等等", "等", "和", "与", "或", "及", "的", "了", "在", "为", "对", "从",
    "进行", "开展", "完成", "通过", "根据", "按照", "包括", "具有", "拥有", "提供",
    "优先一", "届", "校园招聘",
}

_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./_-]{1,24}")
_DATE_OK_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


@dataclass
class KeywordTerm:
    term: str                 # 归一化词条（小写）
    display: str              # 原始写法（首次出现）
    category: str             # hard / soft / other
    jd_count: int = 0         # JD 内出现次数（含同义写法）
    in_title: bool = False
    in_requirements: bool = False
    weight: float = 0.0
    variants: list[str] = field(default_factory=list)  # 命中判定用的同义写法集合


class ATSScanError(ValueError):
    """非法扫描请求（岗位/简历版本不存在、简历无内容等）。"""


class ATSScanService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---------- 主入口 ----------

    def scan(self, job_id: str, resume_version_id: str) -> dict:
        job = self._job_text(job_id)
        sections = self._resume_sections(resume_version_id)
        terms = self._extract_jd_keywords(job)
        sec_texts = self._flatten_sections(sections)

        matched, missing = [], []
        for t in terms:
            hits = self._find_hits(t, sec_texts)
            entry = {
                "term": t.display, "category": t.category,
                "jd_count": t.jd_count, "in_title": t.in_title,
                "weight": round(t.weight, 2),
            }
            if hits:
                entry["hits"] = hits
                entry["resume_count"] = sum(h["count"] for h in hits)
                matched.append(entry)
            else:
                missing.append(entry)

        cov = self._coverage_score(matched, missing)
        fmt, fmt_score = self._format_checks(sections)
        score = min(round(cov["score"] + fmt_score), 100)
        report = {
            "job_id": job_id,
            "resume_version_id": resume_version_id,
            "score": score,
            "target": TARGET_SCORE,
            "band": self._band(score),
            "coverage": cov,
            "parseability": {"score": fmt_score, "checks": fmt},
            "keywords": {
                "matched": sorted(matched, key=lambda e: -e["weight"]),
                "missing": sorted(missing, key=lambda e: -e["weight"]),
                "hard_missing": [e["term"] for e in missing if e["category"] == "hard"],
            },
            "advisory": self._advisories(sections, sec_texts, job, matched),
            "methodology": {
                "counted": [
                    "关键词覆盖 70 分（硬技能45/软技能10/其他重要词15，按 JD 内权重加权）",
                    "可解析性 30 分（联系方式10/标准分节10/日期一致性10）",
                ],
                "advisory_only": ["量化成果占比", "职位名对齐", "关键词堆砌黄牌"],
                "note": "分数是可解析性与词汇覆盖的机械度量，不是简历好坏的判断；"
                        "缺失词不建议无证据堆砌（见黄牌）。",
            },
        }
        return report

    # ---------- JD 关键词提取 ----------

    def _job_text(self, job_id: str) -> dict:
        row = self.con.execute(
            """SELECT id, title, description, responsibilities, keywords_json
               FROM job_postings WHERE id=?""",
            (job_id,),
        ).fetchone()
        if not row:
            raise ATSScanError(f"岗位不存在: {job_id}")
        import json as _json

        keywords = [str(k) for k in (_json.loads(row["keywords_json"] or "[]"))]
        reqs = row["responsibilities"] or ""
        return {
            "title": row["title"] or "",
            "body": row["description"] or "",
            "requirements": reqs,
            "keywords": keywords,
        }

    def _extract_jd_keywords(self, job: dict) -> list[KeywordTerm]:
        title_l = job["title"].lower()
        req_l = job["requirements"].lower()
        body_l = job["body"].lower()
        # 同义词组 → 归一化词条（组内任一写法出现都算该技能）
        group_of: dict[str, str] = {}
        for canon, variants in SKILL_SYNONYM_GROUPS.items():
            for v in [canon, *variants]:
                group_of[v.lower()] = canon

        counts: dict[str, KeywordTerm] = {}

        def _register(display: str, category_hint: str | None, where: str) -> None:
            low = display.lower()
            canon = group_of.get(low)
            key = canon if canon else low
            if canon:
                variants = [canon.lower(), *[v.lower() for v in SKILL_SYNONYM_GROUPS[canon]]]
            else:
                variants = [low]
            if key in counts:
                t = counts[key]
            else:
                category = (
                    "soft" if any(v in [w.lower() for w in SOFT_SKILL_WORDS] for v in variants)
                    else "hard" if canon else (category_hint or "other")
                )
                t = counts[key] = KeywordTerm(
                    term=key, display=display, category=category, variants=list(variants),
                )
            t.jd_count += sum(
                len(re.findall(re.escape(v), body_l))
                + len(re.findall(re.escape(v), req_l))
                for v in variants
            )
            t.in_title = t.in_title or any(v in title_l for v in variants)
            t.in_requirements = t.in_requirements or any(v in req_l for v in variants)

        # 1) 结构化 keywords 字段（信源机器可读，权重最高）→ 默认 hard
        for k in job["keywords"]:
            k = k.strip()
            if k and k.lower() not in _STOPWORDS and len(k) >= 2:
                _register(k, "hard", "keywords")
        # 2) 标题分词（职位名即最强的岗位定义信号）
        for w, _flag in pseg.cut(job["title"]):
            w = w.strip()
            if len(w) >= 2 and w.lower() not in _STOPWORDS:
                _register(w, None, "title")
        for tok in _LATIN_RE.findall(job["title"]):
            if tok.lower() not in _STOPWORDS:
                _register(tok, "hard", "title")
        # 3) 正文 + 任职要求：名词性中文词 + 英文技术词
        text = job["body"] + "\n" + job["requirements"]
        for w, flag in pseg.cut(text):
            w = w.strip()
            if len(w) < 2 or w.lower() in _STOPWORDS:
                continue
            if flag.startswith("n") or flag in ("vn", "eng"):
                _register(w, None, "body")
        for tok in _LATIN_RE.findall(text):
            if tok.lower() not in _STOPWORDS and len(tok) >= 2:
                _register(tok, "hard", "body")

        out = [t for t in counts.values() if t.jd_count > 0 or t.in_title]
        for t in out:
            t.weight = (
                (3 if t.in_title else 1)
                * (2 if t.in_requirements else 1)
                * min(t.jd_count, 3)
                * {"hard": 1.0, "soft": 0.8, "other": 0.6}[t.category]
            )
        # 只保留有信号量的词：标题命中、要求命中或出现≥2 次（单次正文提及噪声大）
        out = [t for t in out if t.in_title or t.in_requirements or t.jd_count >= 2]
        out.sort(key=lambda t: -t.weight)
        return out[:60]  # 上限防长尾淹没重点

    # ---------- 简历侧 ----------

    def _resume_sections(self, version_id: str) -> dict:
        row = self.con.execute(
            "SELECT sections_json FROM resume_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not row:
            raise ATSScanError(f"简历版本不存在: {version_id}")
        import json as _json

        return _json.loads(row["sections_json"] or "{}")

    @staticmethod
    def _flatten_sections(sections: dict) -> dict[str, str]:
        """各分节 → 可检索文本（含字段名与值，保序）。"""
        out: dict[str, str] = {}

        def _walk(prefix: str, obj: object) -> None:
            if isinstance(obj, dict):
                for v in obj.values():
                    _walk(prefix, v)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(prefix, v)
            elif obj is not None:
                out[prefix] = out.get(prefix, "") + " " + str(obj)

        b = sections.get("basics") or {}
        out["basics"] = " ".join(
            str(v) for v in (b.get("name"), b.get("label"), b.get("summary"),
                             b.get("email"), b.get("phone")) if v
        )
        for name in ("work", "projects", "education", "skills", "awards", "certifications"):
            if sections.get(name):
                _walk(name, sections[name])
        return out

    @staticmethod
    def _find_hits(term: KeywordTerm, sec_texts: dict[str, str]) -> list[dict]:
        hits = []
        for sec, text in sec_texts.items():
            low = text.lower()
            positions = []
            count = 0
            for v in term.variants:
                for m in re.finditer(re.escape(v), low):
                    count += 1
                    positions.append((m.start(), m.end(), v))
            if count:
                positions.sort()
                start, end, v = positions[0]
                snippet = low[max(0, start - 18):end + 18].replace("\n", " ").strip()
                hits.append({"section": sec, "count": count, "snippet": snippet})
        return hits

    # ---------- 计分 ----------

    @staticmethod
    def _coverage_score(matched: list[dict], missing: list[dict]) -> dict:
        detail = {}
        total_score = 0.0
        for cat, quota in (("hard", 45.0), ("soft", 10.0), ("other", 15.0)):
            pool = [e for e in matched + missing if e["category"] == cat]
            if not pool:
                detail[cat] = {"covered": 0, "total": 0, "score": quota if cat != "hard" else quota,
                               "note": f"JD 无{ {'hard':'硬技能','soft':'软技能','other':'其他重要词'}[cat] }信号"}
                total_score += quota  # 无信号即不惩罚
                continue
            total_w = sum(e["weight"] for e in pool)
            got_w = sum(e["weight"] for e in pool if "hits" in e)
            ratio = got_w / total_w if total_w else 0.0
            s = round(quota * ratio, 1)
            detail[cat] = {
                "covered": sum(1 for e in pool if "hits" in e),
                "total": len(pool), "ratio": round(ratio, 3), "score": s,
            }
            total_score += s
        return {"score": round(total_score, 1), "by_category": detail}

    @staticmethod
    def _format_checks(sections: dict) -> tuple[list[dict], float]:
        checks: list[dict] = []
        score = 0.0
        b = sections.get("basics") or {}
        contact_ok = bool((b.get("email") or "").strip() and (b.get("phone") or "").strip())
        checks.append({
            "item": "联系方式完整（邮箱+电话）", "ok": contact_ok, "score": 10,
            "fix": "在「我的画像」补全手机/邮箱——缺失直接导致 HR 联系不到你",
        })
        score += 10 if contact_ok else 0

        nonempty = [n for n in ("work", "projects", "education", "skills") if sections.get(n)]
        sections_ok = len(nonempty) >= 3
        checks.append({
            "item": f"标准分节（现有 {len(nonempty)}/4：{'、'.join(nonempty) or '无'}）",
            "ok": sections_ok, "score": 10,
            "fix": "ATS 解析依赖标准分节标题；至少保留 经历/项目/教育/技能 中的 3 节",
        })
        score += 10 if sections_ok else 0

        dates: list[str] = []
        for coll in ("work", "projects", "education"):
            for item in sections.get(coll) or []:
                dates += [str(item.get(k) or "") for k in ("startDate", "endDate")]
        bad = [d for d in dates if d and not _DATE_OK_RE.match(d)]
        dates_ok = not bad
        checks.append({
            "item": f"日期格式一致（YYYY-MM；{'异常: ' + bad[0] if bad else '全部合规'}）",
            "ok": dates_ok, "score": 10,
            "fix": "机器解析对 '2024.03'/'2024年3月' 等写法易错位；统一用 2024-03",
        })
        score += 10 if dates_ok else 0
        return checks, score

    # ---------- 仅建议项（不计分） ----------

    def _advisories(
        self, sections: dict, sec_texts: dict[str, str], job: dict, matched: list[dict],
    ) -> list[dict]:
        out: list[dict] = []
        # 量化成果占比
        bullets: list[str] = []
        for coll in ("work", "projects"):
            for item in sections.get(coll) or []:
                bullets += [str(h) for h in (item.get("highlights") or [])]
                for ln in str(item.get("summary") or "").splitlines():
                    bullets.append(ln)
        if bullets:
            quantified = sum(1 for b in bullets if re.search(r"\d", b))
            ratio = round(quantified / len(bullets), 2)
            out.append({
                "kind": "quantification",
                "title": f"量化成果：{quantified}/{len(bullets)} 条 bullet 含数字（{int(ratio*100)}%）",
                "detail": "带数字的成果比形容词更有说服力；只补真实数字，不要编。",
            })
        # 职位名对齐
        label = ((sections.get("basics") or {}).get("label") or "").lower()
        title_l = job["title"].lower()
        if label and title_l:
            overlap = self._title_overlap(label, title_l)
            out.append({
                "kind": "title_alignment",
                "title": f"职位名对齐：简历 label「{label}」 vs JD「{title_l}」词面重合 {overlap} 词",
                "detail": "label 与目标职位一致可提高 HR 关键词检索命中率（如实改写，不撒谎）。",
            })
        # 关键词堆砌黄牌：技能词只出现在 skills 分节
        skills_text = (sec_texts.get("skills") or "").lower()
        evidence_text = " ".join(
            sec_texts.get(k, "") for k in ("work", "projects")
        ).lower()
        stuffed = []
        for e in matched:
            if e["category"] == "hard" and all(
                v in skills_text for v in [e["term"].lower()]
            ) and e["term"].lower() not in evidence_text:
                stuffed.append(e["term"])
        if stuffed:
            out.append({
                "kind": "stuffing_warning",
                "title": f"疑似堆砌（不计分，仅提醒）：{len(stuffed)} 个技能词只出现在技能栏"
                         f"（{ '、'.join(stuffed[:5]) }{'…' if len(stuffed) > 5 else ''}）",
                "detail": "经历/项目中没有任何这些技能的支撑痕迹。HR 与背调最反感无上下文的"
                          "技能罗列——用真实经历佐证，或从简历移除。",
            })
        return out

    @staticmethod
    def _title_overlap(a: str, b: str) -> int:
        wa = {w for w in re.split(r"[\s/·、，,-]+", a) if len(w) >= 2}
        wb = {w for w in re.split(r"[\s/·、，,-]+", b) if len(w) >= 2}
        return len(wa & wb)

    @staticmethod
    def _band(score: int) -> str:
        if score >= TARGET_SCORE:
            return "达标（行业共识目标线 75+）"
        if score >= 50:
            return "接近目标（50-74：补关键缺口后通常可达标）"
        return "需重点修改（<50：关键词覆盖或可解析性存在结构性缺口）"


__all__ = ["ATSScanService", "ATSScanError", "TARGET_SCORE", "SOFT_SKILL_WORDS"]
