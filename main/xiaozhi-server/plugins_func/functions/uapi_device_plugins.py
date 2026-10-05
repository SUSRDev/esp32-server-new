#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UAPI / 聚合能力封装：搜索、热榜、天气、IP、翻译。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__
UAPI_BASE = "https://uapis.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 XiaozhiAgent/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def _get(path: str, params: Optional[dict] = None, timeout: int = 12) -> Any:
    r = requests.get(UAPI_BASE + path, params=params or {}, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict, timeout: int = 20) -> Any:
    r = requests.post(UAPI_BASE + path, json=body, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def uapi_search(query: str, limit: int = 5) -> List[Dict[str, str]]:
    data = _post(
        "/api/v1/search/aggregate",
        {"query": query, "limit": max(1, min(int(limit or 5), 10))},
    )
    out = []
    for it in data.get("results") or []:
        title = (it.get("title") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        url = (it.get("url") or "").strip()
        if title or snippet:
            out.append({"title": title, "snippet": snippet[:220], "url": url})
    return out


HOTBOARD_DESC = {
    "type": "function",
    "function": {
        "name": "get_hotboard",
        "description": (
            "查询网络热榜/热搜。用户说热搜、热榜、微博热搜、今日热点时调用。"
            "type 可选：weibo/zhihu/baidu/douyin/bilibili/toutiao，默认 weibo。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "热榜来源：weibo/zhihu/baidu/douyin/bilibili/toutiao",
                },
                "limit": {"type": "integer", "description": "返回条数，默认8"},
            },
            "required": [],
        },
    },
}


@register_function("get_hotboard", HOTBOARD_DESC, ToolType.SYSTEM_CTL)
def get_hotboard(conn, type: str = "weibo", limit: int = 8):
    src = (type or "weibo").strip().lower() or "weibo"
    alias = {
        "微博": "weibo",
        "知乎": "zhihu",
        "百度": "baidu",
        "抖音": "douyin",
        "b站": "bilibili",
        "哔哩哔哩": "bilibili",
        "头条": "toutiao",
    }
    src = alias.get(src, src)
    try:
        limit = max(1, min(int(limit or 8), 15))
    except Exception:
        limit = 8
    try:
        data = _get("/api/v1/misc/hotboard", {"type": src})
        items = []
        if isinstance(data, dict):
            items = data.get("list") or data.get("data") or data.get("results") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        lines = [f"{src}热榜："]
        n = 0
        if isinstance(items, list):
            for it in items:
                if n >= limit:
                    break
                if isinstance(it, dict):
                    title = it.get("title") or it.get("name") or it.get("word") or ""
                    hot = it.get("hot_value") or it.get("hot") or it.get("heat") or ""
                else:
                    title, hot = str(it), ""
                title = str(title).strip()
                if not title:
                    continue
                n += 1
                extra = f"（{hot}）" if hot else ""
                lines.append(f"{n}. {title}{extra}")
        if n == 0:
            return ActionResponse(
                Action.REQLLM,
                f"热榜接口返回了数据但解析为空，原始摘要：{json.dumps(data, ensure_ascii=False)[:400]}",
                None,
            )
        lines.append("请用口语简洁播报前几条热点，不要念链接。")
        try:
            conn.logger.bind(tag=TAG).info(f"hotboard {src} n={n}")
        except Exception:
            pass
        return ActionResponse(Action.REQLLM, "\n".join(lines), None)
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"热榜暂时不可用：{e}", None)


IPINFO_DESC = {
    "type": "function",
    "function": {
        "name": "lookup_ip",
        "description": "查询IP归属地/网络信息。用户问IP是哪里、查IP、我的网络位置时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "IP地址；空则查本机出口IP"},
            },
            "required": [],
        },
    },
}


@register_function("lookup_ip", IPINFO_DESC, ToolType.SYSTEM_CTL)
def lookup_ip(conn, ip: str = ""):
    try:
        if (ip or "").strip():
            data = _get("/api/v1/network/ipinfo", {"ip": ip.strip()})
        else:
            data = _get("/api/v1/network/myip")
        text = json.dumps(data, ensure_ascii=False)
        return ActionResponse(
            Action.REQLLM,
            f"IP查询结果：{text}\n请用口语简短告诉用户位置与运营商，不要念JSON。",
            None,
        )
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"IP查询失败：{e}", None)


RECIPE_DESC = {
    "type": "function",
    "function": {
        "name": "search_recipe",
        "description": "搜索菜谱。用户问怎么做某道菜、菜谱、食谱时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "dish": {"type": "string", "description": "菜名，如红烧肉"},
            },
            "required": ["dish"],
        },
    },
}


@register_function("search_recipe", RECIPE_DESC, ToolType.SYSTEM_CTL)
def search_recipe(conn, dish: str):
    dish = (dish or "").strip()
    if not dish:
        return ActionResponse(Action.RESPONSE, "失败", "请告诉我要查哪道菜")
    try:
        data = _get("/api/v1/food/recipe", {"q": dish, "keyword": dish, "name": dish})
        return ActionResponse(
            Action.REQLLM,
            f"菜谱搜索「{dish}」结果：{json.dumps(data, ensure_ascii=False)[:1200]}\n请口语简述做法要点。",
            None,
        )
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"菜谱搜索失败：{e}", None)


DEVICE_CTRL_DESC = {
    "type": "function",
    "function": {
        "name": "device_control",
        "description": (
            "控制或查询本机设备。支持：查状态/电量/音量/网络；调音量；调亮度；"
            "开音乐/游戏/小说/图片/文件/网络/亮度/设置/录音界面；切换深色浅色主题；"
            "开关气泡样式、深度休眠、实时打断。"
            "用户说音量调到50、打开设置、现在电量多少、把屏幕调亮时必须调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "动作：status/volume/brightness/theme/wallpaper/"
                        "open_music/open_games/open_novels/open_images/open_files/"
                        "open_network/open_brightness/open_settings/open_recording/"
                        "chat_style/deep_sleep/realtime_interrupt/sdcard"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "参数值。音量/亮度0-100；主题light/dark；开关true/false；壁纸文件名可空",
                },
            },
            "required": ["action"],
        },
    },
}


def _mcp_call(conn, tool_name: str, arguments: dict | None = None):
    """同步包装调用设备端 MCP 工具。"""
    import asyncio

    arguments = arguments or {}
    mcp = getattr(conn, "mcp_client", None)
    if mcp is None:
        raise RuntimeError("设备MCP未就绪")

    # 兼容 name_mapping：优先原名，再试 sanitized
    candidates = [tool_name, tool_name.replace(".", "_")]
    chosen = None
    for name in candidates:
        try:
            if mcp.has_tool(name):
                chosen = name
                break
        except Exception:
            continue
    if not chosen:
        # 再扫一遍 mapping 值
        try:
            for san, raw in (getattr(mcp, "name_mapping", {}) or {}).items():
                if raw == tool_name or san == tool_name:
                    chosen = san
                    break
        except Exception:
            pass
    if not chosen:
        raise RuntimeError(f"设备不支持工具 {tool_name}")

    from core.handle.mcpHandle import call_mcp_tool

    fut = asyncio.run_coroutine_threadsafe(
        call_mcp_tool(conn, mcp, chosen, arguments, timeout=30),
        conn.loop,
    )
    return fut.result(timeout=35)


@register_function("device_control", DEVICE_CTRL_DESC, ToolType.SYSTEM_CTL)
def device_control(conn, action: str, value: str = ""):
    action = (action or "").strip().lower()
    value = "" if value is None else str(value).strip()

    mapping = {
        "status": ("self.get_device_status", {}),
        "sdcard": ("self.sdcard.get_status", {}),
        "open_music": ("self.ui.open_music", {}),
        "open_games": ("self.ui.open_games", {}),
        "open_novels": ("self.ui.open_novels", {}),
        "open_images": ("self.ui.open_images", {}),
        "open_files": ("self.ui.open_files", {}),
        "open_network": ("self.ui.open_network", {}),
        "open_brightness": ("self.ui.open_brightness", {}),
        "open_settings": ("self.ui.open_settings", {}),
        "open_recording": ("self.ui.open_recording", {}),
    }

    try:
        if action in mapping:
            tool, args = mapping[action]
            result = _mcp_call(conn, tool, args)
            return ActionResponse(
                Action.REQLLM,
                f"设备操作 {action} 结果：{result}\n请用口语简短反馈用户。",
                None,
            )

        if action in ("volume", "set_volume"):
            vol = int(float(value)) if value else 50
            vol = max(0, min(100, vol))
            result = _mcp_call(conn, "self.audio_speaker.set_volume", {"volume": vol})
            return ActionResponse(Action.RESPONSE, "ok", f"好的，音量已调到{vol}")

        if action in ("brightness", "set_brightness"):
            bri = int(float(value)) if value else 70
            bri = max(0, min(100, bri))
            result = _mcp_call(conn, "self.screen.set_brightness", {"brightness": bri})
            return ActionResponse(Action.RESPONSE, "ok", f"好的，亮度已调到{bri}")

        if action == "theme":
            theme = value if value in ("light", "dark") else ("dark" if "暗" in value or "深" in value else "light")
            _mcp_call(conn, "self.screen.set_theme", {"theme": theme})
            return ActionResponse(Action.RESPONSE, "ok", f"已切换到{'深色' if theme=='dark' else '浅色'}主题")

        if action == "wallpaper":
            args = {"filename": value} if value else {}
            _mcp_call(conn, "self.screen.set_wallpaper", args)
            return ActionResponse(Action.RESPONSE, "ok", "壁纸已切换")

        if action == "chat_style":
            enable = value.lower() in ("1", "true", "yes", "on", "开", "打开", "开启")
            _mcp_call(conn, "self.settings.set_chat_style", {"enable": enable})
            return ActionResponse(Action.RESPONSE, "ok", "气泡样式已" + ("打开" if enable else "关闭"))

        if action == "deep_sleep":
            enable = value.lower() in ("1", "true", "yes", "on", "开", "打开", "开启")
            _mcp_call(conn, "self.settings.set_deep_sleep", {"enable": enable})
            return ActionResponse(Action.RESPONSE, "ok", "深度休眠已" + ("开启" if enable else "关闭"))

        if action == "realtime_interrupt":
            enable = value.lower() in ("1", "true", "yes", "on", "开", "打开", "开启")
            _mcp_call(conn, "self.settings.set_realtime_interrupt", {"enable": enable})
            return ActionResponse(Action.RESPONSE, "ok", "实时打断已" + ("开启" if enable else "关闭"))

        return ActionResponse(
            Action.RESPONSE,
            "失败",
            "支持：查状态、调音量、调亮度、开设置/游戏/音乐、换主题等。你想做什么？",
        )
    except Exception as e:
        try:
            conn.logger.bind(tag=TAG).error(f"device_control failed: {e}")
        except Exception:
            pass
        return ActionResponse(Action.RESPONSE, "失败", f"设备操作失败了：{e}")
