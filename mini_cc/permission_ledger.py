from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PermissionLedger:
    """Append-only permission decision ledger."""

    path: Path | None

    def record(
        self,
        *,
        decision: str,
        name: str,
        action: str,
        risk: str,
        reason: str = "",
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
        subagent: str | None = None,
        request_id: str | None = None,
    ) -> str:
        request_id = request_id or uuid.uuid4().hex
        if self.path is None:
            return request_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "request_id": request_id,
            "ts": _now(),
            "decision": decision,
            "name": name,
            "action": action,
            "risk": risk,
            "reason": reason,
            "input": redact_permission_value(tool_input or {}),
            "session_id": session_id,
            "subagent": subagent,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return request_id


def redact_permission_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ["token", "authorization", "api_key", "apikey", "secret", "password"]):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_permission_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_permission_value(item) for item in value]
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200] + f"\n[truncated {len(value) - 1200} chars]"
    return value
