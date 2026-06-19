from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.tool_recovery import ToolRecoveryPolicy, classify_tool_failure
from mini_cc.tools import ToolResult, ToolRunner


class FlakyToolRunner(ToolRunner):
    def __init__(self, workspace: Path) -> None:
        super().__init__(
            workspace,
            permission="auto",
            recovery_policy=ToolRecoveryPolicy(max_retries=1, backoff_seconds=0),
        )
        self.calls = 0

    def _run_once(self, name: str, tool_input: dict) -> ToolResult:
        if name == "flaky":
            self.calls += 1
            if self.calls == 1:
                return ToolResult("HTTP 503 temporarily unavailable", is_error=True)
            return ToolResult("ok after retry")
        return super()._run_once(name, tool_input)


class AlwaysFailingToolRunner(ToolRunner):
    def __init__(self, workspace: Path) -> None:
        super().__init__(
            workspace,
            permission="auto",
            recovery_policy=ToolRecoveryPolicy(max_retries=1, backoff_seconds=0, enable_degraded_mode=True),
        )

    def _run_once(self, name: str, tool_input: dict) -> ToolResult:
        if name == "remote":
            return ToolResult("HTTP 503 temporarily unavailable", is_error=True)
        return super()._run_once(name, tool_input)


class ToolRecoveryTests(unittest.TestCase):
    def test_failure_classifier_separates_permission_parameter_and_network(self) -> None:
        permission = classify_tool_failure("write_file", {}, ToolResult("Permission denied in read-only mode", is_error=True))
        parameter = classify_tool_failure("replace_text", {}, ToolResult("Old text was not found", is_error=True))
        network = classify_tool_failure("mcp__docs__search", {}, ToolResult("HTTP 503 temporarily unavailable", is_error=True))

        self.assertEqual(permission.category, "permission_denied")
        self.assertFalse(permission.retryable)
        self.assertEqual(parameter.category, "parameter_error")
        self.assertTrue(parameter.alternative_allowed)
        self.assertEqual(network.category, "transient_network")
        self.assertTrue(network.retryable)

    def test_retry_with_backoff_recovers_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FlakyToolRunner(Path(tmp))

            result = runner.run("flaky", {})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("recovered from transient_network by retry", result.content)
            self.assertEqual(runner.calls, 2)
            recovery = result.metadata["recovery"]
            self.assertTrue(recovery["recovered"])
            self.assertEqual(recovery["recovered_by"], "retry")
            self.assertTrue(recovery["post_failure_verifier"]["passed"])
            self.assertEqual([step["action"] for step in recovery["trace"]], ["classify", "retry"])

    def test_alternative_tool_selection_recovers_missing_read_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            runner = ToolRunner(root, permission="auto", recovery_policy=ToolRecoveryPolicy.default())

            result = runner.run("read_file", {"path": "missing/README.md"})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("recovered from not_found by alternative", result.content)
            self.assertIn("README.md", result.content)
            recovery = result.metadata["recovery"]
            self.assertEqual(recovery["recovered_by"], "alternative")
            self.assertEqual(recovery["trace"][-1]["tool"], "list_files")

    def test_degraded_mode_records_unresolved_recoverable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = AlwaysFailingToolRunner(Path(tmp))

            result = runner.run("remote", {})

            self.assertTrue(result.is_error)
            self.assertIn("[degraded mode]", result.content)
            recovery = result.metadata["recovery"]
            self.assertTrue(recovery["degraded"])
            self.assertFalse(recovery["recovered"])
            self.assertTrue(recovery["post_failure_verifier"]["passed"])

    def test_permission_denial_is_not_retried_or_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only", recovery_policy=ToolRecoveryPolicy.default())

            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})

            self.assertTrue(result.is_error)
            self.assertNotIn("[degraded mode]", result.content)
            recovery = result.metadata["recovery"]
            self.assertEqual(recovery["failure"]["category"], "permission_denied")
            self.assertFalse(recovery["recovered"])
            self.assertEqual([step["action"] for step in recovery["trace"]], ["classify"])
            self.assertTrue(recovery["post_failure_verifier"]["passed"])


if __name__ == "__main__":
    unittest.main()
