"""테스트가 저장소를 오염시키지 못하게 하는 정적 가드 (0382 B0001 / NR0003 제안 2-c).

0382 의 사고는 실행 시점에는 아무 신호도 내지 않았다. 두 스펙이 스크래치 기본값을
``server/.test-tmp-<번호>`` 로 두었고, 그 폴더는 화면이 감추는 종류라 261개가 남의
커밋에 실려 main 에 들어갈 때까지 아무도 못 봤다.

conftest 의 세션 감시자(제안 2-b)는 **이미 벌어진** 오염을 잡는다. 이 스펙은 그보다
한 걸음 앞에서, 소스를 읽어 "저장소 경로 아래에 폴더를 만들거나 파일을 쓰는" 테스트를
잡는다. 같은 방식의 선례가 이 저장소에 이미 있다 — ``test_event_loop_blocking_0279``
는 소스를 읽어 규칙 위반을 잡는다.

판정은 보수적이다. ``__file__`` 에서 유도된 이름(그리고 그 이름에서 다시 유도된
이름)에 대해서만 쓰기 호출을 본다. 읽기는 통과한다 — 저장소 파일을 읽는 스펙은
정상이고, 실제로 여럿 있다.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[2]
_TESTS_DIR = _SERVER_DIR / "tests"
_REPO_ROOT = _SERVER_DIR.parent

# 경로 객체에 대고 부르면 디스크에 쓰는 메서드.
_WRITING_METHODS = frozenset({
    "mkdir", "touch", "write_text", "write_bytes", "rename", "replace", "unlink", "rmdir",
})
# 첫 인자로 경로를 받아 디스크에 쓰는 함수(모듈 접두어는 보지 않는다).
_WRITING_FUNCS = frozenset({"makedirs", "mkdir", "copytree", "copyfile", "copy", "copy2", "move"})
_WRITE_MODES = ("w", "a", "x", "+")


# pytest 가 실제로 수집·임포트하는 파일들. 여기가 이 가드의 범위다.
#
# 손으로 돌리는 시나리오 준비 스크립트(예: seed_ts004.py)는 일부러 뺐다. 그런 스크립트는
# 저장소 안 정해진 자리에 픽스처를 만드는 것이 목적이라 이 규칙과 목적이 어긋나고,
# pytest 세션 중에 돌면 conftest 의 실행 시점 감시자(제안 2-b)가 어차피 잡는다.
_SCANNED_EXTRA = ("conftest.py", "scratch_support.py")


def _python_sources() -> list[Path]:
    collected = {p for p in _TESTS_DIR.rglob("test_*.py") if p.is_file()}
    for name in _SCANNED_EXTRA:
        path = _TESTS_DIR / name
        if path.is_file():
            collected.add(path)
    return sorted(collected)


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _scope_nodes(tree):
    """Walk one lexical scope; a different function's locals are unrelated."""
    yield tree
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from _scope_nodes(child)


def _derives_from_file(expr: ast.AST, known: set[str]) -> bool:
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr in {"read_text", "read_bytes", "read", "readline", "readlines"}:
            return False  # file contents are values, not filesystem locations
    if isinstance(expr, ast.Name):
        return expr.id == "__file__" or expr.id in known
    return any(_derives_from_file(child, known) for child in ast.iter_child_nodes(expr))


def _repo_derived_names(tree: ast.AST, inherited=None) -> set[str]:
    nodes = list(_scope_nodes(tree))
    known = set(inherited or ())
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Python locals (including parameters) shadow names in the outer scope.
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                known.discard(node.id)
        for arg in (*tree.args.posonlyargs, *tree.args.args, *tree.args.kwonlyargs):
            known.discard(arg.arg)
        for arg in (tree.args.vararg, tree.args.kwarg):
            if arg is not None:
                known.discard(arg.arg)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            value, targets = None, []
            if isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign):
                value, targets = node.value, [node.target]
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                value, targets = node.iter, [node.target]
            if value is None or not _derives_from_file(value, known):
                continue
            for target in targets:
                for name in _target_names(target):
                    if name not in known:
                        known.add(name)
                        changed = True
    return known


def _root_name(expr: ast.AST) -> str | None:
    """``A / "b" / "c"`` 나 ``A.parent`` 의 뿌리 이름."""
    node = expr
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "read_text", "read_bytes", "read", "readline", "readlines"}:
                return None
            node = node.func
        else:
            return None


def _is_write_mode(call: ast.Call, *, method=False) -> bool:
    args = list(call.args[0 if method else 1:]) + [kw.value for kw in call.keywords if kw.arg == "mode"]
    for arg in args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if any(flag in arg.value for flag in _WRITE_MODES):
                return True
    return False


def _violations(source: str, known_extra: set[str] | None = None) -> list[str]:
    found: list[str] = []

    def scan(tree, inherited):
        known = _repo_derived_names(tree, inherited)
        for node in _scope_nodes(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _WRITING_METHODS:
                if _root_name(func.value) in known:
                    found.append(f"line {node.lineno}: .{func.attr}() on a repo path")
            elif isinstance(func, ast.Attribute) and func.attr == "open" and _is_write_mode(node, method=True):
                if _root_name(func.value) in known:
                    found.append(f"line {node.lineno}: .open(write mode) on a repo path")
            elif isinstance(func, ast.Attribute) and func.attr in _WRITING_FUNCS:
                # copy reads src and writes dst; move mutates both paths.
                positions = (0, 1) if func.attr == "move" else (
                    (1,) if func.attr in {"copy", "copy2", "copyfile", "copytree"} else (0,))
                targets = [node.args[i] for i in positions if len(node.args) > i]
                targets += [kw.value for kw in node.keywords if kw.arg in ("dst", "name", "path")]
                if any(_root_name(target) in known for target in targets):
                    found.append(f"line {node.lineno}: {func.attr}() on a repo path")
            elif isinstance(func, ast.Name) and func.id == "open" and _is_write_mode(node):
                if node.args and _root_name(node.args[0]) in known:
                    found.append(f"line {node.lineno}: open(write mode) on a repo path")
        # Inspect each nested scope separately, including definitions under if/try.
        def nested(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    scan(child, known)
                else:
                    nested(child)
        nested(tree)

    scan(ast.parse(source), known_extra or set())
    return found


# ── TC1 ───────────────────────────────────────────────────────────────────────

def test_no_test_writes_inside_the_repository():
    offenders: dict[str, list[str]] = {}
    for path in _python_sources():
        # utf-8-sig: at least one spec in this tree carries a BOM, and ast.parse
        # rejects it. A crash here would read as "the guard is broken", not as a
        # finding — the guard has to survive the tree it inspects.
        hits = _violations(path.read_text(encoding="utf-8-sig"))
        if hits:
            offenders[str(path.relative_to(_REPO_ROOT)).replace("\\", "/")] = hits
    assert not offenders, (
        "테스트가 저장소 경로 아래에 쓰고 있습니다 (0382 B0001 재발 방지).\n"
        + "\n".join(f"  {name}: {', '.join(hits)}" for name, hits in offenders.items())
        + "\n스크래치는 scratch_support.session_scratch() 로 저장소 밖에 만드십시오."
    )


# ── TC2: 검출기 자체가 살아 있는가 (가드가 조용히 죽지 않게) ─────────────────

def test_detector_flags_a_repo_write():
    source = (
        "from pathlib import Path\n"
        "_ROOT = Path(__file__).resolve().parents[1]\n"
        "_SCRATCH = _ROOT / '.test-tmp-9999'\n"
        "_SCRATCH.mkdir(parents=True, exist_ok=True)\n"
    )
    assert _violations(source), "검출기가 전형적인 위반을 못 잡으면 TC1 은 늘 초록이다"


def test_detector_allows_reading_repo_files():
    source = (
        "from pathlib import Path\n"
        "_ROOT = Path(__file__).resolve().parents[1]\n"
        "text = (_ROOT / 'README.md').read_text(encoding='utf-8')\n"
        "files = sorted((_ROOT / 'sql').glob('*.sql'))\n"
    )
    assert _violations(source) == []


# ── TC3: 그때그때 확인하려고 쓴 스크립트가 커밋돼 있지 않은가 ────────────────

def test_no_ad_hoc_scratch_scripts_committed():
    strays = sorted(
        str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
        for path in _TESTS_DIR.rglob("*.py")
        if path.name.startswith(("_tmp_", "_scratch_", "tmp_"))
    )
    assert not strays, (
        "그때그때 확인하려고 쓴 스크립트가 저장소에 남아 있습니다: " + ", ".join(strays)
    )


# ── TC4: 두 번째·세 번째 층이 실제로 걸려 있는가 ────────────────────────────

def test_gitignore_covers_test_scratch():
    text = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".test-tmp-*/" in text


def test_conftest_installs_the_pollution_guard():
    text = (_TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def repo_pollution_guard" in text
    assert 'scope="session", autouse=True' in text


def test_pollution_guard_fails_a_session_that_leaves_ignored_scratch(tmp_path):
    """문자열 존재가 아니라 실제 pytest 종료 실패까지 고정한다 (0382 review)."""
    repo = tmp_path / "repo"
    tests_dir = repo / "server" / "tests"
    tests_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".test-tmp-*/\n", encoding="utf-8")
    (tests_dir / "conftest.py").write_text(
        (_TESTS_DIR / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tests_dir / "test_leak.py").write_text(
        "from pathlib import Path\n"
        "def test_leaks_ignored_scratch():\n"
        "    root = Path(__file__).resolve().parents[2]\n"
        "    leak = root / '.test-tmp-9999'\n"
        "    leak.mkdir()\n"
        "    (leak / 'left.txt').write_text('x', encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    # This child is intentionally a minimal synthetic repository: it has no API source
    # tree because the only contract under test is the pollution guard. Do not inherit
    # the parent release gate, or capability validation masks the guard failure this
    # subprocess exists to assert.
    env.pop("FLOWGATE_REQUIRE_FULL_SUITE", None)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # The assertion below checks Korean text. Windows redirected stdout otherwise
    # escapes it to ASCII under a non-Korean locale, even though the guard fired.
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_leak.py", "-q"],
        cwd=str(tests_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )

    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0
    assert "테스트가 저장소 안에 파일을 남겼습니다" in output
    assert ".test-tmp-9999" in output


@pytest.mark.parametrize("spec", [
    "test_empty_remote_bootstrap_0313.py",
    "test_base_branch_absent_bootstrap_0318.py",
    "test_t509_src_content_routes.py",
])
def test_known_offenders_now_use_the_shared_scratch(spec):
    """이 세 스펙이 이번 사고의 출혈 지점이었다 — 되돌아가면 여기서 걸린다."""
    matches = [path for path in _TESTS_DIR.rglob(spec) if path.is_file()]
    assert len(matches) == 1, f"{spec}: expected exactly one file under tests, found {len(matches)}"
    text = matches[0].read_text(encoding="utf-8-sig")
    assert "session_scratch(" in text
    # 주석 줄은 뺀다 — 그 안에는 "예전 기본값이 무엇이었는지"가 일부러 적혀 있다.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert ".test-tmp-" not in code
    assert "_scratch_t509_src" not in code


@pytest.mark.parametrize("source", [
    "ROOT = Path(__file__).parent\n(ROOT / 'x').read_text().replace('a', 'b')\n",
    "ROOT = Path(__file__).parent\ntext = (ROOT / 'x').read_text()\ntext.replace('a', 'b')\n",
    "ROOT = Path(__file__).parent\nshutil.copyfile(ROOT / 'x', tmp_path / 'x')\n",
    "ROOT = Path(__file__).parent\ndef read():\n    path = ROOT / 'x'\n    return path.read_text()\ndef write(tmp_path):\n    path = tmp_path / 'x'\n    path.write_text('ok')\n",
    "ROOT = Path(__file__).parent\ndef write(ROOT):\n    ROOT.mkdir()\n",
])
def test_detector_keeps_contents_copy_sources_and_local_scopes_separate(source):
    assert _violations(source) == []


@pytest.mark.parametrize("operation", [
    "ROOT.mkdir()", "ROOT.write_text('x')", "ROOT.open('w')",
    "shutil.copyfile(tmp_path / 'x', ROOT / 'x')",
    "shutil.copyfile(src=tmp_path / 'x', dst=ROOT / 'x')",
    "shutil.move(ROOT / 'x', tmp_path / 'x')",
    "open(ROOT / 'x', 'w')",
])
def test_detector_still_rejects_repository_mutations(operation):
    assert _violations("ROOT = Path(__file__).parent\ndef mutate(tmp_path):\n    " + operation + "\n")
