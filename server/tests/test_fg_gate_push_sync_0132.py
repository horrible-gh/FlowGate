"""0132 T#2 (NR §4.2 / §4.3; §5.3-1, 2, 3, 6, 7, 10): the push phase's remote sync on a real repository and a
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
    mt.invalidate_check_targets()
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

@pytest.mark.parametrize("target", ["base", "nonbase"])
def test_remote_ahead_is_synced_once_and_pushed_keeping_the_applied_sha(git_env, pushes, target):
    r = git_env
    env, g, run_id, branch, frozen, did, jid = _accepted(r, target, diverge_base=target == "base")
    h1 = _advance(r, branch, "theirs.txt")
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0
    assert (a0 != frozen) == (target == "base")
    sync = effects["remote_sync"]
    sev = _ev(sync)
    s = sev["sync_commit"]
    assert (sync["status"], sync["sha_before"], sync["sha_after"], sync["attempt_count"]) == ("succeeded", a0, s, 1)
    assert (sev["pre_head"], sev["remote_head"], sev["local_applied"], sev["reviewed"]) == (a0, h1, a0, False)
    assert [x["sync_commit"] for x in sev["rounds"]] == [s]
    assert _parents(r["base"], s) == [a0, h1]                                 # --no-ff: L first, h1 second
    assert _rev(r["base"], "refs/heads/" + branch) == s == _rev(r["bare"], "refs/heads/" + branch)
    assert _ancestor(r["bare"], h1, s) and _ancestor(r["bare"], a0, s)        # the remote holds both sides
    push = effects["push"]
    pev = _ev(push)
    assert (push["status"], push["attempt_count"], pev["sent_sha"]) == ("succeeded", 2, s)
    assert pev["applied_sha"] == a0 and pev["rejected_rounds"] == 1 and pev["sync_commit"] == s
    first = pev["rejections"][0]
    assert (first["code"], first["sent_sha"], first["exit_code"]) == ("push_remote_ahead", a0, 1)
    assert "rejected" in first["diagnostic_tail"]
    # The applied SHA contract: A0 everywhere the decision is read back.
    checkpoint = _checkpoint(env, did)
    assert (checkpoint["source_sha"], checkpoint["applied_sha"]) == (frozen, a0)
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}-applied") == (a0 if a0 != frozen else None)
    assert effects["merge"]["attempt_count"] == 1                             # the approved merge was not redone
    assert _merges(r["base"], branch) == (2 if target == "base" else 1)
    assert len(pushes) == 2
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


# ── §5.3-2: the remote keeps moving ──────────────────────────────────────────

@pytest.mark.parametrize("advances,then", [(2, None), (None, "retry"), (None, "abandon")],
                         ids=["within_limit", "over_limit_retry", "over_limit_abandon"])
def test_a_racing_remote_is_synced_each_round_up_to_the_limit(git_env, pushes, monkeypatch, advances, then):
    from flowgate.gate_decisions import publish
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    assert publish.sync_round_limit() == 3
    real, tips = svc._run_git, []

    def racing(args, **kwargs):
        if args and args[0] == "push" and (advances is None or len(tips) < advances):
            tips.append(_advance(r, branch, f"race{len(tips)}.txt"))   # just before each push
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", racing)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sev = _ev(effects["remote_sync"])
    syncs = [x["sync_commit"] for x in sev["rounds"]]
    for older, newer, tip in zip([a0, *syncs], syncs, tips):                 # one sync per round, chained
        assert _parents(r["base"], newer) == [older, tip]
    remote_now = _rev(r["bare"], "refs/heads/main")
    assert all(_ancestor(r["bare"], tip, remote_now) for tip in tips)       # nothing on the remote was overwritten
    if then is None:
        assert job["status"] == "succeeded", job
        assert len(syncs) == 2 and len(pushes) == 3 and remote_now == syncs[-1]
        assert _decision(env, did)["target_sha"] == a0 and _merges(r["base"]) == 2
        return
    assert (job["status"], job["phase_note"], job["last_error_code"], job["retryable"]) == (
        "recovery_required", "gate_recovery", "push_remote_race", 1)
    assert _decision(env, did)["status"] == "recovery_required"
    assert len(syncs) == 3 and len(pushes) == 4 and len(tips) == 4
    assert _rev(r["base"], "refs/heads/main") == syncs[-1] and remote_now == tips[-1]
    push = effects["push"]
    assert (push["status"], push["error_code"]) == ("failed", "push_remote_race")
    assert _ev(push)["sync_round_limit"] == 3 and _ev(push)["rejected_rounds"] == 4
    assert _merges(r["base"]) == 3
    monkeypatch.setattr(svc, "_run_git", real)                               # the remote stops moving
    if then == "retry":
        _act(env, run_id, g, did, "retry", {"request_id": "race-retry", "expected_decision_version":
                                            _version(env, did)}, 202)
        job, _ = _run_job(jid)
        assert job["status"] == "succeeded", job
        sev = _ev(_effects(env, did)["remote_sync"])
        assert len(sev["rounds"]) == 4 and sev["rounds"][-1]["pre_head"] == syncs[-1]
        assert _rev(r["bare"], "refs/heads/main") == sev["sync_commit"]
        assert _effects(env, did)["merge"]["attempt_count"] == 1 and _decision(env, did)["target_sha"] == a0
        return
    _act(env, run_id, g, did, "resolve", {"request_id": "race-abandon", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the remote keeps moving", **_evidence(env, g)}, 200)
    kept = _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-sync")
    assert kept == syncs[-1]
    row = _effects(env, did)["remote_sync"]
    assert row["recovery_state"] == "confirmed" and row["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}-sync"
    assert _ev(row)["retention"] == {"sha": syncs[-1], "ref": f"refs/flowgate/gate-recovery/{did}-sync",
                                     "kept": True}


# ── §5.3-3: a real conflict between the local result and the remote ──────────

@pytest.mark.parametrize("review", ["completed", "aborted"])
def test_a_sync_conflict_goes_to_the_review_and_goes_on_or_parks_after_it(git_env, pushes, review):
    from flowgate.gate_decisions import publish, recovery
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", name="shared.txt", text="group side\n")
    h1 = _advance(r, "main", "shared.txt", "remote side\n")
    job, _ = _run_job(jid)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    assert job["recovery_since"] is not None                                 # goes on by itself after the review
    decision = repo.get(did)
    assert decision["status"] == "recovery_required" and repo.effects_state(did) == repo.EFFECTS_PRESENT
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    assert (sync["status"], sev["conflict"], sev["pre_head"], sev["remote_head"]) == ("pending", True, a0, h1)
    mid = int(sev["merge_id"])
    assert mid != _ev(effects["merge"]).get("merge_id")
    session = svc.db_git.get_session(mid)
    context = svc.db_git.session_context(session)
    assert session["status"] == "open" and context["gate_sync_decision_id"] == did
    assert "gate_decision_id" not in context                                 # never read as the decision's merge
    # The one active-conflict lookup: the sync's review, for the Sweeper, retry / cancel and the Stop alike.
    assert publish._conflict_merge_id(job) == mid and recovery._conflict_open(decision) is True
    refused = _act(env, run_id, g, did, "retry", {"request_id": "sync-retry", "expected_decision_version":
                                                  _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_retryable" and "merge_conflict_open" in json.dumps(refused)
    _due(jid)
    job_sweeper.step_recovery_required_retry()                               # UNDECIDABLE while the review is open
    assert job_store.get_job(jid)["status"] == "recovery_required"
    assert _rev(r["bare"], "refs/heads/main") == h1 and _rev(base, "refs/heads/main") == a0
    if review == "completed":
        (base / "shared.txt").write_text("resolved\n", encoding="utf-8")
        _git(["add", "shared.txt"], cwd=base)
        _git(["commit", "--no-edit"], cwd=base)
        resolved = _rev(base, "HEAD")
        svc._finish_reviewed_attempt(FA, mid, PROJECT, resolved[:7], False)
    else:
        conflict.abort_merge(FA, mid)                                         # the review's [abort]
        assert _rev(base, "HEAD") == a0 and _git(["status", "--porcelain", "--untracked-files=no"], cwd=base) == ""
    _due(jid)
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(jid)["status"] == "pending"
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    if review == "completed":
        assert job["status"] == "succeeded", job
        assert (sync["status"], sev["sync_commit"], sev["reviewed"]) == ("succeeded", resolved, True)
        assert _rev(r["bare"], "refs/heads/main") == resolved == _ev(effects["push"])["sent_sha"]
        d = _decision(env, did)
        assert d["status"] == "succeeded" and d["target_sha"] == a0
        assert _checkpoint(env, did)["applied_sha"] == a0
        assert len(pushes) == 2 and effects["merge"]["attempt_count"] == 1
        return
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_recovery",
                                                                          "push_sync_conflict_aborted")
    assert (sync["status"], sync["error_code"], sev["changed"]) == ("failed", "push_sync_conflict_aborted", False)
    assert _rev(base, "refs/heads/main") == a0 and _rev(r["bare"], "refs/heads/main") == h1   # target unchanged
    assert _decision(env, did)["status"] == "recovery_required" and len(pushes) == 1
    assert publish._conflict_merge_id(job_store.get_job(jid)) == _ev(effects["merge"]).get("merge_id")


# ── §5.3-6: A merged, B merged on the same target, then A resumes ─────────────

@pytest.mark.parametrize("case", ["foreign", "dirty", "detached", "diverged"])
def test_a_resumed_push_judges_the_local_target_and_its_checkout(git_env, pushes, monkeypatch, case):
    from flowgate.gate_decisions import git_adapter
    r = git_env
    base = r["base"]
    _push_rejected_once(monkeypatch)
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", diverge_base=True)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_rejected")
    a0 = _effects(env, did)["merge"]["sha_after"]
    assert a0 != frozen and _rev(base, "refs/heads/main") == a0
    # B: another Flow's gate merges its own result into the same local target while A is parked.
    side = _side_commit(r, a0, "b_result.txt")
    _git(["merge", "--no-ff", "-m", "gate B merge", side], cwd=base)
    m_b = _rev(base, "HEAD")
    h1 = _advance(r, "main", "theirs.txt")
    expected = {"dirty": "target_checkout_mismatch", "detached": "target_checkout_mismatch",
                "diverged": "target_diverged"}.get(case)
    if case == "dirty":
        (base / "README.md").write_text("tracked change\n", encoding="utf-8")
    elif case == "detached":
        _git(["checkout", "--detach", "HEAD"], cwd=base)
    elif case == "diverged":
        root = _git(["rev-list", "--max-parents=0", "HEAD"], cwd=base).splitlines()[0]
        elsewhere = _side_commit(r, root, "elsewhere.txt")
        _git(["checkout", "--detach", "HEAD"], cwd=base)
        _git(["update-ref", "refs/heads/main", elsewhere], cwd=base)
        _git(["checkout", "main"], cwd=base)
    local_before = _rev(base, "refs/heads/main")
    _act(env, run_id, g, did, "retry", {"request_id": f"resume-{case}", "expected_decision_version":
                                        _version(env, did)}, 202)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    if expected:
        assert (job["status"], job["last_error_code"]) == ("recovery_required", expected)
        assert "remote_sync" not in effects and len(pushes) == 0
        assert _rev(base, "refs/heads/main") == local_before and _rev(r["bare"], "refs/heads/main") == h1
        assert effects["merge"]["attempt_count"] == 1
        return
    assert job["status"] == "succeeded", job
    pev, sev = _ev(effects["push"]), _ev(effects["remote_sync"])
    assert set(pev["foreign_included"]) == {m_b, side} and pev["applied_sha"] == a0
    assert (sev["pre_head"], sev["remote_head"], sev["local_applied"]) == (m_b, h1, a0)   # L measured, not A0
    s = sev["sync_commit"]
    assert _parents(base, s) == [m_b, h1] and _rev(r["bare"], "refs/heads/main") == s == pev["sent_sha"]
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0
    assert _rev(base, f"refs/flowgate/gate-archive/{did}-applied") == a0 and _checkpoint(env, did)["applied_sha"] == a0
    # B's own push afterwards reads the remote first: it holds B's result, so B reconciles (no second push).
    assert git_adapter.remote_contains(git_adapter.slot_of(FA), "main", m_b) is True
    assert effects["merge"]["attempt_count"] == 1 and _merges(base) == 3   # A0, B, one sync: no duplicate merge
    assert len(pushes) == 2


# ── §5.3-7: a sync cut off by a restart ──────────────────────────────────────

@pytest.mark.parametrize("landed", [True, False], ids=["after_sync_commit", "before_sync_commit"])
def test_a_sync_cut_off_is_judged_from_its_attempt_without_a_second_merge(git_env, pushes, monkeypatch, landed):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    h1 = _advance(r, "main", "theirs.txt")
    real_finish, real_git, real_fail = merge_target.finish_manual_attempt, svc._run_git, merge_target.fail_attempt
    calls = []
    if landed:
        def crash(ctx, **kwargs):
            calls.append(ctx.merge_id)
            if len(calls) == 2:                                              # 1: the approved merge, 2: the sync
                raise RuntimeError("process died after the sync commit")
            return real_finish(ctx, **kwargs)
        monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    else:
        def killed(args, **kwargs):
            if "merge" in args and "--no-ff" in args:
                raise RuntimeError("process died before the sync commit")
            return real_git(args, **kwargs)
        monkeypatch.setattr(svc, "_run_git", killed)
        monkeypatch.setattr(merge_target, "fail_attempt", lambda *a, **k: None)   # nothing ran after the kill
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync = effects["remote_sync"]
    mid = _ev(sync)["merge_id"]
    assert sync["status"] == "unknown" and svc.db_git.get_session(mid)["status"] == "open"
    if landed:
        cut = _rev(base, "refs/heads/main")
        assert _parents(base, cut) == [a0, h1]
    else:
        assert _rev(base, "refs/heads/main") == a0
    monkeypatch.setattr(merge_target, "finish_manual_attempt", real_finish)
    monkeypatch.setattr(merge_target, "fail_attempt", real_fail)
    monkeypatch.setattr(svc, "_run_git", real_git)
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    s = sev["sync_commit"]
    assert sync["status"] == "succeeded" and len(sev["rounds"]) == 1
    assert svc.db_git.get_session(mid)["status"] != "open"
    if landed:
        assert s == cut and sync["attempt_count"] == 1                     # the landed commit is the result
        assert len(pushes) == 2
    else:
        assert sync["attempt_count"] == 2 and _parents(base, s) == [a0, h1]   # a new round, one sync commit
        assert len(pushes) == 3
    assert _merges(base) == 1 and effects["merge"]["attempt_count"] == 1
    assert _rev(r["bare"], "refs/heads/main") == s and _ancestor(r["bare"], h1, s)
    assert _decision(env, did)["target_sha"] == a0


# ── §5.3-10: the sync moves the target under another accepted decision ───────

def test_another_decision_accepted_on_the_old_target_fails_target_moved_after_a_sync(git_env, pushes):
    r = git_env
    env = r["env"]
    keys = _confirmed(env, [node("tmp_r", "R"), node("tmp_g1", None, "gate"), node("tmp_g2", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_g1"), edge("tmp_e2", "tmp_r", "tmp_g2")])
    g1, g2 = keys["tmp_g1"], keys["tmp_g2"]
    run_id = _start(env)["run_id"]
    assert (_states(env)[g1], _states(env)[g2]) == ("open", "open")
    branch = _target(r, "nonbase")
    before = _rev(r["base"], branch)
    _work(r, "ours.txt")
    for g in (g1, g2):
        _gate_merge(env, g, branch)
    first, second = _confirm(env, run_id, g1, "first-0132"), _confirm(env, run_id, g2, "second-0132")
    a_did, a_job = first["decision"]["decision_id"], first["job"]["job_id"]
    c_did, c_job = second["decision"]["decision_id"], second["job"]["job_id"]
    for did in (a_did, c_did):
        assert json.loads(_decision(env, did)["settings_json"])["target_basis"]["accepted_target_sha"] == before
    h1 = _advance(r, branch, "theirs.txt")
    job, _ = _run_job(a_job)
    assert job["status"] == "succeeded", job
    s = _ev(_effects(env, a_did)["remote_sync"])["sync_commit"]
    assert _rev(r["base"], branch) == s == _rev(r["bare"], "refs/heads/" + branch) and _ancestor(r["bare"], h1, s)
    job, _ = _run_job(c_job)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_failed",
                                                                          "target_moved")
    assert _decision(env, c_did)["status"] == "failed"                       # no effect outside
    merge = _effects(env, c_did)["merge"]
    assert (merge["status"], merge["error_code"]) == ("failed", "target_moved")
    assert (_ev(merge)["accepted_target_sha"], _ev(merge)["observed_target_sha"]) == (before, s)
    assert "remote_sync" not in _effects(env, c_did) and "push" not in _effects(env, c_did)
    assert _rev(r["base"], branch) == s == _rev(r["bare"], "refs/heads/" + branch) and len(pushes) == 2


# ── commit mode: no sync, the non-fast-forward parks ─────────────────────────

def test_commit_mode_parks_a_remote_ahead_push_without_a_sync(git_env, pushes):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    frozen = _work(r, "local.txt")
    _gate(env, g, merge_mode="commit", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "commit-ahead-0132")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    branch = r["branch"]
    remote = _advance(r, branch, "remote.txt")
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_remote_ahead")
    assert _decision(env, did)["status"] == "failed"
    effects = _effects(env, did)
    assert "remote_sync" not in effects and effects["push"]["error_code"] == "push_remote_ahead"
    assert _rev(r["base"], "refs/heads/" + branch) == frozen == _rev(r["wt"], "HEAD")
    assert _rev(r["bare"], "refs/heads/" + branch) == remote and len(pushes) == 1


# ── 0132 T#2 rework (TR0008 review rej_01M6MARNVBXBMCNB) ─────────────────────

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
def test_a_sync_conflict_hand_off_cut_off_before_its_record_is_the_active_conflict_after_the_resume(
        git_env, pushes, monkeypatch):
    from flowgate.gate_decisions import git_adapter, publish, recovery
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper, merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False, name="shared.txt",
                                                         text="group side\n", contained=True)
    h1 = _advance(r, "main", "shared.txt", "remote side\n")
    real_hand_off, real_fail = git_adapter._hand_off_conflict, merge_target.fail_attempt

    def cut(*args, **kwargs):
        real_hand_off(*args, **kwargs)
        raise RuntimeError("process died right after the conflict hand-off")

    monkeypatch.setattr(git_adapter, "_hand_off_conflict", cut)
    monkeypatch.setattr(merge_target, "fail_attempt", lambda *a, **k: None)   # nothing ran after the kill
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    mid = int(sev["merge_id"])
    assert sync["status"] == "unknown" and svc.db_git.get_session(mid)["status"] == "open"
    assert (effects["merge"]["status"], _ev(effects["merge"])["changed"]) == ("succeeded", False)   # contained
    merge_mid = _ev(effects["merge"]).get("merge_id")
    assert mid != merge_mid
    # Already before the resume records it, the one lookup reads the sync's attempt (not the approved merge's).
    assert publish._conflict_merge_id(job_store.get_job(jid)) == mid
    monkeypatch.setattr(git_adapter, "_hand_off_conflict", real_hand_off)
    monkeypatch.setattr(merge_target, "fail_attempt", real_fail)
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    sync, sev = _effects(env, did)["remote_sync"], _ev(_effects(env, did)["remote_sync"])
    assert (sync["status"], sev["conflict"], int(sev["merge_id"]), sev["hand_off_recovered"]) == (
        "pending", True, mid, True)
    assert (sev["pre_head"], sev["remote_head"]) == (frozen, h1)
    decision = repo.get(did)
    assert decision["status"] == "failed" and repo.effects_state(did) == repo.EFFECTS_NONE   # nothing changed outside
    assert publish._conflict_merge_id(job) == mid and recovery._conflict_open(decision) is True
    refused = _act(env, run_id, g, did, "retry", {"request_id": "cut-retry", "expected_decision_version":
                                                  _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_retryable" and "merge_conflict_open" in json.dumps(refused)
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "cut-cancel", "expected_decision_version":
                                                   _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_cancelable" and "merge_conflict_open" in json.dumps(refused)
    _due(jid)
    job_sweeper.step_recovery_required_retry()                               # UNDECIDABLE while the review is open
    assert job_store.get_job(jid)["status"] == "recovery_required"
    _stop_holds(env, run_id, g, did)                                         # the Stop waits for the sync's review
    assert _rev(base, "refs/heads/main") == frozen and _rev(r["bare"], "refs/heads/main") == h1
    conflict.abort_merge(FA, mid)                                             # the review's [abort]
    _due(jid)
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(jid)["status"] == "pending"
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_sync_conflict_aborted")
    assert _effects(env, did)["remote_sync"]["status"] == "failed"
    assert publish._conflict_merge_id(job) == merge_mid
    assert _merges(base) == 0 and len(pushes) == 1 and _effects(env, did)["merge"]["attempt_count"] == 1


# P1-2: a done sync round stays an effect after a later round of the same decision fails.
@pytest.mark.parametrize("later", ["aborted", "failed"])
def test_a_done_sync_round_stays_an_effect_after_a_later_round_fails(git_env, pushes, monkeypatch, later):
    from flowgate.gate_decisions import publish
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False, contained=True)
    h1 = _advance(r, "main", "theirs.txt")
    tips = []

    def move():
        # Before the second push the remote moves again: on our own file (a conflict) or not (the merge then fails).
        if later == "aborted":
            tips.append(_advance(r, "main", "ours.txt", "remote ours\n"))
        else:
            tips.append(_advance(r, "main", "race.txt"))

    real = _on_push(monkeypatch, {2: move})
    if later == "failed":
        from_push, merges = svc._run_git, []

        def refused_merge(args, **kwargs):
            if args and "merge" in args and "--no-ff" in args:
                merges.append(1)
                if len(merges) == 2:                                         # the second sync's merge cannot run
                    return subprocess.CompletedProcess(args, 1, "", "fatal: simulated merge failure")
            return from_push(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", refused_merge)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    assert (effects["merge"]["status"], _ev(effects["merge"])["changed"]) == ("succeeded", False)   # contained
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    s1 = sev["rounds"][0]["sync_commit"]
    assert len(sev["rounds"]) == 1 and _parents(base, s1) == [frozen, h1]
    assert _rev(base, "refs/heads/main") == s1                              # the local target moved to S1
    assert effects["push"]["status"] == "failed"
    if later == "aborted":
        assert (job["status"], job["phase_note"]) == ("recovery_required", "gate_conflict")
        assert (sync["status"], sev["conflict"], sev["pre_head"], sev["remote_head"]) == ("pending", True, s1, tips[0])
        assert repo.effects_state(did) == repo.EFFECTS_PRESENT
        assert _decision(env, did)["status"] == "recovery_required"
        conflict.abort_merge(FA, int(sev["merge_id"]))
        _due(jid)
        job_sweeper.step_recovery_required_retry()
        assert job_store.get_job(jid)["status"] == "pending"
        monkeypatch.setattr(svc, "_run_git", real)
        job, _ = _run_job(jid)
        code = "push_sync_conflict_aborted"
    else:
        code = "sync_merge_failed"
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_recovery", code)
    sync, sev = _effects(env, did)["remote_sync"], _ev(_effects(env, did)["remote_sync"])
    assert (sync["status"], sev["changed"], [x["sync_commit"] for x in sev["rounds"]]) == ("failed", False, [s1])
    decision = repo.get(did)
    assert repo.effects_state(did) == repo.EFFECTS_PRESENT == publish.effects_now(decision)
    assert decision["status"] == "recovery_required"                        # never "failed" (no effect)
    refused = _act(env, run_id, g, did, "cancel", {"request_id": f"later-{later}", "expected_decision_version":
                                                   _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_effects_present"
    _stop_holds(env, run_id, g, did)                                         # nor does a Stop cancel it
    assert _rev(base, "refs/heads/main") == s1 and _rev(r["bare"], "refs/heads/main") == tips[0]
    monkeypatch.setattr(svc, "_run_git", real)
    _act(env, run_id, g, did, "resolve", {"request_id": f"later-abandon-{later}", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the later sync failed", **_evidence(env, g)}, 200)
    assert _rev(base, f"refs/flowgate/gate-recovery/{did}-sync") == s1        # the last done round is kept
    assert _merges(base) == 1 and _effects(env, did)["merge"]["attempt_count"] == 1


# P1-3: abandon keeps the newest sync commit that landed, also one its round never recorded done.
@pytest.mark.parametrize("rounds", [1, 2], ids=["first_round", "after_a_done_round"])
def test_an_abandon_keeps_the_landed_sync_commit_its_round_never_recorded(git_env, pushes, monkeypatch, rounds):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    tips = [_advance(r, "main", "theirs.txt")]
    real = _on_push(monkeypatch, {2: lambda: tips.append(_advance(r, "main", "race.txt"))} if rounds == 2 else {})
    real_finish, calls = merge_target.finish_manual_attempt, []

    def crash(ctx, **kwargs):
        calls.append(ctx.merge_id)
        if len(calls) == rounds + 1:                                         # 1: the approved merge, then the syncs
            raise RuntimeError("process died after the sync commit")
        return real_finish(ctx, **kwargs)

    monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    done = [x["sync_commit"] for x in sev["rounds"]]
    landed = _rev(base, "refs/heads/main")
    assert sync["status"] == "unknown" and len(done) == rounds - 1 and landed not in done
    assert _parents(base, landed) == [done[-1] if done else a0, tips[-1]]
    monkeypatch.setattr(merge_target, "finish_manual_attempt", real_finish)
    monkeypatch.setattr(svc, "_run_git", real)
    (base / "README.md").write_text("tracked change\n", encoding="utf-8")    # the resume cannot settle the round
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "sync_unsettled")
    sync = _effects(env, did)["remote_sync"]
    assert sync["status"] == "unknown" and [x["sync_commit"] for x in _ev(sync)["rounds"]] == done
    assert _decision(env, did)["status"] == "recovery_required"
    _act(env, run_id, g, did, "resolve", {"request_id": f"landed-abandon-{rounds}", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the sync could not be settled", **_evidence(env, g)}, 200)
    ref = f"refs/flowgate/gate-recovery/{did}-sync"
    assert _rev(base, ref) == landed                                         # the newest landed sync commit
    row = _effects(env, did)["remote_sync"]
    assert _ev(row)["retention"] == {"sha": landed, "ref": ref, "kept": True}
    assert (row["recovery_state"], row["recovery_ref"]) == ("unknown", ref)
    assert _rev(base, "refs/heads/main") == landed and len(pushes) == rounds  # nothing reset, nothing more pushed
    assert _effects(env, did)["merge"]["attempt_count"] == 1


# P2: a retried push still records the other owner's results it sends (counted from A0).
def test_a_retried_push_after_a_sync_round_keeps_the_foreign_results_it_sends(git_env, pushes, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    base = r["base"]
    _push_rejected_once(monkeypatch)
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", diverge_base=True)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_rejected")
    a0 = _effects(env, did)["merge"]["sha_after"]
    side = _side_commit(r, a0, "b_result.txt")                               # B merges into the same local target
    _git(["merge", "--no-ff", "-m", "gate B merge", side], cwd=base)
    m_b = _rev(base, "HEAD")
    h1 = _advance(r, "main", "theirs.txt")
    monkeypatch.setenv("FLOWGATE_GATE_SYNC_ROUNDS", "1")
    tips = []
    real = _on_push(monkeypatch, {2: lambda: tips.append(_advance(r, "main", "race.txt"))})
    _act(env, run_id, g, did, "retry", {"request_id": "foreign-1", "expected_decision_version": _version(env, did)},
         202)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_remote_race")
    effects = _effects(env, did)
    s1 = _ev(effects["remote_sync"])["sync_commit"]
    assert _parents(base, s1) == [m_b, h1] and _rev(base, "refs/heads/main") == s1
    assert set(_ev(effects["push"])["foreign_included"]) == {m_b, side}
    monkeypatch.setattr(svc, "_run_git", real)                               # the remote stops moving
    _act(env, run_id, g, did, "retry", {"request_id": "foreign-2", "expected_decision_version": _version(env, did)},
         202)
    job, _ = _run_job(jid)                                                   # A = S1 = L now: still counted from A0
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    pev, sev = _ev(effects["push"]), _ev(effects["remote_sync"])
    s2 = sev["sync_commit"]
    assert [x["sync_commit"] for x in sev["rounds"]] == [s1, s2] and _parents(base, s2) == [s1, tips[0]]
    assert set(pev["foreign_included"]) == {m_b, side}                       # B's results, nothing of the remote
    assert pev["sent_sha"] == s2 == _rev(r["bare"], "refs/heads/main") and pev["applied_sha"] == a0
    assert [x["sent_sha"] for x in pev["rejections"]] == [m_b, s1, s1]      # every refusal of every claim
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0 and _checkpoint(env, did)["applied_sha"] == a0
    assert effects["merge"]["attempt_count"] == 1 and _merges(base) == 4    # A0, B, S1, S2: no duplicate merge
    assert len(pushes) == 4                                                 # 2 + 2 real pushes, never forced
