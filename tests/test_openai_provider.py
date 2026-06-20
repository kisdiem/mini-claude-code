from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from mini_cc.llm import OpenAIProvider, normalize_openai_base_url


class FakeResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(output_text="ok", output=[])


class FailingResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        exc = RuntimeError("503 service temporarily unavailable for responses")
        setattr(exc, "status_code", 503)
        raise exc


class FakeChatCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        message = SimpleNamespace(content="chat ok", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class OpenAIProviderTests(unittest.TestCase):
    def test_normalize_openai_base_url_adds_v1_for_host_only_url(self) -> None:
        self.assertEqual(normalize_openai_base_url("https://yybb.codes"), "https://yybb.codes/v1")
        self.assertEqual(normalize_openai_base_url("https://yybb.codes/v1"), "https://yybb.codes/v1")
        self.assertIsNone(normalize_openai_base_url(None))

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

    def test_messages_to_chat_messages_converts_tool_use_and_result(self) -> None:
        messages = [
            {"role": "user", "content": "fix bug"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect."},
                    {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "app.py"}},
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "1: x = 1"}]},
        ]

        payload = OpenAIProvider.messages_to_chat_messages(messages)

        self.assertEqual(payload[0], {"role": "user", "content": "fix bug"})
        self.assertEqual(payload[1]["role"], "assistant")
        self.assertEqual(payload[1]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(payload[2], {"role": "tool", "tool_call_id": "call_1", "content": "1: x = 1"})

    def test_complete_falls_back_to_chat_completions_when_responses_unavailable(self) -> None:
        fake_chat = FakeChatCompletions()
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=FailingResponses(), chat=SimpleNamespace(completions=fake_chat))
        provider.model = "gpt-test"
        provider.max_tokens = 100
        provider.reasoning_effort = "medium"
        provider.base_url = "https://yybb.codes/v1"
        provider.api_mode = "auto"

        response = provider.complete([{"role": "user", "content": "hello"}], [], "system")

        self.assertEqual(response.content[0].text, "chat ok")
        self.assertEqual(fake_chat.requests[0]["messages"][0], {"role": "system", "content": "system"})
        self.assertNotIn("reasoning", fake_chat.requests[0])

    def test_custom_base_url_prefers_chat_completions_in_auto_mode(self) -> None:
        failing_responses = FailingResponses()
        fake_chat = FakeChatCompletions()
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=failing_responses, chat=SimpleNamespace(completions=fake_chat))
        provider.model = "gpt-test"
        provider.max_tokens = 100
        provider.reasoning_effort = "xhigh"
        provider.base_url = "https://yybb.codes/v1"
        provider.api_mode = "auto"

        response = provider.complete([{"role": "user", "content": "hello"}], [], "system")

        self.assertEqual(response.content[0].text, "chat ok")
        self.assertEqual(failing_responses.requests, [])
        self.assertEqual(fake_chat.requests[0]["model"], "gpt-test")

    def test_responses_mode_does_not_fallback_to_chat(self) -> None:
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.api_mode = "responses"
        exc = RuntimeError("503 service temporarily unavailable for responses")
        setattr(exc, "status_code", 503)

        self.assertFalse(provider._should_fallback_to_chat(exc))


if __name__ == "__main__":
    unittest.main()
