"""
服务端长期记忆：只写入智控台/MySQL，绝不写设备、不写本地 yaml。
按设备 MAC（role_id）持久化，跨会话保留用户画像与重要事件。
"""
from __future__ import annotations

import time
from ..base import MemoryProviderBase, logger
from config.manage_api_client import save_mem_local_short
from core.utils.util import check_model_key

TAG = __name__

LONG_TERM_MEMORY_PROMPT = """你是长期记忆管家。任务：根据「本轮对话 + 历史长期记忆」输出更新后的长期记忆全文。

硬性规则：
1. 只提取与用户本人相关的稳定信息（姓名/称呼、喜好、家人、城市、习惯、重要约定、忌讳、常用设备偏好等）。
2. 禁止写入：临时天气、临时点歌、音量调节、退出指令、无意义闲聊、设备报错。
3. 历史记忆中已有的重要信息默认保留，不要无故删除；新信息合并进去。
4. 同一事实只保留最新表述；冲突时以更新时间为准。
5. 输出纯文本摘要，不要 JSON、不要代码块、不要解释。
6. 总长度严格控制在 6000 字以内；超长时优先保留身份/偏好/关系，压缩琐事。
7. 用条目式中文，例如：
- 称呼：...
- 城市：...
- 喜好：...
- 重要事件：YYYY-MM-DD ...
"""


class MemoryProvider(MemoryProviderBase):
    def __init__(self, config, summary_memory=None):
        super().__init__(config)
        self.long_memory = summary_memory or ""
        self.max_chars = int(config.get("max_chars") or 6000)

    def init_memory(
        self, role_id, llm, summary_memory=None, save_to_file=True, **kwargs
    ):
        # save_to_file 参数忽略：本模块强制只存服务端
        super().init_memory(role_id, llm, **kwargs)
        if summary_memory:
            self.long_memory = summary_memory
        logger.bind(tag=TAG).info(
            f"服务端长期记忆已加载 role={role_id} chars={len(self.long_memory or '')}"
        )

    async def save_memory(self, msgs):
        model_info = getattr(self.llm, "model_name", str(self.llm.__class__.__name__))
        logger.bind(tag=TAG).debug(f"长期记忆保存模型: {model_info}")
        api_key = getattr(self.llm, "api_key", None)
        memory_key_msg = check_model_key("长期记忆专用LLM", api_key)
        if memory_key_msg:
            logger.bind(tag=TAG).error(memory_key_msg)
        if self.llm is None:
            logger.bind(tag=TAG).error("LLM is not set for long memory provider")
            return None
        if not msgs or len(msgs) < 2:
            return None

        dialog = []
        for msg in msgs:
            if msg.role == "user":
                dialog.append(f"User: {msg.content}")
            elif msg.role == "assistant":
                dialog.append(f"Assistant: {msg.content}")
        if not dialog:
            return None

        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        prompt_user = (
            "本轮对话：\n"
            + "\n".join(dialog[-30:])
            + "\n\n历史长期记忆：\n"
            + (self.long_memory or "（空）")
            + f"\n\n当前时间：{now}\n请输出更新后的长期记忆全文："
        )

        try:
            result = self.llm.response_no_stream(
                LONG_TERM_MEMORY_PROMPT,
                prompt_user,
                max_tokens=2500,
                temperature=0.2,
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"长期记忆总结失败: {e}")
            return None

        result = (result or "").strip()
        if not result:
            return self.long_memory

        # 去掉可能的代码围栏
        if result.startswith("```"):
            result = result.strip("`")
            if result.startswith("text"):
                result = result[4:].lstrip()

        if len(result) > self.max_chars:
            result = result[: self.max_chars]

        # 强制只写服务端（智控台 MySQL），不写设备、不写本地文件
        try:
            saved = save_mem_local_short(self.role_id, result)
            if saved is None:
                logger.bind(tag=TAG).error(
                    f"写入服务端长期记忆失败(返回空) role={self.role_id}"
                )
                return None
            self.long_memory = result
            logger.bind(tag=TAG).info(
                f"长期记忆已存服务端 role={self.role_id} chars={len(result)}"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"写入服务端长期记忆失败: {e}")
            return None
        return self.long_memory

    async def query_memory(self, query: str) -> str:
        return self.long_memory or ""
