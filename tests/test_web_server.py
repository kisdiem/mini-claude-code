from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_cc.web_server import build_cli_command, redact_secret, run_agent


class WebServerTests(unittest.TestCase):
    def test_build_mock_command_uses_json_cli(self) -> None:
        command, env, workspace, timeout = build_cli_command(
            {
                "provider": "mock",
                "prompt": "s20 snapshot",
                "workspace": ".",
                "permissionMode": "auto",
                "s20": True,
            }
        )

        self.assertIn("-m", command)
        self.assertIn("mini_cc", command)
        self.assertIn("--mock", command)
        self.assertIn("--output-format", command)
        self.assertIn("json", command)
        self.assertEqual(workspace, Path(".").resolve())
        self.assertEqual(timeout, 120)
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")

    def test_build_openai_command_sets_key_in_env_not_command(self) -> None:
        command, env, _workspace, _timeout = build_cli_command(
            {
                "provider": "openai",
                "apiKey": "sk-test-secret",
                "baseUrl": "https://example.test",
                "model": "gpt-test",
                "reasoningEffort": "high",
                "prompt": "list files",
            }
        )

        joined = " ".join(command)
        self.assertIn("--provider openai", joined)
        self.assertIn("--base-url https://example.test", joined)
        self.assertNotIn("sk-test-secret", joined)
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test-secret")

    def test_redact_secret(self) -> None:
        self.assertEqual(redact_secret("key=abc", "abc"), "key=[redacted-api-key]")

    def test_run_agent_returns_validation_error_for_missing_prompt(self) -> None:
        with self.assertRaises(ValueError):
            build_cli_command({"provider": "mock", "prompt": ""})

    def test_run_agent_redacts_timeout_output(self) -> None:
        expired = subprocess.TimeoutExpired(
            cmd=["python"],
            timeout=5,
            output="secret sk-value",
            stderr="bad sk-value",
        )

        with patch("mini_cc.web_server.subprocess.run", side_effect=expired):
            result = run_agent({"provider": "openai", "apiKey": "sk-value", "prompt": "list files"})

        self.assertFalse(result["ok"])
        self.assertIn("[redacted-api-key]", result["stdout"])
        self.assertIn("[redacted-api-key]", result["stderr"])


if __name__ == "__main__":
    unittest.main()
