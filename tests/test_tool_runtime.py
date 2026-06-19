from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.tool_runtime import (
    TOOL_RUNTIME_CAPABILITIES,
    build_tool_runtime_report,
    write_tool_runtime_evidence_smoke,
    write_tool_runtime_report,
)


class ToolRuntimeReportTests(unittest.TestCase):
    def test_tool_runtime_report_covers_v3_capability_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = build_tool_runtime_report(Path(tmp))

            names = {capability.name for capability in report.capabilities}
            self.assertEqual(names, set(TOOL_RUNTIME_CAPABILITIES))
            self.assertEqual(report.schema_version, "3.15")
            self.assertEqual(report.status, "needs_evidence")
            self.assertEqual(report.summary["implemented"], report.summary["total"])
            self.assertLess(report.summary["production_ready"], report.summary["total"])
            self.assertLess(report.summary["score"], 1.0)
            self.assertLess(report.summary["evidence_score"], 1.0)

            by_name = {capability.name: capability for capability in report.capabilities}
            self.assertTrue(by_name["mcp_registry"].implemented)
            self.assertFalse(by_name["mcp_registry"].observed)
            self.assertIn(str(Path(tmp, ".mini_cc", "mcp-registry.json")), by_name["mcp_registry"].missing_evidence)
            self.assertTrue(by_name["broad_event_coverage"].implemented)
            self.assertFalse(by_name["broad_event_coverage"].production_ready)
            self.assertIn(".mini_cc/hooks.log with runtime events", by_name["broad_event_coverage"].missing_evidence)
            self.assertFalse(by_name["tool_use_benchmark"].observed)
            self.assertIn("tool-use runtime trace artifact", by_name["tool_use_benchmark"].missing_evidence)

    def test_report_reads_registry_eval_and_hook_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mini = root / ".mini_cc"
            mini.mkdir()
            Path(mini, "mcp-registry.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "local",
                                "health_status": "ok",
                                "tools": [{"name": "search", "quality": {"score": 0.9}}],
                                "resources": [{"uri": "resource://doc", "governance": {"read_allowed_by_policy": True}}],
                                "prompts": [{"name": "review", "governance": {"get_allowed_by_policy": True}}],
                            }
                        ],
                        "capability_index": {"search": ["local.search"]},
                    }
                ),
                encoding="utf-8",
            )
            eval_dir = mini / "tool-use-eval"
            eval_dir.mkdir()
            Path(eval_dir, "tool-use-eval.json").write_text(
                json.dumps({"schema_version": "2.8", "score": 1.0, "total": 10}),
                encoding="utf-8",
            )
            Path(mini, "hooks.log").write_text(
                "\n".join(
                    json.dumps({"event": event})
                    for event in [
                        "UserPromptSubmit",
                        "InstructionsLoaded",
                        "SessionEnd",
                        "FileChanged",
                        "WorktreeCreate",
                        "TaskCreated",
                        "TaskCompleted",
                        "SubagentStart",
                        "SubagentStop",
                        "PreCompact",
                        "PostCompact",
                        "StopFailure",
                        "ConfigChange",
                    ]
                ),
                encoding="utf-8",
            )
            Path(eval_dir, "tool-use-trace.json").write_text(
                json.dumps({"source": "runtime_trace", "events": [{"tool": "read_file"}]}),
                encoding="utf-8",
            )

            report = build_tool_runtime_report(root)
            by_name = {capability.name: capability for capability in report.capabilities}

            self.assertEqual(by_name["mcp_registry"].metrics["servers"], 1)
            self.assertEqual(by_name["mcp_registry"].metrics["tools"], 1)
            self.assertEqual(by_name["resource_prompt_governance"].metrics["resources"], 1)
            self.assertEqual(by_name["resource_prompt_governance"].metrics["prompts"], 1)
            self.assertEqual(by_name["tool_use_benchmark"].metrics["latest_score"], 1.0)
            self.assertGreater(by_name["broad_event_coverage"].metrics["observed_events"], 0)
            self.assertTrue(by_name["mcp_registry"].production_ready)
            self.assertTrue(by_name["mcp_health_capability_index"].production_ready)
            self.assertTrue(by_name["broad_event_coverage"].production_ready)
            self.assertTrue(by_name["tool_use_benchmark"].production_ready)

    def test_write_tool_runtime_report_outputs_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "workspace")
            root.mkdir()
            out = Path(tmp, "reports")

            paths = write_tool_runtime_report(root, out)

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            self.assertEqual(payload["schema_version"], "3.15")
            self.assertIn("Tool-Use Runtime v3.15 Evidence Report", markdown)
            self.assertIn("Production-ready score", markdown)
            self.assertIn("Missing evidence", markdown)
            self.assertEqual(len(payload["capabilities"]), len(TOOL_RUNTIME_CAPABILITIES))
            self.assertIn("production_ready", payload["capabilities"][0])

    def test_evidence_smoke_materializes_missing_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "workspace")
            root.mkdir()

            before = build_tool_runtime_report(root)
            self.assertLess(before.summary["production_ready"], before.summary["total"])

            paths = write_tool_runtime_evidence_smoke(root)

            for path in paths.values():
                self.assertTrue(path.exists(), path)
            after = build_tool_runtime_report(root)
            by_name = {capability.name: capability for capability in after.capabilities}

            self.assertEqual(after.status, "ready")
            self.assertEqual(after.summary["production_ready"], after.summary["total"])
            self.assertEqual(after.summary["score"], 1.0)
            self.assertTrue(by_name["mcp_registry"].production_ready)
            self.assertTrue(by_name["mcp_health_capability_index"].production_ready)
            self.assertTrue(by_name["broad_event_coverage"].production_ready)
            self.assertTrue(by_name["tool_use_benchmark"].production_ready)


if __name__ == "__main__":
    unittest.main()
