from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .tools import MAX_TOOL_OUTPUT, _clip


CHARS_PER_TOKEN_ESTIMATE = 4


CONTEXT_SOURCE_PRIORITY: dict[str, int] = {
    "registry": 110,
    "user_instructions": 100,
    "durable_memory": 90,
    "recent_session_facts": 80,
    "tool_summaries": 70,
    "compressed_conversation": 60,
    "workspace": 50,
    "model_inference": 30,
}


def context_source_priority(source_type: str, fallback: int = 50) -> int:
    return CONTEXT_SOURCE_PRIORITY.get(source_type, fallback)


@dataclass(frozen=True)
class ContextSection:
    title: str
    content: str
    priority: int
    min_tokens: int = 32
    source_type: str = "workspace"


@dataclass(frozen=True)
class ContextBudgetReport:
    token_budget: int
    estimated_tokens: int
    compressed_sections: list[str]


@dataclass(frozen=True)
class BudgetedContext:
    text: str
    report: ContextBudgetReport


@dataclass(frozen=True)
class ContextSource:
    id: str
    source_type: str
    title: str
    priority: int
    chars: int


class SnapshotToolRunner(Protocol):
    root: Path

    def list_files(self, path: str = ".", recursive: bool = False, max_entries: int = 120) -> str: ...

    def git_status(self) -> str: ...

    def todo_read(self) -> str: ...

    def memory_read(self) -> str: ...

    def memory_recall(
        self,
        query: str = "",
        scope: str | None = None,
        min_priority: int = 0,
        limit: int = 12,
    ) -> str: ...


class ContextBuilder:
    """Build compact task context from independent context sources."""

    def __init__(self, runner: SnapshotToolRunner, *, default_token_budget: int = 4096) -> None:
        self.runner = runner
        self.default_token_budget = default_token_budget

    def workspace_snapshot(
        self,
        token_budget: int | None = None,
        query: str = "",
        memory_limit: int = 12,
    ) -> str:
        budget = self.build_workspace_snapshot(
            token_budget=token_budget,
            query=query,
            memory_limit=memory_limit,
        )
        return budget.text

    def build_workspace_snapshot(
        self,
        token_budget: int | None = None,
        query: str = "",
        memory_limit: int = 12,
    ) -> BudgetedContext:
        try:
            git_status = self.runner.git_status()
        except Exception as exc:
            git_status = f"[git unavailable] {exc}"
        try:
            memory = self.runner.memory_recall(query=query, limit=memory_limit)
        except (AttributeError, TypeError):
            memory = self.runner.memory_read()
        user_instructions = self.load_user_instructions()
        recent_session_facts = self.load_recent_session_facts()
        tool_summaries = self.load_tool_summaries()

        sections = [
            ContextSection("Workspace", str(self.runner.root), priority=context_source_priority("workspace"), min_tokens=12, source_type="workspace"),
            ContextSection(
                "Files",
                self.runner.list_files(".", recursive=False, max_entries=120),
                priority=context_source_priority("workspace"),
                min_tokens=48,
                source_type="workspace",
            ),
            ContextSection("Git", git_status, priority=context_source_priority("workspace"), min_tokens=48, source_type="workspace"),
            ContextSection("Todos", self.runner.todo_read(), priority=context_source_priority("recent_session_facts"), min_tokens=48, source_type="recent_session_facts"),
            ContextSection("Durable Memory", memory, priority=context_source_priority("durable_memory"), min_tokens=48, source_type="durable_memory"),
        ]
        if user_instructions:
            sections.append(
                ContextSection(
                    "User Instructions",
                    user_instructions,
                    priority=context_source_priority("user_instructions"),
                    min_tokens=48,
                    source_type="user_instructions",
                )
            )
        if recent_session_facts:
            sections.append(
                ContextSection(
                    "Recent Session Facts",
                    recent_session_facts,
                    priority=context_source_priority("recent_session_facts"),
                    min_tokens=48,
                    source_type="recent_session_facts",
                )
            )
        if tool_summaries:
            sections.append(
                ContextSection(
                    "Tool Summaries",
                    tool_summaries,
                    priority=context_source_priority("tool_summaries"),
                    min_tokens=48,
                    source_type="tool_summaries",
                )
            )
        registry = self.build_source_registry(sections)
        sections.insert(0, ContextSection("Context Source Registry", registry, priority=context_source_priority("registry"), min_tokens=48, source_type="registry"))
        if query:
            sections.insert(2, ContextSection("Task Query", query, priority=context_source_priority("user_instructions"), min_tokens=24, source_type="user_instructions"))
        budget = max(128, int(token_budget or self.default_token_budget))
        return self.render_budgeted(sections, budget)

    def build_source_registry(self, sections: list[ContextSection]) -> str:
        sources = [
            ContextSource(
                id=f"source-{index}",
                source_type=section.source_type,
                title=section.title,
                priority=section.priority,
                chars=len(section.content),
            )
            for index, section in enumerate(sections, start=1)
        ]
        rows = [
            "source_type values: user_instructions, durable_memory, recent_session_facts, tool_summaries, compressed_conversation, workspace, model_inference",
            "evidence_priority_order: user_instructions > durable_memory > recent_session_facts > tool_summaries > compressed_conversation > workspace > model_inference",
            "registered_source_types: "
            + "; ".join(
                f"type={name}; priority={priority}"
                for name, priority in sorted(CONTEXT_SOURCE_PRIORITY.items(), key=lambda item: item[1], reverse=True)
            ),
        ]
        rows.extend(
            f"- {source.id}: type={source.source_type}; title={source.title}; priority={source.priority}; chars={source.chars}"
            for source in sources
        )
        return "\n".join(rows)

    def load_user_instructions(self) -> str:
        path = self.runner.root / "AGENTS.md"
        if not path.exists() or not path.is_file():
            return ""
        return _clip(path.read_text(encoding="utf-8", errors="replace").strip(), limit=3000)

    def load_recent_session_facts(self) -> str:
        state_dir = getattr(self.runner, "state_dir", None)
        if state_dir is None:
            return ""
        sessions_dir = Path(state_dir) / "sessions"
        if not sessions_dir.exists():
            return ""
        rows: list[str] = []
        for path in sorted(sessions_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:3]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                f"- session={payload.get('id', path.stem)} status={payload.get('status', '[unknown]')} "
                f"prompt={_clip(str(payload.get('prompt', '')), 240)}"
            )
            events = payload.get("events", [])
            if isinstance(events, list):
                names = [str(event.get("event")) for event in events if isinstance(event, dict) and event.get("event")]
                if names:
                    rows.append("  events=" + ", ".join(names[-8:]))
        return "\n".join(rows)

    def load_tool_summaries(self) -> str:
        state_dir = getattr(self.runner, "state_dir", None)
        if state_dir is None:
            return ""
        sessions_dir = Path(state_dir) / "sessions"
        if not sessions_dir.exists():
            return ""
        rows: list[str] = []
        for path in sorted(sessions_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:5]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for event in payload.get("events", []):
                if not isinstance(event, dict) or event.get("event") != "tool_use":
                    continue
                detail = event.get("payload", {})
                if not isinstance(detail, dict):
                    continue
                rows.append(
                    "- "
                    + f"session={payload.get('id', path.stem)} turn={detail.get('turn')} "
                    + f"tool={detail.get('name')} is_error={detail.get('is_error')} chars={detail.get('chars')}"
                )
            for message in payload.get("messages", []):
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content = message["content"]
                    if content.startswith("Conversation compaction summary:"):
                        rows.append("- compacted_summary=" + _clip(content, 700))
        return "\n".join(rows[-12:])

    def render_budgeted(self, sections: list[ContextSection], token_budget: int) -> BudgetedContext:
        overhead_tokens = estimate_tokens("\n\n".join(f"# {section.title}" for section in sections))
        available = max(32, token_budget - overhead_tokens - 24)
        weights = sum(max(1, section.priority) for section in sections)
        compressed: list[str] = []
        rendered: list[str] = []
        for section in sections:
            section_tokens = estimate_tokens(section.content)
            allocation = max(section.min_tokens, int(available * max(1, section.priority) / weights))
            if section_tokens > allocation:
                compressed.append(section.title)
                content = compress_text(section.content, token_budget=allocation)
            else:
                content = section.content
            rendered.extend([f"# {section.title}", content, ""])
        body = "\n".join(rendered).rstrip()
        estimated = estimate_tokens(body)
        if estimated > token_budget:
            body = compress_text(body, token_budget=token_budget)
            compressed.append("full_snapshot")
            estimated = estimate_tokens(body)
        report = ContextBudgetReport(
            token_budget=token_budget,
            estimated_tokens=estimated,
            compressed_sections=compressed,
        )
        report_text = "\n".join(
            [
                "# Context Budget",
                f"token_budget: {report.token_budget}",
                f"estimated_tokens: {report.estimated_tokens}",
                "compressed_sections: "
                + (", ".join(report.compressed_sections) if report.compressed_sections else "[none]"),
            ]
        )
        final_text = _clip(body + "\n\n" + report_text, limit=MAX_TOOL_OUTPUT)
        return BudgetedContext(final_text, report)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)


def compress_text(text: str, *, token_budget: int) -> str:
    char_budget = max(80, token_budget * CHARS_PER_TOKEN_ESTIMATE)
    if len(text) <= char_budget:
        return text
    omitted = len(text) - char_budget
    head = max(32, char_budget * 2 // 3)
    tail = max(24, char_budget - head)
    return (
        text[:head].rstrip()
        + f"\n[context compressed: omitted {omitted} chars]\n"
        + text[-tail:].lstrip()
    )
