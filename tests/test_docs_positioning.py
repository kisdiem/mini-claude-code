from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsPositioningTests(unittest.TestCase):
    def test_readme_leads_with_evidence_first_positioning(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        first_half = readme[: readme.index("## Providers")]
        normalized = " ".join(first_half.split())

        self.assertIn("Mini Claude Code is an evidence-first local coding-agent runtime.", first_half)
        self.assertIn("prevents unverified code edits from being reported as successful", normalized)
        self.assertIn("## Core Runtime", first_half)
        self.assertIn("## Optional Extensions", first_half)
        self.assertIn("## Experimental Features", first_half)
        self.assertIn("py -3 -m mini_cc evidence", first_half)
        self.assertIn("Evidence Report", first_half)

    def test_experimental_features_are_not_core_claims(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        core = readme[readme.index("## Core Runtime") : readme.index("## Optional Extensions")]
        experimental = readme[readme.index("## Experimental Features") : readme.index("## Quick Start")]

        self.assertNotIn("MCP", core)
        self.assertNotIn("subagent", core.lower())
        self.assertIn("not required for the core loop", experimental)
        self.assertIn("not part of the main reliability claim", experimental)

    def test_evidence_first_runtime_doc_exists(self) -> None:
        doc = (ROOT / "docs" / "evidence_first_runtime.md").read_text(encoding="utf-8")

        self.assertIn("evidence-first local coding-agent runtime", doc)
        self.assertIn("Final Success Gates", doc)
        self.assertIn(".mini_cc/task-success/last-run.json", doc)


if __name__ == "__main__":
    unittest.main()
