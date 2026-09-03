"""Pure helpers extracted from the ai_invoke assembled namespace (0501 T0006 / T2).

This module owns NO runtime state: no ``_runs``, no locks, no DB, no subprocess, no
network, no logging. Every helper is fully determined by its arguments and has no
side effect. It must never import ``ai_invoke_service`` and must never use
exec()/globals().update() — callers pass in whatever module constants (sentinels,
default values) the original code read directly from the assembled namespace, so
this module stays import-only.

Functions that looked pure but log a warning on bad input (`resolve_review_count`)
were deliberately left in place in `ai_invoke_review.py` (flowgate.default.0501 T5 --
formerly `ai_invoke_part3_chain.py`) rather than moved here
— moving them would either drop that observable behavior or force this module to
own a logger, both of which are bigger changes than a first extraction should make.
"""
import hashlib
import json
from typing import Optional


def map_lookup(overrides: Optional[dict], item_seq: Optional[int]):
    """Both key spellings, exactly as _resolve_continuation_hop_override accepts them."""
    if not overrides or item_seq is None:
        return None
    return overrides.get(str(item_seq), overrides.get(item_seq))


def resolve_round_limit(count: int, no_limit: int) -> int:
    """How many review+rework rounds this step gets; ``no_limit`` = no ceiling.

    -1 is the user asking for "until it passes", and it is taken literally (0414
    0022-TR rejection): there is no round number at which the chain gives up and calls
    a human. Only a `pass`, a `hold`, or a loop breaker ends it.
    """
    return no_limit if count == -1 else int(count)


def review_rounds_remain(rounds_used: int, limit: int, no_limit: int) -> bool:
    """Is another review round allowed? An unbounded budget always says yes."""
    return limit == no_limit or rounds_used < limit


def review_key(value) -> str:
    """One comparable form for a `document_reviews.id` (T0005 2.1.2).

    The column is a positive integer, but it round-trips through the rejection_history
    JSON and can come back as "244". Comparing normalized strings makes 244 and "244"
    one key. Values that cannot identify a review row -- None, bool, an empty or
    whitespace-only string, a non-numeric string -- all fold to "", which matches
    nothing.
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


def review_findings(review: Optional[dict]) -> list:
    """A review row's findings as a list — the column stores a JSON array string."""
    findings = (review or {}).get("findings")
    if isinstance(findings, str):
        try:
            findings = json.loads(findings)
        except (TypeError, ValueError):
            return []
    return findings if isinstance(findings, list) else []


def normalize_ws(value) -> str:
    return " ".join(str(value or "").split())


def review_finding_digest(review: Optional[dict]) -> str:
    """A deterministic fingerprint of one review's findings (L0008 §2.3).

    Whitespace-normalized so a reflowed line is not mistaken for a new complaint. Two
    consecutive `issues` verdicts with the SAME digest mean the rework changed nothing
    the reviewer cares about, which is the practical safety net behind an unbounded -1.
    """
    parts = []
    for finding in review_findings(review):
        if isinstance(finding, dict):
            parts.append(normalize_ws(finding.get("locus")) + "␟"
                         + normalize_ws(finding.get("note")))
        else:
            parts.append(normalize_ws(finding))
    return hashlib.sha256("␞".join(parts).encode("utf-8")).hexdigest()


def prioritize_chain(chain: list[dict], provider_id: str) -> list[dict]:
    """Move the assigned provider to the front, keeping the rest as the fallback tail
    (D0004 §3: assignment beats fallback, but a spawn failure falls through). Unlike an
    explicit UI pin — which collapses the chain to one provider and disables fallback —
    a doc-type assignment only re-orders, so the existing _worker fallback loop still
    protects the run."""
    head = [p for p in chain if p.get("id") == provider_id]
    if not head:
        return chain
    return head + [p for p in chain if p.get("id") != provider_id]
