"""长期记忆：整理、合并、筛选与检索。

- 整理：把最近的问答交给模型压缩成若干条长期记忆，自动合并相似条目
- 筛选：提问时按关键词重合度挑出最相关的几条注入上下文
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import math
import re
from collections import Counter

import numpy as np

from core.config import Config
from core.embeddings import EmbeddingService
from core.llm import DeepSeekClient
from core.store import Store

LOGGER = logging.getLogger("biliask")

CONSOLIDATE_PROMPT = """你是学习助手的记忆整理器。下面是用户最近的问答记录。

请提取值得长期记住的信息，压缩成不超过 8 条记忆：
1. 用户的偏好和习惯（例如“代码截图希望用黑底”“报告模板用学校统一格式”）；
2. 反复出现的知识点、结论、易错点；
3. 正在进行的任务与进度。

忽略一次性细节、寒暄和临时问题。相似或重复的内容必须合并成一条。
每条记忆不超过 50 个字，用陈述句。

只输出 JSON 数组，不要输出其他内容，格式：
[{"content": "……", "tags": ["标签1", "标签2"]}]
"""


class MemoryManager:
    def __init__(self, store: Store, config: Config, llm_factory=None, embedder=None) -> None:
        self.store = store
        self.config = config
        self._llm_factory = llm_factory
        self.embedder = embedder if embedder is not None else EmbeddingService(config)

    # ------------------------------------------------------------------ 检索
    @staticmethod
    def _grams(text: str) -> set[str]:
        text = (text or "").lower()
        grams = set(re.findall(r"[a-z0-9_]{2,}", text))
        han = re.findall(r"[\u4e00-\u9fff]", text)
        for index in range(len(han) - 1):
            grams.add(han[index] + han[index + 1])
        return grams

    def retrieve(self, question: str, limit: int = 5, context_text: str = "") -> list[dict]:
        # 追问（“这个为什么”“继续”）本身关键词很少，把最近几轮问题一起当检索 query
        query = f"{question} {context_text}".strip()
        question_grams = self._grams(query)
        if not question_grams:
            return []
        memories = self.store.list_memories("", include_disabled=False)
        if not memories:
            return []

        query_vector, memory_vectors = self._embedding_vectors(query, memories)
        scored: list[tuple[float, str, dict]] = []
        for memory in memories:
            memory_grams = self._grams(memory["content"] + " " + memory["tags"])
            overlap = len(question_grams & memory_grams)
            similarity = self._cosine(question_grams, memory_grams)
            semantic = 0.0
            vector = memory_vectors.get(memory["id"])
            if query_vector is not None and vector is not None:
                semantic = self._array_cosine(query_vector, vector)
            # 关键词/字符相似度 + 语义向量混合；模型缺失时 semantic=0，自动回退
            if overlap == 0 and similarity < 0.18 and semantic < 0.38:
                continue
            score = overlap * 2.0 + similarity * 4.0 + semantic * 8.0
            scored.append((score, memory["updated_at"], memory))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [memory for _, _, memory in scored[:limit]]

    def _embedding_vectors(
        self, query: str, memories: list[dict]
    ) -> tuple[np.ndarray | None, dict[str, np.ndarray]]:
        """返回 (query 向量, memory_id → 向量)；模型不可用时返回 (None, {})。"""
        if self.embedder is None:
            return None, {}
        try:
            if not self.embedder.available():
                return None, {}
            vectors: dict[str, np.ndarray] = {}
            missing: list[tuple[dict, str, str]] = []
            model_name = getattr(self.embedder, "model_name", "embedding")
            for memory in memories:
                text = f"{memory['content']} {memory['tags']}".strip()
                text_hash = hashlib.sha1(
                    f"{model_name}:{text}".encode("utf-8")
                ).hexdigest()
                raw = memory.get("embedding")
                if raw and memory.get("embedding_hash") == text_hash:
                    vector = np.frombuffer(raw, dtype=np.float32)
                    if vector.size:
                        vectors[memory["id"]] = vector
                        continue
                missing.append((memory, text, text_hash))

            if missing:
                encoded = self.embedder.embed([item[1] for item in missing])
                if encoded:
                    for (memory, _text, text_hash), vector in zip(missing, encoded):
                        vector = np.asarray(vector, dtype=np.float32)
                        self.store.set_memory_embedding(
                            memory["id"], vector.tobytes(), text_hash
                        )
                        vectors[memory["id"]] = vector

            encoded_query = self.embedder.embed([query])
            query_vector = (
                np.asarray(encoded_query[0], dtype=np.float32)
                if encoded_query
                else None
            )
            return query_vector, vectors
        except Exception:  # noqa: BLE001
            LOGGER.exception("语义记忆检索失败，回退关键词")
            return None, {}

    @staticmethod
    def _array_cosine(left: np.ndarray, right: np.ndarray) -> float:
        left_norm = float(np.linalg.norm(left))
        right_norm = float(np.linalg.norm(right))
        if not left_norm or not right_norm:
            return 0.0
        return float(np.dot(left, right) / (left_norm * right_norm))

    @staticmethod
    def _cosine(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        left_counter = Counter(left)
        right_counter = Counter(right)
        dot = sum(count * right_counter.get(gram, 0) for gram, count in left_counter.items())
        left_norm = math.sqrt(sum(count * count for count in left_counter.values()))
        right_norm = math.sqrt(sum(count * count for count in right_counter.values()))
        if not left_norm or not right_norm:
            return 0.0
        return dot / (left_norm * right_norm)

    def build_context(self, question: str, context_text: str = "") -> str:
        memories = self.retrieve(question, context_text=context_text)
        if not memories:
            return ""
        return "\n".join(f"- {memory['content']}" for memory in memories)

    # ------------------------------------------------------------------ 整理
    def consolidate(self, session_ids: list[str], max_sessions: int = 20) -> dict:
        conversations = self._collect(session_ids[:max_sessions])
        if not conversations:
            return {"added": 0, "merged": 0, "error": "没有可整理的问答记录"}

        prompt = CONSOLIDATE_PROMPT + "\n\n" + "\n\n".join(conversations)
        try:
            client = self._client()
            answer = client.chat([{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("整理记忆失败")
            return {"added": 0, "merged": 0, "error": str(exc)}

        items = self._parse_items(answer)
        if not items:
            return {"added": 0, "merged": 0, "error": "模型没有返回可用的记忆条目"}

        added = 0
        merged = 0
        existing = self.store.list_memories("")
        for item in items:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            tags = ",".join(str(tag) for tag in (item.get("tags") or []) if str(tag).strip())[:80]
            match = self._find_similar(content, existing)
            if match is not None:
                if len(content) >= len(match["content"]):
                    self.store.update_memory(
                        match["id"],
                        content=content,
                        tags=tags or match["tags"],
                        enabled=True,
                    )
                    match["content"] = content
                else:
                    self.store.update_memory(match["id"], enabled=True)
                merged += 1
            else:
                new_id = self.store.add_memory(content, tags, ",".join(session_ids[:3]))
                existing.append({"id": new_id, "content": content, "tags": tags})
                added += 1
        return {"added": added, "merged": merged, "error": None}

    def _collect(self, session_ids: list[str]) -> list[str]:
        conversations = []
        for sid in session_ids:
            session = self.store.get_session(sid)
            messages = self.store.get_messages(sid)
            if not session or not messages:
                continue
            lines = [f"# 记录：{session['question'][:60]}"]
            for message in messages[-6:]:
                role = "问" if message["role"] == "user" else "答"
                lines.append(f"{role}：{message['content'][:200]}")
            conversations.append("\n".join(lines))
        return conversations

    def _client(self):
        if self._llm_factory is not None:
            return self._llm_factory()
        return DeepSeekClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            max_tokens=900,
            temperature=0.2,
        )

    @staticmethod
    def _parse_items(answer: str) -> list[dict]:
        text = answer or ""
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict) and item.get("content")]
            except json.JSONDecodeError:
                pass
        # 兜底：按行解析
        items = []
        for line in text.splitlines():
            line = line.strip().lstrip("-•*0123456789.、 ").strip()
            if line and not line.startswith("{") and len(line) >= 4:
                items.append({"content": line[:60], "tags": []})
        return items[:12]

    @staticmethod
    def _find_similar(content: str, existing: list[dict]) -> dict | None:
        normalized = re.sub(r"\s+", "", content).lower()
        for memory in existing:
            other = re.sub(r"\s+", "", memory["content"]).lower()
            if not other:
                continue
            if normalized in other or other in normalized:
                return memory
            if difflib.SequenceMatcher(None, normalized, other).ratio() >= 0.72:
                return memory
        return None
