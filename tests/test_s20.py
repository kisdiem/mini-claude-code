from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.s20 import S20ToolRunner


class S20ToolRunnerTests(unittest.TestCase):
    def test_todo_and_memory_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = S20ToolRunner(Path(tmp), permission="auto")

            todo = runner.run(
                "todo_write",
                {
                    "items": [
                        {"id": "1", "content": "inspect", "status": "completed"},
                        {"id": "2", "content": "edit", "status": "in_progress"},
                    ]
                },
            )
            self.assertFalse(todo.is_error, todo.content)
            self.assertIn("edit", runner.run("todo_read", {}).content)

            memory = runner.run(
                "memory_write",
                {"key": "style", "value": "keep changes minimal"},
            )
            self.assertFalse(memory.is_error, memory.content)
            self.assertIn("style", runner.run("memory_read", {}).content)

    def test_memory_v2_writes_metadata_and_recalls_relevant_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = S20ToolRunner(Path(tmp), permission="auto", state_dir=Path(tmp, ".mini_cc"))

            runner.run(
                "memory_write",
                {
                    "key": "python-style",
                    "value": "prefer surgical edits",
                    "scope": "repo",
                    "priority": 80,
                    "source": "test",
                    "tags": ["python", "style"],
                },
            )
            runner.run(
                "memory_write",
                {
                    "key": "docker-note",
                    "value": "check daemon before Terminal-Bench",
                    "priority": 30,
                    "tags": ["docker"],
                },
            )

            memory = runner.run("memory_read", {})
            self.assertFalse(memory.is_error, memory.content)
            self.assertIn("scope=repo", memory.content)
            self.assertIn("priority=80", memory.content)
            self.assertIn("tags=python,style", memory.content)

            recalled = runner.run("memory_recall", {"query": "python edits", "limit": 3})
            self.assertFalse(recalled.is_error, recalled.content)
            self.assertIn("python-style", recalled.content)
            self.assertNotIn("docker-note", recalled.content)

    def test_memory_v2_reads_legacy_key_value_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp, ".mini_cc")
            state_dir.mkdir()
            Path(state_dir, "memory.json").write_text(
                '{"style": "keep changes minimal"}\n',
                encoding="utf-8",
            )
            runner = S20ToolRunner(Path(tmp), permission="auto", state_dir=state_dir)

            result = runner.run("memory_read", {})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("style: keep changes minimal", result.content)
            self.assertIn("scope=project", result.content)

    def test_rejects_multiple_in_progress_todos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = S20ToolRunner(Path(tmp), permission="auto")
            result = runner.run(
                "todo_write",
                {
                    "items": [
                        {"id": "1", "content": "a", "status": "in_progress"},
                        {"id": "2", "content": "b", "status": "in_progress"},
                    ]
                },
            )
            self.assertTrue(result.is_error)
            self.assertIn("Only one todo", result.content)

    def test_context_snapshot_contains_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "README.md").write_text("# Demo\n", encoding="utf-8")
            runner = S20ToolRunner(Path(tmp), permission="auto")
            result = runner.run("context_snapshot", {})
            self.assertFalse(result.is_error, result.content)
            self.assertIn("# Workspace", result.content)
            self.assertIn("README.md", result.content)

    def test_context_snapshot_uses_query_aware_memory_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp, ".mini_cc")
            runner = S20ToolRunner(Path(tmp), permission="auto", state_dir=state_dir)
            runner.run(
                "memory_write",
                {"key": "terminal-bench", "value": "parse results.json before scoring", "priority": 90},
            )
            runner.run(
                "memory_write",
                {"key": "ui-style", "value": "avoid oversized cards", "priority": 90},
            )

            result = runner.run(
                "context_snapshot",
                {"query": "Terminal-Bench results.json", "memory_limit": 1, "token_budget": 300},
            )

            self.assertFalse(result.is_error, result.content)
            self.assertIn("# Task Query", result.content)
            self.assertIn("# Context Source Registry", result.content)
            self.assertIn("# Durable Memory", result.content)
            self.assertIn("terminal-bench", result.content)
            self.assertNotIn("ui-style", result.content)

    def test_context_snapshot_distinguishes_source_registry_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc"
            sessions_dir = state_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            Path(root, "AGENTS.md").write_text("Always keep changes minimal.\n", encoding="utf-8")
            Path(sessions_dir, "abc.json").write_text(
                """{
  "id": "abc",
  "started_at": "2026-06-19T00:00:00+00:00",
  "prompt": "inspect project",
  "status": "completed",
  "events": [
    {"event": "turn_start", "payload": {"turn": 1}, "ts": "2026-06-19T00:00:00+00:00"},
    {"event": "tool_use", "payload": {"turn": 1, "name": "list_files", "is_error": false, "chars": 42}, "ts": "2026-06-19T00:00:00+00:00"}
  ],
  "messages": [
    {"role": "user", "content": "Conversation compaction summary:\\nTool calls:\\n- tool=list_files status=ok input={} result=README.md"}
  ]
}
""",
                encoding="utf-8",
            )
            runner = S20ToolRunner(root, permission="auto", state_dir=state_dir)
            runner.run("memory_write", {"key": "style", "value": "surgical edits", "priority": 90})

            result = runner.run("context_snapshot", {"query": "style", "token_budget": 1200})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("type=durable_memory", result.content)
            self.assertIn("type=recent_session_facts", result.content)
            self.assertIn("type=tool_summaries", result.content)
            self.assertIn("type=user_instructions", result.content)
            self.assertIn("# User Instructions", result.content)
            self.assertIn("# Recent Session Facts", result.content)
            self.assertIn("# Tool Summaries", result.content)
            self.assertIn("tool=list_files", result.content)


if __name__ == "__main__":
    unittest.main()
