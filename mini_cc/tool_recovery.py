from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


RECOVERABLE_RETRY_CATEGORIES = {
    "timeout",
    "transient_network",
    "mcp_server_failure",
}

NO_RETRY_CATEGORIES = {
    "permission_denied",
    "hook_blocked",
    "parameter_error",
    "not_found",
    "path_escape",
    "unknown_tool",
}


@dataclass(frozen=True)
class ToolFailure:
    category: str
    reason: str
    retryable: bool = False
    alternative_allowed: bool = False
    degraded_allowed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "reason": self.reason,
            "retryable": self.retryable,
            "alternative_allowed": self.alternative_allowed,
            "degraded_allowed": self.degraded_allowed,
        }


@dataclass(frozen=True)
class ToolRecoveryPolicy:
    enabled: bool = True
    max_retries: int = 1
    backoff_seconds: float = 0.0
    alternative_tools: dict[str, list[str]] = field(default_factory=dict)
    enable_degraded_mode: bool = True

    @classmethod
    def default(cls) -> "ToolRecoveryPolicy":
        return cls(
            max_retries=1,
            backoff_seconds=0.0,
            alternative_tools={
                "read_file": ["list_files"],
                "search_text": ["list_files"],
                "replace_text": ["read_file"],
                "run_shell": ["list_files"],
            },
        )


def classify_tool_failure(name: str, tool_input: dict[str, Any], result: ToolResult) -> ToolFailure:
    del tool_input
    text = result.content.lower()
    if not result.is_error:
        return ToolFailure("none", "tool succeeded")
    if "path escapes workspace" in text:
        return ToolFailure("path_escape", "tool input tried to escape the workspace")
    if "unknown tool" in text:
        return ToolFailure("unknown_tool", "tool name is not registered")
    if "hook denied" in text or "hook blocked" in text or "blocked by hook" in text:
        return ToolFailure("hook_blocked", "hook policy blocked the tool")
    if "permission denied" in text or "read-only" in text or "plan-scoped" in text or "user denied" in text:
        return ToolFailure("permission_denied", "permission policy denied the tool")
    if "oauth" in text or "invalid_token" in text or "expired token" in text or "http 401" in text or "http 403" in text:
        return ToolFailure("mcp_auth_failure", "MCP authentication failed", degraded_allowed=True)
    if "timed out" in text or "timeout" in text:
        return ToolFailure("timeout", "tool timed out", retryable=True, degraded_allowed=True)
    if any(marker in text for marker in ["connection reset", "connection refused", "temporarily unavailable", "http 429", "http 500", "http 502", "http 503", "http 504"]):
        return ToolFailure("transient_network", "tool failed with a transient network/server error", retryable=True, degraded_allowed=True)
    if name.startswith("mcp__") and any(marker in text for marker in ["server", "json-rpc", "request failed", "no json response"]):
        return ToolFailure("mcp_server_failure", "MCP server failed", retryable=True, degraded_allowed=True)
    if "old text was not found" in text or "expected " in text or "invalid regex" in text or "missing " in text:
        return ToolFailure("parameter_error", "tool parameters did not match the target state", alternative_allowed=True)
    if "does not exist" in text or "not found" in text or "not a file" in text or "not a directory" in text:
        return ToolFailure("not_found", "target path or tool target was not found", alternative_allowed=True)
    return ToolFailure("unknown", "tool failed without a recognized recovery category", degraded_allowed=True)


def recover_tool_failure(
    *,
    name: str,
    tool_input: dict[str, Any],
    initial_result: ToolResult,
    execute: Callable[[str, dict[str, Any]], ToolResult],
    policy: ToolRecoveryPolicy,
) -> ToolResult:
    failure = classify_tool_failure(name, tool_input, initial_result)
    trace: list[dict[str, Any]] = [
        trace_entry(
            action="classify",
            tool=name,
            tool_input=tool_input,
            result=initial_result,
            failure=failure,
            attempt=0,
        )
    ]
    if not policy.enabled or not initial_result.is_error:
        return with_recovery_metadata(initial_result, failure, trace, verified=not initial_result.is_error)
    if failure.category in NO_RETRY_CATEGORIES and not failure.alternative_allowed:
        return with_recovery_metadata(initial_result, failure, trace, verified=failure.category in {"permission_denied", "hook_blocked"})

    current = initial_result
    if failure.retryable:
        for attempt in range(1, max(0, policy.max_retries) + 1):
            if policy.backoff_seconds > 0:
                time.sleep(policy.backoff_seconds * attempt)
            current = execute(name, dict(tool_input))
            trace.append(
                trace_entry(
                    action="retry",
                    tool=name,
                    tool_input=tool_input,
                    result=current,
                    failure=classify_tool_failure(name, tool_input, current),
                    attempt=attempt,
                )
            )
            if not current.is_error:
                return recovered_result(
                    current,
                    failure,
                    trace,
                    verifier_reason="retry succeeded",
                    recovered_by="retry",
                )

    alternatives = policy.alternative_tools.get(name, []) if failure.alternative_allowed else []
    for alternative in alternatives:
        alternative_input = build_alternative_input(name, alternative, tool_input)
        if alternative_input is None:
            continue
        alternative_result = execute(alternative, alternative_input)
        trace.append(
            trace_entry(
                action="alternative",
                tool=alternative,
                tool_input=alternative_input,
                result=alternative_result,
                failure=classify_tool_failure(alternative, alternative_input, alternative_result),
                attempt=len(trace),
            )
        )
        if not alternative_result.is_error:
            return recovered_result(
                alternative_result,
                failure,
                trace,
                verifier_reason=f"alternative tool {alternative} succeeded",
                recovered_by="alternative",
            )

    if policy.enable_degraded_mode and failure.degraded_allowed:
        degraded = make_tool_result(
            initial_result,
            "[degraded mode]\n"
            + "The original tool failed and no retry or alternative fully recovered it.\n"
            + initial_result.content,
            True,
            dict(initial_result.metadata),
        )
        trace.append(
            {
                "action": "degraded_mode",
                "tool": name,
                "input": dict(tool_input),
                "is_error": True,
                "content_preview": degraded.content[:400],
                "failure": failure.to_json(),
            }
        )
        return with_recovery_metadata(degraded, failure, trace, verified=True, degraded=True)

    return with_recovery_metadata(current, failure, trace, verified=False)


def build_alternative_input(original: str, alternative: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    path = str(tool_input.get("path") or ".")
    if alternative == "list_files":
        if original == "read_file":
            return {"path": ".", "recursive": False, "max_entries": 120}
        parent = "."
        if "/" in path:
            parent = path.rsplit("/", 1)[0] or "."
        elif "\\" in path:
            parent = path.rsplit("\\", 1)[0] or "."
        return {"path": parent, "recursive": False, "max_entries": 120}
    if original == "replace_text" and alternative == "read_file":
        return {"path": path, "start_line": 1, "max_lines": 120}
    if alternative == "search_text":
        stem = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].split(".", 1)[0] or path
        return {"pattern": stem, "path": ".", "max_matches": 20}
    return None


def recovered_result(
    result: ToolResult,
    failure: ToolFailure,
    trace: list[dict[str, Any]],
    *,
    verifier_reason: str,
    recovered_by: str,
) -> ToolResult:
    content = (
        f"[recovered from {failure.category} by {recovered_by}]\n"
        + result.content
    )
    recovered = make_tool_result(result, content, False, dict(result.metadata))
    return with_recovery_metadata(recovered, failure, trace, verified=True, recovered_by=recovered_by, verifier_reason=verifier_reason)


def with_recovery_metadata(
    result: ToolResult,
    failure: ToolFailure,
    trace: list[dict[str, Any]],
    *,
    verified: bool,
    recovered_by: str = "",
    verifier_reason: str = "",
    degraded: bool = False,
) -> ToolResult:
    metadata = dict(result.metadata)
    metadata["recovery"] = {
        "schema_version": "2.9",
        "failure": failure.to_json(),
        "recovered": bool(recovered_by),
        "recovered_by": recovered_by,
        "degraded": degraded,
        "trace": trace,
        "post_failure_verifier": {
            "passed": bool(verified),
            "reason": verifier_reason or default_verifier_reason(result, failure, verified, degraded),
        },
    }
    return make_tool_result(result, result.content, result.is_error, metadata)


def default_verifier_reason(result: ToolResult, failure: ToolFailure, verified: bool, degraded: bool) -> str:
    if verified and failure.category in {"permission_denied", "hook_blocked"}:
        return "safe block respected"
    if verified and degraded:
        return "degraded mode recorded original failure without claiming success"
    if verified and not result.is_error:
        return "recovery result is non-error"
    return "tool failure remains unresolved"


def trace_entry(
    *,
    action: str,
    tool: str,
    tool_input: dict[str, Any],
    result: ToolResult,
    failure: ToolFailure,
    attempt: int,
) -> dict[str, Any]:
    return {
        "action": action,
        "attempt": attempt,
        "tool": tool,
        "input": dict(tool_input),
        "is_error": result.is_error,
        "content_preview": result.content[:400],
        "failure": failure.to_json(),
    }


def make_tool_result(example: Any, content: str, is_error: bool, metadata: dict[str, Any]) -> Any:
    return example.__class__(content, is_error=is_error, metadata=metadata)
