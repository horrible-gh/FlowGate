"""Register mutation context SSOT and its failure telemetry — flowgate.default.0492 T0018.

What B0001 actually was (N0012 / NR0013): the API provider proxy posted every registration
as ``action="new"``, so an edit-scoped run entered ``_handle_new`` and died on its scope
check, three turns running. Fixing the string alone was never enough — ``_handle_test_run``
does not read ``prev_doc_id`` at all, so a test_run "fixed" that way turns the 403 into a 400.

So these tests pin the whole contract, not the one string:

* the provider-facing schemas carry no routing field the model could set (§1);
* the envelope's routing fields come from the run/token context and match each scope's
  handler contract (§2);
* both boundaries compare action/project/group/doc in that fixed order, reject with the
  same body, and leave one axis-classified row per rejection (§3, §4);
* memory diagnostics and the DB row say the same thing, once, even across a finalize retry
  (§5);
* migration 094 applies to a real schema and the legacy backfill invents nothing (§6).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import register_context_failures as db_failures  # noqa: E402
from modules.flow_gate.db.backfills import register_context_failure_backfill as backfill  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as invoke  # noqa: E402
from modules.flow_gate.services import api_server_tools as tools  # noqa: E402
from modules.flow_gate.services import register_binding as binding  # noqa: E402

MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
PROJECT = "flowgate"
GROUP = "flowgate.default.0492"
DOC = "flowgate.default.0492.0008-TR"

# Every routing name a model must never be able to set, in any spelling the handlers accept.
SERVER_OWNED_FIELDS = {
    "action", "project", "module", "group", "group_name", "doc_id", "doc_ref",
    "target_id", "prev_doc_id", "token", "raw_token", "cwd", "root", "source_root",
}


def _token(scope=None, project=PROJECT, group=GROUP, doc=DOC, token_id="tok-1",
           ai_run_id="aiv_test_000001", **over):
    rec = {
        "token_id": token_id,
        "project": project,
        "group_id": group,
        "doc_ref": doc,
        "action_scope": scope,
        "issued_to": "usr-1",
        "ai_run_id": ai_run_id,
        "provider_id": "prov-1",
    }
    rec.update(over)
    return rec


def _run(scope="new", project=PROJECT, group=GROUP, doc=DOC, **over):
    run = {
        "run_id": "aiv_test_000001",
        "project_id": project,
        "group_id": group,
        "doc_ref": doc,
        "action_scope": scope,
        "module": "default",
        "raw_token": "raw",
        "token_id": "tok-1",
        "current_token_id": "tok-1",
        "api_base_url": "http://127.0.0.1:0/flowgate/api/v1",
        "register_errors": [],
    }
    run.update(over)
    return run


@pytest.fixture
def bound(monkeypatch):
    """A live token verification plus a document/group resolver, both server-side."""
    state = {"token": _token("new")}

    monkeypatch.setattr(invoke.token_service, "verify", lambda _raw: state["token"])
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: state["token"])
    monkeypatch.setattr(
        binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
    )
    return state


# ── §1. The model input surface ────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["new", "edit", "review", "test_run"])
def test_register_schema_exposes_no_server_owned_field(scope):
    """L0010 R4: routing is server property, so it is not even nameable by the model."""
    schema = tools.REGISTER_SCHEMAS[scope]
    assert set(schema["properties"]) & SERVER_OWNED_FIELDS == set()
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("field", sorted(SERVER_OWNED_FIELDS))
def test_register_schema_rejects_an_injected_routing_field(field):
    with pytest.raises(tools.ToolError) as caught:
        tools.validate(tools.REGISTER_SCHEMAS["new"],
                       {"doc_type": "TR", "content": "x", field: "injected"})
    assert caught.value.reason == "schema_validation_failed"


# ── §2. Envelope assembly per scope ────────────────────────────────────────────────────

@pytest.mark.parametrize("scope,doc_field", [
    ("new", "prev_doc_id"), ("edit", "doc_id"), ("review", "doc_id"), ("test_run", "doc_id"),
])
def test_envelope_routing_comes_from_the_context(scope, doc_field):
    context = binding.canonical_context(scope, PROJECT, GROUP, DOC)
    body = invoke._register_envelope(context, _run(scope), {"content": "c", "doc_type": "TR",
                                                            "edit_reason": "worker_self",
                                                            "verdict": "pass"})
    assert body["action"] == scope
    assert body["project"] == PROJECT and body["group_name"] == GROUP
    assert body[doc_field] == DOC
    # The key the OTHER shape uses must be absent, not merely ignored.
    assert ("doc_id" if scope == "new" else "prev_doc_id") not in body


def test_test_run_envelope_carries_the_key_its_handler_reads():
    """NR0013 §4: `_handle_test_run` reads doc_id/doc_ref/target_id and never prev_doc_id,
    so an action-only fix would have produced 'Required field missing: doc_id' (400)."""
    context = binding.canonical_context("test_run", PROJECT, GROUP, DOC)
    body = invoke._register_envelope(context, _run("test_run"), {})
    assert body["doc_id"] == DOC and "prev_doc_id" not in body
    assert (body.get("doc_id") or body.get("doc_ref") or body.get("target_id")) == DOC


def test_model_input_cannot_reach_the_envelope_outside_its_allowlist():
    context = binding.canonical_context("edit", PROJECT, GROUP, DOC)
    body = invoke._register_envelope(context, _run("edit"), {
        "content": "c", "edit_reason": "rejected",
        "action": "new", "doc_id": "other", "group_name": "other", "project": "other",
    })
    assert body["action"] == "edit" and body["doc_id"] == DOC
    assert body["group_name"] == GROUP and body["project"] == PROJECT


def test_b0001_replay_edit_token_no_longer_produces_a_new_action(bound, monkeypatch):
    """The incident, replayed: an edit-scoped run must post action=edit at doc_id."""
    bound["token"] = _token("edit")
    captured = {}
    monkeypatch.setattr(invoke.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    context, _rec = invoke._bind_register_context(_run("edit"), "raw")
    captured = invoke._register_envelope(context, _run("edit"), {"content": "c",
                                                                 "edit_reason": "worker_self"})
    assert captured["action"] == "edit"
    assert captured["doc_id"] == DOC and "prev_doc_id" not in captured
    assert inbox_routes  # the dispatcher picks _handle_edit for this body, not _handle_new


# ── §3. The four axes, both boundaries ────────────────────────────────────────────────

AXIS_CASES = [
    # (token overrides, expected first axis, expected full axes)
    ({"scope": "edit"}, "action", ["action"]),
    ({"project": "other"}, "project", ["project"]),
    ({"group": "flowgate.default.0001"}, "group", ["group"]),
    ({"doc": "flowgate.default.0492.0009-TR"}, "doc", ["doc"]),
    ({"scope": "edit", "project": "other"}, "action", ["action", "project"]),
    ({"project": "other", "group": "flowgate.default.0001"}, "project", ["project", "group"]),
    ({"group": "flowgate.default.0001", "doc": "flowgate.default.0492.0009-TR"},
     "group", ["group", "doc"]),
    ({"scope": "edit", "project": "other", "group": "flowgate.default.0001",
      "doc": "flowgate.default.0492.0009-TR"}, "action",
     ["action", "project", "group", "doc"]),
]


@pytest.mark.parametrize("overrides,first,axes", AXIS_CASES)
def test_dispatch_boundary_names_the_axis_in_fixed_order(bound, monkeypatch, overrides, first, axes):
    bound["token"] = _token(**{"scope": "new", **overrides})
    monkeypatch.setattr(invoke.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("a rejected bind must not do I/O"))
    run = _run("new")
    status, response = invoke._inbox_register(run, "raw", {"doc_type": "TR", "content": "c"})

    assert status == 403
    assert response["error"]["code"] == "forbidden"
    details = response["error"]["details"]
    assert details["reason"] == "context_binding_mismatch"
    assert details["axis"] == first and details["axes"] == axes
    assert details["correlation_id"]

    recorded = run["register_errors"]
    assert len(recorded) == 1
    assert recorded[0]["boundary"] == "register_dispatch"
    assert recorded[0]["axis"] == first and recorded[0]["axes"] == axes


@pytest.mark.parametrize("overrides,first,axes", AXIS_CASES)
def test_inbox_boundary_names_the_same_axis(bound, monkeypatch, overrides, first, axes):
    bound["token"] = _token(**{"scope": "new", **overrides})
    monkeypatch.setattr(
        inbox_routes.register_binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
    )
    failure = inbox_routes._check_context_binding(
        action_handler="new", project=PROJECT, doc=DOC,
        token_rec=bound["token"], group=GROUP, prev_doc_id=DOC,
    )
    assert failure is not None
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error_message"] == binding.BINDING_MESSAGE
    assert payload["error"]["details"]["axis"] == first
    assert payload["error"]["details"]["axes"] == axes


def test_binding_response_leaks_no_identifier(bound):
    bound["token"] = _token(scope="edit", token_id="tok-secret",
                            doc="flowgate.default.0001.0001-B")
    run = _run("new")
    _status, response = invoke._inbox_register(run, "raw-secret", {"doc_type": "TR", "content": "c"})
    blob = json.dumps(response, ensure_ascii=False)
    for secret in ("tok-secret", "raw-secret", "flowgate.default.0001.0001-B"):
        assert secret not in blob


def test_permission_denied_is_not_a_binding_failure():
    response = inbox_routes._permission_denied("Insufficient permissions for this operation")
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert response.status_code == 403
    assert payload["error"]["code"] == "forbidden"
    assert payload["error"]["details"] == {"reason": "permission_denied"}
    assert "axis" not in payload["error"]["details"]


def test_a_matching_context_passes_and_records_nothing(bound, monkeypatch):
    bound["token"] = _token("new")
    monkeypatch.setattr(invoke, "_register_envelope", lambda *a: {"stop": True})
    monkeypatch.setattr(
        invoke.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network disabled in test")),
    )
    run = _run("new")
    status, _response = invoke._inbox_register(run, "raw", {"doc_type": "TR", "content": "c"})
    assert status == 0  # the transport error path, i.e. binding passed
    assert run["register_errors"] == []


def test_a_token_from_the_previous_hop_is_refused_before_any_side_effect(bound, monkeypatch):
    """L0010 §5 continuation race: the axes may still line up, the token is still stale."""
    bound["token"] = _token("new", token_id="tok-previous")
    monkeypatch.setattr(invoke.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("a stale token must not reach /inbox"))
    run = _run("new", current_token_id="tok-current")
    status, response = invoke._inbox_register(run, "raw", {"doc_type": "TR", "content": "c"})
    assert status == 403
    assert response["error"]["details"]["reason"] == "token_not_current"
    # Not an axis fault, so it must not be filed as one.
    assert run["register_errors"] == []


def test_continuation_moves_the_token_and_the_axes_together(bound):
    run = _run("new")
    bound["token"] = _token("edit", token_id="tok-2",
                            doc="flowgate.default.0492.0009-TR")
    assert invoke._adopt_continuation_token(run, "next-raw") is True
    assert run["current_token_id"] == "tok-2"
    assert run["register_context"]["action"] == "edit"
    assert run["register_context"]["doc"] == "flowgate.default.0492.0009-TR"
    # And the run's own project/group stay pinned.
    assert run["register_context"]["project"] == PROJECT
    assert run["register_context"]["group"] == GROUP


def test_a_continuation_that_leaves_the_group_is_refused(bound):
    run = _run("new")
    before = dict(invoke._run_register_context(run))
    bound["token"] = _token("new", token_id="tok-2", group="flowgate.default.0001",
                            doc="flowgate.default.0001.0001-B")
    assert invoke._adopt_continuation_token(run, "next-raw") is False
    assert run["register_context"] == before
    assert run["current_token_id"] == "tok-1"


# ── §3b. The legacy review relaxation ─────────────────────────────────────────────────

def test_empty_doc_ref_relaxes_only_for_a_human_legacy_review_token(bound, monkeypatch):
    monkeypatch.setattr(
        inbox_routes.register_binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
    )
    human = _token("review", doc="", ai_run_id=None, provider_id=None)
    assert binding.token_kind(human) == "human_legacy"
    assert inbox_routes._check_context_binding(
        action_handler="review", project=PROJECT, doc=DOC, token_rec=human,
        target_doc_id=DOC,
    ) is None

    provider = _token("review", doc="", ai_run_id="aiv_test_000001")
    assert binding.token_kind(provider) == "api_run_bound"
    failure = inbox_routes._check_context_binding(
        action_handler="review", project=PROJECT, doc=DOC, token_rec=provider,
        target_doc_id=DOC,
    )
    assert failure is not None
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error"]["details"]["axis"] == "doc"


def test_a_legacy_review_token_with_no_group_column_still_passes(bound, monkeypatch):
    """The waiver would be dead letter otherwise.

    A human legacy review token has neither a group column nor a doc_ref, so its group could
    only ever have come from the doc_ref that is missing. Refusing it on `group` after
    waiving `doc` would reject exactly the tokens L0010 §2.3 exists to let through.
    """
    monkeypatch.setattr(
        inbox_routes.register_binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
    )
    token = _token("review", doc="", group=None, ai_run_id=None, provider_id=None)
    assert inbox_routes._check_context_binding(
        action_handler="review", project=PROJECT, doc=DOC, token_rec=token, target_doc_id=DOC,
    ) is None


def test_the_waiver_does_not_blind_a_group_the_token_actually_names(bound, monkeypatch):
    """Same empty doc_ref, but this token DOES carry a group — and it is the wrong one."""
    monkeypatch.setattr(
        inbox_routes.register_binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
    )
    token = _token("review", doc="", group="flowgate.default.0001",
                   ai_run_id=None, provider_id=None)
    failure = inbox_routes._check_context_binding(
        action_handler="review", project=PROJECT, doc=DOC, token_rec=token, target_doc_id=DOC,
    )
    assert failure is not None
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error"]["details"]["axes"] == ["group"]


def test_a_group_axis_that_cannot_be_resolved_fails_closed(bound, monkeypatch):
    """A legacy token with no group column and an unresolvable doc must not pass."""
    monkeypatch.setattr(inbox_routes.register_binding.db_docs, "get_by_id", lambda _id: None)
    failure = inbox_routes._check_context_binding(
        action_handler="new", project=PROJECT, doc=DOC, group=GROUP,
        token_rec=_token("new", group=None), prev_doc_id=DOC,
    )
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error"]["details"]["axis"] == "group"


def test_target_document_group_lookup_failure_fails_closed_even_with_token_group(bound, monkeypatch):
    """Target ownership is DB-derived; a matching token group cannot substitute for it."""
    monkeypatch.setattr(inbox_routes.register_binding.db_docs, "get_by_id", lambda _id: None)
    failure = inbox_routes._check_context_binding(
        action_handler="review", project=PROJECT, doc=DOC,
        token_rec=_token("review"), target_doc_id=DOC,
    )
    assert failure is not None
    assert failure.status_code == 403
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error"]["details"]["reason"] == "context_binding_mismatch"
    assert payload["error"]["details"]["axis"] == "group"
    assert payload["error"]["details"]["axes"] == ["group"]


def test_a_new_whose_predecessor_lives_in_another_group_is_a_group_fault(bound, monkeypatch):
    monkeypatch.setattr(
        inbox_routes.register_binding.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id,
                        "group_id": "flowgate.default.0001" if doc_id == DOC else GROUP},
    )
    failure = inbox_routes._check_context_binding(
        action_handler="new", project=PROJECT, doc=DOC, group=GROUP,
        token_rec=_token("new"), prev_doc_id=DOC,
    )
    payload = json.loads(bytes(failure.body).decode("utf-8"))
    assert payload["error"]["details"]["axes"] == ["group"]


# ── §4. CRUD contract ──────────────────────────────────────────────────────────────────

def _live_row(**over):
    row = {
        "recorded_at": "2026-09-01T00:00:00+09:00",
        "run_id": "aiv_test_000001",
        "correlation_id": "corr-1",
        "boundary": "inbox",
        "action_scope_run": "new",
        "action_scope_token": "edit",
        "action_scope_request": "new",
        "project_run": PROJECT,
        "project_token": PROJECT,
        "group_run": GROUP,
        "group_token_db": GROUP,
        "group_token_resolved": GROUP,
        "doc_ref_run": DOC,
        "doc_ref_token": DOC,
        "prev_doc_id_request": DOC,
        "target_doc_id_request": None,
        "ai_run_id": "aiv_test_000001",
        "axis_first_mismatch": "action",
        "axes_all_mismatches": ["action"],
        "token_id_hash": "hash",
        "expected_fingerprint": "fp1",
        "actual_fingerprint": "fp2",
        "binding_relaxed": False,
        "relaxed_axis": None,
        "status": 403,
        "code": "forbidden",
        "reason": "context_binding_mismatch",
        "turn": 1,
        "notes": None,
    }
    row.update(over)
    return row


@pytest.mark.parametrize("over,message", [
    ({"axes_all_mismatches": []}, "non-empty array"),
    ({"axes_all_mismatches": ["action", "colour"]}, "unknown axes"),
    ({"axes_all_mismatches": ["project"]}, "must equal axis_first_mismatch"),
    ({"axis_first_mismatch": "colour"}, "must be one of"),
    ({"boundary": "somewhere"}, "boundary must be one of"),
    ({"action_scope_token": None}, "required on a inbox row"),
    ({"binding_relaxed": True}, "requires relaxed_axis"),
    ({"relaxed_axis": "doc"}, "requires binding_relaxed"),
])
def test_crud_refuses_a_row_the_table_would_not_want(over, message):
    with pytest.raises(db_failures.RegisterFailureError) as caught:
        db_failures.bind_row(_live_row(**over))
    assert message in str(caught.value)


def test_crud_accepts_an_unclassified_legacy_row():
    db_failures.bind_row(_live_row(
        boundary="legacy_unclassified", axis_first_mismatch=None,
        axes_all_mismatches=None, action_scope_run=None, action_scope_token=None,
        project_token=None, group_token_resolved=None, doc_ref_token=None,
    ))


def test_rows_from_run_errors_ignores_a_failure_that_has_no_axis():
    """A 409 dup_body is a registration failure with nothing to classify — giving it an
    axis would be inventing the very fact this table exists to record."""
    rows = db_failures.rows_from_run_errors(
        "aiv_test_000001",
        [{"status": 409, "reason": "dup_body", "turn": 1},
         binding.failure_record(
             boundary="inbox", axes=["action"],
             run_context=binding.canonical_context("new", PROJECT, GROUP, DOC),
             token_context=binding.canonical_context("edit", PROJECT, GROUP, DOC),
             correlation_id="corr-x", run_id="aiv_test_000001", turn=2)],
        recorded_at="2026-09-01T00:00:00+09:00",
        fallback={"project_id": PROJECT, "group_id": GROUP, "doc_ref": DOC},
    )
    assert len(rows) == 1
    assert rows[0]["axis_first_mismatch"] == "action" and rows[0]["turn"] == 2


# ── §5 / §6. Migration 094, the CRUD against it, and the legacy backfill ───────────────

class _Txn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()


class _MockDB:
    """The sqloader surface the store and the backfill actually use."""

    db_type = None  # SQLite -> db.dialect.translate is a no-op

    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _Txn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    """A store over a database built by applying EVERY migration, 094 included.

    The conftest builder does not swallow a failing migration, so this fixture existing at
    all is the first assertion: 094 applies to the real schema, in name order, on SQLite.
    """
    from modules.flow_gate.db import connection as conn_mod

    path = migrated_sqlite_db("t0018.db")
    mock = _MockDB(path)
    now = "2026-09-01T00:00:00+09:00"
    mock.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at)"
        " VALUES (?,?,1,?,?)", [PROJECT, "FlowGate", now, now])
    mock.execute(
        "INSERT OR IGNORE INTO groups (group_id, project_id, module, title, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?)", [GROUP, PROJECT, "default", "T0018", now, now])

    class _Patched(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock
            self._sq = None

    original = conn_mod.STORE
    conn_mod.STORE = _Patched()
    yield mock
    conn_mod.STORE = original
    mock.close()


def _seed_run(store_db, run_id="aiv_test_000001", register_errors=None):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    db_runs.upsert({
        "run_id": run_id, "group_id": GROUP, "project_id": PROJECT, "doc_ref": DOC,
        "mode": "single", "register_errors": register_errors,
        "started_at": "2026-09-01T00:00:00+09:00",
        "finished_at": "2026-09-01T00:01:00+09:00",
        "created_at": "2026-09-01T00:00:00+09:00",
        "updated_at": "2026-09-01T00:01:00+09:00",
    })


def test_migration_094_exists_in_all_three_dialects_with_the_same_shape():
    files = {d: (MIGRATIONS / d / "094_register_context_failures.sql") for d in
             ("sqlite", "postgres", "mysql")}
    assert all(path.is_file() for path in files.values())
    for dialect, path in files.items():
        sql = path.read_text(encoding="utf-8")
        for column in db_failures.COLUMNS:
            assert re.search(rf"\b{column}\b", sql), f"{dialect} is missing {column}"
        for constraint in ("uq_rcf_correlation_boundary", "ck_rcf_axis_pair",
                           "ck_rcf_live_rows_are_classified",
                           "ck_rcf_relaxed_requires_axis",
                           "ck_rcf_relaxed_axis_requires_flag"):
            assert constraint in sql, f"{dialect} is missing {constraint}"
        for index in ("idx_rcf_run_time", "idx_rcf_boundary_axis", "idx_rcf_recorded"):
            assert index in sql, f"{dialect} is missing {index}"


def test_the_postgres_file_owns_no_transaction():
    """T0018 measurement step 5: a file that BEGINs and COMMITs itself cannot be applied
    and rolled back inside a caller's transaction, which is how it is proven on the live DB."""
    sql = (MIGRATIONS / "postgres" / "094_register_context_failures.sql").read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert not re.search(r"\bBEGIN\b|\bCOMMIT\b", body, re.IGNORECASE)


def test_the_migration_does_not_drop_the_legacy_column():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (MIGRATIONS / dialect / "094_register_context_failures.sql").read_text(encoding="utf-8")
        body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
        assert "DROP COLUMN" not in body.upper()


def test_a_round_trip_through_the_real_table(store):
    _seed_run(store)
    assert db_failures.insert_many([_live_row()]) == 1
    rows = db_failures.list_by_run("aiv_test_000001")
    assert len(rows) == 1
    row = rows[0]
    assert row["boundary"] == "inbox"
    assert row["axis_first_mismatch"] == "action"
    assert row["axes_all_mismatches"] == ["action"]
    assert row["status"] == 403 and row["code"] == "forbidden"
    assert row["reason"] == "context_binding_mismatch" and row["turn"] == 1


def test_the_same_correlation_and_boundary_cannot_be_stored_twice(store):
    _seed_run(store)
    db_failures.insert_many([_live_row()])
    db_failures.insert_many([_live_row(status=500)])
    assert db_failures.count_for_run("aiv_test_000001") == 1
    # The same correlation at the OTHER boundary is a different observation and is kept.
    db_failures.insert_many([_live_row(boundary="register_dispatch")])
    assert db_failures.count_for_run("aiv_test_000001") == 2


def test_no_dialect_declares_the_flag_as_a_boolean():
    """Every hand-written CRUD here binds a flag as 0/1 in ONE statement for all three
    dialects, and PostgreSQL refuses to coerce an integer into a boolean column. Measured:
    a BOOLEAN spelling of `binding_relaxed` made the first live-DB insert fail with
    DatatypeMismatch. ai_invoke_runs.resumable / turn_limit_exhausted / oracle_mismatch are
    INTEGER on the live PostgreSQL for the same reason."""
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (MIGRATIONS / dialect / "094_register_context_failures.sql").read_text(encoding="utf-8")
        body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
        assert "BOOLEAN" not in body.upper()
        assert "binding_relaxed IN (0, 1)" in body


@pytest.mark.parametrize("bad", [
    {"boundary": "elsewhere"},
    {"axis_first_mismatch": "colour"},
    {"axes_all_mismatches": None},
    {"axis_first_mismatch": None, "axes_all_mismatches": None},
    # The NULL trap: `binding_relaxed = 0 OR relaxed_axis = 'doc'` is NULL for (1, NULL),
    # and SQL treats a NULL CHECK as satisfied, so the row it exists to stop went in.
    {"binding_relaxed": True},
    {"binding_relaxed": True, "relaxed_axis": "project"},
    {"relaxed_axis": "doc"},
])
def test_the_table_itself_refuses_a_bad_row(store, bad):
    """Not just the CRUD guard: the CHECK constraints are in the schema."""
    _seed_run(store)
    values = db_failures._bind(_live_row(**bad))
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            f"INSERT INTO register_context_failures ({', '.join(db_failures.COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(db_failures.COLUMNS))})", values)


def test_deleting_the_run_cascades_to_its_failures(store):
    _seed_run(store)
    db_failures.insert_many([_live_row()])
    store.execute("DELETE FROM ai_invoke_runs WHERE run_id = ?", ["aiv_test_000001"])
    assert db_failures.count_for_run("aiv_test_000001") == 0


def test_finalize_writes_the_rows_once_and_a_retry_adds_none(store, monkeypatch):
    _seed_run(store)
    record = binding.failure_record(
        boundary="register_dispatch", axes=["action"],
        run_context=binding.canonical_context("new", PROJECT, GROUP, DOC),
        token_context=binding.canonical_context("edit", PROJECT, GROUP, DOC),
        correlation_id="corr-finalize", run_id="aiv_test_000001", turn=1)
    run = _run("new", register_errors=[record, {"status": 409, "reason": "dup", "turn": 2}])

    invoke._persist_register_context_failures(run, "2026-09-01T00:02:00+09:00")
    invoke._persist_register_context_failures(run, "2026-09-01T00:03:00+09:00")

    rows = db_failures.list_by_run("aiv_test_000001")
    assert len(rows) == 1
    stored = rows[0]
    # The eight keys memory and storage must agree on, one by one.
    assert stored["status"] == record["status"]
    assert stored["code"] == record["code"]
    assert stored["reason"] == record["reason"]
    assert stored["turn"] == record["turn"]
    assert stored["boundary"] == record["boundary"]
    assert stored["axis_first_mismatch"] == record["axis"]
    assert stored["axes_all_mismatches"] == record["axes"]
    assert stored["correlation_id"] == record["correlation_id"]


def test_a_clean_run_leaves_no_failure_row(store):
    _seed_run(store)
    invoke._persist_register_context_failures(_run("new"), "2026-09-01T00:02:00+09:00")
    assert db_failures.count_for_run("aiv_test_000001") == 0


# ── §6. The legacy backfill ────────────────────────────────────────────────────────────

B0001_ERRORS = [
    {"status": 403, "reason": '{"ok": false, "http_status": 403, "error_message": '
                              '"Context binding mismatch. Use the correct token."}', "turn": n}
    for n in (1, 2, 3)
]


def test_backfill_on_an_empty_database_is_a_no_op(store):
    assert backfill.run_register_context_failure_backfill(store) == 0


def test_backfill_skips_a_run_with_no_register_errors(store):
    _seed_run(store, register_errors=[])
    assert backfill.run_register_context_failure_backfill(store) == 0


@pytest.mark.parametrize("errors,expected", [
    (B0001_ERRORS[:1], 1),
    (B0001_ERRORS, 3),
])
def test_backfill_expands_each_legacy_element(store, errors, expected):
    _seed_run(store, register_errors=errors)
    assert backfill.run_register_context_failure_backfill(store) == expected
    rows = db_failures.list_by_run("aiv_test_000001")
    assert len(rows) == expected
    for row, source in zip(rows, errors):
        # Nothing invented, everything preserved.
        assert row["boundary"] == "legacy_unclassified"
        assert row["axis_first_mismatch"] is None
        assert row["axes_all_mismatches"] == []
        assert row["status"] == source["status"]
        assert row["reason"] == source["reason"]
        assert row["turn"] == source["turn"]
        assert row["project_run"] == PROJECT and row["group_run"] == GROUP
        assert row["doc_ref_run"] == DOC


def test_backfill_leaves_the_source_column_untouched(store):
    _seed_run(store, register_errors=B0001_ERRORS)
    backfill.run_register_context_failure_backfill(store)
    row = store.fetch_one("SELECT register_errors FROM ai_invoke_runs WHERE run_id = ?",
                          ["aiv_test_000001"])
    assert json.loads(row["register_errors"]) == B0001_ERRORS


def test_backfill_is_idempotent(store):
    _seed_run(store, register_errors=B0001_ERRORS)
    backfill.run_register_context_failure_backfill(store)
    backfill.run_register_context_failure_backfill(store)
    backfill.run_register_context_failure_backfill(store)
    assert db_failures.count_for_run("aiv_test_000001") == 3


def test_backfill_survives_a_broken_element(store):
    _seed_run(store, register_errors=[
        {"status": 403, "reason": "ok", "turn": 1},
        "not a dict",
        {"reason": "truncated", "dropped": 4},
        {"status": "not-a-number", "reason": "still recorded", "turn": "x"},
    ])
    assert backfill.run_register_context_failure_backfill(store) == 2
    rows = db_failures.list_by_run("aiv_test_000001")
    assert [row["reason"] for row in rows] == ["ok", "still recorded"]
    assert rows[1]["status"] is None and rows[1]["turn"] is None


def test_backfill_skips_a_run_whose_json_is_unparsable(store):
    _seed_run(store)
    store.execute("UPDATE ai_invoke_runs SET register_errors = ? WHERE run_id = ?",
                  ["{not json", "aiv_test_000001"])
    assert backfill.run_register_context_failure_backfill(store) == 0


def test_backfill_keeps_an_element_that_already_carried_its_axis(store):
    _seed_run(store, register_errors=[{
        "status": 403, "code": "forbidden", "reason": "context_binding_mismatch", "turn": 1,
        "boundary": "inbox", "axis": "action", "axes": ["action"],
        "correlation_id": "corr-live",
        "action_scope_run": "new", "action_scope_token": "edit",
        "project_token": PROJECT, "group_token_resolved": GROUP, "doc_ref_token": DOC,
    }])
    assert backfill.run_register_context_failure_backfill(store) == 1
    row = db_failures.list_by_run("aiv_test_000001")[0]
    assert row["boundary"] == "inbox"
    assert row["axis_first_mismatch"] == "action"
    assert row["correlation_id"] == "corr-live"
