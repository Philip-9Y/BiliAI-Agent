"""无 GUI 自检（--check）。

目标：不启动 Qt、不弹窗、不依赖 stdout；结果写入 data/selfcheck.json。
退出码：核心检查通过 0，数据库致命错误 1。热键被占用属于 warning。
"""

from __future__ import annotations

import json
import socket
import sys
from datetime import datetime

import requests

from core.config import APP_DIR, DATA_DIR, Config
from core.hotkey import DEFAULT_BINDINGS, MOD_NOREPEAT
from core.store import Store


def _hotkey_check() -> list[dict]:
    if sys.platform != "win32":
        return [{"name": "hotkeys", "ok": True, "detail": "非 Windows，跳过"}]
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    results = []
    for index, (mods, vk, name) in DEFAULT_BINDINGS.items():
        hotkey_id = 0x7A00 + index
        ok = bool(user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk))
        err = ctypes.get_last_error() if not ok else 0
        if ok:
            user32.UnregisterHotKey(None, hotkey_id)
        results.append(
            {
                "name": "hotkey",
                "ok": ok,
                "detail": name if ok else f"{name} 被占用（WinError {err}），可用托盘/界面按钮",
                "warning": not ok,
            }
        )
    return results


def _ollama_check(config: Config) -> dict:
    base = str(config.data.get("local_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = str(config.data.get("local_model") or "qwen2.5-3b:latest")
    try:
        response = requests.get(f"{base}/api/tags", timeout=2)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        names = [item.get("name") for item in response.json().get("models", [])]
        if model in names:
            return {"name": "ollama", "ok": True, "detail": f"已找到 {model}"}
        return {
            "name": "ollama",
            "ok": False,
            "warning": True,
            "detail": f"Ollama 在线，但未找到 {model}；可用模型：{', '.join(map(str, names[:6]))}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "ollama",
            "ok": False,
            "warning": True,
            "detail": f"Ollama 不可用：{type(exc).__name__}: {exc}",
        }


def run_self_check(config: Config, store: Store) -> int:
    checks: list[dict] = []
    fatal = False

    try:
        sessions = store.list_sessions(limit=1)
        checks.append(
            {
                "name": "database",
                "ok": True,
                "detail": f"SQLite 正常，最近会话数 {len(sessions)}",
            }
        )
    except Exception as exc:  # noqa: BLE001
        fatal = True
        checks.append(
            {
                "name": "database",
                "ok": False,
                "warning": False,
                "detail": f"SQLite 打开失败：{type(exc).__name__}: {exc}",
            }
        )

    checks.append(
        {
            "name": "config",
            "ok": True,
            "detail": f"配置：{config.path}",
        }
    )
    if not config.api_key:
        checks.append(
            {
                "name": "api_key",
                "ok": False,
                "warning": True,
                "detail": "未配置云端 API Key；本地问答仍可用",
            }
        )

    model_dir = APP_DIR / "models"
    model_files = list(model_dir.rglob("*.onnx")) if model_dir.exists() else []
    checks.append(
        {
            "name": "embedding_model",
            "ok": bool(model_files),
            "warning": not bool(model_files),
            "detail": (
                f"本地语义模型：{model_dir}"
                if model_files
                else "未找到 models/；语义记忆会回退关键词检索"
            ),
        }
    )
    checks.append(
        {
            "name": "browser_extension",
            "ok": (APP_DIR / "extension" / "manifest.json").exists(),
            "warning": True,
            "detail": "需要手动在 Chrome/Edge 加载 extension/ 目录",
        }
    )

    try:
        port = int(config.data.get("bridge_port", 8765))
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            bridge_ok = True
            bridge_detail = f"端口 {port} 可监听"
        finally:
            probe.close()
    except OSError as exc:
        bridge_ok = False
        bridge_detail = f"端口被占用或不可用：{exc}"
    checks.append(
        {"name": "browser_bridge", "ok": bridge_ok, "warning": True, "detail": bridge_detail}
    )
    checks.append(_ollama_check(config))
    checks.extend(_hotkey_check())

    warnings = [item for item in checks if item.get("warning")]
    report = {
        "ok": not fatal,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": len(warnings),
        "checks": checks,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "selfcheck.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if sys.stdout is not None:
        try:
            print(text, flush=True)
        except Exception:
            pass
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    cfg = Config()
    db = Store(DATA_DIR)
    try:
        raise SystemExit(run_self_check(cfg, db))
    finally:
        db.close()
