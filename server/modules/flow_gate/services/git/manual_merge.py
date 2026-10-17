"""Manual merge, per-target merge results and their follow-up push (0066 D#1 §3-6, §3-8).

E8/E9: ``manual_merge_admission`` replaces finalize()'s approval gates with technical
guards only (no wf_done, AC or Graph gate). An admitted merge is a ``base_publish`` job
(action ``manual_merge``) that holds G -> B/W -> R for its whole run, judges admission
again under those locks and runs finalize's own execution with the manual options
(no conversation snapshot, no absorb, target-based no-work refusal, the post-fetch
re-check, ``origin=manual_merge`` attempts, recovery rows, Group kept on success).

E10/E11: the evidence of a follow-up push is the latest completed manual merge attempt of
each target branch (existing attempt rows, no schema change). A push names one of them by
``merge_id``; its job (action ``merge_result_push``) holds G -> B/W -> R from the
re-validation to its terminal write and pushes exactly the target tip it pinned (h1).
Phases: start -> planned (h1 recorded) -> pushed -> done.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import operation_job as db_jobs

from . import approval_freeze
from . import approval_intent
from . import base_publish
from . import finalize
from . import finalize_publish
from . import job_store as store
from . import lock_manager as locks
from . import manual_path_policy as policy
from . import merge_target
from .credentials import GitServiceError
from .refs import _ahead_of_base, _full_sha_matches, _is_ancestor, _rev_parse, _unpushed_local_merge_of

_log = logging.getLogger(__name__)

MANUAL_MERGE = base_publish.MANUAL_MERGE
MERGE_RESULT_PUSH = base_publish.MERGE_RESULT_PUSH
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_ADMITTED_STATUSES = ("none", "awaiting_choice", "waiting")


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def _public_error(exc: GitServiceError) -> dict:
    out = {"code": exc.code, "message": exc.message}
    if exc.details:
        out["details"] = policy.public_details(exc.details)
    return out


# ── Graph execution (D#1 §3-6(3)) ─────────────────────────────────────────────

def graph_state(group_id: str) -> dict:
    """``run_present``, ``blocker`` (None or a §3-6(3) reason) and ``gate_open`` (info only)."""
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import graph_execution_hooks
    try:
        run = run_store.active_run(group_id)
    except Exception:
        _log.warning("manual merge: graph run of %s unreadable", group_id, exc_info=True)
        return {"run_present": None, "blocker": "graph_state_unavailable", "gate_open": None}
    if run is None:
        return {"run_present": False, "blocker": None, "gate_open": False}
    try:
        reason = graph_execution_hooks.call("manual_git_blocker", run["run_id"])
    except Exception:
        # Unregistered hook or an unreadable ledger: fail closed.
        _log.warning("manual merge: graph blocker of %s unavailable", group_id, exc_info=True)
        reason = "graph_state_unavailable"
    try:
        gate_open = bool(run_store.has_active_attempts(run["run_id"]))
    except Exception:
        gate_open = None
    return {"run_present": True, "blocker": reason, "gate_open": gate_open}


# ── Uncommitted files (D#1 §3-6(4)) ───────────────────────────────────────────

def _excluded_new_untracked(row: dict) -> bool:
    """A new artifact outside the index under an exclusion rule whose policy class is not
    managed: only such an entry may stay behind uncommitted (``unverified`` = ownership
    unreadable, which loses the exemption). Managed (TR2-owned) or unsupported entries
    keep their protection even under an exclusion rule."""
    from modules.flow_gate.services import path_exclusion_rules
    return (not row["hidden"] and not row["in_head"] and row["untracked"] and not row["staged"]
            and row["unsupported"] is None and path_exclusion_rules.is_excluded_path(row["path"])
            and row["class"] in ("excluded", "unverified"))


def uncommitted_summary(group_id: str) -> dict:
    """Counts of what blocks a manual merge. Nothing is absorbed: every uncommitted entry
    blocks except a new excluded artifact outside the index, and that exemption needs a
    readable TR2 ownership that classifies the path as excluded (a managed path under an
    exclusion rule still blocks). Hidden paths are counted only (managed ones as hidden)."""
    from . import worktree_manual
    *_, root = worktree_manual._context(group_id)
    ownership = policy.read_ownership(group_id)
    entries = worktree_manual._collect(root, ownership)["entries"]
    counts = dict.fromkeys(("staged", "unstaged", "new", "hidden", "managed", "unverified",
                            "unsupported", "excluded_new_untracked"), 0)
    blocking = 0
    exemption_lost = False
    for row in entries:
        if _excluded_new_untracked(row):
            counts["excluded_new_untracked"] += 1
            if ownership.available:
                continue
            exemption_lost = True
        blocking += 1
        counts["staged"] += bool(row["staged"])
        counts["unstaged"] += bool(row["unstaged"])
        counts["new"] += not row["in_head"]
        counts["unsupported"] += bool(row["unsupported"])
        if row["hidden"]:
            counts["hidden"] += 1
        elif row["class"] == "managed":
            counts["managed"] += 1
        elif row["class"] == "unverified":
            counts["unverified"] += 1
    return {"blocking": blocking, "counts": counts,
            "protected_uncommitted": counts["hidden"] + counts["managed"],
            "reason": "ownership_unavailable" if exemption_lost else None}


def _uncommitted_error(summary: dict) -> GitServiceError:
    details = {"count": summary["blocking"], "counts": summary["counts"],
               "protected_uncommitted": summary["protected_uncommitted"]}
    if summary["reason"]:
        details["reason"] = summary["reason"]
    return GitServiceError(409, "uncommitted_changes",
                           "commit or revert the group's uncommitted changes before merging",
                           details=details)


# ── Target and merge need (D#1 §3-6(2) 6, 12) ─────────────────────────────────

def _readonly_target(group_id: str, project_id: str, cfg: dict,
                     requested: Optional[str]) -> merge_target.MergeTargetContext:
    """plan_finalize_target without its plan-time reclaim (E8 changes nothing)."""
    gs = _gs()
    fixed = merge_target.raise_if_retarget(group_id, requested)
    if fixed is not None:
        return fixed
    base_branch = merge_target.project_base_branch(cfg)
    defaulted = requested is None
    branch = requested or merge_target.default_target_of_group(project_id, group_id, cfg)
    if branch == base_branch:
        return replace(merge_target._base_context(project_id, base_branch), defaulted_to_work_base=defaulted)
    try:
        merge_target.validate_target_branch(project_id, gs._base_root_of(project_id), branch)
    except GitServiceError as exc:
        if not defaulted:
            raise
        raise GitServiceError(
            exc.status, "merge_target_work_base_invalid",
            "the group's work base cannot be used as the merge target; choose a target explicitly",
            details={**(exc.details or {}), "work_base_ref": branch, "cause": exc.code},
        ) from exc
    verdict, details = merge_target.inspect_workspace(project_id, branch)
    if verdict == "busy":
        raise merge_target._target_error(409, "merge_target_busy",
                                         merge_target._busy_message(branch, details), branch, **details)
    if verdict == "mismatch":
        raise merge_target._target_error(
            409, "merge_target_owner_mismatch",
            "the target workspace owner does not match any open attempt; refusing to take it over",
            branch, **details)
    wdir = merge_target.workspace_dir(project_id, branch)
    return merge_target.MergeTargetContext(
        project_id=project_id, base_branch=base_branch, target_branch=branch,
        is_project_base=False, root=wdir / merge_target.WORKSPACE_TREE, workspace_dir=wdir,
        workspace_key=merge_target.workspace_key(branch), defaulted_to_work_base=defaulted,
    )


def merge_need(project_id: str, group_id: str, cfg: dict, state: dict, base_root: Path,
               target: merge_target.MergeTargetContext) -> tuple[int, Optional[str]]:
    """``(ahead_of_target, recovery_merge_commit)``; raises the §3-6(5) refusals."""
    gs = _gs()
    branch = state["branch"]
    base_branch = merge_target.project_base_branch(cfg)
    ahead = _ahead_of_base(base_root, f"refs/heads/{target.target_branch}", branch)
    if ahead is None:
        raise GitServiceError(409, "invalid_state", "commits to merge cannot be counted",
                              details={"reason": "ahead_unmeasurable"})
    if ahead > 0:
        return ahead, None
    work_base_ref = gs.resolve_group_work_base_ref(project_id, group_id, config=cfg)
    recovery = (
        _unpushed_local_merge_of(base_root, base_branch, branch)
        if target.is_project_base and work_base_ref == base_branch else None
    )
    if recovery is None:
        raise GitServiceError(409, "nothing_to_merge", "the target already contains this group's commits")
    recorded = merge_target.recorded_manual_merge(group_id, target.target_branch, recovery)
    if recorded is not None:
        raise GitServiceError(
            409, "nothing_to_merge", "this merge is already recorded; push it from its merge result",
            details={"reason": "already_recorded", "merge_id": recorded, "push_via": MERGE_RESULT_PUSH})
    return 0, recovery


# ── Admission (D#1 §3-6(1)(2)) ────────────────────────────────────────────────

@dataclass
class Admission:
    project_id: str
    cfg: dict
    state: dict
    base_root: Path
    wt_path: Path
    target: merge_target.MergeTargetContext
    ahead: int
    recovery_commit: Optional[str]
    excluded_new: int


def _judge(group_id: str, requested: Optional[str], *, collect: bool, readonly: bool):
    """The §3-6(2) conditions 2..13 in their error order. ``collect=False`` raises the
    first refusal; ``collect=True`` (E8) gathers them. Returns (admission, blocked, info)."""
    gs = _gs()
    blocked: list[dict] = []
    info: dict = {}

    def fail(exc: GitServiceError) -> None:
        if not collect:
            raise exc
        blocked.append(_public_error(exc))

    try:                                                                   # 2
        cfg, state, project_id, base_root, wt_path = gs._finalize_context(group_id)
        if not wt_path.is_dir():
            raise GitServiceError(409, "invalid_state", "group worktree directory is missing")
    except GitServiceError as exc:
        fail(exc)
        return None, blocked, info
    info.update(cfg=cfg, state=state, project_id=project_id, base_root=base_root)
    try:                                                                   # 3
        gs.guard_group_branch_merge_free(group_id)
    except GitServiceError as exc:
        fail(exc)
    session = gs.db_git.get_open_session_by_group(group_id)
    kind = gs.db_git.session_kind(session) if session is not None else None
    if kind == gs.db_git.SESSION_KIND_GROUP_UPDATE:                       # 4
        fail(GitServiceError(409, "invalid_state", "resolve or abort the group update first"))
    if approval_intent.clean_retry_of_state(state) is not None:            # 5
        fail(GitServiceError(409, "approval_retry_pending",
                             "terminal Git already belongs to an approval-only retry"))
    target = None
    try:                                                                   # 6
        target = (_readonly_target if readonly else merge_target.plan_finalize_target)(
            group_id, project_id, cfg, requested)
    except GitServiceError as exc:
        fail(exc)
    info["target"] = target
    status = state.get("status") or "none"                                 # 7
    if status == "conflict":
        pinned = merge_target.fixed_target_of_group(group_id)
        fail(GitServiceError(409, "invalid_state", "resolve or abort the merge first",
                             details=pinned.public() if pinned is not None else None))
    elif status == "merging":
        fail(GitServiceError(409, "git_busy",
                             f"Another git operation is in progress for project '{project_id}' (try again shortly)"))
    elif status in ("merged", "pushed"):
        fail(GitServiceError(409, "invalid_state", "already finalized"))
    elif status not in _ADMITTED_STATUSES:
        fail(GitServiceError(409, "invalid_state", f"manual merge not available in state '{status}'"))
    if (session is not None and kind != gs.db_git.SESSION_KIND_GROUP_UPDATE   # 8
            and status not in ("conflict", "merging")):
        fail(GitServiceError(409, "invalid_state", "another Git session of this group is open",
                             details={"session_kind": kind}))
    try:                                                                   # 9
        finalize._guard_group_update_ai_idle(group_id)
    except GitServiceError as exc:
        fail(exc)
    graph = graph_state(group_id)                                          # 10
    info["graph"] = graph
    if graph["blocker"]:
        fail(GitServiceError(409, "graph_execution_active", "a Graph execution is working on this group",
                             details={"reason": graph["blocker"]}))
    summary = None
    try:                                                                   # 11
        summary = uncommitted_summary(group_id)
        if summary["blocking"]:
            fail(_uncommitted_error(summary))
    except GitServiceError as exc:
        fail(exc)
    info["uncommitted"] = summary
    ahead, recovery = None, None
    if target is not None:                                                 # 12
        try:
            ahead, recovery = merge_need(project_id, group_id, cfg, state, base_root, target)
        except GitServiceError as exc:
            if exc.code == "nothing_to_merge":
                ahead = 0
            fail(exc)
        if target.is_project_base:                                         # 13
            try:
                gs.guard_base_free(project_id)
            except GitServiceError as exc:
                fail(exc)
    info.update(ahead=ahead, recovery=recovery)
    if blocked or target is None or ahead is None:
        return None, blocked, info
    return Admission(project_id, cfg, state, base_root, wt_path, target, ahead, recovery,
                     (summary or {}).get("counts", {}).get("excluded_new_untracked", 0)), blocked, info


def manual_merge_admission(group_id: str, target: Optional[str], push: bool) -> Admission:
    """D#1 §3-6(1): technical guards only; raises the first refusal."""
    if not isinstance(push, bool):
        raise GitServiceError(422, "invalid_request", "push must be a boolean")
    adm, _, _ = _judge(group_id, merge_target.normalize_requested_target(target),
                       collect=False, readonly=False)
    assert adm is not None
    return adm


# ── E8 ────────────────────────────────────────────────────────────────────────

def _target_candidates(project_id: str, cfg: dict, base_root: Optional[Path], work_base_ref: str) -> list[dict]:
    gs = _gs()
    from .branches import internal_slot_owner
    base_branch = merge_target.project_base_branch(cfg)
    if base_root is None or not (base_root / ".git").exists():
        return []
    proc = gs._run_git(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"],
                       cwd=base_root)
    if proc.returncode != 0:
        return []
    local, remote = [], []
    for ref in (proc.stdout or "").split():
        if ref.startswith("refs/heads/"):
            local.append(ref[len("refs/heads/"):])
        elif ref.startswith("refs/remotes/origin/") and ref != "refs/remotes/origin/HEAD":
            remote.append(ref[len("refs/remotes/origin/"):])
    out = []
    for name in sorted(set(local) | set(remote)):
        reason = None
        if name not in local:
            reason = "remote_only"
        elif name != base_branch and internal_slot_owner(project_id, name) is not None:
            reason = "internal_slot"
        elif name != base_branch:
            verdict, _ = merge_target.inspect_workspace(project_id, name)
            reason = {"busy": "workspace_busy", "mismatch": "owner_mismatch"}.get(verdict)
        out.append({"name": name, "kind": "base" if name == base_branch else "local",
                    "eligible": reason is None, "blocked_reason": reason,
                    "is_work_base": name == work_base_ref, "unmerge_supported": False})
    return out


def merge_view(group_id: str, target: Optional[str] = None) -> dict:
    """GET …/manual-merge: the lock-free admission verdict and what a merge would be."""
    gs = _gs()
    requested = merge_target.normalize_requested_target(target)
    adm, blocked, info = _judge(group_id, requested, collect=True, readonly=True)
    project_id = gs._project_of_group(group_id)
    cfg = info.get("cfg") or gs.db_git.get_config(project_id) or {}
    ac_id = finalize._group_ac_doc_id(group_id)
    ac_doc = finalize.db_documents.get_by_id(ac_id) if ac_id else None
    work_base_ref = merge_target.default_target_of_group(project_id, group_id, cfg)
    result = {
        "can_merge": adm is not None and not blocked,
        "blocked": blocked,
        "admission_basis": "wf_done" if gs._group_root_wf_done(group_id) else "no_wf_done",
        "approval": {"ac_doc_id": ac_id, "status": (ac_doc or {}).get("doc_review_status"),
                     "final_approval_bound": approval_intent.group_is_final_approval_bound(group_id)},
        "default_target": work_base_ref,
        "targets": _target_candidates(project_id, cfg, info.get("base_root"), work_base_ref),
        "preview": None, "commit_count": None, "floor_error": None,
        "ahead_of_target": info.get("ahead"),
        "merge_needed": bool(info.get("ahead")) or info.get("recovery") is not None,
        "recovery": info.get("recovery") is not None,
        "uncommitted": None,
        "graph": info.get("graph"),
    }
    summary = info.get("uncommitted")
    if summary is not None:
        result["uncommitted"] = {"blocking": summary["blocking"], "counts": summary["counts"],
                                 "protected_uncommitted": summary["protected_uncommitted"],
                                 "reason": summary["reason"]}
    if info.get("target") is not None:
        try:
            result["preview"] = merge_target.preview_target(group_id, info["target"].target_branch)
        except GitServiceError as exc:
            result["preview"] = {"error": _public_error(exc)}
    if info.get("state") is not None:
        try:
            from . import worktree_manual
            status = worktree_manual.worktree_status(group_id)
            result["commit_count"] = (status.get("committed") or {}).get("commit_count")
            result["floor_error"] = (status.get("floor") or {}).get("error")
        except GitServiceError as exc:
            result["floor_error"] = _public_error(exc)
    return result


# ── E9 ────────────────────────────────────────────────────────────────────────

def _run_job(project_id: str, group_id: str, req: dict, payload: dict) -> dict:
    """base_publish's request run; a queued answer also names the job phase and, for a
    Group an approval froze, the D#1 reason ``group_frozen_for_approval``."""
    try:
        return base_publish._run_request(project_id, group_id, req, payload)
    except GitServiceError as exc:
        job_id = (exc.details or {}).get("job_id") if exc.code in ("git_busy", "recovery_required") else None
        if job_id:
            job = store.get_job(job_id) or {}
            exc.details["phase"] = job.get("phase")
            if job.get("blocked_operation") == "approval_freeze":
                exc.details["reason_code"] = "group_frozen_for_approval"
        raise


def _merge_seq(group_id: str) -> int:
    jobs = db_jobs.jobs_of_group(group_id, base_publish.KIND, db_jobs.TERMINAL_STATUSES)
    return sum(1 for job in jobs if approval_freeze._json(job.get("payload")).get("action") == MANUAL_MERGE)


def request_merge(group_id: str, target_branch: Optional[str] = None, push: bool = False) -> dict:
    """POST …/manual-merge: fast admission, then the job run once here (queued → git_busy)."""
    gs = _gs()
    adm = manual_merge_admission(group_id, target_branch, push)
    if not gs.git_available():
        raise GitServiceError(500, "git_unavailable",
                              "git binary not found on server (install git in the runtime image)")
    requested = merge_target.normalize_requested_target(target_branch)
    action = "merge" if push else "merge_only"
    payload = {"action": MANUAL_MERGE, "git_action": action, "target_branch": requested}
    req = {"action": MANUAL_MERGE,
           "source_identity": f"{group_id}:{requested or '-'}:{action}:{_merge_seq(group_id)}"}
    job = _run_job(adm.project_id, group_id, req, payload)
    out = dict(approval_freeze._json(job.get("result")) or {})
    result = dict(out.get("result") or {})
    result["job_id"] = job["job_id"]
    return result


_GIT_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
_GIT_OCTAL_RE = re.compile(r"[0-3][0-7]{2}")


def _git_unquote(path: str) -> Optional[str]:
    r"""The real path behind one ``git diff --name-only`` line (finalize's conflict list).

    Git C-quotes a path holding a non-ASCII byte (``core.quotePath``), ``"``, ``\`` or a
    control character: the whole line is wrapped in double quotes and each such byte is a
    ``\ooo`` octal escape of its UTF-8 byte or a C escape. An unquoted line is the path
    itself. None = a quoted line that is not valid Git quoting."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    body, raw, i = path[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            return None
        if ch != "\\":
            raw += ch.encode("utf-8")
            i += 1
            continue
        code = body[i + 1:i + 2]
        if code in _GIT_ESCAPES:
            raw.append(_GIT_ESCAPES[code])
            i += 2
        elif _GIT_OCTAL_RE.fullmatch(body[i + 1:i + 4]):
            raw.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            return None
    return raw.decode("utf-8", "surrogateescape")


def _hidden_conflict_path(path: str) -> bool:
    # The policy judges the real path: a quoted line reads as a dot directory
    # (``"server/.env-\355…"`` → excluded) and would slip through. Bad quoting fails closed.
    real = _git_unquote(path)
    return real is None or policy.hidden(real) or policy.hidden(path)


def _spellings(path: str) -> list[str]:
    r"""Every form of one hidden conflict line an answer could carry: the Git line, its
    quoted body, the real path, each with ``/`` and ``\`` separators."""
    forms = {path}
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        forms.add(path[1:-1])
    real = _git_unquote(path)
    if real:
        forms.add(real)
    forms |= {form.replace("/", "\\") for form in forms}
    return sorted((form for form in forms if form), key=len, reverse=True)


def _redact(value, forms: list[str]):
    if isinstance(value, dict):
        return {_redact(k, forms): _redact(v, forms) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, forms) for v in value]
    if isinstance(value, str):
        for form in forms:
            value = value.replace(form, "<hidden>")
    return value


def _public_conflict(r: dict) -> dict:
    """D#1 §3-1(3): an E9 answer (first run, resume, the stored job result) names no
    hidden path. Hidden conflict files are counted only; the conflict session keeps the
    real paths the resolution needs. Any other nested string is filtered the same way.

    ``conflict_files`` holds finalize's ``git diff --name-only`` lines, so a non-ASCII or
    special-character path arrives C-quoted; it is judged, and redacted, by its real path
    and by every spelling (``_spellings``). Visible lines keep finalize's form."""
    files = [str(path) for path in (r.get("conflict_files") or [])]
    secret = [path for path in files if _hidden_conflict_path(path)]
    forms = sorted({form for path in secret for form in _spellings(path)}, key=len, reverse=True)
    reals = [real for real in (_git_unquote(path) for path in secret) if real]
    rest = _redact({k: v for k, v in r.items() if k != "conflict_files"}, forms)
    r = policy.public_details(rest, [*files, *reals])
    r["conflict_files"] = [path for path in files if path not in secret]
    r["hidden_conflict_count"] = len(secret)
    return r


def _merge_answer(out: dict, excluded_new: Optional[int] = None) -> dict:
    r = _public_conflict(dict(out.get("result") or {}))
    conflict = r.get("status") == "conflict"
    r["group_status"] = "conflict" if conflict else "none"
    r["worktree_kept"] = True
    r["unmerge_supported"] = False
    r.setdefault("recovered", False)
    r.pop("approval_intent_id", None)
    if excluded_new is not None:
        r["excluded_artifact_count"] = excluded_new
    return {"ok": True, "result": r}


def _execute_merge(group_id: str, p: dict, holder: str) -> dict:
    """Under G, B/W and R: the final admission, then finalize's execution, manual options."""
    action = p.get("git_action")
    adm = manual_merge_admission(group_id, p.get("target_branch"), action == "merge")
    out = finalize._finalize_execute(
        group_id, action, None, p.get("target_branch"),
        cfg=adm.cfg, state=adm.state, project_id=adm.project_id, base_root=adm.base_root,
        wt_path=adm.wt_path, requested_target=p.get("target_branch"), job_holder=holder,
        options=finalize.MANUAL_MERGE_EXECUTE,
    )
    return _merge_answer(out, adm.excluded_new)


def _completed_answer(action: str, rec: dict, result: dict, merge_id: int) -> dict:
    return _merge_answer({"result": {
        "action": action, "status": "merged", "merge_commit": result.get("merge_commit"),
        "merge_commit_sha": result.get("merge_commit_sha"), "pushed": bool(result.get("pushed")),
        "merge_id": merge_id, "conflict_files": [], "target_branch": rec.get("branch"),
        "recovered": bool(result.get("recovered")), "resumed": True}})


def _resume_merge(ctx: store.JobContext, p: dict) -> Optional[dict]:
    """What an earlier claim of this job left, under its locks. None = run (again)."""
    gs = _gs()
    group_id, action = ctx.job["group_id"], p.get("git_action")
    holder = finalize_publish.holder_of(ctx.job_id)
    session = gs.db_git.get_open_session_by_group(group_id)
    rec = (merge_target.target_record(session) or {}) if session is not None else {}
    if session is not None and rec.get("lock_holder") == holder:
        merge_id = int(session["merge_id"])
        if merge_target.attempt_phase(session) == merge_target.PHASE_CONFLICT:
            files = [row["path"] for row in gs.db_git.session_files(merge_id)]
            return _merge_answer({"result": {
                "action": action, "status": "conflict", "merge_commit": None, "pushed": False,
                "merge_id": merge_id, "conflict_files": files,
                "target_branch": rec.get("branch"), "resumed": True}})
        store.ensure_lease(ctx)
        verdict = merge_target.recover_interrupted_attempt(session, "manual_merge_job_resume")
        if verdict == merge_target.ATTEMPT_INTERRUPTED:
            return None
        if verdict == merge_target.ATTEMPT_COMPLETED:
            closed = gs.db_git.session_context(gs.db_git.get_session(merge_id))
            return _completed_answer(action, rec, closed.get("attempt_result") or {}, merge_id)
        raise base_publish._recovery("manual_merge_attempt_unsettled")
    for old, old_rec in finalize_publish._own_attempts(group_id, holder):
        context = gs.db_git.session_context(old)
        if context.get(merge_target.ATTEMPT_STATE_KEY) == merge_target.ATTEMPT_COMPLETED:
            return _completed_answer(action, old_rec, context.get("attempt_result") or {},
                                     int(old["merge_id"]))
    return None


def _run_merge(ctx: store.JobContext) -> Optional[store.RunResult]:
    held: list = []
    try:
        if (ctx.job.get("phase") or "start") == "done":
            return None
        p = approval_freeze._json(ctx.job.get("payload"))
        finalize_publish._admit(ctx, p, held)
        result = _resume_merge(ctx, p)
        if result is None:
            store.ensure_lease(ctx)
            ev = approval_freeze._json(ctx.job.get("evidence"))
            ev["started"] = True
            store.job_write(ctx, phase="start", evidence=store._canonical_json(ev))
            try:
                result = _execute_merge(ctx.job["group_id"], p, finalize_publish.holder_of(ctx.job_id))
            except (store.LeaseLost, locks.LockProgramError, store.JobProgramError):
                raise
            except Exception as exc:
                raise finalize_publish._fail(exc)
        finalize_publish._finish(ctx, result)
        return None
    except base_publish._Stop as stop:
        return stop.result
    finally:
        finalize_publish._release_all(ctx, held)


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """base_publish's executor for actions ``manual_merge`` and ``merge_result_push``."""
    if entry != store.PUBLISH:
        raise store.JobProgramError("manual_merge_entry_not_publish", job_id=ctx.job_id, entry=entry)
    if approval_freeze._json(ctx.job.get("payload")).get("action") == MERGE_RESULT_PUSH:
        return _run_result_push(ctx)
    return _run_merge(ctx)


# ── E10 (D#1 §3-8(4)) ─────────────────────────────────────────────────────────

def _group_blocker(group_id: str, state: dict) -> Optional[str]:
    gs = _gs()
    status = state.get("status") or "none"
    if status in ("merging", "conflict"):
        return status
    session = gs.db_git.get_open_session_by_group(group_id)
    return gs.db_git.session_kind(session) if session is not None else None


def _count(repo: Path, spec: str) -> Optional[int]:
    proc = _gs()._run_git(["rev-list", "--count", spec], cwd=repo)
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "").strip())
    except ValueError:
        return None


def _remote_view(base_root: Optional[Path], branch: str, merge_commit: Optional[str]) -> tuple[dict, Optional[bool]]:
    """Local refs only (Fetch refreshes them): the remote view of one result and whether
    the local target still contains the merge commit (None = cannot tell)."""
    gs = _gs()
    view = {"measured": False, "remote_branch_missing": None, "local_ahead_count": None,
            "contains_merge_commit": None}
    if base_root is None or not (base_root / ".git").exists() or not merge_commit:
        return view, None
    local_ref, remote_ref = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
    local_contains = _is_ancestor(base_root, merge_commit, local_ref) if gs._ref_exists(base_root, local_ref) else False
    if not gs._ref_exists(base_root, remote_ref):
        view.update(measured=local_contains is not None, remote_branch_missing=True,
                    contains_merge_commit=False,
                    local_ahead_count=_count(base_root, local_ref) if local_contains is not None else None)
        return view, local_contains
    contains = _is_ancestor(base_root, merge_commit, remote_ref)
    ahead = _count(base_root, f"{remote_ref}..{local_ref}") if gs._ref_exists(base_root, local_ref) else 0
    view.update(measured=contains is not None and local_contains is not None,
                remote_branch_missing=False, contains_merge_commit=contains, local_ahead_count=ahead)
    return view, local_contains


def _result_row(rec: dict, base_root: Optional[Path], base_branch: str, blocker: Optional[str]) -> dict:
    branch = rec["target_branch"]
    is_base = branch == base_branch
    remote, local_contains = _remote_view(base_root, branch, rec["merge_commit"])
    if not remote["measured"]:
        reason = "unmeasured"
    elif remote["contains_merge_commit"]:
        reason = "already_published"
    elif not local_contains:
        reason = "target_moved"
    elif blocker is not None:
        reason = "group_blocked"
    else:
        reason = None
    return {
        "merge_id": rec["merge_id"],
        "target": {"target_branch": branch, "target_kind": "branch", "target_group_id": None,
                   "managed_workspace": not is_base, "is_project_base": is_base,
                   "unmerge_supported": False, "merge_id": rec["merge_id"],
                   "started_at": rec["started_at"], "legacy": False},
        "merge_commit": rec["merge_commit"],
        "pushed": rec["pushed"], "recovered": rec["recovered"], "completed_at": rec["completed_at"],
        "remote": remote,
        "push_available": reason is None,
        "push_blocked_reason": reason,
    }


def merge_results_view(group_id: str) -> dict:
    """GET …/merge-result: one row per target (its latest completed manual merge)."""
    gs = _gs()
    project_id = gs._project_of_group(group_id)
    cfg = gs.db_git.get_config(project_id) or {}
    state = gs.db_git.get_state(group_id) or {}
    base_root = gs._base_root_of(project_id)
    base_branch = merge_target.project_base_branch(cfg)
    blocker = _group_blocker(group_id, state)
    rows = [_result_row(rec, base_root, base_branch, blocker)
            for rec in merge_target.manual_merge_results(group_id)]
    return {"group_status": state.get("status") or "none", "results": rows,
            "push_candidates": sum(1 for r in rows if r["push_available"]),
            "group_blocker": blocker}


# ── E11 (D#1 §3-8) ────────────────────────────────────────────────────────────

def _require_group_valid(group_id: str) -> None:
    gs = _gs()
    state = gs.db_git.get_state(group_id) or {}
    status = state.get("status") or "none"
    if status in ("merging", "conflict"):
        raise GitServiceError(409, "invalid_state", "the group has a merge in progress",
                              details={"status": status})
    session = gs.db_git.get_open_session_by_group(group_id)
    if session is not None:
        raise GitServiceError(409, "invalid_state", "a Git session of this group is open",
                              details={"session_kind": gs.db_git.session_kind(session)})


def _require_standing(group_id: str, merge_id, merge_commit: str) -> dict:
    row, latest = merge_target.manual_merge_standing(group_id, merge_id)
    if row is None:
        raise GitServiceError(409, "merge_result_missing",
                              "merge_id is not a completed manual merge of this group",
                              details={"merge_id": merge_id})
    if latest is not None and latest["merge_id"] != row["merge_id"]:
        raise GitServiceError(409, "stale_target", "a newer manual merge of this target exists",
                              details={"reason": "superseded", "merge_id": row["merge_id"],
                                       "latest_merge_id": latest["merge_id"]})
    if not _full_sha_matches(row["merge_commit"] or "", merge_commit):
        raise GitServiceError(409, "stale_target", "merge_commit does not match the merge result",
                              details={"reason": "merge_commit_mismatch", "merge_id": row["merge_id"]})
    return row


def _require_target_contains(base_root: Path, branch: str, sha: str) -> None:
    gs = _gs()
    contained = (_is_ancestor(base_root, sha, f"refs/heads/{branch}")
                 if gs._ref_exists(base_root, f"refs/heads/{branch}") else False)
    if contained is None:
        raise GitServiceError(500, "git_error", "Git command failed")
    if not contained:
        raise GitServiceError(409, "merge_target_moved", "the target branch no longer contains the merge",
                              details={"target_branch": branch})


def _require_target_owner(project_id: str, branch: str, is_base: bool) -> None:
    gs = _gs()
    if is_base:
        gs.guard_base_free(project_id)
    else:
        merge_target.raise_if_workspace_unavailable(project_id, branch)


def _push_seq(group_id: str, merge_id: int) -> int:
    """Terminal ``merge_result_push`` jobs of this merge result. In-flight duplicates share
    one job; once a push job ends (succeeded, failed or cancelled) the next request is a
    new job that re-fetches and re-validates under the locks (a remote rewound after an
    earlier success is pushed again; an already published one ends without a push)."""
    jobs = db_jobs.jobs_of_group(group_id, base_publish.KIND, db_jobs.TERMINAL_STATUSES)
    count = 0
    for job in jobs:
        payload = approval_freeze._json(job.get("payload"))
        if payload.get("action") == MERGE_RESULT_PUSH and payload.get("merge_id") == merge_id:
            count += 1
    return count


def request_result_push(group_id: str, merge_id=None, merge_commit=None) -> dict:
    """POST …/merge-result/push: request-time checks, then the job run once here."""
    gs = _gs()
    if merge_id is None or merge_commit is None:
        raise GitServiceError(422, "invalid_request", "merge_id and merge_commit are required")
    if isinstance(merge_id, bool) or not isinstance(merge_id, int) or merge_id <= 0:
        raise GitServiceError(422, "invalid_request", "merge_id must be a positive integer")
    commit = merge_commit.strip().lower() if isinstance(merge_commit, str) else ""
    if not _SHA_RE.match(commit):
        raise GitServiceError(422, "invalid_request",
                              "merge_commit must be a 7 to 40 character hexadecimal sha")
    project_id = gs._project_of_group(group_id)
    cfg = gs._require_enabled_config(project_id)
    base_root = gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    row = _require_standing(group_id, merge_id, commit)
    _require_group_valid(group_id)
    branch = row["target_branch"]
    full = _rev_parse(base_root, row["merge_commit"]) or row["merge_commit"]
    _require_target_contains(base_root, branch, full)
    is_base = branch == merge_target.project_base_branch(cfg)
    _require_target_owner(project_id, branch, is_base)
    if not gs.git_available():
        raise GitServiceError(500, "git_unavailable",
                              "git binary not found on server (install git in the runtime image)")
    payload = {"action": MERGE_RESULT_PUSH, "group_id": group_id, "merge_id": row["merge_id"],
               "merge_commit": full, "target_branch": branch, "is_project_base": is_base}
    req = {"action": MERGE_RESULT_PUSH,
           "source_identity": f"{group_id}:{row['merge_id']}:{full}:{_push_seq(group_id, row['merge_id'])}"}
    job = _run_job(project_id, group_id, req, payload)
    out = dict(approval_freeze._json(job.get("result")) or {})
    result = dict(out.get("result") or {})
    result["job_id"] = job["job_id"]
    return result


def _origin_contains(project_id: str, repo: Path, branch: str, sha: str) -> Optional[bool]:
    """Under R: origin synced with the config, its refs fetched with pruning (a branch
    deleted on origin loses its stale tracking ref), then ancestry."""
    return merge_target.origin_branch_contains(project_id, repo, branch, sha)


def _revalidate(project_id: str, group_id: str, p: dict) -> Path:
    """D#1 §3-8(2) 1..5 under G, B/W and R (first run and every resume). Returns the
    repository the push runs from."""
    gs = _gs()
    _require_group_valid(group_id)
    _require_standing(group_id, p["merge_id"], p["merge_commit"])
    cfg = gs.db_git.get_config(project_id) or {}
    if (p["target_branch"] == merge_target.project_base_branch(cfg)) != bool(p["is_project_base"]):
        raise GitServiceError(409, "stale_target", "the project base changed while the push waited",
                              details={"reason": "base_changed", "merge_id": p["merge_id"]})
    base_root = gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    _require_target_contains(base_root, p["target_branch"], p["merge_commit"])
    _require_target_owner(project_id, p["target_branch"], bool(p["is_project_base"]))
    return base_root


def _push_answer(ctx: store.JobContext, p: dict, ev: dict) -> dict:
    plan = ev.get("plan") or {}
    return {"ok": True, "result": {
        "pushed_ref": f"refs/heads/{p['target_branch']}", "pushed_head": plan.get("h1"),
        "target_branch": p["target_branch"], "is_project_base": bool(p["is_project_base"]),
        "merge_id": p["merge_id"], "merge_commit": p["merge_commit"],
        "already_published": bool(ev.get("already_published")), "phase": "done"}}


def _run_result_push(ctx: store.JobContext) -> Optional[store.RunResult]:
    held: list = []
    try:
        phase = ctx.job.get("phase") or "start"
        if phase == "done":
            return None
        p = approval_freeze._json(ctx.job.get("payload"))
        project_id, group_id = ctx.job["project_id"], ctx.job["group_id"]
        target_lock = (("B", None, None, "publish") if p.get("is_project_base")
                       else ("W", None, p.get("target_branch"), "publish"))
        finalize_publish._admit(ctx, p, held, wanted=[
            ("G", group_id, None, "publish"), target_lock, ("R", None, None, "publish")])
        if phase != "pushed":
            try:
                base_root = _revalidate(project_id, group_id, p)
            except GitServiceError as exc:
                if exc.code == "git_error":
                    raise base_publish._transient("ref_check_failed")
                raise finalize_publish._fail(exc)
            _pin_and_push(ctx, p, base_root, phase)
        _finish_push(ctx, p)
        return None
    except base_publish._Stop as stop:
        return stop.result
    finally:
        finalize_publish._release_all(ctx, held)


def _pin_and_push(ctx: store.JobContext, p: dict, base_root: Path, phase: str) -> None:
    """start/planned -> pushed: h1 pinned under the locks, then exactly h1 is pushed."""
    gs = _gs()
    branch, project_id = p["target_branch"], ctx.job["project_id"]
    tip = base_publish._rev(base_root, f"refs/heads/{branch}")
    if tip is base_publish._READ_ERROR:
        raise base_publish._transient("ref_check_failed")
    if not tip:
        raise finalize_publish._fail(GitServiceError(
            409, "merge_target_moved", "the target branch no longer contains the merge",
            details={"target_branch": branch}))
    ev = approval_freeze._json(ctx.job.get("evidence"))
    plan = ev.get("plan") if phase == "planned" else None
    if plan:
        kept = _is_ancestor(base_root, plan["h1"], tip)
        if kept is None:
            raise base_publish._transient("ref_check_failed")
        if not kept:
            # A history a person dropped is never pushed again.
            raise finalize_publish._fail(GitServiceError(
                409, "merge_target_moved", "the target branch no longer contains the pinned head",
                details={"target_branch": branch}))
        h1 = plan["h1"]
    else:
        h1 = tip
        plan = {"h1": h1, "merge_commit": p["merge_commit"], "remote_ref": f"refs/heads/{branch}"}
        store.ensure_lease(ctx)
        base_publish._phase(ctx, "planned", plan=plan)
    published = _origin_contains(project_id, base_root, branch, h1)
    if published is None:
        raise base_publish._transient("remote_head_unknown")
    if published:
        base_publish._phase(ctx, "pushed", remote_head=h1, already_published=phase == "start")
        return
    merged = _origin_contains(project_id, base_root, branch, p["merge_commit"])
    if merged is None:
        raise base_publish._transient("remote_head_unknown")
    if merged:
        base_publish._phase(ctx, "pushed", remote_head=None, already_published=True)
        return
    cfg = gs.db_git.get_config(project_id) or {}
    store.ensure_lease(ctx)
    proc = gs._run_git(["push", "origin", f"{h1}:refs/heads/{branch}"], cwd=base_root,
                       timeout=gs.GIT_NET_TIMEOUT_SEC, username=cfg.get("username"),
                       secret=gs._load_secret_for(cfg) or "")
    if proc.returncode != 0:
        landed = _origin_contains(project_id, base_root, branch, h1)
        if landed is None:
            raise base_publish._transient("push_result_unknown")
        if not landed:
            raise finalize_publish._fail(GitServiceError(
                500, "push_rejected", "Git push was rejected", diagnostic=gs._last_line(proc.stderr)))
    base_publish._phase(ctx, "pushed", remote_head=h1, already_published=False)


def _finish_push(ctx: store.JobContext, p: dict) -> None:
    gs = _gs()
    ev = approval_freeze._json(ctx.job.get("evidence"))
    finalize_publish._finish(ctx, _push_answer(ctx, p, ev))
    if p.get("is_project_base"):
        try:
            gs._emit_pending_changed(ctx.job["project_id"], None, None)
        except Exception:
            _log.warning("merge result push emit failed for %s", ctx.job["project_id"], exc_info=True)
