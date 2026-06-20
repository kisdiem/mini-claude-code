from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .runtime_types import (
    EvidenceRecord,
    FileChange,
    FinalDecision,
    GateDecision,
    PlanRecord,
    RuntimeEventKind,
    VerificationResult,
)


class EvidenceLedger:
    """Append-only audit ledger for one runtime run.

    The ledger records facts only. Policy decisions stay in TaskRuntime,
    TaskStateMachine, semantic gates, and VerificationPolicy.
    """

    def __init__(self, *, run_id: str | None = None) -> None:
        self.run_id = run_id or f"run_{uuid4().hex[:12]}"
        self.records: list[EvidenceRecord] = []
        self._counter = 0

    def start_run(self, prompt: str) -> EvidenceRecord:
        return self.add_event(RuntimeEventKind.TASK_START, "task_runtime", "task started", metadata={"prompt": prompt})

    def add(self, record: EvidenceRecord) -> EvidenceRecord:
        self._counter += 1
        if not record.id:
            record.id = f"ev_{self._counter:06d}"
        if not record.timestamp:
            record.timestamp = _now()
        if record.run_id is None:
            record.run_id = self.run_id
        if not record.severity:
            record.severity = "error" if record.is_error else "info"
        self.records.append(record)
        return record

    def extend(self, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
        return [self.add(record) for record in records]

    def add_event(
        self,
        kind: str,
        source: str,
        summary: str,
        *,
        phase: str | None = None,
        paths: list[str] | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        is_error: bool = False,
        severity: str | None = None,
        metadata: dict[str, Any] | None = None,
        turn: int | None = None,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
    ) -> EvidenceRecord:
        return self.add(
            EvidenceRecord(
                kind=kind,
                source=source,
                summary=summary,
                phase=phase,
                paths=list(paths or []),
                command=command,
                exit_code=exit_code,
                is_error=is_error,
                metadata=dict(metadata or {}),
                turn=turn,
                tool_call_id=tool_call_id,
                parent_id=parent_id,
                severity=severity or ("error" if is_error else "info"),
            )
        )

    def record_tool_call(
        self,
        name: str,
        tool_input: dict[str, Any],
        phase: str | None,
        *,
        turn: int | None = None,
        tool_call_id: str | None = None,
    ) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.TOOL_CALL,
            name,
            f"tool called: {name}",
            phase=phase,
            paths=_paths_from_input(tool_input),
            command=str(tool_input.get("command")) if "command" in tool_input else None,
            turn=turn,
            tool_call_id=tool_call_id,
            metadata={"input_keys": sorted(str(key) for key in tool_input.keys())},
        )

    def record_tool_result(
        self,
        name: str,
        result: Any,
        phase: str | None,
        *,
        parent_id: str | None = None,
        turn: int | None = None,
    ) -> EvidenceRecord:
        content = str(getattr(result, "content", ""))
        return self.add_event(
            RuntimeEventKind.TOOL_RESULT,
            name,
            f"tool result: {name}",
            phase=phase,
            is_error=bool(getattr(result, "is_error", False)),
            severity="error" if bool(getattr(result, "is_error", False)) else "info",
            parent_id=parent_id,
            turn=turn,
            metadata={"content_excerpt": content[:1000]},
        )

    def record_state_transition(self, from_phase: str | None, to_phase: str | None, reason: str) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.STATE_TRANSITION,
            "task_state",
            reason,
            phase=to_phase,
            metadata={"from_phase": from_phase, "to_phase": to_phase},
        )

    def record_plan_declared(self, plan_record: PlanRecord, phase: str | None = None) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.PLAN_DECLARED,
            "assistant",
            "plan declared",
            phase=phase,
            paths=plan_record.planned_files,
            command=plan_record.verification_command or None,
            is_error=bool(plan_record.blockers),
            severity="error" if plan_record.blockers else ("warning" if plan_record.warnings else "info"),
            metadata=plan_record.to_json(),
        )

    def record_file_read(self, path: str, phase: str | None = None) -> EvidenceRecord:
        return self.add_event(RuntimeEventKind.FILE_READ, "read_file", f"file read: {path}", phase=phase, paths=[path])

    def record_file_modified(self, file_change: FileChange, phase: str | None = None) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.FILE_MODIFIED,
            file_change.tool_name or "task_runtime",
            f"file {file_change.operation}: {file_change.path}",
            phase=phase,
            paths=[file_change.path],
            metadata=file_change.to_json(),
        )

    def record_semantic_decision(self, source: str, decision: Any, phase: str | None = None) -> EvidenceRecord:
        allow = bool(getattr(decision, "allow", True))
        metadata = decision.to_json() if hasattr(decision, "to_json") else dict(getattr(decision, "__dict__", {}))
        return self.add_event(
            RuntimeEventKind.SEMANTIC_DECISION,
            source,
            str(getattr(decision, "reason", "semantic decision")),
            phase=phase,
            is_error=not allow,
            severity="error" if not allow else ("warning" if getattr(decision, "warnings", []) else "info"),
            metadata=metadata,
        )

    def record_verification_result(self, result: VerificationResult, phase: str | None = None) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.VERIFICATION_RESULT,
            "verification_policy",
            "verification passed" if result.passed else "verification failed or blocked",
            phase=phase,
            command=result.command,
            exit_code=result.exit_code,
            is_error=not result.passed,
            severity="info" if result.passed else "error",
            metadata=result.to_json(),
        )

    def record_final_decision(self, final_decision: FinalDecision) -> EvidenceRecord:
        return self.add_event(
            RuntimeEventKind.FINAL_DECISION,
            "task_runtime",
            final_decision.reason or final_decision.status,
            is_error=not final_decision.allow_final or final_decision.status not in {"passed", "not_required"},
            severity="info" if final_decision.allow_final and not final_decision.blockers else "error",
            metadata=final_decision.to_json(),
        )

    def warnings(self) -> list[str]:
        values: list[str] = []
        for record in self.records:
            if record.severity == "warning":
                values.append(record.summary)
            values.extend(str(item) for item in record.metadata.get("warnings", []) if item)
        return _unique(values)

    def blockers(self) -> list[str]:
        values: list[str] = []
        for record in self.records:
            if record.severity == "error" or record.is_error:
                values.append(record.summary)
            values.extend(str(item) for item in record.metadata.get("blockers", []) if item)
        return _unique(values)

    def modified_files(self) -> list[str]:
        files: list[str] = []
        for record in self.records:
            if record.kind == RuntimeEventKind.FILE_MODIFIED:
                files.extend(record.paths)
        return _unique(files)

    def verification_results(self) -> list[dict[str, Any]]:
        return [
            dict(record.metadata)
            for record in self.records
            if record.kind == RuntimeEventKind.VERIFICATION_RESULT
        ]

    def latest(self, kind: str) -> EvidenceRecord | None:
        for record in reversed(self.records):
            if record.kind == kind:
                return record
        return None

    def to_json(self) -> list[dict[str, Any]]:
        return [record.to_json() for record in self.records]

    def to_report_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evidence": self.to_json(),
            "evidence_summary": {
                "total_events": len(self.records),
                "warnings": self.warnings(),
                "blockers": self.blockers(),
                "modified_files": self.modified_files(),
                "verification_results": self.verification_results(),
            },
        }


def record_task_start(prompt: str) -> EvidenceRecord:
    return EvidenceRecord(RuntimeEventKind.TASK_START, "task_runtime", "task started", metadata={"prompt": prompt})


def record_tool_call(name: str, tool_input: dict[str, Any], phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        RuntimeEventKind.TOOL_CALL,
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
        RuntimeEventKind.TOOL_RESULT,
        name,
        f"tool result: {name}",
        phase=phase,
        is_error=bool(getattr(result, "is_error", False)),
        severity="error" if bool(getattr(result, "is_error", False)) else "info",
        metadata={"content_excerpt": content[:1000]},
    )


def record_file_modified(paths: list[str], phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(RuntimeEventKind.FILE_MODIFIED, "task_runtime", "files modified", phase=phase, paths=list(paths))


def record_verification_result(result: VerificationResult, phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        RuntimeEventKind.VERIFICATION_RESULT,
        "verification_policy",
        "verification passed" if result.passed else "verification failed or blocked",
        phase=phase,
        command=result.command,
        exit_code=result.exit_code,
        is_error=not result.passed,
        severity="info" if result.passed else "error",
        metadata=result.to_json(),
    )


def record_gate_decision(source: str, decision: GateDecision, phase: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        RuntimeEventKind.SEMANTIC_DECISION,
        source,
        decision.reason,
        phase=phase,
        is_error=not decision.allow,
        severity="error" if not decision.allow else ("warning" if decision.warnings else "info"),
        metadata=decision.to_json(),
    )


def record_final_decision(status: str, reason: str = "") -> EvidenceRecord:
    return EvidenceRecord(RuntimeEventKind.FINAL_DECISION, "task_runtime", reason or status, metadata={"status": status})


def _paths_from_input(tool_input: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("path", "file", "target"):
        value = tool_input.get(key)
        if value:
            paths.append(str(value).replace("\\", "/"))
    return paths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        if item and item not in values:
            values.append(item)
    return values
