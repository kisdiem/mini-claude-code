# Claude Code Gap Analysis After 0.90

Date: 2026-06-19

This document compares the current `mini-claude-code` 0.90 teaching agent with
Claude Code as described in the official Claude Code documentation. It focuses
on architecture gaps that affect benchmark quality, agent reliability, and
longer-term extensibility.

## Current Local Baseline

The current local version has these major systems:

- S20 single-agent loop with file, shell, git, todo, memory, skill, context, and
  subagent tools;
- structured Planner / Executor / Verifier records;
- configurable permissions and command-risk policy;
- command hooks with matcher support for core lifecycle events;
- subagents with prompt, tool allowlist, model override, private memory, private
  hooks, sessions, MCP adapters, capability registry, and structured handoffs;
- MCP stdio and Streamable HTTP adapters with governance, schema guards,
  restart/retry behavior, auth via env references, and audit logging;
- context memory v2 with structured facts and query-aware recall;
- Terminal-Bench shard runner, resume, retry, report generation, automation
  gates, and real-run preflight.

The local implementation is now a coherent teaching architecture, not just a
single agent loop. The remaining gap is no longer "missing all subsystems"; it
is that most subsystems are simplified, deterministic, and local-only versions
of much broader Claude Code capabilities.

## External Reference Points

Official Claude Code documentation describes Claude Code as an agentic coding
tool that reads code, edits files, runs commands, and integrates with developer
tools across terminal, IDE, desktop, web, CI/CD, and chat surfaces. It also
mentions persistent instructions/memory, skills, hooks, MCP, multiple agents,
background agents, custom agents through the Agent SDK, and scheduled or remote
workflows.

Relevant official references:

- Claude Code overview: https://code.claude.com/docs/en/overview
- Hooks reference: https://code.claude.com/docs/en/hooks
- Subagents: https://code.claude.com/docs/en/sub-agents
- MCP: https://code.claude.com/docs/en/mcp

## Highest-Impact Gaps

### 1. Hook Runtime Is Still Narrow

Current state:

- We support core events such as `SessionStart`, `PreToolUse`, `PostToolUse`,
  `Stop`, and `Notification`.
- Configured hooks support command-style handlers and tool/event matchers.
- Subagents have private hook runtimes.

Claude gap:

- Claude Code's hook surface is much broader. The official hooks reference
  lists events such as setup/instructions/user prompt events, permission
  request/denied events, post-tool failure/batch events, subagent start/stop,
  task created/completed, config/cwd/file/worktree changes, pre/post compact,
  session end, elicitation, and teammate idle.
- Claude hooks also include command, HTTP, MCP tool, prompt, and agent hook
  handler types, plus richer decision controls and background execution.

Impact:

- Our hooks cannot yet enforce or observe many important state transitions.
- We cannot build high-quality guardrails around prompt submission, compaction,
  permission escalation, file changes, or task lifecycle.
- This limits both safety and benchmark diagnosis because failures are visible
  only after tool execution, not around the full lifecycle.

Optimization path:

1. Add event model expansion: `UserPromptSubmit`, `PermissionRequest`,
   `PermissionDenied`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`,
   `SubagentStop`, `TaskCreated`, `TaskCompleted`, `PreCompact`,
   `PostCompact`, `SessionEnd`, and `FileChanged`.
2. Add typed payload schemas per event and snapshot tests for every event.
3. Add HTTP and MCP hook handlers.
4. Add prompt/agent hook handlers only after the event model is stable, because
   those handlers can change agent behavior more deeply.

Suggested version split:

- `0.91`: Hook event expansion and event schema tests.
- `0.92`: HTTP/MCP hook handler types.
- `0.93`: Prompt/agent hooks with strict schema validation.

### 2. Subagent Orchestration Is Metadata-Driven, Not Truly Dynamic

Current state:

- We have built-in subagents and configured subagents.
- Each subagent can have isolated prompt/tool/model/memory/hooks/MCP/session.
- Capability registry and `parallel_group` metadata exist.
- Pipelines are still mostly deterministic: benchmark vs standard path, then
  capability-based selection.

Claude gap:

- Claude Code subagents are described as fresh isolated contexts with custom
  prompt, tool access, and independent permissions.
- Claude can delegate based on subagent descriptions, run independent research
  in parallel, chain subagents, resume subagent instances, and support nested
  subagents.
- Subagents can be scoped by managed settings, CLI flags, project files,
  user files, and plugin directories. Supported subagent fields include tools,
  disallowed tools, permission mode, MCP servers, hooks, max turns, skills,
  initial prompt, memory, effort, background, isolation, and color.

Impact:

- Our subagent system is explainable but not yet adaptive enough.
- It records parallel-readiness but does not execute bounded parallel groups.
- It has session files, but not real resume of a subagent conversation with
  full prior tool calls and results.
- It lacks nested subagent delegation.

Optimization path:

1. Implement bounded parallel execution for read-only `parallel_group` steps.
2. Add subagent resume by session id, including previous messages/tool results.
3. Add model-authored orchestration plans with schema validation, while keeping
   capability/permission filters as hard constraints.
4. Add nested subagent delegation behind a max-depth and max-budget guard.
5. Add project/user/plugin subagent discovery order closer to Claude's
   `.claude/agents` model.

Suggested version split:

- `0.94`: Bounded parallel subagent runner.
- `0.95`: Subagent resume by session id.
- `0.96`: Model-authored orchestration plan with validation.
- `0.97`: Nested subagent delegation with depth/budget controls.

### 3. Context Management Lacks Rolling Conversation Compaction

Current state:

- `ContextBuilder` creates workspace snapshots with approximate token budgets.
- Memory v2 stores structured project facts and supports query-aware recall.
- Compression is deterministic head/tail clipping.

Claude gap:

- Claude Code has a documented context-window model and dedicated compaction
  lifecycle hooks.
- Claude Code also has auto memory and persistent instructions through
  project/user memory files.

Impact:

- Our context works for static workspace snapshots, but it does not manage the
  live conversation/tool-result stream.
- Long tasks can still degrade because old tool outputs are either kept too
  long or lost abruptly.
- Memory facts and conversation summaries are not separated as different
  sources with different trust and freshness levels.

Optimization path:

1. Add a `ConversationCompactor` that summarizes old model/tool turns into
   structured trace summaries.
2. Add `PreCompact` and `PostCompact` events once hooks support them.
3. Separate durable memory, recent session facts, tool-output summaries, and
   user instructions into distinct context sources.
4. Add model-specific token counting when possible, with approximate counting
   as a fallback.

Suggested version split:

- `0.98`: Rolling conversation/tool-result compaction.
- `0.99`: Context source registry with durable/recent/tool/user buckets.
- `1.0`: Token-budgeted end-to-end context assembly for agent loop messages.

### 4. MCP Is Functional But Not Production-Grade

Current state:

- We support stdio and Streamable HTTP-like transport.
- We support initialize/capability negotiation, retry/session recovery,
  env-based bearer/header auth, governance policy, audit logs, and schema
  guards.

Claude gap:

- Claude Code supports multiple MCP transports, including HTTP, SSE, stdio, and
  WebSocket.
- Official docs describe HTTP as the recommended remote transport and mention
  OAuth flows, callback ports, dynamic client registration, scope restriction,
  secure credential storage, and metadata discovery.

Impact:

- Our MCP layer works for controlled examples but is weak for real enterprise
  integrations.
- Auth governance is env-based, not full OAuth.
- We do not support WebSocket push-style MCP servers.
- Schema validation is still partial compared with full JSON Schema behavior.

Optimization path:

1. Add WebSocket transport only after HTTP reliability remains stable.
2. Add OAuth metadata discovery and device/browser callback flow.
3. Add token refresh and secure credential-store abstraction.
4. Expand JSON Schema validation for `oneOf`, `anyOf`, formats, bounds, and
   additionalProperties.
5. Add signed/trusted MCP server metadata.

Suggested version split:

- `1.01`: MCP JSON Schema completeness pass.
- `1.02`: OAuth metadata discovery and callback scaffolding.
- `1.03`: OAuth token persistence/refresh abstraction.
- `1.04`: WebSocket MCP transport.

### 5. Permission Policy Is Too Coarse For Real Autonomy

Current state:

- We classify shell command risk and enforce read-only/ask/auto behavior.
- We have configurable allow/block risk policy.
- MCP policy can block high-risk tool names and filter exposed capabilities.

Claude gap:

- Claude Code exposes permission modes, permission request hooks, permission
  denied hooks, and structured permission updates.
- Subagents can have independent permission modes and more granular capability
  restrictions.

Impact:

- Our permissions are good enough for benchmarks and teaching, but not enough
  for real long-running autonomous work.
- There is no durable permission grant history, no prompt-time permission
  mediation, and no per-task narrowing beyond local policies.

Optimization path:

1. Add a permission request event and persisted grant/deny decisions.
2. Add per-task permission narrowing: generated plan declares needed risks;
   runtime rejects tools outside the declared envelope.
3. Add subagent-specific permission profiles that combine tool allowlists,
   shell risk limits, MCP trust profiles, and filesystem scopes.
4. Add a reviewable permission ledger in session records.

Suggested version split:

- `1.05`: PermissionRequest / PermissionDenied events.
- `1.06`: Permission ledger and persisted decisions.
- `1.07`: Plan-scoped permission envelope.

### 6. Planner / Executor / Verifier Is Too Shallow

Current state:

- Planner creates a conservative inspect/execute/verify plan.
- Executor classifies tools against the plan.
- Verifier records basic evidence of checks and failures.

Claude gap:

- Claude Code is described as planning, editing, running commands, and
  verifying across files and workflows.
- In practice, a strong coding agent needs adaptive planning, plan repair,
  evidence tracking, and stricter verification policy.

Impact:

- Our verifier can record that verification happened, but it cannot determine
  whether verification is sufficient for the task risk.
- Planner steps are not yet tied to file ownership, test selection, risk, or
  rollback strategy.

Optimization path:

1. Add model-authored plans with JSON schema and deterministic validator.
2. Add required verification policy by task type and touched file type.
3. Add plan deviation records and repair prompts.
4. Add evidence objects: command, return code, relevant output, changed files,
   and unresolved risks.

Suggested version split:

- `1.08`: Model-authored structured plans.
- `1.09`: Verification policy by task risk.
- `1.10`: Evidence ledger and plan deviation repair.

### 7. Developer Surface And Ecosystem Are Minimal

Current state:

- We have CLI, JSON harness output, local skills, settings, and benchmark
  tools.

Claude gap:

- Claude Code spans terminal, VS Code, JetBrains, desktop, web, CI/CD, Slack,
  remote control, recurring tasks, plugins, and Agent SDK workflows.
- Claude Code also has packageable skills, plugins, and managed/user/project
  scopes.

Impact:

- The local agent is useful for benchmark and architecture teaching, but it is
  not a daily development environment.
- There is no UI for diffs, no background agent dashboard, no plugin
  marketplace, and no CI integration beyond whatever the user scripts manually.

Optimization path:

1. Add a stable JSON event stream for UI/CI consumers.
2. Add GitHub Actions helper workflow templates.
3. Add plugin/skill package discovery and validation.
4. Add a simple local dashboard for sessions, subagents, and benchmark reports.

Suggested version split:

- `1.11`: JSON event stream and CI examples.
- `1.12`: Plugin/skill package manifest validation.
- `1.13`: Local session/subagent/report dashboard.

## Recommended Near-Term Roadmap

The most valuable next steps should improve benchmark scores and architecture
quality at the same time.

### Phase A: Lifecycle Control

Priority: highest.

Implement expanded hook events and permission request events. This gives every
future subsystem better observability and control. It also makes failures easier
to diagnose.

Recommended versions:

- `0.91` Hook Event Surface v2
- `0.92` Hook Handler Types v2
- `0.93` Permission Lifecycle v2

### Phase B: Real Parallel Subagents

Priority: high.

Turn `parallel_group` from metadata into bounded execution. Add subagent resume
after that, because parallel work is much more useful when each worker can be
continued and inspected.

Recommended versions:

- `0.94` Bounded Parallel Subagents
- `0.95` Subagent Resume
- `0.96` Dynamic Orchestration Planner

### Phase C: Long-Context Reliability

Priority: high.

Add rolling compaction and context source separation. This is the most direct
answer to the earlier failures where the agent misunderstood benchmark prompts:
the agent needs better task-state compression and retrieval, not more
prompt-specific hacks.

Recommended versions:

- `0.97` Conversation Compaction
- `0.98` Context Source Registry
- `0.99` End-to-End Context Budgeting

### Phase D: MCP/Auth Hardening

Priority: medium.

MCP is important for Claude parity, but it should come after lifecycle and
context control. Otherwise external tools add complexity before the core agent
can reliably govern them.

Recommended versions:

- `1.01` MCP Schema Completeness
- `1.02` MCP OAuth Discovery
- `1.03` MCP Credential Refresh

## Benchmark Strategy

Terminal-Bench/SWE-bench style tests are useful only if the run is valid and
diagnosable. With 0.90, we now have enough benchmark infrastructure to use a
repeatable loop:

1. Run `--terminal-bench-real-run --tb-preflight-only` first.
2. Fix environment failures before scoring.
3. Run a small shard with `--benchmark-target-score`.
4. Inspect `benchmark-automation.json`, `benchmark-report.md`, and unresolved
   tasks.
5. Convert recurring failure types into architecture work only when they are
   not environment/setup failures.

For architecture validation, the next useful internal benchmark is not just a
Terminal-Bench score. It should include:

- hook lifecycle tests;
- permission escalation tests;
- subagent parallel/resume tests;
- context compaction tests;
- MCP protocol/auth tests;
- real-run benchmark preflight tests.

## Bottom Line

The local agent is now structurally comparable in outline: agent loop,
permissions, hooks, context, memory, subagents, MCP, and benchmark automation
all exist. The main difference is depth:

- Claude Code has a broader lifecycle event model.
- Claude Code has richer subagent configuration, dynamic delegation, parallel
  and nested work patterns, and resume behavior.
- Claude Code has broader MCP transport/auth support.
- Claude Code has stronger ecosystem surfaces: IDE, desktop, web, CI/CD, Slack,
  plugins, skills, background agents, and Agent SDK.
- Our agent has good benchmark-specific infrastructure, but the core runtime
  still needs stronger lifecycle, context, permission, and orchestration
  machinery before benchmark scores should be treated as architecture quality.

The recommended next move is `0.91 Hook Event Surface v2`, because it improves
observability and control for every later subsystem.
