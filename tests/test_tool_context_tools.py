from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.tools import ToolRunner


class ToolContextToolsTests(unittest.TestCase):
    def test_project_overview_symbol_related_and_failure_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "app.py").write_text("def value():\n    return missing\n", encoding="utf-8")
            (root / "tests" / "test_app.py").write_text("def test_value(): pass\n", encoding="utf-8")
            runner = ToolRunner(root, permission="auto")

            overview = runner.run("project_overview", {})
            self.assertFalse(overview.is_error, overview.content)
            self.assertIn("Python", overview.content)

            symbols = runner.run("symbol_search", {"query": "value"})
            self.assertFalse(symbols.is_error, symbols.content)
            self.assertIn("app.py", symbols.content)

            related = json.loads(runner.run("related_files", {"path": "app.py"}).content)
            self.assertEqual(related["tests"], ["tests/test_app.py"])

            failure = runner.run("failure_context", {"command": "python -m pytest", "command_output": "exit_code=1\nNameError: name 'missing' is not defined\n"})
            self.assertFalse(failure.is_error, failure.content)
            self.assertIn("missing", failure.content)

    def test_git_diff_summary_returns_json_or_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = ToolRunner(root, permission="auto")
            result = runner.run("git_diff_summary", {})
            if result.is_error:
                self.assertIn("git diff summary failed", result.content)
            else:
                payload = json.loads(result.content)
                self.assertIn("changed_files", payload)


if __name__ == "__main__":
    unittest.main()
