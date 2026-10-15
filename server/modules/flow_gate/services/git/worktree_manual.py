"""Group worktree status, manual index mutations, commit and revert (0066 D#1, E1--E6).

No automatic absorption and no changes to the legacy scope/diff endpoints.
Snapshots contain immutable object IDs; later consumers need not reopen the index.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from modules.flow_gate.db import operation_job
from modules.flow_gate.documents import tr2_service
from modules.flow_gate.services import git_service as gs, path_exclusion_rules, tr2_file_policy as tr2
from modules.flow_gate.storage.paths import get_storage_root
from . import approval_intent, finalize, lock, lock_manager, manual_path_policy as policy, scope_base
from .credentials import GitServiceError

OPERATIONS = ("stage", "unstage", "commit", "revert", "remove")
OWNERSHIP_OPERATIONS = ("stage", "commit", "revert", "remove", "suggest")
FLOOR_ERRORS = ("group_work_base_unverified", "group_work_base_diverged")


def _git(root: Path, args: list[str]) -> str:
    proc = gs._run_git(args, cwd=root, timeout=gs.GIT_READ_TIMEOUT_SEC)
    if proc.returncode:
        # Git stderr can contain paths not present in the requested path list.
        # Keep it server-side, never copy it into a response or exception message.
        gs._log.warning("Manual Git command failed: %s", proc.stderr)
        raise GitServiceError(500, "git_error", "Git operation failed")
    return proc.stdout or ""


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _context(group_id: str, *, required: bool = True):
    project_id = gs._project_of_group(group_id)
    cfg = gs.db_git.get_config(project_id) or {}
    state = gs.db_git.get_state(group_id) or {}
    root = None
    if (cfg.get("enabled") and state.get("project_id") == project_id
            and state.get("worktree_registered") and state.get("branch")):
        root, _ = gs.effective_src_root_ex(project_id, group_id, state)
    if root is not None:
        root = Path(root)
        # The slot ledger must still name the checked-out branch, never Base or another group.
        probe = gs._run_git(["symbolic-ref", "--quiet", "--short", "HEAD"],
                            cwd=root, timeout=gs.GIT_READ_TIMEOUT_SEC)
        if probe.returncode or (probe.stdout or "").strip() != state["branch"]:
            root = None
    if root is None and required:
        raise GitServiceError(409, "invalid_state", "Group worktree is unavailable",
                              details={"reason": "worktree_unavailable"})
    return project_id, cfg, state, root


@dataclass(frozen=True)
class IndexSnapshot:
    head: str
    entries: tuple[tuple[str, str, str, int], ...]  # path, mode, oid, stage
    object_dir: Path

    @property
    def index_token(self) -> str:
        return _fingerprint((self.head, self.entries))

    def object_git(self, args: list[str]) -> str:
        # The common object store survives removal of the group's linked worktree.
        return _git(self.object_dir, ["--git-dir=" + str(self.object_dir), *args])


def _read_index_once(root: Path) -> IndexSnapshot:
    head = _git(root, ["rev-parse", "--verify", "HEAD"]).strip()
    entries = []
    for line in _git(root, ["ls-files", "--stage", "-z"]).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, oid, stage = meta.split()
            entries.append((path, mode, oid, int(stage)))
    common = Path(_git(root, ["rev-parse", "--git-common-dir"]).strip())
    if not common.is_absolute():
        common = root / common
    return IndexSnapshot(head, tuple(sorted(entries)), common.resolve())


def read_index_snapshot(group_id: str, *, attempts: int = 3) -> IndexSnapshot:
    """Lock-free double read. T#4 can choose a retry bound without changing the format."""
    for _ in range(attempts):
        try:
            *_, root = _context(group_id)
            first = _read_index_once(root)
            *_, next_root = _context(group_id)
            second = _read_index_once(next_root)
        except (GitServiceError, OSError) as exc:
            raise GitServiceError(409, "invalid_state", "Group worktree is unavailable",
                                  details={"reason": "worktree_unavailable"}) from exc
        if first == second and root == next_root:
            return first
    raise GitServiceError(409, "worktree_changed", "Index changed while taking a snapshot")


def snapshot_changes(snapshot: IndexSnapshot) -> tuple[dict, dict]:
    """HEAD entries and staged letters, derived only from immutable snapshot objects."""
    head = {}
    for line in snapshot.object_git(["ls-tree", "-r", "-z", snapshot.head]).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _kind, oid = meta.split()
            head[path] = (mode, oid)
    index = {path: (mode, oid) for path, mode, oid, stage in snapshot.entries if stage == 0}
    unmerged = {path for path, _mode, _oid, stage in snapshot.entries if stage != 0}
    staged = {}
    for path in sorted(head.keys() | index.keys() | unmerged):
        before, after = head.get(path), index.get(path)
        if path in unmerged:
            staged[path] = "U"
        elif before != after:
            staged[path] = ("A" if before is None else "D" if after is None else
                            "T" if before[0][:2] != after[0][:2] else "M")
    return head, staged


def _name_status(root: Path, args: list[str]) -> dict[str, str]:
    fields = _git(root, ["diff", "--no-renames", "--name-status", "-z", *args, "--"]).split("\0")
    return {fields[i + 1]: fields[i][0] for i in range(0, len(fields) - 1, 2) if fields[i]}


def _working_digest(root: Path, path: str) -> str:
    target = root / path
    try:
        # Hash link text rather than following a link to an unrelated file.
        if target.is_symlink():
            return _fingerprint(("link", os.readlink(target)))
        if target.is_dir():
            return "directory"
        with target.open("rb") as handle:
            digest = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            return _fingerprint((target.stat().st_mode, digest.hexdigest()))
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise GitServiceError(500, "git_error", "Cannot inspect working file") from exc


def _collect(root: Path, ownership: policy.Ownership) -> dict:
    snapshot = _read_index_once(root)
    head, staged = snapshot_changes(snapshot)
    unstaged = _name_status(root, [])
    untracked = {p for p in _git(root, ["ls-files", "--others", "--exclude-standard", "-z"]).split("\0") if p}
    entries = []
    for path in sorted(staged.keys() | unstaged.keys() | untracked):
        # Unmerged working changes are represented by staged U (no unstaged U contract).
        working = unstaged.get(path)
        if working == "U":
            working = "M"
        row = {"path": path, "in_head": path in head, "staged": staged.get(path),
               "unstaged": working, "untracked": path in untracked,
               "insertions": None, "deletions": None}
        row.update(policy.classify(path, in_head=row["in_head"], staged=row["staged"],
                                  unstaged=working, untracked=row["untracked"], ownership=ownership))
        entries.append(row)
    token = _fingerprint((snapshot.index_token, [
        (r["path"], r["unstaged"], r["untracked"], _working_digest(root, r["path"]))
        for r in entries if r["unstaged"] or r["untracked"]
    ]))
    return {"snapshot": snapshot, "entries": entries, "worktree_token": token}


def _admission(group_id: str, state: dict) -> None:
    lock.guard_group_branch_merge_free(group_id)
    session = gs.db_git.get_open_session_by_group(group_id)
    if session is not None:
        raise GitServiceError(409, "invalid_state", "A Git session is open",
                              details={"reason": "session_open",
                                       "session_kind": gs.db_git.session_kind(session)})
    if (state.get("status") or "none") not in ("none", "awaiting_choice", "waiting"):
        raise GitServiceError(409, "invalid_state", "Group state blocks manual Git",
                              details={"reason": "group_state"})
    finalize._guard_group_update_ai_idle(group_id)


def _blockers(group_id: str, state: dict) -> list[dict]:
    result = []
    try:
        _admission(group_id, state)
    except GitServiceError as exc:
        result.append({"code": "session_open" if exc.details.get("reason") == "session_open" else exc.code,
                       "operations": list(OPERATIONS)})
    if operation_job.get_group_freeze_claim(group_id):
        result.append({"code": "group_frozen_for_approval", "operations": list(OPERATIONS)})
    return result


def worktree_status(group_id: str) -> dict:
    project_id, cfg, state, root = _context(group_id, required=False)
    counts = dict.fromkeys(("changed", "staged", "unstaged", "untracked", "excluded_new_untracked",
                            "hidden", "managed_dirty", "unsupported"), 0)
    floor = {key: state.get(key) for key in ("work_base_ref", "work_base_sha",
                                           "work_base_sync_sha", "work_base_state")}
    floor.update(scope_base_sha=None, error=None)
    result = {"group_id": group_id, "branch": state.get("branch"), "head": None,
              "worktree_available": root is not None, "worktree_token": None, "index_token": None,
              "git_state": {"status": state.get("status") or "none",
                            "root_wf_done": gs._group_root_wf_done(group_id),
                            "final_approval_bound": approval_intent.group_is_final_approval_bound(group_id)},
              "floor": floor, "committed": None, "files": [], "counts": counts,
              "ownership": {"available": False, "reason": None}, "blockers": []}
    if root is None:
        result["blockers"] = [{"code": "git_inactive" if not cfg.get("enabled") else "worktree_unavailable",
                               "operations": list(OPERATIONS)}]
        return result
    ownership = policy.read_ownership(group_id)
    current = _collect(root, ownership)
    snapshot = current["snapshot"]
    result.update(head=snapshot.head, worktree_token=current["worktree_token"],
                  index_token=snapshot.index_token, ownership=ownership.view(),
                  blockers=_blockers(group_id, state))
    if not ownership.available:
        result["blockers"].append({
            "code": "tr_history_recovery_required" if ownership.reason == "tr_history_recovery_required"
                    else "ownership_unavailable", "operations": list(OWNERSHIP_OPERATIONS)})
    for row in current["entries"]:
        counts["staged"] += bool(row["staged"])
        counts["unstaged"] += bool(row["unstaged"])
        counts["untracked"] += row["untracked"]
        counts["unsupported"] += bool(row["unsupported"])
        if row["hidden"]:
            counts["hidden"] += 1
        elif row["class"] == "managed":
            counts["managed_dirty"] += 1
        if not row["visible"] and not row["hidden"]:
            counts["excluded_new_untracked"] += 1
        if row["visible"]:
            result["files"].append({k: v for k, v in row.items() if k not in ("hidden", "visible")})
    counts["changed"] = len(result["files"])
    try:
        resolved = scope_base.resolve_scope_floor(project_id, group_id, root, state=state,
                                                  config=cfg, head=snapshot.head)
        floor.update({k: v for k, v in resolved.items() if k != "floor_sha"})
        floor["scope_base_sha"] = resolved["floor_sha"]
        count = int(_git(root, ["rev-list", "--count", resolved["floor_sha"] + ".." + snapshot.head]).strip())
        changed = _name_status(root, [resolved["floor_sha"], snapshot.head])
        result["committed"] = {"commit_count": count, "changed_file_count": len(changed)}
    except GitServiceError as exc:
        if exc.code not in FLOOR_ERRORS:
            raise
        # The floor resolver has structured metadata, never raw Git diagnostic text.
        floor["error"] = policy.public_details(
            {"code": exc.code, "message": exc.message, "details": exc.details},
            [row["path"] for row in current["entries"]])
    return result


@contextmanager
def mutation_context(group_id: str):
    project_id, _, state, _ = _context(group_id)
    _admission(group_id, state)
    outcome, ctx = lock_manager.acquire_group(project_id, group_id, holder_kind="source_mutation")
    if not outcome.ok:
        raise GitServiceError(409, "git_busy", "Another Git operation is in progress",
                              details=lock_manager.outcome_details(outcome))
    try:
        context = _context(group_id)
        _admission(group_id, context[2])
        if not lock_manager.still_held(ctx, outcome.lock_key):
            raise GitServiceError(409, "git_busy", "Group lock was lost")
        yield context
    finally:
        lock_manager.release(ctx, outcome.lock_key)


def _git_paths(root: Path, command: list[str], paths: list[str]) -> None:
    if not paths:
        return
    # One Git transaction, with no argv length limit and no pathspec expansion.
    # Runtime scratch is outside every source tree (tests supply their run scratch).
    scratch = Path(os.environ["FLOWGATE_TEST_SCRATCH"]) if os.environ.get("FLOWGATE_TEST_SCRATCH") else (
        get_storage_root(create=True) / "scratch" / "_manual_git")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="paths-", dir=scratch) as temporary:
        pathspec = Path(temporary) / "paths"
        pathspec.write_bytes(b"\0".join(p.encode("utf-8") for p in paths) + b"\0")
        _git(root, ["--literal-pathspecs", *command, "--pathspec-from-file=" + str(pathspec),
                    "--pathspec-file-nul"])


def _apply_paths(root: Path, paths: list[str], *, unstage: bool) -> None:
    _git_paths(root, ["reset", "-q", "HEAD"] if unstage else ["add"], paths)


def _updated_status(group_id: str, result: dict) -> dict:
    try:
        result["status"] = worktree_status(group_id)
        result["status_error"] = None
    except Exception as exc:
        gs._log.warning("Manual Git succeeded; status refresh failed", exc_info=True)
        result["status"] = None
        result["status_error"] = exc.code if isinstance(exc, GitServiceError) else "status_unavailable"
    return result


def _change_index(group_id: str, *, paths: list[str] | None, all: bool,
                  worktree_token: str | None, unstage: bool) -> dict:
    selected = policy.normalize_paths(paths, all)
    policy.guard_selected_visibility(selected, unstage=unstage)
    with mutation_context(group_id) as (_, _, _, root):
        # Read ownership only for Stage admission; Unstage always has an exit.
        ownership = policy.Ownership(frozenset()) if unstage else policy.read_ownership(group_id)
        current = _collect(root, ownership)
        if worktree_token is not None and worktree_token != current["worktree_token"]:
            raise GitServiceError(409, "worktree_changed", "Worktree changed; reload before retrying")
        if not unstage:
            ownership.require()
        rows = {row["path"]: row for row in current["entries"]}
        skipped = dict.fromkeys(("managed", "hidden", "excluded_new", "unsupported"), 0)
        if all:
            selected = []
            for path, row in rows.items():
                if unstage:
                    if row["staged"]:
                        selected.append(path)
                elif row["can_stage"]:
                    selected.append(path)
                else:
                    key = ("hidden" if row["hidden"] else "unsupported" if row["unsupported"]
                           else "managed" if row["class"] == "managed" else "excluded_new")
                    skipped[key] += 1
        else:
            refused = []
            for path in selected:
                row = rows.get(path)
                reason = ("not_changed" if row is None else
                          "not_staged" if unstage and not row["staged"] else
                          None if unstage or row["can_stage"] else
                          "index_entry_unsupported" if row["unsupported"] else row["protected_reason"])
                if reason:
                    refused.append({"path": path, "reason": reason})
            if refused:
                raise GitServiceError(409, "path_protected", "Requested paths cannot be changed",
                                      details={"paths": refused})
        if unstage and all:
            _git(root, ["read-tree", "--reset", "HEAD"])
        else:
            _apply_paths(root, selected, unstage=unstage)
        result = {"applied": policy.visible_paths(selected),
                  "applied_hidden_count": sum(policy.hidden(p) for p in selected) if unstage and all else 0,
                  "skipped": skipped}
        return _updated_status(group_id, result)


def stage(group_id: str, *, paths: list[str] | None = None, all: bool = False,
          worktree_token: str | None = None) -> dict:
    return _change_index(group_id, paths=paths, all=all, worktree_token=worktree_token, unstage=False)


def unstage(group_id: str, *, paths: list[str] | None = None, all: bool = False,
            worktree_token: str | None = None) -> dict:
    return _change_index(group_id, paths=paths, all=all, worktree_token=worktree_token, unstage=True)


# ── E4 commit, E5 revert, E6 remove (0066 D#1 §3-4, §3-5) ──────────────────

def _commit_subject(message) -> str:
    # Same normalizer, limit and wording as FinalizeBody.commit_message; no lock yet.
    subject = gs.normalize_subject(message)
    if not subject:
        raise GitServiceError(422, "commit_message_required", "A commit message is required")
    if len(subject) > gs.COMMIT_SUBJECT_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject):
        raise GitServiceError(422, "invalid_request",
                              f"message must be a single line of at most {gs.COMMIT_SUBJECT_MAX} characters.")
    return subject


def _compare_tokens(current: dict, *, worktree_token: str | None = None,
                    index_token: str | None = None) -> None:
    if ((worktree_token is not None and worktree_token != current["worktree_token"])
            or (index_token is not None and index_token != current["snapshot"].index_token)):
        raise GitServiceError(409, "worktree_changed", "Worktree changed; reload before retrying")


def _require_history_recovered(group_id: str) -> None:
    # A commit rewrites the Group branch history, so it uses general_source_mutation's rule:
    # an unreadable recovery state is treated as recovery required.
    try:
        required = tr2.db_recovery.has_unresolved(group_id)
    except Exception:
        gs._log.warning("Manual Git recovery state unavailable for %s", group_id, exc_info=True)
        required = True
    if required:
        raise GitServiceError(409, "tr_history_recovery_required", "TR history recovery is required",
                              details={"reason": "tr_history_recovery_required"})


def commit(group_id: str, *, message, worktree_token: str | None = None,
           index_token: str | None = None) -> dict:
    """Commit exactly the index. Never stages, unstages, absorbs, merges or pushes."""
    subject = _commit_subject(message)
    with mutation_context(group_id) as (_, cfg, _, root):
        _require_history_recovered(group_id)
        ownership = policy.read_ownership(group_id)
        ownership.require()
        current = _collect(root, ownership)
        _compare_tokens(current, worktree_token=worktree_token, index_token=index_token)
        staged = [row for row in current["entries"] if row["staged"]]
        if not staged:
            raise GitServiceError(422, "nothing_staged", "No staged changes to commit")
        unsupported = [row for row in staged if row["staged"] in ("U", "T")]
        if unsupported:
            raise GitServiceError(409, "index_entry_unsupported", "The index has unsupported entries", details={
                "counts": {"unmerged": sum(row["staged"] == "U" for row in unsupported),
                           "type_change": sum(row["staged"] == "T" for row in unsupported)},
                "paths": [row["path"] for row in unsupported if not row["hidden"]]})
        counts = dict.fromkeys(("hidden", "managed", "excluded_new"), 0)
        protected = []
        for row in staged:
            key = ("hidden" if row["hidden"] else "managed" if row["class"] == "managed"
                   else "excluded_new" if row["protected_reason"] == "excluded_new" else None)
            if key:
                counts[key] += 1
                if key != "hidden":
                    protected.append(row["path"])
        if any(counts.values()):
            # The server never unstages on the requester's behalf.
            raise GitServiceError(409, "staged_protected_paths", "Protected paths are staged",
                                  details={"counts": counts, "paths": protected})
        proc = gs._run_git([*gs._GIT_IDENT, "commit", "-q", "-m", subject], cwd=root,
                           author_env=gs._author_env_from_cfg(cfg))
        if proc.returncode:
            gs._log.warning("Manual Git commit failed: %s", proc.stderr)
            raise GitServiceError(500, "git_error", "Git commit failed")
        sha = _git(root, ["rev-parse", "--verify", "HEAD"]).strip()
        paths = [row["path"] for row in staged]
        result = {"commit_sha": sha, "commit": sha[:7], "subject": subject,
                  "committed_paths": paths, "committed_count": len(paths)}
        return _updated_status(group_id, result)


def _source_mutation_error(exc: Exception, paths: list[str]) -> GitServiceError:
    """Map general_source_mutation refusals without copying upstream details."""
    if isinstance(exc, GitServiceError):
        return exc
    if isinstance(exc, tr2.Tr2OwnershipInvariantError):
        gs._log.warning("Manual Git ownership invariant: %s", exc.details)
        return GitServiceError(409, "ownership_unavailable", "Source ownership could not be verified",
                               details={"reason": "ownership_invariant"})
    if isinstance(exc, tr2.Tr2FilePolicyError):
        if exc.code == tr2.SOURCE_MUTATION_BUSY:
            return GitServiceError(409, "git_busy", "Another Git operation is in progress", details={
                k: v for k, v in exc.details.items() if k not in ("code", "project_id", "group_id")})
        if exc.code == tr2.SOURCE_WORKTREE_UNAVAILABLE:
            return GitServiceError(409, "invalid_state", "Group worktree is unavailable",
                                   details={"reason": "worktree_unavailable"})
        if exc.code == tr2.TR_HISTORY_RECOVERY_REQUIRED:
            return GitServiceError(409, "tr_history_recovery_required", "TR history recovery is required",
                                   details={"reason": "tr_history_recovery_required"})
        if exc.code in (tr2.TR2_MANAGED_FILE, tr2.SOURCE_PATH_ALIAS_NOT_ALLOWED):
            reason = "managed" if exc.code == tr2.TR2_MANAGED_FILE else "alias"
            shown = [p for p in paths if p == exc.path and not policy.hidden(p)]
            return GitServiceError(409, "path_protected", "Requested paths cannot be changed", details={
                "cause_code": exc.code, "paths": [{"path": p, "reason": reason} for p in shown]})
        if exc.code == tr2.SOURCE_PATH_INVALID:
            return GitServiceError(422, "invalid_request", "Invalid file path",
                                   details={"reason": "invalid_path"})
    if isinstance(exc, tr2_service.Tr2ValidationError):
        return GitServiceError(409, "invalid_state", "Group worktree is unavailable",
                               details={"reason": "worktree_unavailable"})
    gs._log.warning("Manual Git ownership read failed", exc_info=exc)
    return GitServiceError(409, "ownership_unavailable", "Source ownership could not be verified",
                           details={"reason": "ownership_read_failed"})


def _ignores_case(root: Path) -> bool:
    proc = gs._run_git(["config", "--bool", "core.ignorecase"], cwd=root, timeout=gs.GIT_READ_TIMEOUT_SEC)
    return not proc.returncode and (proc.stdout or "").strip() == "true"


def _disk_case_alias(root: Path, path: str) -> bool:
    current = root
    for part in path.split("/"):
        try:
            names = os.listdir(current)
        except OSError:
            return False
        if part not in names:
            return any(name.casefold() == part.casefold() for name in names)
        current = current / part
    return False


def _change_files(group_id: str, *, paths: list[str], worktree_token: str | None, remove: bool) -> dict:
    selected = policy.normalize_paths(paths, False)
    policy.guard_selected_visibility(selected)
    project_id, _, state, _ = _context(group_id)
    _admission(group_id, state)
    with ExitStack() as stack:
        try:
            # G, worktree re-resolution, no-alias, TR history recovery, TR2 ownership.
            mutation = stack.enter_context(tr2.general_source_mutation(
                project_id, group_id, exact_paths=selected, allow_missing_leaf=True))
        except Exception as exc:
            raise _source_mutation_error(exc, selected) from None
        _, _, state, root = _context(group_id)
        _admission(group_id, state)
        if root.resolve() != Path(mutation.root).resolve():
            raise GitServiceError(409, "invalid_state", "Group worktree is unavailable",
                                  details={"reason": "worktree_unavailable"})
        ownership = policy.read_ownership(group_id)
        ownership.require()
        current = _collect(root, ownership)
        _compare_tokens(current, worktree_token=worktree_token)
        head, _ = snapshot_changes(current["snapshot"])
        rows = {row["path"]: row for row in current["entries"]}
        known = set(head) | set(rows) | {entry[0] for entry in current["snapshot"].entries}
        folders = {p.rsplit("/", 1)[0] for p in known if "/" in p}
        folders |= {f.rsplit("/", i)[0] for f in folders for i in range(1, f.count("/") + 1)}
        targets = dict(mutation.exact_targets)
        if any(p in folders or targets[p].is_dir() for p in selected):
            raise GitServiceError(422, "invalid_request", "Only file paths are accepted",
                                  details={"reason": "not_a_file"})
        folded = {p.casefold() for p in known}
        ignorecase = _ignores_case(root)
        refused = []
        for path in selected:
            row = rows.get(path)
            reason = ("alias" if ignorecase and path not in known and (
                          path.casefold() in folded or _disk_case_alias(root, path)) else
                      "index_entry_unsupported" if row and row["unsupported"] else
                      "managed" if path in ownership.paths else
                      "excluded_new" if remove and path not in head
                      and path_exclusion_rules.is_excluded_path(path) else None)
            if reason:
                refused.append({"path": path, "reason": reason})
        if refused:
            details = {"paths": refused}
            if any(r["reason"] == "alias" for r in refused):
                details["cause_code"] = tr2.SOURCE_PATH_ALIAS_NOT_ALLOWED
            raise GitServiceError(409, "path_protected", "Requested paths cannot be changed", details=details)
        if remove:
            mismatch = [p for p in selected if p in head]
            if mismatch:
                raise GitServiceError(422, "existing_path_use_revert", "Existing paths must be reverted",
                                      details={"paths": mismatch})
        else:
            mismatch = [p for p in selected if p not in head]
            if mismatch:
                raise GitServiceError(422, "new_path_use_remove", "New paths must be removed",
                                      details={"paths": mismatch})
        # An ignored new file is not a listed change, but naming it still removes it.
        changed = [p for p in selected if p in rows or remove and (
            targets[p].exists() or targets[p].is_symlink())]
        if remove:
            # Dropping an index entry absent from HEAD leaves no trace in the index.
            _git_paths(root, ["reset", "-q", "HEAD"], [p for p in changed if p in rows and rows[p]["staged"]])
            for path in changed:
                try:
                    targets[path].unlink(missing_ok=True)
                except OSError as exc:
                    gs._log.warning("Manual Git remove failed", exc_info=True)
                    raise GitServiceError(500, "git_error", "Cannot remove file") from exc
        else:
            _git_paths(root, ["checkout", "HEAD"], changed)
        done = "removed" if remove else "reverted"
        result = {"results": [{"path": p, "result": done if p in changed else "not_changed"} for p in selected]}
        return _updated_status(group_id, result)


def revert(group_id: str, *, paths: list[str], worktree_token: str | None = None) -> dict:
    return _change_files(group_id, paths=paths, worktree_token=worktree_token, remove=False)


def remove(group_id: str, *, paths: list[str], worktree_token: str | None = None) -> dict:
    return _change_files(group_id, paths=paths, worktree_token=worktree_token, remove=True)
