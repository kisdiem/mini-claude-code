from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coding_loop import CodingLoopDecision, CodingLoopPolicy, parse_exit_code
from .task_state import PhaseDecision, TaskStateMachine
from .tools import ToolResult
from .verification import discover_verification_candidates


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

    def start(self, prompt: str) -> None:
        if not self.enabled:
            return
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
        if self.task_state_machine is None:
            return TaskRuntimeDecision(True)
        decision = self.task_state_machine.before_tool(name, tool_input)
        if decision.allow:
            return TaskRuntimeDecision(True, source="task_state", metadata=self._phase_metadata(decision))
        return self._from_phase_decision(decision)

    def observe_tool_result(self, name: str, tool_input: dict[str, Any], result: ToolResult) -> None:
        if not self.enabled:
            return
        if self.task_state_machine is not None:
            self.task_state_machine.observe_tool_result(name, tool_input, result)
        if self.coding_loop is not None:
            self.coding_loop.observe_tool_result(name, tool_input, result)

    def finish_decision(self) -> TaskRuntimeDecision:
        if not self.enabled:
            return TaskRuntimeDecision(True)
        if self.task_state_machine is not None:
            state_decision = self.task_state_machine.finish_decision()
            if not state_decision.allow:
                return self._from_phase_decision(state_decision)
        if self.coding_loop is not None:
            loop_decision = self.coding_loop.finish_decision()
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
        payload: dict[str, Any] = {
            "status": final_status,
            "final_status": final_status,
            "coding_loop_enabled": self.coding_loop is not None and bool(getattr(coding_state, "enabled", False)),
            "process_checks": self._process_checks(task_state),
            "semantic_checks": dict(getattr(task_state, "semantic_checks", {}) if task_state is not None else {}),
            "coding_loop_state": coding_state.to_json() if coding_state is not None else {},
            "task_state": task_state.to_json() if task_state is not None else {},
            "task_contract": dict(getattr(task_state, "task_contract", {}) if task_state is not None else {}),
            "verification_commands": verification_commands,
            "modified_files": modified_files,
            "planned_files": list(getattr(task_state, "planned_files", []) if task_state is not None else []),
            "last_failure_summary": self._last_failure_summary(task_state, coding_state),
            "blockers": blockers,
            "warnings": warnings,
            "verification_candidates": [candidate.to_json() for candidate in discover_verification_candidates(self.workspace, modified_files)[:5]],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if coding_state is not None:
            payload.update(coding_state.to_json())
            payload["coding_loop_enabled"] = True
            payload["status"] = final_status
            payload["final_status"] = final_status
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
