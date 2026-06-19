# Version History

This file records architecture iterations, benchmark status, and known
validation limits for the teaching agent.

## 3.7.0 - Staged Coding Task State Machine

Date: 2026-06-20

Status: implemented and locally tested.

What changed:

- upgraded package version to `3.7.0`;
- added `mini_cc.task_state.TaskStateMachine`;
- added explicit coding task phases:
  - `INTAKE`;
  - `EXPLORE`;
  - `LOCALIZE`;
  - `PLAN`;
  - `EDIT`;
  - `VERIFY`;
  - `REPAIR`;
  - `FINAL`;
- integrated the state machine into `Agent.run` as a process gate before tool
  execution and before final answers;
- enabled the state machine for CLI/desktop agent construction independently
  from `CodingLoopPolicy`;
- kept `CodingLoopPolicy` as the final code task success gate;
- blocked unsafe coding-process shortcuts:
  - editing before exploration;
  - editing a file that has not been read;
  - editing outside `planned_files`;
  - treating `git_diff`, `context_snapshot`, `echo`, `cat`, or `ls` as code
    verification;
- updated base and S20 prompts to describe the staged coding discipline;
- added direct state-machine tests and Agent integration tests.

Test status:

- targeted task-state, agent, CLI, workflow, S20, and coding-loop tests passed
  locally;
- full `python -m unittest discover` passed locally:
  - 257 tests;
  - `OK`.

Important interpretation:

- this version focuses on process reliability, not on adding more tools;
- MCP, subagent, memory, frontend, and benchmark-report modules were not
  expanded in this change;
- the state machine improves coding task discipline, but it is still a local
  teaching-runtime mechanism rather than a claim of external benchmark parity.

## 3.6.1 - Verification Semantics Cleanup

Date: 2026-06-20

Status: implemented and locally tested.

What changed:

- upgraded package version to `3.6.1`;
- split Runtime Evidence from Code Verification in workflow verification
  results;
- stopped treating `git_status`, `git_diff`, `context_snapshot`, and similar
  inspection tools as code verification;
- reused `CodingLoopPolicy` verification command detection in workflow/report
  logic;
- added workflow and agent-level tests for:
  - `run_shell("git status")` and `run_shell("echo ok")` not counting as code
    verification;
  - `python -m unittest discover` counting as code verification;
  - changed code plus only `git_diff` or `context_snapshot` still being blocked
    by the coding loop;
- updated README and coding loop docs to explain the two-layer verification
  model.

Test status:

- targeted workflow and coding-loop tests passed locally;
- full `python -m unittest discover` passed locally:
  - 245 tests;
  - `OK`.

Important interpretation:

- Runtime Evidence proves what the agent inspected or collected.
- Code Verification proves that changed code passed a real test/check command.
- For code modification tasks, `CodingLoopPolicy` remains the source of truth
  for task-success verification.

## 3.6 - Coding Task Success Loop

Date: 2026-06-20

Status: implemented and locally tested.

What changed:

- upgraded package version to `3.6.0`;
- added `apply_patch`, a workspace-safe unified diff editing tool with:
  - `dry_run` validation;
  - path escape protection;
  - multi-file patch support;
  - existing permission and `FileChanged` hook integration;
- added `mini_cc.coding_loop.CodingLoopPolicy` and `CodingTaskState`;
- added runtime gating before final answers:
  - code edits without a real verification command cannot finish;
  - failed verification forces a minimal repair prompt until the repair limit;
  - passed verification allows final report;
  - max turns writes `max_turns_reached`;
- narrowed code usability verification:
  - `git_status`, `git_diff`, `context_snapshot`, and similar tools are evidence only;
  - only real test/check commands through `run_shell` count as verification;
- added `.mini_cc/task-success/last-run.json` as an independent task success artifact;
- added lightweight task-success eval scaffold:
  - `python -m mini_cc.evals.task_success`;
  - writes `task-success-eval.json`;
  - covers small deterministic Python repair cases.

Test status:

- targeted coding loop, workflow, and task-success eval tests passed locally;
- full `python -m unittest discover` passed locally:
  - 238 tests;
  - `OK`;
- `python -m mini_cc.evals.task_success --output-dir .mini_cc\task_success_eval_smoke` passed:
  - 3 total cases;
  - 3 passed cases;
  - pass rate `1.0`.

Important interpretation:

- this is not a SWE-bench or Terminal-Bench autonomous score;
- the eval is a smoke test for the local task-success loop;
- it improves coding reliability by forcing verification, but does not make the
  project product-level Claude Code parity.

## 3.5 - External Benchmark and Demo Packaging

Date: 2026-06-19

Status: implemented and locally tested.

What changed:

- upgraded package version to `3.5.0`;
- added 3.4 reliability hardening for subagent/context runtime:
  - write-capable parallel merges now require diff/evidence/verification;
  - semantic merge verifier reports adjacent-line, same-symbol, and same-config-key conflicts;
  - plan/human/implementation/verification/merge/reviewer gates all emit quality-gate workflow events;
  - replay output now includes `resume_state` for pipelines, task graphs, nodes, subagents, handoffs, and gates;
  - context source priority is explicit: user instructions > durable memory > recent session facts > tool summaries > compressed conversation > workspace > model inference.
- added 3.5 external review package:
  - `scripts/mock_demo.ps1`;
  - `scripts/tool_use_eval.ps1`;
  - `scripts/runtime_report.ps1`;
  - `scripts/terminal_bench_smoke.ps1`;
  - `scripts/mcp_hook_live_validation.ps1`;
  - `examples/hooks`;
  - `examples/mcp`;
  - `examples/subagents`;
  - `examples/parallel_writer`;
  - `examples/permission_policy`;
  - `CLIENT_README_zh.md`.

Test status:

- targeted 3.4 regression tests passed on Python 3.10;
- full `unittest discover` should be run with the Python 3.10 command recorded in `README.md`;
- Terminal-Bench smoke script is a dry-run/preflight artifact by default, not a full external benchmark score.

Important interpretation:

- this version improves evidence and packaging quality;
- it still should not be described as product-level Claude Code parity;
- the earlier SWE-bench `97.18%` number was gold-patch completed-sample resolved rate, not this agent's autonomous SWE-bench score.

## 3.3 - MCP / Hook Live Validation

Date: 2026-06-19

### Version Scope

Version `3.3` adds a local live validation runner for MCP transports, MCP
failure/auth behavior, OAuth refresh persistence, and hook trust profiles.

Plain-language summary:

- before: MCP and hook support mostly proved that the sockets and switches
  existed;
- now: the validation command starts or connects to controlled local endpoints,
  calls tools/resources/prompts, triggers hook handlers, writes a real
  `hooks.log`, and reports exactly what passed.

New CLI command:

```powershell
py -3 -m mini_cc --workspace . --mcp-hook-live-validation .mini_cc\mcp-hook-live-3.3
```

Generated artifacts:

- `mcp-hook-live-validation.json`;
- `mcp-hook-live-validation.md`;
- `hooks.log`;
- `oauth-token-store.json`;
- local stdio MCP server script used by the smoke.

Validation coverage:

- stdio MCP smoke;
- HTTP MCP smoke;
- SSE MCP smoke;
- WebSocket MCP smoke;
- disconnect classification;
- HTTP 401 / expired token classification;
- missing token classification;
- insufficient scope / 403 classification;
- HTTP 500 classification;
- OAuth refresh persistence into token store;
- hook live trace for:
  - `SessionStart`;
  - `UserPromptSubmit`;
  - `PreToolUse`;
  - `PostToolUse`;
  - `Stop`;
  - `SessionEnd`;
- hook trust profiles:
  - command/local script hook;
  - HTTP hook;
  - MCP hook;
  - prompt hook;
  - agent hook.

Important files:

- `mini_cc.mcp_live`: live validation runner and local smoke servers;
- `mini_cc.cli`: new `--mcp-hook-live-validation` command;
- `tests.test_mcp_live`: verifies local transports, failure/auth refresh, and
  hook trace coverage;
- `tests.test_cli`: CLI argument coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted live validation / CLI tests:

```text
Ran 77 tests in 17.825s
OK
```

Project live validation smoke:

```text
py -3 -m mini_cc --mock --workspace . --mcp-hook-live-validation .mini_cc\mcp-hook-live-3.3
```

Observed result:

```text
Status: ready
Score: 6/6 (100.00%)
```

Observed hook trace includes:

```text
UserPromptSubmit
SessionStart
PreToolUse
PostToolUse
Stop
SessionEnd
```

Full test suite:

```text
Ran 207 tests in 20.802s
OK
```

### Beginner Explanation

3.3 is the difference between:

```text
The code says it can connect to MCP.
```

and:

```text
We started local MCP endpoints, connected to them, listed tools/resources/prompts,
called a tool, forced auth/server failures, refreshed a token, and recorded real
hook events.
```

This is still local smoke validation, not a claim that every internet MCP
server works. Its value is that the core runtime paths are now exercised by a
real command and a reproducible report.

## 3.2 - Real Tool-Use Trace Runner

Date: 2026-06-19

### Version Scope

Version `3.2` changes tool-use evaluation from "score a prefilled observation
fixture" to "run a small deterministic local agent/tool path, record the real
tool trace, then score that trace".

Plain-language summary:

- before: the default tool-use eval could pass because it loaded built-in
  passing observations;
- now: `--tool-use-eval` runs each scenario, records which tools were exposed,
  which tools were called, what parameters were used, which calls failed, and
  what evidence grounded the final answer;
- the built-in observations still exist for unit tests and compatibility, but
  the CLI default uses real local traces.

New behavior:

- `ToolRunner.run()` emits real `PreToolUse` and `PostToolUse` hook events;
- new `RealToolUseTraceRunner`;
- new per-scenario trace files under `traces/*.json`;
- new aggregate `tool-use-trace.json`;
- `tool-use-eval.md` now includes an "Observed Tool Calls" section;
- `--tool-use-eval-input` still lets a caller score an external observation
  file, but omitting it runs real local traces.

Important files:

- `mini_cc.tool_eval`: real trace runner, scripted local provider, trace
  recorder, per-scenario trace writer;
- `mini_cc.tools`: PreToolUse/PostToolUse hook emission around actual tool
  execution;
- `mini_cc.cli`: `--tool-use-eval` now calls real trace eval by default;
- `tests.test_tool_eval`: verifies real traces pass without using the built-in
  observations and writes ten scenario trace files;
- `tests.test_tools`: updated expectations for PreToolUse/PostToolUse events;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted eval/hooks/CLI tests:

```text
Ran 67 tests in 1.680s
OK
```

Project tool-use eval smoke:

```text
py -3 -m mini_cc --mock --workspace . --tool-use-eval .mini_cc\tool-use-eval-3.2
```

Observed result:

```text
Total scenarios: 10
Passed: 10
Score: 100.00%
Per-scenario trace files: 10
```

Full test suite:

```text
Ran 204 tests in 13.417s
OK
```

### Beginner Explanation

3.2 is the difference between these two situations:

```text
Old: I already wrote down "the agent used read_file", so the evaluator scores
     that note.

New: The agent actually runs the scenario, calls read_file, the runner records
     that call, and then the evaluator scores the recorded trace.
```

That makes tool-use evaluation harder to fake and easier to debug. If a future
change picks the wrong tool, sends the wrong parameter, bypasses permission, or
fails to produce grounding evidence, the report shows the failed check and the
actual calls that caused it.

## 3.15 - Runtime Evidence Smoke

Date: 2026-06-19

### Version Scope

Version `3.15` improves the parts that were not 100% in the `3.1` evidence
report. The fix is not to fake the score. Instead, it adds a real local smoke
path that materializes the missing artifacts, then lets the normal report read
them.

Plain-language summary:

- before: the report correctly said `needs_evidence` because MCP registry,
  capability index, hooks log, and tool-use trace were missing;
- now: one command can generate those local evidence artifacts;
- after the smoke, the normal runtime report reaches 100% because the artifacts
  really exist and are inspectable.

New CLI behavior:

```powershell
py -3 -m mini_cc --workspace . --tool-runtime-evidence-smoke --tool-runtime-report .mini_cc\tool-runtime-report-3.15
```

Generated evidence:

- `.mini_cc/mcp-registry.json`;
- `.mini_cc/hooks.log`;
- `.mini_cc/tool-use-eval/tool-use-trace.json`;
- `.mini_cc/tool-use-eval/tool-use-eval.json`;
- `.mini_cc/tool-use-eval/tool-use-eval.md`.

Important implementation details:

- MCP evidence uses a local in-memory MCP server and the real
  `SubagentRuntime.build_mcp_registry()` path;
- hook evidence uses `HookRuntime` to emit the broad lifecycle events required
  by the report;
- tool-use evidence runs real local `ToolRunner` calls for filesystem,
  permission, search, read, and hook-block scenarios;
- MCP auth/server failure rows use the local classifiers, not a remote server,
  and the trace notes this explicitly.

Important files:

- `mini_cc.tool_runtime`: new `write_tool_runtime_evidence_smoke()` and helper
  smoke writers;
- `mini_cc.cli`: new `--tool-runtime-evidence-smoke` option;
- `tests.test_tool_runtime`: verifies that an empty workspace is not 100%, then
  reaches 100% after smoke evidence;
- `tests.test_cli`: CLI argument coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted Tool Runtime / CLI tests:

```text
Ran 26 tests in 0.060s
OK
```

Full test suite:

```text
Ran 203 tests in 13.083s
OK
```

3.15 smoke report on the project workspace:

```text
Status: ready
Production-ready score: 11/11 (100.00%)
Evidence score: 55/55 (100.00%)
```

### Beginner Explanation

3.1 changed the report into a strict inspector. It said:

```text
You have the code, but I do not see the proof files yet.
```

3.15 adds a controlled local test run that creates those proof files. After
that, the report can say:

```text
I see the MCP registry.
I see the capability index.
I see the hook events.
I see the tool-use trace.
Now this local smoke evidence is complete.
```

## 3.1 - Evidence-Gated Runtime Report

Date: 2026-06-19

### Version Scope

Version `3.1` fixes the main weakness of `3.0`: the runtime report no longer
counts a capability as fully ready just because the code path exists.

Plain-language summary:

- before: "the module exists, so the report can look complete";
- now: "the module exists, the config/artifact exists, a real run observed it,
  tests cover it, and only then it becomes production-ready";
- missing evidence is shown directly in the report with a next action.

New behavior:

- report schema version is now `3.1`;
- each capability now has five evidence states:
  - `implemented`: code exists;
  - `configured`: config or artifact exists;
  - `observed`: a real runtime artifact shows it happened;
  - `tested`: tests cover it;
  - `production_ready`: key evidence gates are satisfied;
- report summary now includes:
  - `implemented`;
  - `configured`;
  - `observed`;
  - `tested`;
  - `production_ready`;
  - `evidence_points`;
  - `max_evidence_points`;
  - `score`;
  - `evidence_score`;
- report artifacts now include `tool_use_trace`;
- missing evidence is recorded in `missing_evidence`;
- suggested fixes are recorded in `remediation`.

Important behavior change:

- missing `.mini_cc/mcp-registry.json` means MCP registry is only
  `implemented`, not `observed`;
- missing `capability_index` means MCP health/capability index is not
  production-ready;
- missing or empty `.mini_cc/hooks.log` means broad event coverage is not
  fully ready;
- missing tool-use trace means tool-use benchmark is not production-ready even
  if a built-in eval artifact exists.

Important files:

- `mini_cc.tool_runtime`: evidence-gated capability states, missing evidence,
  remediation, evidence score;
- `tests.test_tool_runtime`: coverage for empty evidence, complete evidence,
  JSON/Markdown output;
- `README.md` and `docs/architecture.md`: updated explanation.

### Real Test Status

Targeted Tool Runtime tests:

```text
Ran 3 tests in 0.017s
OK
```

Tool Runtime plus CLI tests:

```text
Ran 24 tests in 0.031s
OK
```

Full test suite:

```text
Ran 201 tests in 13.229s
OK
```

3.1 smoke report:

```text
Status: needs_evidence
Production-ready score: 7/11 (63.64%)
Evidence score: 44/55 (80.00%)
```

### Beginner Explanation

Think of 3.1 as changing the report from a "feature checklist" into an
"inspection checklist".

If a car has a brake pedal, that only means the brake is implemented. It is not
enough. You also want to know:

```text
Is it connected?        configured
Did we see it stop?     observed
Was it tested?          tested
Can we trust it?        production_ready
```

The agent report now works the same way.

## 3.0 - Tool-Use Runtime v3

Date: 2026-06-19

### Version Scope

Version `3.0` closes the MCP, hooks, tool governance, evaluation, and failure
recovery work into one Tool-Use Runtime v3 reporting layer.

Plain-language summary:

- 2.x added many separate tool capabilities;
- 3.0 adds the dashboard that shows those capabilities together;
- this makes the tool layer easier to review, compare, and hand to someone
  checking the project.

New behavior:

- new module: `mini_cc.tool_runtime`;
- new CLI command:

```powershell
py -3 -m mini_cc --workspace . --tool-runtime-report .mini_cc\tool-runtime-report
```

- output artifacts:
  - `tool-runtime-report.json`;
  - `tool-runtime-report.md`;
- report schema version: `3.0`;
- report status: `ready` when the capability checklist is implemented;
- report reads optional existing artifacts:
  - `.mini_cc/mcp-registry.json`;
  - `.mini_cc/hooks.log`;
  - `.mini_cc/**/tool-use-eval.json`;
- report includes capability status for:
  - MCP registry;
  - MCP health and capability index;
  - dynamic tool retrieval;
  - tool description quality governance;
  - resources/prompts governance;
  - auth/secret governance;
  - hardened hooks;
  - broad event coverage;
  - tool-use benchmark;
  - failure recovery;
  - runtime tool report.

Important files:

- `mini_cc.tool_runtime`: Tool-Use Runtime v3 report builder and Markdown
  renderer;
- `mini_cc.cli`: `--tool-runtime-report`;
- `tests.test_tool_runtime`: capability checklist, artifact reading, report
  output coverage;
- `tests.test_cli`: CLI argument coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted Tool Runtime / CLI tests:

```text
Ran 24 tests in 0.028s
OK
```

CLI smoke test:

```text
python -m mini_cc --workspace . --tool-runtime-report .mini_cc\tool-runtime-report-smoke
```

Output:

```json
{
  "tool_runtime_report_json": "C:\\Users\\sixth\\mini-claude-code\\.mini_cc\\tool-runtime-report-smoke\\tool-runtime-report.json",
  "tool_runtime_report_markdown": "C:\\Users\\sixth\\mini-claude-code\\.mini_cc\\tool-runtime-report-smoke\\tool-runtime-report.md"
}
```

Full test suite:

```text
Ran 201 tests in 13.532s
OK
```

### Beginner Explanation

Think of 3.0 as the "tool layer inspection report".

Before this version, the project had many separate parts:

```text
MCP registry
hook runtime
tool-use benchmark
failure recovery
auth governance
resource governance
```

But a reviewer would still need to search through code and docs to understand
whether they are present.

Now one command produces a report:

```text
Does MCP registry exist?                 yes
Does tool retrieval exist?               yes
Are hook events broad enough?            yes
Can tool failures recover?               yes
Is there a tool-use benchmark?           yes
What evidence/artifacts were found?      listed in the report
```

This does not mean the whole agent is now equal to Claude Code. It means the
tool-use runtime has a coherent v3 surface that can be inspected and improved
as one layer.

## 2.9 - Tool Failure Recovery and Alternative Routing

Date: 2026-06-19

### Version Scope

Version `2.9` adds a runtime recovery layer for failed tool calls.

Plain-language summary:

- before this version, a failed tool mostly returned an error string;
- now the runtime can ask: why did it fail?
- then it can decide whether to retry, switch to a safer alternative tool, or
  record degraded mode.

New behavior:

- new module: `mini_cc.tool_recovery`;
- failure classifier categories:
  - `permission_denied`;
  - `hook_blocked`;
  - `parameter_error`;
  - `not_found`;
  - `path_escape`;
  - `timeout`;
  - `transient_network`;
  - `mcp_auth_failure`;
  - `mcp_server_failure`;
  - `unknown_tool`;
  - `unknown`;
- retry with backoff for retryable categories such as timeout, transient
  network/server failures, and MCP server failures;
- alternative tool routing for safe local cases:
  - failed `read_file` can route to `list_files`;
  - failed `search_text` can route to `list_files`;
  - failed `replace_text` can route to `read_file`;
- degraded mode records unresolved recoverable failures without pretending the
  tool succeeded;
- every recovery attempt writes `ToolResult.metadata["recovery"]`;
- recovery metadata includes:
  - schema version;
  - classified failure;
  - whether recovery happened;
  - recovered-by method;
  - degraded flag;
  - recovery trace;
  - post-failure verifier result;
- S20 mode enables the default recovery policy;
- plain `ToolRunner` can opt in with `ToolRecoveryPolicy`.

Safety rule:

- permission denials and hook blocks are not retried, bypassed, or degraded;
- the post-failure verifier marks those as safe stops when the block was
  respected.

Important files:

- `mini_cc.tool_recovery`: classifier, retry, alternative routing, degraded
  mode, trace, verifier;
- `mini_cc.tools`: optional recovery policy integration in `ToolRunner`;
- `mini_cc.s20`: default recovery policy enabled for S20;
- `tests.test_tool_recovery`: direct coverage for classification, retry,
  alternative routing, degraded mode, and permission safety;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted recovery tests:

```text
Ran 5 tests in 0.010s
OK
```

Related runtime tests:

```text
Ran 79 tests in 2.656s
OK
```

Full test suite:

```text
Ran 197 tests in 13.562s
OK
```

### Beginner Explanation

This version teaches the agent not to treat every tool error the same.

Example:

```text
read_file("missing/README.md") failed
```

That might not mean the task is impossible. It may mean the file path is wrong.
So the recovery layer can try:

```text
list_files(".")
```

and give the model useful information for the next step.

But if the failure is:

```text
Permission denied
```

then the agent must not "recover" by bypassing the policy. It records the block
as a safe stop.

In short:

```text
temporary failure -> retry
wrong target      -> try a safer alternative tool
permission/hook   -> stop and respect the block
still broken      -> record degraded mode with trace
```

## 2.8 - Tool-use Evaluation Harness

Date: 2026-06-19

### Version Scope

Version `2.8` adds a dedicated tool-use evaluation harness.

Plain-language summary:

- Terminal-Bench asks: can the agent finish the task?
- 2.8 asks a smaller but important question: did the agent use tools correctly?
- This helps separate "model reasoning failure" from "tool discovery/selection
  failure".

New behavior:

- new module: `mini_cc.tool_eval`;
- built-in tool-use scenario set covering:
  - tool discovery;
  - tool selection;
  - parameter correctness;
  - permission compliance;
  - hook intervention;
  - MCP auth recovery;
  - MCP server failure recovery;
  - prompt injection resistance;
  - tool bloat control;
  - result grounding;
- each scenario has expected tools, forbidden tools, expected parameters, or
  required runtime signals;
- observations can be loaded from JSON for later real trace evaluation;
- built-in passing observations are included for harness self-checks;
- report output includes:
  - `tool-use-eval.json`;
  - `tool-use-eval.md`;
  - `tool-use-scenarios.json`;
- new CLI command:

```powershell
py -3 -m mini_cc --tool-use-eval .mini_cc\tool-use-eval
```

Important files:

- `mini_cc.tool_eval`: scenarios, observations, evaluator, report renderer;
- `mini_cc.cli`: `--tool-use-eval` and `--tool-use-eval-input`;
- `tests.test_tool_eval`: scenario coverage, pass/fail scoring, report output;
- `tests.test_cli`: CLI argument coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted tool-use/CLI tests:

```text
Ran 25 tests in 0.026s
OK
```

CLI smoke test:

```text
python -m mini_cc --tool-use-eval .mini_cc\tool-use-eval-smoke
```

Output:

```json
{
  "tool_use_eval_json": ".mini_cc\\tool-use-eval-smoke\\tool-use-eval.json",
  "tool_use_eval_markdown": ".mini_cc\\tool-use-eval-smoke\\tool-use-eval.md",
  "tool_use_scenarios": ".mini_cc\\tool-use-eval-smoke\\tool-use-scenarios.json"
}
```

Full test suite:

```text
Ran 192 tests in 13.406s
OK
```

### Beginner Explanation

Think of this as a driving test for tool use.

Terminal-Bench is like asking:

```text
Did the driver reach the destination?
```

The 2.8 harness asks:

```text
Did the driver choose the right road?
Did they obey stop signs?
Did they avoid dangerous shortcuts?
Did they use the map correctly?
```

For an agent, that means:

```text
Did it find the right tool?
Did it choose the right tool?
Did it pass the right parameters?
Did it respect permission and hook blocks?
Did it recover from MCP auth/server errors?
Did it avoid prompt injection?
Did it avoid showing too many tools at once?
Did it ground the final answer in tool results?
```

This does not yet prove the agent is good at every real task. It gives us a
focused tool-use scoreboard so later benchmark failures are easier to diagnose.

## 2.7 - Hook Policy and Event Coverage

Date: 2026-06-19

### Version Scope

Version `2.7` makes the hook event catalog participate in real runtime paths.

Plain-language summary:

- before this version, some hook events were already defined in the catalog;
- but a defined event is only useful if the agent actually fires it while
  working;
- 2.7 connects key events to prompt submission, instruction loading, config
  loading, file writes, todos, session shutdown, and subagent worktrees.

New behavior:

- `Agent.run()` emits `UserPromptSubmit` before planning/model execution;
- prompt hooks may rewrite the submitted prompt through `payload_updates.prompt`;
- `Agent.run()` emits `SessionEnd` on normal completion, exception, and
  max-turn stop;
- `Agent.run()` emits `StopFailure` if a `Stop` hook blocks/fails closed;
- `system_prompt_for_workspace()` emits `InstructionsLoaded` when `AGENTS.md`
  is loaded;
- `load_hooks_file()` emits `ConfigChange` after hook settings are loaded;
- `ToolRunner.write_file()` and `ToolRunner.replace_text()` emit
  `FileChanged` after successful writes;
- `S20ToolRunner.todo_write()` emits `TaskCreated` for todos and
  `TaskCompleted` for completed todos;
- `SubagentRuntime.run()` emits `WorktreeCreate` for isolated write-capable
  subagent worktrees;
- `SubagentRuntime.remove_worktree()` removes an isolated worktree and emits
  `WorktreeRemove`;
- task graph nodes emit `TaskCreated` when recorded and `TaskCompleted` when
  released as completed.

Important files:

- `mini_cc.agent`: prompt, session-end, stop-failure lifecycle emission;
- `mini_cc.cli`: instruction-loading hook emission;
- `mini_cc.hooks`: helper methods for instructions, stop failure, worktree,
  file, and config events;
- `mini_cc.tools`: `FileChanged` after successful file writes;
- `mini_cc.s20`: task events from todo writes;
- `mini_cc.subagents`: worktree and task-graph hook coverage;
- `tests.test_runtime_modules`: runtime event coverage tests.

### Real Test Status

Targeted runtime-module tests:

```text
Ran 25 tests in 1.525s
OK
```

Full test suite:

```text
Ran 186 tests in 13.489s
OK
```

### Beginner Explanation

A hook event is like a sensor point.

Before 2.7, we had a list saying:

```text
there can be a FileChanged event
there can be a SessionEnd event
there can be a ConfigChange event
```

But that is not enough. The agent must actually press those buttons while it
runs.

Now the buttons are connected:

```text
user submits prompt        -> UserPromptSubmit
AGENTS.md is read          -> InstructionsLoaded
hook config is loaded      -> ConfigChange
file is written/replaced   -> FileChanged
todo is created/completed  -> TaskCreated / TaskCompleted
subagent starts/stops      -> SubagentStart / SubagentStop
worktree is created        -> WorktreeCreate
session ends               -> SessionEnd
stop hook fails/blocks     -> StopFailure
```

This improves observability and policy control. In simple terms: later we can
write hooks that react to real agent behavior instead of only reacting to tool
calls.

## 2.6 - Hook Runtime Hardening

Date: 2026-06-19

### Version Scope

Version `2.6` makes configured hooks controllable, recoverable, and observable.

Plain-language summary:

- hooks are automation switches;
- if a hook hangs, crashes, returns nonsense, or prints huge output, it should
  not silently break the whole agent;
- 2.6 gives hooks timeout, retry, failure mode, output limits, spill files, and
  metrics.

New behavior:

- configured hooks support `timeout`;
- configured hooks support `retries`;
- configured hooks support `failure_mode`:
  - `fail-closed`: hook failure blocks the action;
  - `fail-open`: hook failure is recorded but the action continues;
- hook result JSON is schema-validated;
- invalid hook JSON is treated as a controlled hook failure;
- configured hooks support `max_output_chars`;
- oversized hook output is truncated;
- large output can be spilled to a file under a hook spill directory;
- configured hooks support `additionalContext`;
- `HookRuntime.hook_metrics()` reports:
  - emitted event count;
  - configured hook attempts;
  - successes;
  - failures;
  - blocks;
  - retries;
  - spills;
  - duration totals;
  - per-event and per-source summaries.

Important files:

- `mini_cc.hooks`: hook timeout/retry/failure-mode/output/metrics hardening;
- `tests.test_runtime_modules`: fail-open, retry, timeout, additionalContext,
  spill-to-file, schema validation, and metrics coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted runtime-module tests:

```text
Ran 19 tests in 1.387s
OK
```

Full test suite:

```text
Ran 180 tests in 13.262s
OK
```

### Beginner Explanation

A hook is like an automatic switch:

```text
before running a shell command -> call a hook -> hook says allow/block/change input
```

Before 2.6, the switch could run, but it was not hardened enough. If the hook
script hung, printed too much, or returned bad JSON, the behavior was too easy
to make brittle.

Now each configured hook can say:

```json
{
  "type": "command",
  "command": "python scripts/check.py",
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

- `timeout`: do not wait forever;
- `retries`: try again if the hook itself failed;
- `fail-open`: if the hook breaks, keep going;
- `fail-closed`: if the hook breaks, block;
- `max_output_chars`: do not let a hook flood the agent;
- `additionalContext`: pass extra local policy/context to the hook;
- metrics: know which hooks are slow, failing, blocking, or spilling output.

## 2.5 - MCP Auth and Secret Governance v2

Date: 2026-06-19

### Version Scope

Version `2.5` unifies MCP authentication and secret governance.

Plain-language summary:

- external MCP servers often need login;
- login creates tokens, refresh tokens, account identity, and headers;
- 2.5 makes those pieces visible to governance without leaking the secret
  values.

New behavior:

- MCP registry schema is now `2.5`;
- new `MCPTokenStore` JSON store for OAuth state;
- Streamable HTTP MCP can load persisted OAuth tokens from `token_store`;
- OAuth login and refresh now persist token responses when a token store is
  configured;
- device-code login can be resumed from a pending token-store entry;
- account profile metadata is stored with the token record;
- env-var auth can be limited with `env_var_allowlist`;
- auth failures are classified into structured categories such as:
  - `oauth_metadata_required`;
  - `expired_token`;
  - `missing_token`;
  - `insufficient_scope`;
  - `refresh_failed`;
- auth failures can carry a re-auth prompt;
- registry auth metadata records token-store and account-profile status without
  writing token values.

Important files:

- `mini_cc.mcp`: token store, refresh persistence, device-flow resume, auth
  failure classification, redacted token profiles;
- `mini_cc.subagents`: MCP auth config loading, env-var allowlist, registry
  auth metadata;
- `tests.test_mcp`: token persistence, device-flow resume, auth failure
  classification;
- `tests.test_subagents`: env allowlist and account profile config coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted MCP tests:

```text
Ran 27 tests in 9.101s
OK
```

Targeted subagent tests:

```text
Ran 54 tests in 2.235s
OK
```

Full test suite:

```text
Ran 175 tests in 12.734s
OK
```

Note: the first full run had one timing-threshold flaky failure in an unrelated
parallel subagent test (`0.353s < 0.35s`). A clean rerun passed the full suite.

### Beginner Explanation

Before 2.5, the agent could log in to an MCP server, but the login state mostly
lived inside the running adapter.

Now the auth flow has a simple memory box:

```text
.mini_cc/mcp-tokens.json or another configured token_store path
```

That box can remember:

```text
account profile
access token
refresh token
pending device-code login
```

The important part: normal reports and audit views do not print the secrets.
They show status, hashes, and account labels, not raw tokens.

If a remote MCP server says "401 unauthorized", the adapter now classifies the
problem. For example:

```text
oauth_metadata_required
expired_token
insufficient_scope
refresh_failed
```

That gives the agent a better next step than just saying "HTTP 401".

## 2.4 - MCP Resource and Prompt Governance

Date: 2026-06-19

### Version Scope

Version `2.4` expands MCP governance beyond tools.

Plain-language summary:

- MCP is not only "call a tool";
- an MCP server can also expose resources, like documents or context files;
- it can also expose prompts, like reusable templates;
- 2.4 makes resource reads and prompt fetches go through policy, audit, cache,
  and version checks.

New behavior:

- MCP registry schema is now `2.4`;
- registry now includes a top-level governance summary;
- resource catalog entries include governance metadata:
  - whether policy allows the read;
  - policy reason;
  - cache enabled/cached state;
  - sensitive resource detection;
  - content preview availability after read;
- prompt catalog entries include governance metadata:
  - whether policy allows the get;
  - policy reason;
  - prompt version pin state;
  - metadata version hash;
  - content preview availability after get;
- `GovernedMCPAdapter.read_resource()` now supports resource caching;
- resource read audit rows record cache hit, sensitivity, content hash, length,
  and safe preview;
- sensitive resource previews are redacted;
- `GovernedMCPAdapter.get_prompt()` now pins prompt content version on first
  successful get and blocks later unexpected drift;
- prompt get audit rows record prompt version, argument hash, and mismatch
  status.

Important files:

- `mini_cc.mcp`: resource cache, sensitive detection, prompt version pinning,
  resource/prompt audit details;
- `mini_cc.subagents`: registry governance metadata for resources and prompts;
- `tests.test_mcp`: cache, sensitivity audit, and prompt drift tests;
- `tests.test_subagents`: registry governance coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted MCP tests:

```text
Ran 24 tests in 7.416s
OK
```

Targeted subagent tests:

```text
Ran 52 tests in 2.211s
OK
```

Full test suite:

```text
Ran 170 tests in 10.782s
OK
```

### Beginner Explanation

Before this version, MCP governance focused mostly on tools.

But MCP servers can also provide:

```text
resources = files, docs, notes, remote context
prompts   = reusable prompt templates
```

Those can be risky too. A resource might contain secrets. A prompt template
might silently change after the agent already trusted it.

Now resource reads are treated like controlled access:

```text
check policy -> read or block -> cache result -> audit preview safely
```

And prompt fetches are pinned:

```text
first get: remember prompt hash
later get: block if content changed unexpectedly
```

So the agent is not only asking "may I call this tool?" It also asks "may I read
this resource?" and "is this prompt still the version I trusted?"

## 2.35 - Real MCP Tool Vector Index

Date: 2026-06-19

### Version Scope

Version `2.35` connects dynamic tool retrieval to a real local vector index.

Plain-language summary:

- 2.3 could rank tools by words in the task and tool description;
- 2.35 now also writes a real vector file at
  `.mini_cc/mcp-tool-vectors.json`;
- retrieval turns the user task into a vector, compares it with tool vectors,
  and uses that similarity together with lexical scoring.

New behavior:

- registry schema is now `2.35`;
- MCP registry now records vector index metadata;
- new `.mini_cc/mcp-tool-vectors.json`;
- new `SubagentRuntime.build_mcp_tool_vector_index()`;
- new S20 tool: `subagent_mcp_vector_index`;
- `subagent_mcp_tool_retrieval` now defaults to hybrid ranking:
  - lexical score;
  - vector score;
  - combined relevance score;
- retrieval output now includes `embedding_retrieval.enabled=true` when the
  vector index is active.

Important files:

- `mini_cc.subagents`: local hashing embeddings, persisted vector index, hybrid
  vector/lexical ranking;
- `mini_cc.s20`: exposes `subagent_mcp_vector_index`;
- `tests.test_subagents`: vector index persistence and hybrid retrieval tests;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 52 tests in 2.233s
OK
```

Full test suite:

```text
Ran 168 tests in 11.049s
OK
```

### Beginner Explanation

Think of 2.3 as "searching tool labels by keywords".

2.35 adds a second layer: every tool label is converted into a row of numbers,
called a vector. Similar meanings should land closer together than unrelated
tools. The runtime stores those rows here:

```text
.mini_cc/mcp-tool-vectors.json
```

When the task is:

```text
installation docs
```

the runtime also turns that task into a vector and compares it with the tool
vectors. The final ranking now uses both:

```text
lexical_score + vector_score -> relevance_score
```

This version uses a deterministic local hashing embedding called
`mini_cc_hashing_v1`. It is a real persisted vector index, but it is not a paid
external embedding model and does not require network access or an API key.

## 2.3 - Dynamic Tool Retrieval

Date: 2026-06-19

### Version Scope

Version `2.3` adds dynamic MCP tool retrieval.

Plain-language summary:

- before this version, a subagent could be shown every MCP tool schema it was
  allowed to use;
- when the tool list grows, those schemas waste context and make the model more
  likely to pick a wrong tool;
- 2.3 first builds a tool index, then ranks tools by the current task text, and
  exposes only the most relevant MCP tool schemas by default.

New behavior:

- registry schema is now `2.3`;
- `.mini_cc/mcp-registry.json` now includes `tool_index`;
- new `SubagentRuntime.retrieve_mcp_tools()`;
- new S20 tool: `subagent_mcp_tool_retrieval`;
- restricted subagent tool schema exposure now supports:
  - lexical retrieval;
  - `top_k` MCP tool schema exposure;
  - subagent visibility filtering;
  - schema token estimates;
  - second-pass expansion with `expand=true`;
- embedding retrieval is represented as an optional future path, but is not
  enabled in this local deterministic version.

Important files:

- `mini_cc.subagents`: MCP tool index, lexical ranking, top-k schema exposure;
- `mini_cc.s20`: exposes `subagent_mcp_tool_retrieval`;
- `tests.test_subagents`: retrieval ranking and top-k schema exposure tests;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 51 tests in 2.160s
OK
```

Full test suite:

```text
Ran 167 tests in 10.519s
OK
```

### Beginner Explanation

Think of MCP tools as a huge toolbox.

Old behavior was like dumping every tool on the table before every task. That
works when there are only two or three tools, but becomes messy when there are
dozens.

Now the runtime does this first:

```text
task: "find install docs"
top tool: mcp__quality__search_docs
hidden for this turn: ticket creation, delete tools, unrelated tools
```

This does not weaken permissions. A subagent still cannot use a tool outside
its allowlist. It only changes which allowed tool descriptions the model sees
first, so the prompt is smaller and the model has fewer irrelevant choices.

If the first small tool basket is not enough, the caller can request a second
pass with:

```text
subagent_mcp_tool_retrieval {"query": "find install docs", "expand": true}
```

## 2.2 - MCP Tool Description Quality Layer

Date: 2026-06-19

### Version Scope

Version `2.2` adds a quality layer for MCP tool descriptions.

Plain-language summary:

- the model chooses tools mainly by reading tool names, descriptions, and
  schemas;
- if a tool description is vague, too short, or misleading, the model is more
  likely to choose the wrong tool;
- 2.2 gives each MCP tool a deterministic local quality check inside the MCP
  registry.

New behavior:

- registry schema is now `2.2`;
- each MCP tool entry now includes a `quality` object;
- quality includes:
  - `score`;
  - `warnings`;
  - `missing_fields`;
  - `purpose`;
  - `input_constraints`;
  - `risk_notes`;
  - `example_input`;
  - `example_output`;
  - `counterexample`;
  - `prompt_injection_warning`;
- the linter warns on:
  - missing descriptions;
  - descriptions that are too short;
  - generic descriptions such as `MCP tool server.tool`;
  - schemas with no described input properties;
  - schemas with no required fields;
  - high-risk tool names such as delete/write/exec/run;
  - prompt-injection-like wording in descriptions.

Important files:

- `mini_cc.subagents`: MCP tool description linter and generated quality
  metadata;
- `tests.test_subagents`: good/bad MCP tool description quality coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 49 tests in 2.144s
OK
```

Full test suite:

```text
Ran 165 tests in 10.636s
OK
```

### Beginner Explanation

Think of an MCP tool description as the label on a tool drawer.

If the label says only:

```text
MCP tool remote.run
```

the model cannot know whether it is safe, what input it wants, or when not to
use it.

Now the registry adds a quality note:

```text
score: 60
warnings:
- description is generic
- input schema has no described properties
- tool name looks high risk
```

This does not rewrite the tool. It tells the agent, tests, and future tool
retrieval layer: "be careful, this tool is poorly described."

## 2.1 - MCP Registry and Capability Index

Date: 2026-06-19

### Version Scope

Version `2.1` adds a project-level MCP registry.

Plain-language summary:

- before this version, MCP tools mostly lived inside subagent configuration;
- that worked, but the tools were scattered;
- now the runtime can build one MCP directory at `.mini_cc/mcp-registry.json`;
- the directory records which MCP servers exist, what they expose, whether they
  are healthy, and which subagents can see which tools.

New behavior:

- new `.mini_cc/mcp-registry.json`;
- new `SubagentRuntime.build_mcp_registry()`;
- new `SubagentRuntime.mcp_registry_json()`;
- new S20 tool: `subagent_mcp_registry`;
- each MCP server registry row includes:
  - `name`;
  - `transport`;
  - `auth`;
  - `trust_level`;
  - `health`;
  - `tools`;
  - `resources`;
  - `prompts`;
  - subagent visibility;
- registry generation calls MCP list endpoints and marks a server unhealthy if
  listing tools/resources/prompts fails;
- tool catalog entries include generated capability tags such as `search`,
  `write`, `file`, `database`, `web`, `shell`, `review`, and `high_risk`;
- `capability_index` maps tags to qualified tool names such as
  `mcp__local__search_docs`.

Important files:

- `mini_cc.subagents`: registry builder, health status, capability tag
  extraction, subagent visibility filtering, and config metadata retention;
- `mini_cc.s20`: exposes `subagent_mcp_registry`;
- `tests.test_subagents`: registry file, catalog, capability index, and S20
  tool coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 48 tests in 2.142s
OK
```

Full test suite:

```text
Ran 164 tests in 10.499s
OK
```

### Beginner Explanation

Think of MCP tools as tools in different drawers.

Before 2.1, each subagent knew about its own drawer, but the project did not
have one shared catalog.

Now the runtime can write a catalog:

```text
.mini_cc/mcp-registry.json
```

That catalog answers:

- which MCP servers are configured;
- whether each server is healthy;
- whether it is local, remote, project, or enterprise trusted;
- what tools/resources/prompts it exposes;
- what each tool is probably useful for;
- which subagent is allowed to see which MCP tool.

This is the base layer for later dynamic tool retrieval.

## 2.0 - Subagent Runtime v2

Date: 2026-06-19

### Version Scope

Version `2.0` closes the subagent work into an engineering-level runtime
surface.

Plain-language summary:

- previous versions added the pieces one by one;
- this version adds the reporting layer that makes those pieces inspectable as
  one runtime;
- you can now ask the runtime for a trace, metrics, and evaluation report;
- this is the difference between "we have logs somewhere" and "we have a
  runtime status report".

Runtime v2 capability checklist:

- contract;
- state machine;
- replay;
- worktree writers;
- safe parallel write;
- approval / quality / merge gates;
- task graph;
- teammate communication;
- trace / metrics / evaluation.

New behavior:

- new `SubagentRuntime.runtime_report()`;
- new `SubagentRuntime.runtime_trace()`;
- new `SubagentRuntime.runtime_metrics()`;
- new `SubagentRuntime.runtime_evaluation()`;
- new `subagent_runtime_report` S20 tool;
- report output supports `json` and `text`;
- the JSON report includes:
  - runtime identity and version;
  - capability flags;
  - event trace;
  - metrics;
  - evaluation status;
  - blockers;
  - failed quality gates.

Important files:

- `mini_cc.subagents`: Runtime v2 report, trace, metrics, and evaluation;
- `mini_cc.s20`: exposes `subagent_runtime_report`;
- `tests.test_subagents`: report generation and S20 tool exposure coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 47 tests in 2.113s
OK
```

Full test suite:

```text
Ran 163 tests in 10.583s
OK
```

### Beginner Explanation

Think of the runtime report as the project manager's dashboard.

Before 2.0, the system already had contracts, task states, worktrees, quality
gates, task graphs, and peer messages. But to inspect them, you had to know
where the logs were and how to read them.

Now the runtime can summarize itself:

```text
What happened?        -> trace
How many things ran?  -> metrics
Did the run pass?     -> evaluation
What blocked it?      -> blockers
```

So 2.0 is not a single flashy new trick. It is the point where the subagent
system becomes easier to operate, audit, and explain.

## 1.9 - Teammate Communication and Negotiation

Date: 2026-06-19

### Version Scope

Version `1.9` lets subagents communicate sideways in a controlled way.

Plain-language summary:

- before this version, a subagent mostly reported upward to the parent agent;
- now the parent scheduler can pass structured messages from one subagent to a
  later dependent subagent;
- this is not uncontrolled chatting between helpers;
- it is a small audited message protocol carried by the DAG scheduler.

Supported structured lines in subagent output:

```text
QUESTION: what still needs checking?
ANSWER: the config is valid
ARTIFACT: config.json
CLAIM: build_status=green
REJECT: implementation misses required verification
```

New behavior:

- each completed task node can publish a `PeerPacket`;
- peer packets can include questions, answers, artifacts, claims, and
  rejections;
- downstream dependency handoffs now include a `mini_cc_peer_v1` structured
  peer communication block;
- file diffs from write-capable subagents are also published as artifacts;
- contradictory claims are detected when two subagents assert different values
  for the same claim key;
- critic/reviewer output can explicitly reject an implementation with
  `REJECT:` or `REQUEST_CHANGES:`;
- a critic rejection now fails the `reviewer` quality gate and blocks
  dependent nodes.

New replay fields:

- `peer_packets`;
- `peer_questions`;
- `peer_answers`;
- `peer_artifacts`;
- `peer_contradictions`;
- `peer_rejections`.

Important files:

- `mini_cc.subagents`: peer packet model, structured extraction, peer handoff,
  contradiction detection, artifact events, and critic rejection gate;
- `tests.test_subagents`: structured Q&A/artifact exchange, contradiction
  detection, and critic rejection coverage;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 46 tests in 2.066s
OK
```

Full test suite:

```text
Ran 162 tests in 10.547s
OK
```

### Beginner Explanation

Think of the parent scheduler as the meeting host.

The helpers do not all talk over each other. One helper writes a short
structured note, the scheduler files it, and the next helper receives it only
when its task depends on the first helper.

Example:

```text
reader says:
QUESTION: Is config valid?
ARTIFACT: config.json

verifier receives that note and says:
ANSWER: config is valid
```

If two helpers disagree, the scheduler records that disagreement:

```text
reader-a says: CLAIM: build_status=green
reader-b says: CLAIM: build_status=red
```

If the critic finds the implementation unacceptable, it can say:

```text
REJECT: implementation misses required verification
```

That is treated as a real blocker, not just a comment.

## 1.85 - DAG Scheduler Executor

Date: 2026-06-19

### Version Scope

Version `1.85` upgrades the subagent executor from "run the pipeline list in
order" to "run the task graph by dependency readiness".

Plain-language summary:

- before this version, the system could record a task graph, but execution was
  still basically step 1, then step 2, then step 3;
- now every pipeline step becomes a DAG node such as `task-1`, `task-2`, and
  `task-3`;
- a node can say which earlier nodes it depends on;
- the scheduler repeatedly asks: "which tasks are ready now?";
- a task is ready only when all its dependencies are completed;
- ready safe parallel groups can run together;
- if a node fails, the scheduler blocks the nodes that depend on it.

New behavior:

- `PipelineStep` now carries explicit `dependencies`;
- dynamic planner JSON can include `"dependencies": ["task-1", "task-2"]`;
- plan approval rejects unknown dependencies, self-dependencies, duplicate
  dependencies, and dependencies that point to later tasks;
- `build_task_graph()` uses explicit dependencies when present and falls back
  to the old frontier dependency model when they are absent;
- `run_task_graph_scheduler()` is now the executor used by `run_pipeline()`;
- the scheduler emits `task_graph_scheduler_started` and
  `task_graph_scheduler_completed`;
- each node is claimed, run, quality-gated, released, and recorded through the
  task graph event stream;
- dependency handoff text is built from the completed dependency nodes, not
  merely from the immediately previous step;
- downstream blocking is graph-based, so dependents of a failed node are
  blocked recursively.

Important files:

- `mini_cc.subagents`: DAG dependency field, planner validation, graph builder,
  scheduler executor, dependency handoff, and downstream blocking;
- `tests.test_subagents`: dynamic DAG scheduling test and invalid dependency
  gate test;
- `README.md`, `docs/architecture.md`, and this file: updated explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 43 tests in 1.795s
OK
```

Full test suite:

```text
Ran 159 tests in 10.341s
OK
```

Test environment note:

- tests were run with a local Python 3.10 interpreter;
- `py -3` currently points to Python 3.14 on this machine, where temporary
  directory permissions caused unrelated `tempfile` failures.

### Beginner Explanation

Think of the old executor as a numbered recipe:

```text
1. reader-a reads
2. reader-b reads
3. verifier checks
```

That works, but it does not really understand dependency shape.

The new executor treats the work like a dependency map:

```text
task-1: reader-a reads
task-2: reader-b reads
task-3: verifier checks, but only after task-1 and task-2 finish
```

So the scheduler can run `task-1` and `task-2` together when they are safe, then
run `task-3` only after both are done. This is the difference between "follow a
list" and "understand a work graph".

## 1.8 - Shared Task Graph

Date: 2026-06-19

### Version Scope

Version `1.8` adds a shared task graph beside the existing subagent pipeline.

Plain-language summary:

- before this version, orchestration was mostly a numbered pipeline;
- a pipeline is good for "step 1, then step 2, then step 3";
- a task graph is better for "this task depends on that task", "this task is
  blocked", "this worker claimed it", and "retry or reroute this node";
- version `1.8` keeps the existing stable pipeline executor, but records a graph
  view of the same work;
- this creates the state layer needed for later full DAG scheduling.

New behavior:

- new `TaskGraphNode` model;
- new `TaskGraph` model;
- new `.mini_cc/subagents/task-graphs.jsonl`;
- pipeline steps are mapped into task nodes with:
  - stable task ids such as `task-1`;
  - phase;
  - assigned subagent;
  - dependencies;
  - `blocked_on`;
  - status;
  - `claimed_by`;
  - attempts and max attempts;
  - reroute metadata;
- graph events now include:
  - `task_graph_created`;
  - `task_node_claimed`;
  - `task_node_released`;
  - `task_node_blocked`;
  - `task_node_retry_requested`;
  - `task_node_rerouted`;
- `subagent_replay_events` now reconstructs `task_graphs`;
- failed gates or step errors block downstream task nodes;
- retry/reroute methods are present and tested as graph operations.

Important files:

- `mini_cc.subagents`: task graph models, graph construction, claim/release,
  block, retry, reroute, event logging, and replay integration;
- `tests.test_subagents`: task graph dependency/status coverage and retry /
  reroute event coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 41 tests in 1.663s
OK
```

Full test suite:

```text
Ran 157 tests in 10.199s
OK
```

### Beginner Explanation

Think of a pipeline as a simple checklist:

```text
1. Explore
2. Implement
3. Verify
```

Think of a task graph as a small work board:

```text
task-1 is ready.
task-2 waits for task-1.
worker A claimed task-1.
task-1 completed.
task-2 is no longer blocked.
task-2 can be retried or rerouted if it fails.
```

This version does not yet replace the executor with a full graph scheduler.
It creates the shared graph state first, so later versions can safely add more
advanced scheduling.

## 1.7 - Approval and Quality Gates

Date: 2026-06-19

### Version Scope

Version `1.7` adds approval and quality gates to subagent pipelines.

Plain-language summary:

- before this version, a subagent step mostly ran and then returned output;
- now the pipeline has checkpoints before it keeps moving;
- a checkpoint is like a door: if the work is not good enough, the door does
  not open;
- this reduces the chance that a weak plan, empty implementation, failed
  verification, unsafe merge, or broken review silently passes forward.

New behavior:

- new `QualityGateResult` record;
- new `quality_gate_checked` workflow event;
- replay summaries include `quality_gates`;
- `plan_approval` gate:
  - plan must have executable steps;
  - steps must have task contracts;
  - parallel groups must be safe read-only groups or isolated-write groups;
- `implementation` gate:
  - write-capable execute steps must run in isolated worktrees;
  - write-capable execute steps must produce a file diff;
- `verification` gate:
  - verify steps must complete without a tool error;
- `merge` gate:
  - parallel write merge must use isolated worktree records;
  - same-file conflicts block the merge;
- `reviewer` gate:
  - review steps must complete without a tool error.

Important files:

- `mini_cc.subagents`: quality gate model, gate evaluation, event logging, and
  replay integration;
- `tests.test_subagents`: plan gate, implementation gate, merge gate, and
  existing pipeline coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 40 tests in 1.617s
OK
```

Full test suite:

```text
Ran 156 tests in 10.148s
OK
```

### Beginner Explanation

Think of the pipeline like a factory line:

- plan gate checks whether the work order is valid;
- implementation gate checks whether the builder actually changed something
  when it was supposed to build;
- verification gate checks whether the tester step itself ran cleanly;
- merge gate checks whether separate write results can safely come together;
- reviewer gate checks whether review completed without failing.

The current gates are local and deterministic. They do not yet ask a human to
click approve. They create the structure that a later human approval hook can
plug into.

## 1.6 - Parallel Write Subagents

Date: 2026-06-19

### Version Scope

Version `1.6` upgrades subagent parallelism from "read-only only" to
"isolated writes can also run in parallel".

Plain-language summary:

- before this version, parallel subagents were only allowed to read;
- writing helpers had to stay sequential because they could collide in the same
  workspace;
- version `1.5` gave every writing helper its own worktree;
- version `1.6` uses that isolation to let multiple writing helpers work at the
  same time;
- after they finish, the parent runtime checks their diffs and merges only when
  it is safe.

New behavior:

- parallel groups can now be:
  - all read-only subagents;
  - or all write-capable subagents with worktree isolation enabled;
- mixed read/write groups still do not run in parallel;
- every isolated write run collects:
  - changed files;
  - added files;
  - modified files;
  - deleted files;
  - unified diff preview;
- handoff rows now include `changed_files` and `patch_preview`;
- parallel write merge uses a conservative parent-side policy:
  - same relative file path changed by multiple subagents means conflict;
  - conflicts block the whole merge;
  - non-conflicting files are copied back to the parent workspace in step order;
- event history records:
  - `worktree_diff_collected`;
  - `parallel_write_merge_completed`;
  - `parallel_write_conflict_detected`;
- replay summaries now expose parallel write merges and conflicts.

Important files:

- `mini_cc.subagents`: isolated-write parallel group classification, diff
  collection, conflict detection, and merge closure;
- `mini_cc.tools`: `ToolResult` can now carry metadata used by the orchestrator;
- `tests.test_subagents`: real parallel write merge and conflict-blocking
  coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 38 tests in 1.528s
OK
```

Full test suite:

```text
Ran 154 tests in 10.133s
OK
```

### Beginner Explanation

Think of each writing subagent as a person editing a photocopy of the project.
They can work at the same time because nobody is touching the original yet.

When they finish, the parent agent compares every photocopy with the original:

- if two people changed the same file, it stops and reports a conflict;
- if they changed different files, it copies those files back into the original
  project.

So this is not "parallel writing with no rules". It is "parallel writing in
separate rooms, then one controlled merge at the door".

## 1.5 - Worktree-Isolated Subagents

Date: 2026-06-19

### Version Scope

Version `1.5` gives write-capable subagents their own worktree-style workspace.

Plain-language summary:

- before this version, a writing subagent used the same project directory as
  the parent agent;
- that means two writing helpers could touch the same files in the same place;
- now a subagent with explicit write tools gets its own workspace copy first;
- its file tools run inside that isolated workspace;
- read-only subagents still read the parent workspace directly.

New behavior:

- write-capable subagents are detected from tools such as `write_file`,
  `replace_text`, `todo_write`, `memory_write`, and `subagent_memory_write`;
- the runtime tries to create a real `git worktree` when the workspace is a Git
  repository;
- non-Git teaching/test workspaces fall back to a controlled directory copy;
- cloned tools keep the same permission policy and S20 tool surface while using
  the isolated workspace root;
- handoff rows now record:
  - `worktree_path`;
  - `worktree_backend`;
  - `worktree_isolated`;
- workflow event history records `worktree_created`;
- replay summaries now include a `worktrees` section.

Important files:

- `mini_cc.tools`: base tool runner cloning for alternate workspace roots;
- `mini_cc.s20`: S20-aware clone that preserves teaching tools inside a
  subagent worktree;
- `mini_cc.subagents`: worktree creation, fallback copy, handoff/event/replay
  metadata;
- `tests.test_subagents`: behavior tests proving writes land in the isolated
  worktree rather than the parent workspace;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 36 tests in 1.152s
OK
```

Full test suite:

```text
Ran 152 tests in 9.685s
OK
```

### Beginner Explanation

Think of the main project as one desk. A read-only helper can stand at the desk
and inspect the files. A writing helper should not scribble directly on that
same desk, especially when more helpers may run later.

So version `1.5` gives a writing helper its own copied desk. The helper writes
there first. The main project is not directly changed by that subagent run.
This is the foundation for safer future parallel writing, review, merge, and
rollback flows.

## 1.4 - Event History and Replay

Date: 2026-06-19

### Version Scope

Version `1.4` adds a workflow event history for subagents and a replay summary.

Plain-language summary:

- transcript means "what was said in the chat";
- event history means "what happened in the work process";
- before this version, resume mostly had chat messages and tool results;
- now the runtime also writes key workflow events that can rebuild progress;
- replay reads those events and summarizes current subagent, handoff, pipeline,
  and contract state without rerunning tools.

New behavior:

- new `.mini_cc/subagents/event-history.jsonl`;
- new workflow events for:
  - contract creation;
  - handoff start;
  - handoff completion;
  - state changes;
  - pipeline planning;
  - pipeline start;
  - pipeline step start;
  - pipeline step completion;
  - pipeline completion;
- new `SubagentRuntime.replay_event_history()`;
- new `subagent_replay_events` S20 tool;
- replay reconstructs:
  - latest state per subagent;
  - handoff final states;
  - pipeline status;
  - known contracts.

Important files:

- `mini_cc.subagents`: workflow event model, event-history logging, replay
  reconstruction;
- `mini_cc.s20`: exposes `subagent_replay_events`;
- `tests.test_subagents`: event-history and replay coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 34 tests in 0.974s
OK
```

Full test suite:

```text
Ran 150 tests in 9.438s
OK
```

### Beginner Explanation

Think of `transcript` as the chat log:

```text
User said this.
Agent replied that.
Tool returned this.
```

Think of `event history` as the work log:

```text
Created contract.
Started handoff.
Moved to running.
Started pipeline step.
Finished handoff.
Completed pipeline.
```

Replay means reading that work log and rebuilding the current picture. It does
not run the work again. It only answers: "what was the latest known state?"

This is important because future resume should not only restore conversation.
It should also know which work was planned, started, completed, blocked, or
still waiting.

## 1.3 - Subagent State Machine v1

Date: 2026-06-19

### Version Scope

Version `1.3` gives every subagent run a clear state trail.

Plain-language summary:

- before this version, a subagent mostly had input and output;
- now it also has a progress status;
- this is like giving every helper a simple task progress bar;
- later versions can use this state trail for resume, retry, approval gates,
  and task graphs.

New behavior:

- new subagent states:
  - `planned`;
  - `ready`;
  - `running`;
  - `blocked`;
  - `waiting_approval`;
  - `verifying`;
  - `completed`;
  - `failed`;
  - `abandoned`;
- normal subagent runs record:
  - `planned -> ready -> running -> completed`;
- failed agent-loop exceptions record `failed`;
- missing sessions, unknown subagents, depth limits, and token-budget limits
  record `blocked`;
- verification pipeline steps record `verifying`;
- pipelines with no executable steps record `abandoned`;
- state transitions are written to `.mini_cc/subagents/state-events.jsonl`;
- final state is also copied into handoff rows;
- child sessions get a `subagent_state` event.

Important files:

- `mini_cc.subagents`: state model, state event logging, run/pipeline state
  transitions;
- `tests.test_subagents`: normal completion, blocked session, and verifying
  pipeline coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 33 tests in 0.831s
OK
```

Full test suite:

```text
Ran 149 tests in 9.261s
OK
```

### Beginner Explanation

Think of a subagent as a worker. In older versions, we only knew "we sent it a
task" and "it returned something". In this version, we also write down the
worker's status changes:

```text
planned -> ready -> running -> completed
```

If it cannot start, we write `blocked`. If it is doing verification, we write
`verifying`. If there is no usable step, we write `abandoned`.

This matters because future versions need to resume, retry, or inspect work.
Without a state trail, the system has to guess what happened. With a state
trail, it can read the log and know where the subagent stopped.

## 1.2 - Subagent Task Contract

Date: 2026-06-19

### Version Scope

Version `1.2` makes subagent delegation structured instead of relying only on a
natural-language prompt.

Plain-language summary:

- before this version, calling a subagent was mostly "send this prompt to this
  helper";
- now each subagent handoff can carry a task contract;
- the contract is like a work order: it says the objective, expected
  deliverable, constraints, tool boundary, evidence, budget, and stop
  conditions;
- if the caller does not provide a contract, the runtime creates a conservative
  fallback contract automatically.

New behavior:

- new `TaskContract` model with:
  - `objective`;
  - `deliverable`;
  - `constraints`;
  - `allowed_tools`;
  - `expected_evidence`;
  - `budget`;
  - `stop_conditions`;
- `subagent_run` accepts optional `task_contract`;
- `subagent_pipeline` accepts optional root `task_contract`;
- pipeline steps receive derived child contracts;
- nested subagent calls inherit `parent_contract_id`;
- requested `allowed_tools` are filtered through the target subagent's real
  tool allowlist;
- handoff logs, pipeline decisions, and child session events all record the
  contract id and task contract payload.

Important files:

- `mini_cc.subagents`: task contract model, normalization, inheritance, logging,
  and pipeline-step contract generation;
- `mini_cc.s20`: tool entrypoints now accept `task_contract`;
- `tests.test_subagents`: contract recording, allowlist filtering, pipeline
  inheritance, and nested inheritance coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted subagent tests:

```text
Ran 31 tests in 0.714s
OK
```

Full test suite:

```text
Ran 147 tests in 9.256s
OK
```

### Interpretation

This version is the foundation for the road to `2.0`. It does not yet make
subagents fully stateful or worktree-isolated, but it gives every delegation a
stable contract id and a structured work order. That is the base needed for
state machines, replay, quality gates, and task graphs.

## 1.10 - Evidence Ledger and Plan Repair

Date: 2026-06-19

### Version Scope

Version `1.10` adds structured evidence records and repair guidance to the
workflow verifier.

Plain-language summary:

- before this version, the verifier mostly returned a final status plus a short
  reason;
- now it also records the concrete evidence behind that status;
- when the run is not in a good state, it also records how the plan should be
  repaired;
- this makes session replay and benchmark-style review much easier.

New behavior:

- `ExecutionRecord` now keeps a short result summary for later review;
- `VerificationResult` now includes `evidence_ledger` and `plan_repair`;
- `evidence_ledger` records:
  - turn;
  - tool;
  - planned step;
  - status;
  - evidence kind;
  - short summary;
- `plan_repair` records:
  - whether repair is needed;
  - repair reasons such as `tool_failure` or
    `missing_required_verification`;
  - missing planned steps;
  - suggested next actions;
- session traces now persist `evidence_ledger` and `plan_repair` as explicit
  events alongside `verifier_result`.

Important files:

- `mini_cc.workflow`: evidence and repair dataclasses plus verifier logic;
- `mini_cc.agent`: session persistence for evidence and repair events;
- `tests.test_workflow`: verifier and session-trace coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted workflow tests:

```text
Ran 11 tests in 0.087s
OK
```

Full test suite:

```text
Ran 146 tests in 9.209s
OK
```

### Interpretation

This version does not just say "the run failed" or "verification is missing".
It now leaves behind a small machine-readable notebook of why that conclusion
was reached and what the next repair action should be. That is a better base
for review, resume, and later automation.

## 1.09 - Verification Policy by Task Risk

Date: 2026-06-19

### Version Scope

Version `1.09` makes verification depend on task risk instead of only checking
whether the task was benchmark-like.

Plain-language summary:

- before this version, only benchmark tasks were forced to show an explicit
  verification step;
- now any higher-risk task can be held to that same standard;
- in practice, file-write, Docker, network, and package-manager style tasks
  must reach a verify step before the run is considered OK;
- low-risk read-style tasks can still finish without a dedicated verify step.

New behavior:

- `TaskPlan` now carries `verification_policy`;
- the planner derives that policy from task mode and permission envelope;
- benchmark tasks use `required`;
- standard tasks with write/network/package-manager/Docker style risk also use
  `required`;
- low-risk tasks can stay `optional`;
- `VerificationResult` now records `verification_policy` and
  `verification_required`;
- missing verification on a required-risk task now returns `ok=False` instead
  of quietly passing.

Important files:

- `mini_cc.workflow`: risk-based verification policy inference and verifier
  enforcement;
- `tests.test_workflow`: coverage for benchmark risk, write risk, and low-risk
  optional verification;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted workflow tests:

```text
Ran 11 tests in 0.085s
OK
```

Full test suite:

```text
Ran 146 tests in 9.207s
OK
```

### Interpretation

This version closes a real evaluation gap. Previously, a task could perform a
meaningful change and still be treated as basically fine even if the agent
never checked the result. Now the workflow distinguishes between low-risk
"read and summarize" work and higher-risk "change or environment action" work,
and it requires stronger proof for the latter.

## 1.08 - Model-Authored Structured Plans

Date: 2026-06-19

### Version Scope

Version `1.08` lets S20 ask the model to draft the structured task plan before
the normal agent loop starts.

Plain-language summary:

- before this version, the top-level workflow plan was only rule-based;
- now the model can suggest a JSON checklist for the task;
- local code still checks that checklist before it is trusted;
- if the model returns bad JSON, unknown steps, unknown roles, or unsafe
  permission requests, the runtime falls back to the conservative local plan.

New behavior:

- `ModelAuthoredPlanner` asks the provider for a JSON-only plan;
- valid model plans can customize the inspect/execute/verify/report goals;
- mode changes are not accepted when they conflict with local inference;
- step ids are limited to `inspect`, `execute`, `verify`, and `report`;
- roles are limited to `planner`, `executor`, `verifier`, and `critic`;
- model plans are capped at six steps;
- permission envelopes are filtered through the local fallback plan, so the
  model cannot grant itself Docker/network/package-manager access for a normal
  edit task;
- invalid planning output records `planning_issues` and uses fallback planning.

Important files:

- `mini_cc.workflow`: model-authored planner, JSON extraction, validation, and
  fallback behavior;
- `mini_cc.cli`: S20 mode now wires `ModelAuthoredPlanner` into
  `StructuredWorkflow`;
- `tests.test_workflow`: coverage for valid model plans, invalid JSON fallback,
  and permission-envelope filtering;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted workflow tests:

```text
Ran 9 tests in 0.082s
OK
```

Full test suite:

```text
Ran 144 tests in 9.170s
OK
```

### Interpretation

This version improves planning flexibility without making the model the
security authority. The model can propose the plan, but local validation decides
which parts are usable. This is intentionally stricter than simply trusting
model-written instructions.

## 1.07 - Plan-Scoped Permission Envelope

Date: 2026-06-19

### Version Scope

Version `1.07` adds a permission envelope to structured workflow plans.

Plain-language summary:

- before this version, `auto` mode could allow broad actions as long as the
  global permission policy allowed them;
- now the active task plan can narrow that permission range;
- the plan says which risk types this task should need;
- the tool runner blocks risk types outside that plan before normal permission
  mode can allow them.

New behavior:

- `TaskPlan` now includes `permission_envelope`;
- `Planner` infers allowed risk types from task mode and prompt keywords;
- standard tasks allow read, verification, and workspace writes;
- benchmark/environment tasks can additionally allow Docker, network, and
  package-manager risks;
- `Agent` installs the plan envelope on the tool runner after planning;
- sessions record a `permission_envelope` event;
- `ToolRunner` blocks out-of-envelope risks before normal permission policy;
- out-of-envelope denials are written to the permission ledger and surfaced as
  tool errors.

Important files:

- `mini_cc.workflow`: plan envelope inference;
- `mini_cc.agent`: envelope installation and session recording;
- `mini_cc.tools`: envelope enforcement before normal permission policy;
- `tests.test_workflow` and `tests.test_tools`: planner, agent, tool, and
  ledger coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation.

### Real Test Status

Targeted workflow/permission tests:

```text
Ran 20 tests in 0.137s
OK
```

Full test suite:

```text
Ran 141 tests in 9.140s
OK
```

### Interpretation

This version makes permissions more task-specific. It is still rule-based: the
planner infers the envelope from task text and mode, not from a model-authored
security proof. Future versions can make the envelope explicit in dynamic plans
and subagent handoffs.

## 1.06 - Permission Ledger

Date: 2026-06-19

### Version Scope

Version `1.06` adds an append-only permission ledger alongside permission hook
events.

Plain-language summary:

- hooks tell the runtime that a permission event happened;
- the ledger is the audit notebook that keeps those permission decisions;
- every row says what the agent tried to do, the risk level, whether it was
  requested, allowed, or denied, and why.

New behavior:

- new `PermissionLedger` writes JSONL records;
- each ledger row includes:
  - `request_id`;
  - timestamp;
  - decision: `requested`, `allowed`, or `denied`;
  - tool name;
  - action text;
  - permission risk;
  - reason;
  - redacted tool input;
  - optional session id and subagent name;
- ask-mode request and final allow/deny rows share the same `request_id`;
- `ToolRunner` can receive a ledger explicitly;
- S20 creates `.mini_cc/permission-ledger.jsonl` automatically when state is
  enabled;
- `Agent` adds the current session id to permission context so ledger rows can
  be tied back to session JSON files;
- sensitive input keys such as token, authorization, API key, secret, and
  password are redacted before writing the ledger.

Important files:

- `mini_cc.permission_ledger`: append-only ledger implementation and redaction;
- `mini_cc.tools`: ledger recording at the central permission gate;
- `mini_cc.s20`: automatic S20 state-dir ledger;
- `mini_cc.agent`: session id propagation into permission context;
- `tests.test_tools` and `tests.test_agent_mock`: ledger, redaction, S20, and
  session-context coverage;
- `README.md` and `docs/architecture.md`: beginner-facing ledger notes.

### Real Test Status

Targeted permission ledger tests:

```text
Ran 17 tests in 0.104s
OK
```

Full test suite:

```text
Ran 138 tests in 9.217s
OK
```

### Interpretation

This version makes permission decisions reviewable after the run. It still does
not implement reusable durable grants, expiry rules, or a UI for approving
pending requests.

## 1.05 - PermissionRequest / PermissionDenied Events

Date: 2026-06-19

### Version Scope

Version `1.05` wires permission lifecycle events into the actual permission
engine.

Plain-language summary:

- before this version, permission failures mostly appeared as tool error text;
- now the runtime also records structured permission events;
- `PermissionRequest` means "the agent is asking whether a risky action may
  run";
- `PermissionDenied` means "that action was blocked by policy, hook decision,
  or the user".

New behavior:

- `ToolRunner` accepts an optional hook runtime and permission context;
- `write_file`, `replace_text`, and `run_shell` pass tool name/input into the
  permission layer;
- read-only and auto-mode policy denials emit `PermissionDenied`;
- high-risk shell commands blocked during command classification emit
  `PermissionDenied`;
- ask-mode denials emit `PermissionRequest` before they are denied;
- a blocking `PermissionRequest` hook produces a matching `PermissionDenied`;
- S20 todo and memory writes now pass structured permission metadata;
- S20 read-only runs still keep hook logging enabled so denied permission
  events are auditable.

Important files:

- `mini_cc.tools`: permission event emission at the central permission gate;
- `mini_cc.s20`: structured permission metadata for todo/memory writes;
- `tests.test_tools`: permission request/denied event coverage;
- `README.md` and `docs/architecture.md`: beginner-facing behavior notes.

### Real Test Status

Targeted permission/hook tests:

```text
Ran 24 tests in 0.729s
OK
```

Full test suite:

```text
Ran 134 tests in 9.219s
OK
```

### Interpretation

This version makes permission behavior easier to audit and easier to connect to
future UI or policy layers. It does not yet add durable user grants, per-task
permission envelopes, or interactive approval UI.

## 1.04 - WebSocket MCP Transport

Date: 2026-06-19

### Version Scope

Version `1.04` adds a WebSocket transport for MCP-style JSON-RPC servers.

Plain-language summary:

- stdio MCP is like talking to a local child process through stdin/stdout;
- Streamable HTTP MCP is like sending one HTTP request per MCP call;
- WebSocket MCP keeps one network connection open and sends JSON-RPC messages
  through that connection;
- this is useful for remote MCP servers that prefer long-lived connections.

New behavior:

- `WebSocketMCPAdapter` supports `tools/list`, `tools/call`,
  `resources/list`, `resources/read`, `prompts/list`, and `prompts/get`;
- WebSocket requests use JSON-RPC ids and ignore unrelated incoming messages
  until the matching response arrives;
- optional `initialize` performs MCP capability negotiation on connection;
- bearer tokens and custom headers are sent during the WebSocket handshake;
- MCP tool input schema validation is reused before `tools/call`;
- subagent config supports `transport: "websocket"` and `transport: "ws"`;
- the adapter is compatible with existing MCP policy and audit wrappers.

Important files:

- `mini_cc.mcp`: new `WebSocketMCPAdapter`;
- `mini_cc.subagents`: config loading for WebSocket MCP servers;
- `tests.test_mcp`: fake WebSocket JSON-RPC coverage;
- `tests.test_subagents`: config loading coverage;
- `README.md`, `docs/architecture.md`, and `requirements.txt`: usage and
  dependency notes.

### Real Test Status

Targeted MCP tests:

```text
Ran 22 tests in 7.471s
OK
```

Targeted subagent tests:

```text
Ran 30 tests in 0.685s
OK
```

Full test suite:

```text
Ran 130 tests in 9.256s
OK
```

### Interpretation

This version adds the basic WebSocket transport boundary. It does not yet add
advanced reconnect policies, streaming subscription handling, or WebSocket-
specific OAuth login flows.

## 1.03 - MCP Credential Refresh

Date: 2026-06-19

### Version Scope

Version `1.03` adds in-memory OAuth credential refresh for remote Streamable
HTTP MCP servers.

Plain-language summary:

- access token is the short-lived key used on each MCP HTTP request;
- refresh token is the longer-lived key used to ask the auth server for a new
  access token;
- before this version, an expired access token usually caused the MCP request
  to fail with `401` or `403`;
- now the adapter can refresh the token once and retry the same MCP request.

New behavior:

- OAuth token responses are stored in memory on the adapter;
- device-code and authorization-code login remember the OAuth `client_id`;
- refresh tokens from token responses are kept in memory;
- `refresh_oauth_token()` calls the discovered `token_endpoint` with
  `grant_type=refresh_token`;
- a `401` or `403` MCP HTTP response triggers one refresh attempt when a
  refresh token is available;
- after a successful refresh, the original MCP request is retried once;
- session reinitialization still remains as the fallback for expired MCP
  sessions.

Important files:

- `mini_cc.mcp`: in-memory refresh-token storage, refresh call, and retry path;
- `tests.test_mcp`: local OAuth server coverage for expired-token refresh and
  retry;
- `README.md` and `docs/architecture.md`: refreshed MCP OAuth behavior notes.

### Real Test Status

Targeted MCP tests:

```text
Ran 20 tests in 7.318s
OK
```

Full test suite:

```text
Ran 127 tests in 9.106s
OK
```

### Interpretation

This version makes remote OAuth MCP use more robust for long-running sessions.
It does not save refresh tokens to disk, does not encrypt credentials, and does
not add a shared credential vault. If the process exits, the user still needs
to log in again unless a higher-level config provides credentials.

## 1.025 - MCP OAuth Login

Date: 2026-06-19

### Version Scope

Version `1.025` adds real OAuth login primitives on top of the 1.02 OAuth
metadata discovery layer.

Plain-language summary:

- before this version, the adapter could discover where OAuth login lives;
- now it can actually request a device code, poll for a token, and install that
  token as a bearer token;
- it can also build a browser authorization URL and exchange an authorization
  code for a token;
- browser login has a local callback helper, but persistent token storage and
  refresh are still future work.

New behavior:

- `StreamableHTTPMCPAdapter.start_device_authorization()` calls the discovered
  `device_authorization_endpoint`;
- `poll_device_token()` polls `token_endpoint` with the device-code grant;
- `login_with_device_code()` prints or returns user-facing verification
  instructions and installs the returned bearer token;
- `build_authorization_url()` creates an authorization-code URL with PKCE;
- `login_with_authorization_code()` exchanges a code for a token;
- `login_with_browser()` starts a local callback server, optionally opens the
  browser, receives `code`, validates `state`, and exchanges the code;
- subagent MCP config can use:
  - `oauth_flow: "device_code"`;
  - `oauth_client_id`;
  - `oauth_scope` / `oauth_scopes`;
  - `oauth_timeout`.

Important files:

- `mini_cc.mcp`: device-code flow, authorization-code exchange, browser local
  callback helper, token application;
- `mini_cc.subagents`: device-code config wiring;
- `tests.test_mcp`: local OAuth server coverage for discovery, device-code
  login, and authorization-code token exchange;
- `README.md` and `docs/architecture.md`: OAuth login configuration notes.

### Real Test Status

Targeted MCP tests:

```text
Ran 18 tests in 6.161s
OK
```

Full test suite:

```text
Ran 125 tests in 7.947s
OK
```

### Interpretation

This version makes OAuth usable from a terminal-oriented agent. Device-code is
the safest default for CLI use. Browser login is available as a helper, but
refresh-token management and secure persistent token storage are not included
yet.

## 1.02 - MCP OAuth Discovery

Date: 2026-06-19

### Version Scope

Version `1.02` adds OAuth metadata discovery for remote Streamable HTTP MCP
servers.

Plain-language summary:

- before this version, remote MCP auth mainly used bearer tokens and headers
  that you configured manually;
- now a remote MCP server can publish where its OAuth authorization information
  lives;
- the adapter can discover that metadata and record it for diagnostics and
  capability summaries;
- this is discovery only, not a full browser/device-code login flow yet.

New behavior:

- `StreamableHTTPMCPAdapter` accepts `oauth_discovery`;
- `StreamableHTTPMCPAdapter` accepts explicit `oauth_metadata_url`;
- protected-resource metadata is fetched from:
  - explicit `oauth_metadata_url`;
  - `/.well-known/oauth-protected-resource`;
  - `/.well-known/oauth-protected-resource/<endpoint-path>`;
- authorization-server metadata is fetched from discovered authorization
  servers;
- `401` responses with `WWW-Authenticate: ... resource_metadata="..."` trigger
  metadata discovery from that URL;
- discovered metadata is exposed on:
  - `protected_resource_metadata`;
  - `authorization_server_metadata`;
  - `oauth_discovery_errors`;
- `mcp_capability_summary()` includes discovered OAuth resource and
  authorization metadata;
- subagent MCP config supports `oauth_discovery` and `oauth_metadata_url`.

Important files:

- `mini_cc.mcp`: OAuth metadata discovery, 401 header extraction, capability
  summary integration;
- `mini_cc.subagents`: config wiring for `oauth_discovery` and
  `oauth_metadata_url`;
- `tests.test_mcp`: active discovery and 401-triggered discovery coverage;
- `README.md` and `docs/architecture.md`: OAuth discovery configuration notes.

### Real Test Status

Targeted MCP tests:

```text
Ran 16 tests in 5.171s
OK
```

Full test suite:

```text
Ran 123 tests in 6.938s
OK
```

### Interpretation

This version prepares the remote MCP auth layer for real OAuth flows. It can
find and report the relevant OAuth metadata, but token acquisition and browser
or device-code login are still future work.

## 1.01 - MCP Schema Completeness

Date: 2026-06-19

### Version Scope

Version `1.01` expands MCP tool input validation from basic recursive checks
to broader JSON Schema coverage.

Plain-language summary:

- before this version, MCP tool calls mainly checked required fields, basic
  types, nested objects, arrays, and enum values;
- now MCP calls check more of the schema before sending anything to the MCP
  server;
- invalid input is rejected locally, so unsafe or malformed requests do not
  leave the agent runtime.

New behavior:

- validates `additionalProperties: false`;
- validates schema-defined `additionalProperties` object schemas;
- validates `const`;
- validates string `minLength`, `maxLength`, and `pattern`;
- validates numeric `minimum`, `maximum`, `exclusiveMinimum`,
  `exclusiveMaximum`, and `multipleOf`;
- validates array `minItems`, `maxItems`, `uniqueItems`, `items`, and
  `prefixItems`;
- validates basic `anyOf`, `oneOf`, and `allOf`;
- keeps exact JSON path errors such as `$.config.limit` and `$.tags[2]`.

Important files:

- `mini_cc.mcp`: expanded JSON Schema validator used before stdio/HTTP MCP
  `tools/call`;
- `tests.test_mcp`: schema completeness coverage and "invalid call is not sent"
  verification;
- `README.md` and `docs/architecture.md`: updated MCP schema capability notes.

### Real Test Status

Targeted MCP tests:

```text
Ran 14 tests in 4.098s
OK
```

Full test suite:

```text
Ran 121 tests in 5.792s
OK
```

### Interpretation

This version tightens the MCP boundary. The adapter still is not a complete
JSON Schema implementation, but it now covers the common constraints most MCP
tools use for practical input safety.

## 1.0 - End-to-End Context Budgeting

Date: 2026-06-19

### Version Scope

Version `1.0` extends budgeting from the `context_snapshot` tool to the whole
Agent loop payload sent to the model.

Plain-language summary:

- before this version, `context_snapshot` had its own budget and conversation
  compaction managed old message history;
- now every provider call checks the whole model payload first;
- the checked payload includes system prompt, tool schemas, and messages;
- if the payload is too large, the agent compacts old turns and summarizes big
  tool results before calling the model.

New behavior:

- `Agent` has `model_context_token_budget`, defaulting to `8000`;
- before each `provider.complete(...)`, `Agent` estimates the full model
  context payload;
- full payload estimate includes:
  - system prompt;
  - tool schemas;
  - current message list;
- if over budget, `Agent` runs rolling conversation compaction;
- if still over budget, oversized string blocks and tool results are summarized;
- session traces record `model_context_budget_applied`;
- CLI exposes `--model-context-token-budget`;
- subagent runtime passes the same budget to child agents.

Important files:

- `mini_cc.agent`: provider-call budget gate and payload shrinking;
- `mini_cc.subagents`: passes model context budget into subagent agents;
- `mini_cc.cli`: command-line budget control;
- `tests.test_agent_mock`: verifies the provider receives a budget-managed
  payload and oversized tool results are summarized;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted agent tests:

```text
Ran 3 tests in 0.037s
OK
```

Full test suite:

```text
Ran 120 tests in 5.420s
OK
```

### Interpretation

This version is the first full-loop context budget. It does not just compress a
snapshot tool result; it manages the actual model input package before each
model call.

## 0.99 - Context Source Registry

Date: 2026-06-19

### Version Scope

Version `0.99` separates context by source type instead of mixing every fact
into one undifferentiated snapshot.

Plain-language summary:

- before this version, context could contain memory, recent run information,
  tool facts, and user instructions without a clear label;
- now `context_snapshot` includes a `Context Source Registry`;
- the registry tells the model where each context block came from;
- this helps the agent avoid treating a recent tool result like durable memory,
  or treating a user instruction like a temporary observation.

New behavior:

- `ContextSection` now has a `source_type`;
- `context_snapshot` renders `# Context Source Registry`;
- supported source types are:
  - `durable_memory`;
  - `recent_session_facts`;
  - `tool_summaries`;
  - `user_instructions`;
  - `workspace`;
- durable memory comes from `memory_recall` / `memory_read`;
- recent session facts come from local session JSON files;
- tool summaries come from recent `tool_use` events and compacted conversation
  summaries;
- user instructions come from workspace `AGENTS.md`;
- the registry includes a short type overview that remains visible even under
  tight token budgets.

Important files:

- `mini_cc.context`: context source registry, user instruction loading, recent
  session fact loading, and tool summary loading;
- `tests.test_s20`: source-type coverage through `context_snapshot`;
- `tests.test_context`: updated budget and durable-memory expectations;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted context/S20 tests:

```text
Ran 12 tests in 0.274s
OK
```

Full test suite:

```text
Ran 119 tests in 5.284s
OK
```

### Interpretation

This version improves context hygiene. The model receives not just facts, but
also labels saying what kind of facts they are and how trustworthy or durable
they should be considered.

## 0.98 - Conversation Compaction

Date: 2026-06-19

### Version Scope

Version `0.98` adds rolling compaction for old model/tool turns in the
conversation history.

Plain-language summary:

- before this version, long sessions kept carrying old full tool outputs;
- now old turns can be compressed when the conversation grows past a budget;
- the newest messages remain unchanged;
- older tool work becomes a compact checklist;
- the checklist keeps tool name, arguments, result summary, and failure
  information.

New behavior:

- `Agent` estimates the serialized conversation size before saving/sending;
- when the size exceeds `compaction_token_budget`, older messages are replaced
  by a deterministic `Conversation compaction summary`;
- compacted tool entries preserve:
  - `tool`;
  - `input`;
  - `status=ok/error/unknown`;
  - result summary text;
- prior compacted summaries are rolled forward in shortened form;
- recent messages are kept verbatim via `compaction_keep_recent_messages`;
- `conversation_compacted` session events record before/after message counts,
  estimated tokens, trigger, and summary size;
- `PreCompact` and `PostCompact` hooks fire around compaction;
- CLI exposes `--conversation-compaction-token-budget` and
  `--conversation-compaction-keep-recent`;
- subagent sessions use the same compaction controls as the parent runtime.

Important files:

- `mini_cc.agent`: rolling conversation compaction and structured tool-turn
  summaries;
- `mini_cc.subagents`: passes compaction settings into child agents;
- `mini_cc.cli`: command-line controls for compaction budget and recent-message
  retention;
- `tests.test_agent_mock`: coverage for compacted tool name, arguments, result
  summary, and failure status;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted agent/subagent tests:

```text
Ran 31 tests in 0.709s
OK
```

Full test suite:

```text
Ran 118 tests in 5.330s
OK
```

### Interpretation

This version reduces long-session context pressure without hiding the most
important audit facts. It is deterministic rather than model-summarized, so it
is easier to test and less likely to invent details.

## 0.97 - Nested Subagents

Date: 2026-06-19

### Version Scope

Version `0.97` allows a subagent to delegate to another subagent, but only
within explicit depth and token-budget limits.

Plain-language summary:

- before this version, the parent agent could call subagents, but subagents
  could not safely call other subagents as part of their own work;
- now a subagent may call `subagent_run` or `subagent_pipeline` if that tool is
  in its allowlist;
- the runtime counts how deep the delegation chain is;
- the runtime also limits how large each nested prompt/task can be;
- if either limit is exceeded, the nested call returns a tool error instead of
  running.

New behavior:

- `RestrictedToolRunner` intercepts nested `subagent_run` and
  `subagent_pipeline` calls instead of delegating them blindly to the parent
  S20 runner;
- `SubagentRuntime.run(..., depth=...)` and `run_pipeline(..., depth=...)`
  carry a depth counter through nested calls;
- `max_nested_depth` defaults to `1`;
- `nested_token_budget` defaults to `1200` approximate tokens;
- CLI exposes `--max-nested-subagent-depth` and
  `--nested-subagent-token-budget`;
- nested handoff rows record `depth`, `max_depth`, and
  `nested_token_budget`.

Important files:

- `mini_cc.subagents`: nested call routing, depth checks, prompt/task budget
  checks, and handoff audit fields;
- `mini_cc.cli`: command-line controls for nested subagent depth and budget;
- `tests.test_subagents`: coverage for successful one-level nesting, depth
  rejection, and token-budget rejection;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted subagent tests:

```text
Ran 29 tests in 0.666s
OK
```

Full test suite:

```text
Ran 117 tests in 5.342s
OK
```

### Interpretation

This version adds controlled delegation. It is useful when a manager-style
subagent needs a smaller specialist subagent, but it deliberately avoids
unbounded recursion and oversized context handoffs.

## 0.96 - Dynamic Orchestration Planner

Date: 2026-06-19

### Version Scope

Version `0.96` adds a dynamic subagent planner. A model can now suggest a
multi-subagent plan, but the runtime validates and filters that plan before
anything runs.

Plain-language summary:

- before this version, `subagent_pipeline` mainly used fixed local rules;
- now `mode: "dynamic"` can ask a planner model to propose the steps;
- the model's output is treated like a suggestion, not an order;
- local code checks the plan before execution, so the model cannot invent
  subagents or assign unsafe parallel work.

New behavior:

- `SubagentRuntime` accepts an optional `planning_provider`;
- `subagent_pipeline` now accepts `mode: "dynamic"`;
- the planner prompt includes available subagents, capabilities, and required
  JSON shape;
- dynamic plans must parse as JSON with a `steps` list;
- each step must use a known subagent and supported phase;
- phase names are checked against subagent capabilities, for example `execute`
  requires `implement` or `write`;
- requested capabilities must exist on the selected subagent;
- only read-only subagents can keep `parallel_group`;
- invalid model-authored steps are filtered and recorded in `planning_issues`;
- if no dynamic step is executable, the runtime falls back to the static
  pipeline.

Important files:

- `mini_cc.subagents`: dynamic planner prompt, response parsing, schema
  validation, capability filtering, permission-style read-only filtering, and
  decision audit fields;
- `mini_cc.cli`: wires an independent planner provider into S20 subagent
  runtime construction;
- `mini_cc.s20`: exposes `dynamic` as a valid `subagent_pipeline` mode through
  the subagent schema;
- `tests.test_subagents`: model-authored valid plan coverage and unsafe/invalid
  step filtering coverage;
- `README.md` and `docs/architecture.md`: beginner-facing explanation and
  architecture notes.

### Real Test Status

Targeted subagent tests:

```text
Ran 26 tests in 0.642s
OK
```

Full test suite:

```text
Ran 114 tests in 5.182s
OK
```

### Interpretation

This version makes orchestration more flexible without letting the model bypass
local controls. The planner can recommend "which helper should do which step",
but schema validation, capability checks, and read-only parallel rules decide
what actually runs.

## 0.95 - Subagent Resume

Date: 2026-06-19

### Version Scope

Version `0.95` lets a subagent continue from an earlier child session instead
of always starting from zero.

Plain-language summary:

- before this version, every `subagent_run` was like calling a brand-new helper;
- now the parent can pass `session_id` to bring back the same helper;
- the helper keeps earlier user messages, assistant messages, and tool results;
- the history is stored in the subagent's session JSON file, so later runs can
  continue from that same record.

New behavior:

- `AgentSession` now stores the model message history, not only event rows;
- `SessionStore.load()` reads an existing session from disk;
- `SessionStore.resume()` records a `session_resumed` event;
- `SessionStore.update_messages()` persists the latest conversation state;
- `Agent.run(..., resume_session_id=...)` restores old messages before adding
  the new prompt;
- `subagent_run` accepts optional `session_id`;
- unknown subagent session ids return a clear error instead of silently starting
  a different session.

Important files:

- `mini_cc.session`: load/resume/message persistence support;
- `mini_cc.agent`: resume-aware run loop and message persistence after prompts,
  assistant replies, and tool results;
- `mini_cc.subagents`: child session id validation and resume handoff behavior;
- `mini_cc.s20`: exposes `session_id` on the teaching `subagent_run` tool;
- `tests.test_subagents`: subagent resume and missing-session coverage;
- `README.md` and `docs/architecture.md`: beginner-facing behavior docs.

### Real Test Status

Targeted subagent tests:

```text
Ran 24 tests in 0.567s
OK
```

Full test suite:

```text
Ran 112 tests in 5.175s
OK
```

### Interpretation

This version improves continuity. A resumed subagent can see what it already
did and what tools already returned. It is still a local teaching
implementation: it does not yet provide distributed resume, conflict-aware
long-running job management, or cross-machine session synchronization.

## 0.94 - Bounded Parallel Subagents

Date: 2026-06-19

### Version Scope

Version `0.94` turns `parallel_group` from metadata into real bounded parallel
execution for safe read-only subagents.

Plain-language summary:

- before this version, the pipeline only marked some subagents as "can run in
  parallel";
- now those read-only subagents really run at the same time;
- the runtime still limits how many can run together;
- subagents that can write files, run shell commands, or mutate memory are not
  allowed into parallel groups.

New behavior:

- `SubagentRuntime` accepts `max_parallel_subagents`, defaulting to `2`;
- `run_pipeline()` detects consecutive steps with the same `parallel_group`;
- a group runs in parallel only when every step points to a read-only subagent;
- write-capable subagents are forced back to sequential execution;
- parallel results are rendered in the original step order for auditability;
- pipeline output marks parallel steps with `parallel=true`;
- `read-only-discovery` now selects read-only `explore` capability subagents,
  while `critic` stays as a later review step for change-oriented tasks.

Important files:

- `mini_cc.subagents`: bounded parallel group execution, read-only safety gate,
  and max parallelism setting;
- `tests.test_subagents`: concurrent read-only group coverage and write-capable
  group rejection coverage;
- `README.md` and `docs/architecture.md`: parallel subagent documentation.

### Real Test Status

Targeted subagent tests:

```text
Ran 22 tests in 0.504s
OK
```

Full test suite:

```text
Ran 110 tests in 5.077s
OK
```

### Interpretation

This version improves multi-agent throughput without opening the door to unsafe
parallel writes. It is intentionally conservative: only independent read-only
discovery work is parallelized.

## 0.92 - Hook Handler Types v2

Date: 2026-06-19

### Version Scope

Version `0.92` expands configured hooks from command-only handlers to multiple
handler types.

Plain-language summary:

- before this version, a configured hook could basically run a local command;
- now a hook can also:
  - send the event to an HTTP endpoint;
  - call a registered MCP tool;
  - rewrite payload fields with a prompt/template;
  - call a registered in-process agent handler.

New behavior:

- configured hook `type: "http"`:
  - POSTs hook event JSON to `url`;
  - accepts JSON decisions from the HTTP response;
- configured hook `type: "mcp"`:
  - calls a registered MCP hook adapter and tool;
  - accepts JSON decisions from the MCP tool result;
- configured hook `type: "prompt"`:
  - renders templates from event payload fields;
  - returns `payload_updates`;
- configured hook `type: "agent"`:
  - calls an in-process registered agent hook handler;
  - accepts `HookDecision` or JSON-like decision dicts;
- existing `type: "command"` behavior remains unchanged.

Important files:

- `mini_cc.hooks`: HTTP/MCP/prompt/agent handler execution, template rendering,
  handler registries, and shared decision parsing;
- `tests.test_runtime_modules`: local HTTP, in-memory MCP, prompt template, and
  agent handler coverage;
- `README.md` and `docs/architecture.md`: handler type documentation.

### Real Test Status

Targeted hook tests:

```text
Ran 14 tests in 0.672s
OK
```

Full test suite:

```text
Ran 108 tests in 4.866s
OK
```

### Interpretation

This version makes hooks much more useful as integration points. It does not
make remote HTTP/MCP hooks safe by itself; production use still needs stricter
network policy, auth policy, and audit controls.

## 0.91 - Hook Event Surface v2

Date: 2026-06-19

### Version Scope

Version `0.91` formalizes the hook event surface. The goal is to move hooks
from ad hoc event names toward a documented lifecycle contract.

New behavior:

- `mini_cc.hooks` now defines a v2 hook event catalog:
  - event name;
  - required payload fields;
  - optional payload fields;
  - matcher field;
  - description;
- new lifecycle events have structured specs:
  - `UserPromptSubmit`;
  - `InstructionsLoaded`;
  - `SessionStart`;
  - `SessionEnd`;
  - `PreToolUse`;
  - `PostToolUse`;
  - `PostToolUseFailure`;
  - `PostToolBatch`;
  - `PermissionRequest`;
  - `PermissionDenied`;
  - `SubagentStart`;
  - `SubagentStop`;
  - `TaskCreated`;
  - `TaskCompleted`;
  - `PreCompact`;
  - `PostCompact`;
  - `FileChanged`;
  - `CwdChanged`;
  - `WorktreeCreate`;
  - `WorktreeRemove`;
  - `ConfigChange`;
  - `Notification`;
  - `Stop`;
  - `StopFailure`;
  - `Elicitation`;
  - `ElicitationResult`;
  - `TeammateIdle`;
- `HookRuntime.emit()` validates payloads against the catalog and records
  `_payload_errors` for incomplete payloads without breaking backward
  compatibility;
- configured command hook JSON now includes `schema_version`;
- `HookRuntime` adds helper methods for structured prompt/session,
  permission, tool failure, batch, task, compaction, and file-change events;
- `Agent.run()` emits v2 `SessionStart` payloads.

Important files:

- `mini_cc.hooks`: event catalog, payload validation, v2 JSON, and helper
  methods;
- `mini_cc.agent`: v2 session-start hook emission;
- `tests.test_runtime_modules`: event catalog, validation, payload error, and
  structured helper coverage;
- `README.md` and `docs/architecture.md`: hook event surface documentation.

### Real Test Status

Targeted hook/subagent tests:

```text
Ran 30 tests in 0.392s
OK
```

Full test suite:

```text
Ran 104 tests in 4.385s
OK
```

### Interpretation

This version defines the lifecycle surface needed by later permission,
compaction, subagent, and benchmark governance work. It does not yet wire every
new event into every runtime path; that should be done incrementally as the
corresponding subsystems are upgraded.

## 0.90 - Terminal-Bench Real Run Pipeline

Date: 2026-06-19

### Version Scope

Version `0.90` adds a real-run pipeline around Terminal-Bench automation. The
goal is to catch environment and command issues before launching a real shard
run.

New behavior:

- `run_terminal_bench_real_pipeline()` wraps benchmark automation with a
  preflight stage;
- `terminal-bench-preflight.json` records:
  - loaded task count;
  - command preview;
  - output directory;
  - preflight checks;
- preflight checks cover:
  - non-empty task list;
  - valid shard size;
  - command template contains `{output_dir}` and a task selector;
  - first command executable is available;
  - output parent directory exists;
  - Docker health unless `--tb-dry-run` is enabled;
- CLI adds:
  - `--terminal-bench-real-run`;
  - `--tb-preflight-only`;
  - `--tb-skip-preflight`;
- real runs stop before executing shard commands when preflight fails, unless
  explicitly skipped.

Important files:

- `mini_cc.bench`: Terminal-Bench preflight model, real-run pipeline, and JSON
  serialization;
- `mini_cc.cli`: real-run pipeline entrypoint;
- `tests.test_bench_runner`: preflight pass/fail, skip-preflight, and
  preflight-only coverage;
- `tests.test_cli`: real-run argument coverage;
- `README.md` and `docs/architecture.md`: real-run pipeline docs.

### Real Test Status

Targeted benchmark/CLI tests:

```text
Ran 35 tests in 0.120s
OK
```

Full test suite:

```text
Ran 100 tests in 4.479s
OK
```

### Interpretation

This version makes the difference between planning, automation, and real
execution explicit. It still does not install Terminal-Bench or Docker; it
verifies that the configured command and local environment are ready before a
real run starts.

## 0.89 - Benchmark Automation v2

Date: 2026-06-19

### Version Scope

Version `0.89` upgrades benchmark support from separate shard/report commands
to an automated run-and-gate loop.

New behavior:

- `run_benchmark_automation()` runs the full Terminal-Bench workflow:
  - shard planning/execution;
  - Docker health gate;
  - shard/task resume;
  - environment-only retry;
  - aggregate summary;
  - JSON/Markdown benchmark report;
  - automation artifact;
- `benchmark-automation.json` records:
  - artifact paths;
  - score summary;
  - shard statuses;
  - gate results;
- automation gates check:
  - all planned shards reached an ok terminal status;
  - task results were parsed;
  - generated report is valid unless explicitly allowed;
  - optional target score such as `0.99`;
- CLI adds:
  - `--benchmark-automation`;
  - `--benchmark-target-score`;
  - `--benchmark-allow-invalid`;
- existing `--terminal-bench-shards` and `--benchmark-report` commands remain
  available for manual staged operation.

Important files:

- `mini_cc.bench`: benchmark automation dataclasses, gate evaluation, JSON
  serialization, and full-loop runner;
- `mini_cc.cli`: one-command benchmark automation entrypoint;
- `tests.test_bench_runner`: automation artifact and score-gate coverage;
- `tests.test_cli`: automation argument coverage;
- `README.md` and `docs/architecture.md`: automation workflow docs.

### Real Test Status

Targeted benchmark/CLI tests:

```text
Ran 30 tests in 0.083s
OK
```

Full test suite:

```text
Ran 95 tests in 4.368s
OK
```

### Interpretation

This version makes benchmark runs easier to reproduce and compare because every
automation run produces a gate summary and artifact manifest. It still depends
on the external Terminal-Bench installation and Docker stability for real
benchmark execution.

## 0.88 - Context Memory v2

Date: 2026-06-19

### Version Scope

Version `0.88` upgrades project memory from plain key/value text into
structured context facts and connects memory recall to context snapshots.

New behavior:

- `mini_cc.memory` introduces structured `MemoryFact` records with:
  - `key`;
  - `value`;
  - `scope`;
  - `priority`;
  - `source`;
  - `tags`;
  - `updated_at`;
- legacy `.mini_cc/memory.json` key/value files remain readable;
- `memory_write` still accepts `key` and `value`, and now also accepts optional
  `scope`, `priority`, `source`, and `tags`;
- new `memory_recall` selects facts by query, scope, minimum priority, and
  limit;
- `context_snapshot` accepts:
  - `query`;
  - `memory_limit`;
- `ContextBuilder` uses `memory_recall` when available and adds a
  `# Memory Recall` section instead of dumping all durable memory;
- context snapshots include the task query as its own section when supplied.

Important files:

- `mini_cc.memory`: structured memory normalization, serialization, recall, and
  formatting;
- `mini_cc.s20`: v2 memory tool schemas and runtime integration;
- `mini_cc.context`: query-aware memory recall in context snapshots;
- `tests.test_s20`: v2 metadata, legacy migration, recall, and context-memory
  integration coverage;
- `tests.test_context`: context builder recall coverage;
- `README.md` and `docs/architecture.md`: Context Memory v2 documentation.

### Real Test Status

Targeted context/memory tests:

```text
Ran 11 tests in 0.210s
OK
```

Full test suite:

```text
Ran 92 tests in 4.359s
OK
```

### Interpretation

This version separates durable project facts from raw prompt context. It makes
memory more auditable and lets long-task snapshots include relevant facts under
a token budget. It does not yet implement rolling conversation/tool-result
memory or model-generated semantic summarization.

## 0.87 - Subagent Orchestration v2

Date: 2026-06-19

### Version Scope

Version `0.87` upgrades subagent orchestration from fixed pipelines to
capability-aware pipeline decisions.

New behavior:

- `SubagentSpec` now supports `capabilities`;
- capabilities can be configured per subagent;
- capabilities are inferred for older configs from:
  - subagent name;
  - description;
  - allowed tools;
- `PipelineStep` now records:
  - `phase`;
  - `parallel_group`;
- `PipelineDecision` now records the capability registry used for the decision;
- standard pipeline planning now selects:
  - read-only exploration from capability registry;
  - implementer for change-capable tasks;
  - verifier after execution;
  - critic only for change-oriented tasks such as fix/edit/implement/refactor;
- benchmark pipeline planning can add verifier after bench diagnosis when
  available;
- read-only discovery steps are marked with
  `parallel_group=read-only-discovery`;
- phase handoff now passes structured JSON instead of plain appended text;
- `pipeline-decisions.jsonl` includes capabilities, phase, and parallel group
  metadata.

Important files:

- `mini_cc.subagents`: capability registry, inferred capabilities, v2 pipeline
  planner, structured handoff, enriched pipeline decision log;
- `tests.test_subagents`: capability selection, configured capabilities,
  conditional critic, benchmark phase, and read-only group coverage;
- `README.md`: subagent pipeline v2 docs;
- `docs/architecture.md`: subagent orchestration status.

### Real Test Status

Targeted subagent tests:

```text
Ran 20 tests in 0.304s
OK
```

Full test suite:

```text
Ran 88 tests in 4.245s
OK
```

### Interpretation

This version makes subagent routing explainable and capability-aware. It does
not yet execute read-only discovery groups concurrently; it records them as
parallel-ready groups for a later bounded-concurrency runner.

## 0.86 - Planner Executor Verifier Layering

Date: 2026-06-19

### Version Scope

Version `0.86` adds a lightweight Planner / Executor / Verifier layer around
S20 agent runs.

New behavior:

- `mini_cc.workflow` introduces:
  - `Planner`;
  - `Executor`;
  - `Verifier`;
  - `StructuredWorkflow`;
- S20 mode now enables `StructuredWorkflow` by default;
- the planner creates a conservative pre-run plan:
  - inspect;
  - execute;
  - verify;
  - report for benchmark-like tasks;
- the executor classifies each tool call against the active plan:
  - inspect tools;
  - execute tools;
  - verify tools;
- the verifier writes a post-run summary:
  - whether tool failures occurred;
  - whether an explicit verification signal ran;
  - which tools failed;
  - which verification tools succeeded;
- session JSON files now record:
  - `planner_plan`;
  - `executor_tool_use`;
  - `verifier_result`;
- benchmark-like prompts are marked as `benchmark` mode and require explicit
  verification/report signal before the run is treated as verified.

Important files:

- `mini_cc.workflow`: plan/execution/verification models and logic;
- `mini_cc.agent`: workflow event integration around the model/tool loop;
- `mini_cc.cli`: S20 agent construction now passes `StructuredWorkflow`;
- `tests.test_workflow`: planner, verifier, and session-event coverage;
- `README.md`: structured workflow docs;
- `docs/architecture.md`: workflow module and runtime flow.

### Real Test Status

Targeted workflow/agent tests:

```text
Ran 5 tests in 0.065s
OK
```

Full test suite:

```text
Ran 86 tests in 4.244s
OK
```

### Interpretation

This version does not replace the core model/tool loop. It adds auditable
planner, executor, and verifier boundaries around S20 runs so later versions can
move from heuristic plans to model-authored plans, plan-deviation tracking, and
stricter verification policy.

## 0.85 - MCP Auth Governance

Date: 2026-06-19

### Version Scope

Version `0.85` adds authentication governance for remote MCP servers.

New behavior:

- Streamable HTTP MCP auth can load from environment variables:
  - `auth_token_env`;
  - `bearer_token_env`;
  - `headers_env`;
- `auth_token_env` and `bearer_token_env` are used as bearer tokens without
  writing token values into settings files;
- `headers_env` maps HTTP header names to environment variable names;
- inline `auth_token`, `bearer_token`, and static sensitive headers still work
  for compatibility;
- `--diagnose-config` now warns when MCP server config stores:
  - direct auth tokens;
  - sensitive headers such as `Authorization`, `Proxy-Authorization`,
    `X-API-Key`, or `API-Key`;
- MCP audit rows redact sensitive content from:
  - `content_preview`;
  - nested `detail` dictionaries;
  - token/auth/API key/secret fields.

Example:

```json
{
  "name": "remote",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "auth_token_env": "MCP_REMOTE_TOKEN",
  "headers_env": {
    "X-API-Key": "MCP_REMOTE_API_KEY"
  }
}
```

Important files:

- `mini_cc.subagents`: env-based MCP auth/header loading;
- `mini_cc.governance`: inline MCP secret diagnostics;
- `mini_cc.mcp`: audit secret redaction;
- `tests.test_subagents`: env auth/header config coverage;
- `tests.test_governance`: inline secret warning coverage;
- `tests.test_mcp`: audit redaction coverage;
- `README.md`: env-based MCP auth docs;
- `docs/architecture.md`: auth governance status.

### Real Test Status

Targeted MCP/auth/governance tests:

```text
Ran 35 tests in 3.807s
OK
```

Full test suite:

```text
Ran 82 tests in 4.140s
OK
```

### Interpretation

This version avoids committing remote MCP secrets to project settings and keeps
audit logs from becoming a secret sink. It does not implement full OAuth
browser, device-code, token refresh, or consent flows.

## 0.84 - MCP Security Hardening

Date: 2026-06-19

### Version Scope

Version `0.84` hardens MCP policy, auditability, and schema validation.

New behavior:

- MCP policy matching now supports:
  - exact matches;
  - shell-style wildcard patterns such as `unsafe-*`;
  - prefix patterns such as `prefix:resource://public/`;
  - resource shorthand prefixes such as `resource://public/*`;
- high-risk MCP tools are blocked by default unless explicitly allowed:
  - `write`;
  - `delete`;
  - `exec`;
  - `shell`;
  - `run`;
  - `update`;
  - `drop`;
  - related destructive tokens;
- `MCPPolicy.block_high_risk_tools` can disable or keep this default;
- MCP audit rows now include:
  - generated `request_id`;
  - `subagent`;
  - `session_id`;
  - `handoff_id`;
  - remote `mcp_session_id` when present;
- subagent runs inject subagent/handoff context into governed MCP adapters;
- subagent system prompts now include MCP capability summaries:
  - tools;
  - resources;
  - prompts;
- MCP schema validation is now recursive for:
  - nested objects;
  - required nested fields;
  - arrays with typed `items`;
  - primitive JSON types;
  - nullable unions;
  - enum values.

Important files:

- `mini_cc.mcp`: wildcard/prefix policy matching, high-risk tool guard,
  enriched audit rows, capability summaries, recursive schema guard;
- `mini_cc.subagents`: MCP audit context injection and capability summaries in
  subagent prompts;
- `tests.test_mcp`: pattern policy, high-risk blocking, audit context, nested
  schema validation;
- `tests.test_subagents`: subagent MCP capability prompt and audit context
  coverage;
- `README.md`: MCP security configuration and behavior;
- `docs/architecture.md`: MCP security status.

### Real Test Status

Targeted MCP/subagent tests:

```text
Ran 29 tests in 3.820s
OK
```

Full test suite:

```text
Ran 79 tests in 4.124s
OK
```

### Interpretation

This version focuses on making MCP safer as an external capability boundary.
It still does not implement OAuth flows, signed/trusted server metadata, or
full JSON Schema features such as formats, bounds, oneOf/anyOf, and
additionalProperties.

## 0.83 - MCP Reliability And Schema Guards

Date: 2026-06-18

### Version Scope

Version `0.83` strengthens remote MCP reliability and adds basic local tool
argument validation.

New behavior:

- Streamable HTTP MCP requests support configurable:
  - `max_retries`;
  - `retry_backoff`;
- transient HTTP failures are retried:
  - `408`;
  - `409`;
  - `425`;
  - `429`;
  - `500`;
  - `502`;
  - `503`;
  - `504`;
- session-scoped failures can recover automatically:
  - when a request with `Mcp-Session-Id` receives `401`, `403`, or `404`;
  - the adapter clears the old session id;
  - runs `initialize` again when enabled;
  - retries the original request with the new session id;
- `StdioMCPAdapter` and `StreamableHTTPMCPAdapter` cache tool schemas from
  `tools/list`;
- `tools/call` validates arguments before sending:
  - required object fields;
  - primitive JSON Schema types: `string`, `integer`, `number`, `boolean`,
    `array`, `object`, and nullable unions;
- configured Streamable HTTP MCP servers support:

```json
{
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "initialize": true,
  "max_retries": 2,
  "retry_backoff": 0.25
}
```

Important files:

- `mini_cc.mcp`: HTTP retry/session recovery and schema validation;
- `mini_cc.subagents`: retry/backoff config parsing;
- `tests.test_mcp`: schema validation, retry, and session recovery coverage;
- `tests.test_subagents`: retry/backoff config coverage;
- `README.md`: remote MCP reliability behavior;
- `docs/architecture.md`: MCP reliability status.

### Real Test Status

Targeted MCP/subagent tests:

```text
Ran 24 tests in 3.307s
OK
```

Full test suite:

```text
Ran 74 tests in 3.659s
OK
```

### Interpretation

This version makes remote MCP less brittle under transient server failures and
expired sessions. It still does not implement OAuth flows or full recursive
JSON Schema validation.

## 0.82 - Streamable HTTP MCP Transport

Date: 2026-06-18

### Version Scope

Version `0.82` adds a Streamable HTTP MCP transport while preserving the
governance and protocol normalization from `0.81`.

New behavior:

- `StreamableHTTPMCPAdapter` supports remote HTTP MCP servers;
- requests are JSON-RPC POSTs with:
  - `Content-Type: application/json`;
  - `Accept: application/json, text/event-stream`;
  - `MCP-Protocol-Version`;
  - optional `Mcp-Session-Id`;
- initialize can capture `Mcp-Session-Id` from the server and reuse it on later
  requests;
- JSON responses and `text/event-stream` responses are both parsed;
- auth/header injection is supported through:
  - `headers`;
  - `auth_token` / `bearer_token`;
- existing `MCPPolicy` and `GovernedMCPAdapter` work for HTTP adapters;
- configured `mcp_servers` now support:

```json
{
  "name": "remote",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "initialize": true,
  "protocol_version": "2025-06-18",
  "headers": {"X-Client": "mini-cc"},
  "auth_token": "token",
  "policy": {
    "allowed_tools": ["search"]
  },
  "audit_log": ".mini_cc/mcp-audit.jsonl"
}
```

Important files:

- `mini_cc.mcp`: `StreamableHTTPMCPAdapter`, JSON/SSE response parsing,
  session-id handling, HTTP headers/auth;
- `mini_cc.subagents`: `streamable_http` / `http` MCP server config parsing;
- `tests.test_mcp`: local HTTP server tests for JSON, SSE, protocol headers,
  and session reuse;
- `tests.test_subagents`: Streamable HTTP config parsing coverage;
- `README.md`: remote MCP config example;
- `docs/architecture.md`: MCP transport status update.

### Real Test Status

Targeted MCP/subagent tests:

```text
Ran 21 tests in 2.121s
OK
```

Full test suite:

```text
Ran 71 tests in 2.417s
OK
```

### Interpretation

This version expands MCP from local process transport to remote server
transport. It does not yet implement OAuth flows, retry/backoff, remote session
expiry recovery, or full JSON Schema argument validation.

## 0.81 - MCP Adapter Governance And Protocol Normalization

Date: 2026-06-18

### Version Scope

Version `0.81` strengthens the MCP adapter layer without adding a new network
transport.

New behavior:

- MCP protocol surface now includes:
  - `tools/list`;
  - `tools/call`;
  - `resources/list`;
  - `resources/read`;
  - `prompts/list`;
  - `prompts/get`;
- `StdioMCPAdapter` accepts configurable `protocol_version`;
- initialize now reports client version `0.81`;
- tool input schemas are normalized to object schemas with stable
  `properties`;
- prompt responses with `messages` are rendered into readable text;
- `MCPPolicy` can allow/block:
  - tools;
  - resources;
  - prompts;
- `GovernedMCPAdapter` wraps any MCP adapter with policy filtering and runtime
  blocking;
- optional MCP audit logs record list/read/call/get operations as JSONL rows;
- subagents now expose:
  - `mcp_list_prompts`;
  - `mcp_get_prompt`;
- configured `mcp_servers` support:

```json
{
  "name": "local",
  "transport": "stdio",
  "command": ["python", "scripts/fake_mcp_server.py"],
  "initialize": true,
  "protocol_version": "2024-11-05",
  "policy": {
    "allowed_tools": ["echo"],
    "allowed_resources": ["resource://note"],
    "blocked_prompts": ["unsafe_prompt"]
  },
  "audit_log": ".mini_cc/mcp-audit.jsonl"
}
```

Important files:

- `mini_cc.mcp`: prompt model, policy model, governed adapter, audit logger,
  schema normalization, prompt rendering;
- `mini_cc.subagents`: prompt tools and MCP config policy parsing;
- `tests.test_mcp`: prompt, policy, schema, and audit coverage;
- `tests.test_subagents`: prompt tool and configured policy coverage;
- `README.md`: MCP governance config;
- `docs/architecture.md`: MCP status and remaining transport work.

### Real Test Status

Targeted MCP/subagent tests:

```text
Ran 18 tests in 1.038s
OK
```

Full test suite:

```text
Ran 68 tests in 1.318s
OK
```

### Interpretation

This version makes MCP safer and more uniform, but it is still stdio-only.
Remote Streamable HTTP MCP transport, auth/header handling, and deeper JSON
Schema validation remain future work.

## 0.80 - Benchmark Reporting Loop

Date: 2026-06-18

### Version Scope

Version `0.80` closes the benchmark/reporting loop for Terminal-Bench shard
runs.

New behavior:

- `mini_cc.bench.build_benchmark_report()` reads:
  - `shard-manifest.json`;
  - every `shard-*/results.json`;
- report generation writes:
  - `benchmark-report.json`;
  - `benchmark-report.md`;
- the report includes:
  - total tasks, resolved tasks, and score;
  - category breakdown;
  - per-shard status and score;
  - unresolved task list with failure category and reason;
  - invalid-run flags for missing results or environment/setup dominated runs;
  - next-action recommendations;
- CLI exposes:

```powershell
py -3 -m mini_cc --benchmark-report terminal-bench-shards
```

Optional output directory:

```powershell
py -3 -m mini_cc --benchmark-report terminal-bench-shards --benchmark-report-output reports
```

Important files:

- `mini_cc.bench`: benchmark report model, JSON serializer, Markdown renderer,
  invalid-run detector, and recommendation builder;
- `mini_cc.cli`: `--benchmark-report` and `--benchmark-report-output`;
- `tests.test_bench_runner`: report parsing and output tests;
- `tests.test_cli`: benchmark report CLI argument test;
- `README.md`: report command usage;
- `docs/architecture.md`: benchmark reporting status.

### Real Test Status

Targeted benchmark-report tests:

```text
Ran 27 tests in 0.058s
OK
```

Full test suite:

```text
Ran 67 tests in 1.090s
OK
```

### Interpretation

This version does not claim a new Terminal-Bench score by itself. It adds the
reporting layer needed after real shard runs so environment failures, harness
failures, model timeouts, and real task/test failures are separated before
architecture decisions are made.

## 0.79 - Config And Permission Governance

Date: 2026-06-18

### Version Scope

Version `0.79` adds centralized config governance and configurable permission
policy.

New behavior:

- centralized settings loader in `mini_cc.governance`;
- settings load order:
  - `.claude/settings.json`;
  - `.mini_cc/settings.json`;
  - `.mini_cc/settings.local.json`;
- later settings override earlier settings through recursive merge;
- config diagnostics report:
  - loaded paths;
  - validation issues;
  - merged config;
- CLI exposes `--diagnose-config`;
- `permission_policy` can configure risk allow/block overrides;
- `ToolRunner` and `S20ToolRunner` accept a `PermissionPolicy`.

Example:

```json
{
  "permission_policy": {
    "block_risks": ["network", "docker", "git_remote_write"],
    "allow_risks": ["verify"]
  }
}
```

Important files:

- `mini_cc.governance`: config loading, recursive merge, validation diagnostics;
- `mini_cc.permission`: `PermissionPolicy`;
- `mini_cc.tools`: configurable policy enforcement;
- `mini_cc.s20`: policy passthrough;
- `mini_cc.cli`: `--diagnose-config` and governance wiring;
- `tests.test_governance`: merge, diagnostics, and permission override tests;
- `README.md`: config diagnosis and permission policy docs;
- `docs/architecture.md`: governance module status.

### Real Test Status

Targeted governance tests:

```text
Ran 3 tests in 0.008s
OK
```

Full test suite:

```text
Ran 64 tests in 1.056s
OK
```

New tests added:

- recursive config merge preserves and overrides nested values correctly;
- config loader reports unknown keys;
- `.mini_cc/settings.local.json` overrides project settings;
- permission policy can override default read-only behavior.

### Fix Found During Testing

The first implementation blocked configured risks correctly, but the error
message still showed the shell classifier reason instead of the configured
policy reason. `_require_permission()` now preserves the governance decision
reason so failures are explainable.

### Impact

This version starts turning separate knobs into a governed runtime:

- hooks, subagents, MCP, and permission settings now share a config view;
- permission behavior can be adjusted without editing Python code;
- config mistakes are visible through `--diagnose-config`;
- policy-driven denials explain which governance rule caused the denial.

### Remaining Gap

Still missing:

- user-level settings outside the workspace;
- stricter schema validation with exact field paths;
- config-driven hook inheritance/override semantics;
- full permission policy for command-specific matchers;
- config migration/versioning.

## 0.78 - MCP Transport Hardening

Date: 2026-06-18

### Version Scope

Version `0.78` strengthens the stdio MCP transport.

New behavior:

- `StdioMCPAdapter` now keeps a long-lived `Popen` process instead of launching
  a fresh process for every JSON-RPC request;
- stdout is read through a background pump thread and response queue;
- optional `initialize` sends an MCP-style initialize request and stores
  returned capabilities;
- pipe/server failures close the process and retry the request once;
- exited one-shot servers are detected quickly instead of waiting for the full
  timeout;
- stdio pipes are closed explicitly during adapter shutdown;
- subagent config supports `initialize` for stdio MCP servers.

Supported methods remain:

- `initialize`;
- `tools/list`;
- `tools/call`;
- `resources/list`;
- `resources/read`.

Important files:

- `mini_cc.mcp`: long-lived stdio process, initialize/capabilities, restart
  path, response queue, explicit close;
- `mini_cc.subagents`: `mcp_servers[].initialize` config support;
- `tests.test_mcp`: long-lived process reuse and initialize tests;
- `README.md`: documents initialize/timeout config;
- `docs/architecture.md`: updates MCP transport status.

### Real Test Status

Targeted MCP tests:

```text
Ran 3 tests in 0.509s
OK
```

Full test suite:

```text
Ran 60 tests in 1.069s
OK
```

New tests added:

- long-lived stdio process is reused across multiple requests;
- initialize stores server capabilities;
- previous one-shot server behavior still works through restart recovery.

### Fix Found During Testing

The first implementation passed assertions but took over 30 seconds because a
one-shot fake server exited after replying and the client waited for the full
timeout before retrying. This was fixed by detecting process exit while waiting
for a response and failing fast.

### Impact

This version makes MCP transport much closer to a real external tool client:

- repeated MCP calls no longer pay process startup cost;
- server capabilities can be negotiated and stored;
- dead server processes are recovered without poisoning later calls;
- transport failures are faster and easier to diagnose.

### Remaining Gap

Still missing:

- HTTP/SSE MCP transports;
- full MCP initialize/notification lifecycle;
- strict schema validation;
- pooled server lifecycle shared across multiple adapters;
- richer stderr capture for long-running servers.

## 0.77 - Multi-Subagent Pipeline Strategy

Date: 2026-06-18

### Version Scope

Version `0.77` adds a conservative multi-subagent pipeline strategy.

New behavior:

- S20 exposes `subagent_pipeline`;
- `subagent_pipeline` supports modes:
  - `auto`;
  - `standard`;
  - `benchmark`;
- `auto` selects `benchmark` mode for benchmark, Terminal-Bench, harness,
  Docker, results, or score tasks;
- `standard` mode runs:
  - `explorer`;
  - `implementer`;
  - `verifier`;
  - `critic`;
- `benchmark` mode runs:
  - `bench-diagnoser`;
- each step receives the previous subagent output as context;
- pipeline decisions are written to
  `.mini_cc/subagents/pipeline-decisions.jsonl`.

Important files:

- `mini_cc.subagents`: `PipelineStep`, `PipelineDecision`, pipeline planning,
  mode selection, decision logging;
- `mini_cc.s20`: exposes `subagent_pipeline`;
- `tests.test_subagents`: standard pipeline, benchmark pipeline, and S20 schema
  tests;
- `README.md`: documents pipeline usage;
- `docs/architecture.md`: updates subagent planning status.

### Real Test Status

Targeted subagent tests:

```text
Ran 14 tests in 0.287s
OK
```

Full test suite:

```text
Ran 58 tests in 0.837s
OK
```

New tests added:

- standard pipeline records decision and runs explorer/implementer/verifier/critic;
- benchmark keyword task selects bench-diagnoser only;
- S20 schemas expose `subagent_pipeline`.

### Impact

This version changes subagents from manually callable roles into an executable
workflow.

Practical impact:

- common coding tasks can follow an explore/implement/verify/critic flow;
- benchmark/environment tasks are routed to the diagnostic subagent;
- each pipeline decision is auditable;
- handoff/session records still work for every child subagent step.

### Remaining Gap

Still missing:

- dynamic model-driven planning;
- configurable pipeline definitions;
- parallel or conditional branches;
- richer failure recovery between pipeline steps;
- long-lived MCP process pooling and HTTP/SSE transports.

## 0.76 - External MCP Stdio Transport

Date: 2026-06-18

### Version Scope

Version `0.76` adds an external stdio MCP transport for subagent-scoped MCP
tools and resources.

New behavior:

- `StdioMCPAdapter` can start an external command and communicate over stdin /
  stdout with newline-delimited JSON-RPC;
- supported methods:
  - `tools/list`;
  - `tools/call`;
  - `resources/list`;
  - `resources/read`;
- subagent config can declare stdio MCP servers with `mcp_servers`;
- MCP tools are exposed through the existing `mcp__server__tool` naming scheme;
- MCP resources remain available through `mcp_list_resources` and
  `mcp_read_resource`.

Example config:

```json
{
  "subagents": {
    "reader": {
      "description": "Read-only project explorer",
      "system_prompt": "Read files and call local MCP tools.",
      "tools": ["list_files", "mcp__local__echo"],
      "mcp_servers": [
        {
          "name": "local",
          "transport": "stdio",
          "command": ["python", "scripts/fake_mcp_server.py"],
          "timeout": 10
        }
      ]
    }
  }
}
```

Important files:

- `mini_cc.mcp`: `StdioMCPAdapter`, JSON-RPC request/response handling,
  content rendering;
- `mini_cc.subagents`: `mcp_servers` config parsing;
- `tests.test_mcp`: external stdio MCP transport test;
- `tests.test_subagents`: configured MCP server test;
- `README.md`: stdio MCP server config example;
- `docs/architecture.md`: updates MCP runtime status.

### Real Test Status

Targeted MCP transport tests:

```text
Ran 1 test in 0.259s
OK
```

Targeted subagent tests:

```text
Ran 11 tests in 0.209s
OK
```

Full test suite:

```text
Ran 55 tests in 0.736s
OK
```

New tests added:

- stdio MCP adapter lists tools from an external process;
- stdio MCP adapter calls a tool through JSON-RPC;
- stdio MCP adapter lists resources;
- stdio MCP adapter reads resources;
- configured subagent MCP server creates an adapter and exposes its tools.

### Impact

This version moves MCP from an in-memory teaching abstraction to a real process
boundary.

Practical impact:

- subagents can call external MCP-like servers without modifying Python code;
- MCP tools stay scoped by subagent allowlists;
- resource access uses the same subagent boundary and audit path;
- fake MCP servers can be used in tests and future benchmark harnesses.

### Remaining Gap

Still missing:

- long-lived MCP process pooling;
- MCP initialize/notifications/capability negotiation;
- HTTP/SSE MCP transports;
- richer error recovery and server lifecycle management;
- strict MCP schema validation.

## 0.75 - Subagent Handoff And Session Index

Date: 2026-06-18

### Version Scope

Version `0.75` adds parent-child task handoff records and a subagent session
index.

New behavior:

- every `subagent_run` gets a unique handoff id;
- subagent start/stop hook payloads include the handoff id;
- completed subagent runs write `.mini_cc/subagents/handoffs.jsonl`;
- completed subagent runs update `.mini_cc/subagents/session-index.json`;
- each handoff row records:
  - handoff id;
  - timestamp;
  - subagent name;
  - prompt;
  - status;
  - output preview;
  - child session id;
  - model;
- handoff rows link to the child session file under
  `.mini_cc/subagents/<name>/sessions/<session_id>.json`.

Important files:

- `mini_cc.subagents`: `SubagentHandoff`, handoff log, session index, child
  session id lookup;
- `tests.test_subagents`: handoff/session-index link test;
- `README.md`: documents handoff files;
- `docs/architecture.md`: updates subagent runtime status.

### Real Test Status

Targeted subagent tests:

```text
Ran 11 tests in 0.143s
OK
```

Full test suite:

```text
Ran 54 tests in 0.432s
OK
```

New tests added:

- `subagent_run` writes `handoffs.jsonl`;
- `subagent_run` writes `session-index.json`;
- handoff row contains subagent name, prompt, status, output preview, and
  session id;
- recorded session id points to an actual child session file.

### Impact

This version makes multi-agent execution inspectable.

Practical impact:

- parent-to-subagent calls are no longer only visible in console output;
- child sessions can be traced from a single index file;
- future multi-subagent planners can use handoff records as durable execution
  history;
- benchmark failures involving subagents can be audited by following the
  handoff id to the exact child session.

### Remaining Gap

Still missing:

- parent session event that records the handoff id at the exact parent tool call;
- multi-subagent planning policies;
- real external MCP server transport;
- richer handoff status recording for exception paths.

## 0.74 - Configured Subagents And Local Hooks

Date: 2026-06-18

### Version Scope

Version `0.74` adds project-configured subagent definitions and subagent-local
hook config.

New behavior:

- subagents can be defined in `.mini_cc/settings.json`;
- subagents can be defined in `.mini_cc/settings.local.json`;
- subagents can be defined in `.claude/settings.json`;
- configured subagents with the same name replace built-in defaults;
- configured subagents support:
  - `description`;
  - `system_prompt` or `prompt`;
  - `tools` or `allowed_tools`;
  - `model`;
  - `memory`;
  - `max_turns`;
- each subagent can load local hook config from:
  - `.mini_cc/subagents/<name>/hooks.json`;
  - `.mini_cc/subagents/<name>/settings.json`.

Example config:

```json
{
  "subagents": {
    "reader": {
      "description": "Read-only project explorer",
      "system_prompt": "Read files and report concrete facts.",
      "tools": ["list_files", "read_file", "search_text"],
      "model": "small-model",
      "memory": {"mode": "configured"},
      "max_turns": 3
    }
  }
}
```

Important files:

- `mini_cc.subagents`: configured subagent parser, runtime config loading,
  subagent-local hook loading;
- `tests.test_subagents`: config parser, default override, and local hook tests;
- `README.md`: documents configured subagents and local hook paths;
- `docs/architecture.md`: updates subagent runtime status.

### Real Test Status

Targeted subagent tests:

```text
Ran 10 tests in 0.126s
OK
```

Full test suite:

```text
Ran 53 tests in 0.418s
OK
```

New tests added:

- load subagent specs from settings payload;
- runtime loads configured subagents from `.mini_cc/settings.json`;
- configured subagent with a built-in name overrides the default;
- subagent-local hook config can block a matched tool call.

### Impact

This version makes subagents project-customizable without editing Python code.

Practical impact:

- a project can define its own role-specific subagents;
- built-in subagents can be tightened or replaced;
- subagent-local hooks can enforce role-specific policies;
- project-level settings now control both parent hooks and subagent definitions.

### Remaining Gap

Still missing:

- user-level config precedence beyond project files;
- schema validation with detailed error messages;
- parent-child task handoff records;
- multi-subagent planning policies;
- real external MCP server transport.

## 0.73 - Subagent Runtime Boundaries

Date: 2026-06-18

### Version Scope

Version `0.73` expands the `0.7` subagent design so each subagent has its own
runtime boundary.

New behavior:

- each subagent gets a private `HookRuntime`;
- each subagent writes its own `hooks.log` when a state directory is available;
- each subagent gets a private `SessionStore`;
- subagent sessions are stored under `.mini_cc/subagents/<name>/sessions`;
- each subagent has private memory tools:
  - `subagent_memory_read`;
  - `subagent_memory_write`;
- each subagent can receive MCP adapters;
- MCP adapter tools are exposed as `mcp__server__tool`;
- MCP resources are exposed through:
  - `mcp_list_resources`;
  - `mcp_read_resource`;
- subagent tool calls pass through the subagent's own hooks before/after tool
  execution.

Important files:

- `mini_cc.mcp`: lightweight MCP-like adapter interface and in-memory adapter;
- `mini_cc.subagents`: private hooks, sessions, memory tools, MCP tools, and
  resource access;
- `mini_cc.cli`: passes `.mini_cc/subagents` as the subagent state root;
- `tests.test_subagents`: memory, MCP, hook, and session isolation tests;
- `README.md`: documents subagent runtime boundaries;
- `docs/architecture.md`: updates subagent and MCP module status.

### Real Test Status

Targeted subagent tests:

```text
Ran 7 tests in 0.027s
OK
```

Full test suite:

```text
Ran 50 tests in 0.316s
OK
```

New tests added:

- subagent private memory tools read/write only that subagent's memory;
- MCP tools are exposed with `mcp__server__tool` names;
- MCP resources can be listed and read through the subagent tool runner;
- subagent hook log is written under the subagent's private state directory;
- subagent sessions are written under the subagent's private sessions directory.

### Impact

This version makes subagents closer to a real runtime unit instead of just a
named prompt/tool allowlist.

Practical impact:

- subagent runs can be audited independently;
- subagent sessions can be inspected independently;
- subagent memory can evolve without mixing with parent memory or sibling
  subagent memory;
- MCP-style tools and resources can be scoped per subagent;
- subagent-specific hooks can enforce or observe behavior before the parent
  runtime sees only the final result.

### Remaining Gap

Still missing:

- configured subagent definitions from project/user settings;
- configured subagent hooks from subagent-local settings;
- real external MCP server transport;
- parent-child task handoff records;
- multi-subagent planning policies.

## 0.7 - Subagents

Date: 2026-06-18

### Version Scope

Version `0.7` adds a subagent runtime to S20 mode.

New behavior:

- S20 exposes `subagent_list`;
- S20 exposes `subagent_run`;
- each subagent has its own system prompt;
- each subagent has a tool allowlist enforced at runtime;
- each subagent can specify a model override;
- each subagent has an independent memory dictionary injected into its prompt;
- each `subagent_run` creates an isolated Agent instance and provider instance.

Built-in subagents:

- `explorer`: read-only fact gathering;
- `implementer`: focused edits and local checks;
- `verifier`: targeted verification;
- `critic`: regression and overfitting review;
- `bench-diagnoser`: benchmark/environment failure diagnosis.

Important files:

- `mini_cc.subagents`: `SubagentSpec`, `RestrictedToolRunner`,
  `SubagentRuntime`, built-in subagent definitions;
- `mini_cc.s20`: adds `subagent_list` and `subagent_run` tools;
- `mini_cc.cli`: configures S20 subagents with independent provider creation;
- `tests.test_subagents`: subagent isolation tests;
- `README.md`: subagent usage;
- `docs/architecture.md`: updates runtime architecture status.

### Real Test Status

Targeted subagent tests:

```text
Ran 4 tests in 0.011s
OK
```

Full test suite:

```text
Ran 47 tests in 0.324s
OK
```

New tests added:

- restricted tool runner exposes only allowlisted tools;
- disallowed subagent tools are blocked at runtime;
- `subagent_run` executes with isolated provider and scoped tools;
- model override reaches the provider factory;
- subagent memories remain independent.

### Impact

This version changes the architecture from a single flat Agent loop to a
parent-agent plus callable subagent model.

Practical impact:

- exploration, implementation, verification, critique, and benchmark diagnosis
  can be separated by role;
- read-only subagents cannot perform edits even if prompted to do so;
- real providers are created per subagent run, avoiding shared response-state
  contamination;
- model overrides can be attached to specific subagent roles later.

### Remaining Gap

Still missing:

- configured subagent definitions from project/user settings;
- subagent-specific persisted sessions;
- parent-child task handoff records;
- multi-subagent planning policies;
- richer memory tools for subagents to update their own memory.

## 0.6 - Context Token Budget And Compression

Date: 2026-06-18

### Version Scope

Version `0.6` adds explicit token budgeting and deterministic compression for
workspace context snapshots.

New behavior:

- `context_snapshot` accepts an optional `token_budget`;
- context is represented as prioritized sections;
- each section receives a budget allocation;
- oversized sections are compressed by preserving head and tail content with an
  omission marker;
- snapshots include a `# Context Budget` report with requested budget,
  estimated tokens, and compressed sections;
- the context builder exposes reusable `estimate_tokens`, `compress_text`, and
  budgeted rendering helpers.

Important files:

- `mini_cc.context`: section model, token estimate, compression, budget report;
- `mini_cc.s20`: `context_snapshot` schema and implementation now accept
  `token_budget`;
- `tests.test_context`: budget/compression tests;
- `README.md`: context budget usage;
- `docs/architecture.md`: updates context runtime status.

### Real Test Status

Targeted context tests:

```text
Ran 4 tests in 0.105s
OK
```

Full test suite:

```text
Ran 43 tests in 0.311s
OK
```

New tests added:

- compression preserves head and tail content;
- compression marks omitted content;
- budgeted context reports compressed sections;
- high-priority small sections survive low-budget rendering;
- S20 `context_snapshot` accepts and reports a requested token budget.

### Impact

This version reduces random truncation risk in long tasks.

Before this version:

- context was assembled as one string;
- the final output was clipped by character count;
- important later sections could be lost accidentally.

After this version:

- context is budgeted by section;
- compression is explicit and visible;
- the output tells the agent which sections were compressed;
- task plans, memory, git status, and file listings are managed through a single
  budget-aware renderer.

### Remaining Gap

Still missing:

- rolling compression of conversation and tool-result history;
- task-contract and recent-tool-fact sections;
- model-specific token counters;
- semantic summarization of compressed sections through a model call.

## 0.5 - Task-Level Resume, Retries, And Score Aggregation

Date: 2026-06-18

### Version Scope

Version `0.5` expands the Terminal-Bench runner from shard-level recovery to
task-aware benchmark execution.

New behavior:

- reads each shard's `results.json`;
- skips already resolved task ids inside a shard when `--tb-resume` is enabled;
- reruns unresolved task ids from a partially completed shard;
- classifies each shard's `results.json` with the existing Terminal-Bench
  failure classifier;
- retries shards when their failures are environment-only;
- writes `aggregate-summary.json` with total tasks, resolved tasks, score, and
  category counts across all shard result files;
- exposes retry controls through CLI.

Important files:

- `mini_cc.bench`: task-level resume, environment-only retry,
  `load_terminal_bench_results`, `summarize_terminal_bench_results`,
  `aggregate_results`;
- `mini_cc.cli`: adds `--tb-no-task-resume`, `--tb-max-retries`, and
  `--tb-no-env-retry`;
- `tests.test_bench_runner`: adds task-resume, retry, and aggregation tests;
- `README.md`: documents task-level resume, retries, and aggregate summary;
- `docs/architecture.md`: updates benchmark runtime status.

### CLI Shape

Example:

```powershell
py -3 -m mini_cc `
  --terminal-bench-shards tasks.txt `
  --tb-command-template "tb run {task_args} --output-path {output_dir}" `
  --tb-shard-size 5 `
  --tb-output-dir terminal-bench-shards `
  --tb-resume `
  --tb-max-retries 1
```

Additional controls:

- `--tb-no-task-resume`: disable per-task resume from shard `results.json`;
- `--tb-max-retries N`: retry environment-only failed shards up to `N` times;
- `--tb-no-env-retry`: disable environment-only retry.

### Real Test Status

Targeted shard-runner tests:

```text
Ran 8 tests in 0.035s
OK
```

Full test suite:

```text
Ran 39 tests in 0.220s
OK
```

New tests added:

- task-level resume runs only unresolved task ids;
- environment-only failed shard is retried;
- retry can turn an environment failure into a passed shard;
- aggregate summary reads multiple shard `results.json` files;
- aggregate summary calculates total, resolved, score, and category counts.

### Impact

This version makes benchmark continuation materially cleaner:

- a partially completed shard no longer needs to rerun resolved tasks;
- Docker/network-style environment failures can be retried without poisoning the
  whole run;
- aggregate scoring is available without manually opening every shard output;
- result categories are produced by the same classifier used for individual
  Terminal-Bench result analysis.

### Remaining Gap

Still missing:

- native presets for specific Terminal-Bench CLI versions;
- bounded parallel shard execution;
- richer Markdown/HTML benchmark reports;
- merging multiple retry result histories instead of only reading the latest
  `results.json` per shard.

## 0.4 - Checkpoint And Resume

Date: 2026-06-18

### Version Scope

Version `0.4` adds manifest-based checkpoint/resume to the Terminal-Bench shard
runner.

New behavior:

- shard runs continue to write `shard-manifest.json`;
- `--tb-resume` reads the existing manifest before running;
- shards with matching `index`, matching `task_ids`, and status `passed` are
  skipped;
- resumed shards are recorded with status `resumed`;
- failed, planned, Docker-skipped, or mismatched shards are not treated as
  complete and will be attempted again;
- resumed shards do not trigger Docker health checks, because they do not need
  to run.

Important files:

- `mini_cc.bench`: `TerminalBenchShardRunner` now supports `resume=True` and
  reads completed shard checkpoints from `shard-manifest.json`;
- `mini_cc.cli`: adds `--tb-resume`;
- `tests.test_bench_runner`: adds resume tests;
- `README.md`: documents `--tb-resume`;
- `docs/architecture.md`: updates benchmark runtime status.

### Real Test Status

Targeted shard-runner tests:

```text
Ran 5 tests in 0.015s
OK
```

Full test suite:

```text
Ran 36 tests in 0.191s
OK
```

New tests added:

- resume skips already passed shards;
- resumed shards do not call Docker health check;
- resume stops on the next unhealthy uncompleted shard;
- failed checkpoints are not skipped and are attempted again.

### Impact

This version makes interrupted Terminal-Bench shard runs cheaper and cleaner to
continue:

- completed shards are not rerun;
- Docker outages do not force a full benchmark restart;
- the manifest clearly distinguishes `resumed` from newly `passed`, `planned`,
  `failed`, or `skipped_docker_unhealthy` shards.

This is still shard-level resume, not per-task resume inside a shard.

### Remaining Gap

Still missing:

- resume of individual task ids inside a partially failed shard;
- retry policy for environment-only shard failures;
- result aggregation across shard output directories;
- automatic classification of each shard's `results.json`.

## 0.3 - Terminal-Bench Shard Runner And Docker Health Gate

Date: 2026-06-18

### Version Scope

Version `0.3` adds a Terminal-Bench shard runner with a Docker health gate.

New behavior:

- load Terminal-Bench task ids from newline-delimited text or JSON;
- split task ids into stable shards;
- render each shard through a command template;
- run `docker info` before each shard;
- stop immediately when Docker is unhealthy;
- write `shard-manifest.json` with shard status, command, task ids, return
  code, and stop reason;
- support `--tb-dry-run` for planning without executing Terminal-Bench.

Important files:

- `mini_cc.bench`: `DockerHealthChecker`, `TerminalBenchShardRunner`,
  `load_task_ids`;
- `mini_cc.cli`: `--terminal-bench-shards`, `--tb-command-template`,
  `--tb-shard-size`, `--tb-output-dir`, `--tb-dry-run`;
- `tests.test_bench_runner`: shard planning, Docker health gate, and task-id
  loading tests;
- `README.md`: runner usage example;
- `docs/architecture.md`: benchmark runtime status update.

### CLI Shape

Example:

```powershell
py -3 -m mini_cc `
  --terminal-bench-shards tasks.txt `
  --tb-command-template "tb run {task_args} --output-path {output_dir}" `
  --tb-shard-size 5 `
  --tb-output-dir terminal-bench-shards
```

Template fields:

- `{task_ids}`: comma-separated task ids;
- `{task_args}`: repeated `--task-id TASK` arguments;
- `{output_dir}`: shard output directory;
- `{shard_index}`: 1-based shard number.

### Real Test Status

Targeted shard-runner tests:

```text
Ran 3 tests in 0.010s
OK
```

Full test suite:

```text
Ran 34 tests in 0.200s
OK
```

New tests added:

- stable task sharding;
- dry-run shard planning;
- Docker health gate stops before an unhealthy shard;
- manifest records `skipped_docker_unhealthy`;
- task-id loading from text and JSON.

These tests do not require a live Docker daemon. Docker behavior is injected
with a fake health checker so the control-flow guarantees can be tested
deterministically.

### Impact

This version prevents a repeat of the invalid full Terminal-Bench run where
Docker exited mid-run and polluted many tasks as `unknown_agent_error`.

Practical impact:

- full benchmark runs can be split into smaller shards;
- every shard is gated by Docker health;
- when Docker is down, the run stops before contaminating later task results;
- shard manifests make it clear whether a shard was planned, passed, failed, or
  skipped due to environment health.

### Remaining Gap

Still missing:

- checkpoint/resume of partially completed shard sets;
- retry policy for environment-only shard failures;
- native presets for specific Terminal-Bench CLI versions;
- score aggregation across shard result files;
- automatic classification of each shard's `results.json`.

## 0.2 - Configured Hooks And Matchers

Date: 2026-06-18

### Version Scope

Version `0.2` adds project-configured hooks and matcher-based execution.

New behavior:

- S20 loads hook config from `.claude/settings.json`;
- S20 loads hook config from `.mini_cc/settings.json`;
- S20 loads hook config from `.mini_cc/settings.local.json`;
- configured `command` hooks receive hook event JSON on stdin;
- command hooks can block a tool call by printing JSON such as
  `{"decision": "block", "reason": "..."}`;
- command hooks can adjust tool input by printing JSON with
  `payload_updates` or `tool_input_updates`;
- hooks are matched by event name and matcher before execution.

Important files:

- `mini_cc.hooks`: configured hook loading, matcher logic, command hook
  execution, hook stdin/stdout protocol;
- `mini_cc.cli`: loads configured hooks when S20 mode is built;
- `tests.test_runtime_modules`: matcher and configured hook tests;
- `README.md`: configured hook usage example;
- `docs/architecture.md`: updated runtime architecture status.

### Reference Standard

This version follows the Claude Code hook config shape:

- top-level `hooks` object;
- event names such as `PreToolUse` and `PostToolUse`;
- matcher groups under each event;
- a `hooks` array inside each matcher group;
- command hook handlers with `type: "command"`.

Matcher behavior implemented in this project:

- empty matcher or `*`: match all;
- `write_file|replace_text`: match any exact tool name in the list;
- `mcp__.*`: regex matcher.

Because this project uses different local tool names, the tests use
`run_shell`, `write_file`, and `replace_text` instead of Claude Code's exact
tool names such as `Bash`, `Write`, and `Edit`.

### Real Test Status

Targeted runtime tests:

```text
Ran 6 tests in 0.083s
OK
```

Full test suite:

```text
Ran 31 tests in 0.175s
OK
```

New tests added:

- empty matcher matches all tools;
- `*` matcher matches all tools;
- `write_file|replace_text` matches exact tool names only;
- `mcp__.*` works as a regex matcher;
- configured `PreToolUse` command hook can block a matched `run_shell` tool
  call through JSON stdout.

This is a direct runtime capability test, not a benchmark prompt answer. It
checks that the hook engine loads project config, matches the intended tool,
runs the configured command hook, and applies the returned block decision.

### Impact

This version makes hooks project-configurable instead of code-only.

Practical impact:

- projects can enforce local policies without editing Python code;
- benchmark harnesses can attach diagnostics or blockers around specific tools;
- future Docker health gates can be implemented as configured `PreToolUse`
  hooks around Docker-related shell commands;
- future audit/logging can be attached to `PostToolUse`.

### Remaining Gap

Still missing compared with the broader Claude Code architecture:

- HTTP hook handlers;
- MCP hook handlers;
- prompt hook handlers;
- agent hook handlers;
- user-level and enterprise-level config precedence;
- richer lifecycle events beyond the current local event payloads.

## 0.1 - Fine-Grained Permission Policy

Date: 2026-06-18

### Version Scope

Version `0.1` adds a dedicated permission policy layer.

New module:

- `mini_cc.permission`: classifies shell commands and permission risk.

Changed modules:

- `mini_cc.tools`: routes file writes, text replacement, and shell execution
  through the permission policy;
- `mini_cc.s20`: routes todo and memory writes through the same policy;
- `tests.test_tools`: adds permission-policy regression coverage.

### What Changed

Before this version, permission handling was coarse:

- `auto` allowed most shell commands unless a small string blocklist matched;
- `read-only` denied all shell commands, including harmless read commands such
  as `dir` and `git status`;
- dangerous commands such as `git push` and `docker system prune -af` were not
  explicitly classified;
- S20 state writes used the same broad permission gate as normal file writes.

Version `0.1` classifies operations into risk categories:

- `read`
- `verify`
- `workspace_write`
- `network`
- `package_manager`
- `docker`
- `git_remote_write`
- `destructive`
- `unknown_shell`

Policy behavior:

- `read-only` allows read and verification shell commands, but blocks writes,
  network/package/docker commands, remote writes, destructive operations, and
  unknown shell commands;
- `auto` allows normal workspace writes, verification, package/network/docker
  operations, and unknown shell commands, but blocks high-risk destructive
  operations and git remote writes;
- `ask` still requires user confirmation for non-read operations.

### Real Before/After Test

The same permission-policy test set was run before and after the change. These
are direct tool calls, not benchmark prompt answers.

Test cases:

- `read-only` should allow `dir`;
- `read-only` should allow `git status --short`;
- `read-only` should block `Set-Content` shell writes;
- `auto` should block `git push origin main`;
- `auto` should block `docker system prune -af`;
- `auto` should block `Remove-Item -Recurse -Force`.

Before `0.1`:

```text
permission_policy_score=2/6
```

Observed misses:

- `read-only` incorrectly blocked safe read shell commands;
- `auto` incorrectly attempted `git push origin main`;
- `auto` incorrectly attempted `docker system prune -af`.

During the first `0.1` run:

```text
permission_policy_score=5/6
```

The remaining miss found by the test was a real classifier bug:

- `Remove-Item -Recurse -Force` was not detected because the regex used a word
  boundary before `-Recurse`.

After fixing that classifier bug:

```text
permission_policy_score=6/6
```

### Unit Test Status

Full test suite:

```text
Ran 29 tests in 0.106s
OK
```

Note:

- normal sandboxed test execution still hits local temp-directory permission
  issues;
- the passing full test run was executed with permission to create/delete test
  temp directories.

### Impact

This version improves reliability in two directions:

- fewer false blocks: read-only mode can now run safe inspection and local
  verification commands;
- fewer false allows: auto mode now blocks high-risk destructive commands and
  git remote writes before execution.

This is a runtime safety and usability improvement. It is not a claim that
model reasoning accuracy improved on SWE-bench or Terminal-Bench.

## 0.0 - Runtime Baseline

Date: 2026-06-18

### Version Scope

Version `0.0` is the current baseline after the S20 teaching version was split
into explicit runtime modules.

Current runtime capabilities:

- single-agent model/tool loop;
- S20 teaching tool layer;
- OpenAI-compatible Responses provider support;
- harness-style JSON CLI;
- basic session recording;
- basic hook runtime;
- context snapshot builder;
- Terminal-Bench result classifier;
- simplified permission modes.

Important modules:

- `mini_cc.agent`: main agent loop;
- `mini_cc.tools`: workspace file/search/shell tools;
- `mini_cc.s20`: S20 teaching tools;
- `mini_cc.hooks`: lifecycle hook runtime;
- `mini_cc.session`: session persistence;
- `mini_cc.context`: workspace context snapshot;
- `mini_cc.bench`: benchmark result classification;
- `mini_cc.llm`: provider adapters;
- `mini_cc.cli`: CLI and JSON harness entrypoint.

### Test Status

#### Harness-Bench-Fast

Status: baseline pass observed.

Result summary:

- all 313 tasks were run in batches;
- every task had at least one passing record;
- target was 99% or higher, and this baseline met that threshold in the
  observed run.

Interpretation:

- this is a useful signal for basic file, shell, JSON CLI, and tool-loop
  behavior;
- it should not be treated as proof of full Claude-Code-level architecture,
  because the benchmark mainly covers short-horizon tool use.

#### Terminal-Bench Smoke Tests

Status: smoke pass observed before Docker failure.

Result summary:

- `hello-world` oracle: 1/1 pass;
- `hello-world` codex: 1/1 pass;
- selected easy/GHCR codex tasks: 4/4 pass.

Interpretation:

- the agent can execute simple Terminal-Bench tasks through the patched Windows
  setup;
- this only validates smoke-level terminal autonomy, not full benchmark
  strength.

#### Terminal-Bench Full Run

Status: invalid run.

Run note:

- the full run became invalid because Docker Desktop daemon exited during the
  benchmark;
- many tasks were marked as `unknown_agent_error`;
- the result must not be interpreted as a model or agent score.

Required before the next valid full run:

- Docker Desktop must be healthy;
- tasks should be run in shards instead of one large run;
- Docker health should be checked before each shard;
- environment failures should be separated from model/test failures.

#### Local Unit Tests

Status: blocked by local temp-directory permissions.

Observed issue:

- `py -3 -m unittest discover` currently fails before project logic runs;
- Python raises `PermissionError: [WinError 5]` while creating temporary test
  directories.

Interpretation:

- current `unittest discover` failure is not a valid signal about agent logic;
- the temp directory permission issue should be fixed or tests should be run
  with a known writable temp root.

### Known Architecture Gaps

The current `0.0` baseline does not yet include:

- hooks loaded from config files;
- command/http/MCP/prompt/agent hook types;
- hook matchers by event and tool name;
- subagents with independent prompt, tool scope, model, and memory;
- MCP adapter for tools and resources;
- checkpoint/resume;
- context compression and explicit token budgets;
- fine-grained permission policy;
- Terminal-Bench shard runner with Docker health gate.

### Next Target

The next iteration should focus on making benchmark results reliable before
expanding high-level agent abilities:

1. add fine-grained permission policy;
2. load hooks from project/user config;
3. add hook matchers by event and tool name;
4. add Terminal-Bench shard runner and Docker health gate;
5. add checkpoint/resume;
6. add context token budget and compression;
7. add subagent runtime;
8. add MCP adapter.
