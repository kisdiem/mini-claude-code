# S20 Comprehensive

This folder is the final teaching checkpoint for the project. It keeps the original
minimal agent runnable, then enables the comprehensive toolset with `--s20`.

Covered mechanisms:

- agent loop
- tool schema and tool result feedback
- workspace path sandbox
- permission modes
- hooks log
- todo state
- project memory
- local skills
- git read tools
- context snapshot for long tasks
- deterministic mock provider
- real Anthropic provider

Run without an API key:

```powershell
cd C:\Users\sixth\mini-claude-code
py -3 -m mini_cc --mock --s20 --permission auto --workspace . "s20 snapshot"
py -3 -m mini_cc --mock --s20 --permission auto --workspace . "todo"
```

State files are stored in `.mini_cc/` inside the selected workspace.
