# ────────────────────── ai_invoke_service part 3 of 3 — status, resume, review gate ──────────────────────
#
# Not imported on its own in production: ai_invoke_service._load_parts() executes this
# file in THAT module's globals(). It is a pure file split (flowgate.default.0497 T0009)
# — the lines were carried over verbatim, nothing was rewritten. See the file-split note
# in ai_invoke_service.py's module docstring.
#
# Holds: status/cancel, pause/resume and the global active list, the per-hop re-spawn,
# the review gate, SSE and the document review loop

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

# ── Status / cancel (P0005 scenarios 6, 8) ───────────────────────────────────

def get_status(run_id: str) -> dict:
    run = get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["status"] == "finished":
        return {"ok": True, "run_id": run_id, "status": "finished", "mode": run["mode"],
                **finished_payload(run)}
    # 0226 NR0003 §5-2: count run-attributed documents (the same oracle filter the
    # final judge uses) instead of the raw group max-seq delta, which inflated the
    # live counter with drafts (auto-created N/T) and documents outside this run.
    docs_so_far = 0
    if run.get("completion_oracle") is None:
        # A scoped-oracle run targets 0 documents; counting group documents here would
        # report progress like 1/0 against work it does not measure (0248 B0001).
        try:
            docs_so_far = len(_oracle_new_docs(run))
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
        "pending_q_doc_ids": _open_q_doc_ids(run["group_id"]),
        "document_review_loop": document_review_loop_payload(run),
    }

def cancel_run(run_id: str) -> dict:
    run = get_run_record(run_id)
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

    run = get_run_record(run_id)
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
        docs_reached = len(_oracle_new_docs(run))
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
    run = _active_run_for_group(group_id)
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
    return _active_run_for_group(group_id) is not None

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
        _auto_resume[group_id] = dict(payload)
    _write_handoff_row(group_id, payload, _active_run_for_group(group_id))

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

def _review_no_verdict_excerpt(run: Optional[dict]) -> Optional[str]:
    """T0007 §3.2.2/A10-3: the excerpt a human uses to judge WHY a review hop recorded no
    verdict, and it must show both the provider/attempt/exit context AND the actual failure
    core (a usage-limit message, stderr, stdout or a timeout diagnosis) together — neither
    alone is enough to act on.

    Returning "the first non-empty source" (the earlier shape of this function) is wrong
    here: `run["fallback_history"]` always has a truthy `detail` for every attempt this hop
    already retried past (`_no_output_detail` unconditionally constructs a generic "worker
    exited ... without registering a document" sentence, even with no message), so it would
    win over the CURRENT — final — attempt's own `stderr_tail`/`stdout_tail`, which is
    exactly where the incident's real usage-limit text lives. The core below is therefore
    picked from the LATEST attempt's own signals first, the archived (earlier-attempt)
    detail only as a last resort before the fully-generic sentence, and it is always
    composed onto the provider/attempt/exit head rather than substituted for it.

    `last_message`/`stderr_tail`/`stdout_tail` can all carry raw, unfiltered process output
    (§3.2.3): `_recover_cli_last_message` sets `last_message` to the CLI's full trimmed
    stdout for a `claude`-kind attempt, not just a parsed "final answer" field, so a provider
    that echoes its own outgoing `Authorization: Bearer ...` call on failure lands that value
    in `last_message` exactly as readily as in `stderr_tail`/`stdout_tail`. All three are
    routed through `_redact_secrets` before `excerpt()`. The archived-attempt fallback below
    reads the SAME `last_message` back out — `_no_output_detail` embeds it verbatim into the
    `fallback_history[-1]["detail"]` sentence (§2.6) — so that value is redacted too;
    `timeout_diagnosis` is a watchdog-composed sentence (`_resolve_timeout_diagnostics`, pure
    f-strings over counters) that never carries process output, so it is left as-is.

    rev4: `_redact_secrets`'s two regexes only strip a token wearing an `Authorization:`/
    `Bearer ` label. The run's own raw task token rides to the provider process unlabeled
    (the `FLOWGATE_TOKEN` env var), so every core source below also gets the run's own known
    raw token(s) — this attempt's current one and every earlier attempt's, tracked by
    `_note_issued_raw_token` — for literal-value redaction, independent of any label.

    rev5: every prompt text this run has written to a provider's stdin — this attempt's
    current `run["mention"]` and every earlier (possibly already-rotated) attempt's, from
    `_note_issued_prompt` — redacts every core source below, plus the archived-attempt
    fallback (an earlier attempt's OWN prompt could just as easily be echoed into ITS
    archived detail), exactly like `known_tokens`.
    """
    run = run or {}
    known_tokens = _known_run_raw_tokens(run)
    known_prompts = _known_run_prompts(run)
    core = (
        excerpt(_redact_secrets(run.get("last_message"), known_tokens, known_prompts))
        or excerpt(_redact_secrets(run.get("stderr_tail"), known_tokens, known_prompts))
        or excerpt(_redact_secrets(run.get("stdout_tail"), known_tokens, known_prompts))
        or excerpt(run.get("timeout_diagnosis"))
    )
    if not core:
        history = run.get("fallback_history") or []
        if history:
            core = excerpt(_redact_secrets((history[-1] or {}).get("detail"),
                                           known_tokens, known_prompts))
    # T0007 rev2: `continuation_selected_provider_name` is the CHAIN HEAD picked before this
    # attempt ran (0435 T0004) and is never updated afterward. When an override-less review
    # hop's startup fell back past that head, `_execute_provider_chain` moved
    # `run["provider"]`/`run["provider_id"]` to whichever provider actually started
    # (L2620-2626) — that is the provider whose exit_code/attempts_used this sentence
    # describes, so it must win. The two only diverge on a startup fallback; with no
    # fallback `run["provider"]` is chain[0] too (L2512-2513), so this reorder is a no-op
    # for every other shape.
    provider_name = (
        (run.get("provider") or {}).get("name")
        or run.get("continuation_selected_provider_name")
        or run.get("continuation_selected_provider_id")
        or "the reviewer"
    )
    # Never empty: even with no message, tail or diagnosis anywhere, the provider/attempt/
    # exit-code sentence T0007 §3.2.3 requires is always constructible.
    head = (
        f'"{provider_name}" exited {run.get("exit_code")} on attempt '
        f'{int(run.get("attempts_used") or 0)} without recording a review verdict.'
    )
    if core and core not in head:
        return excerpt(f"{head} {core}")
    return excerpt(head)

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
                _review_no_verdict_excerpt(run) if stop_code == REVIEW_NO_VERDICT_STOP_CODE
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
    run["resumable"] = is_resumable(stop_code)
    run["stop_reason"] = _stop_reason_text(stop_code, run)
    if run["stop_code"] == before_code and run["stop_reason"] == before_reason:
        return
    _persist_run_record(run)
    # The engine speaks for this stop: approve_failed / advance_blocked belong to the inbox's
    # set, but the inbox never saw this one — the gate settled the step with no request in
    # flight. `error_text` is the §4.3 sentence rather than the attempts-and-last-message
    # default, because for these two codes the exception IS the news.
    _notify_chain_failure_if_needed(
        run,
        notify_codes=PARK_NOTIFY_STOP_CODES,
        error_text=run.get("stop_reason"),
    )
    # Same pair, same order as _finalize_run: the record is durable before the browser is told
    # to re-read. The card is holding a `hop_handoff` payload right now and is waiting for the
    # successor hop that is never coming; this is what replaces it with the real stop.
    _broadcast(run, "ai_invoke_finished", finished_payload(run))
    _broadcast(run, "group_view_refresh", {
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
    run["resumable"] = is_resumable(stop_code)
    _write_handoff_row(group_id, pending, run, stop_code=stop_code)
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
        return _auto_resume.get(group_id)

def pop_auto_resume(group_id: Optional[str]) -> Optional[dict]:
    if not group_id:
        return None
    with _auto_resume_lock:
        return _auto_resume.pop(group_id, None)

def clear_auto_resume(group_id: Optional[str]) -> None:
    if not group_id:
        return
    with _auto_resume_lock:
        _auto_resume.pop(group_id, None)

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
        _park_handoff(run, pending, parked_code)
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
    _write_handoff_row(group_id, pending, run)
    try:
        # 0414 L0008 §2.1 진입점 2: the gate decides what the next hop IS — review, rework,
        # approve-and-continue, or stop. With no review selection it resolves to "work" and
        # calls the same _spawn_auto_resume this line used to call directly.
        started = run_review_gate(group_id, pending, run)
    except HTTPException as exc:
        logger.warning("ai-invoke auto-resume rejected for %s: %s",
                       group_id, getattr(exc, "detail", exc))
        _park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    except Exception:
        logger.exception("ai-invoke auto-resume failed for %s", group_id)
        _park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    if not started:
        return          # the gate parked the chain; its durable row IS the resume card
    # The follow-up hop actually started. **Only now** is the intent cleared.
    _clear_handoff_row(group_id, run.get("run_id"))

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

    start_run(
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

# ── 0414 L0008: the [검수] gate ───────────────────────────────────────────────────────
#
# Three entry points call resolve_review_gate and they all get the same answer, because the
# answer is DERIVED, never stored (§2.1/§2.3):
#   1. the inbox self-chain boundary (_continuation_self_chain) — "is this slot reviewed?"
#   2. the engine hop settlement (_maybe_auto_resume_hop) — "review / rework / approve / stop?"
#   3. the human resume (resume_chain) — the same question after a restart, with no memory
#
# invariant R1 (0414 M0020 / CH0019): a step whose review count is not 0 never advances with
# a reviewer's complaint left unanswered. It passed, or every round it was given was reviewed
# AND reworked, or the chain stopped. There is no fourth path.
# The earlier form of R1 — "never advances without a `pass`" — ended a spent budget by parking
# the chain, which left the LAST round's findings recorded and never fixed. That is exactly
# what M0020 refused ("지적을 두번했으면 당연히 수정도 두번해야지"), so a finite count is now a
# budget of review+rework PAIRS: N 검수 · 지적마다 수정 · 마지막 수정 뒤 다음 단계.

def _enabled_provider_chain(project_id: Optional[str]) -> list[dict]:
    """The project's effective provider chain, or [] when it cannot be read."""
    if not project_id:
        return []
    try:
        return (ai_settings_service.resolve_effective(project_id) or {}).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("review gate provider chain lookup failed for %s", project_id,
                       exc_info=True)
        return []

def _provider_enabled(project_id: Optional[str], provider_id: Optional[str]) -> bool:
    return bool(provider_id) and any(
        p.get("id") == provider_id for p in _enabled_provider_chain(project_id)
    )

def _first_enabled_provider_id(project_id: Optional[str]) -> Optional[str]:
    """The project default — first entry of the effective chain (L0008 §2.2)."""
    chain = _enabled_provider_chain(project_id)
    return chain[0].get("id") if chain else None

def _provider_name_of(project_id: Optional[str], provider_id: Optional[str]) -> Optional[str]:
    """Display name for a resolved provider id; the id itself when the name is unknown."""
    if not provider_id:
        return None
    for provider in _enabled_provider_chain(project_id):
        if provider.get("id") == provider_id:
            return provider.get("name") or provider_id
    return provider_id

def _map_lookup(overrides: Optional[dict], item_seq: Optional[int]):
    """Both key spellings, exactly as _resolve_continuation_hop_override accepts them."""
    if not overrides or item_seq is None:
        return None
    return overrides.get(str(item_seq), overrides.get(item_seq))

def resolve_review_count(review_count_overrides: Optional[dict], item_seq: Optional[int]) -> int:
    """How many times this step's output is reviewed (L0008 §2.2).

    0 for every step the user did not pick — count 0 never reaches storage, because P0007's
    normalization already dropped it, so "absent" and "0" are the same fact. A value outside
    REVIEW_COUNT_VALUES can only come from a hand-edited row (the write path is 422-guarded),
    and is read as "no review" rather than crashing the chain.
    """
    raw = _map_lookup(review_count_overrides, item_seq)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return REVIEW_COUNT_DEFAULT
    if raw not in REVIEW_COUNT_VALUES:
        logger.warning("review gate: ignoring out-of-range review count %r for item_seq %s",
                       raw, item_seq)
        return REVIEW_COUNT_DEFAULT
    return raw

def resolve_round_limit(count: int) -> int:
    """How many review+rework rounds this step gets; REVIEW_ROUNDS_NO_LIMIT = no ceiling.

    -1 is the user asking for "until it passes", and it is taken literally (0414 0022-TR
    rejection): there is no round number at which the chain gives up and calls a human.
    Only a `pass`, a `hold`, or a loop breaker ends it.
    """
    return REVIEW_ROUNDS_NO_LIMIT if count == -1 else int(count)

def review_rounds_remain(rounds_used: int, limit: int) -> bool:
    """Is another review round allowed? An unbounded budget always says yes."""
    return limit == REVIEW_ROUNDS_NO_LIMIT or rounds_used < limit

def resolve_reviewer(
    reviewer_overrides: Optional[dict], item_seq: Optional[int], project_id: Optional[str]
) -> Optional[str]:
    """Who reviews this step (L0008 §2.2): the step's own pick, else the project default.

    The step EXECUTOR's provider tiers are deliberately not consulted — a reviewer is chosen
    to have the work read by someone else, and folding the executor in here would quietly
    make that self-review.

    A pick that is no longer enabled degrades to the default rather than removing the review:
    a chain a person parked must stay resumable (P0007 [엣지] 재개 시 검수자 소멸). The 422
    that refuses the same pick outright belongs to the fresh-request path only.
    """
    provider_id = _map_lookup(reviewer_overrides, item_seq)
    if provider_id and _provider_enabled(project_id, provider_id):
        return provider_id
    if provider_id:
        logger.warning(
            "review gate: reviewer %s is no longer enabled for %s — "
            "falling back to the project default reviewer for item_seq %s",
            provider_id, project_id, item_seq,
        )
    return _first_enabled_provider_id(project_id)

def _stored_provider_for_item_seq(doc_ref: Optional[str], item_seq: Optional[int]) -> Optional[str]:
    """The provider persisted on that sequence row, if any."""
    if not doc_ref or item_seq is None:
        return None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        for row in db_wfseq.get_sequence_items(seq["id"]) or []:
            if row.get("item_seq") == item_seq:
                return row.get("provider_id")
    except Exception:  # noqa: BLE001 — a stored preference must never stall a hop
        logger.warning("review gate stored provider lookup failed for %s", doc_ref, exc_info=True)
    return None

def resolve_step_executor(
    bundle: dict, item_seq: Optional[int], project_id: Optional[str], doc_ref: Optional[str]
) -> Optional[str]:
    """Who REWORKS this step (L0008 §2.2) — the step's executor, not its reviewer.

    Re-plays start_run's own priority order (step override → explicit pin → stored sequence
    assignment → project default) ahead of time, because the rework hop is mode="single" and
    start_run's continuous tiers would not run for it.
    """
    provider_id = _map_lookup(bundle.get("provider_overrides"), item_seq)
    if provider_id and _provider_enabled(project_id, provider_id):
        return provider_id
    base_provider_id = bundle.get("base_provider_id")
    if bundle.get("provider_pinned") and _provider_enabled(project_id, base_provider_id):
        return base_provider_id
    stored = _stored_provider_for_item_seq(doc_ref, item_seq)
    if stored and _provider_enabled(project_id, stored):
        return stored
    return _first_enabled_provider_id(project_id)

# doc_review_status values that mean "this output is not through the gate yet".
REVIEW_PENDING_DOC_STATUSES = frozenset({"pending_review", "revised", "rejected"})

def _pending_review_slot(doc_ref: Optional[str]) -> Optional[dict]:
    """The slot waiting on the gate — at most one per group (L0008 §2.3).

    One running chain has one hop, which fills one document, so the search is simply "the
    most recently FILLED slot": if its document is still unapproved it is the waiting slot,
    and if it is already approved there is nothing waiting. Slots with no result document
    yet are skipped rather than ending the scan — an ai_direct N/T head can sit empty ahead
    of the report slot that was actually just filled.
    """
    if not doc_ref:
        return None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        items = db_wfseq.get_sequence_items(seq["id"]) or []
    except Exception:  # noqa: BLE001 — an unreadable sequence falls back to the old flow
        logger.warning("review gate slot lookup failed for %s", doc_ref, exc_info=True)
        return None
    for item in sorted(items, key=lambda i: i.get("item_seq") or 0, reverse=True):
        result_doc_id = item.get("result_doc_id")
        if not result_doc_id:
            continue
        doc = db_docs.get_by_id(result_doc_id) or {}
        status = doc.get("doc_review_status") or ""
        if status not in REVIEW_PENDING_DOC_STATUSES:
            return None          # the newest filled slot is already approved → nothing waits
        return {
            "item_seq": item.get("item_seq"),
            "doc_id": doc.get("doc_id") or result_doc_id,
            "doc_type": (doc.get("type_code") or item.get("type") or "").upper(),
            "revision_no": int(doc.get("revision_no") or 0),
            "review_status": status,
            # T0005 2.1.1: the accumulated FACT of which review rows were already turned
            # into an automatic rejection. `review_status` above is a momentary value a
            # landed rework overwrites (`rejected + submit -> revised`); this one only
            # grows, so it survives that transition and lets the gate tell "already
            # rejected once" from "not in rejected status right now".
            "rejection_history": _parse_rejection_history(doc.get("rejection_history")),
        }
    return None

def _parse_rejection_history(raw) -> list:
    """documents.rejection_history as a list of dict items (T0005 2.1.1).

    The column is free-form JSON text. Absent, unparseable, or not-a-list all mean the
    SAME thing here: an empty history. A malformed column degrades the review_id guard
    back to "nothing recorded yet" -- it must never break the gate.
    """
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []

def _review_key(value) -> str:
    """One comparable form for a `document_reviews.id` (T0005 2.1.2).

    The column is a positive integer, but it round-trips through the rejection_history
    JSON and can come back as "244". Comparing normalized strings makes 244 and "244"
    one key. Values that cannot identify a review row -- None, bool, an empty or
    whitespace-only string, a non-numeric string -- all fold to "", which matches nothing.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.isdecimal():
            return ""
        try:
            return text if int(text) > 0 else ""
        except ValueError:
            return ""
    return ""

def _review_findings(review: Optional[dict]) -> list:
    """A review row's findings as a list — the column stores a JSON array string."""
    findings = (review or {}).get("findings")
    if isinstance(findings, str):
        try:
            findings = json.loads(findings)
        except (TypeError, ValueError):
            return []
    return findings if isinstance(findings, list) else []

def _normalize_ws(value) -> str:
    return " ".join(str(value or "").split())

def review_finding_digest(review: Optional[dict]) -> str:
    """A deterministic fingerprint of one review's findings (L0008 §2.3).

    Whitespace-normalized so a reflowed line is not mistaken for a new complaint. Two
    consecutive `issues` verdicts with the SAME digest mean the rework changed nothing the
    reviewer cares about, which is the practical safety net behind an unbounded -1.
    """
    parts = []
    for finding in _review_findings(review):
        if isinstance(finding, dict):
            parts.append(_normalize_ws(finding.get("locus")) + "\u241f"
                         + _normalize_ws(finding.get("note")))
        else:
            parts.append(_normalize_ws(finding))
    return hashlib.sha256("\u241e".join(parts).encode("utf-8")).hexdigest()

def _check_expected_progress(
    bundle: dict, slot: dict, reviews: list[dict]
) -> Optional[str]:
    """Did the hop that just ran leave what it was supposed to leave? (L0008 §2.3)

    Without this the gate re-reads `rounds_used == 0` after a review hop that recorded
    nothing and launches another review hop — forever. This is the only thing standing
    between the gate and that loop.

    Skipped entirely on a COLD start (`last_stage` absent): a human pressing [이어서 진행]
    after a restart has no previous hop to hold to account, and the DB derivation alone is
    already correct for them.
    """
    last_stage = bundle.get("last_stage")
    if not last_stage:
        return None
    if bundle.get("last_stop_code") == "question_pending":
        return "question_pending"          # waiting on a human answer — do not spin the loop
    if last_stage == REVIEW_HOP_KIND and len(reviews) <= int(bundle.get("rounds_before") or 0):
        return REVIEW_NO_VERDICT_STOP_CODE
    if last_stage == REWORK_HOP_KIND and int(slot.get("revision_no") or 0) <= int(
        bundle.get("revision_before") or 0
    ):
        return REVIEW_STALLED_STOP_CODE
    if (
        len(reviews) >= REVIEW_STALL_ROUNDS
        and (reviews[0].get("verdict") or "").lower() == "issues"
        and review_finding_digest(reviews[0]) == review_finding_digest(reviews[1])
    ):
        return REVIEW_STALLED_STOP_CODE
    return None

def _log_review_annotation_failure(kind: str, slot: dict, bundle: dict, error) -> None:
    """Best-effort durable signal; observability must not replace the original outcome."""
    try:
        from modules.flow_gate.workflow import event_logger

        doc = db_docs.get_by_id(slot["doc_id"]) or {}
        group_id = bundle.get("group_id") or doc.get("group_id")
        project_id = doc.get("project_id") or (str(group_id).split(".", 1)[0] if group_id else "__SYSTEM__")
        event_logger.log_review_annotation_failed(
            kind=kind, project_id=project_id,
            actor_user_id=bundle.get("issued_to") or "u-system", group_id=group_id,
            document_id=doc.get("id"), doc_id=slot["doc_id"], error=error,
        )
    except Exception:  # noqa: BLE001
        logger.warning("review gate could not persist annotation %s failure", kind, exc_info=True)

def resolve_review_gate(bundle: dict) -> dict:
    """What happens next for the slot this chain is standing on (L0008 §2.3).

    Returns {stage: work|review|rework|stop, ...}. Every fact it reads is re-derived here
    and now — rounds used is `len(document_reviews)`, "a rework landed" is
    `document.revision_no > the last review's revision_no`, "already rejected" is
    `doc_review_status`. Nothing about the loop's position is persisted, which is what makes
    a restart, an auto-handoff and a manual resume converge on one answer.
    """
    slot = _pending_review_slot(bundle.get("doc_ref"))
    if slot is None:
        return {"stage": WORK_HOP_KIND}                      # nothing to review — old flow

    count = resolve_review_count(bundle.get("review_count_overrides"), slot["item_seq"])
    if count == 0:
        return {"stage": WORK_HOP_KIND, "approve_first": True, "slot": slot, "count": 0}

    limit = resolve_round_limit(count)
    try:
        reviews = db_reviews.list_by_doc(slot["doc_id"]) or []      # newest first
    except Exception as exc:  # noqa: BLE001
        logger.warning("review gate could not read reviews for %s", slot["doc_id"],
                       exc_info=True)
        _log_review_annotation_failure("read", slot, bundle, exc)
        return {"stage": "stop", "stop_code": "review_history_unreadable",
                "slot": slot, "count": count, "limit": limit, "detail": str(exc)}
    rounds_used = len(reviews)
    latest = reviews[0] if rounds_used else None
    common = {"slot": slot, "count": count, "limit": limit, "rounds_used": rounds_used}

    blocked = _check_expected_progress(bundle, slot, reviews)
    if blocked is not None:
        return {"stage": "stop", "stop_code": blocked, **common}

    if rounds_used == 0:
        return {"stage": REVIEW_HOP_KIND, "round_no": 1, **common}

    verdict = (latest.get("verdict") or "").lower()
    if verdict == "pass":
        return {"stage": WORK_HOP_KIND, "approve_first": True, **common}
    if verdict == "hold":
        return {"stage": "stop", "stop_code": REVIEW_VERDICT_HOLD_STOP_CODE, **common}

    # verdict == "issues" from here, and THREE facts gate the rejection. Two of them ask
    # the same idempotency question at different resolutions (0458 NR0003 I1):
    #
    #   * the document is in `rejected` right now — by a human, or by an earlier pass of
    #     this gate — so there is nothing left to reject;
    #   * this exact review row is already in the rejection history. The status alone was
    #     not enough (I3): `rejected` still means "not again", but `revised` does NOT mean
    #     "not yet", because that is precisely the value a landed rework leaves behind.
    #
    # The third is revision match — 0459 NR0003's second defect. The complaint has to be
    # about the revision standing there NOW. `reject_first` used to read the status alone,
    # so a rework that had already landed (revision_no past the review's, status `revised`)
    # was pushed back to `rejected` just before the NEXT review round started. That round
    # then passed, and the pass tried to approve a `rejected` document — a combination
    # transition_rules deliberately does not list — so settle_completed_step returned
    # approve_failed BEFORE its target check and the chain parked one approval short of
    # `completed`. An old verdict does not reject a new revision, and `rejected + approve`
    # stays absent from the transition table.
    latest_revision = int(latest.get("revision_no") or 0)
    slot_revision = int(slot["revision_no"])
    # T0005 2.1.4: a THIRD condition joins status and revision-match by AND — this exact
    # review row must not already have produced a rejection. Status alone is not enough
    # (I3: `rejected` still means "not again", but a landed rework leaves `revised` behind,
    # which does NOT mean "not yet" — that value is precisely what a fixed complaint looks
    # like right before the NEXT review round starts). Without this third term, a human
    # mark_revised back to `pending_review` at the SAME revision (A6) would pass both
    # existing checks and re-reject a review row already recorded in rejection_history.
    reject_first = (
        slot["review_status"] != "rejected"
        and slot_revision == latest_revision
        and not _review_already_rejected(latest, slot, bundle.get("api_base_url"))
    )

    if slot_revision > latest_revision:
        # The rework for this complaint already landed (I2). Reaching here IS the proof that
        # the complaint was rejected and then fixed, so this branch decides the NEXT round
        # only and carries no reject_first at all (0458 NR0003 §8 방향 A). Carrying it was
        # what re-rejected the already-fixed review, drove `revised -> rejected`, and made
        # the following `pass` fail its own approval: the fresh revision keeps its `revised`
        # status into the next round, which is what lets a later `pass` settle through the
        # ordinary `revised + approve -> approved` transition and reach `completed`.
        if review_rounds_remain(rounds_used, limit):
            # -1 never leaves this branch: "until it passes" reviews the fresh revision
            # too, round after round, for as long as the reviewer keeps finding issues.
            return {"stage": REVIEW_HOP_KIND, "round_no": rounds_used + 1, **common}
        # 0414 M0020 / CH0019: a finite count is a budget of review+rework PAIRS, and the
        # last pair has just closed — every complaint this step produced was reworked, so
        # the step is done. The reworked revision is approved and the chain moves on.
        logger.info(
            "review gate: item_seq %s advances after %s review+rework round(s); the finite "
            "budget is spent and every finding was reworked",
            slot["item_seq"], rounds_used,
        )
        return {"stage": WORK_HOP_KIND, "approve_first": True, **common}

    # No rework has landed for this complaint yet. EVERY `issues` verdict earns its rework
    # hop, the LAST round's included — 0414 M0020 "지적을 두번했으면 수정도 두번": a complaint
    # that is only recorded and never fixed is not a review. So count=1 runs
    # review → rework → advance, and count=2 runs review → rework → review → rework → advance.
    return {"stage": REWORK_HOP_KIND, "round_no": rounds_used,
            "reject_first": reject_first, **common}

# The automatic rejection text. English on purpose: T0010 작업 4 forbids new Korean literals
# in server modules, and build_review_mention's own review instructions are English in every
# locale, so the rejection the same reviewer's findings produce matches what it reads.
REVIEW_REJECT_HEADING = "## Automated review rejection"
REVIEW_LOCUS_UNSPECIFIED = "(locus unspecified)"

def build_auto_reject_reason(review: Optional[dict], slot: dict, api_base_url: Optional[str]) -> str:
    """The rejection text, which IS the rework instruction (L0008 §2.6).

    transition_document_review refuses an empty reason, and an `issues` verdict is allowed to
    carry neither a comment nor findings — so the heading is unconditional and the reason can
    never come out blank. Over-length is trimmed from the TAIL: the heading and the first
    findings are the part a reworker needs, and the full set stays one GET away.
    """
    lines = [REVIEW_REJECT_HEADING]
    comment = (review or {}).get("comment")
    if comment and str(comment).strip():
        lines += ["", str(comment).strip()]
    findings = _review_findings(review)
    shown = findings[:REVIEW_REASON_MAX_FINDINGS]
    if shown:
        lines.append("")
        for finding in shown:
            if isinstance(finding, dict):
                locus = _normalize_ws(finding.get("locus")) or REVIEW_LOCUS_UNSPECIFIED
                note = str(finding.get("note") or "").strip()
            else:
                locus, note = REVIEW_LOCUS_UNSPECIFIED, str(finding).strip()
            lines.append(f"- {locus}: {note}")
    if len(findings) > REVIEW_REASON_MAX_FINDINGS:
        lines.append(
            f"({len(findings) - REVIEW_REASON_MAX_FINDINGS} further finding(s) omitted here.)"
        )
    lines += ["", f"GET {(api_base_url or '').rstrip('/')}/document/{slot['doc_id']}/reviews"]
    text = "\n".join(lines)
    return text[:REVIEW_REASON_MAX_CHARS] if len(text) > REVIEW_REASON_MAX_CHARS else text

def _review_already_rejected(
    review: Optional[dict], slot: dict, api_base_url: Optional[str]
) -> bool:
    """Has THIS review row already been turned into a rejection? (T0005 2.1.3)

    The unit of a rejection is one `document_reviews` row, not the document's momentary
    status. `('rejected', 'submit') -> 'revised'` erases the status the old guard read
    alone, so every landed rework could re-open the same complaint for a second rejection.

    Two item shapes answer the question, checked per item in stored order:

    * items that CARRY the `review_id` key are matched by that id and by nothing else --
      two different review rows with byte-identical findings are two separate rejections,
      as they should be (A8). A `review_id` key present but null/blank/whitespace/invalid
      names no row: it must not fall through to the legacy reason match below (A9), or a
      LATER review row that happens to render the same text would be swallowed by it.
    * items with NO `review_id` key at all are the pre-T0005 shape, matched by their exact
      `reason` against the text this review would produce. `build_auto_reject_reason` is
      pure, so the same row always renders the same string, and a human-written reason
      never equals one (every automatic reason opens with REVIEW_REJECT_HEADING).
    """
    review_key = _review_key((review or {}).get("id"))
    legacy_reason = None
    for item in slot.get("rejection_history") or []:
        if "review_id" in item:
            item_key = _review_key(item.get("review_id"))
            if review_key and item_key == review_key:
                return True
            continue        # a different (or unidentifiable) review row -- never fall through
        if legacy_reason is None:
            legacy_reason = build_auto_reject_reason(review, slot, api_base_url)
        if str(item.get("reason") or "") == legacy_reason:
            return True
    return False

def _auto_reject(slot: dict, review: Optional[dict], bundle: dict) -> dict:
    """Turn an `issues` verdict into a real rejection (L0008 §2.6).

    Goes through pipeline_service.transition_document_review — the SINGLE writer of
    doc_review_status — with the chain issuer's real permissions, resolved by the same
    resolver the inbox auto-approve uses. Approval is never bypassed here and neither is
    rejection: an issuer without document.reject stops the chain instead of forcing it.
    """
    actor_user_id = bundle.get("issued_to")
    try:
        from modules.flow_gate.db import users as db_users
        from modules.flow_gate.workflow.routers.workflow import (
            _get_user_permissions as _resolve_user_permissions,
        )

        actor = db_users.get_by_id(actor_user_id) or {"user_id": actor_user_id, "is_admin": 0}
        permissions = _resolve_user_permissions(actor)
    except Exception as exc:  # noqa: BLE001
        logger.exception("review gate permission resolution failed for %s", actor_user_id)
        _log_review_annotation_failure("write", slot, bundle, exc)
        return {"ok": False, "stop_code": REVIEW_REJECT_DENIED_STOP_CODE, "detail": str(exc)}
    if "document.reject" not in permissions:
        detail = "issuer lacks document.reject"
        _log_review_annotation_failure("write", slot, bundle, detail)
        return {"ok": False, "stop_code": REVIEW_REJECT_DENIED_STOP_CODE,
                "detail": detail}
    reason = build_auto_reject_reason(review, slot, bundle.get("api_base_url"))
    try:
        from modules.flow_gate.workflow.pipeline_service import transition_document_review

        transition_document_review(
            doc_id=slot["doc_id"],
            action="reject",
            actor_user_id=actor_user_id,
            user_permissions=permissions,
            comment=reason,
            # T0005 2.1.5: the review row this rejection is FOR, so the stored history item
            # carries a `review_id` key the next gate pass can match against
            # (_review_already_rejected above). A missing review, or a row without an id,
            # passes None -- the pre-existing behaviour: the item is written without the
            # key and falls back to legacy reason matching for it.
            review_id=(review or {}).get("id"),
        )
    except Exception as exc:  # noqa: BLE001 — the stored document is never touched
        logger.warning("review gate auto-reject failed for %s", slot["doc_id"], exc_info=True)
        _log_review_annotation_failure("write", slot, bundle, exc)
        return {"ok": False, "stop_code": REVIEW_REJECT_FAILED_STOP_CODE, "detail": str(exc)}
    return {"ok": True}

def _latest_review_of(slot: dict) -> Optional[dict]:
    try:
        return db_reviews.get_latest_by_doc(slot["doc_id"])
    except Exception:  # noqa: BLE001
        logger.warning("review gate latest-review lookup failed for %s", slot["doc_id"],
                       exc_info=True)
        return None

def _user_pause_row_pending(group_id: Optional[str]) -> bool:
    """A user pause the engine must honour before it starts another hop.

    mark_user_paused cannot answer here: it needs a LIVE run tagged pause_requested, and by
    the time the gate runs the hop that carried that tag has already finished. The durable
    row is what survives, and it is the same row resume_chain consumes.
    """
    if not group_id:
        return False
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        row = db_paused.get_by_group(group_id)
    except Exception:  # noqa: BLE001 — fail open: a probe must not stall a healthy chain
        logger.warning("review gate user-pause probe failed for %s", group_id, exc_info=True)
        return False
    return row is not None and (row.get("stop_kind") or "user") == "user"

def _settle_gate_pass(group_id: str, slot: dict, bundle: dict, run: dict) -> str:
    """Everything that follows a step FINISHING — the SAME helper the inbox uses (§2.7).

    A second implementation of "approve → target reached? → user pause? → continue" would
    drift from the first, so the reviewed path and the unreviewed path share one.

    Two things bring a gated step here: a `pass` verdict, and (0414 M0020) a finite budget
    whose last review+rework pair has closed. Both mean "this step is done", so both settle
    identically — the document the second one approves is the reworked revision.
    """
    from modules.flow_gate.api import inbox_routes as _inbox

    result = _inbox.settle_completed_step(
        project=str(group_id).split(".", 1)[0],
        group_id=group_id,
        doc_id=slot["doc_id"],
        doc_type=slot.get("doc_type") or "",
        actor_user_id=bundle.get("issued_to"),
        completed_seq=slot.get("item_seq"),
        target_seq=bundle.get("target_seq"),
        user_paused_probe=lambda: _user_pause_row_pending(group_id),
    )
    outcome = result.get("outcome")
    if outcome == "continue":
        return "continue"
    stop_code = result.get("stop_code") or "approve_failed"
    if outcome == "completed":
        # The chain reached its target with the last step reviewed AND approved. No card:
        # settle_completed_step already removed the paused row, so only the lease is left.
        _clear_handoff_row(group_id, run.get("run_id"))
        try:
            db_group_ai_leases.release(group_id, run["run_id"])
        except Exception:  # noqa: BLE001
            logger.warning("review gate lease release failed for %s", group_id, exc_info=True)
        return outcome
    # user_paused keeps the human's own row (_write_handoff_row refuses to overwrite it);
    # approve_denied / approve_failed get a system row so the chain is pickable again.
    #
    # 0458 T0007 §2.1-3: ONE storage contract for every stopped outcome this gate can
    # reach — the exception string when settle_completed_step names one (`detail`), and
    # otherwise the sentence it does carry (`reason`), so a stop is never parked with the
    # reason it knows thrown away. `_stop_reason_text` reads this key back for
    # approve_failed and advance_blocked alike; settle_completed_step is also the only
    # entry point through which this gate can reach either code, so no advance path is
    # left storing nothing.
    run["review_reject_detail"] = result.get("detail") or result.get("reason")
    _park_handoff(run, bundle, stop_code)
    return outcome

def _queue_gate_bundle(group_id: str, bundle: dict) -> None:
    """Record the next hop's intent BEFORE launching it (L0008 §2.4).

    _finalize_run reads this queue to decide between begin_handoff and releasing the group
    lease. Launch first and the lease is gone by the time the successor asks for it, so the
    successor dies on 409 run_in_progress.
    """
    request_auto_resume(group_id, bundle)

def _spawn_review_hop(group_id: str, bundle: dict, gate: dict) -> dict:
    """Launch the review hop (L0008 §2.5).

    mode="single" + action_scope="review" is not cosmetic: it is the combination that gives
    the run _probe_doc_reviews as its judge ("did a review row appear?"). Anything else falls
    back to the document oracle, which a review can never satisfy.

    The token carries NO continuation_target_seq, so _continuation_self_chain does not run
    for the verdict submission at all — a review structurally cannot approve its own target
    or advance the chain.
    """
    from modules.flow_gate.services import workflow_decision_service

    slot = gate["slot"]
    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
    locale = bundle.get("locale") or "ko"
    api_base_url = bundle.get("api_base_url")
    issued_to = bundle.get("issued_to")
    reviewer_id = resolve_reviewer(bundle.get("reviewer_overrides"), slot["item_seq"], project_id)
    executor_id = resolve_step_executor(bundle, slot["item_seq"], project_id, bundle.get("doc_ref"))
    if reviewer_id and reviewer_id == executor_id:
        # Allowed — a person may deliberately pick it — but never silent (L0008 §2.2).
        logger.warning(
            "review gate: item_seq %s is being self-reviewed (reviewer and executor are both %s)",
            slot["item_seq"], reviewer_id,
        )

    def _issue_review(ai_run_id: Optional[str] = None) -> dict:
        issued = workflow_decision_service.request_review(
            doc_id=slot["doc_id"],
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            ai_run_id=ai_run_id,
        )
        mention = issued.get("mention") or ""
        return {
            "raw_token": issued["token"],
            "token_id": issued["token_id"],
            "scratch_dir": issued["scratch_dir"],
            "mention": _append_engine_review_clause(mention, gate),
        }

    # flowgate.default.0466 T0007 §3.3.3: resume_chain's cold [이어서 진행] path spawns this
    # same hop directly (not through run_review_gate), and its caller relays start_run's
    # own result dict back to the route the way every other start_run entry point does.
    # run_review_gate itself never reads the return value, so this is a pure addition.
    return start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=slot["doc_id"],
        action_scope="review",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=bundle.get("instruction_mode"),
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_review,
        provider_id=reviewer_id,
        chain_id=bundle.get("chain_id"),
        chain_docs_target=bundle.get("chain_docs_target"),
        chain_docs_reached=bundle.get("chain_docs_reached"),
        continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
        continuation_restart_max_attempts=bundle.get("restart_max_attempts"),
        continuation_review_count_overrides=bundle.get("review_count_overrides"),
        continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        hop_kind=REVIEW_HOP_KIND,
    )

def _append_engine_review_clause(mention: str, gate: dict) -> str:
    """The one clause an ENGINE-driven review hop adds to build_review_mention (L0008 §2.5).

    build_review_mention itself is never touched — the human [멘트복사] path shares it, and
    its body correctly tells that reader "a human decides afterward". On an unmanned chain
    that premise is false, so the reviewer has to be told the verdict wires straight into an
    automatic rejection and how many rounds are left. English, like the review instructions
    it extends (T0010 작업 4: no new Korean literals in server modules).
    """
    if not mention:
        return mention
    count = gate.get("count")
    round_no = gate.get("round_no")
    limit = gate.get("limit")
    budget = (
        "until the document passes"
        if count == -1
        else f"round {round_no} of {limit}"
    )
    # 0414 M0020: every round's findings are reworked, the last round's included, so the
    # clause no longer says "when a round remains". What the LAST reviewer of a finite budget
    # does need to know is that nobody reviews the fix its findings produce — the chain moves
    # on with it — so that round is told to name everything that still has to change.
    last_round = count != -1 and round_no == limit
    return mention + (
        "\n\n## Automated follow-up\n---\n"
        "This review runs inside an unmanned continuous chain, so no human reads your verdict "
        "before it takes effect. 'issues' rejects the document automatically with your comment "
        "and findings as the rejection reason, and hands it back to the step's own worker to "
        f"fix — every round's findings get their fix, this round's included. This is {budget}"
        + (" — there is no round ceiling: review and fix repeat until you return "
           "'pass', so keep reviewing until the document is right."
           if count == -1 else ".")
        + (
            " This is the LAST round: after the fix your findings produce, the chain moves on "
            "to the next step without another review, so name everything that still has to "
            "change." if last_round else ""
        )
        + " 'pass' approves the document and lets the chain move on; 'hold' stops the chain "
        "for a human. Judge accordingly."
    )

def _spawn_rework_hop(group_id: str, bundle: dict, gate: dict) -> dict:
    """Launch the rework hop (L0008 §2.6).

    The REWORKER is the step's own executor, not the reviewer — the reviewer reads, the
    author fixes. The issuer is invoke_mention_service.issue_rework_request, the same one
    the human [AI 수정] button uses, so the two can never drift into separate prompts.
    """
    from modules.flow_gate.services import invoke_mention_service

    slot = gate["slot"]
    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
    locale = bundle.get("locale") or "ko"
    api_base_url = bundle.get("api_base_url")
    issued_to = bundle.get("issued_to")
    executor_id = resolve_step_executor(bundle, slot["item_seq"], project_id, bundle.get("doc_ref"))

    def _issue_rework(ai_run_id: Optional[str] = None) -> dict:
        return invoke_mention_service.issue_rework_request(
            doc_id=slot["doc_id"],
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            ai_run_id=ai_run_id,
        )

    return start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=slot["doc_id"],
        # The TOKEN scope is what start_run receives (ai_invoke_routes maps rework->edit
        # before calling it), and it is also what picks _probe_doc_revision as the judge:
        # "did the revision number go up?" — the same fact §2.3 checks for review_stalled.
        action_scope="edit",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=bundle.get("instruction_mode"),
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_rework,
        provider_id=executor_id,
        chain_id=bundle.get("chain_id"),
        chain_docs_target=bundle.get("chain_docs_target"),
        chain_docs_reached=bundle.get("chain_docs_reached"),
        continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
        # flowgate.default.0476 NR0003 defect1 / T0005: sibling hops (_spawn_auto_resume,
        # _write_handoff_row) already forward this; without it here every rework hop
        # silently fell back to RESTART_MAX_ATTEMPTS_DEFAULT regardless of the user's pick.
        continuation_restart_max_attempts=bundle.get("restart_max_attempts"),
        continuation_review_count_overrides=bundle.get("review_count_overrides"),
        continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        hop_kind=REWORK_HOP_KIND,
    )

def run_review_gate(group_id: str, bundle: dict, run: dict) -> bool:
    """Derive the gate and act on it (L0008 §2.4). True when a next hop actually started.

    False means the chain was parked (a durable row + a released lease), so the caller must
    NOT clear the handoff row it wrote — that row is now the [이어서 진행] card.
    """
    gate = resolve_review_gate(bundle)
    slot = gate.get("slot")

    # 10-1: the rejection happens first and independently of what comes next, so a
    # "rounds exhausted" stop still leaves the reviewer's findings attached to the document.
    if gate.get("reject_first") and slot is not None:
        result = _auto_reject(slot, _latest_review_of(slot), bundle)
        if not result.get("ok"):
            run["review_reject_detail"] = result.get("detail")
            _park_handoff(run, bundle, result["stop_code"])
            return False

    stage = gate.get("stage")
    if stage == "stop":
        _park_handoff(run, bundle, gate.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE)
        return False

    if stage == WORK_HOP_KIND:
        if gate.get("approve_first") and slot is not None:
            if _settle_gate_pass(group_id, slot, bundle, run) != "continue":
                return False
        # Deliberately NOT re-queued, unlike the two branches below: _finalize_run already
        # ran begin_handoff for this boundary, and the work hop's own inbox self-chain is
        # what queues the hop after it. Queueing here instead would leave a live entry
        # behind a hop that produced nothing, and the engine would re-spawn it forever
        # rather than stopping on no_output_exhausted.
        _spawn_auto_resume(group_id, {**bundle, "last_stage": WORK_HOP_KIND})
        return True

    if stage in (REVIEW_HOP_KIND, REWORK_HOP_KIND):
        # last_stage / rounds_before / revision_before live ONLY in the memory queue, never
        # in the paused row (L0008 §2.9): a cold start after a restart must reach the DB
        # derivation path, where the absence of these is exactly the right answer.
        queued = {**bundle, "last_stage": stage}
        if stage == REVIEW_HOP_KIND:
            queued["rounds_before"] = int(gate.get("rounds_used") or 0)
        else:
            queued["revision_before"] = int((slot or {}).get("revision_no") or 0)
        _queue_gate_bundle(group_id, queued)
        try:
            if stage == REVIEW_HOP_KIND:
                _spawn_review_hop(group_id, queued, gate)
            else:
                _spawn_rework_hop(group_id, queued, gate)
        except Exception:
            clear_auto_resume(group_id)     # take the intent back out; the caller parks it
            raise
        return True

    return False

def active_review_selection(group_id: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    """This group's live [검수] selection, for the inbox boundary (L0008 §2.8).

    The maps ride the RUN, not the token, so the inbox — which only ever sees a token —
    has to ask the engine. (None, None) when no engine run is driving this group, which is
    also the correct answer: a copy-mention chain has nothing to launch a review hop with.
    """
    run = _active_run_for_group(group_id)
    if run is None:
        return None, None
    return (
        run.get("continuation_review_count_overrides"),
        run.get("continuation_reviewer_overrides"),
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
        docs_target = _continuation_docs_target(
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

def _resumable_reviewer_overrides(
    project_id: str, reviewer_overrides: Optional[dict]
) -> Optional[dict]:
    """Drop reviewers the project no longer has, keep the rest (P0007 [엣지] 재개).

    The counterpart of _resumable_base_provider, and the opposite of the fresh-request rule:
    a NEW request naming a disabled reviewer is a visible 422, because the person is still
    at the screen and can pick again. A resume has nobody to ask, so it degrades that ONE
    entry to the project default reviewer and says so in the log — the review itself is
    never dropped, only the pick.
    """
    if not reviewer_overrides:
        return None
    kept = {
        item_seq: provider_id
        for item_seq, provider_id in reviewer_overrides.items()
        if _provider_enabled(project_id, provider_id)
    }
    dropped = sorted(set(reviewer_overrides) - set(kept))
    if dropped:
        logger.warning(
            "paused chain reviewer(s) %s are no longer enabled — resuming with the project "
            "default reviewer for item_seq %s",
            sorted({reviewer_overrides[k] for k in dropped}), ", ".join(dropped),
        )
    return kept or None

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
        active = _active_run_for_group(group_id)
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
        preflight = _paused_row_resume_state(project_id, pre_row, include_target=True)
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
            next_seq = _next_incomplete_item_seq(row["doc_ref"])
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
        reviewer_overrides = _resumable_reviewer_overrides(
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
            gate = resolve_review_gate(gate_bundle)
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
            _queue_gate_bundle(group_id, queued)
            spawn = _spawn_review_hop if gate["stage"] == REVIEW_HOP_KIND else _spawn_rework_hop
            try:
                return spawn(group_id, queued, gate)
            except HTTPException as exc:
                clear_auto_resume(group_id)
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
                clear_auto_resume(group_id)
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
            return start_run(
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
        active = _active_run_for_group(group_id)
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
    if _active_run_for_group(row.get("group_id") or "") is not None:
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
            run for run in _runs.values()
            if run.get("issued_to") == user_id and run["status"] != "finished"
        ]
    runs = []
    for run in candidates:
        try:
            status = get_status(run["run_id"])
        except HTTPException:
            continue  # finished/expired between the snapshot and the status read
        status["doc_ref"] = run["doc_ref"]
        runs.append(status)

    # A restart empties `_runs`, but a finished document-review loop is still a card: the
    # durable run identifies its owner and the durable loop rebuilds its complete history.
    # Discover those rows here (the bootstrap path), not only when a caller already knows a
    # run_id. Memory wins during the small finalize overlap and one bad stored row cannot
    # blank every other card.
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    live_ids = {run["run_id"] for run in candidates}
    try:
        stored_loop_rows = db_runs.list_review_loops_by_user(user_id)
    except Exception:
        logger.warning("stored review-loop list failed for %s", user_id, exc_info=True)
        stored_loop_rows = []
    for row in stored_loop_rows:
        if row["run_id"] in live_ids:
            continue
        try:
            restored = _run_detail_from_row(row)
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
        resume_state = _paused_row_resume_state(row["group_id"].split(".")[0], row)
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
            "pending_q_doc_ids": _open_q_doc_ids(row["group_id"]),
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

# Document-scoped review loop (0417 L0009). Kept parallel to resolve_review_gate:
# the continuous workflow gate has deliberately not been changed.
def compute_review_baseline(doc_id: str) -> dict:
    from modules.flow_gate.db import document_reviews as db_reviews
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise _http_error(404, "document_not_found", "Document disappeared before review-loop start.")
    reviews = db_reviews.list_by_doc(doc_id) or []
    latest_id = max((int(item.get("id") or 0) for item in reviews), default=0)
    latest = max(reviews, key=lambda item: int(item.get("id") or 0), default={})
    return {"review_baseline_id": latest_id, "starts_with_rework": latest.get("verdict") == "issues" and not latest.get("responded_at"), "baseline_revision_no": int(doc.get("revision_no") or 0)}

def resolve_loop_provider(bundle: dict, stage: str) -> str:
    if stage == REVIEW_HOP_KIND:
        return bundle["reviewer_provider_id"]
    if stage == REWORK_HOP_KIND:
        return bundle["rework_provider_id"]
    raise ValueError(f"unknown document review-loop stage: {stage}")

def check_expected_progress(bundle: dict, doc: dict, reviews: list[dict]) -> bool:
    """Verify the just-finished hop, never progress left by an earlier round."""
    kind = bundle.get("last_hop_kind")
    baseline = int(bundle.get("review_baseline_id") or 0)
    current = [
        review for review in reviews
        if int(review.get("id") or 0) > baseline
    ]
    if kind == REVIEW_HOP_KIND:
        # round_no is the 1-based review ordinal for this run.  Requiring that
        # many post-baseline rows prevents round N from reusing round N-1's verdict.
        return len(current) >= max(1, int(bundle.get("round_no") or 1))
    if kind == REWORK_HOP_KIND:
        latest = max(current, key=lambda review: int(review.get("id") or 0), default={})
        expected_revision = int(
            latest.get("revision_no") or bundle.get("baseline_revision_no") or 0
        )
        return int(doc.get("revision_no") or 0) > expected_revision
    return True

def resolve_document_review_loop_gate(bundle: dict) -> dict:
    """Return the next persisted stage, or a terminal stopped state (L0009 §2/§4)."""
    now = bundle.get("now")
    reviews = list(bundle.get("reviews") or [])
    baseline = int(bundle.get("review_baseline_id") or 0)
    current = [r for r in reviews if int(r.get("id") or 0) > baseline]
    latest = max(current, key=lambda r: int(r.get("id") or 0), default={})
    common = {"round_no": max(1, int(bundle.get("round_no") or 1)), "stop_reason": None, "stop_detail": None}
    if latest.get("verdict") == "pass":
        return {**common, "current_stage": "stopped", "stop_reason": "review_passed"}
    if bundle.get("document_missing"):
        return {**common, "current_stage": "stopped", "stop_reason": "retry_exhausted", "stop_detail": "target document no longer exists"}
    if bundle.get("last_hop_outcome") == "failed" or bundle.get("history_lookup_failed") or bundle.get("transition_failed"):
        used = int(bundle.get("attempts_used") or 0)
        maximum = int(bundle.get("failure_restart_max_attempts") or 0)
        if maximum == -1 or used <= maximum:
            return {**common, "current_stage": bundle.get("current_stage") or bundle.get("last_hop_kind") or REVIEW_HOP_KIND, "attempts_used": used}
        return {**common, "current_stage": "stopped", "stop_reason": "retry_exhausted", "stop_detail": bundle.get("failure_detail") or "stage retry budget exhausted", "attempts_used": used}
    if now is not None and bundle.get("deadline_at") is not None and now >= bundle["deadline_at"]:
        return {**common, "current_stage": "stopped", "stop_reason": "total_timeout", "stop_detail": "document review loop deadline reached"}
    rounds_used = len(current)
    if rounds_used == 0:
        stage = REWORK_HOP_KIND if bundle.get("starts_with_rework") else REVIEW_HOP_KIND
        return {**common, "current_stage": stage, "attempts_used": 0}
    limit = resolve_round_limit(int(bundle["review_count"]))
    doc = bundle.get("doc") or {}
    # Every non-pass review is first recorded as a real rejection and receives its rework
    # hop, including the final finite round. Only after that rework lands may the review
    # budget stop the loop; otherwise the last findings would never be addressed.
    if (
        bundle.get("last_hop_kind") == REWORK_HOP_KIND
        and bundle.get("last_hop_outcome") == "succeeded"
        and int(doc.get("revision_no") or 0) > int(latest.get("revision_no") or bundle.get("baseline_revision_no") or 0)
    ):
        if limit != REVIEW_ROUNDS_NO_LIMIT and rounds_used >= limit:
            return {**common, "current_stage": "stopped", "stop_reason": "review_count_exhausted", "stop_detail": f"review count {limit} exhausted"}
        return {**common, "current_stage": REVIEW_HOP_KIND, "attempts_used": 0}
    if latest.get("verdict") in (REVIEW_VERDICTS - {"pass"}):
        return {**common, "current_stage": REWORK_HOP_KIND, "round_no": rounds_used + 1, "attempts_used": 0}
    if int(doc.get("revision_no") or 0) > int(latest.get("revision_no") or bundle.get("baseline_revision_no") or 0):
        return {**common, "current_stage": REVIEW_HOP_KIND, "round_no": rounds_used + 1, "attempts_used": 0}
    return {**common, "current_stage": bundle.get("current_stage") or REVIEW_HOP_KIND, "attempts_used": int(bundle.get("attempts_used") or 0)}

def _loop_deadline(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None

def _insert_document_review_loop(run: dict) -> None:
    loop = run.get("document_review_loop")
    if not loop:
        return
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    db_loops.insert({
        **loop,
        "run_id": run["run_id"],
        "group_id": run["group_id"],
        "doc_ref": run["doc_ref"],
    })

def _restore_document_review_loop(run_id: str) -> dict | None:
    """The durable loop row for a run this process no longer holds, or None.

    Best-effort, like every other durable read `_run_detail_from_row` depends on
    (`_paused_row_stop_reason` is the same shape): a run that never carried a
    review loop, and a store this call cannot reach, both answer None. Raising
    here would turn a plain detail lookup for an ORDINARY run into a 500 just
    because the loop table could not be read.
    """
    try:
        from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

        return db_loops.get(run_id)
    except Exception:  # noqa: BLE001 — a card is an aid, not the lookup
        logger.warning("document review-loop restore failed for %s", run_id, exc_info=True)
        return None

def _checkpoint_document_review_loop(run: dict) -> dict | None:
    """Atomically reject a non-pass review and reserve the durable successor stage."""
    with get_store().transaction():
        return _checkpoint_document_review_loop_tx(run)

def _checkpoint_document_review_loop_tx(run: dict) -> dict | None:
    """Transaction body for one completed document-review-loop hop."""
    loop = run.get("document_review_loop")
    if not loop or loop.get("current_stage") == "stopped":
        return loop
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews

    persisted = db_loops.get(run["run_id"])
    if persisted is None:
        raise RuntimeError(f"missing document review loop row for {run['run_id']}")
    doc = db_docs.get_by_id(persisted["doc_ref"])
    reviews = db_reviews.list_by_doc(persisted["doc_ref"]) or []
    stage = persisted["current_stage"]
    succeeded = run.get("outcome") == "complete"
    attempts = int(persisted.get("attempts_used") or 0) + 1
    bundle = {
        **persisted,
        "doc": doc or {},
        "reviews": reviews,
        "document_missing": doc is None,
        "last_hop_kind": stage,
        "last_hop_outcome": "succeeded" if succeeded else "failed",
        "attempts_used": attempts,
        "failure_detail": run.get("last_message") or run.get("end_reason"),
        "now": datetime.now(timezone.utc),
        "deadline_at": _loop_deadline(persisted.get("deadline_at")),
    }
    if succeeded and not check_expected_progress(bundle, doc or {}, reviews):
        bundle["last_hop_outcome"] = "failed"
        bundle["failure_detail"] = f"{stage} hop produced no expected durable progress"

    # A successful review with a new non-pass verdict must become a real document
    # rejection before the rework stage is made visible. Both writes share the outer
    # transaction, so a checkpoint failure rolls the rejection back as well.
    current_reviews = [
        item for item in reviews
        if int(item.get("id") or 0) > int(persisted.get("review_baseline_id") or 0)
    ]
    latest_review = max(
        current_reviews, key=lambda item: int(item.get("id") or 0), default=None
    )
    if (
        stage == REVIEW_HOP_KIND
        and bundle["last_hop_outcome"] == "succeeded"
        and latest_review is not None
        and (latest_review.get("verdict") or "").lower() in (REVIEW_VERDICTS - {"pass"})
        and (doc or {}).get("doc_review_status") != "rejected"
    ):
        slot = {
            "doc_id": persisted["doc_ref"],
            "revision_no": int((doc or {}).get("revision_no") or 0),
            "review_status": (doc or {}).get("doc_review_status") or "",
        }
        rejection = _auto_reject(slot, latest_review, {
            "issued_to": run.get("issued_to"),
            "api_base_url": run.get("api_base_url"),
        })
        if not rejection.get("ok"):
            bundle["transition_failed"] = True
            bundle["last_hop_outcome"] = "failed"
            bundle["failure_detail"] = rejection.get("detail") or rejection.get("stop_code")
        else:
            doc = db_docs.get_by_id(persisted["doc_ref"])
            bundle["doc"] = doc or {}

    resolved = resolve_document_review_loop_gate(bundle)
    updates = {
        "round_no": resolved["round_no"],
        "current_stage": resolved["current_stage"],
        "stop_reason": resolved.get("stop_reason"),
        "stop_detail": resolved.get("stop_detail"),
        "last_hop_kind": stage,
        "last_hop_outcome": bundle["last_hop_outcome"],
        "attempts_used": int(resolved.get("attempts_used") or 0),
    }
    changed, latest = db_loops.checkpoint(
        run["run_id"],
        expected_round_no=int(persisted["round_no"]),
        expected_stage=stage,
        expected_updated_at=persisted["updated_at"],
        **updates,
    )
    run["document_review_loop"] = latest
    return latest

def _loop_finding_count(review: dict) -> int | None:
    """How many findings that review row carried, or None when it cannot be read."""
    raw = review.get("findings")
    if isinstance(raw, list):
        return len(raw)
    if not raw:
        return 0
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return len(parsed) if isinstance(parsed, list) else None

def _loop_rework_ledger(doc: dict, baseline_revision_no: int, started_at) -> list[dict]:
    """Rejections THIS run answered, oldest first.

    documents.rejection_history is the durable ledger the rework hop writes into
    (pipeline_service.record_rejection_response), so an answered entry whose response
    landed past the run's baseline revision is proof that one rework round finished —
    and it stays proof after a restart.
    """
    raw = doc.get("rejection_history")
    try:
        history = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, ValueError):
        return []
    if not isinstance(history, list):
        return []
    answered = []
    for entry in history:
        if not isinstance(entry, dict) or not entry.get("responded_at"):
            continue
        revision = entry.get("response_revision_no")
        if revision is not None:
            if int(revision) > baseline_revision_no:
                answered.append(entry)
        elif started_at and str(entry["responded_at"]) >= str(started_at):
            answered.append(entry)
    return answered

def build_document_review_loop_history(loop: dict, doc_ref: str | None = None) -> list[dict]:
    """Rebuild this run's round table from canonical rows (deck u3digra2 v6 screen 6).

    0417 T0013 item 8 wants screen 6's accumulated round table, and item 7 wants the
    same card back after bootstrap/reconnect. Rows may therefore never come from state
    transitions one browser happened to watch: a refresh, a second tab or a dropped
    poll/SSE event would each produce a different table. Every row here is rebuilt from
    the same durable rows resolve_document_review_loop_gate judges the loop with, so the
    table and the judgment cannot disagree:

      * document_reviews rows newer than review_baseline_id -- this run's review rounds,
        carrying the server-counted finding count screen 6 prints beside the stage name
        (T0010 item 4: no new Korean literals in server modules, so the words themselves
        live in the client i18n bundles);
      * answered documents.rejection_history entries past baseline_revision_no -- its
        rework rounds, with document_revisions as the backstop for an edit that recorded
        no response text.

    Both are re-read on every start/status/finish payload, so a client that missed an
    event still gets the whole table on the next one.
    """
    doc_id = str(loop.get("doc_ref") or doc_ref or "")
    if not doc_id:
        return []
    baseline_review_id = int(loop.get("review_baseline_id") or 0)
    baseline_revision_no = int(loop.get("baseline_revision_no") or 0)
    try:
        reviews = sorted(
            (row for row in (db_reviews.list_by_doc(doc_id) or [])
             if int(row.get("id") or 0) > baseline_review_id),
            key=lambda row: int(row.get("id") or 0),
        )
        reworks = _loop_rework_ledger(
            db_docs.get_by_id(doc_id) or {}, baseline_revision_no, loop.get("started_at")
        )
    except Exception as exc:  # noqa: BLE001 - a card must never break a status response
        import LogAssist.log as logger
        logger.warning(f"[ai-invoke] review-loop history rebuild failed (ignored): {exc}")
        return []
    try:
        # One row per revision the document LEFT since the run started (the backup row
        # carries the revision it backed up), so a rework that landed without response
        # text still gets its line instead of silently vanishing from the table. Read
        # separately: this backstop must never cost us the ledger above.
        from modules.flow_gate.db import document_revisions as db_revisions
        edits = sorted(
            (row for row in (db_revisions.list_by_doc(doc_id) or [])
             if int(row.get("revision_no") or 0) >= baseline_revision_no),
            key=lambda row: int(row.get("revision_no") or 0),
        )
    except Exception:  # noqa: BLE001 - backstop only; the ledger above already stands
        edits = []
    review_rows = [{
        "round_no": index,
        "stage": REVIEW_HOP_KIND,
        "result": "passed" if row.get("verdict") == "pass" else "issues",
        "verdict": row.get("verdict"),
        "finding_count": _loop_finding_count(row),
        "revision_no": int(row.get("revision_no") or 0),
        "at": row.get("reviewed_at") or row.get("created_at"),
    } for index, row in enumerate(reviews, start=1)]
    rework_rows = [{
        "round_no": index,
        "stage": REWORK_HOP_KIND,
        "result": "complete",
        "revision_no": entry.get("response_revision_no"),
        "rejection_id": entry.get("rejection_id"),
        "at": entry.get("responded_at"),
    } for index, entry in enumerate(reworks, start=1)]
    for index in range(len(rework_rows), len(edits)):
        edit = edits[index]
        rework_rows.append({
            "round_no": index + 1,
            "stage": REWORK_HOP_KIND,
            "result": "complete",
            "revision_no": int(edit.get("revision_no") or 0) + 1,
            "rejection_id": None,
            "at": edit.get("created_at"),
        })
    # Screen 6 reads the stages in the order the loop runs them: a run that started on an
    # unanswered rejection opens with its rework, every other run opens with the review
    # whose findings the first rework answers.
    first, second = (
        (rework_rows, review_rows) if loop.get("starts_with_rework") else (review_rows, rework_rows)
    )
    ordered: list[dict] = []
    for index in range(max(len(first), len(second))):
        if index < len(first):
            ordered.append(first[index])
        if index < len(second):
            ordered.append(second[index])
    return ordered

def document_review_loop_payload(run: dict) -> dict | None:
    loop = run.get("document_review_loop")
    if not loop:
        return None
    payload = {key: loop.get(key) for key in ("round_no", "current_stage", "stop_reason", "stop_detail")}
    # 0417 T0013 items 7-8: the round table travels with EVERY start / status / finish
    # payload, rebuilt from canonical rows, so a card restored after F5, a reconnect or a
    # server restart shows the same rounds instead of only what this browser observed.
    payload["history"] = build_document_review_loop_history(loop, run.get("doc_ref"))
    return payload

# 0492 T0014: runtime list serialization shares the settings capability SSOT.
def _provider_brief(provider: Optional[dict]) -> Optional[dict]:
    if not provider:
        return None
    from modules.flow_gate.services.provider_capability_service import provider_capabilities
    return {
        "id": provider.get("id"), "name": provider.get("name"),
        "exec_type": provider.get("exec_type"), "kind": provider.get("kind"),
        "capabilities": provider_capabilities(provider),
    }
