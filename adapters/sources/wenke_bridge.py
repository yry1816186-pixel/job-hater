#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wenke_bridge.py — wenke-radar（MIT）采集桥

复用 repos/wenke-radar 的 45 个官方接口抓取器（飞书ATS/北森/Moka/百库及各厂专有接口，
含滴滴响应加密等已解决的反爬处理），把其 JobItem 输出映射为本系统统一契约，
落盘 data/feeds/wenke.json 供 core/ingest 导入。本脚本不写库、不过滤——分层职责不变。

运行环境：需要 .venv-sources（见 adapters/sources/setup_env.sh），由 CLI fetch 子命令
以该虚拟环境解释器调用。直接手动运行：
    .venv-sources/bin/python adapters/sources/wenke_bridge.py [--only 京东,小米] [--include-social]

来源与协议：wenke-radar（onism1767-creator/wenke-radar，MIT），
归档于 repos/wenke-radar/（含其 LICENSE），本桥仅做调用与字段映射，未复制其源码进本仓库分发边界之外。
采集纪律遵循其 CLAUDE.md：只调公开接口、限速、每日一次。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WENKE = ROOT / "repos" / "wenke-radar"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WENKE))

import config as wenke_config  # noqa: E402  (wenke-radar 纯配置模块)
from mapping import jobitem_to_raw  # noqa: E402

SOCIAL_MARK = "(社招)"          # wenke 源命名约定：社招源以 (社招) 结尾
DEFAULT_OFF = ["offerstar"]     # 聚合源按原作者求职方向调参（运营），默认关闭，需要时可 --only offerstar 启用


def build_session():
    """带重试的共享 HTTP 会话（与 wenke main.py 同款：5xx 自动重试）。"""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers.update({"User-Agent": wenke_config.USER_AGENT})
    retry = Retry(total=wenke_config.MAX_RETRIES, backoff_factor=1,
                  status_forcelist=[500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    for scheme in ("https://", "http://"):
        s.mount(scheme, HTTPAdapter(max_retries=retry))
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description="wenke-radar 校招信源采集桥")
    ap.add_argument("--out", default=str(ROOT / "data" / "feeds" / "wenke.json"))
    ap.add_argument("--health-out", default=str(ROOT / "data" / "feeds" / "wenke_health.json"))
    ap.add_argument("--only", default="", help="逗号分隔源名白名单（调试/首跑小样用）")
    ap.add_argument("--include-social", action="store_true", help="同时采集社招源（默认只采校招）")
    args = ap.parse_args()

    try:
        from scrapers import SCRAPERS, GenericScraper  # noqa: F401
    except ImportError as e:
        print(f"❌ 采集底座依赖缺失（{e}）。请先执行：bash adapters/sources/setup_env.sh", file=sys.stderr)
        return 1

    # —— 源开关覆写（只改 wenke_config 运行时副本，不动上游文件）——
    if not args.include_social:
        for name, on in list(wenke_config.ENABLED_COMPANIES.items()):
            if SOCIAL_MARK in name:
                wenke_config.ENABLED_COMPANIES[name] = False
    for name in DEFAULT_OFF:
        wenke_config.ENABLED_COMPANIES[name] = False

    only = {x.strip() for x in args.only.split(",") if x.strip()}

    # —— JD 正文收割：wenke 校招源有意不带 description，但多数列表接口 payload 里
    # 本就含 description/requirement 等字段。包装各抓取器的 _parse，把已有字段收割为
    # JobItem.description——零额外请求，/apply 的定制简历因此有真实 JD 输入。
    # 字段名清单来自 wenke 各 scraper 对 payload 的既有消费（domain/enrich 同源），非猜测。
    DESC_KEYS = ("description", "requirement", "duty", "job_desc", "responsibility", "content")

    def _harvest_raw_desc(it: dict) -> str:
        parts = []
        for k in DESC_KEYS:
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
            elif isinstance(v, dict):  # 部分站点是 {"zh_cn": ...} 形态
                t = v.get("zh_cn") or v.get("value") or ""
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return "\n".join(parts)[:20000]

    def _wrap_parse(cls):
        orig = cls.__dict__.get("_parse")
        if orig is None:  # 未自定义 _parse（继承自父类，父类已被包装），无需重复包
            return
        def patched(self, it):
            j = orig(self, it)
            if not getattr(j, "description", ""):
                harvested = _harvest_raw_desc(it)
                if harvested:
                    j.description = harvested
            return j
        cls._parse = patched

    for _cls in set(SCRAPERS.values()):
        _wrap_parse(_cls)

    session = build_session()
    all_jobs: list[dict] = []
    health: list[dict] = []

    def run_one(name: str, make):
        t0 = time.time()
        try:
            result = make().fetch()   # wenke 唯一对外契约：FetchResult（items + 完整性）
        except Exception as e:        # 单源失败绝不拖垮整轮
            health.append({"source": name, "ok": False, "fetched": 0, "error": str(e),
                           "duration_s": round(time.time() - t0, 1)})
            print(f"  ❌ {name}: {e}")
            return
        mapped = [jobitem_to_raw(it) for it in result.items]
        if name == "牛客日程":
            # 牛客日程源产出的是「公司+批次」级校招日程线索，不是逐条职位 JD——如实打标
            for m in mapped:
                m["extras"]["lead"] = True
                m["extras"]["origin"] = "wenke-radar 牛客日程（公司级校招线索）"
        all_jobs.extend(mapped)
        note = "" if getattr(result, "complete", True) is not False else "（⚠️ 疑似不完整）"
        health.append({"source": name, "ok": bool(result.success), "fetched": len(mapped),
                       "error": getattr(result, "error", None),
                       "reported_total": getattr(result, "reported_total", None),
                       "duration_s": round(time.time() - t0, 1)})
        mark = "✅" if result.success else "❌"
        print(f"  {mark} {name}: {len(mapped)} 条{note}"
              + (f"（失败：{result.error}）" if not result.success else ""))

    print(f"🔗 wenke 采集桥启动（{datetime.now().isoformat(timespec='seconds')}）"
          f"｜校招源模式：{'开' if not args.include_social else '含社招'}")
    for name, cls in SCRAPERS.items():
        if not wenke_config.ENABLED_COMPANIES.get(name, True):
            continue
        if only and name not in only:
            continue
        run_one(name, lambda c=cls: c(session))

    for src in getattr(wenke_config, "GENERIC_SOURCES", []):
        name = src.get("name", "未知")
        if only and name not in only:
            continue
        if not wenke_config.ENABLED_COMPANIES.get(name, True):
            continue
        run_one(name, lambda s=src: GenericScraper(session, s))

    out = {"schema": "campus-job-agent raw jobs v1", "origin": "wenke-radar (MIT)",
           "fetched_at": datetime.now().isoformat(timespec="seconds"), "jobs": all_jobs}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(args.health_out).write_text(json.dumps(health, ensure_ascii=False, indent=1), encoding="utf-8")

    ok_n = sum(1 for h in health if h["ok"])
    print(f"\n抓取完成：{len(health)} 个源（成功 {ok_n}），共 {len(all_jobs)} 条 → {args.out}")
    if ok_n < len(health):
        print("⚠️ 有源失败属正常波动（官网维护/改版），详见 health 文件；失败源不影响其余数据可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
