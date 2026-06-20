from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.task_planner import build_task_context, plan_minimal_edit


class TaskPlannerTests(unittest.TestCase):
    def test_prompt_path_and_symbol_drive_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text("def test_value(): pass\n", encoding="utf-8")

            context = build_task_context("Fix symbol `value` in app.py with python -m pytest", root)
            plan = plan_minimal_edit("Fix symbol `value` in app.py", context)

            self.assertEqual(context.prompt_paths, ["app.py"])
            self.assertIn("app.py", context.candidate_files)
            self.assertIn("tests/test_app.py", context.candidate_tests)
            self.assertEqual(plan.planned_files, ["app.py"])
            self.assertEqual(plan.verification_command, "python -m pytest")


if __name__ == "__main__":
    unittest.main()
