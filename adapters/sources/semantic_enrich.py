#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_enrich.py — 语义相似度补全（BGE-small-zh 本地向量匹配，可选增强）

路线图 #2（向量匹配）的实现，边界刻意收窄：
- 在 .venv-sources 内用 fastembed（onnxruntime，无 torch）本地编码 BAAI/bge-small-zh-v1.5
- 只把「简历摘要 ↔ JD（标题+描述）」的余弦相似度写回岗位记录 extras.semantic_sim（0~1 模型相对值）
- core/scorer 不改五维权重契约，只在 semantic_sim 存在且明显偏低时输出人工复核提示
- 没有 .venv-sources 或模型下载失败 → 本模块不可用，系统其余功能完全不受影响（诚实降级）

用法：.venv-sources/bin/python adapters/sources/semantic_enrich.py
模型首次下载走 HF_ENDPOINT（国内可设 https://hf-mirror.com），失败时报错退出，不假装成功。
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

MODEL = "BAAI/bge-small-zh-v1.5"
BATCH = 64


def profile_text(profile: dict) -> str:
    prefs = profile.get("preferences", {})
    parts = [prefs.get("target_roles") and "、".join(prefs["target_roles"]) or "",
             "；".join(s["name"] for s in profile.get("skills", [])),
             "；".join(h for e in profile.get("experiences", []) for h in e.get("highlights", []))]
    return "。".join(p for p in parts if p)


def main() -> int:
    from core import store
    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("❌ fastembed 未安装（采集底座缺增强依赖）。执行：.venv-sources/bin/pip install fastembed", file=sys.stderr)
        return 1

    model = TextEmbedding(model_name=MODEL)  # 首次运行触发下载；失败即失败，不重试硬扛
    profile = store.load("profile")
    db = store.load("jobs")
    active = [j for j in db["jobs"] if j.get("status") != "rejected"]
    targets = [j for j in active if (j.get("description") or "").strip()]
    if not targets:
        print("没有带 JD 全文的岗位可增强（先跑 fetch 或粘贴导入）。")
        return 0

    texts = [profile_text(profile)] + [
        (j.get("title", "") + "\n" + j["description"])[:2000] for j in targets
    ]
    vecs = [v for v in model.embed(texts, batch_size=BATCH)]

    def cos(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    ref = [float(x) for x in vecs[0]]
    today = dt.date.today().isoformat()
    sims = []
    for j, v in zip(targets, list(vecs)[1:]):
        s = round(cos(ref, [float(x) for x in v]), 3)  # float()：fastembed 返回 numpy 标量，json 不认
        j.setdefault("extras", {})["semantic_sim"] = s
        j["extras"]["semantic_at"] = today
        sims.append(s)
    store.save("jobs", db)
    n = len(sims)
    hi = sum(1 for s in sims if s >= 0.6)
    print(f"🧠 语义补全完成（{MODEL}，模型相对值非校准分）：{n} 个带JD岗位已写入 semantic_sim｜≥0.6 共 {hi} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
