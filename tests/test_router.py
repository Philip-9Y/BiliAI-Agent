"""模型路由测试：简单问答本地、截图/工具/手动模式云端。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Config  # noqa: E402
from core.model_router import FallbackClient, ModelRouter  # noqa: E402


class _FakeLocal:
    def __init__(self, available: bool = True) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available

    def chat(self, messages, max_tokens=None, temperature=None):
        return "本地回答"

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        return {"role": "assistant", "content": "本地回答"}


class _FakeCloud:
    def chat(self, messages, max_tokens=None, temperature=None):
        return "云端回答"

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        return {"role": "assistant", "content": "云端回答"}


class ModelRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(path=Path(self.tmp.name) / "config.json")
        self.config.data["model_mode"] = "auto"
        self.cloud = _FakeCloud()
        self.local = _FakeLocal()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _router(self, local=None) -> ModelRouter:
        return ModelRouter(self.config, local_client=local or self.local, cloud_client=self.cloud)

    def test_simple_question_uses_local_in_auto(self) -> None:
        client, route = self._router().choose("什么是递归？", has_image=False)
        self.assertEqual(route, "local:auto")
        self.assertIsInstance(client, FallbackClient)

    def test_screenshot_always_uses_cloud(self) -> None:
        client, route = self._router().choose("这个图讲了什么", has_image=True)
        self.assertEqual(route, "cloud:screenshot")
        self.assertIs(client, self.cloud)

    def test_tool_request_uses_cloud_in_auto(self) -> None:
        client, route = self._router().choose("把这段记到笔记里", has_image=False)
        self.assertEqual(route, "cloud:auto")
        self.assertIs(client, self.cloud)

    def test_manual_cloud_mode(self) -> None:
        self.config.data["model_mode"] = "cloud"
        client, route = self._router().choose("什么是递归？", has_image=False)
        self.assertEqual(route, "cloud:manual")
        self.assertIs(client, self.cloud)

    def test_manual_local_is_still_cloud_for_tools(self) -> None:
        self.config.data["model_mode"] = "local"
        _client, route = self._router().choose("什么是递归？", has_image=False)
        self.assertEqual(route, "local:manual")
        _client, route = self._router().choose("把文件读出来", has_image=False)
        self.assertEqual(route, "cloud:local-tool")

    def test_unavailable_local_falls_back_to_cloud(self) -> None:
        client, route = self._router(_FakeLocal(available=False)).choose(
            "什么是递归？", has_image=False
        )
        self.assertEqual(route, "cloud:local-unavailable")
        self.assertIs(client, self.cloud)


if __name__ == "__main__":
    unittest.main()
