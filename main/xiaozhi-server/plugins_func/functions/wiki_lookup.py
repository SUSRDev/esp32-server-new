"""百科查询：优先本机网关，失败则直连维基百科。"""
from __future__ import annotations

import re
import requests
from urllib.parse import quote
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

_UA = {
    "User-Agent": "XiaozhiEsp32Bot/1.0 (wiki_lookup; +https://local)",
    "Accept": "application/json",
}

_ALIASES = {
    "野兽先生": "MrBeast",
    "mrbeast": "MrBeast",
    "mr beast": "MrBeast",
    "初音": "初音未来",
    "miku": "Hatsune Miku",
    "hatsune miku": "Hatsune Miku",
}


def _gateway(path, params):
    for base in (
        "http://127.0.0.1:18081",
        "http://172.17.0.1:18081",
        "http://host.docker.internal:18081",
    ):
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=6)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _wiki_rest(lang: str, title: str):
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title)}"
    try:
        r = requests.get(url, headers=_UA, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        extract = (data.get("extract") or "").strip()
        if not extract:
            return None
        return data.get("title") or title, extract
    except Exception:
        return None


def _wiki_opensearch(lang: str, query: str):
    try:
        r = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            headers=_UA,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, list) and len(data) > 1 and data[1]:
            return data[1][0]
    except Exception:
        return None
    return None


def _normalize_query(query: str) -> str:
    q = (query or "").strip()
    q = re.sub(r"^(什么是|谁是|介绍一下|介绍下|讲讲|说说)\s*", "", q)
    q = re.sub(r"(是谁|是什么|简介|人物介绍)[?？。!！]*$", "", q).strip()
    low = q.lower().strip()
    for k, v in _ALIASES.items():
        if low == k.lower() or q == k:
            return v
    return q


def _fetch_wikipedia(query: str):
    q = _normalize_query(query)
    if not q:
        return None
    for lang in ("zh", "en"):
        hit = _wiki_rest(lang, q)
        if hit:
            return hit
        title = _wiki_opensearch(lang, q)
        if title and title != q:
            hit = _wiki_rest(lang, title)
            if hit:
                return hit
    return None


@register_function("wiki_lookup", DESC, ToolType.SYSTEM_CTL)
def wiki_lookup(conn, query: str = ""):
    query = _normalize_query(query)
    if not query:
        return ActionResponse(Action.RESPONSE, "missing", "你想查哪个词条？")

    body = _gateway("/wiki", {"q": query})
    if body and body.get("status") == "ok":
        title = body.get("title") or query
        extract = (body.get("extract") or "").strip()
        if extract:
            if len(extract) > 280:
                extract = extract[:280] + "…"
            text = f"{title}：{extract}"
            try:
                conn.logger.bind(tag=TAG).info(f"wiki gateway {query} ok")
            except Exception:
                pass
            return ActionResponse(Action.RESPONSE, text, text)

    hit = _fetch_wikipedia(query)
    if hit:
        title, extract = hit
        if len(extract) > 280:
            extract = extract[:280] + "…"
        text = f"{title}：{extract}"
        try:
            conn.logger.bind(tag=TAG).info(f"wiki wikipedia {query} ok")
        except Exception:
            pass
        return ActionResponse(Action.RESPONSE, text, text)

    return ActionResponse(
        Action.REQLLM,
        (
            f"百科暂时查不到「{query}」。请口语简短告诉用户百科没找到，"
            f"建议再说「联网搜索{query}」；不要编造内容。"
        ),
        None,
    )
