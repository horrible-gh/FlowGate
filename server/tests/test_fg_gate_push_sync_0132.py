"""(NR /;, 2, 3, 6, 7, 10): the push phase's remote sync on a real repository and a
temporary bare origin.

Uses the harness of ``test_fg_gate_publish_0085`` (real SQLite DB, routes, a bare ``origin``, the provisioned group
slot, the Runner / Sweeper). The remote is moved ahead from the harness's ``seed`` clone, never through the product.
Every product Git command of these tests goes through a guard that fails the test on a forced push (``--force*``,
``-f``, a ``+`` refspec) or a ``reset --hard``; the remote's earlier tips are checked to stay in its history.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _sql, db_path, edge, env, node  # noqa: E402,F401
from test_fg_gate_intake_0085 import _confirmed, _gate_events, _start, _states, product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    PROJECT, _act, _confirm, _decision, _effects, _evidence, _expire_and_reconcile, _gate, _git, _merges,
    _push_rejected_once, _rev, _run_job, _side_commit, _start_linear, _version, _work, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")

NON_BASE = "review/selected"


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def pushes(git_env, monkeypatch):
    """Every push the product runs (its args), with a forced push or a ``reset --hard`` failing the test."""
    from modules.flow_gate.services import git_service as svc
    real, seen = svc._run_git, []

    def guarded(args, **kwargs):
        if args and args[0] == "push":
            assert not any(a.startswith("--force") or a == "-f" or a.startswith("+") for a in args[1:]), args
            seen.append(list(args))
        assert not (args and args[0] == "reset" and "--hard" in args), args
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", guarded)
    return seen


def _ev(row) -> dict:
    return json.loads(row["evidence_json"] or "{}")


def _ancestor(repo, older, newer) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=str(repo),
                          capture_output=True).returncode == 0


def _parents(repo, sha) -> list:
    return _git(["rev-list", "--parents", "-n", "1", sha], cwd=repo).split()[1:]


def _advance(r, branch, name, text="remote\n"):
    """Another writer moves origin's ``branch`` ahead (from the seed clone): its new tip."""
    seed = r["seed"]
    _git(["fetch", "origin"], cwd=seed)
    if _rev(r["bare"], "refs/heads/" + branch):
        _git(["checkout", "-B", branch, "origin/" + branch], cwd=seed)
    else:
        _git(["checkout", "-B", branch, "main"], cwd=seed)
    (seed / name).write_text(text, encoding="utf-8")
    _git(["add", name], cwd=seed)
    _git(["commit", "-m", f"remote {name}"], cwd=seed)
    _git(["push", "origin", branch], cwd=seed)
    return _rev(seed, "HEAD")


def _due(job_id):
    from modules.flow_gate.db.connection import get_store
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])


def _target(r, target):
    if target == "base":
        return "main"
    from modules.flow_gate.services.git import merge_target as mt
    _git(["branch", NON_BASE, "main"], cwd=r["base"])
    mt.reset_check_targets()        # a branch made outside FlowGate: forget the list from before
    return NON_BASE


def _gate_merge(env, g, branch, **more):
    if branch == "main":
        _gate(env, g, merge_mode="merge", merge_target="origin", push=True, **more)
    else:
        _gate(env, g, merge_mode="merge", merge_target_branch=branch, push=True, **more)


def _accepted(r, target, *, archive=True, diverge_base=False, name="ours.txt", text="ours\n", request="sync-0132",
              contained=False):
    """A merge-mode confirm with push accepted on the project base (``main``) or a non-base local target.
    ``contained``: the local base already holds the frozen result (the approved merge is ``contained``, no change)."""
    env, g, _, run_id = _start_linear(r)
    branch = _target(r, target)
    if diverge_base:                                  # the local target has its own commit: A0 is a merge commit
        (r["base"] / "base.txt").write_text("local target work\n", encoding="utf-8")
        _git(["add", "base.txt"], cwd=r["base"])
        _git(["commit", "-m", "local target work"], cwd=r["base"])
    frozen = _work(r, name, text)
    if contained:
        _git(["merge", "--ff-only", frozen], cwd=r["base"])
    _gate_merge(env, g, branch, archive=archive)
    body = _confirm(env, run_id, g, request)
    return env, g, run_id, branch, frozen, body["decision"]["decision_id"], body["job"]["job_id"]


def _checkpoint(env, did):
    rows = _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))
    assert len(rows) == 1
    return rows[0]


# ── §5.3-1: the remote went ahead (this bug), no conflict ────────────────────



# ── §5.3-2: the remote keeps moving ──────────────────────────────────────────



# ── §5.3-3: a real conflict between the local result and the remote ──────────



# ── §5.3-6: A merged, B merged on the same target, then A resumes ─────────────



# ── §5.3-7: a sync cut off by a restart ──────────────────────────────────────



# ── §5.3-10: the sync moves the target under another accepted decision ───────



# ── commit mode: no sync, the non-fast-forward parks ─────────────────────────



# ── the earlier implementation rework (review rej_01M6MARNVBXBMCNB) ─────────────────────

def _on_push(monkeypatch, moves):
    """Before the ``n``-th product push (1-based, counted from now) run ``moves[n]()`` (the remote moves ahead).
    Returns the ``_run_git`` it wrapped (to put back)."""
    from modules.flow_gate.services import git_service as svc
    real, count = svc._run_git, []

    def racing(args, **kwargs):
        if args and args[0] == "push":
            count.append(1)
            move = moves.get(len(count))
            if move is not None:
                move()
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", racing)
    return real


def _stop_holds(env, run_id, g, did):
    """What the Stop's settle step (``stop._settle_gate_decisions``, run inside its guard) does with this gate: it
    stays held, the decision unchanged."""
    from flowgate.graph_run import stop
    from modules.flow_gate.db.connection import get_store
    before = _decision(env, did)["status"]
    with get_store().transaction():
        held, notes = stop._settle_gate_decisions(run_id)
    assert g in held and g not in notes and _decision(env, did)["status"] == before


# P1-1: a conflict hand-off cut off before its ``pending`` record is the active conflict after the resume.


# P1-2: a done sync round stays an effect after a later round of the same decision fails.


# P1-3: abandon keeps the newest sync commit that landed, also one its round never recorded done.


# P2: a retried push still records the other owner's results it sends (counted from A0).
