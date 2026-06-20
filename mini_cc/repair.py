from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .project_index import ProjectIndex


@dataclass(frozen=True)
class FailureInfo:
    command: str
    failed_tests: list[str] = field(default_factory=list)
    error_files: list[str] = field(default_factory=list)
    error_symbols: list[str] = field(default_factory=list)
    expected_actual: list[str] = field(default_factory=list)
    exit_code: int | None = None
    excerpt: str = ""
    kind: str = "unknown"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairContext:
    failed_tests: list[str]
    error_files: list[str]
    error_symbols: list[str]
    suspected_source_files: list[str]
    exact_failure_excerpt: str
    suggested_next_reads: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def parse_failure_output(command: str, output: str) -> FailureInfo:
    text = output.replace("\r\n", "\n")
    failed_tests = _unique(
        re.findall(r"FAILED\s+([^\s]+)", text)
        + re.findall(r"^([A-Za-z_][\w.]+)\s+\(([^)]+)\)", text, flags=re.MULTILINE)
        + re.findall(r"(?:it|test|describe)\s+[\"']([^\"']+)[\"']", text)
    )
    error_files = _unique(
        match.replace("\\", "/")
        for match in re.findall(r'File "([^"]+\.(?:py|js|ts|tsx|jsx|go|rs|java))", line \d+', text)
        + re.findall(r"([A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|go|rs|java))[:(]\d+", text)
    )
    error_symbols = _unique(
        re.findall(r"NameError: name '([^']+)' is not defined", text)
        + re.findall(r"AttributeError: .* has no attribute '([^']+)'", text)
        + re.findall(r"cannot find name ['\"]?([A-Za-z_][\w$]*)", text, flags=re.IGNORECASE)
        + re.findall(r"not exported.*['\"]([A-Za-z_][\w$]*)", text, flags=re.IGNORECASE)
    )
    expected_actual = _unique(
        re.findall(r"(?:Expected|expected)[:\s]+(.{1,160})", text)
        + re.findall(r"(?:Actual|actual|Received|received)[:\s]+(.{1,160})", text)
        + re.findall(r"AssertionError: (.{1,200})", text)
    )
    exit_match = re.search(r"^exit_code=(-?\d+)\s*$", text, flags=re.MULTILINE)
    kind = _failure_kind(command, text)
    return FailureInfo(
        command=command,
        failed_tests=failed_tests[:20],
        error_files=error_files[:20],
        error_symbols=error_symbols[:20],
        expected_actual=expected_actual[:10],
        exit_code=int(exit_match.group(1)) if exit_match else None,
        excerpt=_excerpt(text),
        kind=kind,
    )


def build_repair_context(
    failure: FailureInfo,
    modified_files: list[str],
    planned_files: list[str],
    project_index: ProjectIndex,
) -> RepairContext:
    suspected = _unique([*modified_files, *planned_files])
    for path in failure.error_files:
        if path not in suspected:
            suspected.append(path)
        if _is_test(path):
            suspected.extend(project_index.related_sources_for(path))
        else:
            suspected.extend(project_index.related_tests_for(path))
    for symbol in failure.error_symbols:
        suspected.extend(record.path for record in project_index.find_symbol(symbol))
    suggested = _unique([*failure.error_files, *suspected, *modified_files, *planned_files])[:12]
    return RepairContext(
        failed_tests=failure.failed_tests,
        error_files=failure.error_files,
        error_symbols=failure.error_symbols,
        suspected_source_files=_unique([path for path in suspected if not _is_test(path)])[:12],
        exact_failure_excerpt=failure.excerpt,
        suggested_next_reads=suggested,
    )


def _failure_kind(command: str, text: str) -> str:
    lowered = f"{command}\n{text}".lower()
    if "traceback" in lowered:
        return "python-traceback"
    if "failed" in lowered and ("pytest" in lowered or "unittest" in lowered):
        return "python-test"
    if any(token in lowered for token in ["npm", "pnpm", "yarn", "vitest", "jest"]):
        return "js-ts-test"
    if any(token in lowered for token in ["type error", "tsc", "mypy", "ruff", "eslint"]):
        return "typecheck-lint"
    if any(token in lowered for token in ["build failed", "compilation failed", "error:"]):
        return "build"
    return "unknown"


def _excerpt(text: str, limit: int = 1800) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    interesting = [line for line in lines if re.search(r"(FAILED|ERROR|Traceback|AssertionError|Expected|Actual|Received|error:|File \")", line)]
    chosen = "\n".join(interesting[:30] or lines[-30:])
    return chosen[:limit] + (f"\n[truncated {len(chosen) - limit} chars]" if len(chosen) > limit else "")


def _is_test(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return lowered.startswith(("tests/", "test/")) or "/tests/" in lowered or "test_" in lowered or "_test." in lowered or ".test." in lowered or ".spec." in lowered


def _unique(items: list[str]) -> list[str]:
    rows: list[str] = []
    for item in items:
        if isinstance(item, tuple):
            item = ".".join(part for part in item if part)
        if item and item not in rows:
            rows.append(item)
    return rows
