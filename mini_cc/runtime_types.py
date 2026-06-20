from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RuntimeEventKind:
    TASK_START = "task_start"
    ASSISTANT_TEXT = "assistant_text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_DECISION = "permission_decision"
    STATE_TRANSITION = "state_transition"
    PLAN_DECLARED = "plan_declared"
    FILE_READ = "file_read"
    FILE_MODIFIED = "file_modified"
    SEMANTIC_DECISION = "semantic_decision"
    VERIFICATION_COMMAND = "verification_command"
    VERIFICATION_RESULT = "verification_result"
    REPAIR_REQUIRED = "repair_required"
    FINAL_DECISION = "final_decision"
    ARTIFACT_WRITTEN = "artifact_written"


class RuntimePhase:
    INTAKE = "INTAKE"
    EXPLORE = "EXPLORE"
    LOCALIZE = "LOCALIZE"
    PLAN = "PLAN"
    EDIT = "EDIT"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    FINAL = "FINAL"


class RunStatus:
    NOT_REQUIRED = "not_required"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SEMANTIC_BLOCKED = "semantic_blocked"
    VERIFICATION_REQUIRED = "verification_required"
    REPAIR_REQUIRED = "repair_required"
    MAX_TURNS = "max_turns"
    MAX_TURNS_REACHED = "max_turns_reached"
    MAX_ATTEMPTS_REACHED = "max_attempts_reached"


@dataclass
class EvidenceRecord:
    kind: str
    source: str
    summary: str
    phase: str | None = None
    paths: list[str] = field(default_factory=list)
    command: str | None = None
    exit_code: int | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    timestamp: str = ""
    run_id: str | None = None
    turn: int | None = None
    tool_call_id: str | None = None
    parent_id: str | None = None
    severity: str = "info"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateDecision:
    allow: bool
    reason: str
    instruction: str = ""
    next_phase: str | None = None
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [record.to_json() for record in self.evidence]
        return payload


@dataclass
class VerificationResult:
    command: str
    command_type: str
    exit_code: int | None
    passed: bool
    is_real_verification: bool
    is_relevant: bool
    has_meaningful_checks: bool
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    failure_summary: str = ""
    coverage: str = "unknown"
    confidence: float = 0.0
    meaningful_reason: str = ""
    relevance_reason: str = ""
    parser_name: str = "generic"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileChange:
    path: str
    operation: str = "unknown"
    tool_name: str = ""
    added_lines: int = 0
    deleted_lines: int = 0
    summary: str = ""
    is_dry_run: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanRecord:
    planned_files: list[str] = field(default_factory=list)
    verification_command: str = ""
    raw_text: str = ""
    is_valid: bool = False
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalDecision:
    status: str
    allow_final: bool
    reason: str = ""
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    unresolved: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
