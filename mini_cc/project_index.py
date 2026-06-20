from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .verification import discover_verification_candidates


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}

SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp"}
TEST_PATTERNS = ("test_", "_test.", ".test.", ".spec.", "tests/", "test/")
CONFIG_NAMES = {
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "setup.py",
    "package.json",
    "tsconfig.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Makefile",
}


@dataclass(frozen=True)
class FileRecord:
    path: str
    suffix: str
    size: int
    likely_role: str


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str
    path: str
    line: int
    detail: str = ""


@dataclass
class ProjectIndex:
    workspace: Path
    project_types: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    files: list[FileRecord] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)

    @classmethod
    def build(cls, workspace: Path) -> "ProjectIndex":
        root = workspace.expanduser().resolve()
        index = cls(workspace=root)
        index.project_types = _detect_project_types(root)
        index.config_files = _config_files(root)
        index.source_dirs = _dirs_matching(root, ["src", "lib", "mini_cc", "app"])
        index.test_dirs = _dirs_matching(root, ["tests", "test"])
        index.files = _scan_files(root)
        index.symbols = _build_symbols(root, index.files)
        return index

    def summarize_project(self) -> dict[str, Any]:
        candidates = discover_verification_candidates(self.workspace)
        return {
            "project_types": self.project_types,
            "config_files": self.config_files[:30],
            "source_dirs": self.source_dirs,
            "test_dirs": self.test_dirs,
            "file_counts": _counts_by_role(self.files),
            "recommended_verification": [candidate.to_json() for candidate in candidates[:6]],
        }

    def find_relevant_files(self, query: str, max_results: int = 10) -> list[FileRecord]:
        terms = _query_terms(query)
        if not terms:
            return self.files[: max(1, max_results)]
        scored: list[tuple[int, FileRecord]] = []
        symbol_paths = {
            symbol.path
            for symbol in self.symbols
            if any(term in symbol.name.lower() or term in symbol.detail.lower() for term in terms)
        }
        for record in self.files:
            haystack = f"{record.path.lower()} {record.suffix} {record.likely_role}"
            score = sum(4 for term in terms if term in haystack)
            if record.path in symbol_paths:
                score += 8
            if record.likely_role == "test" and any(term in query.lower() for term in ["test", "pytest", "unittest", "fail"]):
                score += 3
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        return [record for _, record in scored[: max(1, max_results)]]

    def find_symbol(self, name: str) -> list[SymbolRecord]:
        needle = name.strip().lower()
        if not needle:
            return []
        return [
            symbol
            for symbol in self.symbols
            if needle == symbol.name.lower() or needle in symbol.name.lower() or needle in symbol.detail.lower()
        ][:50]

    def related_tests_for(self, source_path: str) -> list[str]:
        path = _norm(source_path)
        stem = Path(path).stem
        candidates: list[str] = []
        for record in self.files:
            if record.likely_role != "test":
                continue
            test_path = record.path
            test_name = Path(test_path).stem
            if stem in test_name or test_path.endswith(f"test_{stem}.py") or path.replace("/", "_") in test_path:
                candidates.append(test_path)
        return _unique(candidates)

    def related_sources_for(self, test_path: str) -> list[str]:
        path = _norm(test_path)
        stem = Path(path).stem
        source_hint = re.sub(r"^(test_|test-)", "", stem)
        source_hint = re.sub(r"(_test|\.test|\.spec)$", "", source_hint)
        candidates: list[str] = []
        for record in self.files:
            if record.likely_role != "source":
                continue
            if Path(record.path).stem == source_hint or source_hint in record.path:
                candidates.append(record.path)
        return _unique(candidates)

    def to_json(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "project_types": self.project_types,
            "config_files": self.config_files,
            "source_dirs": self.source_dirs,
            "test_dirs": self.test_dirs,
            "files": [asdict(record) for record in self.files],
            "symbols": [asdict(symbol) for symbol in self.symbols],
        }


def render_json(value: Any, limit: int = 24_000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + f"\n\n[truncated {len(text) - limit} chars]"


def _detect_project_types(root: Path) -> list[str]:
    found: list[str] = []
    if any((root / name).exists() for name in ["pyproject.toml", "requirements.txt", "pytest.ini", "setup.py"]) or (root / "tests").exists():
        found.append("Python")
    if any((root / name).exists() for name in ["package.json", "tsconfig.json"]) or (root / "src").exists() or (root / "test").exists():
        found.append("Node/TS")
    if (root / "Cargo.toml").exists():
        found.append("Rust")
    if (root / "go.mod").exists():
        found.append("Go")
    if (root / "pom.xml").exists() or (root / "build.gradle").exists():
        found.append("Java")
    return found or ["Unknown"]


def _config_files(root: Path) -> list[str]:
    rows: list[str] = []
    for path in root.rglob("*"):
        if _ignored(path) or not path.is_file():
            continue
        if path.name in CONFIG_NAMES or path.suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json"}:
            rows.append(path.relative_to(root).as_posix())
    return sorted(rows)


def _dirs_matching(root: Path, names: list[str]) -> list[str]:
    rows: list[str] = []
    for name in names:
        path = root / name
        if path.exists() and path.is_dir():
            rows.append(path.relative_to(root).as_posix())
    return rows


def _scan_files(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in root.rglob("*"):
        if _ignored(path) or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        records.append(FileRecord(rel, path.suffix, size, _role_for(rel, path)))
    return sorted(records, key=lambda record: record.path)


def _build_symbols(root: Path, files: list[FileRecord]) -> list[SymbolRecord]:
    symbols: list[SymbolRecord] = []
    for record in files:
        if record.size > 1_000_000:
            continue
        path = root / record.path
        if record.suffix in {".py", ".pyi"}:
            symbols.extend(_python_symbols(path, record.path))
        elif record.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            symbols.extend(_js_ts_symbols(path, record.path))
    return symbols


def _python_symbols(path: Path, rel: str) -> list[SymbolRecord]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    rows: list[SymbolRecord] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append(SymbolRecord(node.name, "function", rel, node.lineno))
        elif isinstance(node, ast.ClassDef):
            rows.append(SymbolRecord(node.name, "class", rel, node.lineno))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            detail = ".".join(filter(None, [getattr(node, "module", None), ", ".join(names)]))
            rows.append(SymbolRecord(detail or names[0], "import", rel, node.lineno, detail=detail))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assignment_names(node):
                rows.append(SymbolRecord(name, "assignment", rel, node.lineno))
    return rows


def _assignment_names(node: ast.AST) -> list[str]:
    targets = getattr(node, "targets", [getattr(node, "target", None)])
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(item.id for item in target.elts if isinstance(item, ast.Name))
    return names


def _js_ts_symbols(path: Path, rel: str) -> list[SymbolRecord]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: list[SymbolRecord] = []
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        for pattern, kind in [
            (r"\bexport\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "function"),
            (r"\bexport\s+class\s+([A-Za-z_$][\w$]*)", "class"),
            (r"\bexport\s+const\s+([A-Za-z_$][\w$]*)\s*=", "function"),
        ]:
            match = re.search(pattern, stripped)
            if match:
                rows.append(SymbolRecord(match.group(1), kind, rel, line_no, detail=stripped[:160]))
        import_match = re.search(r"^\s*import\s+(.+?)\s+from\s+['\"](.+?)['\"]", line)
        if import_match:
            rows.append(SymbolRecord(import_match.group(2), "import", rel, line_no, detail=import_match.group(1)[:160]))
        test_match = re.search(r"\b(?:it|test|describe)\s*\(\s*['\"]([^'\"]+)['\"]", line)
        if test_match:
            rows.append(SymbolRecord(test_match.group(1), "test", rel, line_no))
    return rows


def _role_for(rel: str, path: Path) -> str:
    lowered = rel.lower()
    if any(token in lowered for token in TEST_PATTERNS):
        return "test"
    if path.name in CONFIG_NAMES or path.suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json"}:
        return "config"
    if path.suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if path.name in {"Makefile"} or path.suffix in {".lock"}:
        return "build"
    if path.suffix in SOURCE_SUFFIXES:
        return "source"
    return "unknown"


def _query_terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{1,}", query) if len(term) > 1]


def _counts_by_role(files: list[FileRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in files:
        counts[record.likely_role] = counts.get(record.likely_role, 0) + 1
    return counts


def _ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def _norm(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix()


def _unique(items: list[str]) -> list[str]:
    rows: list[str] = []
    for item in items:
        if item and item not in rows:
            rows.append(item)
    return rows
