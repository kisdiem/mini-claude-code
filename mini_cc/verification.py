from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class VerificationCandidate:
    command: str
    reason: str
    confidence: float
    scope: str
    kind: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def discover_verification_candidates(
    workspace: Path,
    modified_files: list[str] | None = None,
) -> list[VerificationCandidate]:
    root = workspace.expanduser().resolve()
    modified = [path.replace("\\", "/") for path in (modified_files or [])]
    candidates: list[VerificationCandidate] = []

    candidates.extend(_python_candidates(root, modified))
    candidates.extend(_node_candidates(root))
    candidates.extend(_go_rust_java_candidates(root))

    candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
    return _dedupe_candidates(candidates)


def best_verification_command(
    workspace: Path,
    modified_files: list[str] | None = None,
    explicit: str | None = None,
) -> str | None:
    if explicit:
        return explicit
    candidates = discover_verification_candidates(workspace, modified_files)
    return candidates[0].command if candidates else None


def _python_candidates(root: Path, modified: list[str]) -> list[VerificationCandidate]:
    candidates: list[VerificationCandidate] = []
    tests_dir = root / "tests"
    pytest_signal = _has_pytest_signal(root)
    unittest_signal = (
        (tests_dir.exists() and _has_unittest_style_tests(tests_dir))
        or _has_unittest_style_root_tests(root)
    )

    for path in modified:
        target = _targeted_pytest_path(root, path)
        if target is not None and pytest_signal:
            candidates.append(
                VerificationCandidate(
                    command=f"python -m pytest {target.as_posix()}",
                    reason=f"targeted pytest for modified file {path}",
                    confidence=0.95,
                    scope="targeted",
                    kind="test",
                )
            )

    if pytest_signal:
        candidates.append(
            VerificationCandidate(
                command="python -m pytest",
                reason="pytest configuration or tests directory detected",
                confidence=0.88,
                scope="project",
                kind="test",
            )
        )

    if unittest_signal:
        candidates.append(
            VerificationCandidate(
                command="python -m unittest discover",
                reason="unittest-style tests detected",
                confidence=0.9,
                scope="project",
                kind="test",
            )
        )
    elif (root / "setup.py").exists():
        candidates.append(
            VerificationCandidate(
                command="python -m unittest discover",
                reason="setup.py detected; unittest discover is a conservative fallback",
                confidence=0.55,
                scope="fallback",
                kind="test",
            )
        )

    return candidates


def _node_candidates(root: Path) -> list[VerificationCandidate]:
    package_json = root / "package.json"
    if not package_json.exists():
        return []
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    scripts = payload.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}

    runner = _node_runner(root)
    specs = [
        ("test", f"{runner} test", "test", 0.86),
        ("lint", f"{runner} run lint", "lint", 0.74),
        ("typecheck", f"{runner} run typecheck", "typecheck", 0.72),
        ("build", f"{runner} run build", "build", 0.66),
    ]
    candidates: list[VerificationCandidate] = []
    for script_name, command, kind, confidence in specs:
        if script_name in scripts:
            candidates.append(
                VerificationCandidate(
                    command=command,
                    reason=f"package.json script `{script_name}` detected",
                    confidence=confidence,
                    scope="project",
                    kind=kind,
                )
            )
    return candidates


def _go_rust_java_candidates(root: Path) -> list[VerificationCandidate]:
    candidates: list[VerificationCandidate] = []
    if (root / "go.mod").exists():
        candidates.append(
            VerificationCandidate("go test ./...", "go.mod detected", 0.88, "project", "test")
        )
    if (root / "Cargo.toml").exists():
        candidates.append(
            VerificationCandidate("cargo test", "Cargo.toml detected", 0.86, "project", "test")
        )
        candidates.append(
            VerificationCandidate("cargo check", "Cargo.toml detected", 0.72, "project", "typecheck")
        )
    if (root / "pom.xml").exists():
        candidates.append(
            VerificationCandidate("mvn test", "pom.xml detected", 0.84, "project", "test")
        )
    if (root / "gradlew").exists() or (root / "gradlew.bat").exists() or (root / "build.gradle").exists():
        candidates.append(
            VerificationCandidate("./gradlew test", "Gradle project detected", 0.82, "project", "test")
        )
    return candidates


def _has_pytest_signal(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    pyproject_text = ""
    if pyproject.exists():
        try:
            pyproject_text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            pyproject_text = ""
    return (
        (root / "pytest.ini").exists()
        or (root / "tox.ini").exists()
        or (root / "setup.cfg").exists()
        or "pytest" in pyproject_text
        or "[tool.pytest" in pyproject_text
        or (root / "tests").exists()
        or any(root.glob("test*.py"))
    )


def _has_unittest_style_tests(tests_dir: Path) -> bool:
    inspected = 0
    for path in tests_dir.rglob("test*.py"):
        inspected += 1
        if inspected > 40:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "unittest.TestCase" in text or "import unittest" in text or "from unittest" in text:
            return True
    return False


def _has_unittest_style_root_tests(root: Path) -> bool:
    inspected = 0
    for path in root.glob("test*.py"):
        inspected += 1
        if inspected > 20:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "unittest.TestCase" in text or "import unittest" in text or "from unittest" in text:
            return True
    return False


def _targeted_pytest_path(root: Path, modified_file: str) -> Path | None:
    path = Path(modified_file.replace("\\", "/"))
    if not path.suffix == ".py":
        return None
    if path.as_posix().startswith("tests/") or path.name.startswith("test_"):
        target = root / path
        return path if target.exists() else None

    stem = path.stem
    candidates = [
        Path("tests") / f"test_{stem}.py",
        Path("tests") / path.parent / f"test_{stem}.py",
        path.parent / f"test_{stem}.py",
    ]
    for candidate in candidates:
        if (root / candidate).exists():
            return candidate
    return None


def _node_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _dedupe_candidates(candidates: list[VerificationCandidate]) -> list[VerificationCandidate]:
    seen: set[str] = set()
    result: list[VerificationCandidate] = []
    for candidate in candidates:
        if candidate.command in seen:
            continue
        seen.add(candidate.command)
        result.append(candidate)
    return result
