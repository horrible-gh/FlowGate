"""Orphan judgement with a recovery-evidence guard (flowgate.v02.0153 T#4, T0011 §3).

Pure functions only: nothing here runs Git or touches the DB. The executor
(``cleanup._judge_orphan`` and the TTL path ``cleanup._auto_abort_session``) gathers the
facts under the session lock — fresh row, read-only disk reads — and performs only what
the verdict says.

A session that carries evidence of a human-approved resolution (a merge/publish commit,
preservation refs, reconcile history or a reconcile hand-off) is never closed as an orphan
merely because its base checkout has no MERGE_HEAD: it is resumed into the T#3 reconcile
state machine when every resume condition holds, and otherwise parked in an explicit
recovery hold (``recovery_hold``) for a person (T#5). Only a session with no evidence keeps
the old orphan close (``_close_orphan`` → ``orphan_recovered``).

"No evidence" must be an observed fact, not a failed read (review rej_01M6ZWFPF1E6JE2M): when
a disk read that could carry evidence or contradict "no MERGE_HEAD" failed (the preservation
ref listing, the MERGE_HEAD read; a non-zero exit or a timeout), the facts name it in
``unobserved`` and the verdict is ``unobserved`` — nothing is written, closed, discarded or
aborted, and the next cycle reads again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import approval_intent
from . import preserve

# ── evidence (§3.1, fixed order) ─────────────────────────────────────────────

EV_MERGE_COMMIT = "merge_commit"
EV_PUBLISH_COMMIT = "publish_commit"
EV_PRESERVE_REF = "preserve_ref"
EV_PRESERVE_REF_ON_DISK = "preserve_ref_on_disk"
EV_RECONCILING = "reconciling"
EV_RECONCILE_HISTORY = "reconcile_history"
EV_RECONCILE_HANDOFF = "reconcile_handoff"
EVIDENCE_ORDER = (
    EV_MERGE_COMMIT, EV_PUBLISH_COMMIT, EV_PRESERVE_REF, EV_PRESERVE_REF_ON_DISK,
    EV_RECONCILING, EV_RECONCILE_HISTORY, EV_RECONCILE_HANDOFF,
)

# ── verdicts (§3.1, first matching line wins) ────────────────────────────────

NOT_ORPHAN = "not_orphan"
DELEGATE = "delegate"
HOLD_KEEP = "hold_keep"
CLOSE_ORPHAN = "close_orphan"
RESUME_RECONCILE = "resume_reconcile"
HOLD = "hold"
UNOBSERVED = "unobserved"
VERDICTS = (NOT_ORPHAN, DELEGATE, HOLD_KEEP, UNOBSERVED, CLOSE_ORPHAN, RESUME_RECONCILE, HOLD)

# disk reads whose failure leaves the verdict undecidable (Facts.unobserved)
OBS_MERGE_HEAD = "merge_head"
OBS_PRESERVE_REFS = "preserve_refs"

# review states the guard never judges: they belong to the existing sweep branches
STATE_APPLYING = "applying"
STATE_RECONCILING = "reconciling"
STATE_COMPLETED = "completed"
DELEGATE_STATES = (STATE_RECONCILING, STATE_APPLYING, STATE_COMPLETED)

# context keys / record shapes (§3.2, §3.3)
HOLD_KEY = "recovery_hold"
RESUMED_KEY = "recovery_resumed"
HOLD_CODE = "orphan_evidence_hold"
RESUME_KIND = "push_remote_unknown"
RESUME_ERROR_CODE = "orphan_resumed"
TRIGGER_SWEEP_ORPHAN = "sweep_orphan"
TRIGGER_STARTUP_ORPHAN = "startup_orphan"
TRIGGER_SWEEP_TTL = "sweep_ttl"
TRIGGERS = (TRIGGER_SWEEP_ORPHAN, TRIGGER_STARTUP_ORPHAN, TRIGGER_SWEEP_TTL)

# resume conditions (§3.1 line 5), reported by name when one is missing
RC_SHAS = "merge_commit_and_expected_remote_head"
RC_PRESERVE = "preserve_verified"
RC_HEAD = "head_is_publish_commit"
RC_NO_MERGE_HEAD = "no_merge_head"
RC_CLEAN = "worktree_clean"
RC_ROOT = "base_root_exists"
RC_INTENT = "approval_intent"
RESUME_CONDITIONS = (RC_SHAS, RC_PRESERVE, RC_HEAD, RC_NO_MERGE_HEAD, RC_CLEAN, RC_ROOT, RC_INTENT)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def is_sha40(value) -> bool:
    return isinstance(value, str) and bool(_SHA40.match(value))


def _nonempty(value) -> bool:
    return bool(value)


def preserve_refs_of(merge_id: int, refnames: Iterable[str]) -> list[str]:
    """The ``refs/flowgate/preserve/<id>[-r<N>][-i<k>]`` names that belong to ``merge_id``
    (parsed with ``preserve.parse_preserve_ref`` / ``parse_integration_ref``)."""
    out = []
    for name in refnames or ():
        name = (name or "").strip()
        parsed = preserve.parse_preserve_ref(name) or preserve.parse_integration_ref(name)
        if parsed is not None and parsed[0] == int(merge_id):
            out.append(name)
    return out


def recovery_evidence(context: Optional[dict], *, preserve_refs_on_disk: list[str]) -> list[str]:
    """The names of the evidence items that are true, in :data:`EVIDENCE_ORDER`.

    ``preserve_refs_on_disk`` must already be filtered to this session's merge id
    (:func:`preserve_refs_of`)."""
    ctx = context if isinstance(context, dict) else {}
    found = {
        EV_MERGE_COMMIT: is_sha40(ctx.get("merge_commit")),
        EV_PUBLISH_COMMIT: is_sha40(ctx.get("publish_commit")) or is_sha40(ctx.get("published_commit")),
        EV_PRESERVE_REF: (
            _nonempty(ctx.get("preserve_ref")) or _nonempty(ctx.get("preserve_sha"))
            or _nonempty(ctx.get("preserve_history")) or _nonempty(ctx.get("integration_commits"))
        ),
        EV_PRESERVE_REF_ON_DISK: bool(preserve_refs_on_disk),
        EV_RECONCILING: ctx.get("review_state") in (STATE_RECONCILING, STATE_APPLYING),
        EV_RECONCILE_HISTORY: (
            _attempts(ctx.get("reconcile_attempt_count")) > 0 or _nonempty(ctx.get("reconciliation_kind"))
        ),
        EV_RECONCILE_HANDOFF: _handoff(ctx),
    }
    return [name for name in EVIDENCE_ORDER if found[name]]


def _attempts(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _handoff(ctx: dict) -> bool:
    baseline = ctx.get("resolver_baseline")
    source = baseline.get("source") if isinstance(baseline, dict) else None
    return (isinstance(source, str) and source.startswith("reconcile_")) or _nonempty(
        ctx.get("resolver_baseline_history"))


# ── verdict ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Facts:
    """What the executor observed under the session lock (fresh row, read-only Git)."""
    context: dict
    evidence: tuple = ()
    merge_head_present: bool = False
    base_root_exists: bool = False
    head_sha: Optional[str] = None
    worktree_clean: bool = False
    preserve_verified: bool = False
    hold_present: bool = False
    # names of the reads that failed (OBS_*): "not seen" is not "absent"
    unobserved: tuple = ()


@dataclass(frozen=True)
class Verdict:
    action: str
    # HOLD: the resume conditions that failed; UNOBSERVED: the reads that failed
    missing: tuple = field(default=())


def resume_missing(facts: Facts) -> tuple:
    """The resume conditions (§3.1 line 5) that do not hold, in fixed order."""
    ctx = facts.context or {}
    merge_commit = ctx.get("merge_commit")
    publish = ctx.get("publish_commit") if is_sha40(ctx.get("publish_commit")) else merge_commit
    checks = {
        RC_SHAS: is_sha40(merge_commit) and is_sha40(ctx.get("expected_remote_head")),
        RC_PRESERVE: bool(facts.preserve_verified),
        RC_HEAD: is_sha40(facts.head_sha) and facts.head_sha == publish,
        RC_NO_MERGE_HEAD: not facts.merge_head_present,
        RC_CLEAN: bool(facts.worktree_clean),
        RC_ROOT: bool(facts.base_root_exists),
        RC_INTENT: approval_intent.intent_of_context(ctx) is not None,
    }
    return tuple(name for name in RESUME_CONDITIONS if not checks[name])


def orphan_verdict(facts: Facts) -> Verdict:
    """§3.1: the first matching line decides. Line 3b (review rej_01M6ZWFPF1E6JE2M): a
    failed read of the MERGE_HEAD or of the preservation refs decides nothing — neither a
    close (the refs may be the only evidence) nor a resume/hold — and writes nothing."""
    ctx = facts.context or {}
    if facts.merge_head_present:
        return Verdict(NOT_ORPHAN)
    if ctx.get("review_state") in DELEGATE_STATES:
        return Verdict(DELEGATE)
    if facts.hold_present or isinstance(ctx.get(HOLD_KEY), dict):
        return Verdict(HOLD_KEEP)
    if facts.unobserved:
        return Verdict(UNOBSERVED, tuple(facts.unobserved))
    if not facts.evidence:
        return Verdict(CLOSE_ORPHAN)
    missing = resume_missing(facts)
    if not missing:
        return Verdict(RESUME_RECONCILE)
    return Verdict(HOLD, missing)


# ── records ──────────────────────────────────────────────────────────────────

def hold_record(facts: Facts, *, trigger: str, at: str, instance_id: Optional[str]) -> dict:
    """The ``recovery_hold`` value (§3.3)."""
    ctx = facts.context or {}
    return {
        "code": HOLD_CODE,
        "trigger": trigger,
        "evidence": list(facts.evidence),
        "observed": {
            "merge_head": bool(facts.merge_head_present),
            "base_root_exists": bool(facts.base_root_exists),
            "head": facts.head_sha if is_sha40(facts.head_sha) else None,
            "worktree_clean": bool(facts.worktree_clean),
        },
        "review_state_at_hold": ctx.get("review_state"),
        "at": at,
        "instance_id": instance_id,
    }


def apply_resume(context: dict, *, evidence: list, at: str, instance_id: Optional[str]) -> dict:
    """§3.2: turn the fresh context into a due ``reconciling/push_remote_unknown`` session.

    The cumulative ``reconcile_attempt_count`` and ``reconcile_cycle_count`` are left alone
    (the reconcile counts its own cycles). An existing ``last_error`` is carried under
    ``prior_push_error`` of a new ``orphan_resumed`` marker (T#2 rule), so a later T#3 error
    still finds it through ``_prior_push_error``."""
    from_state = context.get("review_state")
    context["review_state"] = STATE_RECONCILING
    context["reconciliation_kind"] = RESUME_KIND
    context["reconcile_next_at"] = at
    if not context.get("publish_commit"):
        context["publish_commit"] = context.get("merge_commit")
    if not context.get("publish_lease"):
        context["publish_lease"] = context.get("expected_remote_head")
    prior = context.get("last_error")
    if prior:
        context["last_error"] = {"code": RESUME_ERROR_CODE, "prior_push_error": prior,
                                 "at": at, "instance_id": instance_id}
    context[RESUMED_KEY] = {"from_review_state": from_state, "evidence": list(evidence),
                            "at": at, "instance_id": instance_id}
    return context
