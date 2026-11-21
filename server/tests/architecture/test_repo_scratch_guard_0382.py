"""Static repository-write guard (proposal 2-c).

The 0382 incident had no runtime signal: two specs defaulted scratch paths to
server/.test-tmp-<number>, hidden by the UI, and 261 artifacts reached main in
another person's commit before anyone noticed.

conftest's session watcher (proposal 2-b) detects pollution after it happens.
This guard catches tests that create directories or write files below a
repository-derived path by inspecting source first, following the existing
test_event_loop_blocking_0279 pattern.

Classification is conservative: inspect writes only through names derived
transitively from __file__. Reads are allowed; many legitimate specs read
repository files.
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

# Methods that write to disk when called on a path object.
_WRITING_METHODS = frozenset({
    "mkdir", "touch", "write_text", "write_bytes", "rename", "replace", "unlink", "rmdir",
})
# Functions taking a destination path as their first argument, ignoring module prefixes.
_WRITING_FUNCS = frozenset({"makedirs", "mkdir", "copytree", "copyfile", "copy", "copy2", "move"})
_WRITE_MODES = ("w", "a", "x", "+")


# Scope: files actually collected/imported by pytest.
#
# Exclude manually run scenario setup scripts such as seed_ts004.py:
# their purpose is to create fixtures at fixed repository paths, unlike tests.
# If run during pytest, conftest's runtime watcher (proposal 2-b) catches them.
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


def _derives_from_file(expr: ast.AST, known: set[str]) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and (node.id == "__file__" or node.id in known):
            return True
    return False


def _repo_derived_names(tree: ast.AST) -> set[str]:
    """Names bound to paths derived from __file__, closed transitively."""
    known: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value, targets = node.value, list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value, targets = node.value, [node.target]
            if value is None or not _derives_from_file(value, known):
                continue
            for target in targets:
                for name in _target_names(target):
                    if name not in known:
                        known.add(name)
                        changed = True
    return known


def _root_name(expr: ast.AST) -> str | None:
    """The root name of A / "b" / "c" or A.parent."""
    node = expr
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def _is_write_mode(call: ast.Call) -> bool:
    args = list(call.args[1:]) + [kw.value for kw in call.keywords if kw.arg == "mode"]
    for arg in args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if any(flag in arg.value for flag in _WRITE_MODES):
                return True
    return False


def _violations(source: str, known_extra: set[str] | None = None) -> list[str]:
    tree = ast.parse(source)
    known = _repo_derived_names(tree) | (known_extra or set())
    if not known:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WRITING_METHODS:
            if _root_name(func.value) in known:
                found.append(f"line {node.lineno}: .{func.attr}() on a repo path")
        elif isinstance(func, ast.Attribute) and func.attr == "open" and _is_write_mode(node):
            if _root_name(func.value) in known:
                found.append(f"line {node.lineno}: .open(write mode) on a repo path")
        elif isinstance(func, ast.Attribute) and func.attr in _WRITING_FUNCS:
            if node.args and _root_name(node.args[0]) in known:
                found.append(f"line {node.lineno}: {func.attr}() on a repo path")
        elif isinstance(func, ast.Name) and func.id == "open" and _is_write_mode(node):
            if node.args and _root_name(node.args[0]) in known:
                found.append(f"line {node.lineno}: open(write mode) on a repo path")
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


# TC2: the detector itself remains effective.

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


# TC3: ad-hoc investigation scripts are not committed.

def test_no_ad_hoc_scratch_scripts_committed():
    strays = sorted(
        str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
        for path in _TESTS_DIR.rglob("*.py")
        if path.name.startswith(("_tmp_", "_scratch_", "tmp_"))
    )
    assert not strays, (
        "그때그때 확인하려고 쓴 스크립트가 저장소에 남아 있습니다: " + ", ".join(strays)
    )


# TC4: the second and third protection layers are actually installed.

def test_gitignore_covers_test_scratch():
    text = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".test-tmp-*/" in text


def test_conftest_installs_the_pollution_guard():
    text = (_TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def repo_pollution_guard" in text
    assert 'scope="session", autouse=True' in text


def test_pollution_guard_fails_a_session_that_leaves_ignored_scratch(tmp_path):
    """Verify an actual failing pytest exit, beyond string presence (0382 review)."""
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
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_leak.py", "-q"],
        cwd=str(tests_dir),
        capture_output=True,
        text=True,
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
    """These three specs caused the incident; catch a return to those defaults."""
    matches = [path for path in _TESTS_DIR.rglob(spec) if path.is_file()]
    assert len(matches) == 1, f"{spec}: expected exactly one file under tests, found {len(matches)}"
    text = matches[0].read_text(encoding="utf-8-sig")
    assert "session_scratch(" in text
    # Exclude comments, which intentionally document the old defaults.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert ".test-tmp-" not in code
    assert "_scratch_t509_src" not in code
