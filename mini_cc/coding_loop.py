from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tools import ToolResult
from .verification import best_verification_command
from .verification_policy import VerificationPolicy


CODING_TASK_TOKENS = {
    "fix",
    "bug",
    "test",
    "failing",
    "failure",
    "error",
    "implement",
    "refactor",
    "add",
    "update",
    "patch",
    "修改",
    "修复",
    "报错",
    "实现",
    "增加",
    "添加",
    "更新",
    "重构",
    "测试",
    "代码",
    "补丁",
}

WRITE_TOOLS = {"write_file", "replace_text", "apply_patch"}
NON_VERIFICATION_TOOLS = {
    "git_status",
    "git_diff",
    "list_files",
    "read_file",
    "search_text",
    "context_snapshot",
    "memory_read",
    "memory_write",
    "todo_read",
    "todo_write",
    "subagent_pipeline",
}

VERIFICATION_PREFIXES = {
    "make check",
    "make test",
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "py -m pytest",
    "py -3 -m pytest",
    "unittest",
    "python -m unittest",
    "python3 -m unittest",
    "py -m unittest",
    "py -3 -m unittest",
    "npm test",
    "npm run test",
    "npm run lint",
    "pnpm test",
    "pnpm run test",
    "pnpm run lint",
    "yarn test",
    "yarn run test",
    "yarn lint",
    "ruff",
    "ruff check",
    "mypy",
    "tsc",
    "npx tsc",
    "markdownlint",
    "mkdocs",
    "sphinx-build",
    "cargo check",
    "cargo test",
    "go test",
    "mvn test",
    "gradle test",
    ".\\gradlew test",
    "./gradlew test",
}

_VERIFICATION_POLICY = VerificationPolicy()


@dataclass
class VerificationCommand:
    command: str
    exit_code: int | None
    passed: bool
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout_excerpt": self.stdout_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
        }


@dataclass
class CodingTaskState:
    enabled: bool = True
    code_modified: bool = False
    modified_files: list[str] = field(default_factory=list)
    verification_commands: list[VerificationCommand] = field(default_factory=list)
    last_verification_passed: bool = False
    last_verification_failed: bool = False
    last_failed_command: str | None = None
    last_failure_summary: str = ""
    repair_attempts: int = 0
    max_repair_attempts: int = 3
    required: bool = False
    test_command: str | None = None
    discovered_test_command: str | None = None
    final_status: str = "not_required"
    dirty_since_verification: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "code_modified": self.code_modified,
            "modified_files": list(self.modified_files),
            "verification_commands": [command.to_json() for command in self.verification_commands],
            "last_verification_passed": self.last_verification_passed,
            "last_verification_failed": self.last_verification_failed,
            "last_failed_command": self.last_failed_command,
            "last_failure_summary": self.last_failure_summary,
            "repair_attempts": self.repair_attempts,
            "max_repair_attempts": self.max_repair_attempts,
            "required": self.required,
            "test_command": self.test_command,
            "discovered_test_command": self.discovered_test_command,
            "final_status": self.final_status,
            "dirty_since_verification": self.dirty_since_verification,
        }


CodingReliabilityState = CodingTaskState


@dataclass(frozen=True)
class CodingLoopDecision:
    allow_finish: bool
    instruction: str = ""
    reason: str = ""
    status: str = "not_required"


class CodingLoopPolicy:
    def __init__(
        self,
        workspace: Path,
        *,
        enabled: bool = True,
        test_command: str | None = None,
        max_repair_attempts: int = 3,
        require_verification: bool = False,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.enabled = enabled
        self.explicit_test_command = test_command.strip() if test_command else None
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self.require_verification = require_verification
        self.state = CodingTaskState(enabled=enabled, max_repair_attempts=self.max_repair_attempts)

    def start(self, prompt: str) -> None:
        discovered = discover_test_command(self.workspace)
        self.state = CodingTaskState(
            enabled=self.enabled,
            max_repair_attempts=self.max_repair_attempts,
            required=self.require_verification or is_likely_code_task(prompt),
            test_command=self.explicit_test_command or discovered,
            discovered_test_command=discovered,
        )

    def observe_tool_result(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        if not self.enabled:
            return
        if name in WRITE_TOOLS and not result.is_error:
            self.state.required = True
            self.state.code_modified = True
            self.state.dirty_since_verification = True
            self._record_modified_files(name, tool_input, result)
            return
        if name == "run_shell" and is_verification_command(str(tool_input.get("command", ""))):
            verification = parse_verification_result(str(tool_input.get("command", "")), result)
            self.state.verification_commands.append(verification)
            self.state.dirty_since_verification = False
            self.state.last_verification_passed = verification.passed
            self.state.last_verification_failed = not verification.passed
            self.state.last_failed_command = None if verification.passed else verification.command
            self.state.last_failure_summary = "" if verification.passed else summarize_failure(verification)

    def finish_decision(self) -> CodingLoopDecision:
        if not self.enabled:
            return self._decision(True, status="not_required")
        if not self.state.code_modified:
            return self._decision(True, status="not_required")
        if not self.state.verification_commands:
            return self._decision(
                False,
                instruction=self.verification_required_instruction(),
                reason="code modified without verification",
                status="failed",
            )
        if self.state.dirty_since_verification:
            return self._decision(
                False,
                instruction=self.verification_required_instruction(),
                reason="code modified after last verification",
                status="failed",
            )
        if self.state.last_verification_failed and self.state.repair_attempts < self.state.max_repair_attempts:
            self.state.repair_attempts += 1
            return self._decision(
                False,
                instruction=self.repair_required_instruction(),
                reason="last verification command failed",
                status="failed",
            )
        if self.state.last_verification_failed:
            return self._decision(True, reason="repair limit reached", status="max_attempts_reached")
        return self._decision(True, status="passed")

    def _decision(self, allow_finish: bool, instruction: str = "", reason: str = "", status: str = "not_required") -> CodingLoopDecision:
        self.state.final_status = status
        return CodingLoopDecision(allow_finish, instruction=instruction, reason=reason, status=status)

    def verification_required_instruction(self) -> str:
        command_hint = (
            f" Use this command if suitable: {self.state.test_command!r}."
            if self.state.test_command
            else " No test command was auto-detected; inspect the project and choose the most local deterministic test or lint command."
        )
        return (
            "Verification required before final answer. You modified code but have not run a real verification command. "
            "Run the project test command if available. "
            "git_status, git_diff, context_snapshot, list_files, read_file, and search_text do not count as verification."
            + command_hint
        )

    def repair_required_instruction(self) -> str:
        return (
            "The last verification command failed. Read the failure output, identify the cause, make one minimal repair, "
            "then run verification again. Do not make unrelated changes.\n\nLast failure summary:\n"
            + (self.state.last_failure_summary or "[no failure summary]")
        )

    def final_report(self, status: str | None = None) -> str:
        if not self.enabled or not self.state.code_modified:
            return ""
        decision_status = status or self.finish_decision().status
        last = self.state.verification_commands[-1] if self.state.verification_commands else None
        if last is None:
            result = "not run"
            command = "not run"
            exit_code = "n/a"
        else:
            result = "passed" if last.passed else "failed"
            command = last.command
            exit_code = "n/a" if last.exit_code is None else str(last.exit_code)
        unresolved = "none"
        if decision_status in {"failed", "max_attempts_reached", "max_turns_reached"} or (last is not None and not last.passed):
            unresolved = self.state.last_failure_summary or "verification failed; inspect the command output"
            if last is None:
                unresolved = "verification was not completed before the run stopped"
        return "\n".join(
            [
                "Summary:",
                "- Changed files: " + (", ".join(self.state.modified_files) if self.state.modified_files else "[unknown]"),
                "- Main changes: code changes were applied during this run",
                "",
                "Verification:",
                f"- Command: {command}",
                f"- Result: {result}",
                f"- Exit code: {exit_code}",
                "",
                "Remaining issues:",
                f"- {unresolved}",
            ]
        )

    def artifact_status(self) -> str:
        return self.finish_decision().status

    def write_artifact(self, status: str | None = None) -> Path | None:
        if not self.enabled:
            return None
        target = self.workspace / ".mini_cc" / "task-success" / "last-run.json"
        payload = {
            **self.state.to_json(),
            "coding_loop_enabled": True,
            "status": status or self.artifact_status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            return None
        return target

    def _record_modified_files(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        paths: list[str] = []
        if name in {"write_file", "replace_text"} and tool_input.get("path"):
            paths.append(str(tool_input["path"]))
        elif name == "apply_patch":
            match = re.search(r"^changed_files:\s*(?P<files>.+)$", result.content, flags=re.MULTILINE)
            if match:
                paths.extend(path.strip() for path in match.group("files").split(",") if path.strip())
        for path in paths:
            normalized = path.replace("\\", "/")
            if normalized not in self.state.modified_files:
                self.state.modified_files.append(normalized)


def is_likely_code_task(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(token in lowered for token in CODING_TASK_TOKENS)


def is_verification_command(command: str) -> bool:
    return _VERIFICATION_POLICY.is_real_verification(command)


def parse_verification_result(command: str, result: ToolResult) -> VerificationCommand:
    evaluated = _VERIFICATION_POLICY.evaluate_command(command, result.content)
    exit_code = evaluated.exit_code
    stdout = extract_section(result.content, "stdout:", "stderr:")
    stderr = extract_section(result.content, "stderr:", None)
    return VerificationCommand(
        command=command,
        exit_code=exit_code,
        passed=(evaluated.passed and not result.is_error),
        stdout_excerpt=clip_excerpt(stdout),
        stderr_excerpt=clip_excerpt(stderr),
    )


def parse_exit_code(content: str) -> int | None:
    match = re.search(r"^exit_code=(-?\d+)\s*$", content, flags=re.MULTILINE)
    if not match:
        return None
    return int(match.group(1))


def summarize_failure(command: VerificationCommand) -> str:
    detail = command.stderr_excerpt.strip() or command.stdout_excerpt.strip() or "verification command failed"
    return f"{command.command} exited with {command.exit_code}: {detail[:800]}"


def extract_section(content: str, start_marker: str, end_marker: str | None) -> str:
    start = content.find(start_marker)
    if start < 0:
        return ""
    start += len(start_marker)
    if end_marker is None:
        return content[start:].strip()
    end = content.find(end_marker, start)
    if end < 0:
        return content[start:].strip()
    return content[start:end].strip()


def clip_excerpt(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated {len(text) - limit} chars]"


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())


def discover_test_command(workspace: Path, explicit: str | None = None) -> str | None:
    return best_verification_command(workspace, explicit=explicit)


def has_unittest_style_tests(tests_dir: Path) -> bool:
    inspected = 0
    for path in tests_dir.rglob("test*.py"):
        inspected += 1
        if inspected > 30:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "unittest.TestCase" in text or "import unittest" in text or "from unittest" in text:
            return True
    return False
