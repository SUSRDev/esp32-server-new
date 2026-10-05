#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""歌词同步：用 stt 文本上屏，但不触发 tts start（避免打断音乐）。"""
import asyncio
import json
import time
from pathlib import Path

from mutagen.mp3 import MP3

TAG = __name__

_current_music_path = None
_lyrics_dict = {}
_is_running = False
_start_time = 0
_last_sent_text = ""


async def start_lyrics_sync(conn, music_path: str):
    """启动歌词同步"""
    global _current_music_path, _lyrics_dict, _is_running, _start_time, _last_sent_text

    # 轻微偏移，等首包音频落到设备
    await asyncio.sleep(1.2)

    conn.logger.bind(tag=TAG).info(f"开始初始化歌词同步 | 音乐路径: {music_path}")
    _current_music_path = music_path
    _last_sent_text = ""
    _lyrics_dict = _parse_lyrics(conn, music_path)
    conn.logger.bind(tag=TAG).info(f"解析到{len(_lyrics_dict)}条歌词")

    if not _lyrics_dict:
        tip = "这首歌暂时没有歌词"
        try:
            await _send_lyric_display(conn, tip)
        except Exception:
            pass
        return

    _is_running = True
    _start_time = time.time()
    duration = _get_music_duration(conn, music_path)

    # 先推一条提示，确认设备能显示
    try:
        await _send_lyric_display(conn, "♪ 歌词同步中")
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"歌词提示发送失败: {e}")

    while time.time() - _start_time < duration and _is_running:
        # 若连接已断开则停止
        if getattr(conn, "client_abort", False):
            break
        current_pos = time.time() - _start_time
        await _send_lyric(conn, current_pos)
        await asyncio.sleep(0.15)

    conn.logger.bind(tag=TAG).info("歌词同步任务结束")


def _parse_lyrics(conn, music_path):
    """解析同名 .lrc"""
    lrc_path = Path(music_path).with_suffix(".lrc")
    conn.logger.bind(tag=TAG).info(f"尝试加载歌词文件: {lrc_path}")
    lyrics = {}

    if not lrc_path.exists():
        conn.logger.bind(tag=TAG).warning(f"歌词文件不存在: {lrc_path}")
        return lyrics

    # 兼容 utf-8 / gbk
    raw = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312"):
        try:
            raw = lrc_path.read_text(encoding=enc)
            break
        except Exception:
            continue
    if raw is None:
        conn.logger.bind(tag=TAG).error(f"无法读取歌词文件: {lrc_path}")
        return lyrics

    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("[") or "]" not in line:
            continue
        try:
            time_part, text_part = line.split("]", 1)
            time_str = time_part[1:]
            text = text_part.strip()
            if not text:
                continue
            # 跳过 meta 标签
            if ":" not in time_str:
                continue
            # 时间格式校验：允许 mm:ss / mm:ss.xx / mm:ss.xxx
            cleaned = time_str.replace(":", "").replace(".", "")
            if not cleaned.isdigit():
                continue
            if "." in time_str:
                minutes, rest = time_str.split(":", 1)
                seconds = rest.split(".")[0]
                frac = rest.split(".")[1] if "." in rest else "0"
                # 百分秒或毫秒
                if len(frac) <= 2:
                    total_sec = float(minutes) * 60 + float(seconds) + float("0." + frac)
                else:
                    total_sec = float(minutes) * 60 + float(seconds) + float(frac) / (10 ** len(frac))
            else:
                minutes, seconds = time_str.split(":", 1)
                total_sec = float(minutes) * 60 + float(seconds)
            lyrics[round(total_sec, 2)] = text
        except Exception as e:
            conn.logger.bind(tag=TAG).debug(f"跳过无效行: {line} - {e}")
            continue
    return lyrics


async def _send_lyric_display(conn, text: str):
    """上屏歌词：只发 stt 文本，绝不发 tts start（否则会打断在线音乐）。"""
    from core.handle.sendAudioHandle import _ws_send

    payload = json.dumps(
        {
            "type": "stt",
            "text": text,
            "session_id": getattr(conn, "session_id", ""),
        },
        ensure_ascii=False,
    )
    await _ws_send(conn, payload)

    # 额外发一份 llm 文本（部分固件用它刷新气泡/标题）
    payload2 = json.dumps(
        {
            "type": "llm",
            "text": text,
            "emotion": "neutral",
            "session_id": getattr(conn, "session_id", ""),
        },
        ensure_ascii=False,
    )
    await _ws_send(conn, payload2)


async def _send_lyric(conn, current_pos: float):
    global _lyrics_dict, _last_sent_text
    valid_times = [t for t in _lyrics_dict.keys() if t <= current_pos]
    if not valid_times:
        return
    closest_time = max(valid_times)
    text = (_lyrics_dict.get(closest_time) or "").strip()
    if not text:
        return
    if text == _last_sent_text:
        # 同一句不重复刷
        del _lyrics_dict[closest_time]
        return
    try:
        await _send_lyric_display(conn, f"♪ {text}")
        _last_sent_text = text
        conn.logger.bind(tag=TAG).info(f"已发送歌词: {text}")
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"发送歌词失败: {e}")
    # 清理已发送及更早的
    for t in list(_lyrics_dict.keys()):
        if t <= closest_time:
            _lyrics_dict.pop(t, None)


def _get_music_duration(conn, file_path: str) -> float:
    try:
        audio = MP3(file_path)
        if audio.info.length > 0:
            conn.logger.bind(tag=TAG).info(f"歌曲时长: {audio.info.length}秒")
            return float(audio.info.length)
        return 300
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"获取音频时长失败: {str(e)}")
        return 300


async def stop_lyrics_sync(conn):
    global _is_running
    _is_running = False
    conn.logger.bind(tag=TAG).info("歌词推送已中止")
