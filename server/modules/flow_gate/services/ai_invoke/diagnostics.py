"""Run history and status read models (0501 NR0003 §12 `diagnostics.py`).

Read-only projections over a run -- `GET /ai-invoke/runs`, `GET /ai-invoke/runs/{id}`,
the group's active-run status, `get_status`, and the stored-row -> API-shape mapping
they share. It answers "what happened / what is happening", and writes nothing: no
registry mutation, no lease, no spawn. Live rows come from `runtime`; finished rows come
from the durable `ai_invoke_runs` record `finalize` writes.

`get_status` used to live on `chain.py`, which made this module import `chain` for it
while `chain.py` imported this one back for `_run_detail_from_row` -- a direct cycle.
It is a live-run status projection with no chain-specific state (mode/cancel/handoff),
so NR0003 §12's boundary puts it here instead: `chain.py` now reaches it the same
one-way `diagnostics.get_status` `_run_detail_from_row` was already reached the other
direction.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import HTTPException
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import projects as db_projects

from .runtime import (
    RUN_LIST_LIMIT_DEFAULT,
    RUN_LIST_LIMIT_MAX,
    _http_error,
    _svc,
    list_live_runs,
    logger,
)


# ── 0359 L0007 §2.10.2~3: run lookup that survives a restart (bundle 4) ──────────
# Live runs never leave `_runs` (a process only forgets them on restart), so the
# DB fallback below is only ever consulted for a run that finished in an EARLIER
# process. Memory wins whenever both would answer — the moment right after
# finalize persists the row is the only overlap, and it is the freshest source.

def ai_run_succeeded(row: dict) -> bool:
    """Return the single terminal success verdict used by list and detail views."""
    outcome = str(row.get("outcome") or "").strip().lower()
    stop_code = str(row.get("stop_code") or "").strip()
    end_reason = str(row.get("end_reason") or "").strip().lower()
    return outcome == "complete" and not stop_code and end_reason not in {
        "cancelled", "canceled", "failed", "error", "stopped", "timeout",
    }


def get_status(run_id: str) -> dict:
    run = _svc().get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["status"] == "finished":
        return {"ok": True, "run_id": run_id, "status": "finished", "mode": run["mode"],
                **_svc().finished_payload(run)}
    # 0226 NR0003 §5-2: count run-attributed documents (the same oracle filter the
    # final judge uses) instead of the raw group max-seq delta, which inflated the
    # live counter with drafts (auto-created N/T) and documents outside this run.
    docs_so_far = 0
    if run.get("completion_oracle") is None:
        # A scoped-oracle run targets 0 documents; counting group documents here would
        # report progress like 1/0 against work it does not measure (0248 B0001).
        try:
            docs_so_far = len(_svc()._oracle_new_docs(run))
        except Exception:
            pass
    # 0252 P0008 S4: surface the accepted pause request so a reload/poll does not
    # silently revert the card from "stop scheduled" back to plain running.
    status = run["status"]
    if status == "running" and run.get("pause_requested"):
        status = "pause_requested"
    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "mode": run["mode"],
        "group_id": run["group_id"],
        "docs_target": run["docs_target"],
        "docs_reached_so_far": docs_so_far,
        "chain_id": run.get("chain_id"),
        "chain_docs_target": int(run.get("chain_docs_target") or 0),
        "chain_docs_reached": (
            int(run.get("chain_docs_reached") or 0)
            + (0 if run.get("chain_docs_accounted") else docs_so_far)
        ),
        "provider": run["provider"],
        "attempt_no": run["attempt_no"],
        "started_at": run["started_at"],
        "elapsed_ms": int((time.monotonic() - run["started_mono"]) * 1000),
        # 0414 P0007 상태 응답 — the same review facts a live card needs mid-hop.
        "hop_item_seq": run.get("hop_item_seq"),
        "hop_kind": run.get("hop_kind"),
        "hop_review_count": run.get("hop_review_count"),
        "hop_reviewer_provider_id": run.get("hop_reviewer_provider_id"),
        "hop_reviewer_provider_name": run.get("hop_reviewer_provider_name"),
        "continuation_review_count_overrides": run.get("continuation_review_count_overrides"),
        "continuation_reviewer_overrides": run.get("continuation_reviewer_overrides"),
        # 0359 P0006 [hop budget]: a live card can now say how much of the budget is gone
        # without anyone re-deriving the formula from the server log.
        "timeout_sec": run.get("timeout_sec"),
        "deadline_at": run.get("deadline_at"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "attempts_max": run.get("attempts_max"),
        # Server truth: an explicit empty array clears stale client state on the next poll.
        "pending_q_doc_ids": _svc()._open_q_doc_ids(run["group_id"]),
        "document_review_loop": _svc().document_review_loop_payload(run),
    }


def get_run_detail(run_id: str) -> dict:
    """Detail lookup for GET /ai-invoke/{run_id} (L0007 §2.10.2).

    A live or same-process-finished run answers exactly as :func:`get_status`
    always has — only ``persisted`` is added, so no existing caller's response
    shape moves. A run this process never saw falls back to the `ai_invoke_runs`
    row DB0008 kept for it.
    """
    if _svc().get_run_record(run_id) is not None:
        payload = get_status(run_id)
        payload["persisted"] = False
    else:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        row = db_runs.get(run_id)
        if row is None:
            raise _http_error(404, "run_not_found", "Unknown or expired run id.")
        payload = _run_detail_from_row(row)
        payload["persisted"] = True

    project_id = str(payload.get("project_id") or payload.get("group_id") or "").split(".", 1)[0]
    payload["project_id"] = project_id
    payload["succeeded"] = ai_run_succeeded(payload)
    payload["doc_title"] = None
    if payload.get("doc_ref"):
        try:
            doc = db_docs.get_by_id(payload["doc_ref"])
            if doc and doc.get("project_id") == project_id:
                payload["doc_title"] = doc.get("title")
        except Exception:
            logger.debug("AI detail document enrichment skipped", exc_info=True)
    return payload


def _persisted_register_failures(run_id: str) -> list:
    """Structured binding failures for a finished run, or [] when it predates them."""
    try:
        from modules.flow_gate.db import register_context_failures as db_register_failures

        return db_register_failures.list_by_run(run_id)
    except Exception:
        logger.debug("register context failure lookup skipped", exc_info=True)
        return []


def _run_detail_from_row(row: dict) -> dict:
    """Shape a persisted `ai_invoke_runs` row like the live `finished_payload` (same
    field names) so a client renders both through one path."""
    payload = {
        "ok": True,
        "run_id": row["run_id"],
        "status": "finished",
        "mode": row["mode"],
        "group_id": row["group_id"],
        "project_id": row.get("project_id"),
        "doc_ref": row.get("doc_ref"),
        "outcome": row.get("outcome"),
        "docs_reached": row.get("docs_reached"),
        "docs_target": row.get("docs_target"),
        "reached_doc_ids": row.get("reached_doc_ids"),
        "end_reason": row.get("end_reason"),
        "exit_code": row.get("exit_code"),
        "last_message_received": row.get("last_message") is not None,
        "last_message": row.get("last_message"),
        "last_message_excerpt": row.get("last_message_excerpt"),
        "provider_id": row.get("provider_id"),
        "provider_name": row.get("provider_name"),
        "selected_provider_source": row.get("selected_provider_source"),
        "fallback_allowed": bool(row.get("fallback_allowed")),
        "attempt_no": row.get("attempt_no"),
        "fallback_history": row.get("fallback_history"),
        "register_errors": row.get("register_errors"),
        # 0492 T0018 item 3: the axis-classified form of the same failures, when this run
        # has one. `register_errors` above is deliberately left as it was — every existing
        # reader still uses it, and it is both the backfill source and the rollback safety
        # net. A run from before migration 094 has no rows here and keeps answering out of
        # the legacy array alone.
        "register_context_failures": _persisted_register_failures(row["run_id"]),
        "tool_call_misses": row.get("tool_call_misses"),
        "turn_limit_exhausted": bool(row.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(row.get("oracle_mismatch")),
        # 0505 T0006 (DB0005 3.3): same names, same restart-half contract as the four
        # exit diagnostics below -- a run from before migration 095 has none of this
        # and reads back as None on every one of these ten keys. transport_fallback_kind
        # (0496 T0006 §3.2) is the eleventh, on migration 101, and reads back the same
        # way for any run that predates it.
        "operator_api_base": row.get("operator_api_base"),
        "transport_api_base": row.get("transport_api_base"),
        "transport_fallback_kind": row.get("transport_fallback_kind"),
        "last_tool_name": row.get("last_tool_name"),
        "last_tool_status": row.get("last_tool_status"),
        "last_tool_error": row.get("last_tool_error"),
        "api_turns_used": row.get("api_turns_used"),
        "model_http_calls": row.get("model_http_calls"),
        "model_last_http_status": row.get("model_last_http_status"),
        "tool_calls_received": row.get("tool_calls_received"),
        "tool_calls_executed": row.get("tool_calls_executed"),
        "api_turn_trace": row.get("api_turn_trace") or [],
        "source_dirty": row.get("source_dirty"),
        "scratch_retained": row.get("scratch_retained"),
        "duration_ms": row.get("duration_ms"),
        "stop_code": row.get("stop_code"),
        "stop_reason": row.get("stop_reason"),
        "resumable": bool(row.get("resumable")),
        "hop_item_seq": row.get("hop_item_seq"),
        "token_id": row.get("token_id"),
        "issued_to": row.get("issued_to"),
        "attempts_used": row.get("attempts_used"),
        "attempts_max": row.get("attempts_max"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "timeout_sec": row.get("timeout_sec"),
        "deadline_at": row.get("deadline_at"),
        "document_review_loop": _svc().document_review_loop_payload({
            "document_review_loop": _svc()._restore_document_review_loop(row["run_id"])
        }),
        # 0406 T0022 items 3 and 5: the same questions must stay answerable after the run
        # ends: who ran this hop, what the server handled, whether a handoff note went in.
        "worker_document_type": row.get("worker_document_type"),
        "continuation_instruction_mode_requested": row.get(
            "continuation_instruction_mode_requested"
        ),
        "continuation_instruction_mode_normalized": row.get(
            "continuation_instruction_mode_normalized"
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            row.get("continuation_instruction_mode_fallback_applied")
        ),
        "auto_handled_item_seqs": row.get("auto_handled_item_seqs") or [],
        "prompt_message_source": row.get("prompt_message_source"),
        "prompt_common_default_applied": bool(row.get("prompt_common_default_applied")),
        "prompt_user_message_length": row.get("prompt_user_message_length"),
        "prompt_user_message_sha256": row.get("prompt_user_message_sha256"),
        "prompt_final_length": row.get("prompt_final_length"),
        "prompt_final_sha256": row.get("prompt_final_sha256"),
        # 0446 T0016 §3-4: same names, same meaning as `finished_payload` — this is the
        # restart half of the pair, and a field that reads differently here than it did in
        # memory is exactly the divergence that section forbids.
        "timeout_kind": row.get("timeout_kind"),
        "timeout_diagnosis": row.get("timeout_diagnosis"),
        "stdout_tail": row.get("stdout_tail"),
        "stderr_tail": row.get("stderr_tail"),
    }
    # `finished_payload` carries the path list only when something actually spilled. Mirror
    # that, so the key's PRESENCE means the same thing on both sides of a restart.
    if row.get("source_dirty"):
        payload["source_dirty_files"] = list(row.get("source_dirty_files") or [])
    return payload


def _run_list_item_stored(row: dict) -> dict:
    return {
        "run_id": row["run_id"],
        "group_id": row["group_id"],
        "project_id": row.get("project_id"),
        "doc_ref": row.get("doc_ref"),
        "mode": row["mode"],
        "status": "finished",
        "provider_id": row.get("provider_id"),
        "provider_name": row.get("provider_name"),
        "docs_target": row.get("docs_target"),
        "attempts_used": row.get("attempts_used"),
        "hop_item_seq": row.get("hop_item_seq"),
        "started_at": row.get("started_at"),
        "outcome": row.get("outcome"),
        "docs_reached": row.get("docs_reached"),
        "end_reason": row.get("end_reason"),
        "stop_code": row.get("stop_code"),
        "resumable": bool(row.get("resumable")),
        "finished_at": row.get("finished_at"),
        "duration_ms": row.get("duration_ms"),
        "last_message_excerpt": row.get("last_message_excerpt"),
    }


def list_runs(*, group_id: Optional[str] = None, project: Optional[str] = None,
               limit: Optional[int] = None) -> dict:
    """GET /ai-invoke/runs (L0007 §2.10.3): live runs (memory) merged with finished
    runs (DB0008's `ai_invoke_runs`), newest first. Exactly one of group_id/project
    scopes the query — the route layer validates format and permission; this
    function still enforces the XOR and existence checks so a direct caller gets
    the same contract.
    """
    if (group_id is None) == (project is None):
        raise HTTPException(status_code=422, detail={
            "code": "validation_failed",
            "errors": [{"loc": "group_id",
                        "msg": "exactly one of group_id or project is required"}],
        })
    limit_value = limit if limit else RUN_LIST_LIMIT_DEFAULT
    limit_value = max(1, min(limit_value, RUN_LIST_LIMIT_MAX))
    project_id = project if project is not None else group_id.split(".", 1)[0]
    if db_projects.get_by_id(project_id) is None:
        raise _http_error(404, "project_not_found", f"Project not found: {project_id}")

    from modules.flow_gate.db import ai_invoke_runs as db_runs

    if group_id is not None:
        live_items = list_live_runs(group_id=group_id)
        fetch_limit = limit_value + len(live_items)
        stored_rows = db_runs.list_by_group(group_id, fetch_limit)
        total = db_runs.count_by_group(group_id)
    else:
        live_items = list_live_runs(project_id=project_id)
        fetch_limit = limit_value + len(live_items)
        stored_rows = db_runs.list_by_project(project_id, fetch_limit)
        total = db_runs.count_by_project(project_id)

    live_ids = {item["run_id"] for item in live_items}
    stored_ids = {row["run_id"] for row in stored_rows}
    stored_items = [_run_list_item_stored(row) for row in stored_rows if row["run_id"] not in live_ids]
    merged = live_items + stored_items
    merged.sort(key=lambda item: (item.get("started_at") or "", item["run_id"]), reverse=True)
    # A live run has no stored row yet (it has not finished) — it inflates `total`
    # by exactly one over what the table can currently count.
    unstored_live = sum(1 for item in live_items if item["run_id"] not in stored_ids)
    total = total + unstored_live
    items = merged[:limit_value]
    has_more = total > len(items)

    result = {"ok": True, "limit": limit_value, "total": total, "has_more": has_more, "items": items}
    if group_id is not None:
        result["group_id"] = group_id
    else:
        result["project"] = project_id
    return result


def get_active_status(group_id: str) -> dict:
    """Return the live run for a group, if any, without exposing token/process state."""
    run = _svc()._active_run_for_group(group_id)
    if run is None:
        return {"ok": True, "active": False, "group_id": group_id}
    return {
        **get_status(run["run_id"]),
        "active": True,
        "group_id": group_id,
        "doc_ref": run["doc_ref"],
    }
