from config.logger import setup_logging
import os
import re
import time
import random
import asyncio
import difflib
import traceback
from pathlib import Path
from core.utils import p3
from core.handle.sendAudioHandle import send_stt_message, send_tts_message
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from core.utils.dialogue import Message
import requests
from pydub import AudioSegment
from core.handle.sendLyricsHandle import start_lyrics_sync
from core.handle.saveLyricsHandle import save_lyrics
from plugins_func.functions import juice_music

TAG = __name__

MUSIC_CACHE = {}

_PENDING_MUSIC_BY_DEVICE = {}
_PENDING_TTL_SEC = 600


def _device_key(conn):
    try:
        headers = getattr(conn, "headers", None) or {}
        return (getattr(conn, "device_id", None) or headers.get("device-id") or "").strip()
    except Exception:
        return (getattr(conn, "device_id", None) or "").strip()


def save_pending_music(conn, tracks, source=None):
    """Save pending list on conn and by device_id (survives reconnect)."""
    tracks = list(tracks or [])[:5]
    conn.pending_music_choices = tracks
    if source is not None:
        conn.pending_music_source = source
    key = _device_key(conn)
    if key:
        _PENDING_MUSIC_BY_DEVICE[key] = {
            "tracks": tracks,
            "source": getattr(conn, "pending_music_source", source),
            "ts": time.time(),
        }


def clear_pending_music(conn):
    conn.pending_music_choices = None
    key = _device_key(conn)
    if key and key in _PENDING_MUSIC_BY_DEVICE:
        _PENDING_MUSIC_BY_DEVICE.pop(key, None)


def load_pending_music(conn):
    """Restore pending list from conn or device cache."""
    choices = getattr(conn, "pending_music_choices", None) or []
    if choices:
        return choices
    key = _device_key(conn)
    if not key:
        return []
    item = _PENDING_MUSIC_BY_DEVICE.get(key)
    if not item:
        return []
    if time.time() - float(item.get("ts") or 0) > _PENDING_TTL_SEC:
        _PENDING_MUSIC_BY_DEVICE.pop(key, None)
        return []
    tracks = item.get("tracks") or []
    conn.pending_music_choices = tracks
    if item.get("source") is not None:
        conn.pending_music_source = item.get("source")
    return tracks



play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": (
            "在线点歌/听歌。默认网易云搜索，先返回候选列表让用户选第几首，禁止擅自直接播放。"
            "用户说第N首时填 choose=N。用户指定B站/网易云时填 source。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "歌曲名称；若只点歌手可填空字符串。",
                },
                "artist": {
                    "type": "string",
                    "description": "歌手名，可空。",
                },
                "choose": {
                    "type": "integer",
                    "description": "选第几首（从1开始）。未选择时不要填，系统会先播报列表。",
                },
                "source": {
                    "type": "string",
                    "description": "音源：netease/网易云 或 bilibili/B站。用户未指定时默认 netease。",
                },
            },
            "required": ["song_name"],
        },
    },
}


@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
def play_music(conn, song_name: str = "晴天", artist: str = "", choose=None, source: str = ""):
    try:
        # 透传歌手/选择/音源到异步任务
        conn._music_play_artist = (artist or "").strip()
        try:
            conn._music_play_choose = int(choose) if choose not in (None, "", "null") else None
        except Exception:
            conn._music_play_choose = None
        conn._music_play_source = (source or "").strip()
        _q = (song_name or "").strip() or (artist or "").strip() or "晴天"
        music_intent = (
            f"播放音乐 {_q}" if song_name != "random" else "随机播放音乐"
        )

        # 检查事件循环状态
        if not conn.loop.is_running():
            conn.logger.bind(tag=TAG).error("事件循环未运行，无法提交任务")
            return ActionResponse(
                action=Action.RESPONSE, result="系统繁忙", response="请稍后再试"
            )

        # 点歌提示改为轻量字幕，避免 TTS 与歌曲抢队列导致有词无声
        try:
            tip = f"正在为你找歌，{song_name}" if song_name and song_name != "random" else "正在为你找歌，请稍等"
            conn.logger.bind(tag=TAG).info(tip)
        except Exception:
            pass

        # 提交异步任务
        future = asyncio.run_coroutine_threadsafe(
            handle_music_command(conn, music_intent), conn.loop
        )

        def handle_done(f):
            try:
                f.result()
                conn.logger.bind(tag=TAG).info("点歌任务结束")
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"播放失败: {e}")

        future.add_done_callback(handle_done)
        return ActionResponse(
            action=Action.RESPONSE, result="点歌任务已开始", response=""
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"处理音乐意图错误: {e}")
        return ActionResponse(
            action=Action.RESPONSE, result=str(e), response="播放音乐时出错了"
        )


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    for keyword in ["播放音乐"]:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                return parts[1].strip()
    return None


def _find_best_match(conn, potential_song, music_files):
    """查找最匹配的歌曲（增强版）"""
    # 优先匹配cache目录
    cache_dir = os.path.join(MUSIC_CACHE['music_dir'], 'cache')
    cache_files = [f for f in os.listdir(cache_dir) if f.lower().endswith(('.mp3', '.wav'))]
    
    # 检查缓存目录精确匹配
    clean_query = re.sub(r'[^\u4e00-\u9fa5\w\s]', '', potential_song).strip().lower().replace('。', '').replace('.', '')  # 同时处理中英文句号  # 去除两端特殊字符
    conn.logger.bind(tag=TAG).debug(f"缓存查询中: {clean_query}")
    for f in cache_files:
        song_part = f.split(' - ', 1)[0].strip().lower().replace('。', '').replace('.', '')  # 清理缓存文件名中的标点
        # 先精确匹配再模糊匹配
        if clean_query == song_part:
            return os.path.join('cache', f)
        if difflib.SequenceMatcher(None, clean_query, song_part).ratio() >= 0.6:  # 降低模糊匹配阈值
            conn.logger.bind(tag=TAG).debug(f"完整匹配路径: {cache_dir}\{f}")
            conn.logger.bind(tag=TAG).debug(f"缓存模糊匹配成功: {clean_query} vs {song_part}")
            return os.path.join('cache', f)
            return os.path.join('cache', f)
    
    best_match = None
    highest_score = 0
    potential_song = re.sub(r'[^\u4e00-\u9fa5\w\s]', '', potential_song).lower()  # 保留中文字符

    for music_file in music_files:
        song_name = os.path.splitext(music_file)[0]
        clean_name = re.sub(r'[^\w\s]', '', song_name).lower()
        
        # 使用组合相似度算法
        seq_ratio = difflib.SequenceMatcher(None, potential_song, clean_name).ratio()
        partial_ratio = difflib.SequenceMatcher(None, potential_song, clean_name).quick_ratio()
        score = (seq_ratio * 0.6 + partial_ratio * 0.4)  # 组合权重
        
        # 增加绝对匹配检测
        if potential_song in clean_name or clean_name in potential_song:
            score = max(score, 0.85)
            
        if score > highest_score and score > 0.6:  
            highest_score = score
            best_match = music_file
            conn.logger.bind(tag=TAG).debug(f"新最佳匹配: {song_name} 得分: {score:.2f}")
    
    # 如果缓存没找到再匹配主目录
    return best_match if highest_score >= 0.6 else None


def get_music_files(music_dir, music_ext):
    music_dir = Path(music_dir)
    music_files = []
    music_file_names = []
    for file in music_dir.rglob("*"):
        # 判断是否是文件
        if file.is_file():
            # 获取文件扩展名
            ext = file.suffix.lower()
            # 判断扩展名是否在列表中
            if ext in music_ext:
                # 添加相对路径
                music_files.append(str(file.relative_to(music_dir)))
                music_file_names.append(
                    os.path.splitext(str(file.relative_to(music_dir)))[0]
                )
    return music_files, music_file_names


def initialize_music_handler(conn):
    global MUSIC_CACHE
    if MUSIC_CACHE == {}:
        if "play_music" in conn.config["plugins"]:
            MUSIC_CACHE["music_config"] = conn.config["plugins"]["play_music"]
            MUSIC_CACHE["music_dir"] = os.path.abspath(
                MUSIC_CACHE["music_config"].get("music_dir", "./music")  # 默认路径修改
            )
            MUSIC_CACHE["music_ext"] = MUSIC_CACHE["music_config"].get(
                "music_ext", (".mp3", ".wav", ".p3")
            )
            MUSIC_CACHE["refresh_time"] = MUSIC_CACHE["music_config"].get(
                "refresh_time", 60
            )
        else:
            MUSIC_CACHE["music_dir"] = os.path.abspath("./music")
            MUSIC_CACHE["music_ext"] = (".mp3", ".wav", ".p3")
            MUSIC_CACHE["refresh_time"] = 60
        # 获取音乐文件列表
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
        )
        MUSIC_CACHE["scan_time"] = time.time()
        MUSIC_CACHE["music_cache_dir"] = os.path.abspath(os.path.join(MUSIC_CACHE["music_dir"], "cache"))
        os.makedirs(MUSIC_CACHE["music_cache_dir"], exist_ok=True)
        MUSIC_CACHE["download_api"] = "https://api.vkeys.cn/v2/music/tencent"
        juice_cfg = juice_music.resolve_config(MUSIC_CACHE.get("music_config") or {})
        MUSIC_CACHE["juice_base"] = juice_cfg["base"]
        MUSIC_CACHE["juice_api_key"] = juice_cfg["api_key"]
        MUSIC_CACHE["juice_source"] = juice_cfg["source"]
    return MUSIC_CACHE

def _detect_audio_type(file_path):
    """通过文件头检测音频类型（增强版）"""
    max_head_size = 4096  # 读取4KB内容进行检测
    with open(file_path, 'rb') as f:
        head = f.read(max_head_size)
        
        # MP3检测（ID3v1/v2标签）
        if head.startswith(b'ID3'):
            return 'mp3'
        
        # M4A检测（QuickTime文件格式）
        if head.startswith(b'ftyp'):
            return 'm4a'
        
        # WAV检测
        if head.startswith(b'RIFF'):
            return 'wav'
        
        # AAC检测（ADTS头部）
        if head.startswith(b'\x00\x00\x00\x1f\x61\x74\x64\x53'):
            return 'aac'
        
        # 其他流媒体格式检测
        # 继续检查常见的流媒体头部特征
        # FFV1视频流（虽然不是音频，但某些情况可能出现）
        if head.startswith(b'FFV1'):
            return 'unknown'  # 视为未知流媒体
        
        # 如果仍未检测到，继续扫描剩余内容
        # 查找MP3的魔数（可能在文件中间）
        mp3_signature = b'\x49\x44\x33'  # "ID3"
        pos = 0
        while pos < len(head) - 3:
            if head[pos:pos+3] == mp3_signature:
                return 'mp3'
            pos += 1
        
        # 检查MPEG-4音频流
        mpeg4_signature = b'\x00\x00\x01'  # ISO BMFF标识符
        if head.find(mpeg4_signature) != -1:
            return 'm4a'
        
        return None

def _validate_download(temp_path, expected_size):
    """验证下载文件完整性"""
    if not os.path.exists(temp_path):
        return False
    downloaded_size = os.path.getsize(temp_path)
    if downloaded_size < expected_size * 0.9:  # 允许一定误差
        return False
    return True

async def play_online_music(conn, specific_file=None, song_name=None):
    """播放在线音乐文件（强制 tts start + 直接入音频队列，保证出声）"""
    try:
        selected_music = specific_file
        if selected_music and os.path.isabs(selected_music):
            music_path = selected_music
        else:
            music_path = os.path.join(MUSIC_CACHE["music_dir"], selected_music)
        conn.logger.bind(tag=TAG).info(f"验证缓存路径有效性: {os.path.exists(music_path)}")
        conn.logger.bind(tag=TAG).info(f"音乐文件绝对路径: {music_path}")
        if not music_path or not os.path.exists(music_path):
            raise FileNotFoundError(f"音乐文件不存在: {music_path}")

        conn.tts_first_text = song_name or selected_music
        conn.tts_last_text = song_name or selected_music
        conn.llm_finish_task = True
        conn.client_abort = False
        conn.tts.tts_audio_first_sentence = True

        status = f"正在播放歌曲: {song_name}"
        text = f"《{song_name}》"
        # 尽量把音量拉起来（很多“完全没声”其实是音量被调到 0）
        try:
            if getattr(conn, "mcp_client", None) and conn.mcp_client.has_tool("self.audio_speaker.set_volume"):
                from core.handle.mcpHandle import call_mcp_tool
                await call_mcp_tool(conn, conn.mcp_client, "self.audio_speaker.set_volume", '{"volume": 80}', timeout=3)
                conn.logger.bind(tag=TAG).info("已尝试设置扬声器音量为 80")
        except Exception as ve:
            conn.logger.bind(tag=TAG).warning(f"设置音量失败(可忽略): {ve}")

        # 强制开播信号：设备必须收到 tts start 才会播 Opus
        await send_stt_message(conn, text)
        conn.logger.bind(tag=TAG).info(status)
        conn.tts_last_text_index = 0
        conn.tts_first_text_index = 0

        # 转 Opus（放到线程池，避免堵事件循环）
        def _convert():
            if music_path.endswith(".p3"):
                return p3.decode_opus_from_file(music_path)
            from core.utils.util import audio_to_data
            fd = int(getattr(conn, "server_frame_duration", 60) or 60)
            return audio_to_data(music_path, is_opus=True, frame_duration_ms=fd)

        t0 = time.time()
        audio_datas, duration = await asyncio.get_event_loop().run_in_executor(None, _convert)
        took = round(time.time() - t0, 2)
        nframes = len(audio_datas) if audio_datas else 0
        conn.logger.bind(tag=TAG).info(
            f"音乐转码完成 frames={nframes} duration={duration} took={took}s"
        )
        if not audio_datas:
            raise RuntimeError("音乐转码结果为空")

        # 清空残留文本队列，避免旧 TTS 抢播
        try:
            while not conn.tts.tts_text_queue.empty():
                conn.tts.tts_text_queue.get_nowait()
        except Exception:
            pass

        display = song_name or "music"
        conn.tts.tts_audio_queue.put((SentenceType.FIRST, audio_datas, display))
        conn.tts.tts_audio_queue.put((SentenceType.LAST, [], None))

        # 歌词同步当前已关闭，仅保留调用位
        asyncio.create_task(start_lyrics_sync(conn, music_path))
        conn.logger.bind(tag=TAG).info("开始处理歌词")

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"播放在线音乐失败: {str(e)}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
        try:
            tip = "抱歉，播放失败了"
            conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
        except Exception:
            pass



def _cleanup_files(conn, file_paths):
    """清理指定的文件"""
    for path in file_paths:
        if os.path.exists(path):
            try:
                os.remove(path)
                conn.logger.bind(tag=TAG).info(f"清理文件: {path}")
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"清理文件失败: {path} - {str(e)}")

def convert_to_mp3(conn, input_path):
    """将音频文件转换为MP3格式（增强版）"""
    try:
        if input_path.endswith('.m4a'):
            audio = AudioSegment.from_file(input_path, format='m4a')
            output_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{os.path.basename(input_path)}.mp3")
            audio.export(output_path, format='mp3', bitrate='192k')
            return output_path
        elif input_path.endswith('.aac'):
            audio = AudioSegment.from_file(input_path, format='aac')
            output_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{os.path.basename(input_path)}.mp3")
            audio.export(output_path, format='mp3', bitrate='192k')
            return output_path
        elif input_path.endswith('.mp3'):
            return input_path
        else:
            raise ValueError(f"不支持的音频格式: {os.path.splitext(input_path)[1]}")
    except Exception as e:
        _cleanup_files(conn, [input_path])
        raise e

def _download_with_retries(conn, song_name, max_choose=8):
    """尝试多个候选音源，直到下载成功"""
    last_err = None
    for choose in range(1, max_choose + 1):
        try:
            response = requests.get(
                MUSIC_CACHE["download_api"],
                params={"word": song_name, "choose": choose, "quality": 2},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            conn.logger.bind(tag=TAG).info(f"点歌API choose={choose} 响应code={data.get('code')} song={(data.get('data') or {}).get('song')}")
            if data.get("code") != 200 or not (data.get("data") or {}).get("url"):
                last_err = Exception(f"API无可用链接 choose={choose}")
                continue
            music_url = data["data"]["url"]
            dl = requests.get(
                music_url,
                stream=True,
                timeout=12,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            if dl.status_code != 200:
                last_err = Exception(f"下载失败 HTTP {dl.status_code} choose={choose}")
                conn.logger.bind(tag=TAG).warning(str(last_err))
                continue
            # peek first chunk
            it = dl.iter_content(chunk_size=8192)
            first = next(it, b"")
            if not first or len(first) < 100:
                last_err = Exception(f"音源内容无效 choose={choose}")
                conn.logger.bind(tag=TAG).warning(str(last_err))
                continue
            return data, dl, it, first
        except Exception as e:
            last_err = e
            conn.logger.bind(tag=TAG).warning(f"点歌候选失败 choose={choose}: {e}")
            continue
    raise last_err or Exception("没有可用音源")


async def handle_online_song_command(conn, song_name, artist="", choose=None, source=""):
    """在线点歌：默认网易云；无序号时强制先出列表询问，绝不直接播放。"""
    temp_cache_path = None
    try:
        artist = (artist or getattr(conn, "_music_play_artist", "") or "").strip()
        if choose is None:
            choose = getattr(conn, "_music_play_choose", None)
        try:
            choose = int(choose) if choose not in (None, "", "null") else None
        except Exception:
            choose = None

        source_raw = (source or getattr(conn, "_music_play_source", "") or "").strip()
        # 也允许从歌名文本里解析“B站/网易云”
        parsed_src, cleaned_song = juice_music.parse_source_from_text(song_name or "")
        if cleaned_song != (song_name or ""):
            song_name = cleaned_song
        if not source_raw and parsed_src:
            source_raw = parsed_src
        parsed_src2, cleaned_artist = juice_music.parse_source_from_text(artist or "")
        if cleaned_artist != (artist or ""):
            artist = cleaned_artist
        if not source_raw and parsed_src2:
            source_raw = parsed_src2

        user_forced = bool(source_raw)
        source = juice_music.normalize_source(source_raw, juice_music.DEFAULT_SOURCE) if source_raw else juice_music.DEFAULT_SOURCE

        if not song_name or song_name in ("random", "None"):
            song_name = "" if artist else "晴天"
        song_name = (song_name or "").strip()
        # EMOJI_CLEAN_SONG
        import re as _re_em
        song_name = _re_em.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-_'《》]+", "", song_name).strip()
        artist = _re_em.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-_'《》]+", "", artist or "").strip()

        query = juice_music.build_search_keyword(song_name, artist)
        conn.logger.bind(tag=TAG).info(
            f"点歌参数 song={song_name!r} artist={artist!r} choose={choose} source={source!r} forced={user_forced} query={query!r}"
        )

        # ===== JuiceMusic：无 choose 时只出列表，不走缓存直播 =====
        try:
            base = MUSIC_CACHE.get("juice_base") or juice_music.DEFAULT_BASE
            key = MUSIC_CACHE.get("juice_api_key") or juice_music.DEFAULT_KEY

            if user_forced:
                sources = [source]
            else:
                # 默认网易云，失败再试 B站；用户指定源则只用该源
                sources = [juice_music.DEFAULT_SOURCE, "bilibili"]

            conn.logger.bind(tag=TAG).info(f"JuiceMusic 搜索: {query} sources={sources}")
            tracks, used_source = juice_music.search_tracks_multi(
                base, key, query, sources=sources, limit=8
            )
            if not tracks:
                raise RuntimeError("JuiceMusic 未找到歌曲")

            # 有歌手/歌名时重排：原唱优先，翻唱/钢琴版靠后
            try:
                tracks = juice_music.rank_tracks(tracks, song_name=song_name, artist=artist)
            except Exception as re:
                conn.logger.bind(tag=TAG).warning(f"rank_tracks skip: {re}")

            # 只有明确 choose 才播放；否则一律播报列表询问
            if choose is not None:
                track = juice_music.pick_track(tracks, choose=choose)
                if track is None:
                    tip = f"只有{min(len(tracks), 5)}首可选，请说第1首到第{min(len(tracks), 5)}首"
                    await send_stt_message(conn, tip)
                    try:
                        conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
                    except Exception as te:
                        conn.logger.bind(tag=TAG).warning(f"选歌提示失败: {te}")
                    save_pending_music(conn, tracks[:5], used_source)
                    return True
                clear_pending_music(conn)
                conn.pending_music_source = used_source
                await _download_and_play_juice_track(conn, track, base, key)
                return True

            # 强制选歌列表（哪怕只有1首）
            save_pending_music(conn, tracks[:5], used_source)
            src_name = juice_music.source_label(used_source)
            lines = []
            for i, t in enumerate(conn.pending_music_choices, 1):
                lines.append(f"{i}.{juice_music.track_label(t)}")
            tip = f"{src_name}搜到{len(conn.pending_music_choices)}首：" + "，".join(lines) + "。请回复序号，比如第1首"
            conn.logger.bind(tag=TAG).info(f"待选歌曲[{used_source}]: {lines}")
            await send_stt_message(conn, tip)
            try:
                conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
            except Exception as te:
                conn.logger.bind(tag=TAG).warning(f"选歌播报失败: {te}")
            return True
        except Exception as je:
            conn.logger.bind(tag=TAG).warning(f"JuiceMusic 点歌失败: {je}")
            tip = "这首歌暂时搜不到，换个歌名或说用B站搜试试"
            await send_stt_message(conn, tip)
            try:
                conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
            except Exception:
                pass
            return False

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"在线点歌失败: {e}")
        if temp_cache_path and os.path.exists(temp_cache_path):
            try:
                os.remove(temp_cache_path)
            except Exception:
                pass
        tip = "这首歌暂时播不了，换一首试试吧"
        await send_stt_message(conn, tip)
        try:
            conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
        except Exception as te:
            conn.logger.bind(tag=TAG).error(f"失败播报失败: {te}")
        return False



async def _download_and_play_juice_track(conn, track, base, key):
    """下载并播放 JuiceMusic 单曲"""
    track_id = juice_music.track_id_of(track)
    if not track_id:
        raise RuntimeError("歌曲缺少 id")
    title = track.get("title") or track.get("name") or "未知歌曲"
    artist = track.get("author") or track.get("singer") or track.get("artist") or ""
    api_song_name = juice_music.track_label(track)
    processed_song_name = juice_music.safe_filename(title, artist)
    temp_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.tmp")
    ctype, nbytes = juice_music.download_stream(base, key, track_id, temp_cache_path)
    conn.logger.bind(tag=TAG).info(
        f"JuiceMusic 下载完成: id={track_id} bytes={nbytes} ctype={ctype}"
    )
    try:
        lrc = juice_music.fetch_lyric_lrc(base, key, track_id)
        if lrc:
            lrc_path = os.path.join(
                MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.lrc"
            )
            with open(lrc_path, "w", encoding="utf-8") as lf:
                lf.write(lrc)
            conn.logger.bind(tag=TAG).info(f"JuiceMusic 歌词已保存: {lrc_path}")
    except Exception as le:
        conn.logger.bind(tag=TAG).warning(f"JuiceMusic 歌词获取失败: {le}")

    with open(temp_cache_path, "rb") as hf:
        head = hf.read(16)
    audio_type = juice_music.ext_from_content_type(ctype, head) or _detect_audio_type(temp_cache_path) or "mp3"
    final_cache_path = os.path.join(
        MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.{audio_type}"
    )
    if os.path.exists(final_cache_path):
        os.remove(final_cache_path)
    os.rename(temp_cache_path, final_cache_path)
    if audio_type != "mp3":
        converted_path = convert_to_mp3(conn, final_cache_path)
        if not converted_path:
            raise Exception("音频转换失败")
        os.remove(final_cache_path)
        mp3_cache_path = os.path.join(
            MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3"
        )
        if os.path.exists(mp3_cache_path):
            os.remove(mp3_cache_path)
        os.rename(converted_path, mp3_cache_path)
        final_cache_path = mp3_cache_path
    mp3_cache_path = (
        final_cache_path
        if final_cache_path.endswith(".mp3")
        else os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3")
    )
    if not os.path.exists(mp3_cache_path):
        raise ValueError("文件格式转换失败")
    await send_stt_message(conn, f"正在播放在线歌曲: {api_song_name}")
    await play_online_music(conn, specific_file=mp3_cache_path, song_name=api_song_name)


async def play_pending_music_choice(conn, choose: int):
    """播放待选列表中的第 choose 首（1-based），支持断线重连后从设备缓存恢复。"""
    try:
        choose = int(choose)
    except Exception:
        choose = 0
    choices = load_pending_music(conn)
    if not choices:
        tip = "当前没有可选歌曲，请先说想听什么歌"
        await send_stt_message(conn, tip)
        try:
            conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
        except Exception:
            pass
        return False
    if choose < 1 or choose > len(choices):
        tip = f"只有{len(choices)}首，请说第1首到第{len(choices)}首"
        await send_stt_message(conn, tip)
        try:
            conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=tip)
        except Exception:
            pass
        return False
    track = choices[choose - 1]
    clear_pending_music(conn)
    initialize_music_handler(conn)
    base = MUSIC_CACHE.get("juice_base") or juice_music.DEFAULT_BASE
    key = MUSIC_CACHE.get("juice_api_key") or juice_music.DEFAULT_KEY
    await _download_and_play_juice_track(conn, track, base, key)
    return True



async def handle_music_command(conn, text):
    initialize_music_handler(conn)
    global MUSIC_CACHE

    """处理音乐播放指令"""
    clean_text = re.sub(r"[^\w\s]", "", text).strip()
    conn.logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

    song_name = _extract_song_name(clean_text)
    await handle_online_song_command(conn, song_name, artist=getattr(conn, '_music_play_artist', '') or '', choose=getattr(conn, '_music_play_choose', None), source=getattr(conn, '_music_play_source', '') or '')
    return True

    # 尝试匹配具体歌名
    if os.path.exists(MUSIC_CACHE["music_dir"]):
        if time.time() - MUSIC_CACHE["scan_time"] > MUSIC_CACHE["refresh_time"]:
            # 刷新音乐文件列表
            MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = (
                get_music_files(MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"])
            )
            MUSIC_CACHE["scan_time"] = time.time()

        potential_song = _extract_song_name(clean_text)
        if potential_song:
            conn.logger.bind(tag=TAG).debug(f"提取到的歌曲名: {potential_song}")
            best_match = _find_best_match(potential_song, MUSIC_CACHE["music_files"])
            if best_match:
                conn.logger.bind(tag=TAG).info(f"找到最匹配的歌曲: {best_match}")
                await play_local_music(conn, specific_file=best_match)
                return True
    # 检查是否是通用播放音乐命令
    await play_local_music(conn)
    return True


def _get_random_play_prompt(song_name):
    """生成随机播放引导语"""
    # 移除文件扩展名
    clean_name = os.path.splitext(song_name)[0]
    prompts = [
        f"正在为您播放，{clean_name}",
        f"请欣赏歌曲，{clean_name}",
        f"即将为您播放，{clean_name}",
        f"为您带来，{clean_name}",
        f"让我们聆听，{clean_name}",
        f"接下来请欣赏，{clean_name}",
        f"为您献上，{clean_name}",
    ]
    # 直接使用random.choice，不设置seed
    return random.choice(prompts)