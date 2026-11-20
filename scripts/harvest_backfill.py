"""采集暂存后处理：从详情页 <title> 回填公司与标题（实习僧卡片段层的兜底）。

实测（2026-11-20 两批）：实习僧列表卡的 closest 容器层级不稳定，公司字段常空；
但详情页 <title> 形如「{岗位}实习招聘-{公司}实习生招聘-实习僧」稳定可用。
首批 30/30 回填成功。空公司行 ingest 会拒收（title/company 必填），本脚本是
批量入库前的必经工步。

用法：
  python scripts/harvest_backfill.py            # 处理 ~/.jobhater/harvest/shixiseng
  python scripts/harvest_backfill.py --site boss
  python scripts/harvest_backfill.py --dry-run  # 只统计不修改
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

HARVEST_ROOT = Path.home() / ".jobhater" / "harvest"
CDP_PORT = 9333


def parse_title(page_title: str) -> tuple[str, str]:
    """「{岗位}实习招聘-{公司}实习生招聘-实习僧」→ (岗位, 公司)。"""
    t = page_title.split("-实习僧")[0].split("– 实习僧")[0].strip()
    m = re.search(r"^(.+?)(?:实习)?招聘[-—–](.+?)(?:实习生?|实习)?招聘?$", t)
    if m:
        title, company = m.group(1).strip(), m.group(2).strip()
        # 公司里可能混进「实习生」尾缀残留
        company = re.sub(r"(实习生?|实习生招聘)$", "", company).strip()
        return title, company
    return "", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="shixiseng")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    staging = HARVEST_ROOT / args.site / "staging.json"
    if not staging.exists():
        print(f"暂存不存在: {staging}")
        return 2
    data = json.loads(staging.read_text(encoding="utf-8"))
    items = data["items"]
    need = [i for i in items if not i.get("company") or len(i.get("title", "")) < 2]
    print(f"共 {len(items)} 条，需回填 {len(need)} 条")
    if args.dry_run or not need:
        for it in items[:8]:
            print(f"  {it.get('title','')[:22]!r} @ {it.get('company','')!r}")
        return 0

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    fixed = 0
    try:
        for it in need:
            try:
                page.goto(it["url"], wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=8000)
                time.sleep(random.uniform(1.5, 2.5))
                title, company = parse_title(page.title() or "")
                if company:
                    it["company"] = company[:40]
                    if len(it.get("title", "")) < 2 and title:
                        it["title"] = title[:60]
                    fixed += 1
            except KeyboardInterrupt:
                print("中断——已回填部分保留在内存，未写盘")
                raise
            except Exception as e:
                print("  skip", it.get("url", "")[:50], type(e).__name__)
    finally:
        if fixed:
            staging.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            page.close()
            browser.close()
        finally:
            pw.stop()
    print(f"回填 {fixed}/{len(need)}（已写回 {staging}）")
    remain = sum(1 for i in items if not i.get("company"))
    if remain:
        print(f"⚠️ 仍有 {remain} 条空公司——入库会被拒收，建议剔除或人工补")
    return 0


if __name__ == "__main__":
    sys.exit(main())
