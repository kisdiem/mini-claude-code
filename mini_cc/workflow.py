from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import Provider
from .permission import PermissionRisk


@dataclass(frozen=True)
class PlanStep:
    id: str
    role: str
    goal: str
    status: str = "pending"


@dataclass(frozen=True)
class TaskPlan:
    mode: str
    steps: list[PlanStep]
    permission_envelope: list[str] = field(default_factory=list)
    verification_policy: str = "optional"
    planning_issues: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "steps": [asdict(step) for step in self.steps],
            "permission_envelope": list(self.permission_envelope),
            "verification_policy": self.verification_policy,
            "planning_issues": list(self.planning_issues),
        }


@dataclass(frozen=True)
class ExecutionRecord:
    turn: int
    tool: str
    planned_step: str
    is_error: bool
    chars: int
    summary: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    turn: int
    tool: str
    planned_step: str
    status: str
    kind: str
    summary: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanRepair:
    needed: bool
    reasons: list[str] = field(default_factory=list)
    missing_steps: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    verified: bool
    reason: str
    verification_policy: str = "optional"
    verification_required: bool = False
    failed_tools: list[str] = field(default_factory=list)
    verification_tools: list[str] = field(default_factory=list)
    evidence_ledger: list[EvidenceItem] = field(default_factory=list)
    plan_repair: PlanRepair = field(default_factory=lambda: PlanRepair(False))

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_ledger"] = [item.to_json() for item in self.evidence_ledger]
        payload["plan_repair"] = self.plan_repair.to_json()
        return payload


class Planner:
    """Build a conservative task plan before the model/tool loop starts."""

    def plan(self, prompt: str) -> TaskPlan:
        mode = self._mode(prompt)
        permission_envelope = self._permission_envelope(prompt, mode)
        steps = [
            PlanStep("inspect", "planner", "Inspect workspace context and identify target files before edits."),
            PlanStep("execute", "executor", "Run the minimal tool sequence needed to complete the requested task."),
            PlanStep("verify", "verifier", "Run or identify the most local deterministic verification signal."),
        ]
        if mode == "benchmark":
            steps.append(
                PlanStep(
                    "report",
                    "verifier",
                    "Separate environment, harness, model, and implementation causes before scoring.",
                )
            )
        return TaskPlan(
            mode=mode,
            steps=steps,
            permission_envelope=permission_envelope,
            verification_policy=self._verification_policy(mode, permission_envelope),
        )

    def _mode(self, prompt: str) -> str:
        lowered = prompt.lower()
        if any(token in lowered for token in ["benchmark", "terminal-bench", "harness", "docker", "results.json", "score"]):
            return "benchmark"
        return "standard"

    def _permission_envelope(self, prompt: str, mode: str) -> list[str]:
        lowered = prompt.lower()
        risks = {
            PermissionRisk.READ,
            PermissionRisk.VERIFY,
            PermissionRisk.WORKSPACE_WRITE,
        }
        if mode == "benchmark" or any(token in lowered for token in ["docker", "terminal-bench", "swe-bench", "benchmark", "harness"]):
            risks.update({PermissionRisk.DOCKER, PermissionRisk.NETWORK, PermissionRisk.PACKAGE_MANAGER})
        if any(token in lowered for token in ["download", "curl", "http", "github", "git clone", "clone"]):
            risks.add(PermissionRisk.NETWORK)
        if any(token in lowered for token in ["install", "pip", "npm", "package"]):
            risks.update({PermissionRisk.NETWORK, PermissionRisk.PACKAGE_MANAGER})
        return sorted(risk.value for risk in risks)

    def _verification_policy(self, mode: str, permission_envelope: list[str]) -> str:
        if mode == "benchmark":
            return "required"
        high_risks = {
            PermissionRisk.WORKSPACE_WRITE.value,
            PermissionRisk.NETWORK.value,
            PermissionRisk.PACKAGE_MANAGER.value,
            PermissionRisk.DOCKER.value,
            PermissionRisk.GIT_REMOTE_WRITE.value,
            PermissionRisk.DESTRUCTIVE.value,
        }
        if any(risk in high_risks for risk in permission_envelope):
            return "required"
        return "optional"


class ModelAuthoredPlanner(Planner):
    """Ask a model for a structured plan, then validate it locally."""

    VALID_STEP_IDS = {"inspect", "execute", "verify", "report"}
    VALID_ROLES = {"planner", "executor", "verifier", "critic"}

    def __init__(self, provider: Provider, *, fallback: Planner | None = None) -> None:
        self.provider = provider
        self.fallback = fallback or Planner()

    def plan(self, prompt: str) -> TaskPlan:
        fallback_plan = self.fallback.plan(prompt)
        try:
            response = self.provider.complete(
                [
                    {
                        "role": "user",
                        "content": self._planner_prompt(prompt, fallback_plan),
                    }
                ],
                [],
                "Return only a JSON object for the requested structured plan.",
            )
            payload = json.loads(extract_response_text(response))
        except Exception as exc:
            return self._with_issue(fallback_plan, f"model plan unavailable: {exc}")
        if not isinstance(payload, dict):
            return self._with_issue(fallback_plan, "model plan was not a JSON object")
        return self._validate_payload(payload, fallback_plan)

    def _planner_prompt(self, prompt: str, fallback_plan: TaskPlan) -> str:
        return json.dumps(
            {
                "task": prompt,
                "required_json_shape": {
                    "mode": "standard|benchmark",
                    "steps": [
                        {
                            "id": "inspect|execute|verify|report",
                            "role": "planner|executor|verifier|critic",
                            "goal": "short concrete goal",
                            "status": "pending",
                        }
                    ],
                    "permission_envelope": ["read", "verify", "workspace_write"],
                },
                "hard_limits": {
                    "allowed_permission_envelope": fallback_plan.permission_envelope,
                    "allowed_step_ids": sorted(self.VALID_STEP_IDS),
                    "allowed_roles": sorted(self.VALID_ROLES),
                    "max_steps": 6,
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    def _validate_payload(self, payload: dict[str, Any], fallback_plan: TaskPlan) -> TaskPlan:
        issues: list[str] = []
        mode = str(payload.get("mode") or fallback_plan.mode)
        if mode not in {"standard", "benchmark"}:
            issues.append(f"invalid mode: {mode}")
            mode = fallback_plan.mode
        if mode != fallback_plan.mode:
            issues.append(f"model mode {mode} ignored; using inferred mode {fallback_plan.mode}")
            mode = fallback_plan.mode

        allowed_risks = set(fallback_plan.permission_envelope)
        requested_risks = payload.get("permission_envelope", fallback_plan.permission_envelope)
        if not isinstance(requested_risks, list):
            issues.append("permission_envelope must be a list")
            requested_risks = fallback_plan.permission_envelope
        envelope = sorted(str(item) for item in requested_risks if str(item) in allowed_risks)
        rejected_risks = sorted(str(item) for item in requested_risks if str(item) not in allowed_risks)
        if rejected_risks:
            issues.append("permission envelope filtered: " + ", ".join(rejected_risks))
        if not envelope:
            issues.append("permission envelope empty after filtering; using fallback envelope")
            envelope = list(fallback_plan.permission_envelope)

        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return self._with_issue(fallback_plan, "steps must be a list")
        steps: list[PlanStep] = []
        for index, item in enumerate(raw_steps[:6], start=1):
            if not isinstance(item, dict):
                issues.append(f"step {index} is not an object")
                continue
            step_id = str(item.get("id") or "")
            role = str(item.get("role") or "")
            goal = str(item.get("goal") or "").strip()
            status = str(item.get("status") or "pending")
            if step_id not in self.VALID_STEP_IDS:
                issues.append(f"step {index} invalid id: {step_id}")
                continue
            if role not in self.VALID_ROLES:
                issues.append(f"step {index} invalid role: {role}")
                continue
            if status not in {"pending", "in_progress", "completed"}:
                issues.append(f"step {index} invalid status: {status}")
                status = "pending"
            if not goal:
                issues.append(f"step {index} missing goal")
                continue
            steps.append(PlanStep(step_id, role, goal[:500], status))
        if not steps:
            return self._with_issue(fallback_plan, "no valid model-authored steps")
        return TaskPlan(
            mode=mode,
            steps=steps,
            permission_envelope=envelope,
            verification_policy=self.fallback._verification_policy(mode, envelope),
            planning_issues=issues,
        )

    def _with_issue(self, plan: TaskPlan, issue: str) -> TaskPlan:
        return TaskPlan(
            mode=plan.mode,
            steps=plan.steps,
            permission_envelope=plan.permission_envelope,
            verification_policy=plan.verification_policy,
            planning_issues=[*plan.planning_issues, issue],
        )


def extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


class Executor:
    """Classify tool executions against the current plan."""

    VERIFY_TOOLS = {"run_shell", "git_status", "git_diff", "context_snapshot", "subagent_pipeline"}
    INSPECT_TOOLS = {"list_files", "read_file", "search_text", "todo_read", "memory_read", "skill_list", "skill_read"}

    def classify_tool(self, name: str) -> str:
        if name in self.INSPECT_TOOLS:
            return "inspect"
        if name in self.VERIFY_TOOLS:
            return "verify"
        return "execute"


class Verifier:
    """Summarize whether the run reached a defensible verification state."""

    def verify(self, plan: TaskPlan, executions: list[ExecutionRecord]) -> VerificationResult:
        verification_required = plan.verification_policy == "required"
        failed_tools = [record.tool for record in executions if record.is_error]
        verification_tools = [record.tool for record in executions if record.planned_step == "verify" and not record.is_error]
        evidence_ledger = self._build_evidence_ledger(executions)
        plan_repair = self._build_plan_repair(plan, executions, verification_required)
        if failed_tools:
            return VerificationResult(
                ok=False,
                verified=bool(verification_tools),
                reason="one or more tool executions failed",
                verification_policy=plan.verification_policy,
                verification_required=verification_required,
                failed_tools=failed_tools,
                verification_tools=verification_tools,
                evidence_ledger=evidence_ledger,
                plan_repair=plan_repair,
            )
        if verification_tools:
            return VerificationResult(
                ok=True,
                verified=True,
                reason="verification tool executed successfully",
                verification_policy=plan.verification_policy,
                verification_required=verification_required,
                verification_tools=verification_tools,
                evidence_ledger=evidence_ledger,
                plan_repair=plan_repair,
            )
        if verification_required:
            return VerificationResult(
                ok=False,
                verified=False,
                reason="task risk requires an explicit verification or report tool",
                verification_policy=plan.verification_policy,
                verification_required=True,
                verification_tools=[],
                evidence_ledger=evidence_ledger,
                plan_repair=plan_repair,
            )
        return VerificationResult(
            ok=True,
            verified=False,
            reason="no tool failures observed, but no explicit verification tool ran",
            verification_policy=plan.verification_policy,
            verification_required=False,
            verification_tools=[],
            evidence_ledger=evidence_ledger,
            plan_repair=plan_repair,
        )

    def _build_evidence_ledger(self, executions: list[ExecutionRecord]) -> list[EvidenceItem]:
        ledger: list[EvidenceItem] = []
        for record in executions:
            status = "error" if record.is_error else "ok"
            kind = "verification" if record.planned_step == "verify" and not record.is_error else "tool"
            if record.is_error:
                kind = "failure"
            ledger.append(
                EvidenceItem(
                    turn=record.turn,
                    tool=record.tool,
                    planned_step=record.planned_step,
                    status=status,
                    kind=kind,
                    summary=record.summary[:400],
                )
            )
        return ledger

    def _build_plan_repair(
        self,
        plan: TaskPlan,
        executions: list[ExecutionRecord],
        verification_required: bool,
    ) -> PlanRepair:
        observed_steps = {record.planned_step for record in executions}
        relevant_step_ids = [step.id for step in plan.steps if step.id != "report"]
        missing_steps = [step_id for step_id in relevant_step_ids if step_id not in observed_steps]
        reasons: list[str] = []
        suggested_actions: list[str] = []
        if any(record.is_error for record in executions):
            reasons.append("tool_failure")
            suggested_actions.append("Inspect the failed tool result, fix the cause, and rerun the missing step.")
        if verification_required and "verify" not in observed_steps:
            reasons.append("missing_required_verification")
            suggested_actions.append("Run a verify-classified tool before treating the task as complete.")
        for step_id in missing_steps:
            if step_id == "inspect":
                suggested_actions.append("Inspect workspace context before continuing with more edits.")
            elif step_id == "execute":
                suggested_actions.append("Run the minimal execution step needed to complete the requested change.")
            elif step_id == "verify":
                suggested_actions.append("Add a concrete verification step for the changed output.")
        normalized_actions: list[str] = []
        for action in suggested_actions:
            if action not in normalized_actions:
                normalized_actions.append(action)
        return PlanRepair(
            needed=bool(reasons),
            reasons=reasons,
            missing_steps=missing_steps,
            suggested_actions=normalized_actions,
        )


@dataclass
class StructuredWorkflow:
    planner: Planner = field(default_factory=Planner)
    executor: Executor = field(default_factory=Executor)
    verifier: Verifier = field(default_factory=Verifier)
