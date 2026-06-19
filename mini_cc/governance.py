from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_PATHS = [
    ".claude/settings.json",
    ".mini_cc/settings.json",
    ".mini_cc/settings.local.json",
]


@dataclass(frozen=True)
class ConfigIssue:
    path: str
    level: str
    message: str


@dataclass
class GovernanceConfig:
    merged: dict[str, Any] = field(default_factory=dict)
    loaded_paths: list[Path] = field(default_factory=list)
    issues: list[ConfigIssue] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "loaded_paths": [str(path) for path in self.loaded_paths],
            "issues": [issue.__dict__ for issue in self.issues],
            "merged": self.merged,
        }


def load_governance_config(workspace: Path) -> GovernanceConfig:
    config = GovernanceConfig()
    for rel_path in CONFIG_PATHS:
        path = workspace / rel_path
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            config.issues.append(ConfigIssue(str(path), "error", f"invalid json: {exc}"))
            continue
        if not isinstance(payload, dict):
            config.issues.append(ConfigIssue(str(path), "error", "settings root must be an object"))
            continue
        validate_settings_payload(path, payload, config.issues)
        config.merged = deep_merge(config.merged, payload)
        config.loaded_paths.append(path)
    return config


def validate_settings_payload(path: Path, payload: dict[str, Any], issues: list[ConfigIssue]) -> None:
    known = {"hooks", "subagents", "permission_policy", "disableAllHooks"}
    for key in payload:
        if key not in known:
            issues.append(ConfigIssue(str(path), "warning", f"unknown top-level key: {key}"))
    if "hooks" in payload and not isinstance(payload["hooks"], dict):
        issues.append(ConfigIssue(str(path), "error", "hooks must be an object"))
    if "subagents" in payload and not isinstance(payload["subagents"], (dict, list)):
        issues.append(ConfigIssue(str(path), "error", "subagents must be an object or list"))
    validate_subagent_auth_settings(path, payload.get("subagents"), issues)
    policy = payload.get("permission_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            issues.append(ConfigIssue(str(path), "error", "permission_policy must be an object"))
        else:
            for key in policy:
                if key not in {"allow_risks", "block_risks"}:
                    issues.append(ConfigIssue(str(path), "warning", f"unknown permission_policy key: {key}"))
            for key in ("allow_risks", "block_risks"):
                if key in policy and not isinstance(policy[key], list):
                    issues.append(ConfigIssue(str(path), "error", f"permission_policy.{key} must be a list"))


def validate_subagent_auth_settings(path: Path, raw_subagents: Any, issues: list[ConfigIssue]) -> None:
    if isinstance(raw_subagents, dict):
        items = [value for value in raw_subagents.values() if isinstance(value, dict)]
    elif isinstance(raw_subagents, list):
        items = [value for value in raw_subagents if isinstance(value, dict)]
    else:
        return
    for item in items:
        servers = item.get("mcp_servers", item.get("mcp", []))
        if not isinstance(servers, list):
            continue
        for server in servers:
            if not isinstance(server, dict):
                continue
            name = str(server.get("name") or "[unnamed]")
            if server.get("auth_token") or server.get("bearer_token"):
                issues.append(
                    ConfigIssue(
                        str(path),
                        "warning",
                        f"mcp server {name} stores a token directly; prefer auth_token_env or bearer_token_env",
                    )
                )
            headers = server.get("headers")
            if isinstance(headers, dict):
                for header_name, value in headers.items():
                    lowered = str(header_name).lower()
                    if lowered in {"authorization", "proxy-authorization", "x-api-key", "api-key"} and value:
                        issues.append(
                            ConfigIssue(
                                str(path),
                                "warning",
                                f"mcp server {name} stores sensitive header {header_name}; prefer headers_env",
                            )
                        )


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
