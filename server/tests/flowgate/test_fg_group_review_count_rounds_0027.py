"""flowgate.v02test.0027 (0007-T, TR0008 rev1 rejection): a review count the policy allows above 3 runs as stored.

Real migrated SQLite, the real draft route (the creation copy) and the real run: with the policy max raised to 5, the
group's default count 5 is accepted by the review-count write, copied once into a new card at creation, and the card's
result review assignment on the slot path (AI author X, AI reviewer B) records ``review_rounds`` 5 and asks its review
loop for 5 — no frozen (1, 2, 3, -1) on the way (``stage_assignment._rounds``). The loop start there is the L-2
recorder (``starts``), never a real AI; the loop row it would write is checked against the migrated table (192: the
loop's review_count follows the assignment rule, -1 or >= 1) and the lost run-end reconcile reads 5 as a review.
"""
import sqlite3
from pathlib import Path

import pytest

from test_fg_card_settings_review import WORK_RUN, _run_ended
from test_fg_ai_roles_l1_card_init import group_defaults
from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, X, ai_writes_result, db_path, decided, env, flow, one, record_starts, roles, save_instruction, start, starts,
    wired,
)
from test_fg_ai_roles_l2_assignment import stored as put_setting

MIGRATIONS = Path(__file__).resolve().parents[2] / "sql" / "migrations"
MIGRATION_192 = "192_ai_invoke_document_review_loop_review_count_range.sql"


def test_policy_max_5_group_count_5_is_saved_copied_and_run_as_5_rounds(env, decided, monkeypatch, starts):
    from flowgate.card_settings import repository
    from modules.flow_gate.services import ai_group_role_profile_service as service
    from modules.flow_gate.settings import ai_execution_policy_service as policy

    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: 5)
    group_defaults({})
    # 1) saved: the group write accepts 5 under the max 5
    assert service.review_count_choices() == (0, 1, 2, 3, 4, 5)
    saved = service.set_review_count("roles", "common", {"review_count": 5})
    assert saved["review_counts"]["common"] == {"review_count": 5, "effective_review_count": 5, "source": "common"}
    # 2) copied once at creation (no card value of its own)
    key = flow(env)
    assert repository.stored(FA, key, "review_count") == 5
    # 3) run: person instruction, AI X result, AI B review (slots, not the legacy fields)
    roles(key, "not_used", X, B)
    start(env)
    save_instruction(env, key)
    [job] = env["jobs"]
    ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["author"], rr["rework_provider_id"]) == ("ai", B, "ai", X)
    assert rr["source"] != "legacy_fields"
    assert rr["review_rounds"] == 5
    loops = [c for c in starts if c.get("document_review_loop")]
    assert len(loops) == 1 and loops[0]["document_review_loop"]["review_count"] == 5


@pytest.mark.parametrize("ceiling,expected", [(5, "review"), (3, None)])
def test_lost_run_end_reconcile_reads_the_count_by_the_policy_choices(env, monkeypatch, ceiling, expected):
    """``review_stage.review_setting`` (B-3 reconcile) had the same frozen set: 5 under max 5 is a review, not unknown;
    5 left outside today's choices stays unknown as before."""
    from flowgate.card_settings import review_stage
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    key = flow(env)
    put_setting(key, "review_count", 5)
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: ceiling)
    assert review_stage.review_setting(FA, key) == expected


def _insert_loop(conn, run_id, count):
    conn.execute(
        "INSERT INTO ai_invoke_document_review_loops (run_id, group_id, doc_ref, review_count, reviewer_provider_id, "
        "review_criteria, rework_provider_id, rework_timeout_sec, failure_restart_max_attempts, total_timeout_sec, "
        "baseline_revision_no, started_at, deadline_at, current_stage, created_at, updated_at) "
        "VALUES (?, 'g', 'd', ?, 'p', 'document_type_default', 'p', 1800, 0, 3600, 0, 't', 't', 'review', 't', 't')",
        (run_id, count))


@pytest.mark.parametrize("count,accepted", [(5, True), (30, True), (1, True), (-1, True), (0, False), (-2, False)])
def test_192_loop_review_count_follows_the_assignment_rule(db_path, count, accepted):
    conn = sqlite3.connect(str(db_path))                    # a bare connection: foreign keys off, only the CHECKs
    try:
        if accepted:
            _insert_loop(conn, "aiv_c", count)
            assert conn.execute("SELECT review_count FROM ai_invoke_document_review_loops").fetchall() == [(count,)]
        else:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _insert_loop(conn, "aiv_c", count)
    finally:
        conn.close()


def test_192_keeps_the_loop_table_indexes_and_foreign_keys(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(ai_invoke_document_review_loops)")}
        assert {"idx_aidrl_group_updated", "idx_aidrl_doc_updated"} <= indexes
        fks = {(row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list(ai_invoke_document_review_loops)")}
        assert fks == {("groups", "group_id"), ("documents", "doc_ref"), ("ai_providers", "reviewer_provider_id"),
                       ("ai_providers", "rework_provider_id")}
    finally:
        conn.close()


def test_192_three_dialects_widen_only_the_review_count_check():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (MIGRATIONS / dialect / MIGRATION_192).read_text(encoding="utf-8")
        body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        assert "CHECK (review_count = -1 OR review_count >= 1)" in body, dialect
        assert "review_count IN (-1,1,2,3)" not in body, dialect
        assert "ai_invoke_document_review_loops_review_count_check" in body or (
            "CREATE TABLE ai_invoke_document_review_loops_new" in body
            and "idx_aidrl_group_updated" in body and "idx_aidrl_doc_updated" in body
            and "'review_stalled'" in body), dialect
