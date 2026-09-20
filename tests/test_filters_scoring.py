#!/usr/bin/env python3
"""test_filters_scoring.py — 校招过滤层与五维评分层单元测试

运行：python3 tests/test_filters_scoring.py   （期望 ALL PASSED）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ingest, scorer, store  # noqa: E402

CFG = store.load("config") if (store.ROOT / "data" / "config.json").exists() else {
    "filter": {"max_published_age_days": 30, "exclude_headhunter": True,
               "exclude_outsourcing": True, "exclude_experience_gte_years": 1}
}

if (store.ROOT / "data" / "profile" / "profile.json").exists():
    PROFILE = store.load("profile")
else:
    import json as _json
    PROFILE = _json.loads((Path(__file__).parent / "fixtures" / "minimal_profile.json").read_text(encoding="utf-8"))


def J(**kw):
    base = {"title": "工程师", "company": "某公司", "description": "", "id": "t"}
    base.update(kw)
    return base


def main() -> int:
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)

    # —— 过滤层 ——
    def rejected(job):
        flags = ingest.detect_flags(job, CFG)
        keep, reason = ingest.campus_filter(job, flags, CFG)
        return (not keep, reason)

    r = rejected(J(description="岗位要求3年以上Java经验"))
    check("≥1年经验岗应拒绝", r[0] and "经验" in r[1])

    r = rejected(J(title="IT猎头顾问", description="负责候选人寻访"))
    check("猎头岗应拒绝", r[0] and "猎头" in r[1])

    r = rejected(J(description="软件外包开发，驻场服务"))
    check("外包岗应拒绝", r[0])

    r = rejected(J(title="算法实习生", description="经验不限，应届生可投", published_at="2020-01-01"))
    check("过期岗应拒绝", r[0] and "过期" in r[1])

    r = rejected(J(title="", company="无名氏"))
    check("缺标题应拒绝", r[0])

    job_campus = J(title="2027届校招-AI产品经理", description="面向应届生，专业不限")
    keep, _ = ingest.campus_filter(job_campus, ingest.detect_flags(job_campus, CFG), CFG)
    check("校招岗应保留", keep)

    flags = ingest.detect_flags(J(title="软件工程师", description="经验不限"), CFG)
    check("经验不限应识别", flags["experience_years_gte"] == 0.0)

    flags = ingest.detect_flags(J(description="要求1-3年经验"), CFG)
    check("1-3年应解析为1", flags["experience_years_gte"] == 1.0)

    # —— 去重 ——
    k1 = ingest.dedupe_key(J(company="阿里  巴巴（中国）有限公司", title="Java开发工程师"))
    k2 = ingest.dedupe_key(J(company="阿里巴巴中国有限公司", title="java开发工程师"))
    check("同公司同岗位不同写法应同键", k1 == k2)

    # —— 评分层 ——
    w = scorer.WEIGHTS
    check("权重和为1", abs(sum(w.values()) - 1.0) < 1e-9)
    check("工作年限不在评分维度", all("exp" != k or "year" not in k for k in w))

    job_ok = J(title="Agent开发工程师(校招)", company="某AI国企", city="南京", salary="18-28K",
               description="负责智能体Agent与LLM应用开发，要求Python、React，提供落户补贴与培养体系")
    s = scorer.score_job(job_ok, PROFILE)
    check("高分岗位≥80", s["total"] >= 80)
    check("有评分理由", all(d["reasons"] for d in s["dims"].values()))
    check("国企加分存在", any("国企" in b for b, _ in s["bonus"]))

    s2 = scorer.score_job(job_ok, PROFILE)
    import json
    check("评分决定论", json.dumps(s, sort_keys=True) == json.dumps(s2, sort_keys=True))

    job_bad = J(title="资深后端", company="某公司", city="乌鲁木齐", salary="5-8K",
                description="要求10年Java、Kafka经验")
    flags = ingest.detect_flags(job_bad, CFG)
    keep, reason = ingest.campus_filter(job_bad, flags, CFG)
    check("10年经验岗被过滤层拦截", not keep)

    lo, hi = scorer._parse_salary(J(salary="12-18K·14薪"))
    check("薪资解析 12/18", (lo, hi) == (12.0, 18.0))
    lo, hi = scorer._parse_salary(J(salary="1-2万/月"))
    check("薪资解析万 10/20", (lo, hi) == (10.0, 20.0))

    if failures:
        print("FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL TESTS PASSED (17 filter/scoring checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
