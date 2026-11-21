"""Independently selectable scenarios; shared fixtures remain in test_review_round_idempotency_0583.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_review_round_idempotency_0583 import (
    CLAIMS_MIGRATION,
    DOC_ID,
    Env,
    GROUP_ID,
    LOOP_DOC,
    LOOP_GROUP,
    LOOP_MIGRATIONS_107,
    LOOP_OWNER,
    LiveSqliteDB,
    LoopWorld,
    PROJECT,
    Path,
    RUN_A,
    RUN_B,
    TOKENS,
    USER,
    _MIGRATIONS_DIR,
    _SERVER_DIR,
    _Txn,
    _body,
    _build_db,
    _connect_loop_db,
    _raw_post_inbox,
    _run_payload,
    _run_record,
    _seed_unclaimed_verdict,
    ai_runtime,
    contextmanager,
    db_connection,
    db_reviews,
    db_rounds,
    env,
    inbox_routes,
    json,
    loop_world,
    os,
    pytest,
    review_module,
    service,
    sqlite3,
    sse_publisher,
    submit,
    sys,
)

def test_a_verdict_registered_before_the_claim_table_existed_still_closes_its_round(env):
    """The review row is there, its claim is not, and the round must still be closed."""
    _seed_unclaimed_verdict(env.db)
    assert env.db.claims() == [], "the state a deployment is in the moment 113 has run"

    env.as_("tok-a2")  # the same run's second token, submitting after the deploy
    second = submit(_body(verdict="pass", comment="second attempt"))

    assert second.status_code == 409, second.text
    payload = second.json()
    assert payload["code"] == "review_round_already_registered"
    rows = env.db.reviews()
    assert len(rows) == 1, "a second durable verdict landed in one round"
    assert (rows[0]["verdict"], rows[0]["comment"]) == (
        "issues", "the verdict this round already registered")
    assert payload["review_id"] == rows[0]["id"]
    assert payload["verdict"] == "issues"
    assert env.db.claims() == [], "a refused submission writes nothing"
def test_an_unclaimed_verdict_of_another_round_does_not_block_this_one(env):
    """The control. The boundary read is keyed on the round, not on the document: an
    unclaimed verdict of a different run - or of a different revision - must not close
    this round."""
    _seed_unclaimed_verdict(env.db, review_run_id=RUN_B)
    _seed_unclaimed_verdict(env.db, review_run_id=RUN_A, revision_no=2)

    assert submit().status_code == 201, "RUN_A revision 1 is a round of its own"

    rows = env.db.reviews()
    assert len(rows) == 3
    assert [(row["review_run_id"], row["revision_no"]) for row in rows] == [
        (RUN_B, 1), (RUN_A, 2), (RUN_A, 1)]
    assert [(claim["review_run_id"], claim["revision_no"]) for claim in env.db.claims()] == [
        (RUN_A, 1)]
def test_the_transaction_absorbs_an_unclaimed_verdict_the_precheck_did_not_see(
    env, monkeypatch
):
    """The boundary has a race of its own: the unclaimed verdict can commit while a
    second submission is already past its pre-check. So the read that absorbs it lives
    INSIDE the registration transaction too. Blinding the pre-check once leaves that read
    as the only thing between this submission and a second row."""
    _seed_unclaimed_verdict(env.db)
    real = db_rounds.is_registered
    seen = {"n": 0}

    def _blind_first(*args, **kwargs):
        # 1st call is the pre-check; the 2nd is the post-rollback classification.
        seen["n"] += 1
        return False if seen["n"] == 1 else real(*args, **kwargs)

    monkeypatch.setattr(db_rounds, "is_registered", _blind_first)

    env.as_("tok-a2")
    second = submit(_body(verdict="pass", comment="second attempt"))

    assert seen["n"] >= 2, "the claim step should have raised and been classified"
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "review_round_already_registered"
    rows = env.db.reviews()
    assert len(rows) == 1
    assert (rows[0]["verdict"], rows[0]["comment"]) == (
        "issues", "the verdict this round already registered")
    assert env.db.claims() == [], "a rolled-back registration leaves no claim behind"
    token = env.db.fetch_one("SELECT * FROM tokens WHERE token_id = ?", ["tok-a2"])
    assert token["consumed_at"] is None, "no write of a refused request may survive"
def test_migration_113_backfills_every_round_that_already_holds_a_verdict(tmp_path):
    """The bulk half of the boundary, at the only moment it can be closed in bulk.

    The upgrade is applied to a database that already carries review history - a single
    verdict, a pre-barrier duplicate pair, a second run over the same revision, a legacy
    row with no run id, and a review whose document is gone - and the claim table has to
    come out of it describing exactly the rounds that are already registered.
    """
    db = _build_db(str(tmp_path / "pre113.db"), with_claims_migration=False)
    assert db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'document_review_round_claims'") is None, "113 has not run yet"

    _seed_unclaimed_verdict(db, revision_no=1)
    # The incident's own shape: one round, two durable verdicts, 92 seconds apart.
    _seed_unclaimed_verdict(db, revision_no=2, reviewed_at="2026-09-17T08:37:00+09:00")
    _seed_unclaimed_verdict(db, revision_no=2, verdict="pass",
                            reviewed_at="2026-09-17T08:38:32+09:00")
    _seed_unclaimed_verdict(db, review_run_id=RUN_B, revision_no=2)
    _seed_unclaimed_verdict(db, review_run_id=None, revision_no=3)
    # A review whose document is gone. Only reachable on a database that was written
    # while FK enforcement was off (SQLite's pragma is per connection) - which is exactly
    # why the backfill checks for the document instead of trusting the foreign key it
    # would otherwise violate mid-migration.
    db.conn.execute("PRAGMA foreign_keys = OFF")
    _seed_unclaimed_verdict(db, review_run_id="aiv_0583_gone",
                            doc_id="flowgate.default.0583.9999-T")
    db.conn.execute("PRAGMA foreign_keys = ON")

    db.conn.executescript(CLAIMS_MIGRATION.read_text(encoding="utf-8"))

    claims = db.claims()
    assert [(c["review_run_id"], c["doc_id"], c["revision_no"]) for c in claims] == [
        (RUN_A, DOC_ID, 1), (RUN_A, DOC_ID, 2), (RUN_B, DOC_ID, 2),
    ], "one claim per (run, doc, revision) that already holds a verdict"
    # The duplicate pair collapses to a single claim - the backfill cannot trip its own
    # key - and keeps the FIRST verdict's timestamp, the one section 7 calls the result.
    pair = next(c for c in claims if (c["review_run_id"], c["revision_no"]) == (RUN_A, 2))
    assert pair["claimed_at"] == "2026-09-17T08:37:00+09:00"
    assert pair["token_id"] is None, "document_reviews records no token to name"

    # Re-applying the file keeps what is there: a re-run must not be a second chance to
    # fail, nor rewrite a claim a live registration has written since.
    db.conn.executescript(CLAIMS_MIGRATION.read_text(encoding="utf-8"))
    assert db.claims() == claims
def test_a_backfilled_claim_closes_its_round_against_a_token_from_before_the_deploy(
    env, monkeypatch
):
    """End to end: 113 runs over existing history, then the token the pre-deploy retry
    had already minted comes back. The backfilled claim is what answers it."""
    _seed_unclaimed_verdict(env.db)
    env.db.conn.execute("DELETE FROM document_review_round_claims")
    env.db.conn.executescript(CLAIMS_MIGRATION.read_text(encoding="utf-8"))
    assert [(c["review_run_id"], c["revision_no"]) for c in env.db.claims()] == [(RUN_A, 1)]

    # Blind the document_reviews half of the barrier, so the backfilled claim is what has
    # to hold: the pre-check and the claim INSERT each meet it on their own.
    monkeypatch.setattr(db_rounds, "existing_verdict", lambda *_a, **_k: None)
    env.as_("tok-a2")

    second = submit(_body(verdict="pass"))

    assert second.status_code == 409, second.text
    assert second.json()["code"] == "review_round_already_registered"
    assert len(env.db.reviews()) == 1
def test_tc5_an_explicit_rerun_registers_its_own_row_for_the_same_revision(env):
    assert submit().status_code == 201

    env.as_("tok-b1")
    rerun = submit(_body(verdict="pass", comment="rerun verdict"))

    assert rerun.status_code == 201, rerun.text
    rows = env.db.reviews()
    assert len(rows) == 2
    assert [row["revision_no"] for row in rows] == [1, 1]
    assert [row["review_run_id"] for row in rows] == [RUN_A, RUN_B]
    assert [row["review_intent"] for row in rows] == ["normal", "rerun"]
    assert {claim["review_run_id"] for claim in env.db.claims()} == {RUN_A, RUN_B}
def test_a_legacy_review_without_a_run_id_is_left_exactly_as_it_was(env):
    """§3.3: the manual / copy-mention path names no run, so it claims nothing and is
    neither blocked nor deduplicated by this change. Two such reviews of the SAME
    revision still both register, exactly as they did before this T."""
    env.as_("tok-legacy")
    assert submit().status_code == 201, "the first legacy review"
    env.as_("tok-legacy2")
    assert submit(_body(verdict="pass")).status_code == 201, "the second legacy review"

    rows = env.db.reviews()
    assert len(rows) == 2
    assert [row["review_run_id"] for row in rows] == [None, None]
    assert env.db.claims() == []
