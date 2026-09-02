"""AI invoke run registry access — runtime/admission boundary (flowgate.default.0501 T0008 / T3).

Centralizes how the in-memory run registry is read: "what runs does this process know
about, and is each one still going". The registry objects themselves (``_runs``,
``_runs_lock``, ``_group_resume_locks``, ``_group_resume_locks_guard``) stay DEFINED in
``ai_invoke_service.py`` — see that module's own registry-section comment for why a
plain name-copy alias cannot own them safely: roughly thirty existing tests reset the
registry with a full ``monkeypatch.setattr(svc, "_runs", ...)`` reassignment, which a
copied reference would silently stop observing (confirmed by an actual test failure
when a plain alias was tried first). Every function below instead reaches back into
``ai_invoke_service``'s CURRENT globals at call time — a lazy import, the same pattern
that module's own ``_next_run_id`` already uses for a db lookup — so a test
reassignment of ``svc._runs`` is always what these functions see.

Nothing here knows how a run is executed — no worker thread wiring, no provider/CLI
invocation, no chain continuation, no lease acquire/release. Those stay in
``ai_invoke_service.py`` and its exec()-assembled parts (``ai_invoke_worker.py`` /
``ai_invoke_provider_api.py`` / ``ai_invoke_provider_cli.py`` / ``ai_invoke_part3_chain.py``,
flowgate.default.0501 T4 re-split ``ai_invoke_part2_worker.py`` into the first three),
which call the functions below as the one place that knows how to read the registry,
instead of each inlining its own ``with _runs_lock: ...`` block.

Deliberately NOT here (0501 T0008 T3 §2/§8/§9): the run-id counter and its floor date
(plain ints a test also resets by reassignment), lease acquire/release, start_run's
admission sequence, cancel/stop's execution-side cleanup, and respawn_pending/chain
handoff policy.
"""
from __future__ import annotations

import threading
from typing import Optional


def _svc():
    from modules.flow_gate.services import ai_invoke_service
    return ai_invoke_service


def group_resume_lock(group_id: str) -> threading.Lock:
    svc = _svc()
    with svc._group_resume_locks_guard:
        lock = svc._group_resume_locks.get(group_id)
        if lock is None:
            lock = svc._group_resume_locks[group_id] = threading.Lock()
        return lock


def active_run_for_group(group_id: str) -> Optional[dict]:
    svc = _svc()
    with svc._runs_lock:
        for run in svc._runs.values():
            if run["group_id"] == group_id and run["status"] != "finished":
                return run
    return None


def get_run_record(run_id: str) -> Optional[dict]:
    svc = _svc()
    with svc._runs_lock:
        return svc._runs.get(run_id)


def is_run_live(run_id: str) -> bool:
    """Is *run_id* an admission this process still tracks and has not finished?

    The one definition of "alive" shared by the lease-owner mutation gate
    (``mutation_policy._locked``), the manual lease-release guard, and the
    ``GET /ai-invoke/leases`` route — a duplicated check here is exactly how the
    screen and the server told two different stories before (0401 NR0003 §3 cause 3).
    """
    run = get_run_record(run_id)
    return run is not None and run.get("status") != "finished"


def list_live_runs(*, group_id: Optional[str] = None, project_id: Optional[str] = None) -> list[dict]:
    """In-memory runs still going, scoped to exactly one of group/project (caller's
    choice) — the "live" half of GET /ai-invoke/runs (L0007 §2.10.3)."""
    svc = _svc()
    with svc._runs_lock:
        snapshot = list(svc._runs.values())
    items = []
    for run in snapshot:
        if run["status"] == "finished":
            continue
        if group_id is not None and run["group_id"] != group_id:
            continue
        if project_id is not None and run["project_id"] != project_id:
            continue
        items.append(run_list_item_live(run))
    return items


def run_list_item_live(run: dict) -> dict:
    status = run["status"]
    if status == "running" and run.get("pause_requested"):
        status = "pause_requested"
    provider = run.get("provider") or {}
    return {
        "run_id": run["run_id"],
        "group_id": run["group_id"],
        "project_id": run["project_id"],
        "doc_ref": run["doc_ref"],
        "mode": run["mode"],
        "status": status,
        "provider_id": provider.get("id") or run.get("provider_id"),
        "provider_name": provider.get("name"),
        "docs_target": run.get("docs_target"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "hop_item_seq": run.get("hop_item_seq"),
        "started_at": run.get("started_at"),
        # L0007 §2.10.3: the finish-only columns stay null on a live row — present, not
        # omitted, so a client can render every item through the same column set.
        "outcome": None,
        "docs_reached": None,
        "end_reason": None,
        "stop_code": None,
        "resumable": None,
        "finished_at": None,
        "duration_ms": None,
        "last_message_excerpt": None,
    }
