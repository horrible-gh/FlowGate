"""Common AI-run provider provenance for every persistent AI result (0582 T0005).

The AI review path (document_reviews / inbox_routes._review_provenance) already
snapshots the run's requested/actual provider at the moment a review is written, and
that snapshot stays the past-tense fact even after the provider registry later renames
or removes the entry. This module generalizes that lookup so every other AI-authored
persistent result (rejection rework response, in-app Q&A) can pull the SAME evidence
from the SAME source of truth -- the ai-invoke run record the writer's token is bound
to -- instead of a fresh per-screen provider field with its own guessing rules.
"""
from __future__ import annotations

from typing import Any, Optional


def effective_action_scope(run: dict[str, Any]) -> Optional[str]:
    """The run's CURRENT stage, expressed in `action_scope` vocabulary (0582 TR0006 rev1).

    A document_review_loop hop rewrites `run["hop_kind"]` in place as it alternates
    stages -- worker.py's in-process transition sets `run["hop_kind"] = loop["current_stage"]`
    and a fresh `run["provider_id"]` on every stage switch -- while `run["action_scope"]`
    stays pinned to whichever scope the run was FIRST admitted under, for the run's whole
    lifetime (worker.py's own comment: "a document_review_loop hop's reissued token can
    carry a DIFFERENT action_scope than the run started with (review <-> edit as the loop
    alternates stages)"). A loop that starts in "review" and is still on run_id=X when it
    reaches its rework hop reports `action_scope == "review"` there too; the reverse is true
    for a loop that starts in "rework". Filtering on action_scope alone is therefore blind
    to every stage after the run's first one -- exactly what nulled out
    inbox_routes._review_provenance / the rejected-response snapshot on a loop's second hop
    onward. `hop_kind` is the live per-hop signal and takes priority; a plain, non-loop run
    never sets it away from the "work" default, so it falls back to action_scope unchanged.
    """
    hop_kind = run.get("hop_kind")
    if hop_kind == "review":
        return "review"
    if hop_kind == "rework":
        return "edit"
    return run.get("action_scope")


def resolve_run_provenance(
    ai_run_id: Optional[str],
    *,
    doc_id: Optional[str] = None,
    allowed_action_scopes: Optional[tuple[str, ...]] = None,
    run: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Server-owned provider evidence for one AI-authored persistent result.

    The three states of the evidence are decided ONLY from the verified ai-invoke run
    record this token/run is bound to -- never from a request payload, never from the
    project's current provider settings:

    ==========================================  ==========
    evidence in the verified run                 API reads
    ==========================================  ==========
    both provider ids present and different      fallback_used=true
    both present and equal                        fallback_used=false
    anything else                                 {} (every column null)
    ==========================================  ==========

    "Anything else" is: no run id, a run lookup that failed or found nothing, a run whose
    ``effective_action_scope`` (its live hop_kind-aware stage, not necessarily its
    admission-time action_scope -- see that function) is not in ``allowed_action_scopes``
    (when given), a run bound to a different document than ``doc_id`` (when given), or a
    run missing either provider id (a legacy/non-AI token, an external/unconfirmed path).
    All of them return ``{}`` so
    every provenance field stays NULL: this is "no evidence to decide", a different claim
    from "we checked, no fallback happened", and callers must not collapse the two.

    Lookup failures degrade gracefully (empty provenance) rather than failing whatever
    write or read triggered the lookup: provenance is evidence about a real result, not
    the result itself.

    On success returns {"ai_run_id", "requested_provider_id", "actual_provider_id",
    "actual_provider_name", "provider_source", "attempt_no", "fallback_used"}.
    """
    if not ai_run_id:
        return {}
    try:
        if run is None:
            from modules.flow_gate.services.ai_invoke.runtime import get_run_record
            run = get_run_record(ai_run_id)
        if not run:
            return {}
        if allowed_action_scopes is not None and effective_action_scope(run) not in allowed_action_scopes:
            return {}
        if doc_id is not None and run.get("doc_ref") != doc_id:
            return {}
        requested_id = run.get("requested_provider_id")
        actual_id = run.get("provider_id")
        if not requested_id or not actual_id:
            return {}
        fallback_used = requested_id != actual_id
        return {
            "ai_run_id": ai_run_id,
            "requested_provider_id": requested_id,
            "actual_provider_id": actual_id,
            "actual_provider_name": (run.get("provider") or {}).get("name"),
            "provider_source": (
                "fallback" if fallback_used else run.get("selected_provider_source")
            ),
            "attempt_no": int(run.get("attempt_no") or 0) or None,
            "fallback_used": fallback_used,
        }
    except Exception:
        return {}


def to_api_payload(snapshot: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The canonical public shape (0582 T0005 SS6): {ai_run_id, ai_provider_id, ai_provider_name}.

    None when there is no evidence at all, so a legacy row or an external/unconfirmed
    path (e.g. a [Copy Mention] answer, where FlowGate never started a run) can render
    an explicit "external/unconfirmed" label instead of a fabricated provider name.
    """
    if not snapshot:
        return None
    provider_id = snapshot.get("actual_provider_id")
    provider_name = snapshot.get("actual_provider_name")
    run_id = snapshot.get("ai_run_id")
    if not provider_id and not provider_name and not run_id:
        return None
    return {
        "ai_run_id": run_id,
        "ai_provider_id": provider_id,
        "ai_provider_name": provider_name,
    }
