"""公众号文章正文深挖（卡片只带标题摘要，正文里有完整 JD）。

从命中里的 ``apply_method`` 链接收集唯一 URL（白名单：urlsplit 解析后
hostname == mp.weixin.qq.com 且 https），并发抓取（限速防反爬），
提取 SSR 页面正文（``js_content`` 区块），返回 ``{url: Article}``。
失败降级不阻断主扫描——降级原因经结构化日志可观测（BC-009 地基）。

HTTP 客户端用 httpx（核心依赖，BC-005 迁移自未声明的 requests——
标准安装下导入 requests 必然失败，通道静默不可用与产品诚实原则相悖）。
"""
from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("jobhater.wechat.article_fetch")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_TIMEOUT_S = 12
_MAX_TEXT = 6000

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
# js_content 区块到文档尾部/最后的 script 之间全取（尾部版权声明等噪声由阈值过滤）；
# 严格要求紧跟闭合标签在真实页面结构变化时会整体失配
_CONTENT_RE = re.compile(r'<div[^>]*id="js_content"[^>]*>(.*)', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

_http_client: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """模块级惰性共享 Client（BC-005）：get(url, headers=...) 语义与 requests 一致，
    超时由 Client 统一（connect+read 均 _TIMEOUT_S），跟随重定向。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True)
    return _http_client


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
        r = _shared_client().get(url, headers={"User-Agent": UA})
        if "环境异常" in r.text:
            return None
        title, text = extract_article(r.text)
        if len(text) > 60:
            return Article(url=url, title=title[:120], text=text[:_MAX_TEXT])
    except Exception as e:  # noqa: BLE001  # 网络增强通道：任何失败不阻断，但原因必须可观测
        logger.warning(
            "article_fetch_unavailable",
            extra={"reason": type(e).__name__, "url": url, "detail": str(e)[:120]},
        )
        return None
    return None


def _is_weixin_article_url(u: str) -> bool:
    """SEC-006：白名单按 urlsplit 解析后校验（hostname 全等 + https）。

    子串判断 ``"mp.weixin.qq.com/s" in u`` 可被
    ``https://attacker.com/x?mp.weixin.qq.com/s`` 类链接绕过——聊天消息驱动的
    出站请求面必须校验真实目标主机。hostname 属性已小写化（大小写不敏感）。
    """
    try:
        parts = urlsplit(u)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname == "mp.weixin.qq.com"


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
            if _is_weixin_article_url(u):
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
