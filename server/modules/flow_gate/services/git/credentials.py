"""Git credential handling: master-key encryption, secret masking, and
commit-author env resolution.

Extracted from git_service.py (flowgate.default.0550 T0007, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import base64
import copy
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES as _AES

from modules.flow_gate.storage.paths import get_storage_root

_log = logging.getLogger(__name__)

# ── Configurable author (flowgate.default.0237 — R0001/NR0003) ────────────────
# A project may override the AUTHOR of server-made commits so work does not land
# under the FlowGate name (R0001). Only the author moves; the committer above stays
# FlowGate, which is the GitHub-App convention and keeps the history honest —
# contribution graphs key off the author, so this is what R0001 actually needs.
# The override travels in GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL rather than `-c user.*`
# (which would move the committer too) or `--author` (which `git merge` rejects —
# NR0003 §4). Both fields are stored together or not at all; an empty ident makes
# `git commit` fail with "Author identity unknown", so "" is normalized to NULL.
GIT_AUTHOR_NAME_MAX = 100
GIT_AUTHOR_EMAIL_MAX = 200
MASK_KEEP_PREFIX = 4
MASK_KEEP_SUFFIX = 4
MASK_MIN_LEN = 9
SECRET_ENV_KEY = "FLOWGATE_GIT_ENCRYPT_KEY"
SECRET_ENV_KEY_PREV = "FLOWGATE_GIT_ENCRYPT_KEY_PREV"


def _author_env_for(project_id: Optional[str]) -> Optional[dict]:
    """GIT_AUTHOR_* env for a project's configured author, or None to use the default.

    Best-effort: a missing/partial config or an unreadable row simply falls back to
    the FlowGate identity — an author override must never break a commit.
    """
    if not project_id:
        return None
    try:
        from modules.flow_gate.services import git_service as _gs
        cfg = _gs.db_git.get_config(project_id)
    except Exception:
        _log.warning("git author lookup failed for %s", project_id, exc_info=True)
        return None
    return _author_env_from_cfg(cfg)


def _author_env_from_cfg(cfg: Optional[dict]) -> Optional[dict]:
    """Same as _author_env_for but for an already-loaded config row."""
    if not cfg:
        return None
    name = (cfg.get("author_name") or "").strip()
    email = (cfg.get("author_email") or "").strip()
    if not name or not email:   # partial rows are impossible via save_config (E-author)
        return None             # but a hand-edited DB must still commit, not crash
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email}


class GitServiceError(Exception):
    """Stable Git failure contract; message and diagnostic are never UI copy."""

    def __init__(
        self, status: int, code: str, message: str, details: Optional[dict] = None,
        *, params: Optional[dict] = None, diagnostic: Optional[str] = None,
    ):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = copy.deepcopy(details) if isinstance(details, dict) else {}
        clean_params = {}
        for key, value in (params.items() if isinstance(params, dict) else ()):
            if isinstance(value, (str, bool)) or (
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and value == value and abs(value) != float("inf")
            ):
                clean_params[key] = value
            elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
                clean_params[key] = list(value)
        self.params = clean_params
        self.diagnostic = diagnostic if isinstance(diagnostic, str) else None


def git_error_envelope(exc: GitServiceError) -> dict:
    """Serialize public fields identically for local guards and the global handler."""
    error = {"code": exc.code, "message": exc.message}
    if exc.params:
        error["params"] = dict(exc.params)
    if exc.details:
        error["details"] = dict(exc.details)
    return {"ok": False, "error": error}


# ── Master key / encryption / masking (L0006 §2.3 — TOTP precedent) ─────────

def _key_file_path() -> Path:
    return get_storage_root(create=True) / ".flowgate-git-key"


def _load_key_material(env_name: str) -> Optional[bytes]:
    val = os.environ.get(env_name)
    if not val:
        try:
            from config import settings as _settings  # lazy — import cycle safety
            val = getattr(_settings, env_name, None)
        except Exception:
            val = None
    if not val:
        return None
    raw = base64.b64decode(val)
    if len(raw) != 32:
        raise ValueError(f"{env_name} must be a base64-encoded 32-byte key.")
    return raw


def _get_current_key(create: bool = False) -> bytes:
    """Resolve the master key: env/.env → persisted storage file (→ generate).

    Boot-time provisioning (L0006 E5): the docker entrypoint persists the key
    into the container env; host installs without one fall back to a key file
    under the storage root, generated once and chmod 600 — same pattern as the
    entrypoint's .flowgate-secrets.env.
    """
    key = _load_key_material(SECRET_ENV_KEY)
    if key is not None:
        return key
    kf = _key_file_path()
    try:
        if kf.is_file():
            raw = base64.b64decode(kf.read_text(encoding="ascii").strip())
            if len(raw) == 32:
                return raw
        if create:
            raw = os.urandom(32)
            kf.write_text(base64.b64encode(raw).decode("ascii"), encoding="ascii")
            try:
                os.chmod(kf, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            return raw
    except Exception:
        pass
    raise GitServiceError(
        500, "git_encrypt_key_missing",
        f"{SECRET_ENV_KEY} is not configured and no persisted key is available.",
    )


def encrypt_secret(plain: str) -> str:
    """AES-256-GCM → base64(12-byte nonce + ciphertext + 16-byte tag)."""
    key = _get_current_key(create=True)
    nonce = os.urandom(12)
    cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return base64.b64encode(nonce + ciphertext + tag).decode("ascii")


def decrypt_secret(encrypted: str) -> str:
    """Decrypt; retries with the previous key during rotation (TOTP precedent)."""
    data = base64.b64decode(encrypted)
    nonce, tag, ciphertext = data[:12], data[-16:], data[12:-16]
    candidates: list[bytes] = []
    try:
        candidates.append(_get_current_key())
    except GitServiceError:
        pass
    try:
        prev = _load_key_material(SECRET_ENV_KEY_PREV)
        if prev is not None:
            candidates.append(prev)
    except ValueError:
        pass
    for key in candidates:
        try:
            cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception:
            continue
    raise GitServiceError(
        500, "git_secret_unreadable",
        "Stored git credential cannot be decrypted (master key changed?). "
        "Re-enter the token in the project's Git settings.",
    )


def mask_secret(plain: Optional[str]) -> Optional[str]:
    if plain is None:
        return None
    if len(plain) < MASK_MIN_LEN:
        return "********"
    return plain[:MASK_KEEP_PREFIX] + "*" * 12 + plain[-MASK_KEEP_SUFFIX:]


def _scrub(text: Optional[str], *secrets: Optional[str]) -> str:
    """Remove any secret occurrences from git output before storing/returning."""
    out = text or ""
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out


def _load_secret_for(cfg: dict) -> Optional[str]:
    enc = cfg.get("secret_enc")
    return decrypt_secret(enc) if enc else None
