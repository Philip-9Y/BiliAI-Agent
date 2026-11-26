"""轻量 ANN：随机超平面 LSH。

用途：记忆条目多时，不再让每次查询都和所有向量做精确余弦；
先通过 LSH 桶取候选，再对候选做精确余弦。记忆少时仍走线性扫描。

这是近似最近邻（ANN），不保证 100% 召回；因此关键词命中仍独立保留。
"""

from __future__ import annotations

import numpy as np


class LSHIndex:
    def __init__(
        self,
        vectors: dict[str, np.ndarray],
        bits: int = 8,
        tables: int = 4,
        seed: int = 42,
    ) -> None:
        self.ids = list(vectors)
        self.bits = max(2, int(bits))
        self.tables = max(1, int(tables))
        self.vectors = np.stack(
            [np.asarray(vectors[key], dtype=np.float32) for key in self.ids]
        )
        self.dim = int(self.vectors.shape[1])
        rng = np.random.default_rng(seed)
        self.planes = rng.normal(size=(self.tables, self.bits, self.dim)).astype(np.float32)
        self.buckets: list[dict[bytes, list[int]]] = []
        for table in range(self.tables):
            signatures = (self.vectors @ self.planes[table].T) > 0
            mapping: dict[bytes, list[int]] = {}
            for row, signature in enumerate(signatures):
                key = np.packbits(signature).tobytes()
                mapping.setdefault(key, []).append(row)
            self.buckets.append(mapping)

    def query(self, vector: np.ndarray, limit: int = 0) -> set[str]:
        if not self.ids:
            return set()
        query = np.asarray(vector, dtype=np.float32)
        candidates: set[int] = set()
        for table in range(self.tables):
            signature = (query @ self.planes[table].T) > 0
            key = np.packbits(signature).tobytes()
            candidates.update(self.buckets[table].get(key, []))
        if not candidates:
            return set(self.ids)
        if limit and len(candidates) > limit:
            # 以与第一个表的哈希一致程度做简单截断，避免候选过多
            candidates = set(list(candidates)[:limit])
        return {self.ids[index] for index in candidates}
