from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_cc.context import ContextBuilder, ContextSection, context_source_priority, compress_text, estimate_tokens
from mini_cc.s20 import S20ToolRunner


class FakeRunner:
    root = Path("C:/workspace")

    def list_files(self, path: str = ".", recursive: bool = False, max_entries: int = 120) -> str:
        del path, recursive, max_entries
        return "\n".join(f"file_{index}.py" for index in range(200))

    def git_status(self) -> str:
        return "\n".join(f"M file_{index}.py" for index in range(100))

    def todo_read(self) -> str:
        return "1: in_progress - keep this todo"

    def memory_read(self) -> str:
        return "\n".join(f"fact_{index}: value_{index}" for index in range(100))


class RecallRunner(FakeRunner):
    def memory_recall(
        self,
        query: str = "",
        scope: str | None = None,
        min_priority: int = 0,
        limit: int = 12,
    ) -> str:
        del scope, min_priority, limit
        return f"recalled for {query}: stable fact"


class ContextBudgetTests(unittest.TestCase):
    def test_compress_text_preserves_head_tail_and_marks_omission(self) -> None:
        text = "A" * 500 + "MIDDLE" + "Z" * 500

        compressed = compress_text(text, token_budget=40)

        self.assertIn("context compressed", compressed)
        self.assertTrue(compressed.startswith("A"))
        self.assertTrue(compressed.endswith("Z"))
        self.assertLess(len(compressed), len(text))

    def test_budgeted_context_reports_compressed_sections(self) -> None:
        builder = ContextBuilder(FakeRunner())

        result = builder.build_workspace_snapshot(token_budget=220)

        self.assertIn("# Context Budget", result.text)
        self.assertIn("compressed_sections:", result.text)
        self.assertTrue(result.report.compressed_sections)
        self.assertLessEqual(estimate_tokens(result.text), 300)

    def test_section_priority_preserves_high_priority_small_section(self) -> None:
        builder = ContextBuilder(FakeRunner())
        sections = [
            ContextSection("Low", "x" * 4000, priority=1, min_tokens=24),
            ContextSection("High", "important invariant", priority=100, min_tokens=24),
        ]

        result = builder.render_budgeted(sections, token_budget=160)

        self.assertIn("important invariant", result.text)
        self.assertIn("Low", result.report.compressed_sections)

    def test_s20_context_snapshot_accepts_token_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(50):
                Path(tmp, f"file_{index}.txt").write_text("x" * 200, encoding="utf-8")
            runner = S20ToolRunner(Path(tmp), permission="auto")

            result = runner.run("context_snapshot", {"token_budget": 180})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("# Context Budget", result.content)
            self.assertIn("token_budget: 180", result.content)

    def test_context_snapshot_uses_memory_recall_when_available(self) -> None:
        builder = ContextBuilder(RecallRunner())

        result = builder.build_workspace_snapshot(token_budget=260, query="benchmark score", memory_limit=2)

        self.assertIn("# Task Query", result.text)
        self.assertIn("# Durable Memory", result.text)
        self.assertIn("recalled for benchmark score", result.text)

    def test_context_source_registry_documents_evidence_priority(self) -> None:
        builder = ContextBuilder(FakeRunner())

        result = builder.build_workspace_snapshot(token_budget=320)

        self.assertIn("evidence_priority_order:", result.text)
        self.assertGreater(context_source_priority("user_instructions"), context_source_priority("durable_memory"))
        self.assertGreater(context_source_priority("durable_memory"), context_source_priority("recent_session_facts"))
        self.assertGreater(context_source_priority("tool_summaries"), context_source_priority("model_inference"))


if __name__ == "__main__":
    unittest.main()
