"""浏览器桥：本地 HTTP 上报与上下文格式化。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bridge import BrowserBridge  # noqa: E402


class BridgeTest(unittest.TestCase):
    def test_post_context_and_format(self) -> None:
        bridge = BrowserBridge(port=0)
        self.assertTrue(bridge.start())
        try:
            payload = {
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": "递归树_哔哩哔哩_bilibili",
                "currentTime": 125,
                "duration": 600,
                "subtitle": "递归树每一层代表一次递归调用",
                "selection": "T(n)=aT(n/b)+f(n)",
            }
            response = requests.post(
                f"http://127.0.0.1:{bridge.port}/context", json=payload, timeout=3
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            text = bridge.context_text()
            self.assertIn("BV1xx411c7mD", text)
            self.assertIn("02:05", text)
            self.assertIn("递归树每一层", text)
            health = requests.get(
                f"http://127.0.0.1:{bridge.port}/health", timeout=3
            )
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["ok"])
        finally:
            bridge.stop()

    def test_stale_context_is_ignored(self) -> None:
        bridge = BrowserBridge(port=0)
        bridge.update({"title": "旧页面", "url": "https://example.com"})
        self.assertIsNone(bridge.latest(max_age=0))
        self.assertEqual(bridge.context_text(max_age=0), "")

    def test_invalid_payload_rejected(self) -> None:
        bridge = BrowserBridge(port=0)
        self.assertTrue(bridge.start())
        try:
            response = requests.post(
                f"http://127.0.0.1:{bridge.port}/context",
                data="not-json",
                headers={"Content-Type": "application/json"},
                timeout=3,
            )
            self.assertEqual(response.status_code, 400)
        finally:
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
