"""对话式 Agent：在同一个会话里回答问题或调用工具完成任务。

与旧 AgentRunner 的区别：
- 直接读写会话历史（截图/追问都在同一上下文里）
- 工具调用结果持久化到 assistant 消息的 meta 字段，历史可回放
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime

from PIL import Image
from PySide6.QtCore import QObject, Signal

from core.config import Config
from core.llm import SYSTEM_PROMPT, DeepSeekClient, encode_image, image_block
from core.memory import MemoryManager
from core.store import Store
from core.tools import ToolRegistry

LOGGER = logging.getLogger("biliask.chat")

CHAT_SYSTEM_PROMPT = """你是一个桌面学习助手 + 可以调用工具的 Agent。用户可能：
1. 截屏后问学习问题（算法、数学、代码、视频讲解）；
2. 直接用一句话让你帮他做事（查视频、读文件、记笔记、写文件等）。

规则：
- 需要真实信息时调用工具，不要编造工具结果；工具失败就说明并换方法。
- 危险操作会由用户确认；被拒绝就换方案。
- 最终用简体中文回答，Markdown 排版：先给结论，再分点/步骤；公式用 $...$ 或 $$...$$，代码用 ```语言 代码块。
- 用户让你“记笔记/记录下来”时，调用 create_note 工具把内容写进笔记库。
- 用户让你把截图/图片粘贴到 Word 时，按情况调用 insert_clipboard_image_to_word（剪贴板图片）、insert_image_to_word（最近截图/指定文件）或 insert_screenshot_to_word（截当前屏幕）；先确认用户已经打开 Word 文档并把光标放在目标位置。
- 不要输出 HTML，不要寒暄。"""


class ConversationAgent(QObject):
    started = Signal(str)
    step = Signal(str, dict)
    finished = Signal(str, str)
    failed = Signal(str, str)
    confirmation_requested = Signal(str, str, str)

    def __init__(
        self,
        store: Store,
        config: Config,
        registry: ToolRegistry,
        memory: MemoryManager | None = None,
        llm_factory=None,
        max_steps: int = 8,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.config = config
        self.registry = registry
        self.memory = memory
        self._llm_factory = llm_factory
        self.max_steps = max_steps
        self._runs: dict[str, threading.Event] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._confirm_waiters: dict[str, threading.Event] = {}
        self._confirm_results: dict[str, bool] = {}

    # ------------------------------------------------------------------
    def is_running(self, sid: str) -> bool:
        return sid in self._runs

    def stop(self, sid: str | None = None) -> None:
        if sid is None:
            for event in self._stop_events.values():
                event.set()
        else:
            event = self._stop_events.get(sid)
            if event is not None:
                event.set()

    def resolve_confirmation(self, request_id: str, allowed: bool) -> None:
        self._confirm_results[request_id] = allowed
        waiter = self._confirm_waiters.pop(request_id, None)
        if waiter is not None:
            waiter.set()

    def run(self, sid: str, task: str = "") -> None:
        if sid in self._runs:
            self.failed.emit(sid, "这个会话正在生成中")
            return
        stop_event = threading.Event()
        self._runs[sid] = stop_event
        self._stop_events[sid] = stop_event
        self.started.emit(sid)
        threading.Thread(
            target=self._run, args=(sid, task, stop_event), daemon=True, name=f"chat-{sid}"
        ).start()

    # ------------------------------------------------------------------
    def _make_client(self):
        if self._llm_factory is not None:
            return self._llm_factory()
        return DeepSeekClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            max_tokens=int(self.config.data.get("max_tokens", 4096)),
            temperature=float(self.config.data.get("temperature", 0.6)),
        )

    def _prepare_history(self, sid: str, session: dict, rows: list[dict], client):
        """历史过长时，把较早消息压缩成摘要，只保留最近若干条。"""
        summary = (session.get("summary") or "").strip()
        if not bool(self.config.data.get("summary_enabled", True)):
            return rows, summary
        limit = int(self.config.data.get("history_char_limit", 18000))
        keep = max(4, int(self.config.data.get("history_keep_messages", 12)))
        if client is None or len(rows) <= keep:
            return rows, summary
        total_chars = sum(len(row["content"] or "") for row in rows)
        if total_chars <= limit:
            return rows, summary

        recent = rows[-keep:]
        cutoff = int(recent[0]["id"])
        upto = int(session.get("summary_upto") or 0)
        old_rows = [
            row for row in rows if upto < int(row["id"]) < cutoff
        ]
        if old_rows:
            new_summary = self._summarize_history(client, summary, old_rows)
            if new_summary:
                summary = new_summary
                self.store.update_session_summary(sid, summary, cutoff - 1)
        return recent, summary

    def _summarize_history(self, client, existing: str, old_rows: list[dict]) -> str | None:
        lines: list[str] = []
        used = 0
        for row in old_rows[-24:]:
            role = "用户" if row["role"] == "user" else "助手"
            text = (row["content"] or "")[:500]
            lines.append(f"{role}：{text}")
            used += len(text)
            if used >= 8000:
                break
        prompt = (
            "把下面的较早对话压缩成不超过 400 字的学习摘要，保留：关键问题、结论、"
            "公式/定义、未完成事项。不要编造。只输出摘要。\n"
        )
        if existing:
            prompt += f"\n已有摘要（可合并）：\n{existing[:800]}\n"
        prompt += "\n较早对话：\n" + "\n".join(lines)
        try:
            summary = client.chat(
                [
                    {"role": "system", "content": "你是对话摘要器，只输出简洁摘要。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                temperature=0.2,
            ).strip()
        except Exception:  # noqa: BLE001
            LOGGER.exception("长对话摘要失败")
            return None
        if not summary:
            return None
        return summary[:2000]

    def _build_messages(self, sid: str, client=None) -> list[dict]:
        session = self.store.get_session(sid) or {}
        raw_rows = self.store.get_messages(sid)
        rows, history_summary = self._prepare_history(sid, session, raw_rows, client)
        user_texts = [row["content"] for row in rows if row["role"] == "user"]
        question = user_texts[-1] if user_texts else ""
        # 模糊追问（这个/刚才/继续）时，把最近几轮问题也作为记忆检索上下文
        prior_questions = " ".join(user_texts[-3:-1])[:600]
        retrieval_question = self._rewrite_query(client, question, rows)

        system = CHAT_SYSTEM_PROMPT
        context_text = (session.get("context") or "").strip()
        if context_text:
            system += "\n\n【当前屏幕上下文】\n" + context_text
        if history_summary:
            system += "\n\n【较早对话摘要】\n" + history_summary
        if self.memory is not None and retrieval_question:
            memory_text = self.memory.build_context(retrieval_question, prior_questions)
            if memory_text:
                system += (
                    "\n\n【用户的长期记忆（可能相关，参考即可；无关就忽略）】\n" + memory_text
                )
        template = (self.config.note_template or "").strip()
        if template:
            system += (
                "\n\n【记笔记模板】当用户要求记笔记时，调用 create_note，content 按以下结构填写；"
                "如果用户明确指定了格式或详略，以用户要求为准：\n" + template
            )

        messages: list[dict] = [{"role": "system", "content": system}]
        image_cache: dict[str, str] = {}
        first_user_id = next(
            (row["id"] for row in rows if row["role"] == "user"), None
        )
        for row in rows:
            if row["role"] == "user":
                # 每一条用户消息都可能带自己的截图；这样同一会话连续截图不会丢历史
                image_rel = row.get("image_path") or (
                    session.get("image_path") if row["id"] == first_user_id else None
                )
                image_b64: str | None = None
                if image_rel:
                    image_path = self.store.image_abspath(image_rel)
                    if image_path is not None:
                        cache_key = str(image_path)
                        if cache_key not in image_cache:
                            try:
                                image_cache[cache_key] = encode_image(Image.open(image_path))
                            except Exception:  # noqa: BLE001
                                LOGGER.exception("读取截图失败: %s", image_path)
                                image_cache[cache_key] = ""
                        image_b64 = image_cache[cache_key] or None

                if image_b64:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": row["content"]},
                                image_block(image_b64),
                            ],
                        }
                    )
                else:
                    messages.append({"role": "user", "content": row["content"]})
            elif row["role"] == "assistant":
                messages.append({"role": "assistant", "content": row["content"]})
        return messages

    def _rewrite_query(self, client, question: str, rows: list[dict]) -> str:
        """把“这个/刚才/继续”这类追问改写成可用于记忆检索的完整问题。"""
        if client is None or not question:
            return question
        if not bool(self.config.data.get("query_rewrite", True)):
            return question
        if len(rows) < 3:
            return question
        markers = ("这个", "那个", "它", "刚才", "上面", "前面", "继续", "这句", "这一步", "为什么")
        if len(question) > 24 and not any(marker in question for marker in markers):
            return question

        recent = rows[-7:-1]
        lines = []
        for row in recent[-4:]:
            role = "用户" if row["role"] == "user" else "助手"
            lines.append(f"{role}：{(row['content'] or '')[:300]}")
        if not lines:
            return question

        prompt = (
            "你是检索问题改写器。根据最近对话，把用户最新问题改写成一个独立、完整、"
            "适合检索长期记忆的问题。必须保留“这个/刚才/它”指代的对象。"
            "只输出改写后的问题，不要回答问题，不要解释，不超过 60 字。\n\n"
            f"最近对话：\n" + "\n".join(lines) + f"\n\n用户最新问题：{question}"
        )
        try:
            rewritten = client.chat(
                [
                    {"role": "system", "content": "你只做追问改写，输出一行检索问题。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=120,
                temperature=0.1,
            ).strip()
        except Exception:  # noqa: BLE001
            LOGGER.exception("追问改写失败，使用原问题检索")
            return question
        if not rewritten or len(rewritten) > 200:
            return question
        return rewritten

    def _run(self, sid: str, task: str, stop_event: threading.Event) -> None:
        started = time.time()
        steps: list[dict] = []
        try:
            client = self._make_client()
            messages = self._build_messages(sid, client=client)
            if task:
                messages.append({"role": "user", "content": task})
            tools = self.registry.specs()

            final_parts: list[str] = []
            continuations = 0
            empty_retries = 0
            for _ in range(self.max_steps):
                if stop_event.is_set():
                    self.failed.emit(sid, "已停止")
                    return
                message = client.chat_with_tools(messages, tools)
                content = (message.get("content") or "").strip()
                tool_calls = message.get("tool_calls") or []
                finish_reason = message.get("_finish_reason")

                if not tool_calls:
                    if content:
                        final_parts.append(content)

                    # 被 max_tokens 截断：让模型从断点继续写，最多续写 2 次
                    if finish_reason == "length" and continuations < 2:
                        continuations += 1
                        self.step.emit(
                            sid, {"type": "assistant", "text": "回答较长，正在自动续写…"}
                        )
                        if content:
                            messages.append({"role": "assistant", "content": content})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "上一条回答因长度限制被截断。请从截断处直接继续写，"
                                    "不要重复已有内容，不要重新开头。"
                                ),
                            }
                        )
                        continue

                    # 模型偶尔返回空正文：给一次明确的重试机会
                    if not content and not final_parts and empty_retries < 1:
                        empty_retries += 1
                        messages.append(
                            {
                                "role": "user",
                                "content": "请直接输出正文，不要留空；如果无法回答，请说明原因。",
                            }
                        )
                        continue

                    final_text = "".join(final_parts).strip()
                    if not final_text:
                        final_text = "（模型没有返回内容）"
                    truncated = finish_reason == "length"
                    if truncated:
                        final_text += (
                            "\n\n> ⚠️ 回答达到模型长度上限，可能未完整。"
                            "可以在设置里调大「最大回复 tokens」。"
                        )
                    elapsed = round(time.time() - started, 2)
                    meta_data: dict = {"steps": steps}
                    if truncated:
                        meta_data["truncated"] = True
                    self.store.add_message(
                        sid,
                        "assistant",
                        final_text,
                        elapsed_s=elapsed,
                        meta=(
                            json.dumps(meta_data, ensure_ascii=False)
                            if (steps or truncated)
                            else None
                        ),
                    )
                    self.finished.emit(sid, final_text)
                    return

                if content:
                    steps.append({"type": "assistant", "text": content})
                    self.step.emit(sid, {"type": "assistant", "text": content})

                messages.append(
                    {"role": "assistant", "content": content, "tool_calls": tool_calls}
                )
                for call in tool_calls:
                    if stop_event.is_set():
                        self.failed.emit(sid, "已停止")
                        return
                    function = call.get("function") or {}
                    name = function.get("name") or ""
                    raw_arguments = function.get("arguments") or "{}"
                    try:
                        arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
                    except json.JSONDecodeError:
                        arguments = {}
                    self.step.emit(
                        sid, {"type": "tool_call", "name": name, "args": arguments}
                    )
                    steps.append(
                        {"type": "tool_call", "name": name, "args": arguments}
                    )

                    if self._needs_confirmation(name) and not self._ask_confirmation(name, arguments):
                        result_text = "用户拒绝了这次操作，请换一种方式或询问用户。"
                        ok = False
                    else:
                        result = self.registry.execute(name, arguments)
                        result_text = result.content
                        ok = result.ok
                    self.step.emit(
                        sid,
                        {
                            "type": "tool_result",
                            "name": name,
                            "ok": ok,
                            "summary": result_text[:300],
                        },
                    )
                    steps.append(
                        {
                            "type": "tool_result",
                            "name": name,
                            "ok": ok,
                            "summary": result_text[:300],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "content": result_text[:6000],
                        }
                    )
            self.failed.emit(sid, f"超过最大步数（{self.max_steps}），任务未完成")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("对话 Agent 执行失败")
            self.failed.emit(sid, str(exc))
        finally:
            self._runs.pop(sid, None)
            self._stop_events.pop(sid, None)

    def _needs_confirmation(self, tool_name: str) -> bool:
        spec = self.registry.get(tool_name)
        return bool(spec and spec.dangerous)

    def _ask_confirmation(self, tool_name: str, arguments: dict) -> bool:
        request_id = uuid.uuid4().hex[:8]
        waiter = threading.Event()
        self._confirm_waiters[request_id] = waiter
        self.confirmation_requested.emit(
            request_id, tool_name, json.dumps(arguments, ensure_ascii=False)
        )
        if not waiter.wait(timeout=90):
            self._confirm_waiters.pop(request_id, None)
            return False
        return self._confirm_results.pop(request_id, False)
