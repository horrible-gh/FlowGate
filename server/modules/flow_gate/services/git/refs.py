"""Git ref/tree/blob reads: name-status parsing, dirty/untracked detection,
ahead/behind + unpushed-commit accounting.

Extracted from git_service.py (flowgate.default.0550 T0011, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from modules.flow_gate.services import path_exclusion_rules

from .credentials import GitServiceError

_log = logging.getLogger(__name__)


def _ref_exists(repo: Path, ref: str) -> bool:
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["show-ref", "--verify", "--quiet", ref], cwd=repo)
    return proc.returncode == 0


def _ahead_of_base(base_root: Path, base_branch: str, branch: str) -> Optional[int]:
    """Number of commits on `branch` not yet on `base_branch` (local rev-list, no
    network). None when it cannot be counted (missing ref / git failure)."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["rev-list", "--count", f"{base_branch}..{branch}"], cwd=base_root)
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return None


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


def _normalize_git_status(code: str) -> str:
    """A raw ``git diff --name-status`` letter (possibly with a similarity suffix, e.g.
    ``R100``) → one of ``A``/``M``/``D``/``R``. Anything else git might emit (``T``
    type-change, ``U`` unmerged, ...) falls back to ``M``: the path did change and is
    never dropped, it is just not classified more precisely (0493 T0005)."""
    letter = (code or "")[:1].upper()
    if letter in ("A", "M", "D"):
        return letter
    if letter in ("R", "C"):
        return "R"
    return "M"


def _parse_name_status_manifest(stdout: str) -> list[dict]:
    """Same ``-M -z`` stream as ``_parse_name_status_z``, but keeps status and the
    rename's old path instead of collapsing to a bare path list (0493 T0005 —
    reviewers need per-file actual status, not just a path).

    Returns one entry per changed path: ``{"path", "status", "old_path"}``. ``old_path``
    is set only for a rename/copy record (``take_second``); every other status carries
    it as ``None``. Field-walking logic mirrors ``_parse_name_status_z`` — see its
    docstring for why a fixed stride desyncs on the first rename.
    """
    fields = (stdout or "").split("\0")
    entries: list[dict] = []
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
                entries.append({
                    "path": second, "status": _normalize_git_status(status),
                    "old_path": first or None,
                })
        elif first:
            entries.append({
                "path": first, "status": _normalize_git_status(status), "old_path": None,
            })
    return entries


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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["ls-tree", "-z", commit, "--", path], cwd=base_root, timeout=_gs.GIT_READ_TIMEOUT_SEC
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git tree lookup failed", diagnostic=_gs._one_line_subject(proc.stderr))
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["cat-file", "-s", sha], cwd=base_root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git object lookup failed", diagnostic=_gs._one_line_subject(proc.stderr))
    try:
        return int((proc.stdout or "0").strip())
    except ValueError:
        return 0


def _cat_file_blob_head(base_root: Path, sha: str, limit: int) -> bytes:
    """Read up to ``limit`` raw bytes of a blob (bounded so a huge object is never
    slurped whole just to sniff/truncate it)."""
    from modules.flow_gate.services import git_service as _gs
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
            proc.wait(timeout=_gs.GIT_READ_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            pass
    return data


def _dirty(repo: Path, include_untracked: bool = True) -> bool:
    args = ["status", "--porcelain"]
    if not include_untracked:
        # E3 guard scope: untracked build artifacts (e.g. __pycache__/*.pyc,
        # .pytest_cache) in the server's base checkout are NOT "local
        # modifications" — only changes to tracked files require operator
        # intervention. See NR flowgate.default.0165.0009.
        args.append("--untracked-files=no")
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(args, cwd=repo)
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(args, cwd=repo)
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


def _worktree_untracked_paths(wt_path: Path) -> list[str]:
    """Untracked, non-gitignored paths in a worktree ('/'-separated, sorted)."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        return []
    return sorted(p for p in (proc.stdout or "").split("\0") if p)


def _worktree_untracked_summary_for_path(wt_path: Path) -> dict:
    """Classify raw untracked paths with the exact rule used by staging."""
    raw = _worktree_untracked_paths(wt_path)
    kept, artifacts = path_exclusion_rules.partition_paths(raw)
    return {
        "total_count": len(raw),
        "excluded_artifact_count": len(artifacts),
        "staged_new_file_count": len(kept),
    }


def worktree_untracked_summary(project_id: str, group_id: str) -> Optional[dict]:
    """Best-effort submission-time view of a live git worktree's untracked files."""
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        state = _gs.db_git.get_state(group_id)
        if not cfg or not cfg.get("enabled") or not state:
            return None
        if not state.get("worktree_registered") or not state.get("branch"):
            return None
        wt_path = _gs._group_worktree_path(project_id, group_id, state["branch"])
        if wt_path is None:
            return None
        return _worktree_untracked_summary_for_path(wt_path)
    except Exception:
        _log.warning("worktree untracked summary failed for %s", group_id, exc_info=True)
        return None


def probe_worktree_pending_changes(wt_path: Path) -> Optional[bool]:
    """Read-only, lock-free: does this worktree carry any change that is not tool
    debris, tracked or not — regardless of whether the project's git INTEGRATION is
    on (flowgate.default.0548 T0004 §4/R6).

    Unlike :func:`_stage_worker_edits` this never runs ``git add``: it exists only for
    the case where the real commit gate (config off, no registered group git state)
    is what stops :func:`create_tr_commit` from ever asking the worktree itself, so a
    TR that (wrongly, or by omission) declared "no changes" would otherwise be taken
    at its word. No lock is taken because nothing here can race a concurrent commit —
    a plain status read changes nothing.

    Returns ``None`` when git genuinely cannot answer (no git binary, the path is not
    a real repo, a timeout) — the caller then has nothing but whatever other signal it
    already had, exactly as before this existed.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        proc = _gs._run_git(
            ["status", "--porcelain", "-z", "--untracked-files=all"],
            cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
    except GitServiceError:
        return None
    if proc.returncode != 0:
        return None
    for entry in (proc.stdout or "").split("\0"):
        if len(entry) < 4:
            continue
        if not path_exclusion_rules.is_excluded_path(entry[3:]):
            return True
    return False


def _commits_present(wt_path: Path, shas: Sequence[str]) -> bool:
    """Is every target commit an ancestor of this worktree's HEAD? (L0007 §4.1 G11)

    Fail-closed on purpose: if the branch was re-provisioned from base HEAD, or moved
    by hand, the commits the ledger names are not in this tree and reverting "what is
    still here" would peel off somebody else's work. No cancel beats a partial one.
    """
    from modules.flow_gate.services import git_service as _gs
    for sha in shas:
        if not sha:
            return False
        proc = _gs._run_git(
            ["merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            return False
    return True


def _unmerged_paths(wt_path: Path) -> list[str]:
    """The conflicted paths of an in-flight merge or revert, worktree-relative and sorted."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        return []
    return sorted({line.strip() for line in (proc.stdout or "").splitlines() if line.strip()})


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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["status", "--porcelain", "--untracked-files=all"], cwd=repo)
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["check-ignore", "--", *paths], cwd=repo)
    # exit 1 = nothing ignored (empty stdout); 128 = failure → treat as none.
    return [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]


def _query_remote_ref(base_root: Path, cfg: dict, base_branch: str) -> Optional[str]:
    """Best-effort, network ``ls-remote`` read of the real current position of the
    remote base ref (D0006 §3.6 / L0007 §2.8.1) — ``None`` when the query itself
    fails (unreachable/timeout), which the caller must NOT treat as "not found"."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["ls-remote", "origin", f"refs/heads/{base_branch}"],
        cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
        username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
    )
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()[:1]
    if not line:
        return None
    sha = line[0].split("\t", 1)[0].strip()
    return sha or None


def _base_ahead_behind(
    base_root: Optional[Path], base_branch: str
) -> tuple[Optional[int], Optional[int]]:
    """(ahead, behind) of the base checkout vs origin/{base}, from the last
    fetch — no network git (P §2-1). Both None when origin/{base} is absent
    (never fetched), git is unavailable, or the base checkout is missing:
    "unmeasured" is distinct from "in sync" (L §5)."""
    from modules.flow_gate.services import git_service as _gs
    if base_root is None or not _gs.git_available():
        return None, None
    if not (base_root / ".git").exists():
        return None, None
    if not _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        return None, None
    proc = _gs._run_git(
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
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=repo)
    return (proc.stdout or "").strip() or None if proc.returncode == 0 else None


def _rev_parse(repo: Path, rev: str, *, short: bool = False) -> Optional[str]:
    from modules.flow_gate.services import git_service as _gs
    args = ["rev-parse"]
    if short:
        args.append("--short")
    args.append(rev)
    proc = _gs._run_git(args, cwd=repo)
    return (proc.stdout or "").strip() or None if proc.returncode == 0 else None


def _full_sha_matches(full_sha: str, candidate: str) -> bool:
    full = (full_sha or "").lower()
    cand = (candidate or "").lower()
    return bool(full and cand and (full.startswith(cand) or cand.startswith(full)))


def _unpushed_commits(base_root: Optional[Path], base_branch: str) -> Optional[list[dict]]:
    from modules.flow_gate.services import git_service as _gs
    if base_root is None or not _gs.git_available() or not (base_root / ".git").exists():
        return None
    if not _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
        return None
    proc = _gs._run_git(
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
    from modules.flow_gate.services import git_service as _gs
    if base_root is None or not _gs.git_available() or not (base_root / ".git").exists():
        return False
    return not _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}")


def _local_commit_count(base_root: Optional[Path]) -> Optional[int]:
    """Commits reachable from the base checkout's HEAD, or None when it cannot be
    counted (git off, no checkout, unborn HEAD). Lets the client tell "nothing to
    push yet" apart from "one snapshot commit waiting for its first push"."""
    from modules.flow_gate.services import git_service as _gs
    if base_root is None or not _gs.git_available() or not (base_root / ".git").exists():
        return None
    proc = _gs._run_git(["rev-list", "--count", "HEAD"], cwd=base_root)
    if proc.returncode != 0:
        return None
    txt = (proc.stdout or "").strip()
    return int(txt) if txt.isdigit() else None


def _merge_in_progress(base_root: Path) -> bool:
    """True while a conflict session holds the base checkout mid-merge — commit
    and revert must not touch that intermediate state (resolve/abort only).

    0594 T0012: a managed merge-target workspace is a LINKED worktree whose ``.git``
    is a file pointing at ``<repo>/.git/worktrees/<name>`` — ask git there. The
    shared base checkout (a real ``.git`` directory) keeps the direct file check."""
    git_path = base_root / ".git"
    if git_path.is_file():
        from modules.flow_gate.services import git_service as _gs
        proc = _gs._run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=base_root)
        return proc.returncode == 0
    return (git_path / "MERGE_HEAD").exists()
