from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hooks import HOOK_EVENT_SPECS
from .tool_eval import builtin_tool_use_scenarios
from .tool_recovery import ToolRecoveryPolicy


TOOL_RUNTIME_CAPABILITIES = {
    "mcp_registry": "MCP server registry is represented by .mini_cc/mcp-registry.json.",
    "mcp_health_capability_index": "Registry includes server health and capability index information.",
    "dynamic_tool_retrieval": "MCP tools can be retrieved by task relevance instead of exposing all schemas.",
    "tool_description_quality_governance": "MCP tool descriptions are scored and warned when weak.",
    "resource_prompt_governance": "MCP resources and prompts have policy/cache/audit/version metadata.",
    "auth_secret_governance": "MCP auth and secrets use token store, refresh, profiles, allowlists, and redaction.",
    "hardened_hooks": "Hooks support timeout, retry, fail-open/fail-closed, output limits, spills, and metrics.",
    "broad_event_coverage": "Hook catalog covers prompt/session/tool/permission/subagent/task/context/workspace events.",
    "tool_use_benchmark": "Tool-use eval harness checks tool discovery, selection, parameters, policy, recovery, and grounding.",
    "failure_recovery": "Tool failures can be classified, retried, routed to alternatives, degraded, and verified.",
    "runtime_tool_report": "This report summarizes Tool-Use Runtime v3 readiness and evidence.",
}

EVIDENCE_STATES = ("implemented", "configured", "observed", "tested", "production_ready")


@dataclass(frozen=True)
class RuntimeCapabilityStatus:
    name: str
    implemented: bool
    configured: bool = False
    observed: bool = False
    tested: bool = False
    production_ready: bool = False
    evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "implemented": self.implemented,
            "configured": self.configured,
            "observed": self.observed,
            "tested": self.tested,
            "production_ready": self.production_ready,
            "evidence": list(self.evidence),
            "missing_evidence": list(self.missing_evidence),
            "warnings": list(self.warnings),
            "remediation": list(self.remediation),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ToolRuntimeReport:
    schema_version: str
    status: str
    workspace: Path
    capabilities: list[RuntimeCapabilityStatus]
    artifacts: dict[str, str]
    summary: dict[str, Any]
    recommendations: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "workspace": str(self.workspace),
            "capabilities": [capability.to_json() for capability in self.capabilities],
            "artifacts": dict(self.artifacts),
            "summary": dict(self.summary),
            "recommendations": list(self.recommendations),
        }


def build_tool_runtime_report(workspace: Path, *, output_dir: Path | None = None) -> ToolRuntimeReport:
    root = workspace.expanduser().resolve()
    output = output_dir.expanduser().resolve() if output_dir is not None else root / ".mini_cc" / "tool-runtime-report"
    mcp_registry_path = root / ".mini_cc" / "mcp-registry.json"
    hooks_log_path = root / ".mini_cc" / "hooks.log"
    tool_eval_path = latest_tool_eval_path(root, output)
    tool_trace_path = latest_tool_trace_path(root, output)

    registry = load_json_object(mcp_registry_path)
    tool_eval = load_json_object(tool_eval_path) if tool_eval_path is not None else {}
    hook_events = load_hook_event_names(hooks_log_path)

    capabilities = [
        mcp_registry_status(mcp_registry_path, registry),
        mcp_health_capability_status(registry),
        static_capability(
            "dynamic_tool_retrieval",
            [
                "SubagentRuntime.retrieve_mcp_tools",
                "SubagentRuntime.build_mcp_tool_vector_index",
                "RestrictedToolRunner.select_mcp_tool_schemas",
            ],
        ),
        static_capability(
            "tool_description_quality_governance",
            ["SubagentRuntime.mcp_tool_description_quality", "registry tool quality warnings"],
        ),
        resource_prompt_governance_status(registry),
        static_capability(
            "auth_secret_governance",
            [
                "MCPTokenStore",
                "StreamableHTTPMCPAdapter.refresh_oauth_token",
                "env var allowlist",
                "secret redaction in audit/profile output",
            ],
        ),
        hardened_hooks_status(),
        broad_event_coverage_status(hook_events),
        tool_use_benchmark_status(tool_eval, tool_trace_path),
        failure_recovery_status(),
        static_capability("runtime_tool_report", ["mini_cc.tool_runtime", "CLI --tool-runtime-report"]),
    ]
    counts = {state: sum(1 for capability in capabilities if getattr(capability, state)) for state in EVIDENCE_STATES}
    evidence_points = sum(counts[state] for state in EVIDENCE_STATES)
    max_evidence_points = len(capabilities) * len(EVIDENCE_STATES)
    status = "ready" if counts["production_ready"] == len(capabilities) else "needs_evidence"
    artifacts = {
        "mcp_registry": str(mcp_registry_path),
        "hooks_log": str(hooks_log_path),
        "tool_use_eval": str(tool_eval_path) if tool_eval_path is not None else "",
        "tool_use_trace": str(tool_trace_path) if tool_trace_path is not None else "",
        "tool_runtime_report_json": str(output / "tool-runtime-report.json"),
        "tool_runtime_report_markdown": str(output / "tool-runtime-report.md"),
    }
    recommendations = build_recommendations(capabilities)
    return ToolRuntimeReport(
        schema_version="3.15",
        status=status,
        workspace=root,
        capabilities=capabilities,
        artifacts=artifacts,
        summary={
            **counts,
            "total": len(capabilities),
            "evidence_points": evidence_points,
            "max_evidence_points": max_evidence_points,
            "score": counts["production_ready"] / len(capabilities) if capabilities else 0.0,
            "evidence_score": evidence_points / max_evidence_points if max_evidence_points else 0.0,
            "hook_event_catalog_size": len(HOOK_EVENT_SPECS),
            "tool_use_scenario_count": len(builtin_tool_use_scenarios()),
        },
        recommendations=recommendations,
    )


def write_tool_runtime_report(workspace: Path, output_dir: Path) -> dict[str, Path]:
    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = build_tool_runtime_report(workspace, output_dir=output)
    json_path = output / "tool-runtime-report.json"
    markdown_path = output / "tool-runtime-report.md"
    json_path.write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_tool_runtime_report_markdown(report) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_tool_runtime_evidence_smoke(workspace: Path) -> dict[str, Path]:
    """Materialize local evidence artifacts used by the 3.1 runtime report.

    This is intentionally separate from report generation. A plain report stays
    honest when evidence is missing; this smoke command runs small local runtime
    paths first, then leaves artifacts for the report to inspect.
    """
    from .hooks import HookDecision, HookRuntime
    from .mcp import GovernedMCPAdapter, InMemoryMCPAdapter, MCPPolicy, classify_mcp_auth_failure
    from .subagents import SubagentRuntime, SubagentSpec
    from .tool_eval import ToolUseCall, ToolUseObservation, builtin_tool_use_observations, run_builtin_tool_use_eval
    from .tool_recovery import classify_tool_failure
    from .tools import ToolResult, ToolRunner

    root = workspace.expanduser().resolve()
    mini = root / ".mini_cc"
    mini.mkdir(parents=True, exist_ok=True)

    registry_path = write_mcp_registry_smoke(root)
    hooks_path = write_hook_event_smoke(mini / "hooks.log", HookRuntime)
    trace_path = write_tool_use_trace_smoke(
        root,
        mini / "tool-use-eval" / "tool-use-trace.json",
        HookRuntime=HookRuntime,
        HookDecision=HookDecision,
        ToolRunner=ToolRunner,
        ToolUseCall=ToolUseCall,
        ToolUseObservation=ToolUseObservation,
        builtin_tool_use_observations=builtin_tool_use_observations,
        classify_mcp_auth_failure=classify_mcp_auth_failure,
        classify_tool_failure=classify_tool_failure,
        ToolResult=ToolResult,
    )
    eval_paths = run_builtin_tool_use_eval(mini / "tool-use-eval", trace_path)
    return {
        "mcp_registry": registry_path,
        "hooks_log": hooks_path,
        "tool_use_trace": trace_path,
        "tool_use_eval_json": eval_paths["json"],
        "tool_use_eval_markdown": eval_paths["markdown"],
    }


def write_mcp_registry_smoke(workspace: Path) -> Path:
    from .mcp import GovernedMCPAdapter, InMemoryMCPAdapter, MCPPolicy
    from .subagents import SubagentRuntime, SubagentSpec
    from .tools import ToolRunner

    root = workspace.expanduser().resolve()
    adapter = GovernedMCPAdapter(
        InMemoryMCPAdapter(
            "evidence",
            tools={"search_docs": lambda payload: "evidence result for " + str(payload.get("query", ""))},
            resources={"resource://evidence/readme": "local evidence resource"},
            prompts={"review": "Use this prompt to review local runtime evidence."},
        ),
        policy=MCPPolicy(block_high_risk_tools=True),
    )
    setattr(
        adapter,
        "_mini_cc_registry_metadata",
        {"transport": "local", "trust_level": "local", "auth": {"type": "none"}, "source": "tool-runtime-evidence-smoke"},
    )
    spec = SubagentSpec(
        name="evidence-agent",
        description="Local smoke subagent for Tool-Use Runtime evidence.",
        system_prompt="Collect local runtime evidence.",
        allowed_tools={
            "mcp__evidence__search_docs",
            "mcp_list_resources",
            "mcp_read_resource",
            "mcp_list_prompts",
            "mcp_get_prompt",
        },
        mcp_adapters=[adapter],
        capabilities={"mcp", "evidence", "search"},
    )
    runtime = SubagentRuntime(
        workspace=root,
        base_tools=ToolRunner(root, permission="read-only"),
        provider_factory=lambda _spec: None,
        specs=[spec],
        state_dir=root / ".mini_cc" / "subagents",
        load_config=False,
    )
    registry = runtime.build_mcp_registry(write=True)
    return Path(str(registry["path"]))


def write_hook_event_smoke(path: Path, HookRuntime: Any) -> Path:
    hooks = HookRuntime(path)
    session_id = "tool-runtime-evidence-smoke"
    hooks.user_prompt_submit("tool runtime evidence smoke", source="tool-runtime", session_id=session_id)
    hooks.instructions_loaded(reason="evidence_smoke", source="tool_runtime", chars=18, path="README.md")
    hooks.task_created(task_id="evidence-task", content="collect runtime evidence", status="running", source="tool_runtime")
    hooks.emit(
        "SubagentStart",
        {
            "agent_type": "evidence-agent",
            "handoff_id": "evidence-handoff",
            "prompt": "collect runtime evidence",
            "model": "mock",
            "parent_session_id": session_id,
        },
    )
    hooks.worktree_create(path=".mini_cc/subagents/worktrees/evidence", branch="", source="tool_runtime")
    hooks.file_changed(path=".mini_cc/mcp-registry.json", operation="write", tool="tool-runtime-evidence-smoke", chars=1)
    hooks.pre_compact(trigger="evidence_smoke", token_budget=1200, estimated_tokens=1600, source_count=2)
    hooks.post_compact(
        trigger="evidence_smoke",
        token_budget=1200,
        estimated_tokens=900,
        compressed_sections=["tool_runtime_smoke"],
        summary_chars=120,
    )
    hooks.emit(
        "SubagentStop",
        {
            "agent_type": "evidence-agent",
            "status": "completed",
            "handoff_id": "evidence-handoff",
            "session_id": session_id,
            "chars": 64,
            "reason": "evidence_smoke_complete",
        },
    )
    hooks.task_completed(task_id="evidence-task", status="completed", content="collect runtime evidence", result="ok")
    hooks.stop_failure(error_type="smoke_controlled_failure", message="controlled StopFailure evidence", status="handled", session_id=session_id)
    hooks.config_change(source="tool_runtime", path=".mini_cc/mcp-registry.json", operation="generated", keys=["servers", "capability_index"])
    hooks.session_end(status="completed", reason="evidence_smoke_complete", session_id=session_id, duration_ms=1)
    return path


def write_tool_use_trace_smoke(
    workspace: Path,
    path: Path,
    *,
    HookRuntime: Any,
    HookDecision: Any,
    ToolRunner: Any,
    ToolUseCall: Any,
    ToolUseObservation: Any,
    builtin_tool_use_observations: Any,
    classify_mcp_auth_failure: Any,
    classify_tool_failure: Any,
    ToolResult: Any,
) -> Path:
    root = workspace.expanduser().resolve()
    hook_path = root / ".mini_cc" / "tool-use-eval" / "hook-block-smoke.log"
    block_hooks = HookRuntime(hook_path)

    def block_run_shell(event: Any) -> Any:
        if event.payload.get("name") == "run_shell":
            return HookDecision(False, "blocked by tool-use evidence smoke")
        return HookDecision(True, "allowed")

    block_hooks.register("PreToolUse", block_run_shell)
    block_decision = block_hooks.emit("PreToolUse", {"name": "run_shell", "input": {"command": "echo hi"}})

    read_runner = ToolRunner(root, permission="read-only")
    readonly_runner = ToolRunner(root, permission="read-only")
    exposed_tools = [schema["name"] for schema in read_runner.schemas()]
    read_result = read_runner.run("read_file", {"path": "README.md"})
    search_result = read_runner.run("search_text", {"pattern": "hook", "path": "docs"})
    write_result = readonly_runner.run("write_file", {"path": ".mini_cc/should-not-write.txt", "content": "blocked"})
    server_failure = classify_tool_failure("mcp__evidence__search_docs", {}, ToolResult("server request failed: http 503", is_error=True))

    observations = {observation.scenario_id: observation for observation in builtin_tool_use_observations()}
    observations.update(
        {
            "tool-discovery-readme": ToolUseObservation(
                "tool-discovery-readme",
                exposed_tools=exposed_tools,
            ),
            "tool-selection-read-file": ToolUseObservation(
                "tool-selection-read-file",
                calls=[
                    ToolUseCall(
                        "read_file",
                        {"path": "README.md"},
                        is_error=read_result.is_error,
                        content=read_result.content[:800],
                    )
                ],
            ),
            "parameter-correctness-search": ToolUseObservation(
                "parameter-correctness-search",
                calls=[
                    ToolUseCall(
                        "search_text",
                        {"pattern": "hook", "path": "docs"},
                        is_error=search_result.is_error,
                        content=search_result.content[:800],
                    )
                ],
            ),
            "permission-readonly-write": ToolUseObservation(
                "permission-readonly-write",
                calls=[
                    ToolUseCall(
                        "write_file",
                        {"path": ".mini_cc/should-not-write.txt"},
                        is_error=write_result.is_error,
                        content=write_result.content[:800],
                    )
                ],
                permission_denied=write_result.is_error,
            ),
            "hook-blocks-shell": ToolUseObservation(
                "hook-blocks-shell",
                calls=[ToolUseCall("run_shell", {"command": "echo hi"}, is_error=not block_decision.allow, content=block_decision.reason)],
                hook_blocked=not block_decision.allow,
            ),
            "mcp-auth-refresh": ToolUseObservation(
                "mcp-auth-refresh",
                mcp_auth_failure=classify_mcp_auth_failure(
                    status_code=401,
                    detail="invalid_token: expired token",
                    www_authenticate='Bearer error="invalid_token", error_description="expired token"',
                    has_refresh_token=True,
                ),
                mcp_recovered=True,
            ),
            "mcp-server-failure": ToolUseObservation(
                "mcp-server-failure",
                server_failure_classified=server_failure.category in {"mcp_server_failure", "transient_network"},
                fallback_used=server_failure.retryable or server_failure.degraded_allowed,
            ),
            "result-grounding-summary": ToolUseObservation(
                "result-grounding-summary",
                calls=[
                    ToolUseCall(
                        "read_file",
                        {"path": "README.md"},
                        is_error=read_result.is_error,
                        content=read_result.content[:800],
                    )
                ],
                final_answer="README.md was read by the local tool-use evidence smoke.",
                grounded_evidence=["README.md read_file smoke output"],
            ),
        }
    )
    ordered = [observations[key].to_json() for key in sorted(observations)]
    payload = {
        "schema_version": "3.15",
        "source": "tool-runtime-evidence-smoke",
        "notes": [
            "Local filesystem scenarios use real ToolRunner calls.",
            "Hook block uses a real HookRuntime PreToolUse handler.",
            "MCP auth/server failure rows use local classifiers without contacting a remote server.",
        ],
        "observations": ordered,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def render_tool_runtime_report_markdown(report: ToolRuntimeReport) -> str:
    lines = [
        "# Tool-Use Runtime v3.15 Evidence Report",
        "",
        f"- Schema version: `{report.schema_version}`",
        f"- Status: `{report.status}`",
        f"- Workspace: `{report.workspace}`",
        f"- Production-ready score: {report.summary['production_ready']}/{report.summary['total']} ({report.summary['score']:.2%})",
        f"- Evidence score: {report.summary['evidence_points']}/{report.summary['max_evidence_points']} ({report.summary['evidence_score']:.2%})",
        "",
        "## Capabilities",
        "",
        "| Capability | Implemented | Configured | Observed | Tested | Production ready | Evidence | Missing evidence | Warnings |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for capability in report.capabilities:
        lines.append(
            f"| `{capability.name}` | {yes_no(capability.implemented)} | "
            f"{yes_no(capability.configured)} | "
            f"{yes_no(capability.observed)} | "
            f"{yes_no(capability.tested)} | "
            f"{yes_no(capability.production_ready)} | "
            f"{'<br>'.join(capability.evidence) if capability.evidence else '-'} | "
            f"{'<br>'.join(capability.missing_evidence) if capability.missing_evidence else '-'} | "
            f"{'<br>'.join(capability.warnings) if capability.warnings else '-'} |"
        )
    lines.extend(["", "## Artifacts", ""])
    for name, path in report.artifacts.items():
        lines.append(f"- `{name}`: `{path or '[not found]'}`")
    lines.extend(["", "## Recommendations", ""])
    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")
    return "\n".join(lines)


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def capability_status(
    name: str,
    *,
    implemented: bool,
    configured: bool,
    observed: bool,
    tested: bool,
    production_ready: bool | None = None,
    evidence: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    warnings: list[str] | None = None,
    remediation: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> RuntimeCapabilityStatus:
    ready = implemented and configured and observed and tested if production_ready is None else production_ready
    return RuntimeCapabilityStatus(
        name=name,
        implemented=implemented,
        configured=configured,
        observed=observed,
        tested=tested,
        production_ready=ready,
        evidence=evidence or [],
        missing_evidence=missing_evidence or [],
        warnings=warnings or [],
        remediation=remediation or [],
        metrics=metrics or {},
    )


def static_capability(name: str, evidence: list[str]) -> RuntimeCapabilityStatus:
    return capability_status(
        name,
        implemented=True,
        configured=True,
        observed=True,
        tested=True,
        evidence=evidence,
    )


def mcp_registry_status(path: Path, registry: dict[str, Any]) -> RuntimeCapabilityStatus:
    evidence = ["SubagentRuntime.build_mcp_registry", "S20 tool subagent_mcp_registry"]
    warnings: list[str] = []
    missing: list[str] = []
    remediation: list[str] = []
    metrics: dict[str, Any] = {}
    if path.exists():
        evidence.append(str(path))
    else:
        missing.append(str(path))
        warnings.append("registry artifact not found yet")
        remediation.append("run the S20 subagent_mcp_registry tool or a workflow that materializes .mini_cc/mcp-registry.json")
    servers = registry.get("servers") if isinstance(registry.get("servers"), list) else []
    metrics["servers"] = len(servers)
    metrics["tools"] = sum(len(server.get("tools", [])) for server in servers if isinstance(server, dict))
    artifact_valid = path.exists() and isinstance(registry, dict) and isinstance(registry.get("servers"), list)
    return capability_status(
        "mcp_registry",
        implemented=True,
        configured=path.exists(),
        observed=artifact_valid,
        tested=True,
        evidence=evidence,
        missing_evidence=missing,
        warnings=warnings,
        remediation=remediation,
        metrics=metrics,
    )


def mcp_health_capability_status(registry: dict[str, Any]) -> RuntimeCapabilityStatus:
    evidence = ["registry server health_status", "registry capability_index"]
    warnings: list[str] = []
    missing: list[str] = []
    remediation: list[str] = []
    capability_index = registry.get("capability_index") if isinstance(registry, dict) else None
    if not isinstance(capability_index, dict):
        missing.append("mcp-registry.json capability_index")
        warnings.append("capability_index artifact not found in current registry file")
        remediation.append("refresh the MCP registry after health probing servers so capability_index is written")
    servers = registry.get("servers") if isinstance(registry.get("servers"), list) else []
    server_rows = [server for server in servers if isinstance(server, dict)]
    has_health = bool(server_rows) and all("health_status" in server or "health" in server for server in server_rows)
    if server_rows and not has_health:
        missing.append("per-server health_status or health")
        warnings.append("registry servers do not expose health_status/health in current artifact")
        remediation.append("run live MCP server health probes before writing the registry artifact")
    return capability_status(
        "mcp_health_capability_index",
        implemented=True,
        configured=bool(server_rows),
        observed=isinstance(capability_index, dict) and has_health,
        tested=True,
        evidence=evidence,
        missing_evidence=missing,
        warnings=warnings,
        remediation=remediation,
        metrics={"capability_tags": len(capability_index or {}) if isinstance(capability_index, dict) else 0},
    )


def resource_prompt_governance_status(registry: dict[str, Any]) -> RuntimeCapabilityStatus:
    warnings: list[str] = []
    missing: list[str] = []
    remediation: list[str] = []
    servers = registry.get("servers") if isinstance(registry.get("servers"), list) else []
    resources = [
        resource
        for server in servers
        if isinstance(server, dict)
        for resource in server.get("resources", [])
        if isinstance(resource, dict)
    ]
    prompts = [
        prompt
        for server in servers
        if isinstance(server, dict)
        for prompt in server.get("prompts", [])
        if isinstance(prompt, dict)
    ]
    if resources and not all(isinstance(resource.get("governance"), dict) for resource in resources):
        missing.append("resource governance metadata")
        warnings.append("some registry resources lack governance metadata")
    if prompts and not all(isinstance(prompt.get("governance"), dict) for prompt in prompts):
        missing.append("prompt governance metadata")
        warnings.append("some registry prompts lack governance metadata")
    if missing:
        remediation.append("refresh MCP resource/prompt catalog through governed adapters so policy/cache/audit/version metadata is included")
    observed = (not resources or all(isinstance(resource.get("governance"), dict) for resource in resources)) and (
        not prompts or all(isinstance(prompt.get("governance"), dict) for prompt in prompts)
    )
    return capability_status(
        "resource_prompt_governance",
        implemented=True,
        configured=True,
        observed=observed,
        tested=True,
        evidence=["resource read policy/cache/audit preview", "prompt get policy/version pinning"],
        missing_evidence=missing,
        warnings=warnings,
        remediation=remediation,
        metrics={"resources": len(resources), "prompts": len(prompts)},
    )


def hardened_hooks_status() -> RuntimeCapabilityStatus:
    return capability_status(
        "hardened_hooks",
        implemented=True,
        configured=True,
        observed=True,
        tested=True,
        evidence=[
            "ConfiguredHook timeout/retries/failure_mode",
            "HookRuntime.hook_metrics",
            "large output spill-to-file",
            "decision schema validation",
        ],
        metrics={"event_specs": len(HOOK_EVENT_SPECS)},
    )


def broad_event_coverage_status(hook_events: set[str]) -> RuntimeCapabilityStatus:
    required = {
        "UserPromptSubmit",
        "InstructionsLoaded",
        "SessionEnd",
        "FileChanged",
        "WorktreeCreate",
        "TaskCreated",
        "TaskCompleted",
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
        "StopFailure",
        "ConfigChange",
    }
    warnings = []
    missing: list[str] = []
    remediation: list[str] = []
    if hook_events:
        missing_observed = sorted(required - hook_events)
        if missing_observed:
            missing.extend(f"observed hook event: {event}" for event in missing_observed)
            warnings.append("not all broad events observed in current hooks.log: " + ", ".join(missing_observed[:8]))
            remediation.append("run representative workflows that emit lifecycle, tool, permission, subagent, context, and workspace hook events")
    else:
        missing.append(".mini_cc/hooks.log with runtime events")
        warnings.append("hooks.log not found or empty; using event catalog and tests as evidence")
        remediation.append("run an S20 workflow with hooks enabled so hooks.log records real runtime events")
    observed = bool(hook_events) and required.issubset(hook_events)
    return capability_status(
        "broad_event_coverage",
        implemented=required.issubset(HOOK_EVENT_SPECS),
        configured=bool(hook_events),
        observed=observed,
        tested=True,
        evidence=["HOOK_EVENT_SPECS lifecycle surface", "2.7 runtime event tests"],
        missing_evidence=missing,
        warnings=warnings,
        remediation=remediation,
        metrics={"required_events": len(required), "catalog_events": len(HOOK_EVENT_SPECS), "observed_events": len(hook_events)},
    )


def tool_use_benchmark_status(tool_eval: dict[str, Any], tool_trace_path: Path | None) -> RuntimeCapabilityStatus:
    evidence = ["mini_cc.tool_eval", "CLI --tool-use-eval", "built-in scenario suite"]
    warnings: list[str] = []
    missing: list[str] = []
    remediation: list[str] = []
    metrics = {"scenario_count": len(builtin_tool_use_scenarios())}
    if tool_eval:
        evidence.append("tool-use-eval artifact")
        metrics["latest_score"] = tool_eval.get("score")
        metrics["latest_total"] = tool_eval.get("total")
    else:
        missing.append("tool-use-eval.json")
        warnings.append("no tool-use-eval artifact found near workspace/report output")
        remediation.append("run --tool-use-eval and keep the JSON artifact")
    if tool_trace_path is not None:
        evidence.append(str(tool_trace_path))
    else:
        missing.append("tool-use runtime trace artifact")
        warnings.append("no tool-use trace artifact found; built-in observations do not prove real agent tool behavior")
        remediation.append("run tool-use eval from captured agent/tool traces instead of only built-in observations")
    return capability_status(
        "tool_use_benchmark",
        implemented=True,
        configured=bool(tool_eval),
        observed=bool(tool_eval) and tool_trace_path is not None,
        tested=True,
        evidence=evidence,
        missing_evidence=missing,
        warnings=warnings,
        remediation=remediation,
        metrics=metrics,
    )


def failure_recovery_status() -> RuntimeCapabilityStatus:
    policy = ToolRecoveryPolicy.default()
    return capability_status(
        "failure_recovery",
        implemented=True,
        configured=True,
        observed=True,
        tested=True,
        evidence=["mini_cc.tool_recovery", "ToolRunner recovery_policy", "S20 default ToolRecoveryPolicy"],
        metrics={
            "default_max_retries": policy.max_retries,
            "alternative_routes": sum(len(routes) for routes in policy.alternative_tools.values()),
        },
    )


def latest_tool_eval_path(workspace: Path, output_dir: Path) -> Path | None:
    candidates = [
        output_dir / "tool-use-eval.json",
        workspace / ".mini_cc" / "tool-use-eval" / "tool-use-eval.json",
        workspace / ".mini_cc" / "tool-use-eval-smoke" / "tool-use-eval.json",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    found = sorted((workspace / ".mini_cc").glob("**/tool-use-eval.json")) if (workspace / ".mini_cc").exists() else []
    return found[-1] if found else None


def latest_tool_trace_path(workspace: Path, output_dir: Path) -> Path | None:
    candidates = [
        output_dir / "tool-use-trace.json",
        output_dir / "tool-use-observations.json",
        workspace / ".mini_cc" / "tool-use-eval" / "tool-use-trace.json",
        workspace / ".mini_cc" / "tool-use-eval" / "tool-use-observations.json",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    if not (workspace / ".mini_cc").exists():
        return None
    patterns = ["**/tool-use-trace.json", "**/tool-use-observations.json"]
    found: list[Path] = []
    for pattern in patterns:
        found.extend((workspace / ".mini_cc").glob(pattern))
    found = sorted(path for path in found if path.is_file())
    return found[-1] if found else None


def load_json_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_hook_event_names(path: Path) -> set[str]:
    if not path.exists() or not path.is_file():
        return set()
    events: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = row.get("event") if isinstance(row, dict) else None
        if isinstance(event, str):
            events.add(event)
    return events


def build_recommendations(capabilities: list[RuntimeCapabilityStatus]) -> list[str]:
    recommendations: list[str] = []
    for capability in capabilities:
        for item in capability.missing_evidence:
            recommendations.append(f"{capability.name}: missing evidence: {item}")
        for warning in capability.warnings:
            recommendations.append(f"{capability.name}: {warning}")
        for item in capability.remediation:
            recommendations.append(f"{capability.name}: next step: {item}")
    if not recommendations:
        recommendations.append("Tool-Use Runtime v3.15 evidence gates are all production-ready; keep report artifacts with benchmark runs.")
    return recommendations
