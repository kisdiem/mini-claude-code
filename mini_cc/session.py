from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionEvent:
    event: str
    payload: dict[str, Any]
    ts: str = field(default_factory=_now)


@dataclass
class AgentSession:
    id: str
    started_at: str
    prompt: str
    model: str | None = None
    ended_at: str | None = None
    status: str = "running"
    events: list[SessionEvent] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """Persist agent sessions and traces as JSON files."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def start(self, prompt: str, *, model: str | None = None) -> AgentSession:
        session = AgentSession(
            id=uuid.uuid4().hex,
            started_at=_now(),
            prompt=prompt,
            model=model,
        )
        self.save(session)
        return session

    def load(self, session_id: str) -> AgentSession | None:
        if self.root is None:
            return None
        path = self.root / f"{session_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        events = [
            SessionEvent(
                event=str(item.get("event", "")),
                payload=dict(item.get("payload", {})),
                ts=str(item.get("ts", _now())),
            )
            for item in payload.get("events", [])
            if isinstance(item, dict)
        ]
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        return AgentSession(
            id=str(payload["id"]),
            started_at=str(payload.get("started_at", _now())),
            prompt=str(payload.get("prompt", "")),
            model=payload.get("model"),
            ended_at=payload.get("ended_at"),
            status=str(payload.get("status", "running")),
            events=events,
            messages=[item for item in messages if isinstance(item, dict)],
        )

    def resume(self, session_id: str, prompt: str) -> AgentSession | None:
        session = self.load(session_id)
        if session is None:
            return None
        session.status = "running"
        session.ended_at = None
        self.record(session, "session_resumed", {"prompt": prompt})
        return session

    def record(self, session: AgentSession, event: str, payload: dict[str, Any]) -> None:
        session.events.append(SessionEvent(event=event, payload=payload))
        self.save(session)

    def finish(self, session: AgentSession, *, status: str = "completed") -> None:
        session.status = status
        session.ended_at = _now()
        self.save(session)

    def update_messages(self, session: AgentSession, messages: list[dict[str, Any]]) -> None:
        session.messages = messages
        self.save(session)

    def save(self, session: AgentSession) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{session.id}.json"
        target.write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
