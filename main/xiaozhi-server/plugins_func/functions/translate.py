"""翻译：走本机 C++ Agent 网关 /translate。"""
from __future__ import annotations

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DESC = {
    "type": "function",
    "function": {
        "name": "translate",
        "description": "中英互译。用户说翻译成英文/中文、这句话英文怎么说时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的文本"},
                "to": {
                    "type": "string",
                    "description": "目标语言：en 或 zh，默认 en",
                },
            },
            "required": ["text"],
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


@register_function("translate", DESC, ToolType.SYSTEM_CTL)
def translate(conn, text: str = "", to: str = "en"):
    text = (text or "").strip()
    to = (to or "en").strip() or "en"
    if not text:
        return ActionResponse(Action.RESPONSE, "missing", "你要翻译哪句？")
    body = _gateway("/translate", {"text": text, "to": to})
    if not body or body.get("status") != "ok":
        return ActionResponse(Action.RESPONSE, "translate failed", "翻译暂时不可用")
    translated = (body.get("translated") or "").strip()
    tip = f"翻译结果：{translated}" if translated else "没翻出来"
    conn.logger.bind(tag=TAG).info(f"translate to={to} ok")
    return ActionResponse(Action.RESPONSE, tip, tip)
