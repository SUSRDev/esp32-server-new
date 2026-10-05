"""数学计算：支持 + - × ÷ √ ^ 以及根号/平方。"""
from __future__ import annotations

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DESC = {
    "type": "function",
    "function": {
        "name": "calc",
        "description": (
            "数学计算。支持加减乘除、括号、百分号、乘方^、根号√/sqrt、绝对值abs。"
            "用户说算一下、等于多少、根号下、乘除时调用。可直接写 3×(2+1)、√16、2^8。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expr": {
                    "type": "string",
                    "description": "算术表达式，例如 √(9+7)、3×4÷2、2^10、abs(-5)",
                }
            },
            "required": ["expr"],
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


@register_function("calc", DESC, ToolType.SYSTEM_CTL)
def calc(conn, expr: str = ""):
    expr = (expr or "").strip()
    if not expr:
        return ActionResponse(Action.RESPONSE, "缺少表达式", "你要我算什么？")
    body = _gateway("/calc", {"expr": expr})
    if not body or body.get("status") != "ok":
        return ActionResponse(Action.RESPONSE, "calc failed", "这次算不出来，换个简单一点的式子试试")
    spoken = body.get("spoken") or ""
    display = body.get("display") or ""
    result = body.get("result")
    text = spoken or display or f"{expr} 等于 {result}"
    # 同时把符号版塞进回复，方便字幕显示
    if display and display not in text:
        text = f"{display}。{spoken}" if spoken else display
    conn.logger.bind(tag=TAG).info(f"calc {expr} -> {result} display={display}")
    return ActionResponse(Action.RESPONSE, text, text)
