"""Designer terminal sessions on the FlowGate server PC.

One session = one shell on a pseudo-terminal, owned by one administrator for one Flow
(group). Everything lives in this process's memory: the server runs one worker
(``FLOWGATE_WORKERS=1``) and reattaching after a disconnect is out of scope, so there is
nothing to persist except the start/end record.

Life of a session
-----------------
1. ``POST /terminal/sessions`` -> :meth:`TerminalManager.create` checks admin, the
   one-per-Flow and three-per-person limits, decides the start folder (group worktree, else the project branch checkout) and returns a one-time
   ticket valid for 30 seconds. No process exists yet.
2. ``WS /terminal/ws?ticket=`` -> :func:`serve_websocket` redeems the ticket (single use),
   starts the shell and relays input/output until one side ends it.
3. The session ends -- window closed (WS disconnect), ``DELETE``, shell exit, 30 minutes
   without input or output, or server shutdown -- and :meth:`TerminalManager.end` kills the
   whole process tree. A ticket never redeemed frees its slot when it expires.

Keystrokes, commands and output are never recorded: only who, when, which folder
and why it ended.

The terminal is always available to administrators: there is no on/off setting (a
leftover ``terminal.enabled`` row in the DB is simply not read).
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import anyio

from modules.flow_gate.services import terminal_pty

log = logging.getLogger("flowgate.terminal")

MAX_SESSIONS_PER_USER = 3
TICKET_TTL_SECONDS = 30
IDLE_TIMEOUT_SECONDS = 30 * 60
WATCH_INTERVAL_SECONDS = 1.0
CARD_POLL_SECONDS = 5.0
MAX_INPUT_CHARS = 1_000_000
MAX_DIMENSION = 1000

EXIT_REASONS = ("closed", "idle_timeout", "shell_exit", "error")


class TerminalError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict:
        body: dict[str, Any] = {"ok": False, "error": {"code": self.code, "message": self.message,
                                                       "details": dict(self.details)}}
        body.update(self.details)
        return body


def resolve_flow(flow_id: str) -> tuple[str, str]:
    """(project_id, start folder) for a Flow; the server decides, never the screen.

    Reuses the remote source tools' rule (``storage_paths.resolve_project_src_root``):
    the group's worktree when there is one, otherwise the project branch checkout.
    """
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.storage import paths as storage_paths

    group = db_groups.get_by_id(flow_id)
    if not group or not group.get("project_id"):
        raise TerminalError(404, "terminal_flow_not_found", "Flow not found.", flow_id=flow_id)
    project_id = str(group["project_id"])
    root = storage_paths.resolve_project_src_root(project_id, "main", group_id=flow_id)
    if root is None or not root.is_dir():
        raise TerminalError(409, "terminal_cwd_unavailable",
                            "No working folder exists for this Flow on the server.", flow_id=flow_id)
    return project_id, str(root)


def card_running(flow_id: str) -> bool:
    """Whether an AI card currently holds this group's run lease."""
    try:
        from modules.flow_gate.db import group_ai_leases

        return group_ai_leases.get_active(flow_id) is not None
    except Exception:  # noqa: BLE001 -- the banner is advisory; never fail the terminal on it
        return False


def record_event(event: str, session: "TerminalSession", reason: Optional[str] = None) -> None:
    """Start/end record: who, when, which Flow and folder, and why it ended."""
    meta = {
        "session_id": session.session_id,
        "flow_id": session.flow_id,
        "cwd": session.cwd,
        "started_at": session.started_at,
    }
    if reason is not None:
        meta["reason"] = reason
        meta["exit_code"] = session.exit_code
    log.info("terminal %s user=%s flow=%s cwd=%s session=%s reason=%s", event, session.user_id,
             session.flow_id, session.cwd, session.session_id, reason)
    try:
        from modules.flow_gate.db import workflow_events

        workflow_events.create({
            "event_type": event,
            "project_id": session.project_id,
            "group_id": session.flow_id,
            "actor_user_id": session.user_id,
            "metadata": json.dumps(meta, ensure_ascii=False, sort_keys=True),
        })
    except Exception:  # noqa: BLE001 -- recording must never keep a shell alive or block one
        log.warning("terminal %s record failed for session %s", event, session.session_id, exc_info=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TerminalSession:
    session_id: str
    user_id: str
    flow_id: str
    project_id: str
    cwd: str
    cols: int
    rows: int
    created: float
    state: str = "pending"          # pending -> attached -> running -> ended
    started_at: Optional[str] = None
    pty: Any = None
    last_activity: float = 0.0
    exit_code: Optional[int] = None
    end_reason: Optional[str] = None
    _end_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _closers: list = field(default_factory=list, repr=False)

    @property
    def live(self) -> bool:
        return self.state != "ended"


class TerminalManager:
    def __init__(
        self,
        *,
        pty_factory: Callable[..., Any] = terminal_pty.open_pty,
        shell_argv: Optional[Callable[[], list[str]]] = None,
        clock: Callable[[], float] = time.monotonic,
        flow_resolver: Optional[Callable[[str], tuple[str, str]]] = None,
        card_probe: Optional[Callable[[str], bool]] = None,
        recorder: Optional[Callable[..., None]] = None,
    ):
        self.pty_factory = pty_factory
        self.shell_argv = shell_argv or terminal_pty.default_shell_argv
        self.clock = clock
        self.flow_resolver = flow_resolver or resolve_flow
        self.card_probe = card_probe or card_running
        self.recorder = recorder or record_event
        self.max_sessions = MAX_SESSIONS_PER_USER
        self.ticket_ttl = TICKET_TTL_SECONDS
        self.idle_timeout = IDLE_TIMEOUT_SECONDS
        self.watch_interval = WATCH_INTERVAL_SECONDS
        self.card_poll = CARD_POLL_SECONDS
        self._lock = threading.RLock()
        self._sessions: dict[str, TerminalSession] = {}
        self._tickets: dict[str, tuple[str, float]] = {}

    # -- queries ---------------------------------------------------------------------

    def _user_sessions(self, user_id: str) -> list[TerminalSession]:
        return [s for s in self._sessions.values() if s.user_id == user_id and s.live]

    def _flow_session(self, user_id: str, flow_id: str) -> Optional[TerminalSession]:
        for session in self._user_sessions(user_id):
            if session.flow_id == flow_id:
                return session
        return None

    def get(self, session_id: str) -> Optional[TerminalSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def status(self, user: dict, flow_id: Optional[str]) -> dict:
        is_admin = bool(user.get("is_admin"))
        reason = None if is_admin else "not_admin"
        active, flow_session = 0, None
        if is_admin:
            self.reap_expired()
            with self._lock:
                user_id = str(user.get("user_id") or "")
                active = len(self._user_sessions(user_id))
                if flow_id:
                    found = self._flow_session(user_id, flow_id)
                    flow_session = found.session_id if found else None
        return {
            "enabled": True,            # kept for older screens; the terminal has no off switch
            "allowed": reason is None,
            "reason": reason,
            "max_sessions": self.max_sessions,
            "active_sessions": active,
            "flow_session_id": flow_session,
        }

    # -- create / close --------------------------------------------------------------

    def _gate(self, user: dict) -> str:
        if not user.get("is_admin"):
            raise TerminalError(403, "terminal_not_admin", "Administrator access required.")
        user_id = str(user.get("user_id") or "")
        if not user_id:
            raise TerminalError(403, "terminal_not_admin", "Administrator access required.")
        return user_id

    def _check_limits(self, user_id: str, flow_id: str) -> None:
        existing = self._flow_session(user_id, flow_id)
        if existing is not None:
            raise TerminalError(409, "terminal_flow_session_exists",
                                "A terminal is already open for this Flow.", session_id=existing.session_id)
        if len(self._user_sessions(user_id)) >= self.max_sessions:
            raise TerminalError(409, "terminal_session_limit",
                                f"Up to {self.max_sessions} terminals can be open at once.",
                                max_sessions=self.max_sessions)

    def create(self, user: dict, flow_id: str, cols: int, rows: int) -> dict:
        user_id = self._gate(user)
        flow_id = str(flow_id or "").strip()
        if not flow_id:
            raise TerminalError(422, "terminal_invalid_request", "flow_id is required.")
        self.reap_expired()
        with self._lock:
            self._check_limits(user_id, flow_id)
        project_id, cwd = self.flow_resolver(flow_id)     # DB/filesystem: outside the lock
        now = self.clock()
        with self._lock:
            self._check_limits(user_id, flow_id)          # again: another request may have won
            session = TerminalSession(
                session_id="ts_" + uuid.uuid4().hex, user_id=user_id, flow_id=flow_id,
                project_id=project_id, cwd=cwd, cols=_dimension(cols, 80), rows=_dimension(rows, 24),
                created=now, last_activity=now,
            )
            ticket = secrets.token_urlsafe(32)
            self._sessions[session.session_id] = session
            self._tickets[ticket] = (session.session_id, now + self.ticket_ttl)
        return {"session_id": session.session_id, "cwd": cwd, "ticket": ticket,
                "ticket_expires_in": self.ticket_ttl}

    def close(self, user: dict, session_id: str) -> None:
        if not user.get("is_admin"):
            raise TerminalError(403, "terminal_not_admin", "Administrator access required.")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != str(user.get("user_id") or "") or not session.live:
                raise TerminalError(404, "terminal_session_not_found", "Terminal session not found.")
        self.end(session, "closed")

    # -- tickets ---------------------------------------------------------------------

    def redeem(self, ticket: str) -> Optional[TerminalSession]:
        """The session a ticket opens, consuming the ticket. None when unknown/expired/used."""
        if not ticket:
            return None
        with self._lock:
            entry = self._tickets.pop(ticket, None)
            if entry is None:
                return None
            session_id, expires = entry
            session = self._sessions.get(session_id)
            if session is None or session.state != "pending":
                return None
            if self.clock() > expires:
                self._discard(session, "error")
                return None
            session.state = "attached"
            return session

    def reap_expired(self) -> None:
        """Free the slots of tickets that expired before a WebSocket used them."""
        now = self.clock()
        with self._lock:
            for ticket, (session_id, expires) in list(self._tickets.items()):
                if now > expires:
                    self._tickets.pop(ticket, None)
                    session = self._sessions.get(session_id)
                    if session is not None and session.state == "pending":
                        self._discard(session, "error")

    def _discard(self, session: TerminalSession, reason: str) -> None:
        session.state = "ended"
        session.end_reason = reason
        self._sessions.pop(session.session_id, None)
        for ticket, (session_id, _expires) in list(self._tickets.items()):
            if session_id == session.session_id:
                self._tickets.pop(ticket, None)

    # -- process ---------------------------------------------------------------------

    def spawn(self, session: TerminalSession) -> None:
        """Start the shell for an attached session (blocking; run off the event loop)."""
        session.pty = self.pty_factory(list(self.shell_argv()), session.cwd, terminal_pty.shell_env(),
                                       session.cols, session.rows)
        with self._lock:
            if session.state == "ended":       # closed while starting: do not leak the shell
                pty, session.pty = session.pty, None
            else:
                pty = None
                session.state = "running"
                session.started_at = _now_iso()
                session.last_activity = self.clock()
        if pty is not None:
            pty.terminate()
            return
        self.recorder("terminal_session_started", session)

    def touch(self, session: TerminalSession) -> None:
        session.last_activity = self.clock()

    def idle_for(self, session: TerminalSession) -> float:
        return self.clock() - session.last_activity

    def end(self, session: TerminalSession, reason: str, exit_code: Optional[int] = None) -> bool:
        """End a session once: kill its process tree, record it, tell its socket.

        Returns False when the session had already ended. Blocking; never call it on the
        event loop.
        """
        with session._end_lock:
            with self._lock:
                if session.state == "ended":
                    return False
                started = session.state == "running"
                self._discard(session, reason)
                closers = list(session._closers)
            pty = session.pty
            if pty is not None:
                try:
                    pty.terminate()
                except Exception:  # noqa: BLE001
                    log.warning("terminal session %s: terminate failed", session.session_id, exc_info=True)
                if exit_code is None:
                    try:
                        exit_code = pty.exit_code()
                    except Exception:  # noqa: BLE001
                        exit_code = None
            session.exit_code = exit_code
        for closer in closers:
            try:
                closer(reason, exit_code)
            except Exception:  # noqa: BLE001
                pass
        if started:
            self.recorder("terminal_session_ended", session, reason)
        return True

    def open_count(self) -> int:
        with self._lock:
            return sum(s.live for s in self._sessions.values())

    def shutdown(self) -> None:
        """Server stop: no shell may outlive the server process."""
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self.end(session, "closed")


def _dimension(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_DIMENSION, number))


manager = TerminalManager()
atexit.register(lambda: manager.shutdown())


# -- WebSocket relay ------------------------------------------------------------------


async def serve_websocket(websocket, ticket: str, mgr: Optional[TerminalManager] = None) -> None:
    """Relay one terminal WebSocket (the frame shapes agreed with the screen card)."""
    mgr = mgr or manager
    session = mgr.redeem(ticket)
    if session is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    open_state = {"open": True}
    ended = asyncio.Event()
    outcome: dict[str, Any] = {}

    async def send(frame: dict) -> None:
        if not open_state["open"]:
            return
        async with send_lock:
            await websocket.send_text(json.dumps(frame, ensure_ascii=False))

    def on_end(reason: str, code: Optional[int]) -> None:      # any thread
        outcome.setdefault("reason", reason)
        outcome.setdefault("code", code)
        loop.call_soon_threadsafe(ended.set)

    with mgr._lock:
        session._closers.append(on_end)
        if not session.live:
            on_end(session.end_reason or "closed", session.exit_code)
    if not session.live:
        await _finish(websocket, send, open_state, session.end_reason or "closed", session.exit_code)
        return
    try:
        await anyio.to_thread.run_sync(mgr.spawn, session)
    except terminal_pty.PtyError as exc:
        # e.g. pywinpty missing on Windows: a start failure with the fix, never a crash.
        log.warning("terminal session %s: shell start failed: %s", session.session_id, exc)
        await anyio.to_thread.run_sync(mgr.end, session, "error")
        await _finish(websocket, send, open_state, "error", None, message=str(exc))
        return
    except Exception:  # noqa: BLE001
        log.warning("terminal session %s: shell start failed", session.session_id, exc_info=True)
        await anyio.to_thread.run_sync(mgr.end, session, "error")
        await _finish(websocket, send, open_state, "error", None)
        return
    if session.state == "ended":
        await _finish(websocket, send, open_state, outcome.get("reason", "closed"), outcome.get("code"))
        return

    def reader() -> None:                                       # worker thread
        pty = session.pty
        while True:
            chunk = pty.read()
            if chunk is None:
                break
            mgr.touch(session)
            future = asyncio.run_coroutine_threadsafe(send({"type": "output", "data": chunk}), loop)
            try:
                future.result()          # back-pressure: the next read waits for this send
            except Exception:  # noqa: BLE001 -- socket gone; the receive side ends the session
                break
        code = pty.wait(2.0)
        if session.live:
            outcome.setdefault("reason", "shell_exit")
            outcome.setdefault("code", code)
            loop.call_soon_threadsafe(ended.set)

    async def receive() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            raw = message.get("text")
            if raw is None and message.get("bytes") is not None:
                raw = message["bytes"].decode("utf-8", "replace")
            frame = _parse_frame(raw)
            if frame is None:
                continue
            if frame["type"] == "input":
                mgr.touch(session)
                await anyio.to_thread.run_sync(session.pty.write, frame["data"])
            elif frame["type"] == "resize":
                session.cols, session.rows = frame["cols"], frame["rows"]
                session.pty.resize(frame["cols"], frame["rows"])

    async def watch() -> None:
        busy = False
        card = bool(await anyio.to_thread.run_sync(mgr.card_probe, session.flow_id))
        await send({"type": "card_running", "value": card})
        last_card_poll = mgr.clock()
        while True:
            await asyncio.sleep(mgr.watch_interval)
            if mgr.idle_for(session) >= mgr.idle_timeout:
                outcome.setdefault("reason", "idle_timeout")
                outcome.setdefault("code", None)
                return
            now_busy = bool(await anyio.to_thread.run_sync(session.pty.has_children))
            if now_busy != busy:
                busy = now_busy
                await send({"type": "busy", "value": busy})
            if mgr.clock() - last_card_poll >= mgr.card_poll:
                last_card_poll = mgr.clock()
                now_card = bool(await anyio.to_thread.run_sync(mgr.card_probe, session.flow_id))
                if now_card != card:
                    card = now_card
                    await send({"type": "card_running", "value": card})

    reader_thread = threading.Thread(target=reader, name=f"terminal-{session.session_id}", daemon=True)
    reader_thread.start()
    tasks = {
        asyncio.ensure_future(receive()): "receive",
        asyncio.ensure_future(watch()): "watch",
        asyncio.ensure_future(ended.wait()): "ended",
    }
    reason, code = "error", None
    try:
        done, _pending = await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
        first = next(iter(done))
        kind = tasks[first]
        if kind == "receive":
            open_state["open"] = False          # the browser side is gone: nothing to tell it
            reason = "closed"
        elif first.exception() is not None:
            log.warning("terminal session %s: relay failed", session.session_id, exc_info=first.exception())
            reason = "error"
        else:
            reason = outcome.get("reason", "closed")
            code = outcome.get("code")
    finally:
        for task in tasks:
            task.cancel()
        # Started before anything else is awaited: even when this handler is being
        # cancelled (server stop, client gone), the process tree still gets killed.
        ender = threading.Thread(target=mgr.end, args=(session, reason, code), daemon=True,
                                 name=f"terminal-end-{session.session_id}")
        ender.start()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
            await anyio.to_thread.run_sync(ender.join)
    if code is None:
        code = session.exit_code if reason == "shell_exit" else None
    await _finish(websocket, send, open_state, reason, code)


async def _finish(websocket, send, open_state: dict, reason: str, code: Optional[int],
                  message: Optional[str] = None) -> None:
    if not open_state["open"]:
        return
    frame: dict[str, Any] = {"type": "exit", "reason": reason if reason in EXIT_REASONS else "error", "code": code}
    if message:
        frame["message"] = message
    try:
        await send(frame)
    except Exception:  # noqa: BLE001 -- the socket may already be gone
        pass
    open_state["open"] = False
    try:
        await websocket.close(code=1000)
    except Exception:  # noqa: BLE001
        pass


def _parse_frame(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        frame = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(frame, dict):
        return None
    kind = frame.get("type")
    if kind == "input":
        data = frame.get("data")
        if not isinstance(data, str) or not data:
            return None
        return {"type": "input", "data": data[:MAX_INPUT_CHARS]}
    if kind == "resize":
        cols, rows = frame.get("cols"), frame.get("rows")
        if not (isinstance(cols, int) and isinstance(rows, int)) or isinstance(cols, bool) or isinstance(rows, bool):
            return None
        if not (1 <= cols <= MAX_DIMENSION and 1 <= rows <= MAX_DIMENSION):
            return None
        return {"type": "resize", "cols": cols, "rows": rows}
    return None
