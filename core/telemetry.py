"""轻量可观测性：每次 Agent 运行的耗时、路由、工具数写入 metrics.jsonl。

不记录用户问题正文和截图，避免泄露隐私。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger("biliask.telemetry")
_LOCK = threading.Lock()


class Telemetry:
    def __init__(self, data_dir: Path, enabled: bool = True) -> None:
        self.path = Path(data_dir) / "metrics.jsonl"
        self.enabled = enabled

    def record(self, event: str, **fields) -> None:
        if not self.enabled:
            return
        payload = {"ts": round(time.time(), 3), "event": event, **fields}
        try:
            with _LOCK:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            LOGGER.exception("写 metrics 失败")
