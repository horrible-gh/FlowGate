"""Run finalization (0501 NR0003 §12/§17 `finalize.py`).

The last boundary of a run lifecycle, in one place: settle-and-judge, the timeout
diagnostics that separate "made no progress" from "hit the absolute ceiling", the stop
code and its human sentence, the durable `ai_invoke_runs` record, the chain-stop row,
the group-lease denial record, the failure notification, and the finished payload the
status API and the miniplayer render.

Every one of those used to be the tail of the worker file, which is why "the run ended"
had six spellings that could drift apart. They now share one module and one set of
constants (`SOURCE_DIRTY_FILES_LIMIT`, the stop-code sets in `runtime`).
"""

from __future__ import annotations

import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.storage import paths as storage_paths

# `oracle` is also a local variable name in this file, so
# that sibling module is imported under an unambiguous alias. `worker` is not imported
# as a sibling: the one name this module used to reach there (`_absolute_cap_sec`) now
# lives on `runtime` (§13's parameter block), which keeps this module from opening a
# path back into worker.py (worker -> ... -> chain -> finalize would otherwise cycle).
from . import admission
from . import oracle as oracle_module
from . import review
from .runtime import (
    ENGINE_NOTIFY_STOP_CODES,
    INBOX_NOTIFY_STOP_CODES,
    NO_OUTPUT_MAX_ATTEMPTS,
    RESUMABLE_STOP_CODES,
    REVIEW_CAP_REACHED_STOP_CODE,
    REVIEW_EXHAUSTED_STOP_CODE,
    REVIEW_NO_VERDICT_STOP_CODE,
    REVIEW_REJECT_DENIED_STOP_CODE,
    REVIEW_REJECT_FAILED_STOP_CODE,
    REVIEW_STALLED_STOP_CODE,
    REVIEW_VERDICT_HOLD_STOP_CODE,
    SOURCE_DIRTY_FILES_LIMIT,
    _absolute_cap_sec,
    _known_run_prompts,
    _known_run_raw_tokens,
    _mark_scratch_completed,
    _redact_secrets,
    _runs_lock,
    _safe_scratch_log,
    _svc,
    excerpt,
    logger,
)


def _settle_and_judge(run: dict) -> None:
    """Judge, then finalize — the pre-0359 shape, kept for every caller that wants both.

    0359 L0007 §2.1 split these two apart so the no-output retry has somewhere to stand:
    _worker now calls _judge_hop once per ATTEMPT and _finalize_run once per HOP.
    """
    _svc()._judge_hop(run)
    _svc()._finalize_run(run)


def _judge_hop(run: dict) -> None:
    """Decide what this attempt produced (L0007 §2.2). Exactly once per attempt — never twice
    for one attempt, and never by merging two attempts' verdicts. docs_reached is always
    measured from the hop's baseline_seq, so a later attempt's document is simply the answer.

    Judgment content is unchanged from the pre-0359 _settle_and_judge; only the finalize call
    that used to be welded to each branch has been lifted out.
    """
    time.sleep(_svc().ORACLE_SETTLE_SEC)
    if run.get("action_scope") == "resolve_conflict":
        resolved = _conflict_resolved(run)
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete" if resolved else "none"
        return

    oracle = run.get("completion_oracle")
    if oracle is not None:
        # The run's product is not a document (a Q&A answer row, an in-place revision, a
        # review row), so the document-reach oracle would judge every such run 'none'. Ask
        # the scoped judge instead — the scope default from `_SCOPE_PROBES` (0259 B0001) or
        # the caller's `completion_oracle` override (0248 B0001).
        try:
            satisfied = bool(oracle())
        except Exception:
            logger.warning("ai-invoke scoped oracle failed for %s", run["run_id"], exc_info=True)
            satisfied = False
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete" if satisfied else "none"
        # Same "exited cleanly but produced nothing" signal the document oracle raises —
        # here it means the worker returned without ever writing its scope's row.
        run["oracle_mismatch"] = bool(
            not satisfied
            and run.get("end_reason") == "exited"
            and not run.get("register_errors")
            and not run.get("tool_call_misses")
            and not run.get("turn_limit_exhausted")
        )
        return

    new_docs: list[dict] = []
    try:
        new_docs = _svc()._oracle_new_docs(run)
    except Exception:
        logger.warning("ai-invoke oracle query failed for %s", run["run_id"], exc_info=True)

    workflow_decided = False
    if run.get("action_scope") == "workflow_decide":
        try:
            sequence = db_wfseq.get_sequence_by_doc_id(run["doc_ref"])
            workflow_decided = sequence is not None
            if workflow_decided and run.get("target_to_end"):
                # 0226 NR0003 §5-1: to-end scope = every worker item of the decided
                # sequence (item_seq space), not a group doc seq subtraction.
                resolved = admission._continuation_docs_target(
                    run["doc_ref"],
                    None,
                    pending_only=False,
                    continuation_instruction_mode=run["continuation_instruction_mode"],
                    continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
                )
                if resolved is not None:
                    run["docs_target"] = resolved
                    if run.get("chain_docs_target", 0) <= 0:
                        run["chain_docs_target"] = resolved
        except Exception:
            logger.warning("ai-invoke workflow oracle failed for %s", run["run_id"], exc_info=True)

    # 0226 NR0003 §5-2: no min() clamp — an overrun (more docs than the target) stays
    # visible in docs_reached/docs_target instead of being normalized away at the end.
    docs_reached = len(new_docs)
    run["docs_reached"] = docs_reached
    run["reached_doc_ids"] = [d["doc_id"] for d in new_docs]
    # 0317 TR0011 (Q153 opt-1): under per-hop re-spawn each continuous hop delivers ONE
    # document and then hands the chain off (the self-chain withheld next_token and queued
    # the next hop). Judge such a hop against 1, not the whole remaining chain target, so an
    # intermediate hop settles "complete" (scratch cleaned) instead of a misleading "partial".
    hop_target = run["docs_target"]
    if run["mode"] == "continuous" and _svc().peek_auto_resume(run.get("group_id")) is not None:
        hop_target = 1
    if run.get("action_scope") == "workflow_decide" and run["mode"] == "single":
        run["outcome"] = "complete" if workflow_decided else "none"
    elif run.get("action_scope") == "workflow_decide" and not workflow_decided:
        # Pre-decision continuous run that never decided: no resolved target to satisfy.
        run["outcome"] = "partial" if docs_reached >= 1 else "none"
    elif docs_reached >= hop_target:
        run["outcome"] = "complete"
    elif docs_reached >= 1:
        run["outcome"] = "partial"
    else:
        run["outcome"] = "none"

    run["oracle_mismatch"] = bool(
        run["outcome"] == "none"
        and run.get("end_reason") == "exited"
        and not run.get("register_errors")
        and not run.get("tool_call_misses")
        and not run.get("turn_limit_exhausted")
    )


def _conflict_resolved(run: dict) -> bool:
    merge_id = run.get("merge_id")
    if merge_id is None:
        return False
    try:
        conflicts = git_service.list_conflicts(run["group_id"], int(merge_id))
    except GitServiceError as exc:
        # A successful complete=true resolve closes the merge session; list_conflicts
        # then returns not_found. Treat closed/missing as terminal for this scoped oracle.
        return exc.status == 404
    except Exception:
        logger.warning("ai-invoke conflict oracle failed for %s", run["run_id"], exc_info=True)
        return False
    files = conflicts.get("files") or []
    return sum(int(f.get("conflict_count") or 0) for f in files) == 0


# ── 0446 T0016 §2/§3-1: the durable reading of a watchdog stop ───────────────
# T0014's verdict lives in `run["watchdog_kill"]`, and every number in it is an offset
# from a monotonic clock that means nothing once this process is gone. These helpers
# turn it into the pair that goes on the row: a fixed vocabulary the next step can
# branch on, and one sentence a person can read.
#
# The sentence is English, like `stop_reason` — the column right beside it, written by
# `_stop_reason_text`, and read by the same UI. A stored row has no locale to be written
# in; the rework block that quotes this line localizes its own labels around it (§4-5).
_TIMEOUT_KINDS = ("no_progress", "absolute_cap")


def _format_span(seconds: int) -> str:
    """Short, stable reading of a duration — whole minutes once there is one."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    return f"{seconds // 60} min"


def _resolve_timeout_diagnostics(run: dict) -> tuple[Optional[str], Optional[str]]:
    """`(timeout_kind, timeout_diagnosis)` for a finished run, or `(None, None)`.

    Called once from `_finalize_run` AFTER `duration_ms` is fixed, so the total this
    sentence quotes is exactly the total the row stores. Two rounding rules matter:
    `duration_ms` is floored to whole seconds, and the stalled window is then clamped to
    that total. The watchdog measured its own number one poll before finalize measured
    the total, so without the clamp a boundary case can print a stall longer than the run
    that contains it.

    A run with no watchdog mark — a plain `communicate()` expiry, an API-mode run, any row
    written before T0014 — returns `(None, None)`. That NULL is the third state (§2-1):
    not "unknown kind of stall", but "nothing watched this one".
    """
    kill = run.get("watchdog_kill")
    if not isinstance(kill, dict):
        return None, None
    kind = kill.get("kind")
    if kind not in _TIMEOUT_KINDS:
        return None, None
    total_sec = max(0, int(run.get("duration_ms") or 0) // 1000)
    stalled_sec = min(max(0, int(kill.get("stalled_sec") or 0)), total_sec)
    if kind == "absolute_cap":
        # §2-2: a run that was still producing when it hit the ceiling must NOT be filed as
        # stalled. The observation count is the evidence, and it is honest about the blind
        # case too (a run whose samples never read stops here with 0 observations).
        cap_sec = int(kill.get("absolute_cap_sec") or _absolute_cap_sec())
        return kind, (
            f"Reached the {_format_span(cap_sec)} absolute run ceiling after "
            f"{_format_span(total_sec)}; not a no-progress stop "
            f"(progress observations: {int(kill.get('progress_observations') or 0)})."
        )
    return kind, (
        f"No document registration or source change during the last "
        f"{_format_span(stalled_sec)} of {_format_span(total_sec)} total."
    )


def previous_timeout_handoff(group_id: str, doc_ref: Optional[str] = None) -> Optional[dict]:
    """What the run right before this one left behind — but only if it timed out (§4-1/2).

    Read while the rework prompt is being assembled, which is before the new run has a row
    of its own (finalize writes that), so "the latest finished row" really is the previous
    hop. Exactly ONE row is considered: if the newest run exited cleanly this returns None
    even when an older timeout sits behind it, because a stop that has already been
    superseded is not a handoff.

    Returns None for every other shape too — no row at all, a cancel, a fast_fail, another
    group, another document — and that None is what keeps the existing rework prompt
    byte-identical for every run that is not resuming a timeout.

    Never raises. A handoff is an aid; if the row cannot be read the worker gets exactly
    the prompt it would have got before this existed.
    """
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        row = db_runs.latest_finished_for_group(group_id, doc_ref=doc_ref)
    except Exception:
        logger.warning("ai-invoke previous-run handoff lookup failed for group %s",
                       group_id, exc_info=True)
        return None
    if not row:
        return None
    if row.get("end_reason") != "timeout" and row.get("stop_code") != "timeout":
        return None
    source_dirty = row.get("source_dirty")
    return {
        "run_id": row.get("run_id"),
        "finished_at": row.get("finished_at"),
        "timeout_kind": row.get("timeout_kind"),
        "timeout_diagnosis": row.get("timeout_diagnosis"),
        # Three states, kept apart: True (files below), False (the tree was clean), None
        # (the run could not read git at all, so nothing is claimed either way).
        "source_dirty": None if source_dirty is None else bool(source_dirty),
        # A dirty run with an empty list stays dirty — the block says so rather than
        # inventing file names for it.
        "source_dirty_files": (
            list(row.get("source_dirty_files") or [])[:SOURCE_DIRTY_FILES_LIMIT]
            if source_dirty else []
        ),
    }


def _finalize_run(run: dict) -> None:
    """Close the hop out — once, whatever it took to get here (L0007 §2.7).

    Three ordering rules, and all three are about what a human sees:
      1. the stop row is written BEFORE the broadcast — the browser re-reads active-all the
         moment it sees the finish event, so a late row means a late card;
      2. the record is persisted BEFORE the broadcast — a detail lookup triggered by that
         same event must not answer 404;
      3. neither of them may block the broadcast. Both swallow their own failures: a record
         is an aid, not the run.
    """
    # 0482 T0011: group-less base-dirty runs own a separate project admission lease.
    # This block lived in ai_invoke_service._finalize_run, then in main's part-2 worker
    # file; it travels with the function, not with the filename, so 0501 T0019 carries it
    # into finalize.py — _finalize_run's own module (NR0003 §17).
    if run.get("action_scope") == "resolve_base_dirty":
        try:
            from modules.flow_gate.db import project_ai_leases as db_project_ai_leases
            db_project_ai_leases.release(str(run.get("project_id") or ""), str(run.get("run_id") or ""))
        except Exception:
            logger.exception("failed to release project AI lease")

    # Final last_message (L0007 §2.6): the last attempt may have said nothing at all, while an
    # earlier one left the sentence that actually explains the failure. Keep the explanation.
    if not run.get("last_message") and run.get("last_message_seen"):
        run["last_message"] = run["last_message_seen"]
        run["last_message_received"] = True

    # 0357 T0004: fold this HOP's documents into the CHAIN counter — here, at finalize,
    # not in the judge. A hop may be judged several times now (once per no-output attempt,
    # L0007 §2.2), and crediting the chain on the first of those would freeze the count at
    # attempt 1's zero and lose the document a later attempt went on to produce.
    if not run.get("chain_docs_accounted"):
        run["chain_docs_reached"] = (
            int(run.get("chain_docs_reached") or 0) + int(run.get("docs_reached") or 0)
        )
        run["chain_docs_accounted"] = True

    respawn_pending = _svc().peek_auto_resume(run.get("group_id")) is not None
    # Keep ownership across a hop boundary; the successor atomically transfers generation.
    if respawn_pending:
        db_group_ai_leases.begin_handoff(run["group_id"], run["run_id"])
    run["stop_code"] = _resolve_stop_code(run, respawn_pending)
    run["resumable"] = is_resumable(run["stop_code"])
    run["stop_reason"] = _stop_reason_text(run["stop_code"], run)

    # Scratch lifecycle: every deletion passes the manifest/identity boundary again.
    scratch = Path(run["scratch_dir"])
    completed_at = datetime.now(timezone.utc).isoformat()
    manifest_updated = _mark_scratch_completed(
        run["project_id"], run["run_id"], scratch, completed_at
    )
    deleted = False
    cleanup_reason = "non_complete_outcome"
    if run["outcome"] == "complete" and manifest_updated:
        deleted, cleanup_reason = _svc()._delete_owned_scratch(
            run["project_id"], run["run_id"], scratch
        )
    if not deleted:
        try:
            run["scratch_retained"] = storage_paths.to_storage_relative(scratch, run["project_id"])
        except Exception:
            run["scratch_retained"] = run["scratch_dir"]
        _safe_scratch_log(
            run["project_id"], run["run_id"], scratch, "retained", cleanup_reason
        )

    # Source-spill check (§2.8): only the delta vs the start-time snapshot.
    baseline = run.get("dirty_baseline")
    now_paths = _svc()._git_status_paths(Path(run["source_root"]) if run.get("source_root") else None)
    if baseline is None or now_paths is None:
        run["source_dirty"] = None
        run["source_dirty_files"] = []
    else:
        spilled = sorted(now_paths - baseline)
        run["source_dirty"] = bool(spilled)
        run["source_dirty_files"] = spilled[:SOURCE_DIRTY_FILES_LIMIT]

    # The whole hop, every attempt inside it — not just the last one.
    run["duration_ms"] = int((time.monotonic() - run["started_mono"]) * 1000)
    # 0446 T0016 §3-1: source delta and duration are both settled now, so read the
    # watchdog's verdict ONCE, here. Nothing downstream — the row, the finished payload,
    # the next rework prompt — touches `watchdog_kill` again; they read these two.
    run["timeout_kind"], run["timeout_diagnosis"] = _resolve_timeout_diagnostics(run)
    run["finished_at"] = now_iso()
    run["status"] = "finished"

    _apply_stop_row(run, respawn_pending)
    if run.get("document_review_loop") and not run.get("document_review_loop_checkpointed"):
        try:
            review._checkpoint_document_review_loop(run)
        except Exception:
            logger.exception("document review-loop checkpoint failed for %s", run["run_id"])
    _svc()._persist_run_record(run)
    _notify_chain_failure_if_needed(run)

    _svc()._broadcast(run, "ai_invoke_finished", _svc().finished_payload(run))
    _svc()._broadcast(run, "group_view_refresh", {
        "group_id": run["group_id"],
        "reason": "ai_invoke_finished",
    })
    if not respawn_pending:
        db_group_ai_leases.release(run["group_id"], run["run_id"])


# ── Stop classification (0359 L0007 §4.1 ~ §4.3) ─────────────────────────────


def _resolve_stop_code(run: dict, respawn_pending: bool) -> Optional[str]:
    """Why did the chain stop here? (L0007 §4.1 — evaluated in this exact order.)

    1-4 outrank whatever the inbox tagged: a human cancel or a blown deadline is the truth no
    matter what the self-chain thought it was doing when the request arrived. A single-mode or
    non-document run has no stop code at all — nothing about it can stop a chain.
    """
    cancel_event = run.get("cancel_event")
    if (cancel_event is not None and cancel_event.is_set()) or run.get("end_reason") == "cancelled":
        return "cancelled"
    if run.get("end_reason") == "timeout":
        return "timeout"
    if run.get("end_reason") == "user_paused":
        return "user_paused"
    if run.get("end_reason") == "all_providers_failed":
        return "providers_exhausted"
    # 0393 B0001 / T0005 §2-6: the group gate refused this run's OWN worker, so nothing the
    # worker submitted was ever registered. It outranks whatever the inbox tagged because
    # the inbox never saw the request — the middleware turned it away first. Deliberately
    # mode-independent: B0001's three dead reviews were mode="single", the exact shape that
    # used to fall all the way through this function to `return None` (no code, no reason).
    # Only claimed when the run produced nothing; a run that did land its documents and then
    # tripped the gate on a stray call keeps its ordinary ending and the register_errors row.
    if run.get("lease_denied_code") and int(run.get("docs_reached") or 0) == 0:
        return "group_lease_denied"
    if run.get("inbox_stop_code"):
        return run["inbox_stop_code"]
    if respawn_pending:
        return "hop_handoff"
    if run.get("retry_block_reason") == "question_pending":
        # NR0003 follow-up proposal 1/3: this hop's own docs_target/docs_reached shape is
        # identical to no_output_exhausted's (target ≥1, reached 0) — the only thing that
        # tells them apart is WHY nothing landed. _has_pending_question already told
        # _retry_eligible this hop is waiting on a human answer, not silently dead; name it
        # separately so it never reaches _notify_chain_failure_if_needed as a failure.
        return "question_pending"
    if int(run.get("docs_reached") or 0) == 0 and run.get("outcome") == "none" and (
        (run.get("mode") == "continuous" and int(run.get("docs_target") or 0) >= 1)
        # 0446 T0008 §3-7: the same fact told on the axis a scope-oracle rework run actually
        # has — it exited on its own and its judge was not satisfied (the `oracle_mismatch`
        # shape). Its docs_target is 0 by construction, so the document-count clause above can
        # never speak for it, and all 15 measured clean failures ended with stop_code NULL.
        # A rework that DID raise the revision has outcome "complete" and keeps its NULL.
        or (oracle_module._scope_oracle_retry_run(run) and run.get("end_reason") == "exited")
    ):
        # The shape of this incident: the hop ran, the hop finished, the hop made nothing —
        # and until now the system had no name for that, so it treated it as an ordinary end.
        return "no_output_exhausted"
    return None


def is_resumable(stop_code: Optional[str]) -> bool:
    """L0007 §4.2 — one criterion: would re-running this hop have a chance?

    Stops a human has to clean up (head mismatch, approval, blocked advance) and stops a human
    meant (cancel) are deliberately excluded. Resuming those would undo a person's decision.
    """
    return stop_code in RESUMABLE_STOP_CODES


def _stop_reason_text(stop_code: Optional[str], run: dict) -> Optional[str]:
    """L0007 §4.3 — English, because the same sentence is read by the worker, stored on the
    record and shown on the card; one wording for all three."""
    if stop_code is None:
        return None
    if stop_code == "chain_completed":
        return (f"Target step {run.get('continuation_target_seq')} reached; "
                "the chain is complete.")
    if stop_code == "hop_handoff":
        return "This hop produced its document; the next hop starts in a new worker."
    if stop_code == "no_output_exhausted":
        provider_name = run.get("continuation_selected_provider_name") or run.get("continuation_selected_provider_id") or "Selected provider"
        # 0446 T0008 §3-6: `retry_block_reason` has no column in `ai_invoke_runs` and is not
        # in `finished_payload` either, so a run that never got to open its retry
        # ("token_unavailable", "providers_exhausted_for_retry", or a budget too far spent)
        # left no durable trace of WHY. `stop_reason` is persisted, so the reason rides here.
        # question_pending never reaches this branch — it is named above.
        blocked = run.get("retry_block_reason")
        blocked_text = f" No further attempt was opened: {blocked}." if blocked else ""
        return (f'"{provider_name}" produced no document in '
                f"{int(run.get('attempts_used') or 0)} attempts. "
                "The chain stopped without switching to another provider."
                f"{blocked_text}")
    if stop_code == "question_pending":
        return ("This hop registered a query and is waiting for a human answer. "
                "The chain stopped and can be resumed once it is answered.")
    if stop_code == "providers_exhausted":
        return ("No AI provider could be started for this hop. "
                "The chain stopped and can be resumed.")
    if stop_code == "timeout":
        return (f"The hop exceeded its {run.get('timeout_sec')}s budget. "
                "The chain stopped and can be resumed.")
    if stop_code == "cancelled":
        return f"Cancelled by the user during attempt {run.get('attempt_no')}."
    if stop_code == "user_paused":
        return "Paused by the user at the step boundary."
    if stop_code == "head_slot_mismatch":
        return ("The submitted document did not fill the current workflow head slot; "
                "a human must triage.")
    if stop_code == "approve_denied":
        return ("The token issuer lacks document.approve; a human must approve before "
                "continuing.")
    if stop_code in ("approve_failed", "advance_blocked"):
        # 0458 T0007 §2.1: ONE detail lookup for both codes, and it reads BOTH keys the
        # two producers write. The inbox self-chain reaches this table through
        # stop_reason_text(..., detail=...) and lands on `inbox_stop_detail`; the engine's
        # review gate stores the very same sentence on `review_reject_detail`
        # (_settle_gate_pass, below) because it has a live run to hang it on instead of an
        # envelope. Reading only the second key turned every gate-side failure into
        # "unknown error" while the real exception sat one key away — the diagnostic loss
        # 0003-NR §11-1 reported. Order is review-gate-first: when both are set the gate's
        # is the one that describes THIS stop.
        detail = (run.get("review_reject_detail")
                  or run.get("inbox_stop_detail")
                  or "unknown error")
        if stop_code == "approve_failed":
            return f"Auto-approval failed: {detail}"
        return f"Could not advance to the next step: {detail}"
    if stop_code == "review_hold":
        return "Review mode: waiting for the human go."
    # 0414 L0008 §1.2 — one English sentence per code, read by the worker, stored on the
    # record and shown on the card alike.
    if stop_code == REVIEW_EXHAUSTED_STOP_CODE:
        # Legacy (0414 M0020): only chains parked before that change carry this code.
        return ("Every review round this step was given has been used and the reviewer "
                "still reported issues. The document is left rejected with those findings; "
                "a human must take it from here.")
    if stop_code == REVIEW_CAP_REACHED_STOP_CODE:
        # Legacy (0414 0022-TR rejection): "until it passes" has no round ceiling any
        # more, so nothing emits this now; only chains parked earlier carry it. Resuming
        # it is a normal resume — see REVIEW_CAP_REACHED_STOP_CODE in RESUMABLE_STOP_CODES.
        return ("Review was set to run until it passes and was parked at the round "
                "ceiling that setting used to carry. That ceiling is gone: resuming this "
                "run lets review and rework repeat until a pass, same as any other -1 run.")
    if stop_code == REVIEW_VERDICT_HOLD_STOP_CODE:
        return ("The reviewer returned 'hold' — it could not judge this document. "
                "A human must decide; the chain does not resume on its own.")
    if stop_code == REVIEW_STALLED_STOP_CODE:
        return ("The review loop stopped making progress: the rework hop raised no new "
                "revision, or the same findings came back twice. A human must triage.")
    if stop_code == REVIEW_NO_VERDICT_STOP_CODE:
        # flowgate.default.0476 T0007 §3: same blocked_text pattern as no_output_exhausted
        # above — retry_block_reason has no column of its own, so this is the only durable
        # place a human can tell "both attempts ran and still left no verdict" apart from
        # "budget/provider/token exhaustion cut the second attempt before it ever opened".
        attempts_max = run.get("attempts_max")
        if attempts_max is None:
            attempts_max = NO_OUTPUT_MAX_ATTEMPTS
        blocked = run.get("retry_block_reason")
        blocked_text = f" No further attempt was opened: {blocked}." if blocked else ""
        return ("The review hop finished without recording a verdict "
                f"({int(run.get('attempts_used') or 0)} of {attempts_max} attempts used). "
                f"The chain stopped and can be resumed.{blocked_text}")
    if stop_code == REVIEW_REJECT_DENIED_STOP_CODE:
        return ("The chain issuer lacks document.reject, so the reviewer's issues could not "
                "be turned into a rejection. The document is left unapproved.")
    if stop_code == REVIEW_REJECT_FAILED_STOP_CODE:
        return (f"The automatic rejection failed: "
                f"{run.get('review_reject_detail') or 'unknown error'}")
    if stop_code == "group_lease_denied":
        # 0393 T0005 §2-6: the sentence a human reads on the card instead of a bare code.
        denied_op = run.get("lease_denied_operation") or "a group change"
        denied_code = run.get("lease_denied_code") or "GROUP_AI_RUN_OWNER_MISMATCH"
        return (
            f"The group gate refused this run's own worker ({denied_code}) on {denied_op}, "
            "so nothing it submitted was registered. A human must clear this: the run is "
            "not resumable."
        )
    return None


def stop_reason_text(stop_code: Optional[str], *, target_seq: Optional[int] = None,
                     detail: Optional[str] = None) -> Optional[str]:
    """The §4.3 sentence for a caller that has no run dict — i.e. the inbox self-chain.

    The inbox decides a stop on the request thread, and often there is no engine run alive to
    ask (a copy-mention chain has none at all). Routing it back through the same table keeps
    ONE sentence per stop code: the worker reading the response, the stored record and the
    miniplayer card all say the same thing.
    """
    return _stop_reason_text(stop_code, {
        "continuation_target_seq": target_seq,
        "inbox_stop_detail": detail,
    })


def mark_chain_stop(group_id: Optional[str], stop_code: str,
                    detail: Optional[str] = None) -> bool:
    """Let the inbox self-chain tag the live run with ITS stop reason (L0007 §4.1-5).

    Returns False when there is no engine run to tag — a copy-mention (semi-manned) chain,
    where the inbox is the only party that can tell a human anything at all.
    """
    if not group_id or not stop_code:
        return False
    run = _svc()._active_run_for_group(group_id)
    if run is None:
        return False
    run["inbox_stop_code"] = stop_code
    run["inbox_stop_detail"] = detail
    return True


# Bounded so a worker that retries a refused call in a loop cannot grow the record without
# limit; the first refusal is the informative one and it is never evicted.
LEASE_DENIAL_RECORD_LIMIT = 10


def mark_group_lease_denied(
    *,
    group_id: Optional[str],
    run_id: str,
    code: str,
    operation: str,
    status_code: int = 403,
    by_worker: bool = True,
) -> bool:
    """Tell the lease-owning run that the group gate turned its own worker away (§2-6).

    Called from `mutation_policy._record_denied_mutation`, off the event loop. Returns False
    when the run is not in this process's registry (a chain resumed after a restart, or a
    lease left behind by a run that already finished) — the caller treats that as a no-op.

    Two things are written, because the incident report asked for both:
      * `register_errors` — NR0003 §3 measured an empty error list on all three dead reviews.
        The refusal belongs in the same list a failed inbox POST lands in.
      * `lease_denied_*` — read by `_resolve_stop_code` / `_stop_reason_text` so the run
        ends with the name `group_lease_denied` and a sentence, instead of exit code 0.
    """
    run = _svc().get_run_record(run_id) or (_svc()._active_run_for_group(group_id) if group_id else None)
    if run is None:
        return False
    with _runs_lock:
        if not by_worker:
            # GROUP_AI_RUN_LOCKED — somebody ELSE was held off while this run worked. Worth
            # having on the run (T0005 §2-6 names both codes), but it is not this run's
            # error and must never end it.
            others = run.setdefault("lease_blocked_others", [])
            if len(others) < LEASE_DENIAL_RECORD_LIMIT:
                others.append({"status": int(status_code), "code": code, "operation": operation})
            return True
        run.setdefault("register_errors", [])
        if len(run["register_errors"]) < LEASE_DENIAL_RECORD_LIMIT:
            run["register_errors"].append({
                "status": int(status_code),
                "reason": f"{code}: {operation}",
                "turn": run.get("turn"),
            })
        run["lease_denied_code"] = code
        run["lease_denied_operation"] = operation
        run["lease_denied_count"] = int(run.get("lease_denied_count") or 0) + 1
    return True


def stamp_chain_stop(
    envelope: dict,
    stop_code: str,
    *,
    project_id: str,
    group_id: Optional[str],
    actor_user_id: Optional[str],
    anchor_doc_id: Optional[str],
    token_id: Optional[str] = None,
    item_seq: Optional[int] = None,
    detail: Optional[str] = None,
) -> dict:
    """One continuation stop, told to everyone who needs it (L0007 §2.11 / §2.12).

    Called by the self-chaining paths (the inbox `new` handler and the decide kickoff), which
    stop a chain while its worker is still holding the HTTP response open. Until now such a
    stop said WHY in that response body alone — and the only reader of that body is a worker
    about to exit, which is how NR0003 §4's chains died with the explanation still in them.

    Three things happen here:

      1. the envelope gets a machine-readable code, the §4.3 sentence and the resumable flag,
      2. the live engine run is tagged so its record and its miniplayer card end up saying the
         same thing the worker was just told (a no-op on a copy-mention chain, which has no
         engine run at all), and
      3. the four stops a human has to clean up leave a notification behind.

    2 and 3 are best-effort: the submitted document is already saved, and nothing here may turn
    that into a failure. The engine and the inbox hold disjoint notify sets, so a stop is
    announced exactly once regardless of which side saw it.
    """
    reason = stop_reason_text(
        stop_code,
        target_seq=envelope.get("continuation_target_seq"),
        detail=detail,
    )
    envelope["continuation_stop_code"] = stop_code
    envelope["continuation_stop_reason"] = reason
    envelope["continuation_resumable"] = is_resumable(stop_code)

    try:
        _svc().mark_chain_stop(group_id, stop_code, detail)
    except Exception:
        logger.warning("chain stop tagging failed for %s (ignored)", group_id, exc_info=True)

    if stop_code not in INBOX_NOTIFY_STOP_CODES:
        return envelope
    try:
        from modules.flow_gate.workflow import event_logger

        # The notification lands on the document that was just submitted: for all four codes
        # that IS the thing needing triage — the stray document, the one waiting on approval,
        # or the last one that did land before the advance failed.
        anchor = (db_docs.get_by_id(anchor_doc_id) or {}) if anchor_doc_id else {}
        # Carry the run this stop belongs to, exactly as the engine's own signal does — NR0003
        # §4 found 1,346 continuous tokens with no bridge back to their execution, and a
        # notification that cannot name its run repeats the same dead end.
        stopped = _svc()._active_run_for_group(group_id) if group_id else None
        event_logger.log_continuous_work_failed(
            project_id=project_id,
            actor_user_id=actor_user_id,
            document_id=anchor.get("id"),
            doc_id=anchor_doc_id,
            group_id=anchor.get("group_id") or group_id,
            run_id=(stopped or {}).get("run_id"),
            error=reason,
            target_seq=envelope.get("continuation_target_seq"),
            extra={"stop_code": stop_code, "token_id": token_id, "item_seq": item_seq},
        )
    except Exception:
        # Same best-effort contract as continuous_work_ended (L0007 §5).
        logger.warning("chain stop signal failed for %s (ignored)", group_id, exc_info=True)
    return envelope


# ── Stop row / record / human signal (0359 L0007 §2.8, §2.10.1, §2.11) ───────


def _apply_stop_row(run: dict, respawn_pending: bool) -> None:
    """Maintain the miniplayer's [resume] card (L0007 §2.8 / §4.5).

    Before 0359 this deleted the row for EVERY end_reason other than "user_paused", which is
    exactly why NR0003 §6's 24 dead chains could not be resumed: the system destroyed the only
    coordinate the resume path takes. A resumable stop now parks a *system* row instead, and
    resume_chain consumes it through the identical path a user pause uses.
    """
    if run.get("mode") != "continuous":
        return
    if respawn_pending:
        return                                       # the next hop is already queued
    if run.get("end_reason") == "user_paused":
        # The user's own row stays (0252 L0009 §3) — but it is REFRESHED, not left as it
        # was: pause_run snapshots at request time, before the in-flight hop reaches its
        # boundary, so the document that completed while the pause was pending would
        # otherwise be missing from what resume/bootstrap reads back (0357 T0004).
        try:
            from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

            row = db_paused.get_by_group(run["group_id"])
            if row is not None:
                db_paused.upsert(
                    group_id=row["group_id"],
                    doc_ref=row["doc_ref"],
                    paused_by=row["paused_by"],
                    paused_at=row["paused_at"],
                    continuation_target_seq=row.get("continuation_target_seq"),
                    docs_target=run.get("docs_target"),
                    docs_reached=int(run.get("docs_reached") or 0),
                    chain_id=run.get("chain_id"),
                    chain_docs_target=run.get("chain_docs_target"),
                    chain_docs_reached=int(run.get("chain_docs_reached") or 0),
                    stop_kind=row.get("stop_kind") or "user",
                    stop_code=row.get("stop_code"),
                    stop_run_id=row.get("stop_run_id"),
                    stop_last_message_excerpt=row.get("stop_last_message_excerpt"),
                    # This upsert overwrites every column, and it ALWAYS runs right after
                    # a pause. Omitting the selections here would erase what pause_run
                    # just stored (0365 DB0004 §5-3 invariant I3).
                    continuation_base_provider_id=run.get("continuation_base_provider_id"),
                    continuation_provider_pinned=run.get("continuation_provider_pinned"),
                    continuation_provider_overrides=run.get("continuation_provider_overrides"),
                    continuation_default_note=run.get("continuation_default_note"),
                    continuation_note_overrides=run.get("continuation_note_overrides"),
                    # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq auto-approve
                    # selection are exactly as perishable as the provider/note selections above
                    # — this same "overwrites every column" upsert is what silently dropped the
                    # mode on every user-paused chain before this fix.
                    continuation_instruction_mode=run.get("continuation_instruction_mode"),
                    continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
                    continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
                    continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
                    # 0414: this refresh runs immediately after pause_run and overwrites every
                    # column — omitting the two maps here would erase what pause_run just wrote.
                    continuation_review_count_overrides=run.get(
                        "continuation_review_count_overrides"),
                    continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
                )
        except Exception:
            logger.warning(
                "ai-invoke paused-row finalization failed for %s", run["run_id"], exc_info=True
            )
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        if not run.get("resumable"):
            # Cancelled / human-triage stops: no card, exactly as before — a ghost "paused"
            # card for a chain that is really over is worse than none.
            db_paused.delete_by_group(run["group_id"])
            return
        existing = db_paused.get_by_group(run["group_id"])
        if existing is not None and (existing.get("stop_kind") or "user") != "system":
            # A row a HUMAN put there outranks one the system would write (§5 stop-row race).
            return
        db_paused.upsert(
            group_id=run["group_id"],
            doc_ref=run["doc_ref"],
            paused_by=run.get("issued_to"),
            paused_at=run.get("finished_at") or now_iso(),
            continuation_target_seq=run.get("continuation_target_seq"),
            docs_target=run.get("docs_target"),
            docs_reached=int(run.get("docs_reached") or 0),
            # A SYSTEM row deliberately carries no chain counters (0357 T0004): the run
            # record this stop points at (stop_run_id) has none either, and resume_chain
            # re-derives the target from the sequence when the row leaves them NULL. The
            # user-pause refresh above does carry them — that row is a snapshot taken
            # mid-chain and would otherwise go stale.
            stop_kind="system",
            stop_code=run.get("stop_code"),
            stop_run_id=run.get("run_id"),
            stop_last_message_excerpt=excerpt(run.get("last_message")),
            # 0365 DB0004's provider/note preference columns are deliberately NOT written
            # here unless 0435's explicit provider pin is active. This branch only creates
            # a row where none existed (a user row outranks it and returns above), so an
            # unpinned system stop must resume through normal doc-type/default resolution.
            #
            # The explicit pin is the narrow exception: it is execution policy, and a manual
            # resume after no-output exhaustion must keep that provider instead of reviving
            # the stored/default provider.
            continuation_base_provider_id=(
                run.get("continuation_base_provider_id")
                if run.get("continuation_provider_pinned")
                else None
            ),
            continuation_provider_pinned=run.get("continuation_provider_pinned"),
            # 0352 T0004 §3.6 deliberately widens this: unlike the provider/note *preference*
            # columns above, instruction_mode is not a "nice-to-have default" — it is the
            # policy the chain is actually running under. A system stop (e.g.
            # no_output_exhausted) that drops it would resume an ai_direct chain back to
            # auto_approved, silently auto-approving N/T the user chose to author themselves
            # — exactly the mode-loss bug this TR fixes. So mode + its per-item_seq selection
            # ARE written on every system row, even though the other four selections are not.
            # flowgate.default.0400 M0005: the per-hop budget pick is policy in the same sense
            # — dropping it here would resume a chain the user picked 240 minutes for back at
            # HOP_TIMEOUT_SEC (60), silently reopening the exact "hop budget too short" report
            # this feature exists to fix. Written alongside instruction_mode for that reason.
            continuation_instruction_mode=run.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
            continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
            # 0414 DB0009 W4 / L0008 §4.3: policy, not preference — same treatment as
            # instruction_mode and the budget above, and for the same reason. A system stop
            # that dropped the selection would resume the chain with the gate switched off,
            # which is the one failure invariant R1 exists to prevent.
            continuation_review_count_overrides=run.get("continuation_review_count_overrides"),
            continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
        )
    except Exception:
        logger.warning("ai-invoke stop-row update failed for %s", run["run_id"], exc_info=True)


def _persist_run_record(run: dict) -> None:
    """Write the one durable row for this hop (L0007 §2.10.1).

    At finalize — while a run is alive, memory is the truth. NR0003 §4: before this, a run
    that ended while the browser happened to be looking at another project left nothing behind
    at all; the only copy of the worker's explanation was a scratch file the server deletes
    after seven days.

    0458 T0007 §2.1: and once more from _resettle_stop_after_park when a post-finalize review
    gate overrules the stop this row was written with. The upsert is keyed on run_id, so the
    second write corrects the same row rather than adding one.
    """
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        stamp = now_iso()
        db_runs.upsert({
            "run_id": run["run_id"],
            "group_id": run["group_id"],
            "project_id": run["project_id"],
            "doc_ref": run["doc_ref"],
            "mode": run["mode"],
            "outcome": run.get("outcome"),
            "docs_reached": int(run.get("docs_reached") or 0),
            "docs_target": run.get("docs_target"),
            "reached_doc_ids": list(run.get("reached_doc_ids") or []),
            "end_reason": run.get("end_reason"),
            "stop_code": run.get("stop_code"),
            "stop_reason": run.get("stop_reason"),
            "resumable": bool(run.get("resumable")),
            "exit_code": run.get("exit_code"),
            "last_message": run.get("last_message"),
            "last_message_excerpt": excerpt(run.get("last_message")),
            "provider_id": run.get("provider_id"),
            "provider_name": (run.get("provider") or {}).get("name"),
            "selected_provider_source": run.get("selected_provider_source"),
            "fallback_allowed": bool(run.get("fallback_allowed")),
            "attempt_no": int(run.get("attempt_no") or 0),
            "attempts_used": int(run.get("attempts_used") or 0),
            "attempts_max": run.get("attempts_max"),
            "fallback_history": list(run.get("fallback_history") or []),
            "register_errors": list(run.get("register_errors") or []),
            "tool_call_misses": int(run.get("tool_call_misses") or 0),
            "turn_limit_exhausted": bool(run.get("turn_limit_exhausted")),
            "oracle_mismatch": bool(run.get("oracle_mismatch")),
            "source_dirty": run.get("source_dirty"),
            "scratch_retained": run.get("scratch_retained"),
            "hop_item_seq": run.get("hop_item_seq"),
            "token_id": run.get("token_id"),
            "issued_to": run.get("issued_to"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": run.get("duration_ms"),
            "timeout_sec": run.get("timeout_sec"),
            "deadline_at": run.get("deadline_at"),
            # ── 0406 T0022 work items 3 and 5 ───────────────────────────────
            # NR0021 §8: the session-scoped handoff note and the final prompt were kept
            # nowhere, so "did the user's text really go in?" could not be decided later.
            # This fills that gap, in one row addressable by run_id.
            "worker_document_type": run.get("worker_document_type"),
            "continuation_instruction_mode_requested": run.get(
                "continuation_instruction_mode_requested"
            ),
            "continuation_instruction_mode_normalized": run.get(
                "continuation_instruction_mode_normalized"
            ),
            "continuation_instruction_mode_fallback_applied": bool(
                run.get("continuation_instruction_mode_fallback_applied")
            ),
            "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
            "prompt_message_source": run.get("prompt_message_source"),
            "prompt_common_default_applied": bool(run.get("prompt_common_default_applied")),
            "prompt_user_message_length": int(run.get("prompt_user_message_length") or 0),
            "prompt_user_message_sha256": run.get("prompt_user_message_sha256"),
            "prompt_final_length": int(run.get("prompt_final_length") or 0),
            "prompt_final_sha256": run.get("prompt_final_sha256"),
            # ── 0446 T0016 §3-2 (migration 086) ─────────────────────────────
            # The four exit diagnostics that were memory-only until now. Every one is read
            # with .get(): a spawn failure never opened a watchdog, an API-mode run has no
            # tails, and neither may turn a finished hop into an unsaved one.
            "timeout_kind": run.get("timeout_kind"),
            "timeout_diagnosis": run.get("timeout_diagnosis"),
            "stdout_tail": run.get("stdout_tail"),
            "stderr_tail": run.get("stderr_tail"),
            "source_dirty_files": list(run.get("source_dirty_files") or []),
            # -- 0505 T0006 (DB0005 3.3) -- API provider server-mediated self-HTTP
            # diagnostics. Read with .get() like the exit diagnostics above: a CLI run,
            # a spawn failure, or a row from before migration 095 has none of this and
            # stores NULL, not zero.
            "operator_api_base": run.get("operator_api_base"),
            "transport_api_base": run.get("transport_api_base"),
            "last_tool_name": run.get("last_tool_name"),
            "last_tool_status": run.get("last_tool_status"),
            "last_tool_error": run.get("last_tool_error"),
            "api_turns_used": run.get("api_turns_used"),
            "model_http_calls": run.get("model_http_calls"),
            "model_last_http_status": run.get("model_last_http_status"),
            "tool_calls_received": run.get("tool_calls_received"),
            "tool_calls_executed": run.get("tool_calls_executed"),
            "api_turn_trace": list(run.get("api_turn_trace") or []),
            "created_at": stamp,
            "updated_at": stamp,
        })
        db_runs.maybe_purge()
        # 0505 T0006 (DB0005 3.3 / NR0003 §18 P0-1): a structured snapshot of the same
        # diagnostic values just persisted, so a hop that leaves an unread run detail
        # behind still leaves this in the process log. `last_tool_error` goes through
        # `_redact_secrets` here -- a log line is a "shown elsewhere" surface exactly
        # like `_no_output_detail`'s (DB0005 §2 masking) -- while the DB row above keeps
        # the raw text (same trust boundary as `register_errors.reason`).
        logger.info(
            "ai-invoke %s: diagnostics register_errors=%d tool_call_misses=%s "
            "turn_limit_exhausted=%s oracle_mismatch=%s last_tool_name=%s "
            "last_tool_status=%s api_turns_used=%s model_http_calls=%s "
            "model_last_http_status=%s tool_calls_received=%s tool_calls_executed=%s "
            "operator_api_base=%s transport_api_base=%s last_tool_error=%s",
            run["run_id"],
            len(run.get("register_errors") or []),
            run.get("tool_call_misses"),
            run.get("turn_limit_exhausted"),
            run.get("oracle_mismatch"),
            run.get("last_tool_name"),
            run.get("last_tool_status"),
            run.get("api_turns_used"),
            run.get("model_http_calls"),
            run.get("model_last_http_status"),
            run.get("tool_calls_received"),
            run.get("tool_calls_executed"),
            run.get("operator_api_base"),
            run.get("transport_api_base"),
            _redact_secrets(
                run.get("last_tool_error"), _known_run_raw_tokens(run), _known_run_prompts(run)
            ),
        )
        _persist_register_context_failures(run, stamp)
    except Exception:
        # L0007 §5: a storage failure must never turn a finished hop into a crashed one.
        logger.warning("ai-invoke run record persist failed for %s", run["run_id"], exc_info=True)


def _persist_register_context_failures(run: dict, stamp: str) -> None:
    """Flush this run's axis-classified binding failures to their own table (T0018 item 3).

    Called from the finalize flow, after ai_invoke_runs.upsert() has put the row the FK
    points at in place. A LIVE run has no such row — which is exactly why the in-memory
    register_errors list is the SSOT until this moment, not a second store racing it.

    Idempotent on (correlation_id, boundary): a re-settled run upserts twice and still
    leaves one row per failure. Storage trouble is logged, never raised — telemetry must
    not be able to turn a finished hop into a crashed one.
    """
    try:
        from modules.flow_gate.db import register_context_failures as db_register_failures

        rows = db_register_failures.rows_from_run_errors(
            run["run_id"], run.get("register_errors") or [],
            recorded_at=stamp,
            fallback={
                "project_id": run.get("project_id"),
                "group_id": run.get("group_id"),
                "doc_ref": run.get("doc_ref"),
            },
        )
        if rows:
            db_register_failures.insert_many(rows)
    except Exception:
        logger.warning("register context telemetry persist failed for %s",
                       run.get("run_id"), exc_info=True)


def _notify_chain_failure_if_needed(
    run: dict,
    *,
    notify_codes: frozenset = ENGINE_NOTIFY_STOP_CODES,
    error_text: Optional[str] = None,
) -> None:
    """Put the stop somewhere a human will still find it tomorrow (L0007 §2.11).

    NR0003 §4: the worker's explanation existed and was even delivered — over SSE, to a browser
    that was looking at a different project in that second, with no way to look back. This
    writes the same fact to the notification feed, which survives not watching.

    0458 T0007 §2.1: `notify_codes` names which stops this speaker owns. It stays
    ENGINE_NOTIFY_STOP_CODES for the finalize call — the §2.11 split (engine set vs inbox set,
    disjoint by construction) is what keeps a double notification impossible there. The one
    caller that widens it is _resettle_stop_after_park, and only because the stop it announces
    (`approve_failed` / `advance_blocked` raised by the engine's own review gate) reaches nobody
    else: the inbox owns those two codes but never sees this branch — no request arrives, the
    engine settled the step itself. `failure_signal_sent` still caps a run at one notification
    however many speakers look at it. `error_text` replaces the attempts-and-last-message
    sentence for a stop whose real explanation is the §4.3 one in `stop_reason`.
    """
    # 0446 T0008 §3-8: scope-oracle rework runs speak here too. `question_pending` is still
    # absent from ENGINE_NOTIFY_STOP_CODES, so a hop waiting on a human answer stays silent.
    if run.get("mode") != "continuous" and not oracle_module._scope_oracle_retry_run(run):
        return
    if run.get("stop_code") not in notify_codes:
        return
    if run.get("failure_signal_sent"):
        return
    run["failure_signal_sent"] = True
    try:
        from modules.flow_gate.workflow import event_logger

        document_pk, document_doc_id = _anchor_document(run)
        event_logger.log_continuous_work_failed(
            project_id=run["project_id"],
            actor_user_id=run.get("issued_to"),
            document_id=document_pk,
            doc_id=document_doc_id,
            group_id=run["group_id"],
            run_id=run["run_id"],
            target_seq=run.get("continuation_target_seq"),
            error=error_text or _failure_error_text(run),
            extra={
                "stop_code": run.get("stop_code"),
                # 0458 T0007 §2.1: the §4.3 sentence rides along, so the feed carries the same
                # exception detail the card shows instead of a bare code.
                "stop_reason": run.get("stop_reason"),
                "token_id": run.get("token_id"),
                "item_seq": run.get("hop_item_seq"),
                "provider_id": run.get("provider_id"),
                "attempts_used": int(run.get("attempts_used") or 0),
                # 0446 T0008 §3-8: the event TYPE stays `continuous_work_failed` — it is the
                # only one `dashboard_service._NOTIFICATION_EVENT_TYPES` carries to the 🔔
                # feed, and `test_run_service` already fires it from a non-continuous context.
                # These two fields are how a reader tells a dead continuous chain apart from a
                # dead single rework run.
                "mode": run.get("mode"),
                "action_scope": run.get("action_scope"),
            },
        )
    except Exception:
        # Same best-effort contract as continuous_work_ended (L0007 §5).
        logger.warning("ai-invoke failure signal failed for %s", run["run_id"], exc_info=True)


def _failure_error_text(run: dict) -> str:
    return (f"{int(run.get('attempts_used') or 0)} attempts produced no document; "
            f"last worker message: {excerpt(run.get('last_message')) or '(none)'}")


def _anchor_document(run: dict) -> tuple[Optional[int], Optional[str]]:
    """Where the notification lands when a human clicks it (L0007 §2.11).

    A dead hop has no document of its own to point at, so it points at the last place the chain
    actually reached — "this much got done" — and falls back to the chain's spine document. An
    anchorless notification still beats a notification nobody gets (L0007 §5).
    """
    candidates: list[str] = []
    reached = run.get("reached_doc_ids") or []
    if reached:
        candidates.append(reached[-1])
    hop_seq = run.get("hop_item_seq")
    try:
        seq = db_wfseq.get_sequence_for_member_doc(run["doc_ref"])
        items = db_wfseq.get_sequence_items(seq["id"]) if seq is not None else []
        previous = [
            item for item in items or []
            if item.get("result_doc_id")
            and item.get("result_doc_review_status") == "approved"
            and (hop_seq is None or (item.get("item_seq") or 0) < int(hop_seq))
        ]
        if previous:
            candidates.append(
                max(previous, key=lambda item: item.get("item_seq") or 0)["result_doc_id"]
            )
    except Exception:
        logger.warning("ai-invoke anchor lookup failed for %s", run["run_id"], exc_info=True)
    candidates.append(run["doc_ref"])
    for doc_id in candidates:
        try:
            doc = db_docs.get_by_id(doc_id) or {}
        except Exception:
            continue
        if doc.get("id") is not None:
            return int(doc["id"]), doc_id
    return None, None


def finished_payload(run: dict) -> dict:
    payload = {
        "run_id": run["run_id"],
        "group_id": run["group_id"],
        "doc_ref": run.get("doc_ref"),
        "outcome": run["outcome"],
        "docs_reached": run["docs_reached"],
        "docs_target": run["docs_target"],
        "chain_id": run.get("chain_id"),
        "chain_docs_target": int(run.get("chain_docs_target") or 0),
        "chain_docs_reached": int(run.get("chain_docs_reached") or 0),
        "reached_doc_ids": run["reached_doc_ids"],
        "end_reason": run["end_reason"],
        "exit_code": run["exit_code"],
        "last_message_received": run["last_message_received"],
        "last_message": run["last_message"],
        "provider_id": run["provider_id"],
        "provider_name": (run.get("provider") or {}).get("name"),
        "selected_provider_source": run.get("selected_provider_source"),
        "fallback_allowed": bool(run.get("fallback_allowed")),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "attempt_no": run["attempt_no"],
        "fallback_history": run["fallback_history"],
        "register_errors": run.get("register_errors", []),
        "tool_call_misses": run.get("tool_call_misses", 0),
        "turn_limit_exhausted": bool(run.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(run.get("oracle_mismatch")),
        # 0505 T0006 (DB0005 3.3): same names as the durable row above, same names
        # _run_detail_from_row uses to restore a finished run after a restart -- one
        # path renders both a live finish and a persisted one.
        "operator_api_base": run.get("operator_api_base"),
        "transport_api_base": run.get("transport_api_base"),
        "last_tool_name": run.get("last_tool_name"),
        "last_tool_status": run.get("last_tool_status"),
        "last_tool_error": run.get("last_tool_error"),
        "api_turns_used": run.get("api_turns_used"),
        "model_http_calls": run.get("model_http_calls"),
        "model_last_http_status": run.get("model_last_http_status"),
        "tool_calls_received": run.get("tool_calls_received"),
        "tool_calls_executed": run.get("tool_calls_executed"),
        "api_turn_trace": list(run.get("api_turn_trace") or []),
        "source_dirty": run["source_dirty"],
        # 0446 T0016 §3-4: the live half of the restart pair. `source_dirty_files` keeps its
        # existing conditional place at the bottom of this function.
        "timeout_kind": run.get("timeout_kind"),
        "timeout_diagnosis": run.get("timeout_diagnosis"),
        "stdout_tail": run.get("stdout_tail"),
        "stderr_tail": run.get("stderr_tail"),
        "duration_ms": run["duration_ms"],
        # 0359 P0006 [stop confirmed]: why it stopped, whether it can be picked up again, how many
        # attempts it cost and against what budget. All additive — no existing field moved.
        "stop_code": run.get("stop_code"),
        "stop_reason": run.get("stop_reason"),
        "resumable": bool(run.get("resumable")),
        # 0393 T0005 §2-6: refusals this run's lease handed to OTHER actors. Live signal
        # only (there is no column for it), but it is what tells a reader that the lease was
        # actually in force while this run was going.
        "lease_blocked_others": list(run.get("lease_blocked_others") or []),
        "hop_item_seq": run.get("hop_item_seq"),
        # 0414 L0008 §5: which KIND of hop this was. A review or rework hop registers no
        # document, so without this a card can only report "0 documents" for a hop that did
        # exactly what it was asked to do.
        "hop_kind": run.get("hop_kind"),
        "hop_review_count": run.get("hop_review_count"),
        "hop_reviewer_provider_id": run.get("hop_reviewer_provider_id"),
        "hop_reviewer_provider_name": run.get("hop_reviewer_provider_name"),
        "token_id": run.get("token_id"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "attempts_max": run.get("attempts_max"),
        "timeout_sec": run.get("timeout_sec"),
        "deadline_at": run.get("deadline_at"),
        "document_review_loop": _svc().document_review_loop_payload(run),
        # 0406 T0022 items 3 and 5: values that let a finished hop's card tell "the N/T
        # vanished" apart from "the TR worker ran fine". Same names on a live run.
        "worker_document_type": run.get("worker_document_type"),
        "continuation_instruction_mode": run.get("continuation_instruction_mode"),
        "continuation_instruction_mode_requested": run.get(
            "continuation_instruction_mode_requested"
        ),
        "continuation_instruction_mode_normalized": run.get(
            "continuation_instruction_mode_normalized"
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            run.get("continuation_instruction_mode_fallback_applied")
        ),
        "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
        "prompt_message_source": run.get("prompt_message_source"),
        "prompt_common_default_applied": bool(run.get("prompt_common_default_applied")),
        "prompt_user_message_length": run.get("prompt_user_message_length"),
        "prompt_user_message_sha256": run.get("prompt_user_message_sha256"),
        "prompt_final_length": run.get("prompt_final_length"),
        "prompt_final_sha256": run.get("prompt_final_sha256"),
    }
    if run["source_dirty"]:
        payload["source_dirty_files"] = run["source_dirty_files"]
    if run["scratch_retained"]:
        payload["scratch_retained"] = run["scratch_retained"]
    return payload
