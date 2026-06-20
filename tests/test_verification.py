from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.verification import discover_verification_candidates


class VerificationDiscoveryTests(unittest.TestCase):
    def test_python_project_discovers_targeted_and_project_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "app.py").write_text("def value(): return 1\n", encoding="utf-8")
            (root / "tests" / "test_app.py").write_text("def test_value(): pass\n", encoding="utf-8")

            candidates = discover_verification_candidates(root, ["app.py"])
            commands = [candidate.command for candidate in candidates]

            self.assertIn("python -m pytest tests/test_app.py", commands)
            self.assertIn("python -m pytest", commands)
            self.assertGreater(candidates[0].confidence, 0.8)

    def test_unittest_project_discovers_unittest_discover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text("import unittest\n", encoding="utf-8")

            commands = [candidate.command for candidate in discover_verification_candidates(root)]

            self.assertIn("python -m unittest discover", commands)

    def test_node_project_uses_lockfile_runner_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "build": "tsc -b"}}),
                encoding="utf-8",
            )

            commands = [candidate.command for candidate in discover_verification_candidates(root)]

            self.assertEqual(commands[0], "pnpm test")
            self.assertIn("pnpm run lint", commands)
            self.assertIn("pnpm run build", commands)

    def test_go_rust_and_java_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example\n", encoding="utf-8")
            (root / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
            (root / "pom.xml").write_text("<project />\n", encoding="utf-8")

            commands = [candidate.command for candidate in discover_verification_candidates(root)]

            self.assertIn("go test ./...", commands)
            self.assertIn("cargo test", commands)
            self.assertIn("cargo check", commands)
            self.assertIn("mvn test", commands)


if __name__ == "__main__":
    unittest.main()
