"""0146 T#3: durable exact ownership, admission fence, result and retry isolation."""
from __future__ import annotations

import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tests.modules.flow_gate.services.test_self_check_progress_0146 import store, pending  # noqa: F401
from modules.flow_gate.db import tr_self_check_runs as db
from modules.flow_gate.services import tr_self_check_progress as progress
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import graph_source_hooks


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(progress, "_bindings", {})
    monkeypatch.setattr(progress, "_managed", {})
    monkeypatch.setattr(progress, "_samples", {})
    monkeypatch.setattr(service, "_controls", {})
    monkeypatch.setattr(service, "_emit", lambda row: None)
    # A different live server owns these unit-test rows. Its watcher must stop them;
    # requesting cancellation here cannot manufacture a terminal result.
    monkeypatch.setattr(proof, "owner_server_state", lambda _: SimpleNamespace(
        state=proof.MATCH, detail={"self": False}, gone=False))


def parent(name="a", token="t"):
    run = dict(run_id=name, project_id="p", group_id="g", token_id=token)
    progress.open_attempt(run)
    return run


def owner(run):
    return dict(run["self_check_scope"])


def test_copied_owner_after_closed_scope_cannot_insert_or_downgrade(store, monkeypatch):
    run = parent()
    copied, release, copied_event = owner(run), threading.Event(), threading.Event()
    token = dict(token_id="t", project="p", group_id="g")
    monkeypatch.setattr(graph_source_hooks, "write_session", nullcontext)
    monkeypatch.setattr(service, "_attempt_target", lambda *a: {})
    errors = []
    def admit(target, *_):
        assert target["progress_owner"] == copied
        copied_event.set()
        assert release.wait(5)
        return pending(target["progress_owner"])
    monkeypatch.setattr(service, "_admit", admit)
    def start():
        try:
            service._start(dict(project_id="p", group_id="g", caller_token=token), {}, None)
        except Exception as exc:
            errors.append(exc)
    t = threading.Thread(target=start)
    t.start()
    try:
        assert copied_event.wait(5)
        progress.end_owned(run, boundary="hop", reason="timeout")
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()
    assert len(errors) == 1 and errors[0].code == "selfcheck_owner_scope_closed"
    assert db.get_active("p", "g") is None
    assert store._fetch_one("SELECT COUNT(*) AS n FROM tr_self_check_runs")["n"] == 0
    with pytest.raises(db.ProgressOwnerClosedError):
        progress.admission_owner(token, "p", "g")
    assert progress.admission_owner({**token, "token_id": "independent"}, "p", "g") is None


def test_admitted_before_fence_is_in_cancel_set_and_slot_stays_reserved(store):
    run = parent()
    row = pending(owner(run))
    rid = row["self_check_run_id"]
    progress.end_owned(run, boundary="hop", reason="timeout")
    current = db.get_run(rid)
    assert current["status"] == "pending" and current["cancel_requested"]
    assert current["active_key"] == row["active_key"] and current["finished_at"] is None
    with pytest.raises(db.SelfCheckAlreadyRunningError):
        pending(None)
    progress.end_owned(run, boundary="hop", reason="duplicate")
    assert db.get_run(rid) == current
    assert store._fetch_one("SELECT close_reason FROM tr_self_check_owner_scopes")["close_reason"] == "timeout"


def test_hop_close_includes_previous_token_and_attempt_but_delayed_attempt_does_not(store):
    run = parent()
    old = owner(run)
    rid = pending(old)["self_check_run_id"]
    progress.bind_token(run, "next-token")
    progress.open_attempt(run)  # same hop; former child still owns the Group slot
    current = owner(run)
    assert current["provider_attempt_id"] != old["provider_attempt_id"]
    progress.end_owned(run, owner=current, reason="current_failed")
    assert not db.get_run(rid)["cancel_requested"]
    progress.end_owned(run, boundary="hop", reason="hop_ended")
    assert db.get_run(rid)["cancel_requested"]
    db.finish_cancelled(rid, exit_code=-1, stdout_tail="", stderr_tail="")
    run["submission_hop"] = 1
    progress.open_attempt(run)
    following = pending(owner(run))["self_check_run_id"]
    progress.end_owned(run, owner=old, reason="delayed_old_cleanup")
    assert not db.get_run(following)["cancel_requested"]
    assert progress.owner_for(dict(token_id="t", project="p", group_id="g"), "p", "g") == owner(run)
    assert progress.observe(run)["seq"] == 1
    db.record_effective_progress(rid, status="cancelled", output_bytes=9999)
    assert progress.observe(run)["seq"] == 1


@pytest.mark.parametrize("kind", ["independent", "other_run", "other_hop", "next_attempt"])
def test_nonowned_active_run_is_never_cancelled(store, kind):
    run = parent()
    previous = owner(run)
    if kind == "independent":
        target = None
    elif kind == "other_run":
        target = owner(parent("other", "other-token"))
    else:
        if kind == "other_hop":
            run["submission_hop"] = 1
        progress.open_attempt(run)
        target = owner(run)
    rid = pending(target)["self_check_run_id"]
    before = db.get_run(rid)
    progress.end_owned(run, owner=previous, reason="late_failure")
    assert db.get_run(rid) == before


@pytest.mark.parametrize("kind", ["completed", "failed", "timeout", "cancelled"])
def test_terminal_payload_is_immutable_under_duplicate_cleanup(store, kind):
    run = parent()
    rid = pending(owner(run))["self_check_run_id"]
    if kind == "failed":
        db.finish_failed(rid, error_code="test_error", exit_code=9, stdout_tail="out", stderr_tail="err")
    elif kind == "cancelled":
        db.finish_cancelled(rid, exit_code=-1, stdout_tail="out", stderr_tail="err")
    else:
        db.finish_completed(rid, exit_code=1 if kind == "timeout" else 0,
                            timed_out=kind == "timeout", stdout_tail="out", stderr_tail="err")
    if kind == "completed":
        db.record_result_summary(rid, '{"status":"complete","outcomes":{"passed":1}}',
                                 '{"tools":{"pytest":"test-fixture"}}')
    before = db.get_run(rid)
    if kind == "completed":
        assert before["result_summary"] and before["environment_evidence"]
    progress.end_owned(run, boundary="run", reason="parent_timeout")
    progress.end_owned(run, boundary="run", reason="duplicate")
    assert db.get_run(rid) == before


def test_actual_status_not_progress_cache_drives_cancel_and_reaper_retries_intent(store, monkeypatch):
    run = parent()
    rid = pending(owner(run))["self_check_run_id"]
    db.record_effective_progress(rid, status="completed")
    # Model a crash after the durable fence and before request_cancel.
    db.close_owner_scopes([owner(run)], boundary="hop", reason="crash", cancel=True)
    assert not db.get_run(rid)["cancel_requested"]
    monkeypatch.setattr(service, "_reprove", lambda row: (service.KEPT, None))
    monkeypatch.setattr(service.lock_manager, "selfcheck_protected_locks", lambda: [])
    service.reap()
    assert db.get_run(rid)["cancel_requested"]
    assert db.get_run(rid)["status"] == "pending"
    assert db.owned_cancel_runs()[0]["self_check_run_id"] == rid


def test_normal_provider_return_fences_new_admission_but_preserves_running_child(store):
    run = parent()
    rid = pending(owner(run))["self_check_run_id"]
    progress.end_owned(run, cancel=False)
    assert not db.get_run(rid)["cancel_requested"]
    with pytest.raises(db.ProgressOwnerClosedError):
        progress.admission_owner(dict(token_id="t", project="p", group_id="g"), "p", "g")
    db.finish_completed(rid, exit_code=0, timed_out=False, stdout_tail="natural", stderr_tail="")
    with pytest.raises(db.ProgressOwnerClosedError):
        pending(owner(run))
    before = db.get_run(rid)
    progress.end_owned(run, boundary="hop", reason="hop_done")
    assert db.get_run(rid) == before


def test_close_waiting_for_sql_never_holds_progress_registry(store, monkeypatch):
    run = parent()
    entered, release, polled = threading.Event(), threading.Event(), threading.Event()
    real = db.close_owner_scopes
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(db, "close_owner_scopes", blocked)
    t = threading.Thread(target=lambda: progress.end_owned(run))
    t.start()
    try:
        assert entered.wait(5)
        probe = threading.Thread(target=lambda: (progress.owner_for(
            dict(token_id="unrelated", project="p", group_id="g"), "p", "g"), polled.set()))
        probe.start()
        assert polled.wait(1)
        probe.join(1)
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()


def test_admission_transaction_wins_before_close_and_closure_sees_the_child(store, monkeypatch):
    run, entered, release, closed = parent(), threading.Event(), threading.Event(), threading.Event()
    real = db._require_progress_owner
    rows, errors = [], []
    def barrier(*args):
        real(*args)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(db, "_require_progress_owner", barrier)
    def admit():
        try:
            rows.append(pending(owner(run)))
        except Exception as exc:
            errors.append(exc)
    def close():
        try:
            progress.end_owned(run, boundary="hop")
        except Exception as exc:
            errors.append(exc)
        finally:
            closed.set()
    a = threading.Thread(target=admit)
    a.start()
    assert entered.wait(5)
    b = threading.Thread(target=close)
    b.start()
    try:
        assert not closed.wait(.1)
    finally:
        release.set()
        a.join(5)
        b.join(5)
    assert not errors and not a.is_alive() and not b.is_alive()
    assert db.get_run(rows[0]["self_check_run_id"])["cancel_requested"]


def test_durable_binding_without_children_survives_registry_loss(store, monkeypatch):
    run = dict(run_id="durable", project_id="p", group_id="g", token_id="never-started")
    progress.open_attempt(run, durable=True)
    assert store._fetch_one("SELECT scope_id FROM tr_self_check_owner_scopes")
    monkeypatch.setattr(progress, "_bindings", {})
    monkeypatch.setattr(progress, "_managed", {})
    with pytest.raises(db.ProgressOwnerClosedError):
        progress.admission_owner(dict(token_id="never-started", project="p", group_id="g"), "p", "g")
    assert db.get_active("p", "g") is None


def test_one_provider_attempt_ends_all_its_hops_but_not_new_provider(store):
    run = parent()
    first = owner(run)
    run["register_context"] = {"doc": "next-doc", "action": "edit"}
    progress.bind_token(run, "second")
    last = owner(run)
    assert first["hop_id"] != last["hop_id"]
    assert first["provider_attempt_id"] == last["provider_attempt_id"]
    rid = pending(last)["self_check_run_id"]
    progress.end_owned(run, owner=first, boundary="hop", reason="delayed_old_hop")
    assert not db.get_run(rid)["cancel_requested"]
    assert run["self_check_attempt_id"] == last["provider_attempt_id"]
    progress.bind_token(run, "third")
    assert owner(run)["provider_attempt_id"] == last["provider_attempt_id"]
    progress.end_owned(run, owner=first, reason="provider_failed")
    assert db.get_run(rid)["cancel_requested"]
    with pytest.raises(db.ProgressOwnerClosedError):
        progress.admission_owner(dict(token_id="second", project="p", group_id="g"), "p", "g")


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgres", "mysql"])
def test_owner_scope_ddl_and_transaction_queries_are_portable(store, dialect_name, monkeypatch):
    # Exercise all generated DDL and both row-lock branches. Native remote engines
    # are not provisioned by this harness; their placeholder translations are checked.
    import sqlite3
    from pathlib import Path
    from modules.flow_gate.db import dialect
    root = Path(__file__).resolve().parents[4]
    sql = (root / "sql/migrations" / dialect_name / "179_tr_self_check_owner_scopes.sql").read_text()
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(sql)
        assert len(conn.execute("PRAGMA table_info(tr_self_check_owner_scopes)").fetchall()) == 10
        assert len(conn.execute("PRAGMA index_list(tr_self_check_owner_scopes)").fetchall()) == 3

    class Adapter:
        def __init__(self):
            self.dialect = {"sqlite": dialect.SQLITE, "postgres": dialect.POSTGRESQL,
                            "mysql": dialect.MYSQL}[dialect_name]
            self.queries = []
        def _fetch_one(self, query, params):
            self.queries.append(query)
            translated = dialect.translate(query, self.dialect)
            if self.dialect != dialect.SQLITE:
                assert translated.count("%s") == len(params)
            return store._fetch_one(query.replace(" FOR UPDATE", ""), params)
        def _execute_affected(self, query, params):
            self.queries.append(query)
            assert dialect.translate(query, self.dialect).count(
                "?" if self.dialect == dialect.SQLITE else "%s") == len(params)
            return store._execute_affected(query, params)
        def _execute(self, query, params):
            self.queries.append(query)
            assert dialect.translate(query, self.dialect).count(
                "?" if self.dialect == dialect.SQLITE else "%s") == len(params)
            return store._execute(query, params)
        def _fetch_all(self, query, params):
            self.queries.append(query)
            assert dialect.translate(query, self.dialect).count(
                "?" if self.dialect == dialect.SQLITE else "%s") == len(params)
            return store._fetch_all(query, params)
        def transaction(self):
            return store.transaction()
    adapter = Adapter()
    with store.transaction():
        db._lock_host_capacity(adapter)
    assert ("FOR UPDATE" in adapter.queries[0]) == (dialect_name != "sqlite")
    monkeypatch.setattr(db, "get_store", lambda: adapter)
    run = parent()
    db.register_owner_scope(owner(run))
    rid = pending(owner(run))["self_check_run_id"]
    ids = db.close_owner_scopes([owner(run)], boundary="hop", reason="closed", cancel=True)
    assert db.owned_cancel_runs(ids)[0]["self_check_run_id"] == rid
    assert db.managed_owner_token("t", "p", "g")


def test_token_rotation_closes_copied_scope_without_cancelling_its_child(store):
    run = dict(run_id="rotate", project_id="p", group_id="g", token_id="one")
    progress.open_attempt(run, durable=True)
    old = owner(run)
    rid = pending(old)["self_check_run_id"]
    progress.bind_token(run, "two")
    assert not db.get_run(rid)["cancel_requested"]
    db.finish_completed(rid, exit_code=0, timed_out=False, stdout_tail="old", stderr_tail="")
    with pytest.raises(db.ProgressOwnerClosedError):
        pending(old)
    new_id = pending(owner(run))["self_check_run_id"]
    progress.end_owned(run, boundary="hop", reason="done")
    assert db.get_run(new_id)["cancel_requested"] and not db.get_run(rid)["cancel_requested"]


def test_other_project_group_and_document_have_no_cleanup_effect(store):
    run = parent()
    store._execute("INSERT INTO projects(project_id,project_name,created_at,updated_at) VALUES ('q','Q','now','now')")
    store._execute("INSERT INTO groups(group_id,project_id,module,title,status,created_at,updated_at) VALUES ('gq','q','v02','G','OPEN','now','now')")
    store._execute("INSERT INTO documents(doc_id,project_id,group_id,module,type_code,seq,title,status,branch,created_at,updated_at) VALUES ('dq','q','gq','v02','TR',1,'TR','draft','main','now','now')")
    other = dict(run_id="a", project_id="q", group_id="gq", token_id="other")
    progress.open_attempt(other)
    rid = db.create_pending("q", "gq", "dq", None, "1", "pytest", [], "pytest",
                            "pytest", "host", ".", 10, [], progress_owner=owner(other))["self_check_run_id"]
    before = db.get_run(rid)
    progress.end_owned(run, boundary="run")
    assert db.get_run(rid) == before


def test_first_verified_context_handoff_closes_old_hop_and_keeps_provider_identity(store, monkeypatch):
    from modules.flow_gate.services.ai_invoke import worker
    run = dict(run_id="handoff", project_id="p", group_id="g", token_id="first",
               doc_ref="old-doc", action_scope="edit")
    progress.open_attempt(run, durable=True)
    previous = owner(run)
    rid = pending(previous)["self_check_run_id"]
    context = dict(project="p", group="g", doc="next-doc", action="edit")
    monkeypatch.setattr(worker.token_service, "verify", lambda raw: dict(token_id="second"))
    monkeypatch.setattr(worker.register_binding, "token_axes", lambda token: (context, "g", "g"))
    assert worker._adopt_continuation_token(run, "verified-test-token")
    assert db.get_run(rid)["cancel_requested"]
    current = progress.admission_owner(dict(token_id="second", project="p", group_id="g"), "p", "g")
    assert current["provider_attempt_id"] == previous["provider_attempt_id"]
    assert current["hop_id"] != previous["hop_id"]
    assert worker._adopt_continuation_token(run, "verified-test-token")
    assert progress.owner_for(dict(token_id="second", project="p", group_id="g"), "p", "g") == current
