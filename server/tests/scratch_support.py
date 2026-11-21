"""Test scratch: create outside the repository and clean up (proposals 2 and 7).

The incident began when two executable specs defaulted to ``server/.test-tmp-<number>``.
Every run outside FlowGate (manual pytest runs by people or AI) then created directories
inside the repository. Hidden in the UI, these were swept up by the final ``git add -A``,
and 261 artifacts reached main.

This module prevents both causes:

* Default to an external temporary directory. Continue honoring ``FLOWGATE_TEST_SCRATCH``;
  FlowGate creates and removes that directory through test_run_service.
* Clean up what we create. Earlier specs intentionally retained case directories because
  read-only Git objects prevented cleanup on Windows (proposal 7). An ``rmtree`` handler
  that restores write permission resolves that problem.
"""
from __future__ import annotations

import atexit
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def _clear_readonly(func, path, _exc):
    """rmtree onerror: make Git's read-only object file writable and retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        # Cleanup failure must not change the test result; leftovers are outside the repository.
        pass


def remove_tree(path) -> None:
    """Remove a tree even with read-only files (Windows Git objects).

    Python 3.12 deprecates ``onerror`` in favor of ``onexc``. The hooks differ only
    in their third argument, which our handler ignores, so select the hook name.
    """
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_clear_readonly)
    else:
        shutil.rmtree(str(path), onerror=_clear_readonly)


def session_scratch(name: str) -> Path:
    """Return the scratch root for this spec.

    Use ``FLOWGATE_TEST_SCRATCH`` when supplied (owned by FlowGate); otherwise
    create an external temporary directory and remove it at interpreter exit.
    """
    provided = os.environ.get("FLOWGATE_TEST_SCRATCH")
    if provided:
        root = Path(provided)
        root.mkdir(parents=True, exist_ok=True)
        return root
    root = Path(tempfile.mkdtemp(prefix=f"flowgate-{name}-"))
    atexit.register(remove_tree, root)
    return root
