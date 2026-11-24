"""Designer terminal server: permission (always on for admins), limits,
start folder, one-time ticket, WebSocket frames and process cleanup.

The contract tests drive the real routes with a fake pseudo-terminal; the POSIX test at the
end starts a real shell and checks that nothing is left running after the session ends.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from modules.flow_gate.api.v1 import terminal_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import terminal_pty, terminal_sessions as ts

pytestmark = [pytest.mark.timeout(120)]

ADMIN = {"user_id": "u_admin", "is_admin": True}
ADMIN2 = {"user_id": "u_admin2", "is_admin": True}
MEMBER = {"user_id": "u_member", "is_admin": False}
HANGUL = "\ud55c\uae00"   # two Hangul syllables, kept as escapes in source


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakePty:
    def __init__(self, argv, cwd, env, cols, rows):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.size = (cols, rows)
        self.inputs: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.out: "queue.Queue[str | None]" = queue.Queue()
        self.terminated = threading.Event()
        self.code = None
        self.children = False

    def read(self):
        item = self.out.get()
        return item

    def write(self, text):
        self.inputs.append(text)
        if text.startswith("echo "):
            self.out.put(text[5:].rstrip("\r\n") + "\r\n")

    def resize(self, cols, rows):
        self.resizes.append((cols, rows))

    def has_children(self):
        return self.children

    def exit_code(self):
        return self.code

    def wait(self, timeout):
        return self.code

    def terminate(self, grace=0):
        self.terminated.set()
        self.out.put(None)

    def exit(self, code):
        self.code = code
        self.out.put(None)


class Env:
    def __init__(self, tmp_path):
        self.card = False
        self.clock = FakeClock()
        self.ptys: list[FakePty] = []
        self.events: list[tuple[str, str, str | None]] = []
        self.flows: dict[str, Path] = {}
        self.tmp = tmp_path

    def flow(self, flow_id):
        if flow_id not in self.flows:
            raise ts.TerminalError(404, "terminal_flow_not_found", "Flow not found.")
        return "proj", str(self.flows[flow_id])

    def factory(self, argv, cwd, env, cols, rows):
        pty = FakePty(argv, cwd, env, cols, rows)
        self.ptys.append(pty)
        return pty

    def record(self, event, session, reason=None):
        self.events.append((event, session.session_id, reason))


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = Env(tmp_path)
    for name in ("g1", "g2", "g3", "g4"):
        folder = tmp_path / name
        folder.mkdir()
        state.flows[name] = folder
    mgr = ts.TerminalManager(
        pty_factory=state.factory, shell_argv=lambda: ["fake-shell"], clock=state.clock,
        flow_resolver=state.flow,
        card_probe=lambda flow_id: state.card, recorder=state.record,
    )
    mgr.watch_interval = 0.02
    mgr.card_poll = 0.0
    monkeypatch.setattr(ts, "manager", mgr)
    state.mgr = mgr
    yield state
    mgr.shutdown()


def client_for(user):
    app = FastAPI()
    app.include_router(terminal_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def open_session(client, flow="g1", cols=100, rows=30):
    response = client.post("/api/v1/terminal/sessions", json={"flow_id": flow, "cols": cols, "rows": rows})
    assert response.status_code == 201, response.text
    return response.json()


def frames_until(ws, kind):
    seen = []
    while True:
        frame = json.loads(ws.receive_text())
        seen.append(frame)
        if frame["type"] == kind:
            return frame, seen


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- permission ---------------------------------------------------------------------------


def test_status_tells_the_screen_whether_to_hide_or_grey_the_button(env):
    body = client_for(MEMBER).get("/api/v1/terminal/status", params={"flow_id": "g1"}).json()
    assert body == {"enabled": True, "allowed": False, "reason": "not_admin", "max_sessions": 3,
                    "active_sessions": 0, "flow_session_id": None}
    body = client_for(ADMIN).get("/api/v1/terminal/status").json()
    assert (body["enabled"], body["allowed"], body["reason"], body["active_sessions"]) == (True, True, None, 0)


def test_non_admin_is_refused_on_every_session_path(env):
    client = client_for(MEMBER)
    response = client.post("/api/v1/terminal/sessions", json={"flow_id": "g1", "cols": 80, "rows": 24})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "terminal_not_admin"
    response = client.delete("/api/v1/terminal/sessions/ts_x")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "terminal_not_admin"
    assert env.ptys == []


def test_admin_opens_without_any_setting_even_if_an_old_off_value_remains(env, monkeypatch):
    # F85: no on/off switch. A leftover ``terminal.enabled=false`` row is never read.
    from modules.flow_gate.db import system_settings
    monkeypatch.setattr(system_settings, "get_value", lambda key, default=None: "false")
    assert not hasattr(ts, "terminal_enabled")
    client = client_for(ADMIN)
    assert client.get("/api/v1/terminal/status").json()["allowed"] is True
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        assert wait_for(lambda: env.ptys)
        env.ptys[0].exit(0)
        assert frames_until(ws, "exit")[0]["reason"] == "shell_exit"


# -- limits ----------------------------------------------------------------------------


def test_one_per_flow_and_three_per_person(env):
    client = client_for(ADMIN)
    first = open_session(client, "g1")
    assert set(first) == {"session_id", "cwd", "ticket", "ticket_expires_in"}
    assert first["ticket_expires_in"] == 30
    again = client.post("/api/v1/terminal/sessions", json={"flow_id": "g1", "cols": 80, "rows": 24})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "terminal_flow_session_exists"
    assert again.json()["session_id"] == first["session_id"]
    assert again.json()["error"]["details"]["session_id"] == first["session_id"]
    open_session(client, "g2")
    open_session(client, "g3")
    fourth = client.post("/api/v1/terminal/sessions", json={"flow_id": "g4", "cols": 80, "rows": 24})
    assert fourth.status_code == 409
    assert fourth.json()["error"]["code"] == "terminal_session_limit"
    status = client.get("/api/v1/terminal/status", params={"flow_id": "g1"}).json()
    assert status["active_sessions"] == 3 and status["flow_session_id"] == first["session_id"]
    # Another administrator has their own three.
    open_session(client_for(ADMIN2), "g1")


def test_delete_frees_the_slot_and_only_the_owner_can_delete(env):
    client = client_for(ADMIN)
    created = open_session(client, "g1")
    assert client_for(ADMIN2).delete(f"/api/v1/terminal/sessions/{created['session_id']}").status_code == 404
    assert client.delete(f"/api/v1/terminal/sessions/{created['session_id']}").status_code == 204
    assert client.delete(f"/api/v1/terminal/sessions/{created['session_id']}").status_code == 404
    open_session(client, "g1")


def test_unused_ticket_expires_and_frees_the_slot(env):
    client = client_for(ADMIN)
    created = open_session(client, "g1")
    env.clock.now += 31
    assert client.get("/api/v1/terminal/status", params={"flow_id": "g1"}).json()["active_sessions"] == 0
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
            ws.receive_text()
    open_session(client, "g1")


# -- start folder ----------------------------------------------------------------------


def test_start_folder_is_the_group_worktree_else_the_branch_checkout(tmp_path, monkeypatch):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.storage import paths as storage_paths

    worktree, checkout = tmp_path / "wt", tmp_path / "main"
    worktree.mkdir()
    checkout.mkdir()
    calls = []
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"group_id": gid, "project_id": "proj"} if gid != "nope" else None)

    def resolve(project_id, fallback_branch="main", group_id=None):
        calls.append((project_id, group_id))
        return worktree if group_id == "with_wt" else checkout

    monkeypatch.setattr(storage_paths, "resolve_project_src_root", resolve)
    assert ts.resolve_flow("with_wt") == ("proj", str(worktree))
    assert ts.resolve_flow("no_wt") == ("proj", str(checkout))
    assert calls == [("proj", "with_wt"), ("proj", "no_wt")]
    with pytest.raises(ts.TerminalError) as missing:
        ts.resolve_flow("nope")
    assert missing.value.code == "terminal_flow_not_found"
    monkeypatch.setattr(storage_paths, "resolve_project_src_root", lambda *a, **k: tmp_path / "gone")
    with pytest.raises(ts.TerminalError) as gone:
        ts.resolve_flow("no_wt")
    assert gone.value.code == "terminal_cwd_unavailable"


def test_create_reports_the_folder_and_starts_the_shell_there(env):
    client = client_for(ADMIN)
    created = open_session(client, "g2", cols=120, rows=40)
    assert created["cwd"] == str(env.flows["g2"])
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        pty = env.ptys[0]
        assert pty.cwd == str(env.flows["g2"]) and pty.size == (120, 40)
        assert pty.argv == ["fake-shell"]


# -- ticket ----------------------------------------------------------------------------


def test_ticket_is_single_use(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as other:
                other.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/terminal/ws?ticket=made-up") as ws:
            ws.receive_text()
    assert len(env.ptys) == 1


# -- WebSocket frames ------------------------------------------------------------------


def test_input_output_resize_busy_card_and_hangul(env):
    env.card = True
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frame, _ = frames_until(ws, "card_running")
        assert frame == {"type": "card_running", "value": True}
        ws.send_text(json.dumps({"type": "input", "data": f"echo {HANGUL}\r"}))
        frame, _ = frames_until(ws, "output")
        assert frame["data"] == f"{HANGUL}\r\n"
        ws.send_text(json.dumps({"type": "resize", "cols": 132, "rows": 50}))
        ws.send_text(json.dumps({"type": "resize", "cols": 0, "rows": 50}))        # ignored
        ws.send_text("not json")                                                  # ignored
        pty = env.ptys[0]
        pty.children = True
        assert frames_until(ws, "busy")[0] == {"type": "busy", "value": True}
        assert pty.resizes == [(132, 50)]
        env.card = False
        assert frames_until(ws, "card_running")[0] == {"type": "card_running", "value": False}
        pty.children = False
        assert frames_until(ws, "busy")[0] == {"type": "busy", "value": False}
    assert wait_for(env.ptys[0].terminated.is_set)


def test_shell_exit_sends_exit_with_code(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        env.ptys[0].exit(3)
        frame, _ = frames_until(ws, "exit")
        assert frame == {"type": "exit", "reason": "shell_exit", "code": 3}
    assert wait_for(lambda: ("terminal_session_ended", created["session_id"], "shell_exit") in env.events)


def test_closing_the_socket_ends_the_process_tree_and_frees_the_slot(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
    assert wait_for(env.ptys[0].terminated.is_set)
    assert wait_for(lambda: client.get("/api/v1/terminal/status").json()["active_sessions"] == 0)
    assert ("terminal_session_started", created["session_id"], None) in env.events
    assert ("terminal_session_ended", created["session_id"], "closed") in env.events


def test_delete_while_connected_sends_closed(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        assert client.delete(f"/api/v1/terminal/sessions/{created['session_id']}").status_code == 204
        assert env.ptys[0].terminated.is_set()
        frame, _ = frames_until(ws, "exit")
        assert frame["reason"] == "closed"


def test_idle_for_thirty_minutes_ends_the_session(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        env.clock.now += 29 * 60
        ws.send_text(json.dumps({"type": "input", "data": "x"}))     # activity resets the clock
        assert wait_for(lambda: env.ptys[0].inputs == ["x"])
        env.clock.now += 29 * 60
        time.sleep(0.1)
        assert not env.ptys[0].terminated.is_set()
        env.clock.now += 61
        frame, _ = frames_until(ws, "exit")
        assert frame == {"type": "exit", "reason": "idle_timeout", "code": None}
    assert env.ptys[0].terminated.is_set()
    assert ("terminal_session_ended", created["session_id"], "idle_timeout") in env.events


def test_server_shutdown_ends_every_session(env):
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frames_until(ws, "card_running")
        env.mgr.shutdown()
        assert env.ptys[0].terminated.is_set()
        assert frames_until(ws, "exit")[0]["reason"] == "closed"


def test_spawn_failure_reports_error_and_frees_the_slot(env):
    def broken(*args):
        raise terminal_pty.PtyError("no shell")

    env.mgr.pty_factory = broken
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        assert json.loads(ws.receive_text()) == {"type": "exit", "reason": "error", "code": None,
                                                 "message": "no shell"}
    assert client.get("/api/v1/terminal/status").json()["active_sessions"] == 0


def test_missing_pywinpty_is_a_start_failure_with_the_pip_hint(env, monkeypatch):
    monkeypatch.setattr(terminal_pty, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winpty", None)       # import winpty -> ImportError
    env.mgr.pty_factory = terminal_pty.open_pty
    client = client_for(ADMIN)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        frame = json.loads(ws.receive_text())
    assert (frame["type"], frame["reason"], frame["code"]) == ("exit", "error", None)
    assert "pip install -r server/requirements.txt" in frame["message"]
    assert client.get("/api/v1/terminal/status").json()["active_sessions"] == 0


# -- shared app pieces -----------------------------------------------------------------


def test_app_wide_nul_guard_does_not_break_websockets(env):
    from modules.flow_gate.api.input_validation import reject_nul_input

    app = FastAPI(dependencies=[Depends(reject_nul_input)])
    app.include_router(terminal_routes.router)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)
    created = open_session(client)
    with client.websocket_connect(f"/api/v1/terminal/ws?ticket={created['ticket']}") as ws:
        assert frames_until(ws, "card_running")[0]["type"] == "card_running"
    bad = client.post("/api/v1/terminal/sessions", json={"flow_id": "g\x002", "cols": 80, "rows": 24})
    assert bad.status_code == 422


def test_shell_environment_drops_server_secrets():
    base = {"PATH": "/usr/bin", "SECRET_KEY": "s", "DB_PASSWORD": "p", "FLOWGATE_GIT_ENCRYPT_KEY": "k",
            "FLOWGATE_AGENT_PEPPER": "x", "MY_API_KEY": "y", "FLOWGATE_PORT": "8089", "LANG": "C"}
    env = terminal_pty.shell_env(base)
    assert env["PATH"] == "/usr/bin" and env["FLOWGATE_PORT"] == "8089"
    for key in ("SECRET_KEY", "DB_PASSWORD", "FLOWGATE_GIT_ENCRYPT_KEY", "FLOWGATE_AGENT_PEPPER", "MY_API_KEY"):
        assert key not in env
    if not terminal_pty.IS_WINDOWS:
        assert env["TERM"] == "xterm-256color"
        assert "UTF-8" in env["LC_CTYPE"]


def test_default_shell_is_the_os_shell():
    argv = terminal_pty.default_shell_argv()
    if terminal_pty.IS_WINDOWS:
        assert "powershell" in argv[0].lower() or "pwsh" in argv[0].lower()
    else:
        assert argv[-1] == "-l" and os.path.isabs(argv[0])


def test_start_and_end_records_never_contain_keystrokes(env, monkeypatch):
    from modules.flow_gate.db import workflow_events

    rows = []
    monkeypatch.setattr(workflow_events, "create", lambda data: rows.append(data) or data)
    session = ts.TerminalSession(session_id="ts_1", user_id="u_admin", flow_id="g1", project_id="proj",
                                 cwd="/work/g1", cols=80, rows=24, created=0.0, started_at="2026-11-23T00:00:00+00:00")
    ts.record_event("terminal_session_started", session)
    session.exit_code = 0
    ts.record_event("terminal_session_ended", session, "shell_exit")
    assert [r["event_type"] for r in rows] == ["terminal_session_started", "terminal_session_ended"]
    assert rows[0]["actor_user_id"] == "u_admin" and rows[0]["group_id"] == "g1"
    meta = json.loads(rows[1]["metadata"])
    assert meta == {"session_id": "ts_1", "flow_id": "g1", "cwd": "/work/g1",
                    "started_at": "2026-11-23T00:00:00+00:00", "reason": "shell_exit", "exit_code": 0}


# -- real shell (POSIX) ----------------------------------------------------------------


def _pid_alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().split(b") ", 1)[1][:1] != b"Z"
    except OSError:
        pass
    if os.path.isdir("/proc/self"):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_until(pty, needle, timeout=10.0):
    buf, result = [], {}

    def pump():
        while needle not in "".join(buf):
            chunk = pty.read()
            if chunk is None:
                break
            buf.append(chunk)
        result["done"] = True

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    thread.join(timeout)
    return "".join(buf)


@pytest.mark.skipif(terminal_pty.IS_WINDOWS, reason="POSIX pty")
def test_real_shell_runs_in_folder_resizes_speaks_utf8_and_leaves_nothing(tmp_path):
    env = terminal_pty.shell_env()
    env["PS1"] = "$ "
    pty = terminal_pty.open_pty(["/bin/sh"], str(tmp_path), env, 80, 24)
    try:
        pty.write("pwd; stty size; echo M1$((6*7))\n")
        out = _read_until(pty, "M142")
        assert str(tmp_path.resolve()) in out or str(tmp_path) in out, out
        assert "24 80" in out, out
        pty.resize(100, 30)
        pty.write("stty size; echo M2$((1+1))\n")
        out = _read_until(pty, "M22")
        assert "30 100" in out, out
        pty.write(f"printf '%s\\n' '{HANGUL}'X; echo M3$((2+1))\n")
        out = _read_until(pty, "M33")
        assert f"{HANGUL}X" in out, out
        pty.write("sleep 300 & echo M4$((3+1))\n")
        out = _read_until(pty, "M44", timeout=5)
        assert wait_for(pty.has_children)
        children = terminal_pty.descendants(pty.pid)
        assert children, out
    finally:
        shell = pty.pid
        pty.terminate(grace=1.0)
    assert not _pid_alive(shell)
    assert wait_for(lambda: not any(_pid_alive(pid) for pid in children), timeout=5)
