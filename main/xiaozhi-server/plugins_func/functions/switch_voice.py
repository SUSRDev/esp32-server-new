"""切换 EdgeTTS 音色。"""
from __future__ import annotations

from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

VOICE_PRESETS = {
    "晓晓": "zh-CN-XiaoxiaoNeural",
    "女声": "zh-CN-XiaoxiaoNeural",
    "默认女": "zh-CN-XiaoxiaoNeural",
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "晓伊": "zh-CN-XiaoyiNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "云扬": "zh-CN-YunyangNeural",
    "男声": "zh-CN-YunyangNeural",
    "yunyang": "zh-CN-YunyangNeural",
    "云健": "zh-CN-YunjianNeural",
    "yunjian": "zh-CN-YunjianNeural",
    "云希": "zh-CN-YunxiNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "云夏": "zh-CN-YunxiaNeural",
    "yunxia": "zh-CN-YunxiaNeural",
    "辽宁": "zh-CN-liaoning-XiaobeiNeural",
    "小贝": "zh-CN-liaoning-XiaobeiNeural",
    "陕西": "zh-CN-shaanxi-XiaoniNeural",
    "小妮": "zh-CN-shaanxi-XiaoniNeural",
    "香港女": "zh-HK-HiuGaaiNeural",
    "海佳": "zh-HK-HiuGaaiNeural",
    "海曼": "zh-HK-HiuMaanNeural",
    "万龙": "zh-HK-WanLungNeural",
    "香港男": "zh-HK-WanLungNeural",
}

SWITCH_VOICE_DESC = {
    "type": "function",
    "function": {
        "name": "switch_voice",
        "description": (
            "切换语音合成音色。可选：晓晓/女声、晓伊、云扬/男声、云健、云希、云夏、"
            "辽宁小贝、陕西小妮、香港海佳、海曼、万龙。"
            "用户说换声音、换音色、用男声、用女声时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "voice": {
                    "type": "string",
                    "description": "音色名或别名，如晓晓、男声、云希",
                }
            },
            "required": ["voice"],
        },
    },
}


@register_function("switch_voice", SWITCH_VOICE_DESC, ToolType.SYSTEM_CTL)
def switch_voice(conn, voice: str):
    name = (voice or "").strip()
    if not name:
        return ActionResponse(Action.RESPONSE, "失败", "请告诉我要用哪个声音，比如晓晓或男声")

    target = VOICE_PRESETS.get(name) or VOICE_PRESETS.get(name.lower())
    if not target:
        # 模糊
        for k, v in VOICE_PRESETS.items():
            if k in name or name in k:
                target = v
                break
    if not target and name.startswith("zh-"):
        target = name
    if not target:
        tips = "、".join(["晓晓", "晓伊", "云扬", "云健", "云希", "云夏", "辽宁小贝", "陕西小妮"])
        return ActionResponse(Action.RESPONSE, "失败", f"不支持这个音色。可以说：{tips}")

    try:
        if not getattr(conn, "tts", None):
            return ActionResponse(Action.RESPONSE, "失败", "语音模块还没准备好")
        conn.tts.voice = target
        # 同步配置，避免后续重建覆盖
        try:
            tts_sel = conn.config.get("selected_module", {}).get("TTS", "TTS_EdgeTTS")
            if "TTS" in conn.config and tts_sel in conn.config["TTS"]:
                conn.config["TTS"][tts_sel]["voice"] = target
                conn.config["TTS"][tts_sel]["private_voice"] = target
        except Exception:
            pass
        conn.logger.bind(tag=TAG).info(f"切换音色 -> {target}")
        nice = name if name in VOICE_PRESETS else target
        return ActionResponse(Action.RESPONSE, "成功", f"好的，已换成{nice}的声音")
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"切换音色失败: {e}")
        return ActionResponse(Action.RESPONSE, "失败", f"换声音失败了：{e}")
