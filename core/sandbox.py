"""用户插件的进程级沙箱。

隔离措施：
- 独立 Python/Study-AI 子进程，主进程只通过 JSON 行 RPC 调用
- 每次调用超时后杀进程
- 清空 API Key/Token 等敏感环境变量
- 工作目录固定为插件目录
- 启动时不继承主进程的工具注册表

限制：Windows 下没有内核级文件/网络隔离；这是“进程沙箱”，不是完整安全容器。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


class SandboxError(RuntimeError):
    pass


class SandboxTimeout(SandboxError):
    pass


def _clean_env() -> dict[str, str]:
    sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(word in upper for word in sensitive):
            continue
        if upper in {"PYTHONPATH", "PYTHONHOME"}:
            continue
        env[key] = value
    env["STUDY_AI_SANDBOX"] = "1"
    # 让子进程能找到项目内的 core 包（worker 也会再插入一次）
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class PluginSandbox:
    def __init__(self, plugin_dir: Path, timeout: float = 12.0) -> None:
        self.plugin_dir = Path(plugin_dir).resolve()
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []

    def start(self) -> list[dict]:
        if self.proc is not None:
            return self.tools
        if getattr(sys, "frozen", False):
            # 打包版必须使用独立 console worker；windowed 主程序没有 stdout
            worker_exe = (
                Path(sys.executable).resolve().parent
                / "sandbox"
                / "Study-AI-Sandbox.exe"
            )
            if not worker_exe.exists():
                raise SandboxError("缺少 sandbox/Study-AI-Sandbox.exe，插件沙箱不可用")
            command = [str(worker_exe), str(self.plugin_dir)]
        else:
            worker = Path(__file__).with_name("sandbox_worker.py")
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe":
                console_python = python.with_name("python.exe")
                if console_python.exists():
                    python = console_python
            command = [str(python), "-I", str(worker), str(self.plugin_dir)]
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=str(self.plugin_dir),
                env=_clean_env(),
                creationflags=flags,
            )
            result = self._read_response(self.timeout)
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise SandboxError(f"沙箱启动失败：{exc}") from exc
        if not result.get("ok"):
            self.close()
            raise SandboxError(str(result.get("error") or "沙箱加载失败"))
        self.tools = list(result.get("tools") or [])
        return self.tools

    def _read_response(self, timeout: float) -> dict:
        proc = self.proc
        if proc is None or proc.stdout is None:
            raise SandboxError("沙箱进程未启动")
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                result_queue.put(proc.stdout.readline())
            except Exception as exc:  # noqa: BLE001
                result_queue.put(exc)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            line = result_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise SandboxTimeout(f"插件调用超过 {timeout:.0f} 秒，已终止") from exc
        if isinstance(line, Exception):
            raise SandboxError(f"读取沙箱输出失败：{line}")
        if not line:
            stderr = ""
            if proc.stderr is not None:
                try:
                    stderr = proc.stderr.read()[-1000:]
                except Exception:
                    pass
            self.close()
            raise SandboxError(f"沙箱进程异常退出：{stderr}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise SandboxError(f"沙箱返回非法 JSON：{line[:200]}") from exc

    def call(self, tool: str, arguments: dict) -> str:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise SandboxError("沙箱已关闭")
        if proc.poll() is not None:
            raise SandboxError("沙箱进程已退出")
        try:
            proc.stdin.write(
                json.dumps({"action": "call", "tool": tool, "args": arguments}, ensure_ascii=False)
                + "\n"
            )
            proc.stdin.flush()
            result = self._read_response(self.timeout)
        except (OSError, ValueError) as exc:
            self.close()
            raise SandboxError(f"沙箱调用失败：{exc}") from exc
        if not result.get("ok"):
            raise SandboxError(str(result.get("error") or "插件工具执行失败"))
        return str(result.get("content") or "")

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.write('{"action":"shutdown"}\n')
                proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:  # noqa: BLE001
            proc.kill()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
