# Parallel Writer Example

This example documents the required artifacts for write-capable subagents before merge:

- isolated worktree metadata;
- changed file list;
- patch preview or diff;
- `EVIDENCE:` line in the subagent output;
- `VERIFICATION:` or `TEST:` line in the subagent output;
- merge gate result.

The 3.4 runtime blocks merge when any writer is missing diff, evidence, or verification.
