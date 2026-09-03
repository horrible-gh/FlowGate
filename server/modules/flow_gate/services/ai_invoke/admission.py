"""Run admission (0501 NR0003 §12/§14 `admission.py`).

One question: **may this AI run start, and with what?** Provider resolution and
capability/pin checks, the group worktree guard, the group AI lease (acquire at start,
orphan reclamation at boot, manual force-release), token issue, the run object's
initialization, and `start_run` itself -- plus the per-hop policy `start_run` needs to
answer that question for a continuation hop: which provider this hop gets, which
override/note applies to it, how long it may run, how many restarts it may spend.

`start_run` knew all of this before too; NR0003 §14's point is that it knew it *mixed
into* the registry, the oracles and the worker. Those are now separate modules this one
calls, so the file reads as one policy instead of a service prologue.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import HTTPException
from modules.flow_gate import template_provision
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service
from modules.flow_gate.services import invoke_mention_service
from modules.flow_gate.services import token_service
from modules.flow_gate.settings import ai_execution_policy_service
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths

# Neither `chain` nor `worker` is imported as a sibling module here (both are also
# local variable names in this file): chain.py itself reaches admission, and finalize.py
# (which chain.py reaches) reaches worker, so an admission -> chain or admission ->
# worker import would close a cycle. The one function this module used to reach on each
# (`_provider_brief`, `_absolute_cap_sec`) now lives on their real owner -- `oracle`
# (a pure formatter, §16) and `runtime` (the parameter block, §13) -- neither of which
# admission's own callers create a path back through.
from . import oracle
from . import provider_api
from . import review
from .runtime import (
    CHAIN_MEMBER_HOP_KINDS,
    HOP_TIMEOUT_SEC,
    PROVIDER_UNAVAILABLE_CODE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    RESTART_MAX_ATTEMPTS_DEFAULT,
    REVIEW_HOP_KIND,
    REWORK_HOP_KIND,
    STEP_TIMEOUT_MAX_SEC,
    STEP_TIMEOUT_MIN_SEC,
    WORK_HOP_KIND,
    _absolute_cap_sec,
    _http_error,
    _note_issued_prompt,
    _note_issued_raw_token,
    _runs_lock,
    _svc,
    logger,
    prompt_digest,
)


# T0004 work item 6 / NR0003 finding 6: the worktree_unavailable 409 always went out in
# Korean with no locale branch. It reuses the same locale-dictionary pattern as
# remote_tool_service._ERROR_MESSAGES / _CUSTOM_ERROR_MESSAGES.
_WORKTREE_UNAVAILABLE_COPY = {
    "ko": (
        "이 그룹의 작업 폴더(워크트리)를 확인할 수 없어 AI 실행을 시작하지 않습니다 "
        "(원인: {cause}). 워크트리 없이 실행하면 작업이 원본 체크아웃(main)에 "
        "남습니다. 그룹 Git 상태를 복구한 뒤 다시 실행하십시오."
    ),
    "en": (
        "AI execution was not started because this group's working folder (worktree) "
        "could not be confirmed (cause: {cause}). Running without a worktree would leave "
        "the work in the original checkout (main). Recover the group's Git state and "
        "retry."
    ),
    "ja": (
        "このグループの作業フォルダ(worktree)を確認できないため、AI実行を開始しません"
        "(原因: {cause})。worktreeなしで実行すると、作業が元のチェックアウト(main)に"
        "残ります。グループのGit状態を復旧してから再実行してください。"
    ),
}

# Found while reviewing T0004 (flagged by the generalised static guard): the
# run_id_collision 409 using the same _http_error helper was un-branched Korean too.
# It reuses the continuation_locale already received in the same function (start_run).
_RUN_ID_COLLISION_COPY = {
    "ko": "실행 번호 발급이 충돌했습니다. 다시 시도해 주세요.",
    "en": "Run-id issuance collided. Please try again.",
    "ja": "実行番号の発行が競合しました。もう一度お試しください。",
}


def _is_group_worktree(project_id: str, group_id: str, root: Optional[Path]) -> bool:
    """Is *root* the group's OWN worktree, as opposed to the base project tree?

    ``resolve_project_src_root`` is fallback-first by design: when the worktree is
    missing it silently hands back the ordinary project-branch folder (main). The
    return value alone therefore cannot answer "did we get the worktree?", so this
    compares it against the path the group's ledger branch would occupy.
    """
    if root is None:
        return False
    try:
        state = db_git.get_state(group_id) or {}
        branch = (state.get("branch") or "").strip()
        project_name = git_service._project_name(project_id)
        if not branch or not project_name:
            return False
        expected = git_service.src_root(project_name, branch)
        return root.resolve() == expected.resolve()
    except Exception:  # noqa: BLE001 — an unanswerable comparison is a "no"
        return False


def _require_group_worktree(
    project_id: str, module: str, group_id: str, branch: str, locale: Optional[str] = None,
) -> None:
    """Refuse to launch a run that would execute in the base tree (0299 R0001).

    This is the root cause R0001 describes: AI workers sometimes work on the main branch
    instead of the one assigned to them. The remote CRUD endpoints have been gated since
    0205 (remote_tool_service._resolve_root_for_mutation), but the invoked worker's
    *cwd* was not — it came from the fallback-first resolver, so a group whose
    worktree was missing got a CLI agent pointed straight at the base checkout, free
    to edit files there with its own tools. The TR work-scope check (0299 D0004) catches
    afterwards, at report time; this closes it at the front, before any work happens.

    Same shape as the remote-write gate on purpose: one synchronous ensure_worktree
    self-heal, then a 409 carrying the blocking cause. Non-integrated projects and
    group-less runs are untouched — they have no worktree to demand.
    """
    if not group_id or not project_id:
        return
    try:
        cfg = db_git.get_config(project_id)
    except Exception:  # noqa: BLE001 — a config lookup failure must not block a run
        return
    if cfg is None or not cfg.get("enabled"):
        return  # non-integrated project: the base tree IS the source of truth

    root = storage_paths.resolve_project_src_root(project_id, branch, group_id=group_id)
    if _svc()._is_group_worktree(project_id, group_id, root):
        return
    try:
        if git_service.ensure_worktree(
            project_id, module or "default", group_id, trigger="ai_invoke_retry"
        ) == "ok":
            root = storage_paths.resolve_project_src_root(project_id, branch, group_id=group_id)
            if _svc()._is_group_worktree(project_id, group_id, root):
                return
    except Exception:  # noqa: BLE001 — ensure_worktree never raises, but be certain
        logger.warning("ensure_worktree retry failed for group %s", group_id, exc_info=True)

    # Still not there. Report WHY — "worktree unavailable" with no cause is what makes
    # this class of incident unfixable after the fact (0280 NR0003 §4-B).
    try:
        state = db_git.get_state(group_id) or {}
        provision_error = state.get("provision_error")
        session = git_service.open_merge_session_of_project(project_id)
    except Exception:  # noqa: BLE001
        provision_error, session = None, None
    if session is not None:
        cause = "merge_conflict_open"
    elif provision_error:
        cause = "provision_failed"
    else:
        cause = "worktree_missing"
    logger.warning(
        "ai_invoke blocked for group %s — no group worktree (cause=%s, resolved=%s)",
        group_id, cause, root,
    )
    normalized_locale = template_provision.normalize_locale(locale)
    raise _http_error(
        409, "worktree_unavailable",
        _WORKTREE_UNAVAILABLE_COPY[normalized_locale].format(cause=cause),
        group_id=group_id, cause=cause, provision_error=provision_error,
    )


def _record_orphaned_lease_run(lease_row: dict, end_reason: str) -> None:
    """Give a dead lease's run a durable end record, if it doesn't already have one
    (0401 NR0003 / T0004 items 1-2). A lease row alone (group/run/token/timestamps) has
    no doc_ref or mode — both live on the token it was issued with — so this looks
    the token up. Best-effort: a run this cannot explain still gets its lease
    cleared by the caller either way, it just won't carry the extra explanation.
    """
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    run_id = str(lease_row.get("run_id") or "")
    if not run_id or db_runs.get(run_id) is not None:
        return
    doc_ref, mode = "", "single"
    token_id = lease_row.get("token_id")
    if token_id:
        token = db_tokens.get_by_id(token_id)
        if token:
            doc_ref = token.get("doc_ref") or ""
            mode = "continuous" if token.get("continuation_target_seq") is not None else "single"
    stamp = now_iso()
    started = lease_row.get("acquired_at") or stamp
    db_runs.upsert({
        "run_id": run_id,
        "group_id": lease_row["group_id"],
        "project_id": lease_row["project_id"],
        "doc_ref": doc_ref,
        "mode": mode,
        "outcome": "none",
        "end_reason": end_reason,
        "resumable": False,
        "started_at": started,
        "finished_at": stamp,
        "created_at": started,
        "updated_at": stamp,
    })


def _reclaim_orphan_lease_token(lease_row: dict, reason: str) -> None:
    """Best-effort revoke of a dead lease's still-active token (0447 T0007).

    A lease alone only blocks re-entry into the group; the `token_id` it was issued
    with is a separate credential that otherwise survives a restart until its own
    TTL, letting a human replay it back into the same group. No-op when the lease
    carries no token_id, the token cannot be found, or the token is already
    consumed or revoked -- a consumed single-use token must never be flipped to
    revoked after the fact (that would rewrite a settled audit trail for a token
    nobody can replay anyway), and an already-revoked token must not draw a second
    `token_revoked` event. Whether an end record exists or was written for this
    victim's run plays no part in this decision; only the token's own
    consumed/revoked state does. Goes through token_service.revoke() (never
    db/tokens.py directly) so the existing workflow_events.token_revoked audit
    contract is preserved, and that call is itself idempotent under a race --
    including a race against a sibling process, decided by the atomic claim
    marker in db_tokens.revoke() rather than any in-process lock (0447 T0007
    review rev1).
    """
    token_id = lease_row.get("token_id")
    if not token_id:
        return
    token = db_tokens.get_by_id(token_id)
    if token is None:
        return
    if token.get("consumed_at") or token.get("revoked_at"):
        return
    token_service.revoke(token_id, reason=reason)


def startup_recover_leases() -> int:
    """Reclaim AI-run leases orphaned by a server restart (0401 NR0003 / T0004 item 1).

    Called once from ``server/startup.py``, before the app accepts traffic. Every
    lease still on the table at that instant is dead: this process's own ``_runs``
    registry starts empty, so nothing it has admitted yet could hold one. Bound to
    the process's own start time so a multi-process deployment cannot reclaim a
    lease a sibling process is mid-admission on.
    """
    before = now_iso()
    victims = db_group_ai_leases.reclaim_orphaned(before)
    for row in victims:
        try:
            _svc()._record_orphaned_lease_run(row, "orphaned_by_restart")
        except Exception:
            logger.warning(
                "orphaned-lease end record failed for run %s", row.get("run_id"), exc_info=True
            )
        # Independent of the end-record write above (0447 T0007 item 2): a victim
        # whose run already has a normal end record -- e.g. the process died after
        # persisting ai_invoke_runs but before releasing the lease -- still carries
        # an active orphan token that must be reclaimed here.
        try:
            _reclaim_orphan_lease_token(row, "orphaned_by_restart")
        except Exception:
            logger.warning(
                "orphaned-lease token revoke failed for run %s token %s",
                row.get("run_id"), row.get("token_id"), exc_info=True,
            )
    if victims:
        logger.warning("[ai_invoke] startup reclaimed %d orphaned group lease(s)", len(victims))
    # 0406 T0022 work item 4: the same startup also recovers hop handoffs that were cut
    # off. It pairs with lease reclaim: one recovers the lock, the other the intent.
    _svc().startup_recover_handoffs()
    return len(victims)


def force_release_group_lease(group_id: str) -> dict:
    """Manually release a group's lease from the blocked screen (0401 T0004 item 2).

    Refuses (and leaves the lease untouched) when the lease's run is still live —
    the same :func:`is_run_live` gate everything else uses, so this can never cut
    off a run that is actually working. Only a lease whose run this process cannot
    find, or has already finished, is orphaned and eligible.
    """
    lease = db_group_ai_leases.get(group_id)
    if lease is None:
        raise _http_error(404, "lease_not_found", "No AI run lease is held for this group.",
                          group_id=group_id)
    run_id = str(lease.get("run_id") or "")
    if _svc().is_run_live(run_id):
        raise _http_error(409, "run_still_live",
                          "This group's AI run is still active; it cannot be force-released.",
                          group_id=group_id, run_id=run_id)
    released = db_group_ai_leases.release(group_id, run_id)
    if released:
        try:
            _svc()._record_orphaned_lease_run(lease, "orphaned_by_manual_release")
        except Exception:
            logger.warning("orphaned-lease end record failed for run %s", run_id, exc_info=True)
    return {"ok": True, "group_id": group_id, "run_id": run_id, "released": bool(released)}


def _continuation_docs_target(
    doc_ref: str,
    target_item_seq: Optional[int],
    *,
    pending_only: bool = True,
    continuation_instruction_mode: Optional[str] = None,
    # 0352 T0004 §3.5: only the N/T item_seqs the ai_direct chain selected for server
    # auto-handling are excluded from the worker's document count — an unselected ai_direct
    # N/T is still a real worker document and must stay counted.
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[int]:
    """docs_target in the workflow item_seq coordinate system (0226 B0001 / NR0003 §5-1).

    ``continuation_target_seq`` lives in the workflow-sequence item_seq space, which is
    unrelated to the group document seq space (item_seq turns sparse after
    edit_workflow_pending renumbers the pending tail past max_item_seq). The former
    ``target - get_group_max_seq()`` subtraction mixed the two spaces, yielding
    arbitrary targets (the reported 0/9 and 4/3). Count instead the sequence items up
    to the target that will land as worker-visible documents. In ``auto_approved``, N/T
    instruction heads are server-created drafts and remain excluded; in ``ai_direct``
    they are independent worker documents and are counted UNLESS the chain selected that
    exact item_seq for server auto-handling (0353 B0001 / NR0003 §8; 0352 T0004 §2).

    ``pending_only=True`` counts only unrealized slots (start-of-run admission).
    The to-end resolution paths pass False: the whole freshly-decided sequence is the
    run's scope regardless of what has been realized by the time of the query.
    ``target_item_seq=None`` means "no upper bound" (to-end).
    Returns None when the doc has no decided workflow sequence.
    """
    from modules.flow_gate.services.workflow_decision_service import is_auto_handled_step

    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is None:
        return None
    count = 0
    for item in db_wfseq.get_sequence_items(seq["id"]) or []:
        item_seq = item.get("item_seq")
        if (
            target_item_seq is not None
            and item_seq is not None
            and int(item_seq) > int(target_item_seq)
        ):
            continue
        if pending_only and item.get("result_doc_id") is not None:
            continue
        if is_auto_handled_step(
            head_type=item.get("type"),
            item_seq=item_seq,
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        ):
            continue
        count += 1
    return count


# ── Start (L0006 §2.1) ───────────────────────────────────────────────────────

def list_runtime_providers(project_id: str) -> dict:
    """Safe effective-provider view for ordinary document readers."""
    effective = ai_settings_service.resolve_effective(project_id)
    return {
        "ok": True,
        "project": project_id,
        "providers": [oracle._provider_brief(provider) for provider in effective.get("providers") or []],
        "default_provider_id": effective.get("default_provider_id"),
        # flowgate.default.0490 T0005 §3.5: the only metadata endpoint every execution
        # dialog already calls, so this is the SSOT for the client-side max/min instead of
        # a screen fetching /system/settings (system.settings.manage-gated, out of reach
        # for an ordinary document reader).
        "execution_policy": ai_execution_policy_service.execution_policy_payload(),
    }


def resolve_pinned_provider_name(project_id: str, provider_id: Optional[str]) -> Optional[str]:
    """The provider name a mention may claim, or None when it must not claim one.

    0293 NR0004 finding 5: the worker mention is built BEFORE the run picks a provider, and
    `_worker` may fall through the whole chain. Naming chain[0] in the mention would
    therefore be a guess that reads like a server-confirmed fact. A name is only
    returned when the effective chain collapses to exactly ONE provider — an explicit
    UI pin (start_run's `chain = [selected]`), or a project with a single enabled
    provider — because only then is fallback structurally impossible.

    Finding 4: the value is the provider's display NAME, not a model id. `api_model` exists
    for exec_type='api' only; a CLI provider's model is buried in cli_command flags that
    differ per kind, so there is no model string the server reliably knows. The name is
    user-authored, unique per scope, and present for both exec types.

    Never raises: an unusable answer here must not fail the run (start_run validates the
    pin for real, and a missing name only costs a badge)."""
    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001
        return None
    if provider_id:
        selected = next((p for p in chain if p.get("id") == provider_id), None)
        return (selected or {}).get("name") or None
    if len(chain) == 1:
        return chain[0].get("name") or None
    return None


def start_run(
    *,
    project_id: str,
    module: Optional[str],
    group_id: str,
    doc_ref: str,
    action_scope: str,
    mode: str,
    continuation_target_seq: Optional[int],
    continuation_review_mode: bool,
    continuation_instruction_mode: Optional[str],
    continuation_locale: Optional[str],
    issued_to: str,
    api_base_url: str,
    mention_builder: Callable[[str, str], Optional[str]],
    provider_id: Optional[str] = None,
    provider_pinned: Optional[bool] = None,
    issue_builder: Optional[Callable[[], dict]] = None,
    merge_id: Optional[int] = None,
    completion_oracle: Optional[Callable[[], bool]] = None,
    # 0317 T0010 rev4: item_seq (as str, JSON-body keys) -> provider_id, chosen in
    # ContinuousWorkDialog's per-step override table. Session-scoped — this run's start
    # request is the only place it lives; never persisted (T0010 Q&A: session-scoped o1).
    continuation_provider_overrides: Optional[dict] = None,
    # 0346 T0005: handoff-note tab values — a common note for every hop and/or item_seq -> note
    # overrides for individual hops. Never persisted (D0004 §4: session-scoped, like the
    # provider overrides above).
    continuation_default_note: Optional[str] = None,
    continuation_note_overrides: Optional[dict] = None,
    # 0357 T0004: an unmanned continuous chain is made of a fresh run per hop.
    # These internal handoff values keep display progress at chain lifetime while
    # docs_target/docs_reached retain their existing per-run judging semantics.
    chain_id: Optional[str] = None,
    chain_docs_target: Optional[int] = None,
    chain_docs_reached: Optional[int] = None,
    # 0352 T0004 §3.5: the ai_direct chain's per-item_seq N/T auto-approve selection. Rides
    # the run (session-scoped, like the provider/note overrides above) so every hop's
    # provider resolution / docs_target counting / worker item_seq folding agrees with the
    # SAME selection the user made once at the start of the chain.
    continuation_auto_approve_item_seqs: Optional[list] = None,
    # flowgate.default.0400 M0005 + 0446 T0010 §3-1: THIS RUN's wall-clock budget in seconds,
    # picked either in ContinuousWorkDialog's duration section (a continuous hop) or in
    # AiInvokeDialog's time section (a single rejection rework). The `continuation_` prefix is
    # a misnomer kept on purpose — renaming it would move a route field, this argument, an
    # ai_invoke_paused_chains column and a client prop at once, which T0010 §3-1 rules out of
    # scope. Read it as "the run's budget pick", not as "continuous only".
    # Session-scoped like the provider/note overrides above — never persisted outside the
    # paused-chain row a continuous pause snapshots it into. None (or a value outside
    # STEP_TIMEOUT_MIN_SEC..STEP_TIMEOUT_MAX_SEC) falls back to the mode's own default:
    # HOP_TIMEOUT_SEC for continuous, the per-document formula for single.
    continuation_step_timeout_sec: Optional[int] = None,
    # flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick — how many times
    # a no-output hop retries on the SAME step-assigned provider (never a different one).
    # Session-scoped like the fields above; None or an unrecognized value falls back to
    # RESTART_MAX_ATTEMPTS_DEFAULT.
    continuation_restart_max_attempts: Optional[int] = None,
    # 0414 P0007: the [검수] tab's two session-scoped maps — mode-aware worker item_seq ->
    # review count, and -> reviewer provider_id. They ride the RUN (never a token), exactly
    # like the provider/note maps, and are snapshotted into the paused row so a resume or a
    # hop handoff keeps reviewing with the same selection (DB0009).
    continuation_review_count_overrides: Optional[dict] = None,
    continuation_reviewer_overrides: Optional[dict] = None,
    document_review_loop: Optional[dict] = None,
    # A single-request acknowledgement. It is intentionally never persisted or forwarded.
    capability_warning_ack: Optional[bool] = None,
    # 0414 L0008 §5: work / review / rework. A review or rework hop makes no document, so
    # the chain counters do not move for it — this is what lets a card say WHAT is running
    # instead of reporting a frozen progress number.
    hop_kind: str = WORK_HOP_KIND,
) -> dict:
    """Admit and launch a run. mention_builder(raw_token, scratch_dir) builds the
    worker mention through the exact token_routes path so the prompt the AI reads
    is byte-identical to the copy-mention flow (the raw token never leaves the
    server — it is consumed only as the run's FLOWGATE_TOKEN env).

    completion_oracle (0248 B0001): a caller-supplied "did the work land?" predicate for
    runs whose result is NOT a new document, so the document-reach oracle cannot see it.
    The Q&A [Request AI answer] run writes an answer row onto an existing document — under
    the document oracle it would settle as outcome='none' (docs_reached 0 < docs_target 1)
    no matter how well the worker did. Supplying an oracle switches the run to that scoped
    judge and pins docs_target to 0, mirroring the resolve_conflict branch below.

    0259 B0001: that opt-in is now only an OVERRIDE. A scope that produces no document gets
    its default judge from `_SCOPE_PROBES` here in the engine, so forgetting to pass one no
    longer silently falls back to the unreachable document oracle."""
    from modules.flow_gate.services.workflow_decision_service import (
        normalize_continuation_auto_approve_item_seqs,
        normalize_continuation_instruction_mode,
    )

    requested_continuation_instruction_mode = continuation_instruction_mode
    # 0417 D0007/P0008: stage selection precedes provider-chain selection. In particular,
    # an unaddressed rejection starts with the fixed rework provider, never the reviewer.
    if document_review_loop is not None:
        document_review_loop = dict(document_review_loop)
        document_review_loop.update(_svc().compute_review_baseline(doc_ref))
        initial_stage = (
            REWORK_HOP_KIND if document_review_loop["starts_with_rework"] else REVIEW_HOP_KIND
        )
        provider_id = review.resolve_loop_provider(document_review_loop, initial_stage)
        provider_pinned = True
    continuation_instruction_mode = normalize_continuation_instruction_mode(
        requested_continuation_instruction_mode
    )
    continuation_auto_approve_item_seqs = normalize_continuation_auto_approve_item_seqs(
        continuation_auto_approve_item_seqs
    )
    effective = ai_settings_service.resolve_effective(project_id)
    chain = effective.get("providers") or []
    chain_source = effective.get("source")
    selected_provider_source = (
        "review_loop" if document_review_loop is not None else "project_default"
    )
    # A per-step override names this exact hop and remains the highest tier. 0435 T0004
    # deliberately removes every startup fallback tail from an explicit choice: a provider
    # that cannot start fails visibly instead of silently switching to a more expensive one.
    step_override_provider = None
    if mode == "continuous" and continuation_provider_overrides:
        step_override_provider = _svc()._resolve_continuation_hop_override(
            doc_ref,
            continuation_provider_overrides,
            chain,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )

    stored_provider_id = None
    stored_provider_name = None
    stored_provider_item_seq = None
    if (
        mode == "continuous"
        and not step_override_provider
        and not (provider_pinned and provider_id)
    ):
        stored_provider_id, stored_provider_name, stored_provider_item_seq = _svc().stored_hop_provider(
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
    stored_provider_active = bool(
        stored_provider_id
        and any(provider.get("id") == stored_provider_id for provider in chain)
    )

    if step_override_provider:
        selected = next(
            (provider for provider in chain if provider.get("id") == step_override_provider),
            None,
        )
        chain = [selected] if selected else []
        selected_provider_source = "step_override"
    elif mode == "continuous" and provider_pinned and provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected]
        selected_provider_source = "force_all"
    elif stored_provider_active:
        # An unpinned run still follows the persisted sequence assignment (D0006 §6.2).
        selected = next(
            (provider for provider in chain if provider.get("id") == stored_provider_id),
            None,
        )
        chain = [selected] if selected else []
        selected_provider_source = "stored_sequence"
    elif provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected]
        selected_provider_source = (
            "review_loop" if document_review_loop is not None else "request"
        )
    elif mode == "continuous":
        hop_provider = _svc()._resolve_continuation_hop_provider(
            project_id,
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if hop_provider:
            selected = next(
                (provider for provider in chain if provider.get("id") == hop_provider),
                None,
            )
            if selected is not None:
                chain = [selected]
                selected_provider_source = "document_type"

    if mode == "continuous" and stored_provider_id and not stored_provider_active and chain:
        logger.warning(
            "continuation hop provider fallback: %s not active for %s item_seq %s, "
            "falling back to %s",
            stored_provider_id,
            doc_ref,
            stored_provider_item_seq,
            chain[0].get("id"),
        )
    if not chain:
        # 0292 T0003: "no provider was ever registered" used to be indistinguishable
        # from "the registered ones are all switched off" — both read as
        # no_enabled_provider, and the operator of a fresh install was sent to a
        # settings screen to toggle rows that do not exist. An install that skipped
        # the provider seed is a normal path now, so it gets its own code and the
        # command that fixes it.
        # source == "disabled" is excluded: that project turned AI off on purpose, so
        # "nothing is registered" would be a misleading thing to tell its operator.
        if chain_source != "disabled" and not effective.get("registered_count"):
            raise _http_error(
                409, "no_provider_registered",
                "No AI provider is registered. Register one in AI settings, "
                "or run the installer's provider step: ./setup-ai.sh "
                "(Windows: .\\setup-ai.ps1)",
            )
        raise _http_error(
            409, "no_enabled_provider",
            "No enabled AI provider for this project. Configure providers in AI settings.",
        )

    # Capability is checked only after the final provider resolution tier is known and before
    # any lease, token, run record, or worker side effect. This is the server authority: UI
    # badges are advisory and a caller cannot supply its own capability map.
    from modules.flow_gate.services.provider_capability_service import capability_finding
    doc_type = (db_docs.get_by_id(doc_ref) or {}).get("type")
    capability_warning = capability_finding(doc_ref, doc_type, chain[0])
    if capability_warning is not None:
        detail = {
            "code": (
                "provider_capability_restricted"
                if mode == "continuous" else "provider_capability_confirmation_required"
            ),
            "message": "The selected provider cannot modify source or run tests.",
            **capability_warning,
            "provider_resolution": (
                "override" if step_override_provider else
                "stored_step" if stored_provider_active else
                "pinned" if provider_pinned and provider_id else
                "selected" if provider_id else "effective_default"
            ),
        }
        # Continuous execution is never forceable. A single run accepts only literal True
        # on this request; no acknowledgement survives to a later run or hop.
        if mode == "continuous" or capability_warning_ack is not True:
            raise HTTPException(status_code=422, detail=detail)

    # Durable lease admission is authoritative. Memory remains only a UI/live-process signal.
    active = db_group_ai_leases.get_active(group_id)
    handoff_allowed = bool(
        active
        and active.get("state") == "releasing"
        and chain_id
        and active.get("chain_id") == chain_id
    )
    if active is not None and not handoff_allowed:
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active["run_id"])
    # 0299 R0001: refuse before minting a token / creating scratch — a run that would
    # execute in the base tree must not start at all, and failing here keeps the
    # rollback trivial (nothing has been created yet). The doc's branch is only the
    # fallback-branch hint; the guard itself is about the group worktree.
    _svc()._require_group_worktree(
        project_id, module, group_id,
        (db_docs.get_by_id(doc_ref) or {}).get("branch") or "main",
        locale=template_provision.normalize_locale(continuation_locale),
    )

    baseline_seq = db_docs.get_group_max_seq(group_id)
    target_to_end = mode == "continuous" and continuation_target_seq == -1
    scope_oracle_run = oracle._uses_scope_oracle(action_scope, mode, completion_oracle)
    if completion_oracle is not None:
        # Scoped-oracle run: success is judged by the caller's predicate, not by documents.
        docs_target = 0
    elif action_scope in ("workflow_decide", "resolve_conflict"):
        docs_target = 0
    elif mode == "continuous" and continuation_review_mode:
        # NR0003 follow-up proposal 2: review mode is the pre-flight Q-registration phase —
        # mention_service._CONTINUOUS_REVIEW_TEXT tells the worker NOT to create the next
        # document, so a review-mode hop that only registers a Q (or the "no blockers" ack
        # Q) always reaches this doc_ref with docs_reached=0. Targeting >=1 document made
        # that hop indistinguishable from "the hop ran and left nothing" (0359's no-output
        # retry), which reopened wasted attempts and a false "continuous work failed" alert for
        # every review-mode hop. Forcing the target to 0, like the other non-document
        # scopes above, judges it "complete" on 0 reached and never opens a retry.
        docs_target = 0
    elif scope_oracle_run:
        # 0259 B0001: this scope's token cannot register a document, so targeting one made
        # success unreachable. Its default scope oracle is built below (it needs the token).
        docs_target = 0
    elif mode == "single":
        docs_target = 1
    else:
        # 0226 B0001 / NR0003 §5-1: the target is a workflow item_seq, never a group
        # document seq — derive docs_target from the sequence's pending worker items.
        target = int(continuation_target_seq or 0)
        resolved_target = _continuation_docs_target(
            doc_ref,
            target,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if resolved_target is None:
            message = f"continuous run requires a decided workflow sequence on {doc_ref}"
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "message": message,
                "errors": [{"loc": "continuation_target_seq", "msg": message}],
            })
        docs_target = resolved_target
        if docs_target <= 0:
            message = f"no pending worker step at or below workflow item_seq {target}"
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "message": message,
                "errors": [{"loc": "continuation_target_seq", "msg": message}],
            })

    # Allocate and lease before token issuance/worker spawn. The DB primary key makes two
    # concurrent starts atomic across processes; an acquiring lease self-reclaims on expiry.
    run_id = _svc()._next_run_id()
    lease_chain_id = chain_id or run_id
    try:
        lease = db_group_ai_leases.acquire(
            group_id=group_id,
            project_id=project_id,
            run_id=run_id,
            chain_id=lease_chain_id,
            action_scope=action_scope,
            worker_identity=issued_to,
        )
    except db_group_ai_leases.RunIdCollision:
        # 0401 NR0003 §4 / T0004 work item 7: two runs minted the same today-serial in the
        # instant -- genuinely rare even without the floor in _next_run_id, and that floor
        # makes it rarer still. One retry with a freshly minted id is enough for something
        # this rare; a second hit is a real systemic problem, so it surfaces as a clean
        # 409 instead of retrying forever or falling through as a raw DB error.
        run_id = _svc()._next_run_id()
        lease_chain_id = chain_id or run_id
        try:
            lease = db_group_ai_leases.acquire(
                group_id=group_id,
                project_id=project_id,
                run_id=run_id,
                chain_id=lease_chain_id,
                action_scope=action_scope,
                worker_identity=issued_to,
            )
        except db_group_ai_leases.RunIdCollision:
            raise _http_error(
                409, "run_id_collision",
                _RUN_ID_COLLISION_COPY[template_provision.normalize_locale(continuation_locale)],
            )
    if lease is None:
        active = db_group_ai_leases.get_active(group_id) or {}
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active.get("run_id"))

    if document_review_loop is not None and issue_builder is not None:
        # 0417 T0013: tell the (possibly stage-aware) issue_builder which stage this hop is —
        # a loop that starts_with_rework must mint an edit-scoped token on its very first hop,
        # not a review-scoped one. See the matching comment on ai_invoke_routes._issue_review.
        issue_builder.loop_stage = initial_stage
    if issue_builder is not None:
        # 0359 L0007 §2.9: hand the run identity to the builder so the token it mints carries
        # ai_run_id. NR0003 §4 measured 1,346 continuous tokens with an EMPTY ai_run_id — every
        # one of them was issued through this branch, which never passed it, so there was no
        # bridge from a dead hop's token back to the run that died. Builders that do not accept
        # the keyword (review / sequence_edit / test_run) keep being called with no arguments.
        issue = _call_issue_builder(issue_builder, run_id)
        mention = issue.get("mention")
    else:
        issue = token_service.issue(
            project=project_id,
            group_id=group_id,
            action_scope=action_scope,
            doc_ref=doc_ref,
            issued_to=issued_to,
            continuation_target_seq=continuation_target_seq if mode == "continuous" else None,
            continuation_review_mode=bool(mode == "continuous" and continuation_review_mode),
            continuation_instruction_mode=continuation_instruction_mode if mode == "continuous" else None,
            continuation_locale=continuation_locale if mode == "continuous" else None,
            merge_id=merge_id if action_scope == "resolve_conflict" else None,
            provider_id=provider_id,
            ai_run_id=run_id,
            continuation_auto_approve_item_seqs=(
                continuation_auto_approve_item_seqs if mode == "continuous" else None
            ),
        )
        mention = mention_builder(issue["raw_token"], issue["scratch_dir"])
    if not mention:
        # No prompt ⇒ nothing to launch. Discard the token and its acquiring lease.
        try:
            token_service.revoke(issue["token_id"], reason="ai_invoke_mention_unavailable")
        except Exception:
            logger.warning("token revoke failed after mention_unavailable", exc_info=True)
        db_group_ai_leases.release(group_id, run_id)
        raise _http_error(409, "mention_unavailable",
                          "Could not build a worker mention for this document.")

    lease = db_group_ai_leases.activate(
        group_id, run_id, issue.get("token_id"), action_scope, issued_to, _svc().RUN_TIMEOUT_CAP_SEC
    )
    if lease is None:
        try:
            token_service.revoke(issue["token_id"], reason="ai_invoke_lease_lost")
        except Exception:
            logger.warning("token revoke failed after lease loss", exc_info=True)
        raise _http_error(409, "run_lease_lost", "The AI run lease could not be activated.")
    # 0346 T0005 §2-5 / D0004 §3-3: the handoff-note tab's common note and/or this hop's
    # individual note are prepended here, at the single point every hop's prompt (built by
    # whichever of the three builders ran above) has already converged into one string — see
    # D0004 §3-4 for why the builders themselves are never touched. Unlike the provider
    # override, an individual note does NOT replace the common one: D0004 §3-3 treats them as
    # stackable ("what this is for" + "what you take on"), so both are adopted when present.
    # A resolution failure must not stall the hop (same contract as the provider override).
    # 0406 T0022 work item 5: where this hop's user message came from, and what the final
    # prompt became, ride on the run. The text is not kept — only kind, length and hash.
    prompt_audit: dict = {
        "prompt_message_source": "none",
        "prompt_common_default_applied": False,
        "prompt_user_message_length": 0,
        "prompt_user_message_sha256": None,
    }
    if mode == "continuous" or (mode == "single" and action_scope == "new"):
        mention = _inject_hop_notes(
            mention,
            doc_ref,
            default_note=(continuation_default_note if mode == "continuous" else None),
            note_overrides=(continuation_note_overrides if mode == "continuous" else None),
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
            fold_worker_item_seq=(mode == "continuous"),
            locale=continuation_locale,
            audit=prompt_audit,
        )
    _prompt_final_length, _prompt_final_sha256 = prompt_digest(mention)

    if scope_oracle_run:
        # After issue() (the judge target comes from the token) but before the worker is
        # launched below, so the baseline cannot include the work this run is about to do.
        completion_oracle = oracle._scope_oracle(action_scope, issue.get("token_id"), doc_ref)

    _svc()._cleanup_retained_scratches(project_id)
    # 0357 T0004: the chain identity/counters this hop inherits. `run_id` is allocated
    # once, above, and stays the hop's own id — the CHAIN id is what travels hop to hop.
    if mode == "continuous":
        chain_id = chain_id or run_id
        if chain_docs_target is None:
            chain_docs_target = docs_target
        if chain_docs_reached is None:
            chain_docs_reached = 0
    else:
        # A single run is a degenerate one-hop chain. Returning the same payload
        # shape keeps clients simple without changing the run counters' meaning.
        #
        # 0414 L0008 §5 체인 카운터: unless the CALLER named a chain. A review/rework hop is
        # mode="single" but belongs to a running chain, and overwriting chain_id with its own
        # run_id would break the lease handoff (which requires a matching chain_id) and reset
        # the miniplayer's progress to 0. An ordinary single run passes none of these three
        # and still resolves to exactly the values above, so nothing existing moves.
        chain_id = chain_id or run_id
        if chain_docs_target is None:
            chain_docs_target = docs_target
        if chain_docs_reached is None:
            chain_docs_reached = 0
    scratch = _svc()._create_scratch(project_id, run_id)

    doc = db_docs.get_by_id(doc_ref) or {}
    # 0187 rev2: same group-worktree routing as the test runner — the invoked AI's
    # cwd and the pollution diff must watch the tree the group's CRUD writes to.
    source_root = storage_paths.resolve_project_src_root(
        project_id, doc.get("branch") or "main", group_id=group_id
    )

    started_at = now_iso()
    timeout_sec = _resolve_timeout_sec(
        mode, docs_target, target_to_end, continuation_step_timeout_sec, hop_kind
    )
    # 0414 P0007: what THIS hop's review selection resolves to, answered in the start
    # response rather than after the fact — "I picked a reviewer, did it take?" has to be
    # answerable while the run is going, not once it is over (0406 T0022 작업 3's reasoning
    # for the instruction mode, applied to the same class of question).
    review_count_overrides = (
        continuation_review_count_overrides if mode == "continuous" else None
    )
    reviewer_overrides = (
        continuation_reviewer_overrides if mode == "continuous" else None
    )
    hop_item_seq = _hop_item_seq_or_none(doc_ref) if mode == "continuous" else None
    hop_review_count = review.resolve_review_count(review_count_overrides, hop_item_seq)
    hop_reviewer_provider_id = (
        review.resolve_reviewer(reviewer_overrides, hop_item_seq, project_id)
        if hop_review_count else None
    )
    if document_review_loop is not None:
        document_review_loop.update({
            "round_no": 1,
            "current_stage": (REWORK_HOP_KIND if document_review_loop["starts_with_rework"] else REVIEW_HOP_KIND),
            "stop_reason": None,
            "stop_detail": None,
            "attempts_used": 0,
            "started_at": started_at,
            "deadline_at": _deadline_iso(started_at, int(document_review_loop["total_timeout_sec"])),
        })
    run = {
        "run_id": run_id,
        "status": "running",
        "mode": mode,
        "project_id": project_id,
        "module": module,
        "group_id": group_id,
        "doc_ref": doc_ref,
        "docs_target": docs_target,
        # 0357 T0004: chain-lifetime progress, carried across the per-hop runs an
        # unmanned continuous chain is made of (docs_* stay per-hop judging values).
        "chain_id": chain_id,
        "chain_docs_target": int(chain_docs_target or 0),
        "chain_docs_reached": int(chain_docs_reached or 0),
        "chain_docs_accounted": False,
        "baseline_seq": baseline_seq,
        "timeout_sec": timeout_sec,
        # 0359 P0006 [hop budget]: the wall-clock the budget actually lands on. Until now the
        # limit appeared in NO response at all, so "did it die on the clock?" could only be
        # answered by re-deriving the formula from logs — which is exactly the work NR0003 had
        # to do (and got wrong on its first pass).
        "deadline_at": _deadline_iso(started_at, timeout_sec),
        # ── 0446 T0014 §2: two clocks, kept apart by name ───────────────────────
        # `timeout_sec` / `deadline_at` above keep their exact stored meaning and are
        # read from here on as the NO-PROGRESS threshold: the EARLIEST this run may be
        # stopped, and only if it shows nothing new for that long. The ceiling below is
        # the latest it can possibly run, progress or not. Both live in memory on
        # purpose — the column and the migration belong to T#2 (§2-5).
        "absolute_cap_sec": _absolute_cap_sec(),
        "absolute_deadline_at": _deadline_iso(started_at, _absolute_cap_sec()),
        "stall_anchor_mono": None,     # start of the current no-progress window
        "last_progress_mono": None,    # None = nothing was ever observed to move
        "last_progress_at": None,
        "last_progress_signal": None,
        "progress_observations": 0,
        "watchdog_kill": None,         # raw, monotonic, process-local (0446 T0014)
        # 0446 T0016 3-1: the durable reading of the above, resolved once at finalize.
        # These two are what the row, the detail response and the next rework prompt read.
        "timeout_kind": None,
        "timeout_diagnosis": None,
        "stdout_tail": None,
        "stderr_tail": None,
        "provider": None,
        "provider_id": None,
        "attempt_no": 0,
        "fallback_history": [],
        "register_errors": [],
        "tool_call_misses": 0,
        "turn_limit_exhausted": False,
        "oracle_mismatch": False,
        "started_at": started_at,
        "started_mono": time.monotonic(),
        "attempt_started_mono": time.monotonic(),
        "cancel_event": threading.Event(),
        "proc": None,
        "timed_out": False,
        "end_reason": None,
        "exit_code": None,
        "last_message": None,
        "last_message_received": False,
        "outcome": None,
        "docs_reached": 0,
        "reached_doc_ids": [],
        "source_dirty": None,
        "source_dirty_files": [],
        "scratch_dir": str(scratch),
        "scratch_retained": None,
        "duration_ms": None,
        "finished_at": None,
        "dirty_baseline": _svc()._git_status_paths(source_root),
        "source_root": str(source_root) if source_root else None,
        "api_base_url": api_base_url,
        # 0505 T0006 (DB0005 2/3.3): operator_api_base is a one-time sanitized snapshot
        # of this same value, taken here at run start. transport_api_base starts empty
        # -- it is filled once, by whichever of the six mediated self-HTTP calls opens
        # first inside THIS hop (ai_invoke/provider_api.py's _sanitize_diagnostic_base).
        "operator_api_base": provider_api._sanitize_diagnostic_base(api_base_url),
        "transport_api_base": None,
        "last_tool_name": None,
        "last_tool_status": None,
        "last_tool_error": None,
        "api_turns_used": None,
        "model_http_calls": 0,
        "model_last_http_status": None,
        "tool_calls_received": 0,
        "tool_calls_executed": 0,
        "chain_source": chain_source,
        "selected_provider_source": selected_provider_source,
        "fallback_allowed": selected_provider_source == "project_default",
        "action_scope": action_scope,
        # 0446 T0008 §3-1: did the ENGINE plant this run's completion oracle, or did the
        # caller hand one in? Computed at the top of start_run and, until now, discarded —
        # which left `completion_oracle is not None` an unconditional retry block for every
        # scoped run. That is the fourth gate NR0003 §6-2 did not name.
        "scope_oracle_run": scope_oracle_run,
        "target_to_end": target_to_end,
        "continuation_instruction_mode": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        # 0352 T0004 §3.5: the per-item_seq N/T auto-approve selection rides the run the same
        # way instruction_mode does, so every hop's provider/note/docs-target logic re-reads
        # the SAME selection the user made once at the start of the chain.
        "continuation_auto_approve_item_seqs": (
            continuation_auto_approve_item_seqs if mode == "continuous" else None
        ),
        # ── 0406 T0022 items 2/3/5: values for judging this hop after the fact ───────
        # The mode as the request sent it / as the server read it / whether normalisation
        # actually fired. Keeping the three apart is what separates "the user picked
        # auto_approved" from "the entry point omitted it and the server chose instead".
        "continuation_instruction_mode_requested": (
            requested_continuation_instruction_mode if mode == "continuous" else None
        ),
        "continuation_instruction_mode_normalized": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            mode == "continuous"
            and _instruction_mode_fallback_applied(requested_continuation_instruction_mode)
        ),
        # The document type of the slot this hop's worker actually filled, plus the item_seq
        # of N/T the server auto-handled. advance_workflow reports it (not every builder does).
        "worker_document_type": issue.get("worker_document_type"),
        "auto_handled_item_seqs": list(issue.get("auto_handled_item_seqs") or []),
        # How the handoff note resolved, plus its length/hash. The text is not stored.
        **prompt_audit,
        "prompt_final_length": _prompt_final_length,
        "prompt_final_sha256": _prompt_final_sha256,
        # 0359 L0007 §2.5: the retry rebuilds this hop's prompt from scratch when the token
        # has to be reissued, and a prompt is only correct in the locale the chain chose.
        "continuation_locale": (continuation_locale if mode == "continuous" else None),
        # 0317 TR0011 (Q153 opt-1): the per-step override map rides on the run so each
        # re-spawned hop can re-apply it (it never touches a token; it is session-scoped).
        "continuation_provider_overrides": (
            continuation_provider_overrides if mode == "continuous" else None
        ),
        # 0435 T0004: retry code never replays the priority tiers. The finalized chain head is
        # the single source of truth for this hop, regardless of whether it came from a step
        # override, a human pin, a stored row, a header default, or a doc-type assignment.
        # 0446 T0008 §3-5: a scope-oracle rework run needs the same head on the record, or
        # `_retry_provider_chain` returns [] and the loop ends as
        # "providers_exhausted_for_retry" with every gate above it already open. The 0435
        # T0004 contract is untouched: attempt 1 retries exactly once on THIS provider and
        # never switches to another. `_stop_reason_text` reads the name back for the human
        # sentence.
        "continuation_selected_provider_id": (
            chain[0].get("id")
            if chain and (
                mode == "continuous"
                or oracle._scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
                or oracle._review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
            )
            else None
        ),
        "continuation_selected_provider_name": (
            chain[0].get("name")
            if chain and (
                mode == "continuous"
                or oracle._scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
                or oracle._review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
            )
            else None
        ),
        # 0346 T0005: the handoff-note bundle rides the run forward the same way the
        # provider override map does, so a re-spawned hop (_maybe_auto_resume_hop ->
        # _spawn_auto_resume) can re-apply it. Session-scoped — never persisted on a token.
        "continuation_note_overrides": (
            continuation_note_overrides if mode == "continuous" else None
        ),
        "continuation_default_note": (
            continuation_default_note if mode == "continuous" else None
        ),
        # flowgate.default.0400 M0005: the budget PICK rides the run forward the same way the
        # provider/note selections do, so a re-spawned hop (auto-resume, or a resume after a
        # user pause) re-applies the same choice instead of silently falling back to
        # HOP_TIMEOUT_SEC. Session-scoped — never persisted outside a paused-chain snapshot.
        #
        # 0446 T0010 §3-3: no longer blanked on a single run. This read
        # `continuation_step_timeout_sec if mode == "continuous" else None`, so a single run
        # forgot the ORIGIN of its own budget the instant it started — get_run_record showed
        # a 4-hour timeout_sec with nothing to say where it came from. The value only ever
        # LEAVES this dict through continuous-chain code (_maybe_auto_resume_hop /
        # _spawn_auto_resume / _apply_stop_row), and _apply_stop_row returns on
        # `mode != "continuous"` at its first line, so a single run still writes no
        # ai_invoke_paused_chains row and still queues no auto-resume hop. Keeping the value
        # is a record of the user's pick, not a new execution path.
        "continuation_step_timeout_sec": continuation_step_timeout_sec,
        # flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick, carried
        # the same way the budget pick above is — read by attempts_max below and by every
        # pause/resume/handoff snapshot that already threads continuation_step_timeout_sec.
        "continuation_restart_max_attempts": (
            continuation_restart_max_attempts if mode == "continuous" else None
        ),
        # 0317 T0013 defect ③: the header default provider pin rides the run too. Without it a
        # re-spawned hop that has NO per-step override lost the user's chosen default and fell
        # back to the doc-type assignment / project default chain — contradicting the
        # "default: <name>" tag every ContinuousWorkDialog row promises. Session-scoped like the
        # override map; never persisted on a token.
        "continuation_base_provider_id": (provider_id if mode == "continuous" else None),
        "continuation_provider_pinned": (
            bool(provider_pinned and provider_id) if mode == "continuous" else False
        ),
        # 0252 L0009 §2.8: keep the requester on the record so the global active list
        # (active_all) can filter runs per user, and §2.1: the continuation target for
        # the paused-row snapshot (None = to-end, resolved again at resume time).
        "issued_to": issued_to,
        "continuation_target_seq": (
            None if target_to_end or mode != "continuous" else continuation_target_seq
        ),
        "pause_requested": False,
        "user_paused": False,
        "raw_token": issue["raw_token"],
        "merge_id": merge_id,
        "completion_oracle": completion_oracle,
        # ── 0359 L0007 §2.9 / §2.6: what the no-output retry loop needs to open attempt 2 ──
        # The token ID (was: only the raw token, so the retry could not ask whether the token
        # was still usable), the mention it was handed, and the builder that can mint a fresh
        # one for the SAME head when the old token is spent.
        "token_id": issue.get("token_id"),
        "mention": mention,
        "issue_builder": issue_builder,
        # Which workflow slot this hop is filling — rides the run into the record, the
        # notification and the stop row so "where did the chain die?" has an answer.
        "hop_item_seq": hop_item_seq,
        # 0414 P0007 / L0008 §2.9: the two [검수] maps ride the run hop to hop, exactly like
        # the provider/note maps above. Dropped at ANY carrier, the chain reviews its first
        # step and then silently stops reviewing — the failure shape L0008 §2.9 names.
        "continuation_review_count_overrides": review_count_overrides,
        "continuation_reviewer_overrides": reviewer_overrides,
        "document_review_loop": document_review_loop,
        "hop_kind": hop_kind,
        "hop_review_count": hop_review_count,
        "hop_reviewer_provider_id": hop_reviewer_provider_id,
        "hop_reviewer_provider_name": review._provider_name_of(project_id, hop_reviewer_provider_id),
        "attempts_used": 0,
        # flowgate.default.0443 T0002 (R0001): a continuous hop resolves the user's
        # "재시작 횟수" pick via _resolve_restart_max_attempts instead of the fixed
        # NO_OUTPUT_MAX_ATTEMPTS constant.
        # 0446 T0008 §3-3: a record/display value — the real ceiling is the
        # `attempts_used >= NO_OUTPUT_MAX_ATTEMPTS` comparison in `_retry_eligible`. Leaving
        # this at 1 for a scope-oracle rework run would make `ai_invoke_runs` and the screen
        # say one thing while the engine does another. continuation_restart_max_attempts is
        # None outside continuous mode, so _resolve_restart_max_attempts falls back to
        # reproducing the previous fixed NO_OUTPUT_MAX_ATTEMPTS(2) behavior for it.
        # flowgate.default.0476 T0012 / CH0011: an engine-spawned review hop follows the
        # same restart pick as work and rework. With no pick, the resolver still returns 2,
        # preserving 0466 T0007's first-attempt-plus-one-retry default. A pick of -1 remains
        # bounded by the retry budget/recheck guards; a pick of 0 makes the hop one-shot.
        "attempts_max": (
            _resolve_restart_max_attempts(continuation_restart_max_attempts)
            if mode == "continuous"
            or oracle._scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
            or oracle._review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
            else 1
        ),
        "retry_block_reason": None,
        "last_message_seen": None,
        "stop_code": None,
        "stop_reason": None,
        "resumable": False,
        # Tagged by the inbox self-chain (mark_chain_stop) when IT is the one that stopped
        # the chain; the engine's own classification still wins for cancel/timeout/pause.
        "inbox_stop_code": None,
        "failure_signal_sent": False,
    }
    _note_issued_raw_token(run, run.get("raw_token"))
    _note_issued_prompt(run, mention)
    with _runs_lock:
        # The durable loop row is created before the worker can finish; a successful start never
        # exposes memory-only loop state. Roll admission back if persistence fails.
        if document_review_loop is not None:
            try:
                _svc()._insert_document_review_loop(run)
            except Exception:
                db_group_ai_leases.release(group_id, run_id)
                try:
                    token_service.revoke(issue["token_id"], reason="document_review_loop_persist_failed")
                except Exception:
                    logger.warning("review-loop token rollback failed for %s", run_id, exc_info=True)
                raise _http_error(500, "document_review_loop_persist_failed", "Could not persist document review loop state.")
        _svc()._runs[run_id] = run

    thread = threading.Thread(
        target=_svc()._worker,
        args=(run, chain, mention),
        daemon=True,
        name=f"ai-invoke-{run_id}",
    )
    thread.start()

    return {
        "ok": True,
        "run_id": run_id,
        "status": "running",
        "mode": mode,
        "group_id": group_id,
        "doc_ref": doc_ref,
        "docs_target": docs_target,
        "chain_id": run["chain_id"],
        "chain_docs_target": run["chain_docs_target"],
        "chain_docs_reached": run["chain_docs_reached"],
        "continuation_instruction_mode": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        "continuation_auto_approve_item_seqs": (
            continuation_auto_approve_item_seqs if mode == "continuous" else None
        ),
        # 0406 T0022 work item 3: the start response already answers it — the requested and
        # normalised mode (and whether normalisation fired), this hop's real worker slot and
        # document type, and the N/T the server handled with no worker. If these were only
        # known after the run, "the N/T vanished" could never be checked while it runs.
        "continuation_instruction_mode_requested": run[
            "continuation_instruction_mode_requested"
        ],
        "continuation_instruction_mode_normalized": run[
            "continuation_instruction_mode_normalized"
        ],
        "continuation_instruction_mode_fallback_applied": run[
            "continuation_instruction_mode_fallback_applied"
        ],
        "hop_item_seq": run["hop_item_seq"],
        # 0414 P0007 시작 응답: the selection as stored, plus what it resolved to for THIS
        # hop. docs_target above is deliberately untouched — review rounds are not documents,
        # so a reviewed chain and an unreviewed one report the same target.
        "continuation_review_count_overrides": run["continuation_review_count_overrides"],
        "continuation_reviewer_overrides": run["continuation_reviewer_overrides"],
        "hop_kind": run["hop_kind"],
        "hop_review_count": run["hop_review_count"],
        "hop_reviewer_provider_id": run["hop_reviewer_provider_id"],
        "hop_reviewer_provider_name": run["hop_reviewer_provider_name"],
        "worker_document_type": run["worker_document_type"],
        "auto_handled_item_seqs": run["auto_handled_item_seqs"],
        "provider": oracle._provider_brief(chain[0]),
        "selected_provider_source": run["selected_provider_source"],
        "fallback_allowed": run["fallback_allowed"],
        "warnings": [capability_warning] if capability_warning is not None else [],
        "attempt_no": 1,
        "started_at": run["started_at"],
        # 0359 P0006 [hop budget]: the budget and its wall-clock deadline travel with every
        # start / status / finish payload, so nobody has to reconstruct them from logs again.
        "timeout_sec": run["timeout_sec"],
        "deadline_at": run["deadline_at"],
        "document_review_loop": _svc().document_review_loop_payload(run),
    }


def _normalized_instruction_mode(mode: Optional[str]) -> str:
    from modules.flow_gate.services.workflow_decision_service import (
        normalize_continuation_instruction_mode,
    )

    return normalize_continuation_instruction_mode(mode)


def _instruction_mode_fallback_applied(mode: Optional[str]) -> bool:
    from modules.flow_gate.services.workflow_decision_service import (
        instruction_mode_fallback_applied,
    )

    return instruction_mode_fallback_applied(mode)


# ── 0359 L0007: hop budget, run identity, prompt reuse ───────────────────────

def _resolve_timeout_sec(
    mode: str,
    docs_target: int,
    target_to_end: bool,
    continuation_step_timeout_sec: Optional[int] = None,
    hop_kind: str = WORK_HOP_KIND,
) -> int:
    """The run's time budget (L0007 §2.13 / P0006 [hop budget]).

    A continuous run IS one hop (0317 TR0011 re-spawns a worker per step), so scaling its
    budget by how many slots are still ahead — the old min(3600 × slots_left, 14400) — handed
    the LAST hop the SMALLEST budget, which is backwards. NR0003 §7 cleared this of causing
    the reported incident (that hop had 2h and used 2m25s) but kept it as a live hazard: TR
    hops of 74 minutes were measured. Fixed per hop now.

    flowgate.default.0400 M0005: that fixed per-hop budget became a user pick (30-240 minutes,
    ContinuousWorkDialog duration section) instead of always HOP_TIMEOUT_SEC.

    0446 NR0003 §3-5 (R5) / T0010 §3-2: the pick is no longer continuous-only. The single-run
    formula min(RUN_TIMEOUT_BASE_SEC × max(1, docs_target), RUN_TIMEOUT_CAP_SEC) bottoms out
    at exactly 3600 for a rejection rework — its docs_target is 1 and the max(1, …) floor
    pins it there — so every rework got precisely one hour and no screen could ask for more
    (264/264 measured runs had timeout_sec=3600; NR §2 lost two of them at the 3603s boundary
    and a third quit at 59.6 minutes). So the explicit pick is read FIRST, above the mode
    branch:

        an in-range explicit pick is this run's budget, whatever the mode;
        otherwise the previous order stands — continuous ⇒ HOP_TIMEOUT_SEC,
        target_to_end ⇒ RUN_TIMEOUT_CAP_SEC, else the per-document formula.

    The bounds stay STEP_TIMEOUT_MIN_SEC..STEP_TIMEOUT_MAX_SEC — the same pair
    ai_invoke_routes validates against (422) and the same list both dialogs offer — so the
    screen, the route and the engine cannot drift apart. The no-pick default is deliberately
    untouched: 3600 was never the defect, "cannot choose" was.
    """
    # 0446 T0010 §3-1: an explicit in-range pick outranks the mode branch below,
    # whatever the mode — a single rejection rework otherwise bottoms out at exactly
    # 3600 and no screen can ask for more (NR0003 R5).
    if (
        continuation_step_timeout_sec is not None
        and STEP_TIMEOUT_MIN_SEC <= continuation_step_timeout_sec <= STEP_TIMEOUT_MAX_SEC
    ):
        return int(continuation_step_timeout_sec)
    # 0414 L0008 §5: a review/rework hop is mode="single" but belongs to the chain, so it
    # takes the chain's per-hop budget rather than the single-run formula.
    if mode == "continuous" or hop_kind in CHAIN_MEMBER_HOP_KINDS:
        return HOP_TIMEOUT_SEC
    if target_to_end:
        return _svc().RUN_TIMEOUT_CAP_SEC
    return min(_svc().RUN_TIMEOUT_BASE_SEC * max(1, docs_target), _svc().RUN_TIMEOUT_CAP_SEC)


def _resolve_restart_max_attempts(continuation_restart_max_attempts: Optional[int]) -> int:
    """Total attempts allowed for one hop (0443 R0001 "재시작 횟수").

    The dialog picks a RESTART count, not a total-attempts count — this converts it: N
    restarts == N+1 total attempts, and -1 stays -1 (the "될 때까지" unlimited sentinel
    _retry_eligible/_retry_provider_chain both check for explicitly). An unset or
    unrecognized value falls back to RESTART_MAX_ATTEMPTS_DEFAULT, which reproduces the
    fixed NO_OUTPUT_MAX_ATTEMPTS(2) behavior this feature replaces. The bound is read from
    ai_execution_policy_service (SSOT) instead of a frozen literal, so raising the setting
    also widens what a read accepts — flowgate.default.0490 T0005 §4-4.
    """
    restart_count = continuation_restart_max_attempts
    choices = ai_execution_policy_service.repeat_count_choices(allow_zero=True)
    if isinstance(restart_count, bool) or restart_count not in choices:
        restart_count = RESTART_MAX_ATTEMPTS_DEFAULT
    if restart_count == -1:
        return -1
    return int(restart_count) + 1


def _deadline_iso(started_at: str, timeout_sec: int) -> Optional[str]:
    """started_at + timeout_sec, in the same ISO/timezone shape now_iso() produces."""
    try:
        return (
            datetime.fromisoformat(started_at) + timedelta(seconds=int(timeout_sec))
        ).isoformat(timespec="seconds")
    except Exception:
        return None


def _call_issue_builder(issue_builder: Callable, run_id: str) -> dict:
    """Call a token issuer, handing it the run identity when it can take one (L0007 §2.9).

    The contract gained `ai_run_id`, but four of the five builders in the codebase mint a
    single-shot token that has no run worth pointing at, and existing tests call them bare.
    So inspect rather than force: a builder that declares the keyword gets it, the rest are
    called exactly as before.
    """
    try:
        import inspect

        params = inspect.signature(issue_builder).parameters
        accepts = "ai_run_id" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):
        accepts = False
    return issue_builder(ai_run_id=run_id) if accepts else issue_builder()


def _hop_item_seq_or_none(doc_ref: str) -> Optional[int]:
    """Which workflow slot this hop is filling — best effort (L0007 §2.9 / §5).

    None when the sequence is not decided yet (a workflow_decide run). The null rides through
    to the record, the event and the notification rather than suppressing any of them."""
    try:
        return _svc()._next_incomplete_item_seq(doc_ref)
    except Exception:
        logger.warning("ai-invoke hop item_seq lookup failed for %s", doc_ref, exc_info=True)
        return None


def _inject_hop_notes(
    mention: Optional[str],
    doc_ref: str,
    *,
    default_note: Optional[str],
    note_overrides: Optional[dict],
    instruction_mode: Optional[str],
    locale: Optional[str],
    auto_approve_item_seqs: Optional[list] = None,
    fold_worker_item_seq: bool = True,
    audit: Optional[dict] = None,
) -> Optional[str]:
    """Prepend the common note plus the effective step note for continuous and single runs.

    The note is read from the row the AI worker actually fills, and from that row ONLY.
    0408 M0019 re-rejection ("why is the TR/NR mention using T/N's?"): a pair fallback made an
    auto-approved NR hop speak the sentence written for N. Each row carries its own note now
    (work_plan_sequence_service.attach_auto_rows), so the fold picks the row and the row picks
    the note. A present override key wins even when its value normalizes to empty: that empty
    string is the user's tombstone and suppresses the stored note. Only an absent key falls
    back to the note stored on the row. Continuous hops use the mode-aware N/T -> NR/TR fold;
    a single new hop writes the current head directly and therefore does not fold.

    0406 T0022 item 5 — pass ``audit`` and it reports whether this hop's user message
    resolved from (a) a step override, (b) the common default, (c) the stored sequence
    note fallback, or (d) nothing — plus that string's length and sha256. This is exactly
    the structural gap NR0021 §8 pinned down: a session-scoped handoff note is persisted
    nowhere, so what the user remembers typing could be neither proved nor disproved.
    """
    if not mention:
        return mention

    notes: list[str] = []
    common_applied = bool(default_note and default_note.strip())
    if common_applied:
        notes.append(default_note.strip())

    hop_note: Optional[str] = None
    hop_source: Optional[str] = None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        head = db_wfseq.get_effective_head(seq["id"]) if seq is not None else None
        item_seq = None
        if head:
            item_seq = (
                _hop_worker_item_seq(
                    seq["id"],
                    head,
                    continuation_instruction_mode=instruction_mode,
                    continuation_auto_approve_item_seqs=auto_approve_item_seqs,
                )
                if fold_worker_item_seq
                else head.get("item_seq")
            )

        override_present = False
        override_value = None
        if item_seq is not None and isinstance(note_overrides, dict):
            if str(item_seq) in note_overrides:
                override_present = True
                override_value = note_overrides[str(item_seq)]
            elif item_seq in note_overrides:
                override_present = True
                override_value = note_overrides[item_seq]

        if override_present:
            from modules.flow_gate.services.work_plan_sequence_service import normalize_note

            hop_note = normalize_note(override_value) or None
            hop_source = "override"
        elif item_seq is not None:
            hop_note = resolve_stored_step_note(doc_ref, item_seq)
            hop_source = "stored_note" if hop_note else None
    except Exception:  # noqa: BLE001 — a note lookup failure must not stall the hop
        logger.warning("continuation hop note resolution failed for %s", doc_ref, exc_info=True)

    if hop_note:
        notes.append(hop_note)
    elif hop_source == "override":
        # An empty override is the user's tombstone: it means "wipe the stored note", so
        # it is recorded as "an override applied" but contributes no text.
        hop_source = "override_tombstone"
    try:
        if notes:
            mention = invoke_mention_service.prepend_messages_section(mention, notes, locale)
    except Exception:  # noqa: BLE001 — a note failure must not stall the hop
        logger.warning("continuation hop note injection failed for %s", doc_ref, exc_info=True)
    if audit is not None:
        sources = []
        if hop_source:
            sources.append(hop_source)
        if common_applied:
            sources.append("common_default")
        applied = "\n\n".join(notes)
        length, digest = prompt_digest(applied)
        audit.update({
            "prompt_message_source": "+".join(sources) or "none",
            "prompt_common_default_applied": common_applied,
            "prompt_user_message_length": length,
            "prompt_user_message_sha256": digest,
        })
    return mention


def resolve_stored_step_note(doc_ref: str, item_seq: int) -> Optional[str]:
    """Return one normalized sequence-row note; lookup failures degrade to no note."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        for item in db_wfseq.get_sequence_items(seq["id"]) or []:
            if item.get("item_seq") == item_seq:
                from modules.flow_gate.services.work_plan_sequence_service import normalize_note

                return normalize_note(item.get("note")) or None
    except Exception:  # noqa: BLE001 — prompt enrichment must never stop execution
        logger.warning("stored step note resolution failed for %s", doc_ref, exc_info=True)
    return None


def _resolve_continuation_hop_provider(
    project_id: str,
    doc_ref: str,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[str]:
    """Return the doc-type-assigned provider for the worker hop, or the default-chain tier.

    A head only folds to its paired report type when it is server auto-handled — either
    every N/T under ``auto_approved``, or the specific item_seqs selected under ``ai_direct``
    (0352 T0004 §2). Every other N/T, and TS in either mode, is worker-authored and resolves
    its own head type (flowgate.default.0353 B0001 / NR0003). Never raises: lookup gaps fall
    through.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        head_type = (head.get("type") or "").upper()
        from modules.flow_gate.services.workflow_decision_service import (
            AUTO_REPORT_MAP,
            is_auto_handled_step,
        )

        fold_to_report = is_auto_handled_step(
            head_type=head_type,
            item_seq=head.get("item_seq"),
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        worker_type = AUTO_REPORT_MAP.get(head_type, head_type) if fold_to_report else head_type
        # Preserve the legacy auto-approved preference: report assignment first, raw N/T
        # assignment second. Non-folded heads resolve only their own worker-authored type.
        assigned = ai_settings_service.resolve_doctype_provider(project_id, worker_type)
        if assigned is None and fold_to_report and worker_type != head_type:
            assigned = ai_settings_service.resolve_doctype_provider(project_id, head_type)
        return assigned
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop provider resolution failed for %s", doc_ref,
                       exc_info=True)
        return None


def _paired_report_row(items: list[dict], head: dict) -> Optional[dict]:
    """Return the first paired worker report after an N/T head.

    TSR is server-assembled and never has a provider, so TS intentionally has no provider
    fallback candidate here.
    """
    from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

    head_item_seq = head.get("item_seq")
    report_type = AUTO_REPORT_MAP.get((head.get("type") or "").upper())
    if head_item_seq is None or not report_type or report_type == "TSR":
        return None
    return next(
        (
            item
            for item in sorted(items or [], key=lambda i: i.get("item_seq") or 0)
            if (item.get("item_seq") or -1) > head_item_seq
            and (item.get("type") or "").upper() == report_type
        ),
        None,
    )


def _hop_worker_item_seq(
    seq_id: int,
    head: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[int]:
    """Return the item_seq of the slot this worker fills.

    A server auto-handled N/T head — every one under ``auto_approved``, or the specific
    item_seqs selected under ``ai_direct`` (0352 T0004 §2) — folds to the paired NR/TR slot.
    Every other N/T head, and TS in both modes, is an independent worker hop and retains its
    own item_seq (flowgate.default.0353 B0001 / NR0003). Missing or unknown modes normalize
    to the legacy ``auto_approved`` behavior.
    """
    head_item_seq = head.get("item_seq")
    head_type = (head.get("type") or "").upper()
    from modules.flow_gate.services.workflow_decision_service import (
        AUTO_REPORT_MAP,
        is_auto_handled_step,
    )

    fold_to_report = is_auto_handled_step(
        head_type=head_type,
        item_seq=head_item_seq,
        instruction_mode=continuation_instruction_mode,
        auto_approve_item_seqs=continuation_auto_approve_item_seqs,
    )
    if not fold_to_report or head_item_seq is None:
        return head_item_seq
    report = _paired_report_row(db_wfseq.get_sequence_items(seq_id) or [], head)
    return report.get("item_seq") if report is not None else head_item_seq


def _hop_worker_rows(
    seq_id: int,
    head: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> list[dict]:
    """Return provider/note candidates in worker-row then paired-row priority order."""
    worker_item_seq = _hop_worker_item_seq(
        seq_id,
        head,
        continuation_instruction_mode=continuation_instruction_mode,
        continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
    )
    rows = db_wfseq.get_sequence_items(seq_id) or []
    worker = next((row for row in rows if row.get("item_seq") == worker_item_seq), None)
    candidates = [worker] if worker is not None else []
    if head.get("item_seq") != worker_item_seq:
        candidates.append(head)
    else:
        report = _paired_report_row(rows, head)
        if report is not None:
            candidates.append(report)
    return candidates


def stored_hop_provider(
    doc_ref: str,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return the provider stored on the worker row, falling back to the head row."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None, None, None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None, None, None
        candidates = _hop_worker_rows(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        for row in candidates:
            if row and row.get("provider_id"):
                return (
                    row.get("provider_id"),
                    row.get("provider_display_name"),
                    row.get("item_seq"),
                )
        return None, None, None
    except Exception:  # noqa: BLE001 — stored preference resolution must never stall a hop
        logger.warning("stored continuation hop provider resolution failed for %s", doc_ref,
                       exc_info=True)
        return None, None, None


def _resolve_continuation_hop_override(
    doc_ref: str,
    overrides: dict,
    chain: list[dict],
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[str]:
    """Return the enabled provider override keyed to this mode-aware worker item_seq.

    String JSON keys and integer keys are both accepted. A missing or disabled provider
    silently falls through to the explicit pin / doc-type / default tiers.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        item_seq = _hop_worker_item_seq(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if item_seq is None:
            return None
        provider_id = overrides.get(str(item_seq), overrides.get(item_seq))
        if not provider_id:
            return None
        if not any(p.get("id") == provider_id for p in chain):
            return None
        return provider_id
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop override resolution failed for %s", doc_ref,
                       exc_info=True)
        return None


def _resolve_continuation_hop_note(
    doc_ref: str,
    overrides: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[str]:
    """Return the individual note for the same mode-aware item_seq as the provider override.

    Keeping both resolvers on `_hop_worker_item_seq` preserves the D0004 constraint that a
    visible row's provider and note always address the same hop. Failures degrade to no note.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        item_seq = _hop_worker_item_seq(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if item_seq is None:
            return None
        note = overrides.get(str(item_seq), overrides.get(item_seq))
        if not note or not str(note).strip():
            return None
        return str(note).strip()
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop note resolution failed for %s", doc_ref, exc_info=True)
        return None
