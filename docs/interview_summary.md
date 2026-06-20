# Interview Summary

## 30-Second Introduction

I built Mini Claude Code as an evidence-first local coding-agent runtime. The
goal is to study how a coding assistant can be structured as a runtime system
where every step is inspectable: exploration, localization, planning, editing,
verification, repair, and final reporting.

The project is not presented as a Claude Code replacement or an external
benchmark result. Its core value is preventing unverified code edits from being
reported as successful.

## 90-Second Introduction

Mini Claude Code wraps an LLM with local workspace tools, shell execution,
permission policy, staged task state, verification gates, and an Evidence Report.
For coding tasks, the runtime forces the agent to inspect the workspace, read
target files, produce `planned_files`, apply permission-gated edits, run a real
local verification command, and repair failures before a final success report is
allowed.

The system separates three questions. `TaskStateMachine` checks whether the
agent followed the required process. `mini_cc.task_success` checks whether the
plan, edit, and verification evidence are relevant to the user task using
deterministic evidence-based rules. `CodingLoopPolicy` checks whether changed
code passed a real verification command. The result is written to a local
Evidence Report so a reviewer can inspect the run after the fact.

## Technical Highlights

- State-machine enforced process for explore, localize, plan, edit, verify,
  repair, and final.
- Permission-gated tools for workspace writes and shell commands.
- Real verification gate after code edits.
- Deterministic semantic checks for task contract, plan relevance, edit
  relevance, verification relevance, and verification output quality.
- Auditable Evidence Report at `.mini_cc/task-success/last-run.json`.

## Boundaries

- Not Claude Code product parity.
- Not an external SWE-bench or Terminal-Bench score.
- Not proof of global program correctness.
- Optional hooks, memory, MCP, subagents, desktop UI, and web frontend are
  extensions around the runtime, not the main contribution.

## What To Review

- `mini_cc/agent.py`: model/tool loop and runtime integration.
- `mini_cc/task_state.py`: staged process control.
- `mini_cc/task_success.py`: deterministic semantic evidence checks.
- `mini_cc/coding_loop.py`: verification gate after code edits.
- `mini_cc/task_runtime.py`: unified task runtime coordination.
- `mini_cc/tools.py`: workspace tools and shell execution.
- `tests/`: offline unit tests for runtime behavior.
