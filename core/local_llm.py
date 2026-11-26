"""本地 Ollama 客户端（用于简单问答，节省云端 token）。"""

from __future__ import annotations

import json
import logging
import time

import requests

from core.llm import LLMError

LOGGER = logging.getLogger("biliask.local")


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-3b:latest",
        max_tokens: int = 1024,
        temperature: float = 0.6,
        num_ctx: int = 4096,
        keep_alive: str = "10m",
        timeout: int = 180,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout = timeout
        self._available_until = 0.0
        self._available_cache = False

    @staticmethod
    def list_models(base_url: str = "http://127.0.0.1:11434", timeout: float = 2.0) -> list[str]:
        try:
            response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
            if response.status_code != 200:
                return []
            data = response.json()
            return [str(item.get("name")) for item in data.get("models", []) if item.get("name")]
        except Exception:  # noqa: BLE001
            return []

    def available(self) -> bool:
        now = time.time()
        if now < self._available_until:
            return self._available_cache
        models = self.list_models(self.base_url)
        self._available_cache = self.model in models
        self._available_until = now + 30
        return self._available_cache

    @staticmethod
    def _flatten(messages: list[dict]) -> list[dict]:
        """Ollama 简单问答不使用图片；把视觉消息压平成文字，避免格式报错。"""
        flat: list[dict] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                content = "\n".join(part for part in parts if part)
            flat.append({"role": message.get("role", "user"), "content": content or ""})
        return flat

    def chat(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": self._flatten(messages),
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": int(max_tokens or self.max_tokens),
                "num_ctx": self.num_ctx,
            },
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise LLMError(f"本地模型连接失败：{exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"本地模型 HTTP {response.status_code}：{response.text[:300]}")
        data = response.json()
        message = data.get("message") or {}
        content = (message.get("content") or "").strip()
        if not content and message.get("thinking"):
            # qwen3 等思考模型可能只返回 thinking；简单问答不采用思考内容，交给云端兜底
            LOGGER.warning("本地模型只返回了 thinking，内容为空")
        return content

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> dict:
        """本地简单问答不执行工具；返回普通 assistant 消息，让 Agent 直接收尾。"""
        content = self.chat(messages)
        return {
            "role": "assistant",
            "content": content,
            "_finish_reason": "stop" if content else "length",
        }

    def stream_chat(self, messages: list[dict], on_delta, stop_event=None) -> dict:
        payload = {
            "model": self.model,
            "messages": self._flatten(messages),
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        finish_reason = None
        chars = 0
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise LLMError(f"本地模型连接失败：{exc}") from exc
        with response:
            if response.status_code != 200:
                raise LLMError(f"本地模型 HTTP {response.status_code}：{response.text[:300]}")
            for raw in response.iter_lines(decode_unicode=True):
                if stop_event is not None and stop_event.is_set():
                    return {"finish_reason": "stopped", "chars": chars}
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                message = data.get("message") or {}
                text = message.get("content") or ""
                if text:
                    chars += len(text)
                    on_delta(text)
                if data.get("done"):
                    finish_reason = data.get("done_reason") or "stop"
                    break
        return {"finish_reason": finish_reason or "stop", "chars": chars}

