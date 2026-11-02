"""The own effects of a TS / TR2 result, run by the v0.2 completion itself (flowgate.v02.0119 T#2, T-1).

A TS's and a TR2's completion have an effect of the document's own that the v0.1 approval used to carry: a TS admits its
test run (the specification fixed and its automated Cases queued), a TR2 applies its source change (precheck, source
lock, apply, validation, commit). The v0.2 completion runs that effect here, explicitly — never through the v0.1 review
transition (``pipeline_service.transition_document_review``) and with no person recorded as its approver:

    TS    in one transaction under the test admission lock: the completion write (record + status display) and the
          admission of the run (``spec_execution_service.admit_on_approval``) commit together or not at all. A document
          already approved at this revision had its admission made with that approval (atomic as well): only the
          completion is written. The run's ``runner_id`` is the run's issuer (who asked for it), the event's actor the
          system.
    TR2   the strong TR2 apply (``tr2_approval_service.approve``) under the system actor, with the completion write as its
          finalize hook: the record and the document's approved display commit in the TR2's own finalize transaction, so
          an applied change never lacks its completion (and a rolled back one never has it). A document already approved
          at this revision with a succeeded TR2 attempt (the change applied before, its completion lost) needs no apply:
          the caller writes the completion (the recovery path); approved with no succeeded attempt is refused.

``write`` is the caller's completion write (``() -> record``, run inside the effect's transaction); with no node it is
the status display alone. Raises ``EffectRefused`` (the effect is refused: nothing is written) or ``EffectRevisionMoved``
(the document's revision is not ``revision`` any more).

Listed for the directory move (WP 부록 A): ``services.test_run_service`` / ``spec_execution_service`` /
``test_spec_service`` / ``test_basis_service``, ``documents.tr2_approval_service`` / ``tr2_service`` /
``tr2_errors``, ``db.tr2_approval_attempts``, ``services.mutation_policy.system_principal``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)

TS, TR2 = "TS", "TR2"
TYPES = (TS, TR2)
APPROVED = ("approved", "wf_done")
SYSTEM_ACTOR = "system"
LOCALE = "ko"
# The TR2 apply's attempt key of a card completion (the unmanned chain's own is "auto:").
TR2_REQUEST_KEY_PREFIX = "card:"
TR2_REQUEST_KEY_LEN = 48
ADMITTED_EVENT = "test_spec_execution_admitted"


class EffectRefused(RuntimeError):
    """The document's own effect was refused (nothing of it, nor the completion, is written)."""

    def __init__(self, code: str, detail: Optional[str] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class EffectRevisionMoved(RuntimeError):
    """The document's revision is not the one the completion is for any more."""


def type_of(doc: Optional[dict]) -> str:
    return str((doc or {}).get("type_code") or "").upper()


def has_effect(doc: Optional[dict]) -> bool:
    return type_of(doc) in TYPES


def run(doc: dict, *, flow_id: str, issuer: str, permissions, revision: int, write: Callable[[], object]):
    """Run the document's own effect with the completion ``write``; what ``write`` returned when it ran here (None: the
    effect was made before — the caller writes the completion)."""
    kind = type_of(doc)
    if kind == TS:
        return _ts(doc["doc_id"], int(revision), issuer, write)
    if kind == TR2:
        return _tr2(flow_id, doc["doc_id"], int(revision), issuer, permissions, write)
    raise ValueError(f"no own effect for type {kind!r}")


# ── TS: the admission of its test run ────────────────────────────────────────

def _ts_plan(doc: dict):
    """``(parsed, selected)`` of a specification-contract TS (selected empty: its current passing report is kept), or
    None (no specification contract: nothing to admit). Refuses an automated Case whose test file is missing or unsafe."""
    from modules.flow_gate.services import spec_execution_service, test_spec_service  # noqa: PLC0415
    from modules.flow_gate.services import test_run_service as runner  # noqa: PLC0415

    content = runner._read_doc_content_or_empty(doc)
    if test_spec_service.detect_contract_version(content) != test_spec_service.CONTRACT_SPEC:
        return None
    parsed = test_spec_service.parse_spec(content)
    selected = spec_execution_service.automated_selection(parsed["cases"])
    paired = runner._active_tsr_for_ts(doc)
    if paired and (paired.get("doc_review_status") == "approved" or runner.tsr_gate_state(paired).get("passed")):
        selected = []                                    # its current passing report is kept, no other run queued
    if selected:
        try:
            spec_execution_service.check_automation_assets(doc, selected)
        except ValueError as exc:
            raise EffectRefused("ts_automation_assets", str(exc)) from exc
    return parsed, selected


def _admit(doc: dict, parsed: dict, selected: list, runner_id: str) -> dict:
    """The test run admitted (inside the caller's transaction): ``{run, tsr_doc_id, selected_case_ids}``."""
    from modules.flow_gate.services import spec_execution_service  # noqa: PLC0415

    return spec_execution_service.admit_on_approval(doc, parsed, selected, runner_id=runner_id, locale=LOCALE)


def _announce(doc: dict, admitted: dict) -> None:
    """After the commit: the run and its report are visible before anyone is told."""
    from modules.flow_gate.services import test_run_service as runner  # noqa: PLC0415

    runner._emit_started(doc, admitted["run"])
    runner._broadcast("group_view_refresh", doc, {
        "group_id": doc.get("group_id"), "reason": "test_report_opened", "doc_id": admitted["tsr_doc_id"],
        "run_id": admitted["run"]["run_id"]})


def _ts(doc_id: str, revision: int, runner_id: str, write: Callable[[], object]):
    from modules.flow_gate.db import documents as db_docs  # noqa: PLC0415
    from modules.flow_gate.db import events as db_events  # noqa: PLC0415
    from modules.flow_gate.db.connection import get_store  # noqa: PLC0415
    from modules.flow_gate.services import spec_execution_service, test_basis_service  # noqa: PLC0415
    from modules.flow_gate.services import test_run_service as runner  # noqa: PLC0415

    store = get_store()
    admitted = None
    shown = None
    with runner._admission_lock, store.transaction():
        doc = db_docs.get_by_id(doc_id)
        if doc is None or int(doc.get("revision_no") or 0) != revision:
            raise EffectRevisionMoved(doc_id)
        plan = None if doc.get("doc_review_status") in APPROVED else _ts_plan(doc)   # approved: admitted with it
        written = write()
        if plan is not None:
            parsed, selected = plan
            shown = db_docs.update(doc_id, {"meta": test_basis_service.metadata_without_basis({"meta": doc.get("meta")})})
            if not shown:
                raise EffectRefused("ts_update_failed")
            if selected:
                blocker = spec_execution_service.admission_blocker(doc_id)
                if blocker:
                    raise EffectRefused("ts_run_blocked", json.dumps(blocker, ensure_ascii=False, default=str))
                admitted = _admit(shown, parsed, selected, runner_id)
                db_events.insert_event(doc_id, ADMITTED_EVENT, note=json.dumps({
                    "run_id": admitted["run"]["run_id"], "tsr_doc_id": admitted["tsr_doc_id"],
                    "admission": spec_execution_service.ADMISSION_APPROVAL, "ts_revision_no": revision,
                    "case_ids": admitted["selected_case_ids"], "actor": SYSTEM_ACTOR, "requested_by": runner_id,
                }, ensure_ascii=False))
        fresh = db_docs.get_by_id(doc_id)
        if fresh is None or int(fresh.get("revision_no") or 0) != revision:
            raise EffectRevisionMoved(doc_id)
    if admitted:
        _announce(shown, admitted)
    return written


# ── TR2: its source change ───────────────────────────────────────────────────

def _retryable_tr2(exc: Exception) -> bool:
    """A TR2 refusal retried once with a new attempt key (as the unmanned chain's ``settle_completed_step``)."""
    from modules.flow_gate.documents import tr2_service  # noqa: PLC0415
    from modules.flow_gate.documents.tr2_errors import TR2_ERRORS  # noqa: PLC0415

    return (isinstance(exc, tr2_service.Tr2ValidationError) and exc.code != "tr2_in_progress"
            and exc.code in TR2_ERRORS and bool(TR2_ERRORS[exc.code].retryable))


def _tr2_succeeded(doc_id: str, revision: int) -> bool:
    """A succeeded TR2 attempt applied this revision's change."""
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts  # noqa: PLC0415

    success = db_attempts.latest_success(doc_id)
    return bool(success) and int(success.get("document_revision") or -1) == int(revision)


def _tr2_apply(flow_id: str, doc_id: str, revision: int, issuer: str, permissions, request_key: str,
               hook: Callable[[dict], None]) -> None:
    from modules.flow_gate.documents import tr2_approval_service  # noqa: PLC0415
    from modules.flow_gate.services.mutation_policy import system_principal  # noqa: PLC0415

    tr2_approval_service.approve(
        doc_id=doc_id, actor_user_id=SYSTEM_ACTOR, user_permissions=set(permissions),
        mutation_principal=system_principal(user_id=issuer, group_id=flow_id), expected_revision=revision,
        request_key=request_key, locale=LOCALE, completion_hook=hook)


def _tr2(flow_id: str, doc_id: str, revision: int, issuer: str, permissions, write: Callable[[], object]):
    from modules.flow_gate.db import documents as db_docs  # noqa: PLC0415

    doc = db_docs.get_by_id(doc_id) or {}
    if int(doc.get("revision_no") or 0) != revision:
        raise EffectRevisionMoved(doc_id)
    if doc.get("doc_review_status") in APPROVED:
        if _tr2_succeeded(doc_id, revision):
            return None                                  # applied before, its completion lost: the caller writes it
        raise EffectRefused("tr2_approved_without_attempt")
    out: dict = {}

    def hook(_fresh: dict) -> None:                      # inside the TR2's finalize transaction
        out["written"] = write()

    for attempt in range(2):
        digest = hashlib.sha256(f"{doc_id}:{revision}:{attempt}".encode("utf-8")).hexdigest()
        try:
            _tr2_apply(flow_id, doc_id, revision, issuer, permissions,
                       TR2_REQUEST_KEY_PREFIX + digest[:TR2_REQUEST_KEY_LEN], hook)
            return out.get("written")
        except Exception as exc:  # noqa: BLE001 — a retryable refusal is tried once more with a new key
            if attempt == 0 and _retryable_tr2(exc):
                continue
            raise
    return out.get("written")
