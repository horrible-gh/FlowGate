"""Dependency direction between ``flowgate`` and ``modules.flow_gate`` (flowgate.v02.0032 T#1, D0006 §2.4, §4.1).

* ``modules.flow_gate`` never imports the v0.2 application package (it reaches it only
  through the ``chat_run_hooks`` ports registered by the composition root);
* ``import flowgate`` resolves to ``server/flowgate`` -- not to anything under tests/
  (the test package layout must not shadow the application package).

Pure text / import checks: fast tier.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

SERVER_DIR = Path(__file__).resolve().parents[2]
MODULES_DIR = SERVER_DIR / "modules" / "flow_gate"
FLOWGATE_DIR = SERVER_DIR / "flowgate"
TESTS_DIR = SERVER_DIR / "tests"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

_IMPORT_TEXT = re.compile(r"^\s*(?:import\s+flowgate\b|from\s+flowgate(?:\.|\s+import\b))", re.MULTILINE)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_modules_flow_gate_never_imports_flowgate_by_text():
    offenders = []
    for path in _python_files(MODULES_DIR):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in _IMPORT_TEXT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(SERVER_DIR).as_posix()}:{line}: {match.group(0).strip()}")
    assert offenders == [], "modules.flow_gate must not import flowgate:\n" + "\n".join(offenders)


def test_modules_flow_gate_never_imports_flowgate_by_ast():
    """The same rule on the parsed import graph (catches `import flowgate.x as y` too)."""
    offenders = []
    for path in _python_files(MODULES_DIR):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(name == "flowgate" or name.startswith("flowgate.") for name in names):
                offenders.append(f"{path.relative_to(SERVER_DIR).as_posix()}:{node.lineno}")
    assert offenders == []


def test_the_scan_really_covers_modules_flow_gate():
    files = _python_files(MODULES_DIR)
    assert len(files) > 200
    assert MODULES_DIR / "services" / "chat_run_hooks.py" in files
    assert MODULES_DIR / "services" / "ai_invoke" / "worker.py" in files


def test_flowgate_resolves_to_the_application_package():
    import flowgate
    import flowgate.conversation

    assert Path(flowgate.__file__).resolve().parent == FLOWGATE_DIR.resolve()
    assert Path(flowgate.conversation.__file__).resolve().parent == (FLOWGATE_DIR / "conversation").resolve()


def test_tests_root_has_no_flat_flowgate_module_or_package_initializer():
    assert not (TESTS_DIR / "flowgate.py").exists()
    assert not (TESTS_DIR / "__init__.py").exists(), "tests/ must stay a flat rootdir (prepend import mode)"


def test_flowgate_has_no_version_namespace_and_the_designed_modules():
    assert not (FLOWGATE_DIR / "v02").exists()
    expected = {"__init__", "contracts", "routes", "chats", "commands", "command_policy", "activity",
                "run_changes", "repository", "integration", "legacy_adapter"}
    present = {p.stem for p in (FLOWGATE_DIR / "conversation").glob("*.py")}
    assert expected <= present


def test_flowgate_reaches_modules_flow_gate_mostly_through_legacy_adapter():
    """Only the adapter, the storage layer (DB connection) and the hook registration in
    integration import modules.flow_gate at module level."""
    allowed = {"legacy_adapter.py", "repository.py", "integration.py"}
    offenders = []
    for path in _python_files(FLOWGATE_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("modules.") \
                    and path.name not in allowed:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_chat_run_hooks_imports_only_the_standard_library():
    tree = ast.parse((MODULES_DIR / "services" / "chat_run_hooks.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "logging", "threading", "typing"}
