"""The one boundary between the Conversation contract and ``modules.flow_gate`` (D0006 §2.2).

Everything this package reuses from the existing tree -- AI invoke run records and
clocks, the group worktree and Git helpers, the self-check process executor, chat
settings, tokens, documents, numbering and permissions -- is reached through the
functions below, so the reuse surface is visible in one file and tests can replace it
in one place. Imports are deferred to call time: ``modules.flow_gate`` modules import
each other heavily at load time, and nothing here may become part of that cycle.

The direction is fixed: this module imports ``modules.flow_gate``; nothing in
``modules.flow_gate`` imports ``flowgate`` (tests/test_fg_dependency_direction.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# The API worker's tool-result cap. Read, never copied (D0006 §3.5).
from modules.flow_gate.services.ai_invoke.runtime import API_TOOL_RESULT_MAX_CHARS  # noqa: F401


# ── AI invoke run record / clocks ────────────────────────────────────────────

def _ai_invoke():
    from modules.flow_gate.services import ai_invoke_service

    return ai_invoke_service


def get_run_record(run_id: Optional[str]) -> Optional[dict]:
    """The live, process-local record of an AI run (None when unknown or finished long ago)."""
    if not run_id:
        return None
    try:
        return _ai_invoke().get_run_record(str(run_id))
    except Exception:
        return None


def now_mono() -> float:
    """The engine's monotonic clock (the one its watchdog reads)."""
    return float(_ai_invoke()._now_mono())


def absolute_remaining_sec(run: dict) -> float:
    """Seconds left before the run's hard ceiling (``runtime._absolute_remaining_sec``).

    The hop's ``_remaining_sec`` is deliberately NOT offered here: it turns negative for
    a run that keeps working past ``timeout_sec`` (D0006 §3.5.1).
    """
    from modules.flow_gate.services.ai_invoke import runtime

    return float(runtime._absolute_remaining_sec(run))


# ── group worktree / Git ─────────────────────────────────────────────────────

def _git_service():
    from modules.flow_gate.services import git_service

    return git_service


def effective_src_root_ex(project_id: Optional[str], group_id: Optional[str]) -> tuple[Optional[Path], str]:
    """(managed worktree path, reason); the path is None unless reason is ``worktree``."""
    return _git_service().effective_src_root_ex(project_id, group_id)


WORKTREE_REASON = "worktree"


def git_read_timeout_sec() -> int:
    return int(_git_service().GIT_READ_TIMEOUT_SEC)


def run_git(root: Path, args: list[str], *, timeout: int, index_file: Optional[str] = None):
    """``git <args>`` in ``root``; ``index_file`` points git at an isolated index."""
    extra = {"GIT_INDEX_FILE": index_file} if index_file else None
    return _git_service()._run_git(args, cwd=Path(root), timeout=timeout, extra_env=extra)


def is_hidden_source_path(path: str) -> bool:
    return bool(_git_service()._is_hidden_source_path(path))


def validate_blob_path(path: str) -> None:
    _git_service()._validate_blob_path(path)


def diff_side_from_tree(root: Path, tree: Optional[str], path: str) -> dict:
    return _git_service()._diff_side_from_commit(Path(root), tree, path)


def diff_status(old: dict, new: dict, path: str) -> str:
    return _git_service()._diff_status(old, new, path)


def git_service_error_type() -> type:
    return _git_service().GitServiceError


def emit_event(event_type: str, project_id: str, group_id: Optional[str], payload: dict) -> None:
    """Best-effort SSE broadcast through the existing publisher (never raises)."""
    try:
        _git_service()._emit(event_type, project_id, group_id, payload)
    except Exception:
        pass


# ── process execution (the self-check executor and policy primitives) ────────

def selfcheck_policy():
    from modules.flow_gate.services import tr_self_check_policy

    return tr_self_check_policy


def executor():
    from modules.flow_gate.services import tr_self_check_executor

    return tr_self_check_executor


def redact(text: Optional[str]) -> str:
    from modules.flow_gate.services.tr_self_check_service import _redact

    return _redact(text)


# ── chat settings / tokens / auth ────────────────────────────────────────────

def resolve_command_policy(user_id: Optional[str]) -> str:
    from modules.flow_gate.services import chat_settings_service

    return chat_settings_service.resolve_command_policy(user_id)


def command_policy_domain() -> tuple[str, ...]:
    from modules.flow_gate.services import chat_settings_service

    return tuple(chat_settings_service.COMMAND_POLICY_DOMAIN)


def verify_token(raw_token: str) -> dict:
    """Token record of a valid (unrevoked, unconsumed, unexpired) raw token; raises otherwise."""
    from modules.flow_gate.services import token_service

    return token_service.verify(raw_token)


def current_user_dependency():
    """The session-user FastAPI dependency the existing routes use."""
    from modules.flow_gate.auth.middleware import get_current_user

    return get_current_user


def has_permission(user: dict, permission: str, project_id: Optional[str]) -> bool:
    from modules.flow_gate.rbac.decorators import _has_permission

    return bool(_has_permission(user or {}, permission, project_id))


# ── documents / projects / groups / numbering ────────────────────────────────

def get_document(doc_id: Optional[str]) -> Optional[dict]:
    if not doc_id:
        return None
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def get_project(project_id: str) -> Optional[dict]:
    from modules.flow_gate.db import projects as db_projects

    return db_projects.get_by_id(project_id)


def get_group(group_id: str) -> Optional[dict]:
    from modules.flow_gate.db import groups as db_groups

    return db_groups.get_by_id(group_id)


def is_group_disposed(group_id: Optional[str]) -> bool:
    from modules.flow_gate import process_service

    return bool(process_service.is_group_disposed(group_id))


def title_max_len() -> int:
    from modules.flow_gate import linter

    return int(linter.TITLE_MAX_LEN)


def auto_complete_types() -> frozenset:
    from modules.flow_gate.documents.constants import AUTO_COMPLETE_TYPES

    return frozenset(AUTO_COMPLETE_TYPES)


def reserve_document(group_id: str, type_code: str, module: str) -> str:
    """Reserve the next group-shared document code (counter + CAS + unique retry)."""
    from modules.flow_gate.numbering import numbering_service

    return numbering_service.reserve_document(group_id=group_id, doc_type=type_code, module=module)


def doc_code_seq(doc_code: str) -> int:
    from modules.flow_gate.documents.type_code import doc_code_seq_text

    numeric = doc_code_seq_text(doc_code)
    return int(numeric) if numeric is not None else 0


def project_branch(project_id: str) -> str:
    """``project_settings.branch`` (``main`` when unset), as the document routes resolve it."""
    try:
        from modules.flow_gate.db import projects as db_projects

        settings = db_projects.get_settings(project_id)
        if settings:
            return (settings.get("branch") or "main").strip() or "main"
    except Exception:
        pass
    return "main"


def document_file_path(project_id: str, group_id: str, doc_code: str, module: str) -> Path:
    from modules.flow_gate.storage import paths as storage_paths

    return storage_paths.document_path(
        project_id=project_id, group_code=group_id, doc_code=doc_code,
        filename="document.md", module=module, branch=project_branch(project_id),
    )


def to_storage_relative(path: Path, project_id: str) -> str:
    from modules.flow_gate.storage import paths as storage_paths

    return storage_paths.to_storage_relative(path, project_id)


def store_transaction():
    from modules.flow_gate.db.connection import get_store

    return get_store().transaction()


def create_document(data: dict[str, Any], actor_user_id: str) -> dict:
    """Document row + ``doc_created`` workflow event (``document_service.create_document``)."""
    from modules.flow_gate.documents import document_service

    return document_service.create_document(data, actor_user_id=actor_user_id)


def update_document(doc_id: str, fields: dict[str, Any]) -> Optional[dict]:
    from modules.flow_gate.db import documents as db_documents

    db_documents.update(doc_id, fields)
    return db_documents.get_by_id(doc_id)


def update_meta_cas(doc_id: str, expected_meta: str, meta: str) -> bool:
    from modules.flow_gate.db import documents as db_documents

    return bool(db_documents.update_meta_cas(doc_id, expected_meta, meta))


def rename_document(doc_id: str, title: str, actor_user_id: str) -> Optional[dict]:
    """The title write of ``PATCH /documents/{doc_id}`` (``document_service.update_document``)."""
    from modules.flow_gate.documents import document_service

    return document_service.update_document(doc_id, {"title": title}, actor_user_id=actor_user_id)


# ── run-less one prompt (the AI connection test path) ────────────────────────

# ``resolve_subject`` refusals: the provider cannot be used, nothing was asked.
PROMPT_UNAVAILABLE_REASONS = frozenset({"provider_unavailable", "provider_not_found", "provider_disabled"})


def complete_prompt(project_id: str, provider_id: str, prompt: str, *, timeout: float,
                    max_tokens: int) -> tuple[Optional[str], Optional[str]]:
    """(answer, None) or (None, reason) for one prompt on the project's effective AI
    connection, through ``ai_connection_test_service.resolve_subject`` + ``execute_prompt``."""
    from modules.flow_gate.services import ai_connection_test_service as connection
    from modules.flow_gate.services.ai_invoke import runtime as probe

    try:
        subject = connection.resolve_subject(project_id, provider_id, fingerprint=False)
    except connection.ConnectionTestError as exc:
        return None, exc.code
    outcome = connection.execute_prompt(subject, prompt, probe.ProbeHandle(), timeout, max_tokens=max_tokens)
    if outcome.kind != probe.PROBE_OK:
        return None, connection.FAILURE_REASON.get(outcome.kind, "internal_error")
    return outcome.text, None


# ── conversation turns ───────────────────────────────────────────────────────

def current_head_seq(doc_id: str) -> int:
    from modules.flow_gate.db import conversation_turns

    return int(conversation_turns.current_head_seq(doc_id))


def first_turns(doc_id: str, limit: int) -> list[dict]:
    from modules.flow_gate.db import conversation_turns

    return list(conversation_turns.first_turns(doc_id, limit))
