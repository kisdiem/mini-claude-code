from __future__ import annotations

from typing import Any

from .runtime_types import EvidenceRecord, GateDecision, VerificationResult


class EvidenceLedger:
    """Append-only record of what happened during one task run."""

    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []

    def add(self, record: EvidenceRecord) -> None:
        self.records.append(record)

    def extend(self, records: list[EvidenceRecord]) -> None:
        self.records.extend(records)

    def to_json(self) -> list[dict[str, Any]]:
        return [record.to_json() for record in self.records]


def record_task_start(prompt: str) -> EvidenceRecord:
    return EvidenceRecord("task_start", "task_runtime", "task started", metadata={"prompt": prompt})


def record_tool_call(name: str, tool_input: dict[str, Any], phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        "tool_call",
        name,
        f"tool called: {name}",
        phase=phase,
        paths=_paths_from_input(tool_input),
        command=str(tool_input.get("command")) if "command" in tool_input else None,
        metadata={"input_keys": sorted(str(key) for key in tool_input.keys())},
    )


def record_tool_result(name: str, result: Any, phase: str | None = None) -> EvidenceRecord:
    content = str(getattr(result, "content", ""))
    return EvidenceRecord(
        "tool_result",
        name,
        f"tool result: {name}",
        phase=phase,
        is_error=bool(getattr(result, "is_error", False)),
        metadata={"content_excerpt": content[:1000]},
    )


def record_file_modified(paths: list[str], phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord("file_modified", "task_runtime", "files modified", phase=phase, paths=list(paths))


def record_verification_result(result: VerificationResult, phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        "verification_result",
        "verification_policy",
        "verification passed" if result.passed else "verification failed or blocked",
        phase=phase,
        command=result.command,
        exit_code=result.exit_code,
        is_error=not result.passed,
        metadata=result.to_json(),
    )


def record_gate_decision(source: str, decision: GateDecision, phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        "gate_decision",
        source,
        decision.reason,
        phase=phase,
        is_error=not decision.allow,
        metadata=decision.to_json(),
    )


def record_final_decision(status: str, reason: str = "") -> EvidenceRecord:
    return EvidenceRecord("final_decision", "task_runtime", reason or status, metadata={"status": status})


def _paths_from_input(tool_input: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("path", "file", "target"):
        value = tool_input.get(key)
        if value:
            paths.append(str(value).replace("\\", "/"))
    return paths
