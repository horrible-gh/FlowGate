"""Base-slot provisioning, adoption, and direct base operations.

Extracted from git_service.py (flowgate.default.0550 T0013, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.db.connection import now_iso

from .credentials import GitServiceError, _author_env_for
from .refs import _dirty_files, _ignored_paths, _merge_in_progress, _untracked_files

_log = logging.getLogger(__name__)

BASE_COMMIT_MSG_PREFIX = "fix: "

BASE_COMMIT_MSG_JOINER = ", "

ADOPT_SNAPSHOT_MSG = "flowgate: adopt snapshot of {base_branch} ({project_id})"

BOOTSTRAP_SEED_MSG = "flowgate: initialize {base_branch} ({project_id})"

ADOPT_PENDING_MARKER = ".git/flowgate_adopt_pending"

ATTEMPT_RECORD_KEY = "git.provision.last_attempt.{project_id}"


def _judge_base_slot(base_root: Path, base_branch: str) -> str:
    """'empty' | 'occupied' | 'checkout' — L0005 §2.1.

    Completion criterion: refs/heads/{base_branch} exists AND no pending-adopt
    marker. Partial debris (.git without the branch, or a leftover marker)
    reports 'occupied' so a re-run resumes the adopt sequence.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        if not base_root.exists() or not any(base_root.iterdir()):
            return "empty"
    except OSError:
        return "empty"
    if (base_root / ADOPT_PENDING_MARKER).exists():
        return "occupied"
    if (base_root / ".git").exists():
        if not _gs.git_available():
            return "checkout"  # informational approximation; execution paths fail precisely
        proc = _gs._run_git(
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
    from modules.flow_gate.services import git_service as _gs
    return {
        "status": "failed", "reason": _gs._last_line(proc.stderr),
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
    from modules.flow_gate.services import git_service as _gs
    # 1. repository skeleton — working files untouched
    if not (base_root / ".git").exists():
        proc = _gs._run_git(["init"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)
    marker = base_root / ADOPT_PENDING_MARKER
    try:
        marker.touch()
    except OSError as exc:
        return {"status": "failed", "reason": f"adopt marker unwritable: {exc}",
                "snapshot_commit": None, "snapshot_at": None}

    # 2. remote wiring (re-entry: sync the URL only)
    proc = _gs._run_git(["remote", "get-url", "origin"], cwd=base_root)
    if proc.returncode != 0:
        proc = _gs._run_git(["remote", "add", "origin", repo_url], cwd=base_root)
    elif (proc.stdout or "").strip() != repo_url:
        proc = _gs._run_git(["remote", "set-url", "origin", repo_url], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)

    # 3. fetch — the most likely failure point; debris stays for re-entry
    proc = _gs._run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        return _provision_failed(proc)

    # 4. establish the base branch without a checkout (working tree untouched)
    proc = _gs._run_git(["symbolic-ref", "HEAD", f"refs/heads/{base_branch}"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    if _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        # --mixed moves the branch ref and index only; files stay byte-identical
        proc = _gs._run_git(
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["status", "--porcelain", "-z"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    for entry in (proc.stdout or "").split("\0"):
        if len(entry) >= 4 and entry[1] == "D" and entry[2] == " ":
            restore = _gs._run_git(["checkout", "--", entry[3:]], cwd=base_root)
            if restore.returncode != 0:
                # e.g. path-type conflict — stop with all data intact (no auto-fix)
                return _provision_failed(restore)

    snapshot_commit: Optional[str] = None
    snapshot_at: Optional[str] = None
    proc = _gs._run_git(["status", "--porcelain"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    if (proc.stdout or "").strip():
        proc = _gs._run_git(["add", "-A"], cwd=base_root)  # .gitignore is honored
        if proc.returncode != 0:
            return _provision_failed(proc)
        msg = ADOPT_SNAPSHOT_MSG.format(base_branch=base_branch, project_id=project_id)
        proc = _gs._run_git(
            [*_gs._GIT_IDENT, "commit", "-m", msg], cwd=base_root,
            author_env=_author_env_for(project_id),
        )
        if proc.returncode != 0:
            return _provision_failed(proc)
        head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["ls-remote", repo_url],
        timeout=_gs.GIT_TEST_TIMEOUT_SEC,
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["ls-remote", repo_url, f"refs/heads/{base_branch}"],
        timeout=_gs.GIT_TEST_TIMEOUT_SEC,
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
    from modules.flow_gate.services import git_service as _gs
    try:
        base_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed", "reason": f"base dir uncreatable: {exc}",
                "snapshot_commit": None, "snapshot_at": None}

    proc = _gs._run_git(["init", "-b", base_branch, str(base_root)])
    if proc.returncode != 0:
        # git < 2.28 has no `init -b`: init, then point the unborn HEAD at the base.
        proc = _gs._run_git(["init"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)
        proc = _gs._run_git(["symbolic-ref", "HEAD", f"refs/heads/{base_branch}"], cwd=base_root)
        if proc.returncode != 0:
            return _provision_failed(proc)

    proc = _gs._run_git(["remote", "add", "origin", repo_url], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)

    readme = base_root / "README.md"
    if not readme.exists():
        try:
            readme.write_text(f"# {project_id}\n", encoding="utf-8")
        except OSError as exc:
            return {"status": "failed", "reason": f"seed file unwritable: {exc}",
                    "snapshot_commit": None, "snapshot_at": None}
    proc = _gs._run_git(["add", "--", "README.md"], cwd=base_root)
    if proc.returncode != 0:
        return _provision_failed(proc)
    msg = BOOTSTRAP_SEED_MSG.format(base_branch=base_branch, project_id=project_id)
    proc = _gs._run_git(
        [*_gs._GIT_IDENT, "commit", "-m", msg], cwd=base_root,
        author_env=_author_env_for(project_id),
    )
    if proc.returncode != 0:
        return _provision_failed(proc)
    head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
    return {"status": "ok", "reason": None,
            "snapshot_commit": (head.stdout or "").strip() or None,
            "snapshot_at": now_iso()}


def _provision_base_locked(cfg: dict, project_id: str, project_name: str, trigger: str) -> dict:
    """Judge the base slot and establish it (none / clone / adopt) — L0005 §2.2.

    The caller must hold the project git mutex (hook path already does; the
    manual path acquires it in provision_base).
    """
    from modules.flow_gate.services import git_service as _gs
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = _gs.src_root(project_name, base_branch)
    state = _gs._judge_base_slot(base_root, base_branch)
    if state == "checkout":
        # idempotent pass-through — the ledger is NOT updated (P0004 scenario 4)
        return {"status": "ok", "mode": "none", "reason": None,
                "snapshot_commit": None, "snapshot_at": None}

    mode = "clone" if state == "empty" else "adopt"
    try:
        secret = _gs._load_secret_for(cfg) or ""
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
            proc = _gs._run_git(
                ["clone", "--branch", base_branch, repo_url, str(base_root)],
                timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
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
    from modules.flow_gate.services import git_service as _gs
    def _blocked(reason: str) -> dict:
        _record_attempt(project_id, "failed", reason, trigger, "none")
        return {"status": "failed", "mode": "none", "reason": reason,
                "snapshot_commit": None, "snapshot_at": None}

    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        # not recorded; the manual route pre-blocks with 409 not_enabled
        return {"status": "skipped", "mode": "none", "reason": None,
                "snapshot_commit": None, "snapshot_at": None}
    project_name = _gs._project_name(project_id)
    if not project_name:
        return _blocked("project_name missing")
    if not _gs.git_available():
        return _blocked("git_unavailable")

    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        return _blocked("git_busy")
    try:
        return _gs._provision_base_locked(cfg, project_id, project_name, trigger)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def provision_view(project_id: str) -> dict:
    """Status object for GET …/git/provision (P0004) — read-only, no network git."""
    from modules.flow_gate.services import git_service as _gs
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"configured": False, "enabled": False, "base_branch": None,
                "base_path_state": "empty", "base_checkout_exists": False,
                "adopt_snapshot": None, "last_attempt": None}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _gs._project_name(project_id)
    base_root = _gs.src_root(project_name, base_branch) if project_name else None
    state = _gs._judge_base_slot(base_root, base_branch) if base_root else "occupied"

    record = _load_attempt_record(project_id)
    snapshot = None
    if record and record.get("snapshot_commit"):
        snapshot = {"commit": record["snapshot_commit"],
                    "committed_at": record.get("snapshot_at")}
        if base_root and (base_root / ".git").exists() and _gs.git_available():
            proc = _gs._run_git(
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
    from modules.flow_gate.services import git_service as _gs
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = _gs.db_git.get_config(project_id)
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


def manual_fetch(project_id: str) -> dict:
    """POST …/projects/{id}/git/fetch — recovery fetch of the base checkout."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs._require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _gs._project_name(project_id)
    base_root = _gs.src_root(project_name, base_branch) if project_name else None
    if base_root is None or _gs._judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(
            409, "invalid_state", "base checkout is not available for fetch"
        )
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        proc = _gs._run_git(
            ["fetch", "origin"],
            cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        # 0320 B0001: a bare `fetch` only moved refs/remotes/origin/{base} and then
        # *reported* behind_count — the local base branch never advanced, so the
        # base checkout stayed behind upstream forever and the operator-facing
        # "Fetch" action was a no-op recovery ("are you never going to fetch it?"). Finalize was
        # the ONLY path that ran `merge --ff-only origin/{base}` (see finalize). Do
        # the same fast-forward here whenever it is safe: base is clean and can be
        # fast-forwarded. A dirty base is left untouched (never force the server's
        # own checkout), and a genuine divergence (local-only commits) simply fails
        # the ff-only and is reported as behind/ahead — that stays the E4
        # base_diverged condition finalize already owns, not this recovery's job.
        advanced = False
        if (
            _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}")
            and not _gs._dirty(base_root, include_untracked=False)
        ):
            ff = _gs._run_git(
                ["merge", "--ff-only", f"origin/{base_branch}"], cwd=base_root
            )
            advanced = ff.returncode == 0
        ahead, behind = _gs._base_ahead_behind(base_root, base_branch)
        return {"ok": True, "result": {
            "fetched": True, "advanced": advanced, "base_branch": base_branch,
            "ahead_count": ahead, "behind_count": behind,
        }}
    finally:
        _gs.db_git.release_lock(project_id, holder)


def default_base_commit_message(files: list[str]) -> str:
    """Deterministic default subject for a base-checkout commit (L0002 §2.2).

    "fix: a.py, b.py"; when the joined list overflows COMMIT_SUBJECT_MAX the
    abbreviated "fix: a.py and N more" is used (hard-cut as a last resort so the
    result is always a valid subject). The FE seeds its input with the same
    rule, so either side may materialize the message with identical output.
    """
    from modules.flow_gate.services import git_service as _gs
    subject = BASE_COMMIT_MSG_PREFIX + BASE_COMMIT_MSG_JOINER.join(files)
    if len(subject) <= _gs.COMMIT_SUBJECT_MAX:
        return subject
    subject = f"{BASE_COMMIT_MSG_PREFIX}{files[0]} and {len(files) - 1} more"
    return subject[:_gs.COMMIT_SUBJECT_MAX]


def _require_base_checkout(project_id: str) -> tuple[dict, Path]:
    """(cfg, base_root) for base-commit/-revert, or 404/409 per the shared rules."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs._require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _gs._project_name(project_id)
    base_root = _gs.src_root(project_name, base_branch) if project_name else None
    if base_root is None or _gs._judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(
            409, "invalid_state", "base checkout is not available"
        )
    return cfg, base_root


def _base_commit_locked(
    project_id: str, base_root: Path, subject: str, selected: list[str]
) -> dict:
    """Body of `base_commit` that runs under an already-held project git lock.

    Split out so `resolve_base_dirty` (0482 T0011) can acquire the project lock
    once and hold it across baseline capture, discard, and commit — see the
    `_holder` parameter on `base_commit` below."""
    from modules.flow_gate.services import git_service as _gs
    _gs.guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
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
        proc = _gs._run_git(["add", "--", *files], cwd=base_root)
    else:
        # `add -u` = stage tracked changes only (mod/delete), never untracked
        # build artifacts — the exact E3/_dirty_files scope.
        proc = _gs._run_git(["add", "-u"], cwd=base_root)
    if proc.returncode == 0:
        proc = _gs._run_git(
            [*_gs._GIT_IDENT, "commit", "-m", subject], cwd=base_root,
            author_env=_author_env_for(project_id),
        )
    if proc.returncode != 0:
        # The checkout stays dirty (staged-but-uncommitted is still porcelain
        # output), so the E3 guard keeps holding and a retry re-stages.
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=base_root)
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


def base_commit(
    project_id: str, message: Optional[str], paths: Optional[list[str]] = None,
    _holder: Optional[str] = None,
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

    `_holder` (internal): when the caller already holds the project git lock
    under this holder id, reuse it instead of acquiring a fresh one — lets
    `resolve_base_dirty` (0482 T0011) keep baseline capture, discard, and
    commit atomic under a single lock instead of three independently-locked
    calls that leave a race window between them.
    """
    from modules.flow_gate.services import git_service as _gs
    _, base_root = _gs._require_base_checkout(project_id)
    subject = _gs.normalize_subject(message)
    if len(subject) > _gs.COMMIT_SUBJECT_MAX:
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
    _gs.guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    if _holder is not None:
        return _gs._base_commit_locked(project_id, base_root, subject, selected)
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        return _gs._base_commit_locked(project_id, base_root, subject, selected)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _base_revert_locked(project_id: str, base_root: Path, cleaned: list[str]) -> dict:
    """Body of `base_revert` that runs under an already-held project git lock.

    Split out so `resolve_base_dirty` (0482 T0011) can acquire the project lock
    once and hold it across baseline capture, discard, and commit — see the
    `_holder` parameter on `base_revert` below."""
    from modules.flow_gate.services import git_service as _gs
    _gs.guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
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
        proc = _gs._run_git(["checkout", "HEAD", "--", f], cwd=base_root)
        results.append({"path": f, "result": "reverted" if proc.returncode == 0 else "error"})
    remaining = _dirty_files(base_root, include_untracked=False)
    return {
        "ok": all(r["result"] != "error" for r in results),
        "result": {"results": results, "remaining": remaining},
    }


def base_revert(project_id: str, files: list[str], _holder: Optional[str] = None) -> dict:
    """POST …/projects/{id}/git/base-revert — restore the named files of the
    base checkout to HEAD (worktree + index; undoes edits and deletions alike,
    L0002 §2.4). Per-file results; a file that is not dirty reports "not_dirty"
    and counts as success (idempotent against races and double clicks).

    `_holder` (internal): when the caller already holds the project git lock
    under this holder id, reuse it instead of acquiring a fresh one — see
    `base_commit`'s `_holder` docstring for why.
    """
    from modules.flow_gate.services import git_service as _gs
    _, base_root = _gs._require_base_checkout(project_id)
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
    _gs.guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    if _holder is not None:
        return _gs._base_revert_locked(project_id, base_root, cleaned)
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        return _gs._base_revert_locked(project_id, base_root, cleaned)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def base_remove(project_id: str, files: list[str]) -> dict:
    """POST …/projects/{id}/git/base-remove — delete untracked files from the base
    checkout (0350 T0004 / NR0003 §8 R4).

    The `base_untracked_conflict` 409 has always told the operator to "commit or
    remove them", but only the commit half (`base_commit(..., paths=...)`) ever
    shipped. This is the missing remove: for a base-checkout file that was never
    meant to be kept (a stray local experiment, a build artifact that slipped past
    .gitignore), deleting it is the only way to clear a merge that wants to create
    the same path — committing it would just relocate the conflict into base's own
    history.

    Deliberately its own function/route, never folded into `base_revert` (which
    restores TRACKED content to HEAD): revert is non-destructive by construction
    (the content survives in HEAD), while this discards the only copy of a file
    that was never committed anywhere. Kept behind the same base-root
    containment/lock/merge-in-progress gates as base_commit/base_revert, and
    revalidates every path as untracked-and-not-ignored right before deleting —
    never trusts a caller-supplied list on its own.
    """
    from modules.flow_gate.services import git_service as _gs
    _, base_root = _gs._require_base_checkout(project_id)
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
    _gs.guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        _gs.guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
        if _merge_in_progress(base_root):
            raise GitServiceError(
                409, "invalid_state", "a merge is in progress; resolve or abort it first"
            )
        # Re-derive "untracked" right now (not from what the caller remembers) —
        # the same freshness discipline as base_commit's `paths` mode. A path that
        # is tracked or already gone is never a valid delete target.
        untracked = set(_untracked_files(base_root, limit=0))
        unknown = [p for p in cleaned if p not in untracked]
        if unknown:
            # `.gitignore` first, same as base_commit: an ignored file needs its own
            # honest error, never a silent `clean -f -x`.
            ignored = _ignored_paths(base_root, unknown)
            if ignored:
                raise GitServiceError(
                    422, "path_ignored",
                    "these paths are excluded by .gitignore and cannot be removed",
                    details={"files": ignored},
                )
            raise GitServiceError(
                422, "invalid_request",
                "these paths are not untracked files in the base checkout",
                details={"files": unknown},
            )
        results: list[dict] = []
        for f in cleaned:
            # `git clean -f` is a second, git-enforced guard on top of the
            # revalidation above (it refuses tracked/ignored paths on its own) and
            # runs as a literal argv pathspec — no shell, no globbing — matching
            # base_commit's `add -- <files>` / base_revert's `checkout HEAD -- <path>`.
            proc = _gs._run_git(["clean", "-f", "-q", "--", f], cwd=base_root)
            results.append({"path": f, "result": "removed" if proc.returncode == 0 else "error"})
        return {
            "ok": all(r["result"] != "error" for r in results),
            "result": {
                "results": results,
                "remaining_untracked": _untracked_files(base_root),
            },
        }
    finally:
        _gs.db_git.release_lock(project_id, holder)
