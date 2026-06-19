from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .coding_loop import is_likely_code_task, is_verification_command, parse_exit_code
from .tools import ToolResult


class TaskPhase(str, Enum):
    INTAKE = "INTAKE"
    EXPLORE = "EXPLORE"
    LOCALIZE = "LOCALIZE"
    PLAN = "PLAN"
    EDIT = "EDIT"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    FINAL = "FINAL"


@dataclass
class PhaseDecision:
    allow: bool
    reason: str = ""
    next_phase: TaskPhase | None = None
    instruction: str = ""


@dataclass
class TaskState:
    phase: TaskPhase = TaskPhase.INTAKE
    task_type: str = "question"
    is_code_task: bool = False
    explored: bool = False
    localized: bool = False
    planned: bool = False
    edited: bool = False
    verified: bool = False
    verification_passed: bool = False
    repair_attempts: int = 0
    max_repair_attempts: int = 3
    candidate_files: list[str] = field(default_factory=list)
    planned_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    last_failure_summary: str = ""
    phase_history: list[str] = field(default_factory=list)
    read_files: list[str] = field(default_factory=list)
    allow_new_files: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "task_type": self.task_type,
            "is_code_task": self.is_code_task,
            "explored": self.explored,
            "localized": self.localized,
            "planned": self.planned,
            "edited": self.edited,
            "verified": self.verified,
            "verification_passed": self.verification_passed,
            "repair_attempts": self.repair_attempts,
            "max_repair_attempts": self.max_repair_attempts,
            "candidate_files": list(self.candidate_files),
            "planned_files": list(self.planned_files),
            "modified_files": list(self.modified_files),
            "verification_commands": list(self.verification_commands),
            "last_failure_summary": self.last_failure_summary,
            "phase_history": list(self.phase_history),
            "read_files": list(self.read_files),
            "allow_new_files": self.allow_new_files,
        }


class TaskStateMachine:
    """Enforce a staged coding workflow around the model/tool loop."""

    EXPLORE_TOOLS = {"list_files", "read_file", "search_text", "context_snapshot", "git_status", "git_diff"}
    READ_TOOLS = {"read_file"}
    WRITE_TOOLS = {"write_file", "replace_text", "apply_patch"}
    NON_VERIFICATION_COMMANDS = {"echo", "cat", "ls", "dir", "pwd", "find", "grep", "git status", "git diff"}
    MODIFICATION_TOKENS = {
        "fix",
        "bug",
        "failing",
        "failure",
        "error",
        "implement",
        "refactor",
        "add",
        "update",
        "patch",
        "edit",
        "change",
        "修复",
        "修改",
        "报错",
        "实现",
        "增加",
        "添加",
        "更新",
        "重构",
        "补丁",
    }
    NEW_FILE_TOKENS = {"add", "create", "new file", "新增", "添加", "创建"}
    PATH_RE = re.compile(
        r"(?<![\w/\\.-])([A-Za-z0-9_.\-/\\]+"
        r"\.(?:py|pyi|js|jsx|ts|tsx|json|md|txt|toml|yaml|yml|ini|cfg|rs|go|java|c|cc|cpp|h|hpp|cs|html|css|xml|sh|ps1|bat))"
    )

    def __init__(self, workspace: Path, *, max_repair_attempts: int = 3, enabled: bool = True) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.enabled = enabled
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self.state = TaskState(max_repair_attempts=self.max_repair_attempts)

    def start(self, prompt: str) -> None:
        task_prompt = self._extract_user_task(prompt)
        task_type = self._task_type(task_prompt)
        is_code_task = is_likely_code_task(task_prompt) or task_type == "code_modification" or "代码" in task_prompt or "测试" in task_prompt
        self.state = TaskState(
            phase=TaskPhase.INTAKE,
            task_type=task_type,
            is_code_task=is_code_task,
            max_repair_attempts=self.max_repair_attempts,
            allow_new_files=self._allows_new_files(task_prompt),
        )
        self._set_phase(TaskPhase.EXPLORE if self._requires_staged_loop() else TaskPhase.FINAL, "start")

    def observe_assistant_text(self, text: str) -> None:
        if not self._requires_staged_loop() or not text:
            return
        paths = self._extract_paths_from_text(text)
        lowered = text.lower()
        if paths and any(token in lowered for token in ["candidate", "localize", "related", "target", "候选", "定位", "相关"]):
            self._add_many(self.state.candidate_files, paths)
            self.state.localized = True
            self._set_phase(TaskPhase.LOCALIZE, "localized from assistant text")
        if paths and any(token in lowered for token in ["plan", "planned_files", "modify", "edit", "change", "计划", "修改", "文件"]):
            self._add_many(self.state.planned_files, paths)
            self.state.planned = True
            self.state.localized = True
            self._set_phase(TaskPhase.PLAN, "planned files from assistant text")

    def before_tool(self, name: str, tool_input: dict[str, Any]) -> PhaseDecision:
        if not self.enabled or not self._requires_staged_loop():
            return PhaseDecision(True, next_phase=self.state.phase)
        if name in self.WRITE_TOOLS:
            return self._before_write(name, tool_input)
        if name == "run_shell":
            return self._before_shell(tool_input)
        return PhaseDecision(True, next_phase=self.state.phase)

    def observe_tool_result(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        if not self.enabled or not self._requires_staged_loop():
            return
        if result.is_error:
            return
        if name in self.EXPLORE_TOOLS:
            self.state.explored = True
            self._set_phase(TaskPhase.EXPLORE, f"explored with {name}")
            self._record_candidates_from_tool(name, tool_input, result)
        if name in self.READ_TOOLS:
            path = self._normalize_path(str(tool_input.get("path", "")))
            if path:
                self._add(self.state.read_files, path)
                self._add(self.state.candidate_files, path)
                self.state.localized = True
                self._set_phase(TaskPhase.LOCALIZE, f"read target {path}")
        if name in self.WRITE_TOOLS and not bool(tool_input.get("dry_run")):
            targets = self._target_files_for_tool(name, tool_input)
            self._add_many(self.state.modified_files, targets)
            self.state.edited = True
            self.state.verified = False
            self.state.verification_passed = False
            if self.state.phase == TaskPhase.REPAIR:
                self.state.repair_attempts += 1
            self._set_phase(TaskPhase.EDIT, "code edited")
        if name == "run_shell":
            command = str(tool_input.get("command", ""))
            if self._is_code_verification_command(command):
                self._add(self.state.verification_commands, command)
                self.state.verified = True
                exit_code = parse_exit_code(result.content)
                self.state.verification_passed = exit_code == 0
                if self.state.verification_passed:
                    self.state.last_failure_summary = ""
                    self._set_phase(TaskPhase.FINAL, "verification passed")
                else:
                    self.state.last_failure_summary = self._failure_summary(command, exit_code, result.content)
                    self._set_phase(TaskPhase.REPAIR, "verification failed")

    def finish_decision(self) -> PhaseDecision:
        if not self.enabled:
            return PhaseDecision(True, next_phase=TaskPhase.FINAL)
        if not self._requires_staged_loop():
            return PhaseDecision(True, next_phase=TaskPhase.FINAL)
        if not self.state.explored:
            return self._block("exploration required", TaskPhase.EXPLORE, self.explore_instruction())
        if not self.state.localized:
            return self._block("localization required", TaskPhase.LOCALIZE, self.localize_instruction())
        if not self.state.planned:
            return self._block("edit plan required", TaskPhase.PLAN, self.plan_instruction())
        if not self.state.edited:
            return self._block("planned edit not applied", TaskPhase.EDIT, self.edit_instruction())
        if not self.state.verified:
            return self._block("verification required", TaskPhase.VERIFY, self.verify_instruction())
        if self.state.verification_passed:
            self._set_phase(TaskPhase.FINAL, "finish allowed")
            return PhaseDecision(True, next_phase=TaskPhase.FINAL)
        if self.state.repair_attempts < self.state.max_repair_attempts:
            return self._block("repair required", TaskPhase.REPAIR, self.repair_instruction())
        self._set_phase(TaskPhase.FINAL, "repair limit reached")
        return PhaseDecision(
            True,
            reason="repair limit reached",
            next_phase=TaskPhase.FINAL,
            instruction=self.failed_final_instruction(),
        )

    def explore_instruction(self) -> str:
        return (
            "Task phase: EXPLORE. Before editing, inspect the project. Use list_files, search_text, "
            "and read_file to understand the structure, tests, config, and likely target files. Do not edit yet."
        )

    def localize_instruction(self) -> str:
        return (
            "Task phase: LOCALIZE. Identify the most likely files, functions, classes, or tests involved. "
            "Read the target file content before editing."
        )

    def plan_instruction(self) -> str:
        candidates = ", ".join(self.state.candidate_files) if self.state.candidate_files else "[unknown]"
        return (
            "Task phase: PLAN. Produce a concise minimal edit plan before modifying files. "
            "Include a line like `planned_files: path1, path2`, explain why those files are enough, "
            f"and name the verification command. Candidate files: {candidates}."
        )

    def edit_instruction(self) -> str:
        planned = ", ".join(self.state.planned_files) if self.state.planned_files else "[none]"
        return (
            "Task phase: EDIT. Apply the minimal planned change only to planned_files. "
            "Prefer apply_patch or replace_text. Planned files: " + planned
        )

    def verify_instruction(self) -> str:
        return (
            "Task phase: VERIFY. Run a real test, lint, typecheck, or build-check command through run_shell. "
            "git_status, git_diff, context_snapshot, list_files, read_file, search_text, echo, cat, and ls do not count as verification."
        )

    def repair_instruction(self) -> str:
        return (
            "Task phase: REPAIR. The last verification failed. Inspect the failure output, identify the cause, "
            "make one minimal repair to the planned/read file set, then run the same real verification command again.\n\n"
            "Last failure summary:\n" + (self.state.last_failure_summary or "[no failure summary]")
        )

    def failed_final_instruction(self) -> str:
        return (
            "Repair limit reached. Final answer must clearly report that verification failed, include the last failed command, "
            "summarize the failure, list changed files, and state what remains unresolved."
        )

    def _before_write(self, name: str, tool_input: dict[str, Any]) -> PhaseDecision:
        targets = self._target_files_for_tool(name, tool_input)
        if not self.state.explored:
            return self._block("explore before edit", TaskPhase.EXPLORE, self.explore_instruction())
        if not self.state.planned:
            return self._block(
                "plan before edit",
                TaskPhase.PLAN,
                "You must produce a minimal edit plan before modifying files. Include `planned_files: ...` first.",
            )
        if not targets:
            return self._block("unknown edit target", TaskPhase.PLAN, "The edit target is unclear. Update the plan with concrete planned_files.")
        for target in targets:
            exists = self._path_exists(target)
            if exists and target not in self.state.read_files:
                return self._block(
                    "target file not read",
                    TaskPhase.LOCALIZE,
                    f"You must read the target file before editing it: {target}",
                )
            if target not in self.state.planned_files and not (self.state.allow_new_files and not exists):
                return self._block(
                    "file outside plan",
                    TaskPhase.PLAN,
                    f"This file is not in planned_files: {target}. Update the plan first or choose a planned file.",
                )
        return PhaseDecision(True, next_phase=TaskPhase.EDIT)

    def _before_shell(self, tool_input: dict[str, Any]) -> PhaseDecision:
        command = str(tool_input.get("command", ""))
        if not self.state.explored and not self._is_obviously_read_only_shell(command):
            return self._block("explore before shell", TaskPhase.EXPLORE, self.explore_instruction())
        if self.state.edited and not self._is_code_verification_command(command):
            return self._block(
                "real verification command required",
                TaskPhase.VERIFY,
                self.verify_instruction(),
            )
        return PhaseDecision(True, next_phase=self.state.phase)

    def _requires_staged_loop(self) -> bool:
        return self.state.is_code_task and self.state.task_type == "code_modification"

    def _task_type(self, prompt: str) -> str:
        lowered = prompt.lower()
        if any(token in lowered for token in self.MODIFICATION_TOKENS):
            return "code_modification"
        return "question"

    def _extract_user_task(self, prompt: str) -> str:
        markers = [
            "用户原始请求：",
            "用户原始请求:",
            "User original request:",
            "Original user request:",
        ]
        for marker in markers:
            if marker in prompt:
                return prompt.split(marker, 1)[1].strip()
        return prompt

    def _allows_new_files(self, prompt: str) -> bool:
        lowered = prompt.lower()
        return any(token in lowered for token in self.NEW_FILE_TOKENS)

    def _is_obviously_read_only_shell(self, command: str) -> bool:
        normalized = re.sub(r"\s+", " ", command.strip().lower())
        if not normalized:
            return False
        return any(normalized == item or normalized.startswith(item + " ") for item in self.NON_VERIFICATION_COMMANDS)

    def _is_code_verification_command(self, command: str) -> bool:
        return is_verification_command(command)

    def _record_candidates_from_tool(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        if name == "read_file":
            return
        if name == "search_text":
            paths = []
            for line in result.content.splitlines():
                if ":" not in line:
                    continue
                paths.append(line.split(":", 1)[0])
            self._add_many(self.state.candidate_files, [self._normalize_path(path) for path in paths])
            if paths:
                self.state.localized = True
        elif name in {"git_diff", "context_snapshot"}:
            self._add_many(self.state.candidate_files, self._extract_paths_from_text(result.content))

    def _target_files_for_tool(self, name: str, tool_input: dict[str, Any]) -> list[str]:
        if name in {"write_file", "replace_text"}:
            path = self._normalize_path(str(tool_input.get("path", "")))
            return [path] if path else []
        if name == "apply_patch":
            return self._extract_patch_targets(str(tool_input.get("patch", "")))
        return []

    def _extract_patch_targets(self, patch: str) -> list[str]:
        targets: list[str] = []
        pending_old = ""
        for line in patch.splitlines():
            if line.startswith("--- "):
                pending_old = self._normalize_patch_path(line[4:].strip())
            elif line.startswith("+++ "):
                new_path = self._normalize_patch_path(line[4:].strip())
                target = new_path if new_path != "/dev/null" else pending_old
                if target and target != "/dev/null":
                    self._add(targets, target)
        return targets

    def _extract_paths_from_text(self, text: str) -> list[str]:
        paths: list[str] = []
        for match in self.PATH_RE.finditer(text):
            self._add(paths, self._normalize_path(match.group(1)))
        return paths

    def _normalize_patch_path(self, raw_path: str) -> str:
        path = raw_path.split("\t", 1)[0].strip().strip("`'\"")
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        return self._normalize_path(path)

    def _normalize_path(self, path: str) -> str:
        path = path.strip().strip("`'\"")
        if not path or path == "/dev/null":
            return path
        return Path(path.replace("\\", "/")).as_posix()

    def _path_exists(self, path: str) -> bool:
        if not path:
            return False
        try:
            target = (self.workspace / path).resolve()
            target.relative_to(self.workspace)
        except ValueError:
            return False
        return target.exists()

    def _failure_summary(self, command: str, exit_code: int | None, content: str) -> str:
        excerpt = content.strip().replace("\r\n", "\n")
        if len(excerpt) > 1000:
            excerpt = excerpt[:1000] + "\n[truncated]"
        return f"{command} exited with {exit_code}: {excerpt}"

    def _block(self, reason: str, next_phase: TaskPhase, instruction: str) -> PhaseDecision:
        self._set_phase(next_phase, f"blocked: {reason}")
        return PhaseDecision(False, reason=reason, next_phase=next_phase, instruction=instruction)

    def _set_phase(self, phase: TaskPhase, reason: str) -> None:
        self.state.phase = phase
        entry = f"{phase.value}: {reason}"
        if not self.state.phase_history or self.state.phase_history[-1] != entry:
            self.state.phase_history.append(entry)

    def _add(self, target: list[str], item: str) -> None:
        item = self._normalize_path(item)
        if item and item not in target:
            target.append(item)

    def _add_many(self, target: list[str], items: list[str]) -> None:
        for item in items:
            self._add(target, item)
