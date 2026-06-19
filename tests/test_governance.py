from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.governance import deep_merge, load_governance_config
from mini_cc.permission import PermissionPolicy
from mini_cc.tools import ToolRunner


class GovernanceTests(unittest.TestCase):
    def test_deep_merge_overrides_nested_values(self) -> None:
        merged = deep_merge(
            {"permission_policy": {"block_risks": ["network"], "allow_risks": []}},
            {"permission_policy": {"allow_risks": ["verify"]}},
        )

        self.assertEqual(merged["permission_policy"]["block_risks"], ["network"])
        self.assertEqual(merged["permission_policy"]["allow_risks"], ["verify"])

    def test_load_governance_config_reports_unknown_keys_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, ".mini_cc").mkdir()
            Path(root, ".mini_cc", "settings.json").write_text(
                json.dumps(
                    {
                        "unknown": True,
                        "permission_policy": {"block_risks": ["network"]},
                    }
                ),
                encoding="utf-8",
            )
            Path(root, ".mini_cc", "settings.local.json").write_text(
                json.dumps({"permission_policy": {"allow_risks": ["verify"]}}),
                encoding="utf-8",
            )

            config = load_governance_config(root)

            self.assertEqual(len(config.loaded_paths), 2)
            self.assertEqual(config.merged["permission_policy"]["block_risks"], ["network"])
            self.assertEqual(config.merged["permission_policy"]["allow_risks"], ["verify"])
            self.assertTrue(any("unknown top-level key" in issue.message for issue in config.issues))

    def test_governance_warns_about_inline_mcp_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, ".mini_cc").mkdir()
            Path(root, ".mini_cc", "settings.json").write_text(
                json.dumps(
                    {
                        "subagents": {
                            "reader": {
                                "tools": ["mcp__remote__search"],
                                "mcp_servers": [
                                    {
                                        "name": "remote",
                                        "transport": "streamable_http",
                                        "url": "https://example.com/mcp",
                                        "auth_token": "secret-token",
                                        "headers": {"Authorization": "Bearer secret-token"},
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_governance_config(root)

            messages = "\n".join(issue.message for issue in config.issues)
            self.assertIn("prefer auth_token_env", messages)
            self.assertIn("prefer headers_env", messages)

    def test_permission_policy_can_override_read_only_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(
                Path(tmp),
                permission="read-only",
                permission_policy=PermissionPolicy.from_config({"block_risks": ["verify"]}),
            )

            result = runner.run("run_shell", {"command": "python -m unittest", "timeout": 5})

            self.assertTrue(result.is_error)
            self.assertIn("configured permission policy", result.content)


if __name__ == "__main__":
    unittest.main()
