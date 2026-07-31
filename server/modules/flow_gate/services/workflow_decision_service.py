"""Workflow decision (decide) + advance-to-next-step service (T301 — R016 T-C).

P002 §2 decision save + P002 §3 advance-to-next-step.
No auto-mode. No auto_advance flag (R016 correction).
doc_class (R/Q/B) input + response.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services import mention_service

# ── Auto report attachment ────────────────────────────────────────────────────────
# Single source of truth for "every instruction step carries its report step" on the
# decision path. Both callers of /workflow/decide funnel through decide_workflow():
#   - the client WorkflowDecisionModal (already expands via its own AUTO_MAP), and
#   - an AI worker that POSTs a bare sequence directly (NR0006: previously omitted the
#     reports entirely because the expansion only existed in the client).
# Mirrors the client AUTO_MAP (N→NR, T→TR, TS→TSR). V→VR is intentionally excluded: VR
# is not a registered document_type, so attaching it would create an unprocessable step.
AUTO_REPORT_MAP = {"N": "NR", "T": "TR", "TS": "TSR"}

_log = logging.getLogger(__name__)

# ── Continuous-chain instruction auto-completion (group 0092 B0001 / NR0003) ────────
# Instruction-series steps ("무엇을 하라": N/T) are fillable from a fixed server template
# and carry no AI deliverable — only their paired report (NR/TR) does. In the unmanned
# continuous chain we therefore auto-create + auto-approve any instruction head server-side
# (reusing documents.create_next_approved_core, the exact managed "자동승인문서" mechanics)
# instead of spending an AI worker cycle minting + processing a mention for it. The worker
# mention is then issued only at the following report head. This removes the redundant
# instruction cycles per R→…→TR lap that caused B0001's "token двойной/two-fold" symptom.
#
# TS (테스트시나리오 지시) is INTENTIONALLY EXCLUDED here (group 0121 R0001): unlike N/T, a
# test-scenario directive carries meaningful, deliverable content that the AI must author
# itself, so TS must be token-issued — when the head reaches TS the auto-complete loop stops
# and advance_workflow mints a worker token+mention for it (the AI writes TS, it is then
# auto-approved on submit like any non-{M,CH} doc, and the chain proceeds to TSR). Note this
# differs from AUTO_REPORT_MAP, which still pairs TS→TSR for sequence STRUCTURE (TS remains a
# decided step whose report is auto-attached) — only the auto-APPROVAL of TS is removed.
# DS likewise excluded — it is neither here nor in AUTO_REPORT_MAP.
INSTRUCTION_AUTO_TYPES = {"N", "T"}
CONTINUATION_INSTRUCTION_AUTO_APPROVED = "auto_approved"
CONTINUATION_INSTRUCTION_AI_DIRECT = "ai_direct"
CONTINUATION_INSTRUCTION_MODES = {
    CONTINUATION_INSTRUCTION_AUTO_APPROVED,
    CONTINUATION_INSTRUCTION_AI_DIRECT,
}


def normalize_continuation_instruction_mode(mode: Optional[str]) -> str:
    """Return the continuous-chain N/T handling mode, preserving legacy auto behavior."""
    if mode in CONTINUATION_INSTRUCTION_MODES:
        return mode
    return CONTINUATION_INSTRUCTION_AUTO_APPROVED

# ── Continuous-work "run to the end" sentinel (group 0086 R0001) ────────────────────
# A continuous run started BEFORE the workflow is decided ("워크플로 결정부터") cannot name
# a concrete target item_seq — the sequence does not exist yet. The workflow_decide token
# carries this sentinel as its continuation_target_seq; once the decision is saved the
# sentinel is resolved to the last item_seq of the freshly-decided sequence and a normal
# continuation chain (concrete target) takes over from the first step. Negative so it can
# never collide with a real item_seq (which start at 1).
CONTINUATION_TO_END = -1


# ── Corrupted-label guard (group 0114 B0001 / NR0003) ──────────────────────────────
# NR0003 root cause: instruction-step labels (N/T/TS) are user/AI submitted verbatim and
# can arrive already mangled by a lossy submission environment — Korean encoded through an
# ascii-replace path (Windows cp932 console / encode('ascii', errors='replace')), so each
# Hangul glyph becomes a single ASCII '?' (0x3F). The server/DB store the value faithfully,
# so the corruption surfaces in the ContinuousWorkDialog as a run of "?????". Auto-report
# labels are immune because they come from get_type_name() (a DB lookup), not the payload.
#
# We treat a label as corrupted when it is pure ASCII (a real Hangul label is multibyte) and
# the '?' characters dominate its visible content. When that happens we fall back to the
# document type's display name — graceful degradation that is always meaningful and never
# worse than the glyph-less "?????" the user reported. A normal ASCII label that merely ends
# in a question mark ("Done?") stays untouched because '?' does not dominate.
_CORRUPT_MIN_MARKS = 2
_CORRUPT_RATIO = 0.5


def _label_is_corrupted(label: Optional[str]) -> bool:
    """True when ``label`` looks like ascii-replace mojibake (Hangul lost to '?')."""
    if not label:
        return False
    if not label.isascii():
        return False  # real multibyte text survived — not the ascii-replace signature
    marks = label.count("?")
    if marks < _CORRUPT_MIN_MARKS:
        return False
    visible = len(label.replace(" ", ""))
    if visible == 0:
        return False
    return marks / visible >= _CORRUPT_RATIO


def _safe_label(label: Optional[str], type_: Optional[str], locale: str = "ko") -> str:
    """Return ``label`` unless it is corrupted, in which case the type display name."""
    if _label_is_corrupted(label):
        fallback = get_type_name((type_ or "").upper(), locale)
        if fallback:
            return fallback
    return label or ""


def _expand_auto_reports(sequence: list[dict], locale: str = "ko") -> list[dict]:
    """Ensure every instruction step is immediately followed by its report step.

    Idempotent: a sequence that already places the report right after the instruction
    (as the client modal submits) passes through unchanged; a bare sequence (as an AI
    worker submits) gets the report steps inserted. Inserted items are labeled from the
    document type table, and the whole list is renumbered so ``id`` stays contiguous.
    """
    expanded: list[dict] = []
    for idx, item in enumerate(sequence):
        expanded.append(dict(item))
        report_type = AUTO_REPORT_MAP.get((item.get("type") or "").upper())
        if not report_type:
            continue
        nxt = sequence[idx + 1] if idx + 1 < len(sequence) else None
        if nxt is not None and (nxt.get("type") or "").upper() == report_type:
            continue  # report already attached (e.g. client modal) — don't duplicate
        expanded.append({"type": report_type, "label": get_type_name(report_type, locale)})
    for new_id, item in enumerate(expanded, start=1):
        item["id"] = new_id
    return expanded


# ── Workflow decision save ────────────────────────────────────────────────────────

def decide_workflow(doc_id: str, doc_class: str, sequence: list[dict]) -> dict:
    """Save the workflow decision (one-time initial call).

    Parameters
    ----------
    doc_id : str
        Target document canonical ID (e.g. R016)
    doc_class : str
        Classification (R / Q / B)
    sequence : list[dict]
        Sequence item list (P002 §2-1 format)

    Returns
    -------
    dict
        P002 §2-2 response format

    Raises
    ------
    LookupError
        "doc_not_found:{doc_id}" — document not found
    ValueError
        "already_decided:{doc_id}" — sequence already decided
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")

    existing = db_wfseq.get_sequence_by_doc_id(doc_id)
    if existing is not None:
        raise ValueError(f"already_decided:{doc_id}")

    # Attach report steps (NR/TR/TSR) so the AI decision path matches the client modal.
    sequence = _expand_auto_reports(sequence)

    store = get_store()
    with store.transaction():
        db_wfseq.insert_sequence(doc_id)
        seq_row = db_wfseq.get_sequence_by_doc_id(doc_id)
        seq_id = seq_row["id"]
        for idx, item in enumerate(sequence):
            db_wfseq.insert_sequence_item(
                sequence_id=seq_id,
                item_seq=item["id"],
                type_=item["type"],
                # NR0003 §7-2: sanitize a corrupted submission label before it is persisted,
                # so the recurring "?????" rows (06-11 → 06-19 → 06-22) stop accumulating.
                label=_safe_label(item["label"], item["type"]),
                doc_class=doc_class,
                sort_order=idx,
            )

    db_documents.update(
        doc_id,
        {
            "workflow_steps": _json.dumps([item["type"] for item in sequence]),
            "doc_review_status": "wf_in_progress",
        },
    )

    # R0001 group 0125 / NR0003 권고 1: record an explicit "시작" signal now that the document
    # entered wf_in_progress. This feeds the dashboard state board only (get_work_state_summary);
    # it is intentionally NOT a notification-feed event. Never let an event-log failure break the
    # decision itself — the workflow is already persisted above.
    try:
        from modules.flow_gate.workflow import event_logger as _event_logger

        _event_logger.log_work_started(
            project_id=doc["project_id"],
            actor_user_id=doc.get("owner_id") or "system",
            document_id=doc["id"],
            doc_id=doc_id,
            group_id=doc.get("group_id"),
        )
    except Exception:  # noqa: BLE001 — state-board signal is best-effort, never fatal
        _log.warning("work_started signal failed for %s", doc_id, exc_info=True)

    # 0115 H1: a git-integrated project gets its group branch + worktree provisioned
    # right after the decision is recorded (L0006 §2.4). Runs on a background thread
    # (a first-time clone is network-bound) and reports via git_worktree_ready/failed
    # SSE; H2 (worker-token grant creation) re-guarantees it before any source access.
    # Non-integrated projects: ensure_worktree is a strict no-op.
    try:
        if doc.get("project_id") and doc.get("group_id"):
            from modules.flow_gate.services import git_service as _git_service

            _git_service.ensure_worktree_async(
                doc["project_id"],
                doc.get("module") or "default",
                doc["group_id"],
            )
    except Exception:  # noqa: BLE001 — provisioning must never break the decision
        _log.warning("git worktree hook failed for %s", doc_id, exc_info=True)

    head = db_wfseq.get_effective_head(seq_id)
    head_out = None
    if head:
        head_out = {
            "id": head.get("id"),
            "type": head.get("type"),
            "label": head.get("label"),
        }

    return {
        "status": "decided",
        "doc_id": doc_id,
        "sequence_count": len(sequence),
        "head": head_out,
    }


# ── Continuous-chain instruction auto-completion (group 0092 B0001 / NR0003) ────────

def _auto_complete_instruction_heads(
    *,
    spine_doc: dict,
    seq: dict,
    actor_user_id: str,
    locale: str,
    target_seq: Optional[int],
) -> int:
    """Server-side fill any instruction-series head (N/T/TS) so the worker never sees it.

    Loops while the effective head is an instruction type: create + approve it via
    ``documents.create_next_approved_core`` (the same mechanics as the managed "자동승인문서"
    button) so the head advances to its paired report step. Stops at the first
    report/non-instruction head — which the caller (advance_workflow) then mints the worker
    token + mention for. Returns the number of instruction steps auto-completed.

    Permission source = the SAME resolver the live approve button and the inbox self-chain
    use (workflow._get_user_permissions, the is_admin stub), not permission_service (which
    returns ∅ on the live system with unpopulated RBAC tables — the bug fixed in 0086). If
    the actor genuinely lacks document.approve, create_next_approved_core raises (403) and
    we re-raise as ValueError so the chain pauses honestly (P0005 §4 — approve never bypassed).
    """
    from modules.flow_gate.documents.routers.documents import (
        create_next_approved_core,
        NextApprovedError,
    )

    spine_doc_id = spine_doc["doc_id"]
    project_id = spine_doc.get("project_id") or ""
    group_id = spine_doc.get("group_id")
    module = spine_doc.get("module") or "none"

    # Approver permissions are resolved lazily — only once an instruction head is actually
    # encountered — so a continuous advance whose head is already a report touches no users /
    # RBAC dependency (zero new side effects for the common report-head case).
    _perms_cache: dict = {}

    def _approver_perms() -> set:
        if "perms" not in _perms_cache:
            from modules.flow_gate.db import users as _db_users
            from modules.flow_gate.workflow.routers.workflow import (
                _get_user_permissions as _resolve_user_permissions,
            )
            actor_user = _db_users.get_by_id(actor_user_id) or {
                "user_id": actor_user_id, "is_admin": 0,
            }
            _perms_cache["perms"] = _resolve_user_permissions(actor_user)
        return _perms_cache["perms"]

    completed = 0
    prev_item_seq: Optional[int] = None
    while True:
        head = db_wfseq.get_effective_head(seq["id"])
        if head is None:
            break
        head_type = (head.get("type") or "").upper()
        if head_type not in INSTRUCTION_AUTO_TYPES:
            break  # report / AC / other → caller mints the worker mention here
        item_seq = head.get("item_seq")
        # Never run past the chain's stop point (target = last item_seq of the run).
        if target_seq is not None and item_seq is not None and item_seq > target_seq:
            break
        # An already-produced-but-unapproved head means a worker doc is mid-flight; leave it
        # to advance_workflow's head_in_progress guard rather than stacking another doc.
        rdid = head.get("result_doc_id")
        if rdid is not None and head.get("result_doc_review_status") != "approved":
            break
        # Defensive non-progress guard: approving the head must move it forward; if the same
        # head reappears, stop instead of looping forever.
        if prev_item_seq is not None and item_seq == prev_item_seq:
            break
        prev_item_seq = item_seq
        try:
            create_next_approved_core(
                project_id=project_id,
                group_id=group_id,
                module=module,
                prev_doc_id=spine_doc_id,
                type_code=head_type,
                actor_user_id=actor_user_id,
                approver_perms=_approver_perms(),
                locale=locale,
            )
        except NextApprovedError as exc:
            raise ValueError(
                f"instruction_auto_complete_failed:{head_type}:{exc.detail}"
            ) from exc
        completed += 1
    return completed


# ── Advance to next step ─────────────────────────────────────────────────────────

def advance_workflow(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    ref_doc_ids: Optional[list] = None,
    locale: str = "ko",
    continuous: bool = False,
    continuation_target_seq: Optional[int] = None,
    continuation_review_mode: bool = False,
    continuation_instruction_mode: Optional[str] = None,
    # 0359 L0007 §2.9: the AI run this hop belongs to, stamped onto the issued token. Default
    # None keeps every existing caller (managed advance, tests) working unchanged.
    ai_run_id: Optional[str] = None,
) -> dict:
    """Advance to next step — numbering + token issuance + mention creation + head → in_progress.

    Continuous (unmanned) work (group 0051 R0001 / NR0003 B안): when ``continuous`` is set,
    the issued token carries the chain stop point (``continuation_target_seq`` = target
    item_seq) and the AI-review-mode flag, and the generated mention swaps its Q-guard for
    the unmanned/delegation/no-stop/autonomous block. The inbox self-chain
    (inbox_routes._handle_new) reads these off the consumed token to mint the next step.
    ``continuation_target`` / ``continuation_remaining`` are returned so the caller (and the
    self-chain) can tell whether the head has reached the target.

    Parameters
    ----------
    doc_id : str
        Target document canonical ID (e.g. R016)
    issued_to : str
        Token requester user_id (PM)
    api_base_url : str
        API base URL used for the mention (e.g. http://host:port/context/flow_gate/api/v1)
    ref_doc_ids : list[str] | None
        Additional reference document list for the mention (T358). Array of canonical doc_ids. Ignored when None.

    Returns
    -------
    dict
        P002 §3-2 response format (doc_ref, action_scope, group_id, doc_class, token, mention)

    Raises
    ------
    LookupError
        "doc_not_found:{doc_id}" — document not found
    ValueError
        "sequence_not_decided:{doc_id}" — sequence not yet decided
        "sequence_exhausted:{doc_id}" — entire sequence complete
        "head_in_progress:{type}:{label}" — head already in progress
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")

    # 0084 TR0005 (B, defensive): a non-R member doc (a slot's produced child such as an
    # approved CH/N) belongs to the same sequence but is not its root, so the root-only
    # get_sequence_by_doc_id returned None → sequence_not_decided (400). The FE now advances
    # via the parent R (A fix); resolving members here keeps advance working if a child
    # doc_id reaches this path. A genuinely sequence-less doc still resolves to None → 400.
    seq = db_wfseq.get_sequence_for_member_doc(doc_id)
    if seq is None:
        raise ValueError(f"sequence_not_decided:{doc_id}")

    # Continuous (unmanned) chains: auto-create + auto-approve any instruction-series head
    # (N/T/TS) server-side so the worker mention below is only ever issued for a report step
    # (group 0092 B0001 / NR0003 B안). This advances the head past the instruction(s) to its
    # paired report before head/token/mention resolution proceeds as normal. Managed advance
    # (continuous=False) is untouched — the FE still drives "자동승인문서" explicitly there.
    instruction_mode = normalize_continuation_instruction_mode(continuation_instruction_mode)
    if continuous and instruction_mode == CONTINUATION_INSTRUCTION_AUTO_APPROVED:
        _auto_complete_instruction_heads(
            spine_doc=doc,
            seq=seq,
            actor_user_id=issued_to,
            locale=locale,
            target_seq=continuation_target_seq,
        )

    head = db_wfseq.get_effective_head(seq["id"])
    if head is None:
        raise ValueError(f"sequence_exhausted:{doc_id}")

    result_doc_id = head.get("result_doc_id")
    result_review = head.get("result_doc_review_status")
    if result_doc_id is not None and result_review != "approved":
        raise ValueError(f"head_in_progress:{head['type']}:{head['label']}")

    # Q149 double-advance guard: an unconsumed token may already exist for this doc_ref.
    # Previously this raised head_in_progress (-> 409). Because the token TTL is 24h
    # (token_service.TOKEN_TTL_HOURS), a prior advance whose token was never consumed
    # (e.g. the worker run was abandoned/cancelled) would lock out every re-advance on
    # the same document for up to 24 hours -> the "persistent 409" reported in R0001.
    # Since advance is an explicit PM re-initiation, supersede the stale token instead:
    # revoke it, then issue a fresh one below. This preserves Q149's "at most one active
    # token per doc_ref" invariant (the old token is revoked before the new one issues)
    # while letting the PM recover immediately. Note: the result-doc guard above still
    # blocks advancing while a produced downstream document awaits approval.
    from modules.flow_gate.db import tokens as _db_tokens
    _existing_token = _db_tokens.get_unconsumed_by_doc_ref(doc_id)
    if _existing_token is not None:
        token_service.revoke(
            _existing_token["token_id"], reason="superseded_by_readvance"
        )

    group_id: Optional[str] = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")

    head_type: str = head["type"]

    doc_ref = doc_id

    doc_class = _resolve_doc_class(doc)
    project_id: str = doc.get("project_id") or ""

    # ── Unmanned chain × TSR head → server-run test hand-off (group 0150) ──────────────
    # In a continuous chain the TSR is not hand-written: after its TS is approved, the
    # worker's next step is to REQUEST the run (inbox action:test_run) and FlowGate
    # executes the TS and auto-assembles the TSR on all-green (0138 P0005 §3 / 0139 P0002).
    # So the token minted for the chain here must inherit the test_run scope (R0001 group
    # 0150: "체인에 발급되는 토큰에 그 스코프를 물려주는 연결") instead of a 'new' token that
    # would ask the worker to write the TSR by hand. Managed advance (continuous=False) is
    # untouched — the FE drives runs via POST /documents/test-run(-request) explicitly.
    if continuous and head_type.upper() == "TSR":
        pred_doc_id = db_wfseq.get_predecessor_result_doc_id(seq["id"], head.get("id"))
        pred_doc = db_documents.get_by_id(pred_doc_id) if pred_doc_id else None
        if (
            pred_doc is not None
            and (pred_doc.get("type_code") or "").upper() == "TS"
            and pred_doc.get("doc_review_status") == "approved"
        ):
            from modules.flow_gate.services import test_run_service

            head_item_seq = head.get("item_seq")
            remaining: Optional[int] = None
            if continuation_target_seq is not None and head_item_seq is not None:
                remaining = max(0, continuation_target_seq - head_item_seq + 1)
            issue = test_run_service.issue_test_run_request(
                doc_id=pred_doc_id,
                issued_to=issued_to,
                api_base_url=api_base_url,
                continuation_target_seq=continuation_target_seq,
                continuation_review_mode=continuation_review_mode,
                continuation_instruction_mode=instruction_mode,
                locale=locale,
                continuous=True,
            )
            return {
                "doc_ref": issue["doc_ref"],
                "action_scope": "test_run",
                "group_id": issue["group_id"],
                "doc_class": doc_class,
                "token": issue["token"],
                "token_id": issue["token_id"],
                "expires_at": issue["expires_at"],
                "scratch_dir": issue["scratch_dir"],
                "mention": issue["mention"] or "",
                "continuous": True,
                "continuation_target_seq": continuation_target_seq,
                "continuation_review_mode": bool(continuation_review_mode),
                "continuation_instruction_mode": instruction_mode,
                "continuation_remaining": remaining,
                "head_item_seq": head_item_seq,
            }
        # No approved TS predecessor behind this TSR head → fall through to the ordinary
        # 'new' hand-off (the worker writes the TSR from context — pre-0150 behavior).

    # Continuation metadata rides on the next token only in continuous mode; otherwise
    # the token is an ordinary single-step token (NULL/0 columns — migration 050).
    issue_result = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="new",
        doc_ref=doc_ref,
        issued_to=issued_to,
        continuation_target_seq=continuation_target_seq if continuous else None,
        continuation_review_mode=bool(continuous and continuation_review_mode),
        # Persist the chosen locale on the continuation token so the unmanned self-chain
        # honors it on every hop (group 0099 B0001). Ordinary tokens leave it NULL.
        continuation_locale=locale if continuous else None,
        continuation_instruction_mode=instruction_mode if continuous else None,
        # 0359 L0007 §2.9 / NR0003 §4: without this the token cannot name the run that used
        # it, and 1,346 continuous tokens proved that a chain which dies here leaves no bridge
        # back to its own execution record.
        ai_run_id=ai_run_id,
    )
    raw_token: str = issue_result["raw_token"]
    scratch_dir: str = issue_result["scratch_dir"]

    mention_token_rec = {
        "project": project_id,
        "group_id": group_id,
        "scratch_dir": scratch_dir,
    }
    # Anchor recent-docs at the group's latest document, not the workflow-owning
    # root (whose seq is the group minimum). Otherwise docs produced after the
    # parent — e.g. a memo created earlier in this sequence — are excluded and the
    # worker loses that context. Fall back to the parent seq for an empty group.
    recent_before_seq = db_documents.get_group_max_seq(group_id) or doc.get("seq", 0)
    group_recent_docs = db_documents.fetch_recent_group_docs(
        group_id=group_id,
        before_seq=recent_before_seq,
        limit=5,
    )
    # Section 1 'Document information' must reflect the step's predecessor document
    # (NR→N, TR→T …), not the sequence-owning R. Resolve it from the last produced
    # sequence item; fall back to the owning root for the first step (no predecessor yet).
    # The owning root still drives target_id / prev_doc_id / token doc_ref via parent_doc.
    pred_doc_id = db_wfseq.get_predecessor_result_doc_id(seq["id"], head.get("id"))
    head_context_doc = db_documents.get_by_id(pred_doc_id) if pred_doc_id else doc
    # R0001 #1 / T0004: Section 3 'Reference documents' should carry the two most recent
    # predecessor documents (the previous step's result + the one before it) so the worker
    # gets "previous + previous-previous + R" = 3 docs. The live client (NextActionModal)
    # already passes the spine R and the step's own instruction in ref_doc_ids; appending
    # the predecessors here completes the trio. build_mention dedupes by slash-path, so the
    # instruction that coincides with the newest predecessor collapses to one line, and the
    # count naturally falls back to 2 (R + the single predecessor) at the first report step.
    predecessor_ids = db_wfseq.get_predecessor_result_doc_ids(
        seq["id"], head.get("id"), limit=2
    )
    merged_ref_ids = list(ref_doc_ids or [])
    for _pid in predecessor_ids:
        if _pid and _pid not in merged_ref_ids:
            merged_ref_ids.append(_pid)
    mention = mention_service.build_mention_from_token_rec(
        token_rec=mention_token_rec,
        head_type=head_type,
        head_status="pending",
        parent_doc=doc,
        api_base_url=api_base_url,
        raw_token=raw_token,
        group_recent_docs=group_recent_docs if group_recent_docs else None,
        ref_doc_ids=merged_ref_ids or None,
        locale=locale,
        head_context_doc=head_context_doc,
        continuous=continuous,
        continuous_review_mode=bool(continuous and continuation_review_mode),
    )

    # continuation_remaining = steps left from the current head to the target (inclusive
    # of the step about to run). The chain stops once head item_seq reaches the target.
    head_item_seq = head.get("item_seq")
    remaining: Optional[int] = None
    if continuous and continuation_target_seq is not None and head_item_seq is not None:
        remaining = max(0, continuation_target_seq - head_item_seq + 1)

    return {
        "doc_ref": doc_ref,
        "action_scope": "new",
        "group_id": group_id,
        "doc_class": doc_class,
        "token": raw_token,
        "token_id": issue_result["token_id"],
        "expires_at": issue_result["expires_at"],
        "scratch_dir": scratch_dir,
        "mention": mention or "",
        "continuous": continuous,
        "continuation_target_seq": continuation_target_seq if continuous else None,
        "continuation_review_mode": bool(continuous and continuation_review_mode),
        "continuation_instruction_mode": instruction_mode if continuous else None,
        "continuation_remaining": remaining,
        "head_item_seq": head_item_seq,
    }


def request_review(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    ref_doc_ids: Optional[list] = None,
    locale: str = "ko",
) -> dict:
    """Issue a review-request token + mention for an existing document.

    Unlike advance_workflow (which hands off CREATING the next document), this asks a
    worker to REVIEW doc_id itself and submit a verdict via inbox action:review. No
    sequence/next-step resolution is involved, so it works for any document type. The
    token is bound to doc_ref=doc_id so inbox _handle_review accepts the submission.

    Raises
    ------
    LookupError  "doc_not_found:{doc_id}"
    ValueError   "group_not_found:{doc_id}"
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")

    group_id: Optional[str] = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")

    project_id: str = doc.get("project_id") or ""

    # Review tokens are scoped "review" (not "edit"): the mention only authorises
    # an action:review submission, so the token must not double as an edit grant.
    # _handle_review enforces this scope, and _handle_edit/_handle_new reject it
    # (B0057.0001 / NR0057.0003). This matches the advertised action_scope below.
    issue_result = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="review",
        doc_ref=doc_id,
        issued_to=issued_to,
    )
    raw_token: str = issue_result["raw_token"]
    scratch_dir: str = issue_result["scratch_dir"]

    group_recent_docs = db_documents.fetch_recent_group_docs(
        group_id=group_id,
        before_seq=doc.get("seq", 0),
        limit=5,
    )
    mention = mention_service.build_review_mention(
        token_rec={"project": project_id, "group_id": group_id, "scratch_dir": scratch_dir},
        target_doc=doc,
        api_base_url=api_base_url,
        raw_token=raw_token,
        group_recent_docs=group_recent_docs if group_recent_docs else None,
        ref_doc_ids=ref_doc_ids,
        locale=locale,
    )

    return {
        "doc_ref": doc_id,
        "action_scope": "review",
        "group_id": group_id,
        "token": raw_token,
        "token_id": issue_result["token_id"],
        "expires_at": issue_result["expires_at"],
        "scratch_dir": scratch_dir,
        "mention": mention or "",
    }


def request_workflow_decision(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    locale: str = "ko",
    continuous: bool = False,
    continuation_review_mode: bool = False,
    continuation_instruction_mode: Optional[str] = None,
) -> dict:
    """Issue a document-bound token and prompt for AI workflow decision.

    Continuous (unmanned) work (group 0086 R0001): when ``continuous`` is set, the
    continuous run is started *before* the workflow is decided ("워크플로 결정부터").
    The minted workflow_decide token carries the CONTINUATION_TO_END sentinel as its
    continuation_target_seq (the concrete last item_seq is unknown until the sequence is
    decided; it is resolved when the decide self-chain kicks off — see
    continuation_kickoff_after_decide). The decision mention swaps its Q-guard for the
    delegation/unmanned/no-stop/autonomous block so the worker decides autonomously and
    keeps going after the decision is saved.
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")
    if (doc.get("type_code") or "").upper() not in {"R", "B"}:
        raise ValueError(f"workflow_decision_requires_root:{doc_id}")
    if db_wfseq.get_sequence_by_doc_id(doc_id) is not None:
        raise ValueError(f"already_decided:{doc_id}")

    group_id: Optional[str] = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")

    project_id = doc.get("project_id") or ""
    instruction_mode = normalize_continuation_instruction_mode(continuation_instruction_mode)
    issue_result = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="workflow_decide",
        doc_ref=doc_id,
        issued_to=issued_to,
        continuation_target_seq=CONTINUATION_TO_END if continuous else None,
        continuation_review_mode=bool(continuous and continuation_review_mode),
        continuation_instruction_mode=instruction_mode if continuous else None,
    )
    recent_before_seq = db_documents.get_group_max_seq(group_id) or doc.get("seq", 0)
    recent_docs = db_documents.fetch_recent_group_docs(
        group_id=group_id,
        before_seq=recent_before_seq,
        limit=5,
    )
    mention = mention_service.build_workflow_decision_mention(
        token_rec={
            "project": project_id,
            "group_id": group_id,
            "scratch_dir": issue_result["scratch_dir"],
        },
        target_doc=doc,
        api_base_url=api_base_url,
        raw_token=issue_result["raw_token"],
        group_recent_docs=recent_docs or None,
        locale=locale,
        continuous=continuous,
        continuous_review_mode=bool(continuous and continuation_review_mode),
    )
    return {
        "doc_ref": doc_id,
        "action_scope": "workflow_decide",
        "group_id": group_id,
        "raw_token": issue_result["raw_token"],
        "token_id": issue_result["token_id"],
        "expires_at": issue_result["expires_at"],
        "scratch_dir": issue_result["scratch_dir"],
        "mention": mention,
        "continuous": continuous,
        "continuation_review_mode": bool(continuous and continuation_review_mode),
        "continuation_instruction_mode": instruction_mode if continuous else None,
    }


def request_sequence_edit(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    locale: str = "ko",
) -> dict:
    """Issue a document-bound token + prompt for an AI worker to EDIT the pending sequence.

    Parallel of request_workflow_decision, for the post-decision "시퀀스 수정" path
    (R0001 group 0208). The workflow is ALREADY decided, so instead of a decide token this
    mints a ``workflow_sequence_edit``-scoped token bound to the root doc plus a mention that
    hands the worker the current sequence (locked vs pending) and the edit contract. The
    worker applies the change autonomously via PATCH /workflow/sequence — the same endpoint
    the human edit modal uses — so locked/completed steps stay immutable and only the pending
    tail is replaced (the human path is unchanged; this just lets AI drive it too).

    Raises
    ------
    LookupError
        "doc_not_found:{doc_id}" — document not found
    ValueError
        "sequence_not_decided:{doc_id}" — workflow not yet decided (nothing to edit)
        "group_not_found:{doc_id}" — document has no group
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")

    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        raise ValueError(f"sequence_not_decided:{doc_id}")

    group_id: Optional[str] = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")

    project_id = doc.get("project_id") or ""
    issue_result = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="workflow_sequence_edit",
        doc_ref=doc_id,
        issued_to=issued_to,
    )

    items = db_wfseq.get_sequence_items(seq["id"])
    sequence_items = [
        {
            "type": it["type"],
            "label": _safe_label(it["label"], it["type"], locale),
            "status": it["status"],
        }
        for it in items
    ]

    mention = mention_service.build_sequence_edit_mention(
        token_rec={
            "project": project_id,
            "group_id": group_id,
            "scratch_dir": issue_result["scratch_dir"],
        },
        target_doc=doc,
        api_base_url=api_base_url,
        raw_token=issue_result["raw_token"],
        sequence_items=sequence_items,
        locale=locale,
    )
    return {
        "doc_ref": doc_id,
        "action_scope": "workflow_sequence_edit",
        "group_id": group_id,
        "raw_token": issue_result["raw_token"],
        "token_id": issue_result["token_id"],
        "expires_at": issue_result["expires_at"],
        "scratch_dir": issue_result["scratch_dir"],
        "mention": mention,
    }


# ── Continuous-work decide kickoff (group 0086 R0001) ───────────────────────────────

def continuation_kickoff_after_decide(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    locale: str = "ko",
    continuation_target_seq: Optional[int] = None,
    continuation_review_mode: bool = False,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[dict]:
    """Mint the first real step after an unmanned-chain workflow decision is saved.

    Called by the decide endpoint once a workflow_decide token that carried continuation
    metadata (group 0086: a continuous run started before the workflow was decided) has
    been consumed. Resolves the CONTINUATION_TO_END sentinel to the concrete last item_seq
    of the freshly-decided sequence, then advances to the head and returns a continuation
    envelope (next_token / next_mention / continuation_remaining) so the worker proceeds —
    from here the ordinary inbox self-chain (concrete target) carries the rest of the run.

    Returns None when the token was not a continuation token (ordinary decision). Mirrors
    inbox_routes._continuation_self_chain: it NEVER raises — any failure pauses the chain
    (continuation_paused) rather than breaking the already-saved decision.
    """
    if continuation_target_seq is None:
        return None  # ordinary workflow_decide token — not a continuation chain

    review_mode = bool(continuation_review_mode)
    instruction_mode = normalize_continuation_instruction_mode(continuation_instruction_mode)
    envelope: dict = {
        "continuation": True,
        "continuation_review_mode": review_mode,
        "continuation_instruction_mode": instruction_mode,
    }

    def _stop(stop_code: str, *, detail: Optional[str] = None) -> dict:
        """0359 L0007 §2.11/§2.12: the decide→first-step handoff is a step boundary too, so a
        stop here has to be named, tagged and (when a human must act) announced exactly like
        one in the inbox self-chain. Same stamper, so both paths cannot drift apart."""
        from modules.flow_gate.services import ai_invoke_service as _ai_invoke
        doc = {}
        try:
            doc = db_documents.get_by_id(doc_id) or {}
        except Exception:  # pragma: no cover - defensive, the stop matters more than the anchor
            _log.warning("kickoff stop anchor lookup failed (ignored)", exc_info=True)
        return _ai_invoke.stamp_chain_stop(
            envelope,
            stop_code,
            project_id=doc.get("project_id") or "",
            group_id=doc.get("group_id"),
            actor_user_id=issued_to,
            anchor_doc_id=doc_id,
            detail=detail,
        )

    # Resolve the TO_END sentinel against the now-decided sequence.
    target_seq = continuation_target_seq
    try:
        seq = db_wfseq.get_sequence_by_doc_id(doc_id)
        if seq is None:
            envelope["continuation_paused"] = True
            envelope["continuation_reason"] = "sequence not decided after decide"
            return _stop("advance_blocked", detail="sequence not decided after decide")
        if target_seq == CONTINUATION_TO_END:
            items = db_wfseq.get_sequence_items(seq["id"])
            if not items:
                envelope["continuation_paused"] = True
                envelope["continuation_reason"] = "decided sequence is empty"
                return _stop("advance_blocked", detail="decided sequence is empty")
            target_seq = max(it["item_seq"] for it in items)
    except Exception as exc:  # pragma: no cover - defensive
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = f"target resolution failed: {exc}"
        return _stop("advance_blocked", detail=f"target resolution failed: {exc}")

    envelope["continuation_target_seq"] = target_seq

    # Boundary pause check (group 0252 L0009 §2.2): the decide→first-step handoff is a
    # step boundary too — a pause accepted during the decision hop must withhold the
    # first real token here, exactly like the inbox self-chain does between steps.
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as _db_paused
        _doc = db_documents.get_by_id(doc_id) or {}
        _chain_group = _doc.get("group_id")
        if _chain_group and _db_paused.get_by_group(_chain_group):
            from modules.flow_gate.services import ai_invoke_service as _ai_invoke
            _ai_invoke.mark_user_paused(_chain_group)
            envelope["continuation_paused"] = True
            envelope["continuation_reason"] = (
                "paused by user at the step boundary; resume from the miniplayer to continue."
            )
            return _stop("user_paused")
    except Exception:  # pragma: no cover - defensive, fail-open like the inbox probe
        _log.warning("kickoff boundary pause probe failed (ignored)", exc_info=True)

    try:
        adv = advance_workflow(
            doc_id=doc_id,
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            continuous=True,
            continuation_target_seq=target_seq,
            continuation_review_mode=review_mode,
            continuation_instruction_mode=instruction_mode,
        )
    except Exception as exc:
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = f"advance blocked: {exc}"
        return _stop("advance_blocked", detail=str(exc))

    envelope.update(
        {
            "next_token": adv["token"],
            "next_token_id": adv["token_id"],
            "next_mention": adv["mention"],
            "next_expires_at": adv.get("expires_at"),
            "continuation_remaining": adv.get("continuation_remaining"),
            "continuation_instruction_mode": instruction_mode,
        }
    )
    return envelope


# ── Internal helpers ─────────────────────────────────────────────────────────────────

def _resolve_doc_class(doc: dict) -> str:
    """Extract doc_class (R/Q/B) from a document record."""
    type_code = (doc.get("type_code") or "").upper()
    if type_code == "B":
        return "B"
    if type_code == "Q":
        return "Q"
    return "R"


# ── Workflow sequence query ──────────────────────────────────────────────────────

def get_workflow_sequence(doc_id: str) -> dict:
    """Fetch the current workflow sequence + item statuses (for entering edit mode).

    Parameters
    ----------
    doc_id : str
        Target document canonical ID

    Returns
    -------
    dict
        sequence_id, items (includes id / type / label / sort_order / status per item)

    Raises
    ------
    LookupError
        "doc_not_found:{doc_id}" — document not found
    ValueError
        "sequence_not_decided:{doc_id}" — sequence not yet decided
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")

    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        raise ValueError(f"sequence_not_decided:{doc_id}")

    items = db_wfseq.get_sequence_items(seq["id"])
    return {
        "doc_id": doc_id,
        "sequence_id": seq["id"],
        "items": [
            {
                "id": it["id"],
                "item_seq": it["item_seq"],
                "type": it["type"],
                # NR0003 §7-3: rows already corrupted before this fix shipped still live in
                # workflow_sequence_items; degrade their display to the type name so the dialog
                # never shows "?????". No DB migration needed — the read path heals the view.
                "label": _safe_label(it["label"], it["type"]),
                "doc_class": it["doc_class"],
                "sort_order": it["sort_order"],
                "status": it["status"],
            }
            for it in items
        ],
    }


# ── Edit workflow PENDING items ────────────────────────────────────────────────

def edit_workflow_pending(doc_id: str, new_items: list[dict]) -> dict:
    """Replace PENDING items with new_items. Preserves done/in_progress items.

    Implementation decision (T485):
    - L002 §out-of-scope: "Post-sequence edit algorithm — to be defined in a separate R later."
      This implementation applies the minimal safe interpretation: replace only pending items,
      preserve locked items (done/in_progress).
    - Save strategy: explicit save (adopted since D document is not yet defined).

    Parameters
    ----------
    doc_id : str
        Target document canonical ID
    new_items : list[dict]
        New pending item list to replace with. Each item: { type, label }

    Returns
    -------
    dict
        { status: "updated", doc_id, pending_count }

    Raises
    ------
    ValueError
        "sequence_not_decided:{doc_id}" — sequence not yet decided
    """
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        raise ValueError(f"sequence_not_decided:{doc_id}")

    existing = db_wfseq.get_sequence_items(seq["id"])
    locked = [it for it in existing if it.get("result_doc_id") is not None]
    locked_count = len(locked)

    # 0119 B0001 (NR0003 §6-A): refuse an edit that would empty a decided workflow.
    # When nothing is locked (no done/in_progress step — i.e. the workflow was just
    # decided and not yet run) and the new pending list is empty, the sequence would
    # drop to ZERO items: a decided-but-empty "zombie" sequence. That state is
    # unrecoverable — re-decide is blocked by already_decided (the row still exists),
    # advance dies with sequence_exhausted (no head), and the workflow strip collapses.
    # Mirror the decide path, which already rejects an empty sequence (invalid_sequence
    # 400). A shrink that keeps ≥1 locked step is still allowed: that leaves the realized
    # steps + the AC gate, which is a valid "stop here" intent.
    if not new_items and locked_count == 0:
        raise ValueError(f"invalid_sequence_empty:{doc_id}")

    # R0001 group 0208 (NR0003 §3-3): make the edit path symmetric with decide_workflow —
    # attach each instruction step's report (N→NR, T→TR, TS→TSR) here on the server, exactly
    # as decide_workflow does. _expand_auto_reports is idempotent: the human edit modal already
    # interleaves the reports (they pass through untouched), while an AI worker that PATCHes a
    # bare instruction list (the new autonomous 시퀀스 수정 path) gets its report steps inserted,
    # so an AI edit can never drop them and desync the sequence.
    new_items = _expand_auto_reports(new_items)

    # doc_class is inherited from locked items, defaults to 'R'
    doc_class = locked[0]["doc_class"] if locked else "R"

    # Avoid conflicts with existing item_seq: use numbers after the current max
    max_seq = db_wfseq.get_max_item_seq(seq["id"])

    store = get_store()
    with store.transaction():
        db_wfseq.delete_pending_items(seq["id"])
        for idx, item in enumerate(new_items):
            db_wfseq.insert_sequence_item(
                sequence_id=seq["id"],
                item_seq=max_seq + idx + 1,
                type_=item["type"],
                label=_safe_label(item["label"], item["type"]),  # NR0003 §7-2 (edit path)
                doc_class=doc_class,
                sort_order=locked_count + idx,
            )

    # Sync documents.workflow_steps
    all_items = db_wfseq.get_sequence_items(seq["id"])
    db_documents.update(
        doc_id,
        {"workflow_steps": _json.dumps([it["type"] for it in all_items])},
    )

    # Revive a finalized workflow. When the last sequence item is consumed the inbox
    # path marks the parent root doc 'wf_done' (e.g. the memo-only "advise then extend"
    # flow: M is auto-approved → sequence exhausted → wf_done). Appending new pending
    # steps here must re-open the doc, otherwise the workflow view treats it as complete
    # and the freshly-added steps are unreachable.
    if new_items:
        parent = db_documents.get_by_id(doc_id)
        if parent is not None and parent.get("doc_review_status") == "wf_done":
            db_documents.update(doc_id, {"doc_review_status": "wf_in_progress"})
        # Drop any not-yet-approved final-approval (AC) document. AC is opened only
        # when the head reaches final approval; inserting new pending steps before
        # it makes that AC premature and lets it (a) hijack head resolution and
        # (b) be approved out of order. Mirror reopen_workflow, which deletes the
        # ephemeral file-less AC; it is idempotently recreated once the new steps
        # are realized and the head reaches AC again.
        if parent is not None and parent.get("project_id") and parent.get("group_id"):
            _APPROVED = {"approved", "wf_done"}
            for _c in db_documents.list_documents(
                project_id=parent["project_id"],
                group_id=parent["group_id"],
                limit=200,
            ):
                if _c.get("type_code") == "AC" and _c.get("doc_review_status") not in _APPROVED:
                    db_documents.delete(_c["doc_id"])

    return {
        "status": "updated",
        "doc_id": doc_id,
        "pending_count": len(new_items),
    }
