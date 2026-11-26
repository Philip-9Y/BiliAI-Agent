"""下载本地语义记忆模型（BGE-small-zh）。

    python scripts/install_embedding_model.py

模型约 90MB，下载到 Study-AI/models/；该目录已加入 .gitignore。
国内网络默认走 hf-mirror.com；如果你有代理，可先设置 HF_ENDPOINT。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def main() -> int:
    from fastembed import TextEmbedding

    cache = ROOT / "models"
    cache.mkdir(parents=True, exist_ok=True)
    print("下载/加载 BAAI/bge-small-zh-v1.5 ->", cache)
    started = time.time()
    model = TextEmbedding(
        model_name="BAAI/bge-small-zh-v1.5",
        cache_dir=str(cache),
        threads=4,
        providers=["CPUExecutionProvider"],
    )
    vector = list(model.embed(["递归树的时间复杂度"]))[0]
    print(f"完成，用时 {time.time() - started:.1f}s，维度 {len(vector)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
