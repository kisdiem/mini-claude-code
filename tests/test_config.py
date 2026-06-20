from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_cc.config import DEFAULT_ANTHROPIC_MODEL, build_config, load_env_file


class ConfigTests(unittest.TestCase):
    def test_load_env_file_and_build_config_reads_central_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp, ".env")
            env_file.write_text(
                "\n".join(
                    [
                        "MINI_CC_PROVIDER=openai",
                        "MINI_CC_MODEL=claude-env",
                        "MINI_CC_OPENAI_MODEL=gpt-env",
                        "MINI_CC_MAX_TOKENS=1234",
                        "MINI_CC_MAX_TURNS=5",
                        "MINI_CC_SHELL_TIMEOUT=17",
                        "MINI_CC_PERMISSION=auto",
                        "MINI_CC_SYSTEM_PROMPT=custom system",
                        "MINI_CC_S20_SYSTEM_PROMPT=custom s20",
                        "OPENAI_API_KEY=sk-test",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_file)

                config = build_config(workspace=tmp)

            self.assertEqual(config.provider, "openai")
            self.assertEqual(config.model, "claude-env")
            self.assertEqual(config.openai_model, "gpt-env")
            self.assertEqual(config.max_tokens, 1234)
            self.assertEqual(config.max_turns, 5)
            self.assertEqual(config.shell_timeout, 17)
            self.assertEqual(config.permission, "auto")
            self.assertEqual(config.system_prompt, "custom system")
            self.assertEqual(config.s20_system_prompt, "custom s20")
            self.assertEqual(config.openai_api_key, "sk-test")

    def test_cli_values_override_environment_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "MINI_CC_MODEL": "env-model",
                    "MINI_CC_MAX_TURNS": "4",
                    "MINI_CC_SHELL_TIMEOUT": "11",
                },
                clear=True,
            ):
                config = build_config(
                    workspace=tmp,
                    model="cli-model",
                    max_turns=9,
                    shell_timeout=22,
                )

            self.assertEqual(config.model, "cli-model")
            self.assertEqual(config.max_turns, 9)
            self.assertEqual(config.shell_timeout, 22)

    def test_defaults_remain_compatible_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                config = build_config(workspace=tmp)

            self.assertEqual(config.model, DEFAULT_ANTHROPIC_MODEL)
            self.assertEqual(config.provider, "anthropic")
            self.assertEqual(config.max_tokens, 4096)
            self.assertEqual(config.max_turns, 8)


if __name__ == "__main__":
    unittest.main()
