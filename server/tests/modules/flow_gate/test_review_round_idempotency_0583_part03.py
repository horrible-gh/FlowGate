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

def test_a_rework_first_loops_review_hop_stamps_its_own_run_id(env):
    """The 0579 regression. The run was admitted as `edit` (action_scope=rework), so the
    old gate stored NULL provenance for every verdict the loop wrote, and the loop could
    not recognise its own round."""
    env.runs[RUN_A] = _run_record(
        RUN_A,
        action_scope="edit",
        review_intent=None,
        requested_provider_id="aip_reworker",
        provider_id="aip_reviewer",
        provider={"id": "aip_reviewer", "name": "Reviewer"},
        document_review_loop={
            "run_id": RUN_A, "current_stage": "review",
            "reviewer_provider_id": "aip_reviewer", "rework_provider_id": "aip_reworker",
        },
    )

    assert submit().status_code == 201

    row = env.db.reviews()[0]
    assert row["review_run_id"] == RUN_A
    # And the loop's ordinary rework -> review provider switch is not reported as a
    # provider fallback: the requested provider for a REVIEW hop is the loop's reviewer.
    assert row["requested_provider_id"] == "aip_reviewer"
    assert row["actual_provider_id"] == "aip_reviewer"
    assert row["fallback_used"] == 0
    assert row["provider_source"] == "project_default"
def test_a_rework_stage_of_the_same_loop_stamps_nothing(env):
    """The stage is the axis, so only the REVIEW stage of a non-review-scope run counts."""
    env.runs[RUN_A] = _run_record(
        RUN_A, action_scope="edit", review_intent=None,
        document_review_loop={"run_id": RUN_A, "current_stage": "rework",
                              "reviewer_provider_id": "aip_reviewer"},
    )

    assert submit().status_code == 201

    row = env.db.reviews()[0]
    assert row["review_run_id"] is None
    assert env.db.claims() == []
def test_tc3_a_verdict_that_lands_between_judge_and_retry_cancels_the_retry(loop_world):
    """§5. The review hop is judged to have produced nothing, so the gate reserves the
    REVIEW stage again. The verdict then commits. The recheck must adopt it as this
    round's progress so no second reviewer is ever launched."""
    world = loop_world(run_id="aiv_0583_tc3", failure_restart_max_attempts=1)

    # 1. the hop finishes, the checkpoint reads, nothing is there yet.
    service._checkpoint_document_review_loop(_run_payload(world))
    reserved = world.loop()
    assert (reserved["current_stage"], reserved["last_hop_kind"]) == ("review", "review")
    assert reserved["last_hop_outcome"] == "failed"
    assert reserved["attempts_used"] == 1
    assert world.review_rows() == []

    # 2. the provider's POST /inbox commits, late.
    world._post_review("issues", [{"locus": "x", "note": "late but real"}])

    # 3. the worker asks once more before minting the retry's token.
    late = review_module.recheck_review_hop_before_retry(_run_payload(world))

    assert late is not None
    assert late["current_stage"] == "rework", "a second reviewer would have been launched"
    assert late["round_no"] == 2
    # The retry is cancelled, not counted: no attempt was spent asking.
    assert late["attempts_used"] == 0
    assert len(world.review_rows()) == 1
    # And the verdict did what a verdict does - the document is rejected for the rework.
    assert world.document()["doc_review_status"] == "rejected"
    assert world.rejected_review_ids == [world.review_rows()[0]["id"]]
def test_tc3_control_a_review_hop_that_really_produced_nothing_still_retries(loop_world):
    """The other half of §5/§11.8: no-verdict retry must keep working when there really
    is no verdict. The recheck writes nothing and says so."""
    world = loop_world(run_id="aiv_0583_tc3b", failure_restart_max_attempts=1)
    service._checkpoint_document_review_loop(_run_payload(world))
    before = world.loop()

    assert review_module.recheck_review_hop_before_retry(_run_payload(world)) is None

    after = world.loop()
    assert after["current_stage"] == "review"
    assert (after["round_no"], after["attempts_used"]) == (
        before["round_no"], before["attempts_used"])
    assert after["updated_at"] == before["updated_at"], "nothing should have been written"
    assert world.review_rows() == []
def test_the_recheck_declines_any_shape_that_is_not_a_reserved_review_retry(loop_world):
    world = loop_world(run_id="aiv_0583_tc3c", failure_restart_max_attempts=1)
    world._post_review("issues", [{"locus": "x", "note": "n"}])
    service._checkpoint_document_review_loop(_run_payload(world))
    assert world.loop()["current_stage"] == "rework"

    assert review_module.recheck_review_hop_before_retry(_run_payload(world)) is None
def test_tc6_a_real_loop_round_trip_writes_one_row_per_revision(loop_world):
    """§8 TC-6: review rev N issues -> auto reject -> rework rev N+1 -> review pass.
    One review row per round, and no (run, revision) pair ever gets a second one."""
    world = loop_world(run_id="aiv_0583_tc6", review_count=2)
    timeline = []

    def hop(post):
        stage = world.loop()["current_stage"]
        post()
        service._checkpoint_document_review_loop(_run_payload(world))
        after = world.loop()
        timeline.append((stage, after["current_stage"], after["stop_reason"]))

    hop(lambda: world._post_review("issues", [{"locus": "a", "note": "round 1"}]))
    hop(world._land_rework)
    hop(lambda: world._post_review("pass", []))

    assert timeline == [
        ("review", "rework", None),
        ("rework", "review", None),
        ("review", "stopped", "review_passed"),
    ]
    rows = world.review_rows()
    assert len(rows) == 2
    assert [row["revision_no"] for row in rows] == [3, 4]
    assert [row["verdict"] for row in rows] == ["issues", "pass"]
    rounds = [(row["review_run_id"], row["revision_no"]) for row in rows]
    assert len(set(rounds)) == len(rounds), "a round must never hold two durable verdicts"
def test_tc7_a_database_that_already_holds_duplicate_rows_still_reads_as_one_round():
    """§6: the barrier stops NEW duplicates; it cannot and must not rewrite history.
    Two rows of one round collapse to one round, and the loop keeps moving."""
    bundle = {
        "review_count": 2, "review_baseline_id": 0, "baseline_revision_no": 1,
        "starts_with_rework": False, "round_no": 1, "current_stage": "review",
        "attempts_used": 0, "failure_restart_max_attempts": 0,
        "review_run_id": "legacy-run", "last_hop_kind": "review",
        "doc": {"revision_no": 1, "doc_review_status": "pending_review"},
    }
    legacy_rows = [
        {"id": 1, "verdict": "issues", "revision_no": 1, "review_run_id": "legacy-run",
         "attempt_no": 1, "findings": []},
        {"id": 2, "verdict": "issues", "revision_no": 1, "review_run_id": "legacy-run",
         "attempt_no": 1, "findings": []},
    ]

    history, latest = review_module._document_loop_review_view(bundle, legacy_rows)

    assert len(history) == 1 and latest["id"] == 2
    assert service.check_expected_progress(bundle, bundle["doc"], legacy_rows) is True
    state = service.resolve_document_review_loop_gate({**bundle, "reviews": legacy_rows})
    assert (state["current_stage"], state["round_no"]) == ("rework", 2)
    assert state["stop_reason"] is None
