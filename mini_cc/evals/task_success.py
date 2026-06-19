from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from ..coding_loop import CodingLoopPolicy
from ..tools import ToolRunner


@dataclass(frozen=True)
class TaskSuccessCase:
    name: str
    prompt: str
    test_command: str
    setup: Callable[[Path], None]
    patch: str


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def setup_boundary_bug(root: Path) -> None:
    _write(
        root / "calc.py",
        "def clamp(value, low, high):\n"
        "    if value < low:\n"
        "        return high\n"
        "    if value > high:\n"
        "        return low\n"
        "    return value\n",
    )
    _write(
        root / "test_calc.py",
        "import unittest\n"
        "from calc import clamp\n\n"
        "class ClampTests(unittest.TestCase):\n"
        "    def test_low(self):\n"
        "        self.assertEqual(clamp(-1, 0, 10), 0)\n"
        "    def test_high(self):\n"
        "        self.assertEqual(clamp(20, 0, 10), 10)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
    )


def setup_missing_import(root: Path) -> None:
    _write(root / "formatter.py", "def basename(path):\n    return os.path.basename(path)\n")
    _write(
        root / "test_formatter.py",
        "import unittest\n"
        "from formatter import basename\n\n"
        "class FormatterTests(unittest.TestCase):\n"
        "    def test_basename(self):\n"
        "        self.assertEqual(basename('a/b/c.txt'), 'c.txt')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
    )


def setup_path_join(root: Path) -> None:
    _write(root / "paths.py", "def child_path(base, name):\n    return base + name\n")
    _write(
        root / "test_paths.py",
        "import os\n"
        "import unittest\n"
        "from paths import child_path\n\n"
        "class PathTests(unittest.TestCase):\n"
        "    def test_join(self):\n"
        "        self.assertEqual(child_path('root', 'file.txt'), os.path.join('root', 'file.txt'))\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
    )


CASES = [
    TaskSuccessCase(
        name="python_boundary_condition_bug",
        prompt="fix the clamp boundary condition bug",
        test_command="python -m unittest discover",
        setup=setup_boundary_bug,
        patch=(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,6 +1,6 @@\n"
            " def clamp(value, low, high):\n"
            "     if value < low:\n"
            "-        return high\n"
            "+        return low\n"
            "     if value > high:\n"
            "-        return low\n"
            "+        return high\n"
            "     return value\n"
        ),
    ),
    TaskSuccessCase(
        name="python_missing_import",
        prompt="fix the missing import error",
        test_command="python -m unittest discover",
        setup=setup_missing_import,
        patch=(
            "--- a/formatter.py\n"
            "+++ b/formatter.py\n"
            "@@ -1,2 +1,4 @@\n"
            "+import os\n"
            "+\n"
            " def basename(path):\n"
            "     return os.path.basename(path)\n"
        ),
    ),
    TaskSuccessCase(
        name="python_path_join_bug",
        prompt="fix the path joining bug",
        test_command="python -m unittest discover",
        setup=setup_path_join,
        patch=(
            "--- a/paths.py\n"
            "+++ b/paths.py\n"
            "@@ -1,2 +1,4 @@\n"
            "+import os\n"
            "+\n"
            " def child_path(base, name):\n"
            "-    return base + name\n"
            "+    return os.path.join(base, name)\n"
        ),
    ),
]


def run_case(case: TaskSuccessCase, output_dir: Path) -> dict[str, Any]:
    case_dir = output_dir / "cases" / case.name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    case.setup(case_dir)

    policy = CodingLoopPolicy(
        case_dir,
        enabled=True,
        test_command=case.test_command,
        max_repair_attempts=1,
    )
    policy.start(case.prompt)
    runner = ToolRunner(case_dir, permission="auto")

    patch_result = runner.run("apply_patch", {"patch": case.patch})
    policy.observe_tool_result("apply_patch", {}, patch_result)
    verify_result = runner.run("run_shell", {"command": case.test_command, "timeout": 30})
    policy.observe_tool_result("run_shell", {"command": case.test_command}, verify_result)

    decision = policy.finish_decision()
    status = decision.status if decision.allow_finish else "failed"
    policy.write_artifact(status=status)
    return {
        "name": case.name,
        "prompt": case.prompt,
        "status": status,
        "passed": status == "passed",
        "changed_files": list(policy.state.modified_files),
        "verification_command": case.test_command,
        "verification_passed": policy.state.last_verification_passed,
        "patch_error": patch_result.content if patch_result.is_error else "",
        "failure_summary": policy.state.last_failure_summary,
        "case_dir": str(case_dir),
    }


def run_task_success_eval(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [run_case(case, output_dir) for case in CASES]
    passed = sum(1 for result in results if result["passed"])
    payload = {
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "results": results,
    }
    (output_dir / "task-success-eval.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run lightweight task-success smoke cases.")
    parser.add_argument(
        "--output-dir",
        default=".mini_cc/task-success-eval",
        help="Directory for generated cases and task-success-eval.json.",
    )
    args = parser.parse_args(argv)
    payload = run_task_success_eval(Path(args.output_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
