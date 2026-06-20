from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_TURNS = 8
DEFAULT_SHELL_TIMEOUT = 30
DEFAULT_NESTED_SUBAGENT_DEPTH = 1
DEFAULT_NESTED_SUBAGENT_TOKEN_BUDGET = 1200
DEFAULT_COMPACTION_TOKEN_BUDGET = 6000
DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES = 6
DEFAULT_MODEL_CONTEXT_TOKEN_BUDGET = 8000
DEFAULT_PERMISSION = "ask"
DEFAULT_DESKTOP_MODEL_CHOICES = ["", "gpt-5.5", "gpt-5", "gpt-4.1", "claude-sonnet-4-6"]
DEFAULT_SUBAGENT_SYSTEM_PROMPTS = {
    "explorer": "You are a read-only exploration subagent. Gather facts and cite tool observations.",
    "implementer": "You are an implementation subagent. Make focused edits only after inspecting target files.",
    "verifier": "You are a verification subagent. Run targeted checks and classify failures.",
    "critic": "You are a critical review subagent. Prefer concrete risks over general advice.",
    "bench-diagnoser": "You are a benchmark diagnostics subagent. Separate model failures from environment failures.",
}

DEFAULT_SYSTEM_PROMPT = """You are Mini Claude Code, a local coding assistant.

Work like a careful coding agent:
- Inspect the workspace before changing files.
- Use tools for file reads, searches, edits, and commands.
- Prefer apply_patch for code edits when exact string replacement is fragile.
- Keep edits minimal and explain important tradeoffs.
- Never claim a command or edit succeeded unless a tool result confirms it.
- For coding tasks, follow phases: INTAKE, EXPLORE, LOCALIZE, PLAN, EDIT, VERIFY, REPAIR, FINAL.
- Explore and localize before editing. Do not modify a file before reading it.
- Produce a minimal edit plan with planned_files before changing files.
- For code modification tasks, run a real verification command after editing; git_status, git_diff, context_snapshot, list_files, read_file, and search_text are not verification.
- If verification fails, analyze the failure output before making one minimal repair.
- If a task needs writes or shell commands, ask through the available tools and obey permission denials.
"""

DEFAULT_S20_SYSTEM_PROMPT = """You are Mini Claude Code S20, a comprehensive local coding agent.

Use the workspace tools as a disciplined engineering loop:
- For coding tasks, move through phases: INTAKE, EXPLORE, LOCALIZE, PLAN, EDIT, VERIFY, REPAIR, FINAL.
- First inspect context and maintain todo state for multi-step tasks.
- Prefer read/search/git-status before edits.
- Localize the likely files, functions, classes, and tests before editing.
- Produce a minimal plan with planned_files before changing files.
- Store durable project facts with memory_write when they affect future work.
- Use skill_list and skill_read when a named workflow is relevant.
- Use write_file/replace_text only after you know the existing file state; prefer apply_patch for code edits when exact string replacement is fragile.
- Do not modify a file that you have not read.
- Do not make broad rewrites unless the task explicitly requires them.
- For code modification tasks, do not produce a final answer immediately after editing.
- After any write_file, replace_text, or apply_patch, run a real verification command.
- git_status, git_diff, context_snapshot, list_files, read_file, and search_text are not verification.
- If verification fails, inspect the failure output, explain the cause, and make one minimal repair before running verification again.
- Stop only when verification passes, or when the repair limit is reached.
- Final answers for code edits must report changed files and verification result.
- Use run_shell for verification and report exact failures.
- Keep final answers concise and grounded in tool results.

Benchmark discipline:
- You may receive Russian, corrupted, or obfuscated task text. Do not ask for clarification.
- Extract concrete file paths, function/class names, literals, examples, expected outputs, and formats from the prompt.
- Copy quoted text exactly into files when the task asks for a literal or docstring; do not translate it.
- For edit/refactor tasks, preserve unrelated imports, constants, assignments, functions, and formatting unless explicitly told to remove them.
- Before editing an existing file, read it and use replace_text for surgical edits when practical.
- For Python type hint tasks, change only the function signature and keep the body unchanged.
- Treat test files as verification context unless the task explicitly asks you to create or edit tests.
- When AGENTS.md asks you to save user facts in MEMORY.md, infer the canonical fact from the user's message and save it in the exact format required by AGENTS.md or existing MEMORY.md. Do not use an entire prose sentence as the memory key.
- For memory keys, choose the shortest stable category key that matches AGENTS.md or existing memory. Do not include contextual modifiers like current, work, usual, or preferred in the key unless the schema itself uses that key; put the normalized fact value after the colon.
- Distinguish exact literals from semantic facts: exact outputs, code, filenames, and docstrings are copied verbatim; user profile facts, preferences, dates, locations, contacts, and tools are normalized into the requested data/memory schema.
- For deterministic text-derived reports, manifests, and hashes, treat common text files as logical text: read/decode text and normalize CRLF/CR to LF before calculating. Use raw bytes only when the task explicitly says binary, byte-for-byte, or raw bytes.
- If the task names a missing file or directory, create it.
- Complete the requested file changes in the workspace before giving a final answer.
- Stop once the task is complete; do not keep exploring after the necessary files are written.
"""


def load_env_file(path: Path) -> None:
    """Load environment variables from .env via python-dotenv."""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_env_file_fallback(path)
        return
    load_dotenv(path, override=False)


@dataclass(frozen=True)
class AppConfig:
    workspace: Path
    provider: str
    model: str
    openai_model: str
    max_tokens: int
    max_turns: int
    permission: str
    shell_timeout: int
    api_key: str | None
    openai_api_key: str | None
    base_url: str | None
    openai_reasoning_effort: str | None
    system_prompt: str
    s20_system_prompt: str
    nested_subagent_depth: int
    nested_subagent_token_budget: int
    compaction_token_budget: int
    compaction_keep_recent_messages: int
    model_context_token_budget: int


def build_config(
    workspace: str,
    permission: str | None = None,
    max_turns: int | None = None,
    shell_timeout: int | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    openai_model: str | None = None,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
    nested_subagent_depth: int | None = None,
    nested_subagent_token_budget: int | None = None,
    compaction_token_budget: int | None = None,
    compaction_keep_recent_messages: int | None = None,
    model_context_token_budget: int | None = None,
) -> AppConfig:
    provider_value = provider or _env_str("MINI_CC_PROVIDER", "anthropic")
    return AppConfig(
        workspace=Path(workspace).expanduser().resolve(),
        provider=provider_value,
        model=model or _env_str("MINI_CC_MODEL", _env_str("CLAUDE_MODEL", DEFAULT_ANTHROPIC_MODEL)),
        openai_model=openai_model or _env_str("MINI_CC_OPENAI_MODEL", _env_str("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)),
        max_tokens=_env_int(["MINI_CC_MAX_TOKENS", "CLAUDE_MAX_TOKENS"], DEFAULT_MAX_TOKENS),
        max_turns=max_turns if max_turns is not None else _env_int(["MINI_CC_MAX_TURNS"], DEFAULT_MAX_TURNS),
        permission=permission or _env_str("MINI_CC_PERMISSION", DEFAULT_PERMISSION),
        shell_timeout=shell_timeout if shell_timeout is not None else _env_int(["MINI_CC_SHELL_TIMEOUT"], DEFAULT_SHELL_TIMEOUT),
        api_key=_env_optional("ANTHROPIC_API_KEY"),
        openai_api_key=_env_optional("OPENAI_API_KEY"),
        base_url=base_url or _env_optional("MINI_CC_BASE_URL") or _env_optional("ANTHROPIC_BASE_URL"),
        openai_reasoning_effort=reasoning_effort or _env_optional("OPENAI_REASONING_EFFORT"),
        system_prompt=_prompt_from_env("MINI_CC_SYSTEM_PROMPT", "MINI_CC_SYSTEM_PROMPT_FILE", DEFAULT_SYSTEM_PROMPT),
        s20_system_prompt=_prompt_from_env("MINI_CC_S20_SYSTEM_PROMPT", "MINI_CC_S20_SYSTEM_PROMPT_FILE", DEFAULT_S20_SYSTEM_PROMPT),
        nested_subagent_depth=(
            nested_subagent_depth
            if nested_subagent_depth is not None
            else _env_int(["MINI_CC_NESTED_SUBAGENT_DEPTH"], DEFAULT_NESTED_SUBAGENT_DEPTH)
        ),
        nested_subagent_token_budget=(
            nested_subagent_token_budget
            if nested_subagent_token_budget is not None
            else _env_int(["MINI_CC_NESTED_SUBAGENT_TOKEN_BUDGET"], DEFAULT_NESTED_SUBAGENT_TOKEN_BUDGET)
        ),
        compaction_token_budget=(
            compaction_token_budget
            if compaction_token_budget is not None
            else _env_int(["MINI_CC_COMPACTION_TOKEN_BUDGET"], DEFAULT_COMPACTION_TOKEN_BUDGET)
        ),
        compaction_keep_recent_messages=(
            compaction_keep_recent_messages
            if compaction_keep_recent_messages is not None
            else _env_int(["MINI_CC_COMPACTION_KEEP_RECENT_MESSAGES"], DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES)
        ),
        model_context_token_budget=(
            model_context_token_budget
            if model_context_token_budget is not None
            else _env_int(["MINI_CC_MODEL_CONTEXT_TOKEN_BUDGET"], DEFAULT_MODEL_CONTEXT_TOKEN_BUDGET)
        ),
    )


def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _env_str(name: str, default: str) -> str:
    return _env_optional(name) or default


def _env_int(names: list[str], default: int) -> int:
    for name in names:
        value = _env_optional(name)
        if value is None:
            continue
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    return default


def _prompt_from_env(value_name: str, file_name: str, default: str) -> str:
    direct = _env_optional(value_name)
    if direct:
        return direct
    prompt_file = _env_optional(file_name)
    if not prompt_file:
        return default
    try:
        return Path(prompt_file).expanduser().read_text(encoding="utf-8")
    except OSError:
        return default


def _load_env_file_fallback(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def subagent_system_prompt(name: str) -> str:
    key = "MINI_CC_SUBAGENT_" + name.upper().replace("-", "_") + "_SYSTEM_PROMPT"
    return _env_optional(key) or DEFAULT_SUBAGENT_SYSTEM_PROMPTS[name]


def desktop_model_choices() -> list[str]:
    value = _env_optional("MINI_CC_DESKTOP_MODEL_CHOICES")
    if not value:
        return list(DEFAULT_DESKTOP_MODEL_CHOICES)
    return [item.strip() for item in value.split(",") if item.strip() or item == ""]
