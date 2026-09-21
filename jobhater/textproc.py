"""文本处理工具：规范化、分词、薪资/经验解析、相似度。

移植自 v1 原型的可复用算法（ingest.py / scorer.py），剥离全部用户假设：
本模块只做与具体求职者无关的语言学与格式处理。
"""
from __future__ import annotations

import hashlib
import re

try:  # 可选依赖：中文词级分词提升 FTS/BM25 质量；缺失时退回字符 bigram
    import jieba

    _JIEBA_READY = True
except ImportError:
    _JIEBA_READY = False

# ---------- 规范化 ----------

_STRIP_RE = re.compile(r"[\s（）()\[\]【】·、，,。.\-_/|：:；;！!？?]+")


def norm_key(s: str | None) -> str:
    """比较用规范化：去空白/标点、小写。用于去重键与公司名归一。"""
    return _STRIP_RE.sub("", (s or "").lower())


def clean_whitespace(text: str | None) -> str:
    """压缩空白：行内多空格→单空格，行尾空白去除，统一换行。"""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\f\v]+", " ", t)
    return re.sub(r" +\n", "\n", t).strip()


# ---------- 中文分词（FTS 索引与查询共用同一路径） ----------


def tokenize_for_fts(text: str | None) -> str:
    """产出空格分隔 token 串。中文：jieba 搜索模式（可用时）或字符 bigram 兜底；
    ASCII：保持原词。两条路径都保证「查询词是原文子串」即可命中。"""
    t = (text or "").lower()
    if not t:
        return ""
    if _JIEBA_READY:
        return " ".join(tok.strip() for tok in jieba.cut_for_search(t) if tok.strip())
    tokens: list[str] = []
    buf = ""
    for ch in t:
        if "\u4e00" <= ch <= "\u9fff":  # CJK 基本区
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
    if buf:
        tokens.append(buf)
    # bigram 相邻合成（单字 token 仍保留，保证单字查询可命中）
    bigrams = [a + b for a, b in zip(tokens, tokens[1:], strict=False)]
    return " ".join(tokens + bigrams)


def fts_query(terms: list[str]) -> str:
    """用户查询词 → FTS5 MATCH 表达式。

    词间 AND、词内子片段 OR：同一查询词经分词产生的多个 token（如「机械设计」
    → 机械设计/机械/设计，随分词器而异）之间是同义展开而非并列条件——
    文档命中任一子片段即算命中该词，由 BM25 决定强弱。
    """
    groups: list[str] = []
    for term in terms:
        toks = [t for t in tokenize_for_fts(term).split() if t]
        if not toks:
            continue
        quoted = [f'"{t}"' for t in dict.fromkeys(toks)]  # 去重保序
        groups.append(f"({' OR '.join(quoted)})")
    return " AND ".join(groups)


# ---------- 信号词命中（v1 scorer 的词边界算法，防 ASCII 短词误报） ----------


def hit_words(text: str, words: list[str]) -> list[str]:
    """返回命中的词。ASCII 短词（≤3字符）整词匹配，其余子串匹配。"""
    hits: list[str] = []
    lowered = (text or "").lower()
    for w in words:
        lw = w.lower()
        if not lw:
            continue
        if lw.isascii() and len(lw) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(lw)}(?![a-z0-9])", lowered):
                hits.append(w)
        elif lw in lowered:
            hits.append(w)
    return hits


# ---------- 薪资解析 ----------

_SALARY_K_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[kK千]?\s*[-~–—至]\s*(\d+(?:\.\d+)?)\s*[kK千]"
)
_SALARY_WAN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*万?\s*[-~–—至]\s*(\d+(?:\.\d+)?)\s*万"
)
_SALARY_DAYS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~–—至]?\s*(\d+(?:\.\d+)?)?\s*元?/\s*(天|日)")
_SALARY_MONTHS_RE = re.compile(r"(\d{1,2})\s*薪")


def parse_salary(
    salary_text: str | None,
) -> tuple[float | None, float | None, int | None]:
    """从薪资文本解析 (min_k, max_k, months)。千元/月为单位；日薪按 21.75 折算；
    解析失败返回 (None, None, None)——绝不猜测。"""
    raw = salary_text or ""
    if not raw.strip():
        return None, None, None
    months = None
    m = _SALARY_MONTHS_RE.search(raw)
    if m:
        months = int(m.group(1))
    if m_k := _SALARY_K_RE.search(raw):
        return float(m_k.group(1)), float(m_k.group(2)), months
    if m_w := _SALARY_WAN_RE.search(raw):
        return float(m_w.group(1)) * 10, float(m_w.group(2)) * 10, months
    if m_d := _SALARY_DAYS_RE.search(raw):
        lo = float(m_d.group(1))
        hi = float(m_d.group(2)) if m_d.group(2) else lo
        k_per_month = lo * 21.75 / 1000, hi * 21.75 / 1000
        return round(k_per_month[0], 1), round(k_per_month[1], 1), months
    return None, None, months


# ---------- 经验年限解析（v1 ingest 的正则族，含年份误匹配防护） ----------

EXP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d+)\s*[-~–—至]\s*(\d+)\s*年"), "range"),
    (re.compile(r"(\d+)\s*年[以上包括?]+"), "gte"),
    (re.compile(r"(\d+)\s*年及以上"), "gte"),
    (re.compile(r"(\d+)\s*\+\s*年"), "gte"),
    # 兜底：1-2位数字+年（负向环视排除 2026年 这类年份）
    (re.compile(r"(?<!\d)(\d{1,2})\s*年"), "gte"),
]

NO_EXP_WORDS = ["不限", "无经验", "应届", "在校", "经验不限", "无需经验", "不要求经验", "毕业生"]


def parse_experience_years(text: str | None) -> float | None:
    """解析「要求的最低经验年限」。0.0=明确无经验要求；None=未检出。
    荒谬值（>60 年）视为解析事故，返回 None。"""
    s = (text or "").strip()
    if not s:
        return None
    if any(w in s for w in NO_EXP_WORDS):
        return 0.0
    for pat, kind in EXP_PATTERNS:
        m = pat.search(s)
        if m:
            years = min(float(m.group(1)), float(m.group(2))) if kind == "range" else float(m.group(1))
            return years if years <= 60 else None
    return None


# ---------- 相似度（去重用） ----------


def _bigrams(s: str) -> set[str]:
    t = norm_key(s)
    return {t[i : i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else ({t} if t else set())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def title_similarity(a: str, b: str) -> float:
    """标题相似度（字符二元组 Jaccard，0~1）。"""
    return jaccard(_bigrams(a), _bigrams(b))


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
