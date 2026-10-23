"""Evaluation schema, fingerprint, ordering and execution boundary regression tests."""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))


def _schema():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE projects(project_id TEXT PRIMARY KEY);
        CREATE TABLE users(user_id TEXT PRIMARY KEY);
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        INSERT INTO projects VALUES ('p');
        INSERT INTO users VALUES ('u');
        INSERT INTO ai_invoke_runs VALUES ('old');
    """)
    sql = (SERVER / "sql/migrations/sqlite/124_ai_evaluations_and_groups.sql").read_text()
    db.executescript(sql)
    return db


def test_three_dialect_schema_contract():
    db = _schema()
    assert db.execute("SELECT requested_ai_group_id FROM ai_invoke_runs WHERE run_id='old'").fetchone() == (None,)
    for dialect in ("sqlite", "mysql", "postgres"):
        sql = (SERVER / f"sql/migrations/{dialect}/124_ai_evaluations_and_groups.sql").read_text()
        for required in ("ai_evaluation_runs", "ai_groups", "requested_ai_group_id",
                         "project_run_seq", "active_dedupe_key", "name_key_hash"):
            assert required in sql
        assert "UNIQUE" in sql.upper()
    mysql = (SERVER / "sql/migrations/mysql/124_ai_evaluations_and_groups.sql").read_text()
    assert "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin" in mysql
    assert "BIGINT UNSIGNED" in mysql
    db.close()


def test_sqlite_sequence_dedupe_and_cascade_constraints():
    db = _schema()
    base = ["e1", "p", 1, "provider", "a" * 64, "b" * 64, "pending", "t", "t"]
    insert = """INSERT INTO ai_evaluation_runs
        (evaluation_id, project_id, project_run_seq, provider_id, subject_fingerprint,
         active_dedupe_key, lifecycle, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    db.execute(insert, base)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ["e2", "p", 2, "provider", "a" * 64, "b" * 64, "pending", "t", "t"])
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ["e2", "p", 1, "provider", "a" * 64, "c" * 64, "pending", "t", "t"])
    db.execute("UPDATE ai_evaluation_runs SET active_dedupe_key=NULL WHERE evaluation_id='e1'")
    db.execute(insert, ["e2", "p", 2, "provider", "a" * 64, "b" * 64, "pending", "t", "t"])
    assert db.execute("SELECT evaluation_id FROM ai_evaluation_runs ORDER BY project_run_seq DESC").fetchall() == [("e2",), ("e1",)]
    db.execute("DELETE FROM projects WHERE project_id='p'")
    assert db.execute("SELECT COUNT(*) FROM ai_evaluation_runs").fetchone() == (0,)
    db.close()


def test_fingerprint_stability_and_key_rotation(monkeypatch, tmp_path):
    from modules.flow_gate.services import ai_evaluation_fingerprint as fp
    monkeypatch.setattr(fp, "_root", lambda: tmp_path)
    monkeypatch.setattr(fp, "_history_exists", lambda: False)
    monkeypatch.setenv(fp.KEY_ENV, base64.b64encode(b"A" * 32).decode())
    provider = {"exec_type": "api", "kind": "openai", "api_base_url": "https://example.test/v1", "api_model": "m"}
    kwargs = dict(project_id="p", provider_id="a", scope="project", provider=provider, credential="secret")
    first = fp.subject_fingerprint(**kwargs)
    assert fp.subject_fingerprint(**kwargs) == first
    assert fp.subject_fingerprint(**{**kwargs, "credential": "changed"}) != first
    assert fp.subject_fingerprint(**{**kwargs, "provider": {**provider, "name": "renamed"}}) == first
    monkeypatch.setenv(fp.KEY_ENV, base64.b64encode(b"B" * 32).decode())
    with pytest.raises(fp.FingerprintKeyError):
        fp.subject_fingerprint(**kwargs)


def test_subject_is_immutable_and_diagnostic_is_redacted():
    from modules.flow_gate.services import ai_evaluation_service as service
    subject = service.EvaluationExecutionSubject("p", "a", "project", "name", "api", "openai",
        None, "https://example.test/v1", "m", "secret-value", "a" * 64, 1)
    with pytest.raises(FrozenInstanceError):
        subject.api_model = "changed"
    text = service._safe_summary("Authorization: Bearer xyz API key: abc secret-value " + "x" * 2500,
                                 "secret-value")
    assert len(text) == 2000
    assert "xyz" not in text and "abc" not in text and "secret-value" not in text


def test_current_prefers_newer_active_run_even_with_inverted_id(monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as service
    current = service.EvaluationExecutionSubject("p", "provider", "project", "name", "cli", "codex",
        "codex exec", None, None, None, "a" * 64, 1)
    older = dict(evaluation_id="z", project_id="p", provider_id="provider", project_run_seq=1,
                 subject_fingerprint="a" * 64, subject_fingerprint_version=1, lifecycle="completed",
                 verdict="suitable", provider_name_snapshot="name")
    newer = dict(older, evaluation_id="a", project_run_seq=2, lifecycle="pending", verdict=None)
    monkeypatch.setattr(service, "_current_subject", lambda *_: current)
    monkeypatch.setattr(service.repo, "latest", lambda _p, _id, *_: newer)
    result = service.current("p", "provider")
    assert result["current"]["evaluation_id"] == "a"
    assert result["current"]["lifecycle"] == "pending"


def test_safety_recheck_uses_identity_only(monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as service
    subject = service.EvaluationExecutionSubject("p", "a", "project", "name", "api", "openai",
        None, "https://old.example/v1", "old-model", "accepted-secret", "a" * 64, 1)
    monkeypatch.setattr(service, "_effective", lambda _: {"source": "project", "providers": [
        {"id": "a", "api_model": "new-model", "api_base_url": "https://new.example/v1"}]})
    assert service._safety_available(subject)
    assert subject.api_model == "old-model" and subject.credential == "accepted-secret"
    monkeypatch.setattr(service, "_effective", lambda _: {"source": "project", "providers": []})
    assert not service._safety_available(subject)


class _SqliteStore:
    """Small adapter exercising the real repository against the migration schema."""
    def __init__(self):
        self.db = _schema()
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    def _fetch_one(self, sql, params=None):
        with self.lock:
            row = self.db.execute(sql, params or []).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params or []).fetchall()]

    def _execute(self, sql, params=None):
        with self.lock:
            self.db.execute(sql, params or [])

    def _execute_affected(self, sql, params=None):
        with self.lock:
            cursor = self.db.execute(sql, params or [])
            self.db.commit()
            return cursor.rowcount


def test_repository_concurrent_creation_and_active_dedupe(monkeypatch):
    from modules.flow_gate.db import ai_evaluation_runs as repo
    store = _SqliteStore()
    monkeypatch.setattr(repo, "get_store", lambda: store)
    monkeypatch.setattr(repo, "now_iso", lambda: "same-timestamp")
    def create(fingerprint):
        try:
            return repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
                fingerprint=fingerprint, version=1, created_by="u")
        except repo.AlreadyRunning as exc:
            return exc
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(create, "a" * 64), pool.submit(create, "a" * 64),
                   pool.submit(create, "c" * 64)]
        results = [future.result() for future in futures]
    rows = [result for result in results if isinstance(result, dict)]
    duplicates = [result for result in results if isinstance(result, repo.AlreadyRunning)]
    assert len(rows) == 2 and len(duplicates) == 1
    assert duplicates[0].evaluation_id in {row["evaluation_id"] for row in rows}
    assert {row["project_run_seq"] for row in rows} == {1, 2}
    assert repo.latest("p", "provider")["project_run_seq"] == 2
    store.db.close()


def test_repository_terminal_cancel_and_orphan_release(monkeypatch):
    from modules.flow_gate.db import ai_evaluation_runs as repo
    store = _SqliteStore()
    monkeypatch.setattr(repo, "get_store", lambda: store)
    row = repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
        fingerprint="a" * 64, version=1, created_by="u")
    assert repo.mark_running("p", row["evaluation_id"])
    assert repo.finish("p", row["evaluation_id"], lifecycle="cancelled", reason_code="cancelled_by_user")
    assert not repo.finish("p", row["evaluation_id"], lifecycle="failed", reason_code="internal_error")
    assert repo.get("p", row["evaluation_id"])["active_dedupe_key"] is None
    second = repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
        fingerprint="a" * 64, version=1, created_by="u")
    assert repo.recover_orphans() == 1
    assert repo.get("p", second["evaluation_id"])["lifecycle"] == "failed"
    assert repo.get("p", second["evaluation_id"])["active_dedupe_key"] is None
    store.db.close()


def _probe_subject(**over):
    from modules.flow_gate.services import ai_evaluation_service as service
    fields = dict(project_id="p", provider_id="a", scope="project", name="A", exec_type="api",
                  kind="openai", cli_command=None, api_base_url=None, api_model="model",
                  credential="secret", fingerprint="a" * 64, version=1)
    fields.update(over)
    return service.EvaluationExecutionSubject(**fields)


class _Harness:
    """Real repository + real worker/cancel paths over the migration schema.

    Only the transport call (`service._execute`) is ever swapped, so every terminal write goes
    through the production `_settle` -> `repo.finish` conditional UPDATE.
    """

    def __init__(self, monkeypatch):
        from modules.flow_gate.db import ai_evaluation_runs as repo
        from modules.flow_gate.services import ai_evaluation_service as service
        self.service, self.repo = service, repo
        self.store = _SqliteStore()
        self.events: list[tuple[str, str]] = []
        self.ids: list[str] = []
        monkeypatch.setattr(repo, "get_store", lambda: self.store)
        monkeypatch.setattr(service, "_emit", lambda kind, row: self.events.append((kind, row["lifecycle"])))
        monkeypatch.setattr(service, "_safety_available", lambda _subject: True)

    def create(self, subject):
        from modules.flow_gate.services.ai_invoke import runtime as probe
        row = self.repo.create_pending(project_id="p", provider_id="a", provider_name="A",
            fingerprint="a" * 64, version=1, created_by="u")
        state = {"handle": probe.ProbeHandle(), "subject": subject}
        with self.service._RUNS_LOCK:
            self.service._RUNS[row["evaluation_id"]] = state
        self.ids.append(row["evaluation_id"])
        return row["evaluation_id"], state

    def start(self, subject):
        eid, state = self.create(subject)
        worker = threading.Thread(target=self.service._worker, args=(eid, state), daemon=True)
        worker.start()
        return eid, state, worker

    def row(self, eid):
        return self.repo.get("p", eid)

    def close(self):
        with self.service._RUNS_LOCK:
            for eid in self.ids:
                self.service._RUNS.pop(eid, None)
        self.store.db.close()


def _fake_execute(monkeypatch, service, outcome, *, gate=None, entered=None):
    def execute(_subject, _handle):
        if entered is not None:
            entered.set()
        if gate is not None:
            assert gate.wait(5), "test gate never released"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    monkeypatch.setattr(service, "_execute", execute)


def _blocking_api_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    entered, release = threading.Event(), threading.Event()

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            entered.set()
            release.wait(10)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"choices":[{"message":{"content":"ok"}}]}')
            except OSError:
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, entered, release


def test_cancel_interrupts_blocking_api_request_and_ends_cancelled(monkeypatch):
    harness = _Harness(monkeypatch)
    service = harness.service
    server, entered, release = _blocking_api_server()
    try:
        eid, _state, worker = harness.start(
            _probe_subject(api_base_url=f"http://127.0.0.1:{server.server_port}/v1"))
        assert entered.wait(5)
        assert service.cancel("p", eid) == {"evaluation_id": eid, "lifecycle": "cancelled", "verdict": None}
        worker.join(5)
        assert not worker.is_alive(), "cancel left the API request blocked"
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"], row["verdict"]) == ("cancelled", "cancelled_by_user", None)
        assert harness.events == [("started", "running"), ("cancelled", "cancelled")]
        assert eid not in service._RUNS
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        harness.close()


def test_cancel_kills_the_cli_process_tree_and_ends_cancelled(monkeypatch, tmp_path):
    import sys
    import time
    from modules.flow_gate.services.ai_invoke import runtime as ai_runtime
    monkeypatch.setattr(ai_runtime, "_project_scratch_root", lambda project_id: tmp_path / "scratch" / "evaluation")
    monkeypatch.setattr(ai_runtime.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    harness = _Harness(monkeypatch)
    service = harness.service
    beat = tmp_path / "beat.txt"
    script = tmp_path / "hang.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "beat = pathlib.Path(sys.argv[1])\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    beat.write_text(str(i))\n"
        "    time.sleep(0.1)\n")
    try:
        eid, _state, worker = harness.start(
            _probe_subject(exec_type="cli", kind="custom", cli_command=f'"{sys.executable}" "{script}" "{beat}"'))
        deadline = time.time() + 15
        while not beat.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert beat.exists(), "CLI provider never started"
        service.cancel("p", eid)
        worker.join(15)
        assert not worker.is_alive(), "cancel left the CLI process running"
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("cancelled", "cancelled_by_user")
        assert harness.events == [("started", "running"), ("cancelled", "cancelled")]
        first = beat.read_text()
        time.sleep(0.6)
        assert beat.read_text() == first, "the provider process kept running after cancel"
        assert list((tmp_path / "scratch" / "evaluation").iterdir()) == [], "the probe scratch was not cleaned up"
    finally:
        harness.close()


def test_cancel_wins_over_a_racing_completion(monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    harness = _Harness(monkeypatch)
    service = harness.service
    gate, entered = threading.Event(), threading.Event()
    _fake_execute(monkeypatch, service, probe.ProbeOutcome(probe.PROBE_OK, "fine"), gate=gate, entered=entered)
    try:
        eid, _state, worker = harness.start(_probe_subject())
        assert entered.wait(5)
        service.cancel("p", eid)
        gate.set()
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["verdict"], row["reason_code"]) == ("cancelled", None, "cancelled_by_user")
        assert harness.events == [("started", "running"), ("cancelled", "cancelled")]
    finally:
        gate.set()
        harness.close()


def test_completion_wins_over_a_late_cancel(monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    harness = _Harness(monkeypatch)
    service = harness.service
    _fake_execute(monkeypatch, service, probe.ProbeOutcome(probe.PROBE_OK, "fine"))
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        with pytest.raises(service.EvaluationError) as raised:
            service.cancel("p", eid)
        assert (raised.value.code, raised.value.status) == ("evaluation_not_cancellable", 409)
        row = harness.row(eid)
        assert (row["lifecycle"], row["verdict"]) == ("completed", "suitable")
        assert harness.events == [("started", "running"), ("completed", "completed")]
    finally:
        harness.close()


def test_cancel_wins_over_a_racing_timeout_and_a_timeout_wins_over_a_late_cancel(monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    timeout = probe.ProbeOutcome(probe.PROBE_TIMEOUT)

    harness = _Harness(monkeypatch)
    service = harness.service
    gate, entered = threading.Event(), threading.Event()
    _fake_execute(monkeypatch, service, timeout, gate=gate, entered=entered)
    try:
        eid, _state, worker = harness.start(_probe_subject())
        assert entered.wait(5)
        service.cancel("p", eid)
        gate.set()
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("cancelled", "cancelled_by_user")
        assert harness.events == [("started", "running"), ("cancelled", "cancelled")]
    finally:
        gate.set()
        harness.close()

    harness = _Harness(monkeypatch)
    service = harness.service
    _fake_execute(monkeypatch, service, timeout)
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        with pytest.raises(service.EvaluationError) as raised:
            service.cancel("p", eid)
        assert raised.value.code == "evaluation_not_cancellable"
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("failed", "timeout")
        assert harness.events == [("started", "running"), ("failed", "failed")]
    finally:
        harness.close()


def test_adapter_cancelled_outcome_settles_cancelled_never_failed_cancelled(monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    harness = _Harness(monkeypatch)
    service = harness.service
    _fake_execute(monkeypatch, service, probe.ProbeOutcome(probe.PROBE_CANCELLED))
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("cancelled", "cancelled_by_user")
        assert row["reason_code"] != "cancelled"
        assert [kind for kind, _ in harness.events] == ["started", "cancelled"]
    finally:
        harness.close()


def test_cancel_before_the_worker_starts_never_executes(monkeypatch):
    harness = _Harness(monkeypatch)
    service = harness.service

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a cancelled pending evaluation must not reach the transport")
    monkeypatch.setattr(service, "_execute", must_not_run)
    try:
        eid, state = harness.create(_probe_subject())
        service.cancel("p", eid)
        assert state["handle"].cancelled
        worker = threading.Thread(target=service._worker, args=(eid, state), daemon=True)
        worker.start()
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("cancelled", "cancelled_by_user")
        assert harness.events == [("cancelled", "cancelled")]
        assert eid not in service._RUNS
    finally:
        harness.close()


def test_cancel_of_an_unknown_evaluation_is_404(monkeypatch):
    harness = _Harness(monkeypatch)
    try:
        with pytest.raises(harness.service.EvaluationError) as raised:
            harness.service.cancel("p", "missing")
        assert (raised.value.code, raised.value.status) == ("evaluation_not_found", 404)
    finally:
        harness.close()


@pytest.mark.parametrize("kind, reason", [
    ("timeout", "timeout"),
    ("transport_failed", "transport_failed"),
    ("provider_launch_failed", "provider_launch_failed"),
    ("execution_failed", "execution_failed"),
    ("something_new", "internal_error"),
])
def test_failed_outcomes_map_to_the_public_reason_codes(monkeypatch, kind, reason):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    harness = _Harness(monkeypatch)
    _fake_execute(monkeypatch, harness.service, probe.ProbeOutcome(kind))
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("failed", reason)
        assert [kind_ for kind_, _ in harness.events] == ["started", "failed"]
    finally:
        harness.close()


def test_ok_outcomes_choose_the_verdict_and_unexpected_errors_are_internal(monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime as probe
    for text, verdict, reason in (("fine", "suitable", None), ("  ", "unsuitable", "empty_response")):
        harness = _Harness(monkeypatch)
        _fake_execute(monkeypatch, harness.service, probe.ProbeOutcome(probe.PROBE_OK, text))
        try:
            eid, _state, worker = harness.start(_probe_subject())
            worker.join(5)
            row = harness.row(eid)
            assert (row["lifecycle"], row["verdict"], row["reason_code"]) == ("completed", verdict, reason)
        finally:
            harness.close()

    harness = _Harness(monkeypatch)
    _fake_execute(monkeypatch, harness.service, RuntimeError("boom"))
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("failed", "internal_error")
    finally:
        harness.close()


def test_provider_that_became_unavailable_fails_before_any_transport(monkeypatch):
    harness = _Harness(monkeypatch)
    service = harness.service
    monkeypatch.setattr(service, "_safety_available", lambda _subject: False)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("an unavailable provider must not be executed")
    monkeypatch.setattr(service, "_execute", must_not_run)
    try:
        eid, _state, worker = harness.start(_probe_subject())
        worker.join(5)
        row = harness.row(eid)
        assert (row["lifecycle"], row["reason_code"]) == ("failed", "provider_became_unavailable")
    finally:
        harness.close()


def test_execute_dispatches_to_the_shared_ai_invoke_transports(monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as service
    from modules.flow_gate.services.ai_invoke import provider_api, provider_cli
    from modules.flow_gate.services.ai_invoke import runtime as probe
    calls = []
    monkeypatch.setattr(provider_api, "probe_api_completion",
                        lambda **kw: calls.append(("api", kw)) or probe.ProbeOutcome(probe.PROBE_OK, "a"))
    monkeypatch.setattr(provider_cli, "probe_cli_completion",
                        lambda **kw: calls.append(("cli", kw)) or probe.ProbeOutcome(probe.PROBE_OK, "c"))
    handle = probe.ProbeHandle()
    api = _probe_subject(kind="claude", api_base_url="https://api.example/v1")
    assert service._execute(api, handle).text == "a"
    cli = _probe_subject(exec_type="cli", kind="custom", cli_command="tool -p -")
    assert service._execute(cli, handle).text == "c"
    assert [name for name, _ in calls] == ["api", "cli"]
    api_kw, cli_kw = calls[0][1], calls[1][1]
    assert api_kw["handle"] is handle and cli_kw["handle"] is handle
    assert api_kw["prompt"] == service.BASELINE == cli_kw["prompt"]
    assert api_kw["timeout"] == service.TIMEOUT_SEC == cli_kw["timeout"]
    assert (api_kw["kind"], api_kw["base_url"], api_kw["model"], api_kw["credential"]) == (
        "claude", "https://api.example/v1", "model", "secret")
    assert (cli_kw["project_id"], cli_kw["kind"], cli_kw["command"]) == ("p", "custom", "tool -p -")
    assert service._execute(_probe_subject(exec_type="other"), handle).kind == probe.PROBE_LAUNCH_FAILED


def test_evaluation_service_owns_no_transport_or_process_of_its_own():
    import ast
    from modules.flow_gate.services import ai_evaluation_service as service
    tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
    forbidden_modules = {"http", "socket", "subprocess", "tempfile", "urllib", "process_runner"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported |= {alias.name for alias in node.names}
    assert not (imported & forbidden_modules), imported & forbidden_modules
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (attributes & {"Popen", "HTTPConnection", "HTTPSConnection", "urlopen"})
    assert {"probe_api_completion", "probe_cli_completion"} <= attributes
