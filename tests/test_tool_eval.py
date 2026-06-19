from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.tool_eval import (
    ToolUseCall,
    ToolUseObservation,
    builtin_tool_use_observations,
    builtin_tool_use_scenarios,
    evaluate_tool_use,
    run_builtin_tool_use_eval,
    run_real_tool_use_eval,
)


class ToolUseEvalTests(unittest.TestCase):
    def test_builtin_scenarios_cover_core_tool_use_dimensions(self) -> None:
        dimensions = {scenario.dimension for scenario in builtin_tool_use_scenarios()}

        self.assertEqual(
            dimensions,
            {
                "tool_discovery",
                "tool_selection",
                "parameter_correctness",
                "permission_compliance",
                "hook_intervention",
                "mcp_auth_recovery",
                "mcp_server_failure_recovery",
                "prompt_injection_resistance",
                "tool_bloat_control",
                "result_grounding",
            },
        )

    def test_builtin_observations_pass_builtin_scenarios(self) -> None:
        report = evaluate_tool_use(builtin_tool_use_scenarios(), builtin_tool_use_observations())

        self.assertEqual(report.schema_version, "3.2")
        self.assertEqual(report.total, 10)
        self.assertEqual(report.passed, 10)
        self.assertEqual(report.score, 1.0)

    def test_evaluator_flags_bad_tool_selection_and_parameters(self) -> None:
        scenarios = [
            scenario
            for scenario in builtin_tool_use_scenarios()
            if scenario.id in {"tool-selection-read-file", "parameter-correctness-search"}
        ]
        observations = [
            ToolUseObservation(
                "tool-selection-read-file",
                calls=[ToolUseCall("run_shell", {"command": "type README.md"})],
            ),
            ToolUseObservation(
                "parameter-correctness-search",
                calls=[ToolUseCall("search_text", {"pattern": "wrong", "path": "."})],
            ),
        ]

        report = evaluate_tool_use(scenarios, observations)

        self.assertEqual(report.passed, 0)
        failed_checks = {
            check["name"]
            for result in report.results
            for check in result.checks
            if not check["passed"]
        }
        self.assertIn("called:read_file", failed_checks)
        self.assertIn("forbidden:run_shell", failed_checks)
        self.assertIn("parameters:search_text", failed_checks)

    def test_run_builtin_tool_use_eval_writes_json_markdown_and_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = run_builtin_tool_use_eval(Path(tmp))

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            scenarios = json.loads(paths["scenarios"].read_text(encoding="utf-8"))

            self.assertEqual(payload["schema_version"], "3.2")
            self.assertEqual(payload["passed"], 10)
            self.assertIn("Tool-use Evaluation Report", markdown)
            self.assertEqual(len(scenarios), 10)

    def test_run_tool_use_eval_can_load_observations_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observations = root / "observations.json"
            observations.write_text(
                json.dumps(
                    {
                        "observations": [
                            {
                                "scenario_id": "tool-discovery-readme",
                                "exposed_tools": ["list_files", "read_file", "search_text"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            paths = run_builtin_tool_use_eval(root / "out", observations)
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

            self.assertEqual(payload["total"], 10)
            self.assertLess(payload["passed"], 10)

    def test_real_tool_use_eval_runs_agent_trace_and_writes_per_scenario_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "workspace")
            root.mkdir()
            Path(root, "README.md").write_text("# Mini Claude Code\n\nHooks and tools are documented here.\n", encoding="utf-8")
            docs = root / "docs"
            docs.mkdir()
            Path(docs, "hooks.md").write_text("hook runtime notes\n", encoding="utf-8")

            paths = run_real_tool_use_eval(root / ".mini_cc" / "tool-use-eval", root)

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            trace = json.loads(paths["trace"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            trace_dir = root / ".mini_cc" / "tool-use-eval" / "traces"

            self.assertEqual(payload["passed"], 10)
            self.assertEqual(trace["schema_version"], "3.2")
            self.assertEqual(len(trace["observations"]), 10)
            self.assertEqual(len(list(trace_dir.glob("*.json"))), 10)
            self.assertIn("Observed Tool Calls", markdown)
            self.assertIn("search_text", markdown)
            self.assertNotEqual(trace["observations"], [item.to_json() for item in builtin_tool_use_observations()])


if __name__ == "__main__":
    unittest.main()
