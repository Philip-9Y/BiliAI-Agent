"""任务级评测：用真实模型跑一组学习任务，统计成功率/耗时/路由。

用法：
    python scripts/eval_tasks.py
    python scripts/eval_tasks.py --limit 3

注意：会真实调用云端/本地模型，产生少量 token 费用；结果只打印，不写入用户历史。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from core.config import Config  # noqa: E402
from core.conversation import ConversationAgent  # noqa: E402
from core.memory import MemoryManager  # noqa: E402
from core.store import Store  # noqa: E402
from core.tools import build_default_registry  # noqa: E402


def run_one(task: dict) -> dict:
    config = Config()
    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / "data")
    sid = store.create_session(task["question"])
    store.add_message(sid, "user", task["question"])
    agent = ConversationAgent(
        store,
        config,
        build_default_registry(),
        memory=MemoryManager(store, config),
    )
    started = time.time()
    agent.run(sid)
    while agent.is_running(sid) and time.time() - started < 180:
        time.sleep(0.1)
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()
    messages = store.get_messages(sid)
    answer = next(
        (row["content"] for row in reversed(messages) if row["role"] == "assistant"),
        "",
    )
    elapsed = round(time.time() - started, 2)
    expected = task.get("expect_any") or []
    passed = bool(answer) and any(word.lower() in answer.lower() for word in expected)
    result = {
        "id": task["id"],
        "passed": passed,
        "route": getattr(agent, "last_route", ""),
        "elapsed_s": elapsed,
        "chars": len(answer),
    }
    store.close()
    tmp.cleanup()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--tasks", default=str(ROOT / "eval" / "tasks.json"))
    args = parser.parse_args()

    app = QCoreApplication.instance() or QCoreApplication([])
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if args.limit:
        tasks = tasks[: args.limit]

    results = []
    for task in tasks:
        print(f"[{task['id']}] running…", flush=True)
        result = run_one(task)
        results.append(result)
        print(
            f"  {'PASS' if result['passed'] else 'FAIL'} "
            f"route={result['route']} {result['elapsed_s']}s {result['chars']} chars",
            flush=True,
        )

    passed = sum(1 for item in results if item["passed"])
    print(f"\n通过 {passed}/{len(results)}")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
