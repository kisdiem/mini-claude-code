from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_S20_SYSTEM_PROMPT
from .context import ContextBuilder
from .hooks import HookRuntime
from .memory import (
    format_memory_facts,
    format_recalled_memory,
    make_memory_fact,
    normalize_memory_payload,
    recall_memory_facts,
    serialize_memory_payload,
)
from .permission import PermissionPolicy, PermissionRisk
from .permission_ledger import PermissionLedger
from .subagents import SubagentRuntime
from .tool_recovery import ToolRecoveryPolicy
from .tools import MAX_TOOL_OUTPUT, ToolError, ToolResult, ToolRunner, _clip


S20_SYSTEM_PROMPT = DEFAULT_S20_SYSTEM_PROMPT


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"


class JsonStore:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self.default = default

    def read(self) -> Any:
        if not self.path.exists():
            return self.default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"Invalid JSON store {self.path.name}: {exc}") from exc

    def write(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class MemoryStore:
    def __init__(self, default: Any) -> None:
        self.value = default

    def read(self) -> Any:
        return self.value

    def write(self, value: Any) -> None:
        self.value = value


class S20ToolRunner(ToolRunner):
    """Comprehensive teaching runner inspired by the s20 end-state chapter."""

    def __init__(
        self,
        workspace: Path,
        *,
        permission: str = "ask",
        shell_timeout: int = 30,
        state_dir: Path | None = None,
        permission_policy: PermissionPolicy | None = None,
    ) -> None:
        super().__init__(
            workspace,
            permission=permission,
            shell_timeout=shell_timeout,
            permission_policy=permission_policy,
            recovery_policy=ToolRecoveryPolicy.default(),
        )
        self.state_dir = state_dir
        if self.state_dir is None:
            self.todos = MemoryStore([])
            self.memory = MemoryStore({})
            self.hooks = HookRuntime(self.root / ".mini_cc" / "hooks.log", enabled=False)
            self.permission_ledger = None
        else:
            self.todos = JsonStore(self.state_dir / "todos.json", [])
            self.memory = JsonStore(self.state_dir / "memory.json", {})
            self.hooks = HookRuntime(
                self.state_dir / "hooks.log",
                enabled=True,
            )
            self.permission_ledger = PermissionLedger(self.state_dir / "permission-ledger.jsonl")
        self.context_builder = ContextBuilder(self)
        self.subagents: SubagentRuntime | None = None

    def set_subagents(self, subagents: SubagentRuntime) -> None:
        self.subagents = subagents

    def clone_for_workspace(self, workspace: Path) -> "S20ToolRunner":
        clone = S20ToolRunner(
            workspace,
            permission=self.permission,
            shell_timeout=self.shell_timeout,
            state_dir=None,
            permission_policy=self.permission_policy,
        )
        clone.todos = self.todos
        clone.memory = self.memory
        clone.hooks = self.hooks
        clone.permission_context = dict(self.permission_context)
        clone.permission_ledger = self.permission_ledger
        clone.subagents = self.subagents
        clone.set_permission_envelope(self.permission_envelope, reason=self.permission_envelope_reason)
        return clone

    def schemas(self) -> list[dict[str, Any]]:
        schemas = super().schemas() + [
            {
                "name": "todo_read",
                "description": "Read the current task plan.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "todo_write",
                "description": "Replace the current task plan with explicit statuses.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"],
                                    },
                                },
                                "required": ["id", "content", "status"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            },
            {
                "name": "memory_read",
                "description": "Read durable project memory.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "memory_write",
                "description": "Store one durable project fact by key with optional scope, priority, source, and tags.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["project", "task", "user", "repo", "subagent"],
                            "default": "project",
                        },
                        "priority": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "default": 50,
                        },
                        "source": {"type": "string", "default": "manual"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": [],
                        },
                    },
                    "required": ["key", "value"],
                },
            },
            {
                "name": "memory_recall",
                "description": "Recall relevant durable memory facts by query, scope, priority, and limit.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "default": ""},
                        "scope": {
                            "type": "string",
                            "enum": ["project", "task", "user", "repo", "subagent"],
                        },
                        "min_priority": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "default": 0,
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                    },
                },
            },
            {
                "name": "skill_list",
                "description": "List local skills from .mini_cc/skills.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "skill_read",
                "description": "Read one local skill markdown file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "git_status",
                "description": "Read git status without modifying the repository.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "git_diff",
                "description": "Read git diff without modifying the repository.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "default": "."}},
                },
            },
            {
                "name": "context_snapshot",
                "description": "Return a compact workspace snapshot for long tasks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "token_budget": {
                            "type": "integer",
                            "description": "Approximate token budget for the returned context snapshot.",
                            "default": 4096,
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional task query used to recall relevant memory facts.",
                            "default": "",
                        },
                        "memory_limit": {
                            "type": "integer",
                            "description": "Maximum memory facts to include in the snapshot.",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 12,
                        },
                    },
                },
            },
        ]
        if self.subagents is not None:
            schemas.extend(self.subagents.schemas())
        return schemas

    def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        decision = self.hooks.pre_tool_use(name, tool_input)
        if not decision.allow:
            return ToolResult(decision.reason or f"Hook denied tool: {name}", is_error=True)
        if decision.payload_updates:
            tool_input = {**tool_input, **decision.payload_updates}
        if name in {
            "todo_read",
            "todo_write",
            "memory_read",
            "memory_write",
            "memory_recall",
            "skill_list",
            "skill_read",
            "git_status",
            "git_diff",
            "context_snapshot",
            "subagent_list",
            "subagent_run",
            "subagent_pipeline",
            "subagent_replay_events",
            "subagent_runtime_report",
            "subagent_mcp_registry",
            "subagent_mcp_tool_retrieval",
            "subagent_mcp_vector_index",
        }:
            try:
                content = getattr(self, name)(**tool_input)
                result = ToolResult(content)
            except Exception as exc:
                result = ToolResult(str(exc), is_error=True)
        else:
            result = super().run(name, tool_input)
        self.hooks.post_tool_use(
            name,
            tool_input,
            is_error=result.is_error,
            content=result.content,
        )
        return result

    def todo_read(self) -> str:
        items = self.todos.read()
        if not items:
            return "[no todos]"
        return "\n".join(
            f"{item.get('id')}: {item.get('status')} - {item.get('content')}"
            for item in items
        )

    def todo_write(self, items: list[dict[str, str]]) -> str:
        self._require_permission(
            "update todo state",
            PermissionRisk.WORKSPACE_WRITE,
            tool_name="todo_write",
            tool_input={"items": items},
        )
        normalized: list[dict[str, str]] = []
        seen_in_progress = 0
        for item in items:
            todo = TodoItem(
                id=str(item["id"]),
                content=str(item["content"]),
                status=str(item["status"]),
            )
            if todo.status not in {"pending", "in_progress", "completed"}:
                raise ToolError(f"Invalid todo status: {todo.status}")
            if todo.status == "in_progress":
                seen_in_progress += 1
            normalized.append(asdict(todo))
        if seen_in_progress > 1:
            raise ToolError("Only one todo can be in_progress")
        self.todos.write(normalized)
        for todo in normalized:
            self.hooks.task_created(
                task_id=todo["id"],
                content=todo["content"],
                status=todo["status"],
                source="todo_write",
            )
            if todo["status"] == "completed":
                self.hooks.task_completed(
                    task_id=todo["id"],
                    status=todo["status"],
                    content=todo["content"],
                    result="todo marked completed",
                )
        return f"Wrote {len(normalized)} todo item(s)"

    def memory_read(self) -> str:
        facts = normalize_memory_payload(self.memory.read())
        return _clip(format_memory_facts(facts))

    def memory_write(
        self,
        key: str,
        value: str,
        scope: str = "project",
        priority: int = 50,
        source: str = "manual",
        tags: list[str] | None = None,
    ) -> str:
        self._require_permission(
            f"write memory key {key}",
            PermissionRisk.WORKSPACE_WRITE,
            tool_name="memory_write",
            tool_input={
                "key": key,
                "value": value,
                "scope": scope,
                "priority": priority,
                "source": source,
                "tags": tags or [],
            },
        )
        facts = normalize_memory_payload(self.memory.read())
        facts[str(key)] = make_memory_fact(
            key=str(key),
            value=str(value),
            scope=scope,
            priority=priority,
            source=source,
            tags=tags or [],
        )
        self.memory.write(serialize_memory_payload(facts))
        return f"Wrote memory key: {key}"

    def memory_recall(
        self,
        query: str = "",
        scope: str | None = None,
        min_priority: int = 0,
        limit: int = 12,
    ) -> str:
        facts = normalize_memory_payload(self.memory.read())
        recalled = recall_memory_facts(
            facts,
            query=query,
            scope=scope,
            min_priority=min_priority,
            limit=limit,
        )
        return _clip(format_recalled_memory(recalled))

    def skill_list(self) -> str:
        if self.state_dir is None:
            return "[no local skills]"
        skills_dir = self.state_dir / "skills"
        if not skills_dir.exists():
            return "[no local skills]"
        skills = sorted(path.stem for path in skills_dir.glob("*.md") if path.is_file())
        return "\n".join(skills) if skills else "[no local skills]"

    def skill_read(self, name: str) -> str:
        if self.state_dir is None:
            raise ToolError("Skills are unavailable when state_dir is disabled")
        safe_name = Path(name).stem
        target = self.state_dir / "skills" / f"{safe_name}.md"
        if not target.exists():
            raise ToolError(f"Skill not found: {safe_name}")
        return _clip(target.read_text(encoding="utf-8", errors="replace"))

    def git_status(self) -> str:
        return self._git(["status", "--short", "--branch"])

    def git_diff(self, path: str = ".") -> str:
        target = self.resolve(path)
        rel = target.relative_to(self.root).as_posix()
        return self._git(["diff", "--", rel])

    def context_snapshot(
        self,
        token_budget: int | None = None,
        query: str = "",
        memory_limit: int = 12,
    ) -> str:
        return self.context_builder.workspace_snapshot(
            token_budget=token_budget,
            query=query,
            memory_limit=memory_limit,
        )

    def subagent_list(self) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        return self.subagents.list_subagents()

    def subagent_run(
        self,
        name: str,
        prompt: str,
        session_id: str | None = None,
        task_contract: dict[str, Any] | None = None,
    ) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        result = self.subagents.run(name, prompt, session_id=session_id, task_contract=task_contract)
        if result.is_error:
            raise ToolError(result.content)
        return result.content

    def subagent_pipeline(
        self,
        task: str,
        mode: str = "auto",
        task_contract: dict[str, Any] | None = None,
    ) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        result = self.subagents.run_pipeline(task, mode=mode, task_contract=task_contract)
        if result.is_error:
            raise ToolError(result.content)
        return result.content

    def subagent_replay_events(self) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        return self.subagents.replay_event_history_text()

    def subagent_runtime_report(self, format: str = "json") -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        return self.subagents.runtime_report(format=format)

    def subagent_mcp_registry(self, refresh: bool = True) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        return self.subagents.mcp_registry_json(refresh=refresh)

    def subagent_mcp_tool_retrieval(
        self,
        query: str,
        subagent: str | None = None,
        top_k: int = 8,
        expand: bool = False,
        use_embeddings: bool = True,
    ) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        result = self.subagents.retrieve_mcp_tools(
            query,
            subagent=subagent,
            top_k=top_k,
            expand=expand,
            use_embeddings=use_embeddings,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def subagent_mcp_vector_index(self, refresh: bool = True) -> str:
        if self.subagents is None:
            raise ToolError("Subagents are not configured")
        return self.subagents.mcp_tool_vector_index_json(refresh=refresh)

    def _git(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.shell_timeout,
            )
        except FileNotFoundError as exc:
            raise ToolError("git executable was not found") from exc
        output = completed.stdout.strip() or completed.stderr.strip()
        if completed.returncode != 0:
            raise ToolError(output or f"git {' '.join(args)} failed")
        return _clip(output or "[no output]")
