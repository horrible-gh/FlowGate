"""Worker orchestration (0501 NR0003 §12/§15 `worker.py`).

Provider-neutral execution of one admitted run: the fallback loop over the provider
chain, the attempt loop, the no-output retry and its token re-preparation, the stall /
absolute-ceiling clocks, and the FlowGate tool dispatch the API agent loop calls back
into (register / read document / decide workflow / resolve conflict / conversation turn
/ question).

The transports themselves are NOT here -- HTTP/API in `provider_api.py`, subprocess/CLI
in `provider_cli.py` (NR0003 §15) -- and neither is the end of the run: judging, stop
classification, persistence, notification and the finished payload are `finalize.py`
(§17). This module decides what to attempt next; those decide what it meant.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import questions as db_questions
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.services import api_server_tools
from modules.flow_gate.services import q_service
from modules.flow_gate.services import register_binding
from modules.flow_gate.services import token_service
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

# `oracle` is also a local variable name in this file, so that sibling module is
# imported under an unambiguous alias. `chain` is reached only through the `_svc()`
# seam (the documented worker<->chain cycle break -- see
# test_ai_invoke_svc_seam_scope_0501.py), never as a sibling import: chain.py already
# reaches admission/diagnostics/finalize/review, and finalize.py reaches admission, so a
# direct `worker -> chain` import would close a cycle back through either path.
from . import admission
from . import oracle as oracle_module
from . import provider_api
from . import review
from .runtime import (
    API_CALL_MAX_TIMEOUT_SEC,
    HOP_HANDOFF_FAILED_STOP_CODE,
    HOP_HANDOFF_STOP_CODE,
    NO_OUTPUT_MAX_ATTEMPTS,
    RETRY_MIN_REMAINING_SEC,
    REWORK_HOP_KIND,
    _CHAT_TOOL_DESC,
    _CHAT_TOOL_NAME,
    _CHAT_TOOL_SCHEMA,
    _DECIDE_TOOL_DESC,
    _DECIDE_TOOL_NAME,
    _DECIDE_TOOL_SCHEMA,
    _REGISTER_TOOL_DESC,
    _REGISTER_TOOL_NAME,
    _REGISTER_TOOL_SCHEMA,
    _RESOLVE_TOOL_DESC,
    _RESOLVE_TOOL_NAME,
    _RESOLVE_TOOL_SCHEMA,
    _absolute_remaining_sec,
    _note_issued_prompt,
    _note_issued_raw_token,
    _svc,
    excerpt,
    logger,
    prompt_digest,
)


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
        run["provider"] = oracle_module._provider_brief(current_chain[0])
        run["provider_id"] = current_chain[0].get("id")
        run["attempt_no"] = 1
        # Exactly once per run, however many attempts follow (P0006 appendix D: no new event
        # types — a retry is reported as a provider switch, which the UI already draws).
        _svc()._broadcast(run, "ai_invoke_started", {
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
            started_ok = _svc()._execute_provider_chain(run, current_chain, current_prompt)
            _svc()._classify_end_reason(run, started_ok)
            _svc()._judge_hop(run)
            run["attempts_used"] = int(run.get("attempts_used") or 0) + 1

            # A document review loop owns the hop boundary. Checkpoint the durable effect,
            # then either stop or mint the next stage token and continue under its fixed provider.
            if run.get("document_review_loop"):
                loop = review._checkpoint_document_review_loop(run)
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
                prepared = _svc()._prepare_retry_token(run)
                selected_id = review.resolve_loop_provider(loop, loop["current_stage"])
                enabled = ai_settings_service.resolve_effective(run["project_id"]).get("providers") or []
                selected = next((item for item in enabled if item.get("id") == selected_id), None)
                if prepared is None or selected is None:
                    run["outcome"] = "none"
                    run["end_reason"] = "document_review_loop_transition_failed"
                    run["last_message"] = "next review-loop stage could not be scheduled"
                    run["document_review_loop_checkpointed"] = False
                    break
                _svc()._reset_attempt_state(run)
                run["hop_kind"] = loop["current_stage"]
                run["provider"] = oracle_module._provider_brief(selected)
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
            prepared = _svc()._prepare_retry_token(run)
            if prepared is None:
                run["retry_block_reason"] = "token_unavailable"
                break

            previous = run.get("provider") or {}
            _archive_attempt(run, "no_output", prepared["token_id_before"])
            _svc()._reset_attempt_state(run)
            current_chain = next_chain
            current_prompt = prepared["mention"]
            run["provider"] = oracle_module._provider_brief(current_chain[0])
            run["provider_id"] = current_chain[0].get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _svc()._broadcast(run, "ai_invoke_provider_switched", {
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

        _svc()._finalize_run(run)
        # 0317 TR0011 (Q153 opt-1): the run is now finished, so start_run's active-run guard
        # is clear — re-spawn the next hop's worker if the self-chain flagged a boundary.
        _svc()._maybe_auto_resume_hop(run)
    except Exception:
        logger.exception("ai-invoke worker crashed for %s", run["run_id"])
        run["end_reason"] = run.get("end_reason") or "exited"
        try:
            # A crashed attempt is never retried (L0007 §2.1): judge what it left, close out.
            _svc()._judge_hop(run)
            _svc()._finalize_run(run)
        except Exception:
            logger.exception("ai-invoke settle failed for %s", run["run_id"])
            run["status"] = "finished"
        # A crashed hop is a real stop, not a boundary: drop any pending re-spawn so the
        # chain does not silently continue past a failure.
        # 0406 T0022 item 4 — drop the queue, keep the intent. A crash is the third branch
        # that does not spawn: _finalize_run only calls begin_handoff when it sees pending
        # and skips release, so just clearing it blocks the group until the lease expires.
        # A durable row lets the user resume the chain from the same place.
        crashed_pending = _svc().pop_auto_resume(run.get("group_id"))
        if crashed_pending is not None:
            crashed_code = run.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE
            if crashed_code == HOP_HANDOFF_STOP_CODE:
                crashed_code = HOP_HANDOFF_FAILED_STOP_CODE
            _svc()._park_handoff(run, crashed_pending, crashed_code)


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
            run["provider"] = oracle_module._provider_brief(provider)
            run["provider_id"] = provider.get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _svc()._broadcast(run, "ai_invoke_provider_switched", {
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
            classification, detail = _svc()._api_execute(provider, prompt, run)
        else:
            classification, detail = _svc()._cli_execute(provider, prompt, run)

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
    scope_retry = oracle_module._scope_oracle_retry_run(run)
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
    review_hop_recovery = oracle_module._review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
    )
    if _svc().peek_auto_resume(run.get("group_id")) is not None and not review_hop_recovery:
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
    if _svc()._has_pending_question(run.get("doc_ref")):
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
    if oracle_module._scope_oracle_retry_run(run):
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
        new_docs = _svc()._oracle_new_docs(run)
    except Exception:
        logger.warning("ai-invoke retry recheck failed for %s", run["run_id"], exc_info=True)
        return True
    if not new_docs:
        return True
    hop_target = run.get("docs_target") or 1
    if run.get("mode") == "continuous" and _svc().peek_auto_resume(run.get("group_id")) is not None:
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
    if oracle_module._review_hop_recovery_run(run):
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
        issue = admission._call_issue_builder(issue_builder, run["run_id"])
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
        mention = admission._inject_hop_notes(
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
    return run["timeout_sec"] - (_svc()._now_mono() - anchor)


def _retry_remaining_sec(run: dict) -> float:
    """The budget the retry gate asks about: how long could another attempt run?

    0446 T0014 §4-4: `_remaining_sec` alone would answer "none" for every hop that
    legitimately outlived its no-progress threshold BY WORKING, and `_retry_eligible`
    would then report `budget_exhausted` for a run with hours of ceiling left. Both
    limits are real, so the smaller one is the answer.
    """
    return min(_stall_remaining_sec(run), _absolute_remaining_sec(run))


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
    max_turns = max(_svc().API_MAX_TURNS_PER_DOC, max(1, run["docs_target"]) * _svc().API_MAX_TURNS_PER_DOC)

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
        {"role": "system", "content": provider_api._api_system_prompt()},
        {"role": "user", "content": provider_api._api_help_prompt(prompt)},
    ]
    if is_chat:
        # 0505 T0006 (DB0005 3.3): the FIRST self-HTTP this hop may open. Its status/
        # error land in the same three columns as the other five mediated calls, and a
        # failure here ends the hop before the turn loop -- but the same `run` object
        # carries the three fields into persist_run_record/finished_payload regardless.
        status, chat_context = _svc()._conversation_context(run, current_token)
        run["last_tool_name"] = "conversation_context"
        run["last_tool_status"] = status
        if run.get("transport_api_base") is None:
            run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
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
        remaining = _svc()._remaining_sec(run)
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
                reply_text, tool_call, assistant_msg = _svc()._call_anthropic(
                    base_url, model, key, conversation, call_timeout,
                    tool_name, tool_desc, tool_schema, True,
                )
            else:
                reply_text, tool_call, assistant_msg = _svc()._call_openai(
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
        is_glm_openai = provider_api._is_glm_openai_provider(provider)
        if tool_specs is None and not is_glm_openai:
            # Restore the established direct-tool boundary before counting calls: a
            # non-GLM provider that returns a name other than the sole exposed tool is
            # indistinguishable from no tool call and must enter the miss/nudge path.
            tool_calls = [call for call in tool_calls if (call.get("name") or tool_name) == tool_name]
        run["tool_calls_received"] = run.get("tool_calls_received", 0) + len(tool_calls)
        trace["received"] = len(tool_calls)
        if not tool_calls:
            trace["disposition"] = "nudge" if run["tool_call_misses"] < _svc().API_MAX_TOOL_NUDGES else "no_tool"
            run["tool_call_misses"] += 1
            if run["tool_call_misses"] <= _svc().API_MAX_TOOL_NUDGES:
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
                    conversation.append(_svc()._tool_result_msg(
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
                        conversation.append(_svc()._tool_result_msg(
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
                        _status, resp = api_server_tools.run_test(run, call["input"], _svc()._remaining_sec(run))
                    elif call["name"] == "read_document":
                        _status, resp = _svc()._api_read_document(run, current_token, call["input"])
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
                conversation.append(_svc()._tool_result_msg(
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
                        conversation.append(_svc()._tool_result_msg(
                            kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                        ))
                        continue
                    if completion_call is not None:
                        duplicate = api_server_tools.ToolError(422, "invalid_tool_call")
                        _status, resp = api_server_tools.error_payload(call_name, duplicate)
                        conversation.append(_svc()._tool_result_msg(
                            kind, call, json.dumps(resp, ensure_ascii=False)[:16000]
                        ))
                        continue
                    completion_call = call
                    run["tool_calls_executed"] = run.get("tool_calls_executed", 0) + 1
                if completion_call is None:
                    continue
                tool_call = completion_call

        if workflow_pending:
            status, resp = _svc()._workflow_decide(run, current_token, tool_call["input"])
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
                    resolved = admission._continuation_docs_target(
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
                    max_turns = max(max_turns, turn + max(1, run["docs_target"]) * _svc().API_MAX_TURNS_PER_DOC)
                if next_token:
                    current_token = next_token
                    _adopt_continuation_token(run, next_token)
                result_text = next_mention or json.dumps(resp, ensure_ascii=False)[:4000]
                conversation.append(_svc()._tool_result_msg(kind, tool_call, result_text))
                if run["mode"] == "single" or not next_token:
                    break
                continue
            conversation.append(_svc()._tool_result_msg(
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
                conversation.append(_svc()._tool_result_msg(kind, tool_call, json.dumps(resp, ensure_ascii=False)[:4000]))
                break
            conversation.append(_svc()._tool_result_msg(
                kind, tool_call,
                f"Conflict resolve failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        if is_chat:
            status, resp = _svc()._conversation_turn_register(run, current_token, tool_call["input"])
            run["last_tool_name"] = "conversation_turn_register"
            run["last_tool_status"] = status
            if 200 <= status < 300:
                run["last_tool_error"] = None
                registered += 1
                break
            reason = _registration_error_summary(resp)
            run["last_tool_error"] = reason[:500]
            run["register_errors"].append({"status": status, "reason": reason, "turn": turn})
            conversation.append(_svc()._tool_result_msg(
                kind, tool_call,
                f"Chat reply registration failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        trace["register_attempted"] = True
        status, resp = _svc()._inbox_register(run, current_token, tool_call["input"])
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
            conversation.append(_svc()._tool_result_msg(kind, tool_call, result_text))
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
            conversation.append(_svc()._tool_result_msg(
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
    run["last_message"] = oracle_module._truncate_front(last_text)
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
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
    body = {
        "files": tool_input.get("files") or [],
        "complete": bool(tool_input.get("complete")),
    }
    req = urllib.request.Request(
        f"{provider_api._resolve_transport_api_base(run)}/groups/{run['group_id']}/git/merge/{run['merge_id']}/resolve-token",
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


def _workflow_decide(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    # 0505 T0006 (DB0005 3.3): whichever of the six mediated self-HTTP calls opens
    # FIRST in this hop wins transport_api_base; already-set stays untouched.
    if run.get("transport_api_base") is None:
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
    body = {
        "doc_class": tool_input.get("doc_class") or "standard",
        "sequence": tool_input.get("sequence") or [],
    }
    req = urllib.request.Request(
        f"{provider_api._resolve_transport_api_base(run)}/workflow/{run['doc_ref']}/decide",
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
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
    api_base = provider_api._resolve_transport_api_base(run).rstrip("/")
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
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{provider_api._resolve_transport_api_base(run)}{path}", data=data,
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
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
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
        run["transport_api_base"] = provider_api._sanitize_diagnostic_base(provider_api._resolve_transport_api_base(run))
    body = _svc()._register_envelope(context, run, tool_input)
    req = urllib.request.Request(
        f"{provider_api._resolve_transport_api_base(run)}/inbox",
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
