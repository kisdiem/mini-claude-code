# Core Runtime Architecture

Mini Claude Code is organized around an evidence-first coding loop. The core runtime does not try to prove global code correctness. It makes the local agent process inspectable and prevents code edits from being reported as successful without relevant verification evidence.

```text
User Prompt
  -> TaskContract
  -> TaskRuntime
     -> TaskStateMachine
     -> Semantic Gates
     -> VerificationPolicy / VerificationRegistry
     -> RuntimeFinalEvaluator
     -> EvidenceLedger
     -> Report Builder
  -> Evidence Report
```

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

`mini_cc.verification_policy.VerificationPolicy` classifies commands through a small `VerificationRegistry` of deterministic `VerificationRule` objects. Command classes include `test`, `lint`, `typecheck`, `build`, `docs/check`, `runtime-evidence`, `fake`, and `unknown`. It rejects inspection commands such as `git diff`, `git status`, `echo`, `cat`, `ls`, `pwd`, `find`, `grep`, `read_file`, `list_files`, and `context_snapshot` as verification. It also detects zero-test or skipped-check output such as `collected 0 items`, `Ran 0 tests`, `missing script`, and `no tests ran`.

`mini_cc.verification` remains focused on discovering candidate verification commands from the local workspace.

### Evidence Ledger

`mini_cc.evidence.EvidenceLedger` is the append-only record of what happened during a run. Runtime events such as task start, assistant text, tool calls, tool results, file reads, file modifications, plan declarations, verification results, semantic decisions, and final decisions are serialized into the Evidence Report. Records include stable per-run event ids, timestamps, severity, phase, and parent links for tool call/result correlation.

### Runtime Coordinator

`mini_cc.task_runtime.TaskRuntime` coordinates the task contract, process state machine, semantic gates, verification policy, evidence ledger, and the compatibility `CodingLoopPolicy`. `RuntimeFinalEvaluator` normalizes final status in one place so failed verification, repair limits, semantic blockers, max turns, and successful verification are handled consistently.

### Reporting

`mini_cc.reporting.build_evidence_report` builds `.mini_cc/task-success/last-run.json` from runtime state, `EvidenceLedger`, and `FinalDecision`. The report keeps backward-compatible keys while adding richer audit fields.

### Compatibility

`CodingLoopPolicy` is retained for existing CLI behavior, mock-mode tests, and direct policy users. Its artifact writer is legacy-compatible; `TaskRuntime` is the preferred Evidence Report writer for agent runs.

The report remains at:

```text
.mini_cc/task-success/last-run.json
```

The report includes backward-compatible keys plus `schema_version`, `evidence`, `verification_results`, and `final_decision`.

## Optional Extensions

S20 tools, hooks, memory, MCP experiments, subagents, and UI surfaces are optional extensions. They can provide more evidence or a broader demo surface, but they are not required for the core evidence-first loop.

## Limitations

The architecture improves auditability and process reliability. It does not prove that all code behavior is correct, that local tests are complete, or that the project has external benchmark performance.
