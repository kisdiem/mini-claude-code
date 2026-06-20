from __future__ import annotations

import unittest

from mini_cc.task_success import (
    extract_task_contract,
    validate_edit,
    validate_plan,
    validate_verification_command,
    validate_verification_output,
)


class TaskSuccessV2Tests(unittest.TestCase):
    def test_contract_v2_records_evidence_and_ambiguity(self) -> None:
        contract = extract_task_contract(
            'Fix `parse_user` in src/foo.py. Expected output should contain "hello world". Run python -m pytest.'
        )

        self.assertEqual(contract.primary_intent, "bug_fix")
        self.assertEqual(contract.task_type, "bug_fix")
        self.assertIn("src/foo.py", contract.explicit_paths)
        self.assertIn("parse_user", contract.explicit_symbols)
        self.assertTrue(contract.acceptance_criteria)
        self.assertTrue(contract.evidence)
        self.assertLess(contract.ambiguity_score, 0.6)

    def test_simple_bug_fix_plan_and_pytest_verification_allow(self) -> None:
        contract = extract_task_contract("Fix bug in src/foo.py")
        state = StubState(candidate_files=["src/foo.py"], read_files=["src/foo.py"], planned_files=["src/foo.py"])

        decision = validate_plan(contract, state, "Plan: planned_files: src/foo.py. Verify with python -m pytest.")
        verification = validate_verification_command(contract, state, "python -m pytest", ["src/foo.py"])

        self.assertTrue(decision.allow)
        self.assertGreaterEqual(decision.score, 0.65)
        self.assertTrue(verification.is_real_verification)
        self.assertTrue(verification.is_relevant)

    def test_unexplored_model_only_plan_blocks_with_search_instruction(self) -> None:
        contract = extract_task_contract("Fix the parser bug")
        state = StubState(planned_files=["src/random.py"])

        decision = validate_plan(contract, state, "Plan: planned_files: src/random.py. Verify with python -m pytest.")

        self.assertFalse(decision.allow)
        self.assertIn("exploration", decision.reason)
        self.assertIn("search_text", decision.instruction)
        self.assertIn("read_file", decision.instruction)

    def test_only_modify_constraint_blocks_other_plan_and_edit(self) -> None:
        contract = extract_task_contract("Only modify src/a.py to fix the bug")
        state = StubState(candidate_files=["src/a.py", "src/b.py"], read_files=["src/a.py", "src/b.py"], planned_files=["src/b.py"])

        plan = validate_plan(contract, state, "Plan: planned_files: src/b.py. Verify with python -m pytest.")
        edit = validate_edit(contract, state, ["src/b.py"], "changed_files: src/b.py\nadded_lines: 2\ndeleted_lines: 1")

        self.assertFalse(plan.allow)
        self.assertFalse(edit.allow)
        self.assertIn("only-modify", plan.reason)
        self.assertIn("only-modify", edit.reason)

    def test_no_tests_constraint_blocks_test_edit_for_non_test_fix(self) -> None:
        contract = extract_task_contract("Fix src/app.py but do not modify tests")
        state = StubState(planned_files=["src/app.py", "tests/test_app.py"], read_files=["src/app.py", "tests/test_app.py"])

        decision = validate_edit(contract, state, ["tests/test_app.py"], "changed_files: tests/test_app.py\nadded_lines: 1\ndeleted_lines: 1")

        self.assertFalse(decision.allow)
        self.assertIn("do-not-modify-tests", decision.reason)

    def test_multi_file_feature_with_cli_docs_and_tests_allows_when_grounded(self) -> None:
        contract = extract_task_contract("Add CLI --json output and update README.md with usage")
        state = StubState(
            candidate_files=["mini_cc/cli.py", "README.md", "tests/test_cli.py"],
            read_files=["mini_cc/cli.py", "README.md", "tests/test_cli.py"],
            planned_files=["mini_cc/cli.py", "README.md", "tests/test_cli.py"],
        )

        decision = validate_plan(
            contract,
            state,
            "Plan: planned_files: mini_cc/cli.py, README.md, tests/test_cli.py. Verify with python -m pytest tests/test_cli.py.",
        )

        self.assertTrue(decision.allow, decision.reason)

    def test_docs_only_task_accepts_docs_verification(self) -> None:
        contract = extract_task_contract("Update README.md documentation for installation")
        state = StubState(candidate_files=["README.md"], read_files=["README.md"], planned_files=["README.md"])

        decision = validate_plan(contract, state, "Plan: planned_files: README.md. Verify with markdownlint README.md.")
        verification = validate_verification_command(contract, state, "markdownlint README.md", ["README.md"])

        self.assertTrue(decision.allow, decision.reason)
        self.assertTrue(verification.is_real_verification)
        self.assertTrue(verification.is_relevant)

    def test_fake_verification_commands_are_rejected(self) -> None:
        contract = extract_task_contract("Fix bug in src/foo.py")
        state = StubState(modified_files=["src/foo.py"])

        for command in ["echo ok", "git diff", "ls"]:
            evidence = validate_verification_command(contract, state, command, ["src/foo.py"])
            self.assertFalse(evidence.is_real_verification, command)
            self.assertFalse(evidence.is_relevant, command)

    def test_no_tests_ran_is_not_meaningful(self) -> None:
        contract = extract_task_contract("Fix bug in src/foo.py")
        prior = validate_verification_command(contract, StubState(modified_files=["src/foo.py"]), "python -m pytest", ["src/foo.py"])

        evidence = validate_verification_output(
            "python -m pytest",
            "exit_code=0\nstdout:\ncollected 0 items\nno tests ran\nstderr:\n",
            prior=prior,
        )

        self.assertFalse(evidence.has_meaningful_checks)
        self.assertIn("zero", evidence.meaningful_checks_reason)

    def test_over_edit_returns_warning_without_hard_block(self) -> None:
        contract = extract_task_contract("Add support for JSON export in src/exporter.py")
        state = StubState(
            candidate_files=["src/exporter.py", "src/format.py", "src/cli.py", "README.md"],
            read_files=["src/exporter.py", "src/format.py", "src/cli.py", "README.md"],
            planned_files=["src/exporter.py", "src/format.py", "src/cli.py", "README.md"],
        )

        decision = validate_edit(
            contract,
            state,
            ["src/exporter.py", "src/format.py", "src/cli.py", "README.md"],
            "changed_files: src/exporter.py\nadded_lines: 350\ndeleted_lines: 12",
        )

        self.assertTrue(decision.allow)
        self.assertLess(decision.score, 0.8)
        self.assertTrue(decision.warnings)

    def test_implicit_target_from_exploration_allows_plan(self) -> None:
        contract = extract_task_contract("Fix parser bug when input is empty")
        state = StubState(candidate_files=["src/parser.py"], read_files=["src/parser.py"], planned_files=["src/parser.py"])

        decision = validate_plan(contract, state, "Plan: planned_files: src/parser.py. Verify with python -m pytest.")

        self.assertTrue(decision.allow, decision.reason)


class StubState:
    def __init__(
        self,
        *,
        candidate_files: list[str] | None = None,
        read_files: list[str] | None = None,
        planned_files: list[str] | None = None,
        modified_files: list[str] | None = None,
        last_failure_summary: str = "",
    ) -> None:
        self.candidate_files = candidate_files or []
        self.read_files = read_files or []
        self.planned_files = planned_files or []
        self.modified_files = modified_files or []
        self.last_failure_summary = last_failure_summary


if __name__ == "__main__":
    unittest.main()
