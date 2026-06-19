from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from mini_cc.task_state import TaskPhase, TaskStateMachine
from mini_cc.task_success import (
    extract_task_contract,
    validate_edit,
    validate_plan,
    validate_verification_command,
    validate_verification_output,
)
from mini_cc.tools import ToolResult


class SemanticTaskSuccessTests(unittest.TestCase):
    def test_contract_extracts_paths_symbols_constraints_and_task_type(self) -> None:
        contract = extract_task_contract("Fix `parse_user` in src/foo.py. Do not modify tests. Expected output is 'ok'.")

        self.assertEqual(contract.task_type, "bug_fix")
        self.assertIn("src/foo.py", contract.explicit_paths)
        self.assertIn("parse_user", contract.explicit_symbols)
        self.assertTrue(contract.forbid_tests)
        self.assertIn("ok", contract.acceptance_keywords)

    def test_plan_for_explicit_path_rejects_unrelated_readme(self) -> None:
        contract = extract_task_contract("Fix bug in src/foo.py")
        state = StubState(candidate_files=["src/foo.py"], read_files=["src/foo.py"], planned_files=["README.md"])

        decision = validate_plan(contract, state, "Plan: planned_files: README.md. Verify with python -m unittest discover.")

        self.assertFalse(decision.allow)
        self.assertIn("lack exploration evidence", decision.reason)

    def test_only_modify_constraint_rejects_other_planned_file(self) -> None:
        contract = extract_task_contract("Only modify a.py to fix the bug")
        state = StubState(candidate_files=["a.py", "b.py"], read_files=["a.py", "b.py"], planned_files=["a.py", "b.py"])

        decision = validate_plan(contract, state, "Plan: planned_files: a.py, b.py. Verify with python -m unittest discover.")

        self.assertFalse(decision.allow)
        self.assertIn("only-modify", decision.reason)

    def test_do_not_modify_tests_constraint_rejects_test_edit(self) -> None:
        contract = extract_task_contract("Fix app.py but do not modify tests")
        state = StubState(planned_files=["app.py", "tests/test_app.py"])

        decision = validate_edit(contract, state, ["tests/test_app.py"], "changed_files: tests/test_app.py\nadded_lines: 2\ndeleted_lines: 1")

        self.assertFalse(decision.allow)
        self.assertIn("do-not-modify-tests", decision.reason)

    def test_no_new_files_constraint_rejects_new_edit_target(self) -> None:
        contract = extract_task_contract("Fix app.py with no new files")
        state = StubState(planned_files=["new_helper.py"])

        decision = validate_edit(contract, state, ["new_helper.py"], "changed_files: new_helper.py\nadded_lines: 5\ndeleted_lines: 0")

        self.assertFalse(decision.allow)
        self.assertIn("no-new-files", decision.reason)

    def test_documentation_task_rejects_code_only_plan(self) -> None:
        contract = extract_task_contract("Update README.md documentation")
        state = StubState(candidate_files=["README.md", "app.py"], read_files=["README.md", "app.py"], planned_files=["app.py"])

        decision = validate_plan(contract, state, "Plan: planned_files: app.py. Verify with markdownlint README.md.")

        self.assertFalse(decision.allow)
        self.assertIn("documentation task", decision.reason)

    def test_echo_and_git_diff_are_not_relevant_verification(self) -> None:
        contract = extract_task_contract("Fix bug in app.py")
        state = StubState(modified_files=["app.py"])

        echo = validate_verification_command(contract, state, "echo ok", ["app.py"])
        git_diff = validate_verification_command(contract, state, "git diff", ["app.py"])

        self.assertFalse(echo.is_real_verification)
        self.assertFalse(echo.is_relevant)
        self.assertFalse(git_diff.is_real_verification)

    def test_zero_test_pytest_output_is_not_meaningful(self) -> None:
        prior = validate_verification_command(
            extract_task_contract("Fix bug in app.py"),
            StubState(modified_files=["app.py"]),
            "python -m pytest",
            ["app.py"],
        )

        evidence = validate_verification_output("python -m pytest", "exit_code=0\nstdout:\ncollected 0 items\nno tests ran\nstderr:\n", prior=prior)

        self.assertFalse(evidence.has_meaningful_checks)
        self.assertIn("zero", evidence.meaningful_checks_reason)

    def test_valid_unittest_output_is_meaningful(self) -> None:
        prior = validate_verification_command(
            extract_task_contract("Fix bug in app.py"),
            StubState(modified_files=["app.py"]),
            "python -m unittest discover",
            ["app.py"],
        )

        evidence = validate_verification_output("python -m unittest discover", "exit_code=0\nstdout:\nRan 3 tests in 0.01s\nOK\nstderr:\n", prior=prior)

        self.assertTrue(evidence.is_real_verification)
        self.assertTrue(evidence.is_relevant)
        self.assertTrue(evidence.has_meaningful_checks)

    def test_broad_unittest_verification_for_python_edit_is_relevant(self) -> None:
        contract = extract_task_contract("Fix bug in app.py")

        evidence = validate_verification_command(contract, StubState(modified_files=["app.py"]), "python -m unittest discover", ["app.py"])

        self.assertTrue(evidence.is_real_verification)
        self.assertTrue(evidence.is_relevant)

    def test_state_machine_keeps_failure_summary_for_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("Fix bug in app.py")
            machine.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: def value():\n2:     return 1"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Verify with python -m unittest discover.")
            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            machine.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n"),
            )

            decision = machine.finish_decision()

            self.assertFalse(decision.allow)
            self.assertEqual(decision.next_phase, TaskPhase.REPAIR)
            self.assertIn("boom", machine.state.last_failure_summary)

    def test_non_code_question_does_not_require_semantic_gate(self) -> None:
        machine = TaskStateMachine(Path("."))
        machine.start("What is this project?")

        decision = machine.finish_decision()

        self.assertTrue(decision.allow)
        self.assertFalse(machine.state.is_code_task)

    def test_task_state_artifact_records_semantic_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            machine = TaskStateMachine(root)
            machine.start("Fix bug in app.py")
            machine.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: def value():\n2:     return 1"))
            machine.observe_assistant_text("Plan: planned_files: app.py. Verify with python -m unittest discover.")
            machine.observe_tool_result("replace_text", {"path": "app.py"}, ToolResult("Replaced 1 occurrence(s) in app.py"))
            machine.observe_tool_result(
                "run_shell",
                {"command": "python -m unittest discover"},
                ToolResult("exit_code=0\nstdout:\nRan 1 test in 0.01s\nOK\nstderr:\n"),
            )

            path = machine.write_artifact("completed")
            self.assertIsNotNone(path)
            artifact = json.loads(path.read_text(encoding="utf-8"))  # type: ignore[union-attr]

            self.assertEqual(artifact["status"], "passed")
            self.assertTrue(artifact["process_checks"]["verified"])
            self.assertTrue(artifact["semantic_checks"]["meaningful_verification"])
            self.assertEqual(artifact["task_contract"]["task_type"], "bug_fix")


class StubState:
    def __init__(
        self,
        *,
        candidate_files: list[str] | None = None,
        read_files: list[str] | None = None,
        planned_files: list[str] | None = None,
        modified_files: list[str] | None = None,
    ) -> None:
        self.candidate_files = candidate_files or []
        self.read_files = read_files or []
        self.planned_files = planned_files or []
        self.modified_files = modified_files or []


if __name__ == "__main__":
    unittest.main()
