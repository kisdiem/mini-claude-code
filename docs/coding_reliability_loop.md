# Coding Task Success Loop

Coding Task Success Loop v3.6.1 is the runtime guard for code modification tasks.
It is intentionally small: it does not replace MCP, hooks, subagents, memory, or
the existing S20 workflow. It adds one missing production behavior: after code is
changed, the agent must run a real verification command before it can finish.

## Problem

Before this loop, the agent could:

- edit files and immediately answer;
- treat `git_diff` or `context_snapshot` as enough evidence;
- run any shell command and still appear verified;
- stop after a failed test without a repair attempt.

That is useful for demos, but weak for coding reliability. A coding agent should
prove that the edited code still passes a local deterministic check.

## Flow

```text
User Prompt
  |
  v
Agent Loop
  |
  v
Inspect Tools
  |
  v
Edit Tool: apply_patch / replace_text / write_file
  |
  v
CodingLoopPolicy marks code_modified
  |
  v
Verification command required
  |
  v
run_shell test
  |
  +-- passed -> final report
  +-- failed -> repair loop
  +-- max attempts -> failed report
```

## Runtime Rules

- `write_file`, `replace_text`, and `apply_patch` mark the run as code-modified.
- `git_status`, `git_diff`, `context_snapshot`, `list_files`, `read_file`,
  `search_text`, `memory_read`, `memory_write`, `todo_read`, `todo_write`, and
  `subagent_pipeline` are not verification.
- `run_shell` counts as verification only when the command looks like a real
  test or check command, such as `python -m unittest discover`, `pytest`,
  `npm test`, `npm run lint`, `ruff`, `mypy`, `tsc`, `cargo test`, or
  `go test ./...`.
- If code was changed and no verification command ran, the runtime appends a
  forced follow-up instruction instead of finishing.
- If verification failed and the repair limit has not been reached, the runtime
  asks for one minimal repair and another verification run.
- If verification passes, the final answer is allowed.
- If the repair limit is reached, the final answer is allowed but must report
  the failed verification and remaining issue.

## Verification Semantics

The runtime separates two concepts that used to be easy to mix up.

Runtime Evidence means the agent inspected or collected context. Examples:

- `list_files`;
- `read_file`;
- `search_text`;
- `git_status`;
- `git_diff`;
- `context_snapshot`;
- `subagent_pipeline`;
- `todo_read`;
- `memory_read`.

These tools are useful evidence for a report, but they do not prove that changed
code works.

Code Verification means a real local check ran through `run_shell` and exited
successfully. Examples:

- `python -m unittest discover`;
- `python -m pytest`;
- `pytest`;
- `npm test`;
- `npm run lint`;
- `ruff check .`;
- `mypy .`;
- `cargo test`;
- `go test ./...`;
- `make test`.

For code modification tasks, `CodingLoopPolicy` is the source of truth. A
successful `git_diff`, `git_status`, or `context_snapshot` can appear in the
evidence ledger, but it cannot set task success to passed.

## Test Command Discovery

The CLI can receive an explicit command:

```powershell
py -3 -m mini_cc --s20 --coding-loop --test-command "python -m unittest discover" --workspace . "fix the bug"
```

Without `--test-command`, the runtime tries a simple local discovery:

- `package.json` with `scripts.test` -> `npm test`;
- `package.json` with `scripts.lint` -> `npm run lint`;
- `Cargo.toml` -> `cargo test`;
- `go.mod` -> `go test ./...`;
- `pom.xml` -> `mvn test`;
- `gradlew`, `gradlew.bat`, or `build.gradle` -> `./gradlew test`;
- `pytest.ini` or pytest config -> `python -m pytest`;
- unittest-style `tests/` -> `python -m unittest discover`;
- other `tests/` -> `python -m pytest`.

If nothing is detected, the agent is told to inspect the project and choose the
most local deterministic test or lint command.

## apply_patch

`apply_patch` is a workspace-safe unified diff tool. It exists because
`replace_text` is intentionally strict and can fail when large or fragile exact
strings are involved.

It supports:

- common unified diff headers such as `--- a/file.py` and `+++ b/file.py`;
- multi-file patches;
- `dry_run=true` validation without writing files;
- workspace path enforcement;
- `FileChanged` hook events for each modified file.

It does not depend on system `git`; the common unified diff path is implemented
in Python so tests work on Windows and in CI.

## Task Success Artifact

Every enabled run writes:

```text
.mini_cc/task-success/last-run.json
```

The artifact records:

- whether the coding loop was enabled;
- whether code was modified;
- modified files;
- verification commands;
- last verification result;
- repair attempts;
- status: `passed`, `failed`, `not_required`, or `max_attempts_reached`;
- timestamp.

This file is meant for demos and CI-like checks. It gives a reviewer a concrete
artifact instead of asking them to trust a final chat message.

## Task-Success Smoke Eval

Run:

```powershell
python -m mini_cc.evals.task_success
```

The eval creates a few tiny broken Python projects, applies deterministic
patches with `apply_patch`, runs `python -m unittest discover`, and writes:

```text
.mini_cc/task-success-eval/task-success-eval.json
```

This is not a SWE-bench or Terminal-Bench score. It is a small smoke test that
checks whether the local task-success loop can produce changed files,
verification commands, and passed task artifacts.
