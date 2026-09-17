"""Regression coverage for flowgate.default.0552 T0015 startup scan reuse."""
from __future__ import annotations

import os
from unittest.mock import Mock

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")

from modules.flow_gate.services import git_service as svc


def _session(merge_id=1, kind="merge", review_state=None):
    context = {"kind": kind}
    if review_state is not None:
        context["review_state"] = review_state
    return {
        "merge_id": merge_id,
        "group_id": "flowgate.default.0552",
        "status": "open",
        "kind": kind,
        "context": context,
        "created_at": "2099-01-01T00:00:00+00:00",
    }


def _startup_stubs(monkeypatch, sessions):
    listed = Mock(return_value=sessions)
    monkeypatch.setattr(svc.db_git, "list_open_sessions", listed)
    monkeypatch.setattr(svc.db_git, "list_locks", Mock(return_value=[]))
    monkeypatch.setattr(svc, "_project_of_group", Mock(return_value="flowgate"))
    monkeypatch.setattr(svc, "_start_sweep_daemon", Mock())
    return listed


def test_startup_reads_open_sessions_once_even_when_empty(monkeypatch):
    listed = _startup_stubs(monkeypatch, [])
    svc.startup_recovery()
    assert listed.call_count == 1


# T0004: startup_recovery must force-release EVERY project lock row regardless
# of holder string. The old code whitelisted only op:/sweep:/merge:/dispose:
# prefixes, so a lock held by e.g. trcommit:/review:/archive: survived a
# restart forever. These cases must all be cleaned, including a holder string
# that is not on any known list today (a future prefix must not need a
# whitelist update to be swept).
def test_startup_recovery_force_releases_every_lock_regardless_of_holder(monkeypatch):
    locks = [
        {"project_id": "flowgate", "holder": "op:11111111-1111-1111-1111-111111111111"},
        {"project_id": "other-project", "holder": "trcommit:22222222-2222-2222-2222-222222222222"},
        {"project_id": "third-project", "holder": "trconflict:33333333-3333-3333-3333-333333333333"},
        {"project_id": "fourth-project", "holder": "review:1:44444444-4444-4444-4444-444444444444"},
        {"project_id": "fifth-project", "holder": "reconcile:55555555-5555-5555-5555-555555555555"},
        {"project_id": "sixth-project", "holder": "discard:66666666-6666-6666-6666-666666666666"},
        {"project_id": "seventh-project", "holder": "archive:77777777-7777-7777-7777-777777777777"},
        {"project_id": "eighth-project", "holder": "archive-restore:88888888-8888-8888-8888-888888888888"},
        {"project_id": "ninth-project", "holder": "archive-purge:99999999-9999-9999-9999-999999999999"},
        {"project_id": "tenth-project", "holder": "resolve_base_dirty:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        {"project_id": "eleventh-project", "holder": "cancel:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
        {"project_id": "twelfth-project", "holder": "terminal-reopen:cccccccc-cccc-cccc-cccc-cccccccccccc"},
        {"project_id": "thirteenth-project", "holder": "initial_sync:dddddddd-dddd-dddd-dddd-dddddddddddd"},
        {"project_id": "fourteenth-project", "holder": "some-future-holder-not-on-any-list:eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"},
    ]
    _startup_stubs(monkeypatch, [])
    monkeypatch.setattr(svc.db_git, "list_locks", Mock(return_value=locks))
    released = Mock()
    monkeypatch.setattr(svc.db_git, "force_release_lock", released)

    svc.startup_recovery()

    assert released.call_count == len(locks)
    released.assert_any_call("flowgate")
    released.assert_any_call("other-project")
    released.assert_any_call("third-project")
    released.assert_any_call("fourth-project")
    released.assert_any_call("fifth-project")
    released.assert_any_call("sixth-project")
    released.assert_any_call("seventh-project")
    released.assert_any_call("eighth-project")
    released.assert_any_call("ninth-project")
    released.assert_any_call("tenth-project")
    released.assert_any_call("eleventh-project")
    released.assert_any_call("twelfth-project")
    released.assert_any_call("thirteenth-project")
    released.assert_any_call("fourteenth-project")


def test_startup_recovery_releases_nothing_when_no_locks_exist(monkeypatch):
    _startup_stubs(monkeypatch, [])
    released = Mock()
    monkeypatch.setattr(svc.db_git, "force_release_lock", released)

    svc.startup_recovery()

    released.assert_not_called()


def test_startup_orphan_is_closed_once_and_not_reoffered_to_sweep(monkeypatch):
    session = _session()
    listed = _startup_stubs(monkeypatch, [session])
    monkeypatch.setattr(svc, "_base_root_of", Mock(return_value=None))
    close_orphan = Mock()
    monkeypatch.setattr(svc, "_close_orphan", close_orphan)
    monkeypatch.setattr(svc.db_git, "get_session", Mock(return_value=None))
    seen = []
    monkeypatch.setattr(svc, "merge_session_sweep", lambda sessions=None: seen.extend(sessions or []))

    svc.startup_recovery()

    assert listed.call_count == 1
    close_orphan.assert_called_once_with(session, "flowgate")
    assert seen == []


def test_startup_reconcile_uses_snapshot_but_sweep_gets_fresh_pending_row(monkeypatch):
    original = _session(review_state=svc.REVIEW_STATE_RECONCILING)
    fresh = _session(review_state=svc.REVIEW_STATE_PENDING)
    listed = _startup_stubs(monkeypatch, [original])
    monkeypatch.setattr(svc, "_set_status", Mock())
    monkeypatch.setattr(svc.db_git, "get_session", Mock(return_value=fresh))
    reconciled = []
    swept = []
    monkeypatch.setattr(
        svc, "reconcile_due_merge_review_sessions",
        lambda trigger, sessions=None: reconciled.extend(sessions or []),
    )
    monkeypatch.setattr(svc, "merge_session_sweep", lambda sessions=None: swept.extend(sessions or []))

    svc.startup_recovery()

    assert listed.call_count == 1
    assert reconciled == [original]
    assert swept == [fresh]


def test_periodic_sweep_without_argument_still_reads_its_own_list(monkeypatch):
    session = _session(kind=svc.db_git.SESSION_KIND_GROUP_UPDATE)
    listed = Mock(return_value=[session])
    monkeypatch.setattr(svc.db_git, "list_open_sessions", listed)
    monkeypatch.setattr(svc, "_project_of_group", Mock(return_value="flowgate"))
    swept = Mock()
    monkeypatch.setattr(svc, "_sweep_group_update_session", swept)

    svc.merge_session_sweep()

    listed.assert_called_once_with()
    swept.assert_called_once_with(session, "flowgate")


def test_explicit_empty_lists_never_trigger_a_hidden_rescan(monkeypatch):
    listed = Mock(side_effect=AssertionError("must not rescan"))
    monkeypatch.setattr(svc.db_git, "list_open_sessions", listed)

    svc.reconcile_due_merge_review_sessions("server_startup", sessions=[])
    svc.merge_session_sweep(sessions=[])

    listed.assert_not_called()


# NR0025 T0030 §3.6 (권고4) -- the facade owns `_sweep_daemon_started`; reading it
# through `svc.` sees the real state, and resetting it through `svc.` re-arms the
# daemon (0550 §3.5). Never let a real thread run: `_start_sweep_daemon` does
# `import threading` inside its own body, so patching the real `threading` module's
# `Thread` attribute here reaches it regardless of where it is imported.
def test_sweep_daemon_flag_is_readable_and_resettable_through_the_facade(monkeypatch):
    import threading

    started_names = []

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.target = target
            self.name = name
            self.daemon = daemon
            started_names.append(name)

        def start(self):
            pass  # a real _loop would block on time.sleep(1800) -- never run it

    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setattr(svc, "_sweep_daemon_started", False)

    # 1. First call launches one thread and the facade attribute is now True --
    #    reading it through `svc.` sees the real state the daemon flipped.
    svc._start_sweep_daemon()
    assert started_names == ["git-merge-sweep"]
    assert svc._sweep_daemon_started is True

    # 2. Idempotent: a second call while the flag is still True launches nothing more.
    svc._start_sweep_daemon()
    assert started_names == ["git-merge-sweep"]

    # 3. Resetting the flag from OUTSIDE the facade (exactly the shape NR0025 §9
    #    describes: `git_service._sweep_daemon_started = False`) re-arms the daemon --
    #    a second, distinct thread gets started.
    svc._sweep_daemon_started = False
    svc._start_sweep_daemon()
    assert started_names == ["git-merge-sweep", "git-merge-sweep"]

    # 4. And the flag reads True again afterward.
    assert svc._sweep_daemon_started is True


# Backfill scan-scope regression: candidate discovery must precede any existing-row read.
import json
from contextlib import contextmanager
from modules.flow_gate.db.backfills import register_context_failure_backfill as register_backfill


class _BackfillDB:
    db_type = "sqlite"

    def __init__(self, run_rows, existing=()):
        self.run_rows = run_rows
        self.existing = list(existing)
        self.fetches = []
        self.executed = []

    def fetch_all(self, sql, params):
        self.fetches.append((sql, list(params)))
        if "FROM ai_invoke_runs" in sql:
            return self.run_rows
        if "FROM register_context_failures" in sql:
            wanted = set(params)
            return [row for row in self.existing if not wanted or row["correlation_id"] in wanted]
        raise AssertionError(sql)

    @contextmanager
    def begin_transaction(self):
        owner = self

        class _Txn:
            def execute(self, sql, params):
                owner.executed.append((sql, list(params)))

        yield _Txn()


def _legacy_run(errors):
    return {
        "run_id": "run-0552",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0552",
        "doc_ref": "flowgate.default.0552.0015-T",
        "register_errors": json.dumps(errors),
        "updated_at": "2026-09-11T00:00:00+09:00",
        "created_at": "2026-09-11T00:00:00+09:00",
    }


def test_backfill_with_no_candidates_skips_existing_table_entirely():
    db = _BackfillDB([_legacy_run(["broken", {"reason": "truncated", "dropped": 1}])])

    assert register_backfill.run_register_context_failure_backfill(db) == 0
    assert len(db.fetches) == 1
    assert "FROM ai_invoke_runs" in db.fetches[0][0]


def test_backfill_reads_only_candidate_ids_and_remains_idempotent():
    errors = [{"status": 403, "reason": "legacy", "turn": 1}]
    candidate_db = _BackfillDB([_legacy_run(errors)])
    assert register_backfill.run_register_context_failure_backfill(candidate_db) == 1
    scoped = [call for call in candidate_db.fetches if "register_context_failures" in call[0]]
    assert len(scoped) == 1
    assert "WHERE correlation_id IN (?)" in scoped[0][0]
    correlation_id = candidate_db.executed[0][1][2]

    replay_db = _BackfillDB(
        [_legacy_run(errors)],
        existing=[{"correlation_id": correlation_id, "boundary": "legacy_unclassified"}],
    )
    assert register_backfill.run_register_context_failure_backfill(replay_db) == 0
    assert replay_db.executed == []


def test_backfill_candidate_lookup_chunks_at_500_without_full_scan():
    errors = [
        {"status": 403, "reason": "legacy", "turn": i, "correlation_id": f"corr-{i}"}
        for i in range(501)
    ]
    db = _BackfillDB([_legacy_run(errors)])

    assert register_backfill.run_register_context_failure_backfill(db) == 501
    scoped = [call for call in db.fetches if "register_context_failures" in call[0]]
    assert [len(params) for _sql, params in scoped] == [500, 1]
    assert all("WHERE correlation_id IN (" in sql for sql, _params in scoped)
