#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只获取歌词 + 搜索歌曲信息（JuiceMusic，不播放）。"""
from __future__ import annotations

import re
from typing import Optional

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import juice_music

TAG = __name__

GET_LYRICS_DESC = {
    "type": "function",
    "function": {
        "name": "get_lyrics",
        "description": (
            "只获取歌曲歌词，不播放音乐。"
            "用户说查歌词、这首歌的歌词、把歌词念给我、只要歌词不要播放时必须调用。"
            "返回纯歌词文本供口头朗读或摘要。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {"type": "string", "description": "歌名，必填"},
                "artist": {"type": "string", "description": "歌手，可选"},
                "source": {
                    "type": "string",
                    "description": "音源：netease/bilibili/qq/kugou，默认网易云",
                },
                "choose": {
                    "type": "integer",
                    "description": "多结果时选第几首，从1开始；不填默认第1首",
                },
            },
            "required": ["song_name"],
        },
    },
}

SONG_INFO_DESC = {
    "type": "function",
    "function": {
        "name": "song_info",
        "description": (
            "搜索歌曲信息（歌名/歌手/专辑/时长/音源），不播放。"
            "用户问这首歌是谁唱的、歌曲信息、搜一下某首歌资料时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {"type": "string", "description": "歌名或关键词"},
                "artist": {"type": "string", "description": "歌手，可选"},
                "source": {
                    "type": "string",
                    "description": "音源：netease/bilibili/qq/kugou，默认网易云",
                },
                "limit": {"type": "integer", "description": "返回条数，默认5"},
            },
            "required": ["song_name"],
        },
    },
}


def _cfg(conn):
    music_cfg = {}
    try:
        plugins = (conn.config or {}).get("plugins") or {}
        raw = plugins.get("play_music")
        if isinstance(raw, dict):
            music_cfg = raw
        elif isinstance(raw, str) and raw.strip().startswith("{"):
            import json

            music_cfg = json.loads(raw)
    except Exception:
        music_cfg = {}
    return juice_music.resolve_config(music_cfg)


def _lrc_to_plain(lrc: str, max_lines: int = 80) -> str:
    lines = []
    for raw in (lrc or "").splitlines():
        text = re.sub(r"\[[^\]]*\]", "", raw).strip()
        if not text:
            continue
        # skip meta / yrc json fragments
        if text.startswith("{") or text.startswith("["):
            continue
        if '"t":' in text or '"c":' in text:
            continue
        if text.startswith(("ti:", "ar:", "al:", "by:", "offset:")):
            continue
        # skip pure punctuation
        if re.fullmatch(r"[\W_]+", text):
            continue
        lines.append(text)
        if len(lines) >= max_lines:
            break
    # de-dup consecutive
    out = []
    prev = None
    for t in lines:
        if t == prev:
            continue
        out.append(t)
        prev = t
    return "\n".join(out)


@register_function("get_lyrics", GET_LYRICS_DESC, ToolType.SYSTEM_CTL)
def get_lyrics(
    conn,
    song_name: str,
    artist: str = "",
    source: str = "",
    choose: Optional[int] = None,
):
    song_name = (song_name or "").strip()
    artist = (artist or "").strip()
    if not song_name:
        return ActionResponse(Action.RESPONSE, "失败", "请告诉我歌名，我才能查歌词")

    cfg = _cfg(conn)
    src = juice_music.normalize_source(source or cfg.get("source") or "netease")
    keyword = juice_music.build_search_keyword(song_name, artist)
    try:
        tracks, used = juice_music.search_tracks_multi(
            cfg["base"],
            cfg["api_key"],
            keyword,
            sources=[src, "netease", "bilibili"] if src else None,
            limit=8,
        )
    except Exception as e:
        try:
            conn.logger.bind(tag=TAG).error(f"get_lyrics search fail: {e}")
        except Exception:
            pass
        return ActionResponse(Action.REQLLM, f"搜歌失败：{e}。请告知用户暂时查不到歌词。", None)

    if not tracks:
        return ActionResponse(
            Action.REQLLM,
            f"没找到「{keyword}」相关歌曲，无法获取歌词。请让用户换歌名或加歌手。",
            None,
        )

    idx = 1
    try:
        if choose is not None:
            idx = int(choose)
    except Exception:
        idx = 1
    if idx < 1 or idx > len(tracks):
        idx = 1
    track = tracks[idx - 1]
    tid = juice_music.track_id_of(track)
    label = juice_music.track_label(track)
    if not tid:
        return ActionResponse(Action.REQLLM, f"找到了 {label}，但缺少曲目ID，拿不到歌词。", None)

    try:
        lrc = juice_music.fetch_lyric_lrc(cfg["base"], cfg["api_key"], tid)
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"获取「{label}」歌词失败：{e}", None)

    plain = _lrc_to_plain(lrc)
    if not plain:
        return ActionResponse(
            Action.REQLLM,
            f"「{label}」暂时没有歌词数据。可建议用户换音源或换版本。",
            None,
        )

    try:
        conn.logger.bind(tag=TAG).info(f"get_lyrics ok {label} lines={plain.count(chr(10))+1}")
    except Exception:
        pass

    # 只返回歌词，要求朗读/摘要，明确不要去播放
    body = (
        f"已获取「{label}」歌词（音源:{juice_music.source_label(used)}，仅歌词不播放）：\n"
        f"{plain}\n"
        f"请用口语告诉用户这是歌词；可摘要前几句或按用户要求朗读；禁止调用 play_music。"
    )
    return ActionResponse(Action.REQLLM, body, None)


@register_function("song_info", SONG_INFO_DESC, ToolType.SYSTEM_CTL)
def song_info(
    conn,
    song_name: str,
    artist: str = "",
    source: str = "",
    limit: int = 5,
):
    song_name = (song_name or "").strip()
    artist = (artist or "").strip()
    if not song_name:
        return ActionResponse(Action.RESPONSE, "失败", "请告诉我要查哪首歌")

    try:
        limit = max(1, min(int(limit or 5), 8))
    except Exception:
        limit = 5

    cfg = _cfg(conn)
    src = juice_music.normalize_source(source or cfg.get("source") or "netease")
    keyword = juice_music.build_search_keyword(song_name, artist)
    try:
        tracks, used = juice_music.search_tracks_multi(
            cfg["base"],
            cfg["api_key"],
            keyword,
            sources=[src, "netease", "bilibili"],
            limit=limit,
        )
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"歌曲信息搜索失败：{e}", None)

    if not tracks:
        return ActionResponse(
            Action.REQLLM,
            f"没搜到「{keyword}」的歌曲信息，请换关键词。",
            None,
        )

    lines = [
        f"歌曲信息搜索「{keyword}」（音源:{juice_music.source_label(used)}，共{len(tracks)}首，仅信息不播放）："
    ]
    for i, t in enumerate(tracks[:limit], 1):
        title = (t.get("title") or t.get("name") or "未知").strip()
        art = (t.get("author") or t.get("singer") or t.get("artist") or "").strip()
        album = (t.get("album") or t.get("album_name") or "").strip()
        dur = t.get("duration") or t.get("duration_ms") or t.get("time") or ""
        tid = juice_music.track_id_of(t) or ""
        extra = []
        if art:
            extra.append(f"歌手:{art}")
        if album:
            extra.append(f"专辑:{album}")
        if dur:
            try:
                # ms or sec
                dv = float(dur)
                if dv > 10000:
                    dv = dv / 1000.0
                m, s = divmod(int(dv), 60)
                extra.append(f"时长:{m}:{s:02d}")
            except Exception:
                extra.append(f"时长:{dur}")
        if tid:
            extra.append(f"id:{tid}")
        lines.append(f"{i}. {title}" + (("｜" + "，".join(extra)) if extra else ""))
    lines.append("请口语简要播报歌名与歌手等信息；用户若要听再调用 play_music；不要擅自播放。")
    try:
        conn.logger.bind(tag=TAG).info(f"song_info n={len(tracks)} q={keyword!r}")
    except Exception:
        pass
    return ActionResponse(Action.REQLLM, "\n".join(lines), None)
