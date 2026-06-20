from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from mini_cc.agent import Agent
from mini_cc.coding_loop import CodingLoopPolicy
from mini_cc.llm import MockBlock, MockResponse
from mini_cc.task_runtime import TaskRuntime
from mini_cc.task_state import TaskStateMachine
from mini_cc.tools import ToolRunner


@dataclass(frozen=True)
class RealisticCase:
    name: str
    prompt: str
    files: dict[str, str]
    verify_command: str
    allowed_files: list[str] = field(default_factory=list)
    expected_text: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalResult:
    name: str
    passed: bool
    modified_files: list[str]
    planned_files: list[str]
    verification_commands: list[str]
    tool_call_count: int
    repair_attempts: int
    violated_constraints: list[str]
    evidence_report_path: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class ScriptedCodingProvider:
    """Small deterministic provider that drives the real Agent/tool loop offline."""

    def __init__(self, case: RealisticCase) -> None:
        self.case = case
        self.step = 0
        self.target = _target_from_prompt(case.prompt) or next(iter(case.files))
        self.read_content = ""
        self.did_repair = False

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> MockResponse:
        del tools, system
        if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], str):
            instruction = str(messages[-1]["content"])
            if "Task phase: EXPLORE" in instruction:
                return self._tool("project_overview", {})
            if "Task phase: LOCALIZE" in instruction or "read the target file" in instruction:
                return self._tool("read_file", {"path": self.target, "start_line": 1, "max_lines": 240})
            if "Task phase: PLAN" in instruction or "planned_files" in instruction:
                return self._text(f"Plan: planned_files: {self.target}. Make the smallest change and verify with {self.case.verify_command}.")
            if "Task phase: EDIT" in instruction:
                return self._edit_response()
            if "Task phase: VERIFY" in instruction or "Verification required" in instruction:
                return self._tool("run_shell", {"command": self.case.verify_command, "timeout": 10})
            if "Task phase: REPAIR" in instruction:
                self.did_repair = True
                return self._tool("failure_context", {})
        if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list):
            content = str(messages[-1]["content"][0].get("content", ""))
            if "Task phase blocked" in content:
                if "read the target file" in content:
                    return self._tool("read_file", {"path": self.target, "start_line": 1, "max_lines": 240})
                if "minimal edit plan" in content or "planned_files" in content:
                    return self._text(f"Plan: planned_files: {self.target}. Make the smallest change and verify with {self.case.verify_command}.")
                if "Verification required" in content or "VERIFY" in content:
                    return self._tool("run_shell", {"command": self.case.verify_command, "timeout": 10})
                if "REPAIR" in content:
                    self.did_repair = True
                    return self._tool("failure_context", {})
            if re.search(r"^\d+:", content, flags=re.MULTILINE):
                self.read_content = content
                if self.step < 3:
                    self.step = 3
                    edit = self._edit_response()
                    edit.content.insert(
                        0,
                        MockBlock(
                            type="text",
                            text=f"Plan: planned_files: {self.target}. Make the smallest change and verify with {self.case.verify_command}.",
                        ),
                    )
                    return edit
                return self._edit_response()
            if "Applied patch" in content or "Replaced " in content or "Wrote " in content:
                return self._tool("run_shell", {"command": self.case.verify_command, "timeout": 10})
            if "exit_code=0" in content:
                return self._text("Done. Verification passed.")
            if "exit_code=" in content:
                if not self.did_repair:
                    self.did_repair = True
                    return self._tool("failure_context", {"command_output": content, "command": self.case.verify_command})
                return self._text("Verification still failed; stopping with evidence.")
            if "repair_context" in content.lower():
                return self._tool("read_file", {"path": self.target, "start_line": 1, "max_lines": 240})
        self.step += 1
        if self.step == 1:
            return self._tool("project_overview", {})
        if self.step == 2:
            return self._tool("read_file", {"path": self.target, "start_line": 1, "max_lines": 240})
        return self._text("Done.")

    def _edit_response(self) -> MockResponse:
        text = _strip_numbered_lines(self.read_content)
        new_text = _generic_fix(self.case.prompt, text)
        if new_text == text:
            return self._text(f"Plan: planned_files: {self.target}. No deterministic edit matched; verify with {self.case.verify_command}.")
        return self._tool("write_file", {"path": self.target, "content": new_text})

    def _tool(self, name: str, tool_input: dict[str, Any]) -> MockResponse:
        return MockResponse([MockBlock(type="tool_use", id=f"toolu_{self.step}_{name}", name=name, input=tool_input)])

    def _text(self, text: str) -> MockResponse:
        return MockResponse([MockBlock(type="text", text=text)])


def realistic_cases() -> list[RealisticCase]:
    base_py_test = "import app\n\n\ndef test_value():\n    assert app.value() == 2\n"
    return [
        _py("python_missing_import", "Fix missing import in app.py for json.loads", "import json\n\ndef value():\n    return json.loads('{\"x\": 2}')['x']\n", "def value():\n    return json.loads('{\"x\": 2}')['x']\n"),
        _py("python_boundary", "Fix wrong boundary condition in app.py", "def value(n=2):\n    return n >= 2\n", "def value(n=2):\n    return n > 2\n"),
        _py("python_path_join", "Fix wrong path join in app.py", "import os\n\ndef value():\n    return os.path.join('a', 'b')\n", "import os\n\ndef value():\n    return 'a' + '/' + 'b'\n"),
        _py("python_default_argument", "Fix wrong default argument in app.py", "def value(n=2):\n    return n\n", "def value(n=1):\n    return n\n"),
        _py("python_json_error", "Fix JSON parse error handling in app.py", "import json\n\ndef value(raw='bad'):\n    try:\n        return json.loads(raw)\n    except json.JSONDecodeError:\n        return {}\n", "import json\n\ndef value(raw='bad'):\n    return json.loads(raw)\n"),
        _py("python_cli_flag", "Fix CLI flag name mismatch in app.py", "FLAG = '--count'\n\ndef value():\n    return FLAG\n", "FLAG = '--cnt'\n\ndef value():\n    return FLAG\n"),
        _py("python_unittest_assertion", "Fix failing unittest assertion in app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _docs("docs_readme_update", "Update README.md to include Usage section", "# Demo\n\n## Usage\nRun tests.\n", "# Demo\n"),
        _config("config_pyproject_typo", "Fix pyproject typo in pyproject.toml", "[project]\nname = 'demo'\n", "[projet]\nname = 'demo'\n"),
        _config("test_discovery_issue", "Fix test discovery issue in pytest.ini", "[pytest]\ntestpaths = tests\n", "[pytest]\ntestpath = tests\n"),
        _node("js_missing_export", "Fix missing export in src/index.js", "export function value() { return 2; }\n", "function value() { return 2; }\n"),
        _node("js_wrong_return", "Fix wrong function return in src/index.js", "export function value() { return 2; }\n", "export function value() { return 1; }\n"),
        _node_cfg("package_script_mismatch", "Fix package script mismatch in package.json", '{"scripts":{"test":"node test.js","build":"node test.js"}}\n', '{"scripts":{"tests":"node test.js","build":"node test.js"}}\n'),
        _node_cfg("tsconfig_option", "Fix tsconfig option issue in tsconfig.json", '{"compilerOptions":{"strict":true}}\n', '{"compilerOptions":{"strct":true}}\n'),
        _node("simple_build_failure", "Fix simple build failure in src/index.js", "export function value() { return 2; }\n", "export function value() { return 1; }\n", verify="npm run build"),
        _docs("required_section_missing", "Add required Installation section to README.md", "# Demo\n\n## Installation\npip install .\n", "# Demo\n"),
        _docs("broken_relative_link", "Fix broken relative link in README.md", "# Demo\nSee [guide](docs/guide.md).\n", "# Demo\nSee [guide](guide.md).\n", extra={"docs/guide.md": "# Guide\n"}),
        _docs("wrong_command_docs", "Fix wrong command in docs README.md", "# Demo\n\nRun `python -m pytest`.\n", "# Demo\n\nRun `pytestt`.\n"),
        _docs("changelog_format", "Fix changelog format issue in CHANGELOG.md", "# Changelog\n\n## 1.0.0\n- Initial\n", "# Changelog\n\n# 1.0.0\n- Initial\n", target="CHANGELOG.md"),
        _docs("duplicate_heading", "Fix duplicate heading in README.md", "# Demo\n\n## Usage\nRun.\n", "# Demo\n\n## Usage\nRun.\n\n## Usage\nAgain.\n"),
        _py("source_and_test_update", "Fix source + test update in app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _docs("config_and_docs_update", "Fix config + docs update in README.md", "# Demo\n\n## Usage\nRun tests.\n", "# Demo\n"),
        _config("generated_manifest_update", "Fix generated manifest update in package.json", '{"name":"demo","version":"1.0.0","scripts":{"test":"node test.js"}}\n', '{"name":"demo","version":"0.0.0","scripts":{"test":"node test.js"}}\n', target="package.json"),
        _py("preserve_public_api", "Preserve public API while fixing app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("do_not_modify_tests", "Do not modify tests; fix app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("only_one_file", "Only modify one file: app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("new_file_allowed", "New file explicitly allowed but fix app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("new_file_forbidden", "New file forbidden; fix app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("verification_then_repair", "Fix app.py; failing verification then repair if needed", "def value():\n    return 2\n", "def value():\n    return 1\n"),
        _py("ambiguous_exploration", "Something is wrong with value behavior; explore and fix app.py", "def value():\n    return 2\n", "def value():\n    return 1\n"),
    ]


def run_realistic_evals(limit: int | None = None) -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in realistic_cases()[: limit or None]:
        results.append(run_case(case))
    return results


def run_case(case: RealisticCase) -> EvalResult:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path, content in case.files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        output: list[str] = []
        task_state = TaskStateMachine(root, max_repair_attempts=1)
        runtime = TaskRuntime(
            root,
            task_state_machine=task_state,
            coding_loop=CodingLoopPolicy(root, max_repair_attempts=1),
        )
        agent = Agent(
            ScriptedCodingProvider(case),
            ToolRunner(root, permission="auto"),
            max_turns=12,
            output=output.append,
            task_state_machine=task_state,
            task_runtime=runtime,
        )
        agent.run("Fix bug: " + case.prompt)
        evidence = root / ".mini_cc" / "task-success" / "last-run.json"
        violated = _violations(root, case, task_state.state.modified_files)
        passed = not violated and all((root / path).read_text(encoding="utf-8", errors="replace").find(text) >= 0 for path, text in case.expected_text.items())
        return EvalResult(
            name=case.name,
            passed=passed,
            modified_files=list(task_state.state.modified_files),
            planned_files=list(task_state.state.planned_files),
            verification_commands=list(task_state.state.verification_commands),
            tool_call_count=len(runtime.tools_called),
            repair_attempts=task_state.state.repair_attempts,
            violated_constraints=violated,
            evidence_report_path=str(evidence),
        )


def main() -> None:
    print(json.dumps([result.to_json() for result in run_realistic_evals()], ensure_ascii=False, indent=2))


def _py(name: str, prompt: str, expected: str, broken: str) -> RealisticCase:
    files = {"app.py": broken, "tests/test_app.py": "import app\n\n\ndef test_value():\n    assert app.value() == 2\n"}
    return RealisticCase(name, prompt + " in app.py", files, "python -m pytest", ["app.py"], {"app.py": expected.strip().splitlines()[0]})


def _node(name: str, prompt: str, expected: str, broken: str, verify: str = "npm test") -> RealisticCase:
    files = {
        "package.json": '{"scripts":{"test":"node test.js","build":"node test.js"}}\n',
        "src/index.js": broken,
        "test.js": "import { value } from './src/index.js'; if (value() !== 2) process.exit(1);\n",
    }
    return RealisticCase(name, prompt + " in src/index.js", files, verify, ["src/index.js"], {"src/index.js": expected.strip().splitlines()[0]})


def _node_cfg(name: str, prompt: str, expected: str, broken: str) -> RealisticCase:
    files = {"package.json": broken if "package" in prompt else '{"scripts":{"test":"node test.js"}}\n', "tsconfig.json": broken if "tsconfig" in prompt else "{}", "test.js": "process.exit(0)\n"}
    target = "package.json" if "package" in prompt else "tsconfig.json"
    return RealisticCase(name, prompt + f" in {target}", files, "npm test", [target], {target: expected.strip()[:20]})


def _docs(name: str, prompt: str, expected: str, broken: str, target: str = "README.md", extra: dict[str, str] | None = None) -> RealisticCase:
    files = {target: broken, **(extra or {})}
    return RealisticCase(name, prompt + f" in {target}", files, "python -m compileall .", [target], {target: expected.strip().splitlines()[0]})


def _config(name: str, prompt: str, expected: str, broken: str, target: str = "pyproject.toml") -> RealisticCase:
    files = {target: broken, "tests/test_smoke.py": "def test_smoke():\n    assert True\n"}
    return RealisticCase(name, prompt + f" in {target}", files, "python -m pytest", [target], {target: expected.strip().splitlines()[0]})


def _target_from_prompt(prompt: str) -> str:
    match = re.search(r"([A-Za-z0-9_./-]+\.(?:py|js|json|toml|md|ini))", prompt)
    return match.group(1) if match else ""


def _strip_numbered_lines(text: str) -> str:
    rows = []
    for line in text.splitlines():
        match = re.match(r"\s*\d+:\s?(.*)$", line)
        if match:
            rows.append(match.group(1))
    return "\n".join(rows) + ("\n" if rows else "")


def _generic_fix(prompt: str, text: str) -> str:
    lowered = prompt.lower()
    if "json.loads" in text and "import json" not in text:
        return "import json\n\n" + text
    replacements = [
        ("return 1", "return 2"),
        ("n > 2", "n >= 2"),
        ("def value(n=1)", "def value(n=2)"),
        ("return json.loads(raw)", "try:\n        return json.loads(raw)\n    except json.JSONDecodeError:\n        return {}"),
        ("--cnt", "--count"),
        ("function value()", "export function value()"),
        ('"tests"', '"test"'),
        ('"strct"', '"strict"'),
        ("[projet]", "[project]"),
        ("testpath =", "testpaths ="),
        ("version\":\"0.0.0", "version\":\"1.0.0"),
        ("pytestt", "python -m pytest"),
        ("[guide](guide.md)", "[guide](docs/guide.md)"),
        ("\n# 1.0.0", "\n## 1.0.0"),
    ]
    updated = text
    for old, new in replacements:
        updated = updated.replace(old, new)
    if "usage section" in lowered and "## Usage" not in updated:
        updated += "\n## Usage\nRun tests.\n"
    if "installation section" in lowered and "## Installation" not in updated:
        updated += "\n## Installation\npip install .\n"
    if "duplicate heading" in lowered:
        seen = False
        lines = []
        for line in updated.splitlines():
            if line.strip() == "## Usage":
                if seen:
                    continue
                seen = True
            lines.append(line)
        updated = "\n".join(lines) + "\n"
    return updated


def _violations(root: Path, case: RealisticCase, modified: list[str]) -> list[str]:
    violations: list[str] = []
    allowed = set(case.allowed_files)
    if allowed:
        for path in modified:
            if path not in allowed:
                violations.append(f"modified disallowed file: {path}")
    if "do not modify tests" in case.prompt.lower() and any(path.startswith("tests/") for path in modified):
        violations.append("modified tests despite constraint")
    if "only modify one file" in case.prompt.lower() and len(set(modified)) > 1:
        violations.append("modified more than one file")
    if "new file forbidden" in case.prompt.lower():
        for path in modified:
            if not (root / path).exists():
                violations.append(f"created forbidden file: {path}")
    return violations


if __name__ == "__main__":
    main()
