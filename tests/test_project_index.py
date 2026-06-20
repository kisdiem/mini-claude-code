from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.project_index import ProjectIndex


class ProjectIndexTests(unittest.TestCase):
    def test_indexes_python_symbols_and_related_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "app.py").write_text("import os\nVALUE = 1\n\ndef value():\n    return VALUE\n\nclass Box:\n    pass\n", encoding="utf-8")
            (root / "tests" / "test_app.py").write_text("def test_value(): pass\n", encoding="utf-8")

            index = ProjectIndex.build(root)

            self.assertIn("Python", index.project_types)
            self.assertIn("pyproject.toml", index.config_files)
            self.assertEqual(index.related_tests_for("app.py"), ["tests/test_app.py"])
            self.assertEqual(index.related_sources_for("tests/test_app.py"), ["app.py"])
            self.assertTrue(any(symbol.name == "value" and symbol.kind == "function" for symbol in index.symbols))
            self.assertTrue(any(symbol.name == "Box" and symbol.kind == "class" for symbol in index.symbols))

    def test_indexes_js_ts_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "index.ts").write_text("import x from './x'\nexport function run() { return x }\ntest('works', () => {})\n", encoding="utf-8")

            index = ProjectIndex.build(root)
            symbols = index.find_symbol("run")

            self.assertIn("Node/TS", index.project_types)
            self.assertEqual(symbols[0].path, "src/index.ts")
            self.assertEqual(symbols[0].kind, "function")


if __name__ == "__main__":
    unittest.main()
