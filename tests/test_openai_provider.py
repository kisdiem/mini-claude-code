from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from mini_cc.llm import OpenAIProvider


class FakeResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(output_text="ok", output=[])


class OpenAIProviderTests(unittest.TestCase):
    def test_messages_to_responses_input_converts_tool_use_and_result(self) -> None:
        messages = [
            {"role": "user", "content": "fix bug"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect."},
                    {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "app.py"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "1: print('ok')", "is_error": False}
                ],
            },
        ]

        payload = OpenAIProvider.messages_to_responses_input(messages)

        self.assertEqual(payload[0], {"role": "user", "content": "fix bug"})
        self.assertEqual(payload[1], {"role": "assistant", "content": "I will inspect."})
        self.assertEqual(payload[2]["type"], "function_call")
        self.assertEqual(payload[2]["call_id"], "call_1")
        self.assertEqual(payload[2]["name"], "read_file")
        self.assertEqual(payload[3]["type"], "function_call_output")
        self.assertEqual(payload[3]["call_id"], "call_1")

    def test_complete_derives_input_from_messages_without_retained_history(self) -> None:
        fake_responses = FakeResponses()
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake_responses)
        provider.model = "gpt-test"
        provider.max_tokens = 100
        provider.reasoning_effort = None

        old_large_result = "OLD_LARGE_TOOL_RESULT" * 100
        provider.complete(
            [
                {"role": "user", "content": "summary"},
                {"role": "assistant", "content": [{"type": "text", "text": "old"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old", "content": old_large_result}]},
            ],
            [],
            "system",
        )
        provider.complete(
            [{"role": "user", "content": "Conversation compaction summary: keep only this"}],
            [],
            "system",
        )

        self.assertFalse(hasattr(provider, "input_items"))
        second_input = fake_responses.requests[-1]["input"]
        self.assertEqual(second_input, [{"role": "user", "content": "Conversation compaction summary: keep only this"}])
        self.assertNotIn("OLD_LARGE_TOOL_RESULT", str(second_input))


if __name__ == "__main__":
    unittest.main()
