from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .coding_loop import CodingLoopDecision, CodingLoopPolicy
from .evidence import (
    EvidenceLedger,
    record_gate_decision,
)
from .reporting import build_evidence_report
from .runtime_types import FileChange, FinalDecision, GateDecision, PlanRecord, RunStatus, RuntimeEventKind
from .task_state import PhaseDecision, TaskStateMachine
from .tools import ToolResult
from .verification_policy import VerificationPolicy
from .project_index import ProjectIndex, render_json
from .repair import build_repair_context, parse_failure_output


@dataclass(frozen=True)
class TaskRuntimeDecision:
    allow: bool
    instruction: str = ""
    reason: str = ""
    status: str = "not_required"
    source: str = "task_runtime"
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskRuntime:
    """Central coordinator for process gates, verification, evidence, and reporting."""

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
        self.turn = 0
        self._pending_tool_events: dict[str, str] = {}
        self.final_decision: FinalDecision | None = None

    def start(self, prompt: str) -> None:
        if not self.enabled:
            return
        self.task_prompt = prompt
        self.tools_called = []
        self.evidence = EvidenceLedger()
        self.turn = 0
        self._pending_tool_events = {}
        self.final_decision = None
        self.evidence.start_run(prompt)
        if self.task_state_machine is not None:
            self.task_state_machine.start(prompt)
        if self.coding_loop is not None:
            self.coding_loop.start(prompt)

    def observe_assistant_text(self, text: str) -> None:
        if not self.enabled:
            return
        self.turn += 1
        before_planned = list(self.task_state_machine.state.planned_files) if self.task_state_machine is not None else []
        self.evidence.add_event(
            RuntimeEventKind.ASSISTANT_TEXT,
            "assistant",
            "assistant text observed",
            phase=self._phase_name(),
            turn=self.turn,
            metadata={"text_excerpt": text[:1200]},
        )
        if self.task_state_machine is not None:
            self.task_state_machine.observe_assistant_text(text)
            after_planned = list(self.task_state_machine.state.planned_files)
            if after_planned and after_planned != before_planned:
                decision = getattr(self.task_state_machine, "plan_decision", None)
                self.evidence.record_plan_declared(
                    self._plan_record_from_text(text, after_planned, decision),
                    phase=self._phase_name(),
                )
                if decision is not None:
                    self.evidence.record_semantic_decision("task_success.validate_plan", decision, phase=self._phase_name())

    def before_tool(self, name: str, tool_input: dict[str, Any]) -> TaskRuntimeDecision:
        if not self.enabled:
            return TaskRuntimeDecision(True)
        tool_event = self.evidence.record_tool_call(name, tool_input, phase=self._phase_name(), turn=self.turn)
        self._pending_tool_events[self._tool_key(name, tool_input)] = tool_event.id
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
        parent_id = self._pending_tool_events.pop(self._tool_key(name, tool_input), None)
        self.evidence.record_tool_result(name, result, phase=self._phase_name(), parent_id=parent_id, turn=self.turn)
        if self.task_state_machine is not None:
            self.task_state_machine.observe_tool_result(name, tool_input, result)
            if name == "read_file" and tool_input.get("path") and not result.is_error:
                self.evidence.record_file_read(str(tool_input["path"]).replace("\\", "/"), phase=self._phase_name())
            for source, decision in [
                ("task_success.validate_edit", getattr(self.task_state_machine, "edit_decision", None)),
                ("task_success.validate_plan", getattr(self.task_state_machine, "plan_decision", None)),
            ]:
                if decision is not None:
                    self.evidence.record_semantic_decision(source, decision, phase=self._phase_name())
        if self.coding_loop is not None:
            self.coding_loop.observe_tool_result(name, tool_input, result)
        modified = self._target_files_for_tool(name, tool_input, result)
        for path in modified:
            self.evidence.record_file_modified(
                FileChange(
                    path=path,
                    operation=self._file_operation(path),
                    tool_name=name,
                    summary=f"{name} changed {path}",
                    is_dry_run=bool(tool_input.get("dry_run")),
                ),
                phase=self._phase_name(),
            )
        if name == "run_shell":
            command = str(tool_input.get("command", ""))
            if command:
                self.evidence.add_event(
                    RuntimeEventKind.VERIFICATION_COMMAND,
                    "run_shell",
                    "verification command observed" if self.verification_policy.is_real_verification(command) else "non-verification shell command observed",
                    phase=self._phase_name(),
                    command=command,
                    severity="info" if self.verification_policy.is_real_verification(command) else "warning",
                )
            if command and self.verification_policy.is_real_verification(command):
                verification = self.verification_policy.evaluate_command(
                    command,
                    result.content,
                    self._current_modified_files(),
                    self._current_task_contract(),
                    self.workspace,
                )
                self.evidence.record_verification_result(verification, phase=self._phase_name())

    def finish_decision(self) -> TaskRuntimeDecision:
        if not self.enabled:
            return TaskRuntimeDecision(True)
        if self.task_state_machine is not None:
            state_decision = self.task_state_machine.finish_decision()
            self.evidence.add(record_gate_decision("task_state", self._gate_from_phase(state_decision), phase=self._phase_name()))
            if not state_decision.allow:
                return self._from_phase_decision(state_decision)
        coding_decision: CodingLoopDecision | None = None
        if self.coding_loop is not None:
            loop_decision = self.coding_loop.finish_decision()
            coding_decision = loop_decision
            self.evidence.add(record_gate_decision("coding_loop", self._gate_from_coding(loop_decision), phase=self._phase_name()))
        final_decision = RuntimeFinalEvaluator().evaluate(
            agent_status="completed",
            task_state=self.task_state_machine.state if self.task_state_machine is not None else None,
            coding_state=self.coding_loop.state if self.coding_loop is not None else None,
            evidence_ledger=self.evidence,
            coding_decision=coding_decision,
        )
        self.final_decision = final_decision
        self.evidence.record_final_decision(final_decision)
        if not final_decision.allow_final:
            return self._from_final_decision(final_decision)
        return TaskRuntimeDecision(True, reason=final_decision.reason, status=final_decision.status, source="task_runtime")

    def final_report(self, status: str) -> str:
        if not self.enabled or self.coding_loop is None:
            return ""
        return self.coding_loop.final_report(status=status)

    def write_artifact(self, status: str) -> Path | None:
        if not self.enabled:
            return None
        task_state = self.task_state_machine.state if self.task_state_machine is not None else None
        coding_state = self.coding_loop.state if self.coding_loop is not None else None
        target = self.workspace / ".mini_cc" / "task-success" / "last-run.json"
        final_decision = RuntimeFinalEvaluator().evaluate(
            agent_status=status,
            task_state=task_state,
            coding_state=coding_state,
            evidence_ledger=self.evidence,
        )
        self.final_decision = final_decision
        self.evidence.record_final_decision(final_decision)
        self.evidence.add_event(
            RuntimeEventKind.ARTIFACT_WRITTEN,
            "task_runtime",
            "evidence report write attempted",
            metadata={"path": str(target), "status": final_decision.status},
        )
        payload = build_evidence_report(
            workspace=self.workspace,
            task_prompt=self.task_prompt,
            task_state=task_state,
            coding_state=coding_state,
            evidence_ledger=self.evidence,
            final_decision=final_decision,
            tools_called=self.tools_called,
        )
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
        repair_context: dict[str, Any] = {}
        if self.task_state_machine is not None and self.task_state_machine.state.repair_context:
            repair_context = dict(self.task_state_machine.state.repair_context)
        else:
            try:
                index = ProjectIndex.build(self.workspace)
                failure = parse_failure_output(command, summary)
                planned = self.task_state_machine.state.planned_files if self.task_state_machine is not None else []
                repair_context = build_repair_context(failure, modified, planned, index).to_json()
            except Exception:
                repair_context = {}
        return "\n".join(
            [
                "Task phase: REPAIR.",
                "The last verification command failed.",
                f"Failed command: {command or '[unknown]'}",
                f"Exit code: {'n/a' if exit_code is None else exit_code}",
                "Modified files: " + (", ".join(modified) if modified else "[unknown]"),
                "",
                "RepairContext:",
                render_json(repair_context) if repair_context else "{}",
                "",
                "Last failure summary:",
                summary[:1200] if summary else "[no failure output captured]",
                "",
                "Failure output excerpt:",
                summary[:1200] if summary else "[no failure output captured]",
                "",
                "Next step: read suggested_next_reads first, identify the smallest relevant cause, make one minimal repair, then rerun the same verification command.",
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

    def _from_final_decision(self, decision: FinalDecision) -> TaskRuntimeDecision:
        instruction = ""
        if decision.status == RunStatus.VERIFICATION_REQUIRED:
            instruction = (
                "Verification required before final answer. You modified code but have not run a real verification command. "
                "Run a relevant test, lint, typecheck, or build command. "
                "git_status, git_diff, context_snapshot, list_files, read_file, search_text, echo, cat, ls, pwd, find, and grep do not count as verification."
            )
        elif decision.status == RunStatus.REPAIR_REQUIRED:
            instruction = self.repair_instruction()
        elif decision.status == RunStatus.SEMANTIC_BLOCKED:
            instruction = "Task semantic gate failed. Address blockers before final answer:\n" + "\n".join(decision.blockers)
        else:
            instruction = decision.unresolved or decision.reason
        return TaskRuntimeDecision(
            allow=decision.allow_final,
            instruction=instruction,
            reason=decision.reason,
            status=decision.status,
            source="task_runtime",
            metadata={"final_decision": decision.to_json()},
        )

    def _phase_metadata(self, decision: PhaseDecision) -> dict[str, Any]:
        return {"next_phase": decision.next_phase.value if decision.next_phase else None}

    def _phase_name(self) -> str | None:
        if self.task_state_machine is None:
            return None
        phase = getattr(self.task_state_machine.state, "phase", None)
        return getattr(phase, "value", str(phase)) if phase is not None else None

    def _tool_key(self, name: str, tool_input: dict[str, Any]) -> str:
        try:
            encoded = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        except TypeError:
            encoded = str(sorted(tool_input.items()))
        return name + ":" + encoded

    def _plan_record_from_text(self, text: str, planned_files: list[str], decision: Any) -> PlanRecord:
        explicit = self._explicit_planned_files(text)
        warnings: list[str] = []
        if not explicit and planned_files:
            warnings.append("planned_files were inferred from assistant text rather than an explicit planned_files line")
        blockers = list(getattr(decision, "blockers", []) if decision is not None else [])
        if decision is not None and not getattr(decision, "allow", True):
            blockers.append(str(getattr(decision, "reason", "semantic plan gate failed")))
        return PlanRecord(
            planned_files=explicit or list(planned_files),
            verification_command=self._extract_verification_command(text),
            raw_text=text,
            is_valid=bool(decision is None or getattr(decision, "allow", True)),
            warnings=self._unique(warnings + list(getattr(decision, "warnings", []) if decision is not None else [])),
            blockers=self._unique(blockers),
        )

    def _explicit_planned_files(self, text: str) -> list[str]:
        match = re.search(r"(?im)^\s*planned_files\s*:\s*(?P<files>.+)$", text)
        if not match:
            return []
        raw = match.group("files")
        values = re.split(r"[,;\s]+", raw)
        return self._unique([value.strip("`'\". ") for value in values if "." in value])

    def _extract_verification_command(self, text: str) -> str:
        match = re.search(
            r"\b((?:python(?:3)?|py)(?:\s+-3)?\s+-m\s+(?:pytest|unittest)[^\n.;`]*)"
            r"|\b(pytest[^\n.;`]*)"
            r"|\b((?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|lint|typecheck|check|build)[^\n.;`]*)"
            r"|\b((?:ruff|mypy|tsc|cargo\s+(?:test|check)|go\s+test|mvn\s+test|gradle\s+test|\.\/gradlew\s+test|make\s+(?:test|check)|markdownlint|mkdocs|sphinx-build)[^\n.;`]*)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return next(group for group in match.groups() if group).strip()

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

    def _file_operation(self, path: str) -> str:
        return "modify" if (self.workspace / path).exists() else "unknown"

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


class RuntimeFinalEvaluator:
    """Normalize final status in one place from process, semantic, verification, and evidence state."""

    def evaluate(
        self,
        *,
        agent_status: str,
        task_state: Any,
        coding_state: Any,
        evidence_ledger: EvidenceLedger,
        coding_decision: CodingLoopDecision | None = None,
    ) -> FinalDecision:
        changed_files = _unique(
            [
                *(getattr(task_state, "modified_files", []) if task_state is not None else []),
                *(getattr(coding_state, "modified_files", []) if coding_state is not None else []),
                *evidence_ledger.modified_files(),
            ]
        )
        verification_results = evidence_ledger.verification_results()
        semantic_blockers = list(getattr(task_state, "semantic_blockers", []) if task_state is not None else [])
        semantic_warnings = list(getattr(task_state, "semantic_warnings", []) if task_state is not None else [])
        last_failure = self._last_failure(task_state, coding_state)
        if agent_status == "max_turns_reached":
            return FinalDecision(
                RunStatus.MAX_TURNS_REACHED,
                allow_final=True,
                reason="max turns reached",
                blockers=semantic_blockers,
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure or "run stopped before completion",
            )
        if agent_status == "failed":
            return FinalDecision(
                RunStatus.FAILED,
                allow_final=True,
                reason="agent run failed",
                blockers=semantic_blockers,
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure,
            )
        if semantic_blockers:
            return FinalDecision(
                RunStatus.SEMANTIC_BLOCKED,
                allow_final=False,
                reason="semantic blockers remain",
                blockers=semantic_blockers,
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure,
            )
        code_modified = bool(changed_files or getattr(coding_state, "code_modified", False) or getattr(task_state, "edited", False))
        if not code_modified:
            return FinalDecision(
                RunStatus.NOT_REQUIRED,
                allow_final=True,
                reason="no code modifications require verification",
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
            )
        latest = self._latest_verification(verification_results, coding_state)
        if latest is None:
            return FinalDecision(
                RunStatus.VERIFICATION_REQUIRED,
                allow_final=False,
                reason="code modified without real verification",
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved="run a real verification command before final answer",
            )
        if not bool(latest.get("is_real_verification", latest.get("passed") is not None)):
            return FinalDecision(
                RunStatus.VERIFICATION_REQUIRED,
                allow_final=False,
                reason="latest command is not real verification",
                blockers=["latest command is not real verification"],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved="run a test/lint/typecheck/build command",
            )
        if not bool(latest.get("is_relevant", True)):
            return FinalDecision(
                RunStatus.VERIFICATION_REQUIRED,
                allow_final=False,
                reason="verification is not relevant",
                blockers=[str(latest.get("relevance_reason") or "verification is not relevant")],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved="run verification that covers the modified behavior or files",
            )
        if not bool(latest.get("passed", False)) and latest.get("exit_code") not in (0, None):
            repair_attempts = int(getattr(coding_state, "repair_attempts", getattr(task_state, "repair_attempts", 0)) or 0)
            max_repair_attempts = int(getattr(coding_state, "max_repair_attempts", getattr(task_state, "max_repair_attempts", 0)) or 0)
            if repair_attempts < max_repair_attempts:
                return FinalDecision(
                    RunStatus.REPAIR_REQUIRED,
                    allow_final=False,
                    reason="verification failed and repair attempts remain",
                    blockers=["verification failed"],
                    warnings=semantic_warnings,
                    changed_files=changed_files,
                    verification_results=verification_results,
                    unresolved=last_failure or str(latest.get("failure_summary", "")),
                )
            return FinalDecision(
                RunStatus.MAX_ATTEMPTS_REACHED,
                allow_final=True,
                reason="repair limit reached; final must report failure",
                blockers=["verification failed"],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure or str(latest.get("failure_summary", "")),
            )
        if not bool(latest.get("has_meaningful_checks", latest.get("passed", False))):
            return FinalDecision(
                RunStatus.VERIFICATION_REQUIRED,
                allow_final=False,
                reason="verification output is not meaningful",
                blockers=[str(latest.get("meaningful_reason") or latest.get("meaningful_checks_reason") or "verification output is not meaningful")],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved="rerun a verification command that actually checks behavior",
            )
        if not bool(latest.get("passed", False)):
            repair_attempts = int(getattr(coding_state, "repair_attempts", getattr(task_state, "repair_attempts", 0)) or 0)
            max_repair_attempts = int(getattr(coding_state, "max_repair_attempts", getattr(task_state, "max_repair_attempts", 0)) or 0)
            if repair_attempts < max_repair_attempts:
                return FinalDecision(
                    RunStatus.REPAIR_REQUIRED,
                    allow_final=False,
                    reason="verification failed and repair attempts remain",
                    blockers=["verification failed"],
                    warnings=semantic_warnings,
                    changed_files=changed_files,
                    verification_results=verification_results,
                    unresolved=last_failure or str(latest.get("failure_summary", "")),
                )
            return FinalDecision(
                RunStatus.MAX_ATTEMPTS_REACHED,
                allow_final=True,
                reason="repair limit reached; final must report failure",
                blockers=["verification failed"],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure or str(latest.get("failure_summary", "")),
            )
        if coding_decision is not None and not coding_decision.allow_finish:
            return FinalDecision(
                coding_decision.status or RunStatus.VERIFICATION_REQUIRED,
                allow_final=False,
                reason=coding_decision.reason,
                blockers=[coding_decision.reason],
                warnings=semantic_warnings,
                changed_files=changed_files,
                verification_results=verification_results,
                unresolved=last_failure,
            )
        return FinalDecision(
            RunStatus.PASSED,
            allow_final=True,
            reason="code modifications have meaningful relevant verification",
            warnings=semantic_warnings,
            changed_files=changed_files,
            verification_results=verification_results,
        )

    def _latest_verification(self, verification_results: list[dict[str, Any]], coding_state: Any) -> dict[str, Any] | None:
        if verification_results:
            return verification_results[-1]
        if coding_state is not None and getattr(coding_state, "verification_commands", []):
            command = coding_state.verification_commands[-1]
            payload = command.to_json()
            payload.setdefault("is_real_verification", True)
            payload.setdefault("is_relevant", True)
            payload.setdefault("has_meaningful_checks", bool(payload.get("passed")))
            return payload
        return None

    def _last_failure(self, task_state: Any, coding_state: Any) -> str:
        if coding_state is not None and getattr(coding_state, "last_failure_summary", ""):
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
