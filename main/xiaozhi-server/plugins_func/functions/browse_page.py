"""打开网页/抓取页面摘要：走 C++ /fetch，并可用搜索定位链接。"""
from __future__ import annotations

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DESC = {
    "type": "function",
    "function": {
        "name": "browse_page",
        "description": (
            "打开网页并摘要内容，或按关键词先搜索再打开首条结果。"
            "用户说打开网页、浏览网站、访问链接、搜一下并打开时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL，可空"},
                "query": {"type": "string", "description": "没有 URL 时的搜索关键词"},
            },
            "required": [],
        },
    },
}


def _gateway(path, params, timeout=6):
    for base in ("http://127.0.0.1:18081", "http://172.17.0.1:18081", "http://host.docker.internal:18081"):
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


@register_function("browse_page", DESC, ToolType.SYSTEM_CTL)
def browse_page(conn, url: str = "", query: str = ""):
    url = (url or "").strip()
    query = (query or "").strip()
    if not url and query:
        search = _gateway("/search", {"q": query, "limit": 3})
        items = (search or {}).get("results") or []
        if not items:
            return ActionResponse(Action.RESPONSE, "no result", f"没搜到和「{query}」相关的网页")
        url = (items[0].get("url") or "").strip()
        title = items[0].get("title") or query
        if not url:
            return ActionResponse(Action.RESPONSE, "no url", f"搜到了「{title}」但没有可打开的链接")
    if not url:
        return ActionResponse(Action.RESPONSE, "missing", "给我一个网址，或说要搜什么")

    body = _gateway("/fetch", {"url": url}, timeout=8)
    if not body or body.get("status") != "ok":
        # fallback: just return search snippet if we had query
        return ActionResponse(
            Action.RESPONSE,
            "fetch failed",
            f"网页打开失败了：{url}。你可以换个关键词让我再搜。",
        )
    text = (body.get("text") or "").strip()
    if len(text) > 320:
        text = text[:320] + "…"
    tip = f"我打开了网页，摘要如下：{text}" if text else f"打开了 {url}，但没读到正文"
    conn.logger.bind(tag=TAG).info(f"browse_page url={url}")
    return ActionResponse(Action.RESPONSE, tip, tip)
