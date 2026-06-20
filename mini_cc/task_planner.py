from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .project_index import ProjectIndex
from .repair import parse_failure_output
from .verification import best_verification_command


PATH_RE = re.compile(
    r"(?<![\w/\\.-])([A-Za-z0-9_.\-/\\]+\.(?:py|pyi|js|jsx|ts|tsx|json|md|toml|yaml|yml|ini|cfg|rs|go|java|xml|txt))"
)


@dataclass(frozen=True)
class TaskContext:
    prompt_paths: list[str] = field(default_factory=list)
    prompt_symbols: list[str] = field(default_factory=list)
    prompt_errors: list[str] = field(default_factory=list)
    prompt_commands: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    candidate_tests: list[str] = field(default_factory=list)
    verification_command: str | None = None
    project_summary: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MinimalEditPlan:
    task_type: str
    objective: str
    candidate_files: list[str]
    planned_files: list[str]
    verification_command: str | None
    risks: list[str]
    why_these_files: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def build_task_context(prompt: str, workspace: Path) -> TaskContext:
    index = ProjectIndex.build(workspace)
    prompt_paths = _extract_paths(prompt, workspace)
    prompt_symbols = _extract_symbols(prompt)
    prompt_commands = _extract_commands(prompt)
    failure = parse_failure_output(prompt_commands[-1] if prompt_commands else "", prompt)
    candidate_files: list[str] = []
    for path in prompt_paths:
        candidate_files.append(path)
    for symbol in prompt_symbols:
        candidate_files.extend(record.path for record in index.find_symbol(symbol))
    if not candidate_files:
        candidate_files.extend(record.path for record in index.find_relevant_files(prompt, max_results=10))
    candidate_files.extend(failure.error_files)
    candidate_files = _unique(candidate_files)[:12]
    candidate_tests: list[str] = []
    for path in candidate_files:
        if _is_test(path):
            candidate_tests.append(path)
        else:
            candidate_tests.extend(index.related_tests_for(path))
    verification = prompt_commands[-1] if prompt_commands else best_verification_command(workspace, candidate_files)
    return TaskContext(
        prompt_paths=prompt_paths,
        prompt_symbols=prompt_symbols,
        prompt_errors=failure.error_files + failure.error_symbols,
        prompt_commands=prompt_commands,
        candidate_files=candidate_files,
        candidate_tests=_unique(candidate_tests)[:10],
        verification_command=verification,
        project_summary=index.summarize_project(),
    )


def plan_minimal_edit(prompt: str, context: TaskContext) -> MinimalEditPlan:
    planned = context.prompt_paths or context.candidate_files[:4]
    task_type = _task_type(prompt)
    risks: list[str] = []
    lowered = prompt.lower()
    if "do not modify tests" in lowered or "don't modify tests" in lowered:
        planned = [path for path in planned if not _is_test(path)]
        risks.append("tests are constrained by prompt")
    if "only modify one file" in lowered and len(planned) > 1:
        planned = planned[:1]
        risks.append("prompt limits the edit to one file")
    if not planned:
        risks.append("no concrete target file found; exploration is required")
    if "new file forbidden" in lowered or "do not create" in lowered:
        risks.append("new files are forbidden")
    return MinimalEditPlan(
        task_type=task_type,
        objective=_objective(prompt),
        candidate_files=context.candidate_files,
        planned_files=planned,
        verification_command=context.verification_command,
        risks=risks,
        why_these_files="Prompt paths have priority; otherwise files were selected from symbols, failure output, and project index relevance.",
    )


def _extract_paths(prompt: str, workspace: Path) -> list[str]:
    root = workspace.expanduser().resolve()
    paths: list[str] = []
    for match in PATH_RE.finditer(prompt):
        raw = match.group(1).replace("\\", "/")
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        paths.append(Path(raw).as_posix())
    return _unique(paths)


def _extract_symbols(prompt: str) -> list[str]:
    symbols = re.findall(r"`([A-Za-z_][A-Za-z0-9_$]{2,})`", prompt)
    symbols += re.findall(r"\b(?:function|class|symbol|method)\s+([A-Za-z_][A-Za-z0-9_$]{2,})\b", prompt, flags=re.IGNORECASE)
    symbols += re.findall(r"\b([A-Za-z_][A-Za-z0-9_$]{2,})\s+(?:is undefined|not defined|missing export)\b", prompt, flags=re.IGNORECASE)
    return _unique(symbols)[:20]


def _extract_commands(prompt: str) -> list[str]:
    patterns = [
        r"\b(?:uv\s+run\s+pytest|python(?:3)?\s+-m\s+(?:pytest|unittest)|pytest|npm\s+(?:run\s+)?(?:test|build|typecheck|lint)|pnpm\s+(?:run\s+)?(?:test|build|typecheck|lint)|yarn\s+(?:test|build|typecheck|lint)|cargo\s+(?:test|check)|go\s+test|mvn\s+test)[^\n`]*",
    ]
    rows: list[str] = []
    for pattern in patterns:
        rows.extend(match.strip(" .") for match in re.findall(pattern, prompt, flags=re.IGNORECASE))
    return _unique(rows)


def _task_type(prompt: str) -> str:
    lowered = prompt.lower()
    if any(token in lowered for token in ["readme", "docs", "markdown", "changelog"]):
        return "documentation"
    if any(token in lowered for token in ["pyproject", "package.json", "tsconfig", "config", "build"]):
        return "config/build"
    if any(token in lowered for token in ["test", "failing", "failure", "bug", "error"]):
        return "bug_fix"
    if any(token in lowered for token in ["add", "implement", "new"]):
        return "feature_addition"
    return "code_modification"


def _objective(prompt: str) -> str:
    first = " ".join(prompt.strip().split())
    return first[:240] if first else "Complete the requested local code change."


def _is_test(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return lowered.startswith(("tests/", "test/")) or "/tests/" in lowered or Path(lowered).name.startswith("test_") or "_test." in lowered or ".test." in lowered or ".spec." in lowered


def _unique(items: list[str]) -> list[str]:
    rows: list[str] = []
    for item in items:
        if item and item not in rows:
            rows.append(item)
    return rows
