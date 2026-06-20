from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .coding_loop import is_verification_command, parse_exit_code


PATH_RE = re.compile(
    r"(?<![\w/\\.-])([A-Za-z0-9_.\-/\\]+"
    r"\.(?:py|pyi|js|jsx|ts|tsx|json|md|rst|txt|toml|yaml|yml|ini|cfg|rs|go|java|c|cc|cpp|h|hpp|cs|html|css|xml|sh|ps1|bat))"
)
BACKTICK_RE = re.compile(r"`([^`]+)`")
QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
SYMBOL_HINT_RE = re.compile(
    r"\b(?:function|func|class|method|def|variable|command|cli|option|flag)\s+([A-Za-z_][A-Za-z0-9_.:-]*)",
    re.IGNORECASE,
)
CHINESE_SYMBOL_RE = re.compile(r"(?:函数|方法|类|变量|命令|参数)\s*([A-Za-z_][A-Za-z0-9_.:-]*)")
TEST_COMMAND_RE = re.compile(
    r"\b((?:python(?:3)?|py)(?:\s+-3)?\s+-m\s+(?:pytest|unittest)[^\n.;`]*)"
    r"|\b(pytest[^\n.;`]*)"
    r"|\b((?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|lint|typecheck|build)[^\n.;`]*)"
    r"|\b((?:ruff|mypy|tsc|cargo\s+(?:test|check)|go\s+test|mvn\s+test|gradle\s+test|\.\/gradlew\s+test|make\s+(?:test|check)|markdownlint|mkdocs|sphinx-build)[^\n.;`]*)",
    re.IGNORECASE,
)

CANONICAL_INTENTS = {
    "bug_fix",
    "feature_addition",
    "refactor",
    "test_fix",
    "documentation",
    "config_build",
    "investigation",
    "unknown",
}


@dataclass
class EvidenceItem:
    kind: str
    source: str
    summary: str = ""
    paths: list[str] = field(default_factory=list)
    confidence: float = 1.0
    text: str = ""
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskClause:
    kind: str
    text: str
    confidence: float
    evidence: list[EvidenceItem] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "confidence": self.confidence,
            "evidence": [item.to_json() for item in self.evidence],
        }


@dataclass
class TaskIntent:
    name: str
    confidence: float
    reason: str
    evidence: list[EvidenceItem] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": [item.to_json() for item in self.evidence],
        }


@dataclass
class ScopeEvidence:
    path: str
    source: str
    confidence: float
    reason: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConstraintEvidence:
    name: str
    polarity: str
    text: str
    confidence: float
    paths: list[str] = field(default_factory=list)
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AcceptanceCriterion:
    kind: str
    text: str
    confidence: float
    source: str = "prompt"
    command: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


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
    primary_intent: str = "unknown"
    secondary_intents: list[str] = field(default_factory=list)
    target_hints: list[EvidenceItem] = field(default_factory=list)
    negative_constraints: list[ConstraintEvidence] = field(default_factory=list)
    positive_constraints: list[ConstraintEvidence] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    ambiguity_score: float = 1.0
    requires_more_exploration: bool = True

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_hints"] = [item.to_json() for item in self.target_hints]
        payload["negative_constraints"] = [item.to_json() for item in self.negative_constraints]
        payload["positive_constraints"] = [item.to_json() for item in self.positive_constraints]
        payload["acceptance_criteria"] = [item.to_json() for item in self.acceptance_criteria]
        payload["evidence"] = [item.to_json() for item in self.evidence]
        return payload


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
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_json() for item in self.evidence]
        return payload


@dataclass
class SemanticTaskDecision:
    allow: bool
    reason: str
    instruction: str = ""
    score: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_json() for item in self.evidence]
        return payload


def extract_task_contract(prompt: str) -> TaskContract:
    raw_prompt = prompt.strip()
    lowered = raw_prompt.lower()
    evidence: list[EvidenceItem] = []

    explicit_paths = unique(normalize_path(match.group(1)) for match in PATH_RE.finditer(raw_prompt))
    for path in explicit_paths:
        evidence.append(
            EvidenceItem("path", "prompt", summary=f"explicit path {path}", text=path, paths=[path], confidence=0.95, reason="path-like token in user prompt")
        )

    explicit_symbols = extract_symbols(raw_prompt, explicit_paths)
    for symbol in explicit_symbols:
        evidence.append(
            EvidenceItem("symbol", "prompt", summary=f"explicit symbol {symbol}", text=symbol, confidence=0.8, reason="quoted or symbol-hint token in user prompt")
        )

    intents = infer_task_intents(lowered, explicit_paths)
    primary_intent = intents[0].name if intents else "unknown"
    task_type = "config/build" if primary_intent == "config_build" else primary_intent
    secondary_intents = [intent.name for intent in intents[1:]]
    for intent in intents:
        evidence.extend(intent.evidence)

    requested_operations = extract_requested_operations(lowered)
    negative_constraints, positive_constraints = extract_constraint_evidence(raw_prompt)
    constraints = unique([item.name for item in negative_constraints + positive_constraints])
    only_paths = extract_only_paths(raw_prompt)
    forbid_tests = any(item.name == "do_not_modify_tests" for item in negative_constraints)
    forbid_docs = any(item.name == "do_not_modify_docs" for item in negative_constraints)
    forbid_new_files = any(item.name == "no_new_files" for item in negative_constraints)
    preserve_api = any(item.name == "preserve_api" for item in negative_constraints + positive_constraints)
    avoid_refactor = any(item.name == "avoid_refactor" for item in negative_constraints)
    allowed_new_files = (
        any(operation in requested_operations for operation in ["add", "create"])
        or any(item.name in {"add_feature", "add_cli_behavior"} for item in positive_constraints)
    ) and not forbid_new_files

    acceptance_criteria = extract_acceptance_criteria(raw_prompt, explicit_paths, explicit_symbols)
    objective_excerpt = raw_prompt[:160]
    if objective_excerpt:
        evidence.append(
            EvidenceItem(
                "objective",
                "prompt",
                summary="natural language task objective",
                text=objective_excerpt,
                confidence=0.55,
                reason="raw prompt objective excerpt",
            )
        )
    for criterion in acceptance_criteria:
        evidence.append(
            EvidenceItem(
                criterion.kind,
                criterion.source,
                summary=f"acceptance criterion: {criterion.kind}",
                text=criterion.text,
                confidence=criterion.confidence,
                reason="extracted acceptance criterion",
            )
        )
    acceptance_keywords = extract_acceptance_keywords(raw_prompt, explicit_paths, explicit_symbols, acceptance_criteria)
    target_hints = [item for item in evidence if item.kind in {"path", "symbol", "command", "error", "literal", "objective"}]
    ambiguity_score = estimate_ambiguity(primary_intent, explicit_paths, explicit_symbols, acceptance_criteria, raw_prompt)
    requires_more_exploration = ambiguity_score >= 0.55 and not explicit_paths

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
        primary_intent=primary_intent,
        secondary_intents=secondary_intents,
        target_hints=target_hints,
        negative_constraints=negative_constraints,
        positive_constraints=positive_constraints,
        acceptance_criteria=acceptance_criteria,
        evidence=evidence,
        ambiguity_score=ambiguity_score,
        requires_more_exploration=requires_more_exploration,
    )


def validate_plan(contract: TaskContract, task_state: Any, assistant_text: str) -> SemanticTaskDecision:
    planned_files = unique(normalize_path(path) for path in getattr(task_state, "planned_files", []) or [])
    candidate_files = set(normalize_path(path) for path in getattr(task_state, "candidate_files", []) or [])
    read_files = set(normalize_path(path) for path in getattr(task_state, "read_files", []) or [])
    failure_files = set(extract_paths_from_text(str(getattr(task_state, "last_failure_summary", "") or "")))
    prompt_paths = set(contract.explicit_paths)
    checks = {
        "has_planned_files": bool(planned_files),
        "hard_constraints_respected": True,
        "files_grounded": True,
        "relevance_threshold_met": True,
        "mentions_verification": mentions_verification(assistant_text),
        "verification_command_relevant": True,
        "exploration_sufficient": True,
    }
    evidence: list[EvidenceItem] = []
    warnings: list[str] = []

    if not planned_files:
        checks["has_planned_files"] = False
        return block(
            "plan missing planned_files",
            "Add a minimal plan with `planned_files: ...` and a real verification command.",
            checks,
        )

    hard_error = hard_constraint_violation(contract, planned_files, known_files=candidate_files | read_files | prompt_paths | failure_files)
    if hard_error:
        checks["hard_constraints_respected"] = False
        return block(hard_error, "Revise planned_files so the plan respects the user's hard constraints.", checks)

    if contract.requires_more_exploration and not read_files and not candidate_files:
        checks["exploration_sufficient"] = False
        return block(
            "insufficient exploration for ambiguous task",
            "Continue search_text, list_files, and read_file to locate files related to the task objective, then propose grounded planned_files again.",
            checks,
        )

    grounding = [path_grounding(path, contract, candidate_files, read_files, failure_files) for path in planned_files]
    ungrounded = [item.path for item in grounding if item.confidence <= 0.0]
    checks["files_grounded"] = not ungrounded
    if ungrounded:
        return block(
            "planned files lack exploration evidence",
            "Continue search_text, list_files, and read_file to ground these files before editing: " + ", ".join(ungrounded),
            checks,
        )
    evidence.extend(EvidenceItem("grounding", item.source, summary=item.reason, text=item.path, paths=[item.path], confidence=item.confidence) for item in grounding)

    scope_warning = file_scope_warning(contract, planned_files)
    if scope_warning and ("only touches documentation" in scope_warning or "only touches code" in scope_warning):
        return block(scope_warning, "Revise planned_files so they match the requested task type and objective.", checks, evidence=evidence)

    scores = [score_path_relevance(path, contract, candidate_files, read_files, failure_files) for path in planned_files]
    average_score = sum(scores) / len(scores)
    checks["relevance_threshold_met"] = average_score >= 0.45
    if average_score < 0.45:
        return block(
            "planned files are weakly related to the task objective",
            "Read or search for files that mention the requested symbols, behavior, errors, or acceptance criteria before planning edits.",
            checks,
            score=average_score,
            evidence=evidence,
        )
    if average_score < 0.6:
        checks["exploration_sufficient"] = False
        return block(
            "planned files need more evidence before editing",
            "Continue search_text/list_files/read_file to improve localization confidence, then restate planned_files with a verification command.",
            checks,
            score=average_score,
            evidence=evidence,
        )

    if scope_warning:
        warnings.append(scope_warning)

    if contract.preserve_api and mentions_public_interface_change(assistant_text):
        warnings.append("plan may touch public API while preserve-api constraint is present")

    command = extract_verification_command(assistant_text)
    if not command:
        checks["mentions_verification"] = False
        return block(
            "plan missing real verification command",
            "Add the concrete test/lint/typecheck/build/docs-check command to run after editing.",
            checks,
            score=average_score,
            evidence=evidence,
        )
    verification = validate_verification_command(contract, task_state, command, planned_files)
    checks["verification_command_relevant"] = verification.is_real_verification and verification.is_relevant
    if not checks["verification_command_relevant"]:
        return block(
            "planned verification command is not relevant",
            "Choose a verification command that matches the planned file types and task objective: " + verification.relevance_reason,
            checks,
            score=average_score,
            evidence=evidence + verification.evidence,
        )

    return SemanticTaskDecision(
        True,
        "plan is grounded and relevant",
        score=min(1.0, average_score),
        checks=checks,
        warnings=warnings + verification.warnings,
        evidence=evidence + verification.evidence,
    )


def validate_edit(contract: TaskContract, task_state: Any, modified_files: list[str], diff_summary: str = "") -> SemanticTaskDecision:
    planned_files = set(normalize_path(path) for path in getattr(task_state, "planned_files", []) or [])
    candidate_files = set(normalize_path(path) for path in getattr(task_state, "candidate_files", []) or [])
    read_files = set(normalize_path(path) for path in getattr(task_state, "read_files", []) or [])
    modified = unique(normalize_path(path) for path in modified_files)
    checks = {
        "modified_files_present": bool(modified),
        "within_plan_or_allowed_new": True,
        "hard_constraints_respected": True,
        "relevance_threshold_met": True,
        "over_edit_checked": True,
    }
    evidence: list[EvidenceItem] = []
    warnings: list[str] = []

    if not modified:
        checks["modified_files_present"] = False
        return block("edit target unknown", "The edit result did not identify modified files. Re-run with clear patch/edit targets.", checks)

    outside_plan: list[str] = []
    for path in modified:
        known = path in candidate_files or path in read_files or path in contract.explicit_paths
        related_new_file = contract.allowed_new_files and score_path_relevance(path, contract, candidate_files, read_files, set()) >= 0.65
        if path not in planned_files and not (related_new_file and not known):
            outside_plan.append(path)
    checks["within_plan_or_allowed_new"] = not outside_plan
    if outside_plan:
        return block(
            "modified file outside planned_files",
            "Restrict edits to planned_files or revise the plan before editing: " + ", ".join(outside_plan),
            checks,
        )

    hard_error = hard_constraint_violation(contract, modified, known_files=candidate_files | read_files | set(contract.explicit_paths))
    if hard_error:
        checks["hard_constraints_respected"] = False
        return block(hard_error, "Undo or revise edits that violate the user's hard constraints.", checks)

    scores = [score_path_relevance(path, contract, candidate_files, read_files, set()) for path in modified]
    average_score = sum(scores) / len(scores)
    checks["relevance_threshold_met"] = average_score >= 0.45
    if average_score < 0.45:
        return block(
            "modified files are weakly related to the task objective",
            "Re-localize the task and edit files that are grounded in prompt or tool evidence.",
            checks,
            score=average_score,
        )
    if average_score < 0.7:
        warnings.append("modified files have only moderate relevance evidence")

    scope_warning = file_scope_warning(contract, modified)
    if scope_warning:
        warnings.append(scope_warning)

    over_edit_warnings = over_edit_warnings_for(diff_summary, modified, planned_files, contract)
    warnings.extend(over_edit_warnings)
    if contract.preserve_api and mentions_public_interface_change(diff_summary):
        warnings.append("edit may touch public API while preserve-api constraint is present")

    score = average_score
    if warnings:
        score = min(score, 0.72)
    for path, path_score in zip(modified, scores):
        evidence.append(
            EvidenceItem("edit_relevance", "modified_files", summary=f"{path} relevance {path_score:.2f}", text=path, paths=[path], confidence=path_score)
        )
    return SemanticTaskDecision(
        True,
        "edit is grounded and relevant" if not warnings else "edit is relevant with warnings",
        score=score,
        checks=checks,
        warnings=unique(warnings),
        evidence=evidence,
    )


def validate_verification_command(
    contract: TaskContract,
    task_state: Any,
    command: str,
    modified_files: list[str],
    workspace: Path | None = None,
) -> VerificationEvidence:
    normalized = normalize_command(command)
    command_type = classify_verification_command(command)
    fake_prefixes = ("echo", "cat", "ls", "dir", "pwd", "grep", "find", "git status", "git diff")
    if any(normalized == item or normalized.startswith(item + " ") for item in fake_prefixes):
        return verification_block(command, command_type, "command is runtime evidence or shell inspection, not code verification")

    real = is_semantic_verification_command(command)
    if not real:
        return verification_block(command, command_type, "command is not a real test/lint/typecheck/build/docs verification command")

    modified = [normalize_path(path) for path in modified_files]
    evidence: list[EvidenceItem] = [
        EvidenceItem("verification_command", "run_shell", summary=f"{command_type} command", text=command, confidence=0.85)
    ]
    warnings: list[str] = []
    blockers: list[str] = []

    explicit_commands = [criterion.command for criterion in contract.acceptance_criteria if criterion.command]
    if explicit_commands and not command_matches_any(normalized, explicit_commands):
        warnings.append("verification differs from explicit command requested by the user")

    expected_types = expected_verification_types(contract, modified)
    relevant = command_type in expected_types
    reason = f"{command_type} verification matches expected types: {', '.join(sorted(expected_types))}"
    if not relevant:
        reason = f"{command_type} verification does not match expected types: {', '.join(sorted(expected_types))}"
        blockers.append(reason)

    if is_targeted_command_for_modified_file(normalized, modified):
        reason = "targeted verification covers a modified file"
        evidence.append(EvidenceItem("coverage", "command", summary=reason, text=command, paths=modified, confidence=0.95))
    elif command_type in {"test", "lint", "typecheck", "build", "docs/check"}:
        warnings.append("verification is broad; output quality must show meaningful checks")

    if workspace is not None and command_type == "test" and not project_has_test_signal(workspace, command):
        warnings.append("no local test configuration was detected for this broad test command")

    return VerificationEvidence(
        command=command,
        exit_code=None,
        is_real_verification=True,
        is_relevant=relevant,
        relevance_reason=reason,
        has_meaningful_checks=True,
        meaningful_checks_reason="meaningfulness depends on command output",
        command_type=command_type,
        warnings=unique(warnings),
        blockers=unique(blockers),
        evidence=evidence,
    )


def validate_verification_output(command: str, output: str, *, prior: VerificationEvidence | None = None) -> VerificationEvidence:
    exit_code = parse_exit_code(output)
    command_type = prior.command_type if prior is not None else classify_verification_command(command)
    real = prior.is_real_verification if prior is not None else is_semantic_verification_command(command)
    relevant = prior.is_relevant if prior is not None else real
    relevance_reason = prior.relevance_reason if prior is not None else ("real verification command" if real else "not a real verification command")
    warnings = list(prior.warnings if prior is not None else [])
    blockers = list(prior.blockers if prior is not None else [])
    evidence = list(prior.evidence if prior is not None else [])
    lowered = output.lower()
    no_checks_patterns = [
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
    has_no_checks = any(re.search(pattern, lowered) for pattern in no_checks_patterns)
    if exit_code != 0:
        blockers.append("verification command failed")
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
            warnings=unique(warnings),
            blockers=unique(blockers),
            evidence=evidence,
        )
    if has_no_checks:
        blockers.append("verification output indicates no meaningful checks")
        return VerificationEvidence(
            command=command,
            exit_code=exit_code,
            is_real_verification=real,
            is_relevant=relevant,
            relevance_reason=relevance_reason,
            has_meaningful_checks=False,
            meaningful_checks_reason="command output indicates zero, skipped, missing, or unconfigured checks",
            command_type=command_type,
            warnings=unique(warnings),
            blockers=unique(blockers),
            evidence=evidence,
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
        warnings=unique(warnings),
        blockers=unique(blockers),
        evidence=evidence,
    )


def infer_task_intents(lowered_prompt: str, explicit_paths: list[str]) -> list[TaskIntent]:
    specs = [
        ("documentation", ["readme", "docs", "documentation", "markdown", "文档"], 0.86),
        ("refactor", ["refactor", "重构"], 0.86),
        ("test_fix", ["failing test", "test failure", "fix test", "测试失败"], 0.88),
        ("config_build", ["config", "build", "package.json", "pyproject", "配置", "构建"], 0.82),
        ("feature_addition", ["add", "implement", "feature", "support", "cli", "command line", "新增", "添加", "实现"], 0.9),
        ("bug_fix", ["fix", "bug", "error", "failure", "traceback", "修复", "报错", "失败"], 0.8),
        ("investigation", ["inspect", "analyze", "investigate", "review", "检查", "分析"], 0.68),
    ]
    intents: list[TaskIntent] = []
    for name, tokens, confidence in specs:
        matched = [token for token in tokens if token in lowered_prompt]
        if matched:
            item = EvidenceItem("intent", "prompt", summary=f"{name} intent", text=", ".join(matched), confidence=confidence, reason="matched intent tokens")
            intents.append(TaskIntent(name, confidence, f"matched {', '.join(matched[:3])}", [item]))
    if not intents and any(path.lower().endswith((".md", ".rst")) for path in explicit_paths):
        item = EvidenceItem("intent", "prompt", summary="documentation intent from doc path", paths=explicit_paths, confidence=0.65)
        intents.append(TaskIntent("documentation", 0.65, "explicit documentation path", [item]))
    if not intents:
        intents.append(TaskIntent("unknown", 0.2, "no strong intent signal", []))
    intents.sort(key=lambda intent: intent.confidence, reverse=True)
    return intents


def extract_requested_operations(lowered_prompt: str) -> list[str]:
    operations: list[str] = []
    mapping = {
        "fix": ["fix", "repair", "修复"],
        "add": ["add", "新增", "添加"],
        "create": ["create", "new file", "创建"],
        "update": ["update", "change", "modify", "修改", "更新"],
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
    path_set = set(explicit_paths)
    for match in BACKTICK_RE.finditer(prompt):
        value = match.group(1).strip()
        if value and normalize_path(value) not in path_set and not PATH_RE.fullmatch(value):
            symbols.append(value)
    symbols.extend(match.group(1) for match in SYMBOL_HINT_RE.finditer(prompt))
    symbols.extend(match.group(1) for match in CHINESE_SYMBOL_RE.finditer(prompt))
    return unique(symbols)


def extract_constraint_evidence(prompt: str) -> tuple[list[ConstraintEvidence], list[ConstraintEvidence]]:
    lowered = prompt.lower()
    negative: list[ConstraintEvidence] = []
    positive: list[ConstraintEvidence] = []
    negative_specs = {
        "do_not_modify_tests": ["do not modify tests", "don't modify tests", "do not change tests", "不要修改测试", "不要改测试"],
        "do_not_modify_docs": ["do not modify docs", "don't modify docs", "不要修改文档", "不要改文档"],
        "only_modify": ["only modify", "only change", "只修改", "只改"],
        "avoid_refactor": ["do not refactor", "don't refactor", "no refactor", "不要重构", "不要大改"],
        "preserve_api": ["do not change api", "preserve api", "keep api", "不要改变 api", "保持 api"],
        "no_new_files": ["no new files", "do not add files", "do not create files", "不要新增文件", "不要新建文件"],
    }
    positive_specs = {
        "preserve_behavior": ["preserve behavior", "keep behavior", "保持行为"],
        "keep_compatibility": ["keep compatible", "backward compatible", "保持兼容"],
        "add_feature": ["add support", "support ", "新增", "添加支持"],
        "add_cli_behavior": ["cli", "command line", "命令行"],
    }
    for name, tokens in negative_specs.items():
        matched = [token for token in tokens if token in lowered]
        if matched:
            negative.append(ConstraintEvidence(name, "negative", matched[0], 0.9, paths=extract_only_paths(prompt) if name == "only_modify" else [], reason="explicit negative constraint"))
    for name, tokens in positive_specs.items():
        matched = [token for token in tokens if token in lowered]
        if matched:
            positive.append(ConstraintEvidence(name, "positive", matched[0], 0.7, reason="explicit positive constraint"))
    return negative, positive


def extract_only_paths(prompt: str) -> list[str]:
    lowered = prompt.lower()
    if not any(token in lowered for token in ["only modify", "only change", "只修改", "只改"]):
        return []
    return unique(normalize_path(match.group(1)) for match in PATH_RE.finditer(prompt))


def extract_acceptance_criteria(prompt: str, paths: list[str], symbols: list[str]) -> list[AcceptanceCriterion]:
    criteria: list[AcceptanceCriterion] = []
    for path in paths:
        criteria.append(AcceptanceCriterion("path", path, 0.75))
    for symbol in symbols:
        criteria.append(AcceptanceCriterion("symbol", symbol, 0.65))
    for quoted in QUOTED_RE.findall(prompt):
        value = quoted[0] or quoted[1]
        if value and len(value) <= 120:
            criteria.append(AcceptanceCriterion("literal", value, 0.72))
    for match in TEST_COMMAND_RE.finditer(prompt):
        command = next(group for group in match.groups() if group)
        criteria.append(AcceptanceCriterion("verification_command", command.strip(), 0.9, command=command.strip()))
    for phrase in extract_requirement_phrases(prompt):
        criteria.append(AcceptanceCriterion("requirement_phrase", phrase, 0.55))
    return criteria


def extract_acceptance_keywords(
    prompt: str,
    paths: list[str],
    symbols: list[str],
    criteria: list[AcceptanceCriterion] | None = None,
) -> list[str]:
    keywords = list(paths) + list(symbols)
    criteria = criteria or extract_acceptance_criteria(prompt, paths, symbols)
    keywords.extend(item.text for item in criteria if item.kind in {"literal", "requirement_phrase", "verification_command"})
    for token in ["expected", "actual", "error", "traceback", "fails", "should", "must", "输出", "报错", "失败", "期望", "必须", "需要"]:
        if token.lower() in prompt.lower():
            keywords.append(token)
    return unique(keywords)[:30]


def validate_plan_verification_text(text: str) -> str:
    return extract_verification_command(text)


def hard_constraint_violation(contract: TaskContract, paths: list[str], *, known_files: set[str]) -> str:
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
        new_files = [path for path in normalized if path not in known_files]
        if new_files:
            return "planned/modified file violates no-new-files constraint: " + ", ".join(new_files)
    return ""


def path_grounding(path: str, contract: TaskContract, candidate_files: set[str], read_files: set[str], failure_files: set[str]) -> ScopeEvidence:
    if path in contract.explicit_paths:
        return ScopeEvidence(path, "prompt", 0.95, "explicitly named in user prompt")
    if path in read_files:
        return ScopeEvidence(path, "read_file", 0.9, "file was read before planning")
    if path in candidate_files:
        return ScopeEvidence(path, "explore_tool", 0.75, "file appeared in exploration candidates")
    if path in failure_files:
        return ScopeEvidence(path, "verification_failure", 0.8, "file appeared in failure output")
    if contract.allowed_new_files and looks_like_relevant_new_file(path, contract):
        return ScopeEvidence(path, "task_required_new_file", 0.65, "new file is plausible for requested addition")
    return ScopeEvidence(path, "model_text_only", 0.0, "file only appeared in assistant text")


def score_path_relevance(path: str, contract: TaskContract, candidate_files: set[str], read_files: set[str], failure_files: set[str]) -> float:
    path = normalize_path(path)
    lowered_path = path.lower()
    score = 0.0
    if path in contract.explicit_paths:
        score += 0.45
    if path in read_files:
        score += 0.25
    if path in candidate_files:
        score += 0.18
    if path in failure_files:
        score += 0.2
    if any(token and token.lower() in lowered_path for token in contract.explicit_symbols + contract.acceptance_keywords):
        score += 0.18
    if file_matches_intent(path, contract):
        score += 0.22
    if is_test_path(path) and contract.task_type not in {"test_fix", "feature_addition"} and not contract.forbid_tests:
        score -= 0.08
    if is_doc_path(path) and contract.task_type == "bug_fix" and "document" not in contract.requested_operations:
        score -= 0.18
    if is_config_path(path) and contract.task_type not in {"config/build", "config_build"} and "build" not in contract.raw_prompt.lower():
        score -= 0.08
    if score == 0.0 and contract.requires_more_exploration:
        score = 0.1
    return max(0.0, min(1.0, score))


def file_matches_intent(path: str, contract: TaskContract) -> bool:
    task_type = contract.task_type
    if task_type == "documentation":
        return is_doc_path(path)
    if task_type == "config/build":
        return is_config_path(path) or is_code_path(path)
    if task_type == "test_fix":
        return is_test_path(path) or is_code_path(path)
    if task_type == "feature_addition":
        return is_code_path(path) or is_doc_path(path) or is_test_path(path) or is_config_path(path)
    if task_type in {"bug_fix", "refactor"}:
        return is_code_path(path) or is_test_path(path)
    return is_code_path(path) or is_doc_path(path) or is_config_path(path)


def file_scope_warning(contract: TaskContract, paths: list[str]) -> str:
    normalized = [normalize_path(path) for path in paths]
    if contract.task_type == "bug_fix" and normalized and all(is_doc_path(path) for path in normalized):
        return "bug-fix task only touches documentation files"
    if contract.task_type == "documentation" and normalized and all(is_code_path(path) for path in normalized):
        return "documentation task only touches code files"
    if any(is_test_path(path) for path in normalized) and contract.task_type not in {"test_fix", "feature_addition"} and not contract.forbid_tests:
        return "task did not explicitly require test edits"
    if any(is_config_path(path) for path in normalized) and contract.task_type not in {"config/build", "feature_addition"}:
        return "task did not explicitly require config/build edits"
    return ""


def over_edit_warnings_for(diff_summary: str, modified_files: list[str], planned_files: set[str], contract: TaskContract) -> list[str]:
    warnings: list[str] = []
    if len(modified_files) > max(4, len(planned_files) + 2):
        warnings.append("edit changes many more files than the plan")
    added = extract_count(diff_summary, "added_lines")
    deleted = extract_count(diff_summary, "deleted_lines")
    if added + deleted > 300:
        warnings.append("edit changes a large number of lines")
    if contract.task_type == "bug_fix" and sum(1 for path in modified_files if is_doc_path(path) or is_config_path(path)) >= 2:
        warnings.append("bug-fix edit includes multiple docs/config files")
    return warnings


def expected_verification_types(contract: TaskContract, modified_files: list[str]) -> set[str]:
    if contract.task_type == "documentation":
        return {"docs/check", "lint", "build", "test"}
    if contract.task_type == "config/build":
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
    if any(is_doc_path(path) for path in modified_files):
        return {"docs/check", "lint", "build", "test"}
    return {"test", "lint", "typecheck", "build", "docs/check"}


def is_semantic_verification_command(command: str) -> bool:
    if is_verification_command(command):
        return True
    normalized = normalize_command(command)
    return any(
        normalized == prefix or normalized.startswith(prefix + " ")
        for prefix in ["markdownlint", "mkdocs", "sphinx-build", "npm run build", "pnpm run build", "yarn build", "yarn run build"]
    )


def classify_verification_command(command: str) -> str:
    normalized = normalize_command(command)
    if any(token in normalized for token in ["pytest", "unittest", "npm test", "pnpm test", "yarn test", "cargo test", "go test", "mvn test", "gradle test", "make test"]):
        return "test"
    if any(token in normalized for token in ["markdownlint", "mkdocs", "sphinx-build"]):
        return "docs/check"
    if any(token in normalized for token in ["ruff", "lint", "eslint"]):
        return "lint"
    if any(token in normalized for token in ["mypy", "tsc", "cargo check"]):
        return "typecheck"
    if any(token in normalized for token in ["build", "make check"]):
        return "build"
    if any(token in normalized for token in ["python ", "node ", "py "]):
        return "run/smoke"
    return "unknown"


def verification_block(command: str, command_type: str, reason: str) -> VerificationEvidence:
    return VerificationEvidence(
        command=command,
        exit_code=None,
        is_real_verification=False,
        is_relevant=False,
        relevance_reason=reason,
        has_meaningful_checks=False,
        meaningful_checks_reason="command was not accepted as verification",
        command_type=command_type,
        blockers=[reason],
        evidence=[EvidenceItem("verification_command", "run_shell", summary=reason, text=command, confidence=0.0)],
    )


def extract_verification_command(text: str) -> str:
    match = TEST_COMMAND_RE.search(text)
    if not match:
        return ""
    return next(group for group in match.groups() if group).strip().rstrip(".")


def command_matches_any(normalized_command: str, explicit_commands: list[str]) -> bool:
    for command in explicit_commands:
        normalized = normalize_command(command)
        if normalized and (normalized in normalized_command or normalized_command in normalized):
            return True
    return False


def is_targeted_command_for_modified_file(normalized_command: str, modified_files: list[str]) -> bool:
    return any(path and path.lower() in normalized_command for path in modified_files)


def extract_requirement_phrases(prompt: str) -> list[str]:
    phrases: list[str] = []
    patterns = [
        r"\b(?:should|must|expected|actual|outputs?|fails?|error)\b[:\s]+([^.\n;]{1,120})",
        r"(?:需要|必须|期望|输出|报错|失败)[:：\s]*([^。\n；]{1,80})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, prompt, flags=re.IGNORECASE):
            phrases.append(match.group(1).strip())
    return unique(phrases)


def estimate_ambiguity(intent: str, paths: list[str], symbols: list[str], criteria: list[AcceptanceCriterion], prompt: str) -> float:
    score = 0.85
    if intent != "unknown":
        score -= 0.2
    if paths:
        score -= 0.35
    if symbols:
        score -= 0.15
    if criteria:
        score -= 0.15
    if len(prompt.split()) >= 18:
        score -= 0.05
    return max(0.0, min(1.0, score))


def looks_like_relevant_new_file(path: str, contract: TaskContract) -> bool:
    lowered = path.lower()
    if contract.task_type == "documentation":
        return is_doc_path(path)
    if contract.task_type == "feature_addition":
        return is_code_path(path) or is_test_path(path) or is_doc_path(path)
    return any(token.lower() in lowered for token in contract.acceptance_keywords + contract.explicit_symbols)


def mentions_verification(text: str) -> bool:
    return bool(extract_verification_command(text)) or any(token in text.lower() for token in ["verify", "test", "pytest", "unittest", "lint", "typecheck", "build", "验证", "测试"])


def mentions_public_interface_change(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["public api", "export ", "exports.", "signature", "function signature", "class signature", "__all__", "breaking"])


def extract_paths_from_text(text: str) -> list[str]:
    return unique(normalize_path(match.group(1)) for match in PATH_RE.finditer(text))


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


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("tests/") or "/tests/" in lowered or lowered.startswith("test_") or "_test." in lowered or lowered.endswith((".test.ts", ".test.js", ".spec.ts", ".spec.js"))


def is_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".rst")) or lowered.startswith("docs/") or "/docs/" in lowered


def is_code_path(path: str) -> bool:
    return path.lower().endswith((".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs"))


def is_config_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".toml", ".json", ".yaml", ".yml", ".ini", ".cfg")) or Path(lowered).name in {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml"}


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


def block(
    reason: str,
    instruction: str,
    checks: dict[str, bool],
    *,
    score: float = 0.0,
    evidence: list[EvidenceItem] | None = None,
) -> SemanticTaskDecision:
    return SemanticTaskDecision(
        False,
        reason=reason,
        instruction=instruction,
        score=score,
        checks=checks,
        blockers=[reason],
        evidence=evidence or [],
    )


# Backward-compatible helper names retained for external tests/imports.
def constraint_violation(contract: TaskContract, paths: list[str]) -> str:
    return hard_constraint_violation(contract, paths, known_files=set(contract.explicit_paths))


def task_type_file_compatibility(contract: TaskContract, paths: list[str]) -> tuple[bool, str]:
    warning = file_scope_warning(contract, paths)
    if warning and ("only touches documentation" in warning or "only touches code" in warning):
        return False, warning
    return True, warning


def path_exists(task_state: Any, path: str) -> bool:
    normalized = normalize_path(path)
    return normalized in set(getattr(task_state, "candidate_files", []) or []) or normalized in set(getattr(task_state, "read_files", []) or [])


def is_over_edit(diff_summary: str, modified_files: list[str], planned_files: set[str]) -> bool:
    return bool(over_edit_warnings_for(diff_summary, modified_files, planned_files, extract_task_contract("")))
