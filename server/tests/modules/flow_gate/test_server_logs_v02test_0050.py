"""Server WARNING / ERROR records in the DB (flowgate.v02test.0050 T0005).

Completion criteria (a)-(i) of the instruction, plus the decisions they rest on:
(a) any module's ``getLogger(__name__).warning`` lands in ``server_logs``;
(b) a ``uvicorn.error`` exception record keeps exc_type and location (stored once);
(c) a record logged during a request carries that request's ``X-Request-ID`` -- also uvicorn's own
    "Exception in ASGI application" record of an exception that escaped the request (real server);
(d) ``Bearer abc`` / ``token=xyz`` / ``password: p`` are stored as ``***``;
(e) one message 100 times is one row with repeat_count 100;
(f) a failing DB write raises nothing into the caller and never recurses;
(g) a rolled-back request transaction does not take the row with it;
(h) INFO is not stored;
(i) the 30-day retention delete works.
The migration is checked on a fresh and an already-migrated SQLite DB, and verbatim on
PostgreSQL / MySQL when their test DSNs are configured (skipped otherwise).
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

_TESTS_DIR = Path(__file__).resolve().parents[2]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _fg_conversation_support import apply_migrations, fg_store  # noqa: E402,F401

from modules.flow_gate.services import server_logs  # noqa: E402

SERVER_DIR = _TESTS_DIR.parent
MIGRATIONS = SERVER_DIR / "sql" / "migrations"
MIGRATION = "200_server_logs.sql"
THIS_FILE = "test_server_logs_v02test_0050.py"


@pytest.fixture()
def logs(fg_store):
    """The handler installed on root + uvicorn.error over a fresh migrated SQLite store."""
    server_logs.reset_for_tests()
    server_logs.install()
    try:
        yield fg_store
    finally:
        server_logs.uninstall()
        server_logs.reset_for_tests()


def _rows(store, where: str = "1 = 1", params=None) -> list[dict]:
    assert server_logs.flush(10.0), "the writer did not drain the queue"
    return store._fetch_all(f"SELECT * FROM server_logs WHERE {where} ORDER BY id", params or [])


def row_path(row) -> str:
    return __file__


def row_line(row) -> int:
    return int(row["location"].rsplit(":", 1)[1])


def _next_line() -> int:
    return inspect.currentframe().f_back.f_lineno + 1


# ── (a) / (h) ────────────────────────────────────────────────────────────────────────────────

def test_a_plain_module_warning_is_stored_with_its_location(logs):
    line = _next_line()
    logging.getLogger("modules.flow_gate.some_module").warning("disk %s is low", "C:")
    [row] = _rows(logs)
    assert row["level"] == "WARNING"
    assert row["logger_name"] == "modules.flow_gate.some_module"
    assert row["message"] == "disk C: is low"
    assert row["location"].endswith(f"{THIS_FILE}:{line}")
    assert "/" in row["location"] and not os.path.isabs(row["location"])
    assert row["func_name"] == "test_a_plain_module_warning_is_stored_with_its_location"
    assert row["source"] == "server" and row["repeat_count"] == 1
    assert row["first_seen_at"] == row["last_seen_at"]
    assert row["exc_type"] is None and row["exc_text"] is None and row["request_id"] is None
    assert len(row["fingerprint"]) == 64


def test_error_and_critical_are_stored_info_and_debug_are_not(logs):
    log = logging.getLogger("modules.flow_gate.levels")
    log.setLevel(logging.DEBUG)
    try:
        log.debug("debug line")
        log.info("info line")
        log.error("error line")
        log.critical("critical line")
    finally:
        log.setLevel(logging.NOTSET)
    rows = _rows(logs)
    assert [(r["level"], r["message"]) for r in rows] == [("ERROR", "error line"), ("CRITICAL", "critical line")]


def test_info_alone_writes_nothing(logs):
    logging.getLogger("uvicorn.error").info("Started server process")
    logging.getLogger("modules.flow_gate.quiet").info("nothing to see")
    assert _rows(logs) == []


def test_a_logassist_error_is_one_row_pointing_at_its_caller(logs, monkeypatch):
    la = pytest.importorskip("LogAssist.log")
    # Never let the helper initialise itself here: that would open default.log in the working directory.
    monkeypatch.setattr(la, "initialized", True)
    line = _next_line()
    la.error("tree", "rebuild failed")
    rows = _rows(logs)
    assert len(rows) == 1, rows  # not the synthetic "Traceback ..." second record LogAssist writes
    assert rows[0]["logger_name"] == "LogAssist.log"
    assert rows[0]["message"] == "[tree] rebuild failed"
    assert rows[0]["location"].endswith(f"{THIS_FILE}:{line}")
    assert rows[0]["func_name"] == "test_a_logassist_error_is_one_row_pointing_at_its_caller"


# ── (b) uvicorn.error ────────────────────────────────────────────────────────────────────────

def _raise_inner():
    secret_local = "local-value-never-stored"  # noqa: F841 -- a frame local must not reach the row
    raise ValueError("boom with token=xyz")


def _log_unhandled():
    try:
        try:
            _raise_inner()
        except ValueError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=True)


@pytest.mark.parametrize("uvicorn_configured", [False, True])
def test_a_uvicorn_error_exception_is_stored_once_with_type_and_location(logs, uvicorn_configured, monkeypatch):
    if uvicorn_configured:
        # uvicorn's LOGGING_CONFIG: a handler on "uvicorn" and propagate=False, so the root never sees the record.
        uv = logging.getLogger("uvicorn")
        monkeypatch.setattr(uv, "propagate", False)
        monkeypatch.setattr(uv, "handlers", [logging.NullHandler()])
    _log_unhandled()
    rows = _rows(logs)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["level"] == "ERROR" and row["logger_name"] == "uvicorn.error"
    assert row["message"] == "Exception in ASGI application"
    assert row["exc_type"] == "RuntimeError"
    assert THIS_FILE in row["location"] and row["func_name"] == "_log_unhandled"
    text = row["exc_text"]
    assert text.startswith("RuntimeError: wrapped")
    assert "caused by:" in text and "ValueError: boom with token=***" in text
    assert f"{THIS_FILE}:" in text and ":_raise_inner" in text
    # Frame positions only: no source line, no local variable, no secret.
    assert "raise ValueError" not in text and "local-value-never-stored" not in text and "xyz" not in text


# ── (c) request id ───────────────────────────────────────────────────────────────────────────

def test_a_record_during_a_request_carries_its_request_id(logs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.request_observability import RequestIdMiddleware

    app = FastAPI()

    @app.get("/sync")
    def sync_route():  # runs on the thread pool: the contextvar must follow
        logging.getLogger("modules.flow_gate.sync_route").warning("sync route warned")
        return {"ok": True}

    @app.get("/async")
    async def async_route():
        logging.getLogger("modules.flow_gate.async_route").warning("async route warned")
        return {"ok": True}

    app.add_middleware(RequestIdMiddleware)
    client = TestClient(app)
    assert client.get("/sync", headers={"X-Request-ID": "req-0050-sync"}).headers["x-request-id"] == "req-0050-sync"
    answered = client.get("/async").headers["x-request-id"]
    rows = {r["logger_name"]: r for r in _rows(logs)}
    assert rows["modules.flow_gate.sync_route"]["request_id"] == "req-0050-sync"
    assert rows["modules.flow_gate.async_route"]["request_id"] == answered
    # Outside a request there is none.
    logging.getLogger("modules.flow_gate.outside").warning("no request here")
    [outside] = _rows(logs, "logger_name = ?", ["modules.flow_gate.outside"])
    assert outside["request_id"] is None


@pytest.mark.parametrize("uvicorn_configured", [False, True])
def test_an_unhandled_request_exception_row_carries_its_request_id(logs, uvicorn_configured, monkeypatch):
    """The real path: a route raises, the exception leaves RequestIdMiddleware (which resets its contextvar) and a
    real uvicorn server logs "Exception in ASGI application" afterwards. That row still names the request, once."""
    import http.client
    import threading

    import uvicorn
    from fastapi import FastAPI

    from modules.flow_gate.api.request_observability import RequestIdMiddleware, current_request_id

    if uvicorn_configured:
        uv = logging.getLogger("uvicorn")
        monkeypatch.setattr(uv, "propagate", False)
        monkeypatch.setattr(uv, "handlers", [logging.NullHandler()])

    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("unhandled in the route token=xyz")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    app.add_middleware(RequestIdMiddleware)
    # log_config=None: uvicorn must not re-configure (and strip) the process loggers under test.
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, access_log=False, lifespan="off",
                            http="h11", loop="asyncio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="test-uvicorn-0050", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]

        def get(path: str, rid: str) -> int:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"X-Request-ID": rid})
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()

        assert get("/boom", "req-0050-unhandled") == 500
        assert get("/fine", "req-0050-fine") == 200
        deadline = time.monotonic() + 10
        rows: list[dict] = []
        while time.monotonic() < deadline:
            rows = _rows(logs, "logger_name = ? AND level = ?", ["uvicorn.error", "ERROR"])
            if rows:
                break
            time.sleep(0.05)
    finally:
        server.should_exit = True
        thread.join(15)
    assert current_request_id() is None
    assert len(rows) == 1, rows  # stored once (root + uvicorn.error copies), not twice
    row = rows[0]
    assert row["message"].startswith("Exception in ASGI application")
    assert row["request_id"] == "req-0050-unhandled"
    assert row["exc_type"] == "RuntimeError"
    assert row["exc_text"].startswith("RuntimeError: unhandled in the route token=***")
    assert ":boom" in row["exc_text"] and "xyz" not in row["exc_text"]
    assert not _rows(logs, "request_id = ?", ["req-0050-fine"])


def test_the_request_id_tag_follows_chained_and_grouped_exceptions():
    from modules.flow_gate.api.request_observability import _tag_exception, request_id_of_exception

    inner = ValueError("inner")
    _tag_exception(inner, "req-inner")
    _tag_exception(inner, "req-outer")  # the innermost request keeps its id
    try:
        try:
            raise inner
        except ValueError as exc:
            raise RuntimeError("outer") from exc
    except RuntimeError as outer:
        assert request_id_of_exception(outer) == "req-inner"
    assert request_id_of_exception(ExceptionGroup("g", [KeyError("k"), inner])) == "req-inner"
    assert request_id_of_exception(KeyError("untagged")) is None and request_id_of_exception(None) is None
    record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", None,
                               (ValueError, inner, None))
    assert server_logs._entry_from_record(record)["request_id"] == "req-inner"


# ── (d) redaction ────────────────────────────────────────────────────────────────────────────

def test_credentials_are_stored_masked(logs):
    logging.getLogger("modules.flow_gate.auth").warning("refresh failed: Bearer abc token=xyz password: p end")
    [row] = _rows(logs)
    assert row["message"] == "refresh failed: Bearer *** token=*** password: *** end"
    for raw in ("abc", "xyz", ": p "):
        assert raw not in row["message"]


def test_the_shared_rule_is_the_chat_error_rule():
    from flowgate.conversation import chat_errors
    from util.credential_redaction import redact_text

    raw = "401 Authorization: Bearer abc.def-ghi token=s3cr3t FLOWGATE_TOKEN=q MY_API_KEY: k password=p rest"
    assert chat_errors.redact(raw) == redact_text(raw) == (
        "401 Authorization: Bearer *** token=*** FLOWGATE_TOKEN=*** MY_API_KEY: *** password=*** rest")
    assert chat_errors.redact("x" * 5000) == "x" * chat_errors.MAX_MESSAGE_CHARS


# ── (e) repeats ──────────────────────────────────────────────────────────────────────────────

def test_one_message_a_hundred_times_is_one_row_counted_100(logs):
    log = logging.getLogger("modules.flow_gate.retry")
    for attempt in range(100):
        log.warning("retry %d failed", attempt)  # the template folds, whatever the arguments
    rows = _rows(logs)
    assert len(rows) == 1, rows
    assert rows[0]["repeat_count"] == 100
    assert rows[0]["message"] == "retry 0 failed"  # the first occurrence's words
    assert rows[0]["last_seen_at"] >= rows[0]["first_seen_at"]


def test_repeats_fold_across_batches_and_split_after_the_window(logs):
    log = logging.getLogger("modules.flow_gate.window")
    for _ in range(3):
        log.warning("same words")
        assert server_logs.flush(10.0)
    [row] = _rows(logs)
    assert row["repeat_count"] == 3
    # The same record eleven minutes after the row's first occurrence opens a new row.
    later = logging.LogRecord("modules.flow_gate.window", logging.WARNING, row_path(row), row_line(row),
                              "same words", None, None, func="test_repeats_fold_across_batches_and_split_after_the_window")
    later.created = time.time() + server_logs.REPEAT_WINDOW_SECONDS + 60
    entry = server_logs._entry_from_record(later)
    assert entry["fingerprint"] == row["fingerprint"]
    server_logs._enqueue(entry)
    rows = _rows(logs, "logger_name = ?", ["modules.flow_gate.window"])
    assert [r["repeat_count"] for r in rows] == [3, 1]


def test_a_deleted_row_is_not_counted_into_the_void(logs):
    log = logging.getLogger("modules.flow_gate.gone")
    log.warning("gone words")
    [first] = _rows(logs)
    logs._execute("DELETE FROM server_logs WHERE id = ?", [first["id"]])
    log.warning("gone words")
    [again] = _rows(logs)
    assert again["repeat_count"] == 1 and again["message"] == "gone words"


# ── (f) failure and recursion ────────────────────────────────────────────────────────────────

class _BrokenDB:
    db_type = 1

    def __init__(self):
        self.calls = 0

    def _fail(self, *args, **kwargs):
        self.calls += 1
        # The DB layer logging while it fails: this runs in the writer thread and must not be queued again.
        logging.getLogger("modules.flow_gate.db.connection").warning("driver says no")
        raise sqlite3.OperationalError("disk I/O error password=hunter2")

    execute = fetch_one = fetch_all = _fail

    def begin_transaction(self):
        return self._fail()


def test_a_failing_write_raises_nothing_and_does_not_recurse(logs, monkeypatch, capfd):
    from modules.flow_gate.db import connection as conn_mod

    broken_store = conn_mod.FlowGateStore.__new__(conn_mod.FlowGateStore)
    broken = _BrokenDB()
    broken_store._db, broken_store._sq = broken, None
    monkeypatch.setattr(conn_mod, "STORE", broken_store)

    log = logging.getLogger("modules.flow_gate.caller")
    log.warning("first")
    log.error("second %s", "x")
    assert server_logs.flush(10.0)
    stats = server_logs.stats()
    assert stats["queued"] == 2, stats  # the writer thread's own "driver says no" never re-entered the queue
    assert stats["failed_batches"] >= 1 and stats["written"] == 0
    assert broken.calls >= 1  # the retention purge may also have tried (and failed) on this store
    err = capfd.readouterr().err
    assert "[server_logs] write failed" in err
    assert "hunter2" not in err
    assert err.count("[server_logs] write failed") == stats["failed_batches"]

    # The DB comes back: writing resumes.
    monkeypatch.setattr(conn_mod, "STORE", logs)
    log.warning("after recovery")
    rows = _rows(logs)
    assert [r["message"] for r in rows] == ["after recovery"]


def test_a_record_with_a_broken_format_still_becomes_a_row():
    record = logging.LogRecord("modules.flow_gate.caller", logging.WARNING, __file__, 1, "bad format %d",
                               ("not a number",), None)
    entry = server_logs._entry_from_record(record)
    assert entry["message"].startswith("'bad format %d' % ")
    assert entry["level"] == "WARNING"


def test_emit_never_raises_even_when_building_the_row_fails(logs, monkeypatch):
    def explode(record):
        raise RuntimeError("row builder broke")

    monkeypatch.setattr(server_logs, "_entry_from_record", explode)
    logging.getLogger("modules.flow_gate.caller").warning("still fine")  # no exception here
    assert _rows(logs) == []


# ── (g) rollback ─────────────────────────────────────────────────────────────────────────────

def test_a_rolled_back_request_transaction_keeps_the_row(logs):
    with pytest.raises(RuntimeError):
        with logs.transaction():
            logs._execute(
                "INSERT INTO server_logs (first_seen_at, last_seen_at, level, logger_name, location, message, "
                "fingerprint) VALUES ('2026-11-19T00:00:00+09:00', '2026-11-19T00:00:00+09:00', 'WARNING', "
                "'probe', 'probe', 'rolled back probe', 'probe')", [])
            logging.getLogger("modules.flow_gate.request").warning("logged inside the request transaction")
            raise RuntimeError("the request fails")
    rows = _rows(logs)
    assert [r["message"] for r in rows] == ["logged inside the request transaction"]


# ── (i) retention ────────────────────────────────────────────────────────────────────────────

def test_rows_older_than_30_days_are_purged(logs):
    now = time.time()
    for label, age_days in (("old", 31), ("edge", 29), ("new", 0)):
        stamp = server_logs._iso(now - age_days * 86400)
        logs._execute(
            "INSERT INTO server_logs (first_seen_at, last_seen_at, level, logger_name, location, message, fingerprint) "
            "VALUES (?, ?, 'WARNING', 'retention', 'retention', ?, ?)", [stamp, stamp, label, label])
    server_logs.purge_expired(now=now)
    assert [r["message"] for r in _rows(logs, "logger_name = 'retention'")] == ["edge", "new"]


def test_the_writer_purges_at_most_once_a_day(monkeypatch):
    calls = []
    monkeypatch.setattr(server_logs, "purge_expired", lambda *a, **k: calls.append(1))
    writer = server_logs._Writer()  # not started: drive its schedule by hand
    writer._maybe_purge()
    writer._maybe_purge()
    assert calls == [1]
    writer.next_purge = 0
    writer._maybe_purge()
    assert calls == [1, 1]


# ── install / record() / console ─────────────────────────────────────────────────────────────

def test_install_is_idempotent_on_root_and_uvicorn_error(logs):
    server_logs.install()
    server_logs.install()
    for target in (logging.getLogger(), logging.getLogger("uvicorn.error")):
        mine = [h for h in target.handlers if isinstance(h, server_logs.ServerLogHandler)]
        assert len(mine) == 1
        assert mine[0].level == logging.WARNING
    logging.getLogger("modules.flow_gate.once").warning("only once")
    assert len(_rows(logs)) == 1


def test_record_writes_a_browser_row_with_redacted_extra(logs):
    assert server_logs.record("browser", "error", "tree reload failed token=abc", request_id="req-browser-1",
                              extra={"url": "/tree?token=abc", "status": 500, "nested": ["Bearer zzz"]})
    assert server_logs.record("browser", logging.WARNING, "plain") is True
    assert server_logs.record("Not A Source", "error", "x") is False
    assert server_logs.record("browser", "nonsense-level", "x") is False
    rows = _rows(logs)
    assert [(r["source"], r["level"]) for r in rows] == [("browser", "ERROR"), ("browser", "WARNING")]
    row = rows[0]
    assert row["message"] == "tree reload failed token=***" and row["request_id"] == "req-browser-1"
    extra = json.loads(row["extra_json"])
    assert extra == {"nested": ["Bearer ***"], "status": 500, "url": "/tree?token=***"}


def test_record_masks_the_values_of_credential_keys_nested_too(logs):
    extra = {
        "token": "xyz", "password": "p", "api_key": "abc", "status": 500, "token_count": 3, "refresh_token": None,
        "headers": {"Authorization": "Bearer hdr", "X-Api-Key": "k-hdr", "Accept": "text/html"},
        "nested": {"items": [{"accessToken": "at-1", "name": "kept"}, "plain", {"secret": {"deep": "s-1"}}]},
        "credentials": ["c-1", "c-2"],
        "note": "url /tree?token=t-1 and password: p-1",
    }
    assert server_logs._extra_json(extra) is not None
    assert server_logs.record("browser", "error", "with extra", extra=extra)
    [row] = _rows(logs)
    text = row["extra_json"]
    for secret in ("xyz", '"p"', "abc", "hdr", "k-hdr", "at-1", "s-1", "c-1", "c-2", "t-1", "p-1"):
        assert secret not in text, (secret, text)
    assert json.loads(text) == {
        "token": "***", "password": "***", "api_key": "***", "status": 500, "token_count": 3, "refresh_token": None,
        "headers": {"Authorization": "***", "X-Api-Key": "***", "Accept": "text/html"},
        "nested": {"items": [{"accessToken": "***", "name": "kept"}, "plain", {"secret": "***"}]},
        "credentials": "***",
        "note": "url /tree?token=*** and password: ***",
    }


def test_the_key_rule_leaves_the_text_rule_and_chat_errors_alone():
    from flowgate.conversation import chat_errors
    from util import credential_redaction as rule

    text = "Bearer abc token=xyz password: p"
    assert rule.redact_text(text) == chat_errors.redact(text) == "Bearer *** token=*** password: ***"
    assert chat_errors.redact("x" * 5000) == "x" * 4000
    assert [rule.is_secret_key(k) for k in ("token", "Password", "X-Api-Key", "apiKey", "refreshToken", "cookie")] \
        == [True] * 6
    assert [rule.is_secret_key(k) for k in ("status", "url", "token_count", "tokens", "message", "")] == [False] * 6


def test_record_caps_extra(logs):
    assert server_logs.record("browser", "warning", "big", extra={f"k{i}": 'q"' * 1500 for i in range(20)})
    [row] = _rows(logs)
    assert len(row["extra_json"]) <= server_logs.EXTRA_MAX_CHARS
    assert json.loads(row["extra_json"])["truncated"] is True


def test_the_console_fallback_for_handlerless_loggers_is_kept(monkeypatch):
    """Before this change a plain module WARNING reached stderr through logging.lastResort; it still does."""
    seen = []

    class Sink(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    monkeypatch.setattr(logging, "lastResort", Sink(logging.WARNING))
    log = logging.getLogger(f"fg.isolated.{uuid.uuid4().hex[:6]}")
    log.propagate = False
    handler = server_logs.ServerLogHandler()
    log.addHandler(handler)
    monkeypatch.setattr(server_logs, "_enqueue", lambda entry: True)
    log.warning("to the console as before")
    assert seen == ["to the console as before"]
    other = logging.NullHandler()
    log.addHandler(other)
    log.warning("someone else prints this")
    assert seen == ["to the console as before"]


@pytest.mark.parametrize("on_parent", [False, True])
def test_a_present_handler_above_the_level_still_silences_the_console_fallback(monkeypatch, on_parent):
    """logging's callHandlers skips lastResort when it FOUND any handler, whatever that handler's level. A WARNING on a
    path whose only handler is at ERROR printed nothing before this module; it still prints nothing."""
    seen = []

    class Sink(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    monkeypatch.setattr(logging, "lastResort", Sink(logging.WARNING))
    monkeypatch.setattr(server_logs, "_enqueue", lambda entry: True)
    parent = logging.getLogger(f"fg.levelled.{uuid.uuid4().hex[:6]}")
    parent.propagate = False
    log = parent.getChild("child") if on_parent else parent
    (parent if on_parent else log).addHandler(logging.NullHandler(logging.ERROR))
    log.warning("baseline without our handler")
    assert seen == []  # what plain logging does
    parent.addHandler(server_logs.ServerLogHandler())
    log.warning("with our handler installed")
    assert seen == []  # unchanged: no new stderr line
    log.error("an error reaches the ERROR handler, not the fallback")
    assert seen == []


def test_the_app_installs_the_handler_outside_tests_only():
    text = (SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "server_logs" in text and "install_server_logs()" in text


# ── migration 200 ────────────────────────────────────────────────────────────────────────────

_COLUMNS = {"id", "first_seen_at", "last_seen_at", "level", "logger_name", "location", "func_name", "message",
            "exc_type", "exc_text", "request_id", "source", "repeat_count", "fingerprint", "extra_json"}
_INDEXES = {"idx_server_logs_fingerprint", "idx_server_logs_last_seen", "idx_server_logs_level_seen"}


def test_migration_200_exists_in_three_dialects():
    for dialect in ("sqlite", "mysql", "postgres"):
        sql = (MIGRATIONS / dialect / MIGRATION).read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS server_logs" in sql
        for column in _COLUMNS:
            assert f"    {column} " in sql, (dialect, column)
        for index in _INDEXES:
            assert index in sql, (dialect, index)
    assert [p.name for p in (MIGRATIONS / "sqlite").glob("200_*.sql")] == [MIGRATION]


def test_migration_200_applies_to_a_fresh_and_to_an_existing_sqlite_db(tmp_path):
    fresh = tmp_path / "fresh.db"
    apply_migrations(str(fresh))
    existing = tmp_path / "existing.db"
    conn = sqlite3.connect(existing)
    try:
        for migration in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
            if migration.name < "200_":
                conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
                     "first_login_required, created_at, updated_at) VALUES ('u', 'u', 'u@x.test', 'x', 1, 0, 0, 'n', 'n')")
        conn.commit()
        users_before = conn.execute("SELECT * FROM users ORDER BY user_id").fetchall()
        sql = (MIGRATIONS / "sqlite" / MIGRATION).read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.executescript(sql)  # twice: idempotent
        assert conn.execute("SELECT * FROM users ORDER BY user_id").fetchall() == users_before  # additive only
    finally:
        conn.close()
    for path in (fresh, existing):
        conn = sqlite3.connect(path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(server_logs)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(server_logs)")}
            assert columns == _COLUMNS and _INDEXES <= indexes
            conn.execute("INSERT INTO server_logs (first_seen_at, last_seen_at, level, logger_name, location, message, "
                         "fingerprint) VALUES ('t', 't', 'WARNING', 'l', 'loc', 'm', 'f')")
            row = conn.execute("SELECT source, repeat_count FROM server_logs").fetchone()
            assert row == ("server", 1)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO server_logs (first_seen_at, last_seen_at, level, logger_name, location, "
                             "message, fingerprint, repeat_count) VALUES ('t', 't', 'W', 'l', 'loc', 'm', 'f', 0)")
        finally:
            conn.close()


def test_the_real_runner_applies_200_to_a_db_migrated_through_199(tmp_path):
    """A deployed DB has every earlier file in its ledger; the boot that ships 200 applies only 200."""
    migrator = pytest.importorskip("sqloader.migrator")
    wrapper = pytest.importorskip("sqloader.sqlite3")
    seed = tmp_path / "migrations"
    seed.mkdir()
    for path in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
        if path.name != MIGRATION:
            shutil.copy(path, seed / path.name)
    db_path = tmp_path / "deployed.db"
    migrator.DatabaseMigrator(wrapper.SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    def state():
        conn = sqlite3.connect(str(db_path))
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            ledger = {r[0] for r in conn.execute("SELECT filename FROM migrations")}
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        return tables, ledger, users

    tables, ledger, users_before = state()
    assert "server_logs" not in tables and MIGRATION not in ledger
    shutil.copy(MIGRATIONS / "sqlite" / MIGRATION, seed / MIGRATION)
    migrator.DatabaseMigrator(wrapper.SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    tables, ledger, users_after = state()
    assert "server_logs" in tables and MIGRATION in ledger
    assert users_after == users_before


def _statements(script: str) -> list[str]:
    body = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    return [s for s in body.split(";") if s.strip()]


@pytest.fixture(params=["postgres", "mysql"])
def live_engine(request):
    name = "fg_server_logs_200_" + uuid.uuid4().hex[:10]
    if request.param == "postgres":
        dsn = os.environ.get("FLOWGATE_PG_TEST_DSN") or os.environ.get("FLOWGATE_POSTGRES_TEST_DSN")
        if not dsn:
            pytest.skip("PostgreSQL test DSN is not configured (FLOWGATE_PG_TEST_DSN / FLOWGATE_POSTGRES_TEST_DSN)")
        psycopg = pytest.importorskip("psycopg")
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{name}"')
                try:
                    cur.execute(f'SET search_path TO "{name}"')
                    yield "postgres", cur
                finally:
                    cur.execute("SET search_path TO public")
                    cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        return
    dsn = os.environ.get("FLOWGATE_MYSQL_TEST_DSN")
    if not dsn:
        pytest.skip("MySQL test DSN is not configured (FLOWGATE_MYSQL_TEST_DSN)")
    pymysql = pytest.importorskip("pymysql")
    parsed = urlparse(dsn)
    conn = pymysql.connect(host=parsed.hostname, port=parsed.port or 3306,
                           user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
                           database=parsed.path.lstrip("/"), charset="utf8mb4", autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
            try:
                cur.execute(f"USE `{name}`")
                yield "mysql", cur
            finally:
                cur.execute(f"DROP DATABASE `{name}`")
    finally:
        conn.close()


def test_migration_200_runs_verbatim_twice_on_live_engines(live_engine):
    dialect, cur = live_engine
    script = (MIGRATIONS / dialect / MIGRATION).read_text(encoding="utf-8")
    for _ in range(2):
        for statement in _statements(script):
            cur.execute(statement)
    cur.execute("INSERT INTO server_logs (first_seen_at, last_seen_at, level, logger_name, location, message, "
                "fingerprint) VALUES ('t', 't', 'WARNING', 'l', 'loc', 'm', 'f')")
    cur.execute("UPDATE server_logs SET repeat_count = repeat_count + 2, "
                "last_seen_at = CASE WHEN last_seen_at < 'u' THEN 'u' ELSE last_seen_at END WHERE id = 1")
    cur.execute("SELECT source, repeat_count, last_seen_at FROM server_logs")
    assert tuple(cur.fetchone()) == ("server", 3, "u")
