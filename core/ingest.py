#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest.py — 岗位导入、结构化清洗、去重、校招强制过滤

数据链路第一环：各采集适配器（或手动导入）产出的原始岗位条目 →
normalize → dedupe → campus_filter → 入库（data/jobs/jobs.json）。

校招强制过滤（2027届定向，原则见 README）：
- 排除要求 ≥1 年工作经验的岗位
- 排除猎头岗、外包岗
- 排除已过期岗位（发布超过 N 天或截止日期已过）
- 校招模式开启时，保留校招关键词岗位并加 campus 标记
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from core import store

HEADHUNTER_WORDS = ["猎头", "rpo", "招聘顾问", "人力资源服务"]
OUTSOURCING_WORDS = ["外包", "驻场", "劳务派遣", "人力服务"]
CAMPUS_WORDS = ["校招", "校园招聘", "应届", "应届生", "2027届", "2026届", "管培生", "trainee", "campus", "在校生", "毕业生"]
EXP_PATTERNS = [
    (re.compile(r"(\d+)\s*[-~–—至]\s*(\d+)\s*年"), "range"),
    (re.compile(r"(\d+)\s*年[以上包括?]+"), "gte"),
    (re.compile(r"(\d+)\s*年及以上"), "gte"),
    (re.compile(r"(\d+)\s*\+\s*年"), "gte"),
    # 兜底：1-2位数字+年（环视排除 2026年 这类年份误匹配）
    (re.compile(r"(?<!\d)(\d{1,2})\s*年"), "gte"),
]


def _norm_text(s: str) -> str:
    return re.sub(r"[\s（）()\[\]【】·、，,。.\-_/|]+", "", (s or "").lower())


def normalize(raw: dict, source_platform: str = "manual") -> dict:
    """清洗一条原始岗位记录为统一契约字段。缺字段如实留空，绝不臆造。"""
    title = re.sub(r"\s+", " ", str(raw.get("title", ""))).strip()
    desc = re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", "\n", str(raw.get("description") or raw.get("jd_text") or "")).strip()
    job = {
        "id": raw.get("id") or hashlib.sha1(
            (_norm_text(str(raw.get("company", ""))) + "|" + _norm_text(title) + "|" +
             _norm_text(str(raw.get("url", "")))).encode()).hexdigest()[:16],
        "source_platform": raw.get("source_platform") or source_platform,
        "title": title,
        "company": str(raw.get("company", "")).strip(),
        "company_type": raw.get("company_type"),
        "department": raw.get("department"),
        "city": raw.get("city") or raw.get("location"),
        "salary": raw.get("salary") or raw.get("salary_text"),
        "salary_min_k": raw.get("salary_min_k"),
        "salary_max_k": raw.get("salary_max_k"),
        "experience_required": raw.get("experience_required"),
        "education_required": raw.get("education_required"),
        "description": desc,
        "keywords": raw.get("keywords") or [],
        "url": raw.get("url") or "",
        "published_at": raw.get("published_at"),
        "deadline": raw.get("deadline"),
        "fetched_at": raw.get("fetched_at"),
        "extras": raw.get("extras") or {},
    }
    return job


def detect_flags(job: dict, cfg: dict) -> dict:
    """从字段与JD文本中检测：猎头/外包/经验年限/校招信号/过期。只标注，不删除原文。"""
    text = " ".join(str(job.get(k, "")) for k in ("title", "company", "description")).lower()
    flags: dict[str, Any] = {}

    flags["headhunter"] = any(w in text for w in HEADHUNTER_WORDS)
    flags["outsourcing"] = any(w in text for w in OUTSOURCING_WORDS)

    exp = job.get("experience_required")
    years_gte = None
    if isinstance(exp, (int, float)):
        years_gte = float(exp)
    elif isinstance(exp, str) and exp.strip():
        s = exp.strip()
        if any(w in s for w in ["不限", "无经验", "应届", "在校", "经验不限"]):
            years_gte = 0.0
        else:
            for pat, kind in EXP_PATTERNS:
                m = pat.search(s)
                if m:
                    years_gte = float(min(m.groups())) if kind == "range" else float(m.group(1))
                    break
    else:
        # 从JD文本兜底检测
        if re.search(r"经验不限|无需经验|不要求经验|应届生可投|在校生可投", text):
            years_gte = 0.0
        else:
            for pat, kind in EXP_PATTERNS:
                m = pat.search(text)
                if m:
                    years_gte = float(min(m.groups())) if kind == "range" else float(m.group(1))
                    break
    if years_gte is not None and years_gte > 60:
        years_gte = None  # 荒谬值（解析事故）不参与判断
    flags["experience_years_gte"] = years_gte
    flags["experience_unlimited"] = years_gte == 0.0

    flags["campus_signal"] = sorted({w for w in CAMPUS_WORDS if w.lower() in text})

    fcfg = cfg.get("filter", {})
    max_age = int(fcfg.get("max_published_age_days", 30))
    now = dt.date.today()
    expired = False
    deadline = job.get("deadline")
    if deadline:
        try:
            expired = dt.date.fromisoformat(str(deadline)[:10]) < now
        except ValueError:
            pass
    published = job.get("published_at") or job.get("fetched_at")
    if not expired and published:
        try:
            expired = (now - dt.date.fromisoformat(str(published)[:10])).days > max_age
        except ValueError:
            pass
    flags["expired"] = expired
    return flags


def campus_filter(job: dict, flags: dict, cfg: dict) -> tuple[bool, str]:
    """返回 (是否保留, 拒绝原因)。校招强制过滤规则在此集中实现。"""
    fcfg = cfg.get("filter", {})
    if fcfg.get("exclude_headhunter", True) and flags["headhunter"]:
        return False, "猎头岗"
    if fcfg.get("exclude_outsourcing", True) and flags["outsourcing"]:
        return False, "外包岗"
    gte_cfg = float(fcfg.get("exclude_experience_gte_years", 1))
    if flags["experience_years_gte"] is not None and flags["experience_years_gte"] >= gte_cfg:
        return False, f"要求≥{flags['experience_years_gte']:g}年经验"
    if flags["expired"]:
        return False, "已过期"
    if not job.get("title") or not job.get("company"):
        return False, "缺少标题或公司名（清洗失败）"
    return True, ""


def dedupe_key(job: dict) -> str:
    """同公司+同岗位 视为同一条；URL 不同不豁免（各平台重复发布常见）。"""
    return _norm_text(job.get("company", "")) + "|" + _norm_text(job.get("title", ""))


# ---- 近似去重（多源聚合后，同公司岗位常以"改头换面"的标题重复出现）----

NEAR_DUP_TITLE_THRESHOLD = 0.7   # 同公司下标题字符二元组 Jaccard ≥ 此值视为同一条（保守值）
NEAR_DUP_DESC_MIN_CHARS = 30     # 描述短于该长度不做内容指纹（空/模板句无区分度）


def _bigrams(s: str) -> set[str]:
    s = _norm_text(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def title_similarity(a: str, b: str) -> float:
    """标题相似度（字符二元组 Jaccard，0~1）。纯函数，供 ingest 与测试共用。"""
    return _jaccard(_bigrams(a), _bigrams(b))


def _desc_fingerprint(job: dict) -> str | None:
    """内容指纹 = 公司 + 描述前缀。公司必须参与：
    不同公司贴同一段 JD（模板化/中介转发）是不同投递入口，不是同一条岗位。"""
    desc = _norm_text(job.get("description", ""))
    if len(desc) < NEAR_DUP_DESC_MIN_CHARS:
        return None
    basis = _norm_text(job.get("company", "")) + "|" + desc[:200]
    return hashlib.sha1(basis.encode()).hexdigest()


def _is_near_dup(job: dict, by_company: dict, desc_fps: set) -> str | None:
    """返回命中原因（"标题近似" / "内容指纹相同"）或 None。"""
    company_key = _norm_text(job.get("company", ""))
    fp = _desc_fingerprint(job)
    if fp and fp in desc_fps:
        return "内容指纹相同"
    cand = by_company.get(company_key)
    if cand:
        grams = _bigrams(job.get("title", ""))
        for other_grams in cand:
            if _jaccard(grams, other_grams) >= NEAR_DUP_TITLE_THRESHOLD:
                return "标题近似"
    return None


def ingest_jobs(raw_jobs: list[dict], source_platform: str = "manual") -> dict:
    """批量入库：清洗 → 精确/近似去重 → 校招过滤 → 合并写入。返回统计（含每条拒绝原因，透明可查）。"""
    store.ensure_defaults()
    cfg = store.load("config")
    db = store.load("jobs")
    existing = {dedupe_key(j): j for j in db.get("jobs", [])}
    # 近似去重索引：公司 → 已有标题的 bigram 集合列表；全局描述指纹集合
    by_company: dict[str, list[set]] = {}
    desc_fps: set = set()
    for j in db.get("jobs", []):
        by_company.setdefault(_norm_text(j.get("company", "")), []).append(_bigrams(j.get("title", "")))
        fp = _desc_fingerprint(j)
        if fp:
            desc_fps.add(fp)

    stats = {"received": 0, "added": 0, "deduped": 0, "enriched": 0, "near_duped": 0, "rejected": 0, "kept": 0, "details": []}
    for raw in raw_jobs:
        stats["received"] += 1
        job = normalize(raw, source_platform)
        key = dedupe_key(job)
        if key in existing:
            old = existing[key]
            # 补全式去重：同一岗位再次到达时，若老记录缺 JD 而新记录有 → 原地补全（不产生重复）
            if not (old.get("description") or "").strip() and (job.get("description") or "").strip():
                old["description"] = job["description"]
                old.setdefault("extras", {})["jd_enriched_at"] = dt.date.today().isoformat()
                stats["enriched"] += 1
                stats["details"].append({"id": old["id"], "title": old["title"], "company": old["company"], "result": "去重并补全JD"})
            else:
                stats["deduped"] += 1
                stats["details"].append({"id": job["id"], "title": job["title"], "company": job["company"], "result": "去重跳过"})
            continue
        near_reason = _is_near_dup(job, by_company, desc_fps)
        if near_reason:
            stats["near_duped"] += 1
            stats["details"].append({"id": job["id"], "title": job["title"], "company": job["company"], "result": f"近似去重跳过（{near_reason}）"})
            continue
        flags = detect_flags(job, cfg)
        keep, reason = campus_filter(job, flags, cfg)
        job["flags"] = flags
        job["status"] = "new"
        if not keep:
            job["status"] = "rejected"
            job["reject_reason"] = reason
            db["jobs"].append(job)  # 拒绝的也留档，便于审计"为什么这条没进榜单"
            existing[key] = job
            by_company.setdefault(_norm_text(job.get("company", "")), []).append(_bigrams(job.get("title", "")))
            fp = _desc_fingerprint(job)
            if fp:
                desc_fps.add(fp)
            stats["rejected"] += 1
            stats["details"].append({"id": job["id"], "title": job["title"], "company": job["company"], "result": f"拒绝：{reason}"})
        else:
            db["jobs"].append(job)
            existing[key] = job
            by_company.setdefault(_norm_text(job.get("company", "")), []).append(_bigrams(job.get("title", "")))
            fp = _desc_fingerprint(job)
            if fp:
                desc_fps.add(fp)
            stats["added"] += 1
            stats["kept"] += 1
            stats["details"].append({"id": job["id"], "title": job["title"], "company": job["company"], "result": "入库"})
    store.save("jobs", db)
    return stats


def list_active_jobs() -> list[dict]:
    """当前可用（未被过滤拒绝）的岗位。"""
    db = store.load("jobs")
    return [j for j in db.get("jobs", []) if j.get("status") != "rejected"]


def load_json_file(path: str) -> list[dict]:
    """从 JSON 文件读取岗位数组（手动导入的降级路径数据格式）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return data["jobs"]
    raise ValueError("JSON 格式应为岗位数组，或含 jobs 键的对象")
