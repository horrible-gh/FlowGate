"""Baseline reuse judgement for Self-check runs (flowgate.v02.0114 T#3, D 0006 3.4).

``judge`` decides from two stored run records, the after-run's pre-change anchor and an
ancestry check whether a baseline run may stand in for re-running the regression before the
change, and only then splits the failures into new / existing / definition-changed /
resolved-or-gone. Every condition is checked; the first missing or differing piece of
evidence makes the baseline unusable. Missing evidence is never read as "the same".

The result is advisory evidence (D 3.4, 8-4): it reads nothing but stored records, changes
neither run, and is not a pass/fail verdict -- TR / review pass criteria, required gates and
rejection rules are unchanged.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from modules.flow_gate.services import tr_self_check_result as result_block

#: ``git status --porcelain`` / ``git diff --cached`` output of a clean tree, as stored by
#: ``tr_self_check_service._probe``.
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
#: (f) of D 3.4 condition 7: the run row's copied host identifier.
HOST_CATEGORY = "host_node_key"
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")

ADVISORY_NOTE = ("Advisory evidence only: it does not change any run, and TR / review pass criteria, "
                 "required gates and rejection rules are unchanged.")


def _reason(code: str, **detail) -> dict:
    return {"code": code, **detail}


def _baseline_ended_normally(row: dict) -> list[dict]:
    """Condition 2 (+4): completed normally -- not timed out, cancelled, failed or recovered;
    the exit code may be non-zero."""
    reasons = []
    if row.get("status") != "completed":
        reasons.append(_reason("baseline_not_completed", status=row.get("status"),
                               error_code=row.get("error_code")))
    if row.get("timed_out"):
        reasons.append(_reason("baseline_timed_out"))
    if row.get("cancel_requested"):
        reasons.append(_reason("baseline_cancel_requested"))
    if (row.get("recovery_state") or "none") != "none":
        reasons.append(_reason("baseline_recovery_state", recovery_state=row.get("recovery_state")))
    if row.get("source_changed_during_run") or row.get("worktree_state_changed"):
        reasons.append(_reason("baseline_source_changed"))
    return reasons


def _after_usable(row: dict, summary: Optional[dict]) -> list[dict]:
    """The after-run must itself be a complete, normally ended record to be compared at all."""
    reasons = []
    if row.get("status") != "completed" or row.get("timed_out") or (row.get("recovery_state") or "none") != "none":
        reasons.append(_reason("after_run_not_completed", status=row.get("status"),
                               timed_out=bool(row.get("timed_out"))))
    if row.get("source_changed_during_run") or row.get("worktree_state_changed"):
        reasons.append(_reason("after_run_source_changed"))
    status = (summary or {}).get("status", result_block.NONE)
    if status != result_block.COMPLETE:
        reasons.append(_reason("after_run_summary_" + status, detail=(summary or {}).get("reason")))
    return reasons


def _anchor_reasons(row: dict, anchor: Optional[dict], after: dict,
                    is_ancestor: Callable[[str, str], Optional[bool]]) -> list[dict]:
    """Condition 5: the baseline ran on the after-run's pre-change anchor commit, clean before
    and after, and that commit is still an ancestor of the after-run's HEAD."""
    if not anchor or anchor.get("state") != "recorded" or not anchor.get("base_commit"):
        return [_reason("anchor_missing", detail=(anchor or {}).get("reason"))]
    commit = anchor["base_commit"]
    reasons = []
    if not (row.get("source_head_before") == commit == row.get("source_head_after")):
        reasons.append(_reason("head_mismatch", anchor_commit=commit,
                               baseline_head_before=row.get("source_head_before"),
                               baseline_head_after=row.get("source_head_after")))
    clean = all(row.get(k) == EMPTY_SHA256 for k in (
        "source_status_hash_before", "source_status_hash_after"))
    staged_clean = all(row.get(k) in (None, EMPTY_SHA256) for k in (
        "source_index_hash_before", "source_index_hash_after"))
    if not (clean and staged_clean):
        reasons.append(_reason("dirty_worktree"))
    head = after.get("source_head_after") or after.get("source_head_before")
    ancestry = is_ancestor(commit, head) if head else None
    if ancestry is False:
        reasons.append(_reason("anchor_not_ancestor", anchor_commit=commit, head=head))
    elif ancestry is None:
        reasons.append(_reason("ancestry_unverified", anchor_commit=commit, head=head))
    return reasons


def _command_reasons(baseline: dict, after: dict) -> list[dict]:
    """Condition 6: program, args, cwd and policy version all equal -- any differing selection
    argument is a different selection."""
    fields = [f for f in ("program", "args", "cwd_relative", "policy_version") if baseline.get(f) != after.get(f)]
    return [_reason("command_mismatch", fields=fields)] if fields else []


def _environment_reasons(baseline: dict, after: dict) -> list[dict]:
    """Condition 7: every category present, readable and equal on both sides (a-e from the
    block, f = host_node_key). Absent evidence -- a block without it, or a run from before
    this check existed -- is "missing", never "same"."""
    benv = result_block.loads(baseline.get("environment_evidence")) or {}
    aenv = result_block.loads(after.get("environment_evidence")) or {}
    reasons = []
    for category in result_block.ENV_CATEGORIES:
        b, a = benv.get(category), aenv.get(category)
        # Re-checked here: a category that lost a sub-item -- even the same one on both
        # sides -- is unreadable, never equal.
        b = None if b is None else result_block.check_category(category, b)
        a = None if a is None else result_block.check_category(category, a)
        if b is None or a is None:
            reasons.append(_reason("environment_missing", category=category,
                                   side="both" if b is None and a is None else ("baseline" if b is None else "after")))
        elif result_block.UNREADABLE in (b, a):
            reasons.append(_reason("environment_unreadable", category=category))
        elif b != a:
            reasons.append(_reason("environment_mismatch", category=category))
    bhost, ahost = baseline.get("host_node_key"), after.get("host_node_key")
    if not bhost or not ahost:
        reasons.append(_reason("environment_missing", category=HOST_CATEGORY,
                               side="both" if not bhost and not ahost else ("baseline" if not bhost else "after")))
    elif bhost != ahost:
        reasons.append(_reason("environment_mismatch", category=HOST_CATEGORY))
    return reasons


def _run_definition_reasons(bdefs: Optional[dict], adefs: Optional[dict]) -> list[dict]:
    """Condition 8 (a): the run-level definition (pytest settings, conftest chain and its hooks,
    session/autouse fixtures) must be present and equal, else the whole baseline is out."""
    for side, defs in (("baseline", bdefs), ("after", adefs)):
        if defs is None:
            return [_reason("definition_run_missing", side=side)]
        if defs.get("run_status") != "ok" or not defs.get("run"):
            return [_reason("definition_run_unverifiable", side=side, run_status=defs.get("run_status"))]
    if bdefs["run"] != adefs["run"]:
        return [_reason("definition_run_mismatch")]
    return []


def _item_definition(bdefs: dict, adefs: dict, nodeid: str) -> str:
    """same / changed / untraceable / missing for one nodeid (condition 8 (b)(c))."""
    b, a = bdefs["items"].get(nodeid), adefs["items"].get(nodeid)
    if b is None or a is None:
        return "missing"
    if result_block.UNTRACEABLE in (b, a):
        return "untraceable"
    return "same" if a == b else "changed"


def judge(baseline: dict, after: dict, *, anchor: Optional[dict],
          is_ancestor: Callable[[str, str], Optional[bool]]) -> dict:
    """The baseline judgement of ``after`` against ``baseline`` (both stored run rows)."""
    result = {
        "baseline_run_id": baseline.get("self_check_run_id"), "run_id": after.get("self_check_run_id"),
        "reusable": False, "reasons": [], "comparison": None, "advisory": True, "note": ADVISORY_NOTE,
        "anchor": None if not anchor else {k: anchor.get(k) for k in ("anchor_id", "state", "base_commit", "source", "reason")},
    }
    # Condition 1 first: nothing of another Group's run is compared or echoed.
    if (baseline.get("project_id"), baseline.get("group_id")) != (after.get("project_id"), after.get("group_id")):
        result["reasons"] = [_reason("different_group")]
        return result
    bsum = result_block.loads(baseline.get("result_summary"))
    asum = result_block.loads(after.get("result_summary"))
    reasons = _baseline_ended_normally(baseline)
    bstatus = (bsum or {}).get("status", result_block.NONE)
    if bstatus != result_block.COMPLETE:                       # condition 3
        reasons.append(_reason("baseline_summary_" + bstatus, detail=(bsum or {}).get("reason")))
    if baseline.get("self_check_run_id") == after.get("self_check_run_id"):
        reasons.append(_reason("baseline_is_the_run"))
    reasons += _after_usable(after, asum)
    reasons += _anchor_reasons(baseline, anchor, after, is_ancestor)
    reasons += _command_reasons(baseline, after)
    reasons += _environment_reasons(baseline, after)
    bdefs = (bsum or {}).get("definitions")
    adefs = (asum or {}).get("definitions")
    reasons += _run_definition_reasons(bdefs, adefs)
    result["reasons"] = reasons
    if reasons:
        return result

    bfail = {f["nodeid"]: f for f in bsum["failures"]}
    afail = {f["nodeid"]: f for f in asum["failures"]}
    existing, changed = [], []
    for nodeid in sorted(set(afail) & set(bfail)):
        state = _item_definition(bdefs, adefs, nodeid)
        if state == "same":
            existing.append(nodeid)
        else:
            changed.append({"nodeid": nodeid, "definition": state})
    result["reusable"] = True
    result["comparison"] = {
        # New tests and renamed tests have no baseline failure to pair with: new failures.
        "new_failures": sorted(set(afail) - set(bfail)),
        "existing_failures": existing,
        "definition_changed_failures": changed,
        "resolved_or_gone": [{"nodeid": n, "definition": _item_definition(bdefs, adefs, n)}
                             for n in sorted(set(bfail) - set(afail))],
    }
    return result


# ── read path (tr_self_check_service.read / read_draft) ─────────────────────

def _is_ancestor(project_id: str, group_id: str) -> Callable[[str, str], Optional[bool]]:
    def check(commit: str, head: str) -> Optional[bool]:
        if not (_COMMIT.match(commit or "") and _COMMIT.match(head or "")):
            return None
        from modules.flow_gate.services import git_service
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if reason != "worktree" or not root or not Path(root).is_dir():
            return None
        try:
            done = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, head],
                                  shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=15, check=False)
        except Exception:
            return None
        return {0: True, 1: False}.get(done.returncode)
    return check


def compare(after: dict, baseline_run_id: str) -> dict:
    """``judge`` for a stored after-run and a caller-named baseline run id."""
    from modules.flow_gate.db import tr_self_check_runs as db_runs
    from modules.flow_gate.services import tr_self_check_anchor as anchors
    baseline = db_runs.get_run(baseline_run_id) if isinstance(baseline_run_id, str) and baseline_run_id else None
    if baseline is None:
        return {"baseline_run_id": baseline_run_id, "run_id": after.get("self_check_run_id"), "reusable": False,
                "reasons": [_reason("baseline_not_found")], "comparison": None, "anchor": None,
                "advisory": True, "note": ADVISORY_NOTE}
    return judge(baseline, after, anchor=anchors.resolve_for_run(after),
                 is_ancestor=_is_ancestor(after["project_id"], after["group_id"]))
