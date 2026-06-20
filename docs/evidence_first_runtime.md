# Evidence-First Runtime

Mini Claude Code is an evidence-first local coding-agent runtime. It makes each
coding-agent step auditable and prevents unverified code edits from being
reported as successful.

## Why Evidence Matters

A basic model-tool loop can edit files and then produce a confident final
answer. That is not enough for coding work. A reviewer needs to know what the
agent inspected, why it chose specific files, what it changed, what command
verified the change, and whether any blockers remain.

The core runtime records those facts as process state and an Evidence Report.

## Core Flow

```text
Explore
  -> Localize
  -> Plan
  -> Edit
  -> Verify
  -> Repair
  -> Final Report
```

## Evidence By Step

- `Explore`: records file listings, searches, README/config/test inspection, and
  other context-gathering tools.
- `Localize`: records candidate files, symbols, test files, and failure-output
  references.
- `Plan`: records `planned_files`, the reason for editing them, and the intended
  verification command.
- `Edit`: records permission-gated write tools, modified files, and patch/edit
  scope.
- `Verify`: records real local test, lint, typecheck, build, or docs-check
  commands and their exit codes/output quality.
- `Repair`: records the failed command, failure summary, modified files, and the
  next minimal repair attempt.
- `Final Report`: records status, changed files, verification result, semantic
  warnings, blockers, and remaining issues.

## Final Success Gates

The runtime must not report a coding task as successful when:

- the agent did not explore the workspace;
- no target file was localized;
- no `planned_files` were produced;
- an existing file was edited before being read;
- modified files are outside `planned_files`;
- the task violates explicit user constraints such as `only modify`, `do not
  modify tests`, or `no new files`;
- no real verification command ran after edits;
- verification is a fake command such as `echo`, `ls`, `cat`, `git status`, or
  `git diff`;
- verification output shows `no tests ran`, `collected 0 items`, missing
  scripts, command-not-found output, or skipped checks;
- verification failed and the repair limit has not been reached.

## Evidence Report

The compatibility path for the Evidence Report is:

```text
.mini_cc/task-success/last-run.json
```

The report records:

- task prompt and extracted task contract;
- process checks for explore, localize, plan, edit, and verify;
- planned files and modified files;
- tool and verification evidence where available;
- verification command, exit code, and pass/fail state;
- semantic warnings and blockers;
- final status.

## Extension Model

Hooks, local memory, skills, git evidence tools, the desktop UI, and the web
frontend can help inspect or demonstrate the same runtime. They are optional
extensions around the core loop.

MCP, subagents, benchmark hints, Terminal-Bench automation, tool-use eval, and
runtime reports are experimental research surfaces. They are not required for
the core evidence loop and should not be treated as the main reliability claim.

## Limitations

- Evidence does not prove global correctness.
- Local tests may be incomplete or missing.
- Semantic checks are deterministic and may be conservative.
- Benchmark scores are not claimed unless they come from a valid external
  evaluation.
