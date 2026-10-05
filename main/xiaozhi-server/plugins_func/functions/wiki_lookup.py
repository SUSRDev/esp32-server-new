"""百科查询：走本机 C++ Agent 网关 /wiki。"""
from __future__ import annotations

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DESC = {
    "type": "function",
    "function": {
        "name": "wiki_lookup",
        "description": "查百科词条简介。用户问某某是谁、什么是xxx、介绍一下xxx时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "词条/人物/概念名称"},
            },
            "required": ["query"],
        },
    },
}


def _gateway(path, params):
    for base in ("http://127.0.0.1:18081", "http://172.17.0.1:18081", "http://host.docker.internal:18081"):
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=6)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


@register_function("wiki_lookup", DESC, ToolType.SYSTEM_CTL)
def wiki_lookup(conn, query: str = ""):
    query = (query or "").strip()
    if not query:
        return ActionResponse(Action.RESPONSE, "missing", "你想查哪个词条？")
    body = _gateway("/wiki", {"q": query})
    if not body or body.get("status") != "ok":
        return ActionResponse(Action.RESPONSE, "wiki failed", f"没查到「{query}」的百科简介")
    title = body.get("title") or query
    extract = (body.get("extract") or "").strip()
    if len(extract) > 280:
        extract = extract[:280] + "…"
    text = f"{title}：{extract}" if extract else f"找到词条{title}，但没有简介"
    conn.logger.bind(tag=TAG).info(f"wiki {query} ok")
    return ActionResponse(Action.RESPONSE, text, text)
