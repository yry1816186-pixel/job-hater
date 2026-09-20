#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dashboard.py — 投递进度离线 HTML 看板（单文件、零依赖、无 CDN）

内容：KPI 行 → 投递漏斗（单色 ordinal 渐变，明暗双模式均经 dataviz 规范校验）
→ 评分分布直方图 → 信源健康（状态色+图标+文字，不靠颜色单独表意）→ 投递明细表。
图表纯内联 SVG，悬停提示用原生 <title>；每张图都有对应明细表（可及性兜底）。

用法：python3 core/cli.py dashboard  → 生成 data/pipeline_dashboard.html（浏览器打开）
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from core import scorer, store

# dataviz 规范校验过的 ordinal 单色渐变（浅色/深色两套，勿改单点）
RAMP_LIGHT = ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"]
RAMP_DARK = ["#9ec5f4", "#5598e7", "#256abf", "#184f95"]
STATUS = {"ok": ("#0ca30c", "✅ 正常"), "fail": ("#d03b3b", "❌ 失败")}

INK = {
    "light": {"surface": "#fcfcfb", "page": "#f9f9f7", "primary": "#0b0b0b",
              "secondary": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    "dark": {"surface": "#1a1a19", "page": "#0d0d0d", "primary": "#ffffff",
             "secondary": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835"},
}


def _collect() -> dict:
    store.ensure_defaults()
    app = store.load("applications")
    profile = store.load("profile")
    db = store.load("jobs")
    active = [j for j in db["jobs"] if j.get("status") != "rejected"]
    today = dt.date.today().isoformat()
    apps = app.get("applications", [])
    applied = [a for a in apps if a.get("status", "applied") == "applied"]
    interviewing = [a for a in apps if a.get("status") == "interviewing"]
    offers = [a for a in apps if a.get("status") == "offer"]
    candidates = []
    scored = 0
    for j in active:
        s = scorer.score_job(j, profile)
        scored += 1
        if s["total"] >= 60:
            candidates.append((j, s))
    daily = app.get("daily_log", {}).get(today, {})
    health = []
    hp = store.DATA / "feeds" / "wenke_health.json"
    if hp.exists():
        import json
        health = json.loads(hp.read_text(encoding="utf-8"))
    return {
        "today": today,
        "kpi": {
            "today_applied": sum(v.get("count", 0) for v in daily.values()),
            "active_jobs": len(active),
            "candidates": len(candidates),
            "jd_covered": f"{100 * sum(1 for j in active if (j.get('description') or '').strip()) // max(1, len(active))}%",
            "blacklist": len(app.get("blacklist", [])),
        },
        "funnel": [
            ("岗位库（活跃）", len(active)),
            ("候选池（≥60分）", len(candidates)),
            ("已投递", len({a['company'] for a in applied})),
            ("面试中", len(interviewing)),
        ],
        "offers": len(offers),
        "hist": Counter(min(int(s["total"]) // 5 * 5, 95) for _, s in candidates),
        "health": health,
        "apps": apps,
    }


def _funnel_svg(stages: list[tuple[str, int]]) -> str:
    maxv = max(1, max(v for _, v in stages))
    rows, y = [], 8
    label_w, bar_max = 132, 420
    for i, (name, v) in enumerate(stages):
        w = round(bar_max * v / maxv) if v else 0
        rows.append(
            f"<text x='{label_w - 8}' y='{y + 16}' text-anchor='end' class='lbl'>{name}</text>"
            f"<line x1='{label_w}' y1='{y + 21}' x2='{label_w + bar_max}' y2='{y + 21}' class='axis'/>"
            f"<rect x='{label_w}' y='{y}' width='{w}' height='20' rx='4' class='bar s{i}'>"
            f"<title>{name}：{v}</title></rect>"
            f"<text x='{label_w + w + 8}' y='{y + 16}' class='val'>{v}</text>")
        y += 34
    return (f"<svg viewBox='0 0 {label_w + bar_max + 60} {y}' role='img' class='chart'>"
            + "".join(rows) + "</svg>")


def _hist_svg(bins: Counter) -> str:
    lo, hi = 50, 100
    bw, gap, hmax = 34, 2, 150
    total = sum(bins.values())
    peak_bin = max(bins, key=lambda b: bins[b]) if bins else None
    cols, x = [], 0
    for base in range(lo, hi, 5):
        v = bins.get(base, 0)
        h = round(hmax * v / max(1, max(bins.values()))) if v else 0
        label = f"<text x='{x + bw / 2}' y='{hmax - h - 6}' text-anchor='middle' class='val'>{v}</text>" \
            if base == peak_bin else ""
        cols.append(
            f"<g><rect x='{x}' y='{hmax - h}' width='{bw}' height='{h}' rx='4' class='bar hist'>"
            f"<title>{base}–{base + 4}分：{v} 个（占 {100 * v // max(1, total)}%）</title></rect>{label}"
            f"<text x='{x + bw / 2}' y='{hmax + 16}' text-anchor='middle' class='tick'>{base if base % 10 == 0 else ''}</text></g>")
        x += bw + gap
    return (f"<svg viewBox='0 0 {x} {hmax + 44}' role='img' class='chart'>"
            f"<line x1='0' y1='{hmax}' x2='{x}' y2='{hmax}' class='axis'/>" + "".join(cols) + "</svg>"
            f"<p class='cap'>候选池共 {total} 个（≥60分；直方图仅标注峰值箱，悬停查看各箱）</p>")


def render_dashboard() -> str:
    d = _collect()
    k = d["kpi"]
    ok_n = sum(1 for h in d["health"] if h.get("ok"))
    fail_n = len(d["health"]) - ok_n

    def tile(label: str, value: str) -> str:
        return f"<div class='tile'><div class='tv'>{value}</div><div class='tl'>{label}</div></div>"

    kpis = "".join([
        tile(f"今日已投递（{d['today']}）", k["today_applied"]),
        tile("岗位库（活跃）", k["active_jobs"]),
        tile("候选池（≥60分）", k["candidates"]),
        tile("JD全文覆盖", k["jd_covered"]),
        tile("黑名单", k["blacklist"]),
    ])
    health_rows = "".join(
        f"<tr><td>{h.get('source', '?')}</td>"
        f"<td class='{'good' if h.get('ok') else 'crit'}'>{STATUS['ok' if h.get('ok') else 'fail'][1]}</td>"
        f"<td class='num'>{h.get('fetched', 0)}</td><td>{(h.get('error') or '—')[:60]}</td></tr>"
        for h in d["health"]) or "<tr><td colspan='4'>尚无采集记录</td></tr>"
    app_rows = "".join(
        f"<tr><td>{a.get('applied_at', '')[:10]}</td><td>{a.get('company', '')}</td>"
        f"<td>{a.get('platform', '')}</td><td>{a.get('status', 'applied')}</td><td class='num'>{a.get('job_id', '')}</td></tr>"
        for a in d["apps"]) or "<tr><td colspan='5'>暂无投递记录 —— apply --job <id> --send 后这里会有第一行</td></tr>"

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>投递进度看板 · Campus-Job-Agent</title>
<style>
  :root {{ color-scheme: light;
    --surface:{INK['light']['surface']};--page:{INK['light']['page']};--ink:{INK['light']['primary']};
    --ink2:{INK['light']['secondary']};--muted:{INK['light']['muted']};--grid:{INK['light']['grid']};--axis:{INK['light']['axis']};
    --f0:{RAMP_LIGHT[0]};--f1:{RAMP_LIGHT[1]};--f2:{RAMP_LIGHT[2]};--f3:{RAMP_LIGHT[3]};--hist:#2a78d6;}}
  @media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark;
    --surface:{INK['dark']['surface']};--page:{INK['dark']['page']};--ink:{INK['dark']['primary']};
    --ink2:{INK['dark']['secondary']};--muted:{INK['dark']['muted']};--grid:{INK['dark']['grid']};--axis:{INK['dark']['axis']};
    --f0:{RAMP_DARK[0]};--f1:{RAMP_DARK[1]};--f2:{RAMP_DARK[2]};--f3:{RAMP_DARK[3]};--hist:#3987e5;}} }}
  .bar.s0{{fill:var(--f0)}} .bar.s1{{fill:var(--f1)}} .bar.s2{{fill:var(--f2)}} .bar.s3{{fill:var(--f3)}}
  .bar.hist{{fill:var(--hist)}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--page);color:var(--ink);
    font-family:system-ui,-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}
  .wrap{{max-width:960px;margin:0 auto;padding:24px 20px 60px}}
  h1{{font-size:1.25rem}} h2{{font-size:1rem;margin:28px 0 10px}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
  .tile{{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:12px 14px}}
  .tv{{font-size:1.7rem;font-weight:650}} .tl{{font-size:.8rem;color:var(--ink2);margin-top:2px}}
  .card{{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}}
  .chart .lbl{{font-size:12px;fill:var(--ink2)}} .chart .val{{font-size:12px;font-weight:600;fill:var(--ink)}}
  .chart .tick{{font-size:10px;fill:var(--muted);font-variant-numeric:tabular-nums}}
  .chart .axis{{stroke:var(--axis);stroke-width:1}}
  table{{width:100%;border-collapse:collapse;font-size:.82rem}}
  th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid)}}
  td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
  .good{{color:#006300}} .crit{{color:#d03b3b}}
  @media (prefers-color-scheme: dark){{ .good{{color:#0ca30c}} }}
  .cap{{font-size:.78rem;color:var(--muted)}}
</style></head><body><div class="wrap">
<h1>投递进度看板</h1>
<p class="cap">生成于 {d['today']}｜数据全部来自本地 data/，本文件可直接双击打开</p>
<div class="kpis">{kpis}</div>
<h2>投递漏斗（当前存量，非时间序列）</h2>
<div class="card">{_funnel_svg(d['funnel'])}</div>
<h2>候选池评分分布</h2>
<div class="card">{_hist_svg(d['hist'])}</div>
<h2>信源健康（最近一次采集：{ok_n} 成功 / {fail_n} 失败）</h2>
<div class="card"><table><thead><tr><th>源</th><th>状态</th><th class="num">条数</th><th>失败原因</th></tr></thead>
<tbody>{health_rows}</tbody></table></div>
<h2>投递明细</h2>
<div class="card"><table><thead><tr><th>日期</th><th>公司</th><th>渠道</th><th>状态</th><th class="num">岗位ID</th></tr></thead>
<tbody>{app_rows}</tbody></table></div>
</div></body></html>"""
