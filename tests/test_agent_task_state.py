from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mini_cc.agent import Agent
from mini_cc.llm import MockBlock, MockResponse
from mini_cc.task_state import TaskStateMachine
from mini_cc.tools import ToolRunner


class ImmediateWriteThenFinalProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[Any] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.calls += 1
        self.prompts.append(messages[-1].get("content"))
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_write",
                        name="write_file",
                        input={"path": "app.py", "content": "print('bad')\n"},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="done")])


class HappyCodingProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[Any] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.calls += 1
        self.prompts.append(messages[-1].get("content"))
        if self.calls == 1:
            return MockResponse([MockBlock(type="tool_use", id="toolu_ls", name="list_files", input={"path": ".", "recursive": True})])
        if self.calls == 2:
            return MockResponse([MockBlock(type="tool_use", id="toolu_read", name="read_file", input={"path": "app.py"})])
        if self.calls == 3:
            return MockResponse(
                [
                    MockBlock(type="text", text="Plan: planned_files: app.py. Replace the wrong return value and verify with python -m unittest discover."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_replace",
                        name="replace_text",
                        input={"path": "app.py", "old": "return 1", "new": "return 2"},
                    ),
                ]
            )
        if self.calls == 4:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_test",
                        name="run_shell",
                        input={"command": "python -m unittest discover", "timeout": 20},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="final answer")])


class EditWithoutVerifyProvider(HappyCodingProvider):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        if self.calls >= 3:
            self.calls += 1
            self.prompts.append(messages[-1].get("content"))
            return MockResponse([MockBlock(type="text", text="final answer")])
        return super().complete(messages, tools, system)


class FailingVerifyThenFinalProvider(HappyCodingProvider):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.calls += 1
        self.prompts.append(messages[-1].get("content"))
        if self.calls == 1:
            return MockResponse([MockBlock(type="tool_use", id="toolu_ls", name="list_files", input={"path": ".", "recursive": True})])
        if self.calls == 2:
            return MockResponse([MockBlock(type="tool_use", id="toolu_read", name="read_file", input={"path": "app.py"})])
        if self.calls == 3:
            return MockResponse(
                [
                    MockBlock(type="text", text="Plan: planned_files: app.py. Replace the wrong return value and verify with python -m unittest discover."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_replace",
                        name="replace_text",
                        input={"path": "app.py", "old": "return 1", "new": "return 3"},
                    ),
                ]
            )
        if self.calls == 4:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_test",
                        name="run_shell",
                        input={"command": "python -m unittest discover", "timeout": 20},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="final answer")])


class AgentTaskStateTests(unittest.TestCase):
    def test_agent_blocks_initial_write_and_injects_explore_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = ImmediateWriteThenFinalProvider()
            output: list[str] = []
            agent = Agent(
                provider,  # type: ignore[arg-type]
                ToolRunner(root, permission="auto"),
                max_turns=3,
                output=output.append,
                task_state_machine=TaskStateMachine(root),
            )

            agent.run("fix bug in app.py")

            self.assertFalse((root / "app.py").exists())
            self.assertIn("Task phase blocked", "\n".join(output))
            self.assertIn("Task phase: EXPLORE", json.dumps(provider.prompts, ensure_ascii=False))

    def test_agent_can_complete_read_plan_edit_verify_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sample_project(root)
            provider = HappyCodingProvider()
            output: list[str] = []
            agent = Agent(
                provider,  # type: ignore[arg-type]
                ToolRunner(root, permission="auto"),
                max_turns=8,
                output=output.append,
                task_state_machine=TaskStateMachine(root),
            )

            agent.run("fix bug in app.py")

            self.assertIn("return 2", (root / "app.py").read_text(encoding="utf-8"))
            self.assertIn("final answer", "\n".join(output))
            self.assertNotIn("Task phase blocked", "\n".join(output))

    def test_agent_injects_verify_instruction_after_edit_without_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sample_project(root)
            provider = EditWithoutVerifyProvider()
            agent = Agent(
                provider,  # type: ignore[arg-type]
                ToolRunner(root, permission="auto"),
                max_turns=6,
                output=lambda _text: None,
                task_state_machine=TaskStateMachine(root),
            )

            agent.run("fix bug in app.py")

            self.assertIn("Task phase: VERIFY", json.dumps(provider.prompts, ensure_ascii=False))

    def test_agent_injects_repair_instruction_after_failed_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sample_project(root)
            provider = FailingVerifyThenFinalProvider()
            agent = Agent(
                provider,  # type: ignore[arg-type]
                ToolRunner(root, permission="auto"),
                max_turns=7,
                output=lambda _text: None,
                task_state_machine=TaskStateMachine(root, max_repair_attempts=3),
            )

            agent.run("fix bug in app.py")

            serialized = json.dumps(provider.prompts, ensure_ascii=False)
            self.assertIn("Task phase: REPAIR", serialized)
            self.assertIn("Last failure summary", serialized)

    def _write_sample_project(self, root: Path) -> None:
        Path(root, "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        tests = root / "tests"
        tests.mkdir()
        Path(tests, "test_app.py").write_text(
            "import unittest\n\nfrom app import value\n\nclass AppTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 2)\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
