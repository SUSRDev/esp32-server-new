import json
import asyncio
import uuid
from core.handle.sendAudioHandle import send_stt_message
from core.handle.helloHandle import checkWakeupWords
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import ContentType
from core.utils.dialogue import Message
from core.handle.mcpHandle import call_mcp_tool
from plugins_func.register import Action, ActionResponse
from loguru import logger

TAG = __name__


async def handle_user_intent(conn, text):
    # 检查是否有明确的退出命令
    filtered_text = remove_punctuation_and_length(text)[1]
    if await check_direct_exit(conn, filtered_text):
        return True
    # 检查是否是唤醒词
    if await checkWakeupWords(conn, filtered_text):
        return True

    # QA_SEARCH_KEYWORD_SHORTCUT: 「X是什么/是谁」优先联网搜索，避免误点歌
    if await check_qa_search_intent(conn, text):
        return True

    # ONLINE_MUSIC_KEYWORD_SHORTCUT: ChatGLM often prints tool name instead of calling it
    if await check_online_music_intent(conn, text):
        return True

    # SERVER_OPS_KEYWORD_SHORTCUT: 避免 LLM 把 server_control 念出来不调用
    if await check_server_ops_intent(conn, text):
        return True

    if conn.intent_type == "function_call":
        # 使用支持function calling的聊天方法,不再进行意图分析
        return False
    # 使用LLM进行意图分析
    intent_result = await analyze_intent_with_llm(conn, text)
    if not intent_result:
        return False
    # 处理各种意图
    return await process_intent_result(conn, intent_result, text)


async def check_direct_exit(conn, text):
    """检查是否有明确的退出命令"""
    _, text = remove_punctuation_and_length(text)
    cmd_exit = conn.cmd_exit
    for cmd in cmd_exit:
        if text == cmd:
            conn.logger.bind(tag=TAG).info(f"识别到明确的退出命令: {text}")
            await send_stt_message(conn, text)
            await conn.close()
            return True
    return False


async def analyze_intent_with_llm(conn, text):
    """使用LLM分析用户意图"""
    if not hasattr(conn, "intent") or not conn.intent:
        conn.logger.bind(tag=TAG).warning("意图识别服务未初始化")
        return None

    # 对话历史记录
    dialogue = conn.dialogue
    try:
        intent_result = await conn.intent.detect_intent(conn, dialogue.dialogue, text)
        return intent_result
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"意图识别失败: {str(e)}")

    return None


async def process_intent_result(conn, intent_result, original_text):
    """处理意图识别结果"""
    try:
        # 尝试将结果解析为JSON（兼容多段 function_call 粘连）
        intent_data = None
        try:
            intent_data = json.loads(intent_result)
        except Exception:
            # 取最后一个完整 {"function_call": ...} 块
            import re as _re

            blocks = _re.findall(
                r"\{[^{}]*\"function_call\"\s*:\s*\{.*?\}\s*\}",
                intent_result or "",
                flags=_re.S,
            )
            if not blocks:
                # 更宽松：逐行尝试
                for line in (intent_result or "").splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        intent_data = json.loads(line)
                        if "function_call" in intent_data:
                            break
                    except Exception:
                        continue
            else:
                # 优先 web_search / server_control，否则取最后一个
                preferred = None
                for b in blocks:
                    try:
                        obj = json.loads(b)
                    except Exception:
                        continue
                    name = ((obj.get("function_call") or {}).get("name") or "")
                    if name in ("web_search", "server_control", "get_lyrics", "song_info"):
                        preferred = obj
                        break
                    preferred = obj
                intent_data = preferred
            if intent_data is None:
                raise json.JSONDecodeError("no function_call", intent_result or "", 0)

        # 检查是否有function_call
        if "function_call" in intent_data:
            # 直接从意图识别获取了function_call
            conn.logger.bind(tag=TAG).debug(
                f"检测到function_call格式的意图结果: {intent_data['function_call']['name']}"
            )
            function_name = intent_data["function_call"]["name"]
            if function_name == "continue_chat":
                return False

            # REMAP_QA_PLAY_MUSIC: 「是什么/是谁」绝不能走点歌
            import re as _re_qa
            _ot = (original_text or "").strip()
            if function_name == "play_music" and _re_qa.search(
                r"(是什么|是谁|什么是|谁是|人物介绍|简介)", _ot
            ):
                _q = _re_qa.sub(r"[\s。！？!?，,\.😔]+$", "", _ot)
                _q = _re_qa.sub(r"^(什么是|谁是|介绍一下|介绍下)", "", _q)
                _q = _re_qa.sub(r"(是什么|是谁|人物介绍|简介)$", "", _q).strip() or _ot
                if _q.lower() in ("mr", "野兽先生"):
                    _q = "MrBeast"
                if _q in ("十眠",):
                    _q = "失眠"
                function_name = "web_search"
                intent_data["function_call"]["name"] = "web_search"
                intent_data["function_call"]["arguments"] = {"query": _q, "limit": 6}
                conn.logger.bind(tag=TAG).info(f"REMAP play_music->web_search q={_q!r}")

            # LLM 偶尔把选歌写成假函数 choose；映射到 pending 播放
            if function_name in ("choose", "select_song", "play_pending", "select_music"):
                args0 = intent_data["function_call"].get("arguments") or {}
                if isinstance(args0, str):
                    try:
                        args0 = json.loads(args0)
                    except Exception:
                        args0 = {}
                choose_n = args0.get("choose") or args0.get("index") or args0.get("n") or 1
                try:
                    choose_n = int(choose_n)
                except Exception:
                    choose_n = 1
                conn.logger.bind(tag=TAG).info(f"映射假函数 {function_name} -> play_pending #{choose_n}")
                await send_stt_message(conn, original_text)
                conn.client_abort = False

                def _run_choose_map():
                    from plugins_func.functions.play_music import play_pending_music_choice
                    fut = asyncio.run_coroutine_threadsafe(
                        play_pending_music_choice(conn, choose_n), conn.loop
                    )
                    return fut.result(timeout=120)

                try:
                    await asyncio.get_event_loop().run_in_executor(None, _run_choose_map)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"选歌映射失败: {e}")
                return True

            # 任意系统函数缺失时自动补注册（避免插件映射为空导致 NOTFOUND）
            funcItem = conn.func_handler.get_function(function_name)
            if not funcItem:
                conn.func_handler.function_registry.register_function(function_name)
                conn.func_handler.upload_functions_desc()

            function_args = {}
            if "arguments" in intent_data["function_call"]:
                function_args = intent_data["function_call"]["arguments"]
                if function_args is None:
                    function_args = {}
            # 确保参数是字符串格式的JSON
            if isinstance(function_args, dict):
                function_args = json.dumps(function_args)

            function_call_data = {
                "name": function_name,
                "id": str(uuid.uuid4().hex),
                "arguments": function_args,
            }

            await send_stt_message(conn, original_text)
            conn.client_abort = False

            # 使用executor执行函数调用和结果处理
            def process_function_call():
                conn.dialogue.put(Message(role="user", content=original_text))

                # 处理Server端MCP工具调用
                if conn.mcp_manager.is_mcp_tool(function_name):
                    result = conn._handle_mcp_tool_call(function_call_data)
                elif hasattr(conn, "mcp_client") and conn.mcp_client.has_tool(
                    function_name
                ):
                    # 如果是小智端MCP工具调用
                    conn.logger.bind(tag=TAG).debug(
                        f"调用小智端MCP工具: {function_name}, 参数: {function_args}"
                    )
                    try:
                        result = asyncio.run_coroutine_threadsafe(
                            call_mcp_tool(
                                conn, conn.mcp_client, function_name, function_args
                            ),
                            conn.loop,
                        ).result()
                        conn.logger.bind(tag=TAG).debug(f"MCP工具调用结果: {result}")
                        result = ActionResponse(
                            action=Action.REQLLM, result=result, response=""
                        )
                    except Exception as e:
                        conn.logger.bind(tag=TAG).error(f"MCP工具调用失败: {e}")
                        result = ActionResponse(
                            action=Action.REQLLM, result="MCP工具调用失败", response=""
                        )
                else:
                    # 处理系统函数
                    result = conn.func_handler.handle_llm_function_call(
                        conn, function_call_data
                    )

                if result:
                    if result.action == Action.RESPONSE:  # 直接回复前端
                        text = result.response
                        if text is not None:
                            speak_txt(conn, text)
                    elif result.action == Action.REQLLM:  # 调用函数后再请求llm生成回复
                        text = result.result
                        conn.dialogue.put(Message(role="tool", content=text))
                        llm_result = conn.intent.replyResult(text, original_text)
                        if llm_result is None:
                            llm_result = text
                        speak_txt(conn, llm_result)
                    elif (
                        result.action == Action.NOTFOUND
                        or result.action == Action.ERROR
                    ):
                        text = result.result
                        if text is not None:
                            speak_txt(conn, text)
                    elif function_name != "play_music":
                        # For backward compatibility with original code
                        # 获取当前最新的文本索引
                        text = result.response
                        if text is None:
                            text = result.result
                        if text is not None:
                            speak_txt(conn, text)

            # 将函数执行放在线程池中
            conn.executor.submit(process_function_call)
            return True
        return False
    except json.JSONDecodeError as e:
        conn.logger.bind(tag=TAG).error(f"处理意图结果时出错: {e}")
        return False


def speak_txt(conn, text):
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.dialogue.put(Message(role="assistant", content=text))






async def check_qa_search_intent(conn, text):
    """问句直连联网搜索：避免「X是什么/是谁」被误判成点歌或空百科。"""
    import re

    raw = (text or "").strip()
    clean = re.sub(r"[\s。！？!?，,\.😔]+$", "", raw)
    if re.search(r"(我想听|我要听|播放|点歌|来一首|放一首)", clean):
        return False

    query = None
    m = re.match(r"^(?:联网|连网|上网)?搜索\s*(.+)$", clean)
    if m:
        query = m.group(1).strip()
    else:
        m2 = re.match(r"^(?:什么是|谁是|介绍一下|介绍下|讲讲|说说)\s*(.+)$", clean)
        m3 = re.match(r"^(.+?)(?:是什么|是谁|是干嘛的|人物介绍|简介)$", clean)
        if m2:
            query = m2.group(1).strip()
        elif m3:
            query = m3.group(1).strip()
    if not query:
        return False

    query = re.sub(r"^(给我|帮我|请|一下)\s*", "", query)
    query = re.sub(r"(的人物介绍|人物介绍|简介|介绍)$", "", query).strip()
    query = re.sub(r"[。！？!?，,\.😔]+$", "", query).strip()
    if not query or len(query) > 40:
        return False

    aliases = {
        "野兽先生": "MrBeast 野兽先生",
        "mr": "MrBeast",
        "MR": "MrBeast",
        "十眠": "失眠 歌曲",
        "初音": "初音未来",
    }
    query = aliases.get(query, aliases.get(query.lower(), query))
    if str(query).lower().startswith("mr") and len(str(query)) <= 12:
        query = "MrBeast"

    conn.logger.bind(tag=TAG).info(f"关键词命中问答搜索: query={query!r}")
    await send_stt_message(conn, raw)
    conn.client_abort = False
    if not conn.func_handler:
        return False
    if not conn.func_handler.get_function("web_search"):
        try:
            conn.func_handler.function_registry.register_function("web_search")
            conn.func_handler.upload_functions_desc()
        except Exception:
            pass

    def _run():
        conn.dialogue.put(Message(role="user", content=raw))
        return conn.func_handler.handle_llm_function_call(
            conn,
            {
                "name": "web_search",
                "id": str(uuid.uuid4().hex),
                "arguments": json.dumps({"query": query, "limit": 6}, ensure_ascii=False),
            },
        )

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
        action = getattr(result, "action", None)
        if action == Action.RESPONSE:
            speak_txt(
                conn,
                getattr(result, "response", None)
                or getattr(result, "result", "")
                or "",
            )
        elif action == Action.REQLLM:
            text2 = getattr(result, "result", None) or getattr(result, "response", "") or ""
            if text2:
                conn.dialogue.put(Message(role="tool", content=text2))
                llm_result = None
                try:
                    if getattr(conn, "intent", None):
                        llm_result = conn.intent.replyResult(text2, raw)
                except Exception:
                    llm_result = None
                speak_txt(conn, llm_result or text2[:220])
        conn.logger.bind(tag=TAG).info(f"问答搜索已触发: {query}, result={action}")
        return True
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"问答搜索失败: {e}")
        return False


async def check_server_ops_intent(conn, text):
    """关键词直连服务端操作，避免 LLM 把函数名念出来。"""
    import re

    raw = (text or "").strip()
    clean = re.sub(r"[\s。！？!?，,\.😔]+$", "", raw)
    low = clean.lower()

    action = None
    if re.search(r"(磁盘|硬盘|剩余空间|还有多少\s*g|多少\s*g\s*b|多大空间|空间还剩|磁盘占用)", low, re.I):
        action = "disk"
    elif re.search(r"(内存|占内存|还剩多少内存|memory)", low, re.I):
        action = "memory"
    elif re.search(r"(服务器状态|服务端状态|系统状态|容器列表|看看服务器|获取服务器)", low):
        action = "status"
    elif re.search(r"(清(理)?(一下)?音乐缓存|清(理)?缓存)", low):
        action = "clear_music_cache"
    elif re.search(r"(重启小智|重启服务端|重启服务器|重启服务)", low):
        action = "restart"
    elif re.search(r"(看(一下)?(服务)?日志|服务端日志)", low):
        action = "logs"
    else:
        return False

    conn.logger.bind(tag=TAG).info(f"关键词命中服控: action={action}")
    await send_stt_message(conn, raw)
    conn.client_abort = False

    if not conn.func_handler:
        return False
    if not conn.func_handler.get_function("server_control"):
        try:
            conn.func_handler.function_registry.register_function("server_control")
            conn.func_handler.upload_functions_desc()
        except Exception as e:
            conn.logger.bind(tag=TAG).error(f"注册 server_control 失败: {e}")
            return False

    def _run():
        conn.dialogue.put(Message(role="user", content=raw))
        return conn.func_handler.handle_llm_function_call(
            conn,
            {
                "name": "server_control",
                "id": str(uuid.uuid4().hex),
                "arguments": json.dumps({"action": action}, ensure_ascii=False),
            },
        )

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
        action_type = getattr(result, "action", None)
        if action_type == Action.RESPONSE:
            speak_txt(conn, getattr(result, "response", None) or getattr(result, "result", "") or "好的")
        elif action_type == Action.REQLLM:
            text2 = getattr(result, "result", None) or getattr(result, "response", "") or ""
            lines = [ln for ln in str(text2).splitlines() if not ln.startswith("请用口语")]
            spoken = "\n".join(lines).strip()
            if spoken:
                if len(spoken) > 220:
                    spoken = spoken[:220] + "…"
                speak_txt(conn, spoken)
        conn.logger.bind(tag=TAG).info(f"服控已触发: {action}, result={action_type}")
        return True
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"服控触发失败: {e}")
        return False


async def check_online_music_intent(conn, text):
    """关键词直连在线点歌：支持歌手、多结果选第几首"""
    import re

    raw = (text or "").strip()
    clean = re.sub(r"[\s。！？!?，,\.]+$", "", raw)

    cn_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    try:
        from plugins_func.functions.play_music import load_pending_music
        pending = load_pending_music(conn) or []
    except Exception:
        pending = getattr(conn, "pending_music_choices", None) or []
    if pending:
        m_early = re.fullmatch(
            r"(?:(?:播放|放|听|选)?\s*)?(?:第)?\s*([一二两三四五六七八九十\d]+)\s*(?:首|个|曲|首歌)?",
            clean,
        )
        if m_early:
            token = m_early.group(1)
            try:
                choose = cn_map.get(token) if token in cn_map else int(token)
            except Exception:
                choose = None
            if choose:
                conn.logger.bind(tag=TAG).info(f"命中选歌(裸序号): 第{choose}首")
                await send_stt_message(conn, raw)
                conn.client_abort = False

                def _run_choice_early(ch=choose):
                    from plugins_func.functions.play_music import play_pending_music_choice
                    fut = asyncio.run_coroutine_threadsafe(play_pending_music_choice(conn, ch), conn.loop)
                    return fut.result(timeout=120)

                try:
                    await asyncio.get_event_loop().run_in_executor(None, _run_choice_early)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"选歌播放失败: {e}")
                return True

        m = re.search(r"(?:第|选|播放第?)\s*([一二两三四五六七八九十\d]+)\s*(?:首|个|曲)?", clean)
        if not m:
            m = re.match(r"^(?:播放|放|听)?\s*([一二两三四五六七八九十\d]+)\s*(?:首|个|曲)?$", clean)
        if m:
            token = m.group(1)
            choose = cn_map.get(token) if token in cn_map else int(token)
            conn.logger.bind(tag=TAG).info(f"命中选歌: 第{choose}首")
            await send_stt_message(conn, raw)
            conn.client_abort = False

            def _run_choice():
                from plugins_func.functions.play_music import play_pending_music_choice
                fut = asyncio.run_coroutine_threadsafe(play_pending_music_choice(conn, choose), conn.loop)
                return fut.result(timeout=120)

            try:
                await asyncio.get_event_loop().run_in_executor(None, _run_choice)
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"选歌播放失败: {e}")
            return True
        low = clean.lower()
        for i, t in enumerate(pending, 1):
            title = (t.get("title") or t.get("name") or "").strip()
            if title and (title in clean or title.lower() in low):
                conn.logger.bind(tag=TAG).info(f"命中选歌歌名: {title} -> 第{i}首")
                await send_stt_message(conn, raw)
                conn.client_abort = False

                def _run_by_name(idx=i):
                    from plugins_func.functions.play_music import play_pending_music_choice
                    fut = asyncio.run_coroutine_threadsafe(play_pending_music_choice(conn, idx), conn.loop)
                    return fut.result(timeout=120)

                try:
                    await asyncio.get_event_loop().run_in_executor(None, _run_by_name)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"选歌播放失败: {e}")
                return True

    patterns = [
        r"^(?:我想听|我要听|我想播放|播放一下|来一首|放一首|点歌|在线点歌|在线播放|播放歌曲|播放音乐|听一下|放歌)\s*(.+)$",
        r"^(?:播放|放)\s*(.+)$",
        r"^(.+?)的(?:歌|歌曲|音乐)$",
    ]
    song = None
    artist = ""
    choose = None
    for pat in patterns:
        m = re.match(pat, clean)
        if m:
            song = (m.group(1) or "").strip()
            break
    if song is None:
        if clean in ("放歌", "播放音乐", "来一首", "点歌", "在线点歌"):
            song = "晴天"
        else:
            return False

    m_ch = re.search(r"(?:第|选)\s*([一二两三四五六七八九十\d]+)\s*(?:首|个|曲)", song or "")
    if m_ch:
        token = m_ch.group(1)
        choose = cn_map.get(token) if token in cn_map else int(token)
        song = (song[: m_ch.start()] + song[m_ch.end() :]).strip()

    if song:
        m2 = re.match(r"^(.+?)的(.+)$", song)
        if m2:
            left, right = m2.group(1).strip(), m2.group(2).strip()
            if right in ("歌", "歌曲", "音乐", "曲子"):
                artist, song = left, ""
            else:
                artist, song = left, right

    song = re.sub(r"^(一下|歌曲|音乐)\s*", "", song or "").strip()
    # EMOJI_STRIP_SONG
    song = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-\'《》]+", "", song or "").strip()
    artist = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-\'《》]+", "", artist or "").strip()
    if not song and not artist:
        song = "晴天"
    if len((song or "") + (artist or "")) > 50:
        return False

    conn.logger.bind(tag=TAG).info(
        f"关键词命中在线点歌: song={song!r} artist={artist!r} choose={choose}"
    )
    await send_stt_message(conn, raw)
    conn.client_abort = False

    if not conn.func_handler:
        return False
    if not conn.func_handler.get_function("play_music"):
        conn.func_handler.function_registry.register_function("play_music")
        conn.func_handler.upload_functions_desc()

    def _run():
        conn.dialogue.put(Message(role="user", content=raw))
        _song = song or ""
        _artist = artist or ""
        _src = None
        # 解析音源（网易云/B站），并从歌名里剔除
        try:
            from plugins_func.functions import juice_music as _jm
            _src, song2 = _jm.parse_source_from_text(_song)
            if song2 != _song:
                _song = song2
            _src2, artist2 = _jm.parse_source_from_text(_artist)
            if artist2 != _artist:
                _artist = artist2
            if not _src and _src2:
                _src = _src2
        except Exception:
            _src = None
        args = {"song_name": _song or "晴天"}
        if _artist:
            args["artist"] = _artist
        if choose is not None:
            args["choose"] = choose
        if _src:
            args["source"] = _src
        result = conn.func_handler.handle_llm_function_call(
            conn,
            {
                "name": "play_music",
                "id": str(uuid.uuid4().hex),
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        )
        return result

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
        action = getattr(result, "action", None)
        conn.logger.bind(tag=TAG).info("在线点歌已触发: %s/%s, result=%s" % (artist, song, action))
    except Exception as e:
        conn.logger.bind(tag=TAG).error("在线点歌触发失败: %s" % e)
        return False
    return True
