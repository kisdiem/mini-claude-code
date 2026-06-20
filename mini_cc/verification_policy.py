from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .runtime_types import VerificationResult
from .verification import best_verification_command


FAKE_COMMANDS = {
    "echo",
    "cat",
    "type",
    "ls",
    "dir",
    "pwd",
    "find",
    "grep",
    "git status",
    "git diff",
}

RUNTIME_EVIDENCE_COMMANDS = {
    "context_snapshot",
    "list_files",
    "read_file",
    "search_text",
    "git_status",
    "git_diff",
}

NO_CHECK_PATTERNS = [
    r"\bcollected\s+0\s+items\b",
    r"\bno\s+tests?\s+ran\b",
    r"\bran\s+0\s+tests?\b",
    r"\b0\s+tests?\s+(?:run|ran|collected|passed|failed|skipped|executed)\b",
    r"\bempty\s+suite\b",
    r"\bno\s+tests?\s+found\b",
    r"\bpasswithnotests\b",
    r"\bno\s+test\s+files?\s+found\b",
    r"\bno\s+matching\s+files\b",
    r"\bnot\s+configured\b",
    r"\bmissing\s+script\b",
    r"\bcommand\s+not\s+found\b",
    r"\bnot recognized as (?:an internal|a cmdlet|a command)\b",
    r"\bskipped\s+because\b",
    r"\bnothing\s+to\s+(?:check|test|lint|build)\b",
]


class VerificationPolicy:
    """Classify verification commands and evaluate their output."""

    TEST_PATTERNS = [
        r"^(?:uv\s+run\s+)?pytest(?:\s|$)",
        r"^(?:python|python3|py)(?:\s+-3)?\s+-m\s+pytest(?:\s|$)",
        r"^(?:python|python3|py)(?:\s+-3)?\s+-m\s+unittest(?:\s|$)",
        r"^(?:python|python3|py)(?:\s+-3)?\s+manage\.py\s+test(?:\s|$)",
        r"^tox(?:\s|$)",
        r"^nox(?:\s|$)",
        r"^hatch\s+test(?:\s|$)",
        r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+test(?:\s|$)",
        r"^bun\s+test(?:\s|$)",
        r"^cargo\s+test(?:\s|$)",
        r"^go\s+test(?:\s|$)",
        r"^mvn\s+test(?:\s|$)",
        r"^(?:gradle|\.\/gradlew|\.\\gradlew)\s+test(?:\s|$)",
        r"^make\s+test(?:\s|$)",
    ]
    LINT_PATTERNS = [
        r"^ruff(?:\s+check)?(?:\s|$)",
        r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+lint(?:\s|$)",
        r"^eslint(?:\s|$)",
        r"^markdownlint(?:\s|$)",
    ]
    TYPECHECK_PATTERNS = [
        r"^mypy(?:\s|$)",
        r"^(?:npx\s+)?tsc(?:\s|$)",
        r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+(?:typecheck|check)(?:\s|$)",
        r"^cargo\s+check(?:\s|$)",
    ]
    BUILD_PATTERNS = [
        r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+build(?:\s|$)",
        r"^make\s+check(?:\s|$)",
        r"^mvn\s+package(?:\s|$)",
        r"^(?:gradle|\.\/gradlew|\.\\gradlew)\s+build(?:\s|$)",
    ]
    DOCS_PATTERNS = [
        r"^mkdocs(?:\s|$)",
        r"^sphinx-build(?:\s|$)",
        r"^markdownlint(?:\s|$)",
    ]

    def classify_command(self, command: str) -> str:
        normalized = normalize_command(command)
        if not normalized:
            return "unknown"
        if self._matches_fake(normalized):
            return "fake"
        if normalized in RUNTIME_EVIDENCE_COMMANDS:
            return "runtime-evidence"
        for command_type, patterns in [
            ("test", self.TEST_PATTERNS),
            ("docs/check", self.DOCS_PATTERNS),
            ("lint", self.LINT_PATTERNS),
            ("typecheck", self.TYPECHECK_PATTERNS),
            ("build", self.BUILD_PATTERNS),
        ]:
            if any(re.search(pattern, normalized) for pattern in patterns):
                return command_type
        return "unknown"

    def is_real_verification(self, command: str) -> bool:
        return self.classify_command(command) in {"test", "lint", "typecheck", "build", "docs/check"}

    def evaluate_command(
        self,
        command: str,
        output: str,
        modified_files: list[str] | None = None,
        task_contract: Any = None,
        workspace: Path | None = None,
    ) -> VerificationResult:
        command_type = self.classify_command(command)
        exit_code = parse_exit_code(output)
        real = command_type in {"test", "lint", "typecheck", "build", "docs/check"}
        warnings: list[str] = []
        blockers: list[str] = []
        modified = [str(path).replace("\\", "/") for path in (modified_files or [])]
        expected = expected_types_for(task_contract, modified)
        relevant = real and command_type in expected
        if real and not relevant:
            blockers.append(f"{command_type} verification does not match expected types: {', '.join(sorted(expected))}")
        if not real:
            blockers.append("command is not accepted as real verification")
        elif modified and not _targets_modified_file(command, modified):
            warnings.append("verification is broad; ensure output shows meaningful checks")
        if workspace is not None and command_type == "test" and not _project_has_test_signal(workspace, command):
            warnings.append("no local test configuration was detected for this test command")
        lowered = output.lower()
        no_checks = any(re.search(pattern, lowered) for pattern in NO_CHECK_PATTERNS)
        meaningful = real and not no_checks and exit_code == 0
        if no_checks:
            blockers.append("verification output indicates no meaningful checks")
        if exit_code not in (0, None):
            blockers.append("verification command failed")
        passed = bool(real and relevant and meaningful and exit_code == 0 and not blockers)
        return VerificationResult(
            command=command,
            command_type=command_type,
            exit_code=exit_code,
            passed=passed,
            is_real_verification=real,
            is_relevant=relevant,
            has_meaningful_checks=meaningful,
            warnings=_unique(warnings),
            blockers=_unique(blockers),
            failure_summary=summarize_failure(command, exit_code, output) if blockers else "",
        )

    def suggest_command(self, workspace: Path, explicit: str | None = None) -> str | None:
        return best_verification_command(workspace, explicit=explicit)

    def _matches_fake(self, normalized: str) -> bool:
        return any(normalized == item or normalized.startswith(item + " ") for item in FAKE_COMMANDS)


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", str(command).strip().lower())


def parse_exit_code(output: str) -> int | None:
    match = re.search(r"^exit_code=(-?\d+)\s*$", output, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def expected_types_for(task_contract: Any, modified_files: list[str]) -> set[str]:
    task_type = str(getattr(task_contract, "task_type", "") or getattr(task_contract, "primary_intent", "") or "")
    if task_type == "documentation":
        return {"docs/check", "lint", "build", "test"}
    if task_type in {"config/build", "config_build"}:
        return {"build", "test", "lint", "typecheck"}
    if any(path.endswith((".py", ".pyi")) for path in modified_files):
        return {"test", "lint", "typecheck"}
    if any(path.endswith((".js", ".jsx", ".ts", ".tsx")) for path in modified_files):
        return {"test", "lint", "typecheck", "build"}
    if any(path.endswith(".rs") for path in modified_files):
        return {"test", "typecheck"}
    if any(path.endswith(".go") for path in modified_files):
        return {"test"}
    if any(path.endswith((".java", ".kt")) for path in modified_files):
        return {"test", "build"}
    if any(_is_doc_path(path) for path in modified_files):
        return {"docs/check", "lint", "build", "test"}
    return {"test", "lint", "typecheck", "build", "docs/check"}


def summarize_failure(command: str, exit_code: int | None, output: str) -> str:
    excerpt = output.strip().replace("\r\n", "\n")
    if len(excerpt) > 1000:
        excerpt = excerpt[:1000] + "\n[truncated]"
    return f"{command} exited with {exit_code}: {excerpt}"


def _targets_modified_file(command: str, modified_files: list[str]) -> bool:
    normalized = normalize_command(command)
    return any(path.lower() in normalized for path in modified_files)


def _project_has_test_signal(workspace: Path, command: str) -> bool:
    normalized = normalize_command(command)
    if "pytest" in normalized:
        return (workspace / "pytest.ini").exists() or (workspace / "pyproject.toml").exists() or (workspace / "tests").exists()
    if "unittest" in normalized:
        return (workspace / "tests").exists() or any(workspace.glob("test*.py"))
    if any(token in normalized for token in ["npm", "pnpm", "yarn", "bun"]):
        return (workspace / "package.json").exists()
    return True


def _is_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".rst")) or lowered.startswith("docs/") or "/docs/" in lowered


def _unique(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        if item and item not in values:
            values.append(item)
    return values
