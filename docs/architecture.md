# Mini Claude Code Architecture

Mini Claude Code is organized around an evidence-first coding loop. The core
runtime is the path a reviewer should inspect first; optional and experimental
modules extend that path but are not required for the main reliability claim.

## Runtime Layers

- Core Runtime: `agent`, `tools`, `permission`, `task_state`,
  `task_success`, `coding_loop`, `task_runtime`, `verification`, and the
  Evidence Report written under `.mini_cc/task-success/last-run.json`.
- Optional Extensions: hooks, session records, context snapshots, local memory,
  skills, git evidence tools, desktop UI, and web frontend.
- Experimental Features: MCP adapters, subagents, benchmark hints,
  Terminal-Bench automation, tool-use eval, and broad runtime reports.

The historical module table below remains useful for code navigation, but the
default product story is the core Evidence Report path.

## Runtime Modules

| Module | Responsibility |
| --- | --- |
| `mini_cc.agent` | Single model/tool loop. It calls the provider, applies workflow permission envelopes, executes tools, feeds tool results back, rolls old model/tool turns into compact summaries, records sessions, and emits lifecycle hooks. |
| `mini_cc.tools` | Workspace-scoped file/search/shell tools, cloning support for isolated subagent workspaces, plan-scoped permission envelope checks, permission request/denied hook emission, and permission ledger writes. |
| `mini_cc.tool_recovery` | Tool failure recovery layer with failure classification, retry/backoff, alternative tool routing, degraded-mode traces, and post-failure verification metadata. |
| `mini_cc.s20` | S20 teaching toolset: todo, memory, skills, git read tools, context snapshot entrypoint, and workspace cloning that preserves S20 tool behavior inside subagent worktrees. |
| `mini_cc.memory` | Structured context memory facts with legacy key/value migration, priority/scope metadata, query recall, and context formatting. |
| `mini_cc.hooks` | Hook runtime with a v2 event catalog, structured payload specs, payload validation, matcher support, command/HTTP/MCP/prompt/agent hook handlers, timeout/retry/fail-open/fail-closed hardening, decision schema validation, output limits, large-output spill files, additional context injection, hook metrics, and helper methods for prompt/session/tool/permission/subagent/task/context/workspace lifecycle events. |
| `mini_cc.session` | Append-style session JSON records under `.mini_cc/sessions`, including persisted messages and resume support. |
| `mini_cc.context` | Context builder for workspace snapshots, source-typed context registry, git status, todos, query-aware durable memory recall, recent session facts, tool summaries, user instructions, and token-budgeted section compression. |
| `mini_cc.bench` | Terminal-Bench result classifier plus shard runner with Docker health gate, manifest/task-level resume, environment-failure retry, aggregate scoring, JSON/Markdown reporting, automation gates, and real-run preflight pipeline. |
| `mini_cc.tool_eval` | Tool-use evaluation harness with a real local trace runner, per-scenario trace files, observed tool calls, and scoring for tool discovery, selection, parameter correctness, permissions, hooks, MCP auth/server recovery, prompt-injection resistance, tool bloat, and result grounding. |
| `mini_cc.tool_runtime` | Tool-Use Runtime v3 report layer that summarizes MCP registry, MCP health/capabilities, dynamic retrieval, governance, hooks, event coverage, tool-use eval, failure recovery, and report artifacts. |
| `mini_cc.mcp_live` | Local MCP/Hook live validation runner that starts or connects to stdio, HTTP, SSE, and WebSocket MCP endpoints, validates auth/failure classification and token refresh persistence, and exercises command/HTTP/MCP/prompt/agent hook trust profiles with a real hooks.log trace. |
| `mini_cc.subagents` | Isolated subagent runtime with independent prompt, tool allowlist, optional model override, memory tools, hooks, MCP adapters, MCP registry/capability index, MCP tool description quality linting, dynamic MCP tool retrieval, top-k MCP schema exposure, session store, child-session resume by session id, structured task contracts, shared task graph records, DAG scheduler execution, scheduler-mediated peer communication, contradiction detection, critic rejection gates, subagent state-machine events, workflow event history/replay, Runtime v2 trace/metrics/evaluation reporting, worktree-style workspace isolation for write-capable subagents, parallel isolated-write groups with diff collection, conflict detection, conservative merge closure, approval/quality gates for plan/implementation/verification/merge/reviewer checkpoints, bounded nested delegation, config loading, parent-child handoff index, capability registry, schema-validated dynamic planner, structured handoffs, and v2 pipeline orchestration. |
| `mini_cc.mcp` | MCP-like adapter interface plus long-lived stdio JSON-RPC transport, Streamable HTTP JSON/SSE transport, synchronous WebSocket JSON-RPC transport, optional initialize/capability negotiation, retry/session recovery for HTTP, env-based bearer/header auth, env-var allowlists, OAuth metadata discovery plus device-code/browser authorization-code token flows, JSON token store persistence, refresh-token retry/persistence for expired OAuth MCP requests, account profiles, device-flow resume, auth failure classification, restart-on-failure for stdio, subagent-scoped tools/resources/prompts, recursive JSON Schema guards, wildcard/prefix policy filtering, high-risk tool blocking, resource read caching, sensitive resource detection, prompt content version pinning, capability summaries, secret-redacted audit logging. |
| `mini_cc.workflow` | Planner/Executor/Verifier workflow records for S20 runs: conservative fallback planning, model-authored JSON plans with local validation, plan-scoped permission envelope, risk-based verification policy, tool-to-plan execution classification, evidence ledger construction, plan-repair hints, and post-run verification summary. |
| `mini_cc.llm` | Anthropic and OpenAI-compatible provider adapters. |
| `mini_cc.cli` | CLI, JSON harness mode, benchmark-hint extraction, and result classification command. |
| `mini_cc.governance` | Central settings loader, config merge order, validation diagnostics, inline MCP secret warnings, and governance config view. |
| `mini_cc.permission` | Command risk classifier plus configurable allow/block risk policy. |
| `mini_cc.permission_ledger` | Append-only JSONL ledger for permission requests, allows, denials, redacted inputs, and session/subagent context. |

## Current Flow

```text
user prompt
  -> Agent.run
  -> HookRuntime.UserPromptSubmit
  -> Planner creates or validates an inspect/execute/verify plan and permission envelope when workflow is enabled
  -> SessionStore.start + SessionStart hook
  -> Agent installs plan-scoped permission envelope on ToolRunner
  -> provider.complete
  -> tool_use block
  -> Executor classifies tool call against the active plan
  -> HookRuntime.PreToolUse
  -> ToolRunner/S20ToolRunner executes tool
  -> ToolRecoveryPolicy classifies failures and may retry, route to an alternative, or record degraded mode
  -> HookRuntime.PostToolUse
  -> tool_result returned to model
  -> Verifier records verification summary
  -> final text or max_turns
  -> SessionStore.finish + SessionEnd hook + Stop hook
```

## What Changed From The Simplified S20

Before this split, S20 had a small `HookManager` that only wrote
`tool_start/tool_end` rows to `.mini_cc/hooks.log`.

Now hooks are a real runtime layer:

- v2 hook event specs define required fields, optional fields, matcher fields,
  and descriptions for each known event.
- `PreToolUse` can deny a tool call.
- `PostToolUse` receives result metadata and preview content.
- `UserPromptSubmit` is emitted before planning/model execution.
- `InstructionsLoaded` is emitted when workspace `AGENTS.md` instructions are
  loaded into the system prompt.
- `ConfigChange` is emitted when configured hook settings are loaded.
- `Stop` is emitted by the agent at completion, exception, or max turn stop.
- `SessionEnd` is emitted with status/reason/duration when the agent exits.
- `StopFailure` is emitted if a stop hook blocks or fails closed.
- `Notification` exists as a first-class event.
- broader lifecycle events such as `PermissionRequest`, `PermissionDenied`,
  `SubagentStart`, `TaskCreated`, `PreCompact`, and `FileChanged` now have
  payload contracts and are wired into the relevant runtime paths.
- `write_file` and `replace_text` emit `FileChanged` only after a successful
  write.
- `todo_write` emits `TaskCreated` for todo items and `TaskCompleted` when a
  todo is marked completed.
- write-capable subagents emit `WorktreeCreate` when their isolated workspace
  is created. `WorktreeRemove` is emitted by the runtime cleanup helper when an
  isolated worktree is actually removed.
- `PermissionRequest` and `PermissionDenied` are emitted by the permission
  engine when ask-mode or policy decisions block risky tool actions.
- permission decisions are also written to `.mini_cc/permission-ledger.jsonl`
  when state is enabled, so approvals and denials can be audited separately
  from the general hook log.
- workflow plans carry a permission envelope. Tool risks outside that envelope
  are denied before normal permission mode can allow them.
- configured hooks can now use command, HTTP, MCP, prompt, or in-process agent
  handlers.
- Hook handlers are registered on `HookRuntime` rather than hard-coded into
  `S20ToolRunner`.
- Configured hooks are hardened with timeout, retry, failure mode, output size
  control, large-output spill files, decision schema validation,
  `additionalContext`, and runtime metrics.

Before this split, `context_snapshot` assembled context directly inside
`S20ToolRunner`.

Now `ContextBuilder` owns context construction, so task contract extraction,
memory, repo facts, and benchmark facts can evolve independently.

Context memory is now a structured fact layer rather than a plain key/value
dump. `mini_cc.memory` normalizes old memory files, stores scope/priority/source
metadata for new writes, and lets `context_snapshot` recall only facts relevant
to the current task query.

Before this split, benchmark failures had to be inspected manually.

Now `mini_cc.bench.classify_terminal_bench_result()` distinguishes categories
such as:

- `environment_docker_down`
- `environment_apt_network`
- `agent_install_failed`
- `model_timeout`
- `test_failed`
- `unknown_agent_error`

`mini_cc.bench.build_benchmark_report()` now closes the benchmark loop by
reading `shard-manifest.json` and every shard `results.json`, producing:

- aggregate score and category counts;
- per-shard status and score;
- unresolved task lists with failure buckets;
- invalid-run reasons for environment/setup dominated runs;
- recommendations for the next benchmark or architecture action.

`run_benchmark_automation()` wraps the full loop: shard execution, aggregate
summary, benchmark reports, `benchmark-automation.json`, and gates for shard
completion, parsed results, run validity, and optional target score.

`run_terminal_bench_real_pipeline()` adds the outer real-run gate. It writes
`terminal-bench-preflight.json`, checks the task list, command template,
executable, output parent, and Docker health, then runs benchmark automation
only when the preflight passes or the caller explicitly skips it.

`mini_cc.tool_eval` adds a separate harness for tool-use behavior. It does not
try to replace Terminal-Bench. Instead, it scores the lower-level decisions
Terminal-Bench often hides inside a full task: which tools were visible, which
tool was selected, whether parameters were correct, whether permission and hook
blocks were respected, whether MCP auth/server failures were recovered, whether
prompt injection was resisted, whether tool schemas were kept under a top-k
budget, and whether the final answer was grounded in tool evidence. The CLI
entrypoint writes `tool-use-eval.json`, `tool-use-eval.md`, and
`tool-use-scenarios.json`.

`mini_cc.tool_recovery` is the runtime counterpart to that evaluation layer.
When enabled, a failed tool result is classified into categories such as
`permission_denied`, `hook_blocked`, `parameter_error`, `not_found`,
`timeout`, `transient_network`, `mcp_auth_failure`, or `mcp_server_failure`.
Retryable categories can run again with backoff. Some parameter/path failures
can route to safer alternative tools, such as using `list_files` after a
missing `read_file` target or `read_file` after a failed `replace_text`.
Permission and hook blocks are not retried or degraded; the verifier treats
those as successful safety stops. Every recovery attempt writes structured
metadata under `ToolResult.metadata["recovery"]` with a trace and
post-failure verifier result.

`mini_cc.tool_runtime` closes the Tool-Use Runtime v3 layer and, in schema
`3.15`, turns it into an evidence-gated report. Each capability now records
whether it is `implemented`, `configured`, `observed`, `tested`, and
`production_ready`. Code-level support alone is not enough to mark a capability
ready: artifacts such as `.mini_cc/mcp-registry.json`, `.mini_cc/hooks.log`,
capability indexes, tool-use evaluation JSON, and tool-use traces are checked
separately. The CLI command `--tool-runtime-report` writes both JSON and
Markdown summaries. The CLI command `--tool-runtime-evidence-smoke` can first
materialize local evidence artifacts through an in-memory MCP server, real hook
event emission, and local tool-use trace collection. This report is meant to
answer two practical review questions: "Does this project have the MCP, hook,
governance, evaluation, and recovery pieces expected from the tool runtime?"
and "Which of those pieces have real local evidence?"

## Comparison With Claude Code

Claude Code is still broader. The official docs describe hooks as lifecycle
handlers that can run command, HTTP, MCP tool, prompt, or agent hook types.
They also cover many events beyond our current set, including permission,
subagent, compaction, file change, task, and session-end events.

Claude Code subagents can have their own system prompt, tool restrictions,
model, MCP servers, scoped hooks, and memory. This project now has a teaching
version of that boundary, but Claude Code remains broader in lifecycle events,
remote auth flows, and production policy controls.

Claude Code MCP tools appear as normal tools with names like
`mcp__server__tool`. This project follows that naming shape for subagent-scoped
MCP tools.

The subagent orchestrator now has two planning paths. Static planning uses
local rules for standard and benchmark tasks. Dynamic planning asks a model for
a JSON plan, then validates the result locally before execution. The validator
requires a `steps` list, known subagent names, supported phases, phase-to-
capability compatibility, requested capability membership, and read-only
parallel groups. Invalid model-authored steps are filtered and recorded in
`planning_issues`.

Subagent delegation now has a `TaskContract` layer. The natural-language prompt
still exists, but each handoff can also carry objective, deliverable,
constraints, allowed tools, expected evidence, budget, and stop conditions. The
runtime filters requested tools through the actual subagent allowlist, assigns a
contract id, and records that same id in handoff logs, pipeline decisions, and
child session events. Nested subagents inherit `parent_contract_id`.

Subagent execution now also records explicit state transitions. A handoff can
move through `planned`, `ready`, `running`, and then `completed` or `failed`;
invalid starts and budget/session blockers record `blocked`; verification
pipeline steps record `verifying`; empty pipelines record `abandoned`.
Transitions are written to `.mini_cc/subagents/state-events.jsonl` and final
state is copied into handoff rows and child session events.

Subagents also write `.mini_cc/subagents/event-history.jsonl`. This is a
workflow-level event stream, not a chat transcript. It records contract
creation, handoff start/completion, state changes, pipeline planning,
pipeline-step start/completion, and pipeline completion. `subagent_replay_events`
reads that stream and reconstructs a compact view of latest states, handoffs,
pipelines, and contracts without rerunning tools.

Write-capable subagents now run inside a worktree-style workspace. The runtime
keeps read-only helpers on the parent workspace, but helpers with explicit write
tools such as `write_file` or `replace_text` get a separate workspace root for
their tool runner. It tries `git worktree add --detach` first and falls back to
a directory copy when the teaching project is not a Git repository. Handoff
logs store `worktree_path`, `worktree_backend`, and `worktree_isolated`; event
history records `worktree_created` so replay can show which isolated workspace
belonged to each write handoff.

The orchestrator can now run isolated write subagents in parallel. This is not
the same as letting several workers edit the parent workspace at once. Each
write worker edits its own worktree, the runtime collects a diff against the
parent workspace, and then a parent-side merge closure decides what happens.
The current merge policy is file-path based: mixed read/write groups do not run
in parallel, same-file writes block the whole merge, and non-overlapping file
changes are copied back to the parent workspace in step order. Event history
records `worktree_diff_collected`, `parallel_write_merge_completed`, and
`parallel_write_conflict_detected`; replay exposes merge and conflict summaries.

Subagent pipelines now run deterministic approval and quality gates. The plan
approval gate runs after planning and before execution, ensuring the plan has
contracted steps and safe parallel composition. Implementation gates run after
write-capable execute steps and require isolated diffs. Verification and
reviewer gates require their phase to complete without tool errors. Merge gates
run before copying parallel worktree changes back to the parent workspace.
Every gate writes `quality_gate_checked`, and replay includes a `quality_gates`
list for audit and resume decisions.

Subagent orchestration now executes through a shared task graph. `build_task_graph()`
maps pipeline steps into task nodes with stable ids, explicit dependency lists,
`blocked_on` lists, claim/release status, attempts, and reroute metadata. The
graph is stored in `.mini_cc/subagents/task-graphs.jsonl` and mirrored into
event history through `task_graph_created`, `task_graph_scheduler_started`,
`task_node_claimed`, `task_node_released`, `task_node_blocked`,
`task_node_retry_requested`, `task_node_rerouted`, and
`task_graph_scheduler_completed`. The scheduler repeatedly selects ready nodes
whose dependencies are completed, runs safe ready parallel groups together, and
recursively blocks dependent nodes when a prerequisite fails.

Subagent peer communication is scheduler-mediated. Completed dependency nodes
can publish `PeerPacket` records extracted from structured output lines such as
`QUESTION:`, `ANSWER:`, `ARTIFACT:`, `CLAIM:`, and `REJECT:`. The scheduler
adds those packets to later dependency handoffs as `mini_cc_peer_v1` data. It
also publishes peer events for questions, answers, artifacts, contradictions,
and rejections. Contradictions are detected when two packets use the same claim
key with different values. Reviewer/critic rejections fail the reviewer quality
gate, so a rejected implementation cannot silently pass forward.

Subagent Runtime v2 reporting is built from the same event history rather than
from a separate monitoring store. `runtime_report()` produces a JSON or text
summary with three sections: `trace` for the ordered event timeline, `metrics`
for counts and observed run shape, and `evaluation` for pass/needs-attention
status plus blockers. The S20 tool `subagent_runtime_report` exposes this
report to the agent loop.

MCP registry generation creates `.mini_cc/mcp-registry.json` from the configured
subagent MCP adapters. The registry records each server's name, transport,
auth mode, trust level, health status, tool/resource/prompt catalog, generated
tool capability tags, and subagent visibility. The S20 tool
`subagent_mcp_registry` exposes the registry to the agent loop. Tool catalog
entries also include a deterministic description quality report with a score,
warnings, missing fields, input constraints, generated examples, risk notes,
counterexamples, and prompt-injection guidance.

Resource and prompt catalog entries now carry governance metadata too.
Resources record read-policy status, cache state, sensitive-resource detection,
and whether content preview is available after read. Prompts record get-policy
status, content version pinning state, and a metadata version hash. Runtime
reads go through `GovernedMCPAdapter`, so `resources/read` and `prompts/get`
are audited with safe previews instead of being invisible side channels.

MCP auth governance now has a shared shape. Remote HTTP adapters can persist
OAuth token responses and refresh tokens in a configured JSON token store, load
that state on startup, and store account profile metadata next to it. Device
code login can be resumed from a pending token-store entry. Environment-backed
auth can be constrained with an env-var allowlist. HTTP auth failures are
classified into structured categories such as `oauth_metadata_required`,
`expired_token`, `insufficient_scope`, and `refresh_failed`, with re-auth
prompts attached when the next step is user login.

Dynamic MCP tool retrieval now builds on that registry. `tool_index` flattens
server tools into searchable rows with qualified names, tags, quality scores,
subagent visibility, and estimated schema token cost. The runtime also writes a
local vector index to `.mini_cc/mcp-tool-vectors.json`. Each tool row is embedded
with the deterministic `mini_cc_hashing_v1` local embedding, and retrieval
embeds the query the same way before computing vector similarity.
`retrieve_mcp_tools()` combines lexical score and vector score, then returns the
top-k relevant tools for a task. `RestrictedToolRunner.schemas()` uses the same
narrowing idea for subagent MCP tool schemas, so a subagent with many allowed
MCP tools sees a smaller relevant schema set for the current prompt. Permission
allowlists remain separate from retrieval; retrieval only decides which allowed
tool descriptions are shown first. A second-pass expansion is available with
`expand=true`.

The top-level S20 workflow also supports model-authored structured plans. It
asks the provider for a JSON plan, then validates the mode, step ids, roles,
step count, goals, statuses, and permission envelope locally. If the JSON is
invalid or every step is rejected, it falls back to the conservative local
planner and records the reason in `planning_issues`. The model is not allowed
to expand the permission envelope beyond the local fallback plan.

Verification is now policy-driven rather than benchmark-only. The planner
derives a `verification_policy` from the task mode and permission envelope.
Benchmark tasks and tasks that carry write/network/package-manager/Docker risk
are marked `required`; low-risk read-style tasks can stay `optional`. The
verifier uses that policy when deciding whether a run is merely incomplete or
should count as not OK.

The verifier also produces two structured follow-up artifacts. `evidence_ledger`
is the compact audit trail of tool evidence used to justify the final state.
`plan_repair` is the follow-up checklist when the run missed required
verification or hit a failing tool. This keeps "why we believe this run" and
"what still needs fixing" separate from the top-level status flag.

Nested delegation is supported only through explicit tool allowlists. When a
subagent has `subagent_run` or `subagent_pipeline`, the restricted tool runner
routes the call back through `SubagentRuntime` with a depth counter and a
nested prompt/task token budget. Calls past `max_nested_depth` or above
`nested_token_budget` are returned as tool errors instead of being executed.

End-to-end context budgeting runs inside `Agent` before every provider call.
The budget estimate covers the system prompt, tool schemas, and messages. When
the full provider payload is too large, `Agent` first rolls older complete
turns into a deterministic summary message, then summarizes oversized message
payloads such as large tool results. Tool summaries preserve the tool name,
input arguments, result preview, and error status. Recent messages remain
verbatim when possible so active tool-use flow is not broken. `PreCompact`,
`PostCompact`, `conversation_compacted`, and `model_context_budget_applied`
session events record the action.

Context source registry runs inside `ContextBuilder`. It labels context by
source type before rendering the snapshot: `durable_memory`,
`recent_session_facts`, `tool_summaries`, `user_instructions`, and
`workspace`. Recent session facts and tool summaries are read from local session
JSON files when a state directory is available. User instructions are loaded
from `AGENTS.md`.

## Remaining Work

1. Expand hook production controls.
   - Add user-level hook trust profiles.
   - Add signed hook bundles or provenance checks for shared hook packs.
   - Add richer hook metrics export formats.

2. Expand subagents.
   - Add subagent-level MCP trust profiles and per-task capability narrowing.
   - Add richer scheduling policies on top of the bounded read-only parallel runner.

3. Expand Planner/Executor/Verifier.
   - Track plan deviation reasons when tool execution diverges from plan.
   - Add richer plan schemas for dependencies, expected files, and explicit
     success criteria.

4. Expand MCP adapters.
   - Replace the portable JSON token store with encrypted OS keychain storage
     for production deployments.
   - Add WebSocket reconnect policies and subscription-style event handling.
   - Add JSON Schema `format` validation and deeper draft compatibility.
   - Add signed/trusted MCP server metadata.

5. Expand benchmark reporting.
   - Add native Terminal-Bench CLI presets once the target installation is stable.
   - Add optional HTML reports and trend comparison across multiple runs.

6. Expand context management.
   - Add rolling conversation/tool-result compression.
   - Add recent tool/session facts as a separate memory source from durable
     project facts.
   - Add model-specific token counters when provider tokenizers are available.

7. Expand governance.
   - Add user-level settings outside the workspace.
   - Add stricter schema validation with actionable paths.

8. Expand benchmark execution.
   - Add native Terminal-Bench CLI presets once the target installation is stable.
   - Add parallel shard execution with bounded concurrency after Docker stability is verified.

## References

- Local gap analysis after 0.90: [claude-gap-analysis-0.90.md](claude-gap-analysis-0.90.md)
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Claude Code subagents: https://code.claude.com/docs/en/sub-agents
- Claude Code feature overview: https://code.claude.com/docs/en/features-overview
- Claude Agent SDK hooks: https://code.claude.com/docs/en/agent-sdk/hooks
