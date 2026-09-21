"""简历导入：文件/粘贴文本 → 结构化草稿（JSON Resume 形状）。

设计（与 import_json_resume 同一条信任边界）：
- 解析结果只是「草稿」，必须经用户在 UI 里核对/编辑后才落库——落库路径复用
  ResumeService.import_json_resume（同名跳过、幂等、无免检特权），本模块不做任何写库；
- 启发式解析只做确定性提取，识别不到的字段留空 + warnings 提示，绝不臆造；
- 文件格式支持 txt/md（直读）、docx（python-docx 段落+表格）、pdf（pypdf 文本层；
  扫描件无文本层时如实报错，fail closed 不猜测）、json（JSON Resume 直通）；
- AI 精解析是可选增强：走 ai.run_task 的出境披露确认流程，本地模式/失败时
  启发式结果不受影响。
"""
from __future__ import annotations

import io
import json
import re
import sqlite3

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTS = (".pdf", ".docx", ".txt", ".md", ".markdown", ".json")

# 段落识别：键 = 草稿里的段名，值 = 标题行正则（re.I；行内须出现在开头附近）
_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("education", re.compile(r"教育(背景|经历|信息|情况)?|education", re.I)),
    ("work", re.compile(r"(工作|实习|实践|校园)?(经历|经验)|工作与实习|employment", re.I)),
    ("projects", re.compile(r"项目(经历|经验|简介|列表)?|projects?", re.I)),
    ("skills", re.compile(r"(专业|个人)?技能(特长|列表)?|技术栈|skills?", re.I)),
    ("awards", re.compile(r"荣誉(奖项)?|获奖(情况)?|奖项|证书|awards?|certificates?", re.I)),
    ("summary", re.compile(r"自我评价|个人(简介|评价)|关于我|summary|about\s*me", re.I)),
    ("objective", re.compile(r"求职意向|求职目标|期望职位|objective", re.I)),
]

_BULLET_RE = re.compile(r"^[\s]*[-–—•·*▪◦●○▶>]+")
_NUMBERING_RE = re.compile(r"^[（(]?[0-9一二三四五六七八九十]+[)）、.．、]\s*")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_DATE_RANGE_RE = re.compile(
    r"(?P<s_y>\d{4})\s*[./年\-]\s*(?P<s_m>\d{1,2})?\s*月?\s*"
    r"(?:[-—–~至到\s]+|直到)?\s*"
    r"(?:(?P<e_y>\d{4})\s*[./年\-]?\s*(?P<e_m>\d{1,2})?\s*月?|(?P<now>至今|现在|present|current|now))",
    re.I,
)
_SCHOOL_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z（）()·\s]{2,24}?(?:大学|学院|学校|university|institute)", re.I)
_MAJOR_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,24})专业")
_DEGREE_WORDS = [
    ("博士", "博士"), ("phd", "博士"), ("博士", "博士"),
    ("硕士", "硕士"), ("研究生", "硕士"), ("master", "硕士"), ("mba", "硕士"),
    ("本科", "本科"), ("学士", "本科"), ("bachelor", "本科"),
    ("大专", "专科"), ("专科", "专科"),
]
_POSITION_WORDS = (
    "工程师|开发|实习|分析师|设计|产品|运营|算法|测试|运维|安全|数据|架构|经理|主管|总监|"
    "专员|助理|编辑|记者|翻译|行政|人力|财务|会计|审计|律师|顾问|研究|教|医生|护士|"
    "engineer|developer|intern|analyst|designer|manager|scientist|specialist|trainee"
)
_POSITION_RE = re.compile(_POSITION_WORDS, re.I)
_SKILL_VERB_RE = re.compile(r"^(熟悉|熟练掌握|熟练使用|熟练应用|精通|熟练|掌握|了解|具备|会用|能使用?|使用)+")
_SKILL_SPLIT_RE = re.compile(r"[、，,;；/｜|·]+\s*")
_NAME_LABEL_RE = re.compile(r"^\s*姓名\s*[:：]?\s*(?P<name>[\u4e00-\u9fa5A-Za-z·]{2,20})")
_NAME_BARE_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$|^[A-Za-z][A-Za-z .]{1,24}$")
_NAME_EXCLUDE = re.compile(
    r"简历|求职|电话|邮箱|手机|意向|大学|学院|学校|届|先生|女士|小姐|"
    r"工程师|经理|设计|分析|开发|运营|实习|产品|tel|phone|email|resume"
)


class ResumeImportError(ValueError):
    """文件不可读/格式不支持——用户可读的消息直接展示（422）。"""


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """按扩展名抽取纯文本。返回 (text, kind)；kind ∈ pdf/docx/txt/md/json。
    无法抽取时抛 ResumeImportError（消息面向用户，含可行动的替代路径）。"""
    name = (filename or "").lower()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ResumeImportError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限，请精简后重试")
    if name.endswith(".pdf"):
        return _extract_pdf(data), "pdf"
    if name.endswith(".docx"):
        return _extract_docx(data), "docx"
    if name.endswith((".txt", ".md", ".markdown")):
        return _decode_text(data), "md" if name.endswith((".md", ".markdown")) else "txt"
    if name.endswith(".json"):
        try:
            json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ResumeImportError(f"JSON 文件无法解析：{e}") from e
        return data.decode("utf-8-sig"), "json"
    raise ResumeImportError(
        f"不支持的文件类型「{name.rsplit('.', 1)[-1] if '.' in name else name}」。"
        f"支持：{' / '.join(e.lstrip('.').upper() for e in SUPPORTED_EXTS)}"
    )


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - pypdf 是核心依赖，正常不会走到
        raise ResumeImportError("服务器缺少 pypdf 依赖，无法读取 PDF；请改用 DOCX/TXT/MD") from e
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        raise ResumeImportError(f"PDF 读取失败：{e}") from e
    text = "\n".join(pages)
    if len("".join(text.split())) < 40:
        raise ResumeImportError(
            "这个 PDF 里没有可提取的文字（大概率是扫描件或图片型 PDF）。"
            "请改用 DOCX/TXT/MD 文件，或把简历文本直接粘贴到输入框。"
        )
    return text


def _extract_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as e:  # pragma: no cover - python-docx 是核心依赖
        raise ResumeImportError("服务器缺少 python-docx 依赖，无法读取 DOCX；请改用 TXT/MD") from e
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ResumeImportError(f"DOCX 读取失败（文件可能已损坏）：{e}") from e
    lines = [p.text for p in d.paragraphs]
    # 简历常用表格排版（两栏：时间|内容），按行拍平成「时间  内容」
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append("  ".join(c for c in cells if c))
    return "\n".join(lines)


# ---------- 启发式结构化解析 ----------


def _strip_heading(line: str) -> str:
    """去掉标题行的编号/装饰（「三、」「1.」「【】」…），留下段名本体。"""
    s = line.strip().strip("【】[]#*_—―=～~·•：: \t")
    s = _NUMBERING_RE.sub("", s)
    return s.strip().strip("【】[]：: \t")


def _match_section(line: str) -> str | None:
    body = _strip_heading(line)
    if not body or len(body) > 24 or _BULLET_RE.match(line):
        return None
    for name, pat in _SECTION_PATTERNS:
        # 匹配点限制在行首附近：正文中段出现段名（如「参与项目经历梳理」）不算标题
        if pat.match(body) or (pat.search(body) and body.index(pat.search(body).group()) <= 4):
            # 「项目经历」要归 projects 而不是 work——work 的模式允许空前缀，
            # 用顺序兜底：_SECTION_PATTERNS 里 education/projects 排在 work 前，
            # 这里只需防止「项目经历」被 work 的裸「经历」吞掉
            if name == "work" and re.match(r"项目", body):
                continue
            return name
    return None


def _norm_date(y: str, m: str | None) -> str:
    return f"{y}-{int(m):02d}" if m else y


def _find_dates(line: str) -> tuple[str, str, bool]:
    """行内找时间范围 → (start, end, is_current)。找不到返回 ("", "", False)。"""
    m = _DATE_RANGE_RE.search(line)
    if not m:
        return "", "", False
    start = _norm_date(m.group("s_y"), m.group("s_m"))
    if m.group("e_y"):
        end = _norm_date(m.group("e_y"), m.group("e_m"))
        return start, end, False
    if m.group("now"):
        return start, "", True
    return start, "", False


def _clean_token(t: str) -> str:
    return t.strip().strip("·•-–—，,、;；").strip()


def _split_header_parts(rest: str) -> list[str]:
    # 全角空格（U+3000）是中文简历最常用的字段分隔符，单个即分隔；
    # 半角空格要 2 个以上才算分隔（避免切开「后端 开发」这类词内空格）
    parts = re.split(r"\u3000|[｜|@/／]|\s{2,}", rest)
    return [_clean_token(p) for p in parts if _clean_token(p)]


def parse_resume_text(text: str) -> dict:
    """纯文本简历 → JSON Resume 形状草稿 + warnings。

    只提取确定性内容：段没找到→warning；公司/职位分不开→warning；
    所有结果都交给用户核对（导入不产生免检特权）。
    """
    warnings: list[str] = []
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for ln in lines:
        if not ln.strip():
            continue
        sec = _match_section(ln)
        if sec:
            current = sec
            sections.setdefault(sec, [])
            continue
        if current:
            sections[current].append(ln)
    header = [ln for ln in lines if ln.strip()][:6]

    # ---- basics ----
    name = ""
    for ln in header:
        m = _NAME_LABEL_RE.match(ln)
        if m and not name:
            name = m.group("name").strip()
    if not name:
        for ln in header:
            body = _clean_token(_strip_heading(ln))
            if _NAME_BARE_RE.match(body) and not _NAME_EXCLUDE.search(body.lower()) and not _EMAIL_RE.search(ln):
                name = body
                break
    if not name:
        warnings.append("没能识别出姓名——请在下一步手动填写")

    email_m = _EMAIL_RE.search(text)
    phone_m = _PHONE_RE.search(text)
    email = email_m.group() if email_m else ""
    phone = phone_m.group() if phone_m else ""

    label = ""
    obj_lines = sections.get("objective") or []
    if obj_lines:
        first = _clean_token(_strip_heading(obj_lines[0]))
        first = re.sub(r"^求职意向\s*[:：]?\s*", "", first)
        if first:
            label = first[:120]
    else:
        for ln in header:
            m = re.search(r"求职意向\s*[:：]\s*(.{2,60})", ln)
            if m:
                label = _clean_token(m.group(1))[:120]
                break

    summary = ""
    if sections.get("summary"):
        summary = _clean_token(" ".join(x.strip() for x in sections["summary"] if x.strip()))[:400]

    # ---- education ----
    education: list[dict] = []
    edu_lines = sections.get("education") or []
    for ln in edu_lines:
        body = ln.strip()
        if not body:
            continue
        school_m = _SCHOOL_RE.search(body)
        if not school_m:
            # 常见续行（GPA/排名单独一行）：只在已有条目时跳过，首行就放弃并警告
            if education and re.search(r"GPA|排名|绩点|成绩", body, re.I):
                continue
            continue
        school = school_m.group().strip()
        degree = ""
        for word, canonical in _DEGREE_WORDS:
            if word in body.lower():
                degree = canonical
                break
        major = ""
        major_m = _MAJOR_RE.search(body)
        if major_m:
            major = major_m.group(1)
        else:
            m2 = re.search(r"专业\s*[:：]\s*([\u4e00-\u9fa5A-Za-z]{2,24})", body)
            if m2:
                major = m2.group(1)
        if not major:
            # 兜底：去掉学校/日期/学位词后，剩下最长的中文串多为专业名
            # （如「计算机科学与技术」不带「专业」后缀的写法）
            remainder = body.replace(school, " ")
            remainder = _DATE_RANGE_RE.sub(" ", remainder)
            for word, _ in _DEGREE_WORDS:
                remainder = remainder.replace(word, " ")
            remainder = re.sub(r"GPA|绩点|排名|成绩|分数|[（(]([^)）]*)[)）]", " ", remainder, flags=re.I)
            for part in _split_header_parts(remainder):
                if (
                    2 <= len(part) <= 20
                    and re.search(r"[\u4e00-\u9fa5]", part)
                    and not re.search(r"大学|学院|学校|至今|\d", part)
                ):
                    major = part
                    break
        start, end, _cur = _find_dates(body)
        education.append({
            "institution": school, "studyType": degree, "area": major,
            "startDate": start, "endDate": end,
        })
    if not education:
        warnings.append("没识别到教育背景——请在下一步核对补充（学历影响岗位门槛判断）")

    # ---- work ----
    work: list[dict] = []
    work_lines = sections.get("work") or []
    entries: list[dict] = []
    cur: dict | None = None
    for ln in work_lines:
        body = ln.strip()
        if not body:
            continue
        has_dates = _DATE_RANGE_RE.search(body)
        is_bullet = bool(_BULLET_RE.match(body))
        if has_dates and not is_bullet:
            cur = {"line": body, "desc": []}
            entries.append(cur)
        elif cur is not None:
            cur["desc"].append(_BULLET_RE.sub("", body).strip())
        else:
            # 段首就出现的无日期行：也当一条经历的头部（时间缺失警告）
            cur = {"line": body, "desc": []}
            entries.append(cur)
    for idx, ent in enumerate(entries, 1):
        start, end, is_cur = _find_dates(ent["line"])
        rest = _DATE_RANGE_RE.sub(" ", ent["line"])
        parts = _split_header_parts(rest)
        employer, position = "", ""
        pos_hits = [p for p in parts if _POSITION_RE.search(p)]
        if pos_hits:
            position = pos_hits[0]
            others = [p for p in parts if p != position]
            employer = others[0] if others else ""
        elif len(parts) >= 2:
            employer, position = max(parts, key=len), min(parts, key=len)
        elif len(parts) == 1:
            employer, position = parts[0], ""
        if employer and not position:
            warnings.append(f"第 {idx} 段经历没分清公司/职位——请在下一步核对")
        if not employer:
            warnings.append(f"第 {idx} 段经历没识别到公司名——请在下一步核对")
        desc = "\n".join(d for d in ent["desc"] if d)
        work.append({
            "name": employer, "position": position,
            "startDate": start, "endDate": end if not is_cur else "",
            "summary": desc,
            **({"_current": True} if is_cur else {}),
        })
    if not work:
        warnings.append("没识别到工作/实习经历——应届生没有实习属正常，可跳过")

    # ---- projects ----
    projects: list[dict] = []
    proj_lines = sections.get("projects") or []
    p_entries: list[dict] = []
    p_cur: dict | None = None
    for ln in proj_lines:
        body = ln.strip()
        if not body:
            continue
        if not _BULLET_RE.match(body):
            p_cur = {"line": body, "desc": []}
            p_entries.append(p_cur)
        elif p_cur is not None:
            p_cur["desc"].append(_BULLET_RE.sub("", body).strip())
    for ent in p_entries:
        head = _clean_token(ent["line"])
        head = _clean_token(_DATE_RANGE_RE.sub(" ", head))  # 去掉首尾的时间范围
        role = ""
        role_m = re.search(r"[（(]\s*([^)）]{1,16})\s*[)）]\s*$", head)
        if role_m:
            role = role_m.group(1)
            head = _clean_token(head[: role_m.start()])
        projects.append({
            "name": head[:80], "role": role,
            "description": "\n".join(d for d in ent["desc"] if d),
        })

    # ---- skills ----
    skills: list[dict] = []
    seen: set[str] = set()
    for ln in sections.get("skills") or []:
        body = _BULLET_RE.sub("", ln.strip())
        body = _SKILL_VERB_RE.sub("", body)
        body = re.sub(r"等[。.,，]?$", "", body.strip())
        # 「编程语言：Java、Python」→ 标签前缀拆掉，值进入分词
        body = re.sub(r"^[\u4e00-\u9fa5A-Za-z]{2,8}\s*[:：]\s*", "", body)
        for token in _SKILL_SPLIT_RE.split(body):
            t = _clean_token(_SKILL_VERB_RE.sub("", token))  # 逐词再剥「了解 Python」的动词
            t = re.split(r"\s*的(?=[\u4e00-\u9fa5])", t)[0].strip()   # 「Redis 的使用与调优」→ Redis
            if not (1 <= len(t) <= 30) or t.lower() in seen:
                continue
            seen.add(t.lower())
            skills.append({"name": t})
        if len(skills) >= 40:
            break
    if not skills:
        warnings.append("没识别到技能段——技能是匹配打分的核心输入，请补上")

    # ---- awards ----
    awards = [
        {"title": _clean_token(_BULLET_RE.sub("", ln))}
        for ln in sections.get("awards") or []
        if _clean_token(_BULLET_RE.sub("", ln))
    ][:20]

    draft = {
        "basics": {
            "name": name, "label": label, "summary": summary,
            "email": email, "phone": phone,
        },
        "work": work,
        "education": education,
        "projects": projects,
        "skills": skills,
        "awards": awards,
    }
    return {"draft": draft, "warnings": warnings}


class ResumeImportService:
    """路由层薄封装：上传/粘贴 → 草稿（不落库）；AI 精解析（可选增强）。"""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def parse_file(self, filename: str, data: bytes) -> dict:
        text, kind = extract_text(filename, data)
        if kind == "json":
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ResumeImportError("JSON 顶层必须是对象（JSON Resume 结构）")
            resume = parsed.get("resume") if isinstance(parsed.get("resume"), dict) else parsed
            draft = _validated_json_resume(resume)
            return {
                "draft": draft,
                "warnings": ["识别为 JSON Resume 标准文件，字段已直通——仍请快速核对一遍"],
                "source_kind": kind,
                "text_preview": text[:2000],
                "ai_available": self._ai_available(),
            }
        result = parse_resume_text(text)
        result["source_kind"] = kind
        result["text_preview"] = text[:2000]
        result["ai_available"] = self._ai_available()
        return result

    def parse_paste(self, text: str) -> dict:
        result = parse_resume_text(text)
        result["source_kind"] = "paste"
        result["text_preview"] = text[:2000]
        result["ai_available"] = self._ai_available()
        return result

    def ai_parse(self, text: str, *, ack_egress: bool) -> dict:
        """AI 精解析：本地模式返回 executed=False（不是错误）；输出同样只是草稿。"""
        from jobhater.services.ai import AIService

        svc = AIService(self.con)
        result = svc.run_task(
            "resume_parse",
            system=(
                "你是简历结构化解析器。把用户给的简历文本解析为 JSON，字段："
                'basics{name,email,phone,label(一句话头衔/求职意向),summary}；'
                "work[{name=公司, position=职位, startDate=YYYY-MM, endDate=YYYY-MM(在任则留空),"
                " summary=职责描述合并为一段}]；"
                "education[{institution, studyType(本科/硕士/博士/专科), area=专业, startDate, endDate}]；"
                "projects[{name, role, description}]；skills[{name, keywords=同义词}]；"
                "awards[{title}]。只提取简历中真实存在的内容，识别不了的留空/空数组，"
                "绝对不要编造。日期统一 YYYY-MM。"
            ),
            user=text[:20000],
            ack_egress=ack_egress,
            json_schema={
                "type": "object",
                "properties": {
                    "basics": {"type": "object"},
                    "work": {"type": "array", "items": {"type": "object"}},
                    "education": {"type": "array", "items": {"type": "object"}},
                    "projects": {"type": "array", "items": {"type": "object"}},
                    "skills": {"type": "array", "items": {"type": "object"}},
                    "awards": {"type": "array", "items": {"type": "object"}},
                },
            },
        )
        if not result.get("executed"):
            return result
        refined = result.get("result")
        if not isinstance(refined, dict):
            raise ResumeImportError("AI 返回的结果不是对象——请重试或使用启发式结果")
        return {"executed": True, "draft": _validated_json_resume(refined)}

    def _ai_available(self) -> bool:
        from jobhater.services.ai import AIService, NoneProvider

        return not isinstance(AIService(self.con).active_provider(), NoneProvider)


def _validated_json_resume(data: dict) -> dict:
    """宽松校验 JSON Resume 形状：六大段缺的补空，字段非法的丢弃（不臆造）。"""
    out = {
        "basics": {
            k: str((data.get("basics") or {}).get(k) or "")[:400]
            for k in ("name", "label", "summary", "email", "phone")
        },
        "work": [], "education": [], "projects": [], "skills": [], "awards": [],
    }
    for w in (data.get("work") or [])[:30]:
        if not isinstance(w, dict):
            continue
        out["work"].append({
            "name": str(w.get("name") or "")[:80], "position": str(w.get("position") or "")[:80],
            "startDate": str(w.get("startDate") or "")[:10], "endDate": str(w.get("endDate") or "")[:10],
            "summary": str(w.get("summary") or "")[:2000],
        })
    for e in (data.get("education") or [])[:10]:
        if not isinstance(e, dict):
            continue
        out["education"].append({
            "institution": str(e.get("institution") or e.get("school") or "")[:80],
            "studyType": str(e.get("studyType") or e.get("degree") or "")[:20],
            "area": str(e.get("area") or e.get("major") or "")[:60],
            "startDate": str(e.get("startDate") or "")[:10], "endDate": str(e.get("endDate") or "")[:10],
        })
    for p in (data.get("projects") or [])[:30]:
        if not isinstance(p, dict):
            continue
        out["projects"].append({
            "name": str(p.get("name") or "")[:80], "role": str(p.get("role") or "")[:40],
            "description": str(p.get("description") or "")[:2000],
        })
    for s in (data.get("skills") or [])[:60]:
        if isinstance(s, dict) and str(s.get("name") or "").strip():
            kws = [str(k).strip() for k in (s.get("keywords") or []) if str(k).strip()][:8]
            out["skills"].append({"name": str(s["name"])[:40], "keywords": kws})
        elif isinstance(s, str) and s.strip():
            out["skills"].append({"name": s.strip()[:40]})
    for a in (data.get("awards") or [])[:20]:
        if not isinstance(a, dict):
            continue
        out["awards"].append({"title": str(a.get("title") or "")[:120]})
    return out
