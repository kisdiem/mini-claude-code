from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.evidence import EvidenceLedger
from mini_cc.runtime_types import EvidenceRecord
from mini_cc.task_runtime import RuntimeFinalEvaluator, TaskRuntime
from mini_cc.task_state import TaskStateMachine
from mini_cc.coding_loop import CodingLoopPolicy
from mini_cc.tools import ToolResult
from mini_cc.verification_policy import VerificationPolicy


class RuntimeArchitectureTests(unittest.TestCase):
    def test_evidence_ledger_serializes_records(self) -> None:
        ledger = EvidenceLedger()
        ledger.start_run("fix failing test")
        ledger.add(EvidenceRecord("tool_call", "read_file", "read app.py", paths=["app.py"]))

        payload = ledger.to_json()

        self.assertEqual(payload[0]["kind"], "task_start")
        self.assertEqual(payload[1]["paths"], ["app.py"])
        self.assertEqual(payload[0]["id"], "ev_000001")
        self.assertEqual(payload[1]["id"], "ev_000002")
        self.assertTrue(payload[0]["timestamp"])
        self.assertEqual(payload[0]["run_id"], ledger.run_id)

    def test_tool_call_and_result_are_correlated(self) -> None:
        ledger = EvidenceLedger(run_id="run_test")

        call = ledger.record_tool_call("read_file", {"path": "app.py"}, "EXPLORE", tool_call_id="toolu_1")
        result = ledger.record_tool_result("read_file", ToolResult("1: x = 1"), "EXPLORE", parent_id=call.id)

        self.assertEqual(result.parent_id, call.id)
        self.assertEqual(call.tool_call_id, "toolu_1")

    def test_verification_policy_rejects_fake_and_runtime_evidence_commands(self) -> None:
        policy = VerificationPolicy()

        for command in ["git diff", "git status", "echo ok", "ls", "cat app.py", "pwd", "find .", "grep x app.py", "context_snapshot"]:
            self.assertFalse(policy.is_real_verification(command), command)
            self.assertIn(policy.classify_command(command), {"fake", "runtime-evidence"})

    def test_verification_policy_accepts_modern_real_commands(self) -> None:
        policy = VerificationPolicy()

        for command in ["uv run pytest", "tox", "nox", "hatch test", "python manage.py test", "bun test", "pnpm run typecheck"]:
            self.assertTrue(policy.is_real_verification(command), command)

    def test_verification_policy_zero_tests_is_not_meaningful(self) -> None:
        policy = VerificationPolicy()

        result = policy.evaluate_command("python -m pytest", "exit_code=0\nstdout:\ncollected 0 items\nno tests ran\nstderr:\n", ["app.py"])

        self.assertFalse(result.passed)
        self.assertTrue(result.is_real_verification)
        self.assertFalse(result.has_meaningful_checks)
        self.assertTrue(result.blockers)

    def test_verification_policy_unittest_zero_tests_is_not_meaningful(self) -> None:
        result = VerificationPolicy().evaluate_command("python -m unittest discover", "exit_code=0\nstdout:\nRan 0 tests in 0.0s\nOK\nstderr:\n", ["app.py"])

        self.assertFalse(result.passed)
        self.assertFalse(result.has_meaningful_checks)
        self.assertEqual(result.parser_name, "pytest_unittest")

    def test_verification_policy_unittest_one_test_passes(self) -> None:
        result = VerificationPolicy().evaluate_command("python -m unittest discover", "exit_code=0\nstdout:\nRan 1 test in 0.0s\nOK\nstderr:\n", ["app.py"])

        self.assertTrue(result.passed)
        self.assertTrue(result.has_meaningful_checks)

    def test_verification_policy_node_missing_script_fails(self) -> None:
        result = VerificationPolicy().evaluate_command("npm run test", "exit_code=1\nstdout:\nnpm ERR! missing script: test\nstderr:\n", ["src/app.ts"])

        self.assertFalse(result.passed)
        self.assertIn("meaningful", " ".join(result.blockers).lower())

    def test_verification_policy_ruff_success_passes_as_lint(self) -> None:
        result = VerificationPolicy().evaluate_command("ruff check", "exit_code=0\nstdout:\nAll checks passed!\nstderr:\n", ["app.py"])

        self.assertTrue(result.passed)
        self.assertEqual(result.command_type, "lint")
        self.assertTrue(result.has_meaningful_checks)

    def test_targeted_verification_has_higher_confidence_than_broad(self) -> None:
        policy = VerificationPolicy()

        targeted = policy.evaluate_command("python -m pytest tests/test_app.py", "exit_code=0\nstdout:\n1 passed\nstderr:\n", ["tests/test_app.py"])
        broad = policy.evaluate_command("python -m pytest", "exit_code=0\nstdout:\n1 passed\nstderr:\n", ["app.py"])

        self.assertEqual(targeted.coverage, "targeted")
        self.assertGreater(targeted.confidence, broad.confidence)
        self.assertTrue(broad.warnings)

    def test_task_runtime_artifact_is_evidence_report(self) -> None:
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

            self.assertEqual(artifact["schema_version"], "1.1")
            self.assertEqual(artifact["status"], "passed")
            self.assertTrue(artifact["evidence"])
            self.assertTrue(any(record["kind"] == "verification_result" for record in artifact["evidence"]))
            self.assertIn("final_decision", artifact)
            self.assertEqual(artifact["final_decision"]["status"], "passed")
            self.assertIn("reason", artifact["final_decision"])

    def test_task_runtime_records_plan_declared_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(root, task_state_machine=TaskStateMachine(root), coding_loop=CodingLoopPolicy(root, enabled=True))
            runtime.start("fix bug in app.py")
            runtime.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            runtime.observe_assistant_text("Plan:\nplanned_files: app.py\nVerify with python -m unittest discover.")

            plan_event = runtime.evidence.latest("plan_declared")

            self.assertIsNotNone(plan_event)
            self.assertEqual(plan_event.paths, ["app.py"])  # type: ignore[union-attr]
            self.assertEqual(plan_event.command, "python -m unittest discover")  # type: ignore[union-attr]

    def test_inferred_plan_records_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = TaskRuntime(root, task_state_machine=TaskStateMachine(root), coding_loop=CodingLoopPolicy(root, enabled=True))
            runtime.start("fix bug in app.py")
            runtime.observe_tool_result("read_file", {"path": "app.py"}, ToolResult("1: value = 1"))
            runtime.observe_assistant_text("Plan: edit app.py and verify with python -m unittest discover.")

            plan_event = runtime.evidence.latest("plan_declared")

            self.assertIsNotNone(plan_event)
            self.assertTrue(plan_event.metadata["warnings"])  # type: ignore[union-attr]

    def test_final_evaluator_repair_limit_cannot_pass(self) -> None:
        class CodingState:
            code_modified = True
            modified_files = ["app.py"]
            repair_attempts = 1
            max_repair_attempts = 1
            last_failure_summary = "boom"
            verification_commands = []

        ledger = EvidenceLedger()
        ledger.record_verification_result(
            VerificationPolicy().evaluate_command("python -m unittest discover", "exit_code=1\nstdout:\nFAIL\nstderr:\nboom\n", ["app.py"])
        )

        decision = RuntimeFinalEvaluator().evaluate(agent_status="completed", task_state=None, coding_state=CodingState(), evidence_ledger=ledger)

        self.assertTrue(decision.allow_final)
        self.assertEqual(decision.status, "max_attempts_reached")
        self.assertNotEqual(decision.status, "passed")


if __name__ == "__main__":
    unittest.main()
