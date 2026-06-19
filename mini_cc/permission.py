from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PermissionRisk(str, Enum):
    READ = "read"
    VERIFY = "verify"
    WORKSPACE_WRITE = "workspace_write"
    NETWORK = "network"
    PACKAGE_MANAGER = "package_manager"
    DOCKER = "docker"
    GIT_REMOTE_WRITE = "git_remote_write"
    DESTRUCTIVE = "destructive"
    UNKNOWN_SHELL = "unknown_shell"


@dataclass(frozen=True)
class PermissionDecision:
    allow: bool
    risk: PermissionRisk
    reason: str


@dataclass(frozen=True)
class PermissionPolicy:
    allow_risks: set[PermissionRisk]
    block_risks: set[PermissionRisk]

    @classmethod
    def default(cls) -> "PermissionPolicy":
        return cls(allow_risks=set(), block_risks=set())

    @classmethod
    def from_config(cls, payload: dict[str, Any] | None) -> "PermissionPolicy":
        if not isinstance(payload, dict):
            return cls.default()
        return cls(
            allow_risks={risk for risk in (_risk_from_text(item) for item in payload.get("allow_risks", [])) if risk},
            block_risks={risk for risk in (_risk_from_text(item) for item in payload.get("block_risks", [])) if risk},
        )


READ_COMMANDS = {
    "cat",
    "cd",
    "dir",
    "echo",
    "findstr",
    "git diff",
    "git log",
    "git show",
    "git status",
    "get-childitem",
    "get-content",
    "ls",
    "pwd",
    "rg",
    "select-string",
    "type",
    "where",
}

VERIFY_COMMANDS = {
    ".\\gradlew test",
    "./gradlew test",
    "cargo check",
    "cargo test",
    "dotnet test",
    "gradle test",
    "go test",
    "make check",
    "make test",
    "mvn test",
    "mypy",
    "npm test",
    "npm run lint",
    "npm run test",
    "pnpm test",
    "pnpm run lint",
    "pnpm run test",
    "pytest",
    "py -m pytest",
    "py -m unittest",
    "py -3 -m unittest",
    "py -3 -m pytest",
    "python -m mypy",
    "python -m pytest",
    "python -m unittest",
    "ruff",
    "ruff check",
    "tsc",
    "yarn lint",
    "yarn test",
}

NETWORK_COMMANDS = {
    "curl",
    "curl.exe",
    "git clone",
    "invoke-restmethod",
    "invoke-webrequest",
    "wget",
}

PACKAGE_MANAGER_COMMANDS = {
    "apt",
    "apt-get",
    "cargo add",
    "cargo install",
    "choco",
    "npm install",
    "pip install",
    "pip3 install",
    "pnpm add",
    "pnpm install",
    "poetry add",
    "python -m pip install",
    "winget",
    "yarn add",
    "yarn install",
}


def classify_shell_command(command: str) -> PermissionDecision:
    """Classify shell command risk using general command semantics."""
    normalized = _normalize(command)

    if _matches_any(
        normalized,
        [
            r"\brm\s+-[^\n&|;]*r",
            r"\bdel\s+/s\b",
            r"\brmdir\s+/s\b",
            r"\bremove-item\b[^\n&|;]*-recurse\b",
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\b[^\n&|;]*-[^\n&|;]*f",
            r"\bformat\b",
            r"\bshutdown\b",
            r"\brestart-computer\b",
            r"\bstop-computer\b",
            r"\bdocker\s+system\s+prune\b",
            r"\bdocker\s+volume\s+prune\b",
            r"\bdocker\s+container\s+prune\b",
            r"\bdocker\s+image\s+prune\b",
        ],
    ):
        return PermissionDecision(False, PermissionRisk.DESTRUCTIVE, "destructive shell command")

    if _matches_any(
        normalized,
        [
            r"\bgit\s+push\b",
            r"\bgit\s+push\s+--mirror\b",
            r"\bgh\s+repo\s+delete\b",
            r"\bgh\s+release\s+delete\b",
        ],
    ):
        return PermissionDecision(False, PermissionRisk.GIT_REMOTE_WRITE, "remote repository write")

    if _matches_any(
        normalized,
        [
            r"\bset-content\b",
            r"\badd-content\b",
            r"\bout-file\b",
            r"\bnew-item\b",
            r"\bcopy-item\b",
            r"\bmove-item\b",
            r"\btouch\b",
            r"\btee\b",
        ],
    ) or re.search(r"(^|[^>])>{1,2}([^>]|$)", command):
        return PermissionDecision(True, PermissionRisk.WORKSPACE_WRITE, "workspace shell write")

    if _starts_with_any(normalized, PACKAGE_MANAGER_COMMANDS):
        return PermissionDecision(True, PermissionRisk.PACKAGE_MANAGER, "package manager command")

    if _starts_with_any(normalized, NETWORK_COMMANDS):
        return PermissionDecision(True, PermissionRisk.NETWORK, "network command")

    if normalized.startswith("docker "):
        return PermissionDecision(True, PermissionRisk.DOCKER, "docker command")

    if _starts_with_any(normalized, VERIFY_COMMANDS):
        return PermissionDecision(True, PermissionRisk.VERIFY, "verification command")

    if _starts_with_any(normalized, READ_COMMANDS):
        return PermissionDecision(True, PermissionRisk.READ, "read-only shell command")

    return PermissionDecision(True, PermissionRisk.UNKNOWN_SHELL, "unclassified shell command")


def decide_permission(
    mode: str,
    action: str,
    risk: PermissionRisk,
    policy: PermissionPolicy | None = None,
) -> PermissionDecision:
    policy = policy or PermissionPolicy.default()
    if risk in policy.block_risks:
        return PermissionDecision(False, risk, f"blocked by configured permission policy: {action}")
    if risk in policy.allow_risks:
        return PermissionDecision(True, risk, f"allowed by configured permission policy: {action}")

    if mode == "auto":
        if risk in {PermissionRisk.DESTRUCTIVE, PermissionRisk.GIT_REMOTE_WRITE}:
            return PermissionDecision(False, risk, f"blocked high-risk action: {action}")
        return PermissionDecision(True, risk, f"allowed by auto mode: {action}")

    if mode == "read-only":
        if risk in {PermissionRisk.READ, PermissionRisk.VERIFY}:
            return PermissionDecision(True, risk, f"allowed read-only action: {action}")
        return PermissionDecision(False, risk, f"blocked by read-only mode: {action}")

    return PermissionDecision(False, risk, f"requires confirmation: {action}")


def _normalize(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())


def _starts_with_any(command: str, prefixes: set[str]) -> bool:
    return any(command == prefix or command.startswith(prefix + " ") for prefix in prefixes)


def _matches_any(command: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, command) for pattern in patterns)


def _risk_from_text(value: Any) -> PermissionRisk | None:
    try:
        return PermissionRisk(str(value))
    except ValueError:
        return None
