from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .coding_loop import is_verification_command, parse_exit_code
from .tools import ToolResult


PATH_RE = re.compile(
    r"(?<![\w/\\.-])([A-Za-z0-9_.\-/\\]+"
    r"\.(?:py|pyi|js|jsx|ts|tsx|json|md|txt|toml|yaml|yml|ini|cfg|rs|go|java|c|cc|cpp|h|hpp|cs|html|css|xml|sh|ps1|bat))"
)
BACKTICK_RE = re.compile(r"`([^`]+)`")
SYMBOL_HINT_RE = re.compile(
    r"\b(?:function|func|class|method|def|variable|command|cli)\s+([A-Za-z_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
CHINESE_SYMBOL_RE = re.compile(r"(?:函数|方法|类|变量|命令)\s*([A-Za-z_][A-Za-z0-9_.-]*)")


@dataclass
class TaskContract:
    raw_prompt: str
    task_type: str
    objective: str
    requested_operations: list[str] = field(default_factory=list)
    explicit_paths: list[str] = field(default_factory=list)
    explicit_symbols: list[str] = field(default_factory=list)
    acceptance_keywords: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    allowed_new_files: bool = False
    only_paths: list[str] = field(default_factory=list)
    forbid_tests: bool = False
    forbid_docs: bool = False
    forbid_new_files: bool = False
    preserve_api: bool = False
    avoid_refactor: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    kind: str
    source: str
    summary: str
    paths: list[str]
    confidence: float = 1.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationEvidence:
    command: str
    exit_code: int | None
    is_real_verification: bool
    is_relevant: bool
    relevance_reason: str
    has_meaningful_checks: bool
    meaningful_checks_reason: str
    failure_summary: str = ""
    command_type: str = "unknown"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticTaskDecision:
    allow: bool
    reason: str
    instruction: str = ""
    score: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def extract_task_contract(prompt: str) -> TaskContract:
    raw_prompt = prompt.strip()
    lowered = raw_prompt.lower()
    explicit_paths = unique(normalize_path(match.group(1)) for match in PATH_RE.finditer(raw_prompt))
    explicit_symbols = extract_symbols(raw_prompt, explicit_paths)
    task_type = infer_task_type(lowered, explicit_paths)
    requested_operations = extract_requested_operations(lowered)
    constraints = extract_constraints(raw_prompt)
    only_paths = extract_only_paths(raw_prompt)
    forbid_tests = any(token in lowered for token in ["do not modify tests", "don't modify tests", "不要修改测试", "不要改测试"])
    forbid_docs = any(token in lowered for token in ["do not modify docs", "don't modify docs", "不要修改文档", "不要改文档"])
    forbid_new_files = any(token in lowered for token in ["do not add files", "no new files", "不要新增文件", "不要新建文件"])
    allowed_new_files = any(token in lowered for token in ["add", "create", "new file", "新增", "添加", "创建"]) and not forbid_new_files
    preserve_api = any(token in lowered for token in ["keep api", "preserve api", "do not change api", "不要改变 api", "保持兼容"])
    avoid_refactor = any(token in lowered for token in ["do not refactor", "don't refactor", "不要重构", "不要大改"])
    acceptance_keywords = extract_acceptance_keywords(raw_prompt, explicit_paths, explicit_symbols)
    return TaskContract(
        raw_prompt=raw_prompt,
        task_type=task_type,
        objective=raw_prompt[:500],
        requested_operations=requested_operations,
        explicit_paths=explicit_paths,
        explicit_symbols=explicit_symbols,
        acceptance_keywords=acceptance_keywords,
        constraints=constraints,
        allowed_new_files=allowed_new_files,
        only_paths=only_paths,
        forbid_tests=forbid_tests,
        forbid_docs=forbid_docs,
        forbid_new_files=forbid_new_files,
        preserve_api=preserve_api,
        avoid_refactor=avoid_refactor,
    )


def validate_plan(contract: TaskContract, task_state: Any, assistant_text: str) -> SemanticTaskDecision:
    planned_files = list(getattr(task_state, "planned_files", []) or [])
    candidate_files = set(getattr(task_state, "candidate_files", []) or [])
    read_files = set(getattr(task_state, "read_files", []) or [])
    text = assistant_text.lower()
    checks: dict[str, bool] = {
        "has_planned_files": bool(planned_files),
        "files_grounded": True,
        "mentions_verification": mentions_verification(text),
        "task_type_compatible": True,
        "constraints_respected": True,
    }
    if not planned_files:
        return block("plan missing planned_files", "Add a minimal plan with `planned_files: ...` and verification command.", checks)

    allowed_evidence = set(contract.explicit_paths) | candidate_files | read_files
    ungrounded = [path for path in planned_files if path not in allowed_evidence and not (contract.allowed_new_files and not path_exists(task_state, path))]
    checks["files_grounded"] = not ungrounded
    if ungrounded:
        return block(
            "planned files lack exploration evidence",
            "Re-localize or explain/create only task-required new files before planning: " + ", ".join(ungrounded),
            checks,
        )

    constraint_error = constraint_violation(contract, planned_files)
    checks["constraints_respected"] = constraint_error == ""
    if constraint_error:
        return block(constraint_error, "Revise the plan so planned_files respect the user's constraints.", checks)
    if contract.forbid_new_files:
        new_files = [path for path in planned_files if path not in allowed_evidence]
        checks["constraints_respected"] = not new_files
        if new_files:
            return block(
                "planned file violates no-new-files constraint: " + ", ".join(new_files),
                "Revise the plan to use existing explored/read files only.",
                checks,
            )

    compatible, reason = task_type_file_compatibility(contract, planned_files)
    checks["task_type_compatible"] = compatible
    if not compatible:
        return block(reason, "Revise planned_files so they match the requested task type and objective.", checks)

    if not checks["mentions_verification"]:
        return block("plan missing verification method", "Add the concrete test/lint/typecheck/build command you will run after editing.", checks)

    return SemanticTaskDecision(True, "plan is semantically relevant", score=1.0, checks=checks)


def validate_edit(contract: TaskContract, task_state: Any, modified_files: list[str], diff_summary: str = "") -> SemanticTaskDecision:
    planned_files = set(getattr(task_state, "planned_files", []) or [])
    modified = unique(normalize_path(path) for path in modified_files)
    checks: dict[str, bool] = {
        "modified_files_present": bool(modified),
        "within_plan": True,
        "constraints_respected": True,
        "task_type_compatible": True,
        "not_over_edit": True,
    }
    if not modified:
        return block("edit target unknown", "The edit result did not identify modified files. Re-run with a clear patch/edit target.", checks)
    outside_plan = [path for path in modified if path not in planned_files and not (contract.allowed_new_files and not path_exists(task_state, path))]
    checks["within_plan"] = not outside_plan
    if outside_plan:
        return block("modified file outside planned_files", "Restrict edits to planned_files or revise the plan first: " + ", ".join(outside_plan), checks)

    constraint_error = constraint_violation(contract, modified)
    checks["constraints_respected"] = constraint_error == ""
    if constraint_error:
        return block(constraint_error, "Undo or revise edits that violate the user's constraints.", checks)
    if contract.forbid_new_files:
        new_files = [path for path in modified if not path_exists(task_state, path)]
        checks["constraints_respected"] = not new_files
        if new_files:
            return block(
                "modified file violates no-new-files constraint: " + ", ".join(new_files),
                "Undo or revise edits that create new files.",
                checks,
            )

    compatible, reason = task_type_file_compatibility(contract, modified)
    checks["task_type_compatible"] = compatible
    if not compatible:
        return block(reason, "The modified files do not match the requested task. Re-localize and edit relevant files.", checks)

    over_edit = is_over_edit(diff_summary, modified, planned_files)
    checks["not_over_edit"] = not over_edit
    if over_edit:
        return SemanticTaskDecision(
            True,
            "edit has over-edit risk",
            instruction="Review the diff and justify why the changed scope is necessary; otherwise reduce the patch.",
            score=0.65,
            checks=checks,
        )
    return SemanticTaskDecision(True, "edit is semantically relevant", score=1.0, checks=checks)


def validate_verification_command(contract: TaskContract, task_state: Any, command: str, modified_files: list[str], workspace: Path | None = None) -> VerificationEvidence:
    normalized = normalize_command(command)
    command_type = classify_verification_command(command)
    real = is_verification_command(command)
    if not real:
        return VerificationEvidence(
            command=command,
            exit_code=None,
            is_real_verification=False,
            is_relevant=False,
            relevance_reason="command is not a real test/lint/typecheck/build verification command",
            has_meaningful_checks=False,
            meaningful_checks_reason="command was not accepted as verification",
            command_type=command_type,
        )
    modified = [normalize_path(path) for path in modified_files]
    relevant = True
    reason = "broad verification is acceptable for modified files"

    if contract.task_type == "documentation":
        relevant = command_type in {"docs/check", "lint", "build", "test"}
        reason = "documentation tasks should use docs/build/lint checks or a project test that validates generated docs"
    elif contract.task_type == "config/build":
        relevant = command_type in {"build", "test", "lint", "typecheck"}
        reason = "config/build tasks should use build/test/lint/typecheck checks"
    elif any(path.endswith((".py", ".pyi")) for path in modified):
        relevant = command_type in {"test", "lint", "typecheck"}
        reason = "Python edits should be verified by tests, lint, or typecheck"
    elif any(path.endswith((".js", ".jsx", ".ts", ".tsx")) for path in modified):
        relevant = command_type in {"test", "lint", "typecheck", "build"}
        reason = "Node/TS edits should be verified by tests, lint, typecheck, or build"
    elif any(path.endswith((".rs",)) for path in modified):
        relevant = normalized.startswith("cargo test") or normalized.startswith("cargo check")
        reason = "Rust edits should use cargo test/check"
    elif any(path.endswith((".go",)) for path in modified):
        relevant = normalized.startswith("go test")
        reason = "Go edits should use go test"

    prompt_commands = [symbol for symbol in contract.explicit_symbols if " " in symbol or symbol.endswith((".exe", ".bat", ".ps1"))]
    if prompt_commands and not any(symbol.lower() in normalized for symbol in prompt_commands):
        reason = "verification did not mention command-like symbol from prompt; broad tests may still be acceptable"

    if workspace is not None and command_type == "test" and not project_has_test_signal(workspace, command):
        reason = "test command is broad but no local test signal was detected; output quality must prove meaningful checks"

    return VerificationEvidence(
        command=command,
        exit_code=None,
        is_real_verification=True,
        is_relevant=relevant,
        relevance_reason=reason,
        has_meaningful_checks=True,
        meaningful_checks_reason="meaningfulness depends on command output",
        command_type=command_type,
    )


def validate_verification_output(command: str, output: str, *, prior: VerificationEvidence | None = None) -> VerificationEvidence:
    exit_code = parse_exit_code(output)
    command_type = prior.command_type if prior is not None else classify_verification_command(command)
    real = prior.is_real_verification if prior is not None else is_verification_command(command)
    relevant = prior.is_relevant if prior is not None else real
    relevance_reason = prior.relevance_reason if prior is not None else ("real verification command" if real else "not a real verification command")
    lowered = output.lower()
    no_checks_patterns = [
        r"\bcollected\s+0\s+items\b",
        r"\bno\s+tests\s+ran\b",
        r"\bran\s+0\s+tests\b",
        r"\b0\s+tests\s+(?:run|ran|collected|passed|failed|skipped)\b",
        r"\bempty\s+suite\b",
        r"\bno\s+tests\s+found\b",
        r"\bpasswithnotests\b",
        r"\bno\s+test\s+files\s+found\b",
        r"\bno\s+matching\s+files\b",
        r"\bnot\s+configured\b",
        r"\bmissing\s+script\b",
        r"\bcommand\s+not\s+found\b",
    ]
    has_no_checks = any(re.search(pattern, lowered) for pattern in no_checks_patterns)
    if exit_code != 0:
        return VerificationEvidence(
            command=command,
            exit_code=exit_code,
            is_real_verification=real,
            is_relevant=relevant,
            relevance_reason=relevance_reason,
            has_meaningful_checks=False,
            meaningful_checks_reason="verification command failed",
            failure_summary=summarize_output_failure(command, exit_code, output),
            command_type=command_type,
        )
    if has_no_checks:
        return VerificationEvidence(
            command=command,
            exit_code=exit_code,
            is_real_verification=real,
            is_relevant=relevant,
            relevance_reason=relevance_reason,
            has_meaningful_checks=False,
            meaningful_checks_reason="command output indicates zero, skipped, missing, or unconfigured checks",
            command_type=command_type,
        )
    return VerificationEvidence(
        command=command,
        exit_code=exit_code,
        is_real_verification=real,
        is_relevant=relevant,
        relevance_reason=relevance_reason,
        has_meaningful_checks=bool(real and relevant and exit_code == 0),
        meaningful_checks_reason="verification output indicates successful checks",
        command_type=command_type,
    )


def classify_verification_command(command: str) -> str:
    normalized = normalize_command(command)
    if any(token in normalized for token in ["pytest", "unittest", "npm test", "pnpm test", "yarn test", "cargo test", "go test", "mvn test", "gradle test", "make test"]):
        return "test"
    if any(token in normalized for token in ["ruff", "lint", "eslint"]):
        return "lint"
    if any(token in normalized for token in ["mypy", "tsc", "cargo check"]):
        return "typecheck"
    if any(token in normalized for token in ["build", "make check"]):
        return "build"
    if any(token in normalized for token in ["markdownlint", "mkdocs", "sphinx"]):
        return "docs/check"
    if any(token in normalized for token in ["python ", "node ", "py "]):
        return "run/smoke"
    return "unknown"


def infer_task_type(lowered_prompt: str, explicit_paths: list[str]) -> str:
    if any(token in lowered_prompt for token in ["readme", "docs", "documentation", "文档"]):
        return "documentation"
    if any(token in lowered_prompt for token in ["refactor", "重构"]):
        return "refactor"
    if any(token in lowered_prompt for token in ["failing test", "test failure", "修测试", "测试失败"]):
        return "test_fix"
    if any(token in lowered_prompt for token in ["config", "build", "package.json", "pyproject", "配置", "构建"]):
        return "config/build"
    if any(token in lowered_prompt for token in ["add", "implement", "feature", "新增", "添加", "实现"]):
        return "feature_addition"
    if any(token in lowered_prompt for token in ["fix", "bug", "error", "failure", "修复", "报错"]):
        return "bug_fix"
    if any(path.lower().endswith((".md", ".rst")) for path in explicit_paths):
        return "documentation"
    return "unknown"


def extract_requested_operations(lowered_prompt: str) -> list[str]:
    operations: list[str] = []
    mapping = {
        "fix": ["fix", "修复"],
        "add": ["add", "create", "新增", "添加", "创建"],
        "update": ["update", "修改", "更新"],
        "refactor": ["refactor", "重构"],
        "test": ["test", "测试"],
        "document": ["doc", "readme", "文档"],
    }
    for operation, tokens in mapping.items():
        if any(token in lowered_prompt for token in tokens):
            operations.append(operation)
    return operations


def extract_symbols(prompt: str, explicit_paths: list[str]) -> list[str]:
    symbols: list[str] = []
    for match in BACKTICK_RE.finditer(prompt):
        value = match.group(1).strip()
        if value and value not in explicit_paths:
            symbols.append(value)
    symbols.extend(match.group(1) for match in SYMBOL_HINT_RE.finditer(prompt))
    symbols.extend(match.group(1) for match in CHINESE_SYMBOL_RE.finditer(prompt))
    return unique(symbols)


def extract_constraints(prompt: str) -> list[str]:
    lowered = prompt.lower()
    constraints: list[str] = []
    checks = {
        "do_not_modify_tests": ["do not modify tests", "don't modify tests", "不要修改测试", "不要改测试"],
        "only_modify": ["only modify", "only change", "只修改", "只改"],
        "keep_compatibility": ["keep compatible", "保持兼容"],
        "avoid_refactor": ["do not refactor", "don't refactor", "不要重构", "不要大改"],
        "preserve_api": ["do not change api", "preserve api", "不要改变 api"],
        "no_new_files": ["no new files", "do not add files", "不要新增文件", "不要新建文件"],
    }
    for name, tokens in checks.items():
        if any(token in lowered for token in tokens):
            constraints.append(name)
    return constraints


def extract_only_paths(prompt: str) -> list[str]:
    lowered = prompt.lower()
    if not any(token in lowered for token in ["only modify", "only change", "只修改", "只改"]):
        return []
    return unique(normalize_path(match.group(1)) for match in PATH_RE.finditer(prompt))


def extract_acceptance_keywords(prompt: str, paths: list[str], symbols: list[str]) -> list[str]:
    keywords = list(paths) + list(symbols)
    for quoted in re.findall(r"'([^']+)'|\"([^\"]+)\"", prompt):
        value = quoted[0] or quoted[1]
        if value and len(value) <= 80:
            keywords.append(value)
    for token in ["expected", "error", "traceback", "fails", "输出", "报错", "失败"]:
        if token.lower() in prompt.lower():
            keywords.append(token)
    return unique(keywords)[:20]


def constraint_violation(contract: TaskContract, paths: list[str]) -> str:
    normalized = [normalize_path(path) for path in paths]
    if contract.only_paths:
        outside = [path for path in normalized if path not in contract.only_paths]
        if outside:
            return "planned/modified file violates only-modify constraint: " + ", ".join(outside)
    if contract.forbid_tests:
        tests = [path for path in normalized if is_test_path(path)]
        if tests and contract.task_type != "test_fix":
            return "planned/modified file violates do-not-modify-tests constraint: " + ", ".join(tests)
    if contract.forbid_docs:
        docs = [path for path in normalized if is_doc_path(path)]
        if docs:
            return "planned/modified file violates do-not-modify-docs constraint: " + ", ".join(docs)
    if contract.forbid_new_files:
        return ""
    return ""


def task_type_file_compatibility(contract: TaskContract, paths: list[str]) -> tuple[bool, str]:
    normalized = [normalize_path(path) for path in paths]
    if contract.task_type in {"bug_fix", "feature_addition", "test_fix"} and normalized and all(is_doc_path(path) for path in normalized):
        return False, f"{contract.task_type} task planned/modified only documentation files"
    if contract.task_type == "documentation" and normalized and all(is_code_path(path) for path in normalized):
        return False, "documentation task planned/modified only code files"
    if contract.task_type == "config/build" and normalized and all(not is_config_path(path) and not is_code_path(path) for path in normalized):
        return False, "config/build task did not include config, build, or code files"
    return True, ""


def is_over_edit(diff_summary: str, modified_files: list[str], planned_files: set[str]) -> bool:
    if len(modified_files) > max(3, len(planned_files) + 1):
        return True
    added = extract_count(diff_summary, "added_lines")
    deleted = extract_count(diff_summary, "deleted_lines")
    if added + deleted > 300:
        return True
    return False


def extract_count(text: str, key: str) -> int:
    match = re.search(rf"^{re.escape(key)}:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def summarize_output_failure(command: str, exit_code: int | None, output: str) -> str:
    excerpt = output.strip().replace("\r\n", "\n")
    if len(excerpt) > 1000:
        excerpt = excerpt[:1000] + "\n[truncated]"
    return f"{command} exited with {exit_code}: {excerpt}"


def project_has_test_signal(workspace: Path, command: str) -> bool:
    normalized = normalize_command(command)
    if "pytest" in normalized:
        return (workspace / "pytest.ini").exists() or (workspace / "pyproject.toml").exists() or (workspace / "tests").exists()
    if "unittest" in normalized:
        return (workspace / "tests").exists() or any(workspace.glob("test*.py"))
    if "npm" in normalized or "pnpm" in normalized or "yarn" in normalized:
        return (workspace / "package.json").exists()
    return True


def mentions_verification(text: str) -> bool:
    return any(token in text for token in ["verify", "test", "pytest", "unittest", "lint", "typecheck", "build", "验证", "测试"])


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("tests/") or "/tests/" in lowered or lowered.startswith("test_") or "_test." in lowered or lowered.endswith(".test.ts")


def is_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".rst")) or lowered.startswith("docs/") or "/docs/" in lowered


def is_code_path(path: str) -> bool:
    return path.lower().endswith((".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs"))


def is_config_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".toml", ".json", ".yaml", ".yml", ".ini", ".cfg")) or Path(lowered).name in {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml"}


def path_exists(task_state: Any, path: str) -> bool:
    workspace = getattr(getattr(task_state, "__self__", None), "workspace", None)
    del workspace
    return path in set(getattr(task_state, "candidate_files", []) or []) or path in set(getattr(task_state, "read_files", []) or [])


def normalize_path(path: str) -> str:
    path = str(path).strip().strip("`'\"")
    if not path:
        return ""
    return Path(path.replace("\\", "/")).as_posix()


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())


def unique(items: Any) -> list[str]:
    values: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in values:
            values.append(value)
    return values


def block(reason: str, instruction: str, checks: dict[str, bool]) -> SemanticTaskDecision:
    return SemanticTaskDecision(False, reason=reason, instruction=instruction, score=0.0, checks=checks)
