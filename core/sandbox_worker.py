"""插件沙箱 worker。

由 core.sandbox.PluginSandbox 以独立进程启动。它只负责：
1. 加载指定插件目录的 tool.py
2. 返回工具元数据
3. 通过 stdin/stdout JSON 行执行工具调用

注意：这是进程隔离 + 超时 + 环境清理，不是 Windows 内核级文件/网络沙箱。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class _ToolSpec:
    name: str
    description: str
    parameters: dict
    func: object
    dangerous: bool = False
    source: str = "plugin"


# 插件只依赖 ToolSpec；注入轻量 stub，避免把 core.tools/PySide6 打进 worker
if "core.tools" not in sys.modules:
    stub = types.ModuleType("core.tools")
    stub.ToolSpec = _ToolSpec  # type: ignore[attr-defined]
    sys.modules["core.tools"] = stub


class _Spec:
    def __init__(self, name: str, description: str, parameters: dict, func, dangerous: bool = False):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func
        self.dangerous = dangerous


class CaptureRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, _Spec] = {}

    def register(self, spec) -> None:
        self.tools[spec.name] = _Spec(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
            func=spec.func,
            dangerous=bool(getattr(spec, "dangerous", False)),
        )


def _load(plugin_dir: Path) -> tuple[CaptureRegistry, dict]:
    metadata = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    entry = plugin_dir / str(metadata.get("entry") or "tool.py")
    if not entry.exists():
        raise FileNotFoundError(f"入口文件不存在：{entry.name}")
    module_name = f"study_ai_sandbox_{abs(hash(str(plugin_dir))) % 1000000}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载插件入口")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register = getattr(module, "register", None)
    if not callable(register):
        raise AttributeError("tool.py 缺少 register(registry)")
    registry = CaptureRegistry()
    register(registry)
    return registry, metadata


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(plugin_dir: str) -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        registry, _metadata = _load(Path(plugin_dir))
    except Exception as exc:  # noqa: BLE001
        _send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    _send(
        {
            "ok": True,
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                    "dangerous": spec.dangerous,
                }
                for spec in registry.tools.values()
            ],
        }
    )
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            _send({"ok": False, "error": "invalid json"})
            continue
        action = request.get("action")
        if action == "shutdown":
            return 0
        if action != "call":
            _send({"ok": False, "error": "unknown action"})
            continue
        name = str(request.get("tool") or "")
        args = request.get("args") or {}
        spec = registry.tools.get(name)
        if spec is None:
            _send({"ok": False, "error": f"unknown tool: {name}"})
            continue
        try:
            output = spec.func(**args)
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            _send({"ok": True, "content": output})
        except Exception as exc:  # noqa: BLE001
            _send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
