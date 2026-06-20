from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
