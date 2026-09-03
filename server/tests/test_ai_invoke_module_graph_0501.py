"""flowgate.default.0501 T5: ai_invoke module graph is normal Python, no exec() assembly.

Locks T0012 (T5)'s two hard acceptance items that have no other regression test:

  * loader removal (T0012 §10/§21): `_load_parts`, `_PART_FILES` and
    `exec(code, globals())` must be gone from the ai_invoke architecture -- a part
    file executed into ai_invoke_service.py's own globals() dict, rather than
    normally imported, is exactly the transitional structure this T retires.
  * fresh-process import (T0012 §20): the six-module graph
    (ai_invoke_service / ai_invoke_provider_api / ai_invoke_provider_cli /
    ai_invoke_worker / ai_invoke_chain / ai_invoke_review) must import cleanly with
    no circular-import error, in a FRESH interpreter (not just this test process's
    already-warm import cache), regardless of which of the six is imported first.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"

_GRAPH_MODULES = (
    "ai_invoke_service",
    "ai_invoke_provider_api",
    "ai_invoke_provider_cli",
    "ai_invoke_worker",
    "ai_invoke_chain",
    "ai_invoke_review",
)


def test_ai_invoke_part3_chain_no_longer_exists():
    assert not (_SERVICES_DIR / "ai_invoke_part3_chain.py").exists()
    assert not (_SERVICES_DIR / "ai_invoke_part2_worker.py").exists()


def test_the_six_module_files_exist():
    for name in _GRAPH_MODULES:
        assert (_SERVICES_DIR / f"{name}.py").is_file(), name


def test_loader_removed_from_every_ai_invoke_module():
    """T0012 §21: neither `_load_parts`/`_PART_FILES` nor a bare `exec(` assembling one
    module's source into another's globals() may remain anywhere in the architecture."""
    for name in _GRAPH_MODULES:
        source = (_SERVICES_DIR / f"{name}.py").read_text(encoding="utf-8")
        assert "_load_parts" not in source, f"{name}.py still references _load_parts"
        assert "_PART_FILES" not in source, f"{name}.py still references _PART_FILES"
        assert "exec(code, globals())" not in source, (
            f"{name}.py still assembles source via exec(code, globals())"
        )


def _run_fresh_import(entry_module: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("TESTING", "1")
    env.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
    env.setdefault("ALLOWED_ORIGIN", "http://localhost")
    env.setdefault("CONTEXT", "/flowgate")
    env.setdefault("DB_TYPE", "sqlite")
    code = (
        "import sys; sys.path.insert(0, r'%s'); "
        "import modules.flow_gate.services.%s" % (str(_SERVER_DIR), entry_module)
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_SERVER_DIR), env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("entry_module", _GRAPH_MODULES)
def test_fresh_process_import_has_no_circular_import_error(entry_module):
    """T0012 §20: a fresh interpreter must be able to import ANY one of the six modules
    first (not just ai_invoke_service, which every other production caller already
    imports first in practice) and have the rest of the graph load behind it cleanly."""
    result = _run_fresh_import(entry_module)
    assert result.returncode == 0, (
        f"fresh-process import of {entry_module} failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
