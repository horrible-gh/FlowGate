"""Subreaper cleanup runs in a separate process; never adopt pytest's children."""
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import tr_self_check_supervisor as supervisor

SERVER_ROOT = Path(__file__).resolve().parents[3]


def test_non_linux_keeps_existing_path(monkeypatch):
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: pytest.fail("no prctl outside Linux"))
    assert supervisor._enable_subreaper() is False


@pytest.mark.parametrize("failure", ["errno", "missing"])
def test_prctl_failure_keeps_existing_path(monkeypatch, failure):
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(supervisor, "_adopted_children", lambda: [])
    def library(*args, **kwargs):
        if failure == "missing":
            raise OSError("unavailable")
        return SimpleNamespace(prctl=lambda *args: -1)
    monkeypatch.setattr(ctypes, "CDLL", library)
    assert supervisor._enable_subreaper() is False


def test_prctl_arguments(monkeypatch):
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(supervisor, "_adopted_children", lambda: [])
    calls = []
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: SimpleNamespace(
        prctl=lambda *args: calls.append(args) or 0))
    assert supervisor._enable_subreaper() is True
    assert calls == [(36, 1, 0, 0, 0)]


def test_unreadable_children_cannot_prove_empty(monkeypatch):
    def unreadable():
        raise PermissionError("children unavailable")
    monkeypatch.setattr(supervisor, "_adopted_children", unreadable)
    assert supervisor._drain_adopted() is False


def test_live_child_cannot_prove_empty(monkeypatch):
    monkeypatch.setattr(supervisor, "_adopted_children", lambda: [123])
    monkeypatch.setattr(supervisor.os, "waitpid", lambda *a: (0, 0))
    sent = []
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    ticks = iter(range(20))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(ticks))
    assert supervisor._drain_adopted() is False
    assert sent == [(123, signal.SIGTERM), (123, signal.SIGKILL)]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper")
@pytest.mark.parametrize("ending", ["exit", "cancel", "server_lost", "timeout"])
@pytest.mark.parametrize("topology", ["detached", "group"])
def test_descendant_is_killed_and_reaped(tmp_path, ending, topology):
    ready = tmp_path / "daemon_pid"
    target = '''
import os, signal, sys, time
pid = os.fork()
if pid == 0:
    if sys.argv[3] == "detached":
        os.setsid()
        if os.fork():
            os._exit(0)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(sys.argv[1] + ".tmp", "w") as f:
        f.write(str(os.getpid()))
    os.replace(sys.argv[1] + ".tmp", sys.argv[1])
    while True:
        time.sleep(1)
while not os.path.exists(sys.argv[1]):
    time.sleep(.01)
if sys.argv[2] != "exit":
    time.sleep(30)
'''
    payload = {"argv": [sys.executable, "-c", target, str(ready), ending, topology],
               "cwd": os.getcwd(), "env": dict(os.environ), "timeout_seconds": 2}
    proc = subprocess.Popen([sys.executable, "-m", supervisor.__name__],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=SERVER_ROOT)
    daemon = None
    try:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 5
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        if proc.poll() is not None and not ready.exists():
            out, err = proc.communicate(timeout=1)
            pytest.fail(f"supervisor exited before readiness: {proc.returncode}; {out}; {err}")
        assert ready.exists(), "target did not become ready within 5 seconds"
        cleanup_started = time.monotonic()
        daemon = int(ready.read_text())
        if ending == "cancel":
            proc.stdin.write("cancel\n")
            proc.stdin.flush()
        elif ending == "server_lost":
            proc.stdin.close()
            proc.stdin = None
        proc.wait(timeout=12)
        elapsed = time.monotonic() - cleanup_started
        if topology == "group":
            assert elapsed < (4.5 if ending == "timeout" else 2.5), elapsed
        out, err = proc.communicate(timeout=1)
        assert proc.returncode == 0, err
        frames = [json.loads(line) for line in out.splitlines()]
        assert frames[0]["event"] == "started"
        result = frames[-1]
        assert result["event"] == "result"
        assert result["subreaper_enabled"] is True
        assert result["children_empty"] is True
        assert result["timed_out"] is (ending == "timeout")
        assert result["cancelled"] is (ending == "cancel")
        assert not os.path.exists(f"/proc/{daemon}"), "daemon must be reaped before supervisor exits"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        if daemon is not None:
            try:
                os.kill(daemon, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_unavailable_children_disables_adoption_before_prctl(monkeypatch, error):
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform="linux"))
    def unavailable():
        raise error("children unavailable")
    monkeypatch.setattr(supervisor, "_adopted_children", unavailable)
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: pytest.fail("must not enable adoption"))
    assert supervisor._enable_subreaper() is False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux capability fallback")
@pytest.mark.parametrize("failure", ["children", "prctl", "non_linux"])
def test_fallback_supervisor_exits(tmp_path, failure):
    wrapper = '''
import ctypes, sys
from types import SimpleNamespace
from modules.flow_gate.services import tr_self_check_supervisor as s
if sys.argv[1] == "children":
    def unavailable():
        raise FileNotFoundError("CONFIG_PROC_CHILDREN unavailable")
    s._adopted_children = unavailable
elif sys.argv[1] == "prctl":
    ctypes.CDLL = lambda *a, **k: SimpleNamespace(prctl=lambda *a: -1)
else:
    sys.platform = "darwin"
s.main()
'''
    payload = {"argv": [sys.executable, "-c", "print('fallback-ok')"],
               "cwd": str(tmp_path), "env": dict(os.environ), "timeout_seconds": 2}
    proc = subprocess.Popen([sys.executable, "-c", wrapper, failure], cwd=SERVER_ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(json.dumps(payload) + "\n", timeout=5)
        assert proc.returncode == 0, err
        result = [json.loads(line) for line in out.splitlines()][-1]
        assert result["event"] == "result"
        assert "subreaper_enabled" not in result
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
