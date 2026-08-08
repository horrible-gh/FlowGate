"""Token issuance endpoint (D020 §2-7).

POST /api/v1/token/issue
Auth: login session cookie (get_current_user dependency)
"""
from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import invoke_mention_service
from modules.flow_gate.services import mention_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services import git_service
from modules.flow_gate.services.workflow_decision_service import (
    normalize_continuation_auto_approve_item_seqs,
    normalize_continuation_instruction_mode,
    validate_continuation_auto_approve_item_seqs,
)
from modules.flow_gate.utils.id_validators import (
    validate_project_id,
    validate_group_id,
    validate_doc_id,
)
from config import settings

router = APIRouter(prefix="/api/v1", tags=["TokenIssue"])

# Wire scopes accepted on the request, and the TOKEN scope each one is minted under.
# Chat has a dedicated append-only grant; its wire scope also selects the compact mention.
_WIRE_SCOPES = ("new", "edit", "chat", "resolve_conflict")
_WIRE_TOKEN_SCOPE = {"chat": "chat"}


class TokenIssueRequest(BaseModel):
    project: str
    module: Optional[str] = None
    group: str
    # "new" | "edit" | "chat" | "resolve_conflict" | null → auto-determined (T244 §1-2).
    # "chat" mints the dedicated append-only conversation grant.
    action_scope: Optional[str] = None
    doc_ref: Optional[str] = None
    selected_docs: Optional[list] = None  # T384: selected document list (for mention reference doc inclusion)
    # Continuous work (group 0086 R0001 / NR0003 B안): when continuation_target_seq is set,
    # the minted token carries the unmanned-chain stop point + AI-review-mode flag and the
    # mention swaps its Q-guard for the delegation/unmanned block. This is the fallback path;
    # the FE primarily issues the first continuation token via /workflow/advance.
    continuation_target_seq: Optional[int] = None
    continuation_review_mode: bool = False
    merge_id: Optional[int] = None
    continuation_instruction_mode: Optional[str] = None
    # 0352 T0004 §2/§3.4: the ai_direct chain's per-item_seq N/T auto-approve selection —
    # this is the fallback direct-issue path, so a fresh selection is validated (422) here
    # too, exactly like /workflow/advance and /ai-invoke/start.
    continuation_auto_approve_item_seqs: Optional[list[int]] = None


class TokenIssueResponse(BaseModel):
    ok: bool
    raw_token: str
    token_id: str
    expires_at: str
    scratch_dir: str
    action_scope: str                    # resolved action_scope (T244 §1-2)
    doc_ref: Optional[str] = None        # normalized doc_ref (T244 §1-3)
    group_id: Optional[str] = None       # group_id in canonical form (D013 §3-1, T269)
    mention: Optional[str] = None        # M020-format worker mention (when sequence head exists)


@router.post("/token/issue", response_model=TokenIssueResponse)
def issue_token(
    body: TokenIssueRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Token issuance endpoint (D020 §2-7).

    1. Session check (get_current_user)
    2. action_scope validation (if explicit) / auto-determination (if null, T244 §1-2)
    3. Project existence check
    4. Group resolution (group_name → group_id)
    5. Issuance permission check (perm_document_read)
    6. doc_ref canonical form validation
    7. token_service.issue() call
    """
    # Step 1: action_scope handling (validate if explicit, defer auto-determination if null)
    if body.action_scope is not None and body.action_scope not in _WIRE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"action_scope must be one of {', '.join(_WIRE_SCOPES)}",
        )
    if body.action_scope == "chat" and not body.doc_ref:
        raise HTTPException(status_code=422, detail="doc_ref is required for chat")

    # Step 1-b: project_id / group / doc_ref canonical form validation (T261)
    try:
        validate_project_id(body.project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Assemble canonical group_id from short group name
    module_part = body.module if body.module else "none"
    canonical_group_id = f"{body.project}.{module_part}.{body.group}"
    try:
        validate_group_id(canonical_group_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if body.doc_ref is not None:
        try:
            validate_doc_id(body.doc_ref)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if body.action_scope == "resolve_conflict" and body.merge_id is None:
        raise HTTPException(status_code=422, detail="merge_id is required for resolve_conflict")

    # Step 2: project existence check
    project = db_projects.get_by_id(body.project)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {body.project}")

    # Step 3: group resolution
    group_id = _resolve_group(body.project, canonical_group_id)

    # Step 4: issuance permission check (document.read)
    user_id: str = current_user["user_id"]
    if not has_permission(user_id, body.project, "perm_document_read"):
        raise HTTPException(
            status_code=403,
            detail="Token issuance permission denied (perm_document_read required)",
        )

    # Step 5: doc_ref canonical form validation — moved to Step 1-b in T261

    # Step 6: auto-determine action_scope (T244 §1-2)
    if body.action_scope is not None:
        wire_scope = body.action_scope
    else:
        wire_scope = _determine_action_scope(body.doc_ref)
    # The mention is chosen by the WIRE scope, the token by the mapped one.
    resolved_action_scope = _WIRE_TOKEN_SCOPE.get(wire_scope, wire_scope)
    if resolved_action_scope == "resolve_conflict" and group_id is None:
        raise HTTPException(status_code=404, detail=f"Group not found: {canonical_group_id}")

    # The dialog request carries the chosen locale in x-locale; persist it on a continuation
    # token so the unmanned self-chain honors it on every hop (group 0099 B0001).
    req_locale = request.headers.get("x-locale") or "ko"
    is_continuous = body.continuation_target_seq is not None

    # 0352 T0004 §2/§3.4: a fresh selection arriving here is validated (422) the same way
    # /workflow/advance and /ai-invoke/start do — this endpoint is the fallback direct-issue
    # path (T244 §1-2 docstring on issue_token above).
    continuation_auto_approve_item_seqs: list[int] = []
    if is_continuous:
        try:
            continuation_auto_approve_item_seqs = normalize_continuation_auto_approve_item_seqs(
                body.continuation_auto_approve_item_seqs
            )
            if body.doc_ref and continuation_auto_approve_item_seqs:
                validate_continuation_auto_approve_item_seqs(
                    continuation_auto_approve_item_seqs,
                    body.doc_ref,
                    body.continuation_target_seq,
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Step 7: issue token
    result = token_service.issue(
        project=body.project,
        group_id=group_id,
        action_scope=resolved_action_scope,
        doc_ref=body.doc_ref,
        issued_to=user_id,
        continuation_target_seq=body.continuation_target_seq,
        continuation_review_mode=body.continuation_review_mode,
        continuation_locale=req_locale if is_continuous else None,
        merge_id=body.merge_id if resolved_action_scope == "resolve_conflict" else None,
        continuation_instruction_mode=(
            normalize_continuation_instruction_mode(body.continuation_instruction_mode)
            if is_continuous else None
        ),
        continuation_auto_approve_item_seqs=(
            continuation_auto_approve_item_seqs if is_continuous else None
        ),
    )

    # Step 8: build M020 mention (when doc_ref + sequence head exist)
    # Continuous work (group 0086): a continuation token swaps the mention's Q-guard for the
    # delegation/unmanned/no-stop/autonomous block (mention_service continuous branch).
    if wire_scope == "chat":
        # 0293 NR0004 발견 3: the browser used to assemble this text itself and throw the
        # server's mention away. It is served from here now, so the [멘트복사] path and the
        # in-app AI 호출 path cannot drift. No provider is passed: this mention goes to
        # whoever the user pastes it to, which the server cannot know — the worker fills
        # the slot in, or leaves it out.
        mention = invoke_mention_service.build_conversation_mention(
            doc_id=body.doc_ref,
            project=body.project,
            module=body.module,
            group_name=canonical_group_id,
            raw_token=result["raw_token"],
            token_id=result["token_id"],
            api_base_url=_build_api_base(request),
            # 0362 T0012: the range is this user's own setting and the server reads it
            # from their authentication. The request body still carries nothing about it.
            user_id=user_id,
        )
    else:
        mention = _build_mention_for_token(
            doc_ref=body.doc_ref,
            group_id=group_id,
            project_id=body.project,
            scratch_dir=result["scratch_dir"],
            raw_token=result["raw_token"],
            request=request,
            ref_doc_ids=body.selected_docs,
            action_scope=resolved_action_scope,
            locale=req_locale,
            continuous=is_continuous,
            merge_id=body.merge_id,
            continuous_review_mode=bool(is_continuous and body.continuation_review_mode),
        )

    return TokenIssueResponse(
        ok=True,
        raw_token=result["raw_token"],
        token_id=result["token_id"],
        expires_at=result["expires_at"],
        scratch_dir=result["scratch_dir"],
        action_scope=resolved_action_scope,
        doc_ref=body.doc_ref,
        group_id=result.get("group_id"),
        mention=mention,
    )


def _resolve_group(project_id: str, group_name: str) -> Optional[str]:
    """Resolve group_name → group_id. Direct group_id match → title match."""
    # direct group_id match
    grp = db_groups.get_by_id(group_name)
    if grp and grp.get("project_id") == project_id:
        return grp["group_id"]

    # title match
    grp = _get_group_by_title(project_id, group_name)
    if grp:
        return grp["group_id"]

    return None


def _get_group_by_title(project_id: str, title: str) -> Optional[dict]:
    """Fetch a group by project_id + title."""
    from modules.flow_gate.db.connection import get_store
    return get_store()._fetch_one(
        "SELECT * FROM groups WHERE project_id = ? AND title = ? AND deleted_at IS NULL",
        [project_id, title],
    )


# ── T244 helpers ─────────────────────────────────────────────────────────────

def _determine_action_scope(doc_ref_raw: Optional[str]) -> str:
    """Return 'edit' if doc_ref exists in the documents table, else 'new' (T244 §1-2)."""
    if not doc_ref_raw:
        return "new"
    doc = db_documents.get_by_id(doc_ref_raw)
    return "edit" if doc is not None else "new"


# ── M020 mention helpers ──────────────────────────────────────────────────────

_DOC_CODE_RE = re.compile(r'([A-Z]+\d+)$')


def _derive_status(result_doc_id, result_review: str | None) -> str:
    """Derive workflow slot status from result_doc_id + doc_review_status (D030 §2 SSOT)."""
    if result_doc_id is None:
        return "pending"
    if result_review == "approved":
        return "done"
    return "in_progress"


def _build_api_base(request: Request) -> str:
    """Build API base URL from the request."""
    base = str(request.base_url).rstrip("/")
    context = settings.CONTEXT.rstrip("/")
    return f"{base}{context}/api/v1"


def _build_mention_for_token(
    doc_ref: Optional[str],
    group_id: Optional[str],
    project_id: str,
    scratch_dir: str,
    raw_token: str,
    request: Request,
    ref_doc_ids: Optional[list] = None,
    action_scope: str = "new",
    locale: str = "ko",
    continuous: bool = False,
    merge_id: Optional[int] = None,
    continuous_review_mode: bool = False,
) -> Optional[str]:
    """R015 token issuance flow — R018 improved mention generation.

    Returns a mention whenever parent document info is available.
    Generates with placeholders even when no sequence/head exists.
    Includes the 5 most recent group documents in section 4 (section omitted if 0).
    """
    if action_scope == "resolve_conflict":
        if not group_id or merge_id is None:
            return None
        return _build_conflict_mention(
            group_id=group_id,
            project_id=project_id,
            merge_id=merge_id,
            scratch_dir=scratch_dir,
            raw_token=raw_token,
            api_base_url=_build_api_base(request),
        )

    if not doc_ref or not group_id:
        return None

    # Fetch parent document (use canonical doc_ref as-is)
    parent_doc = db_documents.get_by_id(doc_ref)
    if parent_doc is None:
        return None

    # Fetch workflow sequence head — proceed with placeholder if absent
    head_type = ""
    head_status = ""
    # Predecessor document for Section 1 'Document information' (R0001 / T0004): the
    # document the current step builds upon. Defaults to the spine doc for the first
    # step or a sequence-less doc; threading fields keep using parent_doc regardless.
    head_context_doc = parent_doc
    # R0001 #1 / T0004: Section 3 'Reference documents' should carry the two most
    # recent predecessor documents (the previous step's result + the one before it)
    # so the worker receives "previous + previous-previous + R" = 3 docs. This mirrors
    # advance_workflow (workflow_decision_service) — the client (NextActionModal) only
    # passes the spine R and the step's own instruction in selected_docs, so without
    # this merge the token path drops the 2-predecessor (e.g. NR is lost when building
    # TR). build_mention dedupes by slash-path, so a predecessor that coincides with an
    # already-selected doc collapses to one line.
    merged_ref_ids = list(ref_doc_ids or [])
    # 0084 TR0005 (B, defensive): resolve the sequence from the doc_ref even when it is a
    # produced child (a slot's result_doc_id, e.g. an approved CH/N) rather than the root
    # R. The root-only get_sequence_by_doc_id returned None for such a doc_ref, dropping
    # head_type/head_status and degrading the mention to a non-next-step copy (B0001). The
    # FE now passes the parent R (A fix), but resolving members here keeps the token mention
    # correct if any caller still hands a child doc_ref.
    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is not None:
        head = db_wfseq.get_effective_head(seq["id"])
        if head is not None:
            head_type = head["type"]
            head_status = _derive_status(head.get("result_doc_id"), head.get("result_doc_review_status"))
            pred_doc_id = db_wfseq.get_predecessor_result_doc_id(seq["id"], head.get("id"))
            if pred_doc_id:
                head_context_doc = db_documents.get_by_id(pred_doc_id) or parent_doc
            for _pid in db_wfseq.get_predecessor_result_doc_ids(seq["id"], head.get("id"), limit=2):
                if _pid and _pid not in merged_ref_ids:
                    merged_ref_ids.append(_pid)

    # Recent group documents (R018 §2-3, P005 §3-3) — up to 5.
    # Anchor at the group's latest document, not the workflow-owning parent (whose
    # seq is the group minimum); otherwise docs created after the parent — e.g. a
    # memo produced earlier in this sequence — never appear in the recent-docs list.
    # Fall back to the parent seq for an empty group.
    recent_before_seq = db_documents.get_group_max_seq(group_id) or parent_doc.get("seq", 0)
    group_recent_docs = db_documents.fetch_recent_group_docs(
        group_id=group_id,
        before_seq=recent_before_seq,
        limit=5,
    )

    token_rec = {
        "project": project_id,
        "group_id": group_id,
        "scratch_dir": scratch_dir,
    }
    api_base_url = _build_api_base(request)
    current_review = _load_current_revision_review(parent_doc) if action_scope == "edit" else None
    edit_reason = (
        "rejected"
        if action_scope == "edit" and parent_doc.get("doc_review_status") == "rejected"
        else "user_comment"
    )
    return mention_service.build_mention_from_token_rec(
        token_rec=token_rec,
        head_type=head_type,
        head_status=head_status,
        parent_doc=parent_doc,
        api_base_url=api_base_url,
        raw_token=raw_token,
        group_recent_docs=group_recent_docs if group_recent_docs else None,
        ref_doc_ids=merged_ref_ids if merged_ref_ids else None,
        action_scope=action_scope,
        current_review=current_review,
        edit_reason=edit_reason,
        locale=locale,
        head_context_doc=head_context_doc,
        continuous=continuous,
        # 0226 NR0003 §4 (부수): forward the review-mode flag so a continuous
        # review-phase mention carries the Q-allowed review variant, not no-stop.
        continuous_review_mode=continuous_review_mode,
    )


def _split_conflict_chunks(content: str) -> list[dict]:
    chunks: list[dict] = []
    state: Optional[str] = None
    current = {"ours": [], "base": [], "theirs": []}
    ours_label = ""
    theirs_label = ""
    for line in content.splitlines():
        if line.startswith("<<<<<<<"):
            state = "ours"
            current = {"ours": [], "base": [], "theirs": []}
            ours_label = line[7:].strip()
            theirs_label = ""
            continue
        if state == "ours" and line.startswith("|||||||"):
            state = "base"
            continue
        if state in ("ours", "base") and line.startswith("======="):
            state = "theirs"
            continue
        if state == "theirs" and line.startswith(">>>>>>>"):
            theirs_label = line[7:].strip()
            chunks.append({
                "ours_label": ours_label,
                "theirs_label": theirs_label,
                "ours": current["ours"],
                "base": current["base"],
                "theirs": current["theirs"],
            })
            state = None
            continue
        if state in current:
            current[state].append(line)
    return chunks


def _build_conflict_mention(
    *,
    group_id: str,
    project_id: str,
    merge_id: int,
    scratch_dir: str,
    raw_token: str,
    api_base_url: str,
) -> Optional[str]:
    conflicts = git_service.list_conflicts(group_id, merge_id)
    files = conflicts.get("files") or []
    resolve_url = f"{api_base_url}/groups/{group_id}/git/merge/{merge_id}/resolve-token"
    chunks_payload = []
    for file in files:
        content = file.get("content") or ""
        chunks_payload.append({
            "path": file.get("path"),
            "conflict_count": file.get("conflict_count"),
            "chunks": _split_conflict_chunks(content),
            "raw_content": content,
        })
    payload = {
        "group_id": group_id,
        "merge_id": merge_id,
        "branch": conflicts.get("branch"),
        "base_branch": conflicts.get("base_branch"),
        "files": chunks_payload,
    }
    return (
        "## Document information\n"
        "---\n"
        f"project: {project_id}\n"
        f"group: {group_id}\n"
        "type: git_conflict\n"
        f"merge_id: {merge_id}\n\n"
        "## Git conflict auto-resolve task\n"
        "---\n"
        "Resolve every conflict autonomously. Do not ask the user to choose chunks. "
        "Produce complete file contents with all conflict markers removed, then call the bound resolve endpoint.\n\n"
        "## Bound resolve endpoint\n"
        "---\n"
        f"POST {resolve_url}\n"
        f"Authorization: Bearer {raw_token}\n"
        "Content-Type: application/json\n\n"
        "{\n"
        "  \"files\": [\n"
        "    {\"path\": \"<project-source-root relative path>\", \"content\": \"<complete resolved file content>\"}\n"
        "  ],\n"
        "  \"complete\": true\n"
        "}\n\n"
        "The bearer token is bound to exactly this group_id and merge_id. Other git/config/finalize endpoints are not authorized.\n\n"
        "## Conflict session\n"
        "---\n"
        f"scratch_dir: {scratch_dir}\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )

def _load_current_revision_review(doc: dict) -> Optional[dict]:
    """Return the latest review only when it targets the document's current revision."""
    from modules.flow_gate.db import document_reviews as db_reviews

    row = db_reviews.get_latest_by_doc(doc.get("doc_id", ""))
    if row is None:
        return None
    current_revision = int(doc.get("revision_no") or 0)
    if int(row.get("revision_no") or 0) != current_revision:
        return None
    raw_findings = row.get("findings")
    findings: list = []
    if isinstance(raw_findings, str):
        try:
            parsed = json.loads(raw_findings)
            if isinstance(parsed, list):
                findings = parsed
        except (TypeError, ValueError):
            findings = []
    elif isinstance(raw_findings, list):
        findings = raw_findings
    return {
        "revision_no": current_revision,
        "verdict": row.get("verdict"),
        "findings": findings,
        "comment": row.get("comment"),
        "reviewed_at": row.get("reviewed_at"),
    }

