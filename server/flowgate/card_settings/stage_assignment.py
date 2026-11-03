"""Stage assignments of a card run (flowgate.v02.0113 T#3, L-2 §2.1 ~ §2.6, §2.10, §2.13; P-1 §7).

A role card (RUN / TEST / PLAN) of an attempt opened while the assignment store is available records, for each stage it
starts, who works it — one AI, a person, no review, or a wait — once (``(attempt_id, stage, seq)``, first writer wins),
and every later step reads that record instead of the card settings:

- ``start_stage`` (L-2 §2.2.1): store → same key → pre-feature attempt → the card's one pending initialization (under its
  own savepoint: a failure keeps ``pending_init`` and the stage is resolved on the slots left) → role resolution (L-1)
  → ``assignment_from`` (L-2 §2.3, the run starter's selectable AIs decide "no AI") → insert-if-absent, and the
  ``stage_assigned`` run event for an inserted row (P-1 §7.1). Inside the caller's Flow guard only.
- ``dispatch_role_card`` (§2.4.1): the node's opening — its stage plan (§2.5.1), its first stage's assignment, the
  node token (T-1) and the first stage's action (``act_instruction_stage`` / ``act_result_stage``). None = the current
  dispatch takes it (not a role card, the feature off, no attempt).
- ``result_stage_start`` (§2.5): the instruction stage is over; the result stage's assignment is resolved now (a value
  changed meanwhile applies) and acted on.
- ``needs_dispatch`` (§2.10): what an explicit command does with an open node of an assigned attempt.
- ``stage_plan`` / ``is_legacy_attempt`` / ``current_stage_assignment``: what the other consumers read.

0038-T T#3 (0030-M §3.1, 0002-D 개정 §3.6-0 / §3.5.1-8):

- the next stage follows the card's documents the same way for an AI and a person: a result stage whose report is
  already written (``written_result``: its line's report with a body, submitted or approved alike, never the attempt's
  report baseline) asks no writer — no AI, no manual write — and goes to this attempt's result review
  (``take_written_result``) whatever the report's own review state: a report approved before the attempt took it is
  reviewed again by the attempt's review assignment (an AI's review, the automatic approval, or its wait) and the
  binder holds the old approval back until that review passed (``legacy.taken_judged``);
- an attempt that opens with its instruction not written records the report of that moment as its baseline
  (``report_baseline``): the result stage takes only a report saved after it; a baseline that cannot be recorded fails
  the opening, one that cannot be read lets no report be taken;
- a waiting write stage — a person's manual write wait, an AI setup wait, an unavailable AI's wait — with no document
  in it is resolved again by the first command after its role resolves to another assignment (``REINTERPRET``: a new
  attempt, the old one's tokens revoked, its documents kept); unchanged, it keeps waiting with its attempt, assignment
  and tokens. A failed stage is an explicit retry (``REOPEN_NEW_ATTEMPT``).

The feature is on while ``enabled()`` (the assignment store exists, migration 164); an attempt with no assignment is a
pre-feature attempt and every consumer keeps its current function for it (§2.13). Codes are P-1 §1.3 C-2 strings.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from flowgate.ai_roles import assignment_store as _store
from flowgate.ai_roles import availability, card_init, resolve, role_store
from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import slots as card_slots
from flowgate.card_settings import fields, report_baseline, repository
from flowgate.card_settings import legacy_adapter as legacy

_log = logging.getLogger(__name__)


class AssignmentUnreadable(RuntimeError):
    """L-2 §2.14 / T §4.6: an assignment record could not be read — never "no assignment", never a pre-feature attempt.
    Inside a guard it rolls the step back as any error does; a post-commit judgement records B-1
    ``assignment_unreadable`` for its submission once; the reconciliation skips the node; a run read shows the node in
    the error family (``stage_view.waits_on_person``)."""


class _Reads:
    """``assignment_store`` with its reads raising ``AssignmentUnreadable`` (its writes and constants as they are)."""
    _READS = frozenset({"available", "read", "latest", "has_any", "for_node", "ledger_exec_for", "current_attempts",
                        "asked_for_revision"})

    def __getattr__(self, name):
        attr = getattr(_store, name)
        if name not in self._READS or not callable(attr):
            return attr

        def read(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except AssignmentUnreadable:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed read is told apart from an empty one
                raise AssignmentUnreadable(f"assignment {name} failed: {type(exc).__name__}") from exc
        return read


store = _Reads()

LEGACY = "legacy"                                  # the attempt / store is pre-feature: the caller's current function
INSTRUCTION, RESULT, RESULT_REVIEW, INSTRUCTION_REVIEW = store.STAGES
REVIEW_STAGES = store.REVIEW_STAGES
AI, PERSON, NO_REVIEW, WAIT_SETUP, WAIT_UNAVAILABLE = store.ACTORS
ACTIVE, WAITING, DONE, FAILED = store.STAGE_STATES
ROLE_OF = {INSTRUCTION: "instruction", RESULT: "execution", RESULT_REVIEW: "review", INSTRUCTION_REVIEW: "review"}
# actor_reason (C-2)
NOT_USED, NO_AI, LEGACY_PERSON, LEGACY_NO_REVIEW = "not_used", "no_ai", "legacy_person", "legacy_no_review"
NO_REVIEW_SETTING, SETTING_UNREADABLE = "no_review_setting", "setting_unreadable"
# assignment source additions (C-2)
STORE_ERROR, SRC_NO_AI = "store_error", "no_ai"
SRC_INSTRUCTION_REVIEW_FIELD, SRC_INSTRUCTION_REVIEW_SETTING = "instruction_review_field", "instruction_review_setting"
# fail_cause (C-2)
TOKEN_ISSUE_FAILED, REVIEW_NOT_STARTED = "token_issue_failed", "review_not_started"
START_FAILED, INVOKE_FAILED, NO_PROVIDER, START_REFUSED = "start_failed", "invoke_failed", "no_provider", "start_refused:"
# verdict (C-2)
PASSED, ISSUES, AUTO_APPROVED, NOT_PASSED = "passed", "issues", "auto_approved", "not_passed:"
# ai_purpose (C-3 purpose) and the record role it takes (C-3 role)
INSTRUCTION_WRITE, INSTRUCTION_WRITE_RETRY = "instruction_write", "instruction_write_retry"
RESULT_WRITE, RESULT_WRITE_RETRY = "result_write", "result_write_retry"
RESULT_REVIEW_ROUND, RESULT_REWORK = "result_review_round", "result_rework"
INSTRUCTION_REVIEW_ROUND, INSTRUCTION_REWORK = "instruction_review_round", "instruction_rework"
_PURPOSE_ROLE = {INSTRUCTION_WRITE: "instruction", INSTRUCTION_WRITE_RETRY: "instruction",
                 RESULT_WRITE: "execution", RESULT_WRITE_RETRY: "execution",
                 RESULT_REVIEW_ROUND: "review", INSTRUCTION_REVIEW_ROUND: "instruction_review",
                 RESULT_REWORK: "rework", INSTRUCTION_REWORK: "rework"}
# run event causes and token kinds (P-1 §7.1)
EV_ASSIGNED, EV_FAILED, EV_TOKEN_FAILED = "stage_assigned", "stage_failed", "stage_token_issue_failed"
TOKEN_NODE, TOKEN_INSTRUCTION, TOKEN_RESULT = "node", "instruction_stage", "result_supplement"
# needs_dispatch answers (L-2 §2.10; 0038-T T#3 B-6: ``REINTERPRET`` is a new attempt for a waiting write stage whose
# role changed — the old attempt's documents stay the card's, ``execution.reopen_attempt``)
DISPATCH, RETRY_SAME_ATTEMPT, REOPEN_NEW_ATTEMPT, NONE = "dispatch", "retry_same_attempt", "reopen_new_attempt", "none"
REINTERPRET = "reinterpret_wait"
_ZERO, _SOME, _UNKNOWN = "zero", "some", "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── the feature switch, the attempt kind, the plan ───────────────────────────

def enabled() -> bool:
    """The stage assignment feature is on (the store of migration 164 exists). Off: every consumer's current function,
    the opening included (L-2 §2.4.1 row 2, E-02, K-12)."""
    return store.available()


def is_legacy_attempt(attempt_id: Optional[str]) -> bool:
    """L-2 §2.13: the attempt is a pre-feature one (no assignment of it, or the feature off). Read errors propagate."""
    return not enabled() or not attempt_id or not store.has_any(attempt_id)


def kind_of(node: dict) -> Optional[str]:
    return fields.card_kind(node.get("node_type"), node.get("doc_type"))


def role_card(node: Optional[dict]) -> bool:
    return bool(node) and bool(resolve.applicable_roles(kind_of(node)))


def stage_plan_of(kind: Optional[str], node: dict, stored) -> dict:
    """L-2 §2.5.1: the attempt's plan, once, at the node's opening. Unreadable settings plan with the defaults."""
    stored = stored if isinstance(stored, dict) else {}
    if kind == fields.TEST:
        return {"instruction_type": None if stored.get("instruction") == "omit" else "TS", "result_type": "TSR"}
    if kind == fields.PLAN:
        return {"instruction_type": None, "result_type": "WP"}
    nt, dt = node.get("node_type"), node.get("doc_type")
    return {"instruction_type": fields.instruction_stage(nt, dt, stored),
            "result_type": fields.result_doc_type(nt, dt, stored, one_stage=False) or legacy.run_doc_type(nt, dt)}


def first_assignment(attempt_id: Optional[str]) -> Optional[dict]:
    """The attempt's first (seq 0) assignment, the one carrying its plan."""
    if not attempt_id:
        return None
    for stage in (INSTRUCTION, RESULT):
        found = store.read(attempt_id, stage, 0)
        if found is not None and found.get("plan_result_type"):
            return found
    return None


def stage_plan(attempt_id: Optional[str]) -> Optional[dict]:
    """The plan recorded on the attempt's first assignment (``{instruction_type, result_type}``), or None."""
    first = first_assignment(attempt_id)
    if first is None:
        return None
    return {"instruction_type": first.get("plan_instruction_type"), "result_type": first.get("plan_result_type")}


def mark(doc: Optional[dict]) -> Optional[dict]:
    """L-2 §2.1 ``submission_mark``."""
    if not doc:
        return None
    return {"doc_id": doc["doc_id"], "revision_no": int(doc.get("revision_no") or 0),
            "rejection_count": int(legacy.rejection_count(doc) or 0)}


def basis_of(a: dict) -> Optional[dict]:
    if not a or not a.get("basis_doc_id"):
        return None
    return {"doc_id": a["basis_doc_id"], "revision_no": int(a["basis_revision_no"]),
            "rejection_count": int(a["basis_rejection_count"])}


def needs_new_submission(a: Optional[dict], doc: Optional[dict]) -> bool:
    """L-2 §2.1: the document is a new submission for the stage's latest review assignment ``a``."""
    if a is None:
        return True
    if a.get("author") == AI:
        return False
    b = basis_of(a) or {}
    if not doc:
        return False
    if doc["doc_id"] != b.get("doc_id"):
        return True
    if int(doc.get("revision_no") or 0) > int(b.get("revision_no") or 0):
        return True
    return int(legacy.rejection_count(doc) or 0) > int(b.get("rejection_count") or 0) \
        and doc.get("doc_review_status") != "rejected"


def current_review_assignment(attempt_id: Optional[str], stage: str) -> Optional[dict]:
    return store.latest(attempt_id, stage) if attempt_id else None


def current_stage_assignment(attempt_id: Optional[str], row: dict):
    """L-2 §2.10: the assignment the node waits on now (an instruction's review first), ``LEGACY`` or None."""
    if is_legacy_attempt(attempt_id):
        return LEGACY
    status, stage = row.get("status"), row.get("stage")
    if status == "open" and stage == INSTRUCTION:
        ir = current_review_assignment(attempt_id, INSTRUCTION_REVIEW)
        if ir is not None and row.get("instruction_doc_id") == ir.get("basis_doc_id") \
                and not needs_new_submission(ir, legacy.document_row(row.get("instruction_doc_id"))):
            return ir
        return store.read(attempt_id, INSTRUCTION, 0)
    if status == "open" and stage == RESULT:
        return store.read(attempt_id, RESULT, 0)
    if status == "review":
        rr = current_review_assignment(attempt_id, RESULT_REVIEW)
        if rr is not None and not needs_new_submission(rr, legacy.document_row(row.get("result_doc_id"))):
            return rr
        return store.read(attempt_id, RESULT, 0)
    return None


# ── state writes (W-8 / W-9) and the run events ──────────────────────────────

def move(a: dict, to_state: str, *, fail_cause: Optional[str] = None, verdict: Optional[str] = None) -> dict:
    """``a``'s state moved from its current one (CAS); returns the row read back (the winner's on a lost race)."""
    store.update_state(a["attempt_id"], a["stage"], a["seq"], a["stage_state"], to_state,
                       fail_cause=fail_cause, verdict=verdict if verdict is not None else a.get("verdict"))
    return store.read(a["attempt_id"], a["stage"], a["seq"]) or a


def fail(a: dict, cause: str, ctx=None) -> dict:
    """``a`` failed with ``cause`` (``stage_failed`` once, P-1 §7.1). DONE / FAILED rows stay as they are."""
    if a["stage_state"] in (DONE, FAILED):
        return a
    moved = move(a, FAILED, fail_cause=cause)
    if moved.get("stage_state") == FAILED and moved.get("fail_cause") == cause:
        _event(moved, EV_FAILED, cause, {**_assigned_keys(moved), "fail_cause": cause}, ctx)
    return moved


def _assigned_keys(a: dict) -> dict:
    return {"node_key": a["node_key"], "attempt_id": a["attempt_id"], "stage": a["stage"], "seq": a["seq"],
            "role": a["role"], "actor": a["actor"], "actor_reason": a.get("actor_reason"), "source": a["source"],
            "cause_code": a.get("cause"), "provider_id": a.get("provider_id"),
            "provider_name": a.get("provider_name"), "author": a.get("author"), "decided_at": a["decided_at"]}


def _event(a: dict, cause: str, reason: Optional[str], extra: dict, ctx=None) -> None:
    legacy.append_run_event(a["run_id"], cause, reason, extra, ctx)


def token_failed(run_id: str, node_key: str, attempt_id: str, stage: str, seq: int, kind: str, recipient: str,
                 ctx=None) -> None:
    legacy.append_run_event(run_id, EV_TOKEN_FAILED, kind,
                            {"node_key": node_key, "attempt_id": attempt_id, "stage": stage, "seq": seq,
                             "token_kind": kind, "recipient": recipient or None}, ctx)


# ── resolution → assignment (L-2 §2.3) ────────────────────────────────────────

def no_ai(run_starter: Optional[str]) -> str:
    """The run starter's selectable AIs: none (``zero``), some, or not to be told (``unknown``: safe, a wait)."""
    if not run_starter:
        return _UNKNOWN
    try:
        options = availability.selectable_ais(run_starter)
    except Exception:  # noqa: BLE001 — E-07: a registry that cannot be read is no "no AI"
        _log.warning("selectable AIs not read user=%s", run_starter, exc_info=True)
        return _UNKNOWN
    return _ZERO if not options else _SOME


def _a(actor: str, reason: Optional[str] = None, *, source: str, provider_id: Optional[str] = None,
       provider_name: Optional[str] = None, cause: Optional[str] = None) -> dict:
    return {"actor": actor, "actor_reason": reason, "source": source, "provider_id": provider_id,
            "provider_name": provider_name, "cause": cause}


def assignment_from(stage: str, r, run_starter: Optional[str]) -> dict:
    review = stage in REVIEW_STAGES
    if r.outcome == "ai":
        if not isinstance(r.provider_id, str) or not r.provider_id.strip():
            return _a(WAIT_SETUP, source=STORE_ERROR)                                       # E-12
        return _a(AI, source=r.source, provider_id=r.provider_id,
                  provider_name=role_store.name_snapshot(availability.display_name(r.provider_id)))
    if r.outcome == "person":
        reason = LEGACY_PERSON if r.source == "legacy_fields" else NOT_USED
        return _a(NO_REVIEW if review else PERSON, reason, source=r.source)
    if r.outcome == "no_review":
        return _a(NO_REVIEW, LEGACY_NO_REVIEW, source="legacy_fields")
    if r.outcome == "unavailable":
        original = r.original or {}
        pid = original.get("provider_id") if original.get("provider_id") is not None else r.provider_id
        if pid is None:
            return _a(WAIT_SETUP, source=STORE_ERROR)
        return _a(WAIT_UNAVAILABLE, source=r.source, provider_id=pid, cause=r.cause or "registry_unreadable",
                  provider_name=role_store.name_snapshot(original.get("name")))
    if r.outcome == "needs_setup":
        if r.source == STORE_ERROR:
            return _a(WAIT_SETUP, source=STORE_ERROR)
        if no_ai(run_starter) == _ZERO:
            return _a(NO_REVIEW if review else PERSON, NO_AI, source=SRC_NO_AI)
        return _a(WAIT_SETUP, source=r.source)
    raise ValueError(f"stage {stage} is not applicable to the card")                       # STAGE_NOT_APPLICABLE


def _review_count(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def with_result_review_plan(a: dict, attempt_id: str, author: str, r, stored: dict) -> dict:
    """L-2 §2.8.1: rounds and rework AI of a result review assignment."""
    if a["actor"] != AI:
        return a
    if author != AI:
        return {**a, "review_rounds": 1, "rework_provider_id": None}
    count = _review_count(stored.get("review_count"))
    if getattr(r, "source", None) == "legacy_fields" and count is not None and (count >= 1 or count == -1):
        rounds = count
    else:
        rounds = count if count in (1, 2, 3, -1) else 1
    ra = store.read(attempt_id, RESULT, 0)
    return {**a, "review_rounds": rounds,
            "rework_provider_id": ra["provider_id"] if ra is not None and ra["actor"] == AI else None}


def instruction_review_setting(kind: Optional[str], flow_id: str, node_key: str) -> str:
    """L-2 §2.8.2 ``instruction_review_setting``: no_review / needs_review / unknown."""
    if kind != fields.RUN:
        return NO_REVIEW_SETTING                         # TEST / PLAN have no instruction review field
    value = repository.stored(flow_id, node_key, "instruction_review_count")
    if value is repository.ABSENT or value is None:
        return NO_REVIEW_SETTING
    count = _review_count(value)
    if count == 0:
        return NO_REVIEW_SETTING
    return "needs_review" if count in (1, 2, 3, -1) else _UNKNOWN


def review_role_for_instruction(flow_id: str, node: dict, inputs, project_id: Optional[str]):
    r = resolve.resolve_role(flow_id, node, "review", project_id=project_id, inputs=inputs)
    if r.outcome == "no_review" and r.source == "legacy_fields":
        value = (inputs.stored or {}).get("review_ai")
        if isinstance(value, str) and value.strip():
            return resolve.with_availability("review", value, None, "legacy_fields", project_id, True)
        if value not in (None, ""):
            return resolve.Resolution("review", "needs_setup", "store_error", legacy_flag=True)
        return resolve.Resolution("review", "needs_setup", "legacy_fields", legacy_flag=True)
    return r


def instruction_reviewer_assignment(flow_id: str, node: dict, attempt_id: str, author: str, inputs,
                                    run_starter: Optional[str], project_id: Optional[str]) -> dict:
    """L-2 §2.8.2."""
    s = instruction_review_setting(kind_of(node), flow_id, node["node_key"])
    if s == NO_REVIEW_SETTING:
        return _a(NO_REVIEW, NO_REVIEW_SETTING, source=SRC_INSTRUCTION_REVIEW_SETTING)
    if s == _UNKNOWN:
        return _a(WAIT_SETUP, SETTING_UNREADABLE, source=SRC_INSTRUCTION_REVIEW_SETTING)
    stored = inputs.stored or {}
    v = stored.get("instruction_review_ai")
    if isinstance(v, str) and v.strip():
        cause = availability.check_availability(v, availability.CARD, project_id=project_id)
        a = _a(AI, source=SRC_INSTRUCTION_REVIEW_FIELD, provider_id=v,
               provider_name=role_store.name_snapshot(availability.display_name(v))) if cause is None else \
            _a(WAIT_UNAVAILABLE, source=SRC_INSTRUCTION_REVIEW_FIELD, provider_id=v, cause=cause)
    elif v not in (None, ""):
        a = _a(WAIT_SETUP, source=STORE_ERROR)
    else:
        a = assignment_from(INSTRUCTION_REVIEW, review_role_for_instruction(flow_id, node, inputs, project_id),
                            run_starter)
    if a["actor"] == AI:
        ia = store.read(attempt_id, INSTRUCTION, 0)
        count = _review_count(stored.get("instruction_review_count"))
        if author == AI and ia is not None and ia["actor"] == AI:
            a = {**a, "review_rounds": count if count in (1, 2, 3, -1) else 1, "rework_provider_id": ia["provider_id"]}
        else:
            a = {**a, "review_rounds": 1, "rework_provider_id": None}
    return a


def _slot_value(flow_id: str, node_key: str, role: str) -> tuple[Optional[str], Optional[str]]:
    try:
        slot = legacy.isolated(lambda: role_store.slots(flow_id, node_key).get(role), "fg_stage_slot_read")
    except Exception:  # noqa: BLE001 — a snapshot is a record only
        return None, None
    value = getattr(slot, "value", None)
    if value is None:
        return None, None
    if value.kind == role_store.ASSIGNED and isinstance(value.provider_id, str) and value.provider_id.strip():
        return role_store.ASSIGNED, value.provider_id
    if value.kind == role_store.NOT_USED:
        return role_store.NOT_USED, None
    return None, None


def _initialize_pending(flow_id: str, node_key: str, kind: Optional[str]) -> None:
    """L-2 §2.2.1 / T §4.1: the card's one pending fill right before a resolution, the whole call under its own
    savepoint — a failure rolls back to it (``pending_init``, slots and version unchanged) and the stage goes on."""
    try:
        legacy.isolated(lambda: card_init.initialize_pending_card(flow_id, node_key, kind), "fg_stage_pending_init")
    except Exception:  # noqa: BLE001 — L-1 E-23
        _log.warning("card pending init failed at a stage start flow=%s node=%s", flow_id, node_key, exc_info=True)


def start_stage(flow_id: str, run: dict, node: dict, attempt_id: str, stage: str, seq: int = 0, *,
                opening: bool = False, author: Optional[str] = None, basis: Optional[dict] = None,
                author_provider_id: Optional[str] = None, reused_doc_id: Optional[str] = None,
                plan: Optional[dict] = None, project_id: Optional[str] = None, ctx=None):
    """L-2 §2.2.1 / §4.1. Inside the caller's Flow guard. Returns the stored row (this call's or the winner's) or
    ``LEGACY``. Read errors propagate (the caller's guard rolls back, E-03)."""
    legacy.require_guard(flow_id)
    if not enabled():
        return LEGACY
    found = store.read(attempt_id, stage, seq)
    if found is not None:
        return found
    if not opening and not store.has_any(attempt_id):
        return LEGACY
    kind = kind_of(node)
    key = node["node_key"]
    _initialize_pending(flow_id, key, kind)
    project_id = project_id if project_id is not None else legacy.project_of(flow_id)
    starter = run.get("started_by")
    inputs = resolve.read_card_role_inputs(flow_id, node)
    if stage == INSTRUCTION_REVIEW:
        a = instruction_reviewer_assignment(flow_id, node, attempt_id, author, inputs, starter, project_id)
    else:
        r = resolve.resolve_role(flow_id, node, ROLE_OF[stage], project_id=project_id, inputs=inputs)
        a = assignment_from(stage, r, starter)
        if stage == RESULT_REVIEW:
            a = with_result_review_plan(a, attempt_id, author, r, inputs.stored or {})
    review = stage in REVIEW_STAGES
    value_kind, value_pid = _slot_value(flow_id, key, ROLE_OF[stage])
    now = _now()
    row = {
        "attempt_id": attempt_id, "stage": stage, "seq": seq, "run_id": run["run_id"], "group_id": flow_id,
        "node_key": key, "role": ROLE_OF[stage], "actor": a["actor"], "actor_reason": a.get("actor_reason"),
        "provider_id": a.get("provider_id") if a["actor"] in (AI, WAIT_UNAVAILABLE) else None,
        "provider_name": a.get("provider_name") if a["actor"] in (AI, WAIT_UNAVAILABLE) else None,
        "role_value_kind": value_kind, "role_value_provider_id": value_pid,
        "source": a["source"], "cause": a.get("cause") if a["actor"] == WAIT_UNAVAILABLE else None,
        "plan_instruction_type": (plan or {}).get("instruction_type") if plan and not review else None,
        "plan_result_type": (plan or {}).get("result_type") if plan and not review else None,
        "reused_doc_id": reused_doc_id if stage == INSTRUCTION else None,
        "review_rounds": a.get("review_rounds") if review and a["actor"] == AI else None,
        "rework_provider_id": a.get("rework_provider_id") if review and a["actor"] == AI else None,
        "author": author if review else None,
        "author_provider_id": author_provider_id if review and author == AI else None,
        "basis_doc_id": (basis or {}).get("doc_id") if review else None,
        "basis_revision_no": (basis or {}).get("revision_no") if review else None,
        "basis_rejection_count": (basis or {}).get("rejection_count") if review else None,
        "decided_at": now, "stage_state": ACTIVE if a["actor"] == AI else WAITING,
        "fail_cause": None, "verdict": None, "intent_kind": None, "intent_exec_id": None, "updated_at": now,
    }
    stored, inserted = store.insert_if_absent(row)
    if inserted:
        _event(stored, EV_ASSIGNED, stored["actor"], _assigned_keys(stored), ctx)
    return stored


# flowgate.v02.0124 T#1 (0003-NR §5.1.3-4): what the ``stage_assigned`` event of a re-requested review carries — the
# ``seq`` it follows and the cause. ``rerequest_cause`` and not ``cause``: ``append_run_event`` spreads its extra keys over
# the event's own payload, whose ``cause`` is ``stage_assigned``.
REREQUEST_OF_SEQ, REREQUEST_CAUSE, USER_COMMAND = "rerequest_of_seq", "rerequest_cause", "user_command"
_RESTART_COPIED = ("run_id", "group_id", "node_key", "role", "actor", "actor_reason", "provider_id", "provider_name",
                   "role_value_kind", "role_value_provider_id", "source", "cause", "review_rounds", "rework_provider_id",
                   "author", "author_provider_id")


def restart_review_stage(flow_id: str, cur: dict, basis: Optional[dict], ctx=None) -> tuple[dict, bool]:
    """flowgate.v02.0124 T#1 (0003-NR §5.1.3-4), inside the caller's Flow guard: the review assignment that follows
    ``cur`` — the stage's latest one, ended — for an explicit command's re-request. ``cur``'s reviewer, rework AI, rounds,
    source and author are copied as they are (no role is resolved again: the card's current settings never replace
    the reviewer and rework AI the card's result review was assigned), ``basis`` is the document's submission now, and
    the new row is ``active`` with no verdict or intent. ``cur`` itself is only read. Insert-if-absent: ``(row,
    inserted)`` — False when another writer made that ``seq`` first (the caller requests nothing then, W-11). The
    ``stage_assigned`` event of an inserted row carries ``rerequest_of_seq`` and ``rerequest_cause`` (``user_command``).
    ``needs_new_submission`` keeps its meaning: an AI's document saved again opens no ``seq`` by itself."""
    legacy.require_guard(flow_id)
    if cur.get("stage") not in REVIEW_STAGES or cur.get("actor") != AI:
        raise ValueError("only an AI review assignment is requested again")
    now = _now()
    row = {column: cur.get(column) for column in _RESTART_COPIED}
    row.update({
        "attempt_id": cur["attempt_id"], "stage": cur["stage"], "seq": int(cur["seq"]) + 1, "group_id": flow_id,
        "plan_instruction_type": None, "plan_result_type": None, "reused_doc_id": None,
        "basis_doc_id": (basis or {}).get("doc_id"), "basis_revision_no": (basis or {}).get("revision_no"),
        "basis_rejection_count": (basis or {}).get("rejection_count"),
        "decided_at": now, "stage_state": ACTIVE, "fail_cause": None, "verdict": None, "intent_kind": None,
        "intent_exec_id": None, "updated_at": now,
    })
    stored, inserted = store.insert_if_absent(row)
    if inserted:
        _event(stored, EV_ASSIGNED, stored["actor"],
               {**_assigned_keys(stored), REREQUEST_OF_SEQ: int(cur["seq"]), REREQUEST_CAUSE: USER_COMMAND}, ctx)
    return stored, inserted


# ── who works a stage, the AI request of a purpose (L-2 §2.10) ───────────────

def issued_to_of(node: dict, run: dict, actor: Optional[str] = None) -> str:
    """L-2 §2.6: the node token's recipient, fixed at the opening (T-2 / T-3 go to the same person)."""
    user = node.get("assignee_user_id") if node.get("assignee_kind") == "human" else None
    return user or run.get("started_by") or actor or ""


def target_provider(a: dict, purpose: str):
    """The provider a purpose calls, ``NONE`` (no rework: no call) or None (no provider: the stage fails)."""
    if a.get("actor") != AI:
        return None
    if purpose in (RESULT_REWORK, INSTRUCTION_REWORK):
        return a.get("rework_provider_id") or NONE
    pid = a.get("provider_id")
    return pid if isinstance(pid, str) and pid.strip() else None


def origin(a: dict, purpose: str, flow_id: str, *, doc: Optional[dict] = None, round_no: Optional[int] = None) -> dict:
    """P-1 §7.2 ``invocation_origin`` of an assigned call (C-3)."""
    target = target_provider(a, purpose)
    return {"origin_kind": "card_assigned", "purpose": purpose, "role": _PURPOSE_ROLE.get(purpose),
            "role_source": a.get("source"), "requested_ai_id": target if isinstance(target, str) and target != NONE
            else None,
            "target": {"flow_id": flow_id, "graph_run_id": a["run_id"], "node_key": a["node_key"],
                       "attempt_id": a["attempt_id"], "stage": a["stage"], "seq": a["seq"],
                       "doc_id": (doc or {}).get("doc_id"),
                       "doc_revision_no": int(doc.get("revision_no") or 0) if doc else None},
            "round_no": round_no}


def ref_of(a: dict) -> dict:
    return {"attempt_id": a["attempt_id"], "stage": a["stage"], "seq": a["seq"]}


def legacy_origin(flow_id: str, run_id: str, node_key: str, attempt_id: Optional[str], stage: Optional[str],
                  purpose: str) -> dict:
    """P-1 §7.2: a card call of a pre-feature attempt (no ``assignment_ref``)."""
    return {"origin_kind": "card_legacy_attempt", "purpose": purpose, "role": _PURPOSE_ROLE.get(purpose),
            "role_source": "legacy_attempt", "requested_ai_id": None,
            "target": {"flow_id": flow_id, "graph_run_id": run_id, "node_key": node_key, "attempt_id": attempt_id,
                       "stage": stage, "seq": 0, "doc_id": None, "doc_revision_no": None}, "round_no": None}


def _write_job(ctx, node: dict, a: dict, purpose: str, issued_to: str, token: dict, stage: str,
               result_doc_type: str, ref_doc_ids: Optional[list] = None) -> Optional[dict]:
    """The AI write job of an assigned stage: one explicit provider, the plan's type, its ledger reference."""
    target = target_provider(a, purpose)
    if not isinstance(target, str) or target == NONE:
        return None
    job = legacy.ai_job(ctx, node, target, issued_to, token, stage, ref_doc_ids)
    job.update(result_doc_type=result_doc_type, assignment_ref=ref_of(a),
               invocation_origin=origin(a, purpose, ctx.flow_id))
    if stage == INSTRUCTION:
        job["instruction"] = {"doc_type": result_doc_type, "provider_id": target}
    return job


def _request(a: dict, job: dict) -> None:
    """The write's ledger request in the same guard as its assignment, recorded as the assignment's intent once
    (L-2 §2.2.2 / §3.2: ``active``, ``intent = exec``). A refusal propagates: the whole guard rolls back."""
    exec_id = legacy.request_write(job)
    if exec_id:
        store.set_intent(a["attempt_id"], a["stage"], a["seq"], store.INTENT_EXECUTION, exec_id)


# ── the node's opening (L-2 §2.4) ─────────────────────────────────────────────

def instruction_line_document(flow_id: str, node: dict, plan: dict) -> dict:
    """``{doc_id, written}`` of the card's instruction line (TEST: its TS line) — an invalidated attempt's document
    counts as none (0061 T#7)."""
    if not plan.get("instruction_type"):
        return {"doc_id": None, "written": False}
    line = card_lines.line_document(flow_id, node["node_key"], card_slots.INSTRUCTION, node.get("node_type"),
                                    node.get("doc_type")) or {}
    doc = line.get("document")
    if doc and doc["doc_id"] in legacy.invalidated_documents(flow_id, node["node_key"]):
        doc = None
    return {"doc_id": doc["doc_id"] if doc else None, "written": card_lines.has_body(doc)}


def dispatch_role_card(ctx, node: dict) -> Optional[dict]:
    """L-2 §2.4.1, the node's opening inside the run step's Flow guard. None: the current dispatch takes the node."""
    if not role_card(node) or not enabled():
        return None
    key, flow_id = node["node_key"], ctx.flow_id
    attempt_id = legacy.active_attempt_id(ctx.run_id, key)
    if attempt_id is None:
        return None
    kind = kind_of(node)
    issued_to = issued_to_of(node, ctx.run, ctx.actor)
    if store.has_any(attempt_id):
        return _redispatch(ctx, node, attempt_id, issued_to)          # the same attempt again (L-2 §2.10 retry)
    try:
        stored = legacy.isolated(lambda: repository.values(flow_id, key), "fg_stage_plan_read")
    except Exception:  # noqa: BLE001 — E-05: the plan takes the defaults, the resolution reads store_error
        stored = {}
    plan = stage_plan_of(kind, node, stored)
    first = INSTRUCTION if plan["instruction_type"] else RESULT
    found = instruction_line_document(flow_id, node, plan) if first == INSTRUCTION else {"doc_id": None}
    reuse = found["doc_id"] if found.get("written") else None
    a = start_stage(flow_id, ctx.run, node, attempt_id, first, 0, opening=True, reused_doc_id=reuse, plan=plan,
                    project_id=ctx.project_id, ctx=ctx)
    if a == LEGACY:
        return None
    if first == INSTRUCTION:                                          # 0038-T A-2: the report baseline
        # a baseline that cannot be recorded raises (``BaselineUnavailable``): the step's guard rolls the opening back
        report_baseline.record_at_opening(attempt_id, ctx.run_id, flow_id, key, bool(reuse),
                                          report_line(flow_id, node, plan)[1], reuse)
    taken = written_result(flow_id, node, plan, attempt_id) if first == RESULT else None
    legacy.set_stage(ctx.run_id, key, first, reuse)
    if reuse:
        legacy.fill_line(ctx, key, reuse)
    provider = a["provider_id"] if a["actor"] == AI and not reuse and taken is None else None
    shown = provider or issued_to
    token = legacy.issue_node_token(flow_id, ctx.project_id, legacy.root_doc_id(ctx), issued_to, provider)   # T-1
    if token is None:
        fail(a, TOKEN_ISSUE_FAILED, ctx)
        token_failed(ctx.run_id, key, attempt_id, first, 0, TOKEN_NODE, issued_to, ctx)
        return {"token_issued": False, "mention_id": None, "issued_to": shown, "error_code": TOKEN_ISSUE_FAILED}
    legacy.map_token(token["token_id"], ctx.run_id, key)
    if first == INSTRUCTION:
        error = act_instruction_stage(ctx, node, a, issued_to, token, found, plan)
    else:
        error = act_result_stage(ctx, node, a, issued_to, None, plan, token)
    out = {"token_issued": True, "mention_id": None, "issued_to": shown}
    if error:
        out["error_code"] = error
    return out


def act_instruction_stage(ctx, node: dict, a: dict, issued_to: str, token: dict, found: dict,
                          plan: dict) -> Optional[str]:
    """L-2 §2.4.2. Returns the dispatch error to record, or None."""
    from flowgate.card_settings import assigned_review, manual_stage  # noqa: PLC0415 — the stages orchestrate on

    key, flow_id, run_id = node["node_key"], ctx.flow_id, ctx.run_id
    if a.get("reused_doc_id"):
        if a["stage_state"] in (ACTIVE, WAITING):
            move(a, DONE)
        doc_id, attempt_id = a["reused_doc_id"], a["attempt_id"]

        def proceed() -> None:
            # 0113 T#3 rework: the opening's (or the command's) own settlement judged the node before this post-commit
            # step made its review assignment, and nothing judged the run again (an idle run stayed ``no_targets``):
            # the run is settled once the step is over, whatever it did.
            try:
                manual_stage.proceed_written_instruction(flow_id, run_id, key, attempt_id, doc_id, None,
                                                         assigned_review.HELD)
            finally:
                legacy.settle_graph_run(flow_id)

        ctx.jobs.append(proceed)
        return None
    if a["actor"] == AI:
        job = _write_job(ctx, node, a, INSTRUCTION_WRITE, issued_to, token, INSTRUCTION, plan["instruction_type"])
        if job is None:
            fail(a, NO_PROVIDER, ctx)
            return "ai_unavailable:" + NO_PROVIDER
        if found.get("doc_id"):
            legacy.set_stage(run_id, key, INSTRUCTION, found["doc_id"])
        _request(a, job)
        ctx.jobs.append(lambda: legacy.start_ai(job))
        return None
    if a["actor"] == PERSON:
        t2 = legacy.issue_node_token(flow_id, ctx.project_id, legacy.root_doc_id(ctx), issued_to, None)     # T-2
        if t2 is None:
            token_failed(run_id, key, a["attempt_id"], INSTRUCTION, 0, TOKEN_INSTRUCTION, issued_to, ctx)
        else:
            legacy.map_token(t2["token_id"], run_id, key, INSTRUCTION)
        return None
    if a["actor"] == WAIT_UNAVAILABLE:
        return "ai_unavailable:" + str(a.get("cause"))
    return None                                                           # WAIT_SETUP: waits, no error


def report_line(flow_id: str, node: dict, plan: dict) -> tuple[Optional[dict], Optional[dict]]:
    """(line, its live document) of the card's result line of the plan's result type (RUN / TEST: the report line,
    PLAN: the plan line); an invalidated attempt's document counts as none."""
    slot = card_slots.PLAN if plan.get("result_type") == "WP" else card_slots.REPORT
    line = card_lines.line_document(flow_id, node["node_key"], slot, node.get("node_type"), node.get("doc_type"))
    if not line:
        return None, None
    doc = line.get("document")
    if doc and doc["doc_id"] in legacy.invalidated_documents(flow_id, node["node_key"]):
        doc = None
    return line, doc


def result_has_body(kind: Optional[str], doc: Optional[dict]) -> bool:
    if not doc:
        return False
    if kind == fields.PLAN:
        return legacy.work_sets(doc) >= 1
    return card_lines.has_body(doc)


_NOT_WRITTEN_STATES = ("rejected",)                 # judged not to pass: its revision is a new report


def written_result(flow_id: str, node: dict, plan: dict, attempt_id: Optional[str]) -> Optional[dict]:
    """0038-T T#3 A (D 개정 §3.6-0-1 ~ 3): the card's report that satisfies its result stage — the result line's live
    document of the card with a body ("있음": the saved body, blank is none), whether still a draft, submitted or
    approved, never a cancelled or rejected one, nor the attempt's report baseline (A-2), nor a result an earlier
    attempt of the run had bound (a Stop's or a reopening's history, 0061 T#7) — or None. A plan with an instruction
    whose baseline cannot be read raises ``report_baseline.BaselineUnavailable``: no report is taken on an unknown
    baseline (a plan without one never records a baseline)."""
    line, doc = report_line(flow_id, node, plan)
    if doc is None or not line or not line.get("owned", True):
        return None
    if doc.get("status") == "cancelled" or doc.get("doc_review_status") in _NOT_WRITTEN_STATES:
        return None
    if not result_has_body(kind_of(node), doc):
        return None
    if plan.get("instruction_type") and report_baseline.is_baseline(attempt_id, doc):
        return None
    if legacy.earlier_result(attempt_id, node["node_key"], doc["doc_id"]):
        return None
    return doc


def take_written_result(ctx, node: dict, a: dict, issued_to: str, doc: dict, plan: dict) -> Optional[str]:
    """0038-T T#3 A-4: the result stage's report is written already — no writer is asked (no AI, no manual write). The
    report is registered as the node's result with the node token (T-3 when the instruction spent it; a document that
    was submitted or judged already keeps its review state) and goes to this attempt's result review after the commit:
    a person's stage through the binder's ``person_result``, any other (an AI, a wait) through the review opening of a
    finished result (``after_result_work``, its author from the records).

    A report approved before (an earlier attempt's or run's judgement) is no judgement of this attempt: it is recorded
    as taken judged (``legacy.record_taken_judged``) and reviewed as any other — an AI review, the automatic approval
    or the review's wait — and the binder completes the node only once this attempt's review passed or approved it
    automatically. Returns the dispatch error to record, or None."""
    from flowgate.card_settings import assigned_review  # noqa: PLC0415 — the stages orchestrate on use

    key, flow_id, run_id = node["node_key"], ctx.flow_id, ctx.run_id
    token_id = legacy.live_dispatch_token(run_id, key)
    if token_id is None:                                                  # T-3
        fresh = legacy.issue_node_token(flow_id, ctx.project_id, legacy.root_doc_id(ctx), issued_to, None)
        if fresh is None:
            fail(a, TOKEN_ISSUE_FAILED, ctx)
            token_failed(run_id, key, a["attempt_id"], RESULT, 0, TOKEN_RESULT, issued_to, ctx)
            return TOKEN_ISSUE_FAILED
        legacy.map_token(fresh["token_id"], run_id, key)
        token_id = fresh["token_id"]
    person = a["actor"] == PERSON
    if not person and a["stage_state"] in (ACTIVE, WAITING):
        move(a, DONE)                                                     # nobody writes: the stage is over
    if not legacy.register_result(doc["doc_id"], token_id, issued_to):
        return None
    doc_id, doc_type, attempt_id = doc["doc_id"], plan["result_type"], a["attempt_id"]
    if doc.get("doc_review_status") in assigned_review.APPROVED:
        legacy.record_taken_judged(run_id, key, attempt_id, doc, mark(doc), ctx)

    def follow() -> None:
        legacy.tell_graph(flow_id, doc_id, doc_type, token_id)
        if not person:
            assigned_review.after_result_work(flow_id, run_id, node, doc_id, attempt_id, legacy.written_by(doc_id),
                                              assigned_review.HELD)

    ctx.jobs.append(follow)
    return None


def act_result_stage(ctx, node: dict, a: dict, issued_to: str, instruction_doc_id: Optional[str], plan: dict,
                     token: Optional[dict] = None) -> Optional[str]:
    """L-2 §2.5 ``act_result_stage``. Returns the dispatch error to record, or None. 0038-T T#3 A: a written report
    (``written_result``) is taken for every actor, before any writer is asked."""
    key, flow_id, run_id = node["node_key"], ctx.flow_id, ctx.run_id
    written = written_result(flow_id, node, plan, a["attempt_id"]) if a["stage_state"] != FAILED else None
    if written is not None:
        return take_written_result(ctx, node, a, issued_to, written, plan)
    if a["actor"] == AI:
        tokens = legacy.live_dispatch_token(run_id, key)
        job = _write_job(ctx, node, a, RESULT_WRITE, issued_to, token or {"token_id": tokens}, RESULT,
                         plan["result_type"], [instruction_doc_id] if instruction_doc_id else None)
        if job is None:
            fail(a, NO_PROVIDER, ctx)
            return "ai_unavailable:" + NO_PROVIDER
        _request(a, job)
        ctx.jobs.append(lambda: legacy.start_ai(job))
        return None
    if a["actor"] == PERSON:
        token_id = legacy.live_dispatch_token(run_id, key)
        if token_id is None:                                              # T-3
            fresh = legacy.issue_node_token(flow_id, ctx.project_id, legacy.root_doc_id(ctx), issued_to, None)
            if fresh is None:
                fail(a, TOKEN_ISSUE_FAILED, ctx)
                token_failed(run_id, key, a["attempt_id"], RESULT, 0, TOKEN_RESULT, issued_to, ctx)
                return TOKEN_ISSUE_FAILED
            legacy.map_token(fresh["token_id"], run_id, key)
        return None                                                       # the manual write wait
    if a["actor"] == WAIT_UNAVAILABLE:
        return "ai_unavailable:" + str(a.get("cause"))
    return None


def result_stage_start(ctx, node: dict, row: dict, instruction_doc_id: Optional[str], attempt_id: Optional[str]) -> bool:
    """L-2 §2.5 inside ``node_dispatch.start_result_stage``'s guard (the node still open in its instruction stage, its
    refusals judged): False = a pre-feature attempt, the current body goes on."""
    if not role_card(node) or not enabled():
        return False
    key = node["node_key"]
    attempt = attempt_id or legacy.active_attempt_id(ctx.run_id, key)
    if is_legacy_attempt(attempt):
        return False
    a = start_stage(ctx.flow_id, ctx.run, node, attempt, RESULT, 0, project_id=ctx.project_id, ctx=ctx)
    if a == LEGACY:
        return False
    legacy.set_stage(ctx.run_id, key, RESULT, instruction_doc_id)
    legacy.mark_result_floor(ctx.run_id, key)
    if instruction_doc_id:
        legacy.fill_line(ctx, key, instruction_doc_id)
    plan = stage_plan(attempt) or stage_plan_of(kind_of(node), node, {})
    error = act_result_stage(ctx, node, a, issued_to_of(node, ctx.run, ctx.actor), instruction_doc_id, plan)
    if error:
        legacy.set_dispatch_error(ctx.run_id, key, error)
    return True


# ── an explicit command (L-2 §2.10) ──────────────────────────────────────────

def needs_dispatch(ctx, row: dict) -> str:
    """``LEGACY`` (the current judgement), or what an explicit command does with the node.

    0038-T T#3 B-5 / B-6 (rework): a failed stage is the command's explicit retry (``REOPEN_NEW_ATTEMPT``); a waiting
    write stage — a person's manual write, an AI setup wait, an unavailable AI's wait — is resolved again only when
    ``reinterpret_due`` says so (``REINTERPRET``), else it keeps waiting (``NONE``: its attempt, assignment and tokens
    stay). A review stage's setup / unavailable wait (its document under review) keeps L-2 §2.10's new attempt."""
    key = row["node_key"]
    node = ctx.node_rows.get(key) or {}
    if not role_card(node) or not enabled():
        return LEGACY
    if key in legacy.held_nodes(ctx.flow_id):
        return NONE
    attempt_id = legacy.active_attempt_id(ctx.run_id, key)
    a = current_stage_assignment(attempt_id, row)
    if a == LEGACY:
        return LEGACY
    if a is None and row["status"] == "open" and row.get("stage") is None and row.get("result_doc_id") is None:
        return DISPATCH
    if a is None or row["status"] != "open":
        return NONE
    if a["stage"] == RESULT and a["actor"] == AI:
        failure = legacy.write_failure(row)
        if failure == "no_result":
            return RETRY_SAME_ATTEMPT
        # flowgate.v02.0121 T#2: a writer whose provider failed (``provider_failed``: a model at capacity, a dropped
        # stream) with no result is written again on the same attempt by the same assigned AI, as ``no_result`` is —
        # its workspace, branch and files kept; the failed execution and AI run stay recorded. Its assignment stays
        # ``active`` (the end is told by the durable records, never by a state rewritten); a failed assignment keeps
        # its new attempt (below), and every other failure (cancel, pause, timeout, providers_exhausted, ...) its own
        # policy (``NONE``).
        if failure == "ai_invoke_failed" and a["stage_state"] == ACTIVE and legacy.provider_failure_retry_due(row):
            return RETRY_SAME_ATTEMPT
    if a["stage_state"] == FAILED:
        return REOPEN_NEW_ATTEMPT
    if a["stage"] in (INSTRUCTION, RESULT) and a["actor"] in WAIT_ACTORS:
        return REINTERPRET if reinterpret_due(ctx, node, row, a) else NONE
    if a["actor"] in (WAIT_SETUP, WAIT_UNAVAILABLE):
        return REOPEN_NEW_ATTEMPT
    return NONE


WAIT_ACTORS = (PERSON, WAIT_SETUP, WAIT_UNAVAILABLE)                 # the waiting write stages (B-6)


def _same_assignment(a: dict, now: dict) -> bool:
    """The recorded write assignment and the one the role resolves to now are the same (actor and AI)."""
    if a["actor"] != now["actor"]:
        return False
    if a["actor"] in (AI, WAIT_UNAVAILABLE):
        return a.get("provider_id") == now.get("provider_id")
    return True


def reinterpret_due(ctx, node: dict, row: dict, a: dict) -> bool:
    """0038-T T#3 B-5 / B-6 (D 개정 §3.5.1-3·4·8): a waiting write stage (the instruction or the result stage, waiting:
    a person's manual write, an AI setup wait, an unavailable AI's wait) with no document in it is resolved again by
    the first command after its role changed — True when the stage's role resolves now (the card's values, read only:
    nothing initialized, nothing written) to another assignment (actor, AI) than the recorded one. A role that did not
    change keeps the wait (False: the attempt, its assignment and tokens stay); a changed value alone calls nothing
    (the command does). A stage holding a document keeps its assignment: a registered instruction or a bound result,
    and for a person's stage a saved one too (its own save judges it). A setup / unavailable wait whose stage got a
    saved, unregistered document meanwhile goes on by its documents (True: the new attempt reuses the instruction or
    takes the written report — no writer asked). A report whose baseline cannot be read keeps the wait."""
    if a["actor"] not in WAIT_ACTORS or a["stage_state"] != WAITING or a["stage"] not in (INSTRUCTION, RESULT):
        return False
    if row.get("status") != "open" or row.get("result_doc_id") or row.get("stage") != a["stage"]:
        return False
    flow_id = ctx.flow_id
    plan = stage_plan(a["attempt_id"]) or stage_plan_of(kind_of(node), node, {})
    if a["stage"] == INSTRUCTION:
        if row.get("instruction_doc_id"):
            return False
        saved = bool(instruction_line_document(flow_id, node, plan).get("written"))
    else:
        try:
            saved = written_result(flow_id, node, plan, a["attempt_id"]) is not None
        except report_baseline.BaselineUnavailable:
            _log.warning("report baseline unknown: the wait stays flow=%s node=%s", flow_id, node.get("node_key"),
                         exc_info=True)
            return False
    if saved:
        return a["actor"] != PERSON
    try:
        inputs = resolve.read_card_role_inputs(flow_id, node)
        r = resolve.resolve_role(flow_id, node, ROLE_OF[a["stage"]], project_id=ctx.project_id, inputs=inputs)
        now = assignment_from(a["stage"], r, ctx.run.get("started_by"))
    except Exception:  # noqa: BLE001 — a value that cannot be read now changes nothing: the wait stays
        _log.warning("stage role not resolved again flow=%s node=%s", flow_id, node.get("node_key"), exc_info=True)
        return False
    return not _same_assignment(a, now)


def stage_provider(run_id: str, node_key: str, attempt_id: Optional[str], stage: str):
    """L-2 §2.9 retry rows: the same attempt's stage assignment AI (``LEGACY``: the current provider choice; None:
    the stage is no AI's, nothing is written again)."""
    attempt = attempt_id or legacy.active_attempt_id(run_id, node_key)
    if is_legacy_attempt(attempt):
        return LEGACY
    a = store.read(attempt, stage, 0)
    if a is None or a["actor"] != AI:
        return None
    return a


def _row_dispatch(row: Optional[dict]) -> dict:
    row = row or {}
    out = {"token_issued": bool(row.get("dispatch_token_issued")), "mention_id": row.get("dispatch_mention_id"),
           "issued_to": row.get("dispatch_issued_to")}
    if row.get("dispatch_error_code"):
        out["error_code"] = row["dispatch_error_code"]
    return out


def _redispatch(ctx, node: dict, attempt_id: str, issued_to: str) -> dict:
    """An explicit command's dispatch of an assigned attempt's open node: only ``RETRY_SAME_ATTEMPT`` writes (the same
    result assignment's AI, the node token reused or issued again); anything else keeps the node's dispatch."""
    key = node["node_key"]
    row = legacy.node_run(ctx.run_id, key)
    a = current_stage_assignment(attempt_id, row or {})
    if not isinstance(a, dict) or a["stage"] != RESULT or a["actor"] != AI or row is None:
        return _row_dispatch(row)
    token_id = legacy.live_dispatch_token(ctx.run_id, key)
    if token_id is None:
        fresh = legacy.issue_node_token(ctx.flow_id, ctx.project_id, legacy.root_doc_id(ctx), issued_to, a["provider_id"])
        if fresh is None:
            token_failed(ctx.run_id, key, attempt_id, RESULT, 0, TOKEN_NODE, issued_to, ctx)
            return {**_row_dispatch(row), "token_issued": False, "error_code": TOKEN_ISSUE_FAILED}
        legacy.map_token(fresh["token_id"], ctx.run_id, key)
        token_id = fresh["token_id"]
    plan = stage_plan(attempt_id) or {}
    instruction_doc_id = row.get("instruction_doc_id")
    job = _write_job(ctx, node, a, RESULT_WRITE_RETRY, issued_to, {"token_id": token_id}, RESULT,
                     plan.get("result_type") or node.get("doc_type"),
                     [instruction_doc_id] if instruction_doc_id else None)
    if job is None:
        return _row_dispatch(row)
    legacy.request_write(job)
    ctx.jobs.append(lambda: legacy.start_ai(job))
    return {"token_issued": True, "mention_id": None, "issued_to": a["provider_id"]}


def retry_target(run_id: str, node_key: str, attempt_id: Optional[str], stage: str) -> Optional[dict]:
    """``card_settings_ports.stage_retry``: None (a pre-feature attempt: the current provider), ``{}`` (the stage is no
    AI's: nothing is written again) or the assigned AI with its ledger reference and origin."""
    found = stage_provider(run_id, node_key, attempt_id, stage)
    if found == LEGACY:
        return None
    if found is None:
        return {}
    plan = stage_plan(found["attempt_id"]) or {}
    purpose = RESULT_WRITE_RETRY if stage == RESULT else INSTRUCTION_WRITE_RETRY
    return {"provider_id": found["provider_id"], "assignment_ref": ref_of(found),
            "invocation_origin": origin(found, purpose, found["group_id"]),
            "result_doc_type": plan.get("result_type") if stage == RESULT else plan.get("instruction_type")}


def start_failed(job: dict) -> Optional[str]:
    """``card_settings_ports.stage_failed``, inside ``record_ai_failure``'s guard: the job's assigned stage failed to
    start (``start_failed`` / ``start_refused:<code>``) — its assignment fails once and the node records
    ``ai_unavailable:<cause>`` (P-1 C-2). None for a pre-feature job (no assignment reference)."""
    ref = job.get("assignment_ref") or {}
    if not ref or not enabled():
        return None
    a = store.read(ref["attempt_id"], ref["stage"], int(ref["seq"]))
    if a is None or a["actor"] != AI:
        return None
    cause = job.get("start_failure") or START_FAILED
    failed = fail(a, cause)
    return "ai_unavailable:" + str(failed.get("fail_cause") or cause)
