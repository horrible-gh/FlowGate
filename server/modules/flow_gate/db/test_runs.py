"""CRUD for TS remote test execution runs."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .connection import get_store, now_iso


def _today_key() -> str:
    return datetime.fromisoformat(now_iso()).strftime("%Y%m%d")


def next_run_id() -> str:
    date_key = _today_key()
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM test_runs WHERE run_id LIKE ?",
        [f"trun_{date_key}_%"],
    )
    return f"trun_{date_key}_{(row['cnt'] if row else 0) + 1:06d}"


def get_run(run_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM test_runs WHERE run_id = ?", [run_id])


def get_running_by_doc(doc_id: str) -> Optional[dict]:
    # 0358 T0004 §4: 'cancelling' is still an active row (the kill/cleanup hasn't
    # landed yet) — the concurrent-run gate must keep 409-ing until the terminal
    # 'cancelled' row appears. Only 'cancelled' — not 'running'/'cancelling' — allows
    # a fresh admission.
    return get_store()._fetch_one(
        "SELECT * FROM test_runs WHERE doc_id = ? AND status IN ('running', 'cancelling') "
        "ORDER BY created_at DESC LIMIT 1",
        [doc_id],
    )


def get_active_by_group(group_id: str) -> Optional[dict]:
    """The most recent still-running/cancelling run of ANY document in a group.

    0441 TR0005 rev2 (rejection: every OTHER document of the group must go inert too):
    ``get_running_by_doc`` answers the per-document admission gate, so it can only lock
    the tab the run was started from. The action bar has to go inert on every document of
    the group, so the same 'running'/'cancelling' predicate is asked group-wide here.
    """
    if not group_id:
        return None
    return get_store()._fetch_one(
        "SELECT tr.* FROM test_runs tr JOIN documents d ON d.doc_id = tr.doc_id "
        "WHERE d.group_id = ? AND tr.status IN ('running', 'cancelling') "
        "ORDER BY tr.created_at DESC LIMIT 1",
        [group_id],
    )


def insert_run(
    *,
    doc_id: str,
    revision_no: int,
    triggered_via: str,
    runner_id: str,
    setup: list[dict] | None = None,
    cases: list[dict],
    teardown: list[dict] | None = None,
    locale: str | None = None,
) -> dict:
    store = get_store()
    run_id = next_run_id()
    now = now_iso()
    setup = setup or []
    teardown = teardown or []
    with store.transaction():
        store._execute(
            "INSERT INTO test_runs "
            "(run_id, doc_id, revision_no, status, triggered_via, runner_id, "
            "case_total, case_passed, case_failed, error, picked_at, started_at, "
            "finished_at, port, locale, created_at) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, 0, 0, NULL, NULL, ?, NULL, NULL, ?, ?)",
            [run_id, doc_id, revision_no, triggered_via, runner_id, len(cases), now, locale, now],
        )
        for item in [*setup, *cases, *teardown]:
            store._execute(
                "INSERT INTO test_run_cases "
                "(run_id, kind, case_no, case_title, cmd, expect, result, exit_code, "
                "duration_ms, output_tail, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)",
                [
                    run_id,
                    item.get("kind") or "case",
                    item["case_no"],
                    item.get("title") or "",
                    item["cmd"],
                    item.get("expect") or "",
                ],
            )
    run = get_run(run_id)  # type: ignore[assignment]
    if run is not None:
        run["setup_total"] = len(setup)
        run["teardown_total"] = len(teardown)
    return run  # type: ignore[return-value]


def list_by_doc(doc_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM test_runs WHERE doc_id = ? ORDER BY created_at DESC, run_id DESC",
        [doc_id],
    )


def list_cases(run_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM test_run_cases WHERE run_id = ? ORDER BY id ASC",
        [run_id],
    )


def pick_next_running() -> Optional[dict]:
    store = get_store()
    row = store._fetch_one(
        "SELECT * FROM test_runs WHERE status = 'running' AND picked_at IS NULL "
        "ORDER BY created_at ASC, run_id ASC LIMIT 1"
    )
    if row is None:
        return None
    picked_at = now_iso()
    store._execute(
        "UPDATE test_runs SET picked_at = ? WHERE run_id = ? AND picked_at IS NULL",
        [picked_at, row["run_id"]],
    )
    return get_run(row["run_id"])


def mark_case_finished(
    *,
    case_id: int,
    result: str,
    exit_code: Optional[int],
    duration_ms: int,
    output_tail: str,
) -> None:
    get_store()._execute(
        "UPDATE test_run_cases SET result = ?, exit_code = ?, duration_ms = ?, "
        "output_tail = ?, finished_at = ? WHERE id = ?",
        [result, exit_code, duration_ms, output_tail, now_iso(), case_id],
    )


def update_case_observation(
    *,
    case_id: int,
    exit_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    output_tail: Optional[str] = None,
) -> None:
    get_store()._execute(
        "UPDATE test_run_cases SET exit_code = ?, duration_ms = ?, output_tail = ? "
        "WHERE id = ?",
        [exit_code, duration_ms, output_tail, case_id],
    )


def set_run_port(run_id: str, port: int) -> None:
    get_store()._execute(
        "UPDATE test_runs SET port = ? WHERE run_id = ?",
        [port, run_id],
    )


def set_run_source_root(run_id: str, source_root: str, source_root_kind: str) -> None:
    """Record the tree this run actually executed in (0280 NR0003 §6-2).

    *source_root* is storage-root-relative; *source_root_kind* is 'worktree' or the
    reason the run fell back to the base project-branch tree. Written once, right
    after resolution, so the evidence exists even if the run later dies mid-way.
    """
    get_store()._execute(
        "UPDATE test_runs SET source_root = ?, source_root_kind = ? WHERE run_id = ?",
        [source_root, source_root_kind, run_id],
    )


def finish_run(
    *,
    run_id: str,
    status: str,
    case_passed: int = 0,
    case_failed: int = 0,
    tsr_doc_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Terminal write for a natural completion (setup_failed/passed/failed).

    Gated to rows still 'running' (0358 T0004 risk 2): once a cancel has moved a run to
    'cancelling'/'cancelled', a racing natural completion must not resurrect it as
    passed/failed. This WHERE clause is a real guard at the SQL level regardless of
    FlowGateStore._execute's missing affected-row count — it just does not by itself
    tell the caller which side won a race. Callers that need to branch on the outcome
    must serialize with test_run_service._get_run_lock(run_id) around this call.
    """
    get_store()._execute(
        "UPDATE test_runs SET status = ?, case_passed = ?, case_failed = ?, "
        "tsr_doc_id = ?, error = ?, finished_at = ? WHERE run_id = ? AND status = 'running'",
        [status, case_passed, case_failed, tsr_doc_id, error, now_iso(), run_id],
    )


def cas_running_to_cancelling(run_id: str) -> None:
    """Conditional 'running'→'cancelling' write. Caller must hold the per-run lock
    (test_run_service._get_run_lock) to use this as a true CAS — see finish_run."""
    get_store()._execute(
        "UPDATE test_runs SET status = 'cancelling' WHERE run_id = ? AND status = 'running'",
        [run_id],
    )


def cas_cancelling_to_cancelled(run_id: str, *, error: str = "cancelled_by_user") -> None:
    """Conditional 'cancelling'→'cancelled' terminal write. Same locking caveat as
    cas_running_to_cancelling; safe to call twice (second call is a no-op)."""
    get_store()._execute(
        "UPDATE test_runs SET status = 'cancelled', error = ?, finished_at = ? "
        "WHERE run_id = ? AND status = 'cancelling'",
        [error, now_iso(), run_id],
    )


def mark_orphaned_running() -> int:
    """Startup reap (0358 T0004 §4): 'running' rows the crashed process never
    finished become failed/orphaned_by_restart as before; 'cancelling' rows — a
    cancel was accepted but cleanup never landed before the restart — become
    cancelled/cancelled_by_restart rather than being left stuck mid-cancel forever.
    Runs single-threaded at startup before the worker starts, so no lock is needed.
    """
    rows = get_store()._fetch_all(
        "SELECT run_id FROM test_runs WHERE status = 'running'"
    )
    for row in rows:
        finish_run(
            run_id=row["run_id"],
            status="failed",
            case_passed=0,
            case_failed=0,
            error="orphaned_by_restart",
        )
    cancelling_rows = get_store()._fetch_all(
        "SELECT run_id FROM test_runs WHERE status = 'cancelling'"
    )
    for row in cancelling_rows:
        cas_cancelling_to_cancelled(row["run_id"], error="cancelled_by_restart")
    return len(rows) + len(cancelling_rows)
