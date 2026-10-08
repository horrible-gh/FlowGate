"""Server instance registry (flowgate.default.0669 unit 1a; 0666 0009-L §2.8, §2.26 steps 1~2).

Each server process registers one ``server_instance`` row at startup and keeps it alive
with a CAS heartbeat. Locks and legacy mutex rows record that instance id, so a row
whose owner is DEAD can later be reclaimed without touching a live owner's row.

What this module does NOT do yet (later units): reclaim locks, sweep other nodes on a
timer, run jobs. ``on_instance_lost`` is the hook the lock manager (unit 1b) attaches
to so its live contexts get marked lost when this instance is declared dead.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional

from modules.flow_gate.db import git_concurrency as db
from modules.flow_gate.db.connection import now_iso

_log = logging.getLogger(__name__)

ALIVE = "alive"
DEAD = "dead"


# ── parameters (L 1.2; FLOWGATE_ env override, clamped) ──────────────────────

def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    clamped = max(low, min(high, value))
    if clamped != value:
        _log.warning("%s=%s out of [%s, %s]; clamped to %s", name, value, low, high, clamped)
    return clamped


def heartbeat_interval_sec() -> int:
    return _env_int("FLOWGATE_INSTANCE_HEARTBEAT_INTERVAL_SEC", 10, 5, 30)


def dead_after_sec() -> int:
    return _env_int("FLOWGATE_INSTANCE_DEAD_AFTER_SEC", 60, 6 * heartbeat_interval_sec(), 600)


def clock_skew_margin_sec() -> int:
    return _env_int("FLOWGATE_CLOCK_SKEW_MARGIN_SEC", 10, 2, 60)


def node_key() -> str:
    """FLOWGATE_NODE_ID if set, else the lower-cased host name (L 1.6)."""
    configured = (os.environ.get("FLOWGATE_NODE_ID") or "").strip()
    return configured or socket.gethostname().lower()


# ── time rules (L 2.2) ───────────────────────────────────────────────────────

def add_seconds(iso: str, seconds: int) -> str:
    """Shift a now_iso() string, keeping its offset and seconds precision."""
    return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def expired_for_others(t_until: Optional[str], now: str) -> bool:
    return bool(t_until) and add_seconds(t_until, clock_skew_margin_sec()) < now


def valid_for_self(t_until: Optional[str], now: str) -> bool:
    return bool(t_until) and now < add_seconds(t_until, -clock_skew_margin_sec())


# ── process identity ─────────────────────────────────────────────────────────

def _start_identity(pid: int) -> Optional[str]:
    # Reuses the Self-check OS start marker (Windows creation FILETIME / Linux stat
    # starttime). Never os.kill(pid, 0): on Windows that terminates the process.
    from modules.flow_gate.services.tr_self_check_executor import start_identity

    return start_identity(pid)


def os_process_alive(pid: int, started_marker: Optional[str]) -> bool:
    """True only while `pid` exists and is still the process that registered.

    process_started_at holds the OS start marker, so an exact match rules out PID
    reuse (L 2.8 asks for <= 1s agreement; the marker is stricter). Without a stored
    marker, PID existence alone decides; the caller adds the heartbeat-age rule.
    """
    current = _start_identity(int(pid))
    if current is None:
        return False
    if started_marker:
        return current == started_marker
    return True


# ── registry state ───────────────────────────────────────────────────────────

_guard = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_last_heartbeat_ok: Optional[str] = None
_lost_listeners: list[Callable[[str], None]] = []


def current_instance_id() -> Optional[str]:
    return db.current_instance_id()


def last_heartbeat_ok() -> Optional[str]:
    return _last_heartbeat_ok


def on_instance_lost(callback: Callable[[str], None]) -> None:
    """Register a callback run with the OLD instance id when this instance is declared
    dead (heartbeat matched 0 rows). Lock/lease contexts mark themselves lost there."""
    with _guard:
        _lost_listeners.append(callback)


def register() -> str:
    """Insert this process's instance row and make it current (L 2.8 on server start)."""
    global _last_heartbeat_ok
    instance_id = "inst_" + secrets.token_hex(16)
    now = now_iso()
    pid = os.getpid()
    db.insert_instance(instance_id, node_key(), pid, _start_identity(pid), now)
    db.set_current_instance_id(instance_id)
    _last_heartbeat_ok = now
    return instance_id


def heartbeat_once() -> Optional[bool]:
    """One heartbeat. True = alive, False = declared dead (re-registered), None = store error.

    A store error is not a death sentence: the row is untouched, and the instance is
    only DEAD to others once its heartbeat_at ages past INSTANCE_DEAD_AFTER_SEC.
    """
    global _last_heartbeat_ok
    instance_id = db.current_instance_id()
    if instance_id is None:
        return None
    now = now_iso()
    try:
        affected = db.heartbeat_instance(instance_id, now)
    except Exception:
        _log.warning("instance heartbeat failed (store error)", exc_info=True)
        return None
    if affected == 1:
        _last_heartbeat_ok = now
        return True
    _log.error("instance_declared_dead: %s (heartbeat matched no alive row)", instance_id)
    with _guard:
        listeners = list(_lost_listeners)
    for callback in listeners:
        try:
            callback(instance_id)
        except Exception:
            _log.warning("instance-lost callback failed", exc_info=True)
    db.set_current_instance_id(None)
    try:
        new_id = register()
        _log.warning("re-registered as new instance %s", new_id)
    except Exception:
        _log.error("instance re-registration failed", exc_info=True)
    return False


def instance_valid_for_self(now: Optional[str] = None) -> bool:
    """Can this process still believe others do not see it as DEAD (L 2.8)?"""
    if _last_heartbeat_ok is None:
        return False
    return valid_for_self(add_seconds(_last_heartbeat_ok, dead_after_sec()), now or now_iso())


def instance_state(instance_id: Optional[str], now: Optional[str] = None) -> str:
    """ALIVE | DEAD for any instance id (L 2.8 instance_state). Empty/unknown = DEAD."""
    if not instance_id:
        return DEAD
    row = db.get_instance(instance_id)
    if row is None or row.get("status") in ("stopped", "dead"):
        return DEAD
    if instance_id == db.current_instance_id():
        return ALIVE
    now = now or now_iso()
    heartbeat_expired = expired_for_others(add_seconds(row["heartbeat_at"], dead_after_sec()), now)
    if row.get("node_key") == node_key():
        if not os_process_alive(row["pid"], row.get("process_started_at")):
            return DEAD
        # pid exists but no start marker to prove identity: fall back to heartbeat age.
        if not row.get("process_started_at") and heartbeat_expired:
            return DEAD
        return ALIVE
    return DEAD if heartbeat_expired else ALIVE


def mark_prior_same_node_dead() -> int:
    """Startup step 2 (L 2.26): this node's earlier instances whose process is gone -> dead."""
    me = db.current_instance_id()
    marked = 0
    for row in db.list_alive_instances(node_key()):
        if row["instance_id"] == me:
            continue
        if os_process_alive(row["pid"], row.get("process_started_at")):
            continue
        marked += db.mark_instance_dead(row["instance_id"])
    return marked


def startup() -> Optional[str]:
    """Register, retire this node's dead predecessors, start the heartbeat thread.

    Idempotent. Registration failure leaves no current instance (legacy rows are then
    stamped NULL = DEAD, exactly like before this unit) and is reported to the caller.
    """
    global _thread
    with _guard:
        if _thread is not None and _thread.is_alive():
            return db.current_instance_id()
        _stop.clear()
    instance_id = register()
    try:
        retired = mark_prior_same_node_dead()
        if retired:
            _log.info("marked %s prior same-node instance(s) dead", retired)
    except Exception:
        _log.warning("prior instance liveness check failed", exc_info=True)

    def loop() -> None:
        while not _stop.wait(heartbeat_interval_sec()):
            try:
                heartbeat_once()
            except Exception:
                _log.warning("instance heartbeat loop error", exc_info=True)

    thread = threading.Thread(target=loop, name="server-instance-heartbeat", daemon=True)
    with _guard:
        _thread = thread
    thread.start()
    return instance_id


def shutdown() -> None:
    """Stop heartbeating and record alive -> stopped (graceful shutdown, L 2.8)."""
    global _thread
    _stop.set()
    with _guard:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=2)
    instance_id = db.current_instance_id()
    if instance_id is None:
        return
    try:
        db.mark_instance_stopped(instance_id, now_iso())
    except Exception:
        _log.warning("instance stop record failed", exc_info=True)
    db.set_current_instance_id(None)
