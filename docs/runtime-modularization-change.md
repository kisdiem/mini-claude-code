# Runtime Modularization Change

Date: 2026-06-18

## Why This Change Was Made

The previous S20 implementation already contained simplified versions of hooks,
memory, context, and benchmark discipline. However, several capabilities were
mixed together inside `s20.py`.

This made the teaching version runnable, but it also meant the architecture was
not yet close to Claude Code's runtime design. In particular:

- hooks only logged `tool_start` and `tool_end`;
- hooks could not block or modify tool calls;
- context snapshot construction lived inside `S20ToolRunner`;
- agent runs did not have a durable session record;
- benchmark failures had to be classified manually;
- Docker or environment failures could pollute benchmark scores.

This change separates those simplified S20 abilities into explicit runtime
modules.

## Files Added

### `mini_cc/hooks.py`

Adds `HookRuntime`.

Supported events:

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `Stop`
- `Notification`

Important behavior:

- `PreToolUse` can now deny a tool call.
- hook events can still be written to `hooks.log`;
- hook handlers can be registered programmatically;
- hooks are no longer just passive logging.

### `mini_cc/session.py`

Adds `SessionStore`.

It records:

- session id;
- prompt;
- model name;
- turn start events;
- model response metadata;
- tool use metadata;
- errors;
- final status.

For S20 runs with a state directory, sessions are written under:

```text
.mini_cc/sessions/
```

### `mini_cc/context.py`

Adds `ContextBuilder`.

It now owns workspace snapshot construction:

- workspace path;
- file list;
- git status;
- todos;
- memory.

This removes context assembly from `S20ToolRunner`, making it possible to expand
context management later without growing `s20.py`.

### `mini_cc/bench.py`

Adds Terminal-Bench result classification.

Current categories:

- `resolved`
- `environment_docker_down`
- `environment_apt_network`
- `agent_install_failed`
- `model_timeout`
- `unknown_agent_error`
- `test_failed`
- `unresolved`

This is meant to separate model failures from environment failures.

### `tests/test_runtime_modules.py`

Adds tests for:

- hook blocking behavior;
- hook event logging;
- Docker-down classification;
- apt-network classification.

Note: full test execution is currently blocked by local temp-directory
permission errors, not by these assertions.

### `docs/architecture.md`

Adds the current project architecture and the remaining gap against Claude Code.

## Files Changed

### `mini_cc/s20.py`

Before:

```text
S20ToolRunner
  -> internal HookManager
  -> record("tool_start")
  -> run tool
  -> record("tool_end")
```

After:

```text
S20ToolRunner
  -> HookRuntime.PreToolUse
  -> optionally block/update tool input
  -> run tool
  -> HookRuntime.PostToolUse
```

Also changed:

- `context_snapshot()` now delegates to `ContextBuilder`;
- old `HookManager` was removed from `s20.py`;
- S20 still keeps todo, memory, skill, git, and context tools.

### `mini_cc/agent.py`

Before:

```text
prompt
  -> model/tool loop
  -> return or max_turns
```

After:

```text
prompt
  -> SessionStore.start
  -> SessionStart hook
  -> model/tool loop
  -> session records turn/model/tool events
  -> Stop hook
  -> SessionStore.finish
```

If an exception occurs:

```text
error
  -> session records error
  -> session status = failed
  -> Stop hook with reason = exception
  -> exception re-raised
```

### `mini_cc/cli.py`

Changes:

- S20 agents now receive `SessionStore` and `HookRuntime`;
- added `--classify-terminal-bench RESULTS_JSON`;
- classification command outputs JSON with task id, resolved status, failure mode,
  category, and reason.

### `README.md`

Added a Runtime Architecture section linking to `docs/architecture.md`.

## Current Runtime Flow

```text
user prompt
  -> Agent.run
  -> SessionStore.start
  -> HookRuntime.SessionStart
  -> provider.complete
  -> model emits tool_use
  -> HookRuntime.PreToolUse
  -> ToolRunner/S20ToolRunner executes tool
  -> HookRuntime.PostToolUse
  -> tool_result is returned to model
  -> final text or max_turns
  -> SessionStore.finish
  -> HookRuntime.Stop
```

## What This Solves

This change turns earlier S20 teaching shortcuts into real engineering seams:

- hooks can now enforce policy;
- sessions can be inspected after failure;
- context construction is isolated;
- benchmark failures can be bucketed;
- future Docker health checks can live in hooks or benchmark runner logic.

For example, the Docker failure from the Terminal-Bench full run should no
longer be treated as a model failure once the shard runner uses
`environment_docker_down`.

## What Is Still Missing Compared With Claude Code

Still missing:

- configured hooks from project/user settings;
- command/http/MCP hook types;
- matchers by tool name and event type;
- subagents with independent prompt/tool/model/memory scope;
- MCP tool/resource adapter;
- checkpoint/resume;
- context compression and token budgeting;
- richer permission policy;
- Terminal-Bench shard runner with Docker health gate.

## Verification

Lightweight smoke passed:

```text
runtime smoke ok
```

The smoke checked:

- new modules import successfully;
- `PreToolUse` can block `run_shell`;
- CLI argument parsing still works;
- Terminal-Bench Docker-down classification works.

Full `unittest discover` could not be used as a valid signal because the local
Python temp directory is currently returning:

```text
PermissionError: [WinError 5] 拒绝访问
```

The error happens before project logic runs, when tests try to create files in
`tempfile.TemporaryDirectory()`.
