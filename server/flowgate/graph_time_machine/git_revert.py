"""The Git step of a Graph Time Machine operation (0061 T#13; L0007 §4.3: Git first, the DB after it).

The group branch HEAD never moves back. Each integration commit of the targets (the attempt's ``--no-ff`` merge into
the group branch, T#9) is reverted on its own in the group worktree, newest first along the branch's first-parent
line, against its first parent (``-m 1``): exactly what that merge brought, nothing of another branch's merge. An
attempt with no merge of its own (no source change, a document-only attempt) has no Git step; the plan leaves it out.

The whole step runs in the existing cancel session (``open_cancel_session``: the group's G, the gates of L0007 §4.1 —
Git active, a registered and clean worktree, no merge in flight, the commits present) and is the only time G is held;
each revert is the existing ``revert_tr_commit`` (one revert commit per merge, our own subject and body). A revert
that cannot be made safely (conflict, failed commit, timeout, a merge that is not the attempt's) is undone
(``restore_after_failed_revert``) and every revert this operation made is reverted in turn, newest first, until the
tree is the one the operation started from (``tr_commit_service._tree_matches``): the branch reads as it did, the
operation is ``blocked``. Nothing of another branch is removed to get around it.

``blocked`` is said only with its proof: HEAD is the commit the step started from (nothing made) or its tree is the
starting tree (everything made compensated), and the worktree is settled — no tracked change or kept new file left in
the index or the tree, no revert / merge / sequencer state (``_settled``). A restore or a compensation that cannot show
it, a Git read that fails (history, parents, HEAD: never read as "no history"), or a branch that disagrees with the
DB journal's recorded reverts is ``failed`` — the operation's partial failure: the Flow stays refused and the server
start resumes it.

Durable evidence, for a resume after a crash:

- each revert and compensation commit names its operation in a trailer (``FlowGate-Time-Machine``,
  ``FlowGate-Reverts`` / ``FlowGate-Compensates``). The whole first-parent line of HEAD is searched for them (no
  depth limit), so a target already reverted by this operation is never reverted twice, and a compensation begun is
  finished;
- before each revert or compensation touches the worktree a step marker is written (fsync'd) in the worktree's own
  Git directory (``git rev-parse --git-path``; never a tracked or a source path): the operation, the step, the HEAD it
  starts from and the paths the reverted commit changes. It is removed once the step's commit is made or its state
  undone and settled. A crash between ``revert --no-commit`` and its commit (or inside a conflict, or before a
  compensation's commit) leaves the index dirty with the step's change; the resumed session lets the dirty-worktree
  gate through only for that (``_owned``: this operation's marker, HEAD still its starting commit, every dirty path one
  of its paths), puts the worktree back to that commit (``restore_after_failed_revert``), checks it settled and goes on.
  Any other dirty tree keeps the gate closed (``deferred``: nothing is removed).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from modules.flow_gate.services import git_service as git, tr_commit_service
from modules.flow_gate.services.git.commit import cancel_dirty_paths

log = logging.getLogger(__name__)

OK, BLOCKED, FAILED, DEFERRED = "ok", "blocked", "failed", "deferred"
REVERTED, EMPTY = "reverted", "empty"
TRAILER_OPERATION = "FlowGate-Time-Machine"
TRAILER_REVERTS = "FlowGate-Reverts"
TRAILER_COMPENSATES = "FlowGate-Compensates"
STEP_REVERT, STEP_COMPENSATE = "revert", "compensate"
MARKER_NAME = "flowgate-time-machine-{}.json"


class GitReadError(RuntimeError):
    """A Git read the step depends on failed: the branch state is unknown."""


def _read(wt, args: list[str]) -> str:
    proc = git._run_git(args, cwd=wt, timeout=git.GIT_READ_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise GitReadError(f"git {' '.join(args[:2])} failed: {(proc.stderr or '').strip()[:200]}")
    return proc.stdout or ""


def _lines(wt, args: list[str]) -> list[str]:
    return [line.strip() for line in _read(wt, args).splitlines() if line.strip()]


def _head(wt) -> str:
    sha = _read(wt, ["rev-parse", "HEAD"]).strip()
    if len(sha) != 40:
        raise GitReadError("HEAD unreadable")
    return sha


def _parents(wt, sha: str) -> list[str]:
    shas = (_lines(wt, ["rev-list", "--parents", "-n", "1", sha]) or [""])[0].split()
    if not shas or shas[0] != sha:
        raise GitReadError(f"parents of {sha} unreadable")
    return shas[1:]


def _changed_paths(wt, parent: str, sha: str) -> list[str]:
    return [p for p in _read(wt, ["diff", "--no-renames", "--name-only", "-z", parent, sha]).split("\0") if p]


def _trailers(body: str) -> dict[str, str]:
    out = {}
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in (TRAILER_OPERATION, TRAILER_REVERTS, TRAILER_COMPENSATES):
            out[key.strip()] = value.strip()
    return out


def _journal(wt, operation_id: str) -> list[tuple[str, dict[str, str]]]:
    """This operation's commits on the whole first-parent line of HEAD, oldest first. A failed read raises."""
    out = _read(wt, ["log", "--first-parent", "--fixed-strings", f"--grep={TRAILER_OPERATION}: {operation_id}",
                     "--format=%H%x1f%B%x1e", "HEAD"])
    found = []
    for record in out.split("\x1e"):
        sha, _sep, body = record.strip("\r\n").partition("\x1f")
        trailers = _trailers(body)
        if sha.strip() and trailers.get(TRAILER_OPERATION) == operation_id:
            found.append((sha.strip(), trailers))
    found.reverse()
    return found


# ── the step marker (the evidence of an interrupted step) ─────────────────────

def _marker_path(wt, operation_id: str) -> Path:
    found = _read(wt, ["rev-parse", "--git-path", MARKER_NAME.format(operation_id)]).strip()
    if not found:
        raise GitReadError("git path unreadable")
    path = Path(found)
    return path if path.is_absolute() else Path(wt) / path


def _marker(wt, operation_id: str) -> Optional[dict]:
    path = _marker_path(wt, operation_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GitReadError(f"step marker unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("operation_id") != operation_id:
        raise GitReadError("step marker of another operation")
    return data


def _mark(wt, operation_id: str, step: dict) -> None:
    path = _marker_path(wt, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump({"operation_id": operation_id, **step}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _unmark(wt, operation_id: str) -> None:
    _marker_path(wt, operation_id).unlink(missing_ok=True)


def _owned(operation_id: str):
    """``open_cancel_session``'s ``owned_dirt`` on a resume, under G: the dirty tree is this operation's interrupted
    step — its marker, HEAD still the commit the step started from, every dirty path one the step's commit changes."""
    def owned(wt_path) -> bool:
        try:
            step = _marker(wt_path, operation_id)
            if not step or _head(wt_path) != step.get("head"):
                return False
            dirty = cancel_dirty_paths(wt_path)
        except Exception:  # noqa: BLE001 — unproven: the gate stays closed
            log.warning("Graph Time Machine could not judge its interrupted step operation=%s", operation_id,
                        exc_info=True)
            return False
        if dirty is None or not set(dirty) <= set(step.get("paths") or ()):
            log.warning("Graph Time Machine resume: the dirty worktree is not this operation's step operation=%s",
                        operation_id)
            return False
        return True
    return owned


def _settled(wt, head: str) -> bool:
    """The worktree is the commit ``head`` exactly: HEAD is it, no tracked change (index or tree) nor kept new file,
    and no revert / merge / sequencer state."""
    try:
        if _head(wt) != head:
            return False
    except GitReadError:
        return False
    return not git.cancel_blocking_dirty(Path(wt)) and git.check_git_operation_in_progress(Path(wt)) is None


def _step(session: dict, operation_id: str, kind: str, commit_sha: str, parent: str, *, subject: str, body: str,
          mainline: Optional[int] = None) -> dict:
    """One revert (``kind`` revert / compensate) of ``commit_sha`` under its marker. ``ok`` with the commit, ``empty``
    or ``blocked`` with the worktree settled back at the step's start, or ``unsettled`` (the marker stays)."""
    wt = session["wt_path"]
    head = _head(wt)
    _mark(wt, operation_id, {"kind": kind, "commit": commit_sha, "head": head,
                             "paths": _changed_paths(wt, parent, commit_sha)})
    outcome = git.revert_tr_commit(session, commit_sha=commit_sha, subject=subject, body=body, mainline=mainline)
    if outcome.get("kind") == "ok" and outcome.get("commit"):
        if not _settled(wt, outcome["commit"]):
            return {"kind": "unsettled", "commit": outcome["commit"], "sub": "commit_unsettled"}
    else:
        git.restore_after_failed_revert(session)     # also clears an empty revert's state
        if not _settled(wt, head):
            return {"kind": "unsettled", "commit": None, "sub": outcome.get("sub") or "restore_failed"}
    _unmark(wt, operation_id)
    return outcome


def _revert_body(flow_id: str, operation_id: str, target: dict, first_parent: str) -> str:
    return (f"This reverts commit {target['commit_sha']}, reversing\nchanges made to {first_parent}.\n\n"
            f"FlowGate: Graph Time Machine revert of node {target['node_key']} (flow {flow_id}).\n\n"
            f"{TRAILER_OPERATION}: {operation_id}\nFlowGate-Attempt: {target['attempt_id']}\n"
            f"{TRAILER_REVERTS}: {target['commit_sha']}")


def _compensation_body(flow_id: str, operation_id: str, revert: str) -> str:
    return (f"This reverts commit {revert}.\n\n"
            f"FlowGate: Graph Time Machine compensation (flow {flow_id}).\n\n"
            f"{TRAILER_OPERATION}: {operation_id}\n{TRAILER_COMPENSATES}: {revert}")


def _subject(wt, sha: str) -> str:
    try:
        return (_lines(wt, ["log", "-1", "--format=%s", sha]) or [sha[:12]])[0]
    except GitReadError:
        return sha[:12]                                      # only the message's subject; nothing is decided on it


def _compensate(session: dict, flow_id: str, operation_id: str, made: list[str], compensated: dict[str, str],
                base: Optional[str]) -> bool:
    """Revert this operation's reverts (``made``, in the order they were made) newest first, skipping the ones a
    resumed compensation already reverted; True once the tree is the operation's starting tree ``base`` again and the
    worktree is settled."""
    wt = session["wt_path"]
    for revert in reversed(made):
        if revert in compensated:
            continue
        outcome = _step(session, operation_id, STEP_COMPENSATE, revert, _parents(wt, revert)[0],
                        subject=git.cancel_subject(_subject(wt, revert)),
                        body=_compensation_body(flow_id, operation_id, revert))
        if outcome.get("kind") not in ("ok", "empty"):
            return False
    return bool(base) and tr_commit_service._tree_matches(session, base) and _settled(wt, _head(wt))


def _line(target: dict, revert: Optional[str], outcome: str) -> dict:
    return {"attempt_id": target["attempt_id"], "node_key": target["node_key"], "commit_sha": target["commit_sha"],
            "revert_commit": revert, "outcome": outcome}


def _failed(reason: str, made: list[str], **extra) -> dict:
    return {"status": FAILED, "reason": reason, **extra, "reverts": [{"revert_commit": r} for r in made]}


def _apply(session: dict, flow_id: str, operation_id: str, targets: list[dict], recorded: list[dict],
           resume: bool) -> dict:
    wt = session["wt_path"]
    if resume:
        step = _marker(wt, operation_id)
        if step is not None:
            # An interrupted step of this operation (before its commit, in its conflict, before a compensation's
            # commit): the session let its dirty tree through as this step's own (``_owned``). Back to its start.
            if _head(wt) == step.get("head"):
                git.restore_after_failed_revert(session)
            if not _settled(wt, _head(wt)):
                return _failed("restore_failed", [], cause="interrupted_step", step=step.get("kind"),
                               commit_sha=step.get("commit"))
            _unmark(wt, operation_id)

    found = _journal(wt, operation_id)
    done: dict[str, str] = {}
    compensated: dict[str, str] = {}
    made: list[str] = []                                        # every revert of this operation, oldest first
    for sha, trailers in found:
        if trailers.get(TRAILER_REVERTS):
            done.setdefault(trailers[TRAILER_REVERTS], sha)
            made.append(sha)
        if trailers.get(TRAILER_COMPENSATES):
            compensated.setdefault(trailers[TRAILER_COMPENSATES], sha)
    for r in recorded:                                          # the DB journal's reverts (``git_applied``)
        if r.get("revert_commit") and done.get(r.get("commit_sha")) != r["revert_commit"]:
            return _failed("journal_mismatch", made, commit_sha=r.get("commit_sha"))
    head = _head(wt)
    base = _parents(wt, made[0])[0] if made else head
    line = {sha: i for i, sha in enumerate(_lines(wt, ["rev-list", "--first-parent", "HEAD"]))}

    refused = None
    plan = []
    for target in targets:
        sha = target["commit_sha"]
        if sha not in line:
            refused = refused or ("integration_commit_off_branch", sha)
            continue
        parents = _parents(wt, sha)
        if len(parents) < 2 or not target.get("result_commit") or target["result_commit"] not in parents[1:]:
            refused = refused or ("integration_commit_unverified", sha)
        else:
            plan.append((line[sha], target, parents[0]))
    plan.sort(key=lambda item: item[0])                         # newest first on the first-parent line

    def stop(reason: str, sha: Optional[str]) -> dict:
        if not made:
            if _settled(wt, base):
                return {"status": BLOCKED, "reason": reason, "commit_sha": sha, "compensated": False, "reverts": []}
            return _failed("restore_failed", made, cause=reason, commit_sha=sha)
        if _compensate(session, flow_id, operation_id, made, compensated, base):
            return {"status": BLOCKED, "reason": reason, "commit_sha": sha, "compensated": True, "reverts": []}
        return _failed("compensation_failed", made, cause=reason, commit_sha=sha)

    if compensated:
        return stop("compensation_resumed", None)               # a compensation had begun: finish it
    if refused:
        return stop(*refused)
    reverts = []
    for _i, target, first_parent in plan:
        sha = target["commit_sha"]
        if sha in done:
            reverts.append(_line(target, done[sha], REVERTED))
            continue
        outcome = _step(session, operation_id, STEP_REVERT, sha, first_parent,
                        subject=git.cancel_subject(_subject(wt, sha)),
                        body=_revert_body(flow_id, operation_id, target, first_parent), mainline=1)
        kind = outcome.get("kind")
        if kind == "ok":
            made.append(outcome["commit"])
            reverts.append(_line(target, outcome["commit"], REVERTED))
        elif kind == "empty":
            reverts.append(_line(target, None, EMPTY))          # nothing of it left on the branch
        elif kind == "unsettled":
            return _failed("restore_failed", made + ([outcome["commit"]] if outcome.get("commit") else []),
                           cause=outcome.get("sub"), commit_sha=sha)
        else:
            return stop(outcome.get("sub") or "revert_conflict", sha)
    return {"status": OK, "reverts": reverts, "pre_head": base}


def apply(flow_id: str, operation_id: str, targets: list[dict], *, resume: bool = False,
          recorded: Optional[list[dict]] = None) -> dict:
    """Outside every Flow guard and DB transaction. ``{"status": ok, "reverts", "pre_head"}``, ``blocked`` (proven:
    nothing of the operation on the branch, the worktree settled — refused, or compensated), ``failed`` (unknown or
    not restorable: the operation's partial failure) or, on a resume, ``deferred`` (the session cannot be opened now;
    tried again at the next start). ``recorded``: the reverts the DB journal's ``git_applied`` holds, checked against
    the branch."""
    if not targets:
        return {"status": OK, "reverts": [], "pre_head": None}
    opened = git.open_cancel_session(flow_id, [t["commit_sha"] for t in targets],
                                     owned_dirt=_owned(operation_id) if resume else None)
    if not opened.get("ok"):
        # A first run has made nothing yet; a resumed one may have reverts on the branch it cannot see now.
        return {"status": DEFERRED if resume else BLOCKED, "reason": opened.get("blocked_reason"),
                "sub": opened.get("block_sub"), "compensated": False, "reverts": []}
    session = opened["session"]
    try:
        return _apply(session, flow_id, operation_id, targets, list(recorded or ()), resume)
    except GitReadError as exc:
        log.error("Graph Time Machine Git read failed flow=%s operation=%s: %s", flow_id, operation_id, exc)
        return _failed("git_read_failed", [], cause=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 — the branch state is unknown: a partial failure, resumed at the start
        log.exception("Graph Time Machine Git step failed flow=%s operation=%s", flow_id, operation_id)
        return _failed("git_error", [], cause=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        git.close_cancel_session(session)
