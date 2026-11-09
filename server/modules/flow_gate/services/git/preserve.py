"""Preservation refs for human-approved resolution merge commits (flowgate.v02.0153 T#2).

A resolution merge commit that a person approved is pinned under
``refs/flowgate/preserve/...`` immediately BEFORE the first remote contact (push), so a
later ``reset --hard ORIG_HEAD``, an expired reflog or an external ``git gc`` can never
make the only copy of that resolution unreachable. The ref is a plain shared ref
(``refs/flowgate/*`` lives in the common Git directory of every linked worktree), so
it keeps the commit and both of its parents reachable from the base repository.

Contract (T0007 §3-§5):

* :func:`ensure_preserved` validates the commit (object, exactly two parents
  ``[base_head, merge_head]``, the expected remote head is an ancestor of
  ``base_head``) and creates the ref with a zero-oid CAS (create-only). It is
  idempotent (``same`` when the ref already points at the commit), never overwrites
  another SHA (``ref_conflict``) and never raises: every failure is a result with a
  ``reason``. It performs no remote request.
* :func:`verify_preserved` is the read-only re-check run before an existing reset.
* :func:`record_preserved`, :func:`close_round` and :func:`failure_error` only shape
  the session context; persisting it is the caller's job under its lock.
* :func:`release_preserved_ref` is the ONLY deletion path. It is deliberately not
  wired to any automatic path or scheduler.

Git runs through ``git_service._run_git`` / ``git_service._run_session_git`` (the
shared runner: FlowGate committer, auto gc/maintenance off). ``git_service`` is
reached lazily, exactly like ``branch_merge`` does, so this lower module never imports
an upper orchestrator at import time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

PRESERVE_REF_PREFIX = "refs/flowgate/preserve/"
PRESERVE_RETENTION_DAYS = 30
PRESERVE_HISTORY_MAX = 20
ZERO_OID = "0" * 40

RESULT_CREATED = "created"
RESULT_SAME = "same"
RESULT_VERIFIED = "verified"

# Failure reasons (T0007 §3.1). ``last_error.reason`` is always one of these.
REASON_MERGE_COMMIT_MISSING = "merge_commit_missing"
REASON_COMMIT_MISSING = "commit_missing"
REASON_PARENT_MISMATCH = "parent_mismatch"
REASON_REMOTE_NOT_ANCESTOR = "remote_not_ancestor"
REASON_REF_READ_FAILED = "ref_read_failed"
REASON_REF_CONFLICT = "ref_conflict"
REASON_REF_WRITE_FAILED = "ref_write_failed"
REASON_REF_VERIFY_FAILED = "ref_verify_failed"
REASON_EXCEPTION = "exception"
FAILURE_REASONS = (
    REASON_MERGE_COMMIT_MISSING, REASON_COMMIT_MISSING, REASON_PARENT_MISMATCH,
    REASON_REMOTE_NOT_ANCESTOR, REASON_REF_READ_FAILED, REASON_REF_CONFLICT,
    REASON_REF_WRITE_FAILED, REASON_REF_VERIFY_FAILED, REASON_EXCEPTION,
)

# Where the failure was detected (``last_error.stage``).
STAGE_CREATE = "create"
STAGE_VERIFY = "verify"
STAGE_RECONCILE_GATE = "reconcile_gate"
STAGE_RESET_GATE = "reset_gate"
STAGE_REPUSH = "repush"            # T#3 §3.3 (a): re-verification before a reconcile re-push
STAGE_INTEGRATION = "integration"  # T#3 §3.3 (c): the integration commit could not be pinned

# Round-closing reasons recorded in ``preserve_history``.
CLOSE_RE_REVIEW = "re_review"
CLOSE_REMOTE_UNCHANGED = "reconcile_remote_unchanged"
CLOSE_REMOTE_CONFLICT = "reconcile_remote_conflict"      # T#3 §3.5: handed back with new conflicts
CLOSE_REMOTE_UNVERIFIED = "reconcile_remote_unverified"  # T#3 §3.5: clean, but not auto-verifiable

# Context keys this module owns.
KEY_REF = "preserve_ref"
KEY_SHA = "preserve_sha"
KEY_PARENTS = "preserve_parents"
KEY_AT = "preserved_at"
KEY_ROUND = "preserve_round"
KEY_RETAIN = "preserve_retain_until"
KEY_HISTORY = "preserve_history"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_RE = re.compile(r"^refs/flowgate/preserve/([1-9][0-9]*)(?:-r([1-9][0-9]*))?$")
_INTEGRATION_REF_RE = re.compile(
    r"^refs/flowgate/preserve/([1-9][0-9]*)(?:-r([1-9][0-9]*))?-i([1-9][0-9]*)$")
_DETAIL_MAX = 300


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.match(value))


def _round_of(context: dict) -> int:
    try:
        value = int(context.get(KEY_ROUND) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value >= 0 else 0


def preserve_ref_name(merge_id: int, round: int = 0) -> str:  # noqa: A002 - contract name
    """``refs/flowgate/preserve/<merge_id>`` (round 0) or ``...-r<N>`` (round N ≥ 1).

    Only positive integer merge ids and non-negative integer rounds are accepted, so
    every produced name is a valid ``git check-ref-format`` refname."""
    if isinstance(merge_id, bool) or not isinstance(merge_id, int) or merge_id <= 0:
        raise ValueError(f"merge_id must be a positive integer: {merge_id!r}")
    if isinstance(round, bool) or not isinstance(round, int) or round < 0:
        raise ValueError(f"round must be a non-negative integer: {round!r}")
    name = f"{PRESERVE_REF_PREFIX}{merge_id}"
    return name if round == 0 else f"{name}-r{round}"


def parse_preserve_ref(ref: str) -> Optional[tuple[int, int]]:
    """``(merge_id, round)`` of a name :func:`preserve_ref_name` produced, else None."""
    m = _REF_RE.match(ref or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def preserve_ref_namespace() -> str:
    """The namespace every preservation ref lives under, for READ-ONLY listings
    (``for-each-ref``) outside this module — 0153 T#4's orphan guard reads it to find a
    merge's refs on disk. Deletion stays this module's alone (§5.2/§5.4)."""
    return PRESERVE_REF_PREFIX


# ── Git reads (never audited, never remote) ──────────────────────────────────

def _git(repo: Path, args: list[str]):
    gs = _gs()
    return gs._run_git(args, cwd=repo, timeout=gs.GIT_LOCAL_TIMEOUT_SEC)


def _detail(proc) -> Optional[str]:
    """T#1 rule: scrubbed stderr (the shared runner already masked it), last useful
    line, at most 300 characters. Never argv, config or secrets."""
    gs = _gs()
    text = gs._scrub(getattr(proc, "stderr", None) or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "git command failed")[:_DETAIL_MAX]


class _ReadError(Exception):
    def __init__(self, proc=None):
        super().__init__("git read failed")
        self.proc = proc


def _read_ref(repo: Path, ref: str) -> Optional[str]:
    """The exact full refname's SHA; None when absent (exit 1); _ReadError otherwise."""
    proc = _git(repo, ["rev-parse", "--verify", "--quiet", ref])
    if proc.returncode == 0:
        value = (proc.stdout or "").strip()
        if _is_sha(value):
            return value
        raise _ReadError(proc)
    if proc.returncode == 1:
        return None
    raise _ReadError(proc)


def _result(ok: bool, *, status: Optional[str] = None, reason: Optional[str] = None,
            ref: Optional[str] = None, expected: Optional[str] = None,
            observed: Optional[str] = None, parents: Optional[list] = None,
            detail: Optional[str] = None) -> dict:
    return {
        "ok": ok, "result": status, "reason": reason, "ref": ref,
        "expected_sha": expected, "observed_sha": observed,
        "parents": list(parents) if parents else None, "detail": detail,
    }


def _fail(reason: str, ref: Optional[str], expected: Optional[str], *,
          observed: Optional[str] = None, detail: Optional[str] = None) -> dict:
    return _result(False, reason=reason, ref=ref, expected=expected, observed=observed,
                   detail=detail)


def _check_commit(repo: Path, sha: str, context: dict, ref: Optional[str]) -> Optional[dict]:
    """§3.1-2 object / parent / remote-ancestor checks. None when every check passes."""
    proc = _git(repo, ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])
    if proc.returncode != 0 or (proc.stdout or "").strip() != sha:
        return _fail(REASON_COMMIT_MISSING, ref, sha)
    proc = _git(repo, ["rev-list", "--parents", "-n", "1", sha])
    if proc.returncode != 0:
        return _fail(REASON_REF_READ_FAILED, ref, sha, detail=_detail(proc))
    tokens = (proc.stdout or "").split()
    if not tokens or tokens[0] != sha:
        return _fail(REASON_REF_READ_FAILED, ref, sha)
    parents = tokens[1:]
    base_head = context.get("base_head")
    merge_head = context.get("merge_head")
    if (len(parents) != 2 or not _is_sha(base_head) or not _is_sha(merge_head)
            or parents[0] != base_head or parents[1] != merge_head):
        return _fail(REASON_PARENT_MISMATCH, ref, sha)
    expected_remote = context.get("expected_remote_head")
    if expected_remote:
        proc = _git(repo, ["merge-base", "--is-ancestor", str(expected_remote), base_head])
        if proc.returncode == 1:
            return _fail(REASON_REMOTE_NOT_ANCESTOR, ref, sha)
        if proc.returncode != 0:
            return _fail(REASON_REF_READ_FAILED, ref, sha, detail=_detail(proc))
    return None


def _target_ref(merge_id: int, context: dict, sha: str) -> tuple[Optional[str], Optional[dict]]:
    """This round's ref name, or the §3.2 conflict result (a different SHA is already
    recorded for this round — never silently replaced, never a round bump here)."""
    ref = preserve_ref_name(int(merge_id), _round_of(context))
    recorded_sha = context.get(KEY_SHA)
    recorded_ref = context.get(KEY_REF)
    if recorded_sha and recorded_sha != sha:
        return ref, _fail(REASON_REF_CONFLICT, recorded_ref or ref, sha, observed=recorded_sha)
    if recorded_ref and recorded_ref != ref:
        return ref, _fail(REASON_REF_CONFLICT, recorded_ref, sha, observed=recorded_sha)
    return ref, None


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_preserved(repo: Path, merge_id: int, context: dict, *,
                     project_id: Optional[str] = None, group_id: Optional[str] = None) -> dict:
    """Create (or confirm) this round's preservation ref for ``context['merge_commit']``.

    Returns ``{"ok", "result": "created"|"same"|None, "reason", "ref", "expected_sha",
    "observed_sha", "parents", "detail"}``. Never raises."""
    ref: Optional[str] = None
    sha_value = context.get("merge_commit") if isinstance(context, dict) else None
    expected = sha_value if isinstance(sha_value, str) and sha_value else None
    try:
        if not _is_sha(sha_value):
            try:
                ref = preserve_ref_name(int(merge_id), _round_of(context or {}))
            except Exception:
                ref = None
            return _fail(REASON_MERGE_COMMIT_MISSING, ref, expected)
        sha = str(sha_value)
        ref, conflict = _target_ref(merge_id, context, sha)
        if conflict is not None:
            return conflict
        failed = _check_commit(repo, sha, context, ref)
        if failed is not None:
            return failed
        parents = [context.get("base_head"), context.get("merge_head")]
        try:
            current = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_READ_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if current == sha:
            return _result(True, status=RESULT_SAME, ref=ref, expected=sha, observed=sha,
                           parents=parents)
        if current is not None:
            return _fail(REASON_REF_CONFLICT, ref, sha, observed=current)
        gs = _gs()
        write = gs._run_session_git(
            int(merge_id), context, "update-ref", "preserve_merge_commit",
            ["update-ref", "--create-reflog", "-m", f"flowgate: preserve merge {int(merge_id)}",
             ref, sha, ZERO_OID],
            cwd=repo, timeout=gs.GIT_LOCAL_TIMEOUT_SEC,
        )
        _invalidate(project_id, group_id)
        if write.returncode != 0:
            try:
                again = _read_ref(repo, ref)
            except _ReadError:
                again = None
            if again == sha:
                return _result(True, status=RESULT_SAME, ref=ref, expected=sha, observed=sha,
                               parents=parents)
            if again is not None:
                return _fail(REASON_REF_CONFLICT, ref, sha, observed=again)
            return _fail(REASON_REF_WRITE_FAILED, ref, sha, detail=_detail(write))
        try:
            written = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if written != sha:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha, observed=written)
        return _result(True, status=RESULT_CREATED, ref=ref, expected=sha, observed=sha,
                       parents=parents)
    except Exception:
        _log.warning("preserve ref creation failed for merge %s", merge_id, exc_info=True)
        return _fail(REASON_EXCEPTION, ref, expected)


def verify_preserved(repo: Path, context: dict) -> dict:
    """Read-only re-check before a reset: the recorded ref exists, ``preserve_sha ==
    merge_commit ==`` the ref's value, and the commit still passes the object/parent
    checks. Never writes, never raises."""
    ref = context.get(KEY_REF) if isinstance(context, dict) else None
    sha_value = context.get("merge_commit") if isinstance(context, dict) else None
    expected = sha_value if isinstance(sha_value, str) and sha_value else None
    try:
        if not _is_sha(sha_value):
            return _fail(REASON_MERGE_COMMIT_MISSING, ref, expected)
        sha = str(sha_value)
        recorded = context.get(KEY_SHA)
        if not ref or not recorded:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha, observed=recorded or None)
        if recorded != sha:
            return _fail(REASON_REF_CONFLICT, ref, sha, observed=recorded)
        if parse_preserve_ref(ref) is None:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha)
        try:
            current = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_READ_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if current is None:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha)
        if current != sha:
            return _fail(REASON_REF_CONFLICT, ref, sha, observed=current)
        failed = _check_commit(repo, sha, context, ref)
        if failed is not None:
            failed["observed_sha"] = current
            return failed
        return _result(True, status=RESULT_VERIFIED, ref=ref, expected=sha, observed=current,
                       parents=[context.get("base_head"), context.get("merge_head")])
    except Exception:
        _log.warning("preserve ref verification failed", exc_info=True)
        return _fail(REASON_EXCEPTION, ref, expected)


def _invalidate(project_id: Optional[str], group_id: Optional[str]) -> None:
    from . import merge_target
    try:
        merge_target.invalidate_check_targets(project_id, group_id)
    except Exception:
        _log.warning("check target invalidation failed after a preserve ref write", exc_info=True)


def _plus_days(iso_value: str, days: int) -> str:
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    return (dt + timedelta(days=days)).isoformat()


def record_preserved(context: dict, result: dict, *, now: str) -> dict:
    """§3.3: write the preservation facts of a ``created``/``same`` result into the
    context (the caller persists it under its lock BEFORE any push). A repeated
    ``same`` keeps the first ``preserved_at``."""
    sha = result["expected_sha"]
    first = context.get(KEY_AT) if context.get(KEY_SHA) == sha and context.get(KEY_AT) else None
    context[KEY_REF] = result["ref"]
    context[KEY_SHA] = sha
    context[KEY_PARENTS] = list(result.get("parents") or [])
    context[KEY_AT] = first or now
    context[KEY_ROUND] = _round_of(context)
    context[KEY_RETAIN] = _plus_days(context[KEY_AT], PRESERVE_RETENTION_DAYS)
    return context


def close_round(context: dict, reason: str, *, now: str) -> bool:
    """§3.2: the resolution commit leaves the session (re_review success /
    remote-unchanged reset). Move the current preservation into ``preserve_history``
    (last 20), clear the live fields and advance the round. The ref itself is kept.
    A session that never preserved anything is left as it is (False)."""
    if not context.get(KEY_SHA) and not context.get(KEY_REF):
        return False
    entry = {
        "ref": context.get(KEY_REF), "sha": context.get(KEY_SHA),
        "round": _round_of(context), "preserved_at": context.get(KEY_AT),
        "retain_until": context.get(KEY_RETAIN),
        "closed_at": now, "closed_reason": reason,
    }
    history = context.get(KEY_HISTORY)
    history = list(history) if isinstance(history, list) else []
    context[KEY_HISTORY] = [*history, entry][-PRESERVE_HISTORY_MAX:]
    context[KEY_REF] = None
    context[KEY_SHA] = None
    context[KEY_PARENTS] = None
    context[KEY_AT] = None
    context[KEY_RETAIN] = None
    context[KEY_ROUND] = _round_of(context) + 1
    return True


def failure_error(context: dict, result: dict, stage: str, *, now: str,
                  instance_id: Optional[str]) -> dict:
    """§3.4 ``last_error`` of a preserve failure. A T#1 ``push_remote_unknown`` that
    was there before (directly, or already carried by an earlier preserve failure) is
    kept verbatim under ``prior_push_error``."""
    error = {
        "code": "preserve_failed", "stage": stage,
        "reason": result.get("reason") if result.get("reason") in FAILURE_REASONS else REASON_EXCEPTION,
        "preserve_ref": result.get("ref"),
        "expected_sha": result.get("expected_sha"),
        "observed_sha": result.get("observed_sha"),
        "at": now, "instance_id": instance_id,
    }
    if result.get("detail"):
        error["detail"] = str(result["detail"])[:_DETAIL_MAX]
    prior = context.get("last_error")
    if isinstance(prior, dict):
        if prior.get("code") == "push_remote_unknown":
            error["prior_push_error"] = prior
        elif prior.get("code") == "preserve_failed" and isinstance(prior.get("prior_push_error"), dict):
            error["prior_push_error"] = prior["prior_push_error"]
    return error


# ── Integration commits (flowgate.v02.0153 T#3 §3.3 (c)) ─────────────────────
# A reconcile that found the remote ahead with another group's commit builds an
# integration commit I = commit-tree(T, -p publish_commit, -p F) off-tree and pins it
# here BEFORE the local branch moves to it. The resolution ref of the round is left
# exactly as it is; both the resolution result and the new remote tip stay reachable
# as I's parents.

def integration_ref_name(merge_id: int, round: int, k: int) -> str:  # noqa: A002
    """``refs/flowgate/preserve/<merge_id>[-r<N>]-i<k>`` (k ≥ 1)."""
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError(f"integration index must be a positive integer: {k!r}")
    return f"{preserve_ref_name(merge_id, round)}-i{k}"


def parse_integration_ref(ref: str) -> Optional[tuple[int, int, int]]:
    """``(merge_id, round, k)`` of a name :func:`integration_ref_name` produced, else None."""
    m = _INTEGRATION_REF_RE.match(ref or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3))


def round_integrations(merge_id: int, context: dict) -> list[dict]:
    """The ``integration_commits`` entries that belong to the current round."""
    current = _round_of(context)
    out = []
    for entry in context.get("integration_commits") or []:
        parsed = parse_integration_ref((entry or {}).get("ref") or "") if isinstance(entry, dict) else None
        if parsed and parsed[0] == int(merge_id) and parsed[1] == current:
            out.append(entry)
    return out


def _check_parents(repo: Path, sha: str, parents: list, ref: Optional[str]) -> Optional[dict]:
    proc = _git(repo, ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])
    if proc.returncode != 0 or (proc.stdout or "").strip() != sha:
        return _fail(REASON_COMMIT_MISSING, ref, sha)
    proc = _git(repo, ["rev-list", "--parents", "-n", "1", sha])
    if proc.returncode != 0:
        return _fail(REASON_REF_READ_FAILED, ref, sha, detail=_detail(proc))
    tokens = (proc.stdout or "").split()
    if not tokens or tokens[0] != sha or tokens[1:] != list(parents):
        return _fail(REASON_PARENT_MISMATCH, ref, sha)
    return None


def ensure_integration_preserved(repo: Path, merge_id: int, context: dict, sha: str,
                                 parents: list, k: int) -> dict:
    """Pin an integration commit with a zero-oid CAS (create-only, reason
    ``preserve_integration_commit``) after checking its parents are exactly
    ``[publish_commit, F]``. Same result shape as :func:`ensure_preserved`; never raises."""
    ref: Optional[str] = None
    try:
        ref = integration_ref_name(int(merge_id), _round_of(context), int(k))
        if not _is_sha(sha) or len(parents) != 2 or not all(_is_sha(p) for p in parents):
            return _fail(REASON_MERGE_COMMIT_MISSING, ref, sha if isinstance(sha, str) else None)
        failed = _check_parents(repo, sha, parents, ref)
        if failed is not None:
            return failed
        try:
            current = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_READ_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if current == sha:
            return _result(True, status=RESULT_SAME, ref=ref, expected=sha, observed=sha, parents=parents)
        if current is not None:
            return _fail(REASON_REF_CONFLICT, ref, sha, observed=current)
        gs = _gs()
        write = gs._run_session_git(
            int(merge_id), context, "update-ref", "preserve_integration_commit",
            ["update-ref", "--create-reflog", "-m",
             f"flowgate: preserve integration {int(merge_id)}-i{int(k)}", ref, sha, ZERO_OID],
            cwd=repo, timeout=gs.GIT_LOCAL_TIMEOUT_SEC,
        )
        if write.returncode != 0:
            try:
                again = _read_ref(repo, ref)
            except _ReadError:
                again = None
            if again == sha:
                return _result(True, status=RESULT_SAME, ref=ref, expected=sha, observed=sha,
                               parents=parents)
            if again is not None:
                return _fail(REASON_REF_CONFLICT, ref, sha, observed=again)
            return _fail(REASON_REF_WRITE_FAILED, ref, sha, detail=_detail(write))
        try:
            written = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if written != sha:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha, observed=written)
        return _result(True, status=RESULT_CREATED, ref=ref, expected=sha, observed=sha, parents=parents)
    except Exception:
        _log.warning("integration ref creation failed for merge %s", merge_id, exc_info=True)
        return _fail(REASON_EXCEPTION, ref, sha if isinstance(sha, str) else None)


def verify_integration_preserved(repo: Path, entry: dict) -> dict:
    """Read-only re-check of one recorded integration commit: the ref still holds the
    SHA and the commit still has the recorded parents. Never writes, never raises."""
    ref = (entry or {}).get("ref")
    sha = (entry or {}).get("sha")
    try:
        if parse_integration_ref(ref or "") is None or not _is_sha(sha):
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha if isinstance(sha, str) else None)
        try:
            current = _read_ref(repo, ref)
        except _ReadError as exc:
            return _fail(REASON_REF_READ_FAILED, ref, sha,
                         detail=_detail(exc.proc) if exc.proc is not None else None)
        if current is None:
            return _fail(REASON_REF_VERIFY_FAILED, ref, sha)
        if current != sha:
            return _fail(REASON_REF_CONFLICT, ref, sha, observed=current)
        failed = _check_parents(repo, sha, list(entry.get("parents") or []), ref)
        if failed is not None:
            failed["observed_sha"] = current
            return failed
        return _result(True, status=RESULT_VERIFIED, ref=ref, expected=sha, observed=current,
                       parents=list(entry.get("parents") or []))
    except Exception:
        _log.warning("integration ref verification failed", exc_info=True)
        return _fail(REASON_EXCEPTION, ref, sha if isinstance(sha, str) else None)


def verify_round(repo: Path, merge_id: int, context: dict) -> dict:
    """:func:`verify_preserved` for the resolution ref plus every integration ref of the
    current round. The first failure is returned as is."""
    result = verify_preserved(repo, context)
    if not result.get("ok"):
        return result
    for entry in round_integrations(merge_id, context):
        checked = verify_integration_preserved(repo, entry)
        if not checked.get("ok"):
            return checked
    return result


def _retain_until_of(context: dict, ref: str) -> Optional[str]:
    if context.get(KEY_REF) == ref:
        return context.get(KEY_RETAIN)
    for entry in reversed(context.get(KEY_HISTORY) or []):
        if isinstance(entry, dict) and entry.get("ref") == ref:
            return entry.get("retain_until")
    return None


def _session_target_branch(context: dict) -> Optional[str]:
    from . import merge_target
    branch = context.get(merge_target.TARGET_BRANCH_KEY)
    if not branch:
        branch = (context.get("branch_merge") or {}).get("target_branch")
    return str(branch) if branch else None


def release_preserved_ref(repo: Path, ref: str, expected_sha: str, *, now: str,
                          session: Optional[dict] = None, branch: Optional[str] = None,
                          context: Optional[dict] = None) -> dict:
    """The ONLY way a preservation ref is deleted (T0007 §5.4) — explicit, never wired
    to an automatic path or scheduler. Allowed only when the owning session is closed
    as completed, the local ``refs/remotes/origin/<branch>`` already contains
    ``expected_sha`` (no remote request) and the retention period is over. The delete
    is a CAS on ``expected_sha``. Returns ``{"released": bool, "reason": str}``.

    The delete is a session ref move, so it runs through ``_run_session_git`` (T#1
    §5.3): success or failure, ``ref_operations`` of the owning session gets the
    operation, reason, time, instance and the real return code, persisted at once.
    A caller that holds the session's live ``context`` passes it so the audit entry is
    appended to that same object and survives the caller's later whole-JSON write."""
    parsed = parse_preserve_ref(ref)
    if parsed is None:
        return {"released": False, "reason": "invalid_ref"}
    if not _is_sha(expected_sha):
        return {"released": False, "reason": "invalid_sha"}
    gs = _gs()
    merge_id, _round = parsed
    if session is None:
        session = gs.db_git.get_session(merge_id)
    if session is None or int(session.get("merge_id") or 0) != merge_id:
        return {"released": False, "reason": "session_missing"}
    if context is None:
        context = gs.db_git.session_context(session)
    if session.get("status") != "done" or context.get("review_state") != gs.REVIEW_STATE_COMPLETED:
        return {"released": False, "reason": "session_not_completed"}
    retain_until = _retain_until_of(context, ref)
    if not retain_until:
        return {"released": False, "reason": "retention_unknown"}
    try:
        due = datetime.fromisoformat(retain_until)
        current = datetime.fromisoformat(now)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {"released": False, "reason": "retention_unknown"}
    if current < due:
        return {"released": False, "reason": "retention_not_elapsed"}
    branch = branch or _session_target_branch(context)
    if not branch:
        return {"released": False, "reason": "branch_unknown"}
    try:
        value = _read_ref(repo, ref)
    except _ReadError:
        return {"released": False, "reason": "ref_read_failed"}
    if value is None:
        return {"released": False, "reason": "ref_absent"}
    if value != expected_sha:
        return {"released": False, "reason": "ref_conflict"}
    contained = _git(repo, ["merge-base", "--is-ancestor", expected_sha,
                            f"refs/remotes/origin/{branch}"])
    if contained.returncode == 1:
        return {"released": False, "reason": "remote_missing_commit"}
    if contained.returncode != 0:
        return {"released": False, "reason": "ref_read_failed"}
    try:
        proc = gs._run_session_git(
            merge_id, context, "update-ref", "release_preserve_ref",
            ["update-ref", "-d", ref, expected_sha],
            cwd=repo, timeout=gs.GIT_LOCAL_TIMEOUT_SEC,
        )
    except Exception:
        # The audit entry (returncode=None, status=exception) is already persisted.
        _log.warning("preserve ref release failed for %s", ref, exc_info=True)
        proc = None
    _invalidate(gs.db_git.session_project_id(session), session.get("group_id"))
    if proc is None or proc.returncode != 0:
        return {"released": False, "reason": "ref_delete_failed"}
    return {"released": True, "reason": "released"}
