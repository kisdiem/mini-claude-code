from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .evidence import EvidenceLedger
from .runtime_types import FinalDecision
from .verification import discover_verification_candidates


def build_evidence_report(
    *,
    workspace: Any,
    task_prompt: str,
    task_state: Any,
    coding_state: Any,
    evidence_ledger: EvidenceLedger,
    final_decision: FinalDecision,
    tools_called: list[dict[str, Any]],
) -> dict[str, Any]:
    modified_files = _unique(
        [
            *(getattr(task_state, "modified_files", []) if task_state is not None else []),
            *(getattr(coding_state, "modified_files", []) if coding_state is not None else []),
            *evidence_ledger.modified_files(),
        ]
    )
    verification_commands = _verification_commands(task_state, coding_state)
    verification_results = _verification_results(task_state, coding_state, evidence_ledger)
    warnings = _unique(
        [
            *(getattr(task_state, "semantic_warnings", []) if task_state is not None else []),
            *final_decision.warnings,
        ]
    )
    blockers = _unique(
        [
            *(getattr(task_state, "semantic_blockers", []) if task_state is not None else []),
            *final_decision.blockers,
        ]
    )
    payload: dict[str, Any] = {
        "schema_version": "1.1",
        "status": final_decision.status,
        "final_status": final_decision.status,
        "task_prompt": task_prompt,
        "coding_loop_enabled": coding_state is not None and bool(getattr(coding_state, "enabled", False)),
        "process_checks": _process_checks(task_state),
        "semantic_checks": dict(getattr(task_state, "semantic_checks", {}) if task_state is not None else {}),
        "coding_loop_state": coding_state.to_json() if coding_state is not None else {},
        "task_state": task_state.to_json() if task_state is not None else {},
        "task_contract": dict(getattr(task_state, "task_contract", {}) if task_state is not None else {}),
        "verification_commands": verification_commands,
        "verification_results": verification_results,
        "modified_files": modified_files,
        "planned_files": list(getattr(task_state, "planned_files", []) if task_state is not None else []),
        "tools_called": list(tools_called),
        "last_failure_summary": _last_failure_summary(task_state, coding_state) or final_decision.unresolved,
        "blockers": blockers,
        "warnings": warnings,
        "semantic_warnings": warnings,
        "semantic_blockers": blockers,
        "final_decision": final_decision.to_json(),
        "verification_candidates": [candidate.to_json() for candidate in discover_verification_candidates(workspace, modified_files)[:5]],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **evidence_ledger.to_report_payload(),
    }
    if coding_state is not None:
        payload.update(coding_state.to_json())
        payload["coding_loop_enabled"] = True
        payload["status"] = final_decision.status
        payload["final_status"] = final_decision.status
        payload["schema_version"] = "1.1"
        payload["verification_results"] = verification_results
        payload["semantic_warnings"] = warnings
        payload["semantic_blockers"] = blockers
        payload["final_decision"] = final_decision.to_json()
        payload.update(evidence_ledger.to_report_payload())
    return payload


def _process_checks(task_state: Any) -> dict[str, bool]:
    if task_state is None:
        return {}
    return {
        "explored": bool(task_state.explored),
        "localized": bool(task_state.localized),
        "planned": bool(task_state.planned),
        "edited": bool(task_state.edited),
        "verified": bool(task_state.verified),
    }


def _verification_commands(task_state: Any, coding_state: Any) -> list[Any]:
    if coding_state is not None and coding_state.verification_commands:
        return [command.to_json() for command in coding_state.verification_commands]
    if task_state is not None:
        return list(getattr(task_state, "verification_commands", []))
    return []


def _verification_results(task_state: Any, coding_state: Any, evidence_ledger: EvidenceLedger) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if task_state is not None:
        for item in getattr(task_state, "verification_evidence", []) or []:
            if isinstance(item, dict):
                results.append(dict(item))
    results.extend(evidence_ledger.verification_results())
    if coding_state is not None:
        for command in getattr(coding_state, "verification_commands", []) or []:
            payload = command.to_json()
            if payload not in results:
                results.append(payload)
    return results


def _last_failure_summary(task_state: Any, coding_state: Any) -> str:
    if coding_state is not None and coding_state.last_failure_summary:
        return str(coding_state.last_failure_summary)
    if task_state is not None and getattr(task_state, "last_failure_summary", ""):
        return str(task_state.last_failure_summary)
    return ""


def _unique(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        if item and item not in values:
            values.append(item)
    return values
