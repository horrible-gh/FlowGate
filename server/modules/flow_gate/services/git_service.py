"""Git integration service (flowgate.default.0115 — D0004/P0005/L0006/DB0007).

Bridges FlowGate projects and remote Git repositories:

  - per-project config + reversibly-encrypted credentials (L0006 §2.3)
  - connection test (ls-remote, L0006 §2.5)
  - base-slot provisioning: clone into an empty slot, or LOSSLESS adopt of an
    occupied slot + last-attempt ledger + manual trigger
    (flowgate.default.0161 — D0003/P0004/L0005)
  - per-group branch/worktree provisioning (L0006 §2.1·§2.4; hooks H1/H2)
  - effective source-root resolution for workers (L0006 §2.2 — fallback first:
    a non-integrated project NEVER changes behavior)
  - finalize state machine merge/push/wait (L0006 §2.6·§3), conflict sessions
    (L0006 §2.7) and the project-level git mutex (L0006 §2.8, DB-backed)

Secret invariant (L0006 §2.3): the plaintext secret never appears in responses,
logs, git argv, or repository URLs. Git authentication is injected via a
one-shot ASKPASS helper whose values travel in child-process env vars; stderr
is scrubbed before storage/return.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Sequence

from Crypto.Cipher import AES as _AES

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.storage.paths import get_storage_root, src_root

_log = logging.getLogger(__name__)

# ── Parameters (L0006 §1) ─────────────────────────────────────────────────────

GIT_TEST_TIMEOUT_SEC = 15
GIT_NET_TIMEOUT_SEC = 120
GIT_LOCAL_TIMEOUT_SEC = 30
# 0287 NR0004 §3: `worktree remove` recursively deletes a FULL source checkout
# (measured: 864 files / 112 MB) and the storage root is routinely an SMB share,
# where every unlink is a round trip. Under the 30 s local budget the subprocess
# was killed MID-DELETE, leaving a half-erased tree whose `.git` file was already
# gone — the state that then failed every retry forever. Deletion gets its own,
# far larger budget; it is a local filesystem walk, not a network call.
GIT_WORKTREE_RM_TIMEOUT_SEC = 300
# Group branch file explorer — checkout-free ref/tree/blob reads (0186 L0006 §1).
GIT_READ_TIMEOUT_SEC = 15          # local ls-tree / cat-file timeout (no network)
BLOB_MAX_RETURN_BYTES = 1048576    # 1 MiB blob content cap; over → truncated=true
BLOB_BINARY_SNIFF_BYTES = 8000     # NUL-scan window for binary detection (git heuristic)
LOCK_WAIT_SEC = 5
# ── Git tangle prevention (flowgate.default.0205 — D0002/P0003/L0004/DB0005) ──
# A conflict wait no longer holds the project lock; abandoned sessions are
# reclaimed by a sweep so one stalled merge can never silently disable every
# later group's git management (0203 root cause).
MERGE_SESSION_TTL_HOURS = 24   # L0004 §1 — quiet-for-this-long conflict → auto-abort
SWEEP_INTERVAL_MIN = 30        # L0004 §1 — auto-recovery sweep period
BRANCH_MAX_LEN = 100
MASK_KEEP_PREFIX = 4
MASK_KEEP_SUFFIX = 4
MASK_MIN_LEN = 9
SECRET_ENV_KEY = "FLOWGATE_GIT_ENCRYPT_KEY"
SECRET_ENV_KEY_PREV = "FLOWGATE_GIT_ENCRYPT_KEY_PREV"
AUTO_COMMIT_MSG = "chore: finalize workflow changes"
AUTO_COMMIT_DESIGN_TYPES = ("D", "DB", "P", "L")
# ── Commit message pipeline (flowgate.default.0173 — D0002/P0003/L0004) ────────
# Finalize-generated commit subjects are resolved through a fallback chain:
# approved-TR draft → ASCII group title → translated title → fixed English phrase.
COMMIT_SUBJECT_MAX = 200               # normalized subject max length (L0004 §1)
TRANSLATE_TIMEOUT_SEC = 3              # translate HTTP timeout (connect+read)
TRANSLATE_SOURCE = "auto"              # auto-detect source language (CH 0168.0008)
TRANSLATE_TARGET = "en"
# TR doc_review_status set whose commit_message draft is accepted (L0004 §1).
DRAFT_ACCEPT_STATUSES = ("approved", "wf_done")
FIXED_FALLBACK_SUBJECT = "{commit_type}: finalize workflow changes"   # L0004 §1 (D0002 §3-4)
# Known machine-translation hallucinations / web boilerplate (lowercased, punctuation
# stripped, exact match) that must never become a commit subject (CH 0168.0008).
BOILERPLATE_BLACKLIST = frozenset({
    "log in", "login", "sign in", "sign up", "sign out", "skip to content",
    "home", "menu", "search", "about", "contact", "register", "submit",
    "copyright", "all rights reserved", "read more", "learn more",
})
# ── Base-checkout explicit commit / revert (flowgate.default.0177 — L0002) ────
# Default subject for an explicit base-checkout commit: "fix: a.py, b.py", or the
# abbreviated "fix: a.py 외 N건" when the joined list overflows COMMIT_SUBJECT_MAX.
BASE_COMMIT_MSG_PREFIX = "fix: "
BASE_COMMIT_MSG_JOINER = ", "
ADOPT_SNAPSHOT_MSG = "flowgate: adopt snapshot of {base_branch} ({project_id})"
# Subject for the seed commit that BORNs the base branch when a brand-new EMPTY
# remote is connected (0313 B0001): `git clone --branch <base>` cannot create it,
# so provisioning initializes the slot with this one README.md commit instead.
BOOTSTRAP_SEED_MSG = "flowgate: initialize {base_branch} ({project_id})"
# Present while an adopt is unfinished — the slot never reports "checkout"
# until the marker is removed (L0005 §2.1·§2.3, 0161).
ADOPT_PENDING_MARKER = ".git/flowgate_adopt_pending"
# Per-project last-attempt ledger in the generic system_settings KV (no DDL).
ATTEMPT_RECORD_KEY = "git.provision.last_attempt.{project_id}"
PROVIDER_VALUES = ("github", "gitlab", "gitea", "gitbucket", "generic")
ACTION_VALUES = ("merge", "merge_only", "push", "commit_push", "commit_only", "wait")
DEFAULT_FINALIZE_ACTION_VALUES = ("merge", "push", "wait")
FINALIZE_MAIN_CHOICES = ("merge", "merge_only", "wait")
FINALIZE_AUX_CHOICES = ("push",)
# NR flowgate.default.0331.0005 §8 — the approved v4 mockup drives the finalize
# UI from two INDEPENDENT axes (반영 범위 × 원격에 푸시) instead of a flat card
# list, so 6 actions fit where 4 used to. Published ADDITIVELY next to the legacy
# `choices`/`aux_choices` (which stay exactly as they were) so an older client
# keeps rendering while the axis client prefers this matrix. Display order is the
# approved one: 머지 → 커밋 → 대기.
FINALIZE_AXIS_SCOPES = ("merge", "commit", "none")
FINALIZE_AXIS_MATRIX = {
    "merge": {"push": "merge", "no_push": "merge_only"},
    "commit": {"push": "commit_push", "no_push": "commit_only"},
    "none": {"push": "push", "no_push": "wait"},
}
# Actions that produce a commit and therefore need a commit subject from the
# operator. `push` is deliberately absent: since the 0331 contract fix it only
# ships existing commits and 409s on a dirty worktree, so asking for a message
# there would promise a commit the server will not make.
FINALIZE_COMMIT_ACTIONS = ("merge", "merge_only", "commit_push", "commit_only")
SESSION_ACTION_DEFAULT = "merge"
UNMERGE_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
# flowgate.default.0162 L §1 — group git status subsets.
PENDING_STATUSES = ("awaiting_choice", "waiting", "conflict")  # "finalize pending"
SLOT_STATUSES = ("none", "awaiting_choice", "merging", "conflict", "waiting")  # not terminal
# "merging" is a transient state: recorded, but its transition is not broadcast
# (it would flicker the badge n→n-1→n before the terminal event lands, L §2.3).
TRANSIENT_STATUSES = ("merging",)
# flowgate.default.0182 NR0003 §5 — terminal statuses whose slot leftovers
# (worktree dir, local work branch, ledger registration) are cleanup targets.
CLEANUP_STATUSES = ("merged", "pushed")
# flowgate.default.0199 B0001 — RESPONSE/SSE label (not a persisted git state:
# the group_git_state.status CHECK has no such value). A wf_done group that
# produced NO work (its work branch sits at the base tip with a clean worktree —
# e.g. a pure R/CH/AC inquiry with no T) is auto-terminated: its slot is torn
# down (worktree removed, local branch force-deleted, ledger unregistered) with
# NO merge and NO push, so base never gets an empty `--no-ff` merge commit and
# origin never gets a leaked empty branch. The DB row is left status="none" +
# worktree_registered=0 (indistinguishable from an un-provisioned slot, which is
# exactly right — there is nothing to finalize); finalize/status responses report
# this label so callers can tell an auto-discard from a real merge/push.
DISCARDED_STATUS = "discarded"

# Identity for commits the SERVER makes (auto-commit / merge commits). Without
# an explicit identity `git commit` fails on hosts with no global user config.
# This is the COMMITTER (and the author fallback) — it stays "FlowGate" because the
# server really is what ran the commit.
_GIT_IDENT = ["-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost"]
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


def _author_env_for(project_id: Optional[str]) -> Optional[dict]:
    """GIT_AUTHOR_* env for a project's configured author, or None to use the default.

    Best-effort: a missing/partial config or an unreadable row simply falls back to
    the FlowGate identity — an author override must never break a commit.
    """
    if not project_id:
        return None
    try:
        cfg = db_git.get_config(project_id)
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
    """Carries (http_status, error_code, message) to the router envelope."""

    def __init__(self, status: int, code: str, message: str, details: Optional[dict] = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        # Optional structured payload surfaced verbatim in the router envelope
        # (e.g. the base_dirty file list — flowgate.default.0176 T0010 §b).
        self.details = details or {}


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


# ── Branch naming (L0006 §2.1) ────────────────────────────────────────────────

def sanitize_branch(raw: str) -> str:
    s = (raw or "").lower()
    s = re.sub(r"[^a-z0-9._-]", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-.")
    s = s[:BRANCH_MAX_LEN]
    if not s or ".." in s or "@{" in s:
        raise GitServiceError(422, "invalid_branch_name", f"cannot derive a branch name from {raw!r}")
    return s


def worktree_branch_name(project_id: str, module: str, group_id: str) -> str:
    group_no = (group_id or "").rsplit(".", 1)[-1]
    return sanitize_branch(f"{project_id}_{module}_{group_no}")


def _module_of(group_id: str) -> str:
    parts = (group_id or "").split(".", 2)
    return parts[1] if len(parts) == 3 else "default"


def _project_of_group(group_id: str) -> str:
    return (group_id or "").split(".", 1)[0]


def _one_line_subject(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# normalize_subject (L0004 §2.1): collapse newlines/tabs/runs of whitespace to a
# single space and trim — the one canonical subject cleaner for every path.
normalize_subject = _one_line_subject


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def derive_commit_type(group_id: str) -> Optional[str]:
    """Conventional-commit type for a group (L0004 §2.3), or None when undecidable.

    B-rooted → fix; R-rooted → feat when a design doc exists else chore.
    """
    try:
        docs = db_documents.get_documents_by_group_id(group_id)
    except Exception:
        return None
    root_type: Optional[str] = None
    has_design_doc = False
    for doc in docs:
        doc_type = (doc.get("type_code") or doc.get("type") or "").upper()
        if doc_type in AUTO_COMMIT_DESIGN_TYPES:
            has_design_doc = True
        if root_type is None and doc_type in ("B", "R"):
            root_type = doc_type
    if root_type == "B":
        return "fix"
    if root_type == "R":
        return "feat" if has_design_doc else "chore"
    return None


def _commit_subject(commit_type: str, summary: str) -> str:
    return f"{commit_type}: {summary}"


def build_auto_commit_message(group_id: str) -> str:
    """Generate the finalize auto-commit subject from group metadata.

    Falls back to a conventional chore subject whenever metadata is incomplete or
    cannot be read, so finalize never fails because of commit-message generation.
    """
    fallback = AUTO_COMMIT_MSG.format(group_id=group_id)
    try:
        group = db_groups.get_group(group_id)
        title = _one_line_subject(group.get("title") if group else None)
        if not title:
            return fallback
        commit_type = derive_commit_type(group_id) or "chore"
        return _commit_subject(commit_type, title)
    except Exception:
        _log.warning("auto commit message generation failed for %s", group_id, exc_info=True)
        return fallback


def _translate_guard(text: str, source_title: str) -> bool:
    """Reject empty / non-English / echoed / boilerplate translations (L0004 §2.5).

    A blacklist, not a whitelist — full hallucination detection is impossible; the
    user confirmation step is the final defense.
    """
    if not text:
        return False
    if not _is_ascii(text):
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    if text.lower() == (source_title or "").lower():
        return False
    stripped = text.strip(" .,!?:;\"'").strip()
    if stripped.lower() in BOILERPLATE_BLACKLIST:
        return False
    return True


def _try_translate(project_id: str, title: str) -> Optional[str]:
    """Translate a group title to an English subject fragment, or None on any failure.

    Never raises: translation is best-effort and must not fail finalize (L0004 §5).
    """
    try:
        cfg = db_git.get_config(project_id)
        url = ((cfg or {}).get("translate_url") or "").strip()
        if not url:
            return None                       # unset = disabled (normal path, no log)
        import requests  # lazy: keeps the module import light
        resp = requests.post(
            url.rstrip("/") + "/translate",
            json={
                "q": title, "source": TRANSLATE_SOURCE,
                "target": TRANSLATE_TARGET, "format": "text",
            },
            timeout=TRANSLATE_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            _log.warning("translate server returned %s for %s", resp.status_code, project_id)
            return None
        translated = normalize_subject(resp.json().get("translatedText"))
        if _translate_guard(translated, title):
            return translated
        _log.info("translate result rejected by guard: %r", translated)
        return None
    except Exception:
        _log.warning("translate call failed for %s", project_id, exc_info=True)
        return None


def resolve_commit_message(group_id: str) -> tuple[str, str]:
    """Resolve the suggested finalize commit subject and its source (L0004 §2.4).

    Fallback chain: approved-TR draft (tr_draft) → ASCII title (auto_title) →
    translated title (translated) → fixed English phrase (fallback). Side-effect
    free; called by both the GET state query and POST finalize. Wrapped so an
    unexpected error still yields a conventional fallback (finalize never breaks).
    """
    fallback = (AUTO_COMMIT_MSG.format(group_id=group_id), "fallback")
    try:
        # 1) latest approved-TR commit-message draft
        draft = db_documents.get_latest_tr_commit_message(group_id, DRAFT_ACCEPT_STATUSES)
        if draft:
            subject = normalize_subject(draft)
            if 0 < len(subject) <= COMMIT_SUBJECT_MAX:
                return (subject, "tr_draft")
            # abnormal stored value (empty / oversized) → silently fall through

        project_id = _project_of_group(group_id)
        group = db_groups.get_group(group_id)
        title = normalize_subject(group.get("title") if group else None)
        ctype = derive_commit_type(group_id) or "chore"

        if title:
            # 2) ASCII title → existing auto-generation rule
            if _is_ascii(title):
                subject = _commit_subject(ctype, title)
                if len(subject) <= COMMIT_SUBJECT_MAX:
                    return (subject, "auto_title")
            else:
                # 3) non-ASCII title → translate
                translated = _try_translate(project_id, title)
                if translated:
                    subject = _commit_subject(ctype, translated)
                    if len(subject) <= COMMIT_SUBJECT_MAX:
                        return (subject, "translated")

        # 4) fixed English phrase
        return (FIXED_FALLBACK_SUBJECT.format(commit_type=ctype), "fallback")
    except Exception:
        _log.warning("commit message resolution failed for %s", group_id, exc_info=True)
        return fallback


# ── Git runner ────────────────────────────────────────────────────────────────

def git_available() -> bool:
    return shutil.which("git") is not None


def _write_askpass() -> tuple[Path, Path]:
    """One-shot ASKPASS helper pair (launcher + python echo script).

    The secret itself is NEVER written to disk — the helper echoes the
    FLOWGATE_GIT_ASK_USER / FLOWGATE_GIT_ASK_PASS env vars of the git child
    process. Both files are deleted right after the git call (L0006 §2.3).
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="fg-askpass-"))
    helper = tmpdir / "askpass.py"
    helper.write_text(
        "import os, sys\n"
        "prompt = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\n"
        "key = 'FLOWGATE_GIT_ASK_USER' if 'username' in prompt else 'FLOWGATE_GIT_ASK_PASS'\n"
        "sys.stdout.write(os.environ.get(key, '') + '\\n')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmpdir / "askpass.bat"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = tmpdir / "askpass.sh"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n', encoding="utf-8"
        )
        os.chmod(launcher, 0o700)
    return launcher, tmpdir


def _run_git(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = GIT_LOCAL_TIMEOUT_SEC,
    username: Optional[str] = None,
    secret: Optional[str] = None,
    author_env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run git with prompt-free auth injection and secret-scrubbed output.

    ``author_env`` carries GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL for a project-configured
    commit author (0237); None keeps git's own default, which the `-c user.*` ident
    on commit/merge argv resolves to the FlowGate identity.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    # Never inherit an operator's ambient author from the server process env: the
    # author is either the project's configured one or the FlowGate default.
    env.pop("GIT_AUTHOR_NAME", None)
    env.pop("GIT_AUTHOR_EMAIL", None)
    if author_env:
        env.update(author_env)
    askpass_dir: Optional[Path] = None
    if secret is not None:
        launcher, askpass_dir = _write_askpass()
        env["GIT_ASKPASS"] = str(launcher)
        env["FLOWGATE_GIT_ASK_USER"] = username or ""
        env["FLOWGATE_GIT_ASK_PASS"] = secret
    try:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=-1, stdout="", stderr="timeout_expired"
            )
        except FileNotFoundError:
            raise GitServiceError(
                500, "git_unavailable",
                "git binary not found on server (install git in the runtime image)",
            )
        proc = subprocess.CompletedProcess(
            proc.args, proc.returncode,
            _scrub(proc.stdout, secret), _scrub(proc.stderr, secret),
        )
        return proc
    finally:
        if askpass_dir is not None:
            shutil.rmtree(askpass_dir, ignore_errors=True)


# ── SSE emission (P0005 §4·§5 events) ────────────────────────────────────────

def _emit(event_type: str, project: str, group_id: Optional[str], payload: dict) -> None:
    """Best-effort FlowEvent broadcast; never breaks the calling operation."""
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        broadcast_event_threadsafe(FlowEvent(
            event_type=event_type,
            payload=payload,
            audience="*",
            project=project,
            group_id=group_id,
            doc_id=None,
        ))
    except Exception:
        _log.warning("git SSE emit failed (%s)", event_type, exc_info=True)


# ── Pending-set broadcast (flowgate.default.0162 L §2.3) ─────────────────────

def _count_pending(project_id: str) -> int:
    """Project-wide "finalize pending" count, recomputed from the ledger.

    Never stored (DB0005) — a denormalized counter would drift on a missed
    emit; recompute is index-covered (idx_group_git_state_project).
    """
    rows = db_git.list_states_of_project(project_id)
    return sum(1 for r in rows if (r.get("status") in PENDING_STATUSES))


def _emit_pending_changed(project_id: str, group_id: Optional[str], new_status: Optional[str]) -> None:
    _emit("git_pending_changed", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "status": new_status,
        "pending_count": _count_pending(project_id),
    })


def _set_status(
    group_id: str,
    status: str,
    *,
    merge_id: Optional[int] = None,
    merge_commit: Optional[str] = None,
) -> None:
    """Record a group's git status AND broadcast git_pending_changed (L §2.3).

    Single convergence point so no transition can silently skip the badge
    update. The transient "merging" state is recorded but not broadcast.
    """
    db_git.set_status(group_id, status, merge_id=merge_id, merge_commit=merge_commit)
    if status not in TRANSIENT_STATUSES:
        _emit_pending_changed(_project_of_group(group_id), group_id, status)


# ── Project lock (L0006 §2.8) ────────────────────────────────────────────────

def _acquire_lock(project_id: str, holder: str, wait_sec: float = LOCK_WAIT_SEC) -> bool:
    deadline = time.monotonic() + wait_sec
    while True:
        if db_git.try_acquire_lock(project_id, holder):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


# ── Base-protection gate (flowgate.default.0205 P scenario 3 / L §2.2) ────────

def open_merge_session_of_project(project_id: str) -> Optional[dict]:
    """The one open merge session belonging to this project, or None.

    Open sessions are always few (≤ one per group by the DB invariant, and a
    project rarely has more than a couple in flight), so a full scan is fine.
    Should several somehow exist (past bug / manual edit), the newest merge_id
    wins (L §5 boundary condition)."""
    best: Optional[dict] = None
    for session in db_git.list_open_sessions():
        try:
            if _project_of_group(session["group_id"]) != project_id:
                continue
        except Exception:
            continue
        if best is None or int(session["merge_id"]) > int(best["merge_id"]):
            best = session
    return best


def guard_base_free(project_id: str) -> None:
    """Reject a base-mutating op while any unresolved merge session holds the base
    checkout (P scenario 3 / L §2.2). State-based, not lock-based: it survives
    restarts and never depends on a long-held mutex. The blocking session always
    belongs to a DIFFERENT group — a group in 'conflict' cannot itself reach a
    base-mutating entry (its own state guard rejects it first)."""
    session = open_merge_session_of_project(project_id)
    if session is None:
        return
    raise GitServiceError(
        409, "merge_conflict_open",
        f"unresolved merge of group '{session['group_id']}' holds the base checkout "
        "— resolve or abort it first",
        details={
            "blocking_group_id": session["group_id"],
            "merge_id": session.get("merge_id"),
            "conflict_since": session.get("created_at"),
        },
    )


def _base_root_of(project_id: str) -> Optional[Path]:
    """The project's base-checkout path, or None when unresolvable (0205 §2.5)."""
    cfg = db_git.get_config(project_id)
    project_name = _project_name(project_id)
    if not cfg or not project_name:
        return None
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    return src_root(project_name, base_branch)


# ── Config CRUD (P0005 §1·§2) ────────────────────────────────────────────────

_URL_HTTP_RE = re.compile(r"^https?://\S+$")
_URL_SSH_RE = re.compile(r"^(ssh://\S+|[\w.-]+@[\w.-]+:\S+)$")
# file:// mirrors are accepted for same-host repositories (and the test harness).
_URL_FILE_RE = re.compile(r"^file:///\S+$")


def _validate_repo_url(repo_url: str) -> None:
    url = (repo_url or "").strip()
    if not url or not (
        _URL_HTTP_RE.match(url) or _URL_SSH_RE.match(url) or _URL_FILE_RE.match(url)
    ):
        raise GitServiceError(
            422, "invalid_request",
            f"repo_url must be an http(s):// or ssh (git@host:path) URL: {repo_url!r}",
        )
    if "@" in url and _URL_HTTP_RE.match(url):
        # http(s) URLs must not smuggle credentials in userinfo (L0006 §2.3 invariant d).
        raise GitServiceError(
            422, "invalid_request",
            "repo_url must not embed credentials; store the token separately",
        )


def _resolve_author(
    body: dict, existing: Optional[dict]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the commit-author override to store (0237 — R0001/NR0003 §5.3).

    Same exclude_unset protocol as secret/translate_url: field omitted → keep the
    stored value; sent → trim, "" → NULL (= revert to the FlowGate default).

    Validated as a PAIR: a half-set identity would splice a configured name onto the
    default email (or vice versa), and an empty ident makes every commit for the
    project fail with "Author identity unknown" — so a bad value is rejected at the
    door (422) rather than at finalize time, where it would strand the workflow.
    """
    def _field(key: str) -> Optional[str]:
        if key in body:
            return (body.get(key) or "").strip() or None
        return (existing.get(key) or None) if existing else None

    name = _field("author_name")
    email = _field("author_email")

    if (name is None) != (email is None):
        raise GitServiceError(
            422, "invalid_request",
            "author_name and author_email must be set together (send both, or "
            "clear both with \"\" to commit as the default FlowGate identity)",
        )
    if name is None:
        return None, None
    if len(name) > GIT_AUTHOR_NAME_MAX:
        raise GitServiceError(
            422, "invalid_request",
            f"author_name must be at most {GIT_AUTHOR_NAME_MAX} characters",
        )
    if len(email) > GIT_AUTHOR_EMAIL_MAX:
        raise GitServiceError(
            422, "invalid_request",
            f"author_email must be at most {GIT_AUTHOR_EMAIL_MAX} characters",
        )
    # git strips "<", ">" and newlines out of an ident itself (so this can never be
    # an argv/config injection — NR0003 §4); reject them anyway so the operator gets
    # the identity they typed instead of a silently mangled one.
    if any(ch in name for ch in "<>\n\r"):
        raise GitServiceError(
            422, "invalid_request", "author_name must not contain '<', '>' or newlines",
        )
    if any(ch in email for ch in "<>\n\r ") or "@" not in email:
        raise GitServiceError(
            422, "invalid_request",
            f"author_email must be an email address without spaces: {email!r}",
        )
    return name, email


def _config_view(row: dict) -> dict:
    """Row → response config object (P0005 §1-1) with the secret masked."""
    has_secret = bool(row.get("secret_enc"))
    masked: Optional[str] = None
    if has_secret:
        try:
            masked = mask_secret(decrypt_secret(row["secret_enc"]))
        except GitServiceError:
            masked = "********"  # unreadable (E2) — keep has_secret=true
    return {
        "project_id": row["project_id"],
        "repo_url": row["repo_url"],
        "provider": row.get("provider") or "generic",
        "username": row.get("username"),
        "secret_masked": masked,
        "has_secret": has_secret,
        "base_branch": row.get("base_branch") or "main",
        "default_finalize_action": row.get("default_finalize_action") or "wait",
        "enabled": bool(row.get("enabled")),
        "translate_url": row.get("translate_url") or None,
        # null = not overridden → server commits as the FlowGate default (0237).
        "author_name": row.get("author_name") or None,
        "author_email": row.get("author_email") or None,
        # TR 작업범위 검증 적용 단계 (0299 D0004 §3.6). 컬럼이 NULL 인 기존 행은
        # 마이그레이션 071 이전에 만들어진 행이므로 기본값 'observe' 로 읽는다.
        "tr_scope_stage": row.get("tr_scope_stage") or "observe",
        "updated_at": row.get("updated_at"),
    }


def get_config_view(project_id: str) -> dict:
    row = db_git.get_config(project_id)
    if row is None:
        return {"ok": True, "configured": False, "config": None}
    return {"ok": True, "configured": True, "config": _config_view(row)}


def save_config(project_id: str, body: dict) -> dict:
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    _validate_repo_url(body.get("repo_url") or "")
    provider = body.get("provider") or "generic"
    if provider not in PROVIDER_VALUES:
        raise GitServiceError(422, "invalid_request", f"invalid provider: {provider!r}")
    action = body.get("default_finalize_action") or "wait"
    if action not in DEFAULT_FINALIZE_ACTION_VALUES:
        raise GitServiceError(
            422, "invalid_request", f"invalid default_finalize_action: {action!r}"
        )

    existing = db_git.get_config(project_id)
    secret = body.get("secret", None)
    if secret is None:
        secret_enc = existing.get("secret_enc") if existing else None  # keep (P0005 §2-1)
    elif secret == "":
        secret_enc = None  # clear
    else:
        secret_enc = encrypt_secret(str(secret))

    # translate_url (P0003 §4-1): field omitted → keep stored value; sent → trim,
    # empty string stored as NULL (= disabled). Same exclude_unset "keep" protocol
    # as secret above.
    if "translate_url" in body:
        translate_url = (body.get("translate_url") or "").strip() or None
    else:
        translate_url = existing.get("translate_url") if existing else None

    author_name, author_email = _resolve_author(body, existing)

    # tr_scope_stage (0299 D0004 §3.6): omitted → keep stored (or 'observe' on a new
    # row). Same exclude_unset "keep" protocol as translate_url/secret above, so an
    # older client that does not know the field cannot silently reset the stage.
    if "tr_scope_stage" in body:
        tr_scope_stage = (body.get("tr_scope_stage") or "observe").strip() or "observe"
        if tr_scope_stage not in db_git.TR_SCOPE_STAGE_VALUES:
            raise GitServiceError(
                422, "invalid_request", f"invalid tr_scope_stage: {tr_scope_stage!r}"
            )
    else:
        tr_scope_stage = (existing.get("tr_scope_stage") if existing else None) or "observe"

    row = db_git.upsert_config(project_id, {
        "repo_url": (body.get("repo_url") or "").strip(),
        "provider": provider,
        "username": (body.get("username") or None),
        "secret_enc": secret_enc,
        "base_branch": (body.get("base_branch") or "main").strip() or "main",
        "default_finalize_action": action,
        "enabled": bool(body.get("enabled")),
        "translate_url": translate_url,
        "author_name": author_name,
        "author_email": author_email,
        "tr_scope_stage": tr_scope_stage,
    })
    return {"ok": True, "configured": True, "config": _config_view(row)}


def delete_config(project_id: str) -> dict:
    deleted = db_git.delete_config(project_id)
    # Existing worktrees are intentionally left untouched (P0005 §2-3);
    # source resolution falls back immediately via the enabled/config check (E13).
    return {"ok": True, "deleted": deleted}


# ── Connection test (P0005 §3 / L0006 §2.5) ──────────────────────────────────

_AUTH_FAIL_PATTERNS = (
    "authentication failed", "invalid username", "401", "403",
    "could not read username", "permission denied (publickey",
)
_UNREACHABLE_PATTERNS = (
    "could not resolve host", "connection refused", "connection timed out",
    "unable to access", "timeout_expired", "network is unreachable",
)


def test_connection(project_id: str, override: Optional[dict] = None) -> dict:
    override = override or {}
    stored = db_git.get_config(project_id)
    cfg = dict(stored) if stored else {}
    for k in ("repo_url", "username", "base_branch", "provider"):
        if override.get(k) is not None:
            cfg[k] = override[k]
    if not (cfg.get("repo_url") or "").strip():
        raise GitServiceError(
            409, "not_configured",
            f"Git integration is not configured for project '{project_id}'",
        )
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    if override.get("secret") is not None and override.get("secret") != "":
        secret: Optional[str] = str(override["secret"])
    elif stored and stored.get("secret_enc"):
        secret = decrypt_secret(stored["secret_enc"])
    else:
        secret = None

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    repo_url = (cfg.get("repo_url") or "").strip()
    t0 = time.monotonic()
    proc = _run_git(
        ["ls-remote", "--symref", repo_url, "HEAD", f"refs/heads/{base_branch}"],
        timeout=GIT_TEST_TIMEOUT_SEC,
        username=cfg.get("username"),
        secret=secret if secret is not None else "",
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if proc.returncode == 0:
        default_branch = None
        base_exists = False
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("ref:") and line.endswith("HEAD"):
                m = re.match(r"ref:\s+refs/heads/(\S+)\s+HEAD", line)
                if m:
                    default_branch = m.group(1)
            if line.endswith(f"refs/heads/{base_branch}"):
                base_exists = True
        return {
            "reachable": True,
            "authenticated": True,
            "remote_default_branch": default_branch,
            "base_branch_exists": base_exists,
            "elapsed_ms": elapsed_ms,
        }

    err = (proc.stderr or "").strip()
    low = err.lower()
    if any(p in low for p in _AUTH_FAIL_PATTERNS):
        code, reachable, authenticated = "auth_failed", True, False
    elif any(p in low for p in _UNREACHABLE_PATTERNS):
        code, reachable, authenticated = "unreachable", False, None
    else:
        code, reachable, authenticated = "git_error", True, None
    last_line = err.splitlines()[-1] if err else "git command failed"
    return {
        "reachable": reachable,
        "authenticated": authenticated,
        "remote_default_branch": None,
        "base_branch_exists": None,
        "elapsed_ms": elapsed_ms,
        "failure": {"code": code, "message": last_line},
    }


# ── Worktree provisioning (L0006 §2.4 — hooks H1/H2) ─────────────────────────

def _project_name(project_id: str) -> Optional[str]:
    row = db_projects.get_by_id(project_id)
    name = (row.get("project_name") or "").strip() if row else ""
    return name or None


def _load_secret_for(cfg: dict) -> Optional[str]:
    enc = cfg.get("secret_enc")
    return decrypt_secret(enc) if enc else None


def _ref_exists(repo: Path, ref: str) -> bool:
    proc = _run_git(["show-ref", "--verify", "--quiet", ref], cwd=repo)
    return proc.returncode == 0


# ── Worktree liveness: is that directory a REAL worktree? (0287 NR0004) ──────
# Every gate in this module used to equate "the directory exists" with "a healthy
# registered worktree exists". A `worktree remove` interrupted mid-delete breaks
# that equivalence: the directory survives while its `.git` link and most of its
# content are already gone. Two corpse shapes were observed in the field —
#   B) admin dir still in .git/worktrees, worktree `.git` file gone → `prunable`
#   C) admin dir pruned away too → git no longer knows the path at all
# — and BOTH pass `is_dir()`. These helpers tell the three states apart.


def _worktree_link_ok(wt_path: Path) -> bool:
    """Whether *wt_path* still carries its worktree `.git` link.

    Cheap local check (one stat) and the discriminator that matters to readers:
    without this link the directory is a half-deleted corpse, not a source tree.
    A normal worktree has `.git` as a FILE ('gitdir: …'); the base checkout has it
    as a directory. Both count as linked — callers may hand either one in."""
    try:
        return (wt_path / ".git").exists()
    except OSError:
        return False


def _registered_worktrees(base_root: Path) -> Optional[set[Path]]:
    """Resolved paths git currently accepts as live worktrees, or None if unknown.

    Parses `git worktree list --porcelain`. An entry flagged `prunable` is git's
    own statement that the registration is stale, so it is EXCLUDED — for cleanup
    purposes a prunable entry is an orphan, not a worktree.

    Paths are compared resolved, never as strings: `git worktree list` reports the
    real path (e.g. a UNC share `//host/share/…`) while `src_root()` builds the
    junction/mapped form (`C:\\…\\storage\\…`), so the two spellings of one
    directory never match textually (0287 NR0004 §7-1).

    Returns None — meaning "cannot tell" — when git fails or times out, so callers
    can stay conservative instead of mistaking silence for "not registered"."""
    proc = _run_git(["worktree", "list", "--porcelain"], cwd=base_root)
    if proc.returncode != 0:
        _log.warning(
            "worktree list failed in %s: %s", base_root, _last_line(proc.stderr)
        )
        return None
    live: set[Path] = set()
    current: Optional[Path] = None
    prunable = False

    def _flush() -> None:
        if current is not None and not prunable:
            live.add(current)

    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            _flush()
            prunable = False
            raw = line[len("worktree "):].strip()
            try:
                current = Path(raw).resolve()
            except OSError:
                current = None
        elif line.startswith("prunable"):
            prunable = True
    _flush()
    return live


def _classify_worktree_dir(base_root: Path, wt_path: Path) -> str:
    """'live' | 'orphan' | 'unknown' for an EXISTING directory (0287 NR0004 §7-1).

    'live'    — git lists it as a non-prunable worktree AND its `.git` link is intact
    'orphan'  — the directory is there but git does not (or no longer can) own it:
                unregistered, prunable, or link destroyed by an interrupted delete
    'unknown' — git could not answer; the caller must not assume either way
    """
    if not _worktree_link_ok(wt_path):
        # Decisive on its own: `worktree remove` refuses such a path outright
        # ("validation failed, cannot remove working tree: '…/.git' does not exist").
        return "orphan"
    live = _registered_worktrees(base_root)
    if live is None:
        return "unknown"
    try:
        resolved = wt_path.resolve()
    except OSError:
        return "orphan"
    return "live" if resolved in live else "orphan"


def _force_rmtree(path: Path) -> bool:
    """Delete a directory tree that git could not, best-effort. True when gone.

    Used for orphan slots only (a path git refuses to own). Read-only files are a
    normal Windows leftover — clear the attribute and retry rather than aborting
    the whole sweep on one file, which is exactly how the corpse trees were born."""

    def _retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            _log.debug("rmtree could not remove %s", target, exc_info=True)

    try:
        # `onerror` is deprecated since 3.12 in favour of `onexc`; the runtime is
        # already on 3.14, so prefer the supported hook and keep the old one as a
        # fallback rather than letting a removed kwarg fail the whole teardown.
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_retry)
        else:
            shutil.rmtree(path, onerror=_retry)
    except Exception:
        _log.warning("rmtree failed for %s", path, exc_info=True)
    return not path.exists()


# ── Base-slot provisioning: lossless adopt + attempt ledger (0161 L0005) ─────

def _judge_base_slot(base_root: Path, base_branch: str) -> str:
    """'empty' | 'occupied' | 'checkout' — L0005 §2.1.

    Completion criterion: refs/heads/{base_branch} exists AND no pending-adopt
    marker. Partial debris (.git without the branch, or a leftover marker)
    reports 'occupied' so a re-run resumes the adopt sequence.
    """
    try:
        if not base_root.exists() or not any(base_root.iterdir()):
            return "empty"
    except OSError:
        return "empty"
    if (base_root / ADOPT_PENDING_MARKER).exists():
        return "occupied"
    if (base_root / ".git").exists():
        if not git_available():
            return "checkout"  # informational approximation; execution paths fail precisely
        proc = _run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{base_branch}"],
            cwd=base_root,
        )
        if proc.returncode == 0:
            return "checkout"
    return "occupied"


def _record_attempt(
    project_id: str,
    result: str,
    reason: Optional[str],
    trigger: str,
    mode: str,
    *,
    snapshot_commit: Optional[str] = None,
    snapshot_at: Optional[str] = None,
) -> None:
    """Best-effort per-project last-attempt ledger (L0005 §2.5 — KV, no DDL).

    Reasons derived from git stderr arrive here already _scrub-masked (the
    runner scrubs before returning) — the plaintext secret never lands in the DB.
    """
    record = {
        "result": result,
        "reason": reason,
        "trigger": trigger,
        "at": now_iso(),
        "mode": mode,
        "snapshot_commit": snapshot_commit,
        "snapshot_at": snapshot_at,
    }
    try:
        db_settings.set_value(
            ATTEMPT_RECORD_KEY.format(project_id=project_id),
            json.dumps(record, ensure_ascii=False),
            value_type="json",
            description="git provision last attempt",
        )
    except Exception:
        _log.warning("git provision attempt record failed for %s", project_id, exc_info=True)


def _load_attempt_record(project_id: str) -> Optional[dict]:
    try:
        row = db_settings.get(ATTEMPT_RECORD_KEY.format(project_id=project_id))
        if row is None or not row.get("setting_value"):
            return None
        record = json.loads(row["setting_value"])
        return record if isinstance(record, dict) else None
    except Exception:
        # Broken JSON behaves like "no record" (DB0006 §5); next attempt overwrites.
        _log.warning("git provision attempt record unreadable for %s", project_id, exc_info=True)
        return None


def _provision_failed(proc: subprocess.CompletedProcess) -> dict:
    return {
        "status": "failed", "reason": _last_line(proc.stderr),
        "snapshot_commit": None, "snapshot_at": None,
    }


def _adopt(
    base_root: Path,
    base_branch: str,
    repo_url: str,
    username: Optional[str],
    secret: str,
    project_id: str,
) -> dict:
    """Turn an occupied slot into a repository WITHOUT touching any existing
    file (L0005 §2.3). Every step is check-then-act, so a run interrupted at
    any point (auth failure, timeout) resumes to completion on the next call.
    Forced-checkout class commands (checkout -f / reset --hard / clean / stash)
    are banned on this path by design (DS0002).
    """
    # 1. repository skeleton — working files untouched
    if not (base_root / ".git").exists():
        proc = _run_git(["init"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)
    marker = base_root / ADOPT_PENDING_MARKER
    try:
        marker.touch()
    except OSError as exc:
        return {"status": "failed", "reason": f"adopt marker unwritable: {exc}",
                "snapshot_commit": None, "snapshot_at": None}

    # 2. remote wiring (re-entry: sync the URL only)
    proc = _run_git(["remote", "get-url", "origin"], cwd=base_root)
    if proc.returncode != 0:
        proc = _run_git(["remote", "add", "origin", repo_url], cwd=base_root)
    elif (proc.stdout or "").strip() != repo_url:
        proc = _run_git(["remote", "set-url", "origin", repo_url], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)

    # 3. fetch — the most likely failure point; debris stays for re-entry
    proc = _run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        return _provision_failed(proc)

    # 4. establish the base branch without a checkout (working tree untouched)
    proc = _run_git(["symbolic-ref", "HEAD", f"refs/heads/{base_branch}"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    if _ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        # --mixed moves the branch ref and index only; files stay byte-identical
        proc = _run_git(
            ["reset", "--mixed", f"refs/remotes/origin/{base_branch}"], cwd=base_root
        )
        if proc.returncode != 0:
            return _provision_failed(proc)
    # else: remote has no base branch (empty repository) — the branch stays
    # unborn and the snapshot below becomes its first commit.

    # 5. absorb the local↔remote difference
    return _absorb_snapshot(base_root, base_branch, project_id)


def _absorb_snapshot(base_root: Path, base_branch: str, project_id: str) -> dict:
    """Commit the local↔remote difference on the base branch (L0005 §2.4).

    After the mixed reset the working tree is the local original and the index
    is the remote tree. Remote-only files show as worktree deletions and MUST
    be restored first — otherwise the snapshot would record them as deletions
    and a later finalize push would erase them remotely.
    """
    proc = _run_git(["status", "--porcelain", "-z"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    for entry in (proc.stdout or "").split("\0"):
        if len(entry) >= 4 and entry[1] == "D" and entry[2] == " ":
            restore = _run_git(["checkout", "--", entry[3:]], cwd=base_root)
            if restore.returncode != 0:
                # e.g. path-type conflict — stop with all data intact (no auto-fix)
                return _provision_failed(restore)

    snapshot_commit: Optional[str] = None
    snapshot_at: Optional[str] = None
    proc = _run_git(["status", "--porcelain"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    if (proc.stdout or "").strip():
        proc = _run_git(["add", "-A"], cwd=base_root)  # .gitignore is honored
        if proc.returncode != 0:
            return _provision_failed(proc)
        msg = ADOPT_SNAPSHOT_MSG.format(base_branch=base_branch, project_id=project_id)
        proc = _run_git(
            [*_GIT_IDENT, "commit", "-m", msg], cwd=base_root,
            author_env=_author_env_for(project_id),
        )
        if proc.returncode != 0:
            return _provision_failed(proc)
        head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
        snapshot_commit = (head.stdout or "").strip() or None
        snapshot_at = now_iso()

    # completion — removing the marker must be the LAST step
    try:
        (base_root / ADOPT_PENDING_MARKER).unlink(missing_ok=True)
    except OSError as exc:
        return {"status": "failed", "reason": f"adopt marker not removable: {exc}",
                "snapshot_commit": snapshot_commit, "snapshot_at": snapshot_at}
    return {"status": "ok", "reason": None,
            "snapshot_commit": snapshot_commit, "snapshot_at": snapshot_at}


def _remote_is_empty(repo_url: str, username: Optional[str], secret: str) -> bool:
    """True only when the remote is reachable AND advertises no refs at all — a
    brand-new, never-pushed repository (0313 B0001).

    Deliberately narrow: any error, timeout, or non-empty ref advertisement reads
    False, so a genuine fetch/auth failure still flows through the normal clone
    path and surfaces its true reason instead of being masked as "empty".
    """
    proc = _run_git(
        ["ls-remote", repo_url],
        timeout=GIT_TEST_TIMEOUT_SEC,
        username=username,
        secret=secret if secret is not None else "",
    )
    return proc.returncode == 0 and not (proc.stdout or "").strip()


def _remote_lacks_base_branch(
    repo_url: str, username: Optional[str], secret: str, base_branch: str
) -> bool:
    """True when the remote is reachable AND advertises no ``refs/heads/<base>`` —
    a superset of `_remote_is_empty` (0318 B0001).

    A fully bare remote is only one way `git clone --branch <base>` can fatal with
    "Remote branch <base> not found in upstream origin". The other — common — way is
    a remote that DOES have refs but not the configured base branch: a default-branch
    name mismatch (remote `master` vs base `main`, or the reverse), or a brand-new
    repository initialized on some other branch. Both leave the base checkout
    uncreated, so both must route to `_bootstrap_empty_remote` instead of a clone
    that can never succeed.

    Deliberately narrow like `_remote_is_empty`: any error or timeout reads False so
    a genuine fetch/auth failure still flows through the normal clone path and
    surfaces its true reason instead of being masked as "needs bootstrap". The
    fully-qualified `refs/heads/<base>` pattern matches the base head exactly, so an
    unrelated branch whose tail happens to be <base> (e.g. `dev/main`) is not a
    false positive.
    """
    proc = _run_git(
        ["ls-remote", repo_url, f"refs/heads/{base_branch}"],
        timeout=GIT_TEST_TIMEOUT_SEC,
        username=username,
        secret=secret if secret is not None else "",
    )
    return proc.returncode == 0 and not (proc.stdout or "").strip()


def _bootstrap_empty_remote(
    base_root: Path,
    base_branch: str,
    repo_url: str,
    username: Optional[str],
    secret: str,
    project_id: str,
) -> dict:
    """Establish the base checkout for a brand-new EMPTY remote (0313 B0001).

    `git clone --branch <base>` cannot succeed against a repository with no commits
    and no <base> branch, so a freshly-connected empty remote used to fail
    provisioning outright — no base checkout, hence no worktrees, no base-commit,
    no first-push affordance (the whole "can't do anything" report). Here we init
    the slot, wire origin, and seed a single README.md commit so the base branch is
    BORN: worktrees get a commit to fork from and status gets a commit to offer as
    the first push. Nothing is pushed — the seed rides the next finalize's base
    push, exactly like adopt's snapshot. The `secret` is unused (every step is
    local); it is accepted only to mirror the adopt/clone signatures.
    """
    try:
        base_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed", "reason": f"base dir uncreatable: {exc}",
                "snapshot_commit": None, "snapshot_at": None}

    proc = _run_git(["init", "-b", base_branch, str(base_root)])
    if proc.returncode != 0:
        # git < 2.28 has no `init -b`: init, then point the unborn HEAD at the base.
        proc = _run_git(["init"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)
        proc = _run_git(["symbolic-ref", "HEAD", f"refs/heads/{base_branch}"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)

    proc = _run_git(["remote", "add", "origin", repo_url], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)

    readme = base_root / "README.md"
    if not readme.exists():
        try:
            readme.write_text(f"# {project_id}\n", encoding="utf-8")
        except OSError as exc:
            return {"status": "failed", "reason": f"seed file unwritable: {exc}",
                    "snapshot_commit": None, "snapshot_at": None}
    proc = _run_git(["add", "--", "README.md"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    msg = BOOTSTRAP_SEED_MSG.format(base_branch=base_branch, project_id=project_id)
    proc = _run_git(
        [*_GIT_IDENT, "commit", "-m", msg], cwd=base_root,
        author_env=_author_env_for(project_id),
    )
    if proc.returncode != 0:
        return _provision_failed(proc)
    head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
    return {"status": "ok", "reason": None,
            "snapshot_commit": (head.stdout or "").strip() or None,
            "snapshot_at": now_iso()}


def _provision_base_locked(cfg: dict, project_id: str, project_name: str, trigger: str) -> dict:
    """Judge the base slot and establish it (none / clone / adopt) — L0005 §2.2.

    The caller must hold the project git mutex (hook path already does; the
    manual path acquires it in provision_base).
    """
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    state = _judge_base_slot(base_root, base_branch)
    if state == "checkout":
        # idempotent pass-through — the ledger is NOT updated (P0004 scenario 4)
        return {"status": "ok", "mode": "none", "reason": None,
                "snapshot_commit": None, "snapshot_at": None}

    mode = "clone" if state == "empty" else "adopt"
    try:
        secret = _load_secret_for(cfg) or ""
    except GitServiceError as exc:
        result = {"status": "failed", "mode": mode, "reason": exc.code,
                  "snapshot_commit": None, "snapshot_at": None}
        _record_attempt(project_id, "failed", exc.code, trigger, mode)
        return result
    username = cfg.get("username")
    repo_url = (cfg.get("repo_url") or "").strip()

    if state == "empty":
        base_root.parent.mkdir(parents=True, exist_ok=True)
        if _remote_lacks_base_branch(repo_url, username, secret, base_branch):
            # 0313/0318 B0001: a remote WITHOUT the base branch cannot be cloned with
            # `--branch <base>` — the clone dies with "Remote branch <base> not found
            # in upstream origin", leaving the base checkout uncreated and every
            # downstream op (worktree/base-commit/first push) blocked. This covers a
            # fully bare remote (0313) AND one that advertises other refs but no
            # <base> — e.g. a default-branch name mismatch or a repo initialized on
            # another branch (0318). Initialize the slot with a seed commit so the
            # base branch is born, instead of a clone that can never succeed.
            result = _bootstrap_empty_remote(
                base_root, base_branch, repo_url, username, secret, project_id
            )
        else:
            proc = _run_git(
                ["clone", "--branch", base_branch, repo_url, str(base_root)],
                timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if proc.returncode == 0:
                result = {"status": "ok", "reason": None,
                          "snapshot_commit": None, "snapshot_at": None}
            else:
                result = _provision_failed(proc)
    else:  # occupied — pre-existing files or partial debris: lossless adopt
        result = _adopt(base_root, base_branch, repo_url, username, secret, project_id)

    result["mode"] = mode
    _record_attempt(
        project_id, result["status"], result["reason"], trigger, mode,
        snapshot_commit=result["snapshot_commit"], snapshot_at=result["snapshot_at"],
    )
    return result


def provision_base(project_id: str, trigger: str) -> dict:
    """Single provisioning entry shared by hooks and the manual API (L0005 §2.2).

    Returns {status, mode, reason, snapshot_commit, snapshot_at}. Provisioning
    failures are reported results, not exceptions (never-raises contract).
    """
    def _blocked(reason: str) -> dict:
        _record_attempt(project_id, "failed", reason, trigger, "none")
        return {"status": "failed", "mode": "none", "reason": reason,
                "snapshot_commit": None, "snapshot_at": None}

    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        # not recorded; the manual route pre-blocks with 409 not_enabled
        return {"status": "skipped", "mode": "none", "reason": None,
                "snapshot_commit": None, "snapshot_at": None}
    project_name = _project_name(project_id)
    if not project_name:
        return _blocked("project_name missing")
    if not git_available():
        return _blocked("git_unavailable")

    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        return _blocked("git_busy")
    try:
        return _provision_base_locked(cfg, project_id, project_name, trigger)
    finally:
        db_git.release_lock(project_id, holder)


def provision_view(project_id: str) -> dict:
    """Status object for GET …/git/provision (P0004) — read-only, no network git."""
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"configured": False, "enabled": False, "base_branch": None,
                "base_path_state": "empty", "base_checkout_exists": False,
                "adopt_snapshot": None, "last_attempt": None}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _project_name(project_id)
    base_root = src_root(project_name, base_branch) if project_name else None
    state = _judge_base_slot(base_root, base_branch) if base_root else "occupied"

    record = _load_attempt_record(project_id)
    snapshot = None
    if record and record.get("snapshot_commit"):
        snapshot = {"commit": record["snapshot_commit"],
                    "committed_at": record.get("snapshot_at")}
        if base_root and (base_root / ".git").exists() and git_available():
            proc = _run_git(
                ["merge-base", "--is-ancestor", record["snapshot_commit"],
                 f"refs/remotes/origin/{base_branch}"],
                cwd=base_root,
            )
            if proc.returncode == 0:
                snapshot = None  # already reached the remote — hide it
            # exit 1 (not yet pushed) or indeterminate: keep the recorded value

    last_attempt = None
    if record is not None:
        last_attempt = {"result": record.get("result"), "reason": record.get("reason"),
                        "trigger": record.get("trigger"), "at": record.get("at")}
    return {"configured": True, "enabled": True, "base_branch": base_branch,
            "base_path_state": state, "base_checkout_exists": state == "checkout",
            "adopt_snapshot": snapshot, "last_attempt": last_attempt}


def provision_manual(project_id: str) -> dict:
    """POST …/git/provision — synchronous manual run (P0004 scenarios 2~8)."""
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(
            409, "not_enabled",
            f"git integration is not enabled for project '{project_id}'",
        )
    result = provision_base(project_id, "manual")
    return {"ok": True, "result": {
        "status": result["status"],
        "mode": result["mode"],
        "reason": result["reason"],
        "provision": provision_view(project_id),
    }}


def ensure_worktree(
    project_id: str, module: str, group_id: str, trigger: str = "remote_access"
) -> str:
    """Create/guarantee the group's branch + worktree. Idempotent; never raises.

    Returns 'skipped' | 'ok' | 'failed'. A failure only emits git_worktree_failed —
    the workflow itself proceeds on the fallback source path (P0005 §4-2).
    """
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return "skipped"  # non-integrated project: strictly no-op
        project_name = _project_name(project_id)
        if not project_name:
            _record_attempt(project_id, "failed", "project_name missing", trigger, "none")
            _fail_worktree(project_id, group_id, None, "project_name missing")
            return "failed"
        try:
            branch = worktree_branch_name(project_id, module or _module_of(group_id), group_id)
        except GitServiceError as exc:
            _fail_worktree(project_id, group_id, None, exc.code)  # E9
            return "failed"
        if not git_available():
            _record_attempt(project_id, "failed", "git_unavailable", trigger, "none")
            _fail_worktree(project_id, group_id, branch, "git_unavailable")  # E1
            return "failed"

        holder = f"op:{uuid.uuid4()}"
        if not _acquire_lock(project_id, holder):
            _record_attempt(project_id, "failed", "git_busy", trigger, "none")
            _fail_worktree(project_id, group_id, branch, "git_busy")  # E11
            return "failed"
        try:
            return _ensure_worktree_locked(cfg, project_id, project_name, group_id, branch, trigger)
        finally:
            db_git.release_lock(project_id, holder)
    except Exception as exc:  # noqa: BLE001 — the hook must never break its caller
        _log.warning("ensure_worktree failed for %s", group_id, exc_info=True)
        try:
            _fail_worktree(project_id, group_id, None, _scrub(str(exc)))
        except Exception:
            pass
        return "failed"


def _ensure_worktree_locked(
    cfg: dict, project_id: str, project_name: str, group_id: str, branch: str,
    trigger: str = "remote_access",
) -> str:
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    wt_path = src_root(project_name, branch)
    username = cfg.get("username")
    secret = _load_secret_for(cfg) or ""

    # Base checkout: clone (empty slot) or lossless adopt (occupied slot) —
    # 0161 replaces the old clone-only path that died with E7 base_path_occupied.
    provision = _provision_base_locked(cfg, project_id, project_name, trigger)
    if provision["status"] == "failed":
        _fail_worktree(project_id, group_id, branch, provision["reason"] or "git_error")
        return "failed"

    # Idempotence: ledger says the worktree exists and the directory is present.
    state = db_git.get_state(group_id)
    if (
        state is not None
        and state.get("worktree_registered")
        and state.get("branch") == branch
        and wt_path.is_dir()
        # 0287 NR0004 §5.1: `is_dir()` alone declared a half-deleted corpse "ready"
        # and returned ok — no re-provisioning, and a git_worktree_ready event for
        # a tree that no longer holds the source.
        and _worktree_link_ok(wt_path)
    ):
        db_git.clear_provision_failure(group_id)   # a stale marker must not linger (L §2.4)
        _emit_worktree_ready(
            project_id, group_id, branch, base_branch, wt_path,
            created=False, base_root=base_root,
        )
        return "ok"

    if wt_path.exists():
        # Unregistered directory squatting on the slot (E7): never delete automatically.
        _fail_worktree(project_id, group_id, branch, "worktree_path_occupied")
        return "failed"

    proc = _run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        _fail_worktree(project_id, group_id, branch, proc.stderr.strip())
        return "failed"

    if _ref_exists(base_root, f"refs/heads/{branch}"):
        proc = _run_git(["worktree", "add", str(wt_path), branch], cwd=base_root)
    elif _ref_exists(base_root, f"refs/remotes/origin/{branch}"):
        # Reconnect to the group's existing remote branch (restart survival).
        proc = _run_git(
            ["worktree", "add", "--track", "-b", branch, str(wt_path), f"origin/{branch}"],
            cwd=base_root,
        )
    else:
        proc = _run_git(
            ["worktree", "add", "-b", branch, str(wt_path),
             _worktree_start_point(base_root, base_branch)],
            cwd=base_root,
        )
    if proc.returncode != 0:
        _fail_worktree(project_id, group_id, branch, proc.stderr.strip())
        return "failed"

    db_git.register_worktree(group_id, project_id, branch)
    db_git.clear_provision_failure(group_id)   # success clears the failure marker (L §2.4)
    _emit_worktree_ready(
        project_id, group_id, branch, base_branch, wt_path,
        created=True, base_root=base_root,
    )
    return "ok"


def _worktree_start_point(base_root: Path, base_branch: str) -> str:
    """Where a brand-new group branch forks from.

    Historically always `origin/<base>` when that ref existed, so a group always
    started from the newest published state. But `base_commit` deliberately does
    NOT push (its commit rides along on the next finalize's base push), so a
    locally committed file stayed absent from every worktree created afterwards —
    i.e. "commit it and the agent can see it" was still false even *after* 0296
    T0004 gave the operator a way to commit untracked files. The whole fix would
    have stopped one step short.

    So: prefer the LOCAL base branch whenever it already contains everything
    origin has (fast-forward-ahead or equal) — it is then strictly the newer of
    the two and loses nothing. Only when origin is ahead or the two have
    diverged does `origin/<base>` win, preserving the original intent; a genuine
    divergence is the E4 `base_diverged` condition and stays finalize's problem,
    not this function's.
    """
    remote = f"origin/{base_branch}"
    if not _ref_exists(base_root, f"refs/remotes/{remote}"):
        return base_branch
    if not _ref_exists(base_root, f"refs/heads/{base_branch}"):
        return remote
    contains = _run_git(["merge-base", "--is-ancestor", remote, base_branch], cwd=base_root)
    return base_branch if contains.returncode == 0 else remote


def _emit_worktree_ready(
    project_id: str, group_id: str, branch: str, base_branch: str, wt_path: Path, *,
    created: bool, base_root: Optional[Path] = None,
) -> None:
    try:
        rel = wt_path.relative_to(get_storage_root()).as_posix()
    except Exception:
        rel = str(wt_path)
    payload = {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "worktree_path": rel,
        "created": created,
    }
    # 0296 T0004 (NR0003 R3): `worktree add` checks out a COMMIT, so whatever is
    # sitting uncommitted in the base checkout does not exist in the tree the
    # workers read (NR §C1). That isolation is correct and stays — but the
    # operator learns about it, today, only by watching an agent claim a file is
    # missing. Ship the count at the moment the worktree appears so the UI can
    # warn up front. Advisory only: never let it fail the provisioning.
    try:
        if base_root is not None:
            untracked = _untracked_files(base_root)
            if untracked:
                payload["base_untracked_count"] = len(untracked)
                payload["base_untracked"] = untracked[:20]
    except Exception:
        _log.warning("worktree-ready untracked probe failed for %s", group_id, exc_info=True)
    _emit("git_worktree_ready", project_id, group_id, payload)


def _emit_worktree_failed(
    project_id: str, group_id: str, branch: Optional[str], error: str
) -> None:
    _emit("git_worktree_failed", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "error": error,
    })


def _fail_worktree(
    project_id: str, group_id: str, branch: Optional[str], error: str
) -> None:
    """Persist the provisioning failure (0205 L §2.4) then emit the live SSE.

    The persistent record lets the status query resurface the failure long after
    the one-shot SSE is gone (P scenario 4) — a worker without a slot no longer
    fails silently. Persistence is best-effort so a bookkeeping error never
    swallows the operator-facing SSE."""
    try:
        db_git.upsert_provision_failure(group_id, project_id, branch or "", error)
    except Exception:
        _log.warning("record provision failure failed for %s", group_id, exc_info=True)
    _emit_worktree_failed(project_id, group_id, branch, error)


def ensure_worktree_async(project_id: str, module: str, group_id: str) -> None:
    """H1 wrapper: run provisioning off the request thread (clone can be slow).

    The decide response must not wait on network git; H2 (worker-token grant
    creation) re-guarantees the worktree before any source access anyway.
    """
    import threading

    threading.Thread(
        target=ensure_worktree,
        args=(project_id, module, group_id, "workflow_decide"),
        daemon=True,
    ).start()


# ── Effective source-root resolution (L0006 §2.2·§4.1) ───────────────────────

# 0280 NR0003 §4-B: every reason the worktree is NOT used. The fallback itself is
# intended design; what was missing is any record of WHICH condition fired, so a
# "tests ran in main" report could never be confirmed or refuted after the fact.
# These constants are persisted (test_runs.source_root_kind) and rendered in TSR.
SRC_ROOT_WORKTREE = "worktree"
SRC_ROOT_NO_GROUP = "no_group_context"
SRC_ROOT_INTEGRATION_OFF = "git_integration_off"
SRC_ROOT_NO_STATE = "no_group_git_state"
SRC_ROOT_UNREGISTERED = "worktree_unregistered"
SRC_ROOT_NO_BRANCH = "state_branch_empty"
SRC_ROOT_NO_PROJECT_NAME = "project_name_missing"
SRC_ROOT_DIR_MISSING = "worktree_dir_missing"
# 0287 NR0004 §5: the directory is there but it is a corpse — an interrupted
# `worktree remove` took its `.git` link and most of its content with it. Distinct
# from *_dir_missing because the failure looks nothing alike in a TSR: the suite
# runs, finds a tree with its test files but not its modules, and reports import
# errors that read like product bugs.
SRC_ROOT_DIR_BROKEN = "worktree_dir_broken"
SRC_ROOT_ERROR = "resolution_error"


def effective_src_root_ex(
    project_id: Optional[str], group_id: Optional[str]
) -> tuple[Optional[Path], str]:
    """``effective_src_root`` plus the reason, and a log line on every fallback.

    Returns ``(worktree_path, "worktree")`` or ``(None, <SRC_ROOT_* reason>)``.
    0280 NR0003 §6-3: each fallback below used to be a bare ``return None`` with
    no log, no DB column and no UI trace, so a group that silently dropped to the
    base tree left zero evidence. Two of them are routine (integration off / no
    group context) and log at debug; the rest mean a worktree was *expected* and
    is not there — notably ``worktree_unregistered``, which is what a post-merge
    re-run hits (CLEANUP_STATUSES clears the flag) — so they log at warning.
    Never raises.
    """
    if not project_id or not group_id:
        return None, SRC_ROOT_NO_GROUP
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            _log.debug(
                "effective_src_root: base tree for %s (%s)",
                group_id,
                SRC_ROOT_INTEGRATION_OFF,
            )
            return None, SRC_ROOT_INTEGRATION_OFF
        state = db_git.get_state(group_id)
        if state is None:
            _log.warning(
                "effective_src_root: base tree for %s (%s) — git integration is on "
                "but the group has no git state row",
                group_id,
                SRC_ROOT_NO_STATE,
            )
            return None, SRC_ROOT_NO_STATE
        if not state.get("worktree_registered"):
            _log.warning(
                "effective_src_root: base tree for %s (%s, status=%s) — the worktree "
                "was never registered or was released (merged/pushed cleanup)",
                group_id,
                SRC_ROOT_UNREGISTERED,
                state.get("status"),
            )
            return None, SRC_ROOT_UNREGISTERED
        branch = (state.get("branch") or "").strip()
        if not branch:
            _log.warning(
                "effective_src_root: base tree for %s (%s)", group_id, SRC_ROOT_NO_BRANCH
            )
            return None, SRC_ROOT_NO_BRANCH
        project_name = _project_name(project_id)
        if not project_name:
            _log.warning(
                "effective_src_root: base tree for %s (%s, project_id=%s)",
                group_id,
                SRC_ROOT_NO_PROJECT_NAME,
                project_id,
            )
            return None, SRC_ROOT_NO_PROJECT_NAME
        wt_path = src_root(project_name, branch)
        if not wt_path.is_dir():
            # E7/E13: ledger without directory → fallback
            _log.warning(
                "effective_src_root: base tree for %s (%s, expected=%s branch=%s)",
                group_id,
                SRC_ROOT_DIR_MISSING,
                wt_path,
                branch,
            )
            return None, SRC_ROOT_DIR_MISSING
        if not _worktree_link_ok(wt_path):
            # 0287 NR0004 §5: a directory is not a source tree. Without its `.git`
            # link the path is what an interrupted teardown left behind, and
            # returning it here is what silently pointed a suite at a half-erased
            # checkout while the TSR still labelled the root "worktree".
            _log.warning(
                "effective_src_root: base tree for %s (%s, path=%s branch=%s) — the "
                "directory has no .git link (leftover of an interrupted worktree "
                "teardown); it is NOT a usable source tree",
                group_id,
                SRC_ROOT_DIR_BROKEN,
                wt_path,
                branch,
            )
            return None, SRC_ROOT_DIR_BROKEN
        return wt_path.resolve(), SRC_ROOT_WORKTREE
    except Exception:
        _log.warning("effective_src_root failed for %s", group_id, exc_info=True)
        return None, SRC_ROOT_ERROR


def group_worktree_writable(project_id: Optional[str], group_id: Optional[str]) -> bool:
    """True when *group_id* has a live worktree that may be written to.

    0327 T0004 (B0001 / NR0003 권고 1): the explorer used to treat "a group is
    selected" as "read-only", so create/upload stayed blocked even for the group
    the user is actively working in — while the server could already tell the two
    apart. This is that answer, in the one shape the client needs, so the UI stops
    guessing. Groups with no worktree (finalized, disposed, never provisioned)
    remain fully read-only, exactly as before (권고 5).
    """
    return effective_src_root_ex(project_id, group_id)[0] is not None


def effective_src_root(project_id: Optional[str], group_id: Optional[str]) -> Optional[Path]:
    """Group worktree path when it must be used, else None (= caller falls back).

    Fallback-first (L0006 §2.2): missing config, disabled integration, missing
    ledger entry, or a vanished directory all yield None so the caller resolves
    the ordinary project-branch folder. Never raises. Thin wrapper over
    ``effective_src_root_ex`` — callers that need to record WHY the worktree was
    skipped use that one directly (0280 T0005).
    """
    return effective_src_root_ex(project_id, group_id)[0]


# ── Finalize state (P0005 §5-1 / L0006 §3) ───────────────────────────────────

_NONE_STATE = {
    "branch": None, "base_branch": None, "status": "none", "default_action": None,
    "choices": [], "aux_choices": [], "action_axes": None,
    "ahead_count": None, "behind_count": None, "merge_id": None,
}


def _group_root_wf_done(group_id: str) -> bool:
    """True when the group's workflow root (R/B) reached final approval."""
    row = get_store()._fetch_one(
        "SELECT 1 AS hit FROM documents "
        "WHERE group_id = ? AND type_code IN ('R','B') AND doc_review_status = 'wf_done'",
        [group_id],
    )
    return row is not None


def _groups_root_wf_done(group_ids: list[str]) -> set[str]:
    """Batch form of _group_root_wf_done (0282 NR0003 발견 1): one IN query
    instead of one probe per group. project_git_status ran the per-group probe
    inside its slot loop — 8 groups × 2 client calls = 12 of the 68 queries in
    the R0001 screen-load log, growing linearly with group count."""
    if not group_ids:
        return set()
    placeholders = ", ".join("?" for _ in group_ids)
    rows = get_store()._fetch_all(
        "SELECT DISTINCT group_id FROM documents "
        f"WHERE group_id IN ({placeholders}) "
        "AND type_code IN ('R','B') AND doc_review_status = 'wf_done'",
        list(group_ids),
    )
    return {r["group_id"] for r in rows}


def _group_ac_doc_id(group_id: str) -> Optional[str]:
    """Newest AC (final-approval) doc id of the group, or None. Never raises —
    the field is advisory navigation state for the header [open] button
    (flowgate.default.0182 NR0003 §4)."""
    try:
        row = get_store()._fetch_one(
            "SELECT doc_id FROM documents "
            "WHERE group_id = ? AND type_code = 'AC' ORDER BY doc_id DESC",
            [group_id],
        )
        return row["doc_id"] if row else None
    except Exception:
        _log.warning("ac_doc_id lookup failed for %s", group_id, exc_info=True)
        return None


def _group_ac_doc_ids(group_ids: list[str]) -> dict[str, str]:
    """Batch form of _group_ac_doc_id (0282 NR0003 발견 1). MAX(doc_id) per
    group ≡ the single version's ORDER BY doc_id DESC first row. Same advisory
    never-raise contract: on failure every pending row simply carries no
    ac_doc_id and the [open] button falls back to the R root."""
    if not group_ids:
        return {}
    try:
        placeholders = ", ".join("?" for _ in group_ids)
        rows = get_store()._fetch_all(
            "SELECT group_id, MAX(doc_id) AS doc_id FROM documents "
            f"WHERE group_id IN ({placeholders}) AND type_code = 'AC' "
            "GROUP BY group_id",
            list(group_ids),
        )
        return {r["group_id"]: r["doc_id"] for r in rows if r.get("doc_id")}
    except Exception:
        _log.warning("ac_doc_id batch lookup failed", exc_info=True)
        return {}


# ── No-work divergence gating (flowgate.default.0199 B0001) ──────────────────
#
# The none→awaiting_choice transition (three sites: realize_wf_done_transition,
# get_finalize_state lazy, project_git_status aggregation) used to fire on
# `wf_done` + `worktree_registered` ALONE, never checking whether the group's
# work branch actually diverged from base. A pure R/CH/AC inquiry (no T = no code
# change) thus landed in `awaiting_choice`, and the only exits were merge (empty
# `--no-ff` commit + base push) or push (empty branch leaked to origin) — hence
# the "forced git finalize with nothing to finalize" bug. These helpers let each
# transition site prove emptiness first and, when proven, auto-discard the slot
# with no merge and no push instead.

def _ahead_of_base(base_root: Path, base_branch: str, branch: str) -> Optional[int]:
    """Number of commits on `branch` not yet on `base_branch` (local rev-list, no
    network). None when it cannot be counted (missing ref / git failure)."""
    proc = _run_git(["rev-list", "--count", f"{base_branch}..{branch}"], cwd=base_root)
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def _group_has_changes(
    cfg: dict, state: dict, project_name: Optional[str]
) -> Optional[bool]:
    """Whether a group's work branch carries real, mergeable work.

    True  — commits ahead of the base branch, OR any uncommitted / untracked edit
            in the worktree (finalize's `add -A` absorb would turn these into a
            commit, so they count as work).
    False — branch at the base tip AND a pristine worktree: nothing to merge/push.
    None  — divergence cannot be measured (git off, or the branch/worktree/base
            checkout is missing). The caller must then keep the conservative
            awaiting_choice gate — never discard on doubt.
    """
    if not project_name or not git_available():
        return None
    branch = (state.get("branch") or "").strip()
    if not branch:
        return None
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        return None
    ahead = _ahead_of_base(base_root, base_branch, branch)
    if ahead is None:
        return None
    if ahead > 0:
        return True
    # ahead == 0: no committed work. Uncommitted/untracked worktree edits still
    # count (a merge/push would absorb them), so inspect the worktree too.
    wt_path = src_root(project_name, branch)
    if wt_path.is_dir():
        return bool(_dirty(wt_path))
    return False


def _auto_discard_group(project_id: str, group_id: str) -> str:
    """Tear down a PROVEN no-work group's slot without any merge or push. Reuses
    `_cleanup_group_slot(force_discard=True)` (worktree remove --force → prune →
    branch -D → unregister); with ahead_count==0 the force-deleted local branch
    holds no unique commit, so nothing is lost, and origin is never touched.

    Best-effort: if the project git lock is busy the group is left `none` (its
    badge stays hidden — a no-work group is not "pending" — and the next status
    query retries the discard). Never raises. Returns the label the caller should
    treat the group as having: DISCARDED_STATUS on success, "none" when the lock
    was busy (the DB status stays "none" either way — see the constant)."""
    holder = f"discard:{uuid.uuid4()}"
    # wait_sec=0: never block a status GET / an approval on a busy lock — retry
    # opportunistically on the next transition query instead.
    if not _acquire_lock(project_id, holder, wait_sec=0):
        return "none"
    try:
        cleaned = _cleanup_group_slot(project_id, group_id, force_discard=True)
    finally:
        db_git.release_lock(project_id, holder)
    if not cleaned:
        # Cleanup could not complete (e.g. worktree remove blocked) — leave the
        # slot registered so a later query retries rather than orphaning it.
        return "none"
    # Slot unregistered; clear any pending badge and nudge the explorer to re-fetch
    # the group dropdown (mirrors cleanup_disposed_group's post-cleanup emit).
    _emit_pending_changed(project_id, group_id, "none")
    return DISCARDED_STATUS


def _decide_pending_transition(
    project_id: str, cfg: dict, state: dict, group_id: str
) -> str:
    """Resolve a wf_done group out of `none` into its real status.

    A group with actual work (or whose divergence cannot be proven empty) enters
    `awaiting_choice` — the finalize gate, exactly as before. A PROVEN no-work
    group is auto-discarded instead (no merge, no push). Returns the status the
    caller should treat the group as having: "awaiting_choice", "discarded", or
    "none" (no-work but the discard lock was busy — retry later). Never raises the
    git error out to the caller."""
    project_name = _project_name(project_id)
    if _group_has_changes(cfg, state, project_name) is False:
        return _auto_discard_group(project_id, group_id)
    # Had changes, or divergence unmeasurable → preserve the original safe gate.
    _set_status(group_id, "awaiting_choice")
    return "awaiting_choice"


def realize_wf_done_transition(group_id: str) -> None:
    """Eagerly realize the lazy none→awaiting_choice transition at final-approval
    time (0177 NR0016 §3). The lazy design (L0006 §3) only realizes on the NEXT
    status query, so a plain AC approval emitted no git_pending_changed and the
    header badge stayed stale until a reload. Called from the approval paths
    right after the workflow root flips to wf_done; never raises — a git hiccup
    must not disturb the approval that already stood."""
    try:
        project_id = _project_of_group(group_id)
        cfg = db_git.get_config(project_id)
        state = db_git.get_state(group_id)
        if (
            cfg is None or not cfg.get("enabled")
            or state is None or not state.get("worktree_registered")
        ):
            return
        if (state.get("status") or "none") == "none" and _group_root_wf_done(group_id):
            # 0199 B0001: a no-work group is discarded (no merge/push) rather than
            # parked in awaiting_choice; a real group still gets the finalize gate.
            _decide_pending_transition(project_id, cfg, state, group_id)
    except Exception:
        _log.warning(
            "wf_done git transition realization failed for %s", group_id, exc_info=True
        )


def get_finalize_state(group_id: str, *, preview_ac: bool = False) -> dict:
    project_id = _project_of_group(group_id)
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if cfg is None or not cfg.get("enabled") or state is None:
        return {"ok": True, "state": {"group_id": group_id, **_NONE_STATE}}

    status = state.get("status") or "none"
    if not state.get("worktree_registered") and status not in CLEANUP_STATUSES:
        return {"ok": True, "state": {"group_id": group_id, **_NONE_STATE}}
    # Lazy none→awaiting_choice transition (L0006 §3): the workflow module never
    # calls into git; the first state query after wf_done realizes the transition.
    # 0199 B0001: a proven no-work group is auto-discarded here instead of being
    # gated — it has nothing to finalize, so report it as the empty NONE state.
    if status == "none" and _group_root_wf_done(group_id):
        status = _decide_pending_transition(project_id, cfg, state, group_id)
        if status != "awaiting_choice":
            # discarded (torn down, no merge/push) or none (discard lock busy —
            # retry next query): either way there is no finalize gate to show.
            return {"ok": True, "state": {"group_id": group_id, **_NONE_STATE}}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branch = state.get("branch")
    ahead = behind = None
    project_name = _project_name(project_id)
    if project_name and git_available():
        base_root = src_root(project_name, base_branch)
        if (base_root / ".git").exists():
            proc = _run_git(
                ["rev-list", "--left-right", "--count", f"{base_branch}...{branch}"],
                cwd=base_root,
            )
            if proc.returncode == 0:
                m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", proc.stdout or "")
                if m:
                    behind, ahead = int(m.group(1)), int(m.group(2))

    # Suggested commit message (flowgate.default.0173 P0003 §2): only meaningful
    # while the group awaits a commit-producing choice; null otherwise.
    if status in ("awaiting_choice", "waiting"):
        subject, source = resolve_commit_message(group_id)
        commit_message: Optional[dict] = {"suggested": subject, "source": source}
    else:
        commit_message = None

    actionable = status in ("awaiting_choice", "waiting")
    return {"ok": True, "state": {
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "status": status,
        "default_action": cfg.get("default_finalize_action") or "wait",
        "choices": list(FINALIZE_MAIN_CHOICES if actionable else ()),
        "aux_choices": list(FINALIZE_AUX_CHOICES if actionable else ()),
        # Additive (NR 0331.0005 §8): the axis client renders from this and
        # ignores choices/aux_choices; a client that does not know the key falls
        # back to the legacy card list untouched above.
        "action_axes": {
            "scopes": list(FINALIZE_AXIS_SCOPES),
            "matrix": {k: dict(v) for k, v in FINALIZE_AXIS_MATRIX.items()},
            "commit_actions": list(FINALIZE_COMMIT_ACTIONS),
        } if actionable else None,
        "ahead_count": ahead,
        "behind_count": behind,
        "merge_id": state.get("merge_id"),
        "merge_commit": state.get("merge_commit"),
        "commit_message": commit_message,
        # True only for the display-only pre-approval preview (0197 T0004 §B);
        # the persisted status is still 'none'. Advisory for the FE.
        "preview": preview_ac,
    }}


# ── Group branch file explorer: checkout-free ref/tree/blob (0186 L0006 §2) ──
#
# Pure read layer. The group worktree shares base_root/.git with the base
# checkout (git worktree add), so a group branch's ref/tree/blob objects can be
# served straight from the shared object store WITHOUT switching a checkout.
# These functions acquire no git_project_lock, never provision a branch, and
# write no DB row — a missing / disabled / unregistered group is a 409.

_REF_PIN_RE = re.compile(r"^[0-9a-f]{40}$")


def resolve_group_ref(project_id: str, group_id: str) -> tuple[Path, str, str]:
    """(base_root, branch, commit) for a group branch. Pure read (L0006 §2.1)."""
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    state = db_git.get_state(group_id)
    if state is None or not state.get("worktree_registered"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    # Guard against a project_id path param that does not own this group: the
    # config was looked up by project_id but the branch by group_id, so a mismatch
    # would resolve the wrong repository.
    if (state.get("project_id") or _project_of_group(group_id)) != project_id:
        raise GitServiceError(
            409, "invalid_state", f"group '{group_id}' does not belong to project '{project_id}'"
        )
    branch = state.get("branch")
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
    if not git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    proc = _run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    commit = (proc.stdout or "").strip()
    if proc.returncode != 0 or not commit:
        raise GitServiceError(409, "invalid_state", f"branch '{branch}' not found in repository")
    return base_root, branch, commit


def _tree_sort_key(name: str, is_dir: bool) -> tuple:
    # Reuse the exact file-tree ordering (folders-first, natural, case-insensitive)
    # so the group explorer matches the base-branch tree. Lazy import avoids a
    # module-load cycle with process_service.
    from modules.flow_gate.process_service import _file_tree_sort_key
    return _file_tree_sort_key(name, is_dir)


def _build_tree_nodes(files: list[str], dirs: Sequence[str] = ()) -> list[dict]:
    """FileNode list (same contract as process_service.get_file_tree) from a flat
    list of visible blob paths.

    0327 T0004 (B0001): *dirs* carries directory paths that hold no file at all.
    Git has no way to express an empty directory, so a folder just created in a
    group worktree is invisible to every file-based listing — the new folder the
    user asked for would silently not appear. Those paths are registered with
    every segment forced to folder so the tree shows them.
    """
    children: dict[str, dict[str, bool]] = {"": {}}

    def register(path: str, leaf_is_dir: bool) -> None:
        segs = [seg for seg in path.split("/") if seg]
        for i, name in enumerate(segs):
            parent = "/".join(segs[:i])
            is_dir = leaf_is_dir or i < len(segs) - 1
            children.setdefault(parent, {})
            prev = children[parent].get(name)
            children[parent][name] = bool(prev) or is_dir
            if is_dir:
                children.setdefault("/".join(segs[: i + 1]), {})

    for path in files:
        register(path, False)
    for path in dirs:
        register(path, True)
    nodes: list[dict] = []
    counter = [0]

    def walk(dirpath: str, parent_id: Optional[str]) -> None:
        entries = sorted(
            children.get(dirpath, {}).items(),
            key=lambda kv: _tree_sort_key(kv[0], kv[1]),
        )
        for name, is_dir in entries:
            counter[0] += 1
            cur = str(counter[0])
            full = f"{dirpath}/{name}" if dirpath else name
            if is_dir:
                nodes.append({
                    "id": cur, "parent_id": parent_id, "type": "folder",
                    "name": name, "label": name, "path": full,
                    "permissions": ["read"], "children": [],
                })
                walk(full, cur)
            else:
                nodes.append({
                    "id": cur, "parent_id": parent_id, "type": "file",
                    "name": name, "label": name, "path": full,
                    "permissions": ["read", "download"],
                })

    walk("", None)
    return nodes


def _is_hidden_source_path(path: str) -> bool:
    """Group-explorer exposure rule (shared by tree/changes/blob): hide dotfiles and
    ``*.db``, matching the committed-tree filter so the untracked channel never
    surfaces a path the committed view would have hidden."""
    segments = path.split("/")
    return (
        any(seg.startswith(".") for seg in segments)
        or segments[-1].lower().endswith(".db")
    )


def _group_worktree_path(project_id: str, group_id: str, branch: str) -> Optional[Path]:
    """Absolute path of a group's live worktree, or None when it is unavailable.

    NR0003: the checkout-free explorer reads committed git objects only, so a new
    file the worker has not committed is invisible until finalize. The tree/changes/
    blob readers use this worktree to surface those untracked files. A finalized or
    not-yet-provisioned group has no worktree — a normal, non-fatal state (returns
    None), so the committed view still renders on its own."""
    state = db_git.get_state(group_id) or {}
    project_name = _project_name(project_id)
    if not project_name:
        return None
    wt_path = src_root(project_name, state.get("branch") or branch)
    return wt_path if wt_path.exists() else None


def _group_untracked_visible(wt_path: Path) -> list[str]:
    """Exposed untracked (never-committed) paths in a group worktree, sorted.

    ``git diff`` / ``ls-tree`` never report untracked files (NR0003 §3.1·§3.2), so
    these are collected with ``ls-files --others --exclude-standard`` and filtered by
    the same exposure rule as the committed tree. git emits '/'-separated paths."""
    proc = _run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(proc.stderr) or "ls-files failed"
        )
    out: list[str] = []
    for path in (proc.stdout or "").split("\0"):
        if path and not _is_hidden_source_path(path):
            out.append(path)
    return sorted(out)


def _group_empty_dirs_visible(wt_path: Path) -> list[str]:
    """Exposed untracked directories that contain no file anywhere beneath them.

    0327 T0004 (B0001): creating a folder in a group worktree used to leave no
    trace in the explorer — git tracks files, so an empty directory is reported by
    no file listing and the new folder simply never appeared. ``ls-files --others
    --directory`` names the shallowest untracked directory; the ones that do hold
    files are already covered by ``_group_untracked_visible`` (their file paths
    imply the folders), so only the file-less ones are expanded here, together
    with their equally empty subdirectories.
    """
    proc = _run_git(
        ["ls-files", "--others", "--exclude-standard", "--directory", "-z"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(proc.stderr) or "ls-files failed"
        )
    out: list[str] = []
    for entry in (proc.stdout or "").split("\0"):
        rel = entry.rstrip("/")
        # Only collapsed directory entries ('dir/') are of interest; plain files
        # come from the untracked-file scan.
        if not entry.endswith("/") or not rel or _is_hidden_source_path(rel):
            continue
        root = wt_path / rel
        if not root.is_dir():
            continue
        if any(p.is_file() for p in root.rglob("*")):
            continue  # holds files → its untracked file paths already imply it
        out.append(rel)
        for sub in root.rglob("*"):
            if not sub.is_dir():
                continue
            sub_rel = sub.relative_to(wt_path).as_posix()
            if not _is_hidden_source_path(sub_rel):
                out.append(sub_rel)
    return sorted(set(out))


def _group_empty_dirs_safe(project_id: str, group_id: str, branch: str) -> list[str]:
    """``_group_empty_dirs_visible`` for a resolved group, degrading to ``[]``.

    Same contract as ``_group_untracked_safe``: a supplemental channel must never
    break the committed tree read that worked before it existed."""
    try:
        wt_path = _group_worktree_path(project_id, group_id, branch)
        if wt_path is None:
            return []
        return _group_empty_dirs_visible(wt_path)
    except Exception:  # noqa: BLE001 — supplemental channel, never fatal
        _log.warning("group empty-dir scan failed for %s", group_id, exc_info=True)
        return []


def _group_untracked_safe(project_id: str, group_id: str, branch: str) -> list[str]:
    """``_group_untracked_visible`` for a resolved group, degrading to ``[]`` on any
    failure. Untracked files SUPPLEMENT the committed view: a worktree hiccup must
    never break the tree/changes read that worked before this channel existed."""
    try:
        wt_path = _group_worktree_path(project_id, group_id, branch)
        if wt_path is None:
            return []
        return _group_untracked_visible(wt_path)
    except Exception:  # noqa: BLE001 — supplemental channel, never fatal
        _log.warning("group untracked scan failed for %s", group_id, exc_info=True)
        return []


# 0325 T0006: per-file +/- line counts for the changes list. `git diff --numstat`
# already reports them for tracked paths; an untracked file has no diff entry at
# all, so its "added" count is read off disk. Both channels degrade to None (=
# "unknown", e.g. binary) rather than 0, so the client never shows a made-up 0.
_UNTRACKED_STAT_MAX_BYTES = 1_000_000


def _untracked_added_lines(wt_path: Path, rel_path: str) -> Optional[int]:
    """Line count of an untracked file, or None when it is binary/oversized/unreadable."""
    try:
        target = wt_path / rel_path
        if not target.is_file() or target.stat().st_size > _UNTRACKED_STAT_MAX_BYTES:
            return None
        data = target.read_bytes()
    except OSError:
        return None
    if b"\0" in data:  # same binary heuristic git uses for --numstat's "-"
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _diff_line_stats(wt_path: Path, merge_base: str) -> dict[str, tuple[Optional[int], Optional[int]]]:
    """path -> (insertions, deletions) from ``git diff --numstat``.

    Supplemental like the untracked channel: a failure here must not break the
    changes list, so an unusable run yields an empty map and every file falls
    back to None.
    """
    proc = _run_git(
        ["diff", "--numstat", "--no-renames", "-z", merge_base, "--"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        _log.warning("numstat failed in %s: %s", wt_path, _one_line_subject(proc.stderr))
        return {}
    stats: dict[str, tuple[Optional[int], Optional[int]]] = {}
    # -z record shape (renames excluded): "<added>\t<deleted>\t<path>\0".
    # Binary files report "-" for both counts.
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        added, sep, rest = record.partition("\t")
        deleted, sep2, path = rest.partition("\t")
        if not sep or not sep2 or not path:
            continue
        stats[path] = (
            int(added) if added.isdigit() else None,
            int(deleted) if deleted.isdigit() else None,
        )
    return stats


def read_group_tree(project_id: str, group_id: str) -> dict:
    """checkout-free recursive tree of a group branch's HEAD commit (L0006 §2.2)."""
    base_root, branch, commit = resolve_group_ref(project_id, group_id)
    proc = _run_git(
        ["ls-tree", "-r", "-z", commit], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _one_line_subject(proc.stderr) or "ls-tree failed")
    visible_files: list[str] = []
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        if not path:
            continue
        parts = meta.split()
        # entry: "<mode> <type> <sha>"; only blobs are files.
        if len(parts) < 2 or parts[1] != "blob":
            continue
        segments = path.split("/")
        # Same exposure rule as the base-branch tree: hide dotfiles and *.db.
        if any(seg.startswith(".") for seg in segments) or segments[-1].lower().endswith(".db"):
            continue
        visible_files.append(path)
    untracked = _group_untracked_safe(project_id, group_id, branch)
    # 0327 T0004 (B0001): folders created in the group worktree that hold no file
    # yet — they exist on disk but in no file listing, so they need their own channel.
    empty_dirs = _group_empty_dirs_safe(project_id, group_id, branch)
    # _build_tree_nodes dedups by name per directory, so committed + untracked paths
    # can be concatenated directly. worktree_untracked is ALSO returned as a separate
    # channel (NR0003 권고 1): the client caches the tree by commit, but untracked
    # files change without advancing the commit, so this list must not be cached there.
    nodes = _build_tree_nodes(visible_files + untracked, empty_dirs)
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit, "nodes": nodes,
        "worktree_untracked": untracked,
        "worktree_untracked_dirs": empty_dirs,
    }}


def _group_diff_context(project_id: str, group_id: str) -> tuple[str, str, str, Path, str]:
    """(base_branch, branch, commit, worktree_path, merge_base) for group-vs-base diffs.

    Shared by the changes list and the per-file diff reader (0325 TR0007 rev1) so both
    compare against the SAME merge-base — otherwise the summary and the diff a reviewer
    opens from it could disagree about what this group changed.
    """
    base_root, branch, commit = resolve_group_ref(project_id, group_id)
    cfg = db_git.get_config(project_id) or {}
    state = db_git.get_state(group_id) or {}
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    wt_path = src_root(project_name, state.get("branch") or branch)
    if not wt_path.exists():
        raise GitServiceError(409, "invalid_state", "group worktree is not available")

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    merge_proc = _run_git(
        ["merge-base", f"refs/heads/{base_branch}", commit],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    merge_base = (merge_proc.stdout or "").strip()
    if merge_proc.returncode != 0 or not merge_base:
        raise GitServiceError(
            500, "git_error", _one_line_subject(merge_proc.stderr) or "merge-base failed"
        )
    return base_branch, branch, commit, wt_path, merge_base


def read_group_changes(project_id: str, group_id: str) -> dict:
    """Tracked paths changed from the group's base commit through its worktree."""
    base_branch, branch, commit, wt_path, merge_base = _group_diff_context(project_id, group_id)

    diff_proc = _run_git(
        ["diff", "--name-status", "--no-renames", "-z", merge_base, "--"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if diff_proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(diff_proc.stderr) or "git diff failed"
        )

    # 0325 T0006: the final-approval sidebar summarizes "how big is this change",
    # which --name-status cannot answer. A second read-only pass over the same
    # merge-base supplies the per-file +/- counts.
    line_stats = _diff_line_stats(wt_path, merge_base)

    fields = (diff_proc.stdout or "").split("\0")
    changes: list[dict] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index], fields[index + 1]
        if not status or not path:
            continue
        segments = path.split("/")
        if any(seg.startswith(".") for seg in segments) or segments[-1].lower().endswith(".db"):
            continue
        insertions, deletions = line_stats.get(path, (None, None))
        changes.append({
            "path": path, "status": status[:1],
            "insertions": insertions, "deletions": deletions,
        })
    # NR0003 권고 2: git diff never lists untracked files, so a brand-new file would be
    # absent from the changes list entirely — the exact "수정은 보이는데 신규만 안 보이는"
    # asymmetry B0001 reports. Surface each with "?" (git porcelain's untracked marker).
    existing = {change["path"] for change in changes}
    for path in _group_untracked_safe(project_id, group_id, branch):
        if path not in existing:
            # A never-added file deletes nothing, so 0 here is a fact, not a guess.
            changes.append({
                "path": path, "status": "?",
                "insertions": _untracked_added_lines(wt_path, path), "deletions": 0,
            })
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit,
        # 0325 TR0007 rev1: the changes viewer titles itself "<branch> ↔ <base>", and
        # the base branch is a project setting the client had no other way to read.
        "base_branch": base_branch, "changes": changes,
    }}


def collect_scope_changes(project_id: str, group_id: str) -> dict:
    """Every path this group actually changed, seen from its OWN worktree (0299 D0004 §3.3).

    Deliberately NOT ``read_group_changes``. That one resolves the tree through
    ``src_root(project_name, branch)`` and only checks that the directory exists,
    and it looks at committed/tracked changes alone. For 작업범위 검증 both gaps are
    fatal: a group whose worktree is missing must NOT silently be measured against
    the base checkout (that is the very accident this feature exists to catch), and
    a brand-new file that was never ``git add``-ed is the most ordinary shape of
    real work there is — missing it would produce a bogus TRV-003 on an honest
    report. So this resolves strictly via ``effective_src_root_ex`` (no main
    fallback) and unions three sources:

      * merge-base..worktree diff — committed + staged + unstaged tracked changes
      * ``ls-files --others`` — untracked new files
      * renames resolved to the NEW path only (D0004 §3.2: "이름을 바꾼 경우
        바뀐 뒤의 경로만 적는다"), hence ``-M`` instead of ``--no-renames``

    Returns ``{"available": bool, "reason": str, "worktree": str|None,
    "branch": str|None, "paths": [str]}``. Never raises: an unavailable worktree or
    a failing git call is a *result* (``available=False`` + reason), because the
    caller must turn that into TRV-006 rather than a 500 on someone's TR.
    Exclusion rules are NOT applied here — tr_scope_service owns them so the same
    filter runs over the reported list too.
    """
    result: dict = {
        "available": False, "reason": SRC_ROOT_ERROR,
        "worktree": None, "branch": None, "paths": [],
    }
    wt_path, reason = effective_src_root_ex(project_id, group_id)
    result["reason"] = reason
    if wt_path is None:
        return result
    result["worktree"] = str(wt_path)
    try:
        state = db_git.get_state(group_id) or {}
        result["branch"] = (state.get("branch") or "").strip() or None
        cfg = db_git.get_config(project_id) or {}
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"

        paths: set[str] = set()
        merge_proc = _run_git(
            ["merge-base", f"refs/heads/{base_branch}", "HEAD"],
            cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
        )
        merge_base = (merge_proc.stdout or "").strip()
        if merge_proc.returncode == 0 and merge_base:
            diff_proc = _run_git(
                ["diff", "--name-status", "-M", "-z", merge_base, "--"],
                cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
            )
            if diff_proc.returncode != 0:
                result["reason"] = SRC_ROOT_ERROR
                return result
            paths.update(_parse_name_status_z(diff_proc.stdout or ""))
        else:
            # No merge base (unrelated histories / missing base branch) — the
            # committed half cannot be computed. Working-tree state alone would be
            # a partial answer that reads as "you reported files you never changed",
            # so refuse the whole measurement instead of half of it.
            result["reason"] = SRC_ROOT_ERROR
            return result

        others = _run_git(
            ["ls-files", "--others", "--exclude-standard", "-z"],
            cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
        )
        if others.returncode != 0:
            result["reason"] = SRC_ROOT_ERROR
            return result
        for path in (others.stdout or "").split("\0"):
            if path:
                paths.add(path)

        result["available"] = True
        result["reason"] = SRC_ROOT_WORKTREE
        result["paths"] = sorted(paths)
        return result
    except Exception:  # noqa: BLE001 — a verification helper must never 500 a TR
        _log.warning("collect_scope_changes failed for %s", group_id, exc_info=True)
        result["available"] = False
        result["reason"] = SRC_ROOT_ERROR
        return result


def _parse_name_status_z(stdout: str) -> list[str]:
    """``git diff --name-status -M -z`` → changed paths (renames → new path only).

    The -z record shape differs per status: ``M\\0path\\0`` but ``R100\\0old\\0new\\0``.
    A fixed 2-field stride (what read_group_changes can afford with --no-renames)
    desynchronizes the whole stream on the first rename, so this walks the fields.
    """
    fields = (stdout or "").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        take_second = status[:1] in ("R", "C")
        if index >= len(fields):
            break
        first = fields[index]
        index += 1
        if take_second:
            if index >= len(fields):
                break
            second = fields[index]
            index += 1
            if second:
                paths.append(second)
        elif first:
            paths.append(first)
    return paths


def _validate_blob_path(path: str) -> None:
    """Reject empty / absolute / drive-prefixed / '..' paths (P0005 §7)."""
    if not path:
        raise GitServiceError(400, "invalid_path", "path parameter is required")
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise GitServiceError(400, "invalid_path", "absolute paths are not allowed")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise GitServiceError(400, "invalid_path", "drive prefix is not allowed")
    if ".." in normalized.split("/"):
        raise GitServiceError(400, "invalid_path", "'..' path segments are not allowed")


def _ls_tree_entry(base_root: Path, commit: str, path: str) -> Optional[tuple[str, str]]:
    """(object_type, sha) of a single path in a commit tree, or None if absent."""
    proc = _run_git(
        ["ls-tree", "-z", commit, "--", path], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _one_line_subject(proc.stderr) or "ls-tree failed")
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        meta, _, entry_path = record.partition("\t")
        if entry_path != path:
            continue
        parts = meta.split()
        if len(parts) >= 3:
            return parts[1], parts[2]
    return None


def _cat_file_size(base_root: Path, sha: str) -> int:
    proc = _run_git(["cat-file", "-s", sha], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _one_line_subject(proc.stderr) or "cat-file failed")
    try:
        return int((proc.stdout or "0").strip())
    except ValueError:
        return 0


def _cat_file_blob_head(base_root: Path, sha: str, limit: int) -> bytes:
    """Read up to ``limit`` raw bytes of a blob (bounded so a huge object is never
    slurped whole just to sniff/truncate it)."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.Popen(
            ["git", "cat-file", "blob", sha], cwd=str(base_root),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
    except FileNotFoundError:
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    try:
        data = proc.stdout.read(limit) if proc.stdout else b""
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        try:
            proc.wait(timeout=GIT_READ_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            pass
    return data


def _read_group_untracked_blob(
    project_id: str, group_id: str, branch: str, path: str
) -> Optional[dict]:
    """Read an untracked worktree file for the group explorer, or None when the path
    is not an exposed untracked file (the caller then 404s as before).

    NR0003 권고 3: git objects hold committed content only, so a not-yet-committed file
    can be read solely off the worktree disk. The read is gated three ways — the
    exposure filter, git's own untracked list, and a resolved-path containment check
    against the worktree root — so it can never serve a tracked, hidden, or out-of-tree
    file. Binary sniff / truncation mirror read_group_blob. The response carries
    commit=None + untracked=True: it has no point-in-time, so it must not be pinned."""
    wt_path = _group_worktree_path(project_id, group_id, branch)
    if wt_path is None:
        return None
    normalized = path.replace("\\", "/")
    if _is_hidden_source_path(normalized):
        return None
    try:
        if normalized not in set(_group_untracked_visible(wt_path)):
            return None
    except GitServiceError:
        return None
    try:
        wt_resolved = wt_path.resolve()
        file_path = (wt_resolved / normalized).resolve()
        file_path.relative_to(wt_resolved)
    except (ValueError, OSError):
        return None
    if not file_path.is_file():
        return None
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as handle:
            head = handle.read(BLOB_MAX_RETURN_BYTES)
    except OSError:
        return None
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            "group_id": group_id, "branch": branch, "commit": None, "path": path,
            "size": size, "binary": True, "truncated": False,
            "encoding": None, "content": None, "untracked": True,
        }}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    content = body.decode("utf-8", errors="replace")
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": None, "path": path,
        "size": size, "binary": False, "truncated": truncated,
        "encoding": "utf-8", "content": content, "untracked": True,
    }}


def read_group_blob(
    project_id: str, group_id: str, path: str, ref: Optional[str] = None
) -> dict:
    """checkout-free single-file read from a group branch (L0006 §2.3)."""
    _validate_blob_path(path)
    base_root, branch, head_commit = resolve_group_ref(project_id, group_id)
    commit = head_commit
    if ref:
        if not _REF_PIN_RE.match(ref):
            raise GitServiceError(400, "invalid_ref", "ref must be a full 40-hex commit sha")
        tproc = _run_git(["cat-file", "-t", ref], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
        if tproc.returncode != 0 or (tproc.stdout or "").strip() != "commit":
            raise GitServiceError(404, "not_found", f"commit '{ref}' not found")
        commit = ref
    entry = _ls_tree_entry(base_root, commit, path)
    if entry is None or entry[0] != "blob":
        # NR0003 권고 3: the path may be a new file that lives only in the group
        # worktree (no commit object yet). Fall back to reading it off disk before
        # giving up — this is what makes a just-created file openable from the tree.
        fallback = _read_group_untracked_blob(project_id, group_id, branch, path)
        if fallback is not None:
            return fallback
        raise GitServiceError(404, "not_found", f"path '{path}' not found in commit {commit}")
    sha = entry[1]
    size = _cat_file_size(base_root, sha)
    head = _cat_file_blob_head(base_root, sha, BLOB_MAX_RETURN_BYTES)
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            "group_id": group_id, "branch": branch, "commit": commit, "path": path,
            "size": size, "binary": True, "truncated": False,
            "encoding": None, "content": None,
        }}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    content = body.decode("utf-8", errors="replace")
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit, "path": path,
        "size": size, "binary": False, "truncated": truncated,
        "encoding": "utf-8", "content": content,
    }}


# ── Single-file change view (0326 R0001 / NR0005 §4) ─────────────────────────
#
# "파일이 변경됐다"까지만 보이고 "어디가 어떻게" 바뀌었는지는 볼 수 없다는 R0001에
# 대한 백엔드 절반. NR0005 §4 안 (b) 를 택했다: 서버는 patch 텍스트를 만들지 않고
# 한 경로의 old/new 두 시점 내용만 내려주고, 라인 diff 는 클라이언트가 이미 가진
# 엔진(useConflictChunks.buildChunkSideDiff)이 계산한다. 서버에서 `git diff` 를
# 돌려 patch 를 파싱하는 것보다 기존 blob 리더(read_group_blob)와 그대로 겹치고,
# 통합/분할 보기 전환이 서버 왕복 없이 끝난다.
#
# 두 벌인 이유도 §4 그대로다: 베이스 체크아웃은 디스크의 작업 트리를 읽지만,
# 그룹 브랜치 뷰는 checkout-free 라 git 오브젝트에서 읽어야 한다.

def _diff_side_payload(head: bytes, size: int) -> dict:
    """One side of a diff from raw bytes. Binary sniff / 1 MiB cap mirror read_group_blob:
    a diff of a binary or oversize file must degrade to a flag, never to a wall of
    replacement characters."""
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"exists": True, "binary": True, "truncated": False, "size": size, "content": None}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    return {
        "exists": True, "binary": False, "truncated": truncated, "size": size,
        "content": body.decode("utf-8", errors="replace"),
    }


def _diff_side_missing() -> dict:
    """The absent side of an add (no old) or a delete (no new)."""
    return {"exists": False, "binary": False, "truncated": False, "size": 0, "content": None}


def _diff_side_from_commit(base_root: Path, commit: Optional[str], path: str) -> dict:
    """Blob content of ``path`` in ``commit``; missing path/commit → the absent side."""
    if not commit:
        return _diff_side_missing()
    entry = _ls_tree_entry(base_root, commit, path)
    if entry is None or entry[0] != "blob":
        return _diff_side_missing()
    sha = entry[1]
    size = _cat_file_size(base_root, sha)
    return _diff_side_payload(_cat_file_blob_head(base_root, sha, BLOB_MAX_RETURN_BYTES), size)


def _diff_side_from_disk(root: Path, path: str) -> dict:
    """Working-tree content of ``path`` under ``root``, containment-checked.

    Mirrors _read_group_untracked_blob's resolve+relative_to guard so a symlink or a
    crafted path can never read outside the checkout; anything unreadable is reported
    as the absent side (i.e. "deleted"), never as a 500."""
    try:
        root_resolved = root.resolve()
        file_path = (root_resolved / path).resolve()
        file_path.relative_to(root_resolved)
    except (ValueError, OSError):
        return _diff_side_missing()
    if not file_path.is_file():
        return _diff_side_missing()
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as handle:
            head = handle.read(BLOB_MAX_RETURN_BYTES)
    except OSError:
        return _diff_side_missing()
    return _diff_side_payload(head, size)


def _diff_status(old: dict, new: dict, path: str) -> str:
    """git --name-status letter for the pair. Neither side existing is a 404: the
    caller asked about a path that is neither in the old snapshot nor on disk."""
    if not old["exists"] and not new["exists"]:
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    if not old["exists"]:
        return "A"
    if not new["exists"]:
        return "D"
    return "M"


def read_base_file_diff(project_id: str, path: str) -> dict:
    """old (HEAD blob) / new (working tree) content of one base-checkout file.

    The base file explorer's dirty/untracked markers come from ``project_git_status``
    (HEAD vs the checkout on disk), so the diff must be measured over exactly that
    same pair — otherwise a file the tree marks as changed could open with an empty
    diff."""
    _validate_blob_path(path)
    normalized = path.replace("\\", "/")
    cfg = _require_enabled_config(project_id)
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    if _is_hidden_source_path(normalized):
        # Same exposure rule as the file tree (dotfiles / *.db are never listed);
        # a path the tree hides must not become readable through the diff view.
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
    if not git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    head_proc = _run_git(
        ["rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    # An empty repository (no HEAD yet) is not an error here — every file simply
    # reads as added.
    head_commit = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
    old = _diff_side_from_commit(base_root, head_commit or None, normalized)
    new = _diff_side_from_disk(base_root, normalized)
    return {"ok": True, "data": {
        "group_id": None, "branch": base_branch, "base_branch": base_branch,
        "commit": head_commit or None, "path": path,
        "status": _diff_status(old, new, path), "old": old, "new": new,
    }}


def read_group_file_diff(
    project_id: str, group_id: str, path: str, ref: Optional[str] = None
) -> dict:
    """old (merge-base blob) / new (group worktree, else branch commit) content.

    The old side is the merge base with the configured base branch — the same
    reference ``read_group_changes`` diffs against, so the tree's changed markers and
    this view can never disagree. The new side prefers the live worktree file (which
    is what ``read_group_changes`` measures, so uncommitted work shows up) and falls
    back to the branch commit's blob for a finalized group whose worktree is gone.
    ``ref`` pins the commit exactly as ``read_group_blob`` does."""
    _validate_blob_path(path)
    normalized = path.replace("\\", "/")
    if _is_hidden_source_path(normalized):
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    base_root, branch, head_commit = resolve_group_ref(project_id, group_id)
    commit = head_commit
    if ref:
        if not _REF_PIN_RE.match(ref):
            raise GitServiceError(400, "invalid_ref", "ref must be a full 40-hex commit sha")
        tproc = _run_git(["cat-file", "-t", ref], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
        if tproc.returncode != 0 or (tproc.stdout or "").strip() != "commit":
            raise GitServiceError(404, "not_found", f"commit '{ref}' not found")
        commit = ref

    cfg = db_git.get_config(project_id) or {}
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    merge_proc = _run_git(
        ["merge-base", f"refs/heads/{base_branch}", commit],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    merge_base = (merge_proc.stdout or "").strip()
    if merge_proc.returncode != 0 or not merge_base:
        raise GitServiceError(
            500, "git_error", _one_line_subject(merge_proc.stderr) or "merge-base failed"
        )

    old = _diff_side_from_commit(base_root, merge_base, normalized)
    wt_path = _group_worktree_path(project_id, group_id, branch)
    new = _diff_side_from_disk(wt_path, normalized) if wt_path is not None else _diff_side_missing()
    if not new["exists"] and wt_path is None:
        new = _diff_side_from_commit(base_root, commit, normalized)
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "base_branch": base_branch,
        "commit": commit, "merge_base": merge_base, "path": path,
        "status": _diff_status(old, new, path), "old": old, "new": new,
    }}


# ── Finalize execution (P0005 §5 / L0006 §2.6·§4.2) ──────────────────────────

def _finalize_context(group_id: str) -> tuple[dict, dict, str, Path, Path]:
    """(cfg, state, project_id, base_root, wt_path) with the entry guards applied."""
    project_id = _project_of_group(group_id)
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if cfg is None or not cfg.get("enabled") or state is None or not state.get("worktree_registered"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    wt_path = src_root(project_name, state["branch"])
    return cfg, state, project_id, base_root, wt_path


def _dirty(repo: Path, include_untracked: bool = True) -> bool:
    args = ["status", "--porcelain"]
    if not include_untracked:
        # E3 guard scope: untracked build artifacts (e.g. __pycache__/*.pyc,
        # .pytest_cache) in the server's base checkout are NOT "local
        # modifications" — only changes to tracked files require operator
        # intervention. See NR flowgate.default.0165.0009.
        args.append("--untracked-files=no")
    proc = _run_git(args, cwd=repo)
    return bool((proc.stdout or "").strip()) if proc.returncode == 0 else False


def _dirty_files(repo: Path, include_untracked: bool = True) -> list[str]:
    """The changed paths behind `_dirty()` — same scope, but the actual file list.

    Used to tell the operator *which* files leave the base checkout dirty so the
    E3 finalize block and the file-editor save warning name them instead of a bare
    500 (flowgate.default.0176 T0010). Parses `git status --porcelain` v1: the
    2-char status code occupies cols 0-1, the path starts at col 3; a rename is
    rendered `old -> new`, so keep the destination.
    """
    args = ["status", "--porcelain"]
    if not include_untracked:
        args.append("--untracked-files=no")
    proc = _run_git(args, cwd=repo)
    if proc.returncode != 0:
        return []
    files: list[str] = []
    for line in (proc.stdout or "").splitlines():
        entry = line[3:].strip() if len(line) > 3 else line.strip()
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        # porcelain may quote paths with unusual chars; strip surrounding quotes.
        if len(entry) >= 2 and entry[0] == '"' and entry[-1] == '"':
            entry = entry[1:-1]
        files.append(entry)
    return files


# Cap on the untracked list carried in advisory payloads (status / worktree-ready
# event). A base checkout that accumulated a build tree can hold thousands of
# untracked paths; the operator only needs to see that they exist and act on the
# first screenful, and an unbounded list would bloat every status poll.
UNTRACKED_LIST_MAX = 200


def _untracked_files(repo: Path, limit: int = UNTRACKED_LIST_MAX) -> list[str]:
    """The base checkout's untracked — i.e. never-committed — files.

    Deliberately the COMPLEMENT of `_dirty_files(include_untracked=False)`, and
    deliberately carried in a SEPARATE field everywhere it surfaces. NR
    flowgate.default.0296.0003 §C3: `include_untracked=False` was one flag doing
    two jobs — bounding the E3 guard (correct, NR flowgate.default.0165.0009) and
    bounding what the operator is *able* to commit (wrong: it left untracked files
    with no in-app commit path, so they never reached a group worktree). Splitting
    the list splits the concerns; the guard scope below is untouched.

    `--untracked-files=all` expands directories into individual paths — a bare
    `?? newdir/` entry is not something the operator can reason about or hand to
    `git add` file-by-file. `.gitignore` is honoured by git itself, so ignored
    files (NR §C4) never appear here: they cannot be committed, hence cannot be
    offered. `limit` (0 = unbounded) caps the scan for display payloads.
    """
    proc = _run_git(["status", "--porcelain", "--untracked-files=all"], cwd=repo)
    if proc.returncode != 0:
        return []
    files: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if not line.startswith("??"):
            continue
        entry = line[3:].strip()
        # porcelain may quote paths with unusual chars; strip surrounding quotes.
        if len(entry) >= 2 and entry[0] == '"' and entry[-1] == '"':
            entry = entry[1:-1]
        if not entry:
            continue
        files.append(entry)
        if limit and len(files) >= limit:
            break
    return files


def _ignored_paths(repo: Path, paths: list[str]) -> list[str]:
    """Which of `paths` `.gitignore` excludes — used to turn an impossible commit
    into an explanation instead of a bare git failure (NR §C4). `git add -- <p>`
    on an ignored path fails with "use -f if you really want to add them"; forcing
    is NOT the answer (an ignored file is ignored on purpose), so the caller
    rejects with a code the FE can phrase as "this file is git-ignored — a worker
    can never see it"."""
    if not paths:
        return []
    proc = _run_git(["check-ignore", "--", *paths], cwd=repo)
    # exit 1 = nothing ignored (empty stdout); 128 = failure → treat as none.
    return [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]


def base_checkout_dirty_status(project_id: str) -> dict:
    """Lightweight base-checkout dirty status for the file-editor save response
    (flowgate.default.0176 T0010 §a).

    A src-content save writes straight into the base checkout by design (an admin
    edit), which leaves the base dirty and — via the E3 guard — blocks merge
    finalize for EVERY group of the project. The editor calls this right after a
    save so the contamination is visible immediately instead of surfacing later as
    a bare finalize 500. `dirty`/`files` scope matches the guard exactly:
    tracked-file changes only (`include_untracked=False`).

    `untracked` is a SEPARATE field (0296 T0004 / NR0003 R1) and is NOT reflected
    in `dirty`: a brand-new file blocks nothing, but it is invisible to every
    worker until committed (the group worktree is built from a commit — NR §C1),
    so the editor needs to name it without the guard treating it as contamination.

    Never raises: the file write already succeeded, so a git-disabled project, a
    missing base checkout, or any git failure all yield a benign
    {"enabled": ..., "dirty": False, "files": [], "untracked": []} — status is
    advisory and must not turn a saved file into an error.
    """
    empty = {"enabled": False, "dirty": False, "files": [], "untracked": []}
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return dict(empty)
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        project_name = _project_name(project_id)
        base_root = src_root(project_name, base_branch) if project_name else None
        if base_root is None or not Path(base_root).is_dir():
            return {**empty, "enabled": True}
        files = _dirty_files(base_root, include_untracked=False)
        return {
            "enabled": True, "dirty": bool(files), "files": files,
            "untracked": _untracked_files(base_root),
        }
    except Exception:
        _log.warning("base_checkout_dirty_status failed for %s", project_id, exc_info=True)
        return dict(empty)


def _merge_commit_subject(branch: str, base_branch: str) -> str:
    """flowgate.default.0232 B0001 — the `--no-ff` merge commit must NOT reuse the
    work subject. Workers never run `git commit`, so the work branch holds exactly
    one absorb commit carrying finalize_subject(); wrapping that single commit in a
    merge commit of the SAME memoized subject made origin show identical title+diff
    twice ("same code committed twice"). A conventional Merge subject makes the pair
    read as a normal work-commit + merge-commit instead of a duplicate. `--no-ff`
    (the two-parent topology) is deliberately kept so unmerge's `^2` restore
    (flowgate.default.0202) still resolves the merged work branch."""
    return f"Merge branch '{branch}' into '{base_branch}'"


def finalize(group_id: str, action: Optional[str], commit_message: Optional[str] = None) -> dict:
    cfg, state, project_id, base_root, wt_path = _finalize_context(group_id)
    action = action or cfg.get("default_finalize_action") or "wait"
    if action not in ACTION_VALUES:
        raise GitServiceError(422, "invalid_request", f"invalid action: {action!r}")

    # Confirmed commit subject (flowgate.default.0173 P0003 §3): normalize+validate
    # BEFORE any state transition or lock acquisition (422 has no side effects). A
    # blank/omitted value means the unmanned path — resolve it just before use.
    provided_subject = normalize_subject(commit_message)
    if len(provided_subject) > COMMIT_SUBJECT_MAX:
        raise GitServiceError(
            422, "invalid_request",
            "commit_message must be a single line of at most 200 characters.",
        )

    # Refresh the lazy wf_done transition before the state guard (L0006 §4.2).
    status = (state.get("status") or "none")
    if status == "none" and _group_root_wf_done(group_id):
        _set_status(group_id, "awaiting_choice")
        status = "awaiting_choice"
    if status in ("merged", "pushed"):
        raise GitServiceError(409, "invalid_state", "already finalized")
    if status == "conflict":
        raise GitServiceError(409, "invalid_state", "resolve or abort the merge first")
    if status == "merging":
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    if status not in ("awaiting_choice", "waiting"):
        raise GitServiceError(409, "invalid_state", f"finalize not available in state '{status}'")

    if action == "wait":
        _set_status(group_id, "waiting")
        return _finalize_result(group_id, project_id, "wait", "waiting")

    # 0205 L §2.2: a merge mutates the shared base checkout — refuse while another
    # group's unresolved conflict session holds it. Checked BEFORE the lock wait
    # (cheap reject) and again after (race close, below). push never touches base.
    if action in ("merge", "merge_only"):
        guard_base_free(project_id)

    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        if action in ("merge", "merge_only"):
            # 2nd gate: a conflict session may have opened while we waited on the
            # lock (conflict no longer holds it — 0205 §2.1), so re-check now.
            guard_base_free(project_id)
        branch = state["branch"]
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        username = cfg.get("username")
        secret = _load_secret_for(cfg) or ""
        author_env = _author_env_from_cfg(cfg)   # 0237 — configured commit author
        resolved_subject: Optional[str] = None

        def finalize_subject() -> str:
            nonlocal resolved_subject
            if resolved_subject is None:
                resolved_subject = provided_subject or resolve_commit_message(group_id)[0]
            return resolved_subject

        if not wt_path.is_dir():
            raise GitServiceError(409, "invalid_state", "group worktree directory is missing")

        # NR flowgate.default.0331.0005 §3: `push` sends only commits that
        # already exist — it must never fabricate one. A dirty worktree under
        # `push` is rejected (409) instead of silently absorbed, so it stays
        # distinguishable from `commit_push` (which is allowed to commit first)
        # and so uncommitted work is never lost to a bare push. merge/merge_only/
        # commit_push/commit_only all still absorb leftover worker edits first;
        # the subject is the user-confirmed message, or the resolver result on
        # the unmanned path (flowgate.default.0173 L0004 §2.6), resolved lazily
        # so a clean worktree never triggers a translate round-trip.
        if action == "push":
            if _dirty(wt_path):
                raise GitServiceError(
                    409, "dirty_worktree",
                    "group worktree has uncommitted changes; use commit_push to "
                    "commit and push together, or archive before pushing",
                    details={"files": _dirty_files(wt_path)},
                )
        elif _dirty(wt_path):
            absorb_subject = finalize_subject()
            proc = _run_git(["add", "-A"], cwd=wt_path)
            if proc.returncode == 0:
                proc = _run_git(
                    [*_GIT_IDENT, "commit", "-m", absorb_subject],
                    cwd=wt_path, author_env=author_env,
                )
            if proc.returncode != 0:
                raise GitServiceError(500, "git_error", _last_line(proc.stderr))

        # 0199 B0001: no-change short-circuit. After absorbing any worker edits,
        # if the work branch still holds NO commit beyond base there is nothing to
        # merge or push — an explicit merge/push here would only stamp an empty
        # `--no-ff` commit on base or leak an empty branch to origin. Tear the slot
        # down with no merge and no push (mirrors the auto-discard transition).
        # ahead is None when it cannot be counted → fall through to the normal
        # merge/push path (never discard on doubt).
        ahead = _ahead_of_base(base_root, base_branch, branch)
        if ahead == 0:
            _cleanup_group_slot(project_id, group_id, force_discard=True)
            # Leave the DB status "none" on the now-unregistered slot (see
            # DISCARDED_STATUS); "discarded" is only a response/SSE label.
            _set_status(group_id, "none")
            _emit("git_finalize_done", project_id, group_id, {
                "project": project_id, "group_id": group_id,
                "action": action, "status": DISCARDED_STATUS, "merge_commit": None,
            })
            return {"ok": True, "result": {
                "action": action, "status": DISCARDED_STATUS, "merge_commit": None,
                "pushed": False, "merge_id": None, "conflict_files": [],
            }}

        # Publish the work branch to origin ONLY for a bare push. A merge lands
        # the worker's commits into base/default locally (the work branch is a
        # worktree of the same repository, reachable by the base merge without a
        # remote round-trip) and pushes only base; the intermediate work branch
        # is never published to origin on a merge.
        # B flowgate.default.0172.0001-B: the user pressed no push, yet the work
        # branch appeared on the remote and default moved. Only the final merge
        # into default is intended to reach origin.
        if action in ("push", "commit_push"):
            proc = _run_git(
                ["push", "origin", branch],
                cwd=wt_path, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if proc.returncode != 0:
                raise GitServiceError(500, "push_rejected", _last_line(proc.stderr))
            _set_status(group_id, "pushed")
            # 0182 NR0003 §5: drop the slot leftovers right away (origin keeps
            # the pushed branch; only the local worktree/ref/ledger go).
            _cleanup_group_slot(project_id, group_id)
            return _finalize_result(group_id, project_id, action, "pushed", pushed=True)

        if action == "commit_only":
            # NR §3: a local-only commit cannot be followed by terminal cleanup
            # (nothing has left the worktree) — leave the group `waiting` so
            # merge/push/archive can still be chosen for it later.
            _set_status(group_id, "waiting")
            return _finalize_result(group_id, project_id, "commit_only", "waiting")

        # action == "merge" / "merge_only"
        if _dirty(base_root, include_untracked=False):
            # E3 — never auto-stash the server's own checkout. Name the dirty files
            # so the FE can tell the operator exactly what to commit or revert in
            # the header Git panel instead of showing a bare 500 (T0010 §b).
            # 409, not 500 (0177 L0002 §2.5): a user-resolvable precondition, in
            # line with the invalid_state/git_busy family; code+details unchanged.
            raise GitServiceError(
                409, "base_dirty",
                "base checkout has local modifications; operator intervention required",
                details={"files": _dirty_files(base_root, include_untracked=False)},
            )
        proc = _run_git(
            ["fetch", "origin"],
            cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        if _ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
            proc = _run_git(["merge", "--ff-only", f"origin/{base_branch}"], cwd=base_root)
            if proc.returncode != 0:
                raise GitServiceError(  # E4
                    500, "base_diverged",
                    "base checkout has local-only commits and cannot fast-forward",
                )
        _set_status(group_id, "merging")
        # 0232 B0001: the merge commit carries a conventional Merge subject, NOT the
        # work subject — the absorb commit above already holds finalize_subject().
        # Reusing it here stamped two commits of identical title+diff onto origin.
        proc = _run_git(
            [*_GIT_IDENT, "merge", "--no-ff", "-m",
             _merge_commit_subject(branch, base_branch), branch],
            cwd=base_root, author_env=author_env,
        )
        if proc.returncode == 0:
            wants_push = action == "merge"
            if wants_push:
                push = _run_git(
                    ["push", "origin", base_branch],
                    cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
                )
                if push.returncode != 0:
                    # E6 — atomicity: never report merged unless the push landed.
                    _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root)
                    _set_status(group_id, "waiting")
                    raise GitServiceError(500, "push_rejected", _last_line(push.stderr))
            head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
            merge_commit = (head.stdout or "").strip() or None
            _set_status(group_id, "merged", merge_commit=merge_commit)
            # 0182 NR0003 §5: merged content lives in base — remove the group's
            # worktree, work branch and ledger registration best-effort.
            _cleanup_group_slot(project_id, group_id)
            _emit("git_finalize_done", project_id, group_id, {
                "project": project_id, "group_id": group_id,
                "action": action, "status": "merged", "merge_commit": merge_commit,
                "pushed": wants_push,
            })
            return {
                "ok": True,
                "result": {
                    "action": action, "status": "merged", "merge_commit": merge_commit,
                    "pushed": wants_push, "merge_id": None, "conflict_files": [],
                },
            }

        # Merge failed: conflicts keep MERGE_HEAD and become a session; anything
        # else is rolled back to waiting.
        files_proc = _run_git(["diff", "--name-only", "--diff-filter=U"], cwd=base_root)
        files = [l.strip() for l in (files_proc.stdout or "").splitlines() if l.strip()]
        if not files:
            # 0296 T0004 (NR0003 §5 / R5): one non-conflict failure has a specific,
            # user-fixable cause and used to arrive as a bare 500 — an untracked
            # file sitting in the base checkout on a path the merge wants to
            # create. The E3 guard cannot catch it (that guard is tracked-only, by
            # design), so this is where it must be named. git refuses BEFORE
            # starting the merge here, so `merge --abort` below is a harmless no-op.
            blockers = _untracked_merge_blockers(proc.stderr)
            _run_git(["merge", "--abort"], cwd=base_root)
            _set_status(group_id, "waiting")
            if blockers is not None:
                raise GitServiceError(
                    409, "base_untracked_conflict",
                    "the merge is blocked by uncommitted new files in the base "
                    "checkout; commit or remove them, then retry",
                    details={"files": blockers},
                )
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        merge_id = db_git.create_session(group_id, files, finalize_action=action)
        _set_status(group_id, "conflict", merge_id=merge_id)
        # 0205 L §2.1: DO NOT transfer the lock to the session. The conflict wait
        # is expressed by the persistent 'conflict' state + open session — which
        # the base gate reads — not by an indefinitely-held project mutex (the
        # 0203 tangle's root cause). The finally releases the lock unconditionally,
        # so a later group can provision its worktree while this waits.
        session = db_git.get_session(merge_id)
        _emit("git_merge_conflict", project_id, group_id, {
            "project": project_id, "group_id": group_id,
            "merge_id": merge_id, "conflict_count": len(files),
            "conflict_since": session.get("created_at") if session else None,
        })
        return {
            "ok": True,
            "result": {
                "action": action, "status": "conflict", "merge_commit": None,
                "pushed": False, "merge_id": merge_id, "conflict_files": files,
            },
        }
    finally:
        db_git.release_lock(project_id, holder)


def _finalize_result(
    group_id: str, project_id: str, action: str, status: str, *, pushed: bool = False
) -> dict:
    if status in ("pushed", "merged"):
        _emit("git_finalize_done", project_id, group_id, {
            "project": project_id, "group_id": group_id,
            "action": action, "status": status, "merge_commit": None,
        })
    return {
        "ok": True,
        "result": {
            "action": action, "status": status, "merge_commit": None,
            "pushed": pushed, "merge_id": None, "conflict_files": [],
        },
    }


def _last_line(text: Optional[str]) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else "git command failed"


# git aborts a merge that would clobber an untracked file with:
#   error: The following untracked working tree files would be overwritten by merge:
#           path/one.txt
#           path/two.txt
#   Please move or remove them before you merge.
# ("removed by merge" is the delete-side wording of the same refusal.)
_UNTRACKED_MERGE_RE = re.compile(
    r"untracked working tree files? would be (?:overwritten|removed) by", re.I
)


def _untracked_merge_blockers(stderr: Optional[str]) -> Optional[list[str]]:
    """The untracked paths that made git refuse a merge, or None if that is not
    why it failed (0296 T0004 / NR0003 R5).

    None vs [] is load-bearing: the caller only swaps in the dedicated error code
    when this failure was actually identified, so an unrelated git error keeps its
    honest 500 instead of being mislabelled. A recognized header with no parsable
    file lines still returns [] — the diagnosis holds even if the list does not.
    """
    lines = (stderr or "").splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if not found:
            if _UNTRACKED_MERGE_RE.search(line):
                found = True
            continue
        # git indents the offending paths; the first unindented line ends the block.
        if not line[:1].isspace():
            break
        path = line.strip()
        if path:
            out.append(path)
    return out if found else None


# ── Conflict session: list / resolve / abort (P0005 §6 / L0006 §2.7) ─────────

_CONFLICT_OPEN_RE = re.compile(r"^<{7}( |$)")
_CONFLICT_CLOSE_RE = re.compile(r"^>{7}( |$)")


def has_conflict_markers(content: str) -> bool:
    # A bare "=======" line doubles as a Markdown H1 underline — not checked (L0006 §2.7).
    for line in (content or "").splitlines():
        if _CONFLICT_OPEN_RE.match(line) or _CONFLICT_CLOSE_RE.match(line):
            return True
    return False


def _session_context(group_id: str, merge_id: int) -> tuple[dict, dict, str, Path]:
    session = db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id or session.get("status") != "open":
        raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
    cfg, _state, project_id, base_root, _wt = _finalize_context(group_id)
    return session, cfg, project_id, base_root


def list_conflicts(group_id: str, merge_id: int) -> dict:
    _session, cfg, _project_id, base_root = _session_context(group_id, merge_id)
    db_git.touch_session(merge_id)   # activity → resets the sweep TTL (0205 L §1)
    state = db_git.get_state(group_id) or {}
    files = []
    for row in db_git.session_files(merge_id):
        path = row["path"]
        try:
            content = (base_root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        files.append({
            "path": path,
            "content": content,
            "conflict_count": sum(
                1 for l in content.splitlines() if _CONFLICT_OPEN_RE.match(l)
            ),
        })
    return {
        "ok": True,
        "merge_id": merge_id,
        "branch": state.get("branch"),
        "base_branch": (cfg.get("base_branch") or "main"),
        "files": files,
    }


def resolve_conflicts(group_id: str, merge_id: int, files: list[dict], complete: bool) -> dict:
    from modules.flow_gate.storage.safe_path import resolve_in_root

    session, cfg, project_id, base_root = _session_context(group_id, merge_id)
    db_git.touch_session(merge_id)   # activity → resets the sweep TTL (0205 L §1)
    session_paths = {row["path"] for row in db_git.session_files(merge_id)}

    # Validate EVERYTHING before writing anything (E12 — all-or-nothing).
    staged: list[tuple[str, Path, str]] = []
    for f in files or []:
        path = f.get("path")
        content = f.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise GitServiceError(422, "invalid_request", "each file needs path and content")
        if path not in session_paths:
            raise GitServiceError(
                422, "invalid_request", f"'{path}' is not part of merge session {merge_id}"
            )
        if has_conflict_markers(content):
            line_no = next(
                (i for i, l in enumerate(content.splitlines(), start=1)
                 if _CONFLICT_OPEN_RE.match(l) or _CONFLICT_CLOSE_RE.match(l)),
                1,
            )
            raise GitServiceError(
                422, "conflict_markers_remain",
                f"Conflict markers remain in '{path}' (line {line_no})",
            )
        target = resolve_in_root(base_root, path)
        if target is None:
            raise GitServiceError(422, "invalid_request", f"unsafe path: '{path}'")
        staged.append((path, target, content))

    for path, target, content in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        proc = _run_git(["add", "--", path], cwd=base_root)
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        db_git.mark_file_resolved(merge_id, path)

    remaining = db_git.remaining_conflicts(merge_id)
    if not complete or remaining:
        return {
            "ok": True,
            "result": {
                "status": "conflict", "merge_commit": None, "pushed": False,
                "remaining_conflicts": remaining,
            },
        }

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    # 0232 B0001: finishing a conflicted merge also creates a merge commit over the
    # absorb commit — give it the same conventional Merge subject (not the work
    # subject) so the resolved merge is not a title-duplicate of the work commit.
    state = db_git.get_state(group_id) or {}
    branch = (state.get("branch")
              or worktree_branch_name(project_id, _module_of(group_id), group_id))
    proc = _run_git(
        [*_GIT_IDENT, "commit", "-m", _merge_commit_subject(branch, base_branch)],
        cwd=base_root, author_env=_author_env_from_cfg(cfg),
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(proc.stderr))
    session_action = (session.get("finalize_action") or SESSION_ACTION_DEFAULT)
    pushed = False
    if session_action != "merge_only":
        push = _run_git(
            ["push", "origin", base_branch],
            cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
        )
        if push.returncode != 0:
            # E6 — roll the merge commit back; the session ends and the group re-chooses.
            _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root)
            db_git.close_session(merge_id, "aborted")
            _set_status(group_id, "waiting")
            db_git.release_lock(project_id, f"merge:{merge_id}")
            raise GitServiceError(500, "push_rejected", _last_line(push.stderr))
        pushed = True

    head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
    merge_commit = (head.stdout or "").strip() or None
    db_git.close_session(merge_id, "done")
    _set_status(group_id, "merged", merge_commit=merge_commit)
    # 0182 NR0003 §5: same post-merge cleanup as the conflict-free path — while
    # the merge session still holds the project lock.
    _cleanup_group_slot(project_id, group_id)
    db_git.release_lock(project_id, f"merge:{merge_id}")
    _emit("git_finalize_done", project_id, group_id, {
        "project": project_id, "group_id": group_id,
        "action": session_action, "status": "merged", "merge_commit": merge_commit,
        "pushed": pushed,
    })
    return {
        "ok": True,
        "result": {
            "status": "merged", "merge_commit": merge_commit, "pushed": pushed,
            "remaining_conflicts": [],
        },
    }


# ── Post-finalize slot cleanup (flowgate.default.0182 NR0003 §5) ─────────────
# Before 0182 nothing ever removed a finalized group's leftovers: the worktree
# directory (a full source copy per group), the local work branch ref, and the
# ledger row accumulated forever (delete_config intentionally leaves worktrees
# alone, P0005 §2-3 — that guard is about CONFIG deletion and stays). Cleanup
# now runs best-effort right after a finalize reaches merged/pushed, plus a
# manual backlog sweep for everything that piled up before this landed.

def _is_group_disposed(group_id: str) -> bool:
    """Whether the group has been disposed (terminal DC discard). Lazy import to
    avoid a process_service ↔ git_service import cycle; fail-closed on error so a
    lookup failure never force-deletes a live group's branch."""
    try:
        from modules.flow_gate import process_service
        return bool(process_service.is_group_disposed(group_id))
    except Exception:
        return False


def _abort_disposed_merge_session(project_id: str, group_id: str, base_root: Path) -> None:
    """Abort an in-progress merge for a disposed group and release its merge lock.

    A group discarded mid-conflict still owns an open git_merge_session and holds
    the project lock as ``merge:{merge_id}``. Abort the merge (clears the base
    checkout's MERGE_HEAD/index), close the session, and release that lock so slot
    teardown can proceed. Best-effort; idempotent (no open session → no-op)."""
    try:
        session = db_git.get_open_session_by_group(group_id)
        if session is None:
            return
        if (base_root / ".git" / "MERGE_HEAD").exists():
            _run_git(["merge", "--abort"], cwd=base_root)
        merge_id = session.get("merge_id")
        if merge_id is not None:
            db_git.close_session(int(merge_id), "aborted")
            db_git.release_lock(project_id, f"merge:{merge_id}")
    except Exception:
        _log.warning("disposed merge-session abort failed for %s", group_id, exc_info=True)


def cleanup_disposed_group(project_id: str, group_id: str) -> dict:
    """Tear down a DISPOSED group's git leftovers (worktree dir + local work branch
    + ledger registration). Called right after dispose_group succeeds.

    dispose_group itself never touches git, so without this the discarded group's
    entire source-tree worktree copy, its unmerged local branch, and its ledger row
    all survived — the ledger row also kept the group in the §2 status dropdown as
    an unselectable ghost. Disposal has ALREADY succeeded when we run, so a git
    failure must never surface as an error: everything here is best-effort and
    swallowed. No-op when git integration is off or the group holds no slot."""
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return {"ok": True, "cleaned": False, "reason": "git_disabled"}
        state = db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return {"ok": True, "cleaned": False, "reason": "no_slot"}
        if not git_available():
            return {"ok": True, "cleaned": False, "reason": "git_unavailable"}
        # A conflict/merging slot holds the project lock as merge:{id}; abort +
        # release it BEFORE acquiring our own lock (else _acquire_lock times out).
        project_name = _project_name(project_id)
        if project_name and (state.get("status") or "none") in ("conflict", "merging"):
            base_branch = (cfg.get("base_branch") or "main").strip() or "main"
            _abort_disposed_merge_session(project_id, group_id, src_root(project_name, base_branch))
        holder = f"dispose:{uuid.uuid4()}"
        if not _acquire_lock(project_id, holder):
            return {"ok": False, "cleaned": False, "reason": "git_busy"}
        try:
            cleaned = _cleanup_group_slot(project_id, group_id)
        finally:
            db_git.release_lock(project_id, holder)
        # The slot just left the ledger; nudge clients to re-fetch the group
        # dropdown (the explorer subscribes to git_pending_changed → reload slots).
        if cleaned:
            _emit_pending_changed(project_id, group_id, "none")
        return {"ok": True, "cleaned": cleaned}
    except Exception:
        _log.warning("disposed group cleanup failed for %s", group_id, exc_info=True)
        return {"ok": False, "cleaned": False, "reason": "error"}


def _cleanup_group_slot(
    project_id: str, group_id: str, *, force_discard: bool = False
) -> bool:
    """Best-effort removal of one terminal slot's leftovers. Never raises.

    Removes, in order: the worktree directory (`git worktree remove --force` —
    merged/pushed content already lives in base/origin, and stray build
    artifacts must not park the leftovers forever), the local work branch, a
    pre-0172 leftover origin work branch (merged groups only — a PUSHED branch
    on origin is the user's chosen outcome and is never touched), and finally
    the ledger registration (status/merge_commit stay as history).

    Scope guard, consistent with E7: only a ledger-registered slot that is in a
    terminal status (merged/pushed), belongs to a disposed group, OR is being
    force-discarded (0199 B0001: a no-work group, branch at base) is touched — an
    unregistered directory is never deleted. A disposed or force-discarded work
    branch is force-deleted; for a disposed group its unmerged content is
    intentionally thrown away, and for a no-work group the branch holds no unique
    commit so nothing is lost, and origin was never pushed. The caller must hold
    the project git lock. Returns True when the slot ended up unregistered.

    0287 NR0004: the worktree step is three-way, not two-way. A slot whose
    directory git no longer owns (registration pruned, or the `.git` link
    destroyed by a delete that was interrupted mid-run) is an ORPHAN: `worktree
    remove` rejects it on every attempt, so it is pruned + deleted directly and
    the teardown continues to the branch and the ledger. That does not widen the
    E7 scope — we are past the gates above, so the ledger itself says this path is
    THIS group's slot and the group is terminal/disposed/no-work. An undeterminable
    registration (git unavailable/timed out) still defers rather than deleting.
    """
    try:
        cfg = db_git.get_config(project_id)
        project_name = _project_name(project_id)
        if cfg is None or not cfg.get("enabled") or not project_name:
            return False
        state = db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return False
        status = (state.get("status") or "none")
        # 0192 T0005 §3: a DISPOSED group's slot is a cleanup target regardless of
        # status. dispose_group never touched git, and the ledger gate below was
        # merged/pushed-only, so a discarded group's worktree dir + local work
        # branch + ledger row survived forever (and the stale row kept polluting
        # the §2 dropdown). Its work branch is UNMERGED, so it is force-deleted
        # (-D) — the discarded work is intentionally lost, matching the meaning of
        # disposal.
        disposed = _is_group_disposed(group_id)
        # 0199 B0001: a force-discarded no-work slot is cleaned up exactly like a
        # disposed one — worktree torn down and the (base-tip, no-unique-commit)
        # local work branch force-deleted, with NO merge and NO push.
        if status not in CLEANUP_STATUSES and not disposed and not force_discard:
            return False
        branch = (state.get("branch") or "").strip()
        if not branch:
            return False
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        base_root = src_root(project_name, base_branch)
        if not (base_root / ".git").exists() or not git_available():
            return False
        # A disposed group may still hold an in-progress merge session (conflict/
        # merging): abort it so base checkout's MERGE_HEAD/index are clean before
        # the worktree teardown, and close the ledger session. Idempotent — a no-op
        # once the session is already closed (e.g. cleanup_disposed_group aborted it
        # before taking the lock).
        if disposed and status in ("conflict", "merging"):
            _abort_disposed_merge_session(project_id, group_id, base_root)
        wt_path = src_root(project_name, branch)

        if wt_path.is_dir():
            # 0287 NR0004 §4: this used to be a two-state branch — directory present
            # meant "healthy worktree, call remove". The third state (directory
            # present, git registration missing or destroyed) fell into the remove
            # path, where git rejects it every single time ("is not a working tree"
            # / "validation failed … '.git' does not exist"), and the bare
            # `return False` below then skipped the branch delete AND the ledger
            # unregister — so the slot could never leave this state. Classify first.
            kind = _classify_worktree_dir(base_root, wt_path)
            if kind == "live":
                proc = _run_git(
                    ["worktree", "remove", "--force", str(wt_path)],
                    cwd=base_root, timeout=GIT_WORKTREE_RM_TIMEOUT_SEC,
                )
                if proc.returncode != 0 or wt_path.exists():
                    # A remove that fails HALFWAY leaves an orphan behind (that is
                    # how B/C above are created), so re-classify instead of giving
                    # up: if git no longer owns the path, finish the job ourselves.
                    kind = _classify_worktree_dir(base_root, wt_path)
                    if kind == "live":
                        # Still a genuine registered worktree — something outside
                        # our control blocked it (file lock, permissions). Preserve
                        # the ledger row so a later sweep retries, as before.
                        _log.warning(
                            "worktree remove failed for %s (rc=%s, still registered): %s",
                            group_id, proc.returncode, _last_line(proc.stderr),
                        )
                        return False
                    _log.warning(
                        "worktree remove for %s left an orphan directory (rc=%s: %s) — "
                        "reclaiming it directly",
                        group_id, proc.returncode, _last_line(proc.stderr),
                    )
            if kind == "unknown":
                # git could not tell us whether the path is registered. Deleting a
                # possibly-live worktree is the one irreversible mistake here, so
                # stay conservative and let the next sweep retry.
                _log.warning(
                    "worktree registration for %s is undeterminable — cleanup deferred",
                    group_id,
                )
                return False
            if kind == "orphan" and wt_path.exists():
                # Orphan: git refuses to own this path, so `worktree remove` can
                # never clear it. Drop the stale bookkeeping, then delete the
                # directory ourselves and CONTINUE to the branch/ledger teardown.
                _run_git(["worktree", "prune"], cwd=base_root)
                if not _force_rmtree(wt_path):
                    _log.warning(
                        "orphan worktree directory for %s could not be removed: %s",
                        group_id, wt_path,
                    )
                    return False
                _log.info("orphan worktree directory reclaimed for %s: %s", group_id, wt_path)
        else:
            # Directory already gone (manual removal) — just drop the stale
            # worktree bookkeeping so the branch delete below can proceed.
            _run_git(["worktree", "prune"], cwd=base_root)

        if _ref_exists(base_root, f"refs/heads/{branch}"):
            if disposed or force_discard:
                # disposed: unmerged work intentionally thrown away.
                # force_discard (0199): branch sits at base tip with no unique
                # commit, so -D loses nothing; origin was never pushed, so no
                # origin ref to retro-delete below. Force-delete (`-d` refuses).
                proc = _run_git(["branch", "-D", branch], cwd=base_root)
            elif status == "merged":
                proc = _run_git(["branch", "-d", branch], cwd=base_root)
            elif _ref_exists(base_root, f"refs/remotes/origin/{branch}"):
                # pushed: origin retains the content, the local ref is disposable.
                proc = _run_git(["branch", "-D", branch], cwd=base_root)
            else:
                proc = None  # pushed but no origin ref visible — keep the local ref
            if proc is not None and proc.returncode != 0:
                _log.warning(
                    "branch delete failed for %s: %s", group_id, _last_line(proc.stderr)
                )

        if status == "merged" and _ref_exists(base_root, f"refs/remotes/origin/{branch}"):
            # Work branches pushed before the 0172 fix were never meant to be
            # published; retro-delete best-effort (failure is not a cleanup failure).
            _run_git(
                ["push", "origin", "--delete", branch],
                cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
                username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
            )

        db_git.unregister_worktree(group_id)
        return True
    except Exception:
        _log.warning("slot cleanup failed for %s", group_id, exc_info=True)
        return False


def cleanup_terminal_slots(project_id: str) -> dict:
    """POST …/projects/{id}/git/cleanup — backlog sweep of every registered
    slot already finalized (merged/pushed) OR belonging to a disposed group.
    Covers groups finalized/discarded before the per-finalize / per-dispose
    cleanup existed, and any slot whose immediate cleanup failed. (0192 T0005 §3
    adds the disposed backlog: one sweep clears every ghost slot left by a group
    that was discarded before dispose learned to touch git.)"""
    _require_enabled_config(project_id)
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    cleaned: list[str] = []
    failed: list[str] = []
    try:
        for row in db_git.list_states_of_project(project_id):
            gid = row["group_id"]
            terminal = (row.get("status") or "none") in CLEANUP_STATUSES
            if not terminal and not _is_group_disposed(gid):
                continue
            (cleaned if _cleanup_group_slot(project_id, gid) else failed).append(gid)
    finally:
        db_git.release_lock(project_id, holder)
    return {"ok": True, "result": {"cleaned": cleaned, "failed": failed}}


def abort_merge(group_id: str, merge_id: int) -> dict:
    """Manual [보류] — abort the merge, preserve the work branch, reopen re-merge
    (0205 P scenario 9). Shares its end state with the auto-recovery sweep; only
    the trigger differs. The merge:{id} release is now best-effort legacy cleanup
    (0205 §2.1 stopped holding that lock). _set_status already broadcasts
    git_pending_changed so the badge clears immediately (0184 lesson)."""
    _session, _cfg, project_id, base_root = _session_context(group_id, merge_id)
    _run_git(["merge", "--abort"], cwd=base_root)
    db_git.close_session(merge_id, "aborted")
    _set_status(group_id, "waiting")
    db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
    return {"ok": True, "result": {"status": "waiting", "branch_preserved": True}}


# ── Auto-recovery sweep (flowgate.default.0205 P scenario 6 / L §2.5) ─────────

def _ttl_expired(last: Optional[str]) -> bool:
    """Whether an activity timestamp is older than MERGE_SESSION_TTL_HOURS."""
    if not last:
        return False   # unknown activity → never auto-abort on this basis
    try:
        from datetime import datetime, timedelta, timezone

        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt >= timedelta(hours=MERGE_SESSION_TTL_HOURS)
    except Exception:
        return False


def _emit_auto_aborted(project_id: str, group_id: str, merge_id: int, reason: str) -> None:
    state = db_git.get_state(group_id) or {}
    _emit("git_merge_auto_aborted", project_id, group_id, {
        "project": project_id, "group_id": group_id, "merge_id": merge_id,
        "reason": reason, "branch": state.get("branch"), "branch_preserved": True,
    })


def _close_orphan(session: dict, project_id: str) -> None:
    """A session whose base checkout has no MERGE_HEAD — the merge is gone from
    disk (manual cleanup / crash). Close it and return the group to 'waiting'
    (0205 L §2.5). branch_preserved: the work branch is untouched."""
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    db_git.close_session(merge_id, "aborted")
    _set_status(group_id, "waiting")
    db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
    _emit_auto_aborted(project_id, group_id, merge_id, "orphan_recovered")


def _auto_abort_session(
    session: dict, project_id: str, base_root: Path, reason: str
) -> None:
    """Reclaim an abandoned conflict session: git merge --abort (work branch
    preserved), close it, return the group to 'waiting' (0205 L §2.5).

    Takes a short sweep lock; if the project is busy it simply retries next cycle.
    If merge --abort fails (e.g. it collides with unrelated local base changes)
    the session is LEFT intact — a forced reset is never issued, protecting a base
    checkout that has other groups' work mixed in (the exact 0203 accident)."""
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    holder = f"sweep:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        return   # another git op in progress — try again next cycle
    try:
        if (base_root / ".git" / "MERGE_HEAD").exists():
            proc = _run_git(["merge", "--abort"], cwd=base_root)
            if proc.returncode != 0:
                _log.warning(
                    "sweep merge --abort failed for %s (%s) — left intact",
                    group_id, _last_line(proc.stderr),
                )
                return   # never force-reset (L §5)
        db_git.close_session(merge_id, "aborted")
        _set_status(group_id, "waiting")
        db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
        _emit_auto_aborted(project_id, group_id, merge_id, reason)
    finally:
        db_git.release_lock(project_id, holder)


def merge_session_sweep() -> None:
    """Auto-recover abandoned / orphaned conflict sessions (0205 L §2.5).

    For each open session: skip if the base checkout is gone (never guess);
    close it as an orphan if the merge left no MERGE_HEAD on disk; auto-abort it
    if it has been quiet past the TTL; otherwise leave it. Best-effort and fully
    isolated per session so one bad row cannot sink the pass."""
    try:
        sessions = db_git.list_open_sessions()
    except Exception:
        _log.info("merge session sweep skipped (session table unavailable)", exc_info=True)
        return
    for session in sessions:
        try:
            group_id = session["group_id"]
            project_id = _project_of_group(group_id)
            base_root = _base_root_of(project_id)
            if base_root is None or not (base_root / ".git").exists():
                continue   # checkout gone — do not touch (log only)
            if not (base_root / ".git" / "MERGE_HEAD").exists():
                _close_orphan(session, project_id)
                continue
            last = session.get("touched_at") or session.get("created_at")
            if not _ttl_expired(last):
                continue
            _auto_abort_session(session, project_id, base_root, "ttl_expired")
        except Exception:
            _log.warning(
                "merge session sweep failed for merge %s", session.get("merge_id"),
                exc_info=True,
            )


_sweep_daemon_started = False


def _start_sweep_daemon() -> None:
    """Launch the periodic sweep loop once (0205 L §2.6). Idempotent."""
    global _sweep_daemon_started
    if _sweep_daemon_started:
        return
    _sweep_daemon_started = True
    import threading

    def _loop() -> None:
        while True:
            time.sleep(SWEEP_INTERVAL_MIN * 60)
            try:
                merge_session_sweep()
            except Exception:
                _log.warning("periodic merge session sweep failed", exc_info=True)

    threading.Thread(target=_loop, name="git-merge-sweep", daemon=True).start()


# ── Boot recovery (flowgate.default.0205 P scenario 7 / L §2.6) ───────────────

def startup_recovery() -> None:
    """Heal conflict sessions, drop every stale lock, then sweep + start the
    daemon at boot (0205 L §2.6).

    A live MERGE_HEAD session is left in 'conflict' (state re-affirmed) but its
    lock is NOT re-acquired — the base is protected by the state gate, not a mutex
    (0205 §2.1). Sessions with no MERGE_HEAD are auto-aborted (orphan recovery).
    Any surviving lock is stale by definition (nothing legitimately outlives a
    restart) — op:/sweep:/merge:/dispose: locks are all force-released. Finally a
    sweep reclaims TTL-expired sessions and the daemon repeats it periodically."""
    try:
        for session in db_git.list_open_sessions():
            merge_id = session["merge_id"]
            group_id = session["group_id"]
            try:
                project_id = _project_of_group(group_id)
                base_root = _base_root_of(project_id)
                merge_head_exists = bool(
                    base_root and (base_root / ".git" / "MERGE_HEAD").exists()
                )
                if merge_head_exists:
                    # Re-affirm the status; do NOT reclaim a merge:{id} lock (§2.6).
                    _set_status(group_id, "conflict", merge_id=merge_id)
                else:
                    _close_orphan(session, project_id)
            except Exception:
                _log.warning("git session recovery failed for merge %s", merge_id, exc_info=True)
        # One-time lock cleanup: no lock legitimately survives a restart. This
        # includes the legacy merge:{id} inheritance lock (§2.6).
        for lock in db_git.list_locks():
            holder = str(lock.get("holder") or "")
            if holder.startswith(("op:", "sweep:", "merge:", "dispose:")):
                db_git.force_release_lock(lock["project_id"])
        merge_session_sweep()   # reclaim anything already past TTL
        _start_sweep_daemon()
    except Exception:
        # Table may not exist yet (pre-migration boot) — recovery is best-effort.
        _log.info("git startup recovery skipped", exc_info=True)


# ── Project git status aggregation (flowgate.default.0162 P §2 / L §2.2) ──────

def _base_ahead_behind(
    base_root: Optional[Path], base_branch: str
) -> tuple[Optional[int], Optional[int]]:
    """(ahead, behind) of the base checkout vs origin/{base}, from the last
    fetch — no network git (P §2-1). Both None when origin/{base} is absent
    (never fetched), git is unavailable, or the base checkout is missing:
    "unmeasured" is distinct from "in sync" (L §5)."""
    if base_root is None or not git_available():
        return None, None
    if not (base_root / ".git").exists():
        return None, None
    if not _ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        return None, None
    proc = _run_git(
        ["rev-list", "--left-right", "--count", f"origin/{base_branch}...{base_branch}"],
        cwd=base_root,
    )
    if proc.returncode != 0:
        return None, None
    m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", proc.stdout or "")
    if not m:
        return None, None
    behind, ahead = int(m.group(1)), int(m.group(2))
    return ahead, behind


def _short_head(repo: Path) -> Optional[str]:
    proc = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo)
    return (proc.stdout or "").strip() or None if proc.returncode == 0 else None


def _rev_parse(repo: Path, rev: str, *, short: bool = False) -> Optional[str]:
    args = ["rev-parse"]
    if short:
        args.append("--short")
    args.append(rev)
    proc = _run_git(args, cwd=repo)
    return (proc.stdout or "").strip() or None if proc.returncode == 0 else None


def _full_sha_matches(full_sha: str, candidate: str) -> bool:
    full = (full_sha or "").lower()
    cand = (candidate or "").lower()
    return bool(full and cand and (full.startswith(cand) or cand.startswith(full)))


def _unpushed_commits(base_root: Optional[Path], base_branch: str) -> Optional[list[dict]]:
    if base_root is None or not git_available() or not (base_root / ".git").exists():
        return None
    if not _ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        return None
    proc = _run_git(
        [
            "log", "--first-parent", f"origin/{base_branch}..{base_branch}",
            "--format=%H%x1f%P%x1f%cI%x1f%s",
        ],
        cwd=base_root,
    )
    if proc.returncode != 0:
        return None
    commits: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        full_sha, parents, committed_at, subject = parts
        parent_list = [p for p in parents.split() if p]
        commits.append({
            "full_sha": full_sha,
            "parents": parent_list,
            "committed_at": committed_at,
            "subject": subject,
        })
    return commits


def _remote_base_missing(base_root: Optional[Path], base_branch: str) -> bool:
    """True only when the base checkout is healthy and refs/remotes/origin/{base}
    is absent — the remote has no base branch yet (0297 B0001 bootstrap).

    Deliberately narrower than "unmeasured": git being unavailable or the checkout
    missing reads False, so a consumer can never mistake those for "the remote is
    empty, offer the first push"."""
    if base_root is None or not git_available() or not (base_root / ".git").exists():
        return False
    return not _ref_exists(base_root, f"refs/remotes/origin/{base_branch}")


def _local_commit_count(base_root: Optional[Path]) -> Optional[int]:
    """Commits reachable from the base checkout's HEAD, or None when it cannot be
    counted (git off, no checkout, unborn HEAD). Lets the client tell "nothing to
    push yet" apart from "one snapshot commit waiting for its first push"."""
    if base_root is None or not git_available() or not (base_root / ".git").exists():
        return None
    proc = _run_git(["rev-list", "--count", "HEAD"], cwd=base_root)
    if proc.returncode != 0:
        return None
    txt = (proc.stdout or "").strip()
    return int(txt) if txt.isdigit() else None


def _ledger_group_by_merge_sha(project_id: str, full_sha: str) -> Optional[str]:
    matches: list[str] = []
    for row in db_git.list_states_of_project_any(project_id):
        if row.get("status") != "merged" or not row.get("merge_commit"):
            continue
        if full_sha.lower().startswith(str(row["merge_commit"]).lower()):
            matches.append(row["group_id"])
    return matches[0] if len(matches) == 1 else None


def _build_unpushed(
    project_id: str,
    base_root: Optional[Path],
    base_branch: str,
    commit_count: Optional[int] = None,
) -> dict:
    commits = _unpushed_commits(base_root, base_branch)
    if commits is None:
        # 0297 B0001: an unmeasured result used to be indistinguishable from "in
        # sync" downstream (commit_count 0), which hid the ONLY push entry point
        # while the remote was still empty. These two fields carry the bootstrap
        # case explicitly so the client decides instead of guessing.
        return {
            "count": 0, "commit_count": 0, "merges": [], "measured": False,
            "remote_branch_missing": _remote_base_missing(base_root, base_branch),
            "local_commit_count": _local_commit_count(base_root),
        }
    merge_commits = [c for c in commits if len(c["parents"]) >= 2]
    merges: list[dict] = []
    top_sha = commits[0]["full_sha"] if commits else None
    for c in merge_commits:
        group_id = _ledger_group_by_merge_sha(project_id, c["full_sha"])
        is_top = bool(top_sha and c["full_sha"] == top_sha)
        can_unmerge = is_top and group_id is not None
        if can_unmerge:
            blocked_reason = None
        elif group_id is None:
            blocked_reason = "unmapped"
        else:
            blocked_reason = "not_top"
        merges.append({
            "merge_commit": c["full_sha"][:7],
            "group_id": group_id,
            "subject": c["subject"],
            "merged_at": c["committed_at"],
            "can_unmerge": can_unmerge,
            "blocked_reason": blocked_reason,
        })
    return {
        "count": len(merges),
        "commit_count": commit_count if commit_count is not None else len(commits),
        "merges": merges,
        "measured": True,
        # Measured implies origin/{base} exists; keep the shape stable so the
        # client can read both fields unconditionally.
        "remote_branch_missing": False,
        "local_commit_count": None,
    }


def project_git_status(project_id: str) -> dict:
    """GET …/projects/{id}/git/status — status + finalize-pending list + count.

    Local repository only (no network git). Realizes the lazy none→
    awaiting_choice transition for wf_done groups at aggregation time (L §2.2).
    """
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": True, "status": {
            "enabled": False, "base_branch": None, "base_path_state": "empty",
            "ahead_count": None, "behind_count": None,
            "slots": [], "pending": [], "pending_count": 0, "cleanable_count": 0,
        }}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    default_action = cfg.get("default_finalize_action") or "wait"
    project_name = _project_name(project_id)
    base_root = src_root(project_name, base_branch) if project_name else None

    # 0282 NR0003 발견 1: one ledger scan serves both the registered-slot
    # aggregation and the provision-failure surface below (previously two
    # near-identical project scans), and the per-slot wf_done probe is batched
    # into a single IN query so the loop only does set membership.
    all_rows = db_git.list_states_of_project_any(project_id)
    rows = [r for r in all_rows if r.get("worktree_registered")]
    wf_done_groups = _groups_root_wf_done(
        [r["group_id"] for r in rows if (r.get("status") or "none") == "none"]
    )
    for row in rows:
        if (row.get("status") or "none") == "none" and row["group_id"] in wf_done_groups:
            try:
                # 0199 B0001: proven no-work groups are discarded (torn down, no
                # merge/push) here; real groups still transition to awaiting_choice.
                # A discarded group's slot is unregistered by the cleanup, so it
                # drops out of every list below (SLOT/PENDING/CLEANUP filters).
                row["status"] = _decide_pending_transition(
                    project_id, cfg, row, row["group_id"]
                )
            except Exception:
                # One broken group must not sink the whole aggregation
                # (0115 batch-fetch exception-isolation lesson, L §5).
                _log.warning(
                    "lazy git transition failed for %s", row.get("group_id"), exc_info=True
                )

    # 0327 T0004 (B0001): `writable` tells the file explorer whether this slot's
    # worktree is really there, so a working group can offer create/upload instead
    # of the blanket read-only it applied to every selected group. `rows` is already
    # filtered to worktree_registered=1, so this only re-checks the on-disk side
    # (directory present, .git link intact) — a handful of stats per status call.
    slots = [
        {"group_id": r["group_id"], "branch": r.get("branch"),
         "status": r.get("status"), "merge_id": r.get("merge_id"),
         "writable": group_worktree_writable(project_id, r["group_id"])}
        for r in rows if r.get("status") in SLOT_STATUSES
    ]
    pending_rows = [r for r in rows if r.get("status") in PENDING_STATUSES]
    # 0282 NR0003 발견 1: the AC lookup was the next N+1 in line — batched
    # before pending grows with adoption.
    ac_doc_ids = _group_ac_doc_ids([r["group_id"] for r in pending_rows])
    pending = [
        {"group_id": r["group_id"], "branch": r.get("branch"),
         "status": r.get("status"), "default_action": default_action,
         # 0165 T0004: merge_id lets the header panel resolve conflicts inline
         # (no need to open the group's R document / GitFinalizePanel).
         "merge_id": r.get("merge_id"),
         # 0182 NR0003 §4: pending implies the workflow root is wf_done, so the
         # header [open] button targets the AC document (which hosts the git
         # finalize UI since §3) instead of detouring through the R root.
         "ac_doc_id": ac_doc_ids.get(r["group_id"])}
        for r in pending_rows
    ]
    # 0205 P scenario 8: annotate conflict pending rows with how long they have
    # been unresolved (elapsed = now − conflict_since), so the panel can surface
    # the wait time and offer [해소 재개]/[보류]. Other rows carry no field.
    for row in pending:
        if row.get("status") == "conflict" and row.get("merge_id") is not None:
            try:
                s = db_git.get_session(int(row["merge_id"]))
                row["conflict_since"] = s.get("created_at") if s else None
            except Exception:
                row["conflict_since"] = None
    # 0205 P scenario 8: persisted worktree provisioning failures (unregistered
    # rows with a provision_error) so a slot-less group's "깃 미추적" warning
    # survives the one-shot SSE. Disposed groups are excluded. Newest first.
    provision_failures: list[dict] = []
    try:
        for r in all_rows:
            if (
                r.get("provision_error")
                and not r.get("worktree_registered")
                and not _is_group_disposed(r["group_id"])
            ):
                provision_failures.append({
                    "group_id": r["group_id"],
                    "error": r.get("provision_error"),
                    "failed_at": r.get("provision_failed_at"),
                })
        provision_failures.sort(key=lambda x: x.get("failed_at") or "", reverse=True)
    except Exception:
        _log.warning("provision_failures aggregation failed for %s", project_id, exc_info=True)
        provision_failures = []
    # 0182 NR0003 §5: registered slots already finalized (merged/pushed) are
    # cleanup backlog — surfaced so the panel can offer the [clean up] action.
    cleanable_count = sum(1 for r in rows if r.get("status") in CLEANUP_STATUSES)
    ahead, behind = _base_ahead_behind(base_root, base_branch)
    base_path_state = _judge_base_slot(base_root, base_branch) if base_root else "occupied"
    # 0177 L0002 §2.1: base-checkout dirty set (tracked files only) so the header
    # panel can offer commit/revert BEFORE a merge bounces off the E3 guard.
    # Never-raise, matching base_checkout_dirty_status: a missing checkout or any
    # git failure reads as clean — the field is advisory display state.
    # 0296 T0004 (NR0003 R1): the untracked set rides alongside in its OWN field.
    # It must never fold into base_dirty — that would widen the E3 guard to build
    # artifacts, the exact regression 0165.0009 fixed. It exists so the panel can
    # say "N new files are not in any group worktree yet" and offer the commit.
    base_readable = (
        base_root is not None and (base_root / ".git").exists() and git_available()
    )
    try:
        base_dirty_files = _dirty_files(base_root, include_untracked=False) if base_readable else []
    except Exception:
        _log.warning("base_dirty aggregation failed for %s", project_id, exc_info=True)
        base_dirty_files = []
    try:
        base_untracked_files = _untracked_files(base_root) if base_readable else []
    except Exception:
        _log.warning("base_untracked aggregation failed for %s", project_id, exc_info=True)
        base_untracked_files = []
    unpushed = _build_unpushed(project_id, base_root, base_branch, ahead)
    return {"ok": True, "status": {
        "enabled": True, "base_branch": base_branch,
        "base_path_state": base_path_state,
        "ahead_count": ahead, "behind_count": behind,
        "base_dirty": {"dirty": bool(base_dirty_files), "files": base_dirty_files},
        "base_untracked": {
            "count": len(base_untracked_files),
            "files": base_untracked_files,
            "truncated": len(base_untracked_files) >= UNTRACKED_LIST_MAX,
        },
        "slots": slots, "pending": pending, "pending_count": len(pending),
        "cleanable_count": cleanable_count,
        "provision_failures": provision_failures,
        "unpushed": unpushed,
    }}


# ── Manual recovery operations (flowgate.default.0162 P §3 / L §2.4) ──────────

def _require_enabled_config(project_id: str) -> dict:
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(
            409, "invalid_state", f"git integration is not enabled for project '{project_id}'"
        )
    return cfg


def manual_fetch(project_id: str) -> dict:
    """POST …/projects/{id}/git/fetch — recovery fetch of the base checkout."""
    cfg = _require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _project_name(project_id)
    base_root = src_root(project_name, base_branch) if project_name else None
    if base_root is None or _judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(
            409, "invalid_state", "base checkout is not available for fetch"
        )
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        proc = _run_git(
            ["fetch", "origin"],
            cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        # 0320 B0001: a bare `fetch` only moved refs/remotes/origin/{base} and then
        # *reported* behind_count — the local base branch never advanced, so the
        # base checkout stayed behind upstream forever and the operator-facing
        # "Fetch" action was a no-op recovery ("영원히 안가져올건가?"). Finalize was
        # the ONLY path that ran `merge --ff-only origin/{base}` (see finalize). Do
        # the same fast-forward here whenever it is safe: base is clean and can be
        # fast-forwarded. A dirty base is left untouched (never force the server's
        # own checkout), and a genuine divergence (local-only commits) simply fails
        # the ff-only and is reported as behind/ahead — that stays the E4
        # base_diverged condition finalize already owns, not this recovery's job.
        advanced = False
        if (
            _ref_exists(base_root, f"refs/remotes/origin/{base_branch}")
            and not _dirty(base_root, include_untracked=False)
        ):
            ff = _run_git(
                ["merge", "--ff-only", f"origin/{base_branch}"], cwd=base_root
            )
            advanced = ff.returncode == 0
        ahead, behind = _base_ahead_behind(base_root, base_branch)
        return {"ok": True, "result": {
            "fetched": True, "advanced": advanced, "base_branch": base_branch,
            "ahead_count": ahead, "behind_count": behind,
        }}
    finally:
        db_git.release_lock(project_id, holder)


def manual_push(project_id: str, branch: Optional[str]) -> dict:
    """POST …/projects/{id}/git/push — recovery re-push of an accumulated branch.

    ``branch`` must EXACTLY match the base branch or one of this project's
    registered slot branches (no prefix/pattern matching, L §2.4). Terminal
    (merged/pushed) slots are allowed — a re-push is a recovery operation."""
    cfg = _require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branch = (branch or base_branch).strip() or base_branch
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")

    allowed = {base_branch}
    for r in db_git.list_states_of_project(project_id):
        if r.get("branch"):
            allowed.add(r["branch"])
    if branch not in allowed:
        raise GitServiceError(
            422, "invalid_request",
            f"branch '{branch}' is not the base branch or a group slot of project '{project_id}'",
        )
    cwd = src_root(project_name, branch)
    if not cwd.is_dir():
        raise GitServiceError(
            409, "invalid_state", f"checkout for branch '{branch}' is missing"
        )
    # 0205 §2.2: pushing the BASE branch publishes the shared base checkout —
    # gate it while a conflict session holds base. A work-branch re-push does not
    # touch base and is never gated.
    pushes_base = branch == base_branch
    if pushes_base:
        guard_base_free(project_id)   # 1st gate (before lock)
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        if pushes_base:
            guard_base_free(project_id)   # 2nd gate (race close, after lock)
        proc = _run_git(
            ["push", "origin", branch],
            cwd=cwd, timeout=GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "push_rejected", _last_line(proc.stderr))
        ahead, behind = _base_ahead_behind(cwd, base_branch) if pushes_base else (None, None)
        if pushes_base:
            _emit_pending_changed(project_id, None, None)
        return {"ok": True, "result": {
            "pushed": True, "branch": branch,
            "ahead_count": ahead, "behind_count": behind,
        }}
    finally:
        db_git.release_lock(project_id, holder)


def unmerge(group_id: str, merge_commit: str) -> dict:
    """Undo the latest unpushed merge for a group and re-open its worktree."""
    req_sha = (merge_commit or "").strip().lower()
    if not UNMERGE_SHA_RE.match(req_sha):
        raise GitServiceError(
            422, "invalid_request",
            "merge_commit must be a 7 to 40 character hexadecimal sha prefix.",
        )
    project_id = _project_of_group(group_id)
    cfg = _require_enabled_config(project_id)
    state = db_git.get_state(group_id)
    ledger_sha = str((state or {}).get("merge_commit") or "").lower()
    if (
        state is None
        or (state.get("status") or "none") != "merged"
        or not ledger_sha
        or _is_group_disposed(group_id)
    ):
        raise GitServiceError(409, "invalid_state", "group is not an unmergeable merged group")
    if not _full_sha_matches(req_sha, ledger_sha):
        raise GitServiceError(409, "stale_target", "requested merge commit does not match this group")

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_root = src_root(project_name, base_branch)
    if _judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )

    guard_base_free(project_id)
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        guard_base_free(project_id)
        commits = _unpushed_commits(base_root, base_branch)
        if commits is None:
            raise GitServiceError(409, "invalid_state", "unpushed base history is not measurable")
        if not commits:
            raise GitServiceError(409, "already_pushed", "merge commit is no longer unpushed")

        top = commits[0]
        target_in_chain = any(_full_sha_matches(c["full_sha"], ledger_sha) for c in commits)
        if not _full_sha_matches(top["full_sha"], ledger_sha):
            if target_in_chain:
                raise GitServiceError(
                    409, "not_top_merge",
                    "a newer unpushed commit blocks unmerge",
                    details={
                        "top_merge_commit": top["full_sha"][:7],
                        "top_group_id": _ledger_group_by_merge_sha(project_id, top["full_sha"]),
                    },
                )
            raise GitServiceError(409, "already_pushed", "merge commit is no longer unpushed")
        if len(top["parents"]) < 2 or not _full_sha_matches(top["full_sha"], req_sha):
            raise GitServiceError(
                409, "stale_target",
                "requested merge commit is no longer the current top merge",
                details={"current_top": top["full_sha"][:7]},
            )

        branch = (state.get("branch") or worktree_branch_name(project_id, _module_of(group_id), group_id)).strip()
        restored_head = _rev_parse(base_root, f"{top['full_sha']}^2")
        if not restored_head:
            raise GitServiceError(500, "git_error", "cannot resolve merged work branch head")
        if _ref_exists(base_root, f"refs/heads/{branch}"):
            current = _rev_parse(base_root, f"refs/heads/{branch}")
            if current != restored_head:
                raise GitServiceError(
                    500, "git_error",
                    f"local branch '{branch}' exists at an unexpected commit",
                )
        else:
            proc = _run_git(["branch", branch, f"{top['full_sha']}^2"], cwd=base_root)
            if proc.returncode != 0:
                raise GitServiceError(500, "git_error", _last_line(proc.stderr))

        _set_status(group_id, "awaiting_choice")
        reset = _run_git(["reset", "--hard", f"{top['full_sha']}^1"], cwd=base_root)
        if reset.returncode != 0:
            _set_status(group_id, "merged", merge_commit=ledger_sha)
            raise GitServiceError(500, "git_error", _last_line(reset.stderr))
        base_head = _short_head(base_root)
    finally:
        db_git.release_lock(project_id, holder)

    reprovision_result = ensure_worktree(project_id, _module_of(group_id), group_id, trigger="unmerge")
    return {"ok": True, "result": {
        "unmerged": True,
        "merge_commit": top["full_sha"][:7],
        "base_head": base_head,
        "group_status": "awaiting_choice",
        "reprovisioned": reprovision_result == "ok",
    }}


# ── Base-checkout explicit commit / revert (flowgate.default.0177 — L0002) ────
# A src-content save writes straight into the base checkout (an admin edit),
# leaving it dirty and tripping the E3 merge guard for EVERY group of the
# project. These operations are the sanctioned way OUT of that state: an
# explicit, visible commit onto the base branch (no push — the next merge
# finalize carries it), or a per-file restore to HEAD. Scope always matches the
# E3 guard exactly: tracked-file changes only.

def default_base_commit_message(files: list[str]) -> str:
    """Deterministic default subject for a base-checkout commit (L0002 §2.2).

    "fix: a.py, b.py"; when the joined list overflows COMMIT_SUBJECT_MAX the
    abbreviated "fix: a.py 외 N건" is used (hard-cut as a last resort so the
    result is always a valid subject). The FE seeds its input with the same
    rule, so either side may materialize the message with identical output.
    """
    subject = BASE_COMMIT_MSG_PREFIX + BASE_COMMIT_MSG_JOINER.join(files)
    if len(subject) <= COMMIT_SUBJECT_MAX:
        return subject
    subject = f"{BASE_COMMIT_MSG_PREFIX}{files[0]} 외 {len(files) - 1}건"
    return subject[:COMMIT_SUBJECT_MAX]


def _merge_in_progress(base_root: Path) -> bool:
    """True while a conflict session holds the base checkout mid-merge — commit
    and revert must not touch that intermediate state (resolve/abort only)."""
    return (base_root / ".git" / "MERGE_HEAD").exists()


def _require_base_checkout(project_id: str) -> tuple[dict, Path]:
    """(cfg, base_root) for base-commit/-revert, or 404/409 per the shared rules."""
    cfg = _require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _project_name(project_id)
    base_root = src_root(project_name, base_branch) if project_name else None
    if base_root is None or _judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(
            409, "invalid_state", "base checkout is not available"
        )
    return cfg, base_root


def base_commit(
    project_id: str, message: Optional[str], paths: Optional[list[str]] = None
) -> dict:
    """POST …/projects/{id}/git/base-commit — commit the base checkout (L0002 §2.3).

    Two modes, and the distinction is the whole point of 0296 T0004:

    * `paths` omitted — unchanged legacy behaviour: commit ALL dirty **tracked**
      files via `add -u`. Untracked build artifacts are never swept in; this is
      the E3/_dirty_files scope and 0165.0009 depends on it staying that way.
    * `paths` given — commit exactly those paths via `add -- <paths>`, and they
      MAY be untracked. This is the missing exit hatch from NR
      flowgate.default.0296.0003 §C3: a group worktree is checked out from a
      commit (§C1), so a file that was never committed is invisible to every
      worker — yet the only in-app commit affordance refused to stage it, leaving
      "commit it and it appears" true but impossible without a terminal.

    `add -A` is deliberately NOT an option in either mode: it would drag
    `__pycache__`/`.pytest_cache` into base history and undo the 0165.0009 scope
    decision. Only paths the operator explicitly picked are staged.

    No push: the local commit rides on the next merge finalize's base push
    (ff-only against origin stays a no-op while origin/base remains an ancestor).
    An empty dirty set is an idempotent success so the FE's commit-then-merge
    retry never turns a lost race into an error.
    """
    _, base_root = _require_base_checkout(project_id)
    subject = normalize_subject(message)
    if len(subject) > COMMIT_SUBJECT_MAX:
        raise GitServiceError(
            422, "invalid_request",
            "message must be a single line of at most 200 characters.",
        )
    # Normalize + validate BEFORE the lock (a 422 must have no side effects),
    # mirroring base_revert: nothing may reach outside the base checkout.
    selected: list[str] = []
    for raw in (paths or []):
        p = str(raw or "").strip().replace("\\", "/")
        if not p:
            continue
        if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
            raise GitServiceError(422, "invalid_request", f"invalid path: {raw!r}")
        if p not in selected:
            selected.append(p)
    guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
        if _merge_in_progress(base_root):
            raise GitServiceError(
                409, "invalid_state", "a merge is in progress; resolve or abort it first"
            )
        tracked = _dirty_files(base_root, include_untracked=False)
        if selected:
            # Explicit selection: accept anything git currently reports as a
            # pending change — tracked edits/deletions AND untracked new files.
            # An unbounded scan here (limit=0) is right: this is a one-shot
            # user-initiated commit, not a status poll, and silently refusing a
            # file only because it fell past the display cap would be a bug.
            untracked = set(_untracked_files(base_root, limit=0))
            allowed = set(tracked) | untracked
            unknown = [p for p in selected if p not in allowed]
            if unknown:
                # `.gitignore` first: "not a pending change" would be a lie for an
                # ignored file that plainly exists on disk. It cannot be committed
                # at all (NR §C4) — say so, and never force with `add -f`.
                ignored = _ignored_paths(base_root, unknown)
                if ignored:
                    raise GitServiceError(
                        422, "path_ignored",
                        "these paths are excluded by .gitignore and cannot be committed",
                        details={"files": ignored},
                    )
                raise GitServiceError(
                    422, "invalid_request",
                    "these paths have no pending change to commit",
                    details={"files": unknown},
                )
            files = selected
        else:
            files = tracked
        if not files:
            return {"ok": True, "result": {
                "committed": False, "commit": None, "subject": None,
                "files": [], "remaining": [],
                "remaining_untracked": _untracked_files(base_root),
            }}
        if not subject:
            subject = default_base_commit_message(files)
        if selected:
            # Literal argv pathspecs (no shell, no globbing) matching the unquoted
            # porcelain form the two listers produced — same contract as
            # base_revert's `checkout HEAD -- <path>`.
            proc = _run_git(["add", "--", *files], cwd=base_root)
        else:
            # `add -u` = stage tracked changes only (mod/delete), never untracked
            # build artifacts — the exact E3/_dirty_files scope.
            proc = _run_git(["add", "-u"], cwd=base_root)
        if proc.returncode == 0:
            proc = _run_git(
                [*_GIT_IDENT, "commit", "-m", subject], cwd=base_root,
                author_env=_author_env_for(project_id),
            )
        if proc.returncode != 0:
            # The checkout stays dirty (staged-but-uncommitted is still porcelain
            # output), so the E3 guard keeps holding and a retry re-stages.
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
        return {"ok": True, "result": {
            "committed": True,
            "commit": (head.stdout or "").strip() or None,
            "subject": subject,
            "files": files,
            "remaining": _dirty_files(base_root, include_untracked=False),
            # Kept separate from `remaining` so the FE's "base is clean → resume
            # the parked merge" test stays the guard's test. Leftover untracked
            # files never blocked the merge and must not block the resume.
            "remaining_untracked": _untracked_files(base_root),
        }}
    finally:
        db_git.release_lock(project_id, holder)


def base_revert(project_id: str, files: list[str]) -> dict:
    """POST …/projects/{id}/git/base-revert — restore the named files of the
    base checkout to HEAD (worktree + index; undoes edits and deletions alike,
    L0002 §2.4). Per-file results; a file that is not dirty reports "not_dirty"
    and counts as success (idempotent against races and double clicks).
    """
    _, base_root = _require_base_checkout(project_id)
    cleaned = [str(f or "").strip() for f in (files or [])]
    cleaned = [f for f in cleaned if f]
    if not cleaned:
        raise GitServiceError(422, "invalid_request", "files must name at least one path")
    for f in cleaned:
        # Reject absolute paths and parent traversal BEFORE the lock — the
        # operation must never reach outside the base checkout.
        norm = f.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in norm.split("/"):
            raise GitServiceError(422, "invalid_request", f"invalid path: {f!r}")
    guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)
    if not git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
        if _merge_in_progress(base_root):
            raise GitServiceError(
                409, "invalid_state", "a merge is in progress; resolve or abort it first"
            )
        dirty = set(_dirty_files(base_root, include_untracked=False))
        results: list[dict] = []
        for f in cleaned:
            if f not in dirty:
                results.append({"path": f, "result": "not_dirty"})
                continue
            # checkout HEAD -- <path> restores worktree AND index from HEAD; the
            # path travels as a literal argv element (no shell), matching the
            # unquoted porcelain form _dirty_files produced.
            proc = _run_git(["checkout", "HEAD", "--", f], cwd=base_root)
            results.append({"path": f, "result": "reverted" if proc.returncode == 0 else "error"})
        remaining = _dirty_files(base_root, include_untracked=False)
        return {
            "ok": all(r["result"] != "error" for r in results),
            "result": {"results": results, "remaining": remaining},
        }
    finally:
        db_git.release_lock(project_id, holder)


# ── Approval-ride-along git action (flowgate.default.0162 P §1 / L §2.1) ──────

def precheck_approve_git_action(doc: Optional[dict], git_action: str) -> str:
    """Validate a git_action carried on an AC approval BEFORE the approval runs
    (L §2.1 step 1 / §4.2). Returns the group_id; raises GitServiceError(422)
    on any violation so the caller skips the approval entirely (P §1-7)."""
    invalid = GitServiceError(
        422, "invalid_request",
        "git_action is only accepted on AC documents of a git-active group",
    )
    if git_action not in ACTION_VALUES:
        raise invalid
    doc = doc or {}
    if doc.get("type_code") != "AC":
        raise invalid
    group_id = doc.get("group_id") or ""
    project_id = _project_of_group(group_id)
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if (
        cfg is None or not cfg.get("enabled")
        or state is None or not state.get("worktree_registered")
    ):
        raise invalid
    return group_id


def run_approve_git_action(group_id: str, git_action: str) -> dict:
    """Post-approval git finalize (L §2.1 step 3). NEVER raises — a git failure
    is reported as {ok: false, error} while the approval itself stands (D §3.1).
    A merge conflict is a successful {ok: true, result: {status: "conflict"}}."""
    try:
        outcome = finalize(group_id, git_action)
        return {"ok": True, "result": outcome["result"]}
    except GitServiceError as exc:
        # Carry the structured details (e.g. base_dirty's file list) so the approve
        # path can surface an actionable error too, not just a bare message (T0010 §b).
        error = {"code": exc.code, "message": exc.message}
        if getattr(exc, "details", None):
            error["details"] = exc.details
        return {"ok": False, "error": error}


def reopen_group_git(project_id: str, group_id: str) -> None:
    """Re-arm a group's git slot after a time-machine rewind past finalize (B0001,
    flowgate.default.0211).

    The reverse-time-machine rewinds only the document/workflow layer; the git
    ledger keeps whatever the prior finalize left it in. When that state is
    terminal (merged/pushed) the group's worktree was already torn down and
    unregistered by slot cleanup (0182), so the next finalize on the re-worked
    group is impossible: precheck_approve_git_action rejects 422 ("not a git-active
    group") while the worktree is gone, or finalize rejects 409 ("already
    finalized") once a write-gate self-heal re-registers the worktree but leaves
    the status terminal (register_worktree never touches status). Restore the
    invariant "a workflow below final approval is not git-terminal": drop the
    status back to 'none' and re-provision the worktree from base HEAD so the
    group can be finalized again.

    Only terminal slots are touched — a healthy in-progress slot needs no
    re-arming, and an in-flight merge/conflict owns the base checkout and must not
    be disturbed (its own state gate and the sweep handle those). Never raises: the
    caller's document rewind has already committed and must stand regardless.
    """
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return
        state = db_git.get_state(group_id)
        if state is None:
            return
        status = (state.get("status") or "none")
        if status not in ("merged", "pushed"):
            return
        # Clear the terminal marker FIRST so the re-provision below cannot leave a
        # "registered worktree, still merged" contradiction (register_worktree only
        # updates branch/worktree_registered, never the status column).
        _set_status(group_id, "none")
        # Re-provision a clean worktree for the re-work. Idempotent and best-effort:
        # a 'failed' result leaves status 'none', and the existing write-gate
        # self-heal re-attempts provisioning on the next source write.
        ensure_worktree(project_id, _module_of(group_id), group_id, trigger="timemachine_reopen")
    except Exception:
        _log.warning("git reopen re-arm failed for %s", group_id, exc_info=True)


# ── Base source-root resolution for the file explorer (0319 B0001) ────────────
# The base file explorer and the editable base-checkout APIs resolved their source
# directory from project_settings.branch (default "main"), while Git provisioning
# clones/adopts the connected repo into src/{project}/{base_branch} (the git
# integration config). When a connected repo's base branch is not "main" these two
# paths diverge: provisioning lands the existing source under base_branch, but the
# explorer walks an empty src/{project}/main and shows nothing — the B0001 report
# ("the branch name is even the same, yet the file explorer is empty"). The source
# is never actually "not fetched"; the read layer just looks at the wrong branch
# folder. Resolve the base tree from the Git base_branch whenever integration is
# ENABLED (mirroring the effective_src_root gate); a non-integrated or disabled
# project keeps its project_settings.branch folder, so its behaviour never changes
# (fallback-first, L0006 §2.2).
def base_branch_for(project_id: Optional[str]) -> Optional[str]:
    """Git base_branch when integration is enabled for the project, else None.

    Never raises: any lookup failure reads as "not integrated", so the caller
    falls back to the ordinary project-settings branch.
    """
    if not project_id:
        return None
    try:
        cfg = db_git.get_config(project_id)
    except Exception:
        _log.warning("base_branch_for lookup failed for %s", project_id, exc_info=True)
        return None
    if cfg is None or not cfg.get("enabled"):
        return None
    return (cfg.get("base_branch") or "main").strip() or "main"


def base_src_root(
    project_id: Optional[str], project_name: str, fallback_branch: str = "main"
) -> Path:
    """Base source-checkout root for base file-explorer reads/edits (0319 B0001).

    Git-integrated (enabled) → ``src_root(project_name, base_branch)``; otherwise
    ``src_root(project_name, fallback_branch)``. ``fallback_branch`` is the value
    the caller already derived from project_settings, so a non-integrated project
    resolves byte-for-byte the same path as before. The returned Path is NOT
    ``.resolve()``-d — callers that need a resolved path do so themselves, exactly
    as they did with the raw ``src_root`` call this replaces.
    """
    branch = base_branch_for(project_id) or (fallback_branch or "main").strip() or "main"
    return src_root(project_name, branch)

