from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5"


def load_env_file(path: Path) -> None:
    """Load a tiny .env subset without adding another dependency."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class AppConfig:
    workspace: Path
    model: str
    max_tokens: int
    max_turns: int
    permission: str
    shell_timeout: int
    api_key: str | None
    openai_api_key: str | None
    openai_reasoning_effort: str | None


def build_config(
    workspace: str,
    permission: str,
    max_turns: int,
    shell_timeout: int,
) -> AppConfig:
    return AppConfig(
        workspace=Path(workspace).expanduser().resolve(),
        model=os.getenv("CLAUDE_MODEL", DEFAULT_MODEL),
        max_tokens=int(os.getenv("CLAUDE_MAX_TOKENS", "4096")),
        max_turns=max_turns,
        permission=permission,
        shell_timeout=shell_timeout,
        api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT") or None,
    )
