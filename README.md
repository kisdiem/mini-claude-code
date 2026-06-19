# Mini Claude Code

This is a teaching implementation of a Claude-Code-like coding agent. The first
version showed the minimal loop. The current `3.5.0` version adds evidence-gated
runtime reporting, real tool-use traces, MCP/hook live validation, more reliable
subagent/context behavior, and demo packaging for external review.

Reference:

- shareAI-lab/learn-claude-code: https://github.com/shareAI-lab/learn-claude-code
- Anthropic Agent Loop docs: https://code.claude.com/docs/en/agent-sdk/agent-loop

## Quick Start

Run with no API key:

```powershell
cd C:\Users\sixth\mini-claude-code
py -3 -m mini_cc --mock --workspace . "list files"
```

Run the S20 comprehensive mode:

```powershell
py -3 -m mini_cc --mock --s20 --permission auto --workspace . "s20 snapshot"
```

Run through the harness-style non-interactive interface:

```powershell
py -3 -m mini_cc run --mock --s20 --permission-mode bypass --workspace . --output-format json --prompt "s20 snapshot"
```

Diagnose merged project configuration:

```powershell
py -3 -m mini_cc --workspace . --diagnose-config
```

Run tests:

```powershell
py -3 -m unittest discover
```

On this Windows machine, Python 3.10 is the known-good test runner:

```powershell
$py='C:\Users\sixth\AppData\Local\Programs\Python\Python310\python.exe'
$env:TMP='C:\Users\sixth\mini-claude-code\.tmp-tests-py310'
$env:TEMP=$env:TMP
$env:TMPDIR=$env:TMP
$env:PYTHONDONTWRITEBYTECODE='1'
& $py -m unittest discover
```

## Client Demo Package

For a reviewer or client, start with:

```powershell
.\scripts\mock_demo.ps1
.\scripts\tool_use_eval.ps1
.\scripts\runtime_report.ps1
.\scripts\terminal_bench_smoke.ps1
```

Optional local MCP/hook smoke:

```powershell
.\scripts\mcp_hook_live_validation.ps1
```

The Chinese review guide is [CLIENT_README_zh.md](CLIENT_README_zh.md). Example
configs live under `examples/`.

## Local Frontend

Start the native Windows desktop app:

```powershell
.\scripts\start_desktop.ps1
```

You can also double-click `Mini Claude Code.lnk` on the Windows desktop.

Start the simple desktop-like web frontend:

```powershell
.\scripts\start_frontend.ps1
```

Then open:

```text
http://127.0.0.1:8765
```

The frontend lets you manually enter provider, API key, base URL, model,
workspace, permission mode, and prompt. API keys are passed only to the local
backend process for the current run and are not written into project files by
default.

The native desktop app manages `max_turns` and `timeout` automatically from the
task text. It uses shorter budgets for chat, standard budgets for read/search
work, larger budgets for code/test tasks, and long budgets for benchmark tasks.

## Real Claude

Install dependencies:

```powershell
cd C:\Users\sixth\mini-claude-code
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and set:

```text
ANTHROPIC_API_KEY=your_key
CLAUDE_MODEL=claude-sonnet-4-6
```

Then run:

```powershell
.\.venv\Scripts\python -m mini_cc --s20 --workspace . "summarize this project"
```

## OpenAI Provider

Use an OpenAI/Codex API key by setting `OPENAI_API_KEY` and selecting the OpenAI provider:

```powershell
$env:OPENAI_API_KEY = "your_key"
py -3 -m mini_cc run --provider openai --model gpt-5 --s20 --permission-mode bypass --workspace . --output-format json --prompt "list files"
```

The same S20 tools and kbench adapter work with either provider.

## Modes

- `--permission ask`: ask before write tools and shell commands.
- `--permission read-only`: block write tools and shell commands.
- `--permission auto`: allow write tools and shell commands automatically.
- `--mock`: use a deterministic local provider.
- `--s20`: enable the comprehensive teaching toolset.
- `run --prompt ... --output-format json`: non-interactive harness entrypoint.

## Configured Hooks

S20 mode loads project hooks from:

- `.claude/settings.json`
- `.mini_cc/settings.json`
- `.mini_cc/settings.local.json`

The hook shape follows Claude Code's event/matcher/hook list structure:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "run_shell",
        "hooks": [
          {
            "type": "command",
            "command": "py -3 scripts/deny_shell.py"
          }
        ]
      }
    ]
  }
}
```

Matchers:

- empty string or `*`: match all;
- `write_file|replace_text`: match any exact tool name in the list;
- `mcp__.*`: regex matcher.

Hook events use a v2 event catalog in `mini_cc.hooks`. The main runtime paths
now emit these events directly instead of only documenting their payload shape:

- prompt/session: `UserPromptSubmit`, `InstructionsLoaded`, `SessionStart`,
  `SessionEnd`;
- tools/permissions: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `PostToolBatch`, `PermissionRequest`, `PermissionDenied`;
- subagents/tasks: `SubagentStart`, `SubagentStop`, `TaskCreated`,
  `TaskCompleted`;
- context/workspace: `PreCompact`, `PostCompact`, `FileChanged`,
  `CwdChanged`, `WorktreeCreate`, `WorktreeRemove`;
- integration: `ConfigChange`, `Elicitation`, `ElicitationResult`,
  `TeammateIdle`, `Notification`, `Stop`, `StopFailure`.

In plain terms, 2.7 makes hooks more like real runtime sensors:

- when the user submits a prompt, `UserPromptSubmit` fires;
- when `AGENTS.md` is loaded, `InstructionsLoaded` fires;
- when settings hooks are loaded, `ConfigChange` fires;
- when the agent exits, `SessionEnd` and `Stop` fire;
- when a stop hook blocks or fails closed, `StopFailure` fires;
- when `write_file` or `replace_text` changes a file, `FileChanged` fires;
- when `todo_write` creates or completes todo items, task events fire;
- when a subagent starts/stops or creates an isolated worktree, subagent and
  worktree events fire.

Configured command hooks receive JSON with `hook_event_name`,
`schema_version`, `timestamp`, and the event payload. Invalid or incomplete
payloads are logged with `_payload_errors` instead of silently disappearing.

Configured hooks also support runtime hardening fields:

```json
{
  "type": "command",
  "command": "py -3 scripts/check.py",
  "timeout": 5,
  "retries": 1,
  "failure_mode": "fail-open",
  "max_output_chars": 4096,
  "additionalContext": {
    "policy": "strict"
  }
}
```

In plain terms:

- `timeout`: stop waiting if a hook hangs;
- `retries`: retry when the hook itself failed;
- `failure_mode: "fail-open"`: record the hook failure but continue;
- `failure_mode: "fail-closed"`: block when the hook fails;
- `max_output_chars`: cap hook stdout/HTTP/MCP output;
- large hook output is truncated and can be spilled to a file;
- `additionalContext`: attach extra local context to the hook JSON payload;
- `HookRuntime.hook_metrics()` reports attempts, successes, failures, blocks,
  retries, spills, and duration totals.

Hook decisions must be valid JSON. Supported fields are:

```json
{
  "decision": "allow",
  "allow": true,
  "reason": "optional message",
  "payload_updates": {}
}
```

Invalid decision shapes are treated as controlled hook failures and then handled
by the hook's `failure_mode`.

Configured hook handler types:

- `command`: run a local command and read a JSON decision from stdout;
- `http`: POST the hook event JSON to an HTTP endpoint and read a JSON decision
  from the response;
- `mcp`: call a registered MCP hook tool and read a JSON decision from its
  result;
- `prompt`: render a template into `payload_updates`, useful for prompt
  rewriting or adding context;
- `agent`: call a registered in-process agent hook handler.

In plain terms, a hook can now run a script, notify a web service, call an MCP
tool, rewrite prompt payload, or ask a small local handler to decide whether the
event should continue.

Permission events are emitted from the permission engine itself. In plain
terms, `PermissionRequest` means "the agent is about to ask whether this risky
action may run"; `PermissionDenied` means "the action was blocked by policy, a
hook, or the user". These events include the tool name, action text, risk type,
tool input, and optional session/subagent context.

## Terminal-Bench Shards

Plan or run Terminal-Bench task ids in Docker-gated shards:

```powershell
py -3 -m mini_cc `
  --terminal-bench-shards tasks.txt `
  --tb-command-template "tb run {task_args} --output-path {output_dir}" `
  --tb-shard-size 5 `
  --tb-output-dir terminal-bench-shards `
  --tb-dry-run `
  --tb-resume `
  --tb-max-retries 1
```

Template fields:

- `{task_ids}`: comma-separated task ids;
- `{task_args}`: repeated `--task-id TASK` arguments;
- `{output_dir}`: shard output directory;
- `{shard_index}`: 1-based shard number.

Before each shard, the runner executes `docker info`. If Docker is unhealthy,
the current shard is marked `skipped_docker_unhealthy`, later shards are not run,
and `shard-manifest.json` records the stop reason.

With `--tb-resume`, the runner reads `shard-manifest.json` and skips shards that
previously finished with status `passed`. Failed, planned, or Docker-skipped
shards are attempted again.

Resume also reads each shard's `results.json`. If a shard was only partially
completed, already resolved task ids are skipped and unresolved task ids are run
again.

With `--tb-max-retries N`, shards whose `results.json` shows environment-only
failures are retried up to `N` times. The runner also writes
`aggregate-summary.json` with total tasks, resolved tasks, score, and failure
categories aggregated across shard result files.

Build a closed-loop benchmark report from a shard output directory:

```powershell
py -3 -m mini_cc --benchmark-report terminal-bench-shards
```

This writes:

- `benchmark-report.json`: machine-readable score, shard statuses, categories,
  unresolved tasks, invalid-run flags, and recommendations;
- `benchmark-report.md`: human-readable report for review notes.

Use `--benchmark-report-output reports` to write the report files somewhere
other than the shard output directory.

## Benchmark Automation

`--benchmark-automation` runs the shard runner and reporting loop in one command:

```powershell
py -3 -m mini_cc `
  --benchmark-automation tasks.txt `
  --tb-command-template "tb run {task_args} --output-path {output_dir}" `
  --tb-shard-size 5 `
  --tb-output-dir terminal-bench-shards `
  --tb-resume `
  --tb-max-retries 1 `
  --benchmark-target-score 0.99 `
  --benchmark-report-output reports
```

This writes the normal shard artifacts plus:

- `benchmark-report.json`;
- `benchmark-report.md`;
- `benchmark-automation.json`.

Automation gates check shard completion, parsed results, run validity, and the
optional target score. Use `--benchmark-allow-invalid` only when you want the
automation command to report invalid-run diagnostics without failing solely on
the validity gate.

## Terminal-Bench Real Run

`--terminal-bench-real-run` adds a real-run preflight before benchmark
automation:

```powershell
py -3 -m mini_cc `
  --terminal-bench-real-run tasks.txt `
  --tb-command-template "tb run {task_args} --output-path {output_dir}" `
  --tb-output-dir terminal-bench-shards `
  --tb-resume `
  --tb-max-retries 1 `
  --benchmark-target-score 0.99
```

## Tool-use Evaluation

Terminal-Bench checks whether the agent can finish full tasks. The 3.2
tool-use harness checks a narrower question: whether the agent uses tools well
while leaving a real trace.

Run the real local tool-use trace evaluation:

```powershell
py -3 -m mini_cc --tool-use-eval .mini_cc\tool-use-eval
```

This writes:

- `tool-use-eval.json`: machine-readable score and failed checks;
- `tool-use-eval.md`: human-readable report;
- `tool-use-scenarios.json`: the exact scenario list used for the run.
- `tool-use-trace.json`: observations collected from the actual run;
- `traces/*.json`: one per-scenario trace file.

The scenarios cover:

- tool discovery;
- tool selection;
- parameter correctness;
- permission compliance;
- hook intervention;
- MCP auth recovery;
- MCP server failure recovery;
- prompt injection resistance;
- tool bloat control;
- result grounding.

You can also score an external observation file instead of running local
traces:

```powershell
py -3 -m mini_cc --tool-use-eval reports --tool-use-eval-input observations.json
```

In plain terms, the default run no longer grades a prefilled answer sheet. It
starts a small deterministic local agent run for each scenario, records which
tools were visible, which tools were called, which parameters were used,
whether a hook or permission policy blocked something, and what evidence
supported the final answer.

## Tool Failure Recovery

2.9 adds a runtime recovery layer for failed tool calls. It is enabled by
default in S20 mode and can be enabled on a plain `ToolRunner` by passing a
`ToolRecoveryPolicy`.

In plain terms:

- first classify why the tool failed;
- retry only failures that are likely temporary;
- use a safer alternative tool when the first tool was the wrong fit;
- record degraded mode when recovery did not fully fix the failure;
- attach a recovery trace and post-failure verifier result to tool metadata.

Failure categories include:

- `permission_denied`;
- `hook_blocked`;
- `parameter_error`;
- `not_found`;
- `timeout`;
- `transient_network`;
- `mcp_auth_failure`;
- `mcp_server_failure`;
- `unknown`.

Important safety rule: permission and hook blocks are not retried or bypassed.
The recovery verifier treats them as safe stops, not as problems to work
around.

## Tool-Use Runtime v3.15 Evidence Report

3.15 keeps the MCP, hooks, governance, evaluation, and recovery dashboard, and
adds a local evidence smoke that can materialize the artifacts the report
expects.

- `implemented`: the code path exists;
- `configured`: the config or artifact is present;
- `observed`: a real runtime artifact shows the feature happened;
- `tested`: tests cover the feature;
- `production_ready`: the key evidence gates are all satisfied.

In plain terms, the report no longer says "100%" just because a module exists.
If `.mini_cc/mcp-registry.json`, `.mini_cc/hooks.log`, the capability index, or
a tool-use trace is missing, the report stays in `needs_evidence` and tells you
what to run next.

To generate local smoke evidence first, then write the report:

```powershell
py -3 -m mini_cc --workspace . --tool-runtime-evidence-smoke --tool-runtime-report .mini_cc\tool-runtime-report
```

The smoke writes:

- `.mini_cc/mcp-registry.json`;
- `.mini_cc/hooks.log`;
- `.mini_cc/tool-use-eval/tool-use-trace.json`;
- `.mini_cc/tool-use-eval/tool-use-eval.json`.

## MCP / Hook Live Validation

3.3 adds a local live validation command for MCP transports, MCP failures,
OAuth refresh, and hook trust profiles:

```powershell
py -3 -m mini_cc --workspace . --mcp-hook-live-validation .mini_cc\mcp-hook-live
```

This starts or connects to controlled local endpoints and writes:

- `mcp-hook-live-validation.json`;
- `mcp-hook-live-validation.md`;
- `hooks.log`.

It validates:

- stdio MCP smoke;
- HTTP MCP smoke;
- SSE MCP smoke;
- WebSocket MCP smoke;
- disconnect / 401 / expired token / 403 / HTTP 500 classification;
- token refresh persistence;
- command, HTTP, MCP, prompt, and agent hook trust profiles;
- real hook trace events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
  `PostToolUse`, `Stop`, and `SessionEnd`.

In plain terms, this is not just a checklist. The command actually starts local
servers, calls MCP tools/resources/prompts, triggers hook handlers, writes the
hook log, and checks the failure classes.

```powershell
py -3 -m mini_cc --workspace . --tool-runtime-report .mini_cc\tool-runtime-report
```

This writes:

- `tool-runtime-report.json`;
- `tool-runtime-report.md`.

The report checks whether the runtime has:

- MCP registry;
- MCP health and capability index;
- dynamic tool retrieval;
- tool description quality governance;
- resources/prompts governance;
- auth/secret governance;
- hardened hooks;
- broad hook event coverage;
- tool-use benchmark;
- failure recovery;
- runtime tool report.

In plain terms, this is the dashboard you can show someone to explain both what
the tool layer can do and what has actually been observed in local artifacts.

The preflight writes `terminal-bench-preflight.json` and checks:

- task ids were loaded;
- shard size is valid;
- command template includes `{output_dir}` and a task selector;
- the command executable is available;
- output parent directory exists;
- Docker is healthy unless `--tb-dry-run` is set.

Use `--tb-preflight-only` to write diagnostics without running shards. Use
`--tb-skip-preflight` only when the diagnostics are known false positives and
you still want to run automation.

## Context Budget

S20 `context_snapshot` accepts an approximate token budget:

```text
context_snapshot {"token_budget": 1200}
context_snapshot {"query": "Terminal-Bench results.json", "memory_limit": 4, "token_budget": 1200}
```

The context builder allocates budget across workspace, files, git, todos, and
memory recall sections. Oversized sections are compressed by preserving their
head and tail with an omission marker. The snapshot ends with a
`# Context Budget` report showing the requested budget, estimated tokens, and
compressed sections.

`context_snapshot` also includes a `# Context Source Registry`. This registry
labels where each piece of context came from:

- `durable_memory`: long-lived facts from `memory_write` / `memory_recall`;
- `recent_session_facts`: recent session prompts, statuses, and events;
- `tool_summaries`: recent tool names, result sizes, error flags, and compacted
  tool summaries;
- `user_instructions`: workspace instructions such as `AGENTS.md`;
- `workspace`: files, git, todos, and workspace path facts.

In plain terms, the model can now tell whether a fact is a durable memory, a
recent run detail, a tool-result summary, or an instruction from the user.

## Conversation Compaction

Long agent sessions also compact old model/tool turns automatically. This is
different from `context_snapshot`: `context_snapshot` compresses workspace
context, while conversation compaction compresses the chat/tool history sent
back to the model.

When the estimated conversation size passes
`--conversation-compaction-token-budget`, old turns are replaced by a
deterministic summary. The recent messages stay verbatim. The summary keeps:

- tool name;
- tool input arguments;
- result summary;
- whether the tool failed;
- failure text when available.

Useful CLI controls:

```text
--conversation-compaction-token-budget 6000
--conversation-compaction-keep-recent 6
--model-context-token-budget 8000
```

In plain terms, the agent keeps the newest conversation exactly as-is, and
turns older tool work into a compact checklist so the model still knows what
happened without carrying every full tool output forever.

`--model-context-token-budget` is the end-to-end budget gate. It estimates the
whole payload sent to the model: system prompt, tool schemas, and messages. If
the whole payload is too large, the agent first rolls old turns into a summary,
then summarizes oversized tool results before the provider call.

## Context Memory

Project memory is stored in `.mini_cc/memory.json`. Version 2 memory keeps
facts as structured records with:

- `key` and `value`;
- `scope`: `project`, `task`, `user`, `repo`, or `subagent`;
- `priority` from `0` to `100`;
- `source`, `tags`, and `updated_at`.

The old key/value memory format is still readable. New writes use the v2 shape:

```text
memory_write {"key": "terminal-bench", "value": "parse results.json before scoring", "priority": 90, "tags": ["benchmark"]}
memory_recall {"query": "Terminal-Bench results.json", "limit": 3}
```

`context_snapshot` uses `memory_recall` when a query is supplied, so long task
context can include relevant durable facts without dumping all memory into the
prompt.

## Subagents

S20 mode exposes isolated subagents:

```text
subagent_list {}
subagent_run {"name": "explorer", "prompt": "list files"}
subagent_run {"name": "explorer", "prompt": "list files", "task_contract": {"objective": "Inspect files", "deliverable": "File list with evidence"}}
subagent_run {"name": "explorer", "prompt": "read README", "session_id": "<child-session-id>"}
subagent_pipeline {"task": "fix failing tests", "mode": "auto"}
```

Built-in subagents:

- `explorer`: read-only fact gathering;
- `implementer`: focused edits and local checks;
- `verifier`: targeted verification;
- `critic`: regression and overfitting review;
- `bench-diagnoser`: benchmark/environment failure diagnosis.

Each subagent has its own system prompt, tool allowlist, optional model
override, and memory dictionary. Tool allowlists are enforced at runtime, so a
read-only subagent cannot call write tools even if it asks for them.

Each subagent also has its own runtime boundary:

- private hook runtime and `hooks.log`;
- private session store under `.mini_cc/subagents/<name>/sessions`;
- private memory tools: `subagent_memory_read` and `subagent_memory_write`;
- optional MCP adapters exposed as tools such as `mcp__server__tool`;
- MCP resource tools: `mcp_list_resources` and `mcp_read_resource`.

Write-capable subagents also get their own worktree-style workspace. In plain
terms, a read-only helper can look at the main project directly, but a helper
that can write files first gets its own copy of the project. Its `write_file`
or `replace_text` tools run inside that copy, so two writing helpers do not
immediately overwrite each other in the same directory.

When Git is available, the runtime tries to create a real `git worktree`. When
the workspace is not a Git repository, it falls back to a controlled directory
copy under:

```text
.mini_cc/subagents/worktrees/
```

Every handoff records `worktree_path`, `worktree_backend`, and
`worktree_isolated`, and event replay includes `worktree_created` entries.

Parallel write subagents are allowed only through that isolation layer. In
plain terms, two writing helpers may work at the same time, but only because
each one writes in its own worktree first. After they finish, the parent runtime
collects each worktree diff, checks whether two helpers touched the same file,
and then applies the non-conflicting files back to the main workspace.

The merge policy is deliberately conservative:

- read-only parallel groups still run as before;
- write parallel groups require every member to be write-capable and
  worktree-isolated;
- mixed read/write groups do not run in parallel;
- if two write subagents changed the same relative file path, the whole merge
  is blocked;
- if no file-path conflicts exist, files are merged back in step order.

Event replay records `worktree_diff_collected`,
`parallel_write_merge_completed`, and `parallel_write_conflict_detected`.

Subagent pipelines also have approval and quality gates. In plain terms, a gate
is a checkpoint before the system moves to the next stage:

- `plan_approval`: the plan must have executable contracted steps and safe
  parallel groups;
- `implementation`: a write-capable execute step must run in an isolated
  worktree and produce a file diff;
- `verification`: a verify step must complete without a tool error;
- `merge`: parallel write merge must have isolated worktrees and no same-file
  conflicts;
- `reviewer`: a review step must complete without a tool error.

Every gate writes a `quality_gate_checked` event. Failed blocker gates stop the
pipeline before the next risky action. Event replay includes a `quality_gates`
section so the parent can explain which checkpoint passed or failed.

Subagent pipelines also create a shared task graph:

```text
.mini_cc/subagents/task-graphs.jsonl
```

In plain terms, a pipeline is like a numbered checklist, while a task graph is
closer to a work board. Each task node knows:

- which subagent owns the task;
- which phase it belongs to;
- which earlier task ids it depends on;
- which task ids it is currently blocked on;
- whether it is ready, running, completed, failed, or blocked;
- who claimed it;
- how many attempts it has used;
- whether it was rerouted from another subagent.

The runtime now records task graph events such as `task_node_claimed`,
`task_node_released`, `task_node_blocked`, `task_node_retry_requested`, and
`task_node_rerouted`. Event replay includes `task_graphs`, so resume and later
graph scheduling can recover not just "what was said", but "which tasks existed
and where each one stopped".

Subagent delegation now also carries a structured task contract. In plain
terms, this is the handoff sheet that travels with the natural-language prompt:

- `objective`: what the subagent is trying to accomplish;
- `deliverable`: what the subagent should return;
- `constraints`: boundaries such as read-only or stay in a phase;
- `allowed_tools`: tools requested for the task, filtered through the
  subagent's real allowlist;
- `expected_evidence`: what proof should come back;
- `budget`: limits such as max turns or parallel count;
- `stop_conditions`: when the subagent should stop.

If the caller does not provide a contract, the runtime creates a conservative
fallback contract from the prompt, phase, and subagent tool boundary. Handoffs,
pipeline decisions, and child session events record the same contract id, so a
later review can connect "why this helper was called" with "what it actually
did".

Subagents also record a simple state machine. In plain terms, every helper now
has a task progress label instead of only a final output:

- `planned`: the handoff was created;
- `ready`: the contract and tool boundary are prepared;
- `running`: the subagent loop started;
- `blocked`: the subagent could not start or continue, such as a missing
  session id or budget limit;
- `waiting_approval`: reserved for later approval gates;
- `verifying`: a pipeline entered a verification phase;
- `completed`: the subagent returned output;
- `failed`: the subagent raised an exception;
- `abandoned`: the pipeline had no executable subagent steps.

When state is enabled, these transitions are written to:

```text
.mini_cc/subagents/state-events.jsonl
```

In plain terms, this is the subagent progress ledger. It lets later versions
resume or diagnose a subagent by reading what state it reached and why.

Subagents also keep a workflow event history:

```text
.mini_cc/subagents/event-history.jsonl
```

In plain terms, the session transcript is the chat record, while event history
is the work log. It records key workflow events such as contract creation,
handoff start/end, state changes, pipeline planning, step start/end, and
pipeline completion.

The parent agent can call:

```text
subagent_replay_events {}
```

That replay does not rerun tools. It reads the event history and rebuilds a
compact summary of latest subagent states, handoffs, pipelines, and contracts.
This is the first step toward resume that can recover work progress instead of
only restoring chat messages.

Configured `stdio` MCP servers use newline-delimited JSON-RPC and currently
support `initialize`, `tools/list`, `tools/call`, `resources/list`,
`resources/read`, `prompts/list`, and `prompts/get`. The transport keeps a
long-lived process per adapter and restarts it after pipe/server failures.

Configured `streamable_http` MCP servers use HTTP JSON-RPC with
`MCP-Protocol-Version`, optional `Mcp-Session-Id`, JSON responses, and
`text/event-stream` responses.

Subagents can be configured in `.mini_cc/settings.json`,
`.mini_cc/settings.local.json`, or `.claude/settings.json`:

```json
{
  "subagents": {
    "reader": {
      "description": "Read-only project explorer",
      "system_prompt": "Read files and report concrete facts.",
      "tools": ["list_files", "read_file", "search_text", "mcp__local__echo"],
      "model": "small-model",
      "memory": {"mode": "configured"},
      "max_turns": 3,
      "mcp_servers": [
        {
          "name": "local",
          "transport": "stdio",
          "trust_level": "local",
          "command": ["python", "scripts/fake_mcp_server.py"],
          "initialize": true,
          "timeout": 10,
          "protocol_version": "2024-11-05",
          "policy": {
            "allowed_tools": ["echo"],
            "allowed_resources": ["resource://note"],
            "blocked_prompts": ["unsafe_prompt"]
          },
          "audit_log": ".mini_cc/mcp-audit.jsonl"
        }
      ]
    }
  }
}
```

Remote Streamable HTTP MCP example:

```json
{
  "subagents": {
    "remote-reader": {
      "description": "Read from a remote MCP server",
      "system_prompt": "Use remote MCP tools only when relevant.",
      "tools": ["mcp__remote__search", "mcp_list_resources"],
      "mcp_servers": [
        {
          "name": "remote",
          "transport": "streamable_http",
          "trust_level": "remote",
          "url": "https://example.com/mcp",
          "initialize": true,
          "protocol_version": "2025-06-18",
          "headers": {"X-Client": "mini-cc"},
          "auth_token_env": "MCP_REMOTE_TOKEN",
          "headers_env": {"X-API-Key": "MCP_REMOTE_API_KEY"},
          "oauth_discovery": true,
          "oauth_flow": "device_code",
          "oauth_client_id": "mini-claude-code",
          "oauth_scopes": ["mcp:read"],
          "max_retries": 2,
          "retry_backoff": 0.25,
          "policy": {
            "allowed_tools": ["search"],
            "allowed_resources": ["resource://public/*"],
            "blocked_prompts": ["unsafe-*"],
            "block_high_risk_tools": true
          },
          "audit_log": ".mini_cc/mcp-audit.jsonl"
        }
      ]
    }
  }
}
```

Remote WebSocket MCP example:

```json
{
  "subagents": {
    "remote-reader": {
      "description": "Read from a WebSocket MCP server",
      "system_prompt": "Use remote MCP tools only when relevant.",
      "tools": ["mcp__remote_ws__search"],
      "mcp_servers": [
        {
          "name": "remote_ws",
          "transport": "websocket",
          "trust_level": "remote",
          "url": "wss://example.com/mcp",
          "initialize": true,
          "protocol_version": "2025-06-18",
          "auth_token_env": "MCP_REMOTE_TOKEN",
          "headers": {"X-Client": "mini-cc"},
          "policy": {"allowed_tools": ["search"]}
        }
      ]
    }
  }
}
```

Build the MCP registry:

```text
subagent_mcp_registry {"refresh": true}
```

This writes:

```text
.mini_cc/mcp-registry.json
```

The registry is the project MCP directory. It records each server's name,
transport, auth mode, trust level, health status, tools, resources, prompts,
capability tags, and which subagents can see which MCP tools. In plain terms,
MCP tools are no longer just scattered inside subagent config; the project now
has a searchable catalog.

Each tool entry also includes a `quality` section. This is a local linter for
the tool description. It records a score, warnings, missing fields, inferred
purpose, input constraints, risk notes, example input/output, a counterexample,
and a prompt-injection warning. In plain terms, the registry now tells the agent
whether the tool label is clear enough to trust. A vague description like
`MCP tool remote.run` will be marked as generic and risky instead of being
treated like a well-documented tool.

Retrieve the most relevant MCP tools for a task:

```text
subagent_mcp_tool_retrieval {"query": "find install docs", "subagent": "reader", "top_k": 5}
```

This returns a ranked tool list, candidate count, selected count, estimated
schema tokens, estimated token savings, and a fallback note. In plain terms,
the agent no longer has to read every MCP tool label before every task. It first
gets a small basket of likely relevant tools.

Subagent runs also use this idea when exposing MCP tool schemas. If a subagent
has many MCP tools, the runtime ranks the allowed MCP tools against the current
prompt and exposes the top matches first. Normal permission allowlists still
apply; retrieval only narrows which allowed tool descriptions are shown to the
model for that turn.

If the first selection is not enough, call the retrieval tool again with
`"expand": true` to inspect every visible candidate.

Build or inspect the local MCP tool vector index:

```text
subagent_mcp_vector_index {"refresh": true}
```

This writes:

```text
.mini_cc/mcp-tool-vectors.json
```

In plain terms, each MCP tool description is converted into a list of numbers.
When the agent receives a task, the task is converted into the same kind of
number list, and the runtime compares the two. Retrieval now combines keyword
matching with vector similarity. This project uses a deterministic local
hashing embedding named `mini_cc_hashing_v1`, so the tests do not need network
access or an embedding API key.

MCP policy filters exposed tool/resource/prompt lists and blocks disallowed
calls at runtime. When `audit_log` is set, MCP list/read/call actions are
written as JSONL rows for later review. Tool, resource, and prompt policy
entries support exact matches, shell-style wildcards such as `unsafe-*`, and
prefix entries such as `prefix:resource://public/`. Resource allowlists also
accept `resource://public/*` as a prefix pattern.

Resource reads and prompt fetches now have their own governance details. In
plain terms, MCP documents and MCP prompt templates are no longer treated as
harmless side channels.

For resources:

- `resources/read` checks resource policy before reading;
- successful reads can be cached by URI;
- audit rows record cache hits, content hash, length, sensitivity, and a safe
  content preview;
- sensitive resource previews are redacted.

For prompts:

- `prompts/get` checks prompt policy before fetching;
- the first successful fetch pins a content hash;
- later fetches are blocked if the same prompt name returns different content;
- audit rows record prompt version and argument hash.

The registry also exposes this as governance metadata on each resource and
prompt entry, so the agent can inspect risk before it reads.

High-risk MCP tool names containing tokens such as `write`, `delete`, `exec`,
`shell`, `run`, `update`, or `drop` are blocked by default unless explicitly
allowed through `allowed_tools`. Audit rows include a generated `request_id`,
subagent context when available, and remote MCP session id when present. Audit
rows redact sensitive content that looks like authorization, bearer token,
API key, secret, or token material.

For remote MCP auth, prefer environment references:

- `auth_token_env`: environment variable used as a bearer token;
- `bearer_token_env`: alias for `auth_token_env`;
- `headers_env`: maps HTTP header names to environment variable names.
- `env_var_allowlist`: optional patterns limiting which env vars may be read
  for MCP auth.
- `token_store`: optional JSON file used to persist OAuth token and refresh
  state.
- `account_profile`: optional account metadata such as `account_id`, `label`,
  or `subject`.

Inline `auth_token`, `bearer_token`, or sensitive headers such as
`Authorization` still work, but `--diagnose-config` warns because they put
secrets in project settings.

Example:

```json
{
  "name": "remote",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "oauth_discovery": true,
  "oauth_flow": "device_code",
  "oauth_client_id": "mini-cc",
  "token_store": ".mini_cc/mcp-tokens.json",
  "account_profile": {
    "account_id": "work-account",
    "label": "Work MCP"
  },
  "env_var_allowlist": ["MCP_*"]
}
```

Remote Streamable HTTP MCP can discover OAuth metadata when
`oauth_discovery` is enabled. The adapter checks OAuth protected-resource
metadata and then authorization-server metadata, storing discovered fields for
capability summaries and diagnostics. You can also set `oauth_metadata_url`
directly. If a server returns `401` with a `WWW-Authenticate` header containing
`resource_metadata`, discovery is triggered from that URL.

For OAuth login, `oauth_flow: "device_code"` can request a device code, show
the verification URL/code, poll the token endpoint, and install the returned
bearer token on the MCP adapter. The adapter also has browser authorization-code
helpers for building an authorization URL, receiving a local callback, and
exchanging the code for a token. If `token_store` is configured, OAuth token
responses and refresh tokens are persisted there. Pending device-code flows are
also stored, so a device login can be resumed after interruption. Reports use
redacted token profiles rather than printing raw token values.

When a later MCP HTTP request returns `401` or `403`, the adapter classifies the
auth failure, tries refresh when a refresh token is available, persists the new
refresh token when configured, and retries the original request once. Auth
failures are labeled with categories such as `oauth_metadata_required`,
`expired_token`, `missing_token`, `insufficient_scope`, or `refresh_failed`, and
can include a re-auth prompt for the next step.

For Streamable HTTP MCP servers, transient HTTP failures such as `429`, `500`,
`502`, `503`, and `504` are retried up to `max_retries`. If a session-scoped
request returns `401`, `403`, or `404`, the adapter clears the old
`Mcp-Session-Id`, runs `initialize` again when enabled, and retries the request.
MCP tool calls are checked recursively against the tool's JSON schema before
the request is sent. The guard covers required fields, primitive JSON types,
enum/const, string length and pattern, number bounds, array item rules, object
`additionalProperties`, and basic `oneOf`/`anyOf`/`allOf`.

The same settings files support permission governance:

```json
{
  "permission_policy": {
    "block_risks": ["network", "docker", "git_remote_write"],
    "allow_risks": ["verify"]
  }
}
```

When permission governance blocks an action, the runtime emits
`PermissionDenied`. In `ask` mode, the runtime emits `PermissionRequest` before
asking or before a hook blocks the request. This gives logs and configured hooks
a structured permission ledger instead of only a tool error string.

When state is enabled, S20 also writes an append-only permission ledger:

```text
.mini_cc/permission-ledger.jsonl
```

Each row records `request_id`, timestamp, decision (`requested`, `allowed`, or
`denied`), tool name, action, risk, reason, redacted tool input, and optional
session/subagent context. In plain terms, this is the audit notebook for "what
the agent tried to do and why the permission system allowed or blocked it".

When structured workflow is enabled, the planner also creates a plan-scoped
permission envelope. In plain terms, the plan says "this task should only need
these risk types". The tool runner then blocks risks outside that envelope
before normal `auto` or `ask` permission behavior. For example, a normal README
edit plan can allow file writes but still block an unexpected Docker command.
Benchmark plans may include Docker/network/package-manager risks because those
tasks usually need environment checks.

In S20 mode, the plan can now be model-authored. In plain terms, the model is
allowed to suggest a small JSON checklist for the task before the normal agent
loop starts. Local code still checks that checklist before using it:

- the mode must match the locally inferred mode;
- step ids must be known values such as `inspect`, `execute`, `verify`, or
  `report`;
- roles must be known values such as `planner`, `executor`, `verifier`, or
  `critic`;
- the plan can contain at most six steps;
- permission risks are filtered through the local fallback plan, so the model
  cannot turn a normal edit task into a Docker/network/package-manager task;
- invalid JSON or invalid steps fall back to the conservative local planner.

In plain terms, the model can write the first draft of the plan, but the local
validator remains the gatekeeper.

Config load order is `.claude/settings.json`, then `.mini_cc/settings.json`,
then `.mini_cc/settings.local.json`; later files override earlier values.

Configured subagents with the same name replace built-in defaults.

Subagent-local hooks can be placed under:

```text
.mini_cc/subagents/<name>/hooks.json
.mini_cc/subagents/<name>/settings.json
```

Every `subagent_run` writes parent-child handoff metadata when state is enabled:

```text
.mini_cc/subagents/handoffs.jsonl
.mini_cc/subagents/session-index.json
```

The handoff row links the subagent name, prompt, status, output preview, model,
and child session id.

That child session id can be passed back to `subagent_run` to resume the same
subagent. In plain terms, this lets the parent agent bring back the previous
helper instead of starting a fresh one. The resumed subagent keeps the earlier
chat messages and tool results in the same session JSON file under:

```text
.mini_cc/subagents/<name>/sessions/<session-id>.json
```

`subagent_pipeline` runs a conservative multi-subagent strategy. `auto` mode
uses `bench-diagnoser` for benchmark/Docker/results tasks; otherwise it runs:

```text
explorer -> implementer -> verifier
```

`dynamic` mode lets a planner model suggest the subagent plan:

```text
subagent_pipeline {"task": "inspect and verify this project", "mode": "dynamic"}
```

The model is only allowed to suggest a JSON plan. The runtime still checks the
plan before executing it:

- the JSON must contain a `steps` list;
- every step must name an existing subagent;
- the step phase must match that subagent's capabilities;
- requested capabilities must be present on that subagent;
- a `parallel_group` can be all read-only subagents or all isolated write
  subagents;
- a step may declare `dependencies`, for example `["task-1", "task-2"]`;
- dependencies must point to earlier valid task ids, so the scheduler cannot
  deadlock on impossible or circular work;
- if the dynamic plan has no valid executable steps, the runtime falls back to
  the static pipeline.

In plain terms, the model can suggest "who should do what", but local code
still decides whether that suggestion is safe enough to run.

Subagent pipelines now execute through a DAG scheduler. In plain terms, this
means the runtime no longer just walks a list from top to bottom. It builds a
small task graph, finds the nodes whose dependencies are already finished,
claims those nodes, runs them, checks quality gates, then releases or blocks
their dependents. Safe ready parallel groups can run together; later verifier or
reviewer nodes wait for the exact earlier task ids they depend on.

Dependent subagents can also exchange limited peer messages through the
scheduler. A subagent can publish structured lines like:

```text
QUESTION: what still needs checking?
ANSWER: the config is valid
ARTIFACT: config.json
CLAIM: build_status=green
REJECT: implementation misses required verification
```

The scheduler turns those lines into a `mini_cc_peer_v1` packet, records them in
event history, and includes them in the structured handoff for later dependent
nodes. In plain terms, helpers still do not chat freely; the parent scheduler
acts like a meeting host that passes short notes to the helpers who need them.
If two helpers make conflicting `CLAIM key=value` statements, replay records a
contradiction. If a critic says `REJECT:` or `REQUEST_CHANGES:`, the reviewer
quality gate blocks the pipeline.

`subagent_runtime_report` turns the subagent event history into a Runtime v2
report:

```text
subagent_runtime_report {"format": "json"}
subagent_runtime_report {"format": "text"}
```

The report has three practical sections:

- `trace`: the ordered event timeline;
- `metrics`: counts for pipelines, task nodes, gates, worktrees, peer packets,
  conflicts, and rejections;
- `evaluation`: whether the runtime v2 capability checklist is available and
  whether the observed run passed or needs attention.

In plain terms, this is the dashboard for the subagent runtime. It answers
"what happened?", "how much ran?", and "is there a blocker?" without manually
reading every JSONL log.

Subagents can also delegate to another subagent when their allowlist includes
`subagent_run` or `subagent_pipeline`. This is intentionally bounded:

- `--max-nested-subagent-depth` controls how many nested handoffs are allowed;
- `--nested-subagent-token-budget` limits the approximate prompt/task size for
  each nested handoff;
- every nested handoff records `depth`, `max_depth`, and
  `nested_token_budget` in `handoffs.jsonl`.

In plain terms, a helper can ask another helper for help, but it cannot create
an endless chain or pass a huge task blob downward.

Pipeline decisions are written to:

```text
.mini_cc/subagents/pipeline-decisions.jsonl
```

The v2 orchestrator records a capability registry, planner source, and planning
issues for each decision. It selects or filters subagents by capability tags
such as `explore`, `implement`, `verify`, `review`, `benchmark`, and
`diagnose`. Read-only discovery steps are marked with
`parallel_group=read-only-discovery` and can run concurrently through a bounded
parallel runner. Write-capable subagents can also run concurrently when the
group is worktree-isolated, produces diffs, passes file-path conflict detection,
and merges through the parent runtime. Handoffs between phases are structured
JSON blocks rather than free-form appended text. `critic` is selected only for
change-oriented tasks such as fix/edit/implement/refactor requests.
- `--state-dir none`: keep S20 todo/memory/hooks in memory for benchmark runs.

## S20 Features

The S20 mode includes:

- agent loop with tool-use feedback
- Planner / Executor / Verifier workflow records
- file read, list, search, write, replace, and shell tools
- workspace path sandbox
- permission engine
- hooks log in `.mini_cc/hooks.log`
- todo state in `.mini_cc/todos.json`
- structured project memory in `.mini_cc/memory.json`
- local skill listing and reading from `.mini_cc/skills`
- git status and git diff read tools
- context snapshot for long tasks

## Structured Workflow

S20 mode wraps the model/tool loop with a lightweight structured workflow:

- `Planner`: creates a conservative inspect/execute/verify plan before the
  first model call, or validates a model-authored JSON plan in S20 mode;
- `Executor`: classifies each tool call against the active plan;
- `Verifier`: records whether the run had tool failures and whether an
  explicit verification signal ran.

The workflow now also carries a verification policy based on task risk.

In plain terms:

- low-risk read/summarize tasks can finish without a dedicated verification
  step;
- write, Docker, network, package-manager, and benchmark-like tasks require an
  explicit verification signal;
- a verification signal means the agent actually ran a verify-classified tool
  such as `context_snapshot`, `git_diff`, `git_status`, `run_shell`, or
  `subagent_pipeline`;
- if a high-risk task makes changes but never reaches that verification step,
  the verifier marks the run as not OK instead of quietly treating it as good
  enough.

When state is enabled, session JSON files include:

- `planner_plan`;
- `executor_tool_use`;
- `verifier_result`.

The verifier now also records two extra workflow artifacts:

- `evidence_ledger`: the evidence notebook for this run;
- `plan_repair`: the repair checklist when the plan did not land cleanly.

In plain terms:

- `evidence_ledger` answers "what concrete tool evidence do we have?";
- each row records the turn, tool name, plan step, status, evidence kind, and a
  short result summary;
- successful verify-step tools are marked as verification evidence;
- failed tools are marked as failure evidence;
- `plan_repair` answers "if this run is not in a good state, what should be
  repaired next?";
- it records whether repair is needed, why, which planned steps were missed,
  and suggested next actions.

Benchmark-like prompts are marked as `benchmark` mode and require an explicit
verification/report signal before the run is considered verified.

## Runtime Architecture

The S20 teaching implementation has been split into explicit runtime modules:

- `mini_cc.hooks`: lifecycle hooks for `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and `Notification`.
- `mini_cc.session`: persisted session traces under `.mini_cc/sessions`.
- `mini_cc.context`: context snapshot construction.
- `mini_cc.bench`: Terminal-Bench failure classification, shard execution, aggregate scoring, and report generation.

See [docs/architecture.md](docs/architecture.md) for the current architecture and
the remaining gap against Claude Code.

The chapter checkpoint lives in [s20_comprehensive](s20_comprehensive/README.md).
