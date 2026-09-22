"""JD 补全引擎 API 发现探针（B1 前置调研，可复现证据）。

用 Playwright 真浏览器加载各官方招聘站详情页，抓网络层 XHR/fetch，
输出 method + URL 模板 + 响应 JSON 顶层形状——provider 实现的唯一可信依据。

用法：python scripts/probe_jd_apis.py [label:url ...]
不带参数时跑默认目标集。只读公开页，无登录、无写入。
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

TARGETS: dict[str, str] = {
    "bytedance": "https://jobs.bytedance.com/campus/position/7628896431840807173/detail",
    "xiaomi_feishu": "https://xiaomi.jobs.f.mioffice.cn/campus/position/7686764793517001010/detail",
    "zhiye": "https://polycareer.zhiye.com/job/detail?jobAdId=349a47fc-3db6-4c73-b536-ffc87de81a80",
    "mihoyo": "https://jobs.mihoyo.com/#/campus/position/7399",
    "baidu": "https://talent.baidu.com/jobs/detail/GRADUATE/79e85217-8204-4a5f-9b05-03b2bb2597bd",
    "netease163": "https://campus.163.com/app/job/position?id=103",
}

STATIC_EXT = (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".gif", ".ico", ".mp4")


def summarize(resp_body: str) -> str:
    """响应摘要：JSON → 顶层键与正文类字段；HTML → 长度。"""
    try:
        data = json.loads(resp_body)
    except (ValueError, TypeError):
        return f"html/text len={len(resp_body)}"
    if isinstance(data, dict):
        keys = list(data.keys())[:12]
        # 深挖一层找 description/content 字段
        hints = []
        stack = [("", data)]
        while stack and len(hints) < 6:
            path, node = stack.pop(0)
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str) and len(v) > 120 and any(
                        w in k.lower() for w in ("desc", "content", "require", "respons")
                    ):
                        hints.append(f"{path}.{k} len={len(v)}")
                    elif isinstance(v, (dict, list)):
                        stack.append((f"{path}.{k}", v))
            elif isinstance(node, list) and node:
                stack.append((f"{path}[0]", node[0]))
        return f"json keys={keys}" + (f" | 正文字段: {hints}" if hints else "")
    return f"json({type(data).__name__})"


def probe(label: str, url: str) -> None:
    print(f"\n===== {label} : {url}")
    hits: list[tuple[str, str, int, str]] = []

    def on_response(resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            u = resp.url
            if any(u.split("?")[0].endswith(e) for e in STATIC_EXT):
                return
            body = resp.text()
            hits.append((req.method, u, resp.status, summarize(body)))
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        ))
        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"  (load note: {type(e).__name__}: {str(e)[:90]})")
        browser.close()

    seen: set[str] = set()
    for method, u, status, summary in hits:
        # 去掉 Same URL 重复（查询参数全保留，ID 是模板的一部分）
        sig = f"{method} {u}"
        if sig in seen:
            continue
        seen.add(sig)
        if status == 200 and "json" in summary:
            print(f"  {status} {method} {u}\n      {summary[:400]}")


def main() -> None:
    if len(sys.argv) > 1:
        for pair in sys.argv[1:]:
            label, _, url = pair.partition(":")
            probe(label, url)
    else:
        for label, url in TARGETS.items():
            probe(label, url)


if __name__ == "__main__":
    main()
