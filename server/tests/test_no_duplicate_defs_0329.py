"""flowgate.default.0329 R0001/NR0003 — 중복 top-level 정의 재발 방지 가드.

[변경사항 열기]가 크래시한 근본 원인은 `git_service.py` 안에 `read_group_file_diff`가
두 번 정의되어 있었던 것이다. 파이썬은 뒤에 오는 정의로 앞의 정의를 조용히 덮어쓰므로
- import도 성공하고
- 구문 오류도 없고
- 두 정의 각각을 겨냥한 테스트조차(먼저 정의된 쪽을 직접 호출하지 않는 한) 통과한다.

병합(merge)이 충돌 없이 깨끗하게 끝나도 같은 이름의 함수가 두 벌 남을 수 있기 때문에
"충돌 표시된 파일만 확인"으로는 잡히지 않는다. 그래서 이 가드는 리뷰가 아니라 테스트로
둔다: 서버 소스/테스트 트리 전체를 AST로 훑어 같은 스코프에 같은 이름이 두 번 정의되면
그 자리에서 실패시킨다.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("modules", "tests")

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFINITION_NODES = (*_DEF_NODES, ast.ClassDef)

# 같은 이름이 여러 번 나오는 것이 정상인 유일한 문법 패턴들 — property setter/deleter와
# typing.overload 스텁. 이 데코레이터가 붙은 정의는 중복 집계에서 제외한다.
_LEGIT_REDEFINITION_SUFFIXES = ("setter", "deleter", "getter", "overload")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        files.extend(sorted((SERVER_DIR / root).rglob("*.py")))
    return files


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
    """같은 이름으로 두 번 이상 정의된 것들 -> 그 정의들의 행 번호."""
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
    """서버 트리의 모든 .py를 AST로 읽는다.

    BOM(U+FEFF)으로 시작하는 파일이 실제로 몇 개 있어 utf-8-sig로 읽는다 — utf-8로
    읽으면 ast.parse가 non-printable character로 죽고, 그러면 그 파일은 이 가드의
    검사 대상에서 조용히 빠져버린다.
    """
    parsed: list[tuple[Path, ast.Module]] = []
    unreadable: list[str] = []
    for path in _python_files():
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8-sig"))))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unreadable.append(f"{path.relative_to(SERVER_DIR)}: {exc}")
    # 파싱 실패를 넘어가면 가드에 구멍이 생기므로 실패로 취급한다.
    assert unreadable == [], "이 파일들을 AST로 읽지 못해 중복 정의를 검사할 수 없다:\n" + "\n".join(
        unreadable
    )
    assert parsed, f"검사 대상 파이썬 파일을 하나도 찾지 못했다 (roots={SCAN_ROOTS})"
    return parsed


def test_scan_actually_covers_git_service(parsed_sources):
    """가드가 정작 문제의 파일을 보고 있는지부터 확인한다.

    경로 루트가 바뀌거나 rglob이 빈 결과를 내면 아래 두 테스트는 '검사할 게 없어서'
    통과한다. 그 무증상 통과를 막는 앵커다.
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


def test_no_duplicate_top_level_definitions(parsed_sources):
    """모듈 스코프에 같은 이름의 def/class가 두 번 나오면 뒤엣것만 살아남는다."""
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
    """클래스 본문 안에서도 같은 shadowing이 일어난다 (테스트 클래스 포함)."""
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
