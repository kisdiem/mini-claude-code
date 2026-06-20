from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime_types import VerificationResult
from .verification import best_verification_command


COMMAND_TYPES = {"test", "lint", "typecheck", "build", "docs/check", "runtime-evidence", "fake", "unknown"}
REAL_COMMAND_TYPES = {"test", "lint", "typecheck", "build", "docs/check"}


@dataclass(frozen=True)
class VerificationRule:
    name: str
    command_type: str
    patterns: list[str]
    expected_file_suffixes: list[str] = field(default_factory=list)
    project_markers: list[str] = field(default_factory=list)
    confidence: float = 0.5
    description: str = ""

    def matches(self, command: str) -> bool:
        return any(re.search(pattern, command) for pattern in self.patterns)


class VerificationRegistry:
    def __init__(self, rules: list[VerificationRule] | None = None) -> None:
        self.rules = list(rules or default_rules())

    def classify(self, command: str) -> VerificationRule | None:
        normalized = normalize_command(command)
        if not normalized:
            return None
        for rule in self.rules:
            if rule.matches(normalized):
                return rule
        return None

    def is_real(self, command: str) -> bool:
        rule = self.classify(command)
        return bool(rule and rule.command_type in REAL_COMMAND_TYPES)

    def add_rule(self, rule: VerificationRule) -> None:
        self.rules.append(rule)


class VerificationPolicy:
    """Classify verification commands and evaluate their output."""

    def __init__(self, registry: VerificationRegistry | None = None) -> None:
        self.registry = registry or VerificationRegistry()

    def classify(self, command: str) -> VerificationRule | None:
        return self.registry.classify(command)

    def classify_command(self, command: str) -> str:
        rule = self.classify(command)
        return rule.command_type if rule else "unknown"

    def is_real_verification(self, command: str) -> bool:
        return self.registry.is_real(command)

    def evaluate_command(
        self,
        command: str,
        output: str,
        modified_files: list[str] | None = None,
        task_contract: Any = None,
        workspace: Path | None = None,
    ) -> VerificationResult:
        normalized = normalize_command(command)
        rule = self.classify(command)
        command_type = rule.command_type if rule else "unknown"
        parser = parser_for(command_type, normalized)
        parsed = parser(command, output)
        exit_code = parsed["exit_code"]
        warnings: list[str] = []
        blockers: list[str] = []
        modified = [str(path).replace("\\", "/") for path in (modified_files or [])]
        expected = expected_types_for(task_contract, modified)
        real = command_type in REAL_COMMAND_TYPES
        relevant = real and command_type in expected
        relevance_reason = "verification command is relevant"
        if not real:
            blockers.append("command is not accepted as real verification")
            relevance_reason = "command is fake, runtime evidence, or unknown"
        elif not relevant:
            relevance_reason = f"{command_type} verification does not match expected types: {', '.join(sorted(expected))}"
            blockers.append(relevance_reason)

        coverage = coverage_for(normalized, modified)
        confidence = rule.confidence if rule else 0.0
        if real and modified and coverage == "project":
            warnings.append("verification is broad; ensure output shows meaningful checks")
            confidence = min(confidence, 0.75)
        elif real and coverage == "targeted":
            confidence = min(1.0, confidence + 0.12)

        if workspace is not None and rule is not None and not has_project_marker(workspace, rule):
            warnings.append("project markers for this verification command were not detected")
            confidence = min(confidence, 0.68)

        if parsed["no_checks"]:
            blockers.append("verification output indicates no meaningful checks")
        if parsed["empty_success"]:
            blockers.append("verification output is empty or too weak to prove checks ran")
        if exit_code not in (0, None):
            blockers.append("verification command failed")

        meaningful = bool(real and exit_code == 0 and parsed["has_useful_signal"] and not parsed["no_checks"] and not parsed["empty_success"])
        meaningful_reason = str(parsed["meaningful_reason"])
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
            coverage=coverage,
            confidence=confidence,
            meaningful_reason=meaningful_reason,
            relevance_reason=relevance_reason,
            parser_name=str(parsed["parser_name"]),
        )

    def suggest_command(self, workspace: Path, explicit: str | None = None) -> str | None:
        return best_verification_command(workspace, explicit=explicit)


def default_rules() -> list[VerificationRule]:
    return [
        VerificationRule("fake-inspection", "fake", [r"^(?:echo|cat|type|ls|dir|pwd|find|grep)(?:\s|$)", r"^git\s+(?:status|diff)(?:\s|$)"], confidence=1.0),
        VerificationRule("runtime-evidence", "runtime-evidence", [r"^(?:context_snapshot|list_files|read_file|search_text|git_status|git_diff)$"], confidence=1.0),
        VerificationRule("pytest", "test", [r"^(?:uv\s+run\s+)?pytest(?:\s|$)", r"^(?:python|python3|py)(?:\s+-3)?\s+-m\s+pytest(?:\s|$)"], [".py", ".pyi"], ["pytest.ini", "pyproject.toml", "tests"], 0.9, "pytest test command"),
        VerificationRule("unittest", "test", [r"^(?:python|python3|py)(?:\s+-3)?\s+-m\s+unittest(?:\s|$)", r"^(?:python|python3|py)(?:\s+-3)?\s+manage\.py\s+test(?:\s|$)"], [".py", ".pyi"], ["tests"], 0.86, "Python unittest or Django test command"),
        VerificationRule("python-test-runners", "test", [r"^tox(?:\s|$)", r"^nox(?:\s|$)", r"^hatch\s+test(?:\s|$)"], [".py", ".pyi"], ["tox.ini", "noxfile.py", "pyproject.toml"], 0.82),
        VerificationRule("node-test", "test", [r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+test(?:\s|$)", r"^bun\s+test(?:\s|$)"], [".js", ".jsx", ".ts", ".tsx"], ["package.json"], 0.84),
        VerificationRule("python-lint", "lint", [r"^ruff(?:\s+check)?(?:\s|$)"], [".py", ".pyi"], ["pyproject.toml", "ruff.toml"], 0.82),
        VerificationRule("node-lint", "lint", [r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+lint(?:\s|$)", r"^eslint(?:\s|$)"], [".js", ".jsx", ".ts", ".tsx"], ["package.json"], 0.78),
        VerificationRule("python-typecheck", "typecheck", [r"^mypy(?:\s|$)"], [".py", ".pyi"], ["pyproject.toml", "mypy.ini"], 0.78),
        VerificationRule("ts-typecheck", "typecheck", [r"^(?:npx\s+)?tsc(?:\s|$)", r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+(?:typecheck|check)(?:\s|$)", r"^yarn\s+typecheck(?:\s|$)"], [".ts", ".tsx", ".js", ".jsx"], ["package.json", "tsconfig.json"], 0.8),
        VerificationRule("node-build", "build", [r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+build(?:\s|$)"], [".js", ".jsx", ".ts", ".tsx"], ["package.json"], 0.74),
        VerificationRule("make-check", "build", [r"^make\s+check(?:\s|$)"], [], ["Makefile"], 0.72),
        VerificationRule("rust", "test", [r"^cargo\s+test(?:\s|$)"], [".rs"], ["Cargo.toml"], 0.86),
        VerificationRule("rust-check", "typecheck", [r"^cargo\s+check(?:\s|$)"], [".rs"], ["Cargo.toml"], 0.76),
        VerificationRule("go", "test", [r"^go\s+test(?:\s|$)"], [".go"], ["go.mod"], 0.86),
        VerificationRule("java-maven", "test", [r"^mvn\s+test(?:\s|$)"], [".java"], ["pom.xml"], 0.82),
        VerificationRule("java-gradle", "test", [r"^(?:gradle|\.\/gradlew|\.\\gradlew)\s+test(?:\s|$)"], [".java", ".kt"], ["build.gradle", "gradlew", "gradlew.bat"], 0.8),
        VerificationRule("java-build", "build", [r"^(?:gradle|\.\/gradlew|\.\\gradlew)\s+build(?:\s|$)", r"^mvn\s+package(?:\s|$)"], [".java", ".kt"], ["build.gradle", "pom.xml"], 0.72),
        VerificationRule("docs", "docs/check", [r"^markdownlint(?:\s|$)", r"^mkdocs(?:\s|$)", r"^sphinx-build(?:\s|$)"], [".md", ".rst"], ["mkdocs.yml", "docs"], 0.78),
        VerificationRule("make-test", "test", [r"^make\s+test(?:\s|$)"], [], ["Makefile"], 0.78),
    ]


def parser_for(command_type: str, normalized_command: str):
    if "pytest" in normalized_command or "unittest" in normalized_command:
        return parse_pytest_unittest_output
    if any(token in normalized_command for token in ["npm", "pnpm", "yarn", "bun"]):
        return parse_node_output
    if command_type == "typecheck":
        return parse_typecheck_output
    if command_type == "build":
        return parse_build_output
    return parse_generic_output


def parse_generic_output(command: str, output: str) -> dict[str, Any]:
    exit_code = parse_exit_code(output)
    lowered = output.lower()
    no_checks = any(re.search(pattern, lowered) for pattern in NO_CHECK_PATTERNS)
    useful = any(re.search(pattern, lowered) for pattern in USEFUL_PASS_PATTERNS)
    empty_success = exit_code == 0 and not useful and len(strip_shell_wrappers(output)) < 12
    return {
        "parser_name": "generic",
        "exit_code": exit_code,
        "no_checks": no_checks,
        "has_useful_signal": useful or (exit_code == 0 and not empty_success and not no_checks),
        "empty_success": empty_success,
        "meaningful_reason": meaningful_reason(no_checks, empty_success, useful),
    }


def parse_pytest_unittest_output(command: str, output: str) -> dict[str, Any]:
    parsed = parse_generic_output(command, output)
    lowered = output.lower()
    ran_tests = bool(re.search(r"\b(?:ran\s+[1-9]\d*\s+tests?|[1-9]\d*\s+passed|[1-9]\d*\s+failed)\b", lowered))
    ok = bool(re.search(r"(^|\n)\s*ok\s*(\n|$)", lowered))
    parsed.update(
        {
            "parser_name": "pytest_unittest",
            "has_useful_signal": bool(ran_tests or ok or parsed["has_useful_signal"]),
            "empty_success": bool(parsed["exit_code"] == 0 and not (ran_tests or ok or parsed["has_useful_signal"])),
            "meaningful_reason": "test output reports executed tests" if ran_tests or ok else parsed["meaningful_reason"],
        }
    )
    return parsed


def parse_node_output(command: str, output: str) -> dict[str, Any]:
    parsed = parse_generic_output(command, output)
    lowered = output.lower()
    node_ok = bool(re.search(r"\b(?:tests?\s+passed|passing|passed|done|success)\b", lowered))
    parsed.update(
        {
            "parser_name": "node",
            "has_useful_signal": bool(node_ok or parsed["has_useful_signal"]),
            "meaningful_reason": "node output reports successful checks" if node_ok else parsed["meaningful_reason"],
        }
    )
    return parsed


def parse_typecheck_output(command: str, output: str) -> dict[str, Any]:
    parsed = parse_generic_output(command, output)
    lowered = output.lower()
    type_ok = bool(re.search(r"\b(?:found\s+0\s+errors?|success|no\s+issues\s+found|all\s+checks\s+passed)\b", lowered))
    parsed.update(
        {
            "parser_name": "typecheck",
            "has_useful_signal": bool(type_ok or parsed["has_useful_signal"]),
            "meaningful_reason": "typecheck output reports no errors" if type_ok else parsed["meaningful_reason"],
        }
    )
    return parsed


def parse_build_output(command: str, output: str) -> dict[str, Any]:
    parsed = parse_generic_output(command, output)
    lowered = output.lower()
    build_ok = bool(re.search(r"\b(?:built|build\s+successful|success|done|finished)\b", lowered))
    parsed.update(
        {
            "parser_name": "build",
            "has_useful_signal": bool(build_ok or parsed["has_useful_signal"]),
            "meaningful_reason": "build output reports success" if build_ok else parsed["meaningful_reason"],
        }
    )
    return parsed


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

USEFUL_PASS_PATTERNS = [
    r"\bran\s+[1-9]\d*\s+tests?\b",
    r"\b[1-9]\d*\s+passed\b",
    r"\bpassed\b",
    r"(^|\n)\s*ok\s*(\n|$)",
    r"\bsuccess(?:ful)?\b",
    r"\bdone\b",
    r"\bfound\s+0\s+errors?\b",
    r"\bno\s+issues\s+found\b",
    r"\ball\s+checks\s+passed\b",
]


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


def coverage_for(normalized_command: str, modified_files: list[str]) -> str:
    if any(path and path.lower() in normalized_command for path in modified_files):
        return "targeted"
    if modified_files:
        return "project"
    return "unknown"


def has_project_marker(workspace: Path, rule: VerificationRule) -> bool:
    if not rule.project_markers:
        return True
    return any((workspace / marker).exists() for marker in rule.project_markers)


def strip_shell_wrappers(output: str) -> str:
    lines = []
    for line in output.splitlines():
        if re.match(r"^(exit_code=|stdout:|stderr:)\s*$", line.strip()):
            continue
        lines.append(line.strip())
    return "\n".join(line for line in lines if line).strip()


def meaningful_reason(no_checks: bool, empty_success: bool, useful: bool) -> str:
    if no_checks:
        return "output reports zero, skipped, missing, or unconfigured checks"
    if empty_success:
        return "command succeeded but output does not show checks ran"
    if useful:
        return "output contains a recognizable successful-check signal"
    return "output did not include a strong success marker but was non-empty"


def summarize_failure(command: str, exit_code: int | None, output: str) -> str:
    excerpt = output.strip().replace("\r\n", "\n")
    if len(excerpt) > 1000:
        excerpt = excerpt[:1000] + "\n[truncated]"
    return f"{command} exited with {exit_code}: {excerpt}"


def _is_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".rst")) or lowered.startswith("docs/") or "/docs/" in lowered


def _unique(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        if item and item not in values:
            values.append(item)
    return values
