"""Continuous chain control (0501 NR0003 §12/§18 `chain.py`).

status / cancel / pause / resume, the process-wide active-run and paused-chain lists,
the per-hop re-spawn that gives every hop its own provider resolution, and the durable
handoff bookkeeping that survives a restart (the hop_handoff stop row -- FlowGate is a
time machine, so the next hop's intent may not live only in a process dict).

NR0003 §18's caution is respected in the import direction: this module imports
`admission`, `diagnostics`, `finalize` and `review`, and none of those four imports it
back -- the handful of handoff primitives `review` needs in the other direction go
through the `ai_invoke_service` seam instead (as does `worker`'s post-hop auto-resume
trigger), which is also where the existing tests patch them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.db import questions as db_questions
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import process_runner
from modules.flow_gate.settings import ai_settings_service

from . import admission
from . import diagnostics
from . import finalize
from . import review
from .runtime import (
    HOP_HANDOFF_FAILED_STOP_CODE,
    HOP_HANDOFF_GRACE_SEC,
    HOP_HANDOFF_INTERRUPTED_STOP_CODE,
    HOP_HANDOFF_STOP_CODE,
    PARK_NOTIFY_STOP_CODES,
    PROVIDER_UNAVAILABLE_CODE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    REVIEW_HOP_KIND,
    REVIEW_NO_VERDICT_STOP_CODE,
    REWORK_HOP_KIND,
    _auto_resume_lock,
    _http_error,
    _runs_lock,
    _svc,
    excerpt,
    logger,
)
from .runtime import group_resume_lock as _group_resume_lock


# ── Status / cancel (P0005 scenarios 6, 8) ───────────────────────────────────
#
# `get_status` itself lives on `diagnostics.py` (NR0003 §12: it is a read-only
# projection over a run, exactly diagnostics' charter) -- this module reaches it as
# `diagnostics.get_status`, the same one-way edge it already had for
# `diagnostics._run_detail_from_row`.

def cancel_run(run_id: str) -> dict:
    run = _svc().get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["status"] == "finished":
        # Cancel raced the natural finish — idempotent OK, no kill (L0006 §5).
        return {"ok": True, "run_id": run_id, "status": "finished"}
    run["status"] = "cancelling"
    run["cancel_event"].set()
    proc = run.get("proc")
    if proc is not None:
        try:
            process_runner.kill_process_tree(proc)
        except Exception:
            logger.warning("ai-invoke cancel kill failed for %s", run_id, exc_info=True)
    return {"ok": True, "run_id": run_id, "status": "cancelling"}


# ── Pause / resume / global active list (group 0252 D0007·P0008·L0009) ──────

def pause_run(run_id: str, user_id: str) -> dict:
    """Accept a user pause for a continuous run (L0009 §2.1).

    The run is NOT interrupted: the in-flight step runs to completion and the inbox
    self-chain withholds the next token at the step boundary (P0008 S4). The paused
    row is persisted immediately so a server restart cannot lose the user's intent
    (D0007 decision 2). Repeat pause is idempotent (upsert).
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    run = _svc().get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["mode"] != "continuous":
        raise _http_error(422, "pause_not_supported",
                          "Single-mode runs cannot be paused. Use cancel instead.",
                          run_id=run_id)
    if run["status"] == "finished":
        raise _http_error(409, "run_already_finished", "The run has already finished.",
                          run_id=run_id)
    run["pause_requested"] = True
    docs_reached = 0
    try:
        docs_reached = len(_svc()._oracle_new_docs(run))
    except Exception:
        logger.warning("ai-invoke pause oracle query failed for %s", run_id, exc_info=True)
    try:
        db_paused.upsert(
            group_id=run["group_id"],
            doc_ref=run["doc_ref"],
            paused_by=user_id,
            paused_at=now_iso(),
            continuation_target_seq=run.get("continuation_target_seq"),
            docs_target=run.get("docs_target"),
            docs_reached=docs_reached,
            chain_id=run.get("chain_id"),
            chain_docs_target=run.get("chain_docs_target"),
            chain_docs_reached=(
                int(run.get("chain_docs_reached") or 0)
                + (0 if run.get("chain_docs_accounted") else docs_reached)
            ),
            # A user pause belongs to one concrete continuous run. Persisting that identity
            # prevents a stale group-level row from tagging an unrelated single run.
            stop_kind="user",
            stop_run_id=run_id,
            # 0365 B0001: the provider / handoff-note selections live only on the run object
            # (session-scoped by design). The pause is where that memory ends, so they go
            # into the row here — otherwise resume_chain has nothing to restore and falls
            # back to the project default chain's first entry (NR0003 §2-2).
            continuation_base_provider_id=run.get("continuation_base_provider_id"),
            continuation_provider_pinned=run.get("continuation_provider_pinned"),
            continuation_provider_overrides=run.get("continuation_provider_overrides"),
            continuation_default_note=run.get("continuation_default_note"),
            continuation_note_overrides=run.get("continuation_note_overrides"),
            # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq auto-approve
            # selection must survive the pause the same way the provider/note selections
            # do — this is the root fix for the pause->resume mode-loss bug (resume_chain
            # used to hard-code "auto_approved" because this row never carried anything else).
            continuation_instruction_mode=run.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            # flowgate.default.0400 M0005: same reasoning as the provider/note selections above
            # — this row is where the run object's memory ends, so the per-hop budget pick must
            # be written here or resume_chain has nothing to restore it from.
            continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
            # 0443 T0002 (R0001): the "재시작 횟수" pick is exactly as perishable — write it
            # here too or a resumed chain silently falls back to the default retry count.
            continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
            # 0414 P0007 [정상] 일시정지→재개 왕복: the pause is where the run object's memory
            # of the [검수] selection ends, so this row is the only thing that can hand it
            # back to resume_chain.
            continuation_review_count_overrides=run.get("continuation_review_count_overrides"),
            continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
        )
    except Exception:
        logger.warning("ai-invoke paused-row upsert failed for %s", run_id, exc_info=True)
    return {
        "ok": True,
        "run_id": run_id,
        "group_id": run["group_id"],
        "status": "pause_requested",
        "effective_at": "step_boundary",
    }


def mark_user_paused(group_id: str, run_id: Optional[str]) -> bool:
    """Tag only the continuous run that owns the persisted user-pause row.

    Group identity alone is not enough: a stale system stop can coexist with a later
    single review in the same group. Returning the decision lets boundary callers avoid
    withholding a token unless the row and live run identities agree.
    """
    if not run_id:
        return False
    run = _svc()._active_run_for_group(group_id)
    if (
        run is None
        or run.get("run_id") != run_id
        or run.get("mode") != "continuous"
        or run.get("status") == "finished"
        or not run.get("pause_requested")
    ):
        return False
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        row = db_paused.get_by_group(group_id)
    except Exception:
        logger.warning("user-pause identity lookup failed for %s", group_id, exc_info=True)
        return False
    if (
        row is None
        or (row.get("stop_kind") or "user") != "user"
        or row.get("stop_run_id") != run_id
    ):
        return False
    run["user_paused"] = True
    return True


# ── Per-hop re-spawn for unmanned continuous chains (0317 TR0011 / Q153 opt-1) ────────

def has_active_run(group_id: Optional[str]) -> bool:
    """True when an engine-driven run is live for this group. The inbox self-chain uses this
    to separate an unmanned ENGINE chain (which re-spawns a worker — and re-resolves the
    provider — per hop) from a copy-mention semi-manned chain (no engine worker to re-spawn,
    so it must keep next_token self-continuation)."""
    if not group_id:
        return False
    return _svc()._active_run_for_group(group_id) is not None


def request_auto_resume(group_id: Optional[str], payload: dict) -> None:
    """Queue the next hop of an unmanned continuous chain for a fresh worker. Called by the
    inbox self-chain at a step boundary INSTEAD of handing next_token to the still-running
    worker; consumed by _maybe_auto_resume_hop when the current hop's worker settles.

    0406 T0022 item 4: the same intent is also written to the DB. The in-memory dict does
    not survive a restart, and the abnormal-exit branch pops and discards it — either way
    "what was going to happen next" quietly disappeared. Rather than adding a new store
    this reuses ai_invoke_paused_chains: it already has every column needed to revive one
    chain, and resume_chain restarts the chain from that single row.
    """
    if not group_id:
        return
    with _auto_resume_lock:
        _svc()._auto_resume[group_id] = dict(payload)
    _svc()._write_handoff_row(group_id, payload, _svc()._active_run_for_group(group_id))


def _carry(pending: dict, pending_key: str, run: dict, run_key: str):
    """The queued value when the queue has one, otherwise the run's (0414 L0008 §2.9).

    `is not None` rather than truthiness on purpose: chain_docs_reached=0 and
    provider_pinned=False are real values, not "unset".
    """
    value = pending.get(pending_key)
    return value if value is not None else run.get(run_key)


def _handoff_bundle(pending: dict, run: Optional[dict]) -> dict:
    """The queued intent plus this hop's session picks = one set that revives the next hop.

    The payload inbox enqueues has no provider pin, handoff note, or hop budget: those
    ride the run rather than the token, hop to hop. The durable row is only meaningful
    when both halves are joined — storing only half drops a resume back to the project
    default provider and an empty note (the defect 0365 hit).
    """
    run = run or {}
    return {
        "doc_ref": pending.get("doc_ref") or run.get("doc_ref"),
        "target_seq": pending.get("target_seq"),
        "review_mode": bool(pending.get("review_mode")),
        "instruction_mode": (
            pending.get("instruction_mode") or run.get("continuation_instruction_mode")
        ),
        "auto_approve_item_seqs": (
            pending.get("auto_approve_item_seqs")
            if pending.get("auto_approve_item_seqs") is not None
            else run.get("continuation_auto_approve_item_seqs")
        ),
        "locale": pending.get("locale") or run.get("continuation_locale") or "ko",
        "issued_to": pending.get("issued_to") or run.get("issued_to"),
        "api_base_url": pending.get("api_base_url") or run.get("api_base_url"),
        # 0414: the queued value wins when it has one. The inbox's payload never carries
        # these (they ride the run), so a plain inbox handoff still reads them off the run
        # exactly as before — but a gate bundle DOES carry them, and it must not be
        # overwritten by a review/rework hop's run, which is mode="single" and holds None
        # for every continuous-only field. That overwrite is precisely how a chain reviews
        # its first step and then silently stops reviewing.
        "provider_overrides": _carry(pending, "provider_overrides",
                                     run, "continuation_provider_overrides"),
        "base_provider_id": _carry(pending, "base_provider_id",
                                   run, "continuation_base_provider_id"),
        "provider_pinned": _carry(pending, "provider_pinned",
                                  run, "continuation_provider_pinned"),
        "note_overrides": _carry(pending, "note_overrides",
                                 run, "continuation_note_overrides"),
        "default_note": _carry(pending, "default_note", run, "continuation_default_note"),
        "step_timeout_sec": _carry(pending, "step_timeout_sec",
                                   run, "continuation_step_timeout_sec"),
        # flowgate.default.0443 T0002 (R0001): the "재시작 횟수" pick carries forward
        # the same way the budget pick above does — dropped here, a re-spawned hop
        # silently falls back to RESTART_MAX_ATTEMPTS_DEFAULT.
        "restart_max_attempts": _carry(pending, "restart_max_attempts",
                                       run, "continuation_restart_max_attempts"),
        # 0414 P0007 전달 지점 3·4: the two [검수] maps join the durable bundle. DB0009 §5-3
        # calls omitting them the worst of the I3 violations — a lost provider means a
        # pricier resume, a lost review selection means "approved with nobody reviewing it".
        "review_count_overrides": _carry(pending, "review_count_overrides",
                                         run, "continuation_review_count_overrides"),
        "reviewer_overrides": _carry(pending, "reviewer_overrides",
                                     run, "continuation_reviewer_overrides"),
        "chain_id": _carry(pending, "chain_id", run, "chain_id"),
        "chain_docs_target": _carry(pending, "chain_docs_target", run, "chain_docs_target"),
        "chain_docs_reached": _carry(pending, "chain_docs_reached", run, "chain_docs_reached"),
        "stop_run_id": run.get("run_id"),
    }


def _write_handoff_row(
    group_id: Optional[str],
    pending: dict,
    run: Optional[dict],
    *,
    stop_code: str = HOP_HANDOFF_STOP_CODE,
) -> None:
    """Record the handoff intent as a system stop row (0406 T0022 item 4).

    Upholds invariant I3: this upsert overwrites every column, so omitting even one drops
    a resume to the default provider / empty note. A stop row a person created
    (stop_kind='user') is left alone: a user pause outranks a system row.
    Best effort — a failed write must never kill a chain that is running.
    """
    if not group_id:
        return
    bundle = _handoff_bundle(pending, run)
    if not bundle.get("doc_ref") or not bundle.get("issued_to"):
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        existing = db_paused.get_by_group(group_id)
        if existing is not None and (existing.get("stop_kind") or "user") != "system":
            return
        stamp = now_iso()
        db_paused.upsert(
            group_id=group_id,
            doc_ref=bundle["doc_ref"],
            paused_by=bundle["issued_to"],
            paused_at=stamp,
            continuation_target_seq=bundle.get("target_seq"),
            docs_target=(run or {}).get("docs_target"),
            docs_reached=int((run or {}).get("docs_reached") or 0),
            chain_id=bundle.get("chain_id"),
            chain_docs_target=bundle.get("chain_docs_target"),
            chain_docs_reached=int(bundle.get("chain_docs_reached") or 0),
            stop_kind="system",
            stop_code=stop_code,
            stop_run_id=bundle.get("stop_run_id"),
            stop_last_message_excerpt=(
                review._review_no_verdict_excerpt(run) if stop_code == REVIEW_NO_VERDICT_STOP_CODE
                else excerpt((run or {}).get("last_message"))
            ),
            continuation_base_provider_id=bundle.get("base_provider_id"),
            continuation_provider_pinned=bundle.get("provider_pinned"),
            continuation_provider_overrides=bundle.get("provider_overrides"),
            continuation_default_note=bundle.get("default_note"),
            continuation_note_overrides=bundle.get("note_overrides"),
            continuation_instruction_mode=bundle.get("instruction_mode"),
            continuation_auto_approve_item_seqs=bundle.get("auto_approve_item_seqs"),
            continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
            continuation_restart_max_attempts=bundle.get("restart_max_attempts"),
            # 0414 DB0009 W2/W4: written on EVERY handoff settlement and every system park.
            # These two run on every hop, so a miss here loses the selection from the second
            # hop onward — the quietest possible way to break invariant R1.
            continuation_review_count_overrides=bundle.get("review_count_overrides"),
            continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        )
    except Exception:  # noqa: BLE001 — the record is an aid, not a precondition
        logger.warning("ai-invoke handoff row write failed for %s", group_id, exc_info=True)


def _clear_handoff_row(group_id: Optional[str], stop_run_id: Optional[str]) -> None:
    """Call only after the follow-up hop has actually started (0406 T0022 item 4).

    delete_system_stop removes only the **system** row for that stop_run_id — a pause a
    person pressed meanwhile, or a newer stop row, survives.
    """
    if not group_id or not stop_run_id:
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        db_paused.delete_system_stop(group_id, stop_run_id)
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff row cleanup failed for %s", group_id, exc_info=True)


def _resettle_stop_after_park(run: dict, stop_code: str) -> None:
    """Decide the run's stop a SECOND time, because the gate ran after finalize (0458 T0007 §2.1).

    The order is the whole problem. `_finalize_run` goes first and, seeing a queued next hop,
    closes the run out as `hop_handoff` — "this hop produced its document; the next hop starts
    in a new worker". It writes the `ai_invoke_runs` row, fires the finished payload the
    miniplayer keeps, and settles the failure notification. ONLY THEN does
    `_maybe_auto_resume_hop` reach the review gate, and only there can the auto-approval or the
    advance actually fail. Parking a durable row with `stop_code=approve_failed` while those
    three surfaces still say "handoff" is precisely the diagnostic loss 0003-NR §11-1 reported:
    the stored run claimed the chain moved on, and the real exception — sitting in
    `review_reject_detail` since `_settle_gate_pass` — reached no one.

    So the stop is re-derived from the SAME two functions finalize used (`is_resumable`,
    `_stop_reason_text`, which now reads that detail) and the same surfaces are refreshed: the
    persisted record, the finished event, the human notification. Consistency, not a second
    opinion — one stop code, one sentence, everywhere.

    A park that does not change the verdict returns having touched nothing, which is what keeps
    the record write, the finished event and the notification exactly-once for the ordinary
    case (a run that already ended on this very code — a cancel, an inbox-tagged stop — and is
    only being parked so the chain stays resumable).
    """
    if not run or not run.get("run_id"):
        return
    if run.get("status") != "finished":
        # Not finalized yet, so nothing has been written to correct: whatever _finalize_run
        # computes next is the first and only verdict. No park path reaches here today.
        return
    before_code = run.get("stop_code")
    before_reason = run.get("stop_reason")
    run["stop_code"] = stop_code
    run["resumable"] = finalize.is_resumable(stop_code)
    run["stop_reason"] = finalize._stop_reason_text(stop_code, run)
    if run["stop_code"] == before_code and run["stop_reason"] == before_reason:
        return
    _svc()._persist_run_record(run)
    # The engine speaks for this stop: approve_failed / advance_blocked belong to the inbox's
    # set, but the inbox never saw this one — the gate settled the step with no request in
    # flight. `error_text` is the §4.3 sentence rather than the attempts-and-last-message
    # default, because for these two codes the exception IS the news.
    finalize._notify_chain_failure_if_needed(
        run,
        notify_codes=PARK_NOTIFY_STOP_CODES,
        error_text=run.get("stop_reason"),
    )
    # Same pair, same order as _finalize_run: the record is durable before the browser is told
    # to re-read. The card is holding a `hop_handoff` payload right now and is waiting for the
    # successor hop that is never coming; this is what replaces it with the real stop.
    _svc()._broadcast(run, "ai_invoke_finished", _svc().finished_payload(run))
    _svc()._broadcast(run, "group_view_refresh", {
        "group_id": run["group_id"],
        "reason": "ai_invoke_finished",
    })


def _park_handoff(run: dict, pending: dict, stop_code: str) -> None:
    """Terminus of every branch that decides not to spawn the next hop (0406 T0022 item 4).

    Three things must happen.
      1. Leave a durable row, distinguishing the reason via stop_code, so the user can
         resume the chain from the same place. No branch disappears silently.
      2. Correct the run's OWN stop_code and, ONLY when the reason is
         `REVIEW_NO_VERDICT_STOP_CODE`, fire the failure signal (T0007 §3.2.5 — that
         section is entirely about review_no_verdict; nothing in T0007 asks for the other
         six REVIEW_STOP_CODES to speak here). `_finalize_run` could only tag this hop
         `hop_handoff` — a gate bundle was already queued before it even spawned (L0008
         §2.4 "queue first, then launch"), so `respawn_pending` was true and the real
         reason (e.g. `review_no_verdict`) was not knowable yet. `_notify_chain_failure_
         if_needed` re-reads `run["stop_code"]` to decide whether to speak at all, so
         without this correction a review hop that exhausts its retries here writes a
         paused row but never raises the one required 🔔 failure signal. The check is
         NOT redundant with that function's own `stop_code in ENGINE_NOTIFY_STOP_CODES`
         gate: all seven REVIEW_STOP_CODES are members of that set (line ~199), so
         calling unconditionally here would also notify on `review_verdict_hold` /
         `review_stalled` / `review_reject_denied` / `review_reject_failed` — none of
         which T0007 asked for, and the first of which is "waiting on a human answer,
         not a failure" by the exact same reasoning `question_pending` is kept silent
         (see the comment above `_notify_chain_failure_if_needed`). Every OTHER caller of
         this function already passes a stop_code (`hop_handoff_failed`, `cancelled`,
         `approve_denied`, ...) that isn't in `ENGINE_NOTIFY_STOP_CODES`, or a mode that
         isn't continuous/scope-oracle, so this stays a no-op for them regardless.
      3. Release the lease the handoff switched to releasing. _finalize_run only calls
         begin_handoff and skips release when it sees a queue, so without this the
         group's next run is blocked until the lease expires. release only deletes rows
         whose run_id matches, so calling it twice is safe (restart reclaim included).
    """
    group_id = run.get("group_id")
    run["stop_code"] = stop_code
    run["resumable"] = finalize.is_resumable(stop_code)
    _svc()._write_handoff_row(group_id, pending, run, stop_code=stop_code)
    # 0458 T0007 §2.1: the durable row is only ONE of the surfaces this stop has to reach.
    # The run itself was closed out before the gate ever ran, so re-decide it here too.
    _resettle_stop_after_park(run, stop_code)
    if not group_id:
        return
    try:
        db_group_ai_leases.release(group_id, run["run_id"])
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff lease release failed for %s", group_id, exc_info=True)


def startup_recover_handoffs() -> int:
    """Turn handoffs whose in-memory queue was lost to a restart into an explicit
    awaiting-resume state (0406 T0022 item 4).

    At startup this process's ``_auto_resume`` is empty, so every ``hop_handoff`` row
    still in the table means "the process died just as it was about to spawn the next
    hop". The row is NOT deleted — that would be losing the intent. Only stop_code
    changes, taking it out of the grace check and making it a card the user can resume.
    """
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        rows = [
            row for row in db_paused.list_all_system_stops()
            if (row.get("stop_code") or "") == HOP_HANDOFF_STOP_CODE
        ]
        for row in rows:
            db_paused.mark_stop_code(
                row["group_id"], HOP_HANDOFF_INTERRUPTED_STOP_CODE,
                stop_run_id=row.get("stop_run_id"),
            )
        if rows:
            logger.warning(
                "[ai_invoke] startup recovered %d interrupted hop handoff(s)", len(rows)
            )
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff startup recovery failed", exc_info=True)
        return 0


def peek_auto_resume(group_id: Optional[str]) -> Optional[dict]:
    """The queued next hop for this group, WITHOUT consuming it (used by the settle judge to
    recognize a hop that handed the chain off)."""
    if not group_id:
        return None
    with _auto_resume_lock:
        return _svc()._auto_resume.get(group_id)


def pop_auto_resume(group_id: Optional[str]) -> Optional[dict]:
    if not group_id:
        return None
    with _auto_resume_lock:
        return _svc()._auto_resume.pop(group_id, None)


def clear_auto_resume(group_id: Optional[str]) -> None:
    if not group_id:
        return
    with _auto_resume_lock:
        _svc()._auto_resume.pop(group_id, None)


def _maybe_auto_resume_hop(run: dict) -> None:
    """After a hop's worker finalizes, re-spawn the next hop if the inbox self-chain queued
    one (Q153 opt-1). Server-triggered automation of resume_chain: the next start_run
    re-resolves the hop's provider, delivering true per-step providers on an unmanned chain.
    Any real stop (cancel / timeout / provider exhaustion / crash) drops the queued hop rather
    than continuing past it."""
    group_id = run.get("group_id")
    pending = pop_auto_resume(group_id)
    if pending is None:
        return
    # 0406 T0022 item 4: the two branches below used to pop the queue and throw it away.
    # The queue is still dropped — a hop that ended abnormally must not auto-continue —
    # but the **intent** is kept as a durable row, and the releasing lease is released.
    cancel_event = run.get("cancel_event")
    cancelled = cancel_event is not None and cancel_event.is_set()
    if run.get("end_reason") != "exited" or cancelled:
        parked_code = run.get("stop_code")
        if not parked_code or parked_code == HOP_HANDOFF_STOP_CODE:
            parked_code = "cancelled" if cancelled else HOP_HANDOFF_FAILED_STOP_CODE
        _svc()._park_handoff(run, pending, parked_code)
        return
    # Carry the session override map AND the header default pin forward so the re-spawned hop
    # applies them too (neither is persisted on a token — both ride the run, hop to hop). The
    # base pin is what an override-less step resolves to (0317 T0013 결함 ③).
    # 0414: _carry, not a bare overwrite. A review/rework hop is mode="single", so its run
    # holds None for every continuous-only field — clobbering the queued bundle with those
    # would drop the provider pin, the notes, the budget AND the review selection the moment
    # the chain ran its first review hop.
    pending = {
        **pending,
        "provider_overrides": _carry(pending, "provider_overrides",
                                     run, "continuation_provider_overrides"),
        "base_provider_id": _carry(pending, "base_provider_id",
                                   run, "continuation_base_provider_id"),
        "provider_pinned": _carry(pending, "provider_pinned",
                                  run, "continuation_provider_pinned"),
        # 0346 T0005: carry the [전달멘트] note bundle forward the same way — the first-hop-only
        # gap is the exact regression shape this fix is guarding against (D0004 구현 시 반드시
        # 지켜야 할 제약 4).
        "note_overrides": _carry(pending, "note_overrides",
                                 run, "continuation_note_overrides"),
        "default_note": _carry(pending, "default_note", run, "continuation_default_note"),
        # flowgate.default.0400 M0005: the per-hop budget pick carries forward the same way —
        # dropping it here would silently reset a re-spawned hop back to HOP_TIMEOUT_SEC.
        "step_timeout_sec": _carry(pending, "step_timeout_sec",
                                   run, "continuation_step_timeout_sec"),
        # flowgate.default.0443 T0002 (R0001): the "재시작 횟수" pick carries forward
        # the same way the budget pick above does — dropped here, a re-spawned hop
        # silently falls back to RESTART_MAX_ATTEMPTS_DEFAULT.
        "restart_max_attempts": _carry(pending, "restart_max_attempts",
                                       run, "continuation_restart_max_attempts"),
        # 0414 P0007 전달 지점 5 / L0008 §2.9 지점 10.
        "review_count_overrides": _carry(pending, "review_count_overrides",
                                         run, "continuation_review_count_overrides"),
        "reviewer_overrides": _carry(pending, "reviewer_overrides",
                                     run, "continuation_reviewer_overrides"),
        # 0357 T0004: the chain identity and its lifetime counters, so the next hop keeps
        # counting the CHAIN's progress instead of restarting at 0/1 in the miniplayer.
        "chain_id": _carry(pending, "chain_id", run, "chain_id"),
        "chain_docs_target": _carry(pending, "chain_docs_target", run, "chain_docs_target"),
        "chain_docs_reached": _carry(pending, "chain_docs_reached", run, "chain_docs_reached"),
        # The stop code this hop ended on, so the gate can tell "waiting on a human answer"
        # apart from "the hop left nothing" without re-querying (L0008 §2.3).
        "last_stop_code": run.get("stop_code"),
        # 0352 T0004 §3.5: the ai_direct per-item_seq auto-approve selection rides forward
        # the same way instruction_mode does, hop to hop — dropping it here would silently
        # revert a re-spawned hop to "select nothing", handing the AI an N/T the user picked
        # for server auto-handling.
        "auto_approve_item_seqs": run.get("continuation_auto_approve_item_seqs"),
    }
    # Rewrite the durable row as a complete set as of now. At request_auto_resume time only
    # inbox's half was there (document, target, mode); the provider pin, handoff note and
    # hop budget ride the run, so only here is the set complete — invariant I3.
    _svc()._write_handoff_row(group_id, pending, run)
    try:
        # 0414 L0008 §2.1 진입점 2: the gate decides what the next hop IS — review, rework,
        # approve-and-continue, or stop. With no review selection it resolves to "work" and
        # calls the same _spawn_auto_resume this line used to call directly.
        started = review.run_review_gate(group_id, pending, run)
    except HTTPException as exc:
        logger.warning("ai-invoke auto-resume rejected for %s: %s",
                       group_id, getattr(exc, "detail", exc))
        _svc()._park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    except Exception:
        logger.exception("ai-invoke auto-resume failed for %s", group_id)
        _svc()._park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    if not started:
        return          # the gate parked the chain; its durable row IS the resume card
    # The follow-up hop actually started. **Only now** is the intent cleared.
    _svc()._clear_handoff_row(group_id, run.get("run_id"))


def _spawn_auto_resume(group_id: str, pending: dict) -> None:
    """Advance the workflow one step and launch a fresh worker for it — the same
    advance_workflow → start_run handoff resume_chain uses, minus the user-pause row. The
    just-completed step was auto-approved by the self-chain, so advance_workflow resolves the
    NEXT head here, and start_run re-resolves that head's provider."""
    from modules.flow_gate.services import workflow_decision_service

    doc_ref = pending["doc_ref"]
    target_seq = pending["target_seq"]
    review_mode = bool(pending.get("review_mode"))
    instruction_mode = pending.get("instruction_mode")
    locale = pending.get("locale") or "ko"
    issued_to = pending["issued_to"]
    api_base_url = pending["api_base_url"]
    overrides = pending.get("provider_overrides")
    base_provider_id = pending.get("base_provider_id")
    provider_pinned = bool(pending.get("provider_pinned"))
    note_overrides = pending.get("note_overrides")
    default_note = pending.get("default_note")
    step_timeout_sec = pending.get("step_timeout_sec")
    restart_max_attempts = pending.get("restart_max_attempts")
    chain_id = pending.get("chain_id")
    chain_docs_target = pending.get("chain_docs_target")
    chain_docs_reached = pending.get("chain_docs_reached")
    auto_approve_item_seqs = pending.get("auto_approve_item_seqs")
    # 0414 P0007 전달 지점 6: the next WORK hop re-reads the same two maps and resolves them
    # against ITS own worker item_seq.
    review_count_overrides = pending.get("review_count_overrides")
    reviewer_overrides = pending.get("reviewer_overrides")

    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None

    def _issue_next(ai_run_id: Optional[str] = None) -> dict:
        adv = workflow_decision_service.advance_workflow(
            doc_id=doc_ref,
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            continuous=True,
            continuation_target_seq=target_seq,
            continuation_review_mode=review_mode,
            continuation_instruction_mode=instruction_mode,
            # 0359 L0007 §2.9: stamp the hop's run onto its token. Every continuous token ever
            # issued through this path had an empty ai_run_id (NR0003 §4), so a dead hop's
            # token led nowhere — the incident had to be reconstructed from a scratch file.
            ai_run_id=ai_run_id,
            continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        )
        return {
            "raw_token": adv["token"],
            "token_id": adv["token_id"],
            "scratch_dir": adv["scratch_dir"],
            "mention": adv["mention"],
            # 0406 T0022 item 3 — a re-spawned hop carries the same facts.
            "worker_document_type": adv.get("worker_document_type"),
            "auto_handled_item_seqs": adv.get("auto_handled_item_seqs") or [],
        }

    _svc().start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=doc_ref,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_review_mode=review_mode,
        continuation_instruction_mode=instruction_mode,
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_next,
        provider_id=base_provider_id,
        provider_pinned=provider_pinned,
        continuation_provider_overrides=overrides,
        continuation_note_overrides=note_overrides,
        continuation_default_note=default_note,
        chain_id=chain_id,
        chain_docs_target=chain_docs_target,
        chain_docs_reached=chain_docs_reached,
        continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        continuation_step_timeout_sec=step_timeout_sec,
        continuation_restart_max_attempts=restart_max_attempts,
        continuation_review_count_overrides=review_count_overrides,
        continuation_reviewer_overrides=reviewer_overrides,
    )


def _sequence_completion_state(doc_ref: Optional[str]) -> tuple[bool, Optional[int]]:
    """``(a sequence was read, first incomplete item_seq)`` — 0459 T0005 §2-3.

    ``_next_incomplete_item_seq`` collapses two very different facts into one ``None``:
    "every slot is done" and "there is no sequence to look at". Resuming may treat both as
    "nothing to resume", but DELETING a stopped chain's card may not — a missing or
    unreadable sequence is no evidence at all. So the two facts are separated here and the
    old name keeps its single-value contract on top.

    Raises whatever the DB layer raises; callers that must not fail decide what an
    unreadable sequence means to them.
    """
    if not doc_ref:
        return False, None
    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is None:
        return False, None
    items = db_wfseq.get_sequence_items(seq["id"]) or []
    if not items:
        return False, None          # a sequence with no slots proves nothing was finished
    for item in sorted(items, key=lambda i: i.get("item_seq") or 0):
        if item.get("result_doc_id") is None or item.get("result_doc_review_status") != "approved":
            return True, item.get("item_seq")
    return True, None


def _next_incomplete_item_seq(doc_ref: str) -> Optional[int]:
    """First workflow slot that is not complete (L0009 §2.3). Completion uses the
    existing slot definition — result document exists AND is approved — exactly as
    ai_invoke_routes._continuation_target_error judges it."""
    return _sequence_completion_state(doc_ref)[1]


def _group_workflow_finished(group_id: Optional[str]) -> bool:
    """Did this whole group already reach final approval? (0459 T0005 §2-2)

    The same DB reading git_service's finalize state and the dashboard's terminal-group
    filter use — R/B root at ``wf_done`` — reached through the public db.documents helper
    rather than another service's private function. Never raises: a probe that cannot read
    the table answers "not finished", which preserves the row.
    """
    if not group_id:
        return False
    try:
        return bool(db_docs.group_root_wf_done(group_id))
    except Exception:  # noqa: BLE001
        logger.warning("group wf_done probe failed for %s", group_id, exc_info=True)
        return False


def _system_pause_row_is_stale(row: dict) -> bool:
    """Whether a system stop still describes work this group actually owes.

    Judged in this order, most conclusive first (0459 T0005 §2):

      1. the group's R/B workflow root is ``wf_done`` — the group is over, so nothing it
         parked can still be waiting on anybody;
      2. a sequence WAS read and it has no incomplete slot left, or its next incomplete
         slot is past the row's own ``continuation_target_seq`` — the stored scope is
         finished, the same reading ``resume_chain`` calls ``nothing_to_resume``;
      3. only if neither piece of completion evidence exists, the original head test:
         the stop's ``hop_item_seq`` against the current next-incomplete slot.

    Step 3 is why steps 1 and 2 had to come first. Review and rework hops run
    ``mode="single"``, and only continuous runs record a ``hop_item_seq`` — so every card
    a review hop parked reached ``stopped_seq is None`` and returned "not stale" forever,
    which is 0459 NR0003's first defect (the 0457 ``approve_failed`` card).

    Scoped and conservative throughout: only ``stop_kind='system'`` rows carrying a
    ``stop_run_id`` are judged at all, a user pause is never touched, and an absent or
    unreadable sequence is NOT "everything finished" — it is no evidence, so the row is
    kept and a warning is logged. Never raises.
    """
    if (row.get("stop_kind") or "user") != "system" or not row.get("stop_run_id"):
        return False
    group_id = row.get("group_id") or ""

    # 1 — the whole group is finished.
    if _group_workflow_finished(group_id):
        return True

    # 2 — the stored scope is finished.
    try:
        sequence_read, next_seq = _sequence_completion_state(row.get("doc_ref"))
    except Exception:  # noqa: BLE001
        logger.warning(
            "system paused-row sequence lookup failed for %s",
            group_id, exc_info=True,
        )
        sequence_read, next_seq = False, None
    if sequence_read:
        if next_seq is None:
            return True
        target_seq = row.get("continuation_target_seq")
        if target_seq is not None:
            try:
                if int(next_seq) > int(target_seq):
                    return True
            except (TypeError, ValueError):
                pass
    else:
        # Neither wf_done nor a readable sequence: keep the card. A chain whose sequence
        # cannot be read is exactly the one a person still has to look at.
        logger.warning(
            "system paused-row kept for %s: no wf_done and no readable workflow sequence "
            "for %s", group_id, row.get("doc_ref"),
        )
        return False

    # 3 — the original head test, on the evidence it was written for.
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        stopped = db_runs.get(row["stop_run_id"])
        stopped_seq = (stopped or {}).get("hop_item_seq")
    except Exception:  # noqa: BLE001
        logger.warning(
            "system paused-row staleness lookup failed for %s",
            group_id, exc_info=True,
        )
        return False
    if stopped_seq is None:
        # A single-hop stop with no completion evidence above: real outstanding work,
        # and nothing here can tell which slot it was. Missing evidence is not stale.
        return False
    try:
        return int(next_seq) != int(stopped_seq)
    except (TypeError, ValueError):
        return False


def _resumable_base_provider(project_id: str, provider_id: Optional[str]) -> Optional[str]:
    """The stored header pin, but only while it is still usable (0365 DB0004 §5-2).

    Called ONLY for an UNPINNED resume (resume_chain passes the pinned id straight
    through to start_run instead, so start_run's 422 provider_unavailable stays the
    sole authority over a pin — T0005 §3 item 1). start_run rejects an explicit pin
    that is not in the project's enabled chain with a 422; for an unpinned resume there
    is no such guard, so a stored provider that was deleted or switched off degrades to
    "no pin" here — the resume then follows the normal doc-type assignment →
    default-chain order instead of the paused card becoming un-resumable.
    """
    if not provider_id:
        return None
    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("resume provider re-check failed for %s", project_id, exc_info=True)
        return None
    if any(p.get("id") == provider_id for p in chain):
        return provider_id
    logger.info(
        "paused chain %s pinned provider %s is no longer enabled — resuming unpinned",
        project_id, provider_id,
    )
    return None


def _paused_row_resume_state(
    project_id: str, row: dict, *, include_target: bool = False,
) -> dict:
    """Evaluate deterministic pause->resume admission without changing state.

    active-all and resume_chain both call this evaluator. Only read-only facts that are
    stable enough to decide at lookup time belong here: the decided sequence, pending
    worker count, enabled provider chain, and an explicit provider pin. Lease admission,
    worktree repair and advance/start side effects remain launch-time checks.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    def _state(
        available: bool,
        code: Optional[str] = None,
        reason: Optional[str] = None,
        provider_name: Optional[str] = None,
        target_seq: Optional[int] = None,
    ) -> dict:
        result = {
            "resume_available": available,
            "resume_block_code": code,
            "resume_block_reason": reason,
            "resume_provider_name": provider_name,
        }
        if include_target:
            result["_resume_target_seq"] = target_seq
        return result

    try:
        sequence = db_wfseq.get_sequence_for_member_doc(row["doc_ref"])
        items = db_wfseq.get_sequence_items(sequence["id"]) if sequence is not None else []
    except Exception:  # noqa: BLE001 — active-all stays available on transient lookup failure
        logger.warning("resume-state sequence check failed for %s", row.get("group_id"), exc_info=True)
        if include_target:
            raise _http_error(
                500,
                "resume_lookup_failed",
                "Could not read the workflow sequence to resume. Retry later.",
            )
        return _state(True)
    if sequence is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
        )

    target_seq = row.get("continuation_target_seq")
    if target_seq is None:
        target_seq = max(
            (item["item_seq"] for item in items or [] if item.get("item_seq") is not None),
            default=None,
        )
    if target_seq is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
        )

    try:
        docs_target = admission._continuation_docs_target(
            row["doc_ref"],
            int(target_seq),
            continuation_instruction_mode=row.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=db_paused.load_json_list(
                row.get("continuation_auto_approve_item_seqs")
            ),
        )
    except Exception:  # noqa: BLE001 — same fail-open preview / fail-safe resume split
        logger.warning("resume-state worker-step check failed for %s", row.get("group_id"), exc_info=True)
        if include_target:
            raise _http_error(
                500,
                "resume_lookup_failed",
                "Could not read the workflow sequence to resume. Retry later.",
            )
        return _state(True, target_seq=int(target_seq))
    if docs_target is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
            target_seq=int(target_seq),
        )
    if docs_target <= 0:
        return _state(
            False,
            "no_pending_worker_steps",
            f"no pending worker step at or below workflow item_seq {int(target_seq)}",
            target_seq=int(target_seq),
        )

    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("resume-state provider check failed for %s", project_id, exc_info=True)
        return _state(True, target_seq=int(target_seq))
    if not chain:
        return _state(
            False,
            "no_enabled_provider",
            "No enabled AI provider for this project. Configure providers in AI settings.",
            target_seq=int(target_seq),
        )

    provider_id = row.get("continuation_base_provider_id")
    if not bool(row.get("continuation_provider_pinned")) or not provider_id:
        return _state(True, target_seq=int(target_seq))
    active = next((provider for provider in chain if provider.get("id") == provider_id), None)
    if active is not None:
        return _state(True, provider_name=active.get("name"), target_seq=int(target_seq))

    name = None
    try:
        settings_view = ai_settings_service.get_project_settings(project_id, include_catalog=False)
        for provider in settings_view.get("providers") or []:
            if provider.get("id") == provider_id:
                name = provider.get("name")
                break
    except Exception:  # noqa: BLE001 — a missing display name is not a block on its own
        logger.warning("resume-state provider name lookup failed for %s", project_id, exc_info=True)
    return _state(
        False,
        PROVIDER_UNAVAILABLE_CODE,
        PROVIDER_UNAVAILABLE_MESSAGE,
        provider_name=name,
        target_seq=int(target_seq),
    )


def resume_chain(
    *, group_id: str, user_id: str, api_base_url: str, locale: str = "ko",
    is_admin: bool = False,
) -> dict:
    """Resume a user-paused continuous chain from its next incomplete step (L0009 §2.4).

    The ORDER is the overlap protection: (1) group lock → (2) active-run check →
    (3) atomic paused-row consumption → (4) start. A row consumed by another path
    (auto-resume, another session, an external worker) surfaces as resume_conflict,
    never as a second concurrent run.

    0459 T0007 §2: only the user who paused the chain (``paused_by``) or an admin may
    resume it -- a project-read-only teammate sending this group's id must not be able
    to consume or relaunch somebody else's paused chain. The check reads the row and
    the consumption below re-verifies the SAME snapshot via a compare-and-swap
    (0459 TR0008 rev1 fix): a plain group-only delete after the authorization read
    would let a newer row -- upserted by a *different* user between the two calls --
    be consumed by the old owner's already-authorized request. Tying the delete to
    the exact row the authorization check inspected closes that window.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.services import workflow_decision_service

    with _group_resume_lock(group_id):
        active = _svc()._active_run_for_group(group_id)
        if active is not None:
            raise _http_error(409, "run_already_active",
                              "An active run already exists for this group.",
                              run_id=active["run_id"])
        pre_row = db_paused.get_by_group(group_id)
        if pre_row is None:
            raise _http_error(409, "resume_conflict",
                              "This chain was already resumed by another path.",
                              group_id=group_id)
        # 0459 T0007 SS2: ownership is judged BEFORE anything else this row could tell
        # the caller -- a project-read-only teammate sending this group's id must not be
        # able to consume, relaunch, OR preflight somebody else's paused chain.
        if not is_admin and pre_row.get("paused_by") != user_id:
            raise _http_error(403, "paused_chain_forbidden",
                              "Only the user who paused this chain (or an admin) may "
                              "resume it.", group_id=group_id)
        project_id = group_id.split(".", 1)[0]
        preflight = _svc()._paused_row_resume_state(project_id, pre_row, include_target=True)
        if not preflight["resume_available"]:
            raise _http_error(
                422,
                preflight["resume_block_code"],
                preflight["resume_block_reason"],
                group_id=group_id,
            )

        # CAS-consume the exact row the authorization and preflight checks above just
        # read -- not a bare delete_and_return(group_id), which would happily take
        # whatever row is live at THIS instant even if it is not the row that was
        # authorized. A row that changed identity (newer paused_by/paused_at/stop_kind/
        # stop_run_id) between the reads fails the predicate and falls through to
        # resume_conflict below, same as "nothing to consume".
        row = db_paused.release_owned(
            group_id,
            paused_by=pre_row.get("paused_by"),
            paused_at=pre_row.get("paused_at"),
            stop_kind=pre_row.get("stop_kind"),
            stop_run_id=pre_row.get("stop_run_id"),
        )
        # release_owned is tri-state (0459 TR0008 rev2): a bare None (nothing left to
        # consume) and a ReleaseSuperseded (a newer row replaced the one this call was
        # authorized against) both fall through to the SAME resume_conflict here --
        # either way the row this call was authorized for is not the one to relaunch.
        if row is None or isinstance(row, db_paused.ReleaseSuperseded):
            raise _http_error(409, "resume_conflict",
                              "This chain was already resumed by another path.",
                              group_id=group_id)
        target_seq = preflight["_resume_target_seq"]

        def _restore_row() -> None:
            # The resume did not happen — put the consumed row back so the paused
            # card survives and the user can retry (L0009 §5: row preservation).
            try:
                db_paused.upsert(
                    group_id=row["group_id"],
                    doc_ref=row["doc_ref"],
                    paused_by=row["paused_by"],
                    paused_at=row["paused_at"],
                    continuation_target_seq=row.get("continuation_target_seq"),
                    docs_target=row.get("docs_target"),
                    docs_reached=int(row.get("docs_reached") or 0),
                    chain_id=row.get("chain_id"),
                    chain_docs_target=row.get("chain_docs_target"),
                    chain_docs_reached=int(row.get("chain_docs_reached") or 0),
                    stop_kind=row.get("stop_kind") or "user",
                    stop_code=row.get("stop_code"),
                    stop_run_id=row.get("stop_run_id"),
                    stop_last_message_excerpt=row.get("stop_last_message_excerpt"),
                    # Restoring the row means restoring it whole: a retry of this resume
                    # must still find the user's selections (0365 DB0004 §5-3 case 5).
                    continuation_base_provider_id=row.get("continuation_base_provider_id"),
                    continuation_provider_pinned=row.get("continuation_provider_pinned"),
                    continuation_provider_overrides=row.get("continuation_provider_overrides"),
                    continuation_default_note=row.get("continuation_default_note"),
                    continuation_note_overrides=row.get("continuation_note_overrides"),
                    # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq selection are
                    # part of "restoring it whole" too — a failed resume must not silently
                    # drop the chain's ai_direct policy on retry.
                    continuation_instruction_mode=row.get("continuation_instruction_mode"),
                    continuation_auto_approve_item_seqs=row.get("continuation_auto_approve_item_seqs"),
                    continuation_step_timeout_sec=row.get("continuation_step_timeout_sec"),
                    continuation_restart_max_attempts=row.get("continuation_restart_max_attempts"),
                    # 0414: "restore it whole" includes the [검수] selection — a failed resume
                    # must not quietly turn the retry into an unreviewed chain.
                    continuation_review_count_overrides=row.get(
                        "continuation_review_count_overrides"),
                    continuation_reviewer_overrides=row.get("continuation_reviewer_overrides"),
                )
            except Exception:
                logger.warning("paused-row restore failed for %s", group_id, exc_info=True)

        try:
            # Preflight resolved the target before the row was consumed. Re-check only the
            # next item here: if it disappeared in the narrow lookup/delete window, the
            # existing nothing_to_resume self-cleaning contract remains authoritative.
            next_seq = _svc()._next_incomplete_item_seq(row["doc_ref"])
        except Exception:
            _restore_row()
            logger.exception("ai-invoke resume lookup failed for %s", group_id)
            raise _http_error(500, "resume_lookup_failed",
                              "Could not read the workflow sequence to resume. Retry later.")
        if next_seq is None or target_seq is None or int(next_seq) > int(target_seq):
            # Every step at or below the stored target is already complete; the consumed
            # row stays deleted on purpose (self-cleaning, L0009 §2.4).
            raise _http_error(409, "nothing_to_resume",
                              "No remaining workflow step to resume.", group_id=group_id)

        # 0352 T0004 §3.6: the root of the pause->resume mode-loss bug — this row is the
        # ONLY place the chain's N/T authoring policy survives a pause, and until this fix
        # neither the row schema nor this function read it back: _issue_resume never passed
        # continuation_instruction_mode to advance_workflow at all (silently normalizing to
        # auto_approved), and the start_run call below then hard-coded the literal string
        # "auto_approved". An ai_direct chain that got paused resumed as if the user had
        # never chosen ai_direct — the very selection this whole feature exists to keep.
        resume_instruction_mode = workflow_decision_service.normalize_continuation_instruction_mode(
            row.get("continuation_instruction_mode")
        )
        resume_auto_approve_item_seqs = (
            workflow_decision_service.normalize_continuation_auto_approve_item_seqs(
                db_paused.load_json_list(row.get("continuation_auto_approve_item_seqs"))
            )
        )
        # 0414 P0007 [엣지] 재개 시 검수자 소멸: load_json_map degrades corrupt or non-object
        # text to None, so one damaged row loses its selection instead of blocking the resume.
        # A reviewer that has since been switched off is dropped from the map — and ONLY from
        # the map: the review COUNT survives, so that step is still reviewed, by the project
        # default reviewer. A new request would be refused outright (422 reviewer_unavailable);
        # a chain a person parked must never become a card that cannot restart.
        review_count_overrides = db_paused.load_json_map(
            row.get("continuation_review_count_overrides")
        )
        reviewer_overrides = review._resumable_reviewer_overrides(
            project_id, db_paused.load_json_map(row.get("continuation_reviewer_overrides"))
        )
        # Needed by resolve_step_executor if the gate below resolves to a REWORK hop — moved
        # up from the plain "new" path below so both paths read the same resolved values.
        gate_provider_pinned = bool(row.get("continuation_provider_pinned"))
        gate_base_provider_id = (
            row.get("continuation_base_provider_id")
            if gate_provider_pinned
            else _resumable_base_provider(project_id, row.get("continuation_base_provider_id"))
        )
        gate_provider_overrides = db_paused.load_json_map(
            row.get("continuation_provider_overrides")
        )
        # Needed by both the gate-dispatch bundle below and the plain "new" fallback further
        # down — resolved once, here, so the two paths can never drift into two different
        # answers for the same paused row.
        gate_note_overrides = db_paused.load_json_map(row.get("continuation_note_overrides"))
        gate_default_note = (row.get("continuation_default_note") or "").strip() or None
        gate_restart_max_attempts = row.get("continuation_restart_max_attempts")

        # flowgate.default.0466 T0007 §3.3.3/A11: a row parked by REVIEW_NO_VERDICT_STOP_CODE
        # (or a legacy REVIEW_CAP_REACHED_STOP_CODE row that still needs another round) targets
        # a document that is still `pending_review` — `get_effective_head` refuses to advance
        # past it (`head_in_progress`), so the plain "new"-token path below can never resume
        # one. Re-derive the SAME gate `run_review_gate` would and dispatch to the SAME
        # review/rework spawn it uses, cold, exactly the way `resolve_review_gate` already
        # cold-derives everything else — no `last_stage`/`rounds_before` to restore, the DB
        # review-row count IS the round count. A gate that resolves to a plain work step
        # (nothing pending, or count=0) falls through unchanged to the existing "new" path.
        #
        # This bundle is exactly what `run_review_gate`/`_settle_gate_pass`/`_spawn_auto_resume`
        # read from later — a `pass` verdict on the resumed hop settles through THIS SAME
        # dict via the auto-resume queue (§2.9), so every field those callees hard-index
        # (`target_seq`) or silently carry forward (`note_overrides`, `default_note`,
        # `restart_max_attempts`) has to be here now, not just the fields the gate itself
        # reads. Omitting them does not fail loudly at dispatch time — the review/rework hop
        # launches fine — it fails later, either as a `KeyError` in `_spawn_auto_resume` once
        # the verdict lands and the chain tries to advance past this step, or as a silently
        # dropped handoff note / restart budget.
        gate_bundle = {
            "doc_ref": row["doc_ref"],
            "target_seq": target_seq,
            "review_count_overrides": review_count_overrides,
            "reviewer_overrides": reviewer_overrides,
            "locale": locale,
            "issued_to": user_id,
            "api_base_url": api_base_url,
            "instruction_mode": resume_instruction_mode,
            "auto_approve_item_seqs": resume_auto_approve_item_seqs,
            "chain_id": row.get("chain_id"),
            "chain_docs_target": row.get("chain_docs_target"),
            "chain_docs_reached": row.get("chain_docs_reached"),
            "step_timeout_sec": row.get("continuation_step_timeout_sec"),
            "provider_overrides": gate_provider_overrides,
            "base_provider_id": gate_base_provider_id,
            "provider_pinned": gate_provider_pinned,
            "note_overrides": gate_note_overrides,
            "default_note": gate_default_note,
            "restart_max_attempts": gate_restart_max_attempts,
        }
        try:
            gate = review.resolve_review_gate(gate_bundle)
        except Exception:
            _restore_row()
            logger.exception("ai-invoke resume gate lookup failed for %s", group_id)
            raise _http_error(500, "resume_lookup_failed",
                              "Could not read the review gate to resume. Retry later.")
        if gate.get("stage") in (REVIEW_HOP_KIND, REWORK_HOP_KIND):
            queued = {**gate_bundle, "last_stage": gate["stage"]}
            if gate["stage"] == REVIEW_HOP_KIND:
                queued["rounds_before"] = int(gate.get("rounds_used") or 0)
            else:
                queued["revision_before"] = int((gate.get("slot") or {}).get("revision_no") or 0)
            review._queue_gate_bundle(group_id, queued)
            spawn = _svc()._spawn_review_hop if gate["stage"] == REVIEW_HOP_KIND else _svc()._spawn_rework_hop
            try:
                return spawn(group_id, queued, gate)
            except HTTPException as exc:
                _svc().clear_auto_resume(group_id)
                _restore_row()
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if exc.status_code == 409:
                    raise _http_error(
                        409, "resume_launch_failed",
                        str(detail.get("message") or exc.detail or "Resume launch failed."),
                        group_id=group_id, restored=True, resume_stage="gate",
                        cause_code=detail.get("code"),
                    )
                raise
            except Exception:
                _svc().clear_auto_resume(group_id)
                _restore_row()
                logger.exception("ai-invoke resume review-gate spawn failed for %s", group_id)
                raise _http_error(500, "resume_launch_failed",
                                  "Could not relaunch the review gate. Retry later.")

        def _issue_resume(ai_run_id: Optional[str] = None) -> dict:
            # Same advance_workflow path as the continuous first hop / every inbox
            # self-chain hop, so instruction heads (N/T) keep their server-side
            # auto-creation instead of being handed to the AI to write (0226 B0001 ④).
            adv = workflow_decision_service.advance_workflow(
                doc_id=row["doc_ref"],
                issued_to=user_id,
                api_base_url=api_base_url,
                locale=locale,
                continuous=True,
                continuation_target_seq=target_seq,
                continuation_review_mode=False,
                continuation_instruction_mode=resume_instruction_mode,
                continuation_auto_approve_item_seqs=resume_auto_approve_item_seqs,
                ai_run_id=ai_run_id,      # 0359 L0007 §2.9
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"],
                # 0406 T0022 item 3 — a resumed hop is the same.
                "worker_document_type": adv.get("worker_document_type"),
                "auto_handled_item_seqs": adv.get("auto_handled_item_seqs") or [],
            }

        parts = group_id.split(".")
        module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
        # 0365 B0001: hand the paused chain's own selections back to start_run. Passing
        # nothing here is what made every resume fall through to the default chain's first
        # (most expensive) provider — the same loss the per-hop re-spawn path already fixed
        # for auto-resume in 0317 T0013 (NR0003 §2-5).
        # provider_pinned / base_provider_id / provider_overrides were already resolved above
        # (gate_provider_pinned / gate_base_provider_id / gate_provider_overrides), before the
        # gate-dispatch check (T0007 §3.3.3) — this plain "new" path reuses the same values.
        provider_pinned = gate_provider_pinned
        base_provider_id = gate_base_provider_id
        provider_overrides = gate_provider_overrides
        note_overrides = gate_note_overrides
        default_note = gate_default_note
        step_timeout_sec = row.get("continuation_step_timeout_sec")
        restart_max_attempts = gate_restart_max_attempts
        # review_count_overrides / reviewer_overrides were already resolved above, before the
        # gate-dispatch check (T0007 §3.3.3) — this plain "new" path reuses the same values.
        try:
            return _svc().start_run(
                project_id=project_id,
                module=module,
                group_id=group_id,
                doc_ref=row["doc_ref"],
                action_scope="new",
                mode="continuous",
                continuation_target_seq=target_seq,
                continuation_review_mode=False,
                continuation_instruction_mode=resume_instruction_mode,
                continuation_locale=locale,
                issued_to=user_id,
                api_base_url=api_base_url,
                mention_builder=lambda _raw, _scratch: None,
                issue_builder=_issue_resume,
                provider_id=base_provider_id,
                provider_pinned=provider_pinned,
                continuation_provider_overrides=provider_overrides,
                continuation_default_note=default_note,
                continuation_note_overrides=note_overrides,
                chain_id=row.get("chain_id"),
                chain_docs_target=row.get("chain_docs_target"),
                chain_docs_reached=row.get("chain_docs_reached"),
                continuation_auto_approve_item_seqs=resume_auto_approve_item_seqs,
                continuation_step_timeout_sec=step_timeout_sec,
                continuation_restart_max_attempts=restart_max_attempts,
                continuation_review_count_overrides=review_count_overrides,
                continuation_reviewer_overrides=reviewer_overrides,
            )
        except HTTPException as exc:
            _restore_row()
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code == 409:
                raise _http_error(
                    409, "resume_launch_failed",
                    str(detail.get("message") or exc.detail or "Resume launch failed."),
                    group_id=group_id, restored=True, resume_stage="advance_or_start",
                    cause_code=detail.get("code"),
                )
            raise
        except LookupError as exc:
            _restore_row()
            raise _http_error(
                404, "resume_advance_unavailable", str(exc),
                group_id=group_id, restored=True, resume_stage="advance",
            )
        except ValueError as exc:
            _restore_row()
            raise _http_error(
                409, "resume_advance_blocked", str(exc),
                group_id=group_id, restored=True, resume_stage="advance",
            )


def release_paused_chain(*, group_id: str, user_id: str, is_admin: bool = False) -> dict:
    """Explicit user cancel/release of a group-keyed PAUSED CHAIN (0459 T0007).

    Paused-row release ONLY -- never a live-run cancel (:func:`cancel_run`) and never
    a lease force-release (:func:`force_release_group_lease`). Order mirrors
    :func:`resume_chain` exactly so the two can never race into a double-consumption
    or a lost row: (1) group lock → (2) active-run / valid-lease check → (3) pause-row
    lookup + ownership judgement → (4) atomic compare-and-swap delete.

    Deliberately narrow lease handling: a VALID lease means resume/start/handoff may
    be mid-flight in another process (memory not finding the run here does not make
    that lease an orphan -- only :func:`force_release_group_lease`'s own liveness
    check gets to decide that), so this refuses with 409 and leaves both the pause row
    and the lease untouched. An EXPIRED lease is invisible here for free --
    ``db_group_ai_leases.get_active`` already reclaims it before answering.

    0459 TR0008 rev1: the active-run and valid-lease branches raise DIFFERENT 409
    codes (``run_already_active`` vs ``group_lease_active``) even though both stop
    the release -- the caller's remedy differs (adopt the other session's run vs.
    release the lease separately from the locked-group screen), and collapsing them
    into one code left both the API caller and the miniplayer unable to tell which
    applies.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    with _group_resume_lock(group_id):
        active = _svc()._active_run_for_group(group_id)
        if active is not None:
            raise _http_error(409, "run_already_active",
                              "An active run already exists for this group; the paused "
                              "chain was already resumed elsewhere.",
                              group_id=group_id, run_id=active["run_id"])
        lease = db_group_ai_leases.get_active(group_id)
        if lease is not None:
            # Distinct code from the active-run branch above (0459 TR0008 rev1): a
            # caller resuming elsewhere (run_already_active) and a caller that must
            # separately release an orphaned lease (group_lease_active) need
            # different follow-up actions, and both used to collapse into the same
            # code, leaving neither the API caller nor the UI able to tell them apart.
            raise _http_error(409, "group_lease_active",
                              "A valid group lease is held; resume/start/handoff may be "
                              "in progress. If the lease is actually orphaned, release it "
                              "separately from the locked-group screen.",
                              group_id=group_id, run_id=lease.get("run_id"))
        row = db_paused.get_by_group(group_id)
        if row is None:
            # Nothing to release -- same-request replay after a prior success, or the
            # chain already ended some other way. Idempotent 200, never 404 (T0007 §5).
            return {"ok": True, "group_id": group_id, "released": False,
                    "already_released": True}
        if not is_admin and row.get("paused_by") != user_id:
            raise _http_error(403, "paused_chain_forbidden",
                              "Only the user who paused this chain (or an admin) may "
                              "release it.", group_id=group_id)
        deleted = db_paused.release_owned(
            group_id,
            paused_by=row.get("paused_by"),
            paused_at=row.get("paused_at"),
            stop_kind=row.get("stop_kind"),
            stop_run_id=row.get("stop_run_id"),
        )
        if isinstance(deleted, db_paused.ReleaseSuperseded):
            # 0459 TR0008 rev2: a newer user pause or system stop won the read/delete
            # race -- the row THIS call inspected is gone, but a DIFFERENT row for the
            # same group is still live in the table right now. T0007 §5 permits the
            # idempotent already_released 200 ONLY when nothing exists for the group_id
            # at all (the rev1 bug folded this case into that same success and told the
            # store to delete a card for a chain that never left the table). Surface it
            # as a real, non-success conflict instead so the caller reconciles against
            # the CURRENT server state rather than optimistically dropping the card.
            raise _http_error(
                409, "release_conflict",
                "This paused chain was replaced by a newer pause or system stop "
                "before the release completed. Refresh to see the current state.",
                group_id=group_id,
            )
        if deleted is None:
            # Truly nothing left for this group_id -- same-request replay after a
            # prior success, or the chain ended some other way. Idempotent 200.
            return {"ok": True, "group_id": group_id, "released": False,
                    "already_released": True}
        return {"ok": True, "group_id": group_id, "released": True,
                "already_released": False}


def _finished_card_retention_minutes(user_id: str) -> int:
    """This user's finished-card retention, in minutes (0452 L0003 1-1).

    Read through `ui_settings_service`, never re-derived: it is the same number the
    browser's own sweep uses, and two definitions would age a card out on one side and
    keep it on the other. A read that fails must not be able to blank a card, so the
    failure answers RETENTION_NEVER -- the bound simply does not apply this time.
    """
    from modules.flow_gate.services import ui_settings_service

    try:
        settings, _ = ui_settings_service.resolve_ui_settings(user_id)
        return int(settings[ui_settings_service.RETENTION_FIELD])
    except Exception:  # noqa: BLE001
        logger.warning("finished-card retention lookup failed for %s", user_id, exc_info=True)
        return ui_settings_service.RETENTION_NEVER


def _review_loop_card_expired(row: dict, retention_minutes: int) -> bool:
    """Has this restored review-loop card outlived the retention its owner chose?

    -1 ("never expires") is a real choice, not a lower bound (L0003 2-1): it means the
    card stays until somebody removes it, which is exactly what `card_dismissed_at` is
    for. 0 ("disappears immediately") means the bootstrap restores no finished card at
    all, matching the browser's own `retentionTtlMs === 0` branch.

    A row with no readable `finished_at` is NOT expired: the age is unknown, and guessing
    "old" would drop a card nobody asked to lose.
    """
    from modules.flow_gate.services import ui_settings_service

    if retention_minutes == ui_settings_service.RETENTION_NEVER:
        return False
    if retention_minutes == ui_settings_service.RETENTION_IMMEDIATE:
        return True
    stamp = row.get("finished_at")
    if not stamp:
        return False
    try:
        finished = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if finished.tzinfo is None:
        finished = finished.astimezone()
    age_sec = (datetime.now(timezone.utc) - finished.astimezone(timezone.utc)).total_seconds()
    return age_sec >= retention_minutes * 60


def dismiss_review_loop_card(*, run_id: str, user_id: str, is_admin: bool = False) -> dict:
    """Durable [remove from list] for a FINISHED run's monitor card (0529 B0001).

    The counterpart of :func:`release_paused_chain`, for the other kind of card that is
    rebuilt from the database on every bootstrap. A finished document-review-loop card
    used to be removed by a purely local delete in the browser, and `active_all` handed
    the very same card straight back on the next `/ai-invoke/active-all` -- the "it does
    not go away when I press remove" this bug report is about.

    Deliberately NOT a delete (FlowGate is a time machine): the run row and the loop row
    both stay, with every round, stop reason and stop detail still readable through GET
    /ai-invoke/{run_id} and the run list. Only the bootstrap listing skips it afterwards.

    A LIVE run is refused with 409: its card is not a leftover, and dismissing it would
    hide a run that is still working. The idempotent branches mirror release_paused_chain
    -- a replay answers 200 `already_dismissed`, never 404 -- so a double click and a
    retry after a dropped response both land on the same state.
    """
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    row = db_runs.get(run_id)
    if row is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if not is_admin and row.get("issued_to") != user_id:
        raise _http_error(403, "run_card_forbidden",
                          "Only the user who started this run (or an admin) may remove "
                          "its card.", run_id=run_id)
    live = _svc().get_run_record(run_id)
    if live is not None and live.get("status") != "finished":
        raise _http_error(409, "run_still_active",
                          "This run is still active; its card is not a leftover. Cancel "
                          "the run first if you want it to stop.",
                          run_id=run_id, group_id=row.get("group_id"))
    if db_loops.get(run_id) is None:
        # No durable card behind this run at all -- nothing to keep out of the next
        # bootstrap, so the goal state already holds. Idempotent 200, never 404.
        return {"ok": True, "run_id": run_id, "group_id": row.get("group_id"),
                "dismissed": False, "already_dismissed": True}
    if not db_loops.dismiss_card(run_id):
        return {"ok": True, "run_id": run_id, "group_id": row.get("group_id"),
                "dismissed": False, "already_dismissed": True}
    return {"ok": True, "run_id": run_id, "group_id": row.get("group_id"),
            "dismissed": True, "already_dismissed": False}


def _open_q_doc_ids(group_id: str) -> list[str]:
    """Group documents that still have at least one unanswered container item."""
    try:
        pending: set[str] = set()
        for doc in db_docs.get_documents_by_group_id(group_id):
            doc_id = doc.get("doc_id")
            if not doc_id:
                continue
            container = db_questions.get_container_by_doc(doc_id)
            if container and db_question_items.list_unanswered(container["id"]):
                pending.add(doc_id)
        return sorted(pending)
    except Exception:
        logger.warning("open-Q lookup failed for %s", group_id, exc_info=True)
        return []


def _handoff_row_in_flight(row: dict) -> bool:
    """Is this a hop handoff that is still plausibly landing? (0406 T0022 item 4)

    True only for a ``hop_handoff`` system row whose group still has a live run, or whose
    write is younger than :data:`HOP_HANDOFF_GRACE_SEC`. Anything else — a handoff the
    startup recovery re-labelled, a parked failure, a user pause — is a real card.
    """
    if (row.get("stop_kind") or "user") != "system":
        return False
    if (row.get("stop_code") or "") != HOP_HANDOFF_STOP_CODE:
        return False
    if _svc()._active_run_for_group(row.get("group_id") or "") is not None:
        return True
    stamp = row.get("updated_at") or row.get("paused_at")
    if not stamp:
        return False
    try:
        written = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.astimezone()
    age = (datetime.now(timezone.utc) - written.astimezone(timezone.utc)).total_seconds()
    return age < HOP_HANDOFF_GRACE_SEC


def _paused_row_stop_reason(row: dict) -> Optional[str]:
    """The §4.3 sentence behind a parked chain's stop code (0458 T0007 §2.1).

    The paused row stores the code; the run that parked it stores the words — including the
    exception detail an `approve_failed` carries, now that _resettle_stop_after_park writes
    the gate's verdict back onto that run. `stop_run_id` is the join.

    The two codes must still agree. A row whose code was rewritten after the run was persisted
    — `startup_recover_handoffs` turning `hop_handoff` into `hop_handoff_interrupted` is the
    live example — would otherwise be captioned with the previous stop's sentence, which is
    worse than no sentence. Best-effort throughout: a card with no explanation still beats a
    bootstrap that 500s.
    """
    run_id = row.get("stop_run_id")
    if not run_id:
        return None
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        record = db_runs.get(run_id) or {}
    except Exception:  # noqa: BLE001
        logger.warning("paused-chain stop reason lookup failed for %s", run_id, exc_info=True)
        return None
    if (record.get("stop_code") or "") != (row.get("stop_code") or ""):
        return None
    return record.get("stop_reason")


def active_all(user_id: str) -> dict:
    """Global widget bootstrap (P0008 S1): every live run the user started plus every
    chain the user paused — the refresh-proof source the miniplayer restores from."""
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    with _runs_lock:
        candidates = [
            run for run in _svc()._runs.values()
            if run.get("issued_to") == user_id and run["status"] != "finished"
        ]
    runs = []
    for run in candidates:
        try:
            status = diagnostics.get_status(run["run_id"])
        except HTTPException:
            continue  # finished/expired between the snapshot and the status read
        status["doc_ref"] = run["doc_ref"]
        runs.append(status)

    # A restart empties `_runs`, but a finished document-review loop is still a card: the
    # durable run identifies its owner and the durable loop rebuilds its complete history.
    # Discover those rows here (the bootstrap path), not only when a caller already knows a
    # run_id. Memory wins during the small finalize overlap and one bad stored row cannot
    # blank every other card.
    #
    # 0529 B0001: "a restart" is the whole of what this restore is for. Until now it had no
    # end at all -- `list_review_loops_by_user` answered with every loop row the user ever
    # owned, so a card came back on every bootstrap, days after the run ended and after its
    # owner had removed it by hand (run aiv_20260830_000075, still on screen 2026-09-06).
    # Two bounds fix that, and both are the user's OWN existing rules rather than a new
    # policy invented here: the query now skips a card its owner removed
    # (`card_dismissed_at`), and `_review_loop_card_expired` below drops one older than the
    # finished-card retention this user chose -- the same L0003 number the browser already
    # sweeps its in-memory copy of this very card with.
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    live_ids = {run["run_id"] for run in candidates}
    try:
        stored_loop_rows = db_runs.list_review_loops_by_user(user_id)
    except Exception:
        logger.warning("stored review-loop list failed for %s", user_id, exc_info=True)
        stored_loop_rows = []
    retention_minutes = _finished_card_retention_minutes(user_id)
    for row in stored_loop_rows:
        if row["run_id"] in live_ids:
            continue
        if _review_loop_card_expired(row, retention_minutes):
            continue
        try:
            restored = diagnostics._run_detail_from_row(row)
        except Exception:
            logger.warning("stored review-loop restore failed for %s", row.get("run_id"), exc_info=True)
            continue
        restored["persisted"] = True
        runs.append(restored)

    paused = []
    try:
        rows = db_paused.list_by_user(user_id)
    except Exception:
        logger.warning("paused-chain list failed for %s", user_id, exc_info=True)
        rows = []
    for row in rows:
        if _handoff_row_in_flight(row):
            # 0406 T0022 item 4: a normal handoff takes seconds. Drawing a "stopped" card
            # just because the durable row exists for those seconds makes it blink on every
            # successful hop, and the stop badge stops meaning anything. In-flight handoffs
            # are hidden; a row past the grace really did break and falls through to a card.
            continue
        try:
            stale = _system_pause_row_is_stale(row)
        except Exception:  # noqa: BLE001 — one unreadable row must not blank the widget
            logger.warning(
                "system paused-row staleness check failed for %s",
                row.get("group_id"), exc_info=True,
            )
            stale = False
        if stale:
            # Delete only the exact system-stop snapshot inspected above, by
            # (group_id, stop_kind='system', stop_run_id). A user pause written since the
            # read, or a newer system stop for the same group, fails that exact match and
            # survives — which a group-only DELETE would not.
            try:
                db_paused.delete_system_stop(row["group_id"], row.get("stop_run_id"))
            except Exception:
                logger.warning(
                    "stale system paused-row cleanup failed for %s",
                    row.get("group_id"), exc_info=True,
                )
                # The delete failed, but the row IS stale: keeping it out of the response
                # is still right, and the next active-all retries the delete.
            continue
        resume_state = _svc()._paused_row_resume_state(row["group_id"].split(".")[0], row)
        paused.append({
            "group_id": row["group_id"],
            "doc_ref": row["doc_ref"],
            "mode": row.get("mode") or "continuous",
            "paused_by": row["paused_by"],
            "paused_at": row["paused_at"],
            "continuation_target_seq": row.get("continuation_target_seq"),
            "docs_target": row.get("docs_target"),
            "docs_reached": int(row.get("docs_reached") or 0),
            # 0357 T0004: chain-lifetime progress, so a resumed card shows how far the
            # CHAIN got — not how far its last hop got.
            "chain_id": row.get("chain_id"),
            "chain_docs_target": row.get("chain_docs_target"),
            "chain_docs_reached": int(row.get("chain_docs_reached") or 0),
            "pending_q_doc_ids": _svc()._open_q_doc_ids(row["group_id"]),
            # 0359 P0006 [handover]: a chain the SYSTEM parked looks like one a person parked,
            # so the existing card and its [resume] button work unchanged — these four
            # fields only say which it was and why. A legacy row has no stop_kind: it predates
            # system stops, so it can only have been a person (DB0008 §2.3).
            "stop_kind": row.get("stop_kind") or "user",
            "stop_code": row.get("stop_code"),
            # 0458 T0007 §2.1: and WHY, in words. A code cannot carry an exception string, and
            # `ai_invoke_paused_chains` has no column for one (the T forbids a schema change),
            # so the sentence is read back from the run that parked the row. The miniplayer
            # already reads `stop_reason` off this payload (aiInvokeRuns.ts, the paused-row
            # normalizer) and falls through to it whenever the build has no translated
            # sentence for the code — which is every code but one. The server simply never
            # sent it, so a parked chain's card had the code and nothing else.
            "stop_reason": _paused_row_stop_reason(row),
            "stop_run_id": row.get("stop_run_id"),
            "stop_last_message_excerpt": row.get("stop_last_message_excerpt"),
            # T0005 §2: whether THIS paused row can actually resume under the current
            # provider settings snapshot — a display hint only, never a substitute for
            # start_run's own 422 at execution time.
            "resume_available": resume_state["resume_available"],
            "resume_block_code": resume_state["resume_block_code"],
            "resume_block_reason": resume_state["resume_block_reason"],
            "resume_provider_name": resume_state["resume_provider_name"],
        })
    return {"ok": True, "runs": runs, "paused": paused}


# ── SSE ──────────────────────────────────────────────────────────────────────

def _broadcast(run: dict, event_type: str, payload: dict) -> None:
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        broadcast_event_threadsafe(
            FlowEvent(
                event_type=event_type,
                payload=payload,
                audience="*",
                project=run["project_id"],
                group_id=run["group_id"],
                doc_id=run["doc_ref"],
            )
        )
    except Exception:
        logger.warning("ai-invoke SSE broadcast failed", exc_info=True)
