"""DeepSeek 视觉模型客户端（OpenAI 兼容 Chat Completions）。

文档：https://api-docs.deepseek.com/zh-cn/guides/vision
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from typing import Callable

import requests
from PIL import Image

LOGGER = logging.getLogger("biliask.llm")

MAX_IMAGE_SIDE = 2000


class LLMError(RuntimeError):
    pass


def encode_image(img: Image.Image, max_side: int = MAX_IMAGE_SIDE) -> str:
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def image_block(img_b64: str) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "original"},
    }


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        max_tokens: int = 1500,
        temperature: float = 0.6,
        timeout: tuple[float, float] = (10.0, 180.0),
        thinking: bool = False,
        reasoning_effort: str = "low",
        retries: int = 2,
        backoff: float = 1.0,
    ) -> None:
        if not api_key:
            raise LLMError("未配置 API Key，请在设置里填写")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.retries = max(0, int(retries))
        self.backoff = max(0.1, float(backoff))

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, payload: dict, stream: bool = False):
        """带指数退避的连接/限流重试；只重试可安全重放的请求。"""
        retry_statuses = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(
                    self.url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    delay = self.backoff * (2**attempt)
                    LOGGER.warning(
                        "云端请求失败，%.1fs 后重试（%s/%s）：%s",
                        delay,
                        attempt + 1,
                        self.retries,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                raise LLMError(f"网络请求失败（已重试 {self.retries} 次）：{exc}") from exc

            if response.status_code in retry_statuses and attempt < self.retries:
                retry_after = response.headers.get("Retry-After")
                delay = self.backoff * (2**attempt)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                LOGGER.warning(
                    "云端返回 HTTP %s，%.1fs 后重试（%s/%s）",
                    response.status_code,
                    delay,
                    attempt + 1,
                    self.retries,
                )
                response.close()
                time.sleep(delay)
                continue
            return response
        raise LLMError(f"网络请求失败：{last_error}")

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": self.max_tokens,
        }
        if self.thinking:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.reasoning_effort
        else:
            # 默认关闭思考：截图快问快答需要正文，思考模式会把 token 预算耗尽
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = self.temperature
        return payload

    def chat(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        payload = self._payload(messages, stream=False)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None and not self.thinking:
            payload["temperature"] = temperature
        try:
            resp = self._post(payload)
        except LLMError:
            raise
        except requests.RequestException as exc:
            raise LLMError(f"网络请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise LLMError(_http_error(resp.status_code, resp.text))
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"响应格式异常：{data}") from exc

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> dict:
        """工具调用轮次（非流式），返回 assistant message（含 tool_calls）。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "thinking": {"type": "disabled"},
            "tools": tools,
            "tool_choice": tool_choice,
        }
        try:
            resp = self._post(payload)
        except LLMError:
            raise
        except requests.RequestException as exc:
            raise LLMError(f"网络请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise LLMError(_http_error(resp.status_code, resp.text))
        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            # 保留 finish_reason，供上层判断“是否被 max_tokens 截断”
            message["_finish_reason"] = choice.get("finish_reason")
            return message
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"响应格式异常：{data}") from exc

    def stream_chat(
        self,
        messages: list[dict],
        on_delta: Callable[[str], None],
        stop_event=None,
    ) -> dict:
        """流式调用，逐段回调 on_delta；返回 finish_reason / 字符数等元信息。"""
        finish_reason = None
        chars = 0
        reasoning_chars = 0
        try:
            resp = self._post(self._payload(messages, stream=True), stream=True)
        except LLMError:
            raise
        except requests.RequestException as exc:
            raise LLMError(f"网络请求失败：{exc}") from exc

        with resp:
            if resp.status_code != 200:
                raise LLMError(_http_error(resp.status_code, resp.text))
            for raw in resp.iter_lines(decode_unicode=True):
                if stop_event is not None and stop_event.is_set():
                    return {"finish_reason": "stopped", "chars": chars}
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                raw = raw.strip()
                if not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    return {"finish_reason": finish_reason, "chars": chars}
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    reasoning_chars += len(reasoning)
                text = delta.get("content")
                if text:
                    chars += len(text)
                    on_delta(text)
        return {
            "finish_reason": finish_reason,
            "chars": chars,
            "reasoning_chars": reasoning_chars,
        }


def _http_error(status: int, body: str) -> str:
    hint = ""
    if status == 401:
        hint = "（API Key 无效，请检查设置）"
    elif status == 402:
        hint = "（账户余额不足）"
    elif status == 429:
        hint = "（请求太频繁，稍后再试）"
    return f"HTTP {status}{hint}：{body[:300]}"


def build_user_message(question: str, img_b64: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "text", "text": question}, image_block(img_b64)],
    }
