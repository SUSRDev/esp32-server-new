#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务端操作技能：通过宿主机 ops 网关执行白名单指令。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

OPS_URLS = (
    "http://172.19.0.1:10009/api/ops",
    "http://172.17.0.1:10009/api/ops",
    "http://172.18.0.1:10009/api/ops",
)

SERVER_CTRL_DESC = {
    "type": "function",
    "function": {
        "name": "server_control",
        "description": (
            "操作小智服务端/服务器。用户说服务器状态、重启服务、清音乐缓存、看服务日志、"
            "磁盘占用、还剩多少G、内存占用、重启小智、清理缓存时必须调用。"
            "action: status/restart/logs/clear_music_cache/disk/memory/containers。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "status|restart|logs|clear_music_cache|disk|memory|containers"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "可选参数。logs 时可填行数如 50；restart 可填服务名",
                },
            },
            "required": ["action"],
        },
    },
}


def _call_ops(action: str, value: str = "") -> dict:
    last_err = None
    payload = {"action": action, "value": value or ""}
    for url in OPS_URLS:
        try:
            r = requests.post(url, json=payload, timeout=4)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return {"ok": True, "text": r.text[:2000]}
            last_err = f"{url} HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = f"{url}: {e}"
            continue
    raise RuntimeError(last_err or "ops gateway unreachable")


def _parse_root_disk(text: str) -> str:
    """从 df 输出提炼口语摘要。"""
    # 优先 /dev/sda1 挂载 /
    for line in (text or "").splitlines():
        if not line or line.startswith("Filesystem") or line.startswith("==="):
            continue
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "/":
            size, used, avail, pct = parts[1], parts[2], parts[3], parts[4]
            return f"系统盘总共{size}，已用{used}，还剩{avail}，大约占用{pct}"
    # 退而求其次：任意含 Size Used Avail 的行
    m = re.search(
        r"(/\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+%)\s+/",
        text or "",
    )
    if m:
        return f"系统盘总共{m.group(2)}，已用{m.group(3)}，还剩{m.group(4)}，大约占用{m.group(5)}"
    return ""


def _parse_memory(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip().startswith("Mem:"):
            parts = line.split()
            # Mem: total used free shared buff/cache available
            if len(parts) >= 7:
                return (
                    f"内存总共{parts[1]}，已用{parts[2]}，"
                    f"可用大约{parts[6]}"
                )
    return ""


def _speak_summary(action: str, text: str) -> str:
    text = text or ""
    if action == "disk":
        s = _parse_root_disk(text)
        cache = ""
        m = re.search(r"(\d+(?:\.\d+)?[KMGT]?)\s+/opt/xiaozhi-server/music/cache", text)
        if m:
            cache = f"。音乐缓存大约占了{m.group(1)}"
        return (s or "磁盘信息已拿到") + cache + "。空间还很充裕的话就别慌。"
    if action == "memory":
        s = _parse_memory(text)
        return (s or "内存信息已拿到") + "。"
    if action == "status":
        disk = _parse_root_disk(text)
        mem = _parse_memory(text)
        up = ""
        m = re.search(r"up\s+([^,]+)", text)
        if m:
            up = f"已运行{m.group(1).strip()}。"
        bits = [x for x in (up, mem, disk) if x]
        if bits:
            return "服务器正常。" + "".join(bits)
        return "服务器在线，核心服务正常。"
    if action == "clear_music_cache":
        return text.strip() or "缓存清理完成。"
    if action == "containers":
        names = re.findall(r"^(xiaozhi\S+)\s+(\S+(?:\s+\S+)*)", text, re.M)
        if names:
            return "容器：" + "；".join(f"{n} {s}" for n, s in names[:6])
        return "容器列表已拿到。"
    if action == "logs":
        return "日志已拉取，最近几行看服务正常。" if text else "暂时没拉到日志。"
    return text[:180]


@register_function("server_control", SERVER_CTRL_DESC, ToolType.SYSTEM_CTL)
def server_control(conn, action: str, value: str = ""):
    action = (action or "").strip().lower()
    value = "" if value is None else str(value).strip()
    alias = {
        "状态": "status",
        "服务器状态": "status",
        "重启": "restart",
        "重启服务": "restart",
        "重启小智": "restart",
        "日志": "logs",
        "看日志": "logs",
        "清缓存": "clear_music_cache",
        "清理缓存": "clear_music_cache",
        "清音乐缓存": "clear_music_cache",
        "磁盘": "disk",
        "内存": "memory",
        "容器": "containers",
    }
    action = alias.get(action, action)
    if action not in (
        "status",
        "restart",
        "logs",
        "clear_music_cache",
        "disk",
        "memory",
        "containers",
    ):
        return ActionResponse(
            Action.RESPONSE,
            "失败",
            "支持：服务器状态、重启服务、看日志、清音乐缓存、磁盘、内存、容器列表",
        )

    try:
        data = _call_ops(action, value)
    except Exception as e:
        try:
            conn.logger.bind(tag=TAG).error(f"server_control fail: {e}")
        except Exception:
            pass
        return ActionResponse(
            Action.RESPONSE,
            "失败",
            f"服务端操作失败了：{e}",
        )

    text = data.get("text") or data.get("message") or json.dumps(data, ensure_ascii=False)
    ok = data.get("ok", True)
    try:
        conn.logger.bind(tag=TAG).info(f"server_control {action} ok={ok}")
    except Exception:
        pass

    if action == "restart":
        return ActionResponse(
            Action.RESPONSE,
            "ok",
            "好的，已经在重启小智服务了，稍等十几秒再连。",
        )

    spoken = _speak_summary(action, str(text))
    return ActionResponse(Action.RESPONSE, "ok", spoken)
