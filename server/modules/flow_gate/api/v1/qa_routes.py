"""Q/A endpoint (D022 §4-3).

POST /api/v1/qa/{q_id}/answer
Auth: login session cookie (get_current_user dependency)
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import qa_service
from modules.flow_gate.db import commands as db_commands
from modules.flow_gate.commands import resolve_template
from modules.flow_gate.utils.help_url import help_url
from modules.flow_gate.utils.id_validators import validate_doc_id
from config import settings

router = APIRouter(prefix="/api/v1", tags=["QA"])


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": help_url(),
        },
    )


class AnswerRequest(BaseModel):
    answer_body: str
    dispatch_mode: str          # "command" | "ment_copy" | "none"
    command_id: Optional[str] = None


@router.get("/qa/{doc_pk}/form")
def get_answer_form(
    doc_pk: int,
    current_user: dict = Depends(get_current_user),
):
    """Returns answer form data for the Q document (process_service.get_answer_form_data)."""
    from modules.flow_gate import process_service
    result = process_service.get_answer_form_data(doc_pk)
    if result is None:
        return _fail(404, f"Document not found: {doc_pk}")
    return JSONResponse(content=result)


@router.post("/qa/{q_id}/answer")
def post_answer(
    q_id: str,
    body: AnswerRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Answer registration endpoint (D022 §4-3-1).

    Step 1. Verify session auth (get_current_user dependency)
    Step 2. Confirm q_id exists
    Step 3. Check Q.status
    Step 4. Validate permissions (perm_document_create)
    Step 5. Validate dispatch_mode
    Step 6. Number, save, and register A document to DB
    Step 7. Transition Q status open → answered
    Step 8. Branch on dispatch_mode (issue new token / execute command)
    Step 9. Return response
    """
    actor_user_id: str = current_user["user_id"]

    # ── Validate q_id canonical format (T261) ──────────────────────────────────────
    try:
        validate_doc_id(q_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    # ── Step 2 & 3: Verify Q document exists + check status ─────────────────────────────────
    from fastapi import HTTPException
    try:
        q_doc = qa_service.get_q_for_answer(q_id)
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 3.5: Reject disposed (DC) group ────────────────────────────────────────────
    # TR0079.0003 rework (3rd pass): answering a Q (writing a Q&A answer) creates an A
    # document in the group — a forward write that must be rejected once the group is
    # disposed. Shares process_service.is_group_disposed (fail-open for live groups).
    from modules.flow_gate import process_service
    if process_service.is_group_disposed(q_doc.get("group_id")):
        return _fail(409, "Modification not allowed: the group has been disposed.")

    # ── Step 4: Validate permissions (perm_document_create) ─────────────────────────────
    project_id: str = q_doc["project_id"]
    if not has_permission(actor_user_id, project_id, "perm_document_create"):
        return _fail(403, "You do not have permission to perform this action")

    # ── Step 5: Validate dispatch_mode ───────────────────────────────────
    valid_modes = {"command", "ment_copy", "ai", "none"}
    if body.dispatch_mode not in valid_modes:
        return _fail(400, f"dispatch_mode must be one of {', '.join(valid_modes)}")

    if body.dispatch_mode == "command" and not body.command_id:
        return _fail(400, "command_id is required when dispatch_mode is command.")

    # Validate command_id (command mode)
    if body.dispatch_mode == "command":
        cmd_rec = db_commands.get_by_id(body.command_id)  # type: ignore[arg-type]
        if cmd_rec is None:
            return _fail(404, f"Command {body.command_id} does not exist")

    # ── Step 6: Create A document ─────────────────────────────────────────────────
    try:
        a_doc_id, stored_path = qa_service.create_answer_doc(
            q_doc=q_doc,
            answer_body=body.answer_body,
            actor_user_id=actor_user_id,
        )
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 7: Transition Q status open → answered ─────────────────────────────────
    try:
        qa_service.transition_q_to_answered(
            q_id=q_id,
            a_doc_id=a_doc_id,
            actor_user_id=actor_user_id,
        )
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 7.5: Publish SSE (A registration complete notification) ───────────────────────────────
    try:
        import asyncio
        from modules.flow_gate.api.v1.events.publisher import publish_event, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType

        async def _push():
            base = dict(
                project=project_id,
                group_id=q_doc["group_id"],
                doc_id=a_doc_id,
                audience=actor_user_id,
            )
            await publish_event(FlowEvent(
                event_type=EventType.FILE_EXPLORER_REFRESH,
                payload={"operation": "created", "stored_path": stored_path},
                **base,
            ))
            await publish_event(FlowEvent(
                event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                payload={"operation": "created", "doc_id": a_doc_id,
                         "type": "A", "status": "open"},
                **base,
            ))
            await publish_event(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": q_doc["group_id"], "reason": "document_added"},
                **base,
            ))

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_push())
            else:
                loop.run_until_complete(_push())
        except RuntimeError:
            asyncio.run(_push())
    except Exception as _push_exc:
        import LogAssist.log as logger
        logger.warning(f"[qa answer] SSE publish failed (ignored): {_push_exc}")

    # ── Step 8: Branch on dispatch_mode ──────────────────────────────────────────
    raw_token: Optional[str] = None
    token_id: Optional[str] = None
    scratch_dir: Optional[str] = None
    expires_at: Optional[str] = None
    ment_text: Optional[str] = None

    ai_run_id: Optional[str] = None
    if body.dispatch_mode in ("command", "ment_copy", "ai"):
        try:
            token_result = qa_service.issue_followup_token(
                q_doc=q_doc,
                a_doc_id=a_doc_id,
                actor_user_id=actor_user_id,
                dispatch_mode=body.dispatch_mode,
            )
        except HTTPException as exc:
            return _fail(exc.status_code, exc.detail)

        raw_token = token_result["raw_token"]
        token_id = token_result["token_id"]
        scratch_dir = token_result["scratch_dir"]
        expires_at = token_result["expires_at"]

        if body.dispatch_mode == "command":
            # Auto-execute command (D022 §4-3-2)
            _execute_command(
                command_id=body.command_id,  # type: ignore[arg-type]
                raw_token=raw_token,
                scratch_dir=scratch_dir,
            )
        else:
            # ment_copy / ai: Build ment body (per M020)
            base = str(request.base_url).rstrip("/")
            context = settings.CONTEXT.rstrip("/")
            api_base_url = f"{base}{context}/api/v1"
            ment_text = qa_service.build_ment_text(
                q_doc_id=q_id,
                a_doc_id=a_doc_id,
                scratch_dir=scratch_dir,
                prev_doc_id=q_doc.get("triggered_by"),
                api_base_url=api_base_url,
                raw_token=raw_token,
            )

        if body.dispatch_mode == "ai":
            # In-app invoke (group 0223): feed the run the SAME ment_copy text a human
            # would have pasted, through the already-issued follow-up token. On this
            # branch the raw token stays server-side (P0005 표기 규칙).
            from modules.flow_gate.services import ai_invoke_service

            # Auto-resume (group 0252 L0009 §2.5 / P0008 S7): when the group holds a
            # user-paused CONTINUOUS chain, the in-app answer resumes that chain instead
            # of the single follow-up run. A resume conflict never fails the answer —
            # the A document is already registered — it returns ok with resume_code and
            # no re-run (답변 유실 금지, and no silent fallback to a single run either).
            from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
            paused_row = None
            try:
                paused_row = db_paused.get_by_group(q_doc["group_id"])
            except Exception:
                import LogAssist.log as logger
                logger.warning("[qa answer] paused-chain probe failed (ignored)")
            if paused_row is not None and (paused_row.get("mode") or "continuous") == "continuous":
                resume_code: Optional[str] = None
                try:
                    resumed = ai_invoke_service.resume_chain(
                        group_id=q_doc["group_id"],
                        user_id=actor_user_id,
                        api_base_url=api_base_url,
                        locale=request.headers.get("x-locale") or "ko",
                    )
                    ai_run_id = resumed.get("run_id")
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    resume_code = str(detail.get("code") or "resume_failed")
                # The follow-up token minted above is not used on this path; retire it
                # so no live single-run credential lingers behind the resumed chain.
                try:
                    from modules.flow_gate.services import token_service
                    token_service.revoke(token_id, reason="qa_auto_resume")
                except Exception:
                    import LogAssist.log as logger
                    logger.warning("[qa answer] follow-up token revoke failed (ignored)")
                raw_token = None
                ment_text = None
                resp_extra: dict = {"ai_run_mode": "continuous" if ai_run_id else None}
                if resume_code is not None:
                    resp_extra["resume_code"] = resume_code
                resp = {
                    "ok": True,
                    "a_doc_id": a_doc_id,
                    "stored_path": stored_path,
                    "raw_token": raw_token,
                    "token_id": token_id,
                    "scratch_dir": scratch_dir,
                    "expires_at": expires_at,
                    "dispatch_mode": body.dispatch_mode,
                    **resp_extra,
                }
                if ai_run_id is not None:
                    resp["ai_run_id"] = ai_run_id
                return JSONResponse(content=resp)

            try:
                ai_run = ai_invoke_service.start_run(
                    project_id=project_id,
                    module=None,
                    group_id=q_doc["group_id"],
                    doc_ref=q_id,
                    action_scope="edit",
                    mode="single",
                    continuation_target_seq=None,
                    continuation_review_mode=False,
                    continuation_locale=None,
                    issued_to=actor_user_id,
                    api_base_url=api_base_url,
                    mention_builder=lambda _raw, _scratch: ment_text,
                    issue_builder=lambda: {
                        "raw_token": raw_token,
                        "token_id": token_id,
                        "scratch_dir": scratch_dir,
                        "mention": ment_text,
                    },
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                return JSONResponse(status_code=exc.status_code, content={
                    "ok": False, "a_doc_id": a_doc_id, "stored_path": stored_path,
                    "dispatch_mode": body.dispatch_mode, **detail,
                })
            ai_run_id = ai_run.get("run_id")
            raw_token = None
            ment_text = None

    # ── Step 9: Return response ────────────────────────────────────────────────────
    resp: dict = {
        "ok": True,
        "a_doc_id": a_doc_id,
        "stored_path": stored_path,
        "raw_token": raw_token,
        "token_id": token_id,
        "scratch_dir": scratch_dir,
        "expires_at": expires_at,
        "dispatch_mode": body.dispatch_mode,
    }
    if ment_text is not None:
        resp["ment_text"] = ment_text
    if ai_run_id is not None:
        resp["ai_run_id"] = ai_run_id
        # 0252 P0008 S7: tell the widget whether the answer resumed a chain or started
        # the existing single follow-up (the resume path returns "continuous" above).
        resp["ai_run_mode"] = "single"
    return JSONResponse(content=resp)


def _execute_command(command_id: str, raw_token: str, scratch_dir: str) -> None:
    """Execute command (D022 §4-3-2 — D021 §6-4 pattern).

    Injects FLOWGATE_TOKEN / FLOWGATE_SCRATCH via env_overrides and
    runs the command via subprocess. Result is ignored (fire-and-forget).
    """
    cmd_rec = db_commands.get_by_id(command_id)
    if cmd_rec is None:
        return

    try:
        resolved = resolve_template(cmd_rec["template"])["resolved"]
    except Exception:
        return

    env = {
        **os.environ,
        "FLOWGATE_TOKEN": raw_token,
        "FLOWGATE_SCRATCH": scratch_dir,
    }
    try:
        subprocess.Popen(
            resolved,
            shell=True,
            env=env,
        )
    except Exception:
        pass  # fire-and-forget — A registration is complete even on failure
