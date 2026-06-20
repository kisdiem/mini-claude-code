# Core Runtime Architecture

Mini Claude Code is organized around an evidence-first coding loop. The core runtime does not try to prove global code correctness. It makes the local agent process inspectable and prevents code edits from being reported as successful without relevant verification evidence.

## Runtime Layers

### Task Contract

`mini_cc.task_success.extract_task_contract` interprets the user task into deterministic, inspectable fields: intent, explicit paths, symbols, constraints, acceptance criteria, and verification hints. This layer does not manage phases, run tools, or write artifacts.

### Process State Machine

`mini_cc.task_state.TaskStateMachine` owns the staged process:

```text
INTAKE -> EXPLORE -> LOCALIZE -> PLAN -> EDIT -> VERIFY -> REPAIR -> FINAL
```

It enforces process discipline such as explore-before-edit, read-before-edit, plan-before-edit, and planned-file scope. It records process facts, but the runtime coordinator is responsible for the final evidence report.

### Semantic Gates

`mini_cc.task_success` provides deterministic semantic gates for plan relevance, edit relevance, verification relevance, and verification output quality. These checks return structured decisions and evidence. They are not an LLM judge and may be conservative when exploration evidence is weak.

### Verification Policy

`mini_cc.verification_policy.VerificationPolicy` classifies commands as `test`, `lint`, `typecheck`, `build`, `docs/check`, `runtime-evidence`, `fake`, or `unknown`. It rejects inspection commands such as `git diff`, `git status`, `echo`, `cat`, `ls`, `read_file`, `list_files`, and `context_snapshot` as verification. It also detects zero-test or skipped-check output such as `collected 0 items` and `no tests ran`.

`mini_cc.verification` remains focused on discovering candidate verification commands from the local workspace.

### Evidence Ledger

`mini_cc.evidence.EvidenceLedger` is the append-only record of what happened during a run. Runtime events such as task start, tool calls, tool results, file modifications, verification results, gate decisions, and final decisions are serialized into the Evidence Report.

### Runtime Coordinator

`mini_cc.task_runtime.TaskRuntime` coordinates the task contract, process state machine, semantic gates, verification policy, evidence ledger, and the compatibility `CodingLoopPolicy`. It is the preferred place for final run status and Evidence Report writing.

The report remains at:

```text
.mini_cc/task-success/last-run.json
```

The report includes backward-compatible keys plus `schema_version`, `evidence`, `verification_results`, and `final_decision`.

## Optional Extensions

S20 tools, hooks, memory, MCP experiments, subagents, and UI surfaces are optional extensions. They can provide more evidence or a broader demo surface, but they are not required for the core evidence-first loop.

## Limitations

The architecture improves auditability and process reliability. It does not prove that all code behavior is correct, that local tests are complete, or that the project has external benchmark performance.
