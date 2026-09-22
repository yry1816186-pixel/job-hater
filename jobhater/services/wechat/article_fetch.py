"""公众号文章正文深挖（卡片只带标题摘要，正文里有完整 JD）。

从命中里的 ``apply_method`` 链接收集唯一 URL，并发抓取（限速防反爬），
提取 SSR 页面正文（``js_content`` 区块），返回 ``{url: Article}``。
失败静默降级——增强通道不阻断主扫描。
"""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_TIMEOUT_S = 12
_MAX_TEXT = 6000

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
# js_content 区块到文档尾部/最后的 script 之间全取（尾部版权声明等噪声由阈值过滤）；
# 严格要求紧跟闭合标签在真实页面结构变化时会整体失配
_CONTENT_RE = re.compile(r'<div[^>]*id="js_content"[^>]*>(.*)', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _requests():
    """requests 惰性导入：缺失时（最小安装环境）本通道降级为不可用。"""
    import requests  # noqa: PLC0415  # 可选依赖，避免成为全局硬依赖

    return requests


@dataclass
class Article:
    """一篇抓到的文章正文。"""

    url: str
    title: str
    text: str


def extract_article(doc: str) -> tuple[str, str]:
    """SSR 文档 → (标题, 正文纯文本)；非文章页返回 ("", "")。"""
    tm = _TITLE_RE.search(doc)
    t = html.unescape(tm.group(1)).strip() if tm else ""
    m = _CONTENT_RE.search(doc)
    if not m:
        return t, ""
    body = m.group(1)
    # 尾部 script/style 区块整段剔除（贪婪匹配可能带入）
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<br[^>]*>", "\n", body)
    body = re.sub(r"</p>", "\n", body)
    body = _TAG_RE.sub(" ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return t, body


def _fetch_one(url: str) -> Article | None:
    try:
        rq = _requests()
        r = rq.get(url, headers={"User-Agent": UA}, timeout=_TIMEOUT_S)
        if "环境异常" in r.text:
            return None
        title, text = extract_article(r.text)
        if len(text) > 60:
            return Article(url=url, title=title[:120], text=text[:_MAX_TEXT])
    except ImportError:
        return None
    except Exception:  # noqa: BLE001  # 网络增强通道：任何请求失败静默跳过
        return None
    return None


def collect_urls(hits: list[dict], *, limit: int = 2500) -> list[str]:
    """从命中列表收集去重的文章 URL（apply_method 链接），按关联命中置信度降序。

    ``limit``：深挖上限（全量约 5 千篇需 ~15 分钟，限量控制单次扫描时长；
    高置信命中的文章优先，覆盖绝大多数有效信息）。
    """
    scored: dict[str, float] = {}
    for h in hits:
        am = h.get("apply_method") or ""
        if am.startswith("链接 "):
            u = am[3:].strip()
            if "mp.weixin.qq.com/s" in u:
                scored[u] = max(scored.get(u, 0.0), h.get("confidence", 0.0))
    ranked = sorted(scored, key=lambda u: -scored[u])
    return ranked[:limit]


def fetch_articles(urls: list[str], *, workers: int = 6, progress=None) -> dict[str, Article]:
    """并发抓取（每任务微延迟限速）。返回 {url: Article}，失败项不在结果里。"""
    import time

    out: dict[str, Article] = {}
    if not urls:
        return out
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_one, u): u for u in urls}
        for fut in as_completed(futs):
            done += 1
            art = fut.result()
            if art is not None:
                out[art.url] = art
            if progress and done % 50 == 0:
                progress({"articles": done, "fetched": len(out)})
            time.sleep(0.04)
    return out
