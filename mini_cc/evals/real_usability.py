from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent import Agent
from ..coding_loop import CodingLoopPolicy
from ..llm import MockBlock, MockResponse
from ..task_runtime import TaskRuntime
from ..task_state import TaskStateMachine
from ..tools import ToolRunner


@dataclass(frozen=True)
class EvalCaseResult:
    name: str
    passed: bool
    reason: str
    trace: list[str]
    artifact: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "trace": self.trace,
            "artifact": self.artifact,
        }


class ScriptedProvider:
    def __init__(self, turns: list[list[MockBlock]]) -> None:
        self.turns = turns
        self.calls = 0
        self.prompts: list[Any] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        self.prompts.append(messages[-1].get("content"))
        if self.calls < len(self.turns):
            blocks = self.turns[self.calls]
        else:
            blocks = [MockBlock(type="text", text="final answer")]
        self.calls += 1
        return MockResponse(blocks)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _setup_project(root: Path) -> None:
    _write(root / "app.py", "def value():\n    return 1\n")
    _write(
        root / "test_app.py",
        "from app import value\n\n"
        "def test_value():\n"
        "    assert value() == 2\n",
    )


def _setup_unittest_project(root: Path) -> None:
    _write(root / "app.py", "def value():\n    return 1\n")
    _write(
        root / "test_app.py",
        "import unittest\n"
        "from app import value\n\n"
        "class AppTests(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(value(), 2)\n",
    )


def _agent(root: Path, provider: ScriptedProvider, output: list[str], *, max_turns: int = 8) -> Agent:
    task_state = TaskStateMachine(root)
    coding_loop = CodingLoopPolicy(root, enabled=True, max_repair_attempts=2)
    runtime = TaskRuntime(root, task_state_machine=task_state, coding_loop=coding_loop)
    return Agent(
        provider,  # type: ignore[arg-type]
        ToolRunner(root, permission="auto"),
        max_turns=max_turns,
        output=output.append,
        coding_loop=coding_loop,
        task_state_machine=task_state,
        task_runtime=runtime,
    )


def _standard_edit_turns(*, verify_command: str | None = None, replacement: str = "return 2") -> list[list[MockBlock]]:
    turns: list[list[MockBlock]] = [
        [MockBlock(type="tool_use", id="list", name="list_files", input={"path": ".", "recursive": True})],
        [MockBlock(type="tool_use", id="read", name="read_file", input={"path": "app.py"})],
        [
            MockBlock(
                type="text",
                text="Plan: planned_files: app.py. Replace the return value and verify with "
                + (verify_command or "python -m unittest discover")
                + ".",
            ),
            MockBlock(
                type="tool_use",
                id="edit",
                name="replace_text",
                input={"path": "app.py", "old": "return 1", "new": replacement},
            ),
        ],
    ]
    if verify_command:
        turns.append(
            [MockBlock(type="tool_use", id="verify", name="run_shell", input={"command": verify_command, "timeout": 30})]
        )
    turns.append([MockBlock(type="text", text="final answer")])
    return turns


def run_case_edit_without_verify(root: Path) -> EvalCaseResult:
    _setup_unittest_project(root)
    output: list[str] = []
    provider = ScriptedProvider(_standard_edit_turns())
    _agent(root, provider, output, max_turns=6).run("fix bug in app.py")
    serialized = json.dumps(provider.prompts, ensure_ascii=False)
    passed = "Task phase: VERIFY" in serialized or "Verification required" in serialized
    return _result(root, "edit_without_verify_blocks_final", passed, "verification instruction injected", output)


def run_case_fake_verification(root: Path) -> EvalCaseResult:
    _setup_unittest_project(root)
    output: list[str] = []
    provider = ScriptedProvider(_standard_edit_turns(verify_command="echo ok"))
    _agent(root, provider, output, max_turns=7).run("fix bug in app.py")
    joined = "\n".join(output)
    passed = "real verification command required" in joined or "Task phase: VERIFY" in json.dumps(provider.prompts, ensure_ascii=False)
    return _result(root, "fake_verification_is_blocked", passed, "fake verification rejected", output)


def run_case_pytest_success(root: Path) -> EvalCaseResult:
    _setup_project(root)
    output: list[str] = []
    provider = ScriptedProvider(_standard_edit_turns(verify_command="python -m pytest"))
    _agent(root, provider, output, max_turns=7).run("fix bug in app.py")
    artifact = _read_artifact(root)
    passed = artifact.get("status") == "passed" or artifact.get("final_status") == "passed"
    return _result(root, "python_edit_pytest_success", passed, "pytest verification passed", output, artifact)


def run_case_failed_verification_repairs(root: Path) -> EvalCaseResult:
    _setup_unittest_project(root)
    output: list[str] = []
    provider = ScriptedProvider(_standard_edit_turns(verify_command="python -m unittest discover", replacement="return 3"))
    _agent(root, provider, output, max_turns=7).run("fix bug in app.py")
    serialized = json.dumps(provider.prompts, ensure_ascii=False)
    passed = "Failed command:" in serialized and "Modified files:" in serialized and "minimal repair" in serialized
    return _result(root, "failed_verification_gets_repair_instruction", passed, "repair instruction included failure context", output)


def run_case_unread_or_outside_plan_blocked(root: Path) -> EvalCaseResult:
    _setup_unittest_project(root)
    output: list[str] = []
    provider = ScriptedProvider(
        [
            [MockBlock(type="tool_use", id="list", name="list_files", input={"path": ".", "recursive": True})],
            [MockBlock(type="text", text="Plan: planned_files: app.py. Verify with python -m unittest discover.")],
            [
                MockBlock(
                    type="tool_use",
                    id="edit",
                    name="replace_text",
                    input={"path": "other.py", "old": "x", "new": "y"},
                )
            ],
            [MockBlock(type="text", text="final answer")],
        ]
    )
    _agent(root, provider, output, max_turns=5).run("fix bug in app.py")
    joined = "\n".join(output)
    passed = "Task phase blocked" in joined and ("not in planned_files" in joined or "read the target file" in joined)
    return _result(root, "unread_or_outside_plan_edit_blocked", passed, "edit blocked by task runtime", output)


def _result(
    root: Path,
    name: str,
    passed: bool,
    reason: str,
    output: list[str],
    artifact: dict[str, Any] | None = None,
) -> EvalCaseResult:
    return EvalCaseResult(name=name, passed=passed, reason=reason, trace=output[-20:], artifact=artifact or _read_artifact(root))


def _read_artifact(root: Path) -> dict[str, Any]:
    path = root / ".mini_cc" / "task-success" / "last-run.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_real_usability_eval(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        run_case_edit_without_verify,
        run_case_fake_verification,
        run_case_pytest_success,
        run_case_failed_verification_repairs,
        run_case_unread_or_outside_plan_blocked,
    ]
    results: list[EvalCaseResult] = []
    for case in cases:
        case_root = output_dir / "cases" / case.__name__.replace("run_case_", "")
        case_root.mkdir(parents=True, exist_ok=True)
        results.append(case(case_root))
    passed = sum(1 for result in results if result.passed)
    payload = {
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": [result.name for result in results if not result.passed],
        "pass_rate": passed / len(results) if results else 0.0,
        "per_case_result": [result.to_json() for result in results],
        "blocked_fake_verification": _case_passed(results, "fake_verification_is_blocked"),
        "enforced_read_before_edit": _case_passed(results, "unread_or_outside_plan_edit_blocked"),
        "enforced_plan_before_edit": _case_passed(results, "unread_or_outside_plan_edit_blocked"),
        "enforced_real_verification": _case_passed(results, "edit_without_verify_blocks_final"),
        "repair_prompt_quality": _case_passed(results, "failed_verification_gets_repair_instruction"),
    }
    json_path = output_dir / "real-usability-eval.json"
    markdown_path = output_dir / "real-usability-eval.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    return payload


def _case_passed(results: list[EvalCaseResult], name: str) -> bool:
    return any(result.name == name and result.passed for result in results)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Real Usability Eval",
        "",
        f"- total_cases: {payload['total_cases']}",
        f"- passed_cases: {payload['passed_cases']}",
        f"- pass_rate: {payload['pass_rate']:.2%}",
        "",
        "| Case | Passed | Reason |",
        "| --- | --- | --- |",
    ]
    for result in payload["per_case_result"]:
        lines.append(f"| {result['name']} | {result['passed']} | {result['reason']} |")
    lines.append("")
    lines.append("This is a deterministic local smoke eval, not an external benchmark score.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic real-usability smoke cases.")
    parser.add_argument(
        "--output-dir",
        default=".mini_cc/real-usability-eval",
        help="Directory for generated cases and real-usability-eval reports.",
    )
    args = parser.parse_args(argv)
    payload = run_real_usability_eval(Path(args.output_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed_cases"] == payload["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
