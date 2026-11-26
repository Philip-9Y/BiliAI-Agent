"""模型路由：本地 Ollama 负责简单问答，云端负责截图/工具/复杂任务。"""

from __future__ import annotations

import logging
import re

from core.config import Config
from core.llm import DeepSeekClient
from core.local_llm import OllamaClient

LOGGER = logging.getLogger("biliask.router")

_TOOL_OR_VISION = re.compile(
    r"(笔记|记到|记下|记入|Word|文档|插入|打开|文件|目录|读取|读一下|"
    r"保存|导出|下载|视频|B站|BV号?|链接|网址|UP主|字幕|截图|图片|画面|"
    r"这张图|图中|插件|工具|执行|整理|总结成|"
    r"查一下|搜索|找一下|现在几点|当前时间|"
    r"帮我.*(做|改|写|读|查|找|插|生成|整理|保存))",
    re.IGNORECASE,
)


class FallbackClient:
    """本地优先，本地失败/空回答时自动走云端，保证不丢用户请求。"""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback

    def chat(self, messages, max_tokens=None, temperature=None):
        try:
            text = self.primary.chat(
                messages, max_tokens=max_tokens, temperature=temperature
            ).strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            LOGGER.exception("本地模型 chat 失败，回退云端")
        return self.fallback.chat(messages, max_tokens=max_tokens, temperature=temperature)

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        try:
            result = self.primary.chat_with_tools(messages, tools, tool_choice)
            if (result.get("content") or "").strip():
                return result
            LOGGER.warning("本地模型返回空内容，回退云端")
        except Exception:  # noqa: BLE001
            LOGGER.exception("本地模型调用失败，回退云端")
        return self.fallback.chat_with_tools(messages, tools, tool_choice)


class ModelRouter:
    def __init__(self, config: Config, local_client=None, cloud_client=None) -> None:
        self.config = config
        self._local = local_client or OllamaClient(
            base_url=str(config.data.get("local_base_url") or "http://127.0.0.1:11434"),
            model=str(config.data.get("local_model") or "qwen2.5-3b:latest"),
            max_tokens=int(config.data.get("local_max_tokens", 1024)),
            temperature=float(config.data.get("local_temperature", 0.6)),
            num_ctx=int(config.data.get("local_num_ctx", 4096)),
            keep_alive=str(config.data.get("local_keep_alive") or "10m"),
        )
        self._cloud = cloud_client

    def cloud_client(self):
        if self._cloud is not None:
            return self._cloud
        return DeepSeekClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            max_tokens=int(self.config.data.get("max_tokens", 4096)),
            temperature=float(self.config.data.get("temperature", 0.6)),
        )

    def _local_available(self) -> bool:
        try:
            return bool(self._local.available())
        except Exception:  # noqa: BLE001
            LOGGER.exception("检查本地模型失败")
            return False

    def is_simple(self, question: str) -> bool:
        text = (question or "").strip()
        if not text:
            return False
        limit = int(self.config.data.get("auto_local_max_chars", 300))
        if len(text) > limit:
            return False
        return not bool(_TOOL_OR_VISION.search(text))

    def choose(self, question: str, has_image: bool) -> tuple[object, str]:
        """返回 (client, route_label)。"""
        mode = str(self.config.data.get("model_mode") or "auto").lower()
        cloud = self.cloud_client()

        # 截图/带图会话强制云端：本地 3B 没有视觉能力
        if has_image:
            return cloud, "cloud:screenshot"
        if mode == "cloud":
            return cloud, "cloud:manual"
        if not self._local_available():
            return cloud, "cloud:local-unavailable"

        if mode == "local":
            if self.is_simple(question):
                return FallbackClient(self._local, cloud), "local:manual"
            return cloud, "cloud:local-tool"
        if self.is_simple(question):
            return FallbackClient(self._local, cloud), "local:auto"
        return cloud, "cloud:auto"
