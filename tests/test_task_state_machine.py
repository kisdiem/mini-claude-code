from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.task_state import TaskPhase, TaskStateMachine
from mini_cc.tools import ToolResult


class TaskStateMachineTests(unittest.TestCase):
    def test_initial_write_is_blocked_until_explore_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("fix bug in app.py")

            decision = machine.before_tool("write_file", {"path": "app.py", "content": "value = 2\n"})

            self.assertFalse(decision.allow)
            self.assertEqual(decision.next_phase, TaskPhase.EXPLORE)
            self.assertIn("Before editing, inspect", decision.instruction)

    def test_replace_without_reading_target_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("fix bug in app.py")
            machine.observe_tool_result("list_files", {"path": "."}, ToolResult("app.py"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Replace the wrong value.")

            decision = machine.before_tool("replace_text", {"path": "app.py", "old": "1", "new": "2"})

            self.assertFalse(decision.allow)
            self.assertEqual(decision.next_phase, TaskPhase.LOCALIZE)
            self.assertIn("read the target file", decision.instruction)

    def test_read_plan_edit_verify_success_allows_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("fix bug in app.py")

            self.assertTrue(machine.before_tool("read_file", {"path": "app.py"}).allow)
            machine.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Change value and verify with python -m unittest discover.")
            self.assertTrue(machine.before_tool("replace_text", {"path": "app.py", "old": "1", "new": "2"}).allow)
            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            self.assertTrue(machine.before_tool("run_shell", {"command": "python -m unittest discover"}).allow)
            machine.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=0\nstdout:\nOK\nstderr:\n"),
            )

            decision = machine.finish_decision()

            self.assertTrue(decision.allow)
            self.assertEqual(machine.state.phase, TaskPhase.FINAL)
            self.assertTrue(machine.state.verification_passed)

    def test_modified_code_without_verification_blocks_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("fix bug in app.py")
            machine.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Change value.")
            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))

            decision = machine.finish_decision()

            self.assertFalse(decision.allow)
            self.assertEqual(decision.next_phase, TaskPhase.VERIFY)
            self.assertIn("real test", decision.instruction)

    def test_failed_verification_enters_repair_and_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("value = 1\n", encoding="utf-8")
            machine = TaskStateMachine(root, max_repair_attempts=1)
            machine.start("fix bug in app.py")
            machine.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Change value.")
            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            machine.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )

            repair = machine.finish_decision()
            self.assertFalse(repair.allow)
            self.assertEqual(repair.next_phase, TaskPhase.REPAIR)
            self.assertIn("last verification failed", repair.instruction.lower())

            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            machine.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )
            final = machine.finish_decision()

            self.assertTrue(final.allow)
            self.assertIn("repair limit reached", final.reason)

    def test_non_code_question_can_finish_without_state_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            machine = TaskStateMachine(Path(tmp))
            machine.start("what is this repository?")

            decision = machine.finish_decision()

            self.assertTrue(decision.allow)
            self.assertEqual(machine.state.task_type, "question")


if __name__ == "__main__":
    unittest.main()
