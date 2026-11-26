"""云端客户端重试：503 退避后成功，400 不重试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm import DeepSeekClient, LLMError  # noqa: E402


class _Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}
        self.headers = {}
        self.text = ""

    def json(self):
        return self._payload

    def close(self) -> None:
        pass


class RetryTest(unittest.TestCase):
    def test_retry_503_then_success(self) -> None:
        calls = []
        responses = [
            _Response(503),
            _Response(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]

        def fake_post(*_args, **_kwargs):
            calls.append(1)
            return responses.pop(0)

        client = DeepSeekClient("sk-test", retries=2, backoff=0.01)
        with patch("core.llm.requests.post", side_effect=fake_post):
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "ok")
        self.assertEqual(len(calls), 2)

    def test_400_is_not_retried(self) -> None:
        calls = []

        def fake_post(*_args, **_kwargs):
            calls.append(1)
            return _Response(400)

        client = DeepSeekClient("sk-test", retries=2, backoff=0.01)
        with patch("core.llm.requests.post", side_effect=fake_post):
            with self.assertRaises(LLMError):
                client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
