"""Regression guard for group 0102 R0001 (candidate B — backstop).

Every uvicorn startup path must set a finite `timeout_graceful_shutdown`, so any
stuck in-flight request (not just SSE) cannot block shutdown indefinitely if the
cooperative app-level signal is ever bypassed. Also asserts the lifespan handler
wires the cooperative `shutdown_event` (candidate A — primary mechanism).

These are static source assertions: the four entry points are launch scripts /
CLI invocations that are awkward to exercise live, but trivial to drift. Pinning
the flag here keeps all paths consistent (NR0003 §4 candidate C).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _SERVER_DIR.parent


def _read(rel_path: str) -> str:
    return (_REPO_DIR / rel_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel_path", ["server/dev.py", "server/stg.py"])
def test_python_entrypoints_set_graceful_timeout(rel_path):
    src = _read(rel_path)
    assert "timeout_graceful_shutdown=3" in src, (
        f"{rel_path} must pass timeout_graceful_shutdown=3 to uvicorn.run "
        "(group 0102 R0001 backstop)"
    )


def test_run_bat_sets_graceful_timeout():
    src = _read("run.bat")
    assert "--timeout-graceful-shutdown 3" in src, (
        "run.bat must pass --timeout-graceful-shutdown 3 to uvicorn "
        "(group 0102 R0001 backstop)"
    )


def test_dockerfile_cmd_sets_graceful_timeout():
    src = _read("Dockerfile")
    # The flag and its value are separate CMD array tokens.
    assert '"--timeout-graceful-shutdown"' in src and '"3"' in src, (
        "Dockerfile CMD must pass --timeout-graceful-shutdown 3 to uvicorn "
        "(group 0102 R0001 backstop)"
    )


def test_lifespan_wires_cooperative_shutdown_event():
    src = _read("server/routers/main.py")
    assert "app.state.shutdown_event = asyncio.Event()" in src, (
        "lifespan must create the cooperative shutdown_event (candidate A)"
    )
    assert "app.state.shutdown_event.set()" in src, (
        "lifespan must set shutdown_event on shutdown so SSE streams stop"
    )
