"""Token service — issue / verify / consume / revoke (D020 §2).

All hash comparisons use hmac.compare_digest() to prevent timing attacks (D020 §7-1).
Pepper is managed via environment variables FLOWGATE_TOKEN_PEPPER_<id> / FLOWGATE_TOKEN_PEPPER_ACTIVE_ID.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.storage.paths import (
    get_storage_root,
    to_storage_relative,
    resolve_storage_dir,
)

# ── Constants ──────────────────────────────────────────────────────────────────────

TOKEN_TTL_HOURS = 24


# ── Internal helpers ─────────────────────────────────────────────────────────────────

def _pepper_env(name: str) -> str:
    """Resolve a pepper env var, preferring the real environment.

    Real OS env vars (and tests that set os.environ directly) take priority.
    Falls back to the pydantic `settings` object, because pydantic-settings loads
    .env into `settings` only — it does NOT export those values to os.environ, so
    pepper values supplied via .env are invisible to os.environ.get() alone.
    """
    value = os.environ.get(name)
    if not value:
        try:
            from config import settings
            value = getattr(settings, name, None)
            if value is None:
                # pydantic matches .env vars case-insensitively and stores them on
                # uppercase fields; the pepper id (e.g. ACTIVE_ID="v1") may differ
                # in case from the field suffix ("V1"). Mirror that tolerance here.
                lname = name.lower()
                for fname in getattr(settings, "model_fields", {}):
                    if fname.lower() == lname:
                        value = getattr(settings, fname, None)
                        break
        except Exception:
            value = None
    return (value or "").strip()


def _active_pepper() -> tuple[str, str]:
    """Return (pepper_id, pepper_value).

    Reads the active pepper id from FLOWGATE_TOKEN_PEPPER_ACTIVE_ID,
    and the actual value from FLOWGATE_TOKEN_PEPPER_<id>.
    """
    active_id = _pepper_env("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID")
    if not active_id:
        raise RuntimeError(
            "FLOWGATE_TOKEN_PEPPER_ACTIVE_ID environment variable is not set."
        )
    value = _pepper_env(f"FLOWGATE_TOKEN_PEPPER_{active_id}")
    if not value:
        raise RuntimeError(
            f"FLOWGATE_TOKEN_PEPPER_{active_id} environment variable is not set."
        )
    return active_id, value


def _pepper_by_id(pepper_id: str) -> str:
    """Return the pepper value for pepper_id. For supporting old peppers during verification."""
    value = _pepper_env(f"FLOWGATE_TOKEN_PEPPER_{pepper_id}")
    if not value:
        raise RuntimeError(
            f"FLOWGATE_TOKEN_PEPPER_{pepper_id} environment variable is not set."
        )
    return value


def _hash_token(raw: str, pepper: str) -> str:
    """Return SHA256(raw + pepper) (64-character hex string)."""
    return hashlib.sha256((raw + pepper).encode("utf-8")).hexdigest()


def _verify_hash(stored_hash: str, candidate_hash: str) -> bool:
    """Constant-time comparison to prevent timing attacks (D020 §7-1)."""
    return hmac.compare_digest(stored_hash.encode(), candidate_hash.encode())


def _next_token_id() -> str:
    """Assign a token_id in the format tok_YYYYMMDD_NNNNNN."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    count = db_tokens.count_by_date_prefix(date_str)
    return f"tok_{date_str}_{count + 1:06d}"


def _sanitize_project_name(name: str) -> str:
    """Normalize project_name to a filesystem-safe form (allows only alphanumerics, underscores, and hyphens)."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "_"


def _scratch_dir(project_id: str, token_id: str) -> Path:
    """Return the per-token scratch directory path (D020 §2-7).

    Uses project_name instead of project_id in the path for readability (T244 §1-1).
    Falls back to project_id if the project lookup fails.
    """
    project = db_projects.get_by_id(project_id)
    project_name = project["project_name"] if project else project_id
    safe_name = _sanitize_project_name(project_name)
    return get_storage_root() / "work" / safe_name / token_id


def scratch_dir_path(project_id: str, token_id: str) -> str:
    """Return the per-token scratch directory path as a string (single source of truth, D10 correction)."""
    return str(_scratch_dir(project_id, token_id))


# ── Public API ─────────────────────────────────────────────────────────────────

def issue(
    project: str,
    group_id: Optional[str],
    action_scope: str,
    doc_ref: Optional[str],
    issued_to: str,
    continuation_target_seq: Optional[int] = None,
    continuation_review_mode: bool = False,
    continuation_locale: Optional[str] = None,
    continuation_instruction_mode: Optional[str] = None,
) -> dict:
    """Issue a token → return dict containing (raw_token, token_id, expires_at, scratch_dir).

    1. Generate raw_token
    2. Compute hash
    3. INSERT into tokens
    4. Create scratch directory
    5. Record workflow_events.token_issued

    Continuous work (group 0051 R0001 / NR0003 B안): when continuation_target_seq is
    given, the token carries the unmanned-chain stop point (target item_seq) and the
    AI-review-mode flag (T0004/CH0006). Both persist on the token so the inbox self-chain
    (inbox_routes._handle_new) can read them off the just-consumed token and mint the
    next step's token without a human re-issuing it. NULL/False ⇒ ordinary token.
    """
    raw_token = secrets.token_urlsafe(32)
    pepper_id, pepper = _active_pepper()
    token_hash = _hash_token(raw_token, pepper)
    token_id = _next_token_id()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=TOKEN_TTL_HOURS)
    created_at_str = now.isoformat(timespec="seconds")
    expires_at_str = expires_at.isoformat(timespec="seconds")

    scratch_path = _scratch_dir(project, token_id)
    scratch_path.mkdir(parents=True, exist_ok=True)

    db_tokens.create({
        "token_id": token_id,
        "hash": token_hash,
        "pepper_id": pepper_id,
        "project": project,
        "group_id": group_id,
        "doc_ref": doc_ref,
        "action_scope": action_scope,
        "issued_to": issued_to,
        "created_at": created_at_str,
        "expires_at": expires_at_str,
        # Persist relative (L0054.0002): scratch_dir lives under storage_root/work,
        # so it stays host/OS-invariant. verify() resolves it back to absolute.
        "scratch_dir": to_storage_relative(scratch_path, project),
        "continuation_target_seq": continuation_target_seq,
        "continuation_review_mode": continuation_review_mode,
        # Chosen locale persisted so the unmanned self-chain honors it on every hop
        # without depending on a per-request x-locale header (group 0099 B0001).
        "continuation_locale": continuation_locale,
        "continuation_instruction_mode": continuation_instruction_mode,
    })

    db_events.create({
        "event_type": "token_issued",
        "project_id": project,
        "group_id": group_id,
        "document_id": None,
        "actor_user_id": issued_to,
        "from_state": None,
        "to_state": None,
        "metadata": (
            f'{{"token_id":"{token_id}",'
            f'"action_scope":"{action_scope}",'
            f'"doc_ref":"{doc_ref}"}}'
        ),
    })

    return {
        "raw_token": raw_token,
        "token_id": token_id,
        "expires_at": expires_at_str,
        "scratch_dir": str(scratch_path),
        "group_id": group_id,
        "continuation_target_seq": continuation_target_seq,
        "continuation_review_mode": continuation_review_mode,
        "continuation_locale": continuation_locale,
        "continuation_instruction_mode": continuation_instruction_mode,
    }


def verify(raw_token: str) -> dict:
    """Verify a token → return token record. Raises HTTPException on failure.

    State check priority (D020 §2-2):
      1. revoked_at IS NOT NULL → 401
      2. consumed_at IS NOT NULL → 401
      3. expires_at < now() → 401
      4. valid
    """
    # Compute hash with all active/inactive peppers, then DB lookup
    # Try active pepper first; on failure, remap from DB pepper_id and retry
    token_rec = _find_token_by_raw(raw_token)
    if token_rec is None:
        raise HTTPException(
            status_code=401,
            detail="Token is invalid",
        )

    if token_rec.get("revoked_at"):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
        )
    if token_rec.get("consumed_at"):
        raise HTTPException(
            status_code=401,
            detail="Token has already been used",
        )

    expires_at = datetime.fromisoformat(token_rec["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=401,
            detail="Token has expired. Please inform the user of the copied message result.",
        )

    # scratch_dir is persisted relative (L0054.0002). Every consumer of the verified
    # record (the inbox doc_path security jail, worker scratch staging, mention
    # bodies) needs a usable absolute OS path, so resolve it here at the single
    # gateway. Legacy absolute values pass through unchanged.
    stored_scratch = token_rec.get("scratch_dir")
    if stored_scratch:
        resolved_scratch = resolve_storage_dir(stored_scratch, token_rec.get("project"))
        if resolved_scratch is not None:
            token_rec["scratch_dir"] = str(resolved_scratch)

    return token_rec


def _find_token_by_raw(raw_token: str) -> Optional[dict]:
    """Search the tokens table by raw_token.

    1) Compute hash with the active pepper → DB lookup
    2) If not found, cannot try other known peppers from raw_token alone → return None
       (pepper_id is only known from a record whose hash matches)
    Note: Pepper rotation policy (§7-2) — old pepper tokens expire naturally within TTL. This
    implementation searches only with the active pepper, so old tokens issued just after a
    pepper rotation cannot be verified.
    Recommended: exhaust TTL of pre-rotation tokens before rotating pepper (operational procedure).
    """
    try:
        pepper_id, pepper = _active_pepper()
    except RuntimeError:
        return None

    candidate_hash = _hash_token(raw_token, pepper)
    row = db_tokens.get_by_hash(candidate_hash)
    if row and _verify_hash(row["hash"], candidate_hash):
        return row
    return None


def consume(token_id: str, project_id: str, doc_id: Optional[str] = None) -> None:
    """consumed_at = now() + workflow_events.token_consumed (D020 §2-5)."""
    db_tokens.consume(token_id)
    token_rec = db_tokens.get_by_id(token_id)
    if token_rec is None:
        return

    db_events.create({
        "event_type": "token_consumed",
        "project_id": project_id,
        "group_id": token_rec.get("group_id"),
        "document_id": None,
        "actor_user_id": token_rec["issued_to"],
        "from_state": None,
        "to_state": None,
        "metadata": (
            f'{{"token_id":"{token_id}"'
            + (f',"doc_id":"{doc_id}"' if doc_id else "")
            + "}"
        ),
    })


def increment_dry_run(token_id: str) -> None:
    """Bump the per-token dry-run attempt counter (R0001 dry-run, group 0050).

    Thin wrapper that preserves inbox_routes' dependency boundary: the endpoint goes
    through token_service (like verify/consume) and never imports db.tokens directly
    (L0007 §6.2). The actual UPDATE lives in db_tokens.increment_dry_run (DB0008 §3).
    """
    db_tokens.increment_dry_run(token_id)


def revoke(token_id: str, reason: str = "user_cancel") -> None:
    """revoked_at = now() + workflow_events.token_revoked (D020 §2-5)."""
    token_rec = db_tokens.get_by_id(token_id)
    if token_rec is None:
        raise HTTPException(status_code=404, detail=f"Token not found: {token_id}")

    db_tokens.revoke(token_id)

    db_events.create({
        "event_type": "token_revoked",
        "project_id": token_rec["project"],
        "group_id": token_rec.get("group_id"),
        "document_id": None,
        "actor_user_id": token_rec["issued_to"],
        "from_state": None,
        "to_state": None,
        "metadata": f'{{"token_id":"{token_id}","reason":"{reason}"}}',
    })
