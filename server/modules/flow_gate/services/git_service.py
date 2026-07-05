"""Git integration service (flowgate.default.0115 — D0004/P0005/L0006/DB0007).

Bridges FlowGate projects and remote Git repositories:

  - per-project config + reversibly-encrypted credentials (L0006 §2.3)
  - connection test (ls-remote, L0006 §2.5)
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
from typing import Optional

from Crypto.Cipher import AES as _AES

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.storage.paths import get_storage_root, src_root

_log = logging.getLogger(__name__)

# ── Parameters (L0006 §1) ─────────────────────────────────────────────────────

GIT_TEST_TIMEOUT_SEC = 15
GIT_NET_TIMEOUT_SEC = 120
GIT_LOCAL_TIMEOUT_SEC = 30
LOCK_WAIT_SEC = 5
BRANCH_MAX_LEN = 100
MASK_KEEP_PREFIX = 4
MASK_KEEP_SUFFIX = 4
MASK_MIN_LEN = 9
SECRET_ENV_KEY = "FLOWGATE_GIT_ENCRYPT_KEY"
SECRET_ENV_KEY_PREV = "FLOWGATE_GIT_ENCRYPT_KEY_PREV"
AUTO_COMMIT_MSG = "flowgate: work of {group_id}"
MERGE_COMMIT_MSG = "flowgate: merge {branch} into {base_branch} ({group_id})"
PROVIDER_VALUES = ("github", "gitlab", "gitea", "gitbucket", "generic")
ACTION_VALUES = ("merge", "push", "wait")

# Identity for commits the SERVER makes (auto-commit / merge commits). Without
# an explicit identity `git commit` fails on hosts with no global user config.
_GIT_IDENT = ["-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost"]


class GitServiceError(Exception):
    """Carries (http_status, error_code, message) to the router envelope."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


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
) -> subprocess.CompletedProcess:
    """Run git with prompt-free auth injection and secret-scrubbed output."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
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


# ── Project lock (L0006 §2.8) ────────────────────────────────────────────────

def _acquire_lock(project_id: str, holder: str, wait_sec: float = LOCK_WAIT_SEC) -> bool:
    deadline = time.monotonic() + wait_sec
    while True:
        if db_git.try_acquire_lock(project_id, holder):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


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
    if action not in ACTION_VALUES:
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

    row = db_git.upsert_config(project_id, {
        "repo_url": (body.get("repo_url") or "").strip(),
        "provider": provider,
        "username": (body.get("username") or None),
        "secret_enc": secret_enc,
        "base_branch": (body.get("base_branch") or "main").strip() or "main",
        "default_finalize_action": action,
        "enabled": bool(body.get("enabled")),
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


def ensure_worktree(project_id: str, module: str, group_id: str) -> str:
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
            _emit_worktree_failed(project_id, group_id, None, "project_name missing")
            return "failed"
        try:
            branch = worktree_branch_name(project_id, module or _module_of(group_id), group_id)
        except GitServiceError as exc:
            _emit_worktree_failed(project_id, group_id, None, exc.code)  # E9
            return "failed"
        if not git_available():
            _emit_worktree_failed(project_id, group_id, branch, "git_unavailable")  # E1
            return "failed"

        holder = f"op:{uuid.uuid4()}"
        if not _acquire_lock(project_id, holder):
            _emit_worktree_failed(project_id, group_id, branch, "git_busy")  # E11
            return "failed"
        try:
            return _ensure_worktree_locked(cfg, project_id, project_name, group_id, branch)
        finally:
            db_git.release_lock(project_id, holder)
    except Exception as exc:  # noqa: BLE001 — the hook must never break its caller
        _log.warning("ensure_worktree failed for %s", group_id, exc_info=True)
        try:
            _emit_worktree_failed(project_id, group_id, None, _scrub(str(exc)))
        except Exception:
            pass
        return "failed"


def _ensure_worktree_locked(
    cfg: dict, project_id: str, project_name: str, group_id: str, branch: str
) -> str:
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    wt_path = src_root(project_name, branch)
    username = cfg.get("username")
    secret = _load_secret_for(cfg) or ""
    repo_url = (cfg.get("repo_url") or "").strip()

    # Base checkout: clone once if the branch slot is not yet a git checkout.
    if not (base_root / ".git").exists():
        if base_root.exists() and any(base_root.iterdir()):
            _emit_worktree_failed(project_id, group_id, branch, "base_path_occupied")  # E7 analogue
            return "failed"
        base_root.parent.mkdir(parents=True, exist_ok=True)
        proc = _run_git(
            ["clone", "--branch", base_branch, repo_url, str(base_root)],
            timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
        )
        if proc.returncode != 0:
            _emit_worktree_failed(project_id, group_id, branch, proc.stderr.strip())
            return "failed"

    # Idempotence: ledger says the worktree exists and the directory is present.
    state = db_git.get_state(group_id)
    if (
        state is not None
        and state.get("worktree_registered")
        and state.get("branch") == branch
        and wt_path.is_dir()
    ):
        _emit_worktree_ready(project_id, group_id, branch, base_branch, wt_path, created=False)
        return "ok"

    if wt_path.exists():
        # Unregistered directory squatting on the slot (E7): never delete automatically.
        _emit_worktree_failed(project_id, group_id, branch, "worktree_path_occupied")
        return "failed"

    proc = _run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        _emit_worktree_failed(project_id, group_id, branch, proc.stderr.strip())
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
        start_point = (
            f"origin/{base_branch}"
            if _ref_exists(base_root, f"refs/remotes/origin/{base_branch}")
            else base_branch
        )
        proc = _run_git(
            ["worktree", "add", "-b", branch, str(wt_path), start_point], cwd=base_root
        )
    if proc.returncode != 0:
        _emit_worktree_failed(project_id, group_id, branch, proc.stderr.strip())
        return "failed"

    db_git.register_worktree(group_id, project_id, branch)
    _emit_worktree_ready(project_id, group_id, branch, base_branch, wt_path, created=True)
    return "ok"


def _emit_worktree_ready(
    project_id: str, group_id: str, branch: str, base_branch: str, wt_path: Path, *, created: bool
) -> None:
    try:
        rel = wt_path.relative_to(get_storage_root()).as_posix()
    except Exception:
        rel = str(wt_path)
    _emit("git_worktree_ready", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "worktree_path": rel,
        "created": created,
    })


def _emit_worktree_failed(
    project_id: str, group_id: str, branch: Optional[str], error: str
) -> None:
    _emit("git_worktree_failed", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "error": error,
    })


def ensure_worktree_async(project_id: str, module: str, group_id: str) -> None:
    """H1 wrapper: run provisioning off the request thread (clone can be slow).

    The decide response must not wait on network git; H2 (worker-token grant
    creation) re-guarantees the worktree before any source access anyway.
    """
    import threading

    threading.Thread(
        target=ensure_worktree, args=(project_id, module, group_id), daemon=True
    ).start()


# ── Effective source-root resolution (L0006 §2.2·§4.1) ───────────────────────

def effective_src_root(project_id: Optional[str], group_id: Optional[str]) -> Optional[Path]:
    """Group worktree path when it must be used, else None (= caller falls back).

    Fallback-first (L0006 §2.2): missing config, disabled integration, missing
    ledger entry, or a vanished directory all yield None so the caller resolves
    the ordinary project-branch folder. Never raises.
    """
    if not project_id or not group_id:
        return None
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return None
        state = db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return None
        branch = (state.get("branch") or "").strip()
        if not branch:
            return None
        project_name = _project_name(project_id)
        if not project_name:
            return None
        wt_path = src_root(project_name, branch)
        if not wt_path.is_dir():
            return None  # E7/E13: ledger without directory → fallback
        return wt_path.resolve()
    except Exception:
        _log.warning("effective_src_root failed for %s", group_id, exc_info=True)
        return None


# ── Finalize state (P0005 §5-1 / L0006 §3) ───────────────────────────────────

_NONE_STATE = {
    "branch": None, "base_branch": None, "status": "none", "default_action": None,
    "choices": [], "ahead_count": None, "behind_count": None, "merge_id": None,
}


def _group_root_wf_done(group_id: str) -> bool:
    """True when the group's workflow root (R/B) reached final approval."""
    row = get_store()._fetch_one(
        "SELECT 1 AS hit FROM documents "
        "WHERE group_id = ? AND type_code IN ('R','B') AND doc_review_status = 'wf_done'",
        [group_id],
    )
    return row is not None


def get_finalize_state(group_id: str) -> dict:
    project_id = _project_of_group(group_id)
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if (
        cfg is None or not cfg.get("enabled")
        or state is None or not state.get("worktree_registered")
    ):
        return {"ok": True, "state": {"group_id": group_id, **_NONE_STATE}}

    status = state.get("status") or "none"
    # Lazy none→awaiting_choice transition (L0006 §3): the workflow module never
    # calls into git; the first state query after wf_done realizes the transition.
    if status == "none" and _group_root_wf_done(group_id):
        db_git.set_status(group_id, "awaiting_choice")
        status = "awaiting_choice"

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

    return {"ok": True, "state": {
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "status": status,
        "default_action": cfg.get("default_finalize_action") or "wait",
        "choices": list(ACTION_VALUES),
        "ahead_count": ahead,
        "behind_count": behind,
        "merge_id": state.get("merge_id"),
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


def _dirty(repo: Path) -> bool:
    proc = _run_git(["status", "--porcelain"], cwd=repo)
    return bool((proc.stdout or "").strip()) if proc.returncode == 0 else False


def finalize(group_id: str, action: Optional[str]) -> dict:
    cfg, state, project_id, base_root, wt_path = _finalize_context(group_id)
    action = action or cfg.get("default_finalize_action") or "wait"
    if action not in ACTION_VALUES:
        raise GitServiceError(422, "invalid_request", f"invalid action: {action!r}")

    # Refresh the lazy wf_done transition before the state guard (L0006 §4.2).
    status = (state.get("status") or "none")
    if status == "none" and _group_root_wf_done(group_id):
        db_git.set_status(group_id, "awaiting_choice")
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
        db_git.set_status(group_id, "waiting")
        return _finalize_result(group_id, project_id, "wait", "waiting")

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
    transferred = False
    try:
        branch = state["branch"]
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        username = cfg.get("username")
        secret = _load_secret_for(cfg) or ""

        if not wt_path.is_dir():
            raise GitServiceError(409, "invalid_state", "group worktree directory is missing")

        # Absorb leftover worker changes, then preserve the work branch remotely
        # (both merge and push start here — L0006 §2.6).
        if _dirty(wt_path):
            proc = _run_git(["add", "-A"], cwd=wt_path)
            if proc.returncode == 0:
                proc = _run_git(
                    [*_GIT_IDENT, "commit", "-m", AUTO_COMMIT_MSG.format(group_id=group_id)],
                    cwd=wt_path,
                )
            if proc.returncode != 0:
                raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        proc = _run_git(
            ["push", "origin", branch],
            cwd=wt_path, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "push_rejected", _last_line(proc.stderr))

        if action == "push":
            db_git.set_status(group_id, "pushed")
            return _finalize_result(group_id, project_id, "push", "pushed", pushed=True)

        # action == "merge"
        if _dirty(base_root):
            raise GitServiceError(  # E3 — never auto-stash the server's own checkout
                500, "base_dirty",
                "base checkout has local modifications; operator intervention required",
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
        db_git.set_status(group_id, "merging")
        msg = MERGE_COMMIT_MSG.format(branch=branch, base_branch=base_branch, group_id=group_id)
        proc = _run_git([*_GIT_IDENT, "merge", "--no-ff", "-m", msg, branch], cwd=base_root)
        if proc.returncode == 0:
            push = _run_git(
                ["push", "origin", base_branch],
                cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if push.returncode != 0:
                # E6 — atomicity: never report merged unless the push landed.
                _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root)
                db_git.set_status(group_id, "waiting")
                raise GitServiceError(500, "push_rejected", _last_line(push.stderr))
            head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
            merge_commit = (head.stdout or "").strip() or None
            db_git.set_status(group_id, "merged", merge_commit=merge_commit)
            _emit("git_finalize_done", project_id, group_id, {
                "project": project_id, "group_id": group_id,
                "action": "merge", "status": "merged", "merge_commit": merge_commit,
            })
            return {
                "ok": True,
                "result": {
                    "action": "merge", "status": "merged", "merge_commit": merge_commit,
                    "pushed": True, "merge_id": None, "conflict_files": [],
                },
            }

        # Merge failed: conflicts keep MERGE_HEAD and become a session; anything
        # else is rolled back to waiting.
        files_proc = _run_git(["diff", "--name-only", "--diff-filter=U"], cwd=base_root)
        files = [l.strip() for l in (files_proc.stdout or "").splitlines() if l.strip()]
        if not files:
            _run_git(["merge", "--abort"], cwd=base_root)
            db_git.set_status(group_id, "waiting")
            raise GitServiceError(500, "git_error", _last_line(proc.stderr))
        merge_id = db_git.create_session(group_id, files)
        db_git.set_status(group_id, "conflict", merge_id=merge_id)
        db_git.transfer_lock(project_id, holder, f"merge:{merge_id}")
        transferred = True
        _emit("git_merge_conflict", project_id, group_id, {
            "project": project_id, "group_id": group_id,
            "merge_id": merge_id, "conflict_count": len(files),
        })
        return {
            "ok": True,
            "result": {
                "action": "merge", "status": "conflict", "merge_commit": None,
                "pushed": False, "merge_id": merge_id, "conflict_files": files,
            },
        }
    finally:
        if not transferred:
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

    _session, cfg, project_id, base_root = _session_context(group_id, merge_id)
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

    state = db_git.get_state(group_id) or {}
    branch = state.get("branch") or ""
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    msg = MERGE_COMMIT_MSG.format(branch=branch, base_branch=base_branch, group_id=group_id)
    proc = _run_git([*_GIT_IDENT, "commit", "-m", msg], cwd=base_root)
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(proc.stderr))
    push = _run_git(
        ["push", "origin", base_branch],
        cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
        username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
    )
    if push.returncode != 0:
        # E6 — roll the merge commit back; the session ends and the group re-chooses.
        _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root)
        db_git.close_session(merge_id, "aborted")
        db_git.set_status(group_id, "waiting")
        db_git.release_lock(project_id, f"merge:{merge_id}")
        raise GitServiceError(500, "push_rejected", _last_line(push.stderr))

    head = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
    merge_commit = (head.stdout or "").strip() or None
    db_git.close_session(merge_id, "done")
    db_git.set_status(group_id, "merged", merge_commit=merge_commit)
    db_git.release_lock(project_id, f"merge:{merge_id}")
    _emit("git_finalize_done", project_id, group_id, {
        "project": project_id, "group_id": group_id,
        "action": "merge", "status": "merged", "merge_commit": merge_commit,
    })
    return {
        "ok": True,
        "result": {
            "status": "merged", "merge_commit": merge_commit, "pushed": True,
            "remaining_conflicts": [],
        },
    }


def abort_merge(group_id: str, merge_id: int) -> dict:
    _session, _cfg, project_id, base_root = _session_context(group_id, merge_id)
    _run_git(["merge", "--abort"], cwd=base_root)
    db_git.close_session(merge_id, "aborted")
    db_git.set_status(group_id, "waiting")
    db_git.release_lock(project_id, f"merge:{merge_id}")
    return {"ok": True, "result": {"status": "waiting"}}


# ── Boot recovery (L0006 §2.8 / E8) ──────────────────────────────────────────

def startup_recovery() -> None:
    """Restore/clean conflict sessions and drop stale one-shot locks at boot."""
    try:
        for session in db_git.list_open_sessions():
            merge_id = session["merge_id"]
            group_id = session["group_id"]
            try:
                project_id = _project_of_group(group_id)
                cfg = db_git.get_config(project_id)
                project_name = _project_name(project_id)
                merge_head_exists = False
                if cfg and project_name:
                    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
                    base_root = src_root(project_name, base_branch)
                    merge_head_exists = (base_root / ".git" / "MERGE_HEAD").exists()
                holder = f"merge:{merge_id}"
                if merge_head_exists:
                    lock = db_git.get_lock(project_id)
                    if lock is None:
                        db_git.try_acquire_lock(project_id, holder)
                    elif lock.get("holder") != holder:
                        db_git.force_release_lock(project_id)
                        db_git.try_acquire_lock(project_id, holder)
                else:
                    db_git.close_session(merge_id, "aborted")
                    db_git.set_status(group_id, "waiting")
                    db_git.release_lock(project_id, holder)
            except Exception:
                _log.warning("git session recovery failed for merge %s", merge_id, exc_info=True)
        # One-shot op:* locks cannot survive a restart legitimately.
        for lock in db_git.list_locks():
            if str(lock.get("holder") or "").startswith("op:"):
                db_git.force_release_lock(lock["project_id"])
    except Exception:
        # Table may not exist yet (pre-migration boot) — recovery is best-effort.
        _log.info("git startup recovery skipped", exc_info=True)
