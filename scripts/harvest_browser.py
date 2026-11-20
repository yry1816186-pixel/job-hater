"""浏览器采集工具（会话级，v3.4.0 两步走的第一步）：BOSS直聘 / 实习僧。

用法（在仓库根目录）：
  python scripts/harvest_browser.py --site boss --queries "AI产品经理" --city 北京
  python scripts/harvest_browser.py --site shixiseng --queries "产品实习" --max-jobs 30
  python scripts/harvest_browser.py --site boss --probe          # 选择器探针：存页面快照供适配
  python scripts/harvest_browser.py --launch-only                # 只起浏览器等你扫码登录

设计立场（与项目铁律一致）：
- **只读采集**：绝不点击「立即沟通/投递简历/登录注册以外的任何提交类按钮」，
  投递永远由用户亲手完成（job-hater 绝不代投铁律的浏览器延伸）；
- **真实会话**：连接用户自己的 Edge（独立自动化 profile，首次扫码登录后
  cookie 常驻，不碰日常浏览器数据）；
- **人类节奏**：动作间随机 3-6s 停顿、单批岗位数硬顶、验证码/滑块出现即停——
  这是礼貌与风控的共同的底线，出现风控是停跑信号不是绕过信号；
- **断点安全**：每采一条原子写暂存 JSON（~/.jobhater/harvest/{site}/staging.json），
  随时可杀，重跑续采；
- **过目闸**：本脚本只产暂存，不直接入库——入库走 job-hater 的统一 import 链
  （四层去重+证据快照），且需用户过目摘要后才执行。

已知边界（如实说明）：BOSS/实习僧前端选择器会漂移，脚本内置多组候选选择器
与 --probe 自检模式；若结构变化导致采到 0 条，先跑 --probe 存快照再适配。
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

HARVEST_ROOT = Path.home() / ".jobhater" / "harvest"
CDP_PORT = 9333
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0")

# BOSS 城市码（subset：常用）。全国=100010000
BOSS_CITIES = {
    "全国": "100010000", "北京": "101010100", "上海": "101020100",
    "深圳": "101280600", "杭州": "101210100", "南京": "101190100",
    "广州": "101280100", "成都": "101270100", "武汉": "101200100",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def human_pause(a: float = 3.0, b: float = 6.0) -> None:
    time.sleep(random.uniform(a, b))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- 浏览器生命周期 ----------


def edge_path() -> Path:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(p).exists():
            return Path(p)
    raise SystemExit("未找到 msedge.exe，请手工指定路径")


def launch_edge(site: str, port: int = CDP_PORT) -> None:
    """独立自动化 profile 起 Edge（已起则复用同 profile 拉起新进程并静默退出）。绝不使用日常 profile。"""
    profile = HARVEST_ROOT / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(edge_path()),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--window-size=1440,900",
        "https://www.zhipin.com/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"Edge 已启动（独立 profile：{profile}，调试端口 {port}）")


def _port_ready(port: int, timeout_s: float = 30.0) -> bool:
    import socket

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def connect(port: int = CDP_PORT):
    from playwright.sync_api import sync_playwright

    if not _port_ready(port):
        raise SystemExit(f"调试端口 {port} 未就绪——Edge 可能没起来，先跑 --launch-only")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception as e:
        pw.stop()
        raise SystemExit(f"CDP 连接失败（先 --launch-only 起浏览器）：{e}") from e
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    # 不复用 Edge 初始标签页：首启流程会关闭/重定向它（TargetClosedError 实测坑），
    # 自开专属页生命周期完全由本脚本掌控
    page = ctx.new_page()
    return pw, browser, page


# ---------- 通用护栏 ----------


def settle(page, wait_cards: str | None = None) -> None:
    """等页面真正可读：load → networkidle（SPA 常态连接会超时，容忍）→ 可选等卡片。"""
    for state, timeout in (("load", 12000), ("networkidle", 8000)):
        try:
            page.wait_for_load_state(state, timeout=timeout)
        except Exception:
            pass
    if wait_cards:
        try:
            page.wait_for_selector(wait_cards, timeout=6000)
        except Exception:
            pass
    human_pause()


def safe_content(page, tries: int = 3) -> str:
    """SPA 导航跳变期 content() 会抛错——重试兜底，全败返回空串（不误判风控）。"""
    for _ in range(tries):
        try:
            return page.content()
        except Exception:
            time.sleep(2)
    return ""


def abort_if_captcha(page, site: str) -> bool:
    """验证码/滑块/安全校验 = 立即停（是停跑信号，不是绕过信号）。

    词表刻意收窄：BOSS 页面自带监控脚本里就有 "geetest"/"captcha" 字样（实测
    probe 快照），裸词误伤会把正常页当风控——只认用户可见的验证文案。
    """
    html = safe_content(page)
    marks = ("滑动验证", "请拖动滑块", "安全验证", "访问异常", "请完成下方验证")
    if html and any(m in html for m in marks):
        log(f"⛔ 检测到验证/风控页面——立即停止采集（{site}）。稍后人工打开浏览器通过验证再跑。")
        return True
    return False


def looks_like_login_wall(page, expected_path: str = "/web/geek/job") -> bool:
    """BOSS 未登录判定（三信号任一）：落点不在预期搜索页（实测会被弹到登录页
    **或城市首页**——geo 重定向）、URL 含 /web/user/、标题含「注册登录」。"""
    try:
        url = page.url
        title = page.title() or ""
    except Exception:
        return True  # 读不到页面按墙处理（保守：宁可等登录也不空跑）
    return expected_path not in url or "/web/user/" in url or "注册登录" in title


def checkpoint(site: str, items: list[dict], meta: dict) -> Path:
    out = HARVEST_ROOT / site
    out.mkdir(parents=True, exist_ok=True)
    path = out / "staging.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"site": site, "harvested_at": _now_iso(), "meta": meta, "items": items},
        ensure_ascii=False, indent=1,
    ), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------- 抽取助手：多组候选选择器依次尝试（前端会漂移） ----------


def first_text(page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                t = (el.inner_text() or "").strip()
                if t:
                    return t
        except Exception:
            continue
    return ""


# ---------- BOSS直聘 ----------


def boss_login_ok(page) -> bool:
    try:
        page.goto("https://www.zhipin.com/", wait_until="domcontentloaded")
        settle(page)
        html = safe_content(page)
    except Exception:  # 标签页瞬断/导航竞态：视为未登录，由 wait 循环重试
        return False
    if not html:
        return False
    # 登录墙判定以「登录入口链接」为准（页面文案「登录」二字在首屏太常见，误判高）
    return "user/?ka=header-login" not in html


def boss_wait_login(page, timeout_s: int = 240) -> bool:
    """主动打开登录页亮二维码，轮询等扫码（部分落点被弹到首页，二维码不在眼前）。"""
    try:
        page.goto("https://www.zhipin.com/web/user/?ka=header-login",
                  wait_until="domcontentloaded")
        settle(page)
    except Exception:
        pass
    log("📱 请在 Edge 窗口扫码登录 BOSS直聘（最长等 4 分钟，登录后自动继续）……")
    deadline = time.time() + timeout_s
    n = 0
    while time.time() < deadline:
        time.sleep(6)
        n += 1
        try:
            # 登录成功的判据：离开登录页（扫码后自动跳转）
            if "/web/user/" not in page.url and "注册登录" not in (page.title() or ""):
                log("✅ 登录成功")
                return True
            if n % 5 == 0:
                # 部分流程扫码后停在登录页不自动跳：偶尔回首页探一次登录态
                page.goto("https://www.zhipin.com/", wait_until="domcontentloaded")
                settle(page)
                if "注册登录" not in (page.title() or "") and "header-login" not in safe_content(page)[:8000]:
                    log("✅ 登录成功")
                    return True
        except Exception:
            pass
    log("⛔ 等待登录超时（未采集任何数据）")
    return False


# 通用：锚点优先的结构化提取（在页面上下文里一次 evaluate，比逐选择器查询稳）。
# 实测教训：逐 selector query 对 SPA 渲染时序极敏感，且 class 命名常与预期不符；
# 以「岗位详情链接锚点」为唯一事实源，公司/薪资/城市从锚点所在卡片的文本里解析。


def _anchor_extract_js(href_pattern: str, base: str) -> str:
    """Python 侧把 pattern/base 烘焙进 JS（page.evaluate 传参的坑：带参函数体
    序列化后不传 arg，参数永远是 undefined——实测 0 卡片的根因）。"""
    js = """
    () => {
      const seen = new Set(); const out = [];
      document.querySelectorAll('a[href*="__PATTERN__"]').forEach(a => {
        let href = a.href || a.getAttribute('href') || '';
        try { href = href.split('?')[0]; } catch (e) {}
        if (!href || seen.has(href)) return;
        if (href.startsWith('/')) href = '__BASE__' + href;
        seen.add(href);
        const card = a.closest('li, [class*="card"], [class*="item"], [class*="job"], [class*="intern"]') || a.parentElement;
        const title = (a.innerText || a.textContent || '').trim().split('\\n')[0].slice(0, 60);
        const cardText = (card ? card.innerText : (a.parentElement ? a.parentElement.innerText : '')) || '';
        out.push({ href: href, title: title, cardText: cardText.slice(0, 400) });
      });
      return out;
    }
    """
    return js.replace("__PATTERN__", href_pattern).replace("__BASE__", base)


_SALARY_RE = None  # 惰性编译（模块顶层 import re 已有）


def _parse_salary(text: str) -> str:
    import re as _re

    m = _re.search(
        r"\d[\d,.]*\s*[-–~]\s*\d[\d,.]*\s*[Kk千万元/天日月月]*|\d+\s*[-–~]\s*\d+元/天",
        text,
    )
    return m.group(0).strip() if m else ""


def _city_from(text: str, known: str = "") -> str:
    import re as _re

    m = _re.search(
        r"(北京|上海|深圳|广州|杭州|南京|成都|武汉|西安|苏州|天津|长沙|重庆|合肥|郑州|"
        r"厦门|青岛|济南|大连|宁波|无锡|佛山|东莞|福州|沈阳|哈尔滨|昆明|贵阳|南昌|"
        r"太原|石家庄|常州|珠海|全国|线上|远程)",
        text,
    )
    return m.group(1) if m else known


def _cards_from_anchors(page, href_pattern: str, base: str, company_hint_idx: int = 1) -> list[dict]:
    """锚点 → 结构化卡片。cardText 按行拆：第0行=标题，公司取「含公司后缀或第2行」。"""
    try:
        raw = page.evaluate(_anchor_extract_js(href_pattern, base))
    except Exception:
        return []
    out = []
    import re as _re

    for r in raw or []:
        href, title, card_text = r.get("href", ""), r.get("title", ""), r.get("cardText", "")
        if not href or not title:
            continue
        lines = [ln.strip() for ln in card_text.split("\n") if ln.strip()]
        company = ""
        for ln in lines[1:6]:
            if _re.search(r"公司|科技|集团|有限|网络|信息|文化|教育|传媒|研究院|事务所|银行|证券", ln) and ln != title:
                company = ln[:40]
                break
        if not company and len(lines) > company_hint_idx:
            company = lines[company_hint_idx][:40]
        out.append({
            "title": title, "company": company,
            "salary": _parse_salary(card_text), "city": _city_from(card_text), "url": href,
        })
    return out


def boss_extract_cards(page) -> list[dict]:
    """BOSS 搜索结果卡片（锚点=/job_detail/ 链接）。"""
    return _cards_from_anchors(page, "/job_detail/", "https://www.zhipin.com")


def boss_detail(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded")
    settle(page, wait_cards="[class*='job-sec-text'], .job-detail, [class*='job-description']")
    jd = first_text(page, [
        ".job-sec .job-sec-text", ".job-detail-section .job-sec-text",
        "[class*='job-sec-text']", ".job-detail", "[class*='job-description']",
    ])
    title = first_text(page, ["h1", ".name", "[class*='job-name']"])
    salary = first_text(page, [".salary", "[class*='salary']"])
    company = first_text(page, [".company-info .name", "[class*='company-name']", "h2 a"])
    return {"title": title, "salary": salary, "company": company, "jd_text": jd[:20000]}


def harvest_boss(page, queries: list[str], city: str, max_jobs: int,
                 max_pages: int, detail: bool, ensure_login=None) -> list[dict]:
    city_code = BOSS_CITIES.get(city, BOSS_CITIES["全国"])
    items: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        for pg in range(1, max_pages + 1):
            if len(items) >= max_jobs:
                break
            url = (f"https://www.zhipin.com/web/geek/job?query={quote(q)}"
                   f"&city={city_code}&page={pg}")
            log(f"搜索页：{q} · {city} · 第{pg}页")
            page.goto(url, wait_until="domcontentloaded")
            settle(page, wait_cards="li.job-card-box, li.job-card, [class*='job-card']")
            if abort_if_captcha(page, "boss"):
                return items
            # 登录墙：未登录时搜索页会被带到登录/注册页
            if looks_like_login_wall(page):
                log("  检测到登录墙")
                if ensure_login is None or not ensure_login(page):
                    log("⛔ 未登录，无法采集（未采集任何数据）")
                    return items
                page.goto(url, wait_until="domcontentloaded")
                settle(page, wait_cards="li.job-card-box, li.job-card, [class*='job-card']")
            # 提取重试环：登录后的首次搜索加载含多级重定向，settle 可能落在
            # 导航中途（实测 content() 39 字符）——卡片没渲染出来≠失败，再等再提
            cards = boss_extract_cards(page)
            for attempt in range(3):
                if cards:
                    break
                log(f"  卡片未就绪，等待重试（{attempt + 1}/3）……")
                time.sleep(4)
                settle(page, wait_cards="li.job-card-box, li.job-card, [class*='job-card']")
                cards = boss_extract_cards(page)
            log(f"  解析到 {len(cards)} 张卡片")
            if not cards:
                # 0 卡片且非登录墙：存快照供选择器适配（防静默空跑）
                out = HARVEST_ROOT / "boss" / "probe.html"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(safe_content(page), encoding="utf-8")
                log(f"  ⚠️ 0 卡片，快照已存 {out}（选择器漂移或页面异常）")
            for card in cards:
                if len(items) >= max_jobs:
                    break
                if card["url"] in seen:
                    continue
                seen.add(card["url"])
                item = dict(card)
                item["source_job_id"] = card["url"].rstrip("/").split("/")[-1].replace(".html", "")
                if detail:
                    log(f"  详情：{card['title'][:24]} @ {card['company']}")
                    d = boss_detail(page, card["url"])
                    if d["jd_text"]:
                        item["description"] = d["jd_text"]
                        if d["salary"]:
                            item["salary"] = d["salary"]
                        if d["company"]:
                            item["company"] = d["company"]
                    else:
                        item["description"] = ""
                    items.append(item)
                    checkpoint("boss", items, {"query": q, "city": city})
                else:
                    items.append(item)
            if len(cards) < 10:
                break
    checkpoint("boss", items, {"queries": queries, "city": city})
    return items


# ---------- BOSS assist 模式：用户手工导航 + 脚本零导航纯只读 ----------
#
# 实测边界（2026-11-20）：BOSS 对 CDP 发起的导航全站弹 about:blank（连首页都拦），
# 但对浏览器自身打开的标签页，CDP **只读**（query_selector/content）完全正常。
# 因此本模式不做任何反检测对抗：导航与翻页由用户亲手完成，脚本只读取已渲染
# 的 DOM——这是蓝图「用户主动触发、只读公开页」立场的浏览器化形态。


def _boss_find_search_tab(ctx):
    for p in ctx.pages:
        if "/web/geek/job" in p.url or "/web/geek/jobs" in p.url:
            return p
    return None


def boss_assist_harvest(pw, browser, max_jobs: int, idle_timeout_s: int = 600) -> list[dict]:
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    items: list[dict] = []
    seen: set[str] = set()
    last_sig = ""
    print("\n" + "=" * 62)
    print("🤝 BOSS 协作采集模式（你操作浏览器，我只读取，绝不点击）")
    print("  1. 在自动化 Edge 窗口里，亲手打开 BOSS 搜索页（登录后搜索，")
    print("     例如搜索「AI产品经理」）；")
    print("  2. 每翻一页，脚本 3 秒内自动提取当前页卡片（左下角有日志）；")
    print("  3. 采够了在终端按 Ctrl+C 结束（暂存实时保存，随时可停）。")
    print("=" * 62 + "\n")
    deadline = time.time() + idle_timeout_s
    while len(items) < max_jobs and time.time() < deadline:
        time.sleep(3)
        tab = _boss_find_search_tab(ctx)
        if tab is None:
            continue
        deadline = time.time() + idle_timeout_s  # 见到搜索页就续期
        try:
            cards = boss_extract_cards(tab)  # 只读：不 goto、不点击
        except Exception:
            continue
        if not cards:
            continue
        # 以「URL+首卡链接」做页签名，翻页/换词后签名变化才重复提取
        sig = tab.url.split("&page=")[0] + "|" + (cards[0]["url"] if cards else "")
        if sig == last_sig:
            continue
        last_sig = sig
        added = 0
        for c in cards:
            if len(items) >= max_jobs:
                break
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            c["source_job_id"] = c["url"].rstrip("/").split("/")[-1].replace(".html", "")
            items.append(c)
            added += 1
        if added:
            checkpoint("boss", items, {"mode": "assist"})
            log(f"  +{added} 条（累计 {len(items)}）——可以翻下一页了")
    checkpoint("boss", items, {"mode": "assist"})
    log(f"assist 采集结束：{len(items)} 条（列表卡片；JD 正文可在详情页单条补充）")
    return items


# ---------- 实习僧 ----------


def shixiseng_login_ok(page) -> bool:
    return "请登录" not in safe_content(page)[:6000] and "login" not in page.url


def shixiseng_detail_text(page) -> str:
    """详情正文：候选块里取最长 innerText（登录墙折叠时可能偏短，如实入库）。"""
    js = """
    () => {
      const sels = ['.job_detail', '.con-job', '.job-content', '.com-detail',
                    '[class*="detail-content"]', '[class*="job-desc"]', '.intern-detail'];
      let best = '';
      for (const s of sels) {
        document.querySelectorAll(s).forEach(el => {
          const t = (el.innerText || '').trim();
          if (t.length > best.length) best = t;
        });
      }
      return best.slice(0, 20000);
    }
    """
    try:
        return page.evaluate(js)
    except Exception:
        return ""


def shixiseng_cards(page) -> list[dict]:
    """实习僧专用解析（卡片结构实测 2026-11-20）：

    行结构：{标题} {PUA字形薪资}/天 \\n {城市} | {N}天/周 | {M}个月 \\n {公司} \\n 行业/经验 \\n 标签…
    薪资数字用私有区字形反爬（\\ue000-\\uf8ff）——解码即字体逆向军备竞赛，
    如实放弃（薪资空，ingest 的 accept_incomplete_salary 兜底）；
    公司 = 「天/周」行的下一行（实测稳定）。
    """
    import re as _re

    try:
        raw = page.evaluate(_anchor_extract_js("/intern/inn", "https://www.shixiseng.com"))
    except Exception:
        return []
    out = []
    for r in raw or []:
        href = r.get("href", "")
        title_raw = r.get("title", "")
        card_text = r.get("cardText", "")
        if not href or not title_raw:
            continue
        title = _re.split(r"[\ue000-\uf8ff]", title_raw)[0].strip()
        if not title:
            continue
        lines = [ln.strip() for ln in card_text.split("\n") if ln.strip()]
        city = company = ""
        for i, ln in enumerate(lines):
            if "天/周" in ln:
                city = ln.split("|")[0].strip()
                if i + 1 < len(lines):
                    company = lines[i + 1].strip()[:40]
                break
        out.append({"title": title, "company": company, "salary": "",
                    "city": city, "url": href})
    return out


def harvest_shixiseng(page, queries: list[str], max_jobs: int,
                      max_pages: int, detail: bool) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        for pg in range(1, max_pages + 1):
            if len(items) >= max_jobs:
                break
            url = f"https://www.shixiseng.com/interns/?keyword={quote(q)}&page={pg}"
            log(f"搜索页：{q} · 第{pg}页")
            page.goto(url, wait_until="domcontentloaded")
            settle(page, wait_cards="a[href*='/intern/inn']")
            if abort_if_captcha(page, "shixiseng"):
                return items
            cards = shixiseng_cards(page)
            log(f"  解析到 {len(cards)} 张卡片")
            for card in cards:
                if len(items) >= max_jobs:
                    break
                if card["url"] in seen:
                    continue
                seen.add(card["url"])
                item = dict(card)
                item["source_job_id"] = card["url"].rstrip("/").split("/")[-1]
                if detail:
                    log(f"  详情：{card['title'][:24]} @ {card['company']}")
                    try:
                        page.goto(card["url"], wait_until="domcontentloaded")
                        settle(page)
                    except Exception:
                        item["description"] = ""
                        items.append(item)
                        checkpoint("shixiseng", items, {"queries": queries})
                        continue
                    if abort_if_captcha(page, "shixiseng"):
                        item["description"] = ""
                    else:
                        item["description"] = shixiseng_detail_text(page)
                items.append(item)
                checkpoint("shixiseng", items, {"queries": queries})
            if len(cards) < 10:
                break
    checkpoint("shixiseng", items, {"queries": queries})
    return items


# ---------- 主流程 ----------


def main() -> int:
    ap = argparse.ArgumentParser(description="BOSS/实习僧 浏览器采集（只读，绝不代投）")
    ap.add_argument("--site", choices=["boss", "shixiseng"], default="boss")
    ap.add_argument("--queries", nargs="+", default=["AI产品经理"])
    ap.add_argument("--city", default="全国", help="仅 boss：城市名（见 BOSS_CITIES）")
    ap.add_argument("--max-jobs", type=int, default=40, help="单批硬顶")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--list-only", action="store_true", help="不进详情页（无 JD 正文）")
    ap.add_argument("--assist", action="store_true",
                    help="BOSS 协作模式：用户手工导航/翻页，脚本零导航只读提取（绕开全站 CDP 导航拦截的正道）")
    ap.add_argument("--probe", action="store_true", help="选择器探针：存页面快照，不采集")
    ap.add_argument("--launch-only", action="store_true", help="只起浏览器（用于首次扫码登录）")
    args = ap.parse_args()

    HARVEST_ROOT.mkdir(parents=True, exist_ok=True)
    if args.launch_only:
        launch_edge(args.site)
        log("浏览器已起。手工完成登录后，下次运行采集脚本会直接复用会话。")
        return 0

    launch_edge(args.site)
    time.sleep(3)
    pw, browser, page = connect()

    try:
        if args.probe:
            url = ("https://www.zhipin.com/web/geek/job?query=" + quote(args.queries[0])
                   if args.site == "boss"
                   else "https://www.shixiseng.com/interns/?keyword=" + quote(args.queries[0]))
            page.goto(url, wait_until="domcontentloaded")
            human_pause()
            out = HARVEST_ROOT / args.site / "probe.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            html = safe_content(page)
            out.write_text(html, encoding="utf-8")
            log(f"快照已存 {out}（{len(html)} 字符）——用于选择器适配")
            return 0

        # BOSS 对 CDP 导航全站拦截（about:blank），唯一正道是协作模式：
        # 导航/翻页由用户亲手完成，脚本只读已渲染 DOM（实测可行）
        if args.site == "boss" and args.assist:
            try:
                items = boss_assist_harvest(pw, browser, args.max_jobs)
            except KeyboardInterrupt:
                log("收到中断——暂存已实时保存")
                items = []
            log(f"assist 批次：{HARVEST_ROOT / 'boss' / 'staging.json'}（过目后才入库）")
            return 0

        # 登录检查（BOSS 必须登录才可见搜索结果；实习僧列表可匿名，详情常需登录）
        if args.site == "boss" and not boss_login_ok(page):
            if not boss_wait_login(page):
                log("⛔ 未在时限内完成登录，退出（未采集任何数据）")
                return 2
        if args.site == "shixiseng":
            page.goto("https://www.shixiseng.com/", wait_until="domcontentloaded")
            human_pause(2, 4)
            if not shixiseng_login_ok(page):
                log("⚠️ 实习僧未登录：列表可采，详情可能受限。可 --launch-only 先登录。")

        if args.site == "boss":
            items = harvest_boss(page, args.queries, args.city, args.max_jobs,
                                 args.max_pages, detail=not args.list_only,
                                 ensure_login=boss_wait_login)
        else:
            items = harvest_shixiseng(page, args.queries, args.max_jobs,
                                      args.max_pages, detail=not args.list_only)
        with_jd = sum(1 for i in items if i.get("description"))
        log(f"采集完成：{len(items)} 条（含 JD 正文 {with_jd} 条）→ "
            f"{HARVEST_ROOT / args.site / 'staging.json'}")
        log("下一步：暂存需过目后才经 job-hater import 入库（绝不直接导入）。")
        return 0
    finally:
        # 不断开浏览器：会话 cookie 留给下次采集；只断 CDP 连接
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
