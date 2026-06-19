from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


HookHandler = Callable[["HookEvent"], "HookDecision | None"]
AgentHookHandler = Callable[["HookEvent", dict[str, Any]], "HookDecision | dict[str, Any] | None"]


HOOK_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class HookEvent:
    name: str
    payload: dict[str, Any]
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class HookDecision:
    allow: bool = True
    reason: str = ""
    payload_updates: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookEventSpec:
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    matcher_field: str | None = None
    description: str = ""


HOOK_EVENT_SPECS: dict[str, HookEventSpec] = {
    "SessionStart": HookEventSpec(
        "SessionStart",
        ("start_reason", "model"),
        ("prompt", "session_id"),
        "start_reason",
        "Agent session started.",
    ),
    "SessionEnd": HookEventSpec(
        "SessionEnd",
        ("reason", "status"),
        ("session_id", "duration_ms"),
        "reason",
        "Agent session ended.",
    ),
    "UserPromptSubmit": HookEventSpec(
        "UserPromptSubmit",
        ("prompt", "source"),
        ("session_id", "chars"),
        None,
        "User prompt submitted before planning/model execution.",
    ),
    "InstructionsLoaded": HookEventSpec(
        "InstructionsLoaded",
        ("reason", "source"),
        ("chars", "path"),
        "reason",
        "Workspace or user instructions loaded.",
    ),
    "UserPromptExpansion": HookEventSpec(
        "UserPromptExpansion",
        ("command_name", "prompt"),
        ("expanded_prompt", "source"),
        "command_name",
        "Slash command or prompt expansion happened.",
    ),
    "PreToolUse": HookEventSpec(
        "PreToolUse",
        ("name", "input"),
        ("session_id", "turn", "risk"),
        "name",
        "Tool call about to execute.",
    ),
    "PostToolUse": HookEventSpec(
        "PostToolUse",
        ("name", "input", "is_error", "chars", "content_preview"),
        ("session_id", "turn", "returncode"),
        "name",
        "Tool call finished.",
    ),
    "PostToolUseFailure": HookEventSpec(
        "PostToolUseFailure",
        ("name", "input", "error", "content_preview"),
        ("session_id", "turn", "error_type"),
        "name",
        "Tool call finished with an error.",
    ),
    "PostToolBatch": HookEventSpec(
        "PostToolBatch",
        ("count", "failed_count"),
        ("tools", "session_id", "turn"),
        None,
        "Batch of tool calls completed.",
    ),
    "PermissionRequest": HookEventSpec(
        "PermissionRequest",
        ("name", "action", "risk"),
        ("input", "session_id", "subagent"),
        "name",
        "Runtime is asking whether a risky action is allowed.",
    ),
    "PermissionDenied": HookEventSpec(
        "PermissionDenied",
        ("name", "action", "risk", "reason"),
        ("input", "session_id", "subagent"),
        "name",
        "Permission request or policy decision was denied.",
    ),
    "SubagentStart": HookEventSpec(
        "SubagentStart",
        ("agent_type", "handoff_id"),
        ("prompt", "model", "parent_session_id"),
        "agent_type",
        "Subagent run started.",
    ),
    "SubagentStop": HookEventSpec(
        "SubagentStop",
        ("agent_type", "status", "handoff_id"),
        ("session_id", "chars", "reason"),
        "agent_type",
        "Subagent run ended.",
    ),
    "TaskCreated": HookEventSpec(
        "TaskCreated",
        ("task_id", "content"),
        ("status", "source"),
        None,
        "Task or todo created.",
    ),
    "TaskCompleted": HookEventSpec(
        "TaskCompleted",
        ("task_id", "status"),
        ("content", "result"),
        None,
        "Task or todo completed.",
    ),
    "PreCompact": HookEventSpec(
        "PreCompact",
        ("trigger", "token_budget", "estimated_tokens"),
        ("session_id", "source_count"),
        "trigger",
        "Context compaction is about to run.",
    ),
    "PostCompact": HookEventSpec(
        "PostCompact",
        ("trigger", "token_budget", "estimated_tokens", "compressed_sections"),
        ("session_id", "summary_chars"),
        "trigger",
        "Context compaction finished.",
    ),
    "FileChanged": HookEventSpec(
        "FileChanged",
        ("path", "operation"),
        ("tool", "chars", "session_id"),
        None,
        "Workspace file changed.",
    ),
    "CwdChanged": HookEventSpec(
        "CwdChanged",
        ("old_cwd", "new_cwd"),
        ("reason",),
        None,
        "Current working directory changed.",
    ),
    "WorktreeCreate": HookEventSpec(
        "WorktreeCreate",
        ("path", "branch"),
        ("source",),
        None,
        "Worktree created.",
    ),
    "WorktreeRemove": HookEventSpec(
        "WorktreeRemove",
        ("path",),
        ("branch", "source"),
        None,
        "Worktree removed.",
    ),
    "ConfigChange": HookEventSpec(
        "ConfigChange",
        ("source", "path"),
        ("operation", "keys"),
        "source",
        "Configuration changed or was reloaded.",
    ),
    "Notification": HookEventSpec(
        "Notification",
        ("message",),
        ("type", "level", "session_id"),
        "type",
        "Notification emitted.",
    ),
    "Stop": HookEventSpec(
        "Stop",
        ("status", "reason"),
        ("session_id", "error"),
        None,
        "Agent stopped.",
    ),
    "StopFailure": HookEventSpec(
        "StopFailure",
        ("error_type", "message"),
        ("status", "session_id"),
        "error_type",
        "Stop hook or shutdown failed.",
    ),
    "Elicitation": HookEventSpec(
        "Elicitation",
        ("server", "request_id", "prompt"),
        ("schema", "session_id"),
        "server",
        "External server requested user input.",
    ),
    "ElicitationResult": HookEventSpec(
        "ElicitationResult",
        ("server", "request_id", "status"),
        ("response", "session_id"),
        "server",
        "User input request completed.",
    ),
    "TeammateIdle": HookEventSpec(
        "TeammateIdle",
        ("teammate", "idle_ms"),
        ("task_id", "session_id"),
        None,
        "Background teammate became idle.",
    ),
}

MATCHER_FIELDS = {
    name: spec.matcher_field
    for name, spec in HOOK_EVENT_SPECS.items()
    if spec.matcher_field is not None
}

NO_MATCHER_EVENTS = {
    name
    for name, spec in HOOK_EVENT_SPECS.items()
    if spec.matcher_field is None
}


@dataclass(frozen=True)
class ConfiguredHook:
    event_name: str
    matcher: str
    handler: dict[str, Any]
    source: str

    def matches(self, event: HookEvent) -> bool:
        if self.event_name in NO_MATCHER_EVENTS:
            return True
        field_name = MATCHER_FIELDS.get(self.event_name, "name")
        value = str(event.payload.get(field_name, ""))
        return matcher_matches(self.matcher, value)

    def run(self, event: HookEvent, runtime: "HookRuntime | None" = None) -> HookDecision | None:
        attempts = max(1, int(self.handler.get("retries", self.handler.get("retry", 0))) + 1)
        failure_mode = str(self.handler.get("failure_mode", self.handler.get("failureMode", "fail-closed"))).lower()
        last_failure: HookDecision | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                decision = self._run_once(event, runtime)
            except subprocess.TimeoutExpired:
                decision = HookDecision(False, f"configured hook timed out from {self.source}")
            except Exception as exc:
                decision = HookDecision(False, f"configured hook failed from {self.source}: {exc}")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if runtime is not None:
                runtime.record_hook_metric(self, event, decision, elapsed_ms=elapsed_ms, attempt=attempt)
            if not self.is_retryable_failure(decision):
                return decision
            last_failure = decision
            if attempt < attempts and runtime is not None:
                runtime.metrics["configured_hook_retries"] = runtime.metrics.get("configured_hook_retries", 0) + 1
        if failure_mode in {"fail-open", "open", "allow"}:
            reason = last_failure.reason if last_failure is not None else "configured hook failed"
            return HookDecision(True, f"configured hook fail-open from {self.source}: {reason}")
        return last_failure

    def _run_once(self, event: HookEvent, runtime: "HookRuntime | None" = None) -> HookDecision | None:
        hook_type = str(self.handler.get("type", "command"))
        if hook_type == "command":
            return self._run_command(event, runtime)
        if hook_type == "http":
            return self._run_http(event, runtime)
        if hook_type == "mcp":
            return self._run_mcp(event, runtime)
        if hook_type == "prompt":
            return self._run_prompt(event)
        if hook_type == "agent":
            return self._run_agent(event, runtime)
        return HookDecision(False, f"unsupported configured hook type {hook_type!r} from {self.source}")

    def is_retryable_failure(self, decision: HookDecision | None) -> bool:
        if decision is None or decision.allow:
            return False
        reason = decision.reason.lower()
        retry_markers = ["failed", "timed out", "timeout", "http hook failed", "invalid hook decision", "output exceeded"]
        return any(marker in reason for marker in retry_markers)

    def _run_command(self, event: HookEvent, runtime: "HookRuntime | None" = None) -> HookDecision | None:
        command = self.handler.get("command")
        if not isinstance(command, str) or not command.strip():
            return HookDecision(False, f"invalid command hook from {self.source}")
        timeout = int(self.handler.get("timeout", 30))
        payload = event_json(event, self.additional_context())
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            shell=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
            detail = self.control_output(detail, runtime=runtime, label="stderr")
            return HookDecision(False, f"command hook failed from {self.source}: {detail}")
        return decision_from_stdout(self.control_output(completed.stdout, runtime=runtime, label="stdout"))

    def _run_http(self, event: HookEvent, runtime: "HookRuntime | None" = None) -> HookDecision | None:
        url = self.handler.get("url")
        if not isinstance(url, str) or not url.strip():
            return HookDecision(False, f"invalid http hook url from {self.source}")
        method = str(self.handler.get("method", "POST")).upper()
        timeout = int(self.handler.get("timeout", 30))
        headers = {"Content-Type": "application/json"}
        configured_headers = self.handler.get("headers", {})
        if isinstance(configured_headers, dict):
            headers.update({str(key): str(value) for key, value in configured_headers.items()})
        body = json.dumps(event_json(event, self.additional_context()), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip() if exc.fp else str(exc)
            detail = self.control_output(detail, runtime=runtime, label="http-error")
            return HookDecision(False, f"http hook failed from {self.source}: HTTP {exc.code} {detail}")
        except urllib.error.URLError as exc:
            return HookDecision(False, f"http hook failed from {self.source}: {exc.reason}")
        return decision_from_stdout(self.control_output(response_text, runtime=runtime, label="http-response"))

    def _run_mcp(self, event: HookEvent, runtime: "HookRuntime | None") -> HookDecision | None:
        if runtime is None:
            return HookDecision(False, f"mcp hook has no runtime from {self.source}")
        server = str(self.handler.get("server", ""))
        tool = str(self.handler.get("tool", self.handler.get("name", "")))
        if not server or not tool:
            return HookDecision(False, f"invalid mcp hook server/tool from {self.source}")
        adapter = runtime.mcp_hook_adapters.get(server)
        if adapter is None:
            return HookDecision(False, f"mcp hook server not registered: {server}")
        result = adapter.call_tool(tool, {"event": event_json(event, self.additional_context()), "handler": self.handler})
        if result.is_error:
            return HookDecision(False, f"mcp hook failed from {self.source}: {result.content}")
        return decision_from_stdout(self.control_output(result.content, runtime=runtime, label="mcp-result"))

    def _run_prompt(self, event: HookEvent) -> HookDecision | None:
        payload = event_json(event, self.additional_context())
        updates: dict[str, Any] = {}
        configured_updates = self.handler.get("payload_updates")
        if isinstance(configured_updates, dict):
            for key, value in configured_updates.items():
                updates[str(key)] = render_hook_template(str(value), payload)
        template = self.handler.get("template", self.handler.get("prompt"))
        if isinstance(template, str):
            target = str(self.handler.get("target", "prompt"))
            updates[target] = render_hook_template(template, payload)
        if not updates:
            return HookDecision(False, f"prompt hook has no template or payload_updates from {self.source}")
        return HookDecision(True, str(self.handler.get("reason", "")), updates)

    def _run_agent(self, event: HookEvent, runtime: "HookRuntime | None") -> HookDecision | None:
        if runtime is None:
            return HookDecision(False, f"agent hook has no runtime from {self.source}")
        name = str(self.handler.get("agent", self.handler.get("name", "")))
        if not name:
            return HookDecision(False, f"invalid agent hook name from {self.source}")
        handler = runtime.agent_hook_handlers.get(name)
        if handler is None:
            return HookDecision(False, f"agent hook not registered: {name}")
        return decision_from_mapping(handler(event, self.handler))

    def additional_context(self) -> dict[str, Any]:
        raw = self.handler.get("additionalContext", self.handler.get("additional_context", {}))
        return dict(raw) if isinstance(raw, dict) else {}

    def max_output_chars(self) -> int:
        return max(256, int(self.handler.get("max_output_chars", self.handler.get("maxOutputChars", 65536))))

    def control_output(self, output: str, *, runtime: "HookRuntime | None", label: str) -> str:
        limit = self.max_output_chars()
        if len(output) <= limit:
            return output
        spill_path = runtime.spill_hook_output(self, label, output) if runtime is not None else None
        suffix = f"\n[hook output truncated from {len(output)} chars"
        if spill_path is not None:
            suffix += f"; full output spilled to {spill_path}"
            runtime.metrics["configured_hook_spills"] = runtime.metrics.get("configured_hook_spills", 0) + 1
        suffix += "]"
        return output[:limit] + suffix


class HookRuntime:
    """Small event bus for agent runtime hooks.

    The runtime intentionally supports both passive logging and active policy
    decisions. This keeps the teaching version simple while giving PreToolUse
    hooks a real way to block or adjust tool calls.
    """

    def __init__(self, log_path: Path | None = None, *, enabled: bool = True, spill_dir: Path | None = None) -> None:
        self.log_path = log_path
        self.enabled = enabled
        self.spill_dir = spill_dir
        self._handlers: dict[str, list[HookHandler]] = {}
        self._configured: dict[str, list[ConfiguredHook]] = {}
        self.mcp_hook_adapters: dict[str, Any] = {}
        self.agent_hook_handlers: dict[str, AgentHookHandler] = {}
        self.metrics: dict[str, Any] = {
            "events_emitted": 0,
            "configured_hook_attempts": 0,
            "configured_hook_successes": 0,
            "configured_hook_failures": 0,
            "configured_hook_blocks": 0,
            "configured_hook_retries": 0,
            "configured_hook_spills": 0,
            "configured_hook_duration_ms": 0,
            "by_event": {},
            "by_source": {},
        }

    def register(self, event_name: str, handler: HookHandler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def register_configured(self, hook: ConfiguredHook) -> None:
        self._configured.setdefault(hook.event_name, []).append(hook)

    def register_mcp_hook_adapter(self, name: str, adapter: Any) -> None:
        self.mcp_hook_adapters[str(name)] = adapter

    def register_agent_hook(self, name: str, handler: AgentHookHandler) -> None:
        self.agent_hook_handlers[str(name)] = handler

    def emit(self, event_name: str, payload: dict[str, Any]) -> HookDecision:
        normalized = normalize_hook_payload(event_name, payload)
        event = HookEvent(event_name, normalized)
        self._write(event)
        self.metrics["events_emitted"] = self.metrics.get("events_emitted", 0) + 1
        by_event = self.metrics.setdefault("by_event", {})
        event_metrics = by_event.setdefault(event_name, {"emitted": 0, "configured_attempts": 0, "blocks": 0, "failures": 0})
        event_metrics["emitted"] = event_metrics.get("emitted", 0) + 1

        combined_updates: dict[str, Any] = {}
        combined_reasons: list[str] = []
        for configured in self._configured.get(event_name, []):
            if not configured.matches(event):
                continue
            try:
                decision = configured.run(event, self)
            except Exception as exc:
                return HookDecision(False, f"configured hook {event_name} failed: {exc}")
            if decision is None:
                continue
            combined_updates.update(decision.payload_updates)
            if decision.reason:
                combined_reasons.append(decision.reason)
            if not decision.allow:
                return HookDecision(False, decision.reason, combined_updates)
        for handler in self._handlers.get(event_name, []):
            try:
                decision = handler(event)
            except Exception as exc:
                return HookDecision(False, f"hook {event_name} failed: {exc}")
            if decision is None:
                continue
            combined_updates.update(decision.payload_updates)
            if decision.reason:
                combined_reasons.append(decision.reason)
            if not decision.allow:
                return HookDecision(False, decision.reason, combined_updates)
        return HookDecision(True, "; ".join(combined_reasons), combined_updates)

    def record_hook_metric(
        self,
        hook: ConfiguredHook,
        event: HookEvent,
        decision: HookDecision | None,
        *,
        elapsed_ms: int,
        attempt: int,
    ) -> None:
        del attempt
        self.metrics["configured_hook_attempts"] = self.metrics.get("configured_hook_attempts", 0) + 1
        self.metrics["configured_hook_duration_ms"] = self.metrics.get("configured_hook_duration_ms", 0) + elapsed_ms
        source_key = hook.source
        by_source = self.metrics.setdefault("by_source", {})
        source_metrics = by_source.setdefault(source_key, {"attempts": 0, "successes": 0, "failures": 0, "blocks": 0, "duration_ms": 0})
        source_metrics["attempts"] += 1
        source_metrics["duration_ms"] += elapsed_ms
        by_event = self.metrics.setdefault("by_event", {})
        event_metrics = by_event.setdefault(event.name, {"emitted": 0, "configured_attempts": 0, "blocks": 0, "failures": 0})
        event_metrics["configured_attempts"] = event_metrics.get("configured_attempts", 0) + 1
        if decision is None or decision.allow:
            self.metrics["configured_hook_successes"] = self.metrics.get("configured_hook_successes", 0) + 1
            source_metrics["successes"] += 1
            return
        if hook.is_retryable_failure(decision):
            self.metrics["configured_hook_failures"] = self.metrics.get("configured_hook_failures", 0) + 1
            source_metrics["failures"] += 1
            event_metrics["failures"] = event_metrics.get("failures", 0) + 1
        else:
            self.metrics["configured_hook_blocks"] = self.metrics.get("configured_hook_blocks", 0) + 1
            source_metrics["blocks"] += 1
            event_metrics["blocks"] = event_metrics.get("blocks", 0) + 1

    def hook_metrics(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.metrics, ensure_ascii=False))

    def spill_hook_output(self, hook: ConfiguredHook, label: str, output: str) -> Path:
        directory = self.spill_dir
        if directory is None and self.log_path is not None:
            directory = self.log_path.parent / "hook-spills"
        if directory is None:
            directory = Path(".mini_cc") / "hook-spills"
        directory.mkdir(parents=True, exist_ok=True)
        safe_event = re.sub(r"[^A-Za-z0-9_.-]+", "_", hook.event_name)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        path = directory / f"{safe_event}-{safe_label}-{uuid.uuid4().hex}.txt"
        path.write_text(output, encoding="utf-8")
        return path

    def pre_tool_use(self, name: str, tool_input: dict[str, Any]) -> HookDecision:
        return self.emit("PreToolUse", {"name": name, "input": tool_input})

    def post_tool_use(
        self,
        name: str,
        tool_input: dict[str, Any],
        *,
        is_error: bool,
        content: str,
    ) -> HookDecision:
        return self.emit(
            "PostToolUse",
            {
                "name": name,
                "input": tool_input,
                "is_error": is_error,
                "chars": len(content),
                "content_preview": content[:800],
            },
        )

    def stop(self, payload: dict[str, Any]) -> HookDecision:
        return self.emit("Stop", payload)

    def stop_failure(
        self,
        *,
        error_type: str,
        message: str,
        status: str = "",
        session_id: str | None = None,
    ) -> HookDecision:
        return self.emit(
            "StopFailure",
            {
                "error_type": error_type,
                "message": message,
                "status": status,
                "session_id": session_id,
            },
        )

    def notification(self, message: str, **payload: Any) -> HookDecision:
        return self.emit("Notification", {"message": message, **payload})

    def session_start(
        self,
        *,
        prompt: str,
        model: str | None,
        start_reason: str = "user_prompt",
        session_id: str | None = None,
    ) -> HookDecision:
        return self.emit(
            "SessionStart",
            {
                "start_reason": start_reason,
                "prompt": prompt,
                "model": model or "",
                "session_id": session_id,
            },
        )

    def session_end(
        self,
        *,
        status: str,
        reason: str,
        session_id: str | None = None,
        duration_ms: int | None = None,
    ) -> HookDecision:
        return self.emit(
            "SessionEnd",
            {
                "status": status,
                "reason": reason,
                "session_id": session_id,
                "duration_ms": duration_ms,
            },
        )

    def user_prompt_submit(self, prompt: str, *, source: str = "cli", session_id: str | None = None) -> HookDecision:
        return self.emit(
            "UserPromptSubmit",
            {
                "prompt": prompt,
                "source": source,
                "session_id": session_id,
                "chars": len(prompt),
            },
        )

    def instructions_loaded(
        self,
        *,
        reason: str,
        source: str,
        chars: int = 0,
        path: str = "",
    ) -> HookDecision:
        return self.emit(
            "InstructionsLoaded",
            {
                "reason": reason,
                "source": source,
                "chars": chars,
                "path": path,
            },
        )

    def permission_request(
        self,
        *,
        name: str,
        action: str,
        risk: str,
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
        subagent: str | None = None,
    ) -> HookDecision:
        return self.emit(
            "PermissionRequest",
            {
                "name": name,
                "action": action,
                "risk": risk,
                "input": tool_input or {},
                "session_id": session_id,
                "subagent": subagent,
            },
        )

    def permission_denied(
        self,
        *,
        name: str,
        action: str,
        risk: str,
        reason: str,
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
        subagent: str | None = None,
    ) -> HookDecision:
        return self.emit(
            "PermissionDenied",
            {
                "name": name,
                "action": action,
                "risk": risk,
                "reason": reason,
                "input": tool_input or {},
                "session_id": session_id,
                "subagent": subagent,
            },
        )

    def post_tool_use_failure(self, name: str, tool_input: dict[str, Any], *, error: str, content: str = "") -> HookDecision:
        return self.emit(
            "PostToolUseFailure",
            {
                "name": name,
                "input": tool_input,
                "error": error,
                "content_preview": content[:800],
            },
        )

    def post_tool_batch(self, *, tools: list[str], failed_count: int, session_id: str | None = None, turn: int | None = None) -> HookDecision:
        return self.emit(
            "PostToolBatch",
            {
                "count": len(tools),
                "failed_count": failed_count,
                "tools": tools,
                "session_id": session_id,
                "turn": turn,
            },
        )

    def task_created(self, *, task_id: str, content: str, status: str = "pending", source: str = "agent") -> HookDecision:
        return self.emit("TaskCreated", {"task_id": task_id, "content": content, "status": status, "source": source})

    def task_completed(self, *, task_id: str, status: str = "completed", content: str = "", result: str = "") -> HookDecision:
        return self.emit("TaskCompleted", {"task_id": task_id, "status": status, "content": content, "result": result})

    def pre_compact(self, *, trigger: str, token_budget: int, estimated_tokens: int, source_count: int = 0) -> HookDecision:
        return self.emit(
            "PreCompact",
            {
                "trigger": trigger,
                "token_budget": token_budget,
                "estimated_tokens": estimated_tokens,
                "source_count": source_count,
            },
        )

    def post_compact(
        self,
        *,
        trigger: str,
        token_budget: int,
        estimated_tokens: int,
        compressed_sections: list[str],
        summary_chars: int = 0,
    ) -> HookDecision:
        return self.emit(
            "PostCompact",
            {
                "trigger": trigger,
                "token_budget": token_budget,
                "estimated_tokens": estimated_tokens,
                "compressed_sections": compressed_sections,
                "summary_chars": summary_chars,
            },
        )

    def file_changed(
        self,
        *,
        path: str,
        operation: str,
        tool: str = "",
        chars: int = 0,
        session_id: str | None = None,
    ) -> HookDecision:
        return self.emit(
            "FileChanged",
            {
                "path": path,
                "operation": operation,
                "tool": tool,
                "chars": chars,
                "session_id": session_id,
            },
        )

    def worktree_create(self, *, path: str, branch: str = "", source: str = "") -> HookDecision:
        return self.emit("WorktreeCreate", {"path": path, "branch": branch, "source": source})

    def worktree_remove(self, *, path: str, branch: str = "", source: str = "") -> HookDecision:
        return self.emit("WorktreeRemove", {"path": path, "branch": branch, "source": source})

    def config_change(
        self,
        *,
        source: str,
        path: str,
        operation: str = "loaded",
        keys: list[str] | None = None,
    ) -> HookDecision:
        return self.emit(
            "ConfigChange",
            {
                "source": source,
                "path": path,
                "operation": operation,
                "keys": keys or [],
            },
        )

    def _write(self, event: HookEvent) -> None:
        if not self.enabled or self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": event.ts, "event": event.name, "payload": event.payload}
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            return


def matcher_matches(matcher: str | None, value: str) -> bool:
    if matcher is None or matcher == "" or matcher == "*":
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        return value in matcher.split("|")
    try:
        return re.search(matcher, value) is not None
    except re.error:
        return False


def event_json(event: HookEvent, additional_context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(event.payload)
    data = {
        "hook_event_name": event.name,
        "schema_version": HOOK_SCHEMA_VERSION,
        "timestamp": event.ts,
        **payload,
    }
    if additional_context:
        data["additionalContext"] = additional_context
    if "name" in payload:
        data.setdefault("tool_name", payload["name"])
    if "input" in payload:
        data.setdefault("tool_input", payload["input"])
    return data


def normalize_hook_payload(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    errors = validate_hook_payload(event_name, normalized)
    if errors:
        normalized["_payload_errors"] = errors
    return normalized


def validate_hook_payload(event_name: str, payload: dict[str, Any]) -> list[str]:
    spec = HOOK_EVENT_SPECS.get(event_name)
    if spec is None:
        return [f"unknown hook event: {event_name}"]
    errors: list[str] = []
    for field_name in spec.required:
        if field_name not in payload:
            errors.append(f"missing required field: {field_name}")
    return errors


def hook_event_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "required": list(spec.required),
            "optional": list(spec.optional),
            "matcher_field": spec.matcher_field,
            "description": spec.description,
        }
        for spec in sorted(HOOK_EVENT_SPECS.values(), key=lambda item: item.name)
    ]


def decision_from_stdout(stdout: str) -> HookDecision | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return HookDecision(False, f"invalid hook decision JSON: {exc}")
    return decision_from_mapping(payload)


def decision_from_mapping(payload: Any) -> HookDecision | None:
    if isinstance(payload, HookDecision):
        return payload
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return HookDecision(False, f"invalid hook decision payload: {type(payload).__name__}")
    schema_errors = validate_hook_decision_payload(payload)
    if schema_errors:
        return HookDecision(False, "invalid hook decision schema: " + "; ".join(schema_errors))
    decision = payload.get("decision")
    if decision == "block" or payload.get("allow") is False:
        return HookDecision(False, str(payload.get("reason", "configured hook blocked")))
    updates = payload.get("payload_updates") or payload.get("tool_input_updates") or {}
    return HookDecision(True, str(payload.get("reason", "")), updates)


def validate_hook_decision_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    decision = payload.get("decision")
    if decision is not None and decision not in {"allow", "block"}:
        errors.append("decision must be 'allow' or 'block'")
    if "allow" in payload and not isinstance(payload["allow"], bool):
        errors.append("allow must be boolean")
    if "reason" in payload and not isinstance(payload["reason"], str):
        errors.append("reason must be string")
    for key in ("payload_updates", "tool_input_updates"):
        if key in payload and not isinstance(payload[key], dict):
            errors.append(f"{key} must be object")
    return errors


def render_hook_template(template: str, payload: dict[str, Any]) -> str:
    flattened = flatten_for_template(payload)

    class SafeDict(dict[str, Any]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(SafeDict(flattened))
    except (KeyError, ValueError):
        return template


def flatten_for_template(payload: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flattened[str(key)] = "" if value is None else value
        else:
            flattened[str(key)] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if "tool_name" not in flattened and "name" in flattened:
        flattened["tool_name"] = flattened["name"]
    if "tool_input" not in flattened and "input" in flattened:
        flattened["tool_input"] = flattened["input"]
    return flattened


def load_configured_hooks(runtime: HookRuntime, workspace: Path) -> list[Path]:
    loaded: list[Path] = []
    for path in [
        workspace / ".claude" / "settings.json",
        workspace / ".mini_cc" / "settings.json",
        workspace / ".mini_cc" / "settings.local.json",
    ]:
        if load_hooks_file(runtime, path):
            loaded.append(path)
    return loaded


def load_hooks_file(runtime: HookRuntime, path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("disableAllHooks") is True:
        return True
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event_name, matcher_groups in hooks.items():
        if not isinstance(matcher_groups, list):
            continue
        for group in matcher_groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher", ""))
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict):
                    runtime.register_configured(
                        ConfiguredHook(
                            event_name=str(event_name),
                            matcher=matcher,
                            handler=dict(handler),
                            source=str(path),
                        )
                    )
    runtime.config_change(
        source="hooks",
        path=str(path),
        operation="loaded",
        keys=sorted(str(key) for key in hooks.keys()),
    )
    return True
