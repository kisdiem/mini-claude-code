from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


MEMORY_VERSION = 2


@dataclass(frozen=True)
class MemoryFact:
    key: str
    value: str
    scope: str = "project"
    priority: int = 50
    source: str = "manual"
    tags: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_memory_payload(payload: Any) -> dict[str, MemoryFact]:
    """Load legacy key/value memory and v2 memory into one fact map."""

    if not payload:
        return {}
    if isinstance(payload, dict) and payload.get("version") == MEMORY_VERSION and isinstance(payload.get("facts"), dict):
        return {
            str(key): normalize_fact(key, value)
            for key, value in payload["facts"].items()
        }
    if isinstance(payload, dict):
        return {
            str(key): normalize_fact(key, value)
            for key, value in payload.items()
            if key not in {"version", "facts"}
        }
    return {}


def serialize_memory_payload(facts: dict[str, MemoryFact]) -> dict[str, Any]:
    return {
        "version": MEMORY_VERSION,
        "facts": {
            key: fact.to_json()
            for key, fact in sorted(facts.items())
        },
    }


def normalize_fact(key: Any, value: Any) -> MemoryFact:
    if isinstance(value, dict):
        return MemoryFact(
            key=str(value.get("key", key)),
            value=str(value.get("value", "")),
            scope=str(value.get("scope", "project")),
            priority=clamp_priority(value.get("priority", 50)),
            source=str(value.get("source", "manual")),
            tags=normalize_tags(value.get("tags", [])),
            updated_at=str(value.get("updated_at", value.get("updatedAt", ""))) or now_iso(),
        )
    return MemoryFact(key=str(key), value=str(value), updated_at=now_iso())


def make_memory_fact(
    *,
    key: str,
    value: str,
    scope: str = "project",
    priority: int = 50,
    source: str = "manual",
    tags: list[str] | None = None,
) -> MemoryFact:
    return MemoryFact(
        key=str(key),
        value=str(value),
        scope=normalize_scope(scope),
        priority=clamp_priority(priority),
        source=str(source or "manual"),
        tags=normalize_tags(tags or []),
        updated_at=now_iso(),
    )


def format_memory_facts(facts: dict[str, MemoryFact]) -> str:
    if not facts:
        return "[empty memory]"
    rows = []
    for key, fact in sorted(facts.items(), key=lambda item: (-item[1].priority, item[0])):
        tags = f" tags={','.join(fact.tags)}" if fact.tags else ""
        rows.append(
            f"{key}: {fact.value} "
            f"[scope={fact.scope}; priority={fact.priority}; source={fact.source}{tags}; updated_at={fact.updated_at}]"
        )
    return "\n".join(rows)


def recall_memory_facts(
    facts: dict[str, MemoryFact],
    *,
    query: str = "",
    limit: int = 12,
    min_priority: int = 0,
    scope: str | None = None,
) -> list[MemoryFact]:
    terms = tokenize(query)
    normalized_scope = normalize_scope(scope) if scope else None
    candidates = []
    for fact in facts.values():
        if fact.priority < min_priority:
            continue
        if normalized_scope is not None and fact.scope != normalized_scope:
            continue
        score = fact.priority
        haystack = " ".join([fact.key, fact.value, fact.scope, fact.source, *fact.tags]).lower()
        if terms:
            matched = sum(1 for term in terms if term in haystack)
            if matched == 0:
                continue
            score += matched * 25
        candidates.append((score, fact.updated_at, fact.key, fact))
    candidates.sort(key=lambda item: (-item[0], item[2]))
    return [item[3] for item in candidates[: max(1, limit)]]


def format_recalled_memory(facts: list[MemoryFact]) -> str:
    if not facts:
        return "[no matching memory]"
    return "\n".join(
        f"{fact.key}: {fact.value} [scope={fact.scope}; priority={fact.priority}; source={fact.source}]"
        for fact in facts
    )


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_\-.]+", text)
        if len(token) >= 2
    }


def clamp_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        priority = 50
    return max(0, min(100, priority))


def normalize_scope(scope: str | None) -> str:
    cleaned = str(scope or "project").strip().lower()
    return cleaned if cleaned in {"project", "task", "user", "repo", "subagent"} else "project"


def normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    normalized = sorted({str(tag).strip().lower() for tag in tags if str(tag).strip()})
    return normalized[:20]
