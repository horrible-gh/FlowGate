"""flowgate.default.0329 R0001/NR0003: prevent duplicate top-level definitions.

The Open Changes crash came from two read_group_file_diff definitions in
git_service.py. Python silently replaces the first definition with the second:
imports and syntax checks succeed, and even tests intended for either definition
can pass unless they directly invoke the overwritten one.

A conflict-free merge can still leave duplicate functions, so reviewing only
conflicted files cannot catch this. Scan the complete server source/test tree
with AST and fail on duplicate names within the same scope.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]

# 0394 T0016 item 4 (NR0003 5.3): the same-scope unique-name rule applies
# throughout the server, but the guard previously scanned only modules/ and
# tests/. It never inspected root app.py/startup.py/config.py or routers/,
# util/, and tools/. Expand to the entire server tree (403 -> 433 files).
# The expanded scan found zero current violations.
#
# Exclude only non-source trees: virtual environments, caches, and runtime
# artifacts. These are outside the target scope, not rule exemptions.
_SKIP_DIRS = frozenset({"__pycache__", ".venv", "venv", "node_modules", "storage", "logs"})

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFINITION_NODES = (*_DEF_NODES, ast.ClassDef)

# The only legitimate repeated-name syntax: property setters/deleters and
# typing.overload stubs. Exclude definitions with these decorators.
_LEGIT_REDEFINITION_SUFFIXES = ("setter", "deleter", "getter", "overload")


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted(SERVER_DIR.rglob("*.py"))
        if not (_SKIP_DIRS & set(path.relative_to(SERVER_DIR).parts))
    ]


def _decorator_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _is_legit_redefinition(node: ast.AST) -> bool:
    return any(
        name in _LEGIT_REDEFINITION_SUFFIXES for name in _decorator_names(node)
    )


def _duplicates(body: list[ast.stmt], node_types: tuple[type, ...]) -> dict[str, list[int]]:
    """Map names defined more than once to the line numbers of their definitions."""
    lines: dict[str, list[int]] = {}
    for node in body:
        if not isinstance(node, node_types):
            continue
        if _is_legit_redefinition(node):
            continue
        lines.setdefault(node.name, []).append(node.lineno)
    return {name: at for name, at in lines.items() if len(at) > 1}


@pytest.fixture(scope="module")
def parsed_sources() -> list[tuple[Path, ast.Module]]:
    """Parse every server .py file with AST.

    Some real files start with a BOM (U+FEFF), so read utf-8-sig. Plain utf-8
    makes ast.parse fail on a non-printable character and could silently omit
    those files from the guard.
    """
    parsed: list[tuple[Path, ast.Module]] = []
    unreadable: list[str] = []
    for path in _python_files():
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8-sig"))))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unreadable.append(f"{path.relative_to(SERVER_DIR)}: {exc}")
    # Fail on parse errors; skipping them would open a coverage gap.
    assert unreadable == [], "이 파일들을 AST로 읽지 못해 중복 정의를 검사할 수 없다:\n" + "\n".join(
        unreadable
    )
    assert parsed, "검사 대상 파이썬 파일을 하나도 찾지 못했다 (server 트리 전체)"
    return parsed


def test_scan_actually_covers_git_service(parsed_sources):
    """Anchor coverage by checking that the affected files are actually scanned.

    Changed roots or empty rglob results would otherwise let both guards pass
    vacuously because there was nothing to inspect.
    """
    scanned = {path for path, _ in parsed_sources}
    git_service = SERVER_DIR / "modules" / "flow_gate" / "services" / "git_service.py"
    assert git_service in scanned

    tree = dict(parsed_sources)[git_service]
    top_level = [
        node.name for node in tree.body if isinstance(node, _DEFINITION_NODES)
    ]
    assert "read_group_file_diff" in top_level
    assert Counter(top_level)["read_group_file_diff"] == 1


def test_scan_reaches_outside_modules_and_tests(parsed_sources):
    """0394 T0016 item 4: keep the expanded scope expanded.

    The old modules/tests-only scope omitted root app.py/startup.py and all
    routers/util files. Pin collection of files outside those two roots so a
    narrowed scan cannot silently reopen that gap.
    """
    scanned = {path for path, _ in parsed_sources}
    for relative in ("app.py", "startup.py", "config.py"):
        assert SERVER_DIR / relative in scanned, f"{relative} 이 검사 범위에서 빠졌다"
    outside = [
        path for path in scanned
        if path.relative_to(SERVER_DIR).parts[0] not in {"modules", "tests"}
    ]
    assert len(outside) >= 20, (
        "modules/ · tests/ 밖의 파이썬 파일이 거의 수집되지 않았다 — 범위가 다시 좁아졌다: "
        f"{len(outside)}개"
    )


def test_no_duplicate_top_level_definitions(parsed_sources):
    """Only the last same-named def/class survives at module scope."""
    offenders: list[str] = []
    for path, tree in parsed_sources:
        for name, at in _duplicates(tree.body, _DEFINITION_NODES).items():
            offenders.append(
                f"{path.relative_to(SERVER_DIR).as_posix()}: {name} @ lines {at}"
            )
    assert offenders == [], (
        "같은 모듈에 같은 이름의 top-level 정의가 여러 개 있다 — 마지막 정의가 앞의 "
        "정의를 조용히 덮어쓴다. 쓰이지 않는 쪽을 삭제하거나 이름을 바꿔라:\n"
        + "\n".join(offenders)
    )


def test_no_duplicate_methods_within_a_class(parsed_sources):
    """The same shadowing occurs in class bodies, including test classes."""
    offenders: list[str] = []
    for path, tree in parsed_sources:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for name, at in _duplicates(node.body, _DEF_NODES).items():
                offenders.append(
                    f"{path.relative_to(SERVER_DIR).as_posix()}: "
                    f"{node.name}.{name} @ lines {at}"
                )
    assert offenders == [], (
        "한 클래스 안에 같은 이름의 메서드가 여러 개 있다 — 뒤엣것만 살아남고 앞의 "
        "메서드(및 그것을 겨냥한 테스트)는 실행되지 않는다:\n" + "\n".join(offenders)
    )
