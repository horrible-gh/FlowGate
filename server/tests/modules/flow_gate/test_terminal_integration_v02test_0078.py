"""Designer terminal -- integration of the server card and the screen card (0078 · 0007-T).

The card tests of "터미널 서버" drive the routes with a fake pseudo-terminal. This file checks the
joins those tests could not see:

* the real application mounts the agreed paths, and the group AI-run lease middleware does not
  refuse opening/closing a terminal while a card runs (0001-R §3.2-5: show the band, never block);
* a REAL shell on this host (POSIX pty, or ConPTY on Windows) through the real WebSocket relay:
  start folder, rows/columns before and after a resize, Hangul output, the busy frame, and that
  no process of the tree is left after a window close (WS disconnect), DELETE, or the idle
  timeout (shortened here);
* the start/end record keeps who/when/where and never what was typed or printed;
* the Windows ConPTY code path with a stand-in ``winpty``/``psutil`` (runs on every host).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import terminal_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import terminal_pty, terminal_sessions as ts

pytestmark = [pytest.mark.timeout(120), pytest.mark.standard]

ADMIN = {"user_id": "u_admin", "is_admin": True}
HANGUL = "\ud55c\uae00"            # two Hangul syllables, kept as escapes in source
SECRET_MARK = "typed-secret-7f3a"  # what an administrator typed; must never reach a record
_PWD = re.compile(r"PW((?:[A-Za-z]:\\|/)[^|]*)\|")


def _flat(text: str) -> str:
    return _ANSI.sub("", text).replace("\r", "").replace("\n", "")


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]")


# -- the real application ------------------------------------------------------------------


def _all_paths(app):
    found = []

    def walk(routes, prefix=""):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                walk(getattr(original, "routes", []), f"{prefix}{getattr(context, 'prefix', '') or ''}")
                continue
            path = getattr(route, "path", None)
            if path is not None:
                found.append((f"{prefix}{path}", type(route).__name__, frozenset(getattr(route, "methods", None) or ())))

    walk(app.routes)
    return found


def test_the_application_mounts_the_agreed_terminal_paths():
    from routers.main import app

    paths = {(path, kind): methods for path, kind, methods in _all_paths(app) if "/terminal" in path}
    assert "GET" in paths[("/flowgate/api/v1/terminal/status", "APIRoute")]
    assert "POST" in paths[("/flowgate/api/v1/terminal/sessions", "APIRoute")]
    assert "DELETE" in paths[("/flowgate/api/v1/terminal/sessions/{session_id}", "APIRoute")]
    assert ("/flowgate/api/v1/terminal/ws", "APIWebSocketRoute") in paths


def test_terminal_routes_are_not_group_writes_and_a_running_card_does_not_block_them(tmp_path, monkeypatch):
    from modules.flow_gate.db import group_ai_leases
    from modules.flow_gate.services import mutation_policy as policy

    for path, method in (("/flowgate/api/v1/terminal/sessions", "POST"),
                         ("/flowgate/api/v1/terminal/sessions/{session_id}", "DELETE")):
        resource, _reason = policy.classify_mutation_route(path, {method})
        assert resource != "group", path

    # Every group has a live AI-run lease: an ordinary group write would get 423 here.
    lease = {"group_id": "g1", "run_id": "run_x", "token_id": "tok", "action_scope": "edit"}
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: dict(lease, group_id=gid))
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(lease, group_id=gid))
    mgr = ts.TerminalManager(pty_factory=lambda *a: pytest.fail("no shell in this test"),
                             enabled=lambda: True, flow_resolver=lambda fid: ("proj", str(tmp_path)),
                             recorder=lambda *a, **k: None)
    monkeypatch.setattr(ts, "manager", mgr)
    app = FastAPI()
    app.add_middleware(policy.GroupMutationPolicyMiddleware)
    app.include_router(terminal_routes.router, prefix="/flowgate")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)
    created = client.post("/flowgate/api/v1/terminal/sessions", json={"flow_id": "g1", "cols": 80, "rows": 24})
    assert created.status_code == 201, created.text
    assert client.delete(f"/flowgate/api/v1/terminal/sessions/{created.json()['session_id']}").status_code == 204
    # The card_running probe itself reads the same lease the band is about.
    assert ts.card_running("g1") is True


# -- a real shell through the real relay ---------------------------------------------------


def _real_shell_available() -> str | None:
    if terminal_pty.IS_WINDOWS:
        try:
            import winpty  # noqa: F401
        except ImportError:
            return "pywinpty (ConPTY) is not installed on this Windows host"
        try:
            import psutil  # noqa: F401
        except ImportError:
            return "psutil is not installed on this Windows host"
        return None
    return None if hasattr(os, "openpty") else "no pty on this host"


_SKIP_REAL = _real_shell_available()
real_shell = pytest.mark.skipif(_SKIP_REAL is not None, reason=_SKIP_REAL or "")


def _tree(pid: int) -> list[int]:
    if terminal_pty.IS_WINDOWS:
        import psutil

        try:
            return [p.pid for p in psutil.Process(pid).children(recursive=True)]
        except psutil.Error:
            return []
    return terminal_pty.descendants(pid)


def _alive(pid: int) -> bool:
    if terminal_pty.IS_WINDOWS:
        import psutil

        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False
    return terminal_pty._alive(pid)


def _wait(predicate, timeout=10.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class Pump:
    """Reads server frames on a thread so every wait has a deadline."""

    def __init__(self, ws):
        self.ws = ws
        self.frames: "queue.Queue[dict | None]" = queue.Queue()
        self.seen: list[dict] = []
        self.text = ""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while True:
                self.frames.put(json.loads(self.ws.receive_text()))
        except Exception:  # noqa: BLE001 -- socket closed
            self.frames.put(None)

    def until(self, predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        while True:
            if any(predicate(f) for f in self.seen):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                frame = self.frames.get(timeout=remaining)
            except queue.Empty:
                return False
            if frame is None:
                return any(predicate(f) for f in self.seen)
            self.seen.append(frame)
            if frame.get("type") == "output":
                self.text += frame["data"]

    def output_has(self, needle, timeout=20.0):
        def check(_frame):
            return needle in _ANSI.sub("", self.text)
        return self.until(check, timeout)

    def exit_frame(self, timeout=20.0):
        self.until(lambda f: f.get("type") == "exit", timeout)
        return next((f for f in self.seen if f.get("type") == "exit"), None)


if terminal_pty.IS_WINDOWS:
    ENTER = "\r"
    SIZE_CMD = "Write-Output ('SZ'+$Host.UI.RawUI.WindowSize.Height+'x'+$Host.UI.RawUI.WindowSize.Width)"
    PWD_CMD = "Write-Output ('PW'+(Get-Location).Path+'|')"
    HANGUL_CMD = f"Write-Output ('{HANGUL}'+'X'+(1+1))"
    CHILD_CMD = 'cmd /c "ping -n 300 127.0.0.1 >NUL"'
else:
    ENTER = "\n"
    SIZE_CMD = "echo SZ$(stty size | tr ' ' x)"
    PWD_CMD = "echo PW$(pwd -P)'|'"
    HANGUL_CMD = f"printf '%s%s\\n' '{HANGUL}' X$((1+1))"
    CHILD_CMD = "sleep 300"


@pytest.fixture
def real(tmp_path, monkeypatch, caplog):
    from modules.flow_gate.db import workflow_events

    rows: list[dict] = []
    monkeypatch.setattr(workflow_events, "create", lambda data: rows.append(data) or data)
    folder = tmp_path / "wt"
    folder.mkdir()
    mgr = ts.TerminalManager(enabled=lambda: True, flow_resolver=lambda fid: ("proj", str(folder)),
                             card_probe=lambda fid: False, recorder=ts.record_event)
    mgr.watch_interval = 0.1
    monkeypatch.setattr(ts, "manager", mgr)
    app = FastAPI()
    app.include_router(terminal_routes.router)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    caplog.set_level(logging.INFO)
    state = types.SimpleNamespace(mgr=mgr, client=TestClient(app), folder=folder, rows=rows, caplog=caplog)
    yield state
    mgr.shutdown()


def _open(real, cols=90, rows=26):
    response = real.client.post("/api/v1/terminal/sessions", json={"flow_id": "g1", "cols": cols, "rows": rows})
    assert response.status_code == 201, response.text
    return response.json()


def _start_child(real, ws, pump, session_id):
    session = real.mgr.get(session_id)
    assert session is not None and session.pty is not None
    ws.send_text(json.dumps({"type": "input", "data": CHILD_CMD + ENTER}))
    assert pump.until(lambda f: f == {"type": "busy", "value": True}), pump.seen[-5:]
    shell = session.pty.pid
    assert _wait(lambda: _tree(shell)), "the command did not start below the shell"
    return [shell, *_tree(shell)]


@real_shell
def test_real_shell_folder_size_resize_hangul_and_busy(real):
    created = _open(real, cols=90, rows=26)
    assert created["cwd"] == str(real.folder)
    with real.client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        pump = Pump(ws)
        assert pump.until(lambda f: f.get("type") == "card_running")
        ws.send_text(json.dumps({"type": "input", "data": PWD_CMD + ENTER}))
        expected = os.path.realpath(real.folder)
        assert pump.until(lambda _f: _PWD.search(_flat(pump.text)) is not None), _flat(pump.text)[-400:]
        shown = _PWD.search(_flat(pump.text)).group(1)
        assert os.path.normcase(os.path.realpath(shown)) == os.path.normcase(expected), (shown, expected)
        ws.send_text(json.dumps({"type": "input", "data": SIZE_CMD + ENTER}))
        assert pump.output_has("SZ26x90"), _ANSI.sub("", pump.text)[-400:]
        ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        time.sleep(0.3)
        ws.send_text(json.dumps({"type": "input", "data": SIZE_CMD + ENTER}))
        assert pump.output_has("SZ30x100"), _ANSI.sub("", pump.text)[-400:]
        ws.send_text(json.dumps({"type": "input", "data": HANGUL_CMD + ENTER}))
        assert pump.output_has(f"{HANGUL}X2"), _ANSI.sub("", pump.text)[-400:]
        pids = _start_child(real, ws, pump, created["session_id"])
    assert _wait(lambda: not any(_alive(pid) for pid in pids)), [p for p in pids if _alive(p)]


@real_shell
@pytest.mark.parametrize("how", ["window_closed", "delete", "idle_timeout"])
def test_real_process_tree_is_gone_after_the_session_ends(real, how):
    if how == "idle_timeout":
        real.mgr.idle_timeout = 4.0       # 30 minutes in production; shortened for the test
    created = _open(real)
    with real.client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        pump = Pump(ws)
        assert pump.until(lambda f: f.get("type") == "card_running")
        ws.send_text(json.dumps({"type": "input", "data": f"echo {SECRET_MARK}" + ENTER}))
        assert pump.output_has(SECRET_MARK)
        pids = _start_child(real, ws, pump, created["session_id"])
        if how == "delete":
            assert real.client.delete(f"/api/v1/terminal/sessions/{created['session_id']}").status_code == 204
            frame = pump.exit_frame()
            assert frame is not None and frame["reason"] == "closed", pump.seen[-3:]
        elif how == "idle_timeout":
            frame = pump.exit_frame(timeout=30)
            assert frame == {"type": "exit", "reason": "idle_timeout", "code": None}, pump.seen[-3:]
        # window_closed: leaving the block closes the WebSocket, as closing the window does.
    assert _wait(lambda: not any(_alive(pid) for pid in pids)), [p for p in pids if _alive(p)]
    assert _wait(lambda: real.client.get("/api/v1/terminal/status").json()["active_sessions"] == 0)

    # The record: started + ended, who / which Flow / which folder / why -- never the typed text.
    expected_reason = {"window_closed": "closed", "delete": "closed", "idle_timeout": "idle_timeout"}[how]
    assert _wait(lambda: [r["event_type"] for r in real.rows] == ["terminal_session_started", "terminal_session_ended"])
    for row in real.rows:
        assert row["actor_user_id"] == "u_admin" and row["group_id"] == "g1"
        meta = json.loads(row["metadata"])
        assert meta["cwd"] == str(real.folder) and meta["session_id"] == created["session_id"]
        assert set(meta) <= {"session_id", "flow_id", "cwd", "started_at", "reason", "exit_code"}
    assert json.loads(real.rows[1]["metadata"])["reason"] == expected_reason
    everything = json.dumps(real.rows, ensure_ascii=False) + "\n".join(
        record.getMessage() for record in real.caplog.records)
    assert SECRET_MARK not in everything
    assert CHILD_CMD not in everything


# -- the one-time ticket is the only credential in the address ------------------------------


def test_websocket_needs_no_login_token_and_ignores_one_in_the_query(tmp_path, monkeypatch):
    class Quiet:
        pid = 1

        def __init__(self, *a):
            self.out = queue.Queue()

        def read(self):
            return self.out.get()

        def write(self, text):
            pass

        def resize(self, cols, rows):
            pass

        def has_children(self):
            return False

        def exit_code(self):
            return None

        def wait(self, timeout):
            return None

        def terminate(self, grace=0):
            self.out.put(None)

    mgr = ts.TerminalManager(pty_factory=Quiet, shell_argv=lambda: ["x"], enabled=lambda: True,
                             flow_resolver=lambda fid: ("proj", str(tmp_path)), card_probe=lambda fid: False,
                             recorder=lambda *a, **k: None)
    mgr.watch_interval = 0.02
    monkeypatch.setattr(ts, "manager", mgr)
    app = FastAPI()
    app.include_router(terminal_routes.router)

    def no_login():
        raise AssertionError("the WebSocket must not ask for the login token")

    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)
    created = client.post("/api/v1/terminal/sessions", json={"flow_id": "g1", "cols": 80, "rows": 24}).json()
    app.dependency_overrides[get_current_user] = no_login
    # A login token in the query is not a ticket: refused.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/terminal/ws?token=eyJhbGciOi.fake.jwt") as ws:
            ws.receive_text()
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        assert json.loads(ws.receive_text())["type"] == "card_running"
    assert created["ticket"] != created["session_id"]
    assert len(created["ticket"]) >= 32


# -- Windows (ConPTY) code path with stand-ins (every host) ----------------------------------


class FakeWinProc:
    spawned: list = []

    def __init__(self, argv, cwd, env, dimensions):
        self.argv, self.cwd, self.env, self.dimensions = argv, cwd, env, dimensions
        self.pid = 4242
        self.sizes, self.written, self.closed = [], [], False
        self.chunks = ["PS> ", f"{HANGUL}\r\n"]
        self.alive = True
        self.exitstatus = None

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80)):
        proc = cls(argv, cwd, env, dimensions)
        cls.spawned.append(proc)
        return proc

    def read(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        raise EOFError

    def write(self, text):
        self.written.append(text)

    def setwinsize(self, rows, cols):
        self.sizes.append((rows, cols))

    def isalive(self):
        return self.alive

    def close(self, force=False):
        self.closed = True
        self.alive = False
        self.exitstatus = 0


class FakePsProc:
    killed: list = []

    def __init__(self, pid, kids=()):
        self.pid, self._kids = pid, list(kids)

    def children(self, recursive=False):
        return list(self._kids)

    def kill(self):
        FakePsProc.killed.append(self.pid)


@pytest.fixture
def fake_windows(monkeypatch):
    FakeWinProc.spawned.clear()
    FakePsProc.killed.clear()
    from modules.flow_gate.services import terminal_bundle
    monkeypatch.setattr(terminal_bundle, "load_winpty", lambda directory=None: types.SimpleNamespace(PtyProcess=FakeWinProc))
    grandchild = FakePsProc(7)
    child = FakePsProc(6, [grandchild])
    waited = []
    fake_psutil = types.SimpleNamespace(
        Error=Exception,
        Process=lambda pid: FakePsProc(pid, [child, grandchild]) if pid == 4242 else FakePsProc(pid),
        wait_procs=lambda procs, timeout: waited.append(([p.pid for p in procs], timeout)) or ([], []),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    return types.SimpleNamespace(waited=waited)


def test_windows_backend_is_chosen_and_starts_powershell_on_conpty(fake_windows, monkeypatch):
    monkeypatch.setattr(terminal_pty, "IS_WINDOWS", True)
    monkeypatch.setattr(terminal_pty.shutil, "which",
                        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if name == "powershell.exe" else None)
    argv = terminal_pty.default_shell_argv()
    assert argv[0].lower().endswith("powershell.exe") and argv[1:] == ["-NoLogo"]
    env = terminal_pty.shell_env({"PATH": "C:/x", "SECRET_KEY": "s", "FLOWGATE_GIT_ENCRYPT_KEY": "k"})
    assert env == {"PATH": "C:/x"}          # no secrets; no TERM/LC_* on Windows
    pty = terminal_pty.open_pty(argv, r"C:\work\wt", env, 120, 40)
    assert isinstance(pty, terminal_pty.WindowsPty)
    proc = FakeWinProc.spawned[-1]
    assert proc.argv == argv and proc.cwd == r"C:\work\wt" and proc.env == env
    assert proc.dimensions == (40, 120)     # winpty takes (rows, cols)
    pty.resize(132, 50)
    assert proc.sizes == [(50, 132)]
    pty.write(f"echo {HANGUL}\r")
    assert proc.written == [f"echo {HANGUL}\r"]
    assert pty.read() == "PS> " and pty.read() == f"{HANGUL}\r\n" and pty.read() is None
    assert pty.has_children() is True


def test_windows_terminate_kills_the_whole_tree_then_closes(fake_windows):
    pty = terminal_pty.WindowsPty(["powershell.exe", "-NoLogo"], "C:/w", {}, 80, 24)
    pty.terminate(grace=0.5)
    assert sorted(FakePsProc.killed) == [6, 7, 4242]
    assert fake_windows.waited and sorted(fake_windows.waited[0][0]) == [6, 7, 4242]
    assert FakeWinProc.spawned[-1].closed is True
    assert pty.exit_code() == 0
    pty.write("ignored after close")
    assert FakeWinProc.spawned[-1].written == []


def test_windows_terminate_without_psutil_uses_taskkill_tree(fake_windows, monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)      # import psutil -> ImportError
    calls = []
    monkeypatch.setattr(terminal_pty.subprocess, "run", lambda args, **kw: calls.append(args))
    pty = terminal_pty.WindowsPty(["powershell.exe"], "C:/w", {}, 80, 24)
    assert pty.has_children() is False
    pty.terminate()
    assert calls == [["taskkill", "/T", "/F", "/PID", "4242"]]
    assert FakeWinProc.spawned[-1].closed is True


def test_windows_without_pywinpty_is_a_start_failure_not_a_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "winpty", None)
    with pytest.raises(terminal_pty.PtyError):
        terminal_pty.WindowsPty(["powershell.exe"], "C:/w", {}, 80, 24)
