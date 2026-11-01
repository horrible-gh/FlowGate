"""Deletion protection for live work, never for completed history (0116 C-1)."""
from modules.flow_gate.db import ai_evaluations


def active_run_provider_ids() -> set[str]:
    # Called with the lifecycle guard, before opening the settings transaction.
    # Finishing/removing a run can race harmlessly; new registration needs guard.
    from .ai_invoke import runtime
    service = runtime._svc()
    with service._runs_lock:
        ids = set()
        for run in service._runs.values():
            if run.get("status") == "finished":
                continue
            ids.update(run.get("chain_provider_ids") or [])
            ids.add((run.get("provider") or {}).get("id"))
            ids.add(run.get("provider_id"))
        ids.discard(None)
        return ids


def deletion_reason(store, ids: list[str], active_ids: set[str]) -> str | None:
    if not ids:
        return None
    marks = ", ".join("?" for _ in ids)
    if store._fetch_one(
        "SELECT run_id FROM ai_invoke_document_review_loops "
        "WHERE current_stage IN ('rework', 'review') AND stop_reason IS NULL "
        f"AND (reviewer_provider_id IN ({marks}) OR rework_provider_id IN ({marks})) LIMIT 1",
        ids + ids,
    ):
        return "active_loop"
    if active_ids.intersection(ids):
        return "active_run"
    # Leases have no provider column. Only a token attached to a live lease is a
    # supporting marker: historical/unleased tokens must not prevent deletion.
    if store._fetch_one(
        "SELECT l.run_id FROM group_ai_leases l JOIN tokens t ON t.token_id = l.token_id "
        "WHERE l.state IN ('acquiring', 'active', 'releasing') "
        f"AND t.provider_id IN ({marks}) LIMIT 1", ids,
    ):
        return "active_run"
    if ai_evaluations.active_for_providers(ids, store=store):
        return "active_evaluation"
    return None
