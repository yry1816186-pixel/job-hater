#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobradar_leads.py — 从 job-radar 信源清单提取「校招垂直渠道线索索引」

job-radar（Jasmine-Liu-min/job-radar）无 LICENSE 文件，其代码不可复制再用；
但其 config/sources.csv 是"信源 URL + 实测状态"的事实清单（数据而非代码），
本脚本仅把它转写为本系统的线索索引（data/feeds/jobradar_leads.json），
逐条保留来源标注。线索不会自动当成岗位入库——需要人工打开核对或等待
本系统为对应渠道编写自有适配器（降级原则：不假装成功）。

用法：python3 adapters/sources/jobradar_leads.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCES_CSV = ROOT / "repos" / "job-radar" / "config" / "sources.csv"
OUT = ROOT / "data" / "feeds" / "jobradar_leads.json"

FIELDS = ["source_id", "company_name", "org_type", "source_type", "adapter", "endpoint",
          "priority", "fetch_method", "requires_login", "city_scope", "status", "notes"]


def main() -> int:
    if not SOURCES_CSV.exists():
        print(f"❌ 找不到 {SOURCES_CSV}（repos/ 归档缺失）。", file=sys.stderr)
        return 1
    rows = []
    with open(SOURCES_CSV, encoding="utf-8", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        if (row.get("status") or "").strip() != "active":
            continue  # blocked/deprecated 的如实排除，只索引当前可用渠道
        lead = {k: (row.get(k) or "").strip() for k in FIELDS}
        lead["origin"] = "job-radar config/sources.csv（无LICENSE，仅作线索索引，未复制其代码）"
        rows.append(lead)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema": "campus-job-agent leads v1",
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "leads": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    from collections import Counter
    by_adapter = Counter(r["adapter"] for r in rows)
    print(f"🔎 线索索引完成：{len(rows)} 个可用渠道 → {OUT}")
    for adapter, n in by_adapter.most_common():
        print(f"  · {adapter}: {n}")
    print("  （线索 ≠ 岗位：不自动入库。ncss/国聘/华为校招等 API 渠道待本系统逐个实测接入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
