#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切换对话大模型：GLM / DeepSeek / Xiavier GPT 系列。"""
from __future__ import annotations

import os

from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

XIAVIER_KEY = os.environ.get("XIAVIER_API_KEY", "")
XIAVIER_BASE = os.environ.get("XIAVIER_BASE_URL", "https://xiavier.com/v1")

MODEL_PRESETS = {
    # GLM
    "glm": "LLM_ChatGLMLLM",
    "chatglm": "LLM_ChatGLMLLM",
    "智谱": "LLM_ChatGLMLLM",
    "glm-flash": "LLM_ChatGLMLLM",
    "glm4": "LLM_ChatGLMLLM",
    # DeepSeek
    "deepseek": "LLM_DeepSeekLLM",
    "deepseek-flash": "LLM_DeepSeekLLM",
    "flash": "LLM_DeepSeekLLM",
    "ds": "LLM_DeepSeekLLM",
    "deepseek-pro": "LLM_DeepSeekPro",
    "pro": "LLM_DeepSeekPro",
    "ds-pro": "LLM_DeepSeekPro",
    # Xiavier GPT
    "gpt": "LLM_GPT56Sol",
    "gpt5": "LLM_GPT56Sol",
    "gpt-5": "LLM_GPT56Sol",
    "sol": "LLM_GPT56Sol",
    "gpt-5.6-sol": "LLM_GPT56Sol",
    "gpt5.6-sol": "LLM_GPT56Sol",
    "gpt56sol": "LLM_GPT56Sol",
    "terra": "LLM_GPT56Terra",
    "gpt-5.6-terra": "LLM_GPT56Terra",
    "gpt5.6-terra": "LLM_GPT56Terra",
    "gpt56terra": "LLM_GPT56Terra",
    "gpt-5.5": "LLM_GPT55",
    "gpt5.5": "LLM_GPT55",
    "gpt55": "LLM_GPT55",
    "xiavier": "LLM_GPT56Sol",
}

NICE_NAMES = {
    "LLM_ChatGLMLLM": "智谱 GLM-4-Flash",
    "LLM_DeepSeekLLM": "DeepSeek Flash",
    "LLM_DeepSeekPro": "DeepSeek Pro",
    "LLM_GPT56Sol": "GPT-5.6 Sol",
    "LLM_GPT56Terra": "GPT-5.6 Terra",
    "LLM_GPT55": "GPT-5.5",
}

SWITCH_LLM_DESC = {
    "type": "function",
    "function": {
        "name": "switch_llm",
        "description": (
            "切换当前对话使用的大模型。"
            "可选：glm/智谱；deepseek/flash；deepseek-pro/pro；"
            "gpt/sol/gpt-5.6-sol；terra/gpt-5.6-terra；gpt-5.5。"
            "用户说换模型、用GPT、用Sol、用Terra、用DeepSeek、切回GLM时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": (
                        "模型别名：glm、deepseek、flash、pro、"
                        "gpt/sol/gpt-5.6-sol、terra/gpt-5.6-terra、gpt-5.5"
                    ),
                }
            },
            "required": ["model"],
        },
    },
}


def _builtin_cfg(model_key: str):
    if model_key == "LLM_DeepSeekLLM":
        return {
            "type": "openai",
            "model_name": "deepseek-flash",
            "base_url": DEEPSEEK_BASE,
            "api_key": DEEPSEEK_KEY,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
    if model_key == "LLM_DeepSeekPro":
        return {
            "type": "openai",
            "model_name": "deepseek-v4-pro",
            "base_url": DEEPSEEK_BASE,
            "api_key": DEEPSEEK_KEY,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
    if model_key == "LLM_GPT56Sol":
        return {
            "type": "openai",
            "model_name": "gpt-5.6-sol",
            "base_url": XIAVIER_BASE,
            "api_key": XIAVIER_KEY,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
    if model_key == "LLM_GPT56Terra":
        return {
            "type": "openai",
            "model_name": "gpt-5.6-terra",
            "base_url": XIAVIER_BASE,
            "api_key": XIAVIER_KEY,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
    if model_key == "LLM_GPT55":
        return {
            "type": "openai",
            "model_name": "gpt-5.5",
            "base_url": XIAVIER_BASE,
            "api_key": XIAVIER_KEY,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
    return {}


def _resolve_llm_config(conn, model_key: str):
    llm_cfgs = (conn.config or {}).get("LLM") or {}
    cfg = dict(llm_cfgs.get(model_key) or {})
    if not cfg:
        if model_key == "LLM_ChatGLMLLM":
            sel = ((conn.config or {}).get("selected_module") or {}).get("LLM")
            if sel and sel in llm_cfgs:
                cfg = dict(llm_cfgs[sel])
        else:
            cfg = _builtin_cfg(model_key)

    if model_key.startswith("LLM_DeepSeek"):
        cfg["type"] = cfg.get("type") or "openai"
        cfg["base_url"] = cfg.get("base_url") or DEEPSEEK_BASE
        if not cfg.get("api_key") or "你的" in str(cfg.get("api_key")):
            cfg["api_key"] = DEEPSEEK_KEY
        if model_key == "LLM_DeepSeekLLM":
            cfg["model_name"] = cfg.get("model_name") or "deepseek-flash"
        if model_key == "LLM_DeepSeekPro":
            cfg["model_name"] = cfg.get("model_name") or "deepseek-v4-pro"
    elif model_key.startswith("LLM_GPT"):
        builtin = _builtin_cfg(model_key)
        cfg["type"] = "openai"
        cfg["base_url"] = XIAVIER_BASE
        cfg["api_key"] = XIAVIER_KEY
        cfg["model_name"] = builtin.get("model_name") or cfg.get("model_name")
        # 部分中转站对无 UA 会 403
        cfg.setdefault("extra_headers", {"User-Agent": "Mozilla/5.0 XiaozhiAgent/1.0"})

    try:
        cfg["max_tokens"] = max(int(cfg.get("max_tokens") or 0), 2048)
    except Exception:
        cfg["max_tokens"] = 2048
    return cfg


def _fuzzy_key(alias: str, raw: str):
    if "terra" in alias:
        return "LLM_GPT56Terra"
    if "5.5" in alias or "gpt55" in alias.replace(".", "").replace("-", ""):
        return "LLM_GPT55"
    if "sol" in alias or "gpt-5.6" in alias or "gpt5.6" in alias or (
        "gpt" in alias and "deep" not in alias
    ):
        return "LLM_GPT56Sol"
    if "pro" in alias and "gpt" not in alias:
        return "LLM_DeepSeekPro"
    if "deep" in alias or ("flash" in alias and "glm" not in alias) or alias == "ds":
        return "LLM_DeepSeekLLM"
    if "glm" in alias or "智谱" in raw:
        return "LLM_ChatGLMLLM"
    return None


@register_function("switch_llm", SWITCH_LLM_DESC, ToolType.SYSTEM_CTL)
def switch_llm(conn, model: str):
    alias = (model or "").strip().lower()
    raw = (model or "").strip()
    key = MODEL_PRESETS.get(alias) or MODEL_PRESETS.get(raw.lower())
    if not key:
        key = _fuzzy_key(alias, raw)
    if not key:
        return ActionResponse(
            Action.RESPONSE,
            "切换失败",
            "可用模型：智谱GLM、DeepSeek Flash/Pro、GPT-5.6 Sol、GPT-5.6 Terra、GPT-5.5。你想用哪个？",
        )

    cfg = _resolve_llm_config(conn, key)
    if not cfg or not cfg.get("api_key"):
        return ActionResponse(Action.RESPONSE, "切换失败", "该模型配置不完整，请检查后台密钥")

    try:
        from core.utils import llm as llm_utils

        llm_type = cfg.get("type", "openai")
        new_llm = llm_utils.create_instance(llm_type, cfg)
        # 尽量给 openai 客户端加 UA，避免中转站 403
        try:
            extra = cfg.get("extra_headers") or {}
            client = getattr(new_llm, "client", None)
            if client is not None and extra:
                # openai SDK: default_headers
                if hasattr(client, "_custom_headers"):
                    client._custom_headers.update(extra)
                default_headers = getattr(client, "default_headers", None)
                if isinstance(default_headers, dict):
                    default_headers.update(extra)
        except Exception:
            pass

        conn.llm = new_llm
        conn._active_llm_id = key
        conn._active_llm_name = cfg.get("model_name") or key
        if "LLM" not in conn.config:
            conn.config["LLM"] = {}
        conn.config["LLM"][key] = cfg
        if "selected_module" not in conn.config:
            conn.config["selected_module"] = {}
        conn.config["selected_module"]["LLM"] = key
        nice = NICE_NAMES.get(key, cfg.get("model_name") or key)
        conn.logger.bind(tag=TAG).info(f"已切换 LLM -> {key} ({cfg.get('model_name')})")
        return ActionResponse(Action.RESPONSE, "切换成功", f"好的，已切换到{nice}，我们继续聊吧")
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"切换LLM失败: {e}")
        return ActionResponse(Action.RESPONSE, "切换失败", f"切换模型失败了：{e}")
