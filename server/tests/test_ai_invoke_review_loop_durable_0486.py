"""Durable document-review-loop regressions (flowgate.default.0486 T0029 / NR0028 F1-F3).

Every verdict here is read back from a real SQLite database that has the real migrations
applied -- `035`/`104` for `document_reviews` provenance, `091`-`107` for the loop row -- and
is written through the real `db_loops.checkpoint()`, the real gate and the real
`_checkpoint_document_review_loop()`. NR0028's post-mortem of why the existing suite could
not see F1 or F2 is the reason for that shape:

  * the worker/checkpoint regressions in `test_ai_invoke_document_review_loop_0417.py`
    replace `_execute_provider_chain` with a monkeypatch, so the one line that pins
    `run["attempt_no"]` back to 1 on every launch never runs;
  * their SQLite `document_reviews` table predates migration 104, so the provenance branch
    of `_document_loop_review_view` is never entered at all;
  * their loop store is a plain dict, so a `stop_reason` no CHECK constraint would accept
    stores happily.

What `LoopWorld` below simulates is only the OUTER wiring -- the provider process that posts
a review and the rework that lands an edit. `attempt_no` is hard-coded to 1 on purpose: that
is the value a run whose provider starts normally really carries, hop after hop, because
`_execute_provider_chain` recomputes it as `len(fallback_history) + 1` every time.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: E402
from modules.flow_gate.db import document_reviews as db_reviews  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as service  # noqa: E402

MIGRATIONS = _SERVER_DIR / "sql" / "migrations" / "sqlite"
GROUP = "flowgate.default.0486"
DOC = "flowgate.default.0486.0029-T"
OWNER = "owner-0486"

REVIEW_MIGRATIONS = (
    "035_document_reviews.sql",
    "104_document_review_provider_provenance.sql",
)
# 107 is this T's own migration; the tuple without it is the pre-fix control.
LOOP_MIGRATIONS_106 = (
    "091_ai_invoke_document_review_loops.sql",
    "092_ai_invoke_document_review_loop_live_run.sql",
    "102_ai_invoke_review_loop_card_dismissed.sql",
    "105_ai_invoke_document_review_loop_hold_stop_reason.sql",
    "106_ai_invoke_document_review_loop_restart_orphaned.sql",
)
LOOP_MIGRATIONS_107 = LOOP_MIGRATIONS_106 + (
    "107_ai_invoke_document_review_loop_review_stalled.sql",
)


class Store:
    """A real-transaction SQLite store.

    `transaction()` is re-entrant like the production store: the nested transaction
    `db_loops.checkpoint()` opens JOINS the checkpoint's own instead of committing
    underneath it. That is what makes the CHECK-constraint failure below roll the automatic
    document rejection back with it, exactly as NR0028 4 F2 reported.
    """

    def __init__(self, conn):
        self.conn = conn
        self._depth = 0

    def _execute(self, sql, values=()):
        cursor = self.conn.execute(sql, values)
        if self._depth == 0:
            self.conn.commit()
        return cursor

    def _execute_affected(self, sql, values=()):
        cursor = self.conn.execute(sql, values)
        if self._depth == 0:
            self.conn.commit()
        return cursor.rowcount

    def _fetch_one(self, sql, values=()):
        row = self.conn.execute(sql, values).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, values=()):
        return [dict(row) for row in self.conn.execute(sql, values).fetchall()]

    @contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield self
        except Exception:
            self._depth -= 1
            if self._depth == 0:
                self.conn.rollback()
            raise
        self._depth -= 1
        if self._depth == 0:
            self.conn.commit()


def _connect(tmp_path, name, loop_migrations):
    conn = sqlite3.connect(tmp_path / name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(
            doc_id TEXT PRIMARY KEY,
            revision_no INTEGER NOT NULL DEFAULT 0,
            doc_review_status TEXT,
            rejection_history TEXT
        );
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        """
    )
    for name_sql in REVIEW_MIGRATIONS + loop_migrations:
        conn.executescript((MIGRATIONS / name_sql).read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO documents(doc_id, revision_no, doc_review_status, rejection_history) "
        "VALUES (?,?,?,?)",
        [DOC, 3, "pending_review", "[]"],
    )
    conn.execute("INSERT INTO groups VALUES (?)", [GROUP])
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


class LoopWorld:
    """One document review loop, driven hop by hop through the production code."""

    def __init__(self, conn, monkeypatch, *, run_id, review_count):
        self.conn = conn
        self.run_id = run_id
        self.store = Store(conn)
        self.rejected_review_ids = []
        monkeypatch.setattr(db_loops, "get_store", lambda: self.store)
        monkeypatch.setattr(db_reviews, "get_store", lambda: self.store)
        monkeypatch.setattr(service, "get_store", lambda: self.store)
        monkeypatch.setattr(service.db_docs, "get_by_id", self._doc)
        monkeypatch.setattr(service, "_auto_reject", self._auto_reject)
        db_loops.insert({
            "run_id": run_id, "group_id": GROUP, "doc_ref": DOC,
            "review_count": review_count, "reviewer_provider_id": "reviewer",
            "review_criteria": "document_type_default", "rework_provider_id": "reworker",
            "rework_timeout_sec": 1800, "rework_message": "fix every finding",
            "failure_restart_max_attempts": 0, "total_timeout_sec": 14400,
            "review_baseline_id": 0, "baseline_revision_no": 3,
            "starts_with_rework": 0,
            "started_at": _now(), "deadline_at": "2099-01-01T00:00:00+00:00",
            "round_no": 1, "current_stage": "review", "attempts_used": 0,
        })

    # ── the document, as the loop reads it ────────────────────────────────────
    def _doc(self, _doc_id):
        return self.store._fetch_one("SELECT * FROM documents WHERE doc_id = ?", [DOC])

    def document(self):
        return self._doc(DOC)

    def rejection_history(self):
        return json.loads(self.document()["rejection_history"] or "[]")

    def review_rows(self):
        return self.store._fetch_all("SELECT * FROM document_reviews ORDER BY id")

    def loop(self):
        return db_loops.get(self.run_id)

    # ── the outer wiring the production code talks to ────────────────────────
    def _auto_reject(self, slot, review, bundle):
        """What `pipeline_service.transition_document_review(action='reject')` leaves behind.

        The status change and the `review_id`-carrying history item are written inside the
        checkpoint's transaction, which is what lets the tests below observe whether a failed
        checkpoint takes the rejection down with it.
        """
        self.rejected_review_ids.append(review["id"])
        history = json.loads(self.document()["rejection_history"] or "[]")
        history.append({
            "review_id": review["id"],
            "reason": service.build_auto_reject_reason(review, slot, bundle.get("api_base_url")),
            "responded_at": None,
        })
        self.store._execute(
            "UPDATE documents SET doc_review_status = ?, rejection_history = ? WHERE doc_id = ?",
            ["rejected", json.dumps(history, ensure_ascii=False), DOC],
        )
        return {"ok": True}

    def _post_review(self, verdict, findings):
        db_reviews.insert_review(
            doc_id=DOC,
            revision_no=int(self.document()["revision_no"]),
            reviewer_id="ai-reviewer",
            verdict=verdict,
            findings_json=json.dumps(findings, ensure_ascii=False),
            comment=None,
            reviewed_at=_now(),
            review_run_id=self.run_id,
            requested_provider_id="reviewer",
            actual_provider_id="reviewer",
            actual_provider_name="Reviewer",
            provider_source="project_default",
            # The real, unchanging value (NR0028 F1). Not a simplification: no run whose
            # provider starts on the first try ever writes anything else.
            attempt_no=1,
            fallback_used=False,
        )

    def _land_rework(self):
        self.store._execute(
            "UPDATE documents SET revision_no = revision_no + 1, doc_review_status = ? "
            "WHERE doc_id = ?",
            ["revised", DOC],
        )

    def run(self, *, max_hops=16, verdict="issues", findings=None):
        """Drive real hops until the DURABLE row says stopped. Returns the hop timeline."""
        findings = findings or (lambda round_no: [{"locus": "x", "note": f"issue {round_no}"}])
        timeline = []
        for _ in range(max_hops):
            loop = self.loop()
            if loop["current_stage"] == "stopped":
                break
            stage = loop["current_stage"]
            if stage == "review":
                self._post_review(verdict, findings(len(self.review_rows()) + 1))
            else:
                self._land_rework()
            service._checkpoint_document_review_loop({
                "run_id": self.run_id, "group_id": GROUP, "doc_ref": DOC,
                "attempt_no": 1, "outcome": "complete", "issued_to": OWNER,
                "document_review_loop": dict(loop),
            })
            after = self.loop()
            timeline.append((stage, after["round_no"], after["current_stage"], after["stop_reason"]))
        return timeline


@pytest.fixture
def world(tmp_path, monkeypatch):
    made = []

    def _make(*, run_id="aiv_t0029", review_count=3, loop_migrations=LOOP_MIGRATIONS_107):
        conn = _connect(tmp_path, f"{run_id}.db", loop_migrations)
        made.append(conn)
        return LoopWorld(conn, monkeypatch, run_id=run_id, review_count=review_count)

    yield _make
    for conn in made:
        conn.close()


# ── NR0028 F1: the review count really is a ceiling ──────────────────────────

@pytest.mark.parametrize("review_count", [1, 2, 3])
def test_review_count_stops_the_loop_at_exactly_that_many_rounds(world, review_count):
    """The regression NR0028 4 F1 measured: 2 and 3 never stopped, only 1 did.

    Before the fix `_document_loop_review_view` collapsed by `attempt_no`, which is 1 on
    every row a normal run writes, so `rounds_used` was pinned at 1 and only `review_count=1`
    could ever satisfy it. Counts 2 and 3 ran review<->rework until the 4-hour total timeout.
    """
    loops = world(run_id=f"aiv_count_{review_count}", review_count=review_count)

    timeline = loops.run()

    durable = loops.loop()
    assert (durable["current_stage"], durable["stop_reason"]) == (
        "stopped", "review_count_exhausted"
    ), timeline
    assert durable["stop_detail"] == f"review count {review_count} exhausted"
    # One review row per round, and the round counter stands one past the last round run.
    assert len(loops.review_rows()) == review_count
    assert durable["round_no"] == review_count + 1
    assert loops.rejected_review_ids == [row["id"] for row in loops.review_rows()]
    # Every row really did carry the same attempt_no; the count survives that now.
    assert {row["attempt_no"] for row in loops.review_rows()} == {1}
    assert len({row["revision_no"] for row in loops.review_rows()}) == review_count


def test_until_it_passes_is_still_unbounded_by_the_round_count(world):
    """The control: -1 must NOT stop by count, so the ceiling above is the count, not a cap."""
    loops = world(run_id="aiv_count_unlimited", review_count=-1)

    loops.run(max_hops=14)

    durable = loops.loop()
    assert durable["current_stage"] != "stopped"
    # 14 hops alternate review/rework, so seven full rounds ran and the eighth is reserved.
    assert len(loops.review_rows()) == 7
    assert durable["round_no"] == 8


def test_a_pass_stops_the_loop_before_the_count_is_spent(world):
    loops = world(run_id="aiv_count_pass", review_count=3)

    loops.run(verdict="pass")

    durable = loops.loop()
    assert (durable["current_stage"], durable["stop_reason"]) == ("stopped", "review_passed")
    assert loops.rejected_review_ids == []
    assert len(loops.review_rows()) == 1


# ── NR0028 F2: review_stalled is a value the loop table accepts ──────────────

def _same_findings(_round_no):
    return [{"locus": "3", "note": "the same finding, round after round"}]


def test_repeated_findings_stop_as_review_stalled_and_keep_their_rejection(world):
    """Migration 107 is what lets the stall detector's own stop_reason be stored at all."""
    loops = world(run_id="aiv_stalled_107", review_count=-1)

    loops.run(findings=_same_findings)

    durable = loops.loop()
    assert (durable["current_stage"], durable["stop_reason"]) == ("stopped", "review_stalled")
    assert durable["stop_detail"] == "consecutive review rounds returned the same findings"
    # The stall is decided on two CONSECUTIVE rounds, so it stops on the second one.
    assert len(loops.review_rows()) == 2
    # And the automatic rejection that round wrote is still there: it shares the
    # checkpoint's transaction, so a failed checkpoint would have taken it with it.
    assert loops.document()["doc_review_status"] == "rejected"
    assert [item["review_id"] for item in loops.rejection_history()] == [1, 2]


def test_without_migration_107_the_same_stall_rolls_back_the_rejection(world):
    """NR0028 4 F2 reproduced: the CHECK inherited from 106 does not list `review_stalled`.

    This is the control that proves the migration -- not some other edit in this TR -- is
    what closes F2, and it also shows the collateral damage: the automatic rejection written
    earlier in the same transaction is rolled back with the checkpoint, so the reviewer's
    findings never reach the document.
    """
    loops = world(
        run_id="aiv_stalled_106", review_count=-1, loop_migrations=LOOP_MIGRATIONS_106,
    )

    with pytest.raises(sqlite3.IntegrityError) as raised:
        loops.run(findings=_same_findings)

    assert "stop_reason" in str(raised.value)
    durable = loops.loop()
    assert (durable["current_stage"], durable["stop_reason"]) == ("review", None)
    assert durable["round_no"] == 2
    assert len(loops.review_rows()) == 2          # the verdict row itself was committed
    assert loops.document()["doc_review_status"] == "revised"   # ... but its rejection was not
    assert [item["review_id"] for item in loops.rejection_history()] == [1]


# ── NR0028 F2: the migration itself, in all three dialects ──────────────────

_KEPT_STOP_REASONS = (
    "review_passed", "review_count_exhausted", "retry_exhausted",
    "total_timeout", "review_verdict_hold", "restart_orphaned",
)


def test_107_widens_the_check_in_three_dialects_without_dropping_106_values():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (
            _SERVER_DIR / "sql" / "migrations" / dialect
            / "107_ai_invoke_document_review_loop_review_stalled.sql"
        ).read_text(encoding="utf-8")
        assert "review_stalled" in sql
        for kept in _KEPT_STOP_REASONS:
            assert kept in sql, f"{dialect} 107 dropped {kept}"
        # PostgreSQL swaps the named constraint; SQLite and MySQL must rebuild the table,
        # and a rebuild that forgets either index silently loses it (the 106 contract).
        assert "ai_invoke_document_review_loops_stop_reason_check" in sql or (
            "CREATE TABLE ai_invoke_document_review_loops_new" in sql
            and "idx_aidrl_group_updated" in sql
            and "idx_aidrl_doc_updated" in sql
        )


def _pragma(conn, sql):
    return [tuple(row) for row in conn.execute(sql).fetchall()]


def test_107_preserves_rows_indexes_and_foreign_keys_of_a_populated_106_database(tmp_path):
    """The SQLite rebuild is applied to a database that already holds loop rows."""
    conn = _connect(tmp_path, "upgrade-106.db", LOOP_MIGRATIONS_106)
    for run_id, stage, reason, detail in (
        ("aiv_live", "review", None, None),
        ("aiv_done", "stopped", "review_passed", None),
        ("aiv_orphan", "stopped", "restart_orphaned", "worker lease orphaned by server restart"),
    ):
        conn.execute(
            "INSERT INTO ai_invoke_document_review_loops ("
            "run_id, group_id, doc_ref, review_count, reviewer_provider_id, review_criteria,"
            "rework_provider_id, rework_timeout_sec, rework_message,"
            "failure_restart_max_attempts, total_timeout_sec, review_baseline_id,"
            "baseline_revision_no, starts_with_rework, started_at, deadline_at, round_no,"
            "current_stage, stop_reason, stop_detail, attempts_used, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, GROUP, DOC, 2, "reviewer", "document_type_default", "reworker", 1800,
             "fix it", 0, 3600, 0, 3, 0, "2026-09-10T00:00:00+00:00",
             "2026-09-10T04:00:00+00:00", 2, stage, reason, detail, 1,
             "2026-09-10T00:00:00+00:00", "2026-09-10T00:30:00+00:00"],
        )
    conn.commit()
    table = "ai_invoke_document_review_loops"
    before_columns = _pragma(conn, f"PRAGMA table_info({table})")
    before_rows = _pragma(conn, f"SELECT * FROM {table} ORDER BY run_id")
    before_indexes = sorted(row[1] for row in _pragma(conn, f"PRAGMA index_list({table})"))
    before_fks = sorted(_pragma(conn, f"PRAGMA foreign_key_list({table})"))

    conn.executescript(
        (MIGRATIONS / "107_ai_invoke_document_review_loop_review_stalled.sql")
        .read_text(encoding="utf-8")
    )

    assert _pragma(conn, f"PRAGMA table_info({table})") == before_columns
    assert _pragma(conn, f"SELECT * FROM {table} ORDER BY run_id") == before_rows
    assert sorted(row[1] for row in _pragma(conn, f"PRAGMA index_list({table})")) == before_indexes
    assert sorted(_pragma(conn, f"PRAGMA foreign_key_list({table})")) == before_fks
    assert _pragma(conn, "PRAGMA foreign_key_check") == []
    # The one behavioural difference: the value that used to raise now stores.
    conn.execute(
        f"UPDATE {table} SET current_stage='stopped', stop_reason='review_stalled', "
        "stop_detail='consecutive review rounds returned the same findings' WHERE run_id=?",
        ["aiv_live"],
    )
    conn.commit()
    assert conn.execute(
        f"SELECT stop_reason FROM {table} WHERE run_id='aiv_live'"
    ).fetchone()[0] == "review_stalled"
    conn.close()


# ── NR0028 F5: the loop's auto-reject dedupes by review row, not by status ───

def _checkpoint_one_review_hop(loops, loop_row):
    service._checkpoint_document_review_loop({
        "run_id": loops.run_id, "group_id": GROUP, "doc_ref": DOC,
        "attempt_no": 1, "outcome": "complete", "issued_to": OWNER,
        "document_review_loop": dict(loop_row),
    })


def _seed_landed_rework(loops, history):
    """The state a landed rework leaves: the rejection is in the ledger, the status is not.

    `('rejected','submit') -> 'revised'` is the transition that erases it, and a human
    `mark_revised` at the same revision does the same thing.
    """
    loops.store._execute(
        "UPDATE documents SET doc_review_status = ?, rejection_history = ? WHERE doc_id = ?",
        ["revised", json.dumps(history, ensure_ascii=False), DOC],
    )


def test_a_review_row_already_rejected_is_never_rejected_twice(world):
    """The loop's auto-reject asked only "is the document `rejected` right now?".

    Its sibling gate `resolve_review_gate` has ANDed that momentary status with the review
    row's own identity since T0005, precisely because the status does not survive a landed
    rework. The document-review loop did not, so once the status had moved on, the very same
    review row became a rejection candidate again (NR0028 F5 / NR0014 8-5).
    """
    loops = world(run_id="aiv_dedupe", review_count=-1)
    loop_row = loops.loop()
    loops._post_review("issues", [{"locus": "x", "note": "one"}])
    _seed_landed_rework(loops, [{"review_id": 1, "reason": "rejected once already"}])

    _checkpoint_one_review_hop(loops, loop_row)

    assert loops.rejected_review_ids == []
    assert [item["review_id"] for item in loops.rejection_history()] == [1]
    assert loops.document()["doc_review_status"] == "revised"


def test_a_review_row_not_in_the_ledger_is_still_rejected(world):
    """The positive control: the new term is an identity check, not a mute button."""
    loops = world(run_id="aiv_dedupe_positive", review_count=-1)
    loop_row = loops.loop()
    loops._post_review("issues", [{"locus": "x", "note": "one"}])
    _seed_landed_rework(loops, [{"review_id": 999, "reason": "a different review row"}])

    _checkpoint_one_review_hop(loops, loop_row)

    assert loops.rejected_review_ids == [1]
    assert [item["review_id"] for item in loops.rejection_history()] == [999, 1]
    assert loops.document()["doc_review_status"] == "rejected"


# ── NR0028 F3: a loop nobody could checkpoint is still closed ────────────────

def _active_loop(world_factory, run_id):
    loops = world_factory(run_id=run_id, review_count=2)
    return loops


def test_force_stop_closes_an_uncheckpointable_loop_exactly_once(world):
    loops = _active_loop(world, "aiv_zombie")
    run = {"run_id": loops.run_id, "document_review_loop": dict(loops.loop())}

    assert service.force_stop_loop_after_checkpoint_failure(
        run, RuntimeError("CHECK constraint failed")
    ) is True

    durable = loops.loop()
    assert (durable["current_stage"], durable["stop_reason"]) == ("stopped", "retry_exhausted")
    assert durable["stop_detail"].startswith("loop checkpoint could not be written")
    assert "CHECK constraint failed" in durable["stop_detail"]
    # The caller's observable state follows the durable row, so the card stops too.
    assert run["document_review_loop"]["current_stage"] == "stopped"
    # One-way CAS: a replay changes nothing.
    assert service.force_stop_loop_after_checkpoint_failure(run) is False
    assert loops.loop() == durable


def test_force_stop_never_relabels_a_loop_that_already_ended(world):
    loops = _active_loop(world, "aiv_already_held")
    loops.store._execute(
        "UPDATE ai_invoke_document_review_loops SET current_stage='stopped', "
        "stop_reason='review_verdict_hold', stop_detail='human hold' WHERE run_id=?",
        [loops.run_id],
    )
    terminal = loops.loop()

    # Even with a stale in-memory row that still believes the loop is active.
    stale = {"run_id": loops.run_id, "document_review_loop": {"current_stage": "review"}}
    assert service.force_stop_loop_after_checkpoint_failure(stale, RuntimeError("late")) is False
    assert loops.loop() == terminal


def test_finalize_closes_the_loop_before_it_releases_the_group_lease(world, monkeypatch, tmp_path):
    """The whole point of F3: after the release, no restart recovery can reach this row.

    `startup_recover_leases` -> `_record_orphaned_lease_run` -> `stop_for_restart_orphan`
    finds loops through their group lease. Once `_finalize_run` releases it, a loop still
    sitting at `current_stage='review'` is unreachable forever. So the release is made to
    observe what the loop row looked like at the moment it happened.
    """
    loops = _active_loop(world, "aiv_finalize_zombie")
    monkeypatch.setattr(
        db_loops, "checkpoint",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("loop table unwritable")),
    )
    released = {}

    def _release(group_id, run_id, reason=None):
        released["loop"] = db_loops.get(run_id)
        released["reason"] = reason
        return True

    monkeypatch.setattr(db_leases, "release", _release)
    monkeypatch.setattr(service, "peek_auto_resume", lambda _group_id: None)
    monkeypatch.setattr(service, "_broadcast", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_apply_stop_row", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_persist_run_record", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_notify_chain_failure_if_needed", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_git_status_paths", lambda _root: None)
    scratch = tmp_path / loops.run_id
    scratch.mkdir(parents=True, exist_ok=True)

    run = {
        "run_id": loops.run_id, "group_id": GROUP, "project_id": "flowgate", "doc_ref": DOC,
        "mode": "single", "status": "running", "action_scope": "review",
        "scope_oracle_run": False, "completion_oracle": None,
        "docs_target": 1, "docs_reached": 0, "reached_doc_ids": [],
        "outcome": "complete", "end_reason": "completed", "exit_code": 0,
        "last_message": None, "last_message_received": False,
        "provider": {"id": "reviewer", "name": "Reviewer"}, "provider_id": "reviewer",
        "attempt_no": 1, "attempts_used": 1, "attempts_max": 3,
        "fallback_history": [], "register_errors": [], "tool_call_misses": 0,
        "turn_limit_exhausted": False, "oracle_mismatch": False,
        "source_dirty": None, "source_dirty_files": [],
        "scratch_dir": str(scratch), "scratch_retained": None,
        "started_at": "2026-09-10T10:00:00+09:00", "started_mono": time.monotonic() - 60.0,
        "finished_at": None, "duration_ms": None,
        "dirty_baseline": set(), "source_root": None,
        "timeout_sec": 1800, "deadline_at": "2026-09-10T10:30:00+09:00",
        "cancel_event": threading.Event(),
        "hop_item_seq": None, "token_id": "tok_0029", "issued_to": OWNER,
        "watchdog_kill": None, "timeout_kind": None, "timeout_diagnosis": None,
        "stdout_tail": None, "stderr_tail": None,
        "chain_docs_reached": 0, "chain_docs_accounted": False,
        "continuation_target_seq": None,
        "document_review_loop": dict(loops.loop()),
        "document_review_loop_checkpointed": False,
    }

    service._finalize_run(run)

    assert released, "the group lease was never released"
    assert (released["loop"]["current_stage"], released["loop"]["stop_reason"]) == (
        "stopped", "retry_exhausted",
    )
    assert "loop table unwritable" in released["loop"]["stop_detail"]
