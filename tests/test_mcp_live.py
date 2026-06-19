from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.mcp_live import run_live_validation, write_live_validation_report


class MCPLiveValidationTests(unittest.TestCase):
    def test_live_validation_passes_local_transports_failures_refresh_and_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "workspace")
            root.mkdir()
            output = root / ".mini_cc" / "mcp-hook-live"

            report = run_live_validation(root, output)

            self.assertEqual(report.schema_version, "3.3")
            self.assertEqual(report.status, "ready")
            self.assertEqual(report.summary["passed"], report.summary["total"])
            names = {check.name for check in report.checks}
            self.assertIn("stdio_mcp_smoke", names)
            self.assertIn("http_mcp_smoke", names)
            self.assertIn("sse_mcp_smoke", names)
            self.assertIn("websocket_mcp_smoke", names)
            self.assertIn("mcp_failure_and_refresh_classification", names)
            self.assertIn("hook_live_trace_and_trust_profiles", names)

            hook_log = Path(report.artifacts["hook_trace"])
            events = [json.loads(line)["event"] for line in hook_log.read_text(encoding="utf-8").splitlines()]
            for event in ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]:
                self.assertIn(event, events)

    def test_write_live_validation_report_outputs_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "workspace")
            root.mkdir()
            out = Path(tmp, "report")

            paths = write_live_validation_report(root, out)

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            self.assertEqual(payload["schema_version"], "3.3")
            self.assertEqual(payload["status"], "ready")
            self.assertIn("MCP / Hook Live Validation Report", markdown)
            self.assertIn("websocket_mcp_smoke", markdown)


if __name__ == "__main__":
    unittest.main()
