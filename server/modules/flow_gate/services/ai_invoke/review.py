"""The review gate (0501 NR0003 §12/§19 `review.py`).

Its own subsystem, as §19 says: reviewer/executor selection, the review and rework hop
launch, verdict handling, automatic rejection with the findings text, the loop breakers
that make an unbounded `-1` round budget safe, and the document-scoped review loop's
checkpoint/restore.

Nothing about a review round is stored (L0008 §2.3): rounds used, which stage is
running, whether a rejection already happened are all re-derived from `document_reviews`
plus the document's `revision_no` / `doc_review_status` on every read, which is what
makes a restart, a cold resume and an in-flight hop boundary agree for free.

Import direction (§18/§28): this module must not import `chain`. The handoff primitives
it needs back are reached through the `ai_invoke_service` seam.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.settings import ai_settings_service

from . import oracle
from .runtime import (
    HOP_HANDOFF_FAILED_STOP_CODE,
    REVIEW_COUNT_DEFAULT,
    REVIEW_COUNT_VALUES,
    REVIEW_HOP_KIND,
    REVIEW_NO_VERDICT_STOP_CODE,
    REVIEW_REASON_MAX_CHARS,
    REVIEW_REASON_MAX_FINDINGS,
    REVIEW_REJECT_DENIED_STOP_CODE,
    REVIEW_REJECT_FAILED_STOP_CODE,
    REVIEW_ROUNDS_NO_LIMIT,
    REVIEW_STALLED_STOP_CODE,
    REVIEW_STALL_ROUNDS,
    REVIEW_VERDICTS,
    REVIEW_VERDICT_HOLD_STOP_CODE,
    REWORK_HOP_KIND,
    WORK_HOP_KIND,
    _http_error,
    _known_run_prompts,
    _known_run_raw_tokens,
    _redact_secrets,
    _svc,
    excerpt,
    logger,
)
from .oracle import map_lookup as _map_lookup, normalize_ws as _normalize_ws, review_findings as _review_findings, review_key as _review_key


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
    return oracle.resolve_round_limit(count, REVIEW_ROUNDS_NO_LIMIT)


def review_rounds_remain(rounds_used: int, limit: int) -> bool:
    """Is another review round allowed? An unbounded budget always says yes."""
    return oracle.review_rounds_remain(rounds_used, limit, REVIEW_ROUNDS_NO_LIMIT)


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
        and oracle.review_finding_digest(reviews[0]) == oracle.review_finding_digest(reviews[1])
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
        _svc()._log_review_annotation_failure("read", slot, bundle, exc)
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
        _svc()._log_review_annotation_failure("write", slot, bundle, exc)
        return {"ok": False, "stop_code": REVIEW_REJECT_DENIED_STOP_CODE, "detail": str(exc)}
    if "document.reject" not in permissions:
        detail = "issuer lacks document.reject"
        _svc()._log_review_annotation_failure("write", slot, bundle, detail)
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
        _svc()._log_review_annotation_failure("write", slot, bundle, exc)
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
        _svc()._clear_handoff_row(group_id, run.get("run_id"))
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
    _svc()._park_handoff(run, bundle, stop_code)
    return outcome


def _queue_gate_bundle(group_id: str, bundle: dict) -> None:
    """Record the next hop's intent BEFORE launching it (L0008 §2.4).

    _finalize_run reads this queue to decide between begin_handoff and releasing the group
    lease. Launch first and the lease is gone by the time the successor asks for it, so the
    successor dies on 409 run_in_progress.
    """
    _svc().request_auto_resume(group_id, bundle)


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
    return _svc().start_run(
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

    return _svc().start_run(
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
        result = _svc()._auto_reject(slot, _latest_review_of(slot), bundle)
        if not result.get("ok"):
            run["review_reject_detail"] = result.get("detail")
            _svc()._park_handoff(run, bundle, result["stop_code"])
            return False

    stage = gate.get("stage")
    if stage == "stop":
        _svc()._park_handoff(run, bundle, gate.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE)
        return False

    if stage == WORK_HOP_KIND:
        if gate.get("approve_first") and slot is not None:
            if _svc()._settle_gate_pass(group_id, slot, bundle, run) != "continue":
                return False
        # Deliberately NOT re-queued, unlike the two branches below: _finalize_run already
        # ran begin_handoff for this boundary, and the work hop's own inbox self-chain is
        # what queues the hop after it. Queueing here instead would leave a live entry
        # behind a hop that produced nothing, and the engine would re-spawn it forever
        # rather than stopping on no_output_exhausted.
        _svc()._spawn_auto_resume(group_id, {**bundle, "last_stage": WORK_HOP_KIND})
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
                _svc()._spawn_review_hop(group_id, queued, gate)
            else:
                _svc()._spawn_rework_hop(group_id, queued, gate)
        except Exception:
            _svc().clear_auto_resume(group_id)     # take the intent back out; the caller parks it
            raise
        return True

    return False


def active_review_selection(group_id: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    """This group's live [검수] selection, for the inbox boundary (L0008 §2.8).

    The maps ride the RUN, not the token, so the inbox — which only ever sees a token —
    has to ask the engine. (None, None) when no engine run is driving this group, which is
    also the correct answer: a copy-mention chain has nothing to launch a review hop with.
    """
    run = _svc()._active_run_for_group(group_id)
    if run is None:
        return None, None
    return (
        run.get("continuation_review_count_overrides"),
        run.get("continuation_reviewer_overrides"),
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
    with _svc().get_store().transaction():
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
        rejection = _svc()._auto_reject(slot, latest_review, {
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
