"""The Git steps of a confirm gate's ``gate_publish`` job (flowgate.v02.0085 T#3, D0007 §4 / §7.1 / §7.3).

The internal adapter that reuses the v0.1 finalize's checks, merge, push and evidence pieces without its AC approval /
root ``wf_done`` premise, its absorb commit, its slot cleanup or its push rollback (D0007 §4):

- the slot and its guards are finalize's (``_finalize_context``: an enabled Git integration and a registered group
  worktree; ``guard_base_free``, ``raise_if_base_checkout_off_branch``, the tracked-dirty refusal of the base checkout);
- a merge is finalize's attempt record (``merge_target.open_attempt`` before any merge work, ``record_merge_inputs``,
  ``git merge --no-ff`` with the FlowGate identity and the configured author, the landed-merge check of a failed
  command, the conflict session with its EOL separation and rerere checkpoint). The attempt is opened as a manual
  merge (``origin=manual_merge``, action ``merge_only``): a gate is no group completion, so the reviewed end of its
  conflict keeps the group's slot and puts the ledger back to ``none`` exactly as a manual merge does, and the attempt
  never pushes - the push is the job's own step. The session context carries ``gate_decision_id``;
- the frozen SHA is merged or pushed, never the branch name (the group branch may move after the snapshot);
- nothing here commits a dirty work tree (commit mode keeps the already integrated HEAD), resets a branch or pushes
  with force; a push is a plain push that git itself refuses unless it fast-forwards.

Every function runs under the locks its caller (``publish``) holds and never takes one. Every text that leaves here is
scrubbed (``redact``); a failure is a safe code.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flowgate.gate_decisions import redact

_log = logging.getLogger(__name__)

READ_ERROR = object()
ZERO = "0" * 40
REF_PIN = "refs/flowgate/gate-pin/"
REF_ARCHIVE = "refs/flowgate/gate-archive/"
REF_RECOVERY = "refs/flowgate/gate-recovery/"
ATTEMPT_ACTION = "merge_only"
CONTEXT_KEY = "gate_decision_id"
(MERGED, CONTAINED, CONFLICT) = ("merged", "contained", "conflict")
# push outcomes
(PUSHED, PUSH_TRANSIENT, PUSH_REJECTED, PUSH_UNKNOWN) = ("pushed", "transient", "rejected", "unknown")
_TRANSIENT_MARKS = ("could not resolve host", "connection refused", "connection timed out", "timed out",
                    "operation timed out", "failed to connect", "network is unreachable", "temporary failure",
                    "early eof", "the remote end hung up unexpectedly", "rpc failed", "timeout_expired")


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def _mt():
    from modules.flow_gate.services.git import merge_target
    return merge_target


def GitServiceError(*args, **kwargs):  # noqa: N802 — the v0.1 error class, imported late
    from modules.flow_gate.services.git.credentials import GitServiceError as _E
    return _E(*args, **kwargs)


def git_error_type():
    from modules.flow_gate.services.git.credentials import GitServiceError as _E
    return _E


def pin_ref(decision_id: str) -> str:
    return REF_PIN + decision_id


def archive_ref(decision_id: str) -> str:
    return REF_ARCHIVE + decision_id


def applied_ref(decision_id: str) -> str:
    return REF_ARCHIVE + decision_id + "-applied"


def recovery_ref(decision_id: str, part: str = "") -> str:
    return REF_RECOVERY + decision_id + (f"-{part}" if part else "")


def holder_of(job_id: str) -> str:
    return _mt().JOB_HOLDER_PREFIX + job_id


# ── the slot ─────────────────────────────────────────────────────────────────

@dataclass
class Slot:
    project_id: str
    group_id: str
    cfg: dict
    base_root: Path
    wt_path: Path
    group_branch: str

    @property
    def secrets(self) -> tuple:
        return redact.secrets_of(self.cfg)

    def credentials(self) -> dict:
        return {"username": self.cfg.get("username"), "secret": _gs()._load_secret_for(self.cfg) or ""}


def slot_of(group_id: str) -> Slot:
    """finalize's own slot read and entry guard (``_finalize_context``) - a GitServiceError when there is none."""
    cfg, state, project_id, base_root, wt_path = _gs()._finalize_context(group_id)
    return Slot(project_id, group_id, cfg, Path(base_root), Path(wt_path), str(state["branch"]).strip())


def _git(repo: Path, args: list, **kw):
    return _gs()._run_git(args, cwd=repo, **kw)


def rev(repo: Path, ref: str):
    """The exact ref's SHA, None when absent, READ_ERROR when git cannot tell."""
    proc = _git(repo, ["for-each-ref", "--format=%(refname) %(objectname)", ref])
    if proc.returncode != 0:
        return READ_ERROR
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.strip().partition(" ")
        if name == ref and sha:
            return sha
    return None


def commit_exists(repo: Path, sha: Optional[str]) -> bool:
    if not sha:
        return False
    return _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> Optional[bool]:
    proc = _git(repo, ["merge-base", "--is-ancestor", ancestor, descendant])
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def covers(repo: Path, basis_sha: Optional[str], head: Optional[str]) -> Optional[bool]:
    """``basis_sha`` is ``head`` or has it as an ancestor (D0007 §7.1: the basis must contain the real HEAD; the other
    direction - an old SHA that is only an ancestor of HEAD - is not enough)."""
    if not basis_sha or not head:
        return False
    if basis_sha == head:
        return True
    if not commit_exists(repo, basis_sha) or not commit_exists(repo, head):
        return None
    return is_ancestor(repo, head, basis_sha)


def create_ref(repo: Path, ref: str, sha: str) -> str:
    """Create ``ref`` at ``sha`` only when it is absent (``update-ref`` with the zero old value). ``created`` /
    ``same`` (already there at this SHA) / ``other`` (there at another SHA) / ``error``. Under M (the caller)."""
    current = rev(repo, ref)
    if current is READ_ERROR:
        return "error"
    if current == sha:
        return "same"
    if current is not None:
        return "other"
    proc = _git(repo, ["update-ref", ref, sha, ZERO])
    _mt().invalidate_check_targets()
    if proc.returncode != 0:
        again = rev(repo, ref)
        return "same" if again == sha else ("other" if again not in (None, READ_ERROR) else "error")
    return "created"


def delete_ref(repo: Path, ref: str, expected: str) -> bool:
    """Delete ``ref`` only while it still points at ``expected`` (CAS)."""
    deleted = _git(repo, ["update-ref", "-d", ref, expected]).returncode == 0
    _mt().invalidate_check_targets()
    return deleted


def uncommitted(wt_path: Path) -> Optional[int]:
    """How many entries of the work tree are not committed (None: unreadable). They are never taken into a gate's
    result (D0007 §7.1: no automatic stage / commit)."""
    if not wt_path.is_dir():
        return None
    proc = _git(wt_path, ["status", "--porcelain", "--untracked-files=all"])
    if proc.returncode != 0:
        return None
    return sum(1 for line in (proc.stdout or "").splitlines() if line.strip())


# ── the remote ───────────────────────────────────────────────────────────────

def _sync_origin(slot: Slot) -> bool:
    try:
        _gs().ensure_origin_matches_config(slot.base_root, (slot.cfg.get("repo_url") or "").strip())
        return True
    except git_error_type():
        return False


def remote_head(slot: Slot, branch: str):
    """origin's ``refs/heads/<branch>``: its SHA, ``""`` when absent, READ_ERROR when it cannot be read. Under R."""
    if not _sync_origin(slot):
        return READ_ERROR
    ref = f"refs/heads/{branch}"
    proc = _git(slot.base_root, ["ls-remote", "origin", ref], timeout=_gs().GIT_NET_TIMEOUT_SEC, **slot.credentials())
    if proc.returncode != 0:
        _log.info("gate remote read failed for %s: %s", slot.group_id, redact.scrub(proc.stderr, *slot.secrets))
        return READ_ERROR
    for line in (proc.stdout or "").splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref and sha.strip():
            return sha.strip()
    return ""


def fetch_branch(slot: Slot, branch: str) -> bool:
    """Bring origin's branch into ``refs/remotes/origin/<branch>`` (to judge a remote tip against our SHA). Under R."""
    if not _sync_origin(slot):
        return False
    proc = _git(slot.base_root, ["fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
                timeout=_gs().GIT_NET_TIMEOUT_SEC, **slot.credentials())
    _mt().invalidate_check_targets(slot.project_id)
    return proc.returncode == 0


def remote_contains(slot: Slot, branch: str, sha: str):
    """Does origin's branch hold ``sha`` (the same SHA or a descendant)? True / False / READ_ERROR. Under R."""
    head = remote_head(slot, branch)
    if head is READ_ERROR:
        return READ_ERROR
    if not head:
        return False
    if head == sha:
        return True
    if not commit_exists(slot.base_root, head) and not fetch_branch(slot, branch):
        return READ_ERROR
    found = is_ancestor(slot.base_root, sha, head)
    return READ_ERROR if found is None else found


def push(slot: Slot, sha: str, branch: str) -> tuple[str, str]:
    """A plain push of ``sha`` to origin's ``refs/heads/<branch>`` (never forced; git refuses anything but a
    fast-forward). Returns (outcome, safe code). A failed command is judged against the remote itself: holding the
    SHA (also after a timeout) is pushed; unreadable is unknown; else a network failure is transient and anything
    else (rejected, no permission, no repository) is rejected. Under R."""
    if not _sync_origin(slot):
        return PUSH_TRANSIENT, "origin_sync_failed"
    proc = _git(slot.base_root, ["push", "origin", f"{sha}:refs/heads/{branch}"],
                timeout=_gs().GIT_NET_TIMEOUT_SEC, **slot.credentials())
    if proc.returncode == 0:
        return PUSHED, "pushed"
    text = redact.scrub(proc.stderr, *slot.secrets)
    _log.warning("gate push failed for %s (%s): %s", slot.group_id, proc.returncode, text)
    landed = remote_contains(slot, branch, sha)
    if landed is True:
        return PUSHED, "push_landed_after_error"
    if landed is READ_ERROR:
        return PUSH_UNKNOWN, "push_result_unknown"
    lowered = (proc.stderr or "").lower()
    if proc.returncode == -1 or any(mark in lowered for mark in _TRANSIENT_MARKS):
        return PUSH_TRANSIENT, "push_network_error"
    if "auth" in lowered or "permission" in lowered or "403" in lowered or "401" in lowered:
        return PUSH_REJECTED, "push_auth_failed"
    if "not found" in lowered or "does not appear to be a git repository" in lowered:
        return PUSH_REJECTED, "push_remote_missing"
    return PUSH_REJECTED, "push_rejected"


# ── the merge into the target branch ─────────────────────────────────────────

def _session_rows(slot: Slot, decision_id: str, job_id: str) -> list[dict]:
    """This decision's merge attempts (its job's holder and its decision id), newest first."""
    gs, mt = _gs(), _mt()
    out = []
    for session in gs.db_git.sessions_by_group(slot.group_id):
        if gs.db_git.session_kind(session) != gs.db_git.SESSION_KIND_MERGE:
            continue
        rec = mt.target_record(session) or {}
        context = gs.db_git.session_context(session)
        if rec.get("lock_holder") == holder_of(job_id) and context.get(CONTEXT_KEY) == decision_id:
            out.append(session)
    out.sort(key=lambda s: int(s["merge_id"]), reverse=True)
    return out


def merge_state(slot: Slot, decision_id: str, job_id: str) -> Optional[dict]:
    """What an earlier claim's merge attempt left (under the target's lock): None = nothing (run the merge), else
    ``{"outcome": merged | conflict | conflict_closed | unsettled, ...}``. An interrupted attempt of this job is settled
    by finalize's own recovery (``recover_interrupted_attempt``: ``merge --abort`` of a half merge, a landed merge
    commit is the result) and then reads as nothing / merged."""
    gs, mt = _gs(), _mt()
    for session in _session_rows(slot, decision_id, job_id):
        merge_id = int(session["merge_id"])
        context = gs.db_git.session_context(session)
        if session.get("status") == "open":
            if mt.attempt_phase(session) == mt.PHASE_CONFLICT:
                files = [row["path"] for row in gs.db_git.session_files(merge_id)]
                return {"outcome": CONFLICT, "merge_id": merge_id, "files": files}
            verdict = mt.recover_interrupted_attempt(session, "gate_publish_resume")
            if verdict == mt.ATTEMPT_INTERRUPTED:
                return None
            if verdict != mt.ATTEMPT_COMPLETED:
                return {"outcome": "unsettled", "merge_id": merge_id}
            context = gs.db_git.session_context(gs.db_git.get_session(merge_id))
        state = context.get(mt.ATTEMPT_STATE_KEY)
        if state == mt.ATTEMPT_COMPLETED:
            result = context.get("attempt_result") or {}
            sha = result.get("merge_commit_sha") or (
                gs._rev_parse(slot.base_root, result["merge_commit"]) if result.get("merge_commit") else None)
            return {"outcome": MERGED, "merge_id": merge_id, "merge_commit": sha}
        if context.get("conflict_handed_off") or context.get("review_state") or state == mt.ATTEMPT_CONFLICT:
            return {"outcome": "conflict_closed", "merge_id": merge_id}
        return None
    return None


def validate_merge_target(slot: Slot, decision: dict, job_id: str) -> None:
    """Under the recorded B/W lock, compare own evidence before revalidating a new-contract target.

    Legacy decisions retain their recorded target/effective contract. Nothing here repairs a ref or an owner.
    """
    settings = decision.get("settings") or {}
    if "merge_target_branch" not in (settings.get("shown") or {}):
        return
    mt, gs = _mt(), _gs()
    basis = settings.get("target_basis") or {}
    branch = decision.get("target_branch")
    accepted = basis.get("accepted_target_sha")
    evidence = {"target_branch": branch, "accepted_target_sha": accepted, "stage": "target_check",
                "merge_id": None, "changed": None}

    def refuse(code, **more):
        raise GitServiceError(409, code, "the accepted merge target cannot be used", details={**evidence, **more})

    if not branch or not accepted or not basis.get("work_base_ref") or not isinstance(basis.get("is_project_base"), bool):
        refuse("gate_git_unavailable", reason="git_context_changed")
    rows = _session_rows(slot, decision["decision_id"], job_id)
    own = rows[0] if rows else None
    fixed = mt.raise_if_retarget(slot.group_id, branch)
    if fixed is not None and (own is None or int(fixed.merge_id) != int(own["merge_id"])):
        refuse("merge_target_busy", merge_id=fixed.merge_id)
    if own is not None:
        evidence["merge_id"] = own["merge_id"]
        rec = mt.target_record(own) or {}
        if rec.get("branch") != branch:
            refuse("merge_target_locked")
        context = gs.db_git.session_context(own)
        if context.get(mt.ATTEMPT_STATE_KEY) == mt.ATTEMPT_COMPLETED:
            # A completed attempt is reconciled from its own result, never from today's tip.
            return
    if (branch == mt.project_base_branch(slot.cfg)) != basis["is_project_base"]:
        refuse("gate_git_unavailable", reason="git_context_changed")
    observed = rev(slot.base_root, f"refs/heads/{branch}")
    if observed is READ_ERROR:
        refuse("gate_git_unavailable", reason="probe_failed", observed_unavailable=True)
    evidence.update(observed_target_sha=observed, target_deleted=observed is None)
    if observed is None:
        refuse("merge_target_not_found")
    own_id = int(own["merge_id"]) if own is not None and own.get("status") == "open" else None
    if observed == accepted:
        evidence["changed"] = False
    if observed != accepted:
        landed = False
        if own_id is not None:
            inputs = gs.db_git.session_context(own).get(mt.MERGE_INPUTS_KEY) or {}
            ctx = mt.resolve_session_target(own)
            from modules.flow_gate.services.git.finalize import _merge_commit_landed
            landed = (inputs.get("pre_head") == accepted and
                      inputs.get("source_head") == decision.get("frozen_sha") and ctx.root is not None and
                      _merge_commit_landed(ctx.root, accepted, inputs["source_head"]))
        if not landed:
            refuse("target_moved")
        evidence["changed"] = True
    try:
        mt.judge_check_target(slot.project_id, slot.group_id, branch,
                              work_base_ref=basis["work_base_ref"], scope_floor_sha=basis.get("scope_floor_sha"),
                              own_merge_id=own_id)
    except git_error_type() as exc:
        # Keep only the public context. Git diagnostics, paths and owner markers stay internal.
        details = {**evidence}
        if (exc.details or {}).get("reason"):
            details["reason"] = exc.details["reason"]
        raise GitServiceError(409, exc.code, "the accepted merge target cannot be used", details=details) from exc


def merge(slot: Slot, decision_id: str, job_id: str, target_branch: str, frozen: str, on_started,
          *, target_sha: Optional[str] = None) -> dict:
    """Merge the frozen SHA into the recorded local target branch (under its B or W). ``{"outcome": contained}`` when the
    target already holds it (no attempt, no commit), ``merged`` with the merge commit, or ``conflict`` (the attempt is
    the conflict session of the existing merge review). A refusal before the merge is a GitServiceError and leaves
    nothing; ``on_started(evidence)`` is called right before ``git merge`` (its effect is unknown from then on)."""
    gs, mt = _gs(), _mt()
    from modules.flow_gate.services.git.commit import _merge_commit_subject
    from modules.flow_gate.services.git.credentials import _author_env_from_cfg
    from modules.flow_gate.services.git.finalize import _merge_commit_landed
    target_ref = f"refs/heads/{target_branch}"
    current = rev(slot.base_root, target_ref)
    if target_sha is not None and (current is READ_ERROR or current != target_sha):
        details = {"target_branch": target_branch, "accepted_target_sha": target_sha,
                   "observed_target_sha": None if current is READ_ERROR else current,
                   "target_deleted": current is None, "stage": "merge_entry", "changed": False}
        code = "gate_git_unavailable" if current is READ_ERROR else (
            "merge_target_not_found" if current is None else "target_moved")
        raise GitServiceError(409, code, "the accepted target changed before merge", details=details)
    if current is READ_ERROR:
        raise GitServiceError(503, "target_ref_unreadable", "the target branch cannot be read")
    if current is None:
        raise GitServiceError(409, "target_branch_missing", "the target branch does not exist")
    if not commit_exists(slot.base_root, frozen):
        raise GitServiceError(409, "frozen_sha_missing", "the frozen group result is gone")
    contained = is_ancestor(slot.base_root, frozen, current)
    if contained is None:
        raise GitServiceError(503, "ancestry_unreadable", "the target ancestry cannot be read")
    if contained:
        return {"outcome": CONTAINED, "pre_head": current, "merge_commit": current}
    target = mt.plan_finalize_target(slot.group_id, slot.project_id, slot.cfg, target_branch)
    if target.target_branch != target_branch:
        raise GitServiceError(409, "target_branch_pinned", "another merge attempt pins a different target")
    if target.is_project_base:
        gs.guard_base_free(slot.project_id)
    attempt = mt.open_attempt(slot.group_id, target, ATTEMPT_ACTION, holder_of(job_id), uuid.uuid4().hex,
                              origin=mt.ORIGIN_MANUAL_MERGE)
    from flowgate.gate_decisions import repository as decisions
    saved = decisions.get(decision_id) or {}
    basis = (saved.get("settings") or {}).get("target_basis")
    additions = {CONTEXT_KEY: decision_id}
    if basis:
        rec = mt.target_record(gs.db_git.get_session(attempt.merge_id)) or {}
        additions[mt.TARGET_RECORD_KEY] = {**rec, "project_base_branch": basis["project_base_branch"],
                                         "is_project_base": basis["is_project_base"],
                                         "unmerge_supported": basis["unmerge_supported"],
                                         "warnings": basis["warnings"],
                                         "accepted_target_sha": basis["accepted_target_sha"]}
    mt._update_context(attempt.merge_id, **additions)
    landed = False
    try:
        if not attempt.is_project_base:
            mt.prepare_workspace(attempt)
        root = attempt.root
        if root is None:
            raise GitServiceError(409, "target_checkout_unavailable", "the target checkout is not available")
        if attempt.is_project_base:
            mt.raise_if_base_checkout_off_branch(slot.project_id, attempt.base_branch)
            if gs._dirty(slot.base_root, include_untracked=False):
                raise GitServiceError(409, "base_dirty", "the base checkout has local modifications")
        pre_head = gs._rev_parse(root, "HEAD")
        if pre_head != current:
            observed = rev(slot.base_root, target_ref)
            code = "gate_git_unavailable" if observed is READ_ERROR else (
                "merge_target_not_found" if observed is None else "target_moved")
            raise GitServiceError(409, code, "the target checkout is not at the accepted branch head",
                                  details={"target_branch": target_branch, "accepted_target_sha": target_sha or current,
                                           "observed_target_sha": None if observed is READ_ERROR else observed,
                                           "target_deleted": observed is None, "observed_unavailable": observed is READ_ERROR,
                                           "stage": "target_checkout", "merge_id": attempt.merge_id, "changed": False})
        mt.record_merge_inputs(attempt, pre_head=pre_head, source_head=frozen,
                               expected_remote_head=gs._rev_parse(root, f"refs/remotes/origin/{target_branch}"))
        on_started({"merge_id": attempt.merge_id, "pre_head": pre_head})
        proc = gs._run_git(
            [*gs._GIT_IDENT, "-c", "merge.conflictStyle=zdiff3", "-c", "rerere.enabled=false",
             "-c", "rerere.autoupdate=false", "merge", "--no-ff", "-m",
             _merge_commit_subject(slot.group_branch, target_branch), frozen],
            cwd=root, author_env=_author_env_from_cfg(slot.cfg))
        landed = proc.returncode == 0
        files: list[str] = []
        if not landed:
            listed = gs._run_git(["diff", "--name-only", "--diff-filter=U"], cwd=root)
            files = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
            if not files and _merge_commit_landed(root, pre_head, frozen):
                if gs._rev_parse(root, "MERGE_HEAD"):
                    gs._run_git(["merge", "--quit"], cwd=root)
                landed = True
        if landed:
            full = gs._rev_parse(root, "HEAD")
            short = gs._rev_parse(root, "HEAD", short=True)
            mt.finish_manual_attempt(attempt, merge_commit=short, merge_commit_sha=full, pushed=False)
            return {"outcome": MERGED, "pre_head": pre_head, "merge_commit": full, "merge_id": attempt.merge_id}
        if not files:
            gs._run_git(["merge", "--abort"], cwd=root)
            _log.warning("gate merge failed for %s: %s", slot.group_id, redact.scrub(proc.stderr, *slot.secrets))
            raise GitServiceError(409, "merge_failed", "the merge could not run")
        _hand_off_conflict(slot, attempt, root, target_branch, files, decision_id)
        return {"outcome": CONFLICT, "pre_head": pre_head, "merge_id": attempt.merge_id, "files": files}
    except BaseException as exc:
        # A merge commit that landed is never closed as failed: the attempt stays open for finalize's own recovery
        # (``recover_interrupted_attempt`` reads it as completed from its recorded merge inputs).
        if not landed:
            mt.fail_attempt(attempt, {"code": redact.safe_code(getattr(exc, "code", None) or type(exc).__name__)})
        raise
    finally:
        mt.invalidate_check_targets(slot.project_id)


def _hand_off_conflict(slot: Slot, attempt, root: Path, target_branch: str, files: list, decision_id: str) -> None:
    """finalize's conflict hand-off, unchanged in shape, with the gate's decision id in the session context."""
    gs, mt = _gs(), _mt()
    from modules.flow_gate.services.git import rerere_checkpoint
    context = {
        "review_state": None, "auto_authority": False,
        "resolver_baseline": {"base_head": gs._rev_parse(root, "HEAD"), "merge_head": gs._rev_parse(root, "MERGE_HEAD"),
                              "expected_remote_head": gs._rev_parse(root, f"refs/remotes/origin/{target_branch}")},
        CONTEXT_KEY: decision_id, "conflict_handed_off": True,
    }
    mt.mark_conflict(attempt, files, context)
    gs.apply_eol_separation(attempt.merge_id, root)
    gs._set_status(slot.group_id, "conflict", merge_id=attempt.merge_id)
    rerere_checkpoint.start_session(slot.group_id, attempt.merge_id, root)
    session = gs.db_git.get_session(attempt.merge_id)
    gs._emit("git_merge_conflict", slot.project_id, slot.group_id, {
        "project": slot.project_id, "group_id": slot.group_id, "merge_id": attempt.merge_id,
        "conflict_count": len(files), "conflict_since": session.get("created_at") if session else None,
        "gate_decision_id": decision_id})
