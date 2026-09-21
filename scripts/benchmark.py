"""job-hater 20k 岗位规模性能基准（独立可运行，零新增依赖）。

用法（仓库根目录）::

    python scripts/benchmark.py                        # 默认 N=20000 全量基准
    python scripts/benchmark.py --jobs 5000            # 快速档（外推需谨慎，见 docs/PERFORMANCE.md）
    python scripts/benchmark.py --out bench.json       # 结构化结果同时写入文件
    python scripts/benchmark.py --db D:/tmp/bench.db   # 指定数据库落盘位置（默认用临时目录并自动清理）

流程：生成合成岗位数据（真实分布特征，键名与 JobService.normalize 接受的 raw 一致）
→ 分批 ingest 计时 → 典型 FTS 中文检索计时 → match/run 全量排序计时
→ 断言性能预算；任一预算不达标以退出码 1 结束。

预算（可经 CLI 覆盖）::

    ingest 吞吐 >= 60 jobs/s（received 口径，含批内去重与 FTS 索引维护）
    检索最差 P95 < 50 ms（@20k，FTS/结构化过滤，LIMIT 50）

注意：jieba 词典加载与首查询执行计划预热均不计入计时（测稳态而非冷启动）；
match/run 走生产路径 rank_jobs（含逐岗位持久化 match_results），只记录不设预算。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # 允许在未安装包的环境下直接运行本脚本

from jobhater import textproc as tp  # noqa: E402  （须在 sys.path 注入之后导入）
from jobhater.db import apply_all, connect  # noqa: E402
from jobhater.services.jobs import JobService  # noqa: E402
from jobhater.services.matching import ENGINE_VERSION, MatchService  # noqa: E402
from jobhater.services.profile import ProfileService  # noqa: E402

SOURCE_ID = "benchmark_synthetic"
DEFAULT_JOBS = 20_000
DEFAULT_BATCH = 1_000
DEFAULT_RUNS = 10
DUP_EXACT_RATE = 0.02  # 批内精确重复占比（同一 dedupe_key、新 source_job_id → L2 去重路径）
DUP_NEAR_RATE = 0.01   # 近似标题占比（同雇主 +2 字后缀 → L3 near_dup_of 标记入库路径）
MIN_INGEST_JOBS_PER_SEC = 60.0
MAX_SEARCH_P95_MS = 50.0

# ---------- 合成数据词库（分布特征：一线集中、科技岗为主、雇主长尾） ----------

CITIES: list[tuple[str, int]] = [
    ("北京", 20), ("上海", 20), ("深圳", 14), ("杭州", 12), ("广州", 8), ("成都", 6),
    ("南京", 5), ("武汉", 4), ("西安", 4), ("苏州", 3), ("长沙", 2), ("合肥", 2),
]
_CITY_NAMES = [c for c, _ in CITIES]
_NAME_A = [
    "云启", "星河", "沐光", "青梧", "岚桥", "知行", "光年", "深空", "白泽", "南屏",
    "北辰", "拾光", "观澜", "青云", "拓维", "天工", "灵犀", "万象", "九章", "方舟",
    "灯塔", "群星", "简仓", "锐维",
]
_NAME_B = ["科技", "信息技术", "网络科技", "智能科技", "数据科技", "数字科技", "云服务", "软件"]
_TAILS = ["有限公司", "股份有限公司"]

# (职位名, 权重, 薪资下限K, 薪资上限K, 技能池)
ROLES: list[dict] = [
    {"title": "后端开发工程师", "weight": 12, "lo": 18, "hi": 45,
     "skills": ["Python", "Go", "Java", "MySQL", "Redis", "微服务", "分布式", "Kafka"]},
    {"title": "前端开发工程师", "weight": 9, "lo": 15, "hi": 38,
     "skills": ["JavaScript", "TypeScript", "React", "Vue", "Webpack", "性能优化"]},
    {"title": "算法工程师", "weight": 6, "lo": 25, "hi": 60,
     "skills": ["机器学习", "深度学习", "Python", "推荐系统", "大模型", "CUDA"]},
    {"title": "机器学习工程师", "weight": 5, "lo": 22, "hi": 55,
     "skills": ["Python", "PyTorch", "机器学习", "特征工程", "大模型"]},
    {"title": "数据分析师", "weight": 6, "lo": 12, "hi": 30,
     "skills": ["SQL", "Python", "Hive", "数据可视化", "A/B 实验"]},
    {"title": "测试开发工程师", "weight": 5, "lo": 12, "hi": 30,
     "skills": ["Python", "自动化测试", "Selenium", "CI/CD", "JMeter"]},
    {"title": "运维工程师", "weight": 4, "lo": 12, "hi": 32,
     "skills": ["Linux", "Kubernetes", "Docker", "Prometheus", "Shell"]},
    {"title": "数据仓库工程师", "weight": 4, "lo": 15, "hi": 35,
     "skills": ["SQL", "Spark", "Flink", "Hive", "数据建模"]},
    {"title": "嵌入式软件工程师", "weight": 3, "lo": 12, "hi": 30,
     "skills": ["C语言", "C++", "RTOS", "单片机", "通信协议"]},
    {"title": "产品经理", "weight": 6, "lo": 15, "hi": 40,
     "skills": ["需求分析", "产品设计", "数据分析", "用户调研", "PRD"]},
    {"title": "运营专员", "weight": 5, "lo": 8, "hi": 18,
     "skills": ["内容运营", "活动策划", "数据分析", "用户增长"]},
    {"title": "UI设计师", "weight": 3, "lo": 10, "hi": 25,
     "skills": ["Figma", "视觉设计", "交互设计", "设计系统"]},
    {"title": "机械设计工程师", "weight": 2, "lo": 8, "hi": 20,
     "skills": ["SolidWorks", "有限元分析", "公差分析", "CAD制图"]},
    {"title": "人力资源专员", "weight": 2, "lo": 6, "hi": 15,
     "skills": ["招聘", "员工关系", "薪酬绩效"]},
    {"title": "财务专员", "weight": 2, "lo": 6, "hi": 15,
     "skills": ["总账", "税务申报", "Excel", "ERP系统"]},
    {"title": "销售代表", "weight": 2, "lo": 6, "hi": 18,
     "skills": ["客户开发", "商务谈判", "CRM系统"]},
]

# (级别前缀, 权重, 薪资系数)
LEVELS: list[tuple[str, int, float]] = [
    ("", 30, 0.9), ("初级", 15, 0.75), ("中级", 20, 1.0), ("高级", 20, 1.35), ("资深", 10, 1.7),
]
# 标题后缀（同时提供 (公司,标题) 组合多样性；含校招/远程等真实信号词）
TITLE_SUFFIXES: list[tuple[str, int]] = [
    ("", 46), ("（急招）", 16), ("（应届）", 10), ("（校招）", 8),
    ("（远程）", 6), ("（核心业务）", 8), ("（双休）", 6),
]
# L3 近似去重用变体后缀：与原标题 bigram Jaccard 恰好 ≥ 0.7（超过 2 字会稀释到阈值以下）
NEAR_SUFFIXES = ["（急聘）", "（速招）", "（招人）"]

EXP_TEXTS: list[tuple[str, int]] = [
    ("1-3年", 30), ("3-5年", 25), ("1年以下", 12), ("经验不限", 15),
    ("不限", 5), ("5-10年", 8), ("2年以上", 5),
]
DEPARTMENTS = ["技术部", "研发中心", "产品部", "运营部", "数据部", "人力资源部", "财务部", "市场部"]
BENEFITS = [
    "五险一金，带薪年假，年度体检。",
    "扁平管理，技术氛围好，核心成员持期权。",
    "六险一金，免费三餐，年度旅游。",
    "弹性工作制，设备补贴，节日福利。",
]

# 基准画像（后端方向；与 ROLES 技能池有真实交叠，保证匹配维度有区分度）
PROFILE_SKILLS: list[tuple[str, int, float, list[str]]] = [
    ("Python", 4, 4.0, ["py3", "python3"]),
    ("Go", 3, 3.0, ["golang"]),
    ("MySQL", 4, 4.0, []),
    ("Redis", 3, 3.0, []),
    ("Kubernetes", 3, 2.0, ["k8s"]),
    ("微服务", 3, 3.0, []),
]

# 典型检索负载：高频中文词 + ASCII 技能词 + FTS 与城市过滤混合
BENCH_QUERIES: list[tuple[str, list[str] | None]] = [
    ("后端开发", None),
    ("机器学习", None),
    ("产品经理", None),
    ("数据分析", None),
    ("python", None),
    ("算法", ["北京", "上海"]),
]


# ---------- 小工具 ----------

def _pick(rng: random.Random, population: list, weights: list[int | float]):
    return rng.choices(population, weights=weights, k=1)[0]


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _percentile(ordered_ms: list[float], pct: float) -> float:
    """最近秩法（nearest-rank）百分位；输入须已升序。"""
    if not ordered_ms:
        return 0.0
    rank = max(1, math.ceil(pct / 100 * len(ordered_ms)))
    return ordered_ms[min(rank, len(ordered_ms)) - 1]


def _latency_stats(runs_ms: list[float]) -> dict:
    ordered = sorted(runs_ms)
    return {
        "runs": len(ordered),
        "min_ms": round(ordered[0], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "p50_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _db_size_mb(db_path: Path) -> float:
    total = db_path.stat().st_size
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            total += side.stat().st_size
    return round(total / (1024 * 1024), 2)


def _reconfigure_stdio() -> None:
    """Windows 控制台/重定向默认本地编码（如 cp936），统一为 UTF-8 防中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# ---------- 合成数据生成 ----------

def build_employers(count: int, rng: random.Random) -> list[str]:
    """「城市+字号+行业+组织形式」组合出真实感公司名，product 保证唯一。"""
    pool = [
        f"{city}{a}{b}{tail}"
        for city in _CITY_NAMES
        for a in _NAME_A
        for b in _NAME_B
        for tail in _TAILS
    ]
    if count > len(pool):
        raise ValueError(f"雇主池不足：需要 {count}，组合上限 {len(pool)}（请减小 --jobs）")
    rng.shuffle(pool)
    return pool[:count]


def _build_description(
    rng: random.Random, role_title: str, skills: list[str],
    city: str, remote: bool, exp_text: str, edu: str,
) -> str:
    stack = "、".join(rng.sample(skills, k=min(3, len(skills))))
    duty = (
        f"负责{role_title}相关业务的设计与交付，主导{skills[0]}方向的技术方案落地；"
        f"与产品、测试团队协作完成日常迭代，参与代码评审与线上问题排查。"
    )
    place = f"base{city}，团队规模10-50人" + ("，支持远程/混合办公。" if remote else "。")
    req = f"任职要求：{exp_text}相关经验；{edu}及以上学历；熟悉{stack}；具备良好的沟通与协作能力。"
    parts = [duty, place, req]
    if rng.random() < 0.6:
        parts.append(rng.choice(BENEFITS))
    return "\n".join(parts)


def generate_raw_jobs(n: int, seed: int) -> tuple[list[dict], dict]:
    """生成 n 条合成岗位（received 口径恰好 n）。

    分布特征：雇主按 Zipf 长尾（头部公司多岗、长尾公司单岗）；科技岗为主；
    薪资/经验/学历按职位族取值；~2% 批内精确重复（L2）+ ~1% 近似标题（L3）。
    返回 (raw 列表, 数据画像统计)。
    """
    rng = random.Random(seed)
    employers = build_employers(max(100, n // 20), rng)
    emp_weights = [1.0 / (i + 8) for i in range(len(employers))]  # Zipf 长尾
    role_weights = [r["weight"] for r in ROLES]
    level_weights = [x[1] for x in LEVELS]
    suffix_weights = [w for _, w in TITLE_SUFFIXES]

    n_exact = max(1, round(n * DUP_EXACT_RATE))
    n_near = max(1, round(n * DUP_NEAR_RATE))
    n_unique = n - n_exact - n_near
    if n_unique < 10:
        raise ValueError("--jobs 太小，无法构造去重负载（至少约 130）")

    raws: list[dict] = []
    used: set[tuple[str, str]] = set()
    today = dt.date.today()
    while len(raws) < n_unique:
        role = _pick(rng, ROLES, role_weights)
        techish = any(k in role["title"] for k in ("开发", "算法", "数据", "测试", "运维", "嵌入式"))
        edu = _pick(
            rng,
            ["本科", "大专", "硕士", "学历不限"],
            [55, 20, 15, 10] if techish else [30, 35, 5, 30],
        )
        for _ in range(64):  # (公司,标题) 撞键重抽；组合空间按 N 缩放，几乎不会耗尽
            employer = _pick(rng, employers, emp_weights)
            level = _pick(rng, LEVELS, level_weights)
            suffix = _pick(rng, TITLE_SUFFIXES, suffix_weights)
            title = f"{level[0]}{role['title']}{suffix}"
            if (employer, title) not in used:
                break
        else:  # 组合空间饱和的兜底（几乎不会触发）：序号保证唯一
            title = f"{role['title']}{len(used):06d}"
        used.add((employer, title))

        salary_mult = level[2] * rng.uniform(0.85, 1.15)
        base_k = rng.uniform(role["lo"], role["hi"]) * salary_mult
        span = base_k * rng.uniform(0.18, 0.35)
        sal_lo = max(3, round(base_k - span))
        sal_hi = max(sal_lo + 1, round(base_k + span))
        salary_text = (
            f"{sal_lo}-{sal_hi}K·{_pick(rng, [12, 13, 14, 15, 16], [45, 15, 25, 5, 10])}薪"
            if rng.random() > 0.08 else "面议"  # ~8% 缺薪资，检验解析降级路径
        )
        exp_text = _pick(rng, [e for e, _ in EXP_TEXTS], [w for _, w in EXP_TEXTS])
        remote = suffix == "（远程）"
        city = _pick(rng, _CITY_NAMES, [w for _, w in CITIES])
        dept = _pick(rng, DEPARTMENTS, [1] * len(DEPARTMENTS)) if rng.random() < 0.8 else None
        days_ago = rng.randrange(60) if rng.random() < 0.9 else rng.randrange(61, 121)
        published = (today - dt.timedelta(days=days_ago)).isoformat()
        deadline = (today - dt.timedelta(days=days_ago - 30)).isoformat() if rng.random() < 0.5 else None
        source_job_id = f"syn-{len(raws):06d}"
        raws.append({
            "title": title,
            "company": employer,
            "city": city,
            "salary": salary_text,
            "experience_required": exp_text,
            "education_required": edu,
            "department": dept,
            "url": f"https://jobs.example.com/syn/{source_job_id}" if rng.random() < 0.7 else "",
            "hr_name": f"{'王李张刘陈杨'[len(raws) % 6]}老师" if rng.random() < 0.3 else "",
            "source_job_id": source_job_id,
            "keywords": rng.sample(role["skills"], k=min(2, len(role["skills"]))),
            "published_at": published,
            "deadline": deadline,
            "description": _build_description(
                rng, role["title"], role["skills"], city, remote, exp_text, edu
            ),
        })

    serial = len(raws)
    for _ in range(n_exact):  # L2 跨源同岗：同 dedupe_key、新 source_job_id
        src = raws[rng.randrange(len(raws))]
        serial += 1
        dup = dict(src)
        dup["source_job_id"] = f"syn-{serial:06d}"
        raws.insert(rng.randrange(len(raws) + 1), dup)
    for _ in range(n_near):  # L3 近似去重：同雇主 +2 字后缀（bigram Jaccard ≥ 0.7）
        src = raws[rng.randrange(len(raws))]
        serial += 1
        dup = None
        for suffix in NEAR_SUFFIXES:
            cand_title = str(src["title"]) + suffix
            if (src["company"], cand_title) not in used:
                dup = dict(src)
                dup["title"] = cand_title
                dup["source_job_id"] = f"syn-{serial:06d}"
                used.add((src["company"], cand_title))
                break
        if dup is None:  # 变体全部撞键时退化为 L2 精确重复（负载仍然有效）
            dup = dict(src)
            dup["source_job_id"] = f"syn-{serial:06d}"
        raws.insert(rng.randrange(len(raws) + 1), dup)

    profile = {
        "employers": len(employers),
        "roles": len(ROLES),
        "cities": len(CITIES),
        "dup_exact": n_exact,
        "dup_near": n_near,
        "avg_description_chars": round(statistics.fmean(len(str(r["description"])) for r in raws), 1),
        "avg_title_chars": round(statistics.fmean(len(str(r["title"])) for r in raws), 1),
        "salary_missing_pct": round(100 * sum(1 for r in raws if r["salary"] == "面议") / len(raws), 1),
    }
    return raws, profile


# ---------- 各阶段基准 ----------

def run_ingest(con: sqlite3.Connection, raws: list[dict], batch_size: int) -> dict:
    """分批 ingest 计时。走生产路径 JobService.ingest（预装载索引+批量事务）。"""
    tp.tokenize_for_fts("基准预热")  # jieba 词典加载不计入（测稳态吞吐，见模块 docstring）
    svc = JobService(con)
    counters = ("received", "added", "deduped_exact", "deduped_near", "enriched", "rejected")
    totals = dict.fromkeys(counters, 0)
    per_batch_ms: list[float] = []
    t_all = time.perf_counter()
    for chunk in _chunks(raws, batch_size):
        t0 = time.perf_counter()
        stats = svc.ingest(chunk, source_id=SOURCE_ID)
        per_batch_ms.append(round((time.perf_counter() - t0) * 1000, 1))
        for k in counters:
            totals[k] += getattr(stats, k)
    seconds = time.perf_counter() - t_all
    accounted = sum(totals[k] for k in counters if k != "received")
    if totals["received"] != accounted:
        raise RuntimeError(
            f"ingest 统计不闭合：received={totals['received']} 但去向合计={accounted}"
        )
    row_count = int(con.execute("SELECT COUNT(*) AS c FROM job_postings").fetchone()["c"])
    return {
        **totals,
        "batches": len(per_batch_ms),
        "batch_size": batch_size,
        "seconds": round(seconds, 2),
        "jobs_per_sec": round(totals["received"] / seconds, 1),
        "per_batch_ms": per_batch_ms,
        "db_row_count": row_count,
    }


def run_search(svc: JobService, runs: int) -> dict:
    """FTS 中文检索计时：每查询 1 次预热（不计）+ runs 次计时。"""
    tp.tokenize_for_fts("基准预热：后端开发工程师 Python 机器学习 产品经理")  # jieba 词典加载不计入
    queries = []
    for query, cities in BENCH_QUERIES:
        svc.search(query, cities=cities, limit=50)  # 预热（执行计划/页缓存）
        total_matches = svc.count_filtered(query, cities=cities)
        if total_matches <= 0:
            raise RuntimeError(f"基准查询 0 命中（数据生成与查询不匹配）: {query!r}")
        runs_ms: list[float] = []
        results = 0
        for _ in range(runs):
            t0 = time.perf_counter()
            rows = svc.search(query, cities=cities, limit=50)
            runs_ms.append((time.perf_counter() - t0) * 1000)
            results = len(rows)
        entry: dict = {
            "query": query,
            "cities": cities,
            "total_matches": total_matches,
            "results_returned": results,
            "runs_ms": [round(x, 3) for x in runs_ms],
        }
        entry.update(_latency_stats(runs_ms))
        queries.append(entry)
    return {"runs_per_query": runs, "queries": queries}


def build_match_env(con: sqlite3.Connection) -> tuple[dict, object]:
    """基准画像 + 偏好（后端方向，与合成数据有真实交叠）。"""
    ps = ProfileService(con)
    prof = ps.create_profile("基准候选人", headline="后端 / 算法方向 · 3 年经验")
    ps.add_education(prof.id, school="示例理工大学", degree="本科", major="计算机科学与技术")
    ps.add_experience(
        prof.id, employer="示例科技有限公司", title="后端开发工程师",
        tags=["后端", "微服务", "高并发", "分布式"],
    )
    for name, level, years, aliases in PROFILE_SKILLS:
        ps.add_skill(prof.id, name=name, level=level, years=years, aliases=aliases)
    preset = ps.create_preset(
        prof.id, name="基准偏好",
        target_roles=["后端开发工程师", "算法工程师"],
        target_cities=["北京", "上海", "杭州"],
        salary_min_k=20.0,
        max_experience_years_required=3.0,
    )
    return ps.match_view(prof.id), preset


def run_match(con: sqlite3.Connection, n: int) -> dict:
    """match/run 全量排序计时：生产路径 rank_jobs（Gate+BM25+维度评分+逐条持久化）。"""
    view, preset = build_match_env(con)
    jobs = JobService(con).search("", statuses=["active"], limit=n)
    t0 = time.perf_counter()
    outcomes = MatchService(con).rank_jobs(view, preset, jobs)
    seconds = time.perf_counter() - t0
    persisted = int(con.execute("SELECT COUNT(*) AS c FROM match_results").fetchone()["c"])
    return {
        "jobs_evaluated": len(jobs),
        "eligible": sum(1 for o in outcomes if o.eligible),
        "needs_review": sum(1 for o in outcomes if o.needs_review),
        "seconds": round(seconds, 2),
        "jobs_per_sec": round(len(jobs) / seconds, 1) if seconds > 0 else None,
        "ms_per_job": round(seconds * 1000 / len(jobs), 3) if jobs else None,
        "persisted_rows": persisted,
    }


def env_info(n: int, seed: int, batch_size: int, runs: int) -> dict:
    jieba_version = getattr(tp.jieba, "__version__", None) if tp._JIEBA_READY else None
    try:
        from importlib import metadata

        app_version = metadata.version("jobhater")
    except Exception:  # 未安装（直接源码运行）时如实标注
        app_version = None
    return {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": f"{platform.system()} {platform.release()}",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", ""),
        "cpu_count": os.cpu_count(),
        "sqlite_version": sqlite3.sqlite_version,
        "jieba_available": tp._JIEBA_READY,
        "jieba_version": jieba_version,
        "jobhater_version": app_version,
        "matching_engine_version": ENGINE_VERSION,
        "params": {
            "jobs": n, "seed": seed, "batch_size": batch_size,
            "search_runs": runs, "source_id": SOURCE_ID,
        },
    }


# ---------- 汇总与断言 ----------

def _print_summary(result: dict) -> None:
    p = result["phases"]
    ing, sea, mat = p["ingest"], p["search"], p["match"]
    worst = max(q["p95_ms"] for q in sea["queries"])
    budget = result["budget"]
    lines = [
        f"[1/4] 生成合成岗位 {result['meta']['params']['jobs']} 条：{p['generate_seconds']}s"
        f"（雇主 {result['data_profile']['employers']}、职位族 {result['data_profile']['roles']}）",
        f"[2/4] 批量导入：{ing['received']} 条 / {ing['seconds']}s = {ing['jobs_per_sec']} jobs/s"
        f"（added {ing['added']}，L2 去重 {ing['deduped_exact']}，L3 近似 {ing['deduped_near']}，"
        f"补全 {ing['enriched']}，拒绝 {ing['rejected']}）",
        f"[3/4] FTS 检索 {len(sea['queries'])} 查询 x {sea['runs_per_query']} 次："
        f"最差 P95 {worst} ms",
        f"[4/4] 全量匹配排序：{mat['jobs_evaluated']} 岗 / {mat['seconds']}s = "
        f"{mat['jobs_per_sec']} jobs/s（eligible {mat['eligible']}，待复核 {mat['needs_review']}）",
        f"预算断言：{'PASS' if result['passed'] else 'FAIL'}"
        f"（ingest >= {budget['min_ingest_jobs_per_sec']} jobs/s；检索 P95 < "
        f"{budget['max_search_p95_ms']} ms）",
    ]
    print("\n".join(lines), file=sys.stderr)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="job-hater 岗位规模性能基准")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help="合成岗位条数（默认 20000）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help="ingest 批大小（默认 1000）")
    parser.add_argument("--seed", type=int, default=42, help="合成数据随机种子（默认 42）")
    parser.add_argument("--search-runs", type=int, default=DEFAULT_RUNS, help="每查询计时次数（默认 10）")
    parser.add_argument("--min-ingest-rate", type=float, default=MIN_INGEST_JOBS_PER_SEC,
                        help=f"ingest 吞吐预算 jobs/s（默认 {MIN_INGEST_JOBS_PER_SEC:g}）")
    parser.add_argument("--max-search-p95-ms", type=float, default=MAX_SEARCH_P95_MS,
                        help=f"检索 P95 预算 ms（默认 {MAX_SEARCH_P95_MS:g}）")
    parser.add_argument("--out", type=Path, default=None, help="结果 JSON 写入路径（缺省仅 stdout）")
    parser.add_argument("--db", type=Path, default=None,
                        help="基准数据库路径（缺省用临时目录并在结束时清理）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _reconfigure_stdio()
    if args.jobs < 130:
        raise SystemExit("--jobs 至少 130（需容纳去重负载注入）")

    t0 = time.perf_counter()
    raws, data_profile = generate_raw_jobs(args.jobs, args.seed)
    gen_seconds = round(time.perf_counter() - t0, 2)
    print(f"[1/4] 生成合成岗位 {len(raws)} 条：{gen_seconds}s", file=sys.stderr)

    tmp_ctx = tempfile.TemporaryDirectory(prefix="jobhater-bench-")
    with tmp_ctx as tmp:
        db_path = args.db if args.db is not None else Path(tmp) / "bench.db"
        if args.db is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        apply_all(db_path)
        con = connect(db_path)
        try:
            print("[2/4] 批量导入计时……", file=sys.stderr)
            ingest = run_ingest(con, raws, args.batch_size)
            ingest["db_size_mb"] = _db_size_mb(db_path)
            print("[3/4] FTS 检索计时……", file=sys.stderr)
            search = run_search(JobService(con), args.search_runs)
            print("[4/4] 全量匹配排序计时……", file=sys.stderr)
            match = run_match(con, args.jobs)
            match["db_size_mb"] = _db_size_mb(db_path)  # 全部写入完成后的最终体积
        finally:
            con.close()

    worst_p95 = max(q["p95_ms"] for q in search["queries"])
    result = {
        "schema": "jobhater-benchmark/1",
        "meta": env_info(args.jobs, args.seed, args.batch_size, args.search_runs),
        "data_profile": data_profile,
        "phases": {
            "generate_seconds": gen_seconds,
            "ingest": ingest,
            "search": search,
            "match": match,
        },
        "budget": {
            "min_ingest_jobs_per_sec": args.min_ingest_rate,
            "max_search_p95_ms": args.max_search_p95_ms,
        },
        "assertions": [
            {
                "name": "ingest_throughput",
                "pass": ingest["jobs_per_sec"] >= args.min_ingest_rate,
                "actual": ingest["jobs_per_sec"],
                "threshold": f">= {args.min_ingest_rate:g} jobs/s",
                "detail": "批量导入吞吐（received 口径，含批内去重与 FTS 索引维护）",
            },
            {
                "name": "search_p95",
                "pass": worst_p95 < args.max_search_p95_ms,
                "actual": worst_p95,
                "threshold": f"< {args.max_search_p95_ms:g} ms",
                "detail": "全部基准查询中最差的 P95 延迟（FTS/结构化过滤，LIMIT 50）",
            },
        ],
    }
    result["passed"] = all(a["pass"] for a in result["assertions"])

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"结果已写入 {args.out}", file=sys.stderr)
    _print_summary(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
