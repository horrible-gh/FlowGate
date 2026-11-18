"""Server WARNING / ERROR records in the DB (flowgate.v02test.0050 T0005).

Nobody reads ``logs/default.log``. Every log record of WARNING and above is kept in one table,
``server_logs``, so an AI asked to "look at the server errors" can read it with plain SQL.
``logs/default.log`` and the console stay exactly as they were (decision 8).

How a record gets there
-----------------------
* :class:`ServerLogHandler` (level WARNING) is attached once to the root logger and to
  ``uvicorn.error`` by :func:`install` (decision 2). ``uvicorn.error`` needs its own copy because
  uvicorn's logging config stops it at the ``uvicorn`` logger (``propagate: False``).
  ``LogAssist.log`` keeps ``propagate`` on (LogAssist 1.1.4 never changes it), so its records reach
  the root copy and need no handler of their own. A record that passes two copies (``uvicorn.error``
  when uvicorn did not configure logging and the record also reaches the root) is stored once: the
  first copy marks the record.
* ``emit`` only builds a small dict (location, redacted message, exception summary, request id,
  fingerprint) and puts it on a bounded queue. A full queue drops the entry and counts it
  (decision 3). Nothing is written in the caller's thread, so a request's transaction rolling back
  cannot take the row with it, and the response is never delayed.
* One daemon thread drains the queue and writes through the store **in its own thread**, i.e. in
  its own transaction and connection (the store's transaction is thread-local). It folds repeats:
  the same fingerprint inside :data:`REPEAT_WINDOW_SECONDS` of the row's first occurrence raises
  ``repeat_count`` / ``last_seen_at`` instead of adding a row (decision 5). Once a day it deletes
  rows whose last occurrence is older than :data:`RETENTION_DAYS` (decision 7). At exit it tries
  to drain what is left (atexit).
* Records emitted by the writer thread itself (the DB layer it calls included) are never queued,
  and a record emitted while ``emit`` is running is ignored, so writing a log row can never log
  again (decision 4). A failed write prints one line to stderr and drops the batch. Nothing here
  raises into the caller.

What a row keeps (decision 6)
-----------------------------
The message and the exception text pass the shared credential rule
(:mod:`util.credential_redaction`, the ``chat_errors.redact`` rule). The
traceback is reduced to ``file:line:function`` per frame -- no source line, no local variable.
In ``record(extra=...)`` every string passes the same rule and the whole value under a credential key
(``token`` / ``password`` / ``api_key`` / ``Authorization`` / ..., :func:`util.credential_redaction.is_secret_key`)
becomes ``***``, at any depth. Request headers and bodies are never read.

The request id is the one of the request being handled; for uvicorn's ``Exception in ASGI application``
(logged after the request ended) it is the id ``RequestIdMiddleware`` tagged on the escaped exception.

Writing a row directly
----------------------
:func:`record` is the public entry for rows that do not come from ``logging`` (the browser
report of the next card uses ``source='browser'``). It goes through the same queue, folding and
redaction.

Reading
-------
There is no screen or API (out of scope). Example::

    SELECT last_seen_at, level, repeat_count, logger_name, location, message, exc_type, request_id
      FROM server_logs
     WHERE source = 'server' AND last_seen_at >= '2026-11-19T00:00:00+09:00'
     ORDER BY last_seen_at DESC
     LIMIT 50;
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from util.credential_redaction import MASK, is_secret_key, redact_text

TABLE = "server_logs"
QUEUE_MAX = 10_000
BATCH_MAX = 500
REPEAT_WINDOW_SECONDS = 10 * 60
RETENTION_DAYS = 30
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
MESSAGE_MAX_CHARS = 4000
EXC_TEXT_MAX_CHARS = 4000
EXTRA_MAX_CHARS = 8000
MAX_FRAMES = 30
MAX_CAUSES = 4
SOURCE_DEFAULT = "server"
SELF_LOGGER = "flowgate.server_logs"

_JST = timezone(timedelta(hours=9))
_SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_LOGGING_DIR = os.path.dirname(os.path.abspath(logging.__file__))
_LOGASSIST_NAME = "LogAssist.log"
_SEEN_ATTR = "_flowgate_server_log_seen"

_local = threading.local()
_lock = threading.Lock()
_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
_writer: Optional["_Writer"] = None
_atexit_registered = False
_stats = {"queued": 0, "dropped": 0, "written": 0, "failed_batches": 0}


# ── row building (runs in the caller's thread, must stay cheap and never raise) ──────────────

def _iso(epoch: float) -> str:
    """JST ISO seconds -- the format of ``connection.now_iso()``, so string comparison is time order."""
    return datetime.fromtimestamp(epoch, _JST).isoformat(timespec="seconds")


def _relative_path(path: Optional[str]) -> str:
    """A path relative to the repository root, the package root for installed libraries, else the file name."""
    if not path:
        return "?"
    try:
        absolute = os.path.abspath(path)
    except Exception:  # noqa: BLE001
        return os.path.basename(str(path))
    for root in (_REPO_ROOT,):
        try:
            if os.path.commonpath([absolute, root]) == root:
                rel = os.path.relpath(absolute, root).replace(os.sep, "/")
                marker = rel.find("site-packages/")
                return rel[marker + len("site-packages/"):] if marker >= 0 else rel
        except ValueError:  # another drive on Windows
            pass
    normalized = absolute.replace(os.sep, "/")
    for marker in ("site-packages/", "dist-packages/"):
        index = normalized.find(marker)
        if index >= 0:
            return normalized[index + len(marker):]
    if os.path.dirname(absolute) == _LOGGING_DIR or normalized.find("/lib/python") >= 0:
        return "stdlib/" + os.path.basename(absolute)
    return os.path.basename(absolute)


def _is_logging_frame(filename: str) -> bool:
    try:
        absolute = os.path.abspath(filename)
    except Exception:  # noqa: BLE001
        return False
    if os.path.dirname(absolute) == _LOGGING_DIR:
        return True
    normalized = absolute.replace(os.sep, "/")
    return "/LogAssist/" in normalized or absolute == os.path.abspath(__file__)


def _caller_of_logassist() -> Optional[tuple[str, int, str]]:
    """The frame that called a ``LogAssist.log`` helper (its own records all point at log.py)."""
    try:
        frame = sys._getframe(1)
    except Exception:  # noqa: BLE001
        return None
    depth = 0
    while frame is not None and depth < 60:
        if not _is_logging_frame(frame.f_code.co_filename):
            return frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name
        frame = frame.f_back
        depth += 1
    return None


def _type_name(exc_type: Any) -> str:
    module = getattr(exc_type, "__module__", "") or ""
    name = getattr(exc_type, "__qualname__", None) or getattr(exc_type, "__name__", None) or str(exc_type)
    return name if module in ("builtins", "") else f"{module}.{name}"


def _exception_summary(exc: BaseException) -> str:
    """Chained ``Type: message`` lines (redacted) and frames as ``file:line:function`` -- never source lines or locals."""
    lines: list[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    causes = 0
    while current is not None and id(current) not in seen and causes < MAX_CAUSES:
        seen.add(id(current))
        if causes:
            lines.append("caused by:")
        try:
            text = str(current)
        except Exception:  # noqa: BLE001
            text = "<unprintable>"
        text = redact_text(text, 1000)
        lines.append(f"{_type_name(type(current))}: {text}" if text else _type_name(type(current)))
        frames = []
        tb = current.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append(f"  at {_relative_path(code.co_filename)}:{tb.tb_lineno}:{code.co_name}")
            tb = tb.tb_next
        if len(frames) > MAX_FRAMES:  # keep the innermost frames: that is where it broke
            frames = [f"  ... {len(frames) - MAX_FRAMES} outer frame(s)"] + frames[-MAX_FRAMES:]
        lines.extend(frames)
        current = current.__cause__ or current.__context__
        causes += 1
    return "\n".join(lines)[:EXC_TEXT_MAX_CHARS]


def _current_request_id() -> Optional[str]:
    try:
        from modules.flow_gate.api.request_observability import current_request_id
        return current_request_id()
    except Exception:  # noqa: BLE001
        return None


def _exception_request_id(exc: Optional[BaseException]) -> Optional[str]:
    """The request id ``RequestIdMiddleware`` tagged on an exception that left its request.

    uvicorn logs ``Exception in ASGI application`` after the middleware has reset its contextvar, so the
    record itself carries no request; the exception it logs does.
    """
    if exc is None:
        return None
    try:
        from modules.flow_gate.api.request_observability import request_id_of_exception
        return request_id_of_exception(exc)
    except Exception:  # noqa: BLE001
        return None


def _fingerprint(level: str, logger_name: str, location: str, func_name: str, template: str, source: str) -> str:
    raw = "\x1f".join((source, level, logger_name, location, func_name, template))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _clean_extra(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "..."
    if isinstance(value, str):
        return redact_text(value, MESSAGE_MAX_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        # A credential key (token / password / api_key / Authorization / ...) hides its whole value, nested
        # containers included: the text rule cannot see a name and a value that sit apart in a mapping.
        return {
            redact_text(str(k), 200): (MASK if v is not None and is_secret_key(k) else _clean_extra(v, depth + 1))
            for k, v in list(value.items())[:200]
        }
    if isinstance(value, (list, tuple, set)):
        return [_clean_extra(v, depth + 1) for v in list(value)[:200]]
    return redact_text(repr(value), MESSAGE_MAX_CHARS)


def _extra_json(extra: Any) -> Optional[str]:
    if extra is None:
        return None
    try:
        text = json.dumps(_clean_extra(extra), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        text = json.dumps({"unserializable": redact_text(repr(extra), 1000)}, ensure_ascii=False)
    if len(text) > EXTRA_MAX_CHARS:
        keep = EXTRA_MAX_CHARS - 100
        while True:  # the preview is re-escaped inside the wrapper, so shrink until the whole fits
            capped = json.dumps({"truncated": True, "preview": text[:keep]}, ensure_ascii=False)
            if len(capped) <= EXTRA_MAX_CHARS or keep <= 0:
                return capped
            keep = keep * 3 // 4
    return text


def _entry_from_record(record: logging.LogRecord) -> Optional[dict]:
    if record.name == _LOGASSIST_NAME and record.funcName in ("error", "critical", "exception"):
        try:
            text = str(record.msg)
        except Exception:  # noqa: BLE001
            text = ""
        # LogAssist's error()/critical()/exception() write the message and then, as a second record, the
        # formatted traceback of an exception it raises itself only to have one. That second record repeats
        # the first and is the very traceback text decision 6 keeps out of the table.
        if text.startswith("Traceback (most recent call last):"):
            return None
    path, line, func = record.pathname, record.lineno, record.funcName
    if record.name == _LOGASSIST_NAME:
        caller = _caller_of_logassist()
        if caller is not None:
            path, line, func = caller
    location = f"{_relative_path(path)}:{line}"
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 -- a bad %-format must not lose the record
        message = f"{record.msg!r} % {record.args!r}"
    try:
        template = str(record.msg)
    except Exception:  # noqa: BLE001
        template = message
    exc_type = exc_text = None
    exc_info = record.exc_info
    if exc_info and exc_info[1] is not None:
        exc_type = _type_name(exc_info[0] or type(exc_info[1]))[:191]
        exc_text = _exception_summary(exc_info[1])
    request_id = _current_request_id() or _exception_request_id(exc_info[1] if exc_info else None)
    level = logging.getLevelName(record.levelno) if isinstance(record.levelno, int) else str(record.levelname)
    name = str(record.name or "root")[:191]
    func_name = str(func or "")[:191]
    return {
        "time": float(record.created or time.time()),
        "level": str(level)[:16],
        "logger_name": name,
        "location": location[:512],
        "func_name": func_name,
        "message": redact_text(message, MESSAGE_MAX_CHARS),
        "exc_type": exc_type,
        "exc_text": exc_text,
        "request_id": (request_id or None),
        "source": SOURCE_DEFAULT,
        "extra_json": None,
        "fingerprint": _fingerprint(str(level), name, location, func_name, template[:1000], SOURCE_DEFAULT),
    }


# ── the handler ──────────────────────────────────────────────────────────────────────────────

def _other_handlers_found(record: logging.LogRecord) -> bool:
    """Whether logging's ``callHandlers`` walk finds a handler other than ours for ``record``.

    ``callHandlers`` counts every handler it meets, whatever that handler's level, and calls ``lastResort`` only when
    it met none. So a handler that is there but above the record's level still silences the fallback, exactly as
    before this module was installed.
    """
    logger: Optional[logging.Logger] = logging.getLogger(record.name) if record.name != "root" else logging.getLogger()
    while logger is not None:
        for handler in logger.handlers:
            if type(handler).__name__ != ServerLogHandler.__name__:
                return True
        if not logger.propagate:
            return False
        logger = logger.parent
    return False


class ServerLogHandler(logging.Handler):
    """Queues WARNING+ records for the ``server_logs`` table. Never raises, never blocks."""

    def __init__(self, level: int = logging.WARNING):
        super().__init__(level=level)

    def handle(self, record: logging.LogRecord):  # no handler lock: emit only touches a thread-safe queue
        rv = self.filter(record)
        if not rv:
            return rv
        if isinstance(rv, logging.LogRecord):  # Python 3.12+: a filter may hand back a replacement record
            record = rv
        self.emit(record)
        return rv

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_local, "busy", False) or getattr(_local, "is_writer", False):
            return
        _local.busy = True
        try:
            if getattr(record, _SEEN_ATTR, False):
                return
            try:
                setattr(record, _SEEN_ATTR, True)
            except Exception:  # noqa: BLE001
                pass
            # The root logger had no handler, so logging's last-resort handler printed plain-module WARNINGs to
            # stderr. A handler on the root turns that fallback off; keep the console as it was (decision 8): print
            # through lastResort only when no other handler exists on the path (the callHandlers condition).
            if logging.lastResort is not None and record.levelno >= logging.lastResort.level \
                    and not _other_handlers_found(record):
                try:
                    logging.lastResort.handle(record)
                except Exception:  # noqa: BLE001
                    pass
            entry = _entry_from_record(record)
            if entry is not None:
                _enqueue(entry)
        except Exception:  # noqa: BLE001 -- logging must never break the caller
            pass
        finally:
            _local.busy = False


# ── queue and writer ─────────────────────────────────────────────────────────────────────────

def _enqueue(entry: dict) -> bool:
    _ensure_writer()
    try:
        _queue.put_nowait(entry)
    except queue.Full:
        with _lock:
            _stats["dropped"] += 1
        return False
    with _lock:
        _stats["queued"] += 1
    return True


def _stderr(line: str) -> None:
    try:
        sys.__stderr__.write(f"[server_logs] {line}\n")
        sys.__stderr__.flush()
    except Exception:  # noqa: BLE001
        pass


class _Writer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="flowgate-server-logs", daemon=True)
        self.stop_event = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self.recent: dict[str, tuple[int, float]] = {}  # fingerprint -> (row id, first occurrence epoch)
        self.next_purge = 0.0
        self.reported_drops = 0

    def run(self) -> None:
        _local.is_writer = True
        while not self.stop_event.is_set():
            try:
                first = _queue.get(timeout=1.0)
            except queue.Empty:
                self._maybe_purge()
                continue
            self.idle.clear()
            batch = [first]
            while len(batch) < BATCH_MAX:
                try:
                    batch.append(_queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self._write(batch)
                self._maybe_purge()
            finally:
                for _ in batch:
                    _queue.task_done()
                if _queue.empty():
                    self.idle.set()

    def _drop_notice(self) -> Optional[dict]:
        with _lock:
            dropped = _stats["dropped"]
        fresh = dropped - self.reported_drops
        if fresh <= 0:
            return None
        self.reported_drops = dropped
        message = f"server_logs queue full: {fresh} record(s) dropped"
        return {
            "time": time.time(), "level": "WARNING", "logger_name": SELF_LOGGER,
            "location": f"{_relative_path(__file__)}:0", "func_name": "_write", "message": message,
            "exc_type": None, "exc_text": None, "request_id": None, "source": SOURCE_DEFAULT,
            "extra_json": json.dumps({"dropped": fresh}),
            "fingerprint": _fingerprint("WARNING", SELF_LOGGER, "queue", "_write", "dropped", SOURCE_DEFAULT),
        }

    def _write(self, batch: list[dict]) -> None:
        notice = self._drop_notice()
        if notice is not None:
            batch = batch + [notice]
        groups: dict[str, list[dict]] = {}
        for entry in batch:
            groups.setdefault(entry["fingerprint"], []).append(entry)
        try:
            learned = write_groups(groups, self.recent)
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _stats["failed_batches"] += 1
            _stderr(f"write failed, {len(batch)} record(s) dropped: {type(exc).__name__}: "
                    f"{redact_text(str(exc), 300)}")
            return
        self.recent.update(learned)
        horizon = time.time() - REPEAT_WINDOW_SECONDS
        if len(self.recent) > 5000:
            self.recent = {fp: v for fp, v in self.recent.items() if v[1] >= horizon}
        with _lock:
            _stats["written"] += len(batch)

    def _maybe_purge(self) -> None:
        now = time.time()
        if now < self.next_purge:
            return
        self.next_purge = now + PURGE_INTERVAL_SECONDS
        try:
            purge_expired()
        except Exception as exc:  # noqa: BLE001
            _stderr(f"retention purge failed: {type(exc).__name__}: {redact_text(str(exc), 300)}")


def _store():
    from modules.flow_gate.db.connection import get_store
    return get_store()


def write_groups(groups: dict[str, list[dict]], recent: dict[str, tuple[int, float]]) -> dict[str, tuple[int, float]]:
    """Write one batch in one transaction; returns the fingerprint cache entries it learned (only on success)."""
    learned: dict[str, tuple[int, float]] = {}
    store = _store()
    with store.transaction():
        for fingerprint, entries in groups.items():
            browser_report = entries[0]["source"] == "browser" and entries[0]["logger_name"] == "browser.tree_read"
            if browser_report:
                if store._fetch_one(f"SELECT id FROM {TABLE} WHERE fingerprint = ? LIMIT 1", [fingerprint]):
                    continue
                entries = entries[:1]
            entries.sort(key=lambda e: e["time"])
            pending = entries
            while pending:
                head = pending[0]
                known = learned.get(fingerprint) or recent.get(fingerprint)
                if known is not None and head["time"] - known[1] >= REPEAT_WINDOW_SECONDS:
                    known = None
                if known is None:
                    row = store._fetch_one(
                        f"SELECT id, first_seen_at FROM {TABLE} WHERE fingerprint = ? AND first_seen_at >= ? "
                        "ORDER BY id DESC LIMIT 1",
                        [fingerprint, _iso(head["time"] - REPEAT_WINDOW_SECONDS)],
                    )
                    if row is not None:
                        known = (int(row["id"]), _epoch(row["first_seen_at"], head["time"]))
                if known is not None:
                    row_id, first_time = known
                    taken = [e for e in pending if e["time"] - first_time < REPEAT_WINDOW_SECONDS] or [head]
                    last = _iso(taken[-1]["time"])
                    affected = store._execute_affected(
                        f"UPDATE {TABLE} SET repeat_count = repeat_count + ?, "
                        "last_seen_at = CASE WHEN last_seen_at < ? THEN ? ELSE last_seen_at END WHERE id = ?",
                        [len(taken), last, last, row_id],
                    )
                    if affected:
                        learned[fingerprint] = (row_id, first_time)
                        pending = pending[len(taken):]
                        continue
                    # The remembered row is gone (deleted by hand or by retention): start a new one.
                    recent.pop(fingerprint, None)
                    learned.pop(fingerprint, None)
                first_time = head["time"]
                taken = [e for e in pending if e["time"] - first_time < REPEAT_WINDOW_SECONDS]
                store._execute(
                    f"INSERT INTO {TABLE} (first_seen_at, last_seen_at, level, logger_name, location, func_name, "
                    "message, exc_type, exc_text, request_id, source, repeat_count, fingerprint, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [_iso(first_time), _iso(taken[-1]["time"]), head["level"], head["logger_name"],
                     head["location"], head["func_name"], head["message"], head["exc_type"], head["exc_text"],
                     head["request_id"], head["source"], len(taken), fingerprint, head["extra_json"]],
                )
                row = store._fetch_one(
                    f"SELECT id FROM {TABLE} WHERE fingerprint = ? AND first_seen_at = ? ORDER BY id DESC LIMIT 1",
                    [fingerprint, _iso(first_time)],
                )
                if row is not None:
                    learned[fingerprint] = (int(row["id"]), first_time)
                pending = pending[len(taken):]
    return learned


def _epoch(value: Any, fallback: float) -> float:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_JST)
        return parsed.timestamp()
    except Exception:  # noqa: BLE001
        return fallback


def purge_expired(days: int = RETENTION_DAYS, now: Optional[float] = None) -> None:
    """Delete rows whose last occurrence is older than ``days`` days (decision 7)."""
    cutoff = _iso((now if now is not None else time.time()) - days * 24 * 60 * 60)
    store = _store()
    with store.transaction():
        store._execute(f"DELETE FROM {TABLE} WHERE last_seen_at < ?", [cutoff])


def _ensure_writer() -> None:
    global _writer, _atexit_registered
    if _writer is not None and _writer.is_alive():
        return
    with _lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = _Writer()
        _writer.start()
        if not _atexit_registered:
            atexit.register(flush, 2.0)
            _atexit_registered = True


# ── public API ───────────────────────────────────────────────────────────────────────────────

def record(source: str, level: Any, message: str, request_id: Optional[str] = None, extra: Any = None, *,
           logger_name: Optional[str] = None, location: Optional[str] = None) -> bool:
    """Queue one row that does not come from ``logging`` (e.g. ``source='browser'``). Never raises.

    ``level`` is a logging level number or name (``'WARNING'`` / ``'ERROR'`` / ...); ``message`` and every string
    in ``extra`` are redacted, ``extra`` is stored as JSON (capped at :data:`EXTRA_MAX_CHARS`). Repeats fold like
    logged records. Returns False when the input was unusable or the queue was full.
    """
    try:
        if not isinstance(source, str) or not _SOURCE.match(source):
            return False
        if isinstance(level, int) and not isinstance(level, bool):
            level_name = logging.getLevelName(level)
        else:
            level_name = str(level or "").strip().upper()
            if level_name == "WARN":
                level_name = "WARNING"
            if not isinstance(logging.getLevelName(level_name), int):  # not a known level name
                return False
        if not isinstance(level_name, str) or not level_name or level_name.startswith("Level "):
            return False
        name = str(logger_name or source)[:191]
        where = str(location or source)[:512]
        template = redact_text(str(message or ""), MESSAGE_MAX_CHARS)
        rid = str(request_id)[:64] if request_id else None
        return _enqueue({
            "time": time.time(), "level": level_name[:16], "logger_name": name, "location": where,
            "func_name": "", "message": template, "exc_type": None, "exc_text": None, "request_id": rid,
            "source": source, "extra_json": _extra_json(extra),
            "fingerprint": _fingerprint(level_name, name, where, "", template[:1000], source),
        })
    except Exception:  # noqa: BLE001
        return False


def flush(timeout: float = 5.0) -> bool:
    """Wait until every queued entry has been written (or dropped on failure). True when the queue drained."""
    writer = _writer
    if writer is None or not writer.is_alive():
        return _queue.empty()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _queue.unfinished_tasks == 0


def stats() -> dict:
    with _lock:
        return dict(_stats)


def install() -> ServerLogHandler:
    """Attach the handler to the root logger and ``uvicorn.error`` once each (idempotent) and start the writer."""
    handler: Optional[ServerLogHandler] = None
    for name in ("", "uvicorn.error"):
        target = logging.getLogger(name) if name else logging.getLogger()
        # By class name, like install_access_log_redaction: a re-imported module must not stack a second copy.
        existing = [h for h in target.handlers if type(h).__name__ == ServerLogHandler.__name__]
        if existing:
            handler = handler or existing[0]
            continue
        handler = handler or ServerLogHandler()
        target.addHandler(handler)
    _ensure_writer()
    return handler  # type: ignore[return-value]


def uninstall() -> None:
    """Remove the handler from both loggers (tests)."""
    for target in (logging.getLogger(), logging.getLogger("uvicorn.error")):
        for handler in list(target.handlers):
            if type(handler).__name__ == ServerLogHandler.__name__:
                target.removeHandler(handler)


def reset_for_tests() -> None:
    """Drain and forget the queue, counters and fold cache (tests only)."""
    flush(5.0)
    while True:
        try:
            _queue.get_nowait()
            _queue.task_done()
        except queue.Empty:
            break
    with _lock:
        for key in _stats:
            _stats[key] = 0
    if _writer is not None:
        _writer.recent.clear()
        _writer.reported_drops = 0
