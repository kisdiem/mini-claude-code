from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.evidence import EvidenceLedger, record_task_start, record_verification_result
from mini_cc.runtime_types import EvidenceRecord
from mini_cc.task_runtime import TaskRuntime
from mini_cc.task_state import TaskStateMachine
from mini_cc.coding_loop import CodingLoopPolicy
from mini_cc.tools import ToolResult
from mini_cc.verification_policy import VerificationPolicy


class RuntimeArchitectureTests(unittest.TestCase):
    def test_evidence_ledger_serializes_records(self) -> None:
        ledger = EvidenceLedger()
        ledger.add(record_task_start("fix failing test"))
        ledger.add(EvidenceRecord("tool_call", "read_file", "read app.py", paths=["app.py"]))

        payload = ledger.to_json()

        self.assertEqual(payload[0]["kind"], "task_start")
        self.assertEqual(payload[1]["paths"], ["app.py"])

    def test_verification_policy_rejects_fake_and_runtime_evidence_commands(self) -> None:
        policy = VerificationPolicy()

        for command in ["git diff", "git status", "echo ok", "ls", "cat app.py", "context_snapshot"]:
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

            self.assertEqual(artifact["schema_version"], "1.0")
            self.assertEqual(artifact["status"], "passed")
            self.assertTrue(artifact["evidence"])
            self.assertTrue(any(record["kind"] == "verification_result" for record in artifact["evidence"]))
            self.assertIn("final_decision", artifact)


if __name__ == "__main__":
    unittest.main()
