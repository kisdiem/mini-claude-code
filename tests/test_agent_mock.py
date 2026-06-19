from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mini_cc.agent import Agent
from mini_cc.llm import MockBlock, MockProvider, MockResponse
from mini_cc.permission_ledger import PermissionLedger
from mini_cc.session import SessionStore
from mini_cc.tools import ToolResult, ToolRunner


class RepeatingToolProvider:
    def __init__(self, calls: int) -> None:
        self.calls = calls
        self.seen = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del messages, tools, system
        if self.seen >= self.calls:
            return MockResponse([MockBlock(type="text", text="done")])
        self.seen += 1
        return MockResponse(
            [
                MockBlock(type="text", text=f"calling big tool {self.seen}"),
                MockBlock(
                    type="tool_use",
                    id=f"toolu_big_{self.seen}",
                    name="big_tool",
                    input={"index": self.seen, "mode": "fail" if self.seen == 2 else "ok"},
                ),
            ]
        )


class BigToolRunner:
    root = Path(".")

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "big_tool",
                "description": "Return a large deterministic result.",
                "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}}},
            }
        ]

    def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        index = int(tool_input.get("index", 0))
        content = f"result-{index} " + ("x" * 1200)
        if tool_input.get("mode") == "fail":
            return ToolResult("failure detail " + content, is_error=True)
        return ToolResult(content)


class HugeToolRunner(BigToolRunner):
    def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        del name, tool_input
        return ToolResult("huge-result " + ("y" * 7000))


class RecordingToolProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.context_token_estimates: list[int] = []
        self.received_messages: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        payload = {"system": system, "tools": tools, "messages": messages}
        self.context_token_estimates.append(max(1, (len(json.dumps(payload, ensure_ascii=False)) + 3) // 4))
        self.received_messages.append(json.loads(json.dumps(messages)))
        self.calls += 1
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(type="text", text="calling huge tool"),
                    MockBlock(type="tool_use", id="toolu_huge", name="big_tool", input={"index": 1}),
                ]
            )
        return MockResponse([MockBlock(type="text", text="done")])


class WriteOnceProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del messages, tools, system
        self.calls += 1
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_write",
                        name="write_file",
                        input={"path": "note.txt", "content": "hello"},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="done")])


class AgentMockTests(unittest.TestCase):
    def test_mock_agent_calls_tool_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "README.md").write_text("# Demo\n", encoding="utf-8")
            output: list[str] = []
            agent = Agent(
                MockProvider(),
                ToolRunner(Path(tmp), permission="auto"),
                max_turns=3,
                output=output.append,
            )

            agent.run("list files")

            joined = "\n".join(output)
            self.assertIn("[tool] list_files", joined)
            self.assertIn("README.md", joined)
            self.assertIn("Mock provider received the tool result", joined)

    def test_zero_max_turns_allows_until_model_finishes(self) -> None:
        output: list[str] = []
        agent = Agent(
            RepeatingToolProvider(calls=2),
            BigToolRunner(),  # type: ignore[arg-type]
            max_turns=0,
            output=output.append,
        )

        agent.run("run until done")

        joined = "\n".join(output)
        self.assertIn("calling big tool 1", joined)
        self.assertIn("calling big tool 2", joined)
        self.assertIn("done", joined)
        self.assertNotIn("Stopped after max_turns", joined)

    def test_agent_compacts_old_tool_turns_with_structured_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp, "sessions")
            agent = Agent(
                RepeatingToolProvider(calls=4),
                BigToolRunner(),  # type: ignore[arg-type]
                max_turns=6,
                output=lambda _text: None,
                session_store=SessionStore(sessions),
                compaction_token_budget=1400,
                compaction_keep_recent_messages=2,
            )

            agent.run("start long tool workflow")

            session_files = list(sessions.glob("*.json"))
            self.assertEqual(len(session_files), 1)
            payload = json.loads(session_files[0].read_text(encoding="utf-8"))
            messages = payload["messages"]
            summary = messages[0]["content"]
            event_names = [event["event"] for event in payload["events"]]
            self.assertEqual(messages[0]["role"], "user")
            self.assertIn("Conversation compaction summary", summary)
            self.assertIn("tool=big_tool", summary)
            self.assertIn('"index":', summary)
            self.assertIn("status=error", summary)
            self.assertIn("failure detail", summary)
            self.assertIn("conversation_compacted", event_names)
            self.assertLess(len(json.dumps(messages)), 3600)

    def test_agent_model_context_budget_applies_to_provider_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp, "sessions")
            provider = RecordingToolProvider()
            agent = Agent(
                provider,
                HugeToolRunner(),  # type: ignore[arg-type]
                max_turns=3,
                output=lambda _text: None,
                session_store=SessionStore(sessions),
                compaction_token_budget=10000,
                compaction_keep_recent_messages=2,
                model_context_token_budget=1200,
            )

            agent.run("start huge tool workflow")

            self.assertGreaterEqual(len(provider.context_token_estimates), 2)
            self.assertLessEqual(provider.context_token_estimates[-1], 1200)
            second_messages = provider.received_messages[-1]
            serialized = json.dumps(second_messages)
            self.assertIn("tool result summarized by model context budget", serialized)
            self.assertNotIn("y" * 3000, serialized)
            payload = json.loads(next(sessions.glob("*.json")).read_text(encoding="utf-8"))
            event_names = [event["event"] for event in payload["events"]]
            self.assertIn("model_context_budget_applied", event_names)

    def test_agent_permission_ledger_records_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp, "sessions")
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            tools = ToolRunner(
                Path(tmp),
                permission="auto",
                permission_ledger=PermissionLedger(ledger_path),
            )
            agent = Agent(
                WriteOnceProvider(),  # type: ignore[arg-type]
                tools,
                max_turns=3,
                output=lambda _text: None,
                session_store=SessionStore(sessions),
            )

            agent.run("write note")

            session_id = next(sessions.glob("*.json")).stem
            rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["decision"], "allowed")
            self.assertEqual(rows[0]["name"], "write_file")
            self.assertEqual(rows[0]["session_id"], session_id)


if __name__ == "__main__":
    unittest.main()
