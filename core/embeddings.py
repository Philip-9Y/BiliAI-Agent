"""本地语义向量：BGE-small-zh（ONNX / fastembed）。

设计原则：
- 模型只从项目 `models/` 加载，不自动联网下载；缺失时自动回退关键词检索。
- 延迟加载，首次记忆检索才会初始化，不拖慢 App 启动。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from core.config import APP_DIR, Config

LOGGER = logging.getLogger("biliask.embeddings")


class EmbeddingService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.model_name = str(
            config.data.get("embedding_model") or "BAAI/bge-small-zh-v1.5"
        )
        cache = config.data.get("embedding_cache_dir") or "models"
        cache_path = Path(str(cache))
        self.cache_dir = cache_path if cache_path.is_absolute() else APP_DIR / cache_path
        self._model = None
        self._failed = False

    @property
    def enabled(self) -> bool:
        return bool(self.config.data.get("embedding_enabled", True))

    def _load(self):
        if self._model is not None:
            return self._model
        if self._failed or not self.enabled:
            return None
        if not self.cache_dir.exists():
            LOGGER.warning("语义模型目录不存在，回退关键词检索: %s", self.cache_dir)
            self._failed = True
            return None
        try:
            # 模型已经在本地；禁止 huggingface 联网检查
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
                threads=4,
                providers=["CPUExecutionProvider"],
            )
            LOGGER.info("语义记忆模型已加载: %s", self.model_name)
            return self._model
        except Exception:  # noqa: BLE001
            LOGGER.exception("语义模型加载失败，回退关键词检索")
            self._failed = True
            return None

    def available(self) -> bool:
        return self._load() is not None

    def embed(self, texts: list[str]) -> list[np.ndarray] | None:
        model = self._load()
        if model is None:
            return None
        try:
            vectors = list(model.embed(texts))
            return [np.asarray(vector, dtype=np.float32) for vector in vectors]
        except Exception:  # noqa: BLE001
            LOGGER.exception("文本向量化失败")
            return None
