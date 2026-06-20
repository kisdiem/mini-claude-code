from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from io import StringIO
from contextlib import redirect_stdout

from mini_cc.cli import build_agent, extract_benchmark_hints, parse_args, permission_mode, prompt_text, system_prompt_for_workspace


class CliHarnessArgsTests(unittest.TestCase):
    def test_harness_run_command_args(self) -> None:
        args = parse_args(
            [
                "run",
                "--prompt",
                "s20 snapshot",
                "--model",
                "claude-sonnet",
                "--workspace",
                ".",
                "--permission-mode",
                "bypass",
                "--output-format",
                "json",
            ]
        )

        self.assertEqual(prompt_text(args), "s20 snapshot")
        self.assertEqual(args.model, "claude-sonnet")
        self.assertEqual(permission_mode(args), "auto")
        self.assertEqual(args.output_format, "json")
        self.assertFalse(args.evidence_mode)

    def test_evidence_command_sets_golden_path_defaults(self) -> None:
        args = parse_args(["evidence", "--workspace", ".", "--prompt", "fix the failing test"])

        self.assertTrue(args.evidence_mode)
        self.assertTrue(args.s20)
        self.assertTrue(args.coding_loop)
        self.assertEqual(permission_mode(args), "auto")
        self.assertEqual(args.output_format, "json")
        self.assertEqual(prompt_text(args), "fix the failing test")

    def test_evidence_command_does_not_break_run_command(self) -> None:
        args = parse_args(["run", "--workspace", ".", "--prompt", "list files"])

        self.assertFalse(args.evidence_mode)
        self.assertFalse(args.s20)
        self.assertEqual(args.output_format, "text")

    def test_help_mentions_evidence_first_and_evidence_report(self) -> None:
        buffer = StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(buffer):
                parse_args(["--help"])

        help_text = buffer.getvalue()
        self.assertIn("evidence-first", help_text)
        self.assertIn("Evidence Report", help_text)

    def test_openai_provider_arg(self) -> None:
        args = parse_args(["run", "--provider", "openai", "--model", "gpt-5", "--prompt", "list files"])
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.model, "gpt-5")
        self.assertEqual(prompt_text(args), "list files")

    def test_build_agent_enables_task_state_machine_without_coding_loop_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = parse_args(["run", "--mock", "--workspace", tmp, "--prompt", "list files"])

            agent = build_agent(args, output=lambda _text: None)

            self.assertIsNotNone(agent.task_state_machine)
            self.assertIsNone(agent.coding_loop)

    def test_diagnose_config_arg(self) -> None:
        args = parse_args(["--diagnose-config", "--workspace", "."])
        self.assertTrue(args.diagnose_config)

    def test_benchmark_report_arg(self) -> None:
        args = parse_args(["--benchmark-report", "terminal-bench-shards", "--benchmark-report-output", "reports"])

        self.assertEqual(args.benchmark_report, "terminal-bench-shards")
        self.assertEqual(args.benchmark_report_output, "reports")

    def test_tool_use_eval_args(self) -> None:
        args = parse_args(["--tool-use-eval", "reports", "--tool-use-eval-input", "observations.json"])

        self.assertEqual(args.tool_use_eval, "reports")
        self.assertEqual(args.tool_use_eval_input, "observations.json")

    def test_tool_runtime_report_arg(self) -> None:
        args = parse_args(["--tool-runtime-report", "runtime-report", "--workspace", "."])

        self.assertEqual(args.tool_runtime_report, "runtime-report")

    def test_tool_runtime_evidence_smoke_arg(self) -> None:
        args = parse_args(["--tool-runtime-evidence-smoke", "--tool-runtime-report", "runtime-report", "--workspace", "."])

        self.assertTrue(args.tool_runtime_evidence_smoke)
        self.assertEqual(args.tool_runtime_report, "runtime-report")

    def test_mcp_hook_live_validation_arg(self) -> None:
        args = parse_args(["--mcp-hook-live-validation", "live-report", "--workspace", "."])

        self.assertEqual(args.mcp_hook_live_validation, "live-report")

    def test_benchmark_automation_args(self) -> None:
        args = parse_args(
            [
                "--benchmark-automation",
                "tasks.txt",
                "--tb-command-template",
                "tb run {task_args} --out {output_dir}",
                "--benchmark-target-score",
                "0.99",
                "--benchmark-allow-invalid",
            ]
        )

        self.assertEqual(args.benchmark_automation, "tasks.txt")
        self.assertEqual(args.benchmark_target_score, 0.99)
        self.assertTrue(args.benchmark_allow_invalid)

    def test_terminal_bench_real_run_args(self) -> None:
        args = parse_args(
            [
                "--terminal-bench-real-run",
                "tasks.txt",
                "--tb-command-template",
                "tb run {task_args} --out {output_dir}",
                "--tb-preflight-only",
                "--tb-skip-preflight",
            ]
        )

        self.assertEqual(args.terminal_bench_real_run, "tasks.txt")
        self.assertTrue(args.tb_preflight_only)
        self.assertTrue(args.tb_skip_preflight)

    def test_positional_prompt_still_works(self) -> None:
        args = parse_args(["--mock", "list", "files"])
        self.assertEqual(prompt_text(args), "list files")
        self.assertEqual(permission_mode(args), "ask")

    def test_benchmark_hints_extract_code_anchors(self) -> None:
        hints = extract_benchmark_hints(
            "圾 src 扼 utils.py 扼 slugify(text: str) -> str. "
            "slugify('Hello, World!') 忱 'hello-world'."
        )

        self.assertIn("src/utils.py", hints)
        self.assertIn("slugify(text: str) -> str", hints)
        self.assertIn("hello-world", hints)

    def test_benchmark_hints_extract_exact_output_phrase(self) -> None:
        hints = extract_benchmark_hints("Create hello.py and print exactly: Hello, world!")

        self.assertIn("hello.py", hints)
        self.assertIn("Hello, world!", hints)

    def test_benchmark_hints_preserve_hidden_file_names(self) -> None:
        hints = extract_benchmark_hints("Create .pre-commit-config.yaml in the workspace root.")

        self.assertIn(".pre-commit-config.yaml", hints)
        self.assertNotIn(" pre-commit-config.yaml", hints)

    def test_benchmark_hints_only_join_nearby_directory_file_mentions(self) -> None:
        hints = extract_benchmark_hints(
            "Create qsort.py with quicksort(). In directory tests there is test_qsort.py."
        )

        self.assertIn("qsort.py", hints)
        self.assertIn("tests/test_qsort.py", hints)
        self.assertNotIn("tests/qsort.py", hints)

    def test_benchmark_hints_warn_to_copy_mojibake_literals(self) -> None:
        hints = extract_benchmark_hints(
            'Create data.json with name "GigaChat" and version 3. '
            "greet('袗薪褟') returns '袩褉懈胁械褌, 袗薪褟!'."
        )

        self.assertIn("without translating", hints)
        self.assertIn("GigaChat", hints)
        self.assertIn("version=3", hints)
        self.assertIn("袩褉懈胁械褌, 袗薪褟!", hints)

    def test_system_prompt_includes_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "AGENTS.md").write_text("Always prefer project memory.\n", encoding="utf-8")

            prompt = system_prompt_for_workspace("base", Path(tmp))

        self.assertIn("base", prompt)
        self.assertIn("AGENTS.md", prompt)
        self.assertIn("Always prefer project memory.", prompt)

    def test_benchmark_hints_build_generic_edit_contract(self) -> None:
        hints = extract_benchmark_hints(
            "В файле math_utils.py добавь аннотации типов к функции add: "
            "оба аргумента — int, возвращаемое значение — int. "
            "Тело функции (return a + b) не меняй."
        )

        self.assertIn("Task contract guidance", hints)
        self.assertIn("preserve unrelated lines", hints)
        self.assertIn("add", hints)
        self.assertIn("int", hints)
        self.assertIn("return a + b", hints)

    def test_benchmark_hints_include_exact_docstring_line(self) -> None:
        hints = extract_benchmark_hints(
            "В функцию calculate из файла calc.py добавь docstring. "
            "Текст docstring — 'Возвращает удвоенное значение x.'."
        )

        self.assertIn('"""Возвращает удвоенное значение x."""', hints)

    def test_benchmark_hints_extract_move_preservation_anchors(self) -> None:
        hints = extract_benchmark_hints(
            "Перенеси функцию helper() из файла a.py в файл b.py. "
            "В a.py должна остаться только строка TOKEN = 'X'; "
            "в b.py должна остаться строка VALUE = 1. "
            "Тело функции (return 'help') менять не нужно."
        )

        self.assertIn("TOKEN = 'X'", hints)
        self.assertIn("VALUE = 1", hints)
        self.assertIn("return 'help'", hints)
        self.assertIn("preservation constraints", hints)
        self.assertNotIn("return=help", hints)

    def test_benchmark_hints_separate_literals_from_semantic_facts(self) -> None:
        hints = extract_benchmark_hints(
            "User says they work from a named city. AGENTS.md says user facts go to MEMORY.md. "
            "Create now.py that prints HH:MM."
        )

        self.assertIn("semantic facts", hints)
        self.assertIn("memory format", hints)
        self.assertIn("shortest stable schema/category key", hints)
        self.assertNotIn("City:", hints)

    def test_benchmark_hints_dedent_structured_blocks(self) -> None:
        hints = extract_benchmark_hints(
            "Create .pre-commit-config.yaml:\n"
            "  repos:\n"
            "    - repo: https://github.com/astral-sh/ruff-pre-commit\n"
            "      rev: v0.6.0\n"
            "      hooks:\n"
        )

        self.assertIn("```\nrepos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: v0.6.0\n    hooks:\n```", hints)

    def test_benchmark_hints_warn_about_text_line_endings_for_hashes(self) -> None:
        hints = extract_benchmark_hints(
            "Create SHA256SUMS for all text files in payload as a deterministic manifest."
        )

        self.assertIn("normalize CRLF/CR to LF", hints)
        self.assertIn("Use raw bytes only", hints)


if __name__ == "__main__":
    unittest.main()
