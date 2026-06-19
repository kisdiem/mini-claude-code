from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mini_cc.agent import Agent
from mini_cc.coding_loop import CodingLoopPolicy, is_verification_command
from mini_cc.llm import MockBlock, MockResponse
from mini_cc.tools import ToolResult, ToolRunner


class WriteThenFinalProvider:
    def __init__(self) -> None:
        self.prompts: list[Any] = []
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.prompts.append(messages[-1].get("content"))
        self.calls += 1
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_write",
                        name="write_file",
                        input={"path": "app.py", "content": "print('ok')\n"},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="done")])


class WriteThenVerifyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.calls += 1
        last = messages[-1].get("content")
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_write",
                        name="write_file",
                        input={"path": "app.py", "content": "print('ok')\n"},
                    )
                ]
            )
        if isinstance(last, str) and "Verification required" in last:
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


class CodingLoopTests(unittest.TestCase):
    def test_policy_marks_code_modified_for_write_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp))
            policy.start("fix bug")

            policy.observe_tool_result("write_file", {"path": "app.py"}, ToolResult("Wrote app.py"))
            policy.observe_tool_result("replace_text", {"path": "lib.py"}, ToolResult("Replaced 1 occurrence"))
            policy.observe_tool_result("apply_patch", {}, ToolResult("changed_files: a.py, b.py"))

            self.assertTrue(policy.state.code_modified)
            self.assertEqual(policy.state.modified_files, ["app.py", "lib.py", "a.py", "b.py"])

    def test_run_shell_test_command_is_verification(self) -> None:
        self.assertTrue(is_verification_command("python -m unittest discover"))
        self.assertTrue(is_verification_command("npm run lint"))
        self.assertTrue(is_verification_command("go test ./..."))

    def test_non_verification_tools_do_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp))
            policy.start("fix bug")

            policy.observe_tool_result("git_diff", {}, ToolResult("diff"))
            policy.observe_tool_result("git_status", {}, ToolResult("clean"))
            policy.observe_tool_result("run_shell", {"command": "git diff"}, ToolResult("exit_code=0\nstdout:\n\nstderr:\n"))

            self.assertEqual(policy.state.verification_commands, [])

    def test_modified_code_without_verification_blocks_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp))
            policy.start("fix bug")
            policy.observe_tool_result("write_file", {"path": "app.py"}, ToolResult("Wrote app.py"))

            decision = policy.finish_decision()

            self.assertFalse(decision.allow_finish)
            self.assertIn("Verification required", decision.instruction)

    def test_failed_verification_requires_repair_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp), max_repair_attempts=2)
            policy.start("fix bug")
            policy.observe_tool_result("write_file", {"path": "app.py"}, ToolResult("Wrote app.py"))
            policy.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )

            decision = policy.finish_decision()

            self.assertFalse(decision.allow_finish)
            self.assertIn("last verification command failed", decision.reason)
            self.assertIn("make one minimal repair", decision.instruction)

    def test_passed_verification_allows_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp))
            policy.start("fix bug")
            policy.observe_tool_result("write_file", {"path": "app.py"}, ToolResult("Wrote app.py"))
            policy.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=0\nstdout:\nok\nstderr:\n"),
            )

            decision = policy.finish_decision()

            self.assertTrue(decision.allow_finish)
            self.assertEqual(decision.status, "passed")

    def test_repair_limit_allows_failed_finish_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = CodingLoopPolicy(Path(tmp), max_repair_attempts=1)
            policy.start("fix bug")
            policy.observe_tool_result("write_file", {"path": "app.py"}, ToolResult("Wrote app.py"))
            policy.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )
            policy.state.repair_attempts = 1

            decision = policy.finish_decision()

            self.assertTrue(decision.allow_finish)
            self.assertEqual(decision.status, "max_attempts_reached")

    def test_agent_appends_verification_required_message_before_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = WriteThenFinalProvider()
            agent = Agent(
                provider,  # type: ignore[arg-type]
                ToolRunner(Path(tmp), permission="auto"),
                max_turns=3,
                output=lambda _text: None,
                coding_loop=CodingLoopPolicy(Path(tmp), enabled=True),
            )

            agent.run("fix bug")

            serialized_prompts = json.dumps(provider.prompts, ensure_ascii=False)
            self.assertIn("Verification required before final answer", serialized_prompts)

    def test_agent_allows_finish_after_verification_and_writes_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "test_smoke.py").write_text(
                "import unittest\n\nclass SmokeTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            output: list[str] = []
            agent = Agent(
                WriteThenVerifyProvider(),  # type: ignore[arg-type]
                ToolRunner(root, permission="auto"),
                max_turns=5,
                output=output.append,
                coding_loop=CodingLoopPolicy(root, enabled=True),
            )

            agent.run("fix bug")

            joined = "\n".join(output)
            self.assertIn("Summary:", joined)
            self.assertIn("Verification:", joined)
            artifact = json.loads((root / ".mini_cc" / "task-success" / "last-run.json").read_text(encoding="utf-8"))
            self.assertTrue(artifact["coding_loop_enabled"])
            self.assertTrue(artifact["code_modified"])
            self.assertEqual(artifact["status"], "passed")
            self.assertEqual(artifact["verification_commands"][0]["command"], "python -m unittest discover")


if __name__ == "__main__":
    unittest.main()
