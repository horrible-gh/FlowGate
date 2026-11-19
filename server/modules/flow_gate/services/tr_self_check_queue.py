"""The Self-check queue (flowgate.v02test.0053 1-B, T0005).

``run_self_check`` puts the request in this queue and returns at once (status ``queued``); the
caller goes on with its work. What the request tests is a copy of the worktree taken at that
moment (the *snapshot*, uncommitted changes included), so the run never takes the Group lock
(G), a Group may have any number of runs queued or running, and nothing written to the worktree
afterwards changes what is tested.

Rules
- Order: oldest request first, projects alternating (``db.claim_next_queued``).
- Workers: queue threads inside this server, as many as the host cap
  (``tr_self_check_policy.host_capacity``); a run starts only when its processor reservation fits
  under that cap -- otherwise it waits (no 409 ``selfcheck_host_capacity_exhausted``).
- The same card (Graph attempt / TR / draft owner, ``card_key``) queuing again cancels its older
  requests that have not started; a started one runs to its end. The insert and that cancel
  are one transaction serialized on the capacity row (``db.create_queued(supersede_code=...)``),
  and only requests older than the new one are cancelled, so concurrent requests of one card
  leave exactly the last stored one waiting.
- Wait limit (``wait_limit_sec``, 30 minutes): a request still waiting is re-queued once by the
  system; waiting past the limit again, it fails (``selfcheck_queue_wait_expired``).
- Restart: queued requests stay in the store. A run that was executing is ended by the existing
  recovery rules (``tr_self_check_service.recover`` / ``reap``) and then queued again with its
  snapshot. A claim whose lease lapsed before anything was spawned goes back to the queue for
  another worker.
- Claim fence: every claim stores a new token (``queue_lease_owner``); the worker passes it to
  every write it makes on the run (spawn stamp, process records, running, terminal, recovery
  hold), so a worker that resumes after its claim was handed over spawns nothing and writes
  nothing. Its lease keeper stops renewing and signals the loss as soon as a renewal fails.
- Snapshot: failing to take one fails only that request, at once, with the reason. Failing to
  remove one leaves ``cleanup_pending`` and the next maintenance pass retries.
- A run whose process tree is not proved gone keeps only its own execution slot (its
  reservation) as recovery_incomplete and is re-checked periodically by the reaper; it holds no
  Group lock.

Server functions for 1-C / 1-D (no HTTP route or screen here):
``card_key``, ``card_status``, ``cancel_waiting``, ``force_stop``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.storage import filesystem as storage_fs

_log = logging.getLogger(__name__)

SNAPSHOT_DIR_ENV = "FLOWGATE_SELFCHECK_SNAPSHOT_DIR"
WAIT_LIMIT_ENV = "FLOWGATE_SELFCHECK_QUEUE_WAIT_SEC"
LEASE_ENV = "FLOWGATE_SELFCHECK_QUEUE_LEASE_SEC"
AUTOSTART_ENV = "FLOWGATE_SELFCHECK_QUEUE_AUTOSTART"
WAIT_LIMIT_DEFAULT = 1800
LEASE_DEFAULT = 60
POLL_SEC = 2.0
MAINTAIN_EVERY_SEC = 30.0
#: Ignored dependency folders the snapshot links to instead of copying (too large to copy; the
#: run reads them, it does not test them).
SHARED_DIRS = frozenset({"node_modules", ".venv", "venv"})
META_FILE = "meta.json"
TREE_DIR = "tree"

# error codes recorded on a queue run
SUPERSEDED = "selfcheck_superseded"
WAIT_EXPIRED = "selfcheck_queue_wait_expired"
SNAPSHOT_FAILED = "selfcheck_snapshot_failed"
SNAPSHOT_MISSING = "selfcheck_snapshot_missing"
CANCELLED_WAITING = "selfcheck_queue_cancelled"
FORCE_STOPPED = "selfcheck_force_stopped"


class QueueError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or code)
        self.code, self.detail = code, detail or code


# ── parameters ──────────────────────────────────────────────────────────────

def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def wait_limit_sec() -> int:
    return _env_int(WAIT_LIMIT_ENV, WAIT_LIMIT_DEFAULT, 1, 24 * 3600)


def lease_sec() -> int:
    return _env_int(LEASE_ENV, LEASE_DEFAULT, 5, 3600)


def worker_count() -> int:
    """The queue's worker threads: the host cap, as before (no separate setting)."""
    return policy.host_capacity()[0]


def snapshot_root() -> Path:
    raw = os.environ.get(SNAPSHOT_DIR_ENV)
    root = Path(raw) if raw else Path(tempfile.gettempdir()) / "flowgate-selfcheck-queue"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(offset_sec: float = 0.0) -> str:
    """A time in the store's ``now_iso`` format (JST, seconds) -- the format of ``enqueued_at``
    and ``queue_lease_until`` -- shifted by ``offset_sec``, so the two compare as strings."""
    from modules.flow_gate.db.connection import now_iso
    return (datetime.fromisoformat(now_iso()) + timedelta(seconds=offset_sec)).isoformat(timespec="seconds")


# ── card identity ───────────────────────────────────────────────────────────

def card_key(*, attempt_id: str | None = None, tr_doc_id: str | None = None,
             owner_token_id: str | None = None) -> str:
    """The queue identity of the card a request comes from: its Graph attempt when it has one,
    else its TR, else (a TR not registered yet) its draft owner token."""
    if attempt_id:
        return f"attempt:{attempt_id}"
    if tr_doc_id:
        return f"tr:{tr_doc_id}"
    if owner_token_id:
        return f"draft:{owner_token_id}"
    raise ValueError("a card needs an attempt, a TR or a draft owner token")


def _target_card(target: dict) -> str:
    return card_key(attempt_id=target.get("attempt_id"), tr_doc_id=target.get("tr_doc_id"),
                    owner_token_id=target.get("owner_token_id"))


# ── snapshot ────────────────────────────────────────────────────────────────

def _git_list(root: Path, *args: str) -> list[str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *args], shell=False,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                            check=False, env=env)
    if result.returncode != 0:
        raise QueueError(SNAPSHOT_FAILED, "git ls-files failed: "
                         + result.stderr.decode("utf-8", "replace").strip()[:300])
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def _inside(base: Path, rel: str) -> Path:
    target = base / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise QueueError(SNAPSHOT_FAILED, f"path outside the tree: {rel!r}")
    return target


def take_snapshot(root: Path, run_id: str) -> Path:
    """Copy the worktree as it is now into ``<snapshot_root>/<run_id>/tree``: tracked files and
    untracked files that are not ignored, as they are on disk (uncommitted changes included,
    deleted files left out). Ignored dependency folders (``SHARED_DIRS``) are linked, not
    copied. ``.git`` is not part of the copy. Returns the run's snapshot folder; on any failure
    the partial copy is removed and ``QueueError(selfcheck_snapshot_failed)`` is raised."""
    base = snapshot_root() / run_id
    tree = base / TREE_DIR
    try:
        tree.mkdir(parents=True, exist_ok=False)
        for rel in _git_list(root, "--cached", "--others", "--exclude-standard"):
            source, target = _inside(root, rel), _inside(tree, rel)
            if source.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(os.readlink(source), target)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            # a tracked file deleted in the worktree is simply not in the snapshot
        for rel in _git_list(root, "--others", "--ignored", "--exclude-standard", "--directory"):
            rel = rel.rstrip("/")
            if Path(rel).name not in SHARED_DIRS:
                continue
            source, target = _inside(root, rel), _inside(tree, rel)
            if source.is_dir() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                _link_dir(source.resolve(), target)
        return base
    except QueueError:
        remove_snapshot_dir(base)
        raise
    except Exception as exc:
        remove_snapshot_dir(base)
        raise QueueError(SNAPSHOT_FAILED, f"{type(exc).__name__}: {exc}"[:300]) from exc


def _link_dir(source: Path, target: Path) -> None:
    """A directory link: a symlink, or on Windows without the symlink right a junction.
    Removing the snapshot removes the link only (``shutil.rmtree`` never follows either)."""
    try:
        os.symlink(source, target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(source), str(target))


def remove_snapshot_dir(path: str | Path | None) -> None:
    """Remove a snapshot folder (inside ``snapshot_root`` only). Raises on failure."""
    if not path:
        return
    storage_fs.remove_managed_tree(Path(path), snapshot_root(), missing_ok=True)


def _write_meta(base: Path, meta: dict) -> None:
    (base / META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def read_meta(row: dict) -> dict:
    base = Path(row.get("snapshot_path") or "")
    try:
        return json.loads((base / META_FILE).read_text(encoding="utf-8"))
    except Exception as exc:
        raise QueueError(SNAPSHOT_MISSING, f"snapshot meta unreadable: {type(exc).__name__}") from exc


def snapshot_exists(row: dict) -> bool:
    base = row.get("snapshot_path")
    return bool(base) and (Path(base) / TREE_DIR).is_dir() and (Path(base) / META_FILE).is_file()


def cleanup_snapshot(row: dict | None) -> bool:
    """Remove an ended run's snapshot; a failure is recorded as ``cleanup_pending`` for the next
    maintenance pass. True = removed (or nothing to remove)."""
    if not row or not row.get("snapshot_path"):
        return True
    rid = row["self_check_run_id"]
    try:
        remove_snapshot_dir(row["snapshot_path"])
    except Exception as exc:
        error = "snapshot_cleanup_failed %s: %s" % (type(exc).__name__, exc)
        _log.warning("selfcheck %s: %s", rid, error)
        try:
            db_runs.set_cleanup_result(rid, cleanup_pending=True, cleanup_error=error)
        except Exception:
            _log.warning("selfcheck %s: recording snapshot cleanup failure failed", rid, exc_info=True)
        return False
    if row.get("cleanup_pending"):
        db_runs.set_cleanup_result(rid, cleanup_pending=False, cleanup_error=None)
    return True


# ── enqueue (run_self_check) ────────────────────────────────────────────────

def _env_keys(path_value: str, base: Path) -> list[str]:
    probe = Path(tempfile.mkdtemp(prefix="envkeys-", dir=str(base)))
    try:
        return [*policy.scrubbed_env(path_value, probe), *policy.SLOT_ENV_KEYS]
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def enqueue(target: dict, *, program: str, args: list[str], relative_cwd: str, timeout: int,
            share: int, command: "policy.ResolvedCommand", path_value: str, root: Path,
            requested_by: str | None) -> dict:
    """Queue one request: snapshot the worktree, insert the ``queued`` row and cancel the card's
    older waiting requests in the same transaction, wake a worker. Returns the stored row. No
    Group lock is taken.

    A snapshot that cannot be taken fails this request only: the row is recorded as
    ``failed(selfcheck_snapshot_failed)`` with the reason in ``stderr_tail`` and returned."""
    from modules.flow_gate.services import tr_self_check_proof as proof
    from modules.flow_gate.services import tr_self_check_service as service

    run_id = f"scr_{uuid.uuid4().hex[:24]}"
    key = _target_card(target)
    before = service._probe(root)
    snapshot_error = None
    base = snapshot_root() / run_id
    env_keys: list[str] = []
    try:
        take_snapshot(root, run_id)
        _write_meta(base, {"path_value": path_value, "argv_prefix": list(command.argv_prefix),
                           "source_before": before, "worktree": str(root), "program": program})
        env_keys = _env_keys(path_value, base)
    except Exception as exc:
        snapshot_error = exc if isinstance(exc, QueueError) else QueueError(
            SNAPSHOT_FAILED, f"{type(exc).__name__}: {exc}"[:300])
        try:
            remove_snapshot_dir(base)
        except Exception:
            _log.warning("selfcheck %s: removing a partial snapshot failed", run_id)
    try:
        row = db_runs.create_queued(
            target["project_id"], target["group_id"], target.get("tr_doc_id"), requested_by,
            policy.POLICY_VERSION, program, args, command.executable, command.name, command.origin,
            relative_cwd, timeout, env_keys, run_id=run_id, snapshot_path=str(base), queue_key=key,
            owner_token_id=target.get("owner_token_id"), draft_doc_ref=target.get("draft_doc_ref"),
            attempt_id=target.get("attempt_id"), input_commit=target.get("input_commit"),
            cpu_share_requested=share, owner_marker=proof.owner_marker(run_id), source_before=before,
            progress_owner=target.get("progress_owner"),
            # rev2: a request without a snapshot supersedes nothing (it fails at once below).
            supersede_code=None if snapshot_error is not None else SUPERSEDED)
    except Exception:
        try:
            remove_snapshot_dir(base)
        except Exception:
            _log.warning("selfcheck %s: removing the snapshot of a refused request failed", run_id)
        raise
    if snapshot_error is not None:
        row.pop("superseded_runs", None)
        failed = db_runs.fail_queued(run_id, SNAPSHOT_FAILED, snapshot_error.detail)
        row = failed or db_runs.get_run(run_id) or row
        _log.warning("selfcheck %s: snapshot failed: %s", run_id, snapshot_error.detail)
        service._emit(row)
        return row
    for ended in row.pop("superseded_runs", None) or []:
        cleanup_snapshot(ended)
        service._emit(ended)
    service._emit(row)
    wake()
    return row


# ── cancel / force stop / status (for 1-C, 1-D) ─────────────────────────────

def cancel_waiting(run_id: str, reason: str = CANCELLED_WAITING) -> dict | None:
    """Cancel a request that has not started (``queued -> cancelled``, error ``reason``) and
    remove its snapshot. None = the run is not waiting (already started or ended)."""
    from modules.flow_gate.services import tr_self_check_service as service
    ended = db_runs.cancel_queued(run_id, reason)
    if ended is None:
        return None
    cleanup_snapshot(ended)
    service._emit(ended)
    return db_runs.get_run(run_id) or ended


#: run id -> error code a forced stop records instead of ``cancelled`` (read by
#: ``tr_self_check_service._finish``).
_forced: dict[str, str] = {}
_forced_lock = threading.Lock()


def forced_code(run_id: str) -> str | None:
    with _forced_lock:
        return _forced.get(run_id)


def force_stop(run_id: str, reason: str = FORCE_STOPPED, wait_sec: float = 10.0) -> dict:
    """Stop a request whatever its state: a waiting one is cancelled (``cancel_waiting``); an
    executing one has its whole process group / Job killed, its slot (reservation) freed and is
    recorded ``failed`` with ``reason``. The run must be driven by this server; one driven by
    another server (or none) goes through the ordinary cancel and its proof rules. Returns the
    run as stored when this returns."""
    from modules.flow_gate.services import tr_self_check_service as service
    row = db_runs.get_run(run_id)
    if row is None:
        raise QueueError("selfcheck_run_not_found")
    if row["status"] == db_runs.QUEUED:
        return cancel_waiting(run_id, reason) or db_runs.get_run(run_id)
    if row["status"] not in ("pending", "running"):
        return row
    with _forced_lock:
        _forced[run_id] = reason
    try:
        db_runs.request_cancel(run_id)
        with service._controls_lock:
            item = service._controls.get(run_id)
        if item is None:
            try:
                service._cancel(run_id, lambda: db_runs.get_run(run_id))
            except service.SelfCheckError:
                pass
            return db_runs.get_run(run_id)
        item[1].set()
        item[0].cancel()
        deadline = _now() + timedelta(seconds=wait_sec)
        while _now() < deadline:
            current = db_runs.get_run(run_id)
            if current is None or current["status"] not in ("pending", "running"):
                return current
            threading.Event().wait(0.1)
        return db_runs.get_run(run_id)
    finally:
        with _forced_lock:
            _forced.pop(run_id, None)


def _brief(row: dict) -> dict:
    return {"self_check_run_id": row["self_check_run_id"], "status": row["status"],
            "enqueued_at": row.get("enqueued_at"), "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"), "exit_code": row.get("exit_code"),
            "timed_out": row.get("timed_out"), "error_code": row.get("error_code"),
            "recovery_state": row.get("recovery_state"), "tr_doc_id": row.get("tr_doc_id")}


def card_status(key: str, limit: int = 20) -> dict:
    """A card's queue state: ``state`` is ``running`` (a run executes; ``started_at`` set),
    ``queued`` (waiting only), ``ended`` (its last run ended) or ``idle`` (no run), with the
    waiting runs (and their place in the whole queue), the executing runs and the last ended run."""
    rows = db_runs.card_runs(key, limit=limit)
    waiting = [dict(_brief(r), position=db_runs.queue_position(r["self_check_run_id"]))
               for r in reversed(rows) if r["status"] == db_runs.QUEUED]
    running = [_brief(r) for r in rows if r["status"] in ("pending", "running")]
    ended = next((_brief(r) for r in rows if r["status"] in db_runs.QUEUE_TERMINAL), None)
    state = "running" if running else "queued" if waiting else "ended" if ended else "idle"
    return {"card_key": key, "state": state, "queued": waiting, "running": running, "last_ended": ended}


# ── workers ─────────────────────────────────────────────────────────────────

_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()
_last_maintain = [0.0]


def autostart_enabled() -> bool:
    """Whether the server's startup starts the queue workers (``FLOWGATE_SELFCHECK_QUEUE_AUTOSTART``,
    default on)."""
    return (os.environ.get(AUTOSTART_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def wake() -> None:
    """A request was queued or a slot freed: wake a sleeping worker. Workers are started by the
    server's startup (``startup.recover_git_sessions``) or explicitly by ``start``; a request
    queued while none runs waits in the store (the wait limit still applies)."""
    _wake.set()


def start(count: int | None = None) -> int:
    """Start the queue workers (idempotent). Returns the number of live workers."""
    with _threads_lock:
        _threads[:] = [t for t in _threads if t.is_alive()]
        if _threads:
            return len(_threads)
        _stop.clear()
        for index in range(count or worker_count()):
            thread = threading.Thread(target=_worker_loop, daemon=True, name=f"selfcheck-queue-{index}")
            thread.start()
            _threads.append(thread)
        return len(_threads)


def shutdown(timeout: float = 5.0) -> None:
    _stop.set()
    _wake.set()
    with _threads_lock:
        threads = list(_threads)
        _threads.clear()
    for thread in threads:
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)


def _worker_loop() -> None:
    while not _stop.is_set():
        handled = False
        try:
            if _due_maintenance():
                maintain()
            handled = dispatch_once()
        except Exception:
            _log.warning("selfcheck queue worker step failed", exc_info=True)
        if not handled:
            _wake.wait(POLL_SEC)
            _wake.clear()


def _due_maintenance() -> bool:
    import time
    now = time.monotonic()
    if now - _last_maintain[0] < MAINTAIN_EVERY_SEC:
        return False
    _last_maintain[0] = now
    return True


def _instance() -> tuple[str, str | None]:
    """This server's instance id (registered lazily, as a lock acquire does, when the process
    never ran server startup) -- the owner recovery proves a claimed run by -- and its host."""
    from modules.flow_gate.services.git import instance_registry, lock_manager
    instance_id = (instance_registry.current_instance_id() or lock_manager._register_lazily()
                   or f"proc:{os.getpid()}")
    return instance_id, instance_registry.node_key()


def dispatch_once() -> bool:
    """Claim the next queued run this host has room for and drive it to its end (in this
    thread). False = nothing was claimed."""
    instance_id, node = _instance()
    row = db_runs.claim_next_queued(instance_id, _stamp(lease_sec()), node, policy.host_capacity())
    if row is None:
        return False
    execute(row, instance_id)
    return True


class _LeaseKeeper:
    """Renews a worker's claim every third of the lease from the moment it is claimed. A renewal
    that matches nothing (``renew_lease`` False: the claim was handed over or the run ended)
    sets ``lost`` and stops renewing; the worker stops on it (``execute``, ``_finish``)."""

    def __init__(self, run_id: str, claim: str):
        self.run_id, self.claim = run_id, claim
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"selfcheck-lease-{run_id}")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)

    def renew(self) -> bool:
        try:
            held = db_runs.renew_lease(self.run_id, self.claim, _stamp(lease_sec()))
        except Exception:
            _log.warning("selfcheck %s: lease renewal failed", self.run_id, exc_info=True)
            return True                      # unknown: the store fences the writes anyway
        if not held and not self._stop.is_set():
            _log.warning("selfcheck %s: queue claim lost (lease handed over or run ended)", self.run_id)
            self.lost.set()
        return held

    def _loop(self) -> None:
        interval = max(1.0, lease_sec() / 3.0)
        while not self._stop.wait(interval):
            if not self.renew():
                return


def execute(row: dict, instance_id: str) -> dict | None:
    """Drive one claimed run in its snapshot through ``tr_self_check_service._finish`` (the same
    spawn / wait / tree proof / terminal record as before, without any lock). The run executes
    on a thread named ``selfcheck-<run_id>`` -- the name the reaper reads as "its worker is
    alive" -- and this thread waits for it. The snapshot is removed once the run has ended; an
    unsettled run keeps it (it may be queued again after recovery).

    rev2: ``row`` is the claimed row; its ``queue_lease_owner`` is this worker's claim. The lease
    is renewed from the start (preparation included) and every write is fenced to the claim. A
    worker whose claim is gone (its lease lapsed and the run was handed over) returns without
    spawning or writing anything; the run belongs to the worker that took it over."""
    from modules.flow_gate.services import tr_self_check_proof as proof
    from modules.flow_gate.services import tr_self_check_service as service

    rid = row["self_check_run_id"]
    claim = row.get("queue_lease_owner") or instance_id
    runtime = None
    with _LeaseKeeper(rid, claim) as keeper:
        try:
            if not snapshot_exists(row):
                raise QueueError(SNAPSHOT_MISSING, "the snapshot folder is gone")
            meta = read_meta(row)
            tree = Path(row["snapshot_path"]) / TREE_DIR
            cwd = policy.validate_cwd(tree, row["cwd_relative"])
            runtime = Path(tempfile.mkdtemp(prefix=f"flowgate-{rid}-"))
            env = policy.scrubbed_env(meta["path_value"], runtime)
            env[proof.MARKER_ENV] = rid
            if row.get("cpu_reservation"):
                env.update(policy.slot_pool_env(runtime, row["cpu_reservation"]))
            command = policy.ResolvedCommand(row["resolved_executable_path"], tuple(meta.get("argv_prefix") or ()),
                                             row["resolved_executable_name"], row["executable_origin"],
                                             meta["path_value"])
            before = meta["source_before"]
        except Exception as exc:
            code = exc.code if isinstance(exc, (QueueError, policy.PolicyError)) else "selfcheck_internal_error"
            _log.warning("selfcheck %s: cannot start from its snapshot: %s", rid, exc)
            failed = db_runs.finish_failed(rid, error_code=code, claim=claim,
                                           stderr_tail=getattr(exc, "detail", None) or str(exc))
            if runtime is not None:
                service._cleanup_runtime(runtime, rid, tracked=failed is not None)
            if failed is not None:
                service._emit(failed)
                cleanup_snapshot(failed)
            return failed
        if keeper.lost.is_set() or not keeper.renew():
            # The claim went away while this worker prepared: nothing spawned, nothing written.
            service._cleanup_runtime(runtime, rid, tracked=False)
            return db_runs.get_run(rid)
        worker = threading.Thread(target=service._finish,
                                  args=(rid, None, None, None, before, runtime, command,
                                        list(row.get("args") or []), cwd, env, row["timeout_seconds"]),
                                  kwargs={"claim": claim, "lease_lost": keeper.lost},
                                  daemon=True, name=f"selfcheck-{rid}")
        worker.start()
        worker.join()
    final = db_runs.get_run(rid)
    if final is not None and final["status"] in db_runs.QUEUE_TERMINAL and final.get("queue_lease_owner") == claim:
        cleanup_snapshot(final)
    wake()
    return final


# ── maintenance (worker loop and the sweeper's reaper step) ─────────────────

def maintain() -> dict:
    """One pass of the queue rules that depend on time: lapsed claims back to the queue, the
    wait limit (re-queue once, then fail), and snapshot cleanups that failed before."""
    from modules.flow_gate.services import tr_self_check_service as service
    out = {"released": 0, "requeued": 0, "expired": 0, "cleaned": 0}
    try:
        out["released"] = len(db_runs.release_expired_claims(_stamp()))
    except Exception:
        _log.warning("selfcheck queue: releasing lapsed claims failed", exc_info=True)
    cutoff = _stamp(-wait_limit_sec())
    for row in db_runs.queued_waiting_since(cutoff):
        rid = row["self_check_run_id"]
        try:
            if int(row.get("queue_requeue_count") or 0) < 1:
                again = db_runs.requeue_waited(rid, row["enqueued_at"])
                if again is not None:
                    out["requeued"] += 1
                    _log.warning("selfcheck %s: waited past the queue limit; queued again once", rid)
                    service._emit(again)
                continue
            failed = db_runs.fail_queued(rid, WAIT_EXPIRED)
            if failed is not None:
                out["expired"] += 1
                _log.warning("selfcheck %s: waited past the queue limit twice; failed", rid)
                cleanup_snapshot(failed)
                service._emit(failed)
        except Exception:
            _log.warning("selfcheck %s: queue wait rule failed", rid, exc_info=True)
    for row in db_runs.snapshot_cleanup_pending():
        if cleanup_snapshot(row):
            out["cleaned"] += 1
    if out["released"]:
        wake()
    return out
