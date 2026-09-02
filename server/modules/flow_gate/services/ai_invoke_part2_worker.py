# ────────────────────── ai_invoke_service part 2 of 3 — execution, judging, stop rows ──────────────────────
#
# Not imported on its own in production: ai_invoke_service._load_parts() executes this
# file in THAT module's globals(). It is a pure file split (flowgate.default.0497 T0009)
# — the lines were carried over verbatim, nothing was rewritten. See the file-split note
# in ai_invoke_service.py's module docstring.
#
# Holds: the worker and its provider fallback loop, the no-output retry, the progress watchdog, the CLI and API adapters, the register context, judging, finishing and the stop-code records

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from fastapi import HTTPException

from modules.flow_gate.db import conversation_turns as db_conversation_turns
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import questions as db_questions
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate import template_provision
from modules.flow_gate.services import api_server_tools, git_service, invoke_mention_service, process_runner, q_service, register_binding, token_service
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

# Imported directly (tooling that walks the package) rather than executed by
# _load_parts(): the earlier part's names are missing, so take them from the
# assembled module. Under _load_parts() they are already here and this is a no-op.
if "RUN_TIMEOUT_BASE_SEC" not in globals():
    from modules.flow_gate.services import ai_invoke_service as _assembled
    globals().update({k: v for k, v in vars(_assembled).items()
                      if not k.startswith("__")})

# ── Worker: provider fallback loop (L0006 §2.2) ──────────────────────────────

def _worker(run: dict, chain: list[dict], prompt: str) -> None:
    """One hop — one or more attempts (0359 L0007 §2.1).

    Before 0359 this ran `provider loop → classify exit reason → judge+close` with judgment welded onto
    finalize, so there was no seam where "the hop ran and produced NOTHING" could be turned
    back into another attempt. NR0003 §3 is the consequence: the loop had one forward edge
    ("a document was registered") and no other edge at all, so a single wasted lap ended the
    whole chain — silently, with 11 untried providers still on the bench. Judgment and finalize
    are separated here, and the no-output retry lives in the seam between them.
    """
    try:
        current_chain = chain
        current_prompt = prompt
        run["provider"] = _provider_brief(current_chain[0])
        run["provider_id"] = current_chain[0].get("id")
        run["attempt_no"] = 1
        # Exactly once per run, however many attempts follow (P0006 appendix D: no new event
        # types — a retry is reported as a provider switch, which the UI already draws).
        _broadcast(run, "ai_invoke_started", {
            "run_id": run["run_id"],
            "group_id": run["group_id"],
            "doc_ref": run["doc_ref"],
            "mode": run["mode"],
            "started_at": run["started_at"],
            "docs_target": run["docs_target"],
            "chain_id": run["chain_id"],
            "chain_docs_target": run["chain_docs_target"],
            "chain_docs_reached": run["chain_docs_reached"],
            "provider_id": current_chain[0].get("id"),
            "provider_name": current_chain[0].get("name"),
            "attempt_no": 1,
            # 0406 T0022 item 3: the miniplayer builds its card from this payload. Without
            # these the live card has no real worker type until after the run finishes.
            "hop_item_seq": run.get("hop_item_seq"),
            "worker_document_type": run.get("worker_document_type"),
            "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
        })

        while True:
            started_ok = _execute_provider_chain(run, current_chain, current_prompt)
            _classify_end_reason(run, started_ok)
            _judge_hop(run)
            run["attempts_used"] = int(run.get("attempts_used") or 0) + 1

            # A document review loop owns the hop boundary. Checkpoint the durable effect,
            # then either stop or mint the next stage token and continue under its fixed provider.
            if run.get("document_review_loop"):
                loop = _checkpoint_document_review_loop(run)
                run["document_review_loop_checkpointed"] = True
                if not loop or loop.get("current_stage") == "stopped":
                    break
                # 0417 T0013: the reissue inside _prepare_retry_token calls this SAME
                # issue_builder the run started with — without this, a rework hop's reissued
                # token kept the prior review-scope, and POST /inbox action=edit 403'd
                # ("Context binding mismatch") on every rework attempt, so the loop could
                # never reach review_passed. See ai_invoke_routes._issue_review.
                loop_issue_builder = run.get("issue_builder")
                if loop_issue_builder is not None:
                    loop_issue_builder.loop_stage = loop["current_stage"]
                prepared = _prepare_retry_token(run)
                selected_id = resolve_loop_provider(loop, loop["current_stage"])
                enabled = ai_settings_service.resolve_effective(run["project_id"]).get("providers") or []
                selected = next((item for item in enabled if item.get("id") == selected_id), None)
                if prepared is None or selected is None:
                    run["outcome"] = "none"
                    run["end_reason"] = "document_review_loop_transition_failed"
                    run["last_message"] = "next review-loop stage could not be scheduled"
                    run["document_review_loop_checkpointed"] = False
                    break
                _reset_attempt_state(run)
                run["hop_kind"] = loop["current_stage"]
                run["provider"] = _provider_brief(selected)
                run["provider_id"] = selected_id
                run["attempt_no"] = int(run.get("attempt_no") or 0) + 1
                run["document_review_loop_checkpointed"] = False
                current_chain = [selected]
                stage_message = (
                    loop.get("rework_message") if loop["current_stage"] == REWORK_HOP_KIND
                    else f"Review {run['doc_ref']} using {loop.get('review_criteria')}."
                )
                current_prompt = f"{stage_message}\n\n{prepared['mention']}" if stage_message else prepared["mention"]
                continue

            if not _retry_eligible(run):
                break
            if not _recheck_no_output(run):
                break
            next_chain = _retry_provider_chain(run)
            if not next_chain:
                run["retry_block_reason"] = "providers_exhausted_for_retry"
                break
            prepared = _prepare_retry_token(run)
            if prepared is None:
                run["retry_block_reason"] = "token_unavailable"
                break

            previous = run.get("provider") or {}
            _archive_attempt(run, "no_output", prepared["token_id_before"])
            _reset_attempt_state(run)
            current_chain = next_chain
            current_prompt = prepared["mention"]
            run["provider"] = _provider_brief(current_chain[0])
            run["provider_id"] = current_chain[0].get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _broadcast(run, "ai_invoke_provider_switched", {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "from_provider_id": previous.get("id"),
                "from_provider_name": previous.get("name"),
                "to_provider_id": current_chain[0].get("id"),
                "to_provider_name": current_chain[0].get("name"),
                # 0359 P0006 [core] 3: the fourth switch reason. The existing three all mean
                # "it never started"; this one means "it started, finished politely and left
                # nothing" — the shape this incident actually had, and the more common one.
                "reason": "no_output",
                "detail": run["fallback_history"][-1].get("detail"),
                "attempt_no": run["attempt_no"],
                "retry_kind": "no_output",
                "hop_item_seq": run.get("hop_item_seq"),
                "token_id": prepared["token_id"],
                "token_reissued": prepared["reissued"],
            })

        _finalize_run(run)
        # 0317 TR0011 (Q153 opt-1): the run is now finished, so start_run's active-run guard
        # is clear — re-spawn the next hop's worker if the self-chain flagged a boundary.
        _maybe_auto_resume_hop(run)
    except Exception:
        logger.exception("ai-invoke worker crashed for %s", run["run_id"])
        run["end_reason"] = run.get("end_reason") or "exited"
        try:
            # A crashed attempt is never retried (L0007 §2.1): judge what it left, close out.
            _judge_hop(run)
            _finalize_run(run)
        except Exception:
            logger.exception("ai-invoke settle failed for %s", run["run_id"])
            run["status"] = "finished"
        # A crashed hop is a real stop, not a boundary: drop any pending re-spawn so the
        # chain does not silently continue past a failure.
        # 0406 T0022 item 4 — drop the queue, keep the intent. A crash is the third branch
        # that does not spawn: _finalize_run only calls begin_handoff when it sees pending
        # and skips release, so just clearing it blocks the group until the lease expires.
        # A durable row lets the user resume the chain from the same place.
        crashed_pending = pop_auto_resume(run.get("group_id"))
        if crashed_pending is not None:
            crashed_code = run.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE
            if crashed_code == HOP_HANDOFF_STOP_CODE:
                crashed_code = HOP_HANDOFF_FAILED_STOP_CODE
            _park_handoff(run, crashed_pending, crashed_code)

def _execute_provider_chain(run: dict, chain: list[dict], prompt: str) -> bool:
    """One attempt: walk the provider chain until one actually STARTS (L0006 §2.2).

    Unchanged in substance — startup/transport failures (spawn_failed / fast_fail / api_error)
    fall through to the next provider, and the first provider that runs at all ends the walk.
    Whether its work was any good is the judge's question, never this loop's.
    """
    started_ok = False
    last_reason = None
    for index, provider in enumerate(chain):
        # Count the initial launch as attempt 1 as well as later fallback launches.
        run["attempt_no"] = len(run["fallback_history"]) + 1
        if run["cancel_event"].is_set():
            break
        if index > 0:
            prev = chain[index - 1]
            run["provider"] = _provider_brief(provider)
            run["provider_id"] = provider.get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _broadcast(run, "ai_invoke_provider_switched", {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "from_provider_id": prev.get("id"),
                "from_provider_name": prev.get("name"),
                "to_provider_id": provider.get("id"),
                "to_provider_name": provider.get("name"),
                "reason": last_reason,
                "attempt_no": run["attempt_no"],
                # 0359: name the KIND of switch, now that there is more than one kind.
                "retry_kind": "spawn_failure",
                "hop_item_seq": run.get("hop_item_seq"),
            })

        if provider.get("exec_type") == "api":
            classification, detail = _api_execute(provider, prompt, run)
        else:
            classification, detail = _cli_execute(provider, prompt, run)

        if classification == "started_ok":
            started_ok = True
            break
        last_reason = classification
        run["fallback_history"].append({
            "provider_id": provider.get("id"),
            "provider_name": provider.get("name"),
            "reason": classification,
            "detail": detail,
            "attempt_no": run.get("attempt_no"),
            "token_id": run.get("token_id"),
        })
        if run["cancel_event"].is_set():
            break

    if not started_ok:
        run["provider_id"] = None
        run["provider"] = None
    return started_ok

def _classify_end_reason(run: dict, started_ok: bool) -> None:
    """end_reason classification (L0006 §4.1 / L0007 §2.1.2) — order unchanged, default "exited".

    "user_paused" (0252 P0008 S4): the inbox self-chain hit the user's pause flag at a step
    boundary and withheld the next token; the worker then exits normally, so the flag — not the
    exit itself — is what distinguishes a boundary stop.

    Producing nothing is deliberately NOT an end_reason. It is a JUDGMENT (outcome == "none"),
    and it is the combination of a clean "exited" with that judgment that opens a retry.
    """
    if not started_ok and not run["cancel_event"].is_set():
        run["end_reason"] = "all_providers_failed"
    elif run["cancel_event"].is_set():
        run["end_reason"] = "cancelled"
    elif run["timed_out"]:
        run["end_reason"] = "timeout"
    elif run.get("user_paused"):
        run["end_reason"] = "user_paused"
    else:
        run["end_reason"] = "exited"

# ── No-output retry (0359 L0007 §2.3 ~ §2.6) ─────────────────────────────────

def _attempt_elapsed_sec(run: dict) -> int:
    started = run.get("attempt_started_mono") or run.get("started_mono") or time.monotonic()
    return max(0, int(time.monotonic() - started))

def _has_pending_question(doc_ref: Optional[str]) -> bool:
    """NR0003 follow-up proposal 1: does doc_ref carry a query still waiting on the human?

    q_service.add_questions/register_answer keep the container's status 'pending' for as
    long as any item has answer_count=0, and flip it to 'done' only once every item is
    answered — so 'pending' here means exactly "a Q was just registered and nobody has
    answered it yet", never a stale done container. An AI that registers a Q and exits
    (mention_service._REMINDER_TEXT explicitly tells it to) produced nothing on purpose;
    treating that hop the same as one that silently failed is the defect this guards.

    The registration router does not query doc_ref directly either — past the first hop
    it reanchors the container through q_service.resolve_question_anchor before touching
    it (q_tapi_routes.py). Querying doc_ref (the run's spine) here would silently miss
    every container the router actually wrote once that reanchoring moved off the spine.
    """
    if not doc_ref:
        return False
    try:
        anchor = q_service.resolve_question_anchor(doc_ref)
        container = db_questions.get_container_by_doc(anchor)
    except Exception:
        logger.warning("ai-invoke pending-question probe failed for %s", doc_ref, exc_info=True)
        return False
    return bool(container) and container.get("status") == "pending"

def _retry_eligible(run: dict) -> bool:
    """May this hop open ANOTHER attempt? (L0007 §2.4 — this exact order.)

    Condition 3 is the axis the whole fix turns on. The existing provider-switch test is
    "exit code != 0 within 15 seconds", which the incident's worker passed cleanly: it ran for
    145 seconds, reported that it could not work, and exited 0. That is not a startup failure —
    it is a completed attempt with nothing to show, and it needs an edge of its own.
    """
    scope_retry = _scope_oracle_retry_run(run)
    if run.get("mode") != "continuous" and not scope_retry:
        return False
    cancel_event = run.get("cancel_event")
    if cancel_event is not None and cancel_event.is_set():
        return False
    if run.get("end_reason") != "exited":
        # cancelled / timeout / user_paused are human or clock decisions — reviving them would
        # override the person who made them. all_providers_failed is already the provider
        # walk's own verdict, reached inside the attempt.
        return False
    if run.get("pause_requested"):
        return False
    if run.get("completion_oracle") is not None and not scope_retry:
        # 0446 T0008 §3-1: the ENGINE's own scope default judge is re-askable —
        # `_recheck_no_output` below does exactly that before a second worker starts. A
        # caller's override is not, so it keeps the original block.
        return False
    if run.get("action_scope") in ("workflow_decide", "resolve_conflict"):
        return False
    if int(run.get("docs_target") or 0) < 1 and not scope_retry:
        # 0446 T0008 §3-2: start_run pins a scope-oracle run's docs_target to 0 because its
        # token cannot register a document at all. For that run the count is not a low
        # number — it is not a measurement.
        return False
    review_hop_recovery = _review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
    )
    if peek_auto_resume(run.get("group_id")) is not None and not review_hop_recovery:
        # flowgate.default.0466 T0007: this check predates the review gate (0359 L0007
        # §2.4) and reads a queue entry as proof THIS hop already produced a document and
        # handed off — true for the continuous chains it was written for, where
        # `request_auto_resume` is only ever called from the inbox AFTER a submission. But
        # `run_review_gate`'s review/rework dispatch (0414 L0008 §2.4, "queue first, then
        # launch") calls `_queue_gate_bundle` — the SAME `request_auto_resume` — BEFORE
        # spawning the hop at all, so a review hop reliably finds its own dispatcher's
        # queue entry sitting here on attempt 1, before it has run at all, and this check
        # silently ate every retry: A10's `attempts_max=2` never got past 1 in production
        # (confirmed by driving the real `run_review_gate` → `_spawn_review_hop` →
        # `_worker` path, not just the worker or the gate alone). A review hop's own token
        # structurally cannot register a document (§2.5: "a review token carries NO
        # continuation_target_seq" and `docs_target` is pinned to 0), so its worker can
        # never be the reason a NEW queue entry appears mid-run — every entry it can ever
        # see here is the pre-spawn one, and reading that as "already handed off" is
        # simply wrong for this hop kind. `_scope_oracle_retry_open` (rework) keeps the
        # existing behavior: a rework's `edit` token DOES submit a document mid-run, so a
        # queue entry appearing there can be the real thing this check exists to catch.
        return False        # this hop DID hand off; the next hop is already queued
    if int(run.get("docs_reached") or 0) >= 1:
        return False        # partial output is still output — a rerun would double-write
    if scope_retry and run.get("outcome") != "none":
        # 0446 T0008 §3-2: the guard directly above cannot see a scope-oracle SUCCESS —
        # `_judge_hop` pins docs_reached to 0 for every scoped run, satisfied or not. Without
        # this line a rework that correctly raised the document's revision would qualify for a
        # retry and write a SECOND revision over its own work. The scoped equivalent of
        # "output is output" is the judge's own verdict, so require it explicitly.
        return False
    # Reached with outcome == "none": a hop that only registered a Q looks exactly like one
    # that died, and the guard below is the only thing that tells them apart (§3-2).
    if _has_pending_question(run.get("doc_ref")):
        # NR0003 follow-up proposal 1: this hop stopped to wait for a human answer, not because
        # it failed — spending another attempt (and another provider) on a question the
        # human has not even seen yet would waste both without ever getting a different
        # outcome. _resolve_stop_code below reads this back into "question_pending" instead
        # of "no_output_exhausted" so the false-failure notification never fires.
        run["retry_block_reason"] = "question_pending"
        return False
    # 0443 T0002 (R0001): the cap is now the run's own resolved "재시작 횟수" pick
    # (attempts_max), not always the fixed constant — -1 means unlimited.
    attempts_max = run.get("attempts_max")
    if attempts_max is None:
        attempts_max = NO_OUTPUT_MAX_ATTEMPTS
    if attempts_max != -1 and int(run.get("attempts_used") or 0) >= attempts_max:
        return False
    if _retry_remaining_sec(run) < RETRY_MIN_REMAINING_SEC:
        # 0446 T0014 §4-4: the reading, not the gate, changed — `_retry_remaining_sec` is
        # min(no-progress clock, absolute ceiling). A hop that ran 90 productive minutes on
        # a 60-minute threshold has a NEGATIVE `_remaining_sec` and would have been refused
        # a retry for a budget it never actually exhausted. With no watchdog anchor recorded
        # the two readings are identical, so every case below still decides as it did.
        # 0446 T0010 §3-4 — the hole T0008 §3-6 / TR0009 §7-4 explicitly left to R5. This gate
        # blocked the retry and said nothing. `_stop_reason_text` already listed "a budget too
        # far spent" among the reasons no further attempt opened, but no code ever SET that
        # reason, so the only durable field (stop_reason) read "produced no document in N
        # attempts" and the real cause never left the process. No new stop_code: the reason
        # rides the existing "No further attempt was opened: <reason>." tail on
        # no_output_exhausted. Ordered BELOW the question_pending probe on purpose — a Q stop
        # is not a failure (T0008 §3-2) and must keep its own name.
        run["retry_block_reason"] = "budget_exhausted"
        return False
    # register_errors / tool_call_misses / turn_limit_exhausted deliberately do NOT block:
    # "tried to register and failed" is still zero documents, and another AI may get through.
    return True

def _recheck_no_output(run: dict) -> bool:
    """Last gate before a retry: is the hop STILL empty? (L0007 §5 — the last guard against duplicate documents.)

    The judge already waited out the settle window, but a registration can land between that
    judgment and the moment a second worker would start. Two documents from one hop is worse
    than one wasted hop, so ask again and cancel the retry if anything appeared.
    """
    if _scope_oracle_retry_run(run):
        # 0446 T0008 §3-4: a rework run never creates a document, so the document query below
        # would answer "still empty" for it under every circumstance — turning the one guard
        # standing between a late write and a duplicate one into a constant True. Ask the
        # scope's own judge again instead, exactly as `_judge_hop` did.
        oracle = run.get("completion_oracle")
        if oracle is None:
            return False
        try:
            satisfied = bool(oracle())
        except Exception:
            logger.warning("ai-invoke scoped retry recheck failed for %s",
                           run["run_id"], exc_info=True)
            # Deliberately the opposite of the document path's fallback below: when the judge
            # cannot answer, a wasted hop is cheaper than a second revision written over the
            # worker's own (L0007 §5).
            return False
        if not satisfied:
            return True
        # The same shape `_judge_hop` gives a satisfied scope oracle.
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete"
        run["oracle_mismatch"] = False
        return False
    try:
        new_docs = _oracle_new_docs(run)
    except Exception:
        logger.warning("ai-invoke retry recheck failed for %s", run["run_id"], exc_info=True)
        return True
    if not new_docs:
        return True
    hop_target = run.get("docs_target") or 1
    if run.get("mode") == "continuous" and peek_auto_resume(run.get("group_id")) is not None:
        hop_target = 1
    run["docs_reached"] = len(new_docs)
    run["reached_doc_ids"] = [d["doc_id"] for d in new_docs]
    run["outcome"] = "complete" if len(new_docs) >= hop_target else "partial"
    run["oracle_mismatch"] = False
    return False

def _retry_provider_chain(run: dict) -> list[dict]:
    """Return the same selected provider for this hop's no-output retry.

    0435 T0004 replaces both older schedules — individual -> individual -> common and
    configured-order fallback — with one contract for every origin tier: a retry NEVER
    replays the priority tiers or falls back to another provider, only the finalized chain
    head. 0443 T0002 (R0001) makes how MANY retries fire a per-run pick ("재시작 횟수")
    instead of a fixed one-shot — `attempts_max` (see _resolve_restart_max_attempts) is
    the same cap `_retry_eligible` already checked before calling this, re-checked here
    as a second line of defense; -1 means unlimited.
    """
    attempts_used = int(run.get("attempts_used") or 0)
    attempts_max = run.get("attempts_max")
    if attempts_max is None:
        attempts_max = NO_OUTPUT_MAX_ATTEMPTS
    if attempts_max != -1 and attempts_used >= attempts_max:
        return []
    if _review_hop_recovery_run(run):
        # T0007 §2.3/§3.1.5: retry the provider that ACTUALLY STARTED this attempt
        # (`run["provider_id"]`, set by `_execute_provider_chain` even when it had to walk
        # past an earlier startup failure in the SAME attempt), never the original
        # priority-tier head. A reviewer-override hop's chain is already a single provider
        # so this is a no-op for it; an unpinned review hop's chain can hold several, and
        # the no-verdict retry must not re-walk that tier a second time.
        selected_provider_id = (
            run.get("provider_id") or run.get("continuation_selected_provider_id")
        )
    else:
        selected_provider_id = run.get("continuation_selected_provider_id")
    if not selected_provider_id:
        return []
    try:
        effective = ai_settings_service.resolve_effective(run["project_id"])
        chain = effective.get("providers") or []
    except Exception:
        logger.warning("ai-invoke retry chain lookup failed for %s", run["run_id"], exc_info=True)
        return []
    selected = next(
        (provider for provider in chain if provider.get("id") == selected_provider_id),
        None,
    )
    return [selected] if selected else []

def _token_reusable(row: dict) -> bool:
    """Can the dead attempt's work token simply be handed to the next provider?

    Not merely "is it alive": a token with two minutes left would expire mid-attempt, and
    handing a worker a token that dies under it is worse than minting a fresh one (L0007 §2.5).
    """
    if row.get("consumed_at") or row.get("revoked_at"):
        return False
    expires_at = row.get("expires_at")
    if not expires_at:
        return False
    try:
        remaining = (
            datetime.fromisoformat(str(expires_at)) - datetime.now(timezone.utc)
        ).total_seconds()
    except Exception:
        return False
    return remaining >= RETRY_MIN_REMAINING_SEC

def _prepare_retry_token(run: dict) -> Optional[dict]:
    """A usable work token + prompt for the next attempt (L0007 §2.5).

    Reuse first. A dead hop's token is normally neither consumed nor revoked — NR0003 §6 found
    24 of them sitting in the tokens table, one being this incident's own tok_20260730_000032 —
    so the next provider can just be handed the same one. Reissue only when it is spent, revoked
    or about to expire: advance_workflow re-opens the SAME head (this hop registered nothing, so
    the effective head has not moved) and revokes the stale token itself, which is why this can
    neither double-issue nor skip a slot. No reissue path at all means no retry.
    """
    token_id = run.get("token_id")
    row = None
    if token_id:
        try:
            row = db_tokens.get_by_id(token_id)
        except Exception:
            logger.warning("ai-invoke retry token lookup failed for %s",
                           run["run_id"], exc_info=True)
            row = None
    if row is not None and _token_reusable(row):
        return {"mention": run.get("mention"), "token_id": token_id,
                "token_id_before": token_id, "reissued": False}

    issue_builder = run.get("issue_builder")
    if issue_builder is None:
        return None
    try:
        issue = _call_issue_builder(issue_builder, run["run_id"])
    except Exception:
        logger.warning("ai-invoke retry token reissue failed for %s",
                       run["run_id"], exc_info=True)
        return None
    mention = issue.get("mention")
    if not mention:
        return None
    if run.get("mode") == "continuous" or (
        run.get("mode") == "single" and run.get("action_scope") == "new"
    ):
        retry_audit: dict = {}
        mention = _inject_hop_notes(
            mention,
            run["doc_ref"],
            default_note=run.get("continuation_default_note"),
            note_overrides=run.get("continuation_note_overrides"),
            instruction_mode=run.get("continuation_instruction_mode"),
            auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            fold_worker_item_seq=(run.get("mode") == "continuous"),
            locale=run.get("continuation_locale"),
            audit=retry_audit,
        )
        # 0406 T0022 item 5: a retry rebuilds the prompt from scratch, so the audit must
        # point at THAT prompt — otherwise attempt 1's hash gets attached to attempt 2's
        # run while still claiming "the note went in".
        run.update(retry_audit)
    before = token_id
    run["token_id"] = issue.get("token_id")
    if run.get("group_id"):
        # 0417 T0013: a document_review_loop hop's reissued token can carry a DIFFERENT
        # action_scope than the run started with (review <-> edit as the loop alternates
        # stages) — refresh the lease's recorded scope to match, or mutation_policy's
        # owner-match check 403s the very next real call this token makes. Every other
        # (non-loop) run keeps one scope for its whole lifetime, so this lookup is skipped
        # for them and update_token's action_scope stays None (no behavior change).
        reissued_action_scope = None
        if run.get("document_review_loop") and run["token_id"]:
            reissued_token = db_tokens.get_by_id(run["token_id"])
            reissued_action_scope = reissued_token.get("action_scope") if reissued_token else None
        db_group_ai_leases.update_token(
            run["group_id"], run["run_id"], run["token_id"], action_scope=reissued_action_scope,
        )
    run["raw_token"] = issue.get("raw_token") or run.get("raw_token")
    _note_issued_raw_token(run, run.get("raw_token"))
    run["mention"] = mention
    _note_issued_prompt(run, mention)
    run["prompt_final_length"], run["prompt_final_sha256"] = prompt_digest(mention)
    if issue.get("worker_document_type"):
        run["worker_document_type"] = issue.get("worker_document_type")
    if issue.get("auto_handled_item_seqs") is not None:
        run["auto_handled_item_seqs"] = list(issue.get("auto_handled_item_seqs") or [])
    return {"mention": mention, "token_id": issue.get("token_id"),
            "token_id_before": before, "reissued": True}

def _no_output_detail(run: dict) -> Optional[str]:
    """The sentence that finally reaches a human (L0007 §2.6).

    In the incident this text existed, was precise, and even named its own fix — and lived only
    in a scratch file the server deletes after seven days.

    0505 T0009: API providers branch to diagnosis by category (exit_code / register_errors /
    tool_call_misses / turn_limit_exhausted) instead of generic "worker exited"; CLI providers
    keep the generic form for backward compat.

    0505 T0010: API provider exit None now shows status by category (completed/failed) rather
    than generic "worker exited None".
    """
    provider = run.get("provider") or {}
    is_api_provider = provider.get("exec_type") == "api"

    if is_api_provider:
        # API provider: return diagnostic category first
        register_errors = run.get("register_errors") or []
        if register_errors:
            reasons = []
            for err in register_errors:
                reason = err.get("reason", "registration error")
                status = err.get("status")
                reasons.append(f"{reason}{'/' + str(status) if status else ''}")
            return f"register failed: {'; '.join(reasons)}"

        if run.get("turn_limit_exhausted"):
            return "worker stopped: turn limit exhausted"

        tool_misses = run.get("tool_call_misses") or 0
        if tool_misses > 0:
            return f"worker stopped: tool not called {tool_misses} time(s)"

        if run.get("oracle_mismatch"):
            return "worker stopped: oracle mismatch detected"

        # Fallback: check exit status to determine completed vs failed
        exit_code = run.get("exit_code")
        if exit_code is None:
            return "completed: no output to register"
        else:
            head = (
                f"failed: worker exited {exit_code} after {_attempt_elapsed_sec(run)}s "
                "without registering a document"
            )
            message = excerpt(run.get("last_message"))
            return head if not message else f"{head}; last message: {message}"
    else:
        # CLI provider: keep generic form for backward compat
        head = (
            f"worker exited {run.get('exit_code')} after {_attempt_elapsed_sec(run)}s "
            "without registering a document"
        )
        message = excerpt(run.get("last_message"))
        return head if not message else f"{head}; last message: {message}"

def _archive_attempt(run: dict, reason: str, token_id: Optional[str]) -> None:
    """Fold the attempt that just ended into the history (L0007 §2.6)."""
    run["fallback_history"].append({
        "provider_id": run.get("provider_id"),
        "provider_name": (run.get("provider") or {}).get("name"),
        "reason": reason,
        "detail": _no_output_detail(run),
        "token_id": token_id,
        "attempt_no": run.get("attempt_no"),
        "exit_code": run.get("exit_code"),
        "duration_sec": _attempt_elapsed_sec(run),
    })
    # Keep the most recent NON-EMPTY message: a later attempt may say nothing at all, and the
    # sentence that explains the failure is usually the one an earlier attempt left behind.
    if run.get("last_message"):
        run["last_message_seen"] = run["last_message"]

def _reset_attempt_state(run: dict) -> None:
    """Clear the per-ATTEMPT observations, keep the per-HOP identity (L0007 §2.6).

    run_id / started_mono / timeout_sec / deadline_at / baseline_seq all survive: one hop gets
    one budget and one baseline no matter how many attempts it spends inside them.
    """
    run["exit_code"] = None
    run["timed_out"] = False
    run["last_message"] = None
    run["last_message_received"] = False
    run["stdout_tail"] = None
    run["stderr_tail"] = None
    run["register_errors"] = []
    run["tool_call_misses"] = 0
    run["turn_limit_exhausted"] = False
    # 0505 T0006 (DB0005 3.3): "this attempt's last mediated self-HTTP call", not a
    # cross-attempt history -- reset alongside the three siblings above.
    run["last_tool_name"] = None
    run["last_tool_status"] = None
    run["last_tool_error"] = None
    # 0505 T0008: re-derive next attempt's transport base rather than trust a value
    # cached from a prior attempt -- the recompute is cheap and this keeps the cache
    # from ever outliving the per-attempt state it was read alongside.
    run["_transport_api_base_resolved"] = None
    run["attempt_started_mono"] = time.monotonic()
    # 0446 T0014 §4-1: the previous attempt's watchdog verdict is not this one's. The
    # no-progress window is re-anchored when the next watchdog starts; the absolute
    # ceiling is not, because started_mono survives this reset by the contract above.
    run["watchdog_kill"] = None
    # codex writes its final message to a file in the run scratch. Leaving the previous
    # attempt's file behind would let the next attempt inherit words it never said.
    try:
        stale = Path(run["scratch_dir"]) / "last_message.txt"
        if stale.is_file():
            stale.unlink()
    except Exception:
        logger.warning("ai-invoke stale last-message cleanup failed for %s",
                       run["run_id"], exc_info=True)

def _now_mono() -> float:
    """The monotonic clock behind ONE name, so the watchdog's 30-minute and 4-hour
    edges can be exercised without waiting for them (0446 T0014 §5). Nothing else
    about it differs from calling time.monotonic() directly."""
    return time.monotonic()

def _remaining_sec(run: dict) -> float:
    """The hop's nominal budget measured from hop START — unchanged, and still exactly
    what the `timeout_sec` / `deadline_at` of every response and stored row means.

    0446 T0014 §2-5: that deadline is now the EARLIEST this run may be stopped rather
    than the latest, and it lands only if the run shows nothing new for the whole
    budget. The two helpers below are the ones the watchdog and the retry gate ask.
    """
    return run["timeout_sec"] - (time.monotonic() - run["started_mono"])

def _stall_remaining_sec(run: dict) -> float:
    """Seconds left on the NO-PROGRESS clock (0446 T0014 §2-3).

    The same budget with a different anchor: it runs from the last point this run was
    known to be moving (`stall_anchor_mono` — set when an attempt launches, moved
    forward by every observed document or source change) instead of from hop start.
    With no anchor recorded this is precisely `_remaining_sec`, which is why a run that
    never had a watchdog keeps its previous behaviour to the second.
    """
    anchor = run.get("stall_anchor_mono")
    if anchor is None:
        anchor = run["started_mono"]
    return run["timeout_sec"] - (_now_mono() - anchor)

def _absolute_cap_sec() -> int:
    """The run's hard ceiling, in seconds (0446 T0014 §2-4).

    Deliberately RUN_TIMEOUT_CAP_SEC itself, read at CALL time instead of copied into
    a second four-hour literal: that constant is already four hours, already the roof
    of the per-document formula and already what a `target_to_end` run gets, so a
    duplicate could only drift away from it. Reading it through a function is also
    what keeps `test_ai_invoke_0187.py::TestForcedKill` honest — it shortens the cap to
    one second to prove the timeout path, and a bound-at-import alias would have
    silently ignored it.
    """
    return RUN_TIMEOUT_CAP_SEC

def _absolute_remaining_sec(run: dict) -> float:
    """Seconds left before the run's hard ceiling (0446 T0014 §2-4).

    Measured from `started_mono` — the HOP's start, not the attempt's — so a no-output
    retry inherits what is left of the four hours instead of being handed a fresh four.
    """
    return _absolute_cap_sec() - (_now_mono() - run["started_mono"])

def _retry_remaining_sec(run: dict) -> float:
    """The budget the retry gate asks about: how long could another attempt run?

    0446 T0014 §4-4: `_remaining_sec` alone would answer "none" for every hop that
    legitimately outlived its no-progress threshold BY WORKING, and `_retry_eligible`
    would then report `budget_exhausted` for a run with hours of ceiling left. Both
    limits are real, so the smaller one is the answer.
    """
    return min(_stall_remaining_sec(run), _absolute_remaining_sec(run))

def _work_landed(run: dict) -> bool:
    """Did this run already produce something? Fast-fail's "nothing was lost" check.

    0259 B0001 §3: this used to be a raw group max-seq delta for every run. On a run whose
    product is not a document that is False however well the worker did, so a worker that
    finished its edit and then exited nonzero inside the fast-fail window was re-run on the
    next provider. Ask the run's own judge — the scope default or the caller's override —
    and only fall back to the seq delta for the document-producing scopes it is true for.

    NOTE this is deliberately NOT the judge's `_oracle_new_docs` (non-draft docs past the
    baseline): the seq delta is the wider net, and counting a stray draft here only makes
    fast-fail more conservative, which is the safe direction for a "may I discard this
    attempt?" question.
    """
    oracle = run.get("completion_oracle")
    if oracle is not None:
        try:
            return bool(oracle())
        except Exception:
            logger.warning("ai-invoke fast-fail oracle failed for %s", run["run_id"], exc_info=True)
            return False
    try:
        return db_docs.get_group_max_seq(run["group_id"]) > run["baseline_seq"]
    except Exception:
        return False

def _truncate_front(text: Optional[str], max_bytes: int = LAST_MESSAGE_MAX_BYTES) -> Optional[str]:
    """Keep the tail (the dying message's end matters most), drop the front."""
    if text is None:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", errors="replace")

def _resolve_agent_api_base(operator_api_base: str) -> str:
    """Compute the address this server can reach itself at.

    Shared by external CLI process launch and, since 0505 T0008, the API provider's
    server-mediated self-HTTP (`_resolve_transport_api_base`) -- both ask the same
    question, "what address can this server use to reach itself?", and now get the
    same answer. The browser/operator origin remains in the stored run and in the
    help text/prompts a person reads. A configured agent origin wins; otherwise the
    operator scheme and explicit port are retained while the host becomes loopback.
    When the operator origin has no explicit port, the trusted local
    ``FLOWGATE_PORT`` is used.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not operator_api_base:
        return operator_api_base
    parts = urlsplit(operator_api_base)
    try:
        operator_port = parts.port
    except ValueError as exc:
        raise ValueError(f"Invalid operator API base port: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            "operator API base must be an absolute http(s) URL with a hostname"
        )

    from config import settings as _settings

    configured = getattr(_settings, "FLOWGATE_AGENT_API_BASE", None)
    if configured is not None:
        setting = str(configured).strip()
        if not setting:
            raise ValueError(
                "FLOWGATE_AGENT_API_BASE must not be empty or whitespace"
            )
        agent = urlsplit(setting)
        try:
            agent_port = agent.port
        except ValueError as exc:
            raise ValueError(f"Invalid FLOWGATE_AGENT_API_BASE port: {exc}") from exc
        if (
            agent.scheme not in ("http", "https")
            or not agent.hostname
            or agent.username is not None
            or agent.password is not None
            or agent.path not in ("", "/")
            or agent.query
            or agent.fragment
        ):
            raise ValueError(
                "FLOWGATE_AGENT_API_BASE must be an http(s) origin "
                "(scheme://host[:port])"
            )
        netloc = agent.hostname
        if ":" in netloc:
            netloc = f"[{netloc}]"
        if agent_port is not None:
            netloc += f":{agent_port}"
        path = parts.path.rstrip("/")
        return urlunsplit((agent.scheme, netloc, path, parts.query, ""))

    port = operator_port
    if port is None:
        port = int(_settings.FLOWGATE_PORT)
        if not 1 <= port <= 65535:
            raise ValueError("FLOWGATE_PORT must be between 1 and 65535")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, f"127.0.0.1:{port}", path, parts.query, ""))

def _sanitize_diagnostic_base(url: Optional[str]) -> Optional[str]:
    """Strip a live `api_base_url` down to a safe diagnostic snapshot (DB0005 2, 3.3, 5-5).

    `operator_api_base`/`transport_api_base` store what this returns, never the raw
    value: run["api_base_url"] is browser/operator-supplied and unvalidated end to end
    (ai_invoke_routes._operator_facing_api_base -> token_routes._build_api_base ->
    str(request.base_url) -> ai_invoke_service.start_run(api_base_url=...)), and
    _resolve_agent_api_base's userinfo check only fires on its FLOWGATE_AGENT_API_BASE
    branch -- CLI providers, never this API path. Anything that fails to parse as an
    absolute http(s) URL, has no hostname, or carries an unparsable port becomes None
    outright rather than a partially-sanitized value: a NULL diagnostic column is
    honest about "could not be read safely"; a best-effort fragment is not.
    """
    if not url:
        return None
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = parts.hostname
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

def _resolve_transport_api_base(run: dict) -> str:
    """The address this hop's server-mediated self-HTTP should dial (0505 T0008).

    All six mediated self-HTTP call sites (conversation_context, conversation_turn_
    register, api_bound_request, inbox_register, resolve_conflict, workflow_decide)
    used to send `run["api_base_url"]` -- the operator/browser origin -- straight
    back to themselves. That is fine when operator and agent origins coincide, but
    wrong in exactly the topology 0472 B0001 hit for the CLI path: a public/proxy
    origin that this server cannot dial as itself. `_resolve_agent_api_base` already
    solved this for CLI launch; this wraps it for the API provider's self-HTTP and
    caches the result on `run` so all six sites agree within one hop.
    """
    cached = run.get("_transport_api_base_resolved")
    if cached:
        return cached
    operator_base = run.get("api_base_url") or ""
    try:
        resolved = _resolve_agent_api_base(operator_base)
    except ValueError:
        logger.warning(
            "ai-invoke %s: transport base resolution failed for operator base %r, "
            "falling back to operator base for self-HTTP",
            run.get("run_id"), operator_base,
        )
        resolved = operator_base
    run["_transport_api_base_resolved"] = resolved
    return resolved

def _canonicalize_cli_prompt(prompt: str, operator_api_base: str) -> tuple[str, str]:
    """Rewrite only exact operator-base occurrences and return the exported base."""
    agent_api_base = _resolve_agent_api_base(operator_api_base)
    if agent_api_base and operator_api_base and agent_api_base != operator_api_base:
        prompt = prompt.replace(operator_api_base, agent_api_base)
    return prompt, agent_api_base or operator_api_base

# ── No-progress watchdog (0446 T0014 §3) ─────────────────────────────────────
#
# `_cli_execute` waits for the worker with a single `communicate(timeout=...)`, so the
# only question it could ever ask was "has the clock run out?". NR0003 measured both
# ways that goes wrong on a fixed hour: a 74-minute TR hop that was still registering
# documents got cut off, and a worker that died in its first minutes still held its
# group for the remaining 59. This watchdog asks the other question — "is anything
# still happening?" — beside that wait, on its own thread, and answers it from the two
# signals the run already carries: the group's document max-seq and `git status` on the
# group worktree. The stdin prompt is still handed to `communicate()` exactly once and
# the watchdog never touches the pipes (§3-1).
_watchdog_kill_lock = threading.Lock()

def _observe_group_max_seq(run: dict) -> Optional[int]:
    """The document signal, or None for "could not observe" (§3-5).

    Deliberately the same draft-INCLUSIVE max-seq `_work_landed` falls back on: counting
    a stray draft as progress only makes this guard more reluctant to kill, and that is
    the safe direction for a "may I end this process?" question.
    """
    try:
        return int(db_docs.get_group_max_seq(run["group_id"]))
    except Exception:
        logger.warning("ai-invoke %s: progress watchdog could not read the document seq",
                       run.get("run_id"), exc_info=True)
        return None

def _claim_watchdog_kill(run: dict, proc, stop_event: threading.Event,
                         kind: str, now: float) -> bool:
    """Decide — once — whether the watchdog may end this process tree (§4-2).

    Every other exit owns the same process, so the claim is taken under a lock and
    re-checks all of them: the run thread already past `communicate()` (`stop_event`), a
    user cancel (which outranks the clock in `_classify_end_reason` and must stay
    `cancelled`), an earlier tick's claim, and a child that exited on its own between the
    poll and here. Losing any of those races means doing nothing at all.
    """
    with _watchdog_kill_lock:
        if stop_event.is_set():
            return False
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return False
        if run.get("watchdog_kill") is not None:
            return False
        if proc.poll() is not None:
            return False
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        run["watchdog_kill"] = {
            "kind": kind,                       # "no_progress" | "absolute_cap"
            "stalled_sec": int(max(0.0, now - anchor)),
            "elapsed_sec": int(max(0.0, now - run["started_mono"])),
            "threshold_sec": int(run.get("timeout_sec") or 0),
            "absolute_cap_sec": _absolute_cap_sec(),
            "last_progress_at": run.get("last_progress_at"),
            "progress_observations": int(run.get("progress_observations") or 0),
            "attempt_no": int(run.get("attempt_no") or 0),
        }
        # The same flag an expired `communicate()` raises, so this ends as end_reason
        # "timeout" / stop_code "timeout" and NEVER as a provider spawn_failed or
        # fast_fail (§4-3). `watchdog_kill` is the minimal in-memory mark that tells the
        # two kinds apart; T#2 turns it into the durable sentence. No new stop code and
        # no new column here (§3-4).
        run["timed_out"] = True
        claim = run["watchdog_kill"]
    logger.warning(
        "ai-invoke %s: %s — ending the worker (stalled %ss of %ss, elapsed %ss of %ss)",
        run.get("run_id"), kind, claim["stalled_sec"], claim["threshold_sec"],
        claim["elapsed_sec"], claim["absolute_cap_sec"],
    )
    try:
        process_runner.kill_process_tree(proc)
    except Exception:
        logger.warning("ai-invoke %s: watchdog kill failed", run.get("run_id"), exc_info=True)
    return True

def _progress_watchdog_loop(run: dict, proc, stop_event: threading.Event,
                            interval: float = STALL_POLL_INTERVAL_SEC) -> Optional[str]:
    """The poll body. Returns the kill kind, or None if it never killed anything."""
    source_root = Path(run["source_root"]) if run.get("source_root") else None
    # §3-3: the run's OWN start snapshot is the first comparison point, and every tick
    # after that is compared with the previous SUCCESSFUL read. Comparing forever against
    # the baseline instead would count one early edit as progress on every later tick — a
    # worker that wrote a single file and then hung would look busy until the ceiling.
    git_watermark = run.get("dirty_baseline")
    # A run with no source tree at all (the scratch fallback) has no source signal to
    # fail: that is not an unreadable sample, and treating it as one would disable the
    # guard outright for those runs. The document signal alone speaks for them.
    git_enabled = source_root is not None and source_root.is_dir()
    doc_watermark = run.get("baseline_seq")          # §3-2
    if not git_enabled:
        logger.info("ai-invoke %s: progress watchdog has no source tree — documents only",
                    run.get("run_id"))

    while not stop_event.wait(interval):
        now = _now_mono()
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return None
        if proc.poll() is not None:
            return None
        # The ceiling is unconditional (§3-6): it does not care how much progress there
        # has been, and it does not need a readable sample to be true.
        if now - run["started_mono"] >= _absolute_cap_sec():
            return "absolute_cap" if _claim_watchdog_kill(
                run, proc, stop_event, "absolute_cap", now) else None

        moved: list[str] = []
        readable = True
        seq = _observe_group_max_seq(run)
        if seq is None:
            readable = False
        elif doc_watermark is None:
            doc_watermark = seq                      # first reading: nothing to compare to
        elif seq > doc_watermark:
            doc_watermark = seq
            moved.append("document")
        if git_enabled:
            paths = _git_status_paths(source_root)
            if paths is None:
                readable = False
            elif git_watermark is None:
                git_watermark = paths
            elif paths != git_watermark:
                git_watermark = paths
                moved.append("source")               # added AND removed paths both count

        if moved:
            # §3-5, second half: one signal actually moving is enough — the other one
            # standing still proves nothing about it.
            run["stall_anchor_mono"] = now
            run["last_progress_mono"] = now
            run["last_progress_at"] = now_iso()
            run["last_progress_signal"] = ",".join(moved)
            run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
            continue
        if not readable:
            # §3-5, first half: an unreadable sample means "unknown", not "nothing
            # happened". Warn and let the next tick decide. A permanently blind run is
            # still ended on time by the ceiling above.
            logger.warning("ai-invoke %s: progress watchdog sample was unreadable — retrying",
                           run.get("run_id"))
            continue
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        if now - anchor >= float(run["timeout_sec"]):
            return "no_progress" if _claim_watchdog_kill(
                run, proc, stop_event, "no_progress", now) else None
    return None

def _start_progress_watchdog(run: dict, proc,
                             interval: float = STALL_POLL_INTERVAL_SEC) -> tuple:
    """Start the watchdog for ONE attempt. Returns (stop_event, thread)."""
    stop_event = threading.Event()
    # Each attempt opens its own no-progress window — a fresh worker cannot be charged
    # for the silence of the one before it. The ABSOLUTE ceiling deliberately does NOT
    # reset: it is measured from started_mono, which `_reset_attempt_state` keeps (§2-4).
    run["stall_anchor_mono"] = _now_mono()
    run["watchdog_kill"] = None
    thread = threading.Thread(
        target=_progress_watchdog_loop, args=(run, proc, stop_event, interval),
        name=f"ai-invoke-watchdog-{run.get('run_id')}", daemon=True,
    )
    thread.start()
    return stop_event, thread

def _stop_progress_watchdog(stop_event: Optional[threading.Event], thread,
                            run_id: Optional[str] = None) -> None:
    """Stop and join the watchdog on EVERY exit of the attempt (§4-1).

    Called from `_cli_execute`'s finally, so a natural exit, a TimeoutExpired, a broken
    stdin pipe, a user cancel and a fast-fail all come through here. The join matters as
    much as the event: a thread left running would still hold a `proc` the next attempt
    is about to replace.
    """
    if stop_event is None:
        return
    stop_event.set()
    if thread is None:
        return
    thread.join(timeout=STALL_WATCHDOG_JOIN_SEC)
    if thread.is_alive():
        # It can no longer kill anything — `_claim_watchdog_kill` re-reads stop_event
        # under the lock — but it should not have taken this long, so say so.
        logger.warning("ai-invoke %s: progress watchdog did not stop within %ss",
                       run_id, STALL_WATCHDOG_JOIN_SEC)

# ── CLI adapter (L0006 §2.3) ─────────────────────────────────────────────────

_CLI_LAUNCH_AUDIT_SCHEMA = "flowgate.external-cli-launch.v1"

def _shell_kind() -> str:
    return "windows_cmd" if os.name == "nt" else "posix_sh"

def _stable_provider_kind(provider: dict) -> str:
    kind = str(provider.get("kind") or "").lower()
    return kind if kind in {"codex", "claude"} else "other"

def _audit_cli_launch(decision: dict) -> None:
    """Emit exactly one secret-free, line-safe launch decision event."""
    allowed = {
        "schema", "event", "decision", "reason", "run_id", "provider_kind",
        "cwd_source", "spawn_cwd", "agent_cwd", "cwd_transition",
        "shell_kind", "is_unc",
    }
    event = {key: decision.get(key) for key in allowed}
    event["schema"] = _CLI_LAUNCH_AUDIT_SCHEMA
    event["event"] = "external_cli_launch_decision"
    try:
        logger.info("ai-invoke cli spawn decision %s", json.dumps(event, ensure_ascii=True))
    except Exception:
        pass  # logging must never affect the launch outcome

def _blocked_cli_launch(run: dict, provider: dict, reason: str) -> dict:
    return {
        "decision": "blocked", "reason": reason,
        "run_id": run.get("run_id") if _RUN_ID_RE.fullmatch(str(run.get("run_id") or "")) else "<invalid>",
        "provider_kind": _stable_provider_kind(provider),
        "cwd_source": None, "spawn_cwd": None, "agent_cwd": None,
        "cwd_transition": None, "shell_kind": _shell_kind(),
        "is_unc": None,
    }

def _resolve_cli_launch(provider: dict, run: dict, command: str) -> tuple[Optional[dict], str]:
    """Resolve the sole product CLI spawn contract, or fail closed with a fixed code.

    Product runs always carry project_id and therefore must prove the current run scratch
    manifest even when a group worktree is the agent cwd (the scratch is still the UNC
    bootstrap and temp/cache boundary). The project-less compatibility branch exists only
    for older isolated unit harnesses; start_run never creates such a run.
    """
    scratch = Path(run.get("scratch_dir") or "")
    project_id = run.get("project_id")
    run_id = str(run.get("run_id") or "")
    if project_id:
        manifest, reason = _validate_scratch_manifest(project_id, run_id, scratch)
        if manifest is None:
            return None, f"scratch_{reason}"
    try:
        scratch_abs = scratch.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "scratch_unavailable"
    if not scratch_abs.is_absolute() or not scratch_abs.is_dir() or _is_reparse_or_symlink(scratch):
        return None, "scratch_unavailable"

    source = Path(run["source_root"]) if run.get("source_root") else None
    agent_cwd = scratch_abs
    cwd_source = "run_scratch"
    if source is not None and source.is_dir() and run.get("group_id") and project_id:
        try:
            integrated = bool((db_git.get_config(project_id) or {}).get("enabled"))
        except Exception:
            integrated = False
        if integrated:
            if not _is_group_worktree(project_id, run["group_id"], source):
                return None, "group_worktree_identity_invalid"
            agent_cwd = source.absolute() if str(source).startswith("\\\\") else source.resolve(strict=True)
            cwd_source = "group_worktree"
    elif source is not None and source.is_dir() and not project_id:
        # Test-only legacy run shape; real runs are covered by the manifest branch above.
        agent_cwd = source.absolute() if str(source).startswith("\\\\") else source.resolve(strict=True)
        cwd_source = "group_worktree"

    effective_command, effective_cwd = process_runner.unc_safe_shell(command, agent_cwd)
    is_unc = str(agent_cwd).startswith("\\\\")
    if effective_cwd is None:
        if not scratch_abs.is_absolute() or str(scratch_abs).startswith("\\\\"):
            return None, "unc_bootstrap_unavailable"
        spawn_cwd = scratch_abs
        transition = "pushd"
    else:
        spawn_cwd = Path(effective_cwd).resolve(strict=True)
        transition = "none"
    if not spawn_cwd.is_absolute() or not spawn_cwd.is_dir():
        return None, "spawn_cwd_unavailable"
    return {
        "decision": "launch", "reason": None, "run_id": run_id,
        "provider_kind": _stable_provider_kind(provider), "cwd_source": cwd_source,
        "spawn_cwd": str(spawn_cwd), "agent_cwd": str(agent_cwd),
        "cwd_transition": transition,
        "shell_kind": _shell_kind(),
        "is_unc": is_unc, "effective_command": effective_command,
    }, "valid"

def _cli_execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """stdin-injected CLI run (claude/copilot/codex; args are forbidden — cp932
    truncation). Returns (classification, failure_detail)."""
    import subprocess

    cmd = (provider.get("cli_command") or "").strip()
    if not cmd:
        return "spawn_failed", "cli_command not set"
    kind = provider.get("kind") or ""
    # 0295 NR0003 §5-2: injects codex's --skip-git-repo-check when the stored command lacks
    # it. The cwd resolved below is NOT guaranteed to be a git repo (scratch fallback, or a
    # project mirror that is not a checkout), and codex exec exits 1 immediately when it is
    # not — burning the provider as a fast_fail before it ever reads the prompt.
    cmd = ai_settings_service.normalize_cli_command(kind, cmd)
    scratch = Path(run["scratch_dir"])
    last_message_file = scratch / "last_message.txt"
    if kind == "codex":
        cmd = f'{cmd} --output-last-message "{last_message_file}"'

    # T0011: cwd is selected once below by _resolve_cli_launch. No caller cwd, HOME,
    # installation directory, base checkout, or OS temp fallback is permitted.
    # Group 0235 (D0005 §3-4 / L0008 §2-5): the external agent runs on THIS host and
    # must post results to an address it can actually reach. The mention was built
    # with the operator-facing base; rewrite it (and export it) to an agent-reachable
    # base (configured setting -> same-host loopback -> operator base).
    operator_api_base = run.get("api_base_url") or ""
    prompt, agent_api_base = _canonicalize_cli_prompt(prompt, operator_api_base)
    # CLI providers authenticate themselves; a configured api_key is deliberately
    # NOT exported (leak prevention, L0006 §2.3).
    env = {
        "FLOWGATE_TOKEN": run["raw_token"],
        "FLOWGATE_SCRATCH": run["scratch_dir"],
        "TMP": str(scratch / "tmp"),
        "TEMP": str(scratch / "tmp"),
        "TMPDIR": str(scratch / "tmp"),
        "XDG_CACHE_HOME": str(scratch / "cache"),
        "PIP_CACHE_DIR": str(scratch / "cache" / "pip"),
        "NPM_CONFIG_CACHE": str(scratch / "cache" / "npm"),
        "FLOWGATE_API_BASE": agent_api_base or operator_api_base,
    }
    decision, reason = _resolve_cli_launch(provider, run, cmd)
    if decision is None:
        blocked = _blocked_cli_launch(run, provider, reason)
        _audit_cli_launch(blocked)
        return "spawn_failed", f"CLI launch blocked: {reason}"
    eff_cmd = decision.pop("effective_command")
    agent_cwd = Path(decision["agent_cwd"])
    kwargs = process_runner.popen_kwargs(agent_cwd, env)
    kwargs["cwd"] = decision["spawn_cwd"]
    kwargs["stdin"] = subprocess.PIPE
    _audit_cli_launch(decision)

    launched = time.monotonic()
    try:
        proc = subprocess.Popen(eff_cmd, **kwargs)
    except Exception:
        return "spawn_failed", "unable to start CLI process"

    run["proc"] = proc
    # Close the cancel-vs-spawn race: a cancel that landed between admission and
    # Popen saw proc=None and killed nothing — reap the child ourselves now.
    if run["cancel_event"].is_set():
        process_runner.kill_process_tree(proc)
    timed_out = False
    # 0446 T0014 §3-1: the no-progress threshold is enforced BESIDE this wait, by the
    # watchdog thread, because `communicate()` cannot be asked "is it still working?".
    # What is left for the wait itself is the absolute ceiling — the one deadline that
    # holds however well the worker is doing (§3-6) — so a run that keeps producing is
    # no longer cut off at its threshold, and a run that produces nothing is still
    # ended there, by the watchdog, long before this timeout could fire.
    watchdog_stop, watchdog_thread = _start_progress_watchdog(run, proc)
    remaining = max(1.0, _absolute_remaining_sec(run))
    try:
        stdout, stderr = proc.communicate(input=prompt.encode("utf-8"), timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout = getattr(exc, "output", None)
            stderr = getattr(exc, "stderr", None)
    except Exception as exc:
        # e.g. stdin pipe broken before the child read the prompt
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = None, None
        elapsed = time.monotonic() - launched
        if (
            elapsed < FAST_FAIL_WINDOW_SEC
            and not _work_landed(run)
            # 0446 T0014 §4-3: a watchdog kill is a clock decision, never a provider
            # startup failure — it must not send the chain to the next provider.
            and run.get("watchdog_kill") is None
        ):
            return "spawn_failed", str(exc)[:500]
    finally:
        run["proc"] = None
        _stop_progress_watchdog(watchdog_stop, watchdog_thread, run.get("run_id"))

    # 0446 T0014 §4-3: a watchdog kill ends `communicate()` NORMALLY — the child is
    # already gone, so there is no TimeoutExpired to catch and the local flag above is
    # still False. Merge the verdict in before anything reads it: the exit code of a
    # killed worker is not a verdict (it stays None, as on any other timeout) and a
    # killed worker is not a fast_fail candidate. `watchdog_kill` — not `run["timed_out"]`
    # — is the source here, because it is re-armed per attempt by the watchdog itself.
    timed_out = timed_out or run.get("watchdog_kill") is not None
    elapsed = time.monotonic() - launched
    out_text = process_runner.safe_decode(stdout)
    err_text = process_runner.safe_decode(stderr)
    run["stdout_tail"] = out_text[-OUTPUT_TAIL_BYTES:]
    run["stderr_tail"] = err_text[-OUTPUT_TAIL_BYTES:]
    run["exit_code"] = proc.returncode if not timed_out else None
    if timed_out:
        run["timed_out"] = True

    cancelled = run["cancel_event"].is_set()
    # Fast-fail: startup failure ⇒ fallback candidate. A user cancel or timeout is
    # never a provider startup failure (L0006 §2.2 / §4.3).
    if (
        not cancelled
        and not timed_out
        and proc.returncode is not None
        and proc.returncode != 0
        and elapsed < FAST_FAIL_WINDOW_SEC
        and not _work_landed(run)
    ):
        detail = (err_text or out_text).strip()[-500:] or f"exit {proc.returncode} within {int(elapsed)}s"
        return "fast_fail", detail

    _recover_cli_last_message(run, kind, out_text, last_message_file)
    return "started_ok", None

def _copilot_last_message(stdout_text: str) -> Optional[str]:
    """Last assistant text from copilot's `--output-format=json` event stream.

    The stream is NDJSON — one event object per line, no blank lines anywhere — so the
    blank-line block splitter below returns the WHOLE dump as a single "message" and the
    operator sees MCP server status logs where the answer should be (0292 CH0002, 0295
    NR0003 §6). The answer lives in the last `assistant.message` event's `data.content`;
    `assistant.message_delta` carries the same text in fragments and `result` carries no
    text at all, so neither is a usable substitute.

    Returns None when nothing parses, leaving the caller on its block-splitting fallback —
    a copilot run that failed before emitting any event still has stderr/plain output worth
    showing.
    """
    message: Optional[str] = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant.message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            message = content.strip()
    return message

def _recover_cli_last_message(run: dict, kind: str, stdout_text: str, last_message_file: Path) -> None:
    """Per-kind last-message recovery (hive providers.py rule table, rules only):
    claude = full stdout trimmed / codex = --output-last-message file /
    copilot = last `assistant.message` event / custom = last non-blank block of the tail."""
    message: Optional[str] = None
    if kind == "claude":
        message = stdout_text.strip() or None
    elif kind == "codex":
        try:
            if last_message_file.is_file():
                message = last_message_file.read_text(encoding="utf-8", errors="replace").strip() or None
        except Exception:
            message = None
    elif kind == "copilot":
        message = _copilot_last_message(stdout_text)
    if message is None and kind not in ("claude", "codex"):
        tail = stdout_text[-OUTPUT_TAIL_BYTES:]
        blocks = [b.strip() for b in re.split(r"\n\s*\n", tail) if b.strip()]
        message = blocks[-1] if blocks else None
    run["last_message"] = _truncate_front(message)
    run["last_message_received"] = bool(message)

# ── API adapter: minimal agent loop (L0006 §2.4) ─────────────────────────────

def _api_system_prompt() -> str:
    """Non-negotiable API-agent contract shared by OpenAI-compatible providers."""
    return (
        "You are a FlowGate API agent. Use the supplied tools to perform the bound work. "
        "A natural-language claim of completion never registers, replies, decides, or completes "
        "the work: call the required tool with its complete payload. Only use exposed tools and "
        "their declared JSON schemas."
    )


def _api_help_prompt(prompt: str) -> str:
    """Match API-provider guidance to mediated tools; CLI mentions remain unchanged."""
    lines = [line for line in prompt.splitlines()
             if not ("GET " in line and "/help" in line)]
    guidance = (
        "Use the `read_help` tool for personalized FlowGate help: empty input returns "
        "the index, item returns one item, and item plus child returns one child."
    )
    return guidance + "\n\n" + "\n".join(lines)

def _is_glm_openai_provider(provider: dict) -> bool:
    """Identify GLM even when its OpenAI-compatible endpoint is configured as custom."""
    kind = str(provider.get("kind") or "").lower()
    base_url = str(provider.get("api_base_url") or "").lower()
    model = str(provider.get("api_model") or "").lower()
    return (
        kind in {"glm", "zhipu", "zai"}
        or model.startswith("glm-")
        or "bigmodel.cn" in base_url
        or "z.ai" in base_url
    )


_API_TRACE_MAX_TURNS = 20
_API_TRACE_MAX_TOOLS = 12

def _api_trace_turn(run: dict, turn: int, *, model_status: int, response_text: bool = False) -> dict:
    """Append a bounded, input-free API turn record and return its mutable entry."""
    trace = run.setdefault("api_turn_trace", [])
    entry = {"turn": turn, "model_status": int(model_status), "response_text": bool(response_text),
             "received": 0, "valid": 0, "dispatched": 0, "completion_selected": False,
             "register_attempted": False, "register_succeeded": False, "tools": [], "disposition": "response"}
    trace.append(entry)
    if len(trace) > _API_TRACE_MAX_TURNS:
        del trace[:-_API_TRACE_MAX_TURNS]
    return entry

def _api_trace_tool(entry: dict, name: object, status: object, *, registration: bool = False) -> None:
    tools = entry["tools"]
    if len(tools) < _API_TRACE_MAX_TOOLS:
        tools.append({"name": str(name or "tool")[:80], "status": int(status or 0), "registration": registration})

def _api_execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """Minimal tool loop for API providers, including workflow decision kickoff."""
    run.setdefault("register_errors", [])
    run.setdefault("tool_call_misses", 0)
    run.setdefault("turn_limit_exhausted", False)
    secret_scope = run["project_id"] if run.get("chain_source") == "project" else None
    try:
        key = ai_settings_service.get_provider_secret(secret_scope, provider.get("id"))
    except ApiKeyCryptoError:
        # 0371: a key IS stored, the master key just cannot read it. Reporting it as
        # "not set" would send the operator hunting for a key nobody removed.
        return "spawn_failed", "api_key_unreadable"
    if not key:
        return "spawn_failed", "api_key_not_set"
    logger.info(
        "ai-invoke %s: api provider %s key present (len=%d)",
        run["run_id"], provider.get("id"), len(key),
    )

    kind = provider.get("kind") or "openai"
    base_url = (provider.get("api_base_url") or "").rstrip("/")
    model = provider.get("api_model") or ""
    max_turns = max(API_MAX_TURNS_PER_DOC, max(1, run["docs_target"]) * API_MAX_TURNS_PER_DOC)

    current_token = run["raw_token"]
    # 0492 T0018 item 1: the run's four register axes and the token they are bound to
    # are seeded together here and only ever move together (_adopt_continuation_token).
    _run_register_context(run)
    registered = 0
    workflow_pending = run.get("action_scope") == "workflow_decide"
    conflict_pending = run.get("action_scope") == "resolve_conflict"
    is_chat = run.get("action_scope") == "chat"
    last_text: Optional[str] = None
    conversation: list[dict] = [
        {"role": "system", "content": _api_system_prompt()},
        {"role": "user", "content": _api_help_prompt(prompt)},
    ]
    if is_chat:
        # 0505 T0006 (DB0005 3.3): the FIRST self-HTTP this hop may open. Its status/
        # error land in the same three columns as the other five mediated calls, and a
        # failure here ends the hop before the turn loop -- but the same `run` object
        # carries the three fields into persist_run_record/finished_payload regardless.
        status, chat_context = _conversation_context(run, current_token)
        run["last_tool_name"] = "conversation_context"
        run["last_tool_status"] = status
        if run.get("transport_api_base") is None:
            run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
        if not (200 <= status < 300 and isinstance(chat_context, dict)):
            run["last_tool_error"] = _registration_error_summary(chat_context or {})[:500]
            return "api_error", "conversation_context_unavailable"
        run["last_tool_error"] = None
        run["_chat_based_on_seq"] = int(chat_context.get("head_seq") or 0)
        conversation.append({
            "role": "user",
            "content": (
                "The server fetched the conversation for you. Use this as the conversation "
                "you are replying to; do not claim that you still need to fetch it:\n"
                + json.dumps(chat_context, ensure_ascii=False)
            ),
        })
    turn = 0
    # Only a natural loop-boundary exit represents exhausted API turns. A model HTTP
    # or transport failure can happen on the final numbered turn but has priority over
    # the limit diagnosis.
    model_call_failed = False

    while turn < max_turns:
        turn += 1
        run["_api_turn"] = turn
        if run["cancel_event"].is_set():
            break
        # 0446 T0014 §4-5: the model-call loop keeps the ORIGINAL reading. It has no
        # subprocess to watch and no watchdog attached, so `_stall_remaining_sec` would
        # return this very number anyway; naming `_remaining_sec` here keeps the API
        # call ceiling and its cancel/timeout behaviour bit-for-bit unchanged.
        remaining = _remaining_sec(run)
        if remaining <= 0:
            run["timed_out"] = True
            break
        call_timeout = min(remaining, API_CALL_MAX_TIMEOUT_SEC)
        tool_specs = None
        if workflow_pending:
            tool_name, tool_desc, tool_schema = _DECIDE_TOOL_NAME, _DECIDE_TOOL_DESC, _DECIDE_TOOL_SCHEMA
        elif conflict_pending:
            tool_name, tool_desc, tool_schema = _RESOLVE_TOOL_NAME, _RESOLVE_TOOL_DESC, _RESOLVE_TOOL_SCHEMA
        elif is_chat:
            tool_name, tool_desc, tool_schema = _CHAT_TOOL_NAME, _CHAT_TOOL_DESC, _CHAT_TOOL_SCHEMA
        elif run.get("action_scope") == "workflow_decide" or not run.get("doc_ref"):
            # Compatibility for the post-decision continuation and old isolated harnesses.
            # Production document runs always carry doc_ref and take the mediated registry.
            tool_name, tool_desc, tool_schema = _REGISTER_TOOL_NAME, _REGISTER_TOOL_DESC, _REGISTER_TOOL_SCHEMA
        else:
            try:
                tool_specs = api_server_tools.definitions_for_run(run)
            except api_server_tools.ToolError as exc:
                return "api_error", exc.reason
            tool_name, tool_desc, tool_schema = tool_specs, "", {}
        # 0505 T0006 (DB0005 2/3.3): counted whether the call below succeeds, raises, or
        # times out -- "how many model calls did this hop make" is the question, not
        # "how many succeeded". model_last_http_status uses the same 0/exc.code sentinel
        # convention as last_tool_status; the adapters below do not surface the real
        # success-path status without widening _http_post_json's contract (shared with
        # _call_openai/_call_anthropic and monkeypatched directly by existing tests as a
        # dict-returning function) -- OpenAI/Anthropic-compatible chat completions are
        # 200 on any non-raising return, so that is what a success is recorded as.
        run["model_http_calls"] = run.get("model_http_calls", 0) + 1
        try:
            if kind == "claude":
                reply_text, tool_call, assistant_msg = _call_anthropic(
                    base_url, model, key, conversation, call_timeout,
                    tool_name, tool_desc, tool_schema, True,
                )
            else:
                reply_text, tool_call, assistant_msg = _call_openai(
                    base_url, model, key, conversation, call_timeout,
                    tool_name, tool_desc, tool_schema, True,
                )
            run["model_last_http_status"] = 200
            trace = _api_trace_turn(run, turn, model_status=200, response_text=bool(reply_text))
        except urllib.error.HTTPError as exc:
            model_call_failed = True
            run["model_last_http_status"] = exc.code
            _api_trace_turn(run, turn, model_status=exc.code)["disposition"] = "model_http_error"
            # An attempted first request is still a consumed turn.  Record it before
            # returning because the shared post-loop finalizer is intentionally skipped
            # for the immediate API-error contract.
            if turn == 1:
                run["api_turns_used"] = turn
                return "api_error", f"{exc.code} {exc.reason}"
            logger.warning("ai-invoke %s: api error after first turn: %s", run["run_id"], exc)
            break
        except Exception as exc:
            model_call_failed = True
            run["model_last_http_status"] = 0
            _api_trace_turn(run, turn, model_status=0)["disposition"] = "model_transport_error"
            # Keep the same attempted-turn accounting for a first transport/parse error.
            if turn == 1:
                run["api_turns_used"] = turn
                return "spawn_failed", str(exc)[:500]
            logger.warning("ai-invoke %s: api transport error after first turn: %s", run["run_id"], exc)
            break

        conversation.append(assistant_msg)
        if reply_text:
            last_text = reply_text
        tool_calls = tool_call if isinstance(tool_call, list) else ([tool_call] if tool_call else [])
        is_glm_openai = _is_glm_openai_provider(provider)
        if tool_specs is None and not is_glm_openai:
            # Restore the established direct-tool boundary before counting calls: a
            # non-GLM provider that returns a name other than the sole exposed tool is
            # indistinguishable from no tool call and must enter the miss/nudge path.
            tool_calls = [call for call in tool_calls if (call.get("name") or tool_name) == tool_name]
        run["tool_calls_received"] = run.get("tool_calls_received", 0) + len(tool_calls)
        trace["received"] = len(tool_calls)
        if not tool_calls:
            trace["disposition"] = "nudge" if run["tool_call_misses"] < API_MAX_TOOL_NUDGES else "no_tool"
            run["tool_call_misses"] += 1
            if run["tool_call_misses"] <= API_MAX_TOOL_NUDGES:
                conversation.append({
                    "role": "user",
                    "content": (
                        (f"The required action is not complete. Call the `{tool_name}` tool now with the actual full payload. "
                         if tool_specs is None else
                         "Use the available tools to inspect or change the bound work, then call `register_document` when complete. ")
                        + "Do not merely say that you registered or attached it."
                    ),
                })
                continue
            break

        if tool_specs is not None:
            exposed = {item["name"]: item for item in tool_specs}
            validation_errors = []
            for call in tool_calls:
                try:
                    spec = exposed.get(call.get("name"))
                    if spec is None:
                        raise api_server_tools.ToolError(422, "invalid_tool_call")
                    tool_input = call.get("input")
                    if call["name"] == "read_document":
                        tool_input = api_server_tools.normalize_read_document_input(tool_input)
                    elif call["name"] == "read_help":
                        tool_input = api_server_tools.normalize_read_help_input(tool_input)
                    else:
                        api_server_tools.validate(spec["schema"], tool_input)
                    call["input"] = tool_input
                    trace["valid"] += 1
                    validation_errors.append(None)
                except api_server_tools.ToolError as exc:
                    validation_errors.append(exc)

            completion_call = None
            for call, validation_error in zip(tool_calls, validation_errors):
                if validation_error is not None:
                    _status, resp = api_server_tools.error_payload(
                        call.get("name") or "tool_call", validation_error
                    )
                    conversation.append(_tool_result_msg(
                        kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                    ))
                    continue
                if call["name"] == _REGISTER_TOOL_NAME:
                    if completion_call is None:
                        completion_call = call
                        trace["completion_selected"] = True
                        # 0505 T0006 (DB0005 2): validated and about to be dispatched to the
                        # register handler below (_inbox_register) -- counted here, at the
                        # ONE call that survives the duplicate-register rejection just below.
                        run["tool_calls_executed"] = run.get("tool_calls_executed", 0) + 1
                    else:
                        duplicate = api_server_tools.ToolError(422, "invalid_tool_call")
                        _status, resp = api_server_tools.error_payload(call["name"], duplicate)
                        conversation.append(_tool_result_msg(
                            kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                        ))
                    continue
                # 0505 T0006 (DB0005 2): sent to a real handler regardless of what it
                # returns -- "executed" counts dispatch, not success.
                run["tool_calls_executed"] = run.get("tool_calls_executed", 0) + 1
                try:
                    if call["name"] in api_server_tools.SOURCE_OPS:
                        _status, resp = api_server_tools.source_call(run, current_token, call["name"], call["input"])
                    elif call["name"] == "run_test":
                        _status, resp = api_server_tools.run_test(run, call["input"], _remaining_sec(run))
                    elif call["name"] == "read_document":
                        _status, resp = _api_read_document(run, current_token, call["input"])
                    elif call["name"] == "read_help":

                        _status, resp = api_server_tools.read_help(run, current_token, call["input"])

                    else:

                        _status, resp = _api_create_question(run, current_token, call["input"])
                except api_server_tools.ToolError as exc:
                    _status, resp = api_server_tools.error_payload(call["name"], exc)
                # 0505 T0006 (DB0005 3.3): read_document/create_question both dispatch
                # through _api_bound_request -- one self-HTTP call point, one name. The
                # other three branches above (SOURCE_OPS, run_test, read_help) are direct
                # in-process handlers, never self-HTTP, and stay out of last_tool_name
                # entirely (DB0005 2 scope note).
                if call["name"] not in api_server_tools.SOURCE_OPS and call["name"] not in ("run_test", "read_help"):
                    run["last_tool_name"] = "api_bound_request"
                    run["last_tool_status"] = _status
                    run["last_tool_error"] = (
                        None if 200 <= _status < 300 else _registration_error_summary(resp)[:500]
                    )
                trace["dispatched"] += 1
                _api_trace_tool(trace, call["name"], _status)
                conversation.append(_tool_result_msg(
                    kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                ))
            if completion_call is None:
                trace["disposition"] = "direct_tools_only"
                continue
            tool_call = completion_call
        else:
            if not is_glm_openai:
                # Preserve established OpenAI/Anthropic direct-scope behavior.
                run["tool_calls_executed"] = run.get("tool_calls_executed", 0) + 1
                tool_call = tool_calls[0]
            else:
                # A forced GLM provider choice is untrusted input: emit a result for every
                # received call, and dispatch exactly one validated call.
                completion_call = None
                for call in tool_calls:
                    call_name = call.get("name") or tool_name
                    call["name"] = call_name
                    try:
                        if call_name != tool_name:
                            raise api_server_tools.ToolError(422, "invalid_tool_call")
                        api_server_tools.validate(tool_schema, call.get("input"))
                    except api_server_tools.ToolError as exc:
                        _status, resp = api_server_tools.error_payload(call_name or "tool_call", exc)
                        conversation.append(_tool_result_msg(
                            kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                        ))
                        continue
                    if completion_call is not None:
                        duplicate = api_server_tools.ToolError(422, "invalid_tool_call")
                        _status, resp = api_server_tools.error_payload(call_name, duplicate)
                        conversation.append(_tool_result_msg(
                            kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                        ))
                        continue
                    completion_call = call
                    run["tool_calls_executed"] = run.get("tool_calls_executed", 0) + 1
                if completion_call is None:
                    continue
                tool_call = completion_call

        if workflow_pending:
            status, resp = _workflow_decide(run, current_token, tool_call["input"])
            run["last_tool_name"] = "workflow_decide"
            run["last_tool_status"] = status
            run["last_tool_error"] = None if 200 <= status < 300 else _registration_error_summary(resp)[:500]
            if 200 <= status < 300:
                workflow_pending = False
                next_token = resp.get("next_token")
                next_mention = resp.get("next_mention")
                resolved_target = resp.get("continuation_target_seq")
                if run.get("target_to_end") and isinstance(resolved_target, int) and resolved_target > 0:
                    # 0226 NR0003 §5-1: resolved_target is an item_seq — count the decided
                    # sequence's worker items instead of subtracting the group doc seq.
                    resolved = _continuation_docs_target(
                        run["doc_ref"],
                        resolved_target,
                        pending_only=False,
                        continuation_instruction_mode=run["continuation_instruction_mode"],
                        continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
                    )
                    if resolved is not None:
                        run["docs_target"] = resolved
                        # A to-end workflow-decision run starts before the sequence
                        # exists, so its chain target is resolved exactly once here.
                        if run.get("chain_docs_target", 0) <= 0:
                            run["chain_docs_target"] = resolved
                    max_turns = max(max_turns, turn + max(1, run["docs_target"]) * API_MAX_TURNS_PER_DOC)
                if next_token:
                    current_token = next_token
                    _adopt_continuation_token(run, next_token)
                result_text = next_mention or json.dumps(resp, ensure_ascii=False)[:4000]
                conversation.append(_tool_result_msg(kind, tool_call, result_text))
                if run["mode"] == "single" or not next_token:
                    break
                continue
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Workflow decision failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        if conflict_pending:
            status, resp = _resolve_conflict(run, current_token, tool_call["input"])
            run["last_tool_name"] = "resolve_conflict"
            run["last_tool_status"] = status
            run["last_tool_error"] = None if 200 <= status < 300 else _registration_error_summary(resp)[:500]
            if 200 <= status < 300:
                conversation.append(_tool_result_msg(kind, tool_call, json.dumps(resp, ensure_ascii=False)[:4000]))
                break
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Conflict resolve failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        if is_chat:
            status, resp = _conversation_turn_register(run, current_token, tool_call["input"])
            run["last_tool_name"] = "conversation_turn_register"
            run["last_tool_status"] = status
            if 200 <= status < 300:
                run["last_tool_error"] = None
                registered += 1
                break
            reason = _registration_error_summary(resp)
            run["last_tool_error"] = reason[:500]
            run["register_errors"].append({"status": status, "reason": reason, "turn": turn})
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Chat reply registration failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        trace["register_attempted"] = True
        status, resp = _inbox_register(run, current_token, tool_call["input"])
        trace["dispatched"] += 1
        _api_trace_tool(trace, "register_document", status, registration=True)
        run["last_tool_name"] = "inbox_register"
        run["last_tool_status"] = status
        if 200 <= status < 300:
            trace["register_succeeded"] = True
            trace["disposition"] = "registered"
            run["last_tool_error"] = None
            registered += 1
            next_token = resp.get("next_token")
            next_mention = resp.get("next_mention")
            if next_token:
                current_token = next_token
                # The hop moved: adopt the new token AND its axes atomically, so a
                # delayed call holding the previous one is refused at the next bind.
                _adopt_continuation_token(run, next_token)
            result_text = next_mention or json.dumps(
                {k: resp.get(k) for k in ("ok", "doc_id", "message") if k in resp},
                ensure_ascii=False,
            )
            conversation.append(_tool_result_msg(kind, tool_call, result_text))
            if run["mode"] == "single" or registered >= run["docs_target"] or not next_token:
                break
        else:
            trace["disposition"] = "register_failed"
            reason = _registration_error_summary(resp)
            run["last_tool_error"] = reason[:500]
            if not _absorb_binding_failure(run, resp, turn):
                run["register_errors"].append({
                    "status": status,
                    "reason": reason,
                    "turn": turn,
                })
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Registration failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))

    goal_met = (
        not workflow_pending
        and (
            (run.get("action_scope") == "workflow_decide" and run["mode"] == "single")
            or registered >= run["docs_target"]
        )
    )
    if (
        turn >= max_turns
        and not goal_met
        and not run["cancel_event"].is_set()
        and not run.get("timed_out")
        and not model_call_failed
    ):
        run["turn_limit_exhausted"] = True

    # 0505 T0006 (DB0005 2): the loop's own exit turn -- set once, here, regardless of
    # which branch above broke out of it.
    run["api_turns_used"] = turn
    run["exit_code"] = None
    run["last_message"] = _truncate_front(last_text)
    run["last_message_received"] = bool(last_text)
    return "started_ok", None

def _absorb_binding_failure(run: dict, response: dict, turn: int) -> bool:
    """Was this 403 already recorded in full, axes and all?

    A context-binding rejection is written exactly once — by the dispatch binder before it
    posts anything, or by the /inbox boundary reaching this same live run through the
    token's ai_run_id. Both hand the correlation id back in the response, which is how the
    turn loop tells "already recorded, with its axis" from "an ordinary 409/422 that has no
    axis to record" — and does not stack a second, axis-less row on top of the first.

    The turn number belongs to the loop, not to either boundary, so it is stamped here.
    """
    # `error` is a free-form field elsewhere in this loop (a plain string from a transport
    # failure, for one), so read it only when it is the normalized object.
    error = (response or {}).get("error")
    details = error.get("details") if isinstance(error, dict) else None
    correlation_id = details.get("correlation_id") if isinstance(details, dict) else None
    if not correlation_id:
        return False
    for item in (run.get("register_errors") or []):
        if not isinstance(item, dict) or item.get("correlation_id") != correlation_id:
            continue
        if item.get("turn") is None:
            item["turn"] = turn
            telemetry = item.get("telemetry")
            if isinstance(telemetry, dict):
                telemetry["turn"] = turn
        return True
    return False

def _registration_error_summary(response: dict) -> str:
    for key in ("code", "error", "message", "detail"):
        value = response.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)[:500]
        return str(value)[:500]
    return json.dumps(response, ensure_ascii=False)[:500] or "unknown registration error"

def _tool_result_msg(kind: str, tool_call: dict, text: str) -> dict:
    if kind == "claude":
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call["id"],
                "content": text,
            }],
        }
    return {"role": "tool", "tool_call_id": tool_call["id"], "content": text}

def _resolve_conflict(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set (e.g. by the chat
    # prefetch) stays untouched.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    body = {
        "files": tool_input.get("files") or [],
        "complete": bool(tool_input.get("complete")),
    }
    req = urllib.request.Request(
        f"{_resolve_transport_api_base(run)}/groups/{run['group_id']}/git/merge/{run['merge_id']}/resolve-token",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

def _http_post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _call_anthropic(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict, force_tool: bool = False,
) -> tuple[Optional[str], Optional[dict], dict]:
    multi = isinstance(tool_name, list)
    specs = tool_name if multi else [{"name": tool_name, "description": tool_desc, "schema": tool_schema}]
    data = _http_post_json(
        f"{base_url}/v1/messages",
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": model,
            "max_tokens": API_MAX_TOKENS,
            "messages": conversation,
            "tools": [{"name": spec["name"], "description": spec["description"], "input_schema": spec["schema"]} for spec in specs],
            **({"tool_choice": ({"type": "any"} if multi else {"type": "tool", "name": specs[0]["name"]})} if force_tool else {}),
        },
        timeout,
    )
    content = data.get("content") or []
    text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
    tool_calls = []
    exposed = {spec["name"] for spec in specs}
    for block in content:
        if block.get("type") == "tool_use":
            name = block.get("name")
            tool_calls.append({"id": block.get("id"), "name": name, "input": block.get("input")})
    assistant_msg = {"role": "assistant", "content": content}
    return ("\n".join(p for p in text_parts if p) or None), tool_calls, assistant_msg

def _call_openai(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict, force_tool: bool = False,
) -> tuple[Optional[str], Optional[dict], dict]:
    multi = isinstance(tool_name, list)
    specs = tool_name if multi else [{"name": tool_name, "description": tool_desc, "schema": tool_schema}]
    data = _http_post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": conversation,
            "tools": [{"type": "function", "function": {"name": spec["name"], "description": spec["description"], "parameters": spec["schema"]}} for spec in specs],
            **({"tool_choice": ("required" if multi else {"type": "function", "function": {"name": specs[0]["name"]}})} if force_tool else {}),
        },
        timeout,
    )
    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    tool_calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args or "{}")
            except (TypeError, ValueError):
                args = None
        else:
            args = None
        tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "input": args})
    # Keep every received call, including unknown names and malformed inputs, so the
    # dispatcher can emit a call-id-specific error instead of misclassifying it as a miss.
    return message.get("content"), tool_calls, message

def _workflow_decide(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set stays untouched.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    body = {
        "doc_class": tool_input.get("doc_class") or "standard",
        "sequence": tool_input.get("sequence") or [],
    }
    req = urllib.request.Request(
        f"{_resolve_transport_api_base(run)}/workflow/{run['doc_ref']}/decide",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

class _BearerOnlyRequest:
    """Request stand-in for direct in-process calls (0505 T0018).

    `conversation_routes._authenticate`/`auth_outbound.verify_bearer` never read
    anything off a live Request except this header, so a full ASGI Request is
    unnecessary for the two sites that no longer dial self-HTTP.
    """

    __slots__ = ("headers",)

    def __init__(self, raw_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {raw_token}"}


def _conversation_context(run: dict, raw_token: str) -> tuple[int, dict]:
    """Fetch the unread conversation that an API model cannot retrieve itself.

    0505 T0018: in-process call, not self-HTTP. GET /conversation/{doc_id}/turns never
    reaches GroupMutationPolicyMiddleware -- mutation_policy.classify_mutation_route's
    first check is `methods & MUTATION_METHODS` (POST/PUT/PATCH/DELETE only), so a GET
    route is classified "read_only" before any group-lease check runs (mutation_policy.py
    290-304, 347). The only binding this call ever had was
    conversation_routes._authenticate (token action_scope/doc_ref/project/group match),
    unchanged and reused as-is through the route's own plain-Python _list_authenticated --
    no new binding logic, no self-HTTP round trip. Still returns (status, body) like the
    five self-HTTP call sites below (_api_bound_request/_workflow_decide/_resolve_conflict/
    _conversation_turn_register/_inbox_register).
    """
    from modules.flow_gate.api.v1 import conversation_routes
    try:
        response = conversation_routes._list_authenticated(
            run["doc_ref"], raw_token, after_seq=0, before_seq=None, limit=None,
            include_head=True,
        )
    except Exception as exc:
        return 0, {"error": str(exc)}
    return response.status_code, json.loads(response.body)

def _conversation_turn_register(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    """Append an API-provider reply through the token-bound conversation endpoint."""
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set stays untouched.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    api_base = _resolve_transport_api_base(run).rstrip("/")
    body = {
        "body": tool_input.get("body") or "",
        "idempotency_key": run["token_id"],
        "based_on_seq": int(run.get("_chat_based_on_seq") or 0),
        "display_name": (run.get("provider") or {}).get("name") or "AI",
    }
    req = urllib.request.Request(
        f"{api_base}/conversation/{run['doc_ref']}/turn",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

def _api_bound_request(run: dict, raw_token: str, path: str, method: str = "GET", body: Optional[dict] = None) -> tuple[int, dict]:
    """Call a document service through its normal token gate with only server-owned routing."""
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set stays untouched.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{_resolve_transport_api_base(run)}{path}", data=data,
        headers={"Authorization": f"Bearer {raw_token}", **({"Content-Type": "application/json"} if data is not None else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

def _api_read_document(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    """0505 T0018: in-process call, not self-HTTP -- same GET-skips-the-lease-middleware
    reasoning as _conversation_context above. `_api_bound_request`/`_api_create_question`
    keep dialing self-HTTP unchanged; this is the only call inside the shared dispatch
    branch (1707-1719) that no longer does.

    document_routes.get_document/get_document_section are FastAPI route functions whose
    Query(...)-typed parameters must all be passed explicitly here -- calling one without
    a value falls back to the raw `Query` sentinel object, not the default it wraps.
    `_BearerOnlyRequest` stands in for the real Request: verify_bearer, reused unchanged,
    only ever reads `request.headers`, and neither route reads anything else off it.

    transport_api_base is still resolved here even though it is no longer dialed, so
    DB0005 2's "first opener wins" diagnostic keeps reporting the same per-hop value
    regardless of which of the six sites happens to run first (T0018 item 3 judgment:
    last_tool_name/transport_api_base semantics are left unchanged for this site).
    """
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    from modules.flow_gate.api.v1 import document_routes
    fake_request = _BearerOnlyRequest(raw_token)
    try:
        if not tool_input:
            response = document_routes.get_document(fake_request, run["doc_ref"])
        else:
            query = {}
            for key in ("section", "section_id"):
                if key in tool_input:
                    query[key] = tool_input[key]
            for key in ("lines", "chars"):
                if key in tool_input:
                    # Public schemas use typed ranges; the document HTTP contract is a-b.
                    value = tool_input[key]
                    query[key] = f"{value['start']}-{value['end']}"
            response = document_routes.get_document_section(
                fake_request, run["doc_ref"],
                section=query.get("section"), section_id=query.get("section_id"),
                lines=query.get("lines"), chars=query.get("chars"),
                include_children=True, max_chars=None, revision_no=None,
            )
    except Exception as exc:
        return 0, {"error": str(exc)}
    return response.status_code, json.loads(response.body)

def _api_create_question(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    return _api_bound_request(run, raw_token, f"/q/{run['doc_ref']}/questions", "POST", tool_input)

# ── Register mutation context (0492 T0018 item 1 / L0010 §2.1-§2.2) ──────────
#
# The four routing axes of a register call are server property. Before T0018 the proxy
# built them from `run` alone and, before TR0017, from a hard-coded "new" — which is the
# whole of B0001: an edit-scoped token was posted as action="new", so /inbox picked
# _handle_new and its scope check rejected it three turns running (NR0013, conclusion).
#
# Now every call re-reads the run AND re-verifies the live token, compares the two on
# action/project/group/doc in that fixed order, and only then assembles an envelope. The
# model contributes document content and nothing else.

# The scopes an API provider may register under. workflow_decide / resolve_conflict / chat
# have their own dedicated proxies and never reach here.
_REGISTER_SCOPES = ("new", "edit", "review", "test_run")

# Per-scope allowlist of model-authored fields. Anything else the model sends is dropped
# here even if a future schema change lets it through validation — this list, not the
# model's input, decides what an /inbox body may contain besides the server's own axes.
_REGISTER_MODEL_FIELDS = {
    "new": ("doc_type", "title", "content"),
    "edit": ("content", "edit_reason", "rejection_response", "rejection_id",
             "rejection_review_id"),
    "review": ("verdict", "findings", "comment"),
    "test_run": (),
}

class _RegisterBindingRejected(Exception):
    """A register call refused before any side effect. Carries the 403 body to return."""

    def __init__(self, response: dict, record: Optional[dict] = None):
        self.response = response
        self.record = record
        super().__init__(response.get("error_message") or "register binding rejected")

def _run_register_context(run: dict) -> dict:
    """The run's CURRENT hop axes — server-owned, seeded from the run itself.

    Held apart from run["doc_ref"]/["action_scope"], which stay pinned to the hop the
    oracle and the judge measure. Only _adopt_continuation_token moves it, and only to a
    token this server just issued.
    """
    context = run.get("register_context")
    if not context:
        context = register_binding.canonical_context(
            run.get("action_scope"), run.get("project_id"),
            run.get("group_id"), run.get("doc_ref"),
        )
        run["register_context"] = context
        run["current_token_id"] = run.get("current_token_id") or run.get("token_id")
    return context

def _adopt_continuation_token(run: dict, raw_token: str) -> bool:
    """Move the run's current token and its four axes together, or not at all.

    L0010 §2.1: after a hop change only the refreshed pair is honoured, so a delayed call
    holding the previous token fails the ownership check below before it can do anything.
    A continuation that would leave the run's project or group is refused outright — those
    two axes are immutable for the life of a run.
    """
    try:
        token_rec = token_service.verify(raw_token)
    except Exception:
        logger.warning("ai-invoke %s: continuation token could not be verified",
                       run.get("run_id"), exc_info=True)
        return False
    context, _group_db, _group_resolved = register_binding.token_axes(token_rec)
    pinned = register_binding.canonical_context(
        context["action"], run.get("project_id"), run.get("group_id"), context["doc"],
    )
    if context["project"] != pinned["project"] or context["group"] != pinned["group"]:
        logger.warning("ai-invoke %s: continuation token leaves the run's project/group",
                       run.get("run_id"))
        return False
    run["register_context"] = context
    run["current_token_id"] = token_rec.get("token_id")
    return True

def _bind_register_context(run: dict, raw_token: str) -> tuple[dict, dict]:
    """L0010 §2.2 R5. Returns (context, token_rec) or raises _RegisterBindingRejected.

    Nothing from the model or the request participates: `expected` is the run's server-owned
    hop context, `actual` is the verified token. The group axis of a token minted before the
    group column existed is completed from the DB group of its own doc_ref, and a failed
    lookup stays unresolved — which is a mismatch, not a pass.
    """
    expected = _run_register_context(run)
    try:
        token_rec = token_service.verify(raw_token)
    except HTTPException as exc:
        # An invalid/expired/consumed token is an authentication fault, not a binding one.
        raise _RegisterBindingRejected({
            "ok": False, "http_status": exc.status_code, "error_message": str(exc.detail),
        }) from exc

    actual, group_db, group_resolved = register_binding.token_axes(token_rec)
    axes = register_binding.compare_axes(expected, actual)
    if axes:
        record = register_binding.failure_record(
            boundary=register_binding.BOUNDARY_DISPATCH,
            axes=axes,
            run_context=expected,
            token_context=actual,
            correlation_id=register_binding.new_correlation_id(),
            run_id=run.get("run_id"),
            ai_run_id=run.get("run_id"),
            token_id=token_rec.get("token_id"),
            group_token_db=group_db,
            group_token_resolved=group_resolved,
            turn=run.get("_api_turn"),
        )
        register_binding.log_failure(record)
        raise _RegisterBindingRejected({
            "ok": False,
            "http_status": 403,
            "error_message": register_binding.BINDING_MESSAGE,
            "error": register_binding.forbidden_details(record),
        }, record)

    current_token_id = run.get("current_token_id")
    if current_token_id and token_rec.get("token_id") != current_token_id:
        # The axes happen to line up, but this is not the token the server is currently
        # bound to — a call left over from before a continuation. Refuse it here rather
        # than let it register against a hop that has moved on.
        raise _RegisterBindingRejected({
            "ok": False,
            "http_status": 403,
            "error_message": register_binding.BINDING_MESSAGE,
            "error": {
                "code": register_binding.CODE_FORBIDDEN,
                "message": register_binding.BINDING_MESSAGE,
                "details": {"reason": "token_not_current"},
            },
        })

    if expected["action"] not in _REGISTER_SCOPES:
        raise _RegisterBindingRejected({
            "ok": False, "http_status": 422,
            "error_message": f"action_scope {expected['action']!r} cannot register a document",
        })
    return expected, token_rec

def _register_envelope(context: dict, run: dict, tool_input: Optional[dict]) -> dict:
    """The /inbox body: server axes plus the scope's allowlisted model fields.

    `doc` is the predecessor for new and the target for the other three. test_run takes
    `doc_id` and never `prev_doc_id` — the key its handler does not read at all, which is
    why fixing only the action string would have turned B0001's 403 into a 400 (NR0013 §4).
    """
    action, doc = context["action"], context["doc"]
    payload = tool_input if isinstance(tool_input, dict) else {}
    body: dict = {
        "action": action,
        "project": context["project"],
        "module": run.get("module") or "none",
        "group_name": context["group"],
    }
    body["prev_doc_id" if action == "new" else "doc_id"] = doc
    for field in _REGISTER_MODEL_FIELDS[action]:
        if field in payload:
            body[field] = payload[field]
    if action == "new":
        body["doc_type"] = str(payload.get("doc_type") or "").strip()
        body["title"] = payload.get("title") or ""
        body["content"] = payload.get("content") or ""
    return body

def _inbox_register(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    """Server-side proxy registration for API providers: POST the server-assembled
    body to our own /inbox with the run token, exactly as an external worker
    would — every inbox validation and the chain self-advance stay in force."""
    try:
        context, _token_rec = _bind_register_context(run, raw_token)
    except _RegisterBindingRejected as rejected:
        if rejected.record is not None:
            run.setdefault("register_errors", []).append(rejected.record)
        return int(rejected.response.get("http_status") or 403), rejected.response
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set stays untouched. Placed
    # after the binding check above, not at function entry -- a binding rejection
    # never opens a socket, so it must not claim the transport base either.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = _sanitize_diagnostic_base(_resolve_transport_api_base(run))
    body = _register_envelope(context, run, tool_input)
    req = urllib.request.Request(
        f"{_resolve_transport_api_base(run)}/inbox",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

# ── Judge / finish (L0006 §2.6–2.8) ──────────────────────────────────────────

def _oracle_new_docs(run: dict) -> list[dict]:
    """Run-attributed documents: non-draft docs past the run's baseline seq.

    The single filter shared by the live progress counter (get_status) and the final
    judge (_settle_and_judge) — 0226 NR0003 §5-2. The live counter previously showed
    the raw group max-seq delta (drafts and documents this run never made included),
    so it could read 4/3 mid-run while the judge later clamped to 3/3.
    """
    docs = db_docs.get_documents_by_group_id(run["group_id"])
    return sorted(
        (
            d for d in docs
            if (d.get("seq") or 0) > run["baseline_seq"] and (d.get("status") or "") != "draft"
        ),
        key=lambda d: d.get("seq") or 0,
    )

def _settle_and_judge(run: dict) -> None:
    """Judge, then finalize — the pre-0359 shape, kept for every caller that wants both.

    0359 L0007 §2.1 split these two apart so the no-output retry has somewhere to stand:
    _worker now calls _judge_hop once per ATTEMPT and _finalize_run once per HOP.
    """
    _judge_hop(run)
    _finalize_run(run)

def _judge_hop(run: dict) -> None:
    """Decide what this attempt produced (L0007 §2.2). Exactly once per attempt — never twice
    for one attempt, and never by merging two attempts' verdicts. docs_reached is always
    measured from the hop's baseline_seq, so a later attempt's document is simply the answer.

    Judgment content is unchanged from the pre-0359 _settle_and_judge; only the finalize call
    that used to be welded to each branch has been lifted out.
    """
    time.sleep(ORACLE_SETTLE_SEC)
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
        new_docs = _oracle_new_docs(run)
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
                resolved = _continuation_docs_target(
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
    if run["mode"] == "continuous" and peek_auto_resume(run.get("group_id")) is not None:
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

    respawn_pending = peek_auto_resume(run.get("group_id")) is not None
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
        deleted, cleanup_reason = _delete_owned_scratch(
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
    now_paths = _git_status_paths(Path(run["source_root"]) if run.get("source_root") else None)
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
            _checkpoint_document_review_loop(run)
        except Exception:
            logger.exception("document review-loop checkpoint failed for %s", run["run_id"])
    _persist_run_record(run)
    _notify_chain_failure_if_needed(run)

    _broadcast(run, "ai_invoke_finished", finished_payload(run))
    _broadcast(run, "group_view_refresh", {
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
        or (_scope_oracle_retry_run(run) and run.get("end_reason") == "exited")
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
    run = _active_run_for_group(group_id)
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
    run = get_run_record(run_id) or (_active_run_for_group(group_id) if group_id else None)
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
        mark_chain_stop(group_id, stop_code, detail)
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
        stopped = _active_run_for_group(group_id) if group_id else None
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
    if run.get("mode") != "continuous" and not _scope_oracle_retry_run(run):
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
        "document_review_loop": document_review_loop_payload(run),
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
