# Interview Summary

Mini Claude Code is a local coding-agent runtime prototype. I built it to study
how an LLM coding assistant can be structured as a runtime system instead of a
single prompt.

The project includes a model-tool loop, workspace file tools, shell execution,
permission control, hook events, context snapshots, task-state enforcement,
verification gates, and semantic task-success checks. The main engineering
focus is reliability: preventing the agent from editing before understanding
the workspace, requiring a plan before modification, forcing real verification
after code edits, and recording evidence for each run.

The project is intentionally presented as an engineering prototype, not as a
replacement for Claude Code or as a benchmark-scored coding agent.

## What To Review

- `mini_cc/agent.py`: the model/tool loop and runtime integration points.
- `mini_cc/tools.py`: workspace tools and command execution.
- `mini_cc/permission.py`: risk classification and permission policy.
- `mini_cc/hooks.py`: lifecycle hook events and hook decisions.
- `mini_cc/coding_loop.py`: verification gate after code edits.
- `mini_cc/task_state.py`: staged coding task process control.
- `mini_cc/task_success.py`: deterministic semantic evidence checks.
- `tests/`: offline unit tests for the runtime mechanisms.

## Evaluation Framing

The repository should be evaluated as a compact engineering study of coding
agent orchestration. The most relevant review questions are:

- Is the runtime behavior inspectable?
- Are risky operations controlled by explicit policy?
- Does the agent record evidence for what it did?
- Does the coding flow prevent common failure modes such as editing too early or
  finishing without verification?

Local smoke tests and task-success artifacts are included to make these
questions easier to inspect. They are not presented as external benchmark
results.
