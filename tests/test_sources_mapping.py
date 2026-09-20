#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sources_mapping.py — 信源映射 + 近似去重测试集

运行：python3 tests/test_sources_mapping.py   （期望输出 ALL TESTS PASSED）
覆盖：mapping.py 两组映射、title_similarity、近似去重（同公司标题近似 / 跨公司内容指纹），
ingest 端到端用例在临时目录隔离运行，绝不触碰 data/ 真实库。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters" / "sources"))

from core import ingest, store  # noqa: E402
from mapping import jobitem_to_raw, xiaozhao_to_raw  # noqa: E402


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(("✅" if cond else "❌") + f" {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def test_jobitem_mapping() -> bool:
    item = {
        "company": "小米", "job_id": "12345", "title": "AI产品经理-大模型方向",
        "category": "产品类", "location": "北京·南京", "url": "https://app.mokahr.com/x/1",
        "publish_time": "2026-09-18 10:00:00", "tags": "27届,提前批/急招",
        "recruit_type": "校招", "description": "负责大模型产品需求分析",
    }
    raw = jobitem_to_raw(item)
    ok = check("JobItem→raw：标题/公司/城市透传", raw["title"] == "AI产品经理-大模型方向" and raw["company"] == "小米" and raw["city"] == "北京·南京")
    ok &= check("JobItem→raw：publish_time 截断为日期", raw["published_at"] == "2026-09-18")
    ok &= check("JobItem→raw：tags 拆为关键词", raw["keywords"] == ["27届", "提前批", "急招"])
    ok &= check("JobItem→raw：类别进 department，来源可溯源", raw["department"] == "产品类" and raw["extras"]["origin"] == "wenke-radar")
    empty = jobitem_to_raw({"company": "X", "job_id": "1", "title": "T"})
    ok &= check("JobItem→raw：缺字段如实留空不臆造", empty["keywords"] == [] and empty["published_at"] is None and empty["city"] is None)
    return ok


def test_xiaozhao_mapping() -> bool:
    e = {"c": "百度", "p": "技术类、产品类", "l": "北京/上海/广州/深圳", "e": "",
         "w": "批次:27届秋招正式批", "d": "2026  10  31", "s": "校招信息聚合平台",
         "t": "互联网", "ind": "互联网科技", "u": "https://dwz.cn/uBmEsGqL"}
    raw = xiaozhao_to_raw(e)
    ok = check("xiaozhao→raw：公司/方向/多城市透传", raw["company"] == "百度" and raw["title"] == "技术类、产品类（27届秋招正式批）" and raw["city"] == "北京/上海/广州/深圳")
    ok &= check("xiaozhao→raw：空格日期规范化为 ISO", raw["deadline"] == "2026-10-31")
    ok &= check("xiaozhao→raw：批次清洗后进 keywords 与描述", raw["keywords"] == ["批次:27届秋招正式批", "互联网科技"] and "27届秋招正式批" in raw["description"])
    ok &= check("xiaozhao→raw：id 留空由 ingest 哈希生成", raw["id"] is None)
    e2 = {"c": "X公司", "p": "管培生", "l": "多地", "d": "招满即止", "w": "批次:27届暑期实习", "ind": "", "s": "", "u": ""}
    raw2 = xiaozhao_to_raw(e2)
    ok &= check("xiaozhao→raw：非日期截止如实置 None", raw2["deadline"] is None)
    ok &= check("xiaozhao→raw：岗位方向缺失不虚构", xiaozhao_to_raw({"c": "Y"})["title"] == "校招（方向未标注）")
    return ok


def test_title_similarity() -> bool:
    ok = check("相似度：完全相同=1.0", ingest.title_similarity("AI产品经理（2027校招）", "AI产品经理（2027校招）") == 1.0)
    ok &= check("相似度：改头换面 repost 高于阈值", ingest.title_similarity("AI产品经理（2027校招）", "AI产品经理2027届校招") >= 0.7)
    ok &= check("相似度：不同岗位低于阈值", ingest.title_similarity("AI产品经理", "后端开发工程师") < 0.3)
    return ok


def test_near_dup_end_to_end() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="cja-test-"))
    orig_paths = dict(store.PATHS)
    store.PATHS.update({"jobs": tmp / "jobs.json", "applications": tmp / "applications.json", "config": tmp / "config.json"})
    try:
        store.ensure_defaults()
        A = {"title": "AI产品经理（2027校招）", "company": "甲公司", "city": "南京",
             "url": "https://a.example/1", "description": "负责大模型产品需求分析与落地，熟悉LLM与Agent技术边界者优先，沟通能力强。" * 1}
        A_near = {"title": "AI产品经理2027届校招", "company": "甲公司", "city": "南京",
                  "url": "https://b.example/2", "description": "另一段完全不同的描述文本，用于确认按标题近似命中而非内容指纹。" * 1}
        B_fp = {"title": "后端开发工程师", "company": "甲公司", "city": "北京",
                "url": "https://c.example/3", "description": A["description"]}
        C = {"title": "前端开发工程师", "company": "丙公司", "city": "上海",
             "url": "https://d.example/4", "description": "负责 Web 前端架构与交互实现。" * 3}
        C2 = {"title": "前端开发工程师（苏州）", "company": "丁公司", "city": "苏州",
              "url": "https://e.example/5", "description": "负责 Web 前端架构与交互实现。" * 3}
        A_dup = dict(A)

        s1 = ingest.ingest_jobs([A], "test")
        ok = check("端到端：首条入库", s1["added"] == 1 and s1["rejected"] == 0)
        s2 = ingest.ingest_jobs([A_near], "test")
        ok &= check("端到端：同公司标题近似 → 近似去重", s2.get("near_duped") == 1, json.dumps(s2, ensure_ascii=False))
        s3 = ingest.ingest_jobs([B_fp], "test")
        ok &= check("端到端：同公司同JD不同标题 → 内容指纹近似去重", s3.get("near_duped") == 1, json.dumps(s3, ensure_ascii=False))
        s4 = ingest.ingest_jobs([C], "test")
        ok &= check("端到端：不同公司不同岗位 → 正常入库", s4["added"] == 1)
        s5 = ingest.ingest_jobs([A_dup], "test")
        ok &= check("端到端：精确重复 → 原有去重仍生效", s5["deduped"] == 1)
        s6 = ingest.ingest_jobs([C2], "test")
        ok &= check("端到端：他公司贴同段JD → 判定为不同机会而非重复", s6["added"] == 1, json.dumps(s6, ensure_ascii=False))
        E = {"title": "测试工程师", "company": "戊公司", "city": "南京", "url": "https://f.example/6", "description": ""}
        E2 = {"title": "测试工程师", "company": "戊公司", "city": "南京", "url": "https://f.example/6",
              "description": "负责测试平台搭建与自动化用例设计，要求熟悉Python与CI流程，有性能测试经验者优先。" * 2}
        s7 = ingest.ingest_jobs([E], "test")
        s8 = ingest.ingest_jobs([E2], "test")
        ok &= check("端到端：空JD先入库，再到达带JD版本 → 补全而非丢弃", s7["added"] == 1 and s8.get("enriched") == 1,
                    json.dumps({"s7": s7["added"], "s8": s8}, ensure_ascii=False))
        jobs = store.load("jobs")["jobs"]
        ok &= check("端到端：库内实际条目数正确", len(jobs) == 4, f"实际 {len(jobs)}")
        rec = next(j for j in jobs if j["company"] == "戊公司")
        ok &= check("端到端：补全后描述真实落库", "测试平台" in rec["description"])
        return ok
    finally:
        store.PATHS = orig_paths


def main() -> int:
    results = [test_jobitem_mapping(), test_xiaozhao_mapping(), test_title_similarity(), test_near_dup_end_to_end()]
    failed = results.count(False)
    if failed:
        print(f"\n{failed} 组用例未通过")
        return 1
    print("\nALL TESTS PASSED (4 suites: mapping×2 / similarity / near-dup e2e)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
