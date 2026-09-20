"""Regression tests for flowgate.default.0582 T0005 — common AI-run provider provenance.

Covers the T's own completion checklist (T0005 §8):
  8.1 requested-provider != effective-provider -> only the effective one is ever shown
      (BAD_REQUESTED_PROVIDER / GOOD_EFFECTIVE_PROVIDER sentinels).
  8.2 a snapshot already written is immune to a later provider rename (proven at the
      Q&A layer here; document_reviews' own instance of this is covered by
      test_document_reviews.py::test_provider_provenance_is_snapshotted_and_exposed).
  8.3 legacy rows with every new column NULL do not break API shaping.
  8.5 an automatic rejection's provider comes from the review that produced the
      `issues` verdict, never the actor who executed the auto-reject transition.
  8.6 a rework response's provider is independent of (and may differ from) the
      review's provider.
  8.7 an AI question/answer's run/provider snapshot persists and reads back; a human
      item is unaffected by the new columns existing.
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

_QUERIES: dict = {}
_raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
for _section, _entries in _raw.items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


BAD_REQUESTED_PROVIDER = "aip_bad_requested"
GOOD_EFFECTIVE_PROVIDER = "aip_good_effective"


# ── Part A: ai_invoke.provenance -- pure logic, no DB ────────────────────────────

def _fake_run(**overrides):
    base = {
        "action_scope": "edit",
        "doc_ref": "flowgate.default.0582.0001-T",
        "requested_provider_id": BAD_REQUESTED_PROVIDER,
        "provider_id": GOOD_EFFECTIVE_PROVIDER,
        "provider": {"name": "Claude Sonnet 5"},
        "selected_provider_source": "request",
        "attempt_no": 1,
    }
    base.update(overrides)
    return base


def test_fallback_snapshot_names_only_the_effective_provider():
    """8.1: fallback landed -> the snapshot and its public payload show GOOD only."""
    from modules.flow_gate.services.ai_invoke import provenance as prov

    run = _fake_run()
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_1", allowed_action_scopes=("edit",))

    assert snap["actual_provider_id"] == GOOD_EFFECTIVE_PROVIDER
    assert snap["requested_provider_id"] == BAD_REQUESTED_PROVIDER
    assert snap["fallback_used"] is True
    assert snap["provider_source"] == "fallback"

    payload = prov.to_api_payload(snap)
    assert payload == {
        "ai_run_id": "aiv_1",
        "ai_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "ai_provider_name": "Claude Sonnet 5",
    }
    assert BAD_REQUESTED_PROVIDER not in _json.dumps(payload)


def test_no_fallback_still_names_the_single_provider():
    from modules.flow_gate.services.ai_invoke import provenance as prov
    run = _fake_run(requested_provider_id=GOOD_EFFECTIVE_PROVIDER)
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_2", allowed_action_scopes=("edit",))
    assert snap["fallback_used"] is False
    assert snap["provider_source"] == "request"


def test_action_scope_mismatch_returns_no_evidence():
    from modules.flow_gate.services.ai_invoke import provenance as prov
    run = _fake_run(action_scope="review")
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_1", allowed_action_scopes=("edit",))
    assert snap == {}


def test_doc_id_mismatch_returns_no_evidence():
    from modules.flow_gate.services.ai_invoke import provenance as prov
    run = _fake_run(doc_ref="some.other.doc")
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_1", doc_id="flowgate.default.0582.0001-T")
    assert snap == {}


def test_missing_run_id_or_run_record_returns_no_evidence():
    """8.3: no run id / no run found degrades to {} — a legacy or non-AI token."""
    from modules.flow_gate.services.ai_invoke import provenance as prov
    assert prov.resolve_run_provenance(None) == {}
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=None):
        assert prov.resolve_run_provenance("aiv_missing") == {}


def test_missing_provider_id_returns_no_evidence_not_false():
    """Half evidence must not collapse into fallback_used=False (see module docstring)."""
    from modules.flow_gate.services.ai_invoke import provenance as prov
    run = _fake_run(provider_id=None)
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        assert prov.resolve_run_provenance("aiv_1") == {}


def test_prefetched_run_is_not_looked_up_again():
    """The run= shortcut is what keeps a caller's own get_run_record call count at one
    (test_review_atomicity_0535 asserts inbox_routes._review_provenance calls it exactly
    once per submission)."""
    from modules.flow_gate.services.ai_invoke import provenance as prov
    run = _fake_run()
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record") as get_run:
        snap = prov.resolve_run_provenance("aiv_1", run=run)
    get_run.assert_not_called()
    assert snap["actual_provider_id"] == GOOD_EFFECTIVE_PROVIDER


def test_to_api_payload_is_none_for_empty_or_missing_snapshot():
    from modules.flow_gate.services.ai_invoke import provenance as prov
    assert prov.to_api_payload({}) is None
    assert prov.to_api_payload(None) is None


# ── Part A2: document_review_loop stage-transition provenance (0582 TR0006 rev1) ─
# A document_review_loop hop rewrites run["hop_kind"] in place as it alternates
# stages, while run["action_scope"] stays pinned to whatever scope the RUN was first
# admitted under, for its whole lifetime (worker.py's loop transition never touches
# it). These regressions pin effective_action_scope/resolve_run_provenance to the
# live hop_kind instead, in BOTH loop directions.

def test_effective_action_scope_follows_hop_kind_over_a_stale_action_scope():
    from modules.flow_gate.services.ai_invoke import provenance as prov

    # A loop that started admitted as "review" (starts_with_rework=False) but whose
    # SAME run has since moved on to its rework hop -- action_scope never moves.
    review_started_now_reworking = _fake_run(action_scope="review", hop_kind="rework")
    assert prov.effective_action_scope(review_started_now_reworking) == "edit"

    # The mirror: a loop that started admitted as "edit" (starts_with_rework=True)
    # whose SAME run has since moved on to its review hop.
    rework_started_now_reviewing = _fake_run(action_scope="edit", hop_kind="review")
    assert prov.effective_action_scope(rework_started_now_reviewing) == "review"

    # A plain, non-loop run never sets hop_kind away from the "work" default and
    # falls back to action_scope unchanged (manual [AI 검수] never sets hop_kind).
    plain_review = _fake_run(action_scope="review", hop_kind="work")
    assert prov.effective_action_scope(plain_review) == "review"
    plain_review_unset = _fake_run(action_scope="review", hop_kind=None)
    assert prov.effective_action_scope(plain_review_unset) == "review"


def test_review_provenance_survives_a_review_started_loops_rework_stage():
    """A loop admitted with action_scope="review" (starts_with_rework=False) whose
    SAME run is now on its rework hop must still resolve for allowed_action_scopes=
    ("edit",) -- this is the rework-response snapshot inbox_routes._handle_edit takes
    (2611-2678 / 4746-4752), which used to come back {} for this exact case."""
    from modules.flow_gate.services.ai_invoke import provenance as prov

    run = _fake_run(action_scope="review", hop_kind="rework")
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_loop_1", allowed_action_scopes=("edit",))
    assert snap != {}
    assert snap["actual_provider_id"] == GOOD_EFFECTIVE_PROVIDER


def test_review_provenance_survives_a_rework_started_loops_review_stage():
    """The mirror: a loop admitted with action_scope="edit" (starts_with_rework=True)
    whose SAME run is now on its review hop must still resolve for
    allowed_action_scopes=("review",) -- this is _review_provenance's own snapshot,
    which used to come back {} for this exact case, losing document_reviews'
    provider and the review_id-joined automatic rejection's provider with it."""
    from modules.flow_gate.services.ai_invoke import provenance as prov

    run = _fake_run(action_scope="edit", hop_kind="review")
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        snap = prov.resolve_run_provenance("aiv_loop_2", allowed_action_scopes=("review",))
    assert snap != {}
    assert snap["actual_provider_id"] == GOOD_EFFECTIVE_PROVIDER


def test_inbox_review_provenance_helper_uses_effective_action_scope():
    """inbox_routes._review_provenance's own early filter (not just
    resolve_run_provenance's internal one) must use the same hop_kind-aware scope --
    it is what gates review_intent/superseded_review_id too, ahead of the
    resolve_run_provenance call."""
    from modules.flow_gate.api import inbox_routes

    run = _fake_run(
        action_scope="edit", hop_kind="review", doc_ref="flowgate.default.0582.0006-TR",
    )
    with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=run):
        provenance = inbox_routes._review_provenance(
            {"ai_run_id": "aiv_loop_3"}, "flowgate.default.0582.0006-TR",
        )
    assert provenance.get("actual_provider_id") == GOOD_EFFECTIVE_PROVIDER


# ── Part B: pipeline_service rejection provenance shaping ───────────────────────

def test_rejection_provenance_view_resolves_review_id_to_the_reviews_own_provider():
    """8.5: an AUTOMATIC rejection's provider is the review's, never rejected_by (the
    chain-issuer actor the server executed the transition as)."""
    from modules.flow_gate.workflow import pipeline_service as ps
    item = {"review_id": 42, "rejected_by": "u-chain-issuer", "reason": "x"}
    fake_row = {
        "review_run_id": "aiv_r1", "actual_provider_id": "aip_x",
        "actual_provider_name": "Claude Opus 5",
    }
    with patch("modules.flow_gate.db.document_reviews.get_by_id", return_value=fake_row) as get_by_id:
        view = ps.rejection_provenance_view(item)
    get_by_id.assert_called_once_with(42)
    assert view["rejection_provider"] == {
        "ai_run_id": "aiv_r1", "ai_provider_id": "aip_x", "ai_provider_name": "Claude Opus 5",
    }
    assert view["response_provider"] is None
    # The original item is untouched; the view is a new dict.
    assert "rejection_provider" not in item


def test_rejection_provenance_view_human_rejection_has_no_provider():
    from modules.flow_gate.workflow import pipeline_service as ps
    item = {"rejected_by": "u-human", "reason": "x"}
    view = ps.rejection_provenance_view(item)
    assert view["rejection_provider"] is None
    assert view["response_provider"] is None


def test_rejection_provenance_view_response_provider_independent_of_review():
    """8.6: review=A, rework response=B -> the response's own provider must show B."""
    from modules.flow_gate.workflow import pipeline_service as ps
    item = {
        "review_id": 7,
        "reason": "x",
        "response_ai_run_id": "aiv_response",
        "response_actual_provider_id": "aip_reworker_B",
        "response_actual_provider_name": "Codex1 Sol",
    }
    fake_review_row = {
        "review_run_id": "aiv_review", "actual_provider_id": "aip_reviewer_A",
        "actual_provider_name": "Claude Opus 5",
    }
    with patch("modules.flow_gate.db.document_reviews.get_by_id", return_value=fake_review_row):
        view = ps.rejection_provenance_view(item)
    assert view["rejection_provider"]["ai_provider_id"] == "aip_reviewer_A"
    assert view["response_provider"]["ai_provider_id"] == "aip_reworker_B"
    assert view["rejection_provider"] != view["response_provider"]


def test_rejection_provenance_view_legacy_review_row_missing_is_safe():
    """8.3: a review_id naming a deleted/unknown row degrades to null, never raises."""
    from modules.flow_gate.workflow import pipeline_service as ps
    item = {"review_id": 9999, "reason": "x"}
    with patch("modules.flow_gate.db.document_reviews.get_by_id", return_value=None):
        view = ps.rejection_provenance_view(item)
    assert view["rejection_provider"] is None


def test_rejection_provenance_view_legacy_item_with_no_new_columns_at_all():
    """8.3: a pre-0582 rejection_history item has none of the new keys — must not raise."""
    from modules.flow_gate.workflow import pipeline_service as ps
    item = {"reason": "x", "rejected_at": "2026-01-01T00:00:00Z", "rejected_by": "u1"}
    view = ps.rejection_provenance_view(item)
    assert view["rejection_provider"] is None
    assert view["response_provider"] is None


def test_enrich_rejection_history_provenance_maps_every_item_and_skips_non_dicts():
    from modules.flow_gate.workflow import pipeline_service as ps
    history = [{"reason": "a"}, "not-a-dict", {"reason": "b", "review_id": 5}]
    with patch("modules.flow_gate.db.document_reviews.get_by_id", return_value=None):
        out = ps.enrich_rejection_history_provenance(history)
    assert len(out) == 2
    assert all("rejection_provider" in item and "response_provider" in item for item in out)


# ── Part C: Q&A provenance, end to end on a real SQLite DB ───────────────────────
# Mirrors test_qa_container.py's harness exactly (same migrations + sqloader queries).

class _MockDB:
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
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]


@pytest.fixture()
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mock_db = _MockDB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()

    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()

    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    projects.create({"project_id": "p582", "project_name": "P582"})
    users.create({"user_id": "u1", "username": "u1", "email": "u1@e", "password": "x"})
    groups.create({"group_id": "p582.none.0001", "project_id": "p582", "module": "none", "title": "G"})
    md = Path(path).with_suffix(".doc.md")
    md.write_text("# d", encoding="utf-8")
    store_obj = conn_mod.STORE
    now = "2026-06-13T00:00:00Z"
    store_obj._execute(
        "INSERT OR IGNORE INTO document_types (project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": "p582.none.0001.0001-D", "project_id": "p582", "type_code": "D", "seq": 1,
        "title": "Doc One", "group_id": "p582.none.0001", "module": "none",
        "owner_id": "u1", "file_path": str(md), "status": "open",
    })
    yield mock_db
    conn_mod.STORE = original
    mock_db.close()
    os.unlink(path)


DOC = "p582.none.0001.0001-D"


def test_ai_question_persists_and_exposes_asker_provider(store):
    """8.7: an AI-registered question's run/provider survives the round trip."""
    from modules.flow_gate.services import q_service

    res = q_service.add_questions(
        DOC, [{"title": "범위", "body": "scope?"}], asker_kind="ai",
        asker_provenance={
            "ai_run_id": "aiv_q1", "actual_provider_id": "aip_asker",
            "actual_provider_name": "Claude Sonnet 5",
        },
    )
    item_id = res["added_item_ids"][0]

    detail = q_service.get_qa_detail(DOC)
    item = next(it for it in detail["items"] if it["id"] == item_id)
    assert item["asker_provider"] == {
        "ai_run_id": "aiv_q1", "ai_provider_id": "aip_asker", "ai_provider_name": "Claude Sonnet 5",
    }
    # The raw columns are nested, not left dangling on the API shape.
    assert "asker_ai_run_id" not in item


def test_ai_answer_persists_and_exposes_author_provider_independent_of_asker(store):
    """8.6/8.7 analogue for Q&A: the answering AI's provider is independent of the
    question's asker provider, and both survive the round trip."""
    from modules.flow_gate.services import q_service

    res = q_service.add_questions(
        DOC, [{"title": "범위", "body": "scope?"}], asker_kind="ai",
        asker_provenance={
            "ai_run_id": "aiv_q1", "actual_provider_id": "aip_asker",
            "actual_provider_name": "Claude Sonnet 5",
        },
    )
    item_id = res["added_item_ids"][0]

    q_service.register_answer(
        DOC, item_id, "AI answer", author_kind="ai", author_id="ignored",
        author_provenance={
            "ai_run_id": "aiv_a1", "actual_provider_id": "aip_answerer",
            "actual_provider_name": "Codex1 GPT-5.6 Sol",
        },
    )

    detail = q_service.get_qa_detail(DOC)
    item = next(it for it in detail["items"] if it["id"] == item_id)
    answer = item["answers"][0]
    assert answer["author_provider"] == {
        "ai_run_id": "aiv_a1", "ai_provider_id": "aip_answerer", "ai_provider_name": "Codex1 GPT-5.6 Sol",
    }
    assert answer["author_provider"] != item["asker_provider"]


def test_human_question_and_answer_are_unaffected_by_the_new_columns(store):
    """A human [+query]/answer never resolves an AI provenance, and the new columns
    existing must not change any pre-existing human-path behaviour."""
    from modules.flow_gate.services import q_service

    res = q_service.add_questions(DOC, [{"title": "t", "body": "b?"}], asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    q_service.register_answer(DOC, item_id, "human answer", author_kind="human", author_id="u1")

    detail = q_service.get_qa_detail(DOC)
    item = next(it for it in detail["items"] if it["id"] == item_id)
    assert item["asker_provider"] is None
    assert item["answers"][0]["author_provider"] is None


def test_ai_item_with_no_resolvable_run_is_null_not_fabricated(store):
    """8.3/§2.3: an AI question/answer whose token carried no bound run (a legacy row,
    or the [Copy Mention] hand-off which starts none by design) must show no provider,
    never a guessed one -- add_questions/register_answer are called exactly as the AI
    call sites do when resolve_run_provenance found nothing (asker_provenance={})."""
    from modules.flow_gate.services import q_service

    res = q_service.add_questions(DOC, [{"title": "t2", "body": "b2?"}], asker_kind="ai", asker_provenance={})
    item_id = res["added_item_ids"][0]
    q_service.register_answer(DOC, item_id, "ai answer", author_kind="ai", author_provenance={})

    detail = q_service.get_qa_detail(DOC)
    item = next(it for it in detail["items"] if it["id"] == item_id)
    assert item["asker_provider"] is None
    assert item["answers"][0]["author_provider"] is None
    # Still correctly AI-attributed on the existing asker_kind/author_kind fields.
    assert item["asker_kind"] == "ai"
    assert item["answers"][0]["author_kind"] == "ai"


def test_provider_rename_does_not_alter_an_already_stored_snapshot(store):
    """8.2: two AI questions registered under what were, at the time, two different
    provider names -- each keeps its OWN snapshot regardless of what the "current"
    provider registry says later (there is no live join back to it)."""
    from modules.flow_gate.services import q_service

    res1 = q_service.add_questions(
        DOC, [{"title": "before rename", "body": "b1?"}], asker_kind="ai",
        asker_provenance={
            "ai_run_id": "aiv_before", "actual_provider_id": "aip_stable_id",
            "actual_provider_name": "Claude Sonnet 5",
        },
    )
    res2 = q_service.add_questions(
        DOC, [{"title": "after rename", "body": "b2?"}], asker_kind="ai",
        asker_provenance={
            "ai_run_id": "aiv_after", "actual_provider_id": "aip_stable_id",
            "actual_provider_name": "Sonnet Renamed",
        },
    )

    detail = q_service.get_qa_detail(DOC)
    item1 = next(it for it in detail["items"] if it["id"] == res1["added_item_ids"][0])
    item2 = next(it for it in detail["items"] if it["id"] == res2["added_item_ids"][0])
    assert item1["asker_provider"]["ai_provider_name"] == "Claude Sonnet 5"
    assert item2["asker_provider"]["ai_provider_name"] == "Sonnet Renamed"



# ── Part C: T0007 revision / failure-origin persistence and API shaping ─────────

def test_revision_provider_snapshot_round_trips_without_fabricating_legacy_rows(store):
    from modules.flow_gate.db import document_revisions as revisions
    from modules.flow_gate.services.ai_invoke.provenance import to_api_payload

    saved = revisions.create({
        "doc_id": DOC, "revision_no": 1, "backup_path": "revisions/r1.md",
        "edit_reason": "worker_self", "created_by": "u1",
        "ai_run_id": "aiv_edit", "actual_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "actual_provider_name": "GOOD_EFFECTIVE_PROVIDER",
    })
    assert to_api_payload(saved) == {
        "ai_run_id": "aiv_edit", "ai_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "ai_provider_name": "GOOD_EFFECTIVE_PROVIDER",
    }
    legacy = revisions.create({
        "doc_id": DOC, "revision_no": 0, "backup_path": "revisions/r0.md",
        "edit_reason": "user_comment", "created_by": "u1",
    })
    assert to_api_payload(legacy) is None
    assert legacy["created_by"] == "u1"


def test_failure_origin_snapshot_is_atomic_and_shape_run_exposes_it(store):
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_run_service

    run = test_runs.insert_run(
        doc_id=DOC, revision_no=1, triggered_via="ui", runner_id="u1",
        cases=[{"case_no": "TC-1", "cmd": "false", "title": "fails"}],
    )
    store._conn.execute(
        "UPDATE test_runs SET status = 'failed' WHERE run_id = ?", [run["run_id"]]
    )
    store._conn.commit()
    test_runs.store_failure_origin(
        run_id=run["run_id"], reviewer_id="u1", classification="product_defect",
        findings_json="[]", comment=None, reviewed_at="2026-09-20T00:00:00Z",
        ai_run_id="aiv_classifier", actual_provider_id=GOOD_EFFECTIVE_PROVIDER,
        actual_provider_name="GOOD_EFFECTIVE_PROVIDER",
    )
    saved = test_runs.get_run(run["run_id"])
    assert saved["failure_origin_reviewer_id"] == "u1"
    assert test_run_service.shape_run(saved)["failure_origin_provider"] == {
        "ai_run_id": "aiv_classifier", "ai_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "ai_provider_name": "GOOD_EFFECTIVE_PROVIDER",
    }


def test_legacy_failure_origin_shape_has_null_provider(store):
    from modules.flow_gate.services import test_run_service

    shaped = test_run_service.shape_run({
        "run_id": "legacy", "doc_id": DOC, "failure_origin": "test_defect",
    })
    assert shaped["failure_origin_provider"] is None


def _route_client(*routers):
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return TestClient(app)


def test_inbox_rejected_edit_reuses_one_snapshot_and_relations_exposes_it(store, tmp_path):
    """T0007 §7.2: exercise the real POST inbox edit route and GET relations route.

    The resolver must run once; the rejection response and revision row must receive
    that same snapshot, and the public relations serializer must return it unchanged.
    """
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import git_service, token_service
    from modules.flow_gate.services.ai_invoke import runtime

    doc = db_docs.get_by_id(DOC)
    history = [{
        "rejection_id": "rej_route_1", "review_id": 91, "reason": "revise",
        "rejected_at": "2026-09-20T00:00:00Z", "rejected_by": "u1",
    }]
    store._conn.execute(
        "UPDATE documents SET status = 'rejected', rejection_history = ? WHERE doc_id = ?",
        [_json.dumps(history), DOC],
    )
    store._conn.commit()
    token_rec = {
        "token_id": "tok_edit_route", "project": "p582", "action_scope": "edit",
        "doc_ref": DOC, "group_id": "p582.none.0001", "issued_to": "u1",
        "scratch_dir": str(tmp_path / "scratch"), "ai_run_id": "aiv_edit_route",
    }
    run = _fake_run(doc_ref=DOC, action_scope="edit")
    run["provider"]["name"] = "GOOD_EFFECTIVE_PROVIDER"

    with patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None), \
            patch.object(inbox_routes, "has_permission", return_value=True), \
            patch.object(inbox_routes.document_service, "is_final_approved", return_value=False), \
            patch.object(inbox_routes.document_service, "is_document_editable", return_value=True), \
            patch.object(inbox_routes, "_design_template_submission_error", return_value=None), \
            patch.object(git_service, "worktree_untracked_summary", return_value={}), \
            patch.object(inbox_routes.step_verification_service, "evaluate",
                         return_value={"verdict": "pass", "codes": []}), \
            patch.object(runtime, "get_run_record", return_value=run) as get_run:
        response = _route_client(inbox_routes.router).post(
            "/api/v1/inbox",
            json={
                "project": "p582", "module": "none", "group_name": "p582.none.0001",
                "action": "edit", "doc_id": DOC, "edit_reason": "rejected",
                "content": "# revised by route\n", "rejection_response": "addressed",
                "rejection_id": "rej_route_1", "review_id": 91,
            },
            headers={"Authorization": "Bearer raw"},
        )

    assert response.status_code == 200, response.text
    get_run.assert_called_once_with("aiv_edit_route")
    saved_doc = db_docs.get_by_id(DOC)
    saved_history = _json.loads(saved_doc["rejection_history"])
    response_provider = {
        "ai_run_id": saved_history[0]["response_ai_run_id"],
        "ai_provider_id": saved_history[0]["response_actual_provider_id"],
        "ai_provider_name": saved_history[0]["response_actual_provider_name"],
    }

    with patch.object(document_routes, "verify_bearer", return_value={"user_id": "u1"}):
        relations = _route_client(document_routes.router).get(
            f"/api/v1/document/{DOC}/relations",
            headers={"Authorization": "Bearer reader"},
        )
    assert relations.status_code == 200, relations.text
    editor_provider = relations.json()["revisions"][0]["editor_provider"]
    expected = {
        "ai_run_id": "aiv_edit_route", "ai_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "ai_provider_name": "GOOD_EFFECTIVE_PROVIDER",
    }
    assert editor_provider == expected
    assert response_provider == expected
    assert BAD_REQUESTED_PROVIDER not in _json.dumps(relations.json())


def test_inbox_failure_origin_route_persists_and_document_api_exposes_provider(store, tmp_path):
    """T0007 §7.3: real failure_origin_review inbox POST, DB write, and detail API."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_run_service, token_service
    from modules.flow_gate.services.ai_invoke import runtime

    test_run = test_runs.insert_run(
        doc_id=DOC, revision_no=2, triggered_via="ui", runner_id="u1",
        cases=[{"case_no": "TC-route", "cmd": "false", "title": "fails"}],
    )
    store._conn.execute(
        "UPDATE test_runs SET status = 'failed' WHERE run_id = ?", [test_run["run_id"]]
    )
    store._conn.commit()
    token_rec = {
        "token_id": "tok_failure_route", "project": "p582",
        "action_scope": "failure_origin_review", "doc_ref": DOC,
        "group_id": "p582.none.0001", "issued_to": "u1",
        "scratch_dir": str(tmp_path / "scratch"), "ai_run_id": "aiv_classifier_route",
        "failure_origin_target_run_id": test_run["run_id"],
        "failure_origin_before_marker": None,
    }
    run = _fake_run(doc_ref=DOC, action_scope="failure_origin_review")
    run["provider"]["name"] = "GOOD_EFFECTIVE_PROVIDER"

    with patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None), \
            patch.object(inbox_routes, "has_permission", return_value=True), \
            patch.object(test_run_service, "resume_failure_origin_branch",
                         return_value={"continued": False}), \
            patch.object(runtime, "get_run_record", return_value=run):
        response = _route_client(inbox_routes.router).post(
            "/api/v1/inbox",
            json={
                "project": "p582", "action": "failure_origin_review", "doc_id": DOC,
                "run_id": test_run["run_id"], "classification": "product_defect",
                "findings": [{"code": "route"}], "comment": "classified",
            },
            headers={"Authorization": "Bearer raw"},
        )

    expected = {
        "ai_run_id": "aiv_classifier_route", "ai_provider_id": GOOD_EFFECTIVE_PROVIDER,
        "ai_provider_name": "GOOD_EFFECTIVE_PROVIDER",
    }
    assert response.status_code == 200, response.text
    assert response.json()["failure_origin_provider"] == expected
    saved = test_runs.get_run(test_run["run_id"])
    assert saved["failure_origin_reviewer_id"] == "u1"
    assert saved["failure_origin_actual_provider_id"] == GOOD_EFFECTIVE_PROVIDER

    with patch.object(document_routes, "verify_bearer", return_value={"user_id": "u1"}):
        detail = _route_client(document_routes.router).get(
            f"/api/v1/document/{DOC}",
            headers={"Authorization": "Bearer reader"},
        )
    assert detail.status_code == 200, detail.text
    shaped = next(
        item for item in detail.json()["test_run_history"]
        if item["run_id"] == test_run["run_id"]
    )
    assert shaped["failure_origin_provider"] == expected
    if detail.json()["test_run"]["run_id"] == test_run["run_id"]:
        assert detail.json()["test_run"]["failure_origin_provider"] == expected
