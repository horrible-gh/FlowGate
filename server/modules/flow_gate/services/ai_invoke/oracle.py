"""Completion oracles + pure helpers (0501 NR0003 §12/§16/§21 Phase 1 `oracle.py`).

"Did the work actually land?" -- never an exit code (L0006). Which question that is
depends on what the run's token is allowed to write (0259 B0001): a `new` token
registers documents and is judged by document-reach, every other scope is judged by the
row IT can write, which is what `_SCOPE_PROBES` enumerates -- conversation turn,
document revision, document review, test run, workflow sequence item.

Alongside those probes this module holds the package's stateless helpers: the review
digest/key/findings normalizers and the provider-chain re-ordering that 0501 T2 first
extracted as `ai_invoke_helpers.py`, plus the progress-watchdog primitives
(`_work_landed` / `_observe_group_max_seq` / `_truncate_front`) and the provider-brief
formatter (`_provider_brief`) T6 moved here to break the admission<->worker and
admission<->chain cycles those left behind in their original modules. They live here for
the reason NR0003 §21 gives for extracting the oracle first -- all of them are fully
determined by their arguments (plus, for the watchdog probes, the DB row they read), and
this module is the package's one home for computation that owns no run state.

This module reaches NOTHING through the `ai_invoke_service` compatibility shim: it is
the only member of the package with no seam at all.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Optional

from modules.flow_gate.db import conversation_turns as db_conversation_turns
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import process_runner

from .runtime import (
    LAST_MESSAGE_MAX_BYTES,
    REVIEW_HOP_KIND,
    logger,
)


# ── Source-spill check (L0006 §2.8) ──────────────────────────────────────────

def _git_status_paths(source_root: Optional[Path]) -> Optional[set[str]]:
    """Path set from `git status --porcelain`; None = unknown (git absent/failed)."""
    if source_root is None or not source_root.is_dir():
        return None
    try:
        timed_out, exit_code, output = process_runner.run_command(
            "git status --porcelain", source_root, 30, None
        )
        if timed_out or exit_code != 0:
            return None
        return {line[3:].strip() for line in output.splitlines() if line.strip()}
    except Exception:
        return None


# ── Default completion oracle per token scope (0259 B0001) ───────────────────
#
# Only a `new`-scope token can register a document, so only a `new` run may be judged by
# the document-reach oracle. The inbox rejects `action:'new'` from any other token
# outright (inbox_routes `_handle_new`/`_handle_edit`/`_handle_review` scope guards), so
# "docs_reached >= docs_target=1" was an UNREACHABLE success condition for every other
# single-mode scope — review/edit/rework/vr_correction/chat runs settled 'none' no matter
# how well the worker did. Each scope is judged by the row its own token may write.
#
# 0248 added `completion_oracle` for the same defect but left it opt-in per call site, and
# the very call sites it cited were never migrated. The default now lives in the engine and
# is keyed by the scope, so a scope that registers no judge cannot silently inherit the
# document oracle; `completion_oracle` stays as the per-call override.

def _probe_conversation_head(doc_id: str) -> int:
    """Highest stored turn seq for an append-only chat run."""
    return db_conversation_turns.current_head_seq(doc_id)


def _probe_doc_revision(doc_id: str) -> int:
    """Revisions of the bound document. `_handle_edit` does `revision_no = revision_no + 1`."""
    return int((db_docs.get_by_id(doc_id) or {}).get("revision_no") or 0)


def _probe_doc_reviews(doc_id: str) -> int:
    """Review rows on the bound document. `_handle_review` INSERTs one child row per review."""
    return len(db_reviews.list_by_doc(doc_id) or [])


def _probe_test_runs(doc_id: str) -> int:
    """Test-run rows on the bound document (0268 B0001).

    A `test_run` token may only POST /documents/test-run, which INSERTs one run row for the
    TS it is bound to — it can never register a document, so the document oracle would make
    success unreachable here exactly as 0259 B0001 described for edit/review.
    """
    return len(db_test_runs.list_by_doc(doc_id) or [])


def _probe_sequence_max_item(doc_id: str) -> int:
    """Highest item_seq in the bound document's workflow sequence (0268 B0001).

    A `workflow_sequence_edit` worker calls PATCH /workflow/sequence, which registers no
    document — so, like edit/review, the document oracle could never credit it. The probe
    is the sequence's max item_seq because `edit_workflow_pending` deletes the pending tail
    and re-inserts at `max_item_seq + 1`: a plain count could FALL across a valid shrink,
    but the max is strictly monotonic across any edit that inserts.

    Known limitation, deliberate: shrinking a sequence to locked-steps-only inserts nothing,
    so that one edit settles 'none'. A false negative on a rare edit is the safer error here
    than the false POSITIVE the alternative gives — with no probe at all, docs_target 0 makes
    `docs_reached >= docs_target` trivially true and a worker that did NOTHING reports
    'complete', which is the "verdict renamed instead of judged" failure 0259 B0001 fixed.
    """
    try:
        seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    except Exception:  # noqa: BLE001
        return 0
    if seq is None:
        return 0
    return int(db_wfseq.get_max_item_seq(seq["id"]) or 0)


def _probe_base_dirty(project_id: str) -> int:
    """Negative tracked-dirty count: each resolved file strictly increases it."""
    from modules.flow_gate.services import git_service
    status = git_service.project_git_status(project_id).get("status") or {}
    files = (status.get("base_dirty") or {}).get("files") or []
    return -len(files)


# Keyed by TOKEN scope — the value `start_run` actually receives. Chat is no longer
# remapped to edit: its append-only endpoint advances the conversation head without
# revising the document row, so it needs its own completion probe.
_SCOPE_PROBES: dict[str, Callable[[str], int]] = {
    "chat": _probe_conversation_head,
    "edit": _probe_doc_revision,
    "review": _probe_doc_reviews,
    "test_run": _probe_test_runs,
    "workflow_sequence_edit": _probe_sequence_max_item,
    "resolve_base_dirty": _probe_base_dirty,
}


def _oracle_doc_id(token_id: Optional[str], fallback: str) -> str:
    """The document the run's TOKEN binds to — the only one its worker may write.

    Not the run's own doc_ref: the two differ on the legacy Q&A follow-up, which starts the
    run on the Q document while `qa_service.issue_followup_token` binds the token to the
    parent work item. The inbox honours the token (`token_rec['doc_ref'] != doc_id` ⇒ 403),
    so a judge that watched the run's doc_ref would watch a document the worker cannot touch.
    """
    if not token_id:
        return fallback
    try:
        token = db_tokens.get_by_id(token_id)
    except Exception:
        logger.warning("ai-invoke token lookup failed for %s", token_id, exc_info=True)
        return fallback
    return (token or {}).get("doc_ref") or fallback


def _probe(probe: Callable[[str], int], doc_id: str) -> Optional[int]:
    try:
        return probe(doc_id)
    except Exception:
        logger.warning("ai-invoke scope probe failed for %s", doc_id, exc_info=True)
        return None


def _scope_oracle(action_scope: str, token_id: Optional[str], doc_ref: str) -> Optional[Callable[[], bool]]:
    """The scope's default "did the work land?" predicate, or None to keep the document oracle.

    Returning None here means `new` (and any future document-producing scope): those are
    judged by documents, which is what the document oracle is for.
    """
    probe = _SCOPE_PROBES.get(action_scope)
    if probe is None:
        return None
    doc_id = _oracle_doc_id(token_id, doc_ref)
    # Baseline BEFORE the worker starts, so the oracle only credits this run's work.
    baseline = _probe(probe, doc_id)

    def _oracle() -> bool:
        current = _probe(probe, doc_id)
        # An unresolvable baseline/probe cannot confirm the work landed. This is not the
        # old unreachable case — a missing target means the worker had nothing it could
        # write, and the inbox would have refused it too.
        return baseline is not None and current is not None and current > baseline

    return _oracle


def _uses_scope_oracle(action_scope: str, mode: str, completion_oracle: Optional[Callable]) -> bool:
    """mode='single' only: a continuous run's scope is new/workflow_decide, and its
    docs_target is derived from the sequence's pending worker items, which do make
    documents — the document oracle can see those, so it was never wrong for them."""
    return completion_oracle is None and mode == "single" and action_scope in _SCOPE_PROBES


def _scope_oracle_retry_open(mode: Optional[str], action_scope: Optional[str],
                             scope_oracle_run: Optional[bool]) -> bool:
    """0446 T0008 §3-2: may this SINGLE run use the no-output recovery machinery?

    NR0003 measured 27 of 242 post-rejection rework runs ending with nothing registered,
    and found the cause structural rather than incidental: retry, stop code and failure
    notification all live inside `mode == "continuous"`, while rework is always `mode="single"`
    carrying an `edit` token.

    Two deliberate narrowings:
      * `scope_oracle_run` — the ENGINE planted this run's judge (`_scope_oracle`). A
        caller-supplied `completion_oracle` override (0248 B0001, the legacy Q&A follow-up in
        `q_answer_invoke_service`) keeps the old block: its success criterion is defined
        outside the engine, so the engine must not re-ask or re-run it.
      * `edit` only — `_SCOPE_PROBES` also holds chat / review / test_run /
        workflow_sequence_edit, but NR0003's measurement is the 264 edit/single runs. Opening
        the others would move the 0259/0268 judging contract with nothing behind it.
    """
    return bool(scope_oracle_run) and mode == "single" and action_scope == "edit"


def _review_hop_recovery_open(mode: Optional[str], action_scope: Optional[str],
                              scope_oracle_run: Optional[bool],
                              hop_kind: Optional[str]) -> bool:
    """flowgate.default.0466 T0007 §3.1.1: may an ENGINE-spawned review hop with no verdict
    reopen a second attempt in the SAME round?

    Deliberately a second, narrower predicate rather than a widening of
    `_scope_oracle_retry_open`'s edit-only condition (T0007 explicitly forbids that).
    `hop_kind == REVIEW_HOP_KIND` is the whole narrowing: `start_run`'s `hop_kind` parameter
    defaults to `WORK_HOP_KIND`, and `_spawn_review_hop` is the ONLY caller anywhere in the
    codebase that ever passes `REVIEW_HOP_KIND` — a person's plain single review call
    (`POST /ai-invoke` with action_scope='review') never does, so it stays one-shot exactly
    as before. (`chain_id` is NOT part of this predicate: `start_run` defaults every
    single-mode run's chain_id to its own run_id — `chain_id = chain_id or run_id` — so a
    plain single call already carries a non-empty chain_id and checking it would narrow
    nothing.)
    """
    return (
        bool(scope_oracle_run)
        and mode == "single"
        and action_scope == "review"
        and hop_kind == REVIEW_HOP_KIND
    )


def _scope_oracle_retry_run(run: dict) -> bool:
    """The same question asked of a live run dict — `scope_oracle_run` rides on it (§3-1).

    0466 T0007 §3.1.1: ORs in `_review_hop_recovery_open` above, so every no-output-retry
    consumer (`_retry_eligible`, `_recheck_no_output`, the docs_target=0 guard) treats an
    ENGINE-spawned review hop's no-verdict outcome exactly like a scope-oracle edit/rework
    run — same recheck-before-retry, same "output is output" guard — without duplicating any
    of that machinery for review specifically.
    """
    return _scope_oracle_retry_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run")
    ) or _review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
    )


def _review_hop_recovery_run(run: dict) -> bool:
    """`_review_hop_recovery_open` asked of a live run dict, standalone (T0007 §2.3/§3.1.5) —
    used where the caller must tell a review-hop recovery apart from an edit/rework
    scope-oracle retry (they resolve their retry PROVIDER differently)."""
    return _review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
    )


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


def _provider_brief(provider: Optional[dict]) -> Optional[dict]:
    if not provider:
        return None
    from modules.flow_gate.services.provider_capability_service import provider_capabilities
    return {
        "id": provider.get("id"), "name": provider.get("name"),
        "exec_type": provider.get("exec_type"), "kind": provider.get("kind"),
        "capabilities": provider_capabilities(provider),
    }


def _work_landed(run: dict) -> bool:
    """Did this run already produce something? Fast-fail's "nothing was lost" check.

    0259 B0001 §3: this used to be a raw group max-seq delta for every run. On a run whose
    product is not a document that is False however well the worker did, so a worker that
    finished its edit and then exited nonzero inside the fast-fail window was re-run on the
    next provider. Ask the run's own judge — the scope default or the caller's override —
    and only fall back to the seq delta for the document-producing scopes it is true for.

    NOTE this is deliberately NOT `_oracle_new_docs` (non-draft docs past the baseline):
    the seq delta is the wider net, and counting a stray draft here only makes fast-fail
    more conservative, which is the safe direction for a "may I discard this attempt?"
    question.
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
