"""flowgate.default.0441 TR0005 rev2 — a running test locks the WHOLE group, not one tab.

Rejection: "테스트 중일떄는 \"그룹 내 다른 문서의\" 액션바\"도\" 전부 비활성화 해놔야 할거아냐".

rev1 locked the action bar from the fetched document's own ``test_run`` embed, so only the
tab the run was started from went inert; every sibling document of the same group kept a
fully live bar. The lock now rides on a group-scoped answer the server computes:

  db_test_runs.get_active_by_group  ->  test_run_service.load_group_test_run
                                    ->  GET /documents/{doc_id}["group_test_run"]

The DB layer is exercised against a real sqlite connection with all migrations applied,
because the whole point of the new query is the ``documents`` JOIN — a monkeypatched store
would prove nothing about whether the group filter actually filters.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest


GROUP = "flowgate.default.9441"
OTHER_GROUP = "flowgate.default.9442"
TS_DOC = f"{GROUP}.9001-TS"
TR_DOC = f"{GROUP}.9005-TR"
OTHER_DOC = f"{OTHER_GROUP}.9001-TS"


class _TestStore:
    """Minimal FlowGateStore stand-in bound to all_migrations_db's real connection.

    Same shape as tests/test_test_run_cancel_0358.py: no ``_sql``, so an accidental call
    to a helper this file does not exercise fails loudly instead of silently passing.
    """

    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def transaction(self):
        yield self

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, sql, params=None):
        rows = self._conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]


def _seed_group(conn, group_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO groups "
        "(group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?, '__SYSTEM__', 'default', 'seed group', 'OPEN', "
        "datetime('now'), datetime('now'))",
        [group_id],
    )
    conn.commit()


def _seed_doc(conn, doc_id: str, group_id: str, type_code: str, seq: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        "created_at, updated_at) "
        "VALUES (?, '__SYSTEM__', 'default', ?, ?, ?, 'seed', 'approved', "
        "datetime('now'), datetime('now'))",
        [doc_id, group_id, type_code, seq],
    )
    conn.commit()


def _seed_run(conn, run_id: str, doc_id: str, *, status: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO test_runs "
        "(run_id, doc_id, revision_no, status, triggered_via, runner_id, "
        "case_total, case_passed, case_failed, picked_at, started_at, created_at) "
        "VALUES (?, ?, 0, ?, 'ui', 'user-1', 1, 0, 0, NULL, datetime('now'), ?)",
        [run_id, doc_id, status, created_at],
    )
    conn.commit()


def _clear(conn) -> None:
    conn.execute("DELETE FROM test_runs WHERE run_id LIKE 'trun_9441_%'")
    conn.execute(
        "DELETE FROM documents WHERE group_id IN (?, ?)", [GROUP, OTHER_GROUP]
    )
    conn.execute("DELETE FROM groups WHERE group_id IN (?, ?)", [GROUP, OTHER_GROUP])
    conn.commit()


@pytest.fixture
def store(all_migrations_db, monkeypatch):
    from modules.flow_gate.db import connection

    monkeypatch.setattr(connection, "STORE", _TestStore(all_migrations_db))
    conn = all_migrations_db
    _clear(conn)
    _seed_group(conn, GROUP)
    _seed_group(conn, OTHER_GROUP)
    _seed_doc(conn, TS_DOC, GROUP, "TS", 9001)
    _seed_doc(conn, TR_DOC, GROUP, "TR", 9005)
    _seed_doc(conn, OTHER_DOC, OTHER_GROUP, "TS", 9001)
    try:
        yield conn
    finally:
        _clear(conn)


# ── DB layer: the group JOIN ────────────────────────────────────────────────


def test_a_running_run_on_the_ts_is_visible_from_the_group(store):
    """The query the sibling TR's action bar ultimately depends on."""
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_run(store, "trun_9441_1", TS_DOC, status="running", created_at="2026-08-26T10:00:00+09:00")

    row = db_test_runs.get_active_by_group(GROUP)
    assert row is not None
    assert row["run_id"] == "trun_9441_1"
    assert row["doc_id"] == TS_DOC

    # The per-document gate still answers only for the document that owns the run —
    # that asymmetry is the whole reason the group query had to be added.
    assert db_test_runs.get_running_by_doc(TS_DOC) is not None
    assert db_test_runs.get_running_by_doc(TR_DOC) is None


def test_another_group_is_not_locked_by_this_run(store):
    """Negative control. Without the group filter every screen in the project would lock."""
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_run(store, "trun_9441_2", TS_DOC, status="running", created_at="2026-08-26T10:00:00+09:00")

    assert db_test_runs.get_active_by_group(OTHER_GROUP) is None


@pytest.mark.parametrize("status", ["running", "cancelling"])
def test_both_active_statuses_count(store, status):
    """'cancelling' is still an in-flight run — the kill/cleanup has not landed yet."""
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_run(store, "trun_9441_3", TS_DOC, status=status, created_at="2026-08-26T10:00:00+09:00")

    assert db_test_runs.get_active_by_group(GROUP) is not None


# The table CHECK admits exactly these five statuses; the two active ones are covered above.
@pytest.mark.parametrize("status", ["passed", "failed", "cancelled"])
def test_a_terminal_run_unlocks_the_group(store, status):
    """The other direction of the regression: a finished run must not lock forever."""
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_run(store, "trun_9441_4", TS_DOC, status=status, created_at="2026-08-26T10:00:00+09:00")

    assert db_test_runs.get_active_by_group(GROUP) is None


def test_the_newest_active_run_wins_when_two_are_in_flight(store):
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_run(store, "trun_9441_5", TS_DOC, status="running", created_at="2026-08-26T10:00:00+09:00")
    _seed_run(store, "trun_9441_6", TR_DOC, status="cancelling", created_at="2026-08-26T11:00:00+09:00")

    assert db_test_runs.get_active_by_group(GROUP)["run_id"] == "trun_9441_6"


@pytest.mark.parametrize("group_id", ["", None])
def test_a_missing_group_id_answers_none_without_touching_the_store(monkeypatch, group_id):
    """A document with no group (a loose file) must not be able to lock anything."""
    from modules.flow_gate.db import connection, test_runs as db_test_runs

    def _boom():
        raise AssertionError("the store must not be consulted for an empty group id")

    monkeypatch.setattr(connection, "get_store", _boom)
    monkeypatch.setattr(db_test_runs, "get_store", _boom)

    assert db_test_runs.get_active_by_group(group_id) is None


# ── Service layer: the shape the client reads ───────────────────────────────


def test_load_group_test_run_reports_active_with_the_owning_document(store):
    from modules.flow_gate.services import test_run_service

    _seed_run(store, "trun_9441_7", TS_DOC, status="running", created_at="2026-08-26T10:00:00+09:00")

    block = test_run_service.load_group_test_run(GROUP)
    assert block == {
        "active": True,
        "run_id": "trun_9441_7",
        "doc_id": TS_DOC,
        "status": "running",
    }


def test_load_group_test_run_is_inactive_with_no_run(store):
    from modules.flow_gate.services import test_run_service

    assert test_run_service.load_group_test_run(GROUP) == {
        "active": False, "run_id": None, "doc_id": None, "status": None,
    }


def test_load_group_test_run_fails_closed_to_unconfirmed_on_a_db_error(monkeypatch):
    """A display-only extra must never take the document GET down with it.

    0441 TR0005 rev10: this used to assert ``active is False`` — but ``False`` is what the
    client reads as "confirmed: no run in this group", which unlocks every sibling document's
    action bar. A DB error is not a confirmation of anything; it must answer ``None``
    (unconfirmed) so DocHeader/MainPanel's fail-closed default keeps every sibling locked.
    """
    from modules.flow_gate.services import test_run_service

    def _raise(_group_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(test_run_service.db_test_runs, "get_active_by_group", _raise)

    assert test_run_service.load_group_test_run(GROUP) == {
        "active": None, "run_id": None, "doc_id": None, "status": None,
    }


# ── Route layer: the field lands on every document of the group ─────────────


def _fake_user():
    return {"user_id": "usr_admin", "is_admin": True}


def test_get_document_ships_group_test_run_for_a_sibling_document(monkeypatch):
    """The TR is not the document running the test, yet its payload says the group is busy."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    doc = {
        "doc_id": TR_DOC,
        "project_id": "flowgate",
        "group_id": GROUP,
        "type_code": "TR",
        "title": "TR",
        "doc_review_status": "pending_review",
    }
    asked: list[str | None] = []

    monkeypatch.setattr(doc_routes.document_service, "get_document", lambda _id: doc)
    monkeypatch.setattr(doc_routes, "_parse_doc_workflow", lambda d: dict(d))
    monkeypatch.setattr(doc_routes, "_load_ai_reviews", lambda _id: (None, []))
    monkeypatch.setattr(doc_routes, "_load_test_runs", lambda _id: (None, []))

    def _fake_group_load(group_id):
        asked.append(group_id)
        return {"active": True, "run_id": "trun_9441_8", "doc_id": TS_DOC, "status": "running"}

    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(test_run_service, "load_group_test_run", _fake_group_load)

    out = doc_routes.get_document(TR_DOC, _fake_user())

    # Asked about the GROUP, not about the fetched document.
    assert asked == [GROUP]
    assert out["test_run"] is None
    assert out["group_test_run"]["active"] is True
    assert out["group_test_run"]["doc_id"] == TS_DOC


def test_get_document_always_carries_the_key_even_when_idle(monkeypatch):
    """The client reads `group_test_run.active`; an absent key would read as undefined."""
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.services import test_run_service

    doc = {"doc_id": TR_DOC, "project_id": "flowgate", "group_id": GROUP, "type_code": "TR"}
    monkeypatch.setattr(doc_routes.document_service, "get_document", lambda _id: doc)
    monkeypatch.setattr(doc_routes, "_parse_doc_workflow", lambda d: dict(d))
    monkeypatch.setattr(doc_routes, "_load_ai_reviews", lambda _id: (None, []))
    monkeypatch.setattr(doc_routes, "_load_test_runs", lambda _id: (None, []))
    monkeypatch.setattr(
        test_run_service,
        "load_group_test_run",
        lambda _g: {"active": False, "run_id": None, "doc_id": None, "status": None},
    )

    out = doc_routes.get_document(TR_DOC, _fake_user())

    assert "group_test_run" in out
    assert out["group_test_run"]["active"] is False


def test_get_document_group_test_run_fails_closed_to_unconfirmed_not_false(monkeypatch):
    """The route's own except-branch must not contradict the service layer's fail-closed value.

    0441 TR0005 rev10: this drives a failure through `_load_group_test_run`'s own `except`
    (not the service's), by making `load_group_test_run` itself raise, so both independent
    fail-closed layers are pinned to the same `None` (unconfirmed) answer.
    """
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.services import test_run_service

    doc = {"doc_id": TR_DOC, "project_id": "flowgate", "group_id": GROUP, "type_code": "TR"}
    monkeypatch.setattr(doc_routes.document_service, "get_document", lambda _id: doc)
    monkeypatch.setattr(doc_routes, "_parse_doc_workflow", lambda d: dict(d))
    monkeypatch.setattr(doc_routes, "_load_ai_reviews", lambda _id: (None, []))
    monkeypatch.setattr(doc_routes, "_load_test_runs", lambda _id: (None, []))

    def _raise(_g):
        raise RuntimeError("db down")

    monkeypatch.setattr(test_run_service, "load_group_test_run", _raise)

    out = doc_routes.get_document(TR_DOC, _fake_user())

    assert out["group_test_run"] == {
        "active": None, "run_id": None, "doc_id": None, "status": None,
    }
def test_end_to_end_the_sibling_document_payload_reports_the_run_from_the_real_db(store, monkeypatch):
    """No stubbing between the route and the table — the whole server half in one call.

    Only ``document_service.get_document`` and the two unrelated embeds are replaced (they
    reach for files and review rows this fixture does not seed). The group block is computed
    for real: route -> test_run_service.load_group_test_run -> db_test_runs.get_active_by_group
    -> the JOIN against the seeded documents/test_runs rows.
    """
    from modules.flow_gate.documents.routers import documents as doc_routes

    _seed_run(store, "trun_9441_9", TS_DOC, status="running", created_at="2026-08-26T10:00:00+09:00")

    doc = {
        "doc_id": TR_DOC,
        "project_id": "flowgate",
        "group_id": GROUP,
        "type_code": "TR",
        "title": "TR",
    }
    monkeypatch.setattr(doc_routes.document_service, "get_document", lambda _id: doc)
    monkeypatch.setattr(doc_routes, "_parse_doc_workflow", lambda d: dict(d))
    monkeypatch.setattr(doc_routes, "_load_ai_reviews", lambda _id: (None, []))
    monkeypatch.setattr(doc_routes, "_load_test_runs", lambda _id: (None, []))

    out = doc_routes.get_document(TR_DOC, _fake_user())

    assert out["group_test_run"] == {
        "active": True,
        "run_id": "trun_9441_9",
        "doc_id": TS_DOC,
        "status": "running",
    }
