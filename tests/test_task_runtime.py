from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.coding_loop import CodingLoopPolicy
from mini_cc.task_runtime import TaskRuntime
from mini_cc.task_state import TaskPhase, TaskStateMachine
from mini_cc.tools import ToolResult


class TaskRuntimeTests(unittest.TestCase):
    def test_process_blocker_takes_priority_over_coding_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(
                root,
                task_state_machine=TaskStateMachine(root),
                coding_loop=CodingLoopPolicy(root, enabled=True),
            )
            runtime.start("fix bug in app.py")

            decision = runtime.before_tool("write_file", {"path": "app.py", "content": "value = 2\n"})

            self.assertFalse(decision.allow)
            self.assertEqual(decision.source, "task_state")
            self.assertIn("EXPLORE", decision.instruction)

    def test_coding_loop_blocks_after_state_machine_allows_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(
                root,
                task_state_machine=TaskStateMachine(root),
                coding_loop=CodingLoopPolicy(root, enabled=True),
            )
            runtime.start("fix bug in app.py")
            runtime.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            runtime.observe_assistant_text("Plan: planned_files: app.py. Change value and verify with python -m unittest discover.")
            runtime.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))

            decision = runtime.finish_decision()

            self.assertFalse(decision.allow)
            self.assertIn(decision.source, {"task_state", "coding_loop"})
            self.assertIn("verification", decision.instruction.lower())

    def test_repair_instruction_contains_failure_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(
                root,
                task_state_machine=TaskStateMachine(root),
                coding_loop=CodingLoopPolicy(root, enabled=True, max_repair_attempts=2),
            )
            runtime.start("fix bug in app.py")
            runtime.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            runtime.observe_assistant_text("Plan: planned_files: app.py. Change value and verify with python -m unittest discover.")
            runtime.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            runtime.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )

            decision = runtime.finish_decision()

            self.assertFalse(decision.allow)
            self.assertIn("Failed command: python -m unittest discover", decision.instruction)
            self.assertIn("Exit code: 1", decision.instruction)
            self.assertIn("Modified files: app.py", decision.instruction)
            self.assertIn("boom", decision.instruction)

    def test_merged_artifact_contains_process_semantic_and_coding_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(
                root,
                task_state_machine=TaskStateMachine(root),
                coding_loop=CodingLoopPolicy(root, enabled=True),
            )
            runtime.start("fix bug in app.py")
            runtime.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            runtime.observe_assistant_text("Plan: planned_files: app.py. Change value and verify with python -m unittest discover.")
            runtime.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            runtime.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=0\nstdout:\nRan 1 test in 0.01s\nOK\nstderr:\n"),
            )

            artifact_path = runtime.write_artifact("completed")
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]

            self.assertEqual(artifact["status"], "passed")
            self.assertIn("process_checks", artifact)
            self.assertIn("semantic_checks", artifact)
            self.assertIn("coding_loop_state", artifact)
            self.assertEqual(artifact["modified_files"], ["app.py"])
            self.assertEqual(artifact["planned_files"], ["app.py"])


if __name__ == "__main__":
    unittest.main()
