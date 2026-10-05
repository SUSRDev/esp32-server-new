"""公式/化学式显示与口语化：走 C++ /formula。"""
from __future__ import annotations

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DESC = {
    "type": "function",
    "function": {
        "name": "formula_render",
        "description": (
            "把数学公式或化学式整理成可显示符号（×÷√→下标）并给出口语读法。"
            "用户说怎么读这个公式、显示化学式、H2O、方程表达式时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "公式或化学式原文，如 H2O、a^2+b^2、N2+O2"},
            },
            "required": ["text"],
        },
    },
}


def _gateway(path, params):
    for base in ("http://127.0.0.1:18081", "http://172.17.0.1:18081", "http://host.docker.internal:18081"):
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=4)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


@register_function("formula_render", DESC, ToolType.SYSTEM_CTL)
def formula_render(conn, text: str = ""):
    text = (text or "").strip()
    if not text:
        return ActionResponse(Action.RESPONSE, "missing", "你要我读哪个公式？")
    body = _gateway("/formula", {"text": text})
    if not body or body.get("status") != "ok":
        return ActionResponse(Action.RESPONSE, "fail", text)
    display = body.get("display") or text
    spoken = body.get("spoken") or text
    tip = f"{display}。读作：{spoken}"
    conn.logger.bind(tag=TAG).info(f"formula {text} -> {display}")
    return ActionResponse(Action.RESPONSE, tip, tip)
