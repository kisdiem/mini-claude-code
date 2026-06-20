from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coding_loop import CodingLoopDecision, CodingLoopPolicy
from .evidence import (
    EvidenceLedger,
    record_file_modified,
    record_final_decision,
    record_gate_decision,
    record_task_start,
    record_tool_call,
    record_tool_result,
    record_verification_result,
)
from .runtime_types import GateDecision
from .task_state import PhaseDecision, TaskStateMachine
from .tools import ToolResult
from .verification import discover_verification_candidates
from .verification_policy import VerificationPolicy


@dataclass(frozen=True)
class TaskRuntimeDecision:
    allow: bool
    instruction: str = ""
    reason: str = ""
    status: str = "not_required"
    source: str = "task_runtime"
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskRuntime:
    """Compatibility layer that coordinates task process and verification gates."""

    def __init__(
        self,
        workspace: Path,
        *,
        task_state_machine: TaskStateMachine | None = None,
        coding_loop: CodingLoopPolicy | None = None,
        enabled: bool = True,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.enabled = enabled
        self.task_state_machine = task_state_machine
        self.coding_loop = coding_loop
        self.task_prompt = ""
        self.tools_called: list[dict[str, Any]] = []
        self.evidence = EvidenceLedger()
        self.verification_policy = VerificationPolicy()

    def start(self, prompt: str) -> None:
        if not self.enabled:
            return
        self.task_prompt = prompt
        self.tools_called = []
        self.evidence = EvidenceLedger()
        self.evidence.add(record_task_start(prompt))
        if self.task_state_machine is not None:
            self.task_state_machine.start(prompt)
        if self.coding_loop is not None:
            self.coding_loop.start(prompt)

    def observe_assistant_text(self, text: str) -> None:
        if not self.enabled:
            return
        if self.task_state_machine is not None:
            self.task_state_machine.observe_assistant_text(text)

    def before_tool(self, name: str, tool_input: dict[str, Any]) -> TaskRuntimeDecision:
        if not self.enabled:
            return TaskRuntimeDecision(True)
        self.evidence.add(record_tool_call(name, tool_input, phase=self._phase_name()))
        if self.task_state_machine is None:
            return TaskRuntimeDecision(True)
        decision = self.task_state_machine.before_tool(name, tool_input)
        self.evidence.add(record_gate_decision("task_state", self._gate_from_phase(decision), phase=self._phase_name()))
        if decision.allow:
            return TaskRuntimeDecision(True, source="task_state", metadata=self._phase_metadata(decision))
        return self._from_phase_decision(decision)

    def observe_tool_result(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        if not self.enabled:
            return
        self.tools_called.append(
            {
                "name": name,
                "is_error": bool(result.is_error),
                "input_keys": sorted(str(key) for key in tool_input.keys()),
            }
        )
        self.evidence.add(record_tool_result(name, result, phase=self._phase_name()))
        if self.task_state_machine is not None:
            self.task_state_machine.observe_tool_result(name, tool_input, result)
        if self.coding_loop is not None:
            self.coding_loop.observe_tool_result(name, tool_input, result)
        modified = self._target_files_for_tool(name, tool_input, result)
        if modified:
            self.evidence.add(record_file_modified(modified, phase=self._phase_name()))
        if name == "run_shell":
            command = str(tool_input.get("command", ""))
            if command and self.verification_policy.is_real_verification(command):
                verification = self.verification_policy.evaluate_command(
                    command,
                    result.content,
                    self._current_modified_files(),
                    self._current_task_contract(),
                    self.workspace,
                )
                self.evidence.add(record_verification_result(verification, phase=self._phase_name()))

    def finish_decision(self) -> TaskRuntimeDecision:
        if not self.enabled:
            return TaskRuntimeDecision(True)
        if self.task_state_machine is not None:
            state_decision = self.task_state_machine.finish_decision()
            self.evidence.add(record_gate_decision("task_state", self._gate_from_phase(state_decision), phase=self._phase_name()))
            if not state_decision.allow:
                return self._from_phase_decision(state_decision)
        if self.coding_loop is not None:
            loop_decision = self.coding_loop.finish_decision()
            self.evidence.add(record_gate_decision("coding_loop", self._gate_from_coding(loop_decision), phase=self._phase_name()))
            if not loop_decision.allow_finish:
                return self._from_coding_decision(loop_decision)
            return self._from_coding_decision(loop_decision)
        return TaskRuntimeDecision(True, status="not_required")

    def final_report(self, status: str) -> str:
        if not self.enabled or self.coding_loop is None:
            return ""
        return self.coding_loop.final_report(status=status)

    def write_artifact(self, status: str) -> Path | None:
        if not self.enabled:
            return None
        target = self.workspace / ".mini_cc" / "task-success" / "last-run.json"
        task_state = self.task_state_machine.state if self.task_state_machine is not None else None
        coding_state = self.coding_loop.state if self.coding_loop is not None else None
        modified_files = self._unique(
            [
                *(getattr(task_state, "modified_files", []) if task_state is not None else []),
                *(getattr(coding_state, "modified_files", []) if coding_state is not None else []),
            ]
        )
        verification_commands = self._verification_commands(task_state, coding_state)
        blockers = self._unique(
            [
                *(getattr(task_state, "semantic_blockers", []) if task_state is not None else []),
                *(["coding loop: " + coding_state.last_failure_summary] if coding_state is not None and coding_state.last_failure_summary else []),
            ]
        )
        warnings = self._unique(getattr(task_state, "semantic_warnings", []) if task_state is not None else [])
        final_status = self._final_status(status, blockers, task_state, coding_state)
        self.evidence.add(record_final_decision(final_status, "runtime final status decided"))
        verification_results = self._verification_results(task_state, coding_state)
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "status": final_status,
            "final_status": final_status,
            "task_prompt": self.task_prompt,
            "coding_loop_enabled": self.coding_loop is not None and bool(getattr(coding_state, "enabled", False)),
            "process_checks": self._process_checks(task_state),
            "semantic_checks": dict(getattr(task_state, "semantic_checks", {}) if task_state is not None else {}),
            "coding_loop_state": coding_state.to_json() if coding_state is not None else {},
            "task_state": task_state.to_json() if task_state is not None else {},
            "task_contract": dict(getattr(task_state, "task_contract", {}) if task_state is not None else {}),
            "verification_commands": verification_commands,
            "verification_results": verification_results,
            "modified_files": modified_files,
            "planned_files": list(getattr(task_state, "planned_files", []) if task_state is not None else []),
            "tools_called": list(self.tools_called),
            "last_failure_summary": self._last_failure_summary(task_state, coding_state),
            "blockers": blockers,
            "warnings": warnings,
            "semantic_warnings": warnings,
            "semantic_blockers": blockers,
            "evidence": self.evidence.to_json(),
            "final_decision": {
                "status": final_status,
                "reason": self._last_failure_summary(task_state, coding_state) or final_status,
                "blockers": blockers,
                "warnings": warnings,
            },
            "verification_candidates": [candidate.to_json() for candidate in discover_verification_candidates(self.workspace, modified_files)[:5]],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if coding_state is not None:
            payload.update(coding_state.to_json())
            payload["coding_loop_enabled"] = True
            payload["status"] = final_status
            payload["final_status"] = final_status
            payload["schema_version"] = "1.0"
            payload["verification_results"] = verification_results
            payload["semantic_warnings"] = warnings
            payload["semantic_blockers"] = blockers
            payload["evidence"] = self.evidence.to_json()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            return None
        return target

    def repair_instruction(self) -> str:
        command = ""
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        if self.coding_loop is not None and self.coding_loop.state.verification_commands:
            last = self.coding_loop.state.verification_commands[-1]
            command = last.command
            exit_code = last.exit_code
            stdout = last.stdout_excerpt
            stderr = last.stderr_excerpt
        elif self.task_state_machine is not None and self.task_state_machine.verification_evidence is not None:
            evidence = self.task_state_machine.verification_evidence
            command = evidence.command
            exit_code = evidence.exit_code
            stdout = evidence.failure_summary
        modified = self._unique(
            [
                *(self.task_state_machine.state.modified_files if self.task_state_machine is not None else []),
                *(self.coding_loop.state.modified_files if self.coding_loop is not None else []),
            ]
        )
        summary = stderr.strip() or stdout.strip() or self._last_failure_summary(
            self.task_state_machine.state if self.task_state_machine is not None else None,
            self.coding_loop.state if self.coding_loop is not None else None,
        )
        return "\n".join(
            [
                "Task phase: REPAIR.",
                "The last verification command failed.",
                f"Failed command: {command or '[unknown]'}",
                f"Exit code: {'n/a' if exit_code is None else exit_code}",
                "Modified files: " + (", ".join(modified) if modified else "[unknown]"),
                "",
                "Last failure summary:",
                summary[:1200] if summary else "[no failure output captured]",
                "",
                "Failure output excerpt:",
                summary[:1200] if summary else "[no failure output captured]",
                "",
                "Next step: read the failure output, identify the smallest relevant cause, make one minimal repair, then rerun the same verification command.",
            ]
        )

    def _from_phase_decision(self, decision: PhaseDecision) -> TaskRuntimeDecision:
        instruction = decision.instruction
        if decision.reason == "repair required":
            instruction = self.repair_instruction()
        return TaskRuntimeDecision(
            allow=decision.allow,
            instruction=instruction,
            reason=decision.reason,
            status="blocked" if not decision.allow else "not_required",
            source="task_state",
            metadata=self._phase_metadata(decision),
        )

    def _from_coding_decision(self, decision: CodingLoopDecision) -> TaskRuntimeDecision:
        instruction = decision.instruction
        if not decision.allow_finish and decision.reason == "last verification command failed":
            instruction = self.repair_instruction()
        return TaskRuntimeDecision(
            allow=decision.allow_finish,
            instruction=instruction,
            reason=decision.reason,
            status=decision.status,
            source="coding_loop",
            metadata={},
        )

    def _phase_metadata(self, decision: PhaseDecision) -> dict[str, Any]:
        return {"next_phase": decision.next_phase.value if decision.next_phase else None}

    def _phase_name(self) -> str | None:
        if self.task_state_machine is None:
            return None
        phase = getattr(self.task_state_machine.state, "phase", None)
        return getattr(phase, "value", str(phase)) if phase is not None else None

    def _gate_from_phase(self, decision: PhaseDecision) -> GateDecision:
        return GateDecision(
            allow=decision.allow,
            reason=decision.reason,
            instruction=decision.instruction,
            next_phase=decision.next_phase.value if decision.next_phase else None,
            blockers=[] if decision.allow else [decision.reason],
        )

    def _gate_from_coding(self, decision: CodingLoopDecision) -> GateDecision:
        return GateDecision(
            allow=decision.allow_finish,
            reason=decision.reason or decision.status,
            instruction=decision.instruction,
            blockers=[] if decision.allow_finish else [decision.reason],
        )

    def _process_checks(self, task_state: Any) -> dict[str, bool]:
        if task_state is None:
            return {}
        return {
            "explored": bool(task_state.explored),
            "localized": bool(task_state.localized),
            "planned": bool(task_state.planned),
            "edited": bool(task_state.edited),
            "verified": bool(task_state.verified),
        }

    def _verification_commands(self, task_state: Any, coding_state: Any) -> list[Any]:
        if coding_state is not None and coding_state.verification_commands:
            return [command.to_json() for command in coding_state.verification_commands]
        if task_state is not None:
            return list(getattr(task_state, "verification_commands", []))
        return []

    def _verification_results(self, task_state: Any, coding_state: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if task_state is not None:
            for item in getattr(task_state, "verification_evidence", []) or []:
                if isinstance(item, dict):
                    results.append(dict(item))
        if coding_state is not None:
            for command in getattr(coding_state, "verification_commands", []) or []:
                results.append(command.to_json())
        return results

    def _last_failure_summary(self, task_state: Any, coding_state: Any) -> str:
        if coding_state is not None and coding_state.last_failure_summary:
            return str(coding_state.last_failure_summary)
        if task_state is not None and task_state.last_failure_summary:
            return str(task_state.last_failure_summary)
        return ""

    def _final_status(self, status: str, blockers: list[str], task_state: Any, coding_state: Any) -> str:
        if status == "max_turns_reached":
            return "max_turns_reached"
        if status == "failed":
            return "failed"
        if blockers and status == "completed":
            return "semantic_blocked"
        if coding_state is not None:
            if coding_state.code_modified and coding_state.last_verification_passed:
                return "passed"
            if coding_state.code_modified and coding_state.last_verification_failed:
                return "failed"
            return str(coding_state.final_status or status)
        if task_state is not None and getattr(task_state, "edited", False):
            return "passed" if getattr(task_state, "verification_passed", False) else "failed"
        return "not_required" if status == "completed" else status

    def _unique(self, items: list[str]) -> list[str]:
        values: list[str] = []
        for item in items:
            if item and item not in values:
                values.append(item)
        return values

    def _target_files_for_tool(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> list[str]:
        if bool(tool_input.get("dry_run")):
            return []
        paths: list[str] = []
        if name in {"write_file", "replace_text"} and tool_input.get("path"):
            paths.append(str(tool_input["path"]).replace("\\", "/"))
        elif name == "apply_patch":
            import re

            match = re.search(r"^changed_files:\s*(?P<files>.+)$", result.content, flags=re.MULTILINE)
            if match:
                paths.extend(path.strip().replace("\\", "/") for path in match.group("files").split(",") if path.strip())
        return self._unique(paths)

    def _current_modified_files(self) -> list[str]:
        task_state = self.task_state_machine.state if self.task_state_machine is not None else None
        coding_state = self.coding_loop.state if self.coding_loop is not None else None
        return self._unique(
            [
                *(getattr(task_state, "modified_files", []) if task_state is not None else []),
                *(getattr(coding_state, "modified_files", []) if coding_state is not None else []),
            ]
        )

    def _current_task_contract(self) -> Any:
        if self.task_state_machine is not None:
            return getattr(self.task_state_machine, "contract", None)
        return None
