"""AI invoke endpoints (flowgate.default.0187 P0005).

POST /api/v1/ai-invoke/start          — admit + launch a run (session auth)
GET  /api/v1/ai-invoke/{run_id}       — status (running / finished payload)
GET  /api/v1/ai-invoke/active         — active run for a group (session auth)
POST /api/v1/ai-invoke/{run_id}/cancel — tree-kill cancel

The work token is minted server-side and injected only into the run's
environment — unlike the copy-mention flow, the raw token is never returned to
the browser (P0005, notation rules).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import invoke_mention_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services import workflow_decision_service
from modules.flow_gate.services import work_plan_service
from modules.flow_gate.workflow import prompt_copy_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.id_validators import (
    validate_doc_id,
    validate_group_id,
    validate_project_id,
)

router = APIRouter(prefix="/api/v1/ai-invoke", tags=["AiInvoke"])


def _operator_facing_api_base(request: Request) -> str:
    """The OPERATOR-FACING API base for this request — never a CLI target.

    Named, rather than calling ``token_routes._build_api_base`` inline, because
    the two bases in this flow are easy to confuse and 0472 B0001 is what happens
    when they are: the operator base (the origin the browser reached us on, no
    explicit port in this deployment) was passed through to the CLI worker, which
    then called ``http://127.0.0.1/flowgate/api/v1`` — outside the reverse proxy's
    ``Host: flowgate.stg`` route, so every worker request got an empty 200 back
    and no chat ever arrived.

    Every ``api_base_url=`` in this module is this operator base and nothing else.
    It is what a person would see in a copied mention. The AGENT-FACING base the
    CLI process actually dials is derived from it downstream, in one place:
    ``ai_invoke_service._resolve_agent_api_base`` (``FLOWGATE_AGENT_API_BASE``
    when set, otherwise loopback keeping the explicit operator port or
    ``settings.FLOWGATE_PORT``), applied at CLI launch by
    ``_canonicalize_cli_prompt``. Do not rewrite the base here, and do not hand
    this value to a subprocess as ``FLOWGATE_API_BASE``.
    """
    from modules.flow_gate.api import token_routes as _token_routes

    return _token_routes._build_api_base(request)


class DocumentReviewLoopRequest(BaseModel):
    review_count: int
    reviewer_provider_id: str
    review_criteria: str
    rework_provider_id: str
    rework_timeout_sec: int
    rework_message: str = ""
    failure_restart_max_attempts: int
    total_timeout_sec: int


class AiInvokeStartRequest(BaseModel):
    project: str
    module: Optional[str] = None
    group: str
    doc_ref: Optional[str] = None
    action_scope: str = "new"
    mode: str = "single"
    continuation_target_seq: Optional[int] = None
    continuation_review_mode: bool = False
    continuation_instruction_mode: Optional[str] = None
    # 0352 T0004 §2/§3.4: the ai_direct chain's per-item_seq N/T auto-approve selection.
    # Validated (422) below when the request is a fresh continuous 'new' start.
    continuation_auto_approve_item_seqs: Optional[list[int]] = None
    # 0317 T0010 rev4: item_seq (string keys) -> provider_id, from ContinuousWorkDialog's
    # per-step override table. Session-scoped — consulted once, at start_run, never persisted.
    continuation_provider_overrides: Optional[dict[str, str]] = None
    # 0346 T0005: handoff-note tab values — a common note for every hop and item_seq (string
    # keys) -> note overrides for individual hops. Session-scoped, same as the provider map.
    continuation_default_note: Optional[str] = None
    continuation_note_overrides: Optional[dict[str, str]] = None
    # flowgate.default.0400 M0005 + 0446 T0010 §3-1: this run's wall-clock budget in seconds.
    # The name says `continuation_` but the field is mode-independent and always was: the
    # range check below never looked at `mode`, and the start_run call at the bottom of this
    # module forwards it unconditionally. ContinuousWorkDialog sends it for a continuous hop;
    # AiInvokeDialog sends it for a single rejection rework (T0010 §3-6). Session-scoped like
    # the fields above — omitted, or out of range, falls back to the engine's own default for
    # that mode (ai_invoke_service._resolve_timeout_sec).
    continuation_step_timeout_sec: Optional[int] = None
    # flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick — how many
    # times a no-output hop retries on the SAME step-assigned provider. Session-scoped
    # like the field above; omitted or unrecognized falls back to the engine's own
    # default (ai_invoke_service.RESTART_MAX_ATTEMPTS_DEFAULT).
    continuation_restart_max_attempts: Optional[int] = None
    # 0414 P0007 [검수] 탭: item_seq (string or int keys) -> review count, and -> reviewer
    # provider_id. Session-scoped like the maps above. Deliberately NOT narrowed to
    # dict[str, int] / dict[str, str]: a narrowed type would make pydantic emit FastAPI's own
    # error envelope for a bad value while every other failure here emits validation_failed,
    # and an unmanned caller would then have to parse two shapes. One envelope, produced by
    # workflow_decision_service's normalizers below.
    continuation_review_count_overrides: Optional[dict] = None
    continuation_reviewer_overrides: Optional[dict] = None
    provider_id: Optional[str] = None
    # provider_id alone may be an auto-restored default. This explicit signal says the person
    # actively chose it, so start_run can let it outrank an automatically stamped sequence row.
    provider_pinned: Optional[bool] = None
    merge_id: Optional[int] = None
    # Parallel-invoke extras (group 0223): context the matching copy-mention flow
    # assembled in the browser, so the invoke prompt can stay byte-identical.
    selected_docs: Optional[list[str]] = None      # next_step_message reference docs
    messages: Optional[list[str]] = None           # next_step_message / resolve_conflict user messages
    reject_reason: Optional[str] = None            # rework: live (possibly unsaved) reason
    design_types: Optional[list[str]] = None       # design_handoff selected types
    design_mode: Optional[str] = None              # design_handoff "batch" | "single"
    design_first_label: Optional[str] = None       # design_handoff single-mode type label
    work_plan_scope: Optional[dict] = None
    document_review_loop: Optional[DocumentReviewLoopRequest] = None


# Wire scope → token scope. The extra invoke scopes reuse the edit/new token
# grants (the inbox only honours new/edit/review/workflow_decide); what differs
# is the MENTION each scope feeds the worker.
_TOKEN_SCOPE = {
    "new": "new",
    "edit": "edit",
    "workflow_decide": "workflow_decide",
    "chat": "chat",
    "rework": "edit",
    "vr_correction": "edit",
    "next_step_message": "new",
    "design_handoff": "new",
    "work_plan_fill": "edit",
    # 0405 P0004 [AI invoke]: a proposal WRITES a work plan that does not exist yet, so it
    # takes a 'new' token and goes through advance_workflow — the same path [copy mention] uses,
    # which is what keeps the pasted text and the invoked text one function's output.
    # Not to be confused with work_plan_fill, which EDITS an existing plan.
    "work_plan_proposal": "new",
}
# review/resolve_conflict/workflow_sequence_edit/test_run keep their OWN token scope (the
# identity fallthrough of `_TOKEN_SCOPE.get`), because each is minted by a dedicated service
# that also builds its mention — see the issue_builder branches in `start_ai_invoke`.
#
# 0268 B0001: workflow_sequence_edit and test_run were the last two token scopes with a
# [copy mention] entrance but no in-app AI invoke — their surfaces (WorkflowDecisionModal,
# TestRunStrip) sit outside MainPanel's invoke wiring, so the parallel-invoke pass
# (group 0223) never reached them and this allowlist kept the gap structural.
_ALLOWED_SCOPES = (
    *_TOKEN_SCOPE.keys(),
    "review",
    "resolve_conflict",
    "workflow_sequence_edit",
    "test_run",
)


def _err(exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


def _validation_failed(errors: list[dict]) -> JSONResponse:
    message = next(
        (
            str(error.get("msg")).strip()
            for error in errors
            if error.get("msg") is not None and str(error.get("msg")).strip()
        ),
        "Validation failed.",
    )
    return JSONResponse(
        status_code=422,
        content={"code": "validation_failed", "message": message, "errors": errors},
    )


def _continuation_target_error(doc_ref: str, target_seq: int) -> Optional[dict]:
    """Reject a continuation target that is not a remaining step of the real sequence.

    0242 NR0003 finding 4: the only checks here used to be "not null" and "-1 for a pre-decision
    run", so any other number was accepted and failed SILENTLY at chain-termination time
    (inbox_routes: ``completed_seq >= target_seq``) — an already-done seq stopped the chain
    after one document, and a too-large seq ran the sequence to its end. Neither surfaced an
    error, which for an UNMANNED run is a safety problem, not just a UX one.

    The UI now picks the target from the live sequence, but /ai-invoke/start is reachable
    without it, so the rule the hint text only ever *described* is enforced here.

    Returns an error dict for :func:`_validation_failed`, or None when the target is fine.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        items = db_wfseq.get_sequence_items(seq["id"]) if seq is not None else []
    except Exception:  # noqa: BLE001
        # This is a typo guard, not a security control — the run's own DB work would surface a
        # real outage anyway. Fail OPEN so a lookup failure cannot turn a start that worked
        # before 0242 into a 500 from a validation helper.
        return None
    if seq is None:
        # Undecided (or non-sequence) document: there are no item_seqs to validate against.
        # The pre-decision run is already covered by the -1 sentinel rule above.
        return None
    if not items:
        return None
    match = next((i for i in items if i.get("item_seq") == target_seq), None)
    if match is None:
        return {"loc": "continuation_target_seq",
                "msg": f"step {target_seq} does not exist in this workflow sequence"}
    # Slot status per D030 §2 SSOT (mirrors workflow_head_routes._derive_status): a slot is
    # done once its result document exists AND is approved.
    if match.get("result_doc_id") is not None and match.get("result_doc_review_status") == "approved":
        return {"loc": "continuation_target_seq",
                "msg": f"step {target_seq} is already complete — pick a remaining step"}
    return None


def _require_user(request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"code": "user_session_required",
                                                      "message": "A user session is required."})
    return auth


def build_rework_mention(*, base: str, doc_ref: Optional[str], group_id: str,
                         reject_reason: Optional[str], locale: Optional[str]) -> str:
    """The whole prompt a rework (`action_scope="rework"`) worker reads.

    Module level, not a closure inside the route, so this exact composition can be
    exercised without minting a token and starting a run — the alternative is a test that
    re-implements the ordering and then agrees with itself.

    Two chunks may precede the standard edit mention, in this order (0446 T0016 §4-2):
    the rejection reasons, then the previous-run handoff. Both are omitted when they have
    nothing to say, and when BOTH are omitted the return value is `base` itself — the
    prompt every non-timeout rework has always got, byte for byte.
    """
    doc = db_docs.get_by_id(doc_ref) or {} if doc_ref else {}
    history = doc.get("rejection_history") or []
    if isinstance(history, str):
        try:
            import json as _json
            history = _json.loads(history) or []
        except Exception:
            history = []
    last = reject_reason or doc.get("rejection_reason")
    section = invoke_mention_service.build_rejection_section(history, last)
    # 0446 T0016 §4-1: read the group's newest finished run BEFORE this one starts. The run
    # being launched writes its own row only at finalize, so the row found here really is
    # the previous hop's. `previous_timeout_handoff` answers None for everything except a
    # timeout on this same document, and an empty block for None is what keeps the
    # composition below byte-identical to what it produced before (§4-2).
    handoff = invoke_mention_service.build_previous_run_section(
        ai_invoke_service.previous_timeout_handoff(group_id, doc_ref), locale,
    )
    # Both leading chunks already end in a blank line, so joining on "\n" leaves exactly one
    # blank line between every pair — the spacing the rejection section alone produced.
    leading = [chunk for chunk in (section, handoff) if chunk]
    return "\n".join(leading) + "\n" + base if leading else base


@router.post("/start")
def start_ai_invoke(body: AiInvokeStartRequest, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth

    errors: list[dict] = []
    loop = body.document_review_loop
    if loop is not None:
        if body.action_scope != "review":
            errors.append({"loc": "document_review_loop", "msg": "requires action_scope=review"})
        if body.mode != "single":
            errors.append({"loc": "document_review_loop", "msg": "requires mode=single"})
        if not body.doc_ref:
            errors.append({"loc": "doc_ref", "msg": "required for document_review_loop"})
        if body.continuation_review_count_overrides is not None or body.continuation_reviewer_overrides is not None:
            errors.append({"loc": "document_review_loop", "msg": "cannot be combined with continuation review overrides"})
        if body.provider_id is not None or body.provider_pinned not in (None, False):
            errors.append({"loc": "provider_id", "msg": "top-level provider selection is not allowed with document_review_loop"})
        allowed = {
            "review_count": {-1, 1, 2, 3},
            "review_criteria": {"document_type_default", "last_rejection_only"},
            "rework_timeout_sec": {1800, 3600, 7200},
            "failure_restart_max_attempts": {-1, 0, 1, 2},
            "total_timeout_sec": {3600, 7200, 14400},
        }
        for field, choices in allowed.items():
            if getattr(loop, field) not in choices:
                errors.append({"loc": f"document_review_loop.{field}", "msg": f"must be one of {sorted(choices, key=str)}"})
        if not loop.reviewer_provider_id.strip() or not loop.rework_provider_id.strip():
            errors.append({"loc": "document_review_loop", "msg": "both stage providers are required"})
    if body.mode not in ("single", "continuous"):
        errors.append({"loc": "mode", "msg": "must be single or continuous"})
    if body.action_scope not in _ALLOWED_SCOPES:
        errors.append({"loc": "action_scope", "msg": f"must be one of {', '.join(_ALLOWED_SCOPES)}"})
    # 0405 P0004: "mode is always single; this dialog never starts a continuous work chain."
    if body.action_scope == "work_plan_proposal" and body.mode != "single":
        errors.append({"loc": "mode", "msg": "work_plan_proposal must be single"})
    if body.mode == "continuous" and body.action_scope not in ("new", "edit", "workflow_decide"):
        errors.append({"loc": "mode", "msg": "continuous mode is not available for this action_scope"})
    if body.mode == "continuous" and body.continuation_target_seq is None:
        errors.append({"loc": "continuation_target_seq", "msg": "required for continuous mode"})
    if body.continuation_step_timeout_sec is not None and not (
        ai_invoke_service.STEP_TIMEOUT_MIN_SEC
        <= body.continuation_step_timeout_sec
        <= ai_invoke_service.STEP_TIMEOUT_MAX_SEC
    ):
        errors.append({
            "loc": "continuation_step_timeout_sec",
            "msg": f"must be between {ai_invoke_service.STEP_TIMEOUT_MIN_SEC} and "
                   f"{ai_invoke_service.STEP_TIMEOUT_MAX_SEC} seconds",
        })
    if (
        body.continuation_restart_max_attempts is not None
        and body.continuation_restart_max_attempts
        not in ai_invoke_service.RESTART_MAX_ATTEMPTS_CHOICES
    ):
        errors.append({
            "loc": "continuation_restart_max_attempts",
            "msg": "must be one of "
                   f"{ai_invoke_service.RESTART_MAX_ATTEMPTS_CHOICES}",
        })
    if (
        body.action_scope == "workflow_decide"
        and body.mode == "continuous"
        and body.continuation_target_seq != -1
    ):
        errors.append({"loc": "continuation_target_seq", "msg": "must be -1 for a pre-decision run"})
    try:
        validate_project_id(body.project)
    except ValueError as exc:
        errors.append({"loc": "project", "msg": str(exc)})
    module_part = body.module if body.module else "none"
    group_id = f"{body.project}.{module_part}.{body.group}"
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        errors.append({"loc": "group", "msg": str(exc)})
    if body.action_scope == "resolve_conflict":
        if body.merge_id is None:
            errors.append({"loc": "merge_id", "msg": "required for resolve_conflict"})
        if body.mode != "single":
            errors.append({"loc": "mode", "msg": "resolve_conflict must be single"})
    else:
        if not body.doc_ref:
            errors.append({"loc": "doc_ref", "msg": "required"})
        else:
            try:
                validate_doc_id(body.doc_ref)
            except ValueError as exc:
                errors.append({"loc": "doc_ref", "msg": str(exc)})
    # 0414 P0007: shape and value checks are CHEAP, so they run here with the rest of the
    # field validation — before the project lookup, before the token, before the scratch
    # directory. A request that will be refused must not leave anything behind (0299 R0001).
    #
    # workflow_decide is excluded for the same reason continuation_auto_approve_item_seqs is:
    # before the decision there are no item_seqs to key on, so the maps are dropped to None
    # rather than refused. `single` is excluded because it has no next hop to review.
    continuation_review_count_overrides = None
    continuation_reviewer_overrides = None
    if body.mode == "continuous" and body.action_scope != "workflow_decide":
        try:
            continuation_review_count_overrides = (
                workflow_decision_service.normalize_continuation_review_count_overrides(
                    body.continuation_review_count_overrides
                )
            )
        except ValueError as exc:
            errors.append({"loc": "continuation_review_count_overrides", "msg": str(exc)})
        try:
            continuation_reviewer_overrides = (
                workflow_decision_service.normalize_continuation_reviewer_overrides(
                    body.continuation_reviewer_overrides,
                    continuation_review_count_overrides,
                )
            )
        except ValueError as exc:
            errors.append({"loc": "continuation_reviewer_overrides", "msg": str(exc)})
    if errors:
        return _validation_failed(errors)

    if db_projects.get_by_id(body.project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {body.project}"})

    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, body.project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})

    # Needs the DB, so it runs after the cheap field checks above (0242 NR0003 recommendation 3).
    if (
        body.mode == "continuous"
        and body.action_scope != "workflow_decide"
        and body.doc_ref
        and body.continuation_target_seq is not None
    ):
        target_error = _continuation_target_error(body.doc_ref, body.continuation_target_seq)
        if target_error:
            return _validation_failed([target_error])

    # 0352 T0004 §2/§3.4: this is the FRESH client request naming the selection — the entry
    # point where the full 422 validation (including "already done") runs. workflow_decide
    # is excluded: no item_seq exists before the decision (§2 "no partial selection before the decision").
    continuation_auto_approve_item_seqs: list[int] = []
    if body.mode == "continuous" and body.action_scope != "workflow_decide":
        try:
            continuation_auto_approve_item_seqs = (
                workflow_decision_service.normalize_continuation_auto_approve_item_seqs(
                    body.continuation_auto_approve_item_seqs
                )
            )
            if body.doc_ref and continuation_auto_approve_item_seqs:
                workflow_decision_service.validate_continuation_auto_approve_item_seqs(
                    continuation_auto_approve_item_seqs,
                    body.doc_ref,
                    body.continuation_target_seq,
                )
        except ValueError as exc:
            return _validation_failed([{
                "loc": "continuation_auto_approve_item_seqs", "msg": str(exc),
            }])

    # 0414 P0007: the sequence-aware half of the [검수] validation. Needs the DB, so it runs
    # after the cheap checks (0242 NR0003 권고 3) and after the auto-approve selection is
    # normalized — eligibility reads BOTH, because which steps have worker output depends on
    # the instruction mode this very request is asking for.
    if body.mode == "continuous" and body.action_scope != "workflow_decide":
        if body.doc_ref and continuation_review_count_overrides:
            try:
                workflow_decision_service.validate_continuation_review_item_seqs(
                    continuation_review_count_overrides,
                    body.doc_ref,
                    body.continuation_target_seq,
                    instruction_mode=body.continuation_instruction_mode,
                    auto_approve_item_seqs=continuation_auto_approve_item_seqs,
                )
            except ValueError as exc:
                return _validation_failed([{
                    "loc": "continuation_review_count_overrides", "msg": str(exc),
                }])
        # A reviewer this project does not have fails VISIBLY on a fresh request instead of
        # switching silently: the pick exists to get the output read by someone else, and a
        # quiet substitution can land on self-review. Resume is the deliberate opposite
        # (ai_invoke_service._resumable_reviewer_overrides) — there is nobody left to ask.
        unavailable = workflow_decision_service.unavailable_reviewer_provider_ids(
            body.project, continuation_reviewer_overrides,
        )
        if unavailable:
            return JSONResponse(status_code=422, content={
                "code": "reviewer_unavailable",
                "message": "The selected reviewer is not enabled for this project.",
                "reviewer_provider_ids": unavailable,
            })

    # The mention is built through the exact token_routes path so the prompt the
    # invoked AI reads stays byte-identical to the copy-mention flow.
    from modules.flow_gate.api import token_routes as _token_routes

    locale = request.headers.get("x-locale") or "ko"
    is_continuous = body.mode == "continuous"
    token_scope = _TOKEN_SCOPE.get(body.action_scope, body.action_scope)

    def _standard_mention(raw_token: str, scratch_dir: str, ref_doc_ids=None):
        return _token_routes._build_mention_for_token(
            doc_ref=body.doc_ref,
            group_id=group_id,
            project_id=body.project,
            scratch_dir=scratch_dir,
            raw_token=raw_token,
            request=request,
            ref_doc_ids=ref_doc_ids,
            action_scope=token_scope,
            locale=locale,
            continuous=is_continuous,
            merge_id=body.merge_id,
            # 0226 NR0003 §4 (incidental): the review-mode flag previously never reached the
            # mention builder, so a review-mode first hop got the no-stop block instead
            # of the Q-allowed review variant.
            continuous_review_mode=body.continuation_review_mode,
        )

    def _mention_builder(raw_token: str, scratch_dir: str):
        # Each extra scope reproduces the text its [copy mention] counterpart put on the
        # clipboard (group 0223 parallel-invoke; builders in invoke_mention_service).
        if body.action_scope == "chat":
            return invoke_mention_service.build_conversation_mention(
                doc_id=body.doc_ref,
                project=body.project,
                module=body.module,
                group_name=group_id,
                raw_token=raw_token,
                token_id=token_service.inspect_for_replay(raw_token)["token_id"],
                api_base_url=_operator_facing_api_base(request),
                # 0293: the AI turn header carries the provider. Unlike the copy path,
                # here the server knows who is being invoked — but only when the run
                # cannot fall back to a different provider (see the helper's docstring).
                provider=ai_invoke_service.resolve_pinned_provider_name(
                    body.project, body.provider_id,
                ),
                provider_id=body.provider_id,
                # 0362 T0012: whoever pressed [AI invoke]. Their saved range decides how
                # far back the worker is told to start reading.
                user_id=user_id,
            )
        if body.action_scope == "rework":
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return build_rework_mention(
                base=base, doc_ref=body.doc_ref, group_id=group_id,
                reject_reason=body.reject_reason, locale=locale,
            )
        if body.action_scope == "vr_correction":
            try:
                prompt = prompt_copy_service.build_prompt(
                    doc_id=body.doc_ref, actor_user_id=user_id, locale=locale,
                ).get("prompt_text") or ""
            except Exception:
                prompt = ""
            # The copied VR prompt carries no token; the invoked worker still needs
            # credentials, so the standard edit mention follows the copy text.
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return (prompt + invoke_mention_service.SECTION_SEPARATOR + base) if prompt else base
        if body.action_scope == "resolve_conflict":
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return invoke_mention_service.prepend_messages_section(
                base, body.messages or [], locale,
            )
        if body.action_scope == "next_step_message":
            base = _standard_mention(raw_token, scratch_dir, ref_doc_ids=body.selected_docs)
            if not base:
                return None
            return invoke_mention_service.prepend_messages_section(
                base, body.messages or [], locale,
            )
        if body.action_scope == "design_handoff":
            context = invoke_mention_service.build_design_handoff_context(
                types=body.design_types or [],
                mode=body.design_mode or "batch",
                doc_ref=body.doc_ref,
                locale=locale,
                first_label=body.design_first_label,
            )
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return context + invoke_mention_service.SECTION_SEPARATOR + base
        return _standard_mention(raw_token, scratch_dir)

    issue_builder = None
    if body.action_scope == "review":
        # 0393 B0001 / NR0003 §4-2: the keyword MUST be declared here. _call_issue_builder
        # inspects this signature and only hands the run id to a builder that names it, so a
        # bare `def _issue_review():` minted a review token with ai_run_id NULL — and the
        # reviewing worker was then refused by the very lease its own run had just taken
        # (mutation_policy: GROUP_AI_RUN_OWNER_MISMATCH). `_issue_first_hop` below is the
        # shape every issuer in this file has to keep.
        def _issue_review(ai_run_id: Optional[str] = None):
            # 0417 T0013: a document_review_loop hop currently at the rework stage needs an
            # edit-scoped token — its worker calls POST /inbox action=edit, which 403s
            # ("Context binding mismatch") on anything but an edit-scoped token. This mirrors
            # _spawn_rework_hop's issue_rework_request for the continuous-chain review gate.
            # start_run/_worker set this attribute to the loop's current stage before every
            # issue/reissue call for a loop run; a plain (non-loop) review invocation never
            # sets it, so it keeps issuing a review-scoped token exactly as before.
            if getattr(_issue_review, "loop_stage", None) == ai_invoke_service.REWORK_HOP_KIND:
                return invoke_mention_service.issue_rework_request(
                    doc_id=body.doc_ref,
                    issued_to=user_id,
                    api_base_url=_operator_facing_api_base(request),
                    locale=locale,
                    ai_run_id=ai_run_id,
                )
            issued = workflow_decision_service.request_review(
                doc_id=body.doc_ref,
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                ref_doc_ids=None,
                locale=locale,
                ai_run_id=ai_run_id,
            )
            return {
                "raw_token": issued["token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_review
    if body.action_scope == "workflow_sequence_edit":
        # 0268 B0001 (NR0003 defect 1): the invoke twin of WorkflowDecisionModal's [copy mention].
        # Same issuer as POST /workflow/sequence-edit-request, so the worker reads the exact
        # prompt the clipboard path produced — only the delivery differs.
        # 0393 NR0003 §6: same structural gap as review, simply never exercised since the
        # lease landed. Declared here so it can never surface as its own bug report.
        def _issue_sequence_edit(ai_run_id: Optional[str] = None):
            issued = workflow_decision_service.request_sequence_edit(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                locale=locale,
                ai_run_id=ai_run_id,
            )
            return {
                "raw_token": issued["raw_token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_sequence_edit
    if body.action_scope == "work_plan_fill":
        def _issue_work_plan_fill(ai_run_id: Optional[str] = None):
            issued = work_plan_service.request_work_plan_fill(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                scope=body.work_plan_scope or {
                    "quantity_type_codes": [],
                    "step_keys": [],
                    "provider_ids": [],
                },
                locale=locale,
                ai_run_id=ai_run_id,
            )
            return {
                "raw_token": issued["raw_token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_work_plan_fill
    if body.action_scope == "work_plan_proposal":
        # 0405 P0004 [AI invoke — run it immediately with the same scope, references and authoring rules]:
        # the invoke twin of the proposal dialog's [copy mention]. Both call advance_workflow,
        # so the head advances once and the worker reads the very mention a person would
        # have pasted — including the work-plan-scope section built from this scope.
        def _issue_work_plan_proposal(ai_run_id: Optional[str] = None):
            adv = workflow_decision_service.advance_workflow(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                ref_doc_ids=body.selected_docs,
                locale=locale,
                continuous=False,
                ai_run_id=ai_run_id,
                work_plan_scope=body.work_plan_scope,
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"] or "",
            }
        issue_builder = _issue_work_plan_proposal
    if body.action_scope == "test_run":
        # 0268 B0001 (NR0003 defect 2): the invoke twin of TestRunStrip's delegation copy.
        # issue_test_run_request returns the raw token under "token" (not "raw_token"),
        # so it is remapped here to the issue_builder contract start_run expects.
        # 0393 NR0003 §6.
        def _issue_test_run(ai_run_id: Optional[str] = None):
            from modules.flow_gate.services import test_run_service

            issued = test_run_service.issue_test_run_request(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                locale=locale,
                ai_run_id=ai_run_id,
            )
            return {
                "raw_token": issued["token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_test_run
    if body.action_scope == "workflow_decide":
        # 0393 NR0003 §6.
        def _issue_workflow_decision(ai_run_id: Optional[str] = None):
            return workflow_decision_service.request_workflow_decision(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                locale=locale,
                continuous=is_continuous,
                continuation_review_mode=body.continuation_review_mode,
                continuation_instruction_mode=body.continuation_instruction_mode,
                ai_run_id=ai_run_id,
            )
        issue_builder = _issue_workflow_decision
    elif is_continuous and body.action_scope == "new" and not body.continuation_review_mode:
        # 0226 B0001 ④ / NR0003 §4 (§5-5): the continuous first hop used to mint the
        # token directly, bypassing advance_workflow — the ONLY place instruction heads
        # (N/T) are auto-created + auto-approved for an unmanned chain. Starting on an
        # N/T head therefore handed the AI the instruction document to write by hand.
        # Route the first hop through advance_workflow, exactly like every later inbox
        # self-chain hop. Review mode stays on the direct-issue path above: the
        # pre-flight Q phase must not create documents.
        def _issue_first_hop(ai_run_id: Optional[str] = None):
            adv = workflow_decision_service.advance_workflow(
                doc_id=body.doc_ref,
                issued_to=user_id,
                api_base_url=_operator_facing_api_base(request),
                locale=locale,
                continuous=True,
                continuation_target_seq=body.continuation_target_seq,
                continuation_review_mode=body.continuation_review_mode,
                # 0317 T0013 defect ②: forward the instruction mode, exactly like
                # _issue_workflow_decision above. Omitting it let it normalize to
                # auto_approved, which then got baked into the token and propagated down the
                # whole chain — so [write the instruction, then proceed](ai_direct) died on every hop, not just
                # the first, and N/T instruction steps were silently auto-approved away.
                continuation_instruction_mode=body.continuation_instruction_mode,
                # 0359 L0007 §2.9: the first hop's token carries its run id too, so the
                # tokens table can answer "which execution held this?" from hop 1 onward.
                ai_run_id=ai_run_id,
                continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"],
                # 0406 T0022 item 3: which document this hop's worker actually was, and which
                # N/T the server handled with no worker attached at all. These must reach the
                # run record for "the N/T vanished" to be explainable afterwards.
                "worker_document_type": adv.get("worker_document_type"),
                "auto_handled_item_seqs": adv.get("auto_handled_item_seqs") or [],
            }
        issue_builder = _issue_first_hop

    try:
        result = ai_invoke_service.start_run(
            project_id=body.project,
            module=body.module,
            group_id=group_id,
            doc_ref=body.doc_ref or "",
            action_scope=token_scope,
            mode=body.mode,
            continuation_target_seq=body.continuation_target_seq,
            continuation_review_mode=body.continuation_review_mode,
            continuation_instruction_mode=body.continuation_instruction_mode,
            continuation_locale=locale if is_continuous else None,
            issued_to=user_id,
            api_base_url=_operator_facing_api_base(request),
            mention_builder=_mention_builder,
            # The service computes the durable baseline and selects review vs rework before resolving the chain.
            provider_id=body.provider_id,
            provider_pinned=body.provider_pinned,
            issue_builder=issue_builder,
            merge_id=body.merge_id,
            continuation_provider_overrides=body.continuation_provider_overrides,
            continuation_default_note=body.continuation_default_note,
            continuation_note_overrides=body.continuation_note_overrides,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
            continuation_step_timeout_sec=body.continuation_step_timeout_sec,
            continuation_restart_max_attempts=body.continuation_restart_max_attempts,
            # Already normalized above: keys unified to strings, count 0 and orphan reviewers
            # dropped, empty maps folded to None. start_run stores them on the run only for
            # mode="continuous", so the single / workflow_decide paths keep passing None.
            continuation_review_count_overrides=continuation_review_count_overrides,
            continuation_reviewer_overrides=continuation_reviewer_overrides,
            document_review_loop=(body.document_review_loop.dict() if body.document_review_loop else None),
        )
    except HTTPException as exc:
        return _err(exc)
    except LookupError as exc:
        return JSONResponse(status_code=404, content={
            "code": "workflow_decision_unavailable", "message": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={
            "code": "workflow_decision_conflict", "message": str(exc)})
    return JSONResponse(status_code=200, content=result)


@router.get("/providers")
def get_ai_invoke_providers(project: str, request: Request):
    """Return only safe provider briefs for the header runtime selector."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_project_id(project)
    except ValueError as exc:
        return _validation_failed([{"loc": "project", "msg": str(exc)}])
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    effective = ai_invoke_service.list_runtime_providers(project)
    return JSONResponse(status_code=200, content=effective)


@router.get("/active")
def get_active_ai_invoke(group_id: str, request: Request):
    """Restore the group-scoped progress indicator after navigation or reload."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    return JSONResponse(status_code=200, content=ai_invoke_service.get_active_status(group_id))


@router.get("/active-all")
def get_active_all_ai_invoke(request: Request):
    """Miniplayer bootstrap (group 0252 P0008 S1): every live run the requesting user
    started plus every chain they paused, in one shot — restores the widget list after
    a reload or a server restart (runs are in-memory, paused rows are DB-persisted)."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    return JSONResponse(status_code=200, content=ai_invoke_service.active_all(auth["issued_to"]))


@router.post("/resume")
def resume_ai_invoke(body: dict, request: Request):
    """Resume a user-paused continuous chain (group 0252 P0008 S5). Group-keyed:
    a paused chain has no live run to address."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    group_id = str(body.get("group_id") or "")
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})

    locale = request.headers.get("x-locale") or "ko"
    try:
        result = ai_invoke_service.resume_chain(
            group_id=group_id,
            user_id=user_id,
            api_base_url=_operator_facing_api_base(request),
            locale=locale,
            is_admin=bool(auth.get("is_admin")),
        )
    except HTTPException as exc:
        return _err(exc)
    return JSONResponse(status_code=200, content=result)


@router.delete("/paused/{group_id}")
def release_paused_ai_invoke(group_id: str, request: Request):
    """Explicit user cancel/release of a group-keyed PAUSED CHAIN row (0459 T0007).

    Paused-row release ONLY: this is neither a live-run cancel (POST
    /{run_id}/cancel) nor a lease force-release (POST /leases/{group_id}/release) --
    the name and the service it calls (release_paused_chain, never
    force_release_group_lease) both say so. Declared ahead of GET /{run_id} for the
    same path-shadowing reason as GET /leases and POST /leases/{group_id}/release.
    """
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    try:
        result = ai_invoke_service.release_paused_chain(
            group_id=group_id,
            user_id=user_id,
            is_admin=bool(auth.get("is_admin")),
        )
    except HTTPException as exc:
        return _err(exc)
    return JSONResponse(status_code=200, content=result)


@router.get("/runs")
def list_ai_invoke_runs(
    request: Request,
    group_id: Optional[str] = None,
    project: Optional[str] = None,
    limit: Optional[int] = None,
):
    """Browse past runs by number (L0007 §2.10.3) — until now a run was only
    reachable by its id, so a dead chain with no card left could not be found at
    all. Declared ahead of GET /{run_id} so "runs" is never read as a run id."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    if (group_id is None) == (project is None):
        return _validation_failed([{"loc": "group_id",
                                    "msg": "exactly one of group_id or project is required"}])
    if group_id is not None:
        try:
            validate_group_id(group_id)
        except ValueError as exc:
            return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    if project is not None:
        try:
            validate_project_id(project)
        except ValueError as exc:
            return _validation_failed([{"loc": "project", "msg": str(exc)}])
    project_id = project if project is not None else group_id.split(".", 1)[0]
    if db_projects.get_by_id(project_id) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project_id}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project_id, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    try:
        result = ai_invoke_service.list_runs(group_id=group_id, project=project, limit=limit)
    except HTTPException as exc:
        return _err(exc)
    return JSONResponse(status_code=200, content=result)


@router.get("/rework-hint")
def get_ai_invoke_rework_hint(group_id: str, doc_ref: str, request: Request):
    """Return the previous-run timeout kind used by the rework prompt handoff."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    errors = []
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        errors.append({"loc": "group_id", "msg": str(exc)})
    try:
        validate_doc_id(doc_ref)
    except ValueError as exc:
        errors.append({"loc": "doc_ref", "msg": str(exc)})
    if errors:
        return _validation_failed(errors)
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    handoff = ai_invoke_service.previous_timeout_handoff(group_id, doc_ref)
    return JSONResponse(status_code=200, content={
        "ok": True,
        "timeout_kind": handoff.get("timeout_kind") if handoff else None,
    })


@router.get("/leases")
def list_ai_invoke_leases(project: str, request: Request):
    """Locked-group inventory for the manual-unlock screen (0401 T0004 item 2).
    Declared ahead of GET /{run_id} so "leases" is never read as a run id."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_project_id(project)
    except ValueError as exc:
        return _validation_failed([{"loc": "project", "msg": str(exc)}])
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    from modules.flow_gate.db import group_ai_leases as db_leases

    items = []
    for lease in db_leases.list_active_by_project(project):
        run_id = str(lease.get("run_id") or "")
        items.append({
            "group_id": lease.get("group_id"),
            "run_id": run_id,
            "state": lease.get("state"),
            "acquired_at": lease.get("acquired_at"),
            "heartbeat_at": lease.get("heartbeat_at"),
            "expires_at": lease.get("expires_at"),
            "run_live": ai_invoke_service.is_run_live(run_id),
        })
    return JSONResponse(status_code=200, content={"ok": True, "project": project, "items": items})


@router.post("/leases/{group_id}/release")
def release_ai_invoke_lease(group_id: str, request: Request):
    """Manual escape hatch for a lease its owning process can never release again
    (0401 T0004 item 2). Declared ahead of GET /{run_id} for the same path-shadowing
    reason as GET /leases above."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    try:
        result = ai_invoke_service.force_release_group_lease(group_id)
    except HTTPException as exc:
        return _err(exc)
    return JSONResponse(status_code=200, content=result)


@router.get("/{run_id}")
def get_ai_invoke_status(run_id: str, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        payload = ai_invoke_service.get_run_detail(run_id)
    except HTTPException as exc:
        return _err(exc)
    # 0359 L0007 §2.10.2: a persisted run outlives its worktree session, so a run id
    # alone could otherwise open another project's run — checked after the lookup,
    # so an unknown id is still a 404 regardless of the caller's permissions.
    project_id = str(payload.get("project_id") or payload.get("group_id") or "").split(".", 1)[0]
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project_id, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    return JSONResponse(status_code=200, content=payload)


@router.post("/{run_id}/pause")
def pause_ai_invoke(run_id: str, request: Request):
    """Boundary pause for a continuous run (group 0252 P0008 S4): the in-flight step
    completes, the chain stops before the next token — never a mid-step freeze."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200,
                            content=ai_invoke_service.pause_run(run_id, auth["issued_to"]))
    except HTTPException as exc:
        return _err(exc)


@router.post("/{run_id}/cancel")
def cancel_ai_invoke(run_id: str, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200, content=ai_invoke_service.cancel_run(run_id))
    except HTTPException as exc:
        return _err(exc)
