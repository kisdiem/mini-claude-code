# Mini Claude Code

Mini Claude Code is an evidence-first local coding-agent runtime.

It makes every coding-agent step auditable and prevents unverified code edits
from being reported as successful.

For a concise project overview, see [Interview Summary](docs/interview_summary.md).

## Project Scope

Mini Claude Code is:

- a compact runtime for studying coding-agent reliability;
- a local harness around LLM tool use;
- a process-control layer for exploration, planning, editing, verification,
  repair, and evidence reporting.

Mini Claude Code is not:

- a Claude Code replacement;
- a benchmark claim;
- a general autonomous software engineer;
- a collection of unrelated agent features.

The project is organized around a single reliability loop:

```text
User Task
   |
   v
Explore Workspace
   |
   v
Localize Target Files
   |
   v
Produce Planned Files
   |
   v
Apply Permission-Gated Edits
   |
   v
Run Real Verification
   |
   v
Repair If Needed
   |
   v
Write Evidence Report
```

## Evidence-First Loop

The runtime separates three questions:

1. Did the agent follow the required process?
2. Are the plan, edit, and verification relevant to the user task?
3. Did real local verification pass?

`TaskStateMachine` answers the first question by enforcing staged execution.
`mini_cc.task_success` answers the second question through deterministic
evidence-based checks. `CodingLoopPolicy` answers the third question by blocking
successful final reports after code edits until a real verification command has
run.

This does not prove global coding ability. It makes the runtime inspectable: a
reviewer can see what the agent inspected, planned, changed, verified, and
reported.

## Core Runtime

- Agent loop with Anthropic, OpenAI, and deterministic mock providers.
- Tool schemas for workspace file operations and shell commands.
- Project context indexing for project type detection, source/test/config
  discovery, Python AST symbols, JS/TS symbol hints, and related-file lookup.
- Permission policy around risky operations.
- `TaskStateMachine` for explore -> localize -> plan -> edit -> verify ->
  repair -> final process control.
- `TaskPlanner` for deterministic task context, candidate files, minimal edit
  planning, and verification command suggestions.
- Structured repair context from failing test/build/typecheck output so repair
  turns start from concrete files, symbols, and failure excerpts.
- `CodingLoopPolicy` for real verification after code edits.
- Semantic task-success checks for plan, edit, verification relevance, and
  output quality.
- Realistic local eval cases that create temporary projects and drive the real
  agent/tool/runtime path with an offline scripted provider.

## Local Evals

Run the deterministic realistic eval harness:

```bash
python -m mini_cc.evals.realistic_tasks
```

Each case reports pass status, modified files, planned files, verification
commands, tool-call count, repair attempts, constraint violations, and the
Evidence Report path.
- Verification command discovery for local projects.
- Evidence Report artifact for each core coding run.

## Optional Extensions

These features support the same evidence-first runtime, but they are not the
main value proposition:

- hooks for prompt, tool, permission, file, session, and context events;
- local memory, skills, todo state, git evidence tools, and context snapshots;
- desktop UI and web frontend as manual demo surfaces;
- local health checks and deterministic smoke evals.

## Experimental Features

These are research or harness features. They are not required for the core loop
and are not part of the main reliability claim:

- MCP experiments;
- subagent orchestration;
- benchmark hints and Terminal-Bench automation;
- tool-use eval reports and broader runtime reports;
- S20-specific research tools beyond the evidence-first path.

## Quick Start

Install for local development:

```powershell
cd mini-claude-code
py -3 -m pip install --upgrade pip setuptools wheel
py -3 -m pip install -e .
```

For a lightweight demo-only setup, installing `requirements.txt` is also enough:

```powershell
py -3 -m pip install -r requirements.txt
```

Run with no API key:

```powershell
py -3 -m mini_cc --mock --workspace . "list files"
```

Run the golden path for reviewer/demo use:

```powershell
py -3 -m mini_cc evidence --workspace . --prompt "fix the failing test"
```

Equivalent explicit command:

```powershell
py -3 -m mini_cc run --s20 --coding-loop --permission-mode bypass --workspace . --output-format json --prompt "fix the failing test"
```

Run tests:

```powershell
py -3 -m unittest discover
```

If multiple Python versions are installed, select a known-good Python manually:

```powershell
$py='C:\Path\To\python.exe'
$env:TMP="$PWD\.tmp-tests"
$env:TEMP=$env:TMP
$env:TMPDIR=$env:TMP
$env:PYTHONDONTWRITEBYTECODE='1'
& $py -m unittest discover
```

## Evidence Report

The core path writes an Evidence Report. The current compatibility path is:

```text
.mini_cc/task-success/last-run.json
```

This file records process checks, semantic evidence, modified files,
verification results, blockers, warnings, and final status. Example:

```json
{
  "status": "passed",
  "task_prompt": "fix the failing test",
  "process_checks": {
    "explored": true,
    "localized": true,
    "planned": true,
    "edited": true,
    "verified": true
  },
  "semantic_checks": {
    "plan_relevant": true,
    "edit_relevant": true,
    "verification_relevant": true,
    "meaningful_verification": true
  },
  "tools_called": [
    {"name": "read_file", "is_error": false},
    {"name": "apply_patch", "is_error": false},
    {"name": "run_shell", "is_error": false}
  ],
  "planned_files": ["mini_cc/task_success.py"],
  "modified_files": ["mini_cc/task_success.py"],
  "verification_commands": [
    {
      "command": "python -m unittest discover",
      "exit_code": 0,
      "passed": true
    }
  ],
  "semantic_warnings": [],
  "semantic_blockers": [],
  "final_status": "passed"
}
```

## Runtime Rules

- `apply_patch` applies unified diffs and is safer than exact-string
  replacement for larger code edits.
- writes are blocked before exploration and planning;
- existing files must be read before they can be edited;
- edits are limited to `planned_files` unless the task explicitly requires a
  new file;
- after edits, only real test/lint/typecheck/build commands through `run_shell`
  move the task toward `FINAL`;
- failed verification moves the task to `REPAIR` until the repair limit is
  reached.
- `git_diff`, `git_status`, and `context_snapshot` are runtime evidence, not
  pass/fail verification.

## Semantic Task Success Checks

`mini_cc.task_success` adds a deterministic evidence-based semantic layer on top
of the staged process gate. It does not call another model. Instead, it extracts
a structured `TaskContract` from the user prompt and checks whether the plan,
edits, and verification evidence are relevant to that contract.

The semantic layer checks:

- explicit paths, symbols, requested operations, primary and secondary intents,
  and user constraints such as "only modify this file" or "do not modify tests";
- acceptance criteria from paths, symbols, quoted literals, expected/actual
  phrases, error snippets, and explicit verification commands;
- whether `planned_files` are grounded in prompt paths, explored candidates,
  files that were actually read, or failure-output evidence;
- whether edits stay inside `planned_files` and match the task type;
- hard blockers, warnings, and low-confidence exploration-needed decisions;
- whether the verification command is a real test/lint/typecheck/build command
  or docs-check command and relevant to the modified files;
- whether successful verification output is meaningful, for example rejecting
  `pytest` runs that report `collected 0 items` or `no tests ran`.

The task-success artifact includes `task_contract`, `process_checks`,
`semantic_checks`, `semantic_warnings`, and `semantic_blockers`.

These checks improve auditability and reduce obvious off-task edits. They do not
prove global correctness and should not be read as an external benchmark result.

## Providers

### Mock Provider

Use `--mock` for deterministic local runs without an API key:

```powershell
py -3 -m mini_cc --mock --workspace . "list files"
```

### Anthropic API Provider

Install dependencies and create `.env` from `.env.example`:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Set values in `.env`. The CLI loads this file through `python-dotenv`; command
line flags still override environment defaults.

```text
ANTHROPIC_API_KEY=your_key
MINI_CC_MODEL=claude-sonnet-4-6
MINI_CC_MAX_TOKENS=4096
```

Run:

```powershell
.\.venv\Scripts\python -m mini_cc --s20 --workspace . "summarize this project"
```

### OpenAI API Provider

Use an OpenAI-compatible API key by setting `OPENAI_API_KEY` and selecting the
OpenAI provider:

```powershell
$env:OPENAI_API_KEY = "your_key"
$env:MINI_CC_OPENAI_MODEL = "gpt-5"
py -3 -m mini_cc run --provider openai --s20 --permission-mode bypass --workspace . --output-format json --prompt "list files"
```

## CLI Modes

- `--permission ask`: ask before write tools and shell commands.
- `--permission read-only`: block write tools and shell commands.
- `--permission auto`: allow write tools and shell commands automatically.
- `--mock`: use a deterministic local provider.
- `evidence --prompt ...`: recommended reviewer/demo path for the core loop.
- `run --prompt ... --output-format json`: non-interactive harness entrypoint.
- `--coding-loop`: enable the verification gate for code edits.
- `--test-command TEXT`: provide the verification command to prefer.
- `--s20`: enable optional comprehensive tooling used by the evidence path.

Diagnose merged project configuration:

```powershell
py -3 -m mini_cc --workspace . --diagnose-config
```

## Local Development Readiness

The repository includes several pieces that make it easier to review and run
locally:

- `pyproject.toml` for editable installation and console entrypoints.
- GitHub Actions CI for Python 3.10, 3.11, and 3.12.
- Windows health check script: `scripts\health_check.ps1`.
- Native one-click launcher: `scripts\start_desktop.bat`.
- Local secrets and runtime state ignored through `.gitignore`.
- Deterministic mock mode for demos without API keys.
- Unit tests for tools, permissions, hooks, workflow verification, staged task
  state, and semantic task-success checks.

Run a local health check:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
```

Run the full health check including unit tests:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1 -Full
```

## Optional Local UI

The UI is a manual demo surface for the same evidence-first runtime. It is not
the core contribution.

Start the native Windows desktop app:

```powershell
.\scripts\start_desktop.ps1
```

For one-click Windows startup, double-click:

```text
scripts\start_desktop.bat
```

The script auto-detects `py`, `pythonw`, or `python` and launches the Tkinter
desktop app. API keys are entered manually in the settings dialog and are stored
only in local ignored files under `.mini_cc/`.

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

## Optional Hooks And Permissions

S20 mode loads project hooks from:

- `.claude/settings.json`
- `.mini_cc/settings.json`
- `.mini_cc/settings.local.json`

Hook events use the runtime catalog in `mini_cc.hooks`. The main runtime emits
events for prompt/session lifecycle, tool use, permission decisions, file
changes, context compaction, and subagent activity.

Configured hook handler types include:

- `command`: run a local command and read a JSON decision from stdout;
- `http`: POST the hook event JSON to an HTTP endpoint and read a JSON decision;
- `mcp`: call a registered MCP hook tool and read a JSON decision;
- `prompt`: render a template into `payload_updates`;
- `agent`: call a registered in-process agent hook handler.

Permission events are emitted from the permission engine itself. `ask` mode can
request confirmation, `read-only` blocks writes and shell commands, and `auto`
allows common local actions while still recording permission evidence.

## Experimental S20 Tooling

S20 mode includes optional and experimental teaching tools around the core
runtime:

- Planner / Executor / Verifier workflow records;
- file read, list, search, write, replace, patch, and shell tools;
- workspace path sandboxing;
- permission engine and permission ledger;
- hook runtime and hook metrics;
- todo state and structured local memory;
- local skill listing and reading;
- git status and git diff read tools;
- context snapshot support for long tasks;
- subagent and MCP-related runtime experiments.

These tools are useful for studying orchestration, but they are not required to
understand the Evidence Report path.

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

This is a small local smoke validation. It is not an external benchmark score.

## Documentation

- [Evidence-First Runtime](docs/evidence_first_runtime.md)
- [Core Runtime Architecture](docs/core_runtime_architecture.md)
- [Coding Reliability Loop](docs/coding_reliability_loop.md)
- [Interview Summary](docs/interview_summary.md)

Additional notes, localized materials, and historical review documents may
exist under `docs/` and project-specific markdown files. The main README keeps
the public project pitch focused on the English engineering summary above.

## References

- shareAI-lab/learn-claude-code: https://github.com/shareAI-lab/learn-claude-code
- Anthropic Agent Loop docs: https://code.claude.com/docs/en/agent-sdk/agent-loop
