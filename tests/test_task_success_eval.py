from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.evals.task_success import run_task_success_eval


class TaskSuccessEvalTests(unittest.TestCase):
    def test_task_success_eval_smoke_passes_all_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_task_success_eval(Path(tmp))

            self.assertEqual(result["total_cases"], 3)
            self.assertEqual(result["failed_cases"], 0)
            self.assertEqual(result["pass_rate"], 1.0)
            for row in result["results"]:
                self.assertTrue(row["changed_files"])
                self.assertEqual(row["verification_command"], "python -m unittest discover")
                self.assertTrue(row["verification_passed"])


if __name__ == "__main__":
    unittest.main()
