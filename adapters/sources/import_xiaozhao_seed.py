#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_xiaozhao_seed.py — xiaozhao-radar 校招种子数据集一次性导入（冷启动）

数据集：repos/xiaozhao-radar/jobs.json（Apache-2.0），约 1598 条 27届校招项目线索，
来自腾讯文档「27届实习提前批信息汇总」的每周同步快照（本副本 updated=2026-09-03）。
如实定位：这是「公司+批次+报名入口」级线索，不是逐条职位 JD——评分会偏低属正常，
价值在于冷启动覆盖面与"哪些公司已开 27届批次"的信号。

幂等：重复执行时全部走 ingest 去重，不会产生重复数据。
用法：python3 adapters/sources/import_xiaozhao_seed.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from core import ingest  # noqa: E402
from mapping import xiaozhao_to_raw  # noqa: E402

SEED = ROOT / "repos" / "xiaozhao-radar" / "jobs.json"

# 批次时点过滤（2026-09-20 秋招进行中时点判断）：
# - 27届暑期实习（招聘窗口 2026 上半年）与 27转正实习已收尾，入口大概率关闭 → 不导入
# - 秋招正式批/提前批/日常实习为当前或滚动进行中 → 导入
SKIP_WAVE_WORDS = ["暑期实习", "转正实习"]
WAVE_PRIORITY = ["秋招正式批", "秋招", "提前批", "日常实习"]  # 近似去重保留首条：价值高的批次排前


def _wave_of(entry: dict) -> str:
    return str(entry.get("w", ""))


def _priority(entry: dict) -> int:
    w = _wave_of(entry)
    for i, p in enumerate(WAVE_PRIORITY):
        if p in w:
            return i
    return len(WAVE_PRIORITY)


def main() -> int:
    if not SEED.exists():
        print(f"❌ 找不到种子数据集 {SEED}（repos/ 归档缺失）。", file=sys.stderr)
        return 1
    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data.get("jobs", [])
    if not entries:
        print("❌ 种子数据集为空，不导入。", file=sys.stderr)
        return 1
    kept, skipped = [], []
    for e in entries:
        if any(w in _wave_of(e) for w in SKIP_WAVE_WORDS):
            skipped.append(e)
        else:
            kept.append(e)
    # 同批次内近似去重保留首条 → 把信息最全、批次价值最高的排前面
    kept.sort(key=lambda e: (_priority(e), -len(str(e.get("p", "")))))
    if skipped:
        from collections import Counter
        reasons = Counter(_wave_of(e).strip() or "（无批次标注）" for e in skipped)
        detail = "、".join(f"{k} {v} 条" for k, v in reasons.most_common())
        print(f"⏭ 跳过已过时点的批次 {len(skipped)} 条（{detail}）——当前为秋招季，这些入口已收尾")
    raw_jobs = [xiaozhao_to_raw(e) for e in kept]
    stats = ingest.ingest_jobs(raw_jobs, source_platform="xiaozhao_seed")
    print(f"📥 校招种子导入完成（xiaozhao-radar，快照 {data.get('updated', '未知')}）："
          f"收到 {stats['received']}｜入库 {stats['added']}｜精确去重 {stats['deduped']}"
          f"｜近似去重 {stats.get('near_duped', 0)}｜过滤拒绝 {stats['rejected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
