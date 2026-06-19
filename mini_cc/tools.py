from __future__ import annotations

import locale
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .permission import PermissionPolicy, PermissionRisk, classify_shell_command, decide_permission
from .permission_ledger import PermissionLedger
from .tool_recovery import ToolRecoveryPolicy, recover_tool_failure


IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
MAX_TOOL_OUTPUT = 24_000


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class PatchFileChange:
    old_path: str
    new_path: str
    target_path: str
    old_lines: list[str]
    new_lines: list[str]
    added_lines: int
    deleted_lines: int
    is_delete: bool = False


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated {len(text) - limit} chars]"


def _decode_process_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    encodings = ["utf-8", locale.getpreferredencoding(False), "gbk", "cp936"]
    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


class ToolRunner:
    def __init__(
        self,
        workspace: Path,
        *,
        permission: str = "ask",
        shell_timeout: int = 30,
        permission_policy: PermissionPolicy | None = None,
        hooks: Any | None = None,
        permission_context: dict[str, Any] | None = None,
        permission_ledger: PermissionLedger | None = None,
        recovery_policy: ToolRecoveryPolicy | None = None,
    ) -> None:
        self.root = workspace.expanduser().resolve()
        self.permission = permission
        self.shell_timeout = shell_timeout
        self.permission_policy = permission_policy or PermissionPolicy.default()
        self.hooks = hooks
        self.permission_context = permission_context or {}
        self.permission_ledger = permission_ledger
        self.recovery_policy = recovery_policy
        self.permission_envelope: set[PermissionRisk] | None = None
        self.permission_envelope_reason = ""

    def clone_for_workspace(self, workspace: Path) -> "ToolRunner":
        clone = ToolRunner(
            workspace,
            permission=self.permission,
            shell_timeout=self.shell_timeout,
            permission_policy=self.permission_policy,
            hooks=self.hooks,
            permission_context=dict(self.permission_context),
            permission_ledger=self.permission_ledger,
            recovery_policy=self.recovery_policy,
        )
        clone.set_permission_envelope(self.permission_envelope, reason=self.permission_envelope_reason)
        return clone

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_files",
                "description": "List files inside the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to list."},
                        "recursive": {"type": "boolean", "default": False},
                        "max_entries": {"type": "integer", "default": 120},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file with line numbers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "default": 1},
                        "max_lines": {"type": "integer", "default": 200},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "search_text",
                "description": "Search text in workspace files using a Python regular expression.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_matches": {"type": "integer", "default": 50},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or replace a UTF-8 text file inside the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "replace_text",
                "description": "Replace text in one UTF-8 file. Fails if old text is absent.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "expected_replacements": {"type": "integer", "default": 1},
                    },
                    "required": ["path", "old", "new"],
                },
            },
            {
                "name": "apply_patch",
                "description": "Apply a unified diff patch inside the workspace. Use this for code edits when replacing exact old text is fragile.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": "Unified diff patch. File paths must stay inside the workspace.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": "Validate the patch without writing files.",
                        },
                    },
                    "required": ["patch"],
                },
            },
            {
                "name": "run_shell",
                "description": (
                    "Run a shell command in the workspace and return stdout/stderr. "
                    "On Windows, use PowerShell commands for filesystem operations and Start-Process "
                    "to open local programs such as VS Code when the user asks for that."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": self.shell_timeout},
                    },
                    "required": ["command"],
                },
            },
        ]

    def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        if self.hooks is not None:
            decision = self.hooks.emit("PreToolUse", {"name": name, "input": tool_input})
            if isinstance(decision.payload_updates.get("input"), dict):
                tool_input = dict(decision.payload_updates["input"])
            if not decision.allow:
                result = ToolResult(f"blocked by hook: {decision.reason}", is_error=True)
                self.hooks.post_tool_use(name, tool_input, is_error=True, content=result.content)
                return result
        result = self._run_once(name, tool_input)
        if result.is_error and self.recovery_policy is not None and self.recovery_policy.enabled:
            result = recover_tool_failure(
                name=name,
                tool_input=tool_input,
                initial_result=result,
                execute=self._run_once,
                policy=self.recovery_policy,
            )
        if self.hooks is not None:
            self.hooks.post_tool_use(name, tool_input, is_error=result.is_error, content=result.content)
        return result

    def _run_once(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        try:
            if name == "list_files":
                return ToolResult(self.list_files(**tool_input))
            if name == "read_file":
                return ToolResult(self.read_file(**tool_input))
            if name == "search_text":
                return ToolResult(self.search_text(**tool_input))
            if name == "write_file":
                return ToolResult(self.write_file(**tool_input))
            if name == "replace_text":
                return ToolResult(self.replace_text(**tool_input))
            if name == "apply_patch":
                return ToolResult(self.apply_patch(**tool_input))
            if name == "run_shell":
                return ToolResult(self.run_shell(**tool_input))
            raise ToolError(f"Unknown tool: {name}")
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)

    def resolve(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolError(f"Path escapes workspace: {path}") from exc
        return candidate

    def list_files(self, path: str = ".", recursive: bool = False, max_entries: int = 120) -> str:
        base = self.resolve(path)
        if not base.exists():
            raise ToolError(f"Path does not exist: {path}")
        if not base.is_dir():
            raise ToolError(f"Path is not a directory: {path}")

        iterator = base.rglob("*") if recursive else base.iterdir()
        rows: list[str] = []
        for item in iterator:
            if self._is_ignored(item):
                continue
            rel = item.relative_to(self.root).as_posix()
            rows.append(rel + ("/" if item.is_dir() else ""))
            if len(rows) >= max_entries:
                rows.append(f"[stopped after {max_entries} entries]")
                break
        return "\n".join(sorted(rows)) or "[empty]"

    def read_file(self, path: str, start_line: int = 1, max_lines: int = 200) -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"File does not exist: {path}")
        if not target.is_file():
            raise ToolError(f"Path is not a file: {path}")

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        count = max(1, min(int(max_lines), 1000))
        selected = lines[start - 1 : start - 1 + count]
        if not selected:
            return "[no lines in requested range]"
        rendered = [f"{line_no}: {line}" for line_no, line in enumerate(selected, start=start)]
        return _clip("\n".join(rendered))

    def search_text(self, pattern: str, path: str = ".", max_matches: int = 50) -> str:
        base = self.resolve(path)
        if not base.exists():
            raise ToolError(f"Path does not exist: {path}")

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}") from exc

        files = [base] if base.is_file() else base.rglob("*")
        matches: list[str] = []
        limit = max(1, min(int(max_matches), 500))
        for file_path in files:
            if self._is_ignored(file_path) or not file_path.is_file():
                continue
            if file_path.stat().st_size > 1_000_000:
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if regex.search(line):
                    rel = file_path.relative_to(self.root).as_posix()
                    matches.append(f"{rel}:{line_no}: {line[:240]}")
                    if len(matches) >= limit:
                        return _clip("\n".join(matches) + f"\n[stopped after {limit} matches]")
        return _clip("\n".join(matches) if matches else "[no matches]")

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        self._require_permission(
            f"write {target.relative_to(self.root).as_posix()}",
            PermissionRisk.WORKSPACE_WRITE,
            tool_name="write_file",
            tool_input={"path": path, "content": content},
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        rel = target.relative_to(self.root).as_posix()
        self._emit_file_changed(path=rel, operation="write", tool="write_file", chars=len(content))
        return f"Wrote {rel} ({len(content)} chars)"

    def replace_text(
        self,
        path: str,
        old: str,
        new: str,
        expected_replacements: int = 1,
    ) -> str:
        target = self.resolve(path)
        if not target.exists() or not target.is_file():
            raise ToolError(f"File does not exist: {path}")
        original = target.read_text(encoding="utf-8", errors="replace")
        count = original.count(old)
        if count == 0:
            raise ToolError("Old text was not found")
        expected = int(expected_replacements)
        if expected > 0 and count != expected:
            raise ToolError(f"Expected {expected} replacement(s), found {count}")

        self._require_permission(
            f"replace text in {target.relative_to(self.root).as_posix()}",
            PermissionRisk.WORKSPACE_WRITE,
            tool_name="replace_text",
            tool_input={
                "path": path,
                "old": old,
                "new": new,
                "expected_replacements": expected_replacements,
            },
        )
        updated = original.replace(old, new)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
        rel = target.relative_to(self.root).as_posix()
        self._emit_file_changed(path=rel, operation="replace", tool="replace_text", chars=len(updated))
        return f"Replaced {count} occurrence(s) in {rel}"

    def apply_patch(self, patch: str, dry_run: bool = False) -> str:
        changes = self._parse_unified_patch(patch)
        if not changes:
            raise ToolError("Patch contains no file changes")
        changed_files = [change.target_path for change in changes]
        if not dry_run:
            self._require_permission(
                "apply patch to " + ", ".join(changed_files),
                PermissionRisk.WORKSPACE_WRITE,
                tool_name="apply_patch",
                tool_input={"patch": patch, "dry_run": dry_run},
            )
            for change in changes:
                target = self.resolve(change.target_path)
                if change.is_delete:
                    if target.exists():
                        target.unlink()
                    operation = "delete"
                    chars = 0
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = "\n".join(change.new_lines)
                    if change.new_lines:
                        content += "\n"
                    with target.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(content)
                    operation = "patch"
                    chars = len(content)
                self._emit_file_changed(path=change.target_path, operation=operation, tool="apply_patch", chars=chars)
        added = sum(change.added_lines for change in changes)
        deleted = sum(change.deleted_lines for change in changes)
        return (
            "Applied patch" if not dry_run else "Patch dry-run succeeded"
        ) + (
            f"\ndry_run={str(bool(dry_run)).lower()}"
            f"\nchanged_files: {', '.join(changed_files)}"
            f"\nadded_lines: {added}"
            f"\ndeleted_lines: {deleted}"
        )

    def _parse_unified_patch(self, patch: str) -> list[PatchFileChange]:
        lines = patch.splitlines()
        changes: list[PatchFileChange] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.startswith("--- "):
                index += 1
                continue
            old_path = self._normalize_patch_path(line[4:].strip())
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ToolError("Invalid unified diff: missing +++ file header")
            new_path = self._normalize_patch_path(lines[index][4:].strip())
            index += 1
            target_path = new_path if new_path != "/dev/null" else old_path
            if target_path == "/dev/null":
                raise ToolError("Invalid unified diff: both file paths are /dev/null")
            self.resolve(target_path)
            source_path = old_path if old_path != "/dev/null" else target_path
            source = self.resolve(source_path)
            original_lines: list[str] = []
            if source.exists():
                if not source.is_file():
                    raise ToolError(f"Patch target is not a file: {source_path}")
                original_lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
            elif old_path != "/dev/null":
                raise ToolError(f"Patch target does not exist: {source_path}")
            patched_lines, added, deleted, index = self._apply_hunks_to_lines(
                original_lines,
                lines,
                index,
                target_path,
            )
            changes.append(
                PatchFileChange(
                    old_path=old_path,
                    new_path=new_path,
                    target_path=target_path,
                    old_lines=original_lines,
                    new_lines=patched_lines,
                    added_lines=added,
                    deleted_lines=deleted,
                    is_delete=new_path == "/dev/null",
                )
            )
        return changes

    def _apply_hunks_to_lines(
        self,
        original_lines: list[str],
        patch_lines: list[str],
        index: int,
        target_path: str,
    ) -> tuple[list[str], int, int, int]:
        output: list[str] = []
        source_index = 0
        added = 0
        deleted = 0
        saw_hunk = False
        while index < len(patch_lines):
            line = patch_lines[index]
            if line.startswith("--- "):
                break
            if not line.startswith("@@ "):
                index += 1
                continue
            saw_hunk = True
            match = re.match(r"@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@", line)
            if not match:
                raise ToolError(f"Invalid hunk header for {target_path}: {line}")
            old_start = int(match.group("old_start"))
            hunk_source_index = max(0, old_start - 1)
            if hunk_source_index < source_index:
                raise ToolError(f"Overlapping hunk for {target_path}: {line}")
            output.extend(original_lines[source_index:hunk_source_index])
            source_index = hunk_source_index
            index += 1
            while index < len(patch_lines):
                hunk_line = patch_lines[index]
                if hunk_line.startswith("@@ ") or hunk_line.startswith("--- "):
                    break
                if hunk_line.startswith("\\"):
                    index += 1
                    continue
                if not hunk_line:
                    raise ToolError(f"Invalid empty patch line for {target_path}; expected context, +, -, or hunk header")
                marker = hunk_line[0]
                value = hunk_line[1:]
                if marker == " ":
                    if source_index >= len(original_lines) or original_lines[source_index] != value:
                        raise ToolError(f"Patch context mismatch in {target_path}: {value!r}")
                    output.append(value)
                    source_index += 1
                elif marker == "-":
                    if source_index >= len(original_lines) or original_lines[source_index] != value:
                        raise ToolError(f"Patch removal mismatch in {target_path}: {value!r}")
                    source_index += 1
                    deleted += 1
                elif marker == "+":
                    output.append(value)
                    added += 1
                else:
                    raise ToolError(f"Invalid patch line for {target_path}: {hunk_line}")
                index += 1
        if not saw_hunk:
            raise ToolError(f"Unified diff for {target_path} contains no hunks")
        output.extend(original_lines[source_index:])
        return output, added, deleted, index

    def _normalize_patch_path(self, raw_path: str) -> str:
        path = raw_path.split("\t", 1)[0].strip()
        if not path:
            raise ToolError("Invalid unified diff: empty file path")
        if path == "/dev/null":
            return path
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ToolError(f"Path escapes workspace: {path}")
        return candidate.as_posix()

    def run_shell(self, command: str, timeout: int | None = None) -> str:
        command_decision = classify_shell_command(command)
        if not command_decision.allow:
            self._emit_permission_denied(
                name="run_shell",
                action=f"run shell command: {command}",
                risk=command_decision.risk,
                reason=command_decision.reason,
                tool_input={"command": command, "timeout": timeout},
            )
            raise ToolError(f"Blocked {command_decision.risk.value}: {command_decision.reason}: {command}")
        self._require_permission(
            f"run shell command: {command}",
            command_decision.risk,
            command_decision.reason,
            tool_name="run_shell",
            tool_input={"command": command, "timeout": timeout},
        )
        seconds = self.shell_timeout if timeout is None else int(timeout)
        subprocess_timeout = None if seconds <= 0 else seconds
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            shell=True,
            timeout=subprocess_timeout,
        )
        stdout = _decode_process_output(completed.stdout)
        stderr = _decode_process_output(completed.stderr)
        output = (
            f"exit_code={completed.returncode}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
        return _clip(output)

    def _require_permission(
        self,
        action: str,
        risk: PermissionRisk = PermissionRisk.WORKSPACE_WRITE,
        reason: str | None = None,
        *,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        name = tool_name or "permission"
        if self.permission_envelope is not None and risk not in self.permission_envelope:
            detail = self.permission_envelope_reason or "risk not declared by active task plan"
            reason_text = f"blocked by plan-scoped permission envelope: {risk.value}; {detail}"
            self._emit_permission_denied(
                name=name,
                action=action,
                risk=risk,
                reason=reason_text,
                tool_input=tool_input,
            )
            raise ToolError(reason_text)
        decision = decide_permission(self.permission, action, risk, self.permission_policy)
        if decision.allow:
            self._record_permission_ledger(
                decision="allowed",
                name=name,
                action=action,
                risk=risk,
                reason=decision.reason or reason or "",
                tool_input=tool_input,
            )
            return
        if self.permission == "ask":
            request_id = self._record_permission_ledger(
                decision="requested",
                name=name,
                action=action,
                risk=risk,
                reason=decision.reason or reason or "",
                tool_input=tool_input,
            )
            request = self._emit_permission_request(
                name=name,
                action=action,
                risk=risk,
                tool_input=tool_input,
            )
            if not request.allow:
                self._emit_permission_denied(
                    name=name,
                    action=action,
                    risk=risk,
                    reason=request.reason or "permission request hook denied",
                    tool_input=tool_input,
                    ledger_request_id=request_id,
                )
                raise ToolError(request.reason or f"Permission request denied: {action}")
        if self.permission == "read-only":
            detail = decision.reason or reason or ""
            self._emit_permission_denied(
                name=name,
                action=action,
                risk=risk,
                reason=detail,
                tool_input=tool_input,
            )
            raise ToolError(f"Permission denied in read-only mode ({risk.value}): {action}; {detail}")
        if self.permission == "auto":
            detail = decision.reason or reason or ""
            self._emit_permission_denied(
                name=name,
                action=action,
                risk=risk,
                reason=detail,
                tool_input=tool_input,
            )
            raise ToolError(f"Permission denied in auto mode ({risk.value}): {action}; {detail}")
        answer = input(f"Allow agent to {action} ({risk.value})? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            self._emit_permission_denied(
                name=name,
                action=action,
                risk=risk,
                reason="user denied permission request",
                tool_input=tool_input,
                ledger_request_id=request_id,
            )
            raise ToolError(f"User denied: {action}")
        self._record_permission_ledger(
            decision="allowed",
            name=name,
            action=action,
            risk=risk,
            reason="user allowed permission request",
            tool_input=tool_input,
            request_id=request_id,
        )

    def _is_ignored(self, path: Path) -> bool:
        return any(part in IGNORED_DIRS for part in path.parts)

    def _emit_permission_request(
        self,
        *,
        name: str,
        action: str,
        risk: PermissionRisk,
        tool_input: dict[str, Any] | None = None,
    ) -> Any:
        hooks = getattr(self, "hooks", None)
        if hooks is None:
            return _PermissionHookFallback()
        return hooks.permission_request(
            name=name,
            action=action,
            risk=risk.value,
            tool_input=tool_input or {},
            session_id=self.permission_context.get("session_id"),
            subagent=self.permission_context.get("subagent"),
        )

    def _emit_file_changed(self, *, path: str, operation: str, tool: str, chars: int) -> None:
        hooks = getattr(self, "hooks", None)
        if hooks is None:
            return
        hooks.file_changed(
            path=path,
            operation=operation,
            tool=tool,
            chars=chars,
            session_id=self.permission_context.get("session_id"),
        )

    def _emit_permission_denied(
        self,
        *,
        name: str,
        action: str,
        risk: PermissionRisk,
        reason: str,
        tool_input: dict[str, Any] | None = None,
        ledger_request_id: str | None = None,
    ) -> None:
        self._record_permission_ledger(
            decision="denied",
            name=name,
            action=action,
            risk=risk,
            reason=reason,
            tool_input=tool_input,
            request_id=ledger_request_id,
        )
        hooks = getattr(self, "hooks", None)
        if hooks is None:
            return
        hooks.permission_denied(
            name=name,
            action=action,
            risk=risk.value,
            reason=reason,
            tool_input=tool_input or {},
            session_id=self.permission_context.get("session_id"),
            subagent=self.permission_context.get("subagent"),
        )

    def _record_permission_ledger(
        self,
        *,
        decision: str,
        name: str,
        action: str,
        risk: PermissionRisk,
        reason: str,
        tool_input: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> str | None:
        if self.permission_ledger is None:
            return request_id
        return self.permission_ledger.record(
            decision=decision,
            name=name,
            action=action,
            risk=risk.value,
            reason=reason,
            tool_input=tool_input or {},
            session_id=self.permission_context.get("session_id"),
            subagent=self.permission_context.get("subagent"),
            request_id=request_id,
        )

    def set_permission_envelope(self, risks: set[PermissionRisk] | list[str] | None, *, reason: str = "") -> None:
        if risks is None:
            self.permission_envelope = None
            self.permission_envelope_reason = ""
            return
        normalized: set[PermissionRisk] = set()
        for item in risks:
            if isinstance(item, PermissionRisk):
                normalized.add(item)
                continue
            try:
                normalized.add(PermissionRisk(str(item)))
            except ValueError:
                continue
        self.permission_envelope = normalized
        self.permission_envelope_reason = reason


@dataclass(frozen=True)
class _PermissionHookFallback:
    allow: bool = True
    reason: str = ""
