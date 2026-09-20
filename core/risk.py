#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""risk.py — 投递风控闸门（确定性强制，不是建议）

规则（config.json → risk 可调）：
- 单平台每日投递 ≤ 25 份
- 单条消息间隔 ≥ 30 秒
- 平台风控敏感时段（默认 22:00–08:00）禁止自动投递
- 投递黑名单：同一家企业不重复投递

此模块是唯一投递出口的守门人：apply 命令在真正发出投递动作前必须调用
check()，返回 allow=False 时必须中止并把原因告知用户。绝不静默放行。
"""
from __future__ import annotations

import datetime as dt
import re

from core import store


def _now() -> tuple[dt.date, dt.time]:
    return dt.date.today(), dt.datetime.now().time()


def _norm_company(name: str) -> str:
    return re.sub(r"[\s（）()\[\]【】·，,。.\-_/|]*(有限公司|股份|集团|科技|分公司)?$", "", (name or "").strip()).lower()


def in_blacklist(company: str, app: dict) -> tuple[bool, str]:
    n = _norm_company(company)
    for b in app.get("blacklist", []):
        if _norm_company(b) == n or _norm_company(b) in n or n in _norm_company(b):
            return True, f"「{company}」在投递黑名单（记录：{b}）"
    return False, ""


def check(platform: str, company: str) -> dict:
    """投递前检查。返回 {allow, reasons, warnings}。任何 reason 命中即拒绝。"""
    store.ensure_defaults()
    cfg = store.load("config")
    app = store.load("applications")
    rcfg = cfg.get("risk", {})
    reasons: list[str] = []
    warnings: list[str] = []

    ok, why = in_blacklist(company, app)
    if ok:
        reasons.append(why)

    today, now_t = _now()
    cap = int(rcfg.get("daily_apply_cap_per_platform", 25))
    day_entry = app.get("daily_log", {}).get(today.isoformat(), {}).get(platform, {"count": 0})
    if int(day_entry.get("count", 0)) >= cap:
        reasons.append(f"平台「{platform}」今日已投 {day_entry.get('count')}/{cap} 份，达到每日上限（风控保护）")

    for span in rcfg.get("sensitive_hours", ["22:00-08:00"]):
        m = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", span)
        if not m:
            continue
        h1, m1, h2, m2 = map(int, m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        cur = now_t.hour * 60 + now_t.minute
        in_span = (cur >= start or cur < end) if start > end else (start <= cur < end)
        if in_span:
            reasons.append(f"当前处于风控敏感时段 {span}，禁止自动投递（保护账号安全）")
            break

    interval = int(rcfg.get("min_message_interval_sec", 30))
    last = day_entry.get("last_message_at")
    if last:
        try:
            last_dt = dt.datetime.fromisoformat(last)
            elapsed = (dt.datetime.now() - last_dt).total_seconds()
            if elapsed < interval:
                reasons.append(f"距上一条消息仅 {elapsed:.0f}s < 最小间隔 {interval}s，请稍后再投")
            elif elapsed < interval * 2:
                warnings.append(f"当前发送节奏接近风控阈值（间隔 {elapsed:.0f}s），建议放慢")
        except ValueError:
            warnings.append(f"daily_log 中的 last_message_at 无法解析：{last}")

    return {"allow": not reasons, "reasons": reasons, "warnings": warnings}


def record(platform: str, company: str, job_id: str) -> dict:
    """投递成功后记账：更新台账、每日计数与最后发送时间。"""
    app = store.load("applications")
    today = dt.date.today().isoformat()
    app.setdefault("daily_log", {}).setdefault(today, {}).setdefault(platform, {"count": 0})
    app["daily_log"][today][platform]["count"] = int(app["daily_log"][today][platform]["count"]) + 1
    app["daily_log"][today][platform]["last_message_at"] = dt.datetime.now().isoformat(timespec="seconds")
    app["applications"].append({
        "job_id": job_id,
        "company": company,
        "platform": platform,
        "status": "applied",
        "applied_at": dt.datetime.now().isoformat(timespec="seconds"),
        "resume_path": None,
        "cover_letter_path": None,
        "notes": [],
    })
    # 自动维护黑名单：同一企业投递过即记录，防止短期内重复投递同一家
    if not any(_norm_company(b) == _norm_company(company) for b in app["blacklist"]):
        app["blacklist"].append(company)
    store.save("applications", app)
    return app


def update_status(job_id: str, status: str, note: str | None = None) -> dict:
    """更新某个投递的状态（applied→interviewing→offer / rejected 等）。"""
    app = store.load("applications")
    for a in app["applications"]:
        if a["job_id"] == job_id:
            a["status"] = status
            if note:
                a.setdefault("notes", []).append(note)
            store.save("applications", app)
            return a
    raise KeyError(f"投递台账中没有 job_id={job_id} 的记录")
