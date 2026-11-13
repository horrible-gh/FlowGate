"""Chat token/run identity, effective kind, claim state and the in-boundary recheck
(L#3 §2.2, §2.3, §2.7 ``chat_recheck``, §2.8 E1·E2·E8).

Read-only: nothing here writes ``tokens``, ``user_chat_source_access`` or the run record.
The effective kind is the token's issue-time ``source_access`` through the existing
fail-closed ``tool_registry.kind_for_token``; the stored mode is never read again to decide
it. Every refusal is a P#2 §6 code (``chat_errors``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import chat_errors, chat_gate
from .errors import GraphApiError, internal_error

API = "api"
CLI = "cli"
CHAT_WRITE_KIND = "read_write"
RECHECK_DB_READS = 2
LIVE, REVOKED, CONSUMED, EXPIRED = "LIVE", "REVOKED", "CONSUMED", "EXPIRED"


# ── storage / legacy reads (deferred imports; replaced in tests) ─────────────

def _read_token(token_id: str) -> Optional[dict]:
    from modules.flow_gate.db import tokens as db_tokens
    return db_tokens.get_by_id(token_id)


def _read_access_row(user_id: str, project_id: str, group_id: str) -> Optional[dict]:
    from modules.flow_gate.db import user_chat_source_access
    return user_chat_source_access.get(user_id, project_id, group_id)


def _kind_for_token(token_row: dict):
    from modules.flow_gate.services import tool_registry
    return tool_registry.kind_for_token(token_row)


def _verify_token(token_raw: str) -> dict:
    from modules.flow_gate.services import token_service
    return token_service.verify(token_raw)


def _get_run_record(run_id) -> Optional[dict]:
    if not run_id:
        return None
    from modules.flow_gate.services import ai_invoke_service
    return ai_invoke_service.get_run_record(str(run_id))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guarded(read, *args):
    """E8: a failed read refuses the edit (500), never allows it."""
    try:
        return read(*args)
    except GraphApiError:
        raise
    except Exception:
        raise internal_error()


def _empty(value) -> bool:
    return value is None or str(value) == ""


def _current_token_id(run: dict) -> str:
    """The existing ``commands.authorize_token`` fallback: ``current_token_id or token_id``."""
    return str(run.get("current_token_id") or run.get("token_id") or "")


# ── §2.2 chat_authz ──────────────────────────────────────────────────────────

def effective_write_kind(token_row: dict) -> str:
    """``kind_for_token`` as is (``read`` | ``read_write``); anything else refuses editing."""
    kind, _reason = _guarded(_kind_for_token, token_row)
    return kind if kind in ("read", "read_write") else "none"


def claim_pending(token_row: dict) -> bool:
    """CLAIMED with this token's id still in the one-shot marker (read only; no row = no marker)."""
    row = _guarded(_read_access_row, token_row.get("issued_to"),
                   token_row.get("project") or token_row.get("project_id"), token_row.get("group_id"))
    if row is None or _empty(row.get("one_shot_token_id")):
        return False
    return str(row.get("one_shot_token_id")) == str(token_row.get("token_id") or "")


def token_live(token_row: Optional[dict], now: Optional[datetime] = None) -> str:
    """Read-only equivalent of ``token_service.verify``'s order: revoked → consumed → expired."""
    if token_row is None or token_row.get("revoked_at") is not None:
        return REVOKED
    if token_row.get("consumed_at") is not None:
        return CONSUMED
    now = now or _now()
    try:
        expires_at = datetime.fromisoformat(str(token_row.get("expires_at")))
    except (TypeError, ValueError):
        return EXPIRED          # unreadable expiry fails closed
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return EXPIRED
    return LIVE


# ── §2.3 authorize_chat_run ──────────────────────────────────────────────────

def check_run_token_binding(run: Optional[dict], token_row: Optional[dict]) -> None:
    """Pure comparison (no write, no DB read); the entry and the in-boundary recheck share it."""
    if token_row is None or token_row.get("action_scope") != "chat" or _empty(token_row.get("ai_run_id")):
        raise chat_errors.forbidden()
    if run is None:
        raise chat_errors.run_finished(token_row.get("ai_run_id"))
    if run.get("action_scope") != "chat" or _empty(run.get("doc_ref")):
        raise chat_errors.forbidden()
    if _empty(token_row.get("doc_ref")) or str(token_row.get("doc_ref")) != str(run.get("doc_ref")):
        raise chat_errors.forbidden()
    axes = ((token_row.get("project") or token_row.get("project_id"), run.get("project_id")),
            (token_row.get("group_id"), run.get("group_id")),
            (token_row.get("ai_run_id"), run.get("run_id")))
    if any(_empty(left) or str(left) != str(right or "") for left, right in axes):
        raise chat_errors.forbidden()
    current = _current_token_id(run)
    if not current or str(token_row.get("token_id") or "") != current:
        raise chat_errors.forbidden()
    if _empty(token_row.get("issued_to")) or str(token_row.get("issued_to")) != str(run.get("issued_to") or ""):
        raise chat_errors.forbidden()


def _cancel_event_set(run: dict) -> bool:
    event = run.get("cancel_event")
    try:
        return bool(event is not None and event.is_set())
    except Exception:
        return True             # unreadable cancel state fails closed (treated as cancelling)


def _check_run_state(run: dict) -> None:
    """§4.1 rows 5 and 6: the gate's FIRST reason picks the code even while the run still reads running."""
    run_id = run.get("run_id")
    gate_reason = chat_gate.closed_reason(run_id)
    if run.get("status") == "cancelling" or _cancel_event_set(run) or gate_reason == chat_gate.CANCELLED:
        raise chat_errors.run_cancelled(run_id)
    if run.get("status") not in (None, "running") or run.get("finished_at") or gate_reason is not None:
        raise chat_errors.run_finished(run_id)


def authorize_chat_run(via: str, token_raw: Optional[str], run_hint: Optional[dict]):
    """Identity and life of the chat run, API and CLI alike. Returns ``(run, token_row)``.

    CLI: verify the Bearer (401) → run record. API: trust the run the worker handed over;
    an empty current token id is 403 BEFORE any token row read, a dead token (missing row,
    revoked, consumed, expired) behind an existing id is 401.
    """
    if via == CLI:
        if not token_raw:
            raise chat_errors.unauthorized()
        try:
            token_row = _verify_token(token_raw)
        except GraphApiError:
            raise
        except Exception as exc:
            detail = getattr(exc, "detail", None)          # the existing verify message, kept
            raise chat_errors.unauthorized(detail if isinstance(detail, str) else None)
        if not isinstance(token_row, dict):
            raise chat_errors.unauthorized()
        run = _guarded(_get_run_record, token_row.get("ai_run_id")) if token_row.get("ai_run_id") else None
    elif via == API:
        run = run_hint
        if run is None:
            raise chat_errors.run_finished(None)
        current_id = _current_token_id(run)
        if not current_id:
            raise chat_errors.forbidden()                       # (A) no id: decided before any token read
        token_row = _guarded(_read_token, current_id)
        if token_row is None or token_live(token_row) != LIVE:
            raise chat_errors.unauthorized()                    # (B) id present, token dead
    else:
        raise ValueError(f"unknown chat transport: {via}")
    check_run_token_binding(run, token_row)
    _check_run_state(run)
    return run, token_row


# ── flowgate.v02gui.0010: the live recheck of a non-edit write (a card question's answer) ──

def chat_recheck_alive(run: dict, token_row: dict, holder: chat_gate.TicketHolder) -> None:
    """Gate first, then one token read: the run is still alive and its current token is still this one.

    No edit mode and no one-shot claim are asked: a question's answer is judged by the user's
    ``perm_document_create`` alone (Q#600), in reading mode too. The gate ticket keeps the answer ahead of the
    run's finalize, so the run-end follow-up sees it (``question_wait.follow``)."""
    holder.ticket = chat_gate.enter(run.get("run_id"))
    if run.get("status") == "cancelling" or _cancel_event_set(run):
        raise chat_errors.run_cancelled(run.get("run_id"))
    if run.get("finished_at") or run.get("status") not in (None, "running"):
        raise chat_errors.run_finished(run.get("run_id"))
    fresh = _guarded(_read_token, token_row.get("token_id"))
    if token_live(fresh) != LIVE:
        raise chat_errors.unauthorized()
    check_run_token_binding(run, fresh)
    if str(fresh.get("issued_to") or "") != str(token_row.get("issued_to") or ""):
        raise chat_errors.forbidden()


# ── §2.7 chat_recheck (inside the revise() boundary) ─────────────────────────

def chat_recheck(run: dict, token_row: dict, holder: chat_gate.TicketHolder) -> None:
    """Gate first, then two short reads (token row, claim row): RECHECK_DB_READS = 2."""
    holder.ticket = chat_gate.enter(run.get("run_id"))
    if run.get("status") == "cancelling" or _cancel_event_set(run):
        raise chat_errors.run_cancelled(run.get("run_id"))
    if run.get("finished_at") or run.get("status") not in (None, "running"):
        raise chat_errors.run_finished(run.get("run_id"))
    fresh = _guarded(_read_token, token_row.get("token_id"))                    # DB read 1
    if token_live(fresh) != LIVE:
        raise chat_errors.unauthorized()
    check_run_token_binding(run, fresh)          # the run's CURRENT token, re-read now
    if str(fresh.get("issued_to") or "") != str(token_row.get("issued_to") or ""):
        raise chat_errors.forbidden()
    kind = effective_write_kind(fresh)
    if kind != CHAT_WRITE_KIND:
        raise chat_errors.edit_mode_denied(run.get("run_id"), kind)
    if claim_pending(fresh):                                                   # DB read 2
        raise chat_errors.forbidden()
