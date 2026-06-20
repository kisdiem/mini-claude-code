from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.project_index import ProjectIndex
from mini_cc.repair import build_repair_context, parse_failure_output


class RepairPlannerTests(unittest.TestCase):
    def test_parse_failure_and_suggest_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def value(): return missing\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text("def test_value(): pass\n", encoding="utf-8")
            output = 'exit_code=1\nFile "tests/test_app.py", line 3\nNameError: name \'missing\' is not defined\nFAILED tests/test_app.py::test_value\n'

            failure = parse_failure_output("python -m pytest", output)
            context = build_repair_context(failure, ["app.py"], ["app.py"], ProjectIndex.build(root))

            self.assertIn("tests/test_app.py::test_value", failure.failed_tests)
            self.assertIn("missing", failure.error_symbols)
            self.assertIn("app.py", context.suggested_next_reads)


if __name__ == "__main__":
    unittest.main()
