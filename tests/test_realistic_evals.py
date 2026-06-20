from __future__ import annotations

import unittest

from mini_cc.evals.realistic_tasks import realistic_cases, run_realistic_evals


class RealisticEvalSmokeTests(unittest.TestCase):
    def test_defines_at_least_30_cases(self) -> None:
        self.assertGreaterEqual(len(realistic_cases()), 30)

    def test_smoke_runs_first_case_through_agent(self) -> None:
        results = run_realistic_evals(limit=1)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].tool_call_count, 0)
        self.assertTrue(results[0].evidence_report_path)


if __name__ == "__main__":
    unittest.main()
