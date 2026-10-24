"""flowgate.v02.0085 T#3 (D0007 §7.1 / §7.2 / §7.3; §8-4, 6, 7, 12, 13, 14 (retry / cancel / resolve), 15, 17, 18
(closing, contention, lease)) — the confirm gate's ``gate_publish`` job on a real Git repository: snapshot, merge /
commit, push, archive, the one-transaction completion, failures and their recovery (retry / cancel / resolve), the
credential scrub and the end cleanup of the group slot.

Harness: the real SQLite DB, routes and product rules of ``test_fg_gate_intake_0085`` (``product``), plus a real
repository: a bare ``origin`` with ``main``, the project's Git config and the Flow's group slot provisioned by the
existing ``ensure_worktree`` (base checkout + group worktree). The group's integrated result is a commit made in the
group worktree. The job is run through the existing Runner (``job_runner.try_claim`` / ``execute``), a lost lease /
restart through the existing Sweeper reconcile (``job_sweeper.step_expired_leases``). Failures are injected at the
Git command (``git_service._run_git``) or at one step; representative combinations only.

T#3 rework (review rej_01M5NT4F9CPN06M6): the end cleanup is its own ``gate_cleanup`` job (run here through the same
Runner), resolve takes the reviewed evidence (version and references), and the last block holds one focused regression
test per defect of the review: the group's acceptance order and the snapshot reservation against the attempt
integration, the lease fencing of every worker write during merge / push / archive and after a close, an archive cut
off after its ref (cancel / Stop), an orphan pin, a cleanup cut off at each step and its locks / conditions, the gate
checkpoints in the existing archive list / restore, two copies of one recovery request, the reviewed evidence, an
effects_confirmed without a pinned snapshot, and an unwritable recovery ref kept as an obligation.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node, put_draft, setting  # noqa: E402,F401
from test_fg_gate_intake_0085 import (  # noqa: E402,F401
    _attempt, _confirmed, _current, _decide, _decisions, _finish, _gate_events, _jobs, _linear, _nodes, _start,
    _states, allowed, product)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")

PROJECT = "flowgate"


# ── harness ──────────────────────────────────────────────────────────────────

def _git(args, cwd=None):
    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "T",
                "GIT_COMMITTER_EMAIL": "t@t", "GIT_TERMINAL_PROMPT": "0"})
    proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


def _rev(repo, ref):
    proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=str(repo), capture_output=True,
                          text=True)
    return proc.stdout.strip() or None


@pytest.fixture()
def git_env(product, monkeypatch, tmp_path):
    """A real bare origin, the project's Git config and the Flow's provisioned group slot."""
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import instance_registry as reg
    from modules.flow_gate.services.git import lock_manager as lm

    monkeypatch.setenv("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-gate-publish")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    for name in ("FLOWGATE_INTERACTIVE_WAIT_SEC", "FLOWGATE_FREEZE_INTERACTIVE_WAIT_SEC",
                 "FLOWGATE_R_INTERACTIVE_WAIT_SEC"):
        monkeypatch.setenv(name, "0.2")
    reg.register()
    bare, seed = tmp_path / "origin.git", tmp_path / "seed"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seed)])
    (seed / "README.md").write_text("hello\n", encoding="utf-8")
    (seed / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seed)
    _git(["commit", "-m", "init"], cwd=seed)
    _git(["remote", "add", "origin", str(bare)], cwd=seed)
    _git(["push", "origin", "main"], cwd=seed)
    svc.save_config(PROJECT, {"repo_url": bare.as_uri(), "provider": "generic", "base_branch": "main",
                              "default_finalize_action": "merge", "enabled": True})
    assert svc.ensure_worktree(PROJECT, "v02", FA) == "ok"
    state = svc.db_git.get_state(FA)
    branch = state["branch"]
    out = {"env": product, "bare": bare, "seed": seed, "branch": branch,
           "wt": Path(svc.src_root(PROJECT, branch)), "base": Path(svc.src_root(PROJECT, "main"))}
    assert out["wt"].is_dir() and (out["base"] / ".git").exists()
    return out


def _work(r, name, text="x\n", message=None):
    """The group's integrated result: a commit on the group branch (made in its worktree)."""
    path = r["wt"] / name
    path.write_text(text, encoding="utf-8")
    _git(["add", name], cwd=r["wt"])
    _git(["commit", "-m", message or f"work {name}"], cwd=r["wt"])
    return _rev(r["wt"], "HEAD")


def _gate(env, key, **values):
    for field, value in values.items():
        setting(env, FA, key, field, value)


def _run_job(job_id):
    """One Runner claim + execute of the job (its available time moved into the past first)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_store
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    claim = job_runner.try_claim(job_id)
    assert claim is not None, job_store.get_job(job_id)
    job_runner.execute(claim)
    return job_store.get_job(job_id), claim


def _expire_and_reconcile(job_id):
    """A lost owner: its lease ran out and the Sweeper's reconcile takes the job (``step_expired_leases``)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store, job_sweeper
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_until = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    job_sweeper.step_expired_leases(full=True)
    return job_store.get_job(job_id)


def _decision(env, decision_id):
    return _sql(env["db"], "SELECT * FROM gate_decisions WHERE decision_id = ?", (decision_id,))[0]


def _effects(env, decision_id):
    rows = _sql(env["db"], "SELECT * FROM gate_decision_effects WHERE decision_id = ?", (decision_id,))
    return {r["phase"]: r for r in rows}


def _job_row(env, job_id):
    return _sql(env["db"], "SELECT * FROM operation_job WHERE job_id = ?", (job_id,))[0]


def _merges(repo, ref="main"):
    return int(_git(["rev-list", "--merges", "--count", ref], cwd=repo))


def _confirm(env, run_id, key, request_id, expect=202, **extra):
    return _decide(env, run_id, key, {"decision": "confirm", "request_id": request_id, **extra}, expect)


def _act(env, run_id, key, decision_id, action, body, expect):
    res = env["client"].post(f"/api/v1/runs/{run_id}/nodes/{key}/gate-decisions/{decision_id}/{action}",
                             json=body, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _version(env, decision_id):
    return _decision(env, decision_id)["decision_version"]


def _requests(env):
    return _sql(env["db"], "SELECT * FROM gate_decision_requests ORDER BY created_at")


def _start_linear(r):
    env = r["env"]
    g, t = _linear(env)
    run = _start(env)
    return env, g, t, run["run_id"]


def _evidence(env, key):
    """The evidence the person reviewed, as the run read shows it (D0007 §7.2 resolve: version and references)."""
    rec = _nodes(_current(env))[key]["gate"]["recovery"]
    return {"evidence_version": rec["evidence_version"], "evidence_refs": rec["evidence_refs"]}


def _cleanup_jobs(env):
    return _sql(env["db"], "SELECT * FROM operation_job WHERE kind = 'gate_cleanup' ORDER BY created_at, job_id")


def _run_cleanup(env):
    """The end cleanup job the run's completion made, run once through the Runner."""
    jobs = _cleanup_jobs(env)
    assert jobs, "the completion made no gate_cleanup job"
    return _run_job(jobs[-1]["job_id"])[0]


# ── §8-4: the apply modes, archive and push on a real repository ─────────────

def test_a_merge_with_push_and_archive_publishes_the_frozen_result_once_and_keeps_the_slot(git_env):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id = _start_linear(r)
    main_before = _rev(r["base"], "refs/heads/main")
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True, archive=True)
    body = _confirm(env, run_id, g, "m1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    assert (_states(env)[g], _states(env)[t]) == ("open", "pending")         # accepted is not confirmed
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded" and job["phase"] == "done"
    d = _decision(env, did)
    assert (d["status"], d["frozen_sha"]) == ("succeeded", sha)
    merged = _rev(r["base"], "refs/heads/main")
    assert _rev(r["base"], f"{merged}^1") == main_before and _rev(r["base"], f"{merged}^2") == sha
    assert _rev(r["bare"], "refs/heads/main") == merged                     # pushed: the merge result
    assert d["target_sha"] == merged and _merges(r["base"]) == 1
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}-applied") == merged
    checkpoints = _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))
    assert len(checkpoints) == 1 and (checkpoints[0]["source_sha"], checkpoints[0]["applied_sha"]) == (sha, merged)
    effects = _effects(env, did)
    assert {p: e["status"] for p, e in effects.items()} == {
        "snapshot": "succeeded", "merge": "succeeded", "push": "succeeded", "archive": "succeeded"}
    assert (_states(env)[g], _states(env)[t]) == ("done", "open")
    events = _gate_events(env, g)
    assert [e["cause"] for e in events] == ["gate_confirmed"]
    assert json.loads(events[0]["payload_json"])["gate_decision_id"] == did
    assert _attempt(env, run_id, g)["status"] == "ended"
    wire = _nodes(_current(env))[g]["gate"]
    assert wire["archived"] is True and wire["decision_in_progress"] is False
    state = svc.db_git.get_state(FA)                                        # an intermediate gate keeps the slot
    assert int(state["worktree_registered"]) == 1 and r["wt"].is_dir() and _rev(r["base"], f"refs/heads/{r['branch']}")


def test_commit_mode_pushes_the_integrated_head_and_never_commits_the_dirty_work_tree(git_env):
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    (r["wt"] / "feature.txt").write_text("changed, not approved\n", encoding="utf-8")   # tracked change
    (r["wt"] / "scratch.tmp").write_text("untracked\n", encoding="utf-8")            # new file
    main_before = _rev(r["base"], "refs/heads/main")
    _gate(env, g, merge_mode="commit", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "c1")
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    did = body["decision"]["decision_id"]
    assert _decision(env, did)["frozen_sha"] == sha
    assert _rev(r["wt"], "HEAD") == sha                                     # no commit was made
    assert _rev(r["bare"], f"refs/heads/{r['branch']}") == sha              # the group branch pushed at the frozen SHA
    assert _rev(r["base"], "refs/heads/main") == main_before == _rev(r["bare"], "refs/heads/main")
    status = _git(["status", "--porcelain", "--untracked-files=all"], cwd=r["wt"])
    assert "feature.txt" in status and "scratch.tmp" in status              # still uncommitted
    snapshot = json.loads(_effects(env, did)["snapshot"]["evidence_json"])
    assert snapshot["uncommitted_excluded"] == 2
    assert "merge" not in _effects(env, did) and "archive" not in _effects(env, did)


def test_hold_with_archive_only_preserves_and_ignores_the_stored_git_options(git_env):
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    main_before = _rev(r["base"], "refs/heads/main")
    _gate(env, g, merge_mode="hold", merge_target="origin", push=True, keep_branch=True, archive=True)
    body = _confirm(env, run_id, g, "h1")
    did = body["decision"]["decision_id"]
    eff = json.loads(_decision(env, did)["settings_json"])["effective"]
    assert (eff["git_apply"], eff["push"], eff["keep_branch"], eff["archive"]) == (False, False, False, True)
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}-applied") is None
    assert _rev(r["base"], "refs/heads/main") == main_before == _rev(r["bare"], "refs/heads/main")
    assert _rev(r["bare"], f"refs/heads/{r['branch']}") is None             # no push
    assert set(_effects(env, did)) == {"snapshot", "archive"}
    assert _nodes(_current(env))[g]["gate"]["archived"] is True


def test_a_local_merge_without_push_stays_local(git_env):
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    origin_before = _rev(r["bare"], "refs/heads/main")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "l1")
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    merged = _rev(r["base"], "refs/heads/main")
    assert _rev(r["base"], f"{merged}^2") == sha and _rev(r["bare"], "refs/heads/main") == origin_before
    assert _nodes(_current(env))[g]["gate"]["archived"] is False            # archive OFF: no chip


# ── §8-6: failures injected after a step; nothing is done twice ──────────────

def test_a_db_failure_after_the_git_work_retries_only_the_completion(git_env, monkeypatch):
    from flowgate.gate_decisions import intake
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "db1")
    real, calls = intake._apply, []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        return real(*args, **kwargs)

    monkeypatch.setattr(intake, "_apply", failing)
    job, _ = _run_job(body["job"]["job_id"])
    assert (job["status"], job["phase"]) == ("retry_wait", "db_finalize")
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"
    assert _decision(env, body["decision"]["decision_id"])["status"] == "accepted"
    merged = _rev(r["base"], "refs/heads/main")
    assert _rev(r["bare"], "refs/heads/main") == merged
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _rev(r["base"], "refs/heads/main") == merged and _merges(r["base"]) == 1       # not merged again
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
    assert _states(env)[t] == "open"


@pytest.mark.parametrize("landed", [True, False], ids=["timeout_landed", "timeout_unknown"])
def test_a_push_timeout_is_judged_by_the_remote(git_env, monkeypatch, landed):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "pt1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real = svc._run_git
    seen = {"ls": 0, "push": 0}

    def flaky(args, **kwargs):
        if args and args[0] == "push":
            seen["push"] += 1
            if seen["push"] == 1:
                if landed:
                    real(args, **kwargs)                                     # it really landed, then timed out
                return subprocess.CompletedProcess(["git", *args], -1, "", "timeout_expired")
        if args and args[0] == "ls-remote":
            seen["ls"] += 1
            # 1: the remote head before, 2: does it hold our SHA already, 3: the check after the timeout
            if not landed and seen["ls"] == 3:
                return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: unable to access")
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", flaky)
    job, _ = _run_job(job_id)
    if landed:
        assert job["status"] == "succeeded"
        push = _effects(env, did)["push"]
        assert push["status"] == "succeeded" and json.loads(push["evidence_json"])["result"] == "push_landed_after_error"
        assert seen["push"] == 1
        return
    assert job["status"] == "retry_wait" and job["last_error_code"] == "push_result_unknown"
    assert _effects(env, did)["push"]["status"] == "unknown"
    assert _decision(env, did)["status"] == "accepted" and _states(env)[g] == "open"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x1", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _rev(r["bare"], "refs/heads/main") == _rev(r["base"], "refs/heads/main")
    assert _merges(r["base"]) == 1 and [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


def test_a_crash_after_the_archive_ref_resumes_from_the_ref_after_the_reconcile(git_env, monkeypatch):
    from flowgate.gate_decisions import repository
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", archive=True)
    body = _confirm(env, run_id, g, "ar1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = repository.insert_checkpoint, []

    def crash(row):
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("process died")
        return real(row)

    monkeypatch.setattr(repository, "insert_checkpoint", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and job["phase_note"] == "result_unknown"     # the Runner's unknown result
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _states(env)[g] == "open" and _decision(env, did)["status"] == "accepted"
    job = _expire_and_reconcile(job_id)
    assert job["status"] == "pending" and job["phase"] == "archive"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))) == 1
    assert _merges(r["base"]) == 1 and _states(env)[g] == "done"


def test_a_crash_right_after_the_merge_commit_is_settled_by_the_existing_attempt_recovery(git_env, monkeypatch):
    from modules.flow_gate.services.git import merge_target
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "mc1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = merge_target.finish_manual_attempt, []

    def crash(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("process died after the merge commit")
        return real(*args, **kwargs)

    monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    job, _ = _run_job(job_id)
    landed = _rev(r["base"], "refs/heads/main")
    assert _rev(r["base"], f"{landed}^2") == sha and job["status"] == "running"
    assert _effects(env, did)["merge"]["status"] == "unknown" and _states(env)[g] == "open"
    _expire_and_reconcile(job_id)
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _rev(r["base"], "refs/heads/main") == landed and _merges(r["base"]) == 1   # the same merge commit
    merge = _effects(env, did)["merge"]
    assert merge["status"] == "succeeded" and merge["sha_after"] == landed
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


def test_a_stolen_lease_writes_no_completion_and_the_new_owner_completes_once(git_env, monkeypatch):
    from flowgate.gate_decisions import publish
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "ls1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = publish._complete, []

    def stolen(ctx, plan):
        calls.append(1)
        if len(calls) == 1:
            with get_store().transaction():
                get_store()._execute("UPDATE operation_job SET lease_token = 'lt_other_owner' WHERE job_id = ?",
                                     [job_id])
        return real(ctx, plan)

    monkeypatch.setattr(publish, "_complete", stolen)
    job, first = _run_job(job_id)
    assert job["status"] == "running" and job["lease_token"] == "lt_other_owner"
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"
    assert _decision(env, did)["status"] == "accepted"
    job = _expire_and_reconcile(job_id)
    assert job["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded" and _merges(r["base"]) == 1
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):                                 # the first claim's late write vanishes
        job_store.job_write(first.ctx, status="failed")
    assert _job_row(env, job_id)["status"] == "succeeded"


def test_a_conflict_goes_to_the_merge_review_with_the_decision_and_goes_on_once_it_is_resolved(git_env):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import job_store, job_sweeper
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "shared.txt", "group side\n")
    (r["base"] / "shared.txt").write_text("base side\n", encoding="utf-8")
    _git(["commit", "-am", "base change"], cwd=r["base"])
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "cf1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    job, _ = _run_job(job_id)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    d = _decision(env, did)
    assert d["status"] == "failed" and _states(env)[g] == "open" and _gate_events(env, g) == []
    session = svc.db_git.get_open_session_by_group(FA)
    context = svc.db_git.session_context(session)
    assert context["gate_decision_id"] == did and svc.db_git.get_state(FA)["status"] == "conflict"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "cx", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_decision_not_cancelable"
    job_sweeper.step_recovery_required_retry()                               # the review is still open: it waits
    assert job_store.get_job(job_id)["status"] == "recovery_required"
    # The review resolves the conflict (as the existing review completion does for a manual-merge attempt).
    (r["base"] / "shared.txt").write_text("resolved\n", encoding="utf-8")
    _git(["add", "shared.txt"], cwd=r["base"])
    _git(["commit", "--no-edit"], cwd=r["base"])
    merge_commit = _rev(r["base"], "HEAD")
    svc._finish_reviewed_attempt(FA, int(session["merge_id"]), PROJECT, merge_commit[:7], False)
    from modules.flow_gate.db.connection import get_store
    with get_store().transaction():                                          # the Sweeper's next round is due
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _decision(env, did)["status"] == "succeeded" and _effects(env, did)["merge"]["sha_after"] == merge_commit
    assert _states(env)[g] == "done" and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1


# ── Stop contention (D0007 §7.3 Stop paragraph) ──────────────────────────────

@pytest.mark.parametrize("after", ["snapshot", "merge"])
def test_a_stop_during_the_job_cancels_without_effects_and_parks_with_them(git_env, monkeypatch, after):
    from flowgate.gate_decisions import git_adapter, publish
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "st1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]

    def stop_now():
        # What Stop's first transaction does to a run whose gate job is being run: stopping + the cancel request.
        with get_store().transaction():
            get_store()._execute("UPDATE graph_runs SET status = 'stopping' WHERE run_id = ?", [run_id])
            get_store()._execute("UPDATE operation_job SET cancel_requested = 1 WHERE job_id = ?", [job_id])

    target = publish._snapshot if after == "snapshot" else git_adapter.merge
    owner = publish if after == "snapshot" else git_adapter
    name = "_snapshot" if after == "snapshot" else "merge"

    def wrapped(*args, **kwargs):
        out = target(*args, **kwargs)
        stop_now()
        return out

    monkeypatch.setattr(owner, name, wrapped)
    job, _ = _run_job(job_id)
    run = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE run_id = ?", (run_id,))[0]
    if after == "snapshot":
        assert (job["status"], job["phase_note"]) == ("cancelled", "no_effect_canceled")
        assert _decision(env, did)["status"] == "canceled"
        assert (run["status"], run["idle_reason"]) == ("idle", "stopped")    # the Stop went on to its end
        assert _states(env)[g] == "pending" and _merges(r["base"]) == 0
        return
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "stop_requested")
    assert _decision(env, did)["status"] == "recovery_required"
    assert run["status"] == "stopping" and _states(env)[g] == "open"          # not a Stop end yet
    _act(env, run_id, g, did, "resolve", {"request_id": "ab1", "expected_decision_version": _version(env, did),
                                         "resolution": "abandon", "reason": "stopped after the merge",
                                         **_evidence(env, g)}, 200)
    run = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE run_id = ?", (run_id,))[0]
    assert (run["status"], run["idle_reason"]) == ("idle", "stopped")
    assert _decision(env, did)["recovery_reset_required"] == 1 and _states(env)[g] == "pending"


# ── §8-7: the end cleanup judges the real HEAD ───────────────────────────────

def _flow(env, gates_then_tail):
    """R -> G1 [-> T [-> G2]]: ``gates_then_tail`` like ("G", "T", "G")."""
    nodes, edges, prev = [node("tmp_r", "R")], [], "tmp_r"
    keys = []
    for i, kind in enumerate(gates_then_tail):
        ref = f"tmp_{kind.lower()}{i}"
        nodes.append(node(ref, None, "gate") if kind == "G" else node(ref, "T", assignee={
            "kind": "human", "user_id": "u_lee", "provider_id": None}))
        edges.append(edge(f"tmp_e{i}", prev, ref))
        keys.append(ref)
        prev = ref
    key_map = _confirmed(env, nodes, edges)
    return [key_map[k] for k in keys]


def _cleanups(env):
    return _sql(env["db"], "SELECT * FROM gate_cleanup_records ORDER BY created_at")


def _run_status(env, run_id):
    return _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE run_id = ?", (run_id,))[0]


@pytest.mark.parametrize("case", ["gate2_hold", "tail_card"])
def test_an_old_archive_never_lets_a_newer_head_go(git_env, case):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    keys = _flow(env, ("G", "T", "G") if case == "gate2_hold" else ("G", "T"))
    run_id = _start(env)["run_id"]
    sha_a = _work(r, "a.txt")
    _gate(env, keys[0], merge_mode="hold", archive=True)
    body = _confirm(env, run_id, keys[0], "g1")
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    sha_next = _work(r, "b.txt")                                             # SHA-B / the tail card's SHA-C
    _finish(env, keys[1], 90)
    if case == "gate2_hold":
        if _states(env)[keys[2]] == "pending":                               # an idle run's propagation waits
            resume = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h())
            assert resume.status_code == 200, resume.text
        assert _states(env)[keys[2]] == "open"
        _confirm(env, run_id, keys[2], "g2", expect=200)                     # hold, archive OFF: done at once
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _cleanups(env) == [] and _run_cleanup(env)["status"] == "succeeded"     # its own job (D0007 §7.1)
    records = _cleanups(env)
    assert len(records) == 1
    rec = records[0]
    assert (rec["result"], rec["end_head_sha"]) == ("retained_uncovered_head", sha_next)
    bases = json.loads(rec["evidence_json"])["bases"]
    assert any(b["kind"] == "archive_checkpoint" and b["observed"] == sha_a and b["reachable"] is False for b in bases)
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 1 and r["wt"].is_dir()
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha_next


@pytest.mark.parametrize("keep", [False, True], ids=["no_keep", "keep_branch"])
def test_a_head_held_by_its_merge_target_may_go_and_comes_back_at_the_recorded_head(git_env, keep):
    from flowgate.gate_decisions import cleanup
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", keep_branch=keep)
    body = _confirm(env, run_id, g, "k1")
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _run_cleanup(env)["status"] == "succeeded"
    (rec,) = _cleanups(env)
    if keep:
        assert (rec["result"], rec["result_reason"], rec["keep_requested"]) == ("retained", "keep_branch_requested", 1)
        assert r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
        return
    assert (rec["result"], rec["basis_kind"], rec["basis_ref"], rec["end_head_sha"], rec["reachable"]) == (
        "deleted", "merged_target", "refs/heads/main", sha, 1)
    assert not r["wt"].exists() and _rev(r["base"], f"refs/heads/{r['branch']}") is None
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 0
    assert _rev(r["base"], f"refs/flowgate/gate-pin/{body['decision']['decision_id']}") == sha   # pins stay
    start, refused = cleanup.restore_point(PROJECT, FA)
    assert (start, refused) == (sha, None)
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha
    assert svc.ensure_worktree(PROJECT, "v02", FA, trigger="gate_restore", start_point=start) == "ok"
    assert _rev(r["wt"], "HEAD") == sha and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    assert cleanup.restore_point(PROJECT, FA) == (None, None)                # restored: nothing more to do


@pytest.mark.parametrize("case", ["uncommitted", "moved", "basis_lost", "remove_fails"])
def test_the_end_cleanup_keeps_the_slot_on_doubt(git_env, monkeypatch, case):
    from flowgate.gate_decisions import cleanup, git_adapter
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)                           # the archive holds the final HEAD
    body = _confirm(env, run_id, g, "d1")
    did = body["decision"]["decision_id"]
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    assert len(_cleanup_jobs(env)) == 1 and _cleanups(env) == []          # the completion's job, not run yet
    real_rev, real_git = git_adapter.rev, svc._run_git
    if case == "uncommitted":
        (r["wt"] / "left.tmp").write_text("not committed\n", encoding="utf-8")
    elif case == "moved":
        reads = {"n": 0}

        def moving(repo, ref):
            if ref == f"refs/heads/{r['branch']}":
                reads["n"] += 1
                if reads["n"] >= 2:
                    return "f" * 40                                          # the HEAD moved before the delete
            return real_rev(repo, ref)

        monkeypatch.setattr(git_adapter, "rev", moving)
    elif case == "basis_lost":
        _git(["update-ref", "-d", f"refs/flowgate/gate-archive/{did}"], cwd=r["base"])
    else:
        def failing(args, **kwargs):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(["git", *args], 1, "", "locked")
            return real_git(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", failing)
    job = _run_cleanup(env)
    assert job["status"] == ("failed" if case == "remove_fails" else "succeeded")
    (rec,) = _cleanups(env)
    assert rec["job_id"] == job["job_id"]
    expected = {"uncommitted": ("retained", "uncommitted_files"), "moved": ("retained_uncovered_head",
                                                                           "moved_before_delete"),
                "basis_lost": ("retained_uncovered_head", "no_basis_holds_head"),
                "remove_fails": ("failed", "worktree_remove_failed")}[case]
    assert (rec["result"], rec["result_reason"]) == expected
    assert rec["end_head_sha"] == sha
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 1         # never shown as free
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha


def test_the_keep_requests_are_ored_over_every_round_and_skip_what_does_not_count(product):
    from flowgate.gate_decisions import cleanup, repository as repo

    def decision(status, mode, keep, n, decision="confirm"):
        row = repo.insert_decision({
            "group_id": FA, "run_id": "run_k", "node_key": f"g{n}", "attempt_id": f"att_k{n}", "revision_no": 1,
            "actor_user_id": "u_kim", "request_id": f"rk{n}", "request_fingerprint": "f" * 64, "decision": decision,
            "status": repo.REJECTED if decision == "reject" else (repo.SUCCEEDED if status == "succeeded"
                                                                  else repo.ACCEPTED),
            "settings": {"effective": {"merge_mode": mode, "keep_branch": keep}}})
        if status in ("canceled", "recovery_required"):
            assert repo.move(row["decision_id"], 1, status)
        if status == "abandoned":
            assert repo.move(row["decision_id"], 1, repo.RECOVERY_REQUIRED)
            assert repo.move(row["decision_id"], 2, repo.ABANDONED)
        return row["decision_id"]

    counted = [decision("succeeded", "merge", True, 1), decision("abandoned", "commit", True, 2)]
    decision("canceled", "merge", True, 3)
    decision("succeeded", "hold", True, 4)                                   # hold never carries it (preflight)
    decision("succeeded", None, True, 5)
    decision("rejected", "merge", True, 6, decision="reject")
    decision("succeeded", "merge", False, 7)                                  # a later false cancels nothing
    assert sorted(cleanup.keep_requests(FA)) == sorted(counted)


# ── §8-12: retry ─────────────────────────────────────────────────────────────

def _push_rejected_once(monkeypatch):
    from flowgate.gate_decisions import git_adapter
    real, calls = git_adapter.push, []

    def rejected(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return git_adapter.PUSH_REJECTED, "push_rejected"
        return real(*args, **kwargs)

    monkeypatch.setattr(git_adapter, "push", rejected)
    return calls


def _parked_after_merge(r, monkeypatch, request_id="p1"):
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    _push_rejected_once(monkeypatch)
    body = _confirm(env, run_id, g, request_id)
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    job, claim = _run_job(job_id)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_recovery",
                                                                          "push_rejected")
    assert _decision(env, did)["status"] == "recovery_required"
    return env, g, t, run_id, did, job_id, sha, claim


def test_retry_resumes_the_same_decision_and_job_and_never_repeats_what_succeeded(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    merged = _rev(r["base"], "refs/heads/main")
    before = _decision(env, did)
    stale = _act(env, run_id, g, did, "retry", {"request_id": "r0", "expected_decision_version": 1}, 409)
    assert stale["error"]["code"] == "gate_context_changed" and _requests(env) == []
    v = before["decision_version"]
    first = _act(env, run_id, g, did, "retry", {"request_id": "r1", "expected_decision_version": v}, 202)
    assert (first["decision"]["decision_id"], first["job"]["job_id"], first["job"]["status"]) == (did, job_id,
                                                                                                  "pending")
    again = _act(env, run_id, g, did, "retry", {"request_id": "r1", "expected_decision_version": v}, 202)
    assert again["replayed"] is True and again["job"]["job_id"] == job_id                     # a lost answer
    other = _act(env, run_id, g, did, "retry", {"request_id": "r2", "expected_decision_version": v + 1}, 202)
    assert other["job"]["job_id"] == job_id                                                   # the same job going on
    pause = env["client"].post(f"/api/v1/runs/{run_id}/pause", json={}, headers=_h())
    assert pause.status_code == 200, pause.text
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    d = _decision(env, did)
    assert (d["status"], d["frozen_sha"], d["settings_json"]) == ("succeeded", sha, before["settings_json"])
    assert _rev(r["base"], "refs/heads/main") == merged == _rev(r["bare"], "refs/heads/main") and _merges(r["base"]) == 1
    assert len(_decisions(env)) == 1 and len(_jobs(env)) == 1
    assert _states(env)[g] == "done" and _states(env)[t] == "pending"        # paused: what follows waits
    resume = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h())
    assert resume.status_code == 200, resume.text
    assert _states(env)[t] == "open"
    done = _act(env, run_id, g, did, "retry", {"request_id": "r1", "expected_decision_version": v}, 200)
    assert done["decision"]["status"] == "succeeded"                                          # the stored key, now done
    late = _act(env, run_id, g, did, "retry", {"request_id": "r3", "expected_decision_version": _version(env, did)},
                409)
    assert late["error"]["code"] == "gate_decision_not_retryable"
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


# ── §8-13: cancel with no effect, then a new decision in the same attempt ────

def _parked_without_effect(r, request_id="n1"):
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    (r["base"] / "README.md").write_text("dirty base\n", encoding="utf-8")   # finalize's base_dirty refusal
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, request_id)
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    job, _ = _run_job(job_id)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_failed",
                                                                          "base_dirty")
    assert _decision(env, did)["status"] == "failed"
    return env, g, t, run_id, did, job_id


def test_cancel_without_effects_frees_the_attempt_for_new_settings_and_a_new_request(git_env):
    r = git_env
    env, g, t, run_id, did, job_id = _parked_without_effect(r)
    attempt = _attempt(env, run_id, g)["attempt_id"]
    wire = _nodes(_current(env))[g]["gate"]["recovery"]
    assert (wire["cancelable"], wire["retryable"], wire["resolvable"], wire["effects_state"]) == (True, True, False,
                                                                                                  "none")
    stale = _act(env, run_id, g, did, "cancel", {"request_id": "c0", "expected_decision_version": 99}, 409)
    assert stale["error"]["code"] == "gate_context_changed"
    v = _version(env, did)
    out = _act(env, run_id, g, did, "cancel", {"request_id": "c1", "expected_decision_version": v}, 200)
    assert out["decision"]["status"] == "canceled"
    job = _job_row(env, job_id)
    assert (job["status"], job["phase_note"]) == ("cancelled", "no_effect_canceled")
    assert _states(env)[g] == "open" and _attempt(env, run_id, g) == {"attempt_id": attempt, "status": "active"}
    assert _decision(env, did)["valid_attempt_key"] is None
    assert _act(env, run_id, g, did, "cancel", {"request_id": "c1", "expected_decision_version": v}, 200)[
        "decision"]["status"] == "canceled"                                  # the canceled key answers its result
    conflict = _act(env, run_id, g, did, "cancel", {"request_id": "c1", "expected_decision_version": v + 5}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"
    (r["base"] / "README.md").write_text("hello\n", encoding="utf-8")
    setting(env, FA, g, "merge_mode", "hold")                                # settings may change again
    fresh = _confirm(env, run_id, g, "n2", expect=200, expected_attempt_id=attempt)
    assert fresh["decision"]["status"] == "succeeded" and fresh["decision"]["attempt_id"] == attempt
    again = _decide(env, run_id, g, {"decision": "confirm", "request_id": "n3"}, 409)
    assert again["error"]["code"] == "node_not_open"
    assert [d["status"] for d in _decisions(env)] == ["canceled", "succeeded"]


# ── §8-14: permissions ───────────────────────────────────────────────────────

def test_retry_cancel_and_resolve_need_the_current_write_permission_and_their_own_run_and_node(git_env, allowed):
    r = git_env
    env, g, t, run_id, did, job_id = _parked_without_effect(r)
    v = _version(env, did)
    allowed["allow"] = False
    for action, extra in (("retry", {}), ("cancel", {}),
                          ("resolve", {"resolution": "abandon", "reason": "x", "evidence_version": 1,
                                       "evidence_refs": []})):
        res = _act(env, run_id, g, did, action, {"request_id": f"p_{action}", "expected_decision_version": v, **extra},
                   403)
        assert "decision" not in res
    assert _version(env, did) == v and _requests(env) == [] and _job_row(env, job_id)["status"] == "recovery_required"
    allowed["allow"] = True
    _act(env, run_id, g, did, "cancel", {"request_id": "ok1", "expected_decision_version": v}, 200)
    allowed["allow"] = False
    replay = _act(env, run_id, g, did, "cancel", {"request_id": "ok1", "expected_decision_version": v}, 403)
    assert "decision" not in replay and "run" not in replay
    allowed["allow"] = True
    other = _act(env, run_id, t, did, "cancel", {"request_id": "ok1", "expected_decision_version": v}, 404)
    assert other["error"]["code"] == "gate_decision_not_found"
    missing = _act(env, run_id, g, "gdc_nope", "retry", {"request_id": "z", "expected_decision_version": 1}, 404)
    assert missing["error"]["code"] == "gate_decision_not_found"
    bad = _act(env, run_id, g, did, "resolve", {"request_id": "z", "expected_decision_version": v,
                                                "resolution": "maybe"}, 422)
    assert {e["field"] for e in bad["error"]["params"]["errors"]} >= {"resolution", "reason"}


# ── §8-15: remote credentials never stay ─────────────────────────────────────

def test_remote_credentials_are_removed_from_everything_kept_logged_sent_or_shown(git_env, monkeypatch, caplog):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    password, token = "S3cr3tPassw0rdXyz", "ghp_" + "A1b2C3d4E5" * 4
    basic = base64.b64encode(f"gateuser:{password}".encode()).decode()
    svc.save_config(PROJECT, {"repo_url": r["bare"].as_uri(), "provider": "generic", "base_branch": "main",
                              "default_finalize_action": "merge", "enabled": True, "username": "gateuser",
                              "secret": token})
    leaky_url = f"https://gateuser:{password}@git.example.invalid/team/repo.git"
    with get_store().transaction():
        get_store()._execute("UPDATE project_git_config SET repo_url = ? WHERE project_id = ?", [leaky_url, PROJECT])
    emitted = []
    real_emit = svc._emit
    monkeypatch.setattr(svc, "_emit", lambda kind, p, gid, payload: emitted.append((kind, payload)) or
                        real_emit(kind, p, gid, payload))
    real = svc._run_git
    stderr = (f"fatal: unable to access '{leaky_url}/': The requested URL returned error: 403\n"
              f"Authorization: Basic {basic}\nremote: invalid token={token}\n")

    def leaky(args, **kwargs):
        if args and args[0] in ("push", "fetch"):
            return subprocess.CompletedProcess(["git", *args], 128, "", stderr)
        if args and args[0] == "ls-remote":
            return subprocess.CompletedProcess(["git", *args], 0, "", "")      # the remote branch is absent
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", leaky)
    caplog.set_level(logging.DEBUG)
    env_, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="commit", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "s1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    job, _ = _run_job(job_id)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_auth_failed")
    answers = [json.dumps(_current(env)),
               env["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h()).text,
               json.dumps(_act(env, run_id, g, did, "resolve", {"request_id": "s2", "expected_decision_version":
                                                                _version(env, did), "resolution": "abandon",
                                                                "reason": "x", **_evidence(env, g)}, 409)),
               json.dumps(_act(env, run_id, g, did, "retry", {"request_id": "s3", "expected_decision_version":
                                                              _version(env, did)}, 202))]
    _run_job(job_id)                                                         # the same failure again, through retry
    kept = []
    present = {row["name"] for row in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in ("gate_decisions", "gate_decision_effects", "gate_archive_checkpoints", "gate_cleanup_records",
                  "gate_decision_requests", "operation_job", "graph_run_events", "git_merge_session"):
        if table in present:
            kept += [json.dumps(row, default=str) for row in _sql(env["db"], f"SELECT * FROM {table}")]
    assert "gate_decisions" in present and "operation_job" in present
    texts = {"db": "\n".join(kept), "logs": caplog.text, "api": "\n".join(answers),
             "sse": json.dumps([*env["events"], *emitted], default=str)}
    for where, text in texts.items():
        for secret in (password, token, basic):
            assert secret not in text, f"{where} keeps a credential"
    assert "push_auth_failed" in texts["db"] and "origin" in texts["db"]


# ── §8-17: a local merge with a failed push holds the gate ───────────────────

def test_a_local_merge_with_a_failed_push_holds_the_gate_against_every_shortcut(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch, request_id="h1")
    v = _version(env, did)
    marks = (len(_decisions(env)), len(_jobs(env)), len(_gate_events(env, g)))
    new = _confirm(env, run_id, g, "h2", expect=409)
    assert new["error"]["code"] == "gate_decision_in_progress"
    old = _decide(env, run_id, g, {"decision": "reject"}, 409)
    assert old["error"]["code"] == "gate_decision_in_progress"
    locked = setting(env, FA, g, "merge_mode", "hold", expect=409)
    assert locked["error"]["code"] == "gate_settings_locked"
    cancel = _act(env, run_id, g, did, "cancel", {"request_id": "h3", "expected_decision_version": v}, 409)
    assert cancel["error"]["code"] == "gate_effects_present"
    replay = _confirm(env, run_id, g, "h1", expect=202)                      # the original key: a read only
    assert replay["decision"]["decision_id"] == did and replay["decision"]["status"] == "recovery_required"
    unverified = _act(env, run_id, g, did, "resolve", {"request_id": "h4", "expected_decision_version": v,
                                                       "resolution": "effects_confirmed", "reason": "trust me",
                                                       **_evidence(env, g)}, 409)
    assert unverified["error"]["code"] == "gate_effects_unverified" and unverified["error"]["params"]["missing"] == [
        "push"]
    assert (len(_decisions(env)), len(_jobs(env)), len(_gate_events(env, g))) == marks
    assert _version(env, did) == v and _states(env)[t] == "pending" and _states(env)[g] == "open"


# ── §8-18: the two recovery closes ───────────────────────────────────────────

def test_effects_confirmed_closes_with_one_confirm_and_keeps_a_paused_run_paused(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, claim = _parked_after_merge(r, monkeypatch, request_id="e1")
    pause = env["client"].post(f"/api/v1/runs/{run_id}/pause", json={}, headers=_h())
    assert pause.status_code == 200, pause.text
    _git(["push", str(r["bare"]), "main"], cwd=r["base"])                    # the operator pushed it
    v = _version(env, did)
    body = {"request_id": "e2", "expected_decision_version": v, "resolution": "effects_confirmed",
            "reason": "pushed by the operator", **_evidence(env, g)}
    out = _act(env, run_id, g, did, "resolve", body, 200)
    d = _decision(env, did)
    assert (d["status"], d["close_kind"], d["closed_by"], d["close_reason"]) == (
        "succeeded", "effects_confirmed", "u_kim", "pushed by the operator")
    assert out["decision"]["close"]["kind"] == "effects_confirmed"
    assert _job_row(env, job_id)["status"] == "succeeded" and _attempt(env, run_id, g)["status"] == "ended"
    causes = [e["cause"] for e in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE node_key = ? "
                                                    "ORDER BY event_seq", (g,))]
    assert causes.count("gate_confirmed") == 1 and causes[-1] == "gate_recovery_resolved"
    assert _states(env)[g] == "done" and _states(env)[t] == "pending"        # paused: what follows waits
    push = _effects(env, did)["push"]
    assert (push["status"], push["recovery_state"]) == ("succeeded", "confirmed")
    assert _act(env, run_id, g, did, "resolve", body, 200)["decision"]["status"] == "succeeded"   # replay
    conflict = _act(env, run_id, g, did, "resolve", {**body, "reason": "other"}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"
    retry = _act(env, run_id, g, did, "retry", {"request_id": "e3", "expected_decision_version": _version(env, did)},
                 409)
    assert retry["error"]["code"] == "gate_decision_not_retryable"
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):
        job_store.job_write(claim.ctx, status="failed")                     # the discarded lease writes nothing
    resume = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h())
    assert resume.status_code == 200, resume.text
    assert _states(env)[t] == "open"


def test_abandon_closes_without_the_remote_and_blocks_the_gate_until_a_round_split(git_env, monkeypatch):
    from flowgate.gate_decisions import cleanup, repository as repo
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "a1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    shutil.move(str(r["bare"]), str(r["bare"]) + ".gone")                    # the remote was deleted
    job, claim = _run_job(job_id)
    assert job["status"] in ("recovery_required", "retry_wait")
    if job["status"] == "retry_wait":                                        # an unreadable remote retries first
        monkeypatch.setenv("FLOWGATE_RETRY_MAX_ATTEMPTS", "1")
        job, claim = _run_job(job_id)
    assert job["status"] == "recovery_required" and _decision(env, did)["status"] == "recovery_required"
    merged = _rev(r["base"], "refs/heads/main")
    # A writer holds the job: the close waits for it.
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_token = 'lt_x', lease_until = '2999-01-01T00:00:00+00:00' "
                             "WHERE job_id = ?", [job_id])
    v = _version(env, did)
    reviewed = _evidence(env, g)
    busy = _act(env, run_id, g, did, "resolve", {"request_id": "a2", "expected_decision_version": v,
                                                 "resolution": "abandon", "reason": "remote deleted", **reviewed}, 409)
    assert busy["error"]["code"] == "gate_job_active" and _version(env, did) == v
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_token = NULL, lease_until = NULL WHERE job_id = ?",
                             [job_id])
    events_before = len(_gate_events(env, g))
    out = _act(env, run_id, g, did, "resolve", {"request_id": "a3", "expected_decision_version": v,
                                                "resolution": "abandon", "reason": "remote deleted", **reviewed}, 200)
    assert out["recovery_reset_required"] is True
    d = _decision(env, did)
    assert (d["status"], d["recovery_reset_required"], d["close_kind"], d["closed_by"]) == (
        "abandoned", 1, "abandon", "u_kim")
    job = _job_row(env, job_id)
    assert (job["status"], job["phase_note"]) == ("cancelled", "recovery_abandoned")
    assert _attempt(env, run_id, g)["status"] == "ended" and _states(env)[g] == "pending"
    assert len(_gate_events(env, g)) == events_before                       # no confirm, no reject
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}") == sha
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") == merged
    effects = _effects(env, did)
    assert (effects["merge"]["recovery_state"], effects["merge"]["recovery_ref"]) == (
        "confirmed", f"refs/flowgate/gate-recovery/{did}-merge")
    assert effects["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"
    assert all(e["reviewed_evidence_version"] is not None for e in effects.values())
    assert _rev(r["base"], "refs/heads/main") == merged                      # nothing reverted
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "blocked_recovery"}
    wire = _nodes(_current(env))[g]["gate"]
    assert (wire["recovery_reset_required"], wire["can_decide"], wire["archived"]) == (True, False, False)
    refused = _confirm(env, run_id, g, "a4", expect=409)
    assert refused["error"]["code"] == "node_not_open" and refused["error"]["params"]["reason"] == \
        "recovery_reset_required"
    assert setting(env, FA, g, "merge_mode", "hold", expect=409)["error"]["code"] == "gate_settings_locked"
    resumed = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert _states(env)[g] == "pending"                                      # a command does not reopen it
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "blocked_recovery"}
    assert _decision(env, did)["recovery_reset_required"] == 1
    with pytest.raises(repo.DecisionConflict):                                # abandoned keeps holding its attempt
        repo.insert_decision({"group_id": FA, "run_id": run_id, "node_key": g, "attempt_id": d["attempt_id"],
                              "revision_no": 1, "actor_user_id": "u_kim", "request_id": "a5",
                              "request_fingerprint": "f" * 64, "decision": "confirm", "status": "succeeded"})
    assert cleanup.blocker(FA, run_id) in ("run_not_completed", "recovery_reset_required")
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):
        job_store.job_write(claim.ctx, status="succeeded")                  # the discarded lease's late result
    assert _job_row(env, job_id)["status"] == "cancelled"


# ── T#3 rework: the review's defects, each with its focused regression test ──

def _side_commit(r, base_sha, name):
    """A result commit outside the group branch (an attempt's result waiting for its integration)."""
    tmp = r["base"].parent / f"side_{name}"
    _git(["worktree", "add", "-b", f"side_{name}", str(tmp), base_sha], cwd=r["base"])
    (tmp / name).write_text("side\n", encoding="utf-8")
    _git(["add", name], cwd=tmp)
    _git(["commit", "-m", f"side {name}"], cwd=tmp)
    sha = _rev(tmp, "HEAD")
    _git(["worktree", "remove", "--force", str(tmp)], cwd=r["base"])
    return sha


def _second_publish(env, run_id, node_key="gate_order_2"):
    """Another accepted confirm of the same group, accepted after the first (snapshot only, then completion)."""
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store
    with get_store().transaction():
        did = repo.new_decision_id()
        job = job_store.insert_gate_publish(PROJECT, {"decision_id": did, "group_id": FA, "run_id": run_id,
                                                      "node_key": node_key}, group_id=FA, target_key=node_key,
                                            requested_by="u_kim",
                                            payload={"decision_id": did, "run_id": run_id, "node_key": node_key,
                                                     "attempt_id": "att_order_2"})
        repo.insert_decision({"decision_id": did, "group_id": FA, "run_id": run_id, "node_key": node_key,
                              "attempt_id": "att_order_2", "revision_no": 1, "actor_user_id": "u_kim",
                              "request_id": "order-2", "request_fingerprint": "f" * 64, "decision": "confirm",
                              "status": repo.ACCEPTED, "job_id": job["job_id"],
                              "settings": {"effective": {"merge_mode": None, "push": False, "archive": False}}})
    return did, job["job_id"]


def test_a_groups_gate_jobs_run_in_acceptance_order_and_hold_the_integration_until_their_pin(git_env):
    """D0007 §7.3: one group's publishes run in their acceptance order, and a result integrated after an acceptance
    never enters that decision's snapshot (the integration waits for the pin, then becomes the next one's)."""
    from flowgate.graph_run import result_merge
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "o1")
    did1, job1 = body["decision"]["decision_id"], body["job"]["job_id"]
    did2, job2 = _second_publish(env, run_id)
    seqs = {j["job_id"]: json.loads(j["payload"])["group_seq"] for j in _sql(
        env["db"], "SELECT job_id, payload FROM operation_job WHERE kind = 'gate_publish'")}
    assert seqs == {job1: 1, job2: 2}
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job2])
    assert job_runner.try_claim(job2) is None                                 # the earlier job is unfinished
    assert job_store.gate_publish_predecessor(job_store.get_job(job2))["job_id"] == job1
    side = _side_commit(r, sha, "late.txt")
    row = {"group_id": FA, "run_id": run_id, "node_key": t, "attempt_id": "att_side_1", "result_commit": side}
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    assert _rev(r["wt"], "HEAD") == sha                                       # nothing integrated
    job, _ = _run_job(job1)
    assert job["status"] == "succeeded" and _decision(env, did1)["frozen_sha"] == sha
    # the second decision still holds the group branch until its own pin
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    job, _ = _run_job(job2)                                                   # now its turn: it pins HEAD
    assert _decision(env, did2)["frozen_sha"] == sha
    assert job["status"] == "recovery_required" and job["last_error_code"] == "gate_not_open"   # a stand-in gate
    status, merged = result_merge._merge(row)                                 # the pins are taken: it goes on
    assert status == result_merge.INTEGRATED and _rev(r["wt"], "HEAD") == merged != sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did1}") == sha       # the snapshots stay as they were
    assert _decision(env, did1)["frozen_sha"] == _decision(env, did2)["frozen_sha"] == sha


@pytest.mark.parametrize("phase", ["merge", "push", "archive", "push_after_close"])
def test_a_lease_lost_during_a_git_step_writes_no_evidence_and_the_next_owner_judges_it(git_env, monkeypatch, phase):
    """Every worker write is fenced by the job's lease and the decision's state: a worker that lost its lease (or whose
    decision was closed) during a Git command writes nothing; the phase stays ``unknown`` for the next owner."""
    from flowgate.gate_decisions import git_adapter
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True, archive=True)
    body = _confirm(env, run_id, g, "f1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    target_at_accept = _decision(env, did)["target_sha"]
    calls = []

    def lose():
        with get_store().transaction():
            if phase == "push_after_close":                                   # what a resolve's close leaves
                get_store()._execute("UPDATE operation_job SET status = 'cancelled', lease_token = NULL, "
                                     "lease_until = NULL WHERE job_id = ?", [job_id])
                get_store()._execute("UPDATE gate_decisions SET status = 'abandoned', recovery_reset_required = 1, "
                                     "decision_version = decision_version + 1 WHERE decision_id = ?", [did])
            else:
                get_store()._execute("UPDATE operation_job SET lease_token = 'lt_other_owner' WHERE job_id = ?",
                                     [job_id])

    owner, name = (git_adapter, {"merge": "merge", "archive": "create_ref"}.get(phase, "push"))
    real = getattr(owner, name)

    def step(*args, **kwargs):
        out = real(*args, **kwargs)
        if not calls and (phase != "archive" or args[1] == f"refs/flowgate/gate-archive/{did}"):
            calls.append(1)
            lose()
        return out

    monkeypatch.setattr(owner, name, step)
    job, _ = _run_job(job_id)
    effect_phase = "push" if phase == "push_after_close" else phase
    assert calls and _effects(env, did)[effect_phase]["status"] == "unknown"   # the evidence before the command
    assert _states(env)[g] == "open" and _gate_events(env, g) == []
    if phase == "push_after_close":
        d = _decision(env, did)
        assert (job["status"], d["status"]) == ("cancelled", "abandoned")       # the close stands; nothing more
        assert d["target_sha"] == _rev(r["base"], "refs/heads/main")             # written before the close only
        return
    assert job["status"] == "running" and job["lease_token"] == "lt_other_owner"
    d = _decision(env, did)
    assert d["status"] == "accepted"
    if phase == "merge":
        assert d["target_sha"] == target_at_accept and _effects(env, did)["merge"]["sha_after"] is None
    if phase == "archive":
        assert _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,)) == []
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    merged = _rev(r["base"], "refs/heads/main")
    assert _merges(r["base"]) == 1 and _rev(r["bare"], "refs/heads/main") == merged and merged != sha
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))) == 1
    assert {p: e["status"] for p, e in _effects(env, did).items()} == {
        "snapshot": "succeeded", "merge": "succeeded", "push": "succeeded", "archive": "succeeded"}
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


def test_an_archive_cut_off_after_its_ref_is_never_canceled_as_without_effect(git_env, monkeypatch):
    """hold + archive cut off between the ref and its checkpoint: the intent was stored first, so neither the common
    cancel nor a Stop releases it as "no effect" - also when no phase row tells of the ref (the ref itself is read)."""
    from flowgate.gate_decisions import repository
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "x1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = repository.insert_checkpoint, []

    def crash(row):
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("process died after the archive ref")
        return real(row)

    monkeypatch.setattr(repository, "insert_checkpoint", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _effects(env, did)["archive"]["status"] == "unknown" and repository.effects_state(did) == "unknown"
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x2", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown"
    _sql(env["db"], "DELETE FROM gate_decision_effects WHERE decision_id = ? AND phase = 'archive'", (did,))
    assert repository.effects_state(did) == "none"                            # only the ref tells it now
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x3", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown" and _requests(env) == []
    stop = env["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert stop.status_code in (200, 202), stop.text
    d = _decision(env, did)
    assert (d["status"], d["last_error_code"]) == ("recovery_required", "stop_requested")      # never canceled
    assert _job_row(env, job_id)["status"] == "recovery_required"
    assert _run_status(env, run_id)["status"] == "stopping" and _states(env)[g] == "open"
    # The Stop ends only through the explicit close of this intent (a retry would be asked to cancel again).
    out = _act(env, run_id, g, did, "resolve", {"request_id": "x4", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "stopped, archive cut off",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned" and _job_row(env, job_id)["phase_note"] == "recovery_abandoned"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "stopped"}
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha     # nothing removed, nothing reverted
    assert _effects(env, did)["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"


def test_a_pin_left_by_a_cut_off_snapshot_is_taken_back_although_the_head_moved(git_env, monkeypatch):
    """A pin written whose SHA was not stored yet is the snapshot: the resume takes it back instead of the HEAD of now
    (never a permanent pin_ref_mismatch)."""
    from flowgate.gate_decisions import git_adapter
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha_a = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "q1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = git_adapter.create_ref, []

    def crash(repo_root, ref, target):
        out = real(repo_root, ref, target)
        if ref == f"refs/flowgate/gate-pin/{did}" and not calls:
            calls.append(1)
            raise RuntimeError("process died after the pin")
        return out

    monkeypatch.setattr(git_adapter, "create_ref", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _decision(env, did)["frozen_sha"] is None
    assert _rev(r["base"], f"refs/flowgate/gate-pin/{did}") == sha_a
    sha_b = _work(r, "later.txt")                                              # the group HEAD moved meanwhile
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _decision(env, did)["frozen_sha"] == sha_a != sha_b
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha_a
    snapshot = json.loads(_effects(env, did)["snapshot"]["evidence_json"])
    assert snapshot["pin_taken_back"] is True


def _completed_merge_flow(r, keep=False):
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", keep_branch=keep)
    body = _confirm(env, run_id, g, "cl1")
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    (job,) = _cleanup_jobs(env)
    assert job["status"] == "pending"
    return env, g, run_id, sha, job["job_id"]


@pytest.mark.parametrize("cut", ["worktree", "branch", "ledger", "record"])
def test_a_cleanup_cut_off_at_any_step_keeps_its_plan_and_restores_the_real_end_head(git_env, monkeypatch, cut):
    """The plan (the real end HEAD, the basis) is on the job before the first delete; every step is resumed; the
    slot comes back at that HEAD whatever step the cleanup ended at."""
    from flowgate.gate_decisions import cleanup, repository
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, run_id, sha, cj = _completed_merge_flow(r)
    calls = []
    if cut == "record":
        real_insert = repository.insert_cleanup

        def failing(row):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("process died before the record")
            return real_insert(row)
        monkeypatch.setattr(repository, "insert_cleanup", failing)
    else:
        name = {"worktree": "_step_worktree", "branch": "_step_branch", "ledger": "_step_ledger"}[cut]
        real_step = getattr(cleanup, name)

        def cut_after(*args):
            out = real_step(*args)
            if not calls:
                calls.append(1)
                raise RuntimeError(f"process died after the {cut} step")
            return out
        monkeypatch.setattr(cleanup, name, cut_after)
    job, _ = _run_job(cj)
    assert job["status"] == "running" and job["phase"] == cut and calls
    plan = json.loads(job["evidence"])["plan"]
    assert (plan["end_head"], plan["branch"], plan["record"]["basis_kind"]) == (sha, r["branch"], "merged_target")
    assert cleanup.restore_point(PROJECT, FA) == (None, "git_busy")           # never "nothing to restore"
    assert _expire_and_reconcile(cj)["status"] == "pending"
    job, _ = _run_job(cj)
    assert job["status"] == "succeeded"
    (rec,) = _cleanups(env)
    assert (rec["result"], rec["end_head_sha"], rec["job_id"]) == ("deleted", sha, cj)
    assert not r["wt"].exists() and _rev(r["base"], f"refs/heads/{r['branch']}") is None
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 0
    start, refused = cleanup.restore_point(PROJECT, FA)
    assert (start, refused) == (sha, None) and _rev(r["base"], f"refs/heads/{r['branch']}") == sha
    assert svc.ensure_worktree(PROJECT, "v02", FA, trigger="gate_restore", start_point=start) == "ok"
    assert _rev(r["wt"], "HEAD") == sha


def test_the_end_cleanup_waits_for_its_locks_checks_again_under_them_and_holds_the_basis_target(git_env, monkeypatch):
    """A busy G leaves the job blocked (a later claim goes on); a run entered again after the locks were taken is seen
    by the check under them (nothing deleted); while the cleanup deletes, the basis target (B) and G are its own."""
    from types import SimpleNamespace
    from flowgate.gate_decisions import cleanup
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import lock_manager as locks
    r = git_env
    env, g, run_id, sha, cj = _completed_merge_flow(r)
    other = locks.new_context("req")
    held = locks.acquire("G", PROJECT, group_id=FA, holder_kind="source_mutation", ctx=other)
    assert held.ok
    job, _ = _run_job(cj)
    assert (job["status"], job["blocked_domain"]) == ("blocked", "G") and _cleanups(env) == []
    locks.release(other, held.lock_key)
    real_conditions = cleanup._conditions

    def entered(flow_id, run):
        with get_store().transaction():                                       # a new command took the run meanwhile
            get_store()._execute("UPDATE graph_runs SET status = 'running', idle_reason = NULL WHERE run_id = ?",
                                 [run])
        return real_conditions(flow_id, run)

    monkeypatch.setattr(cleanup, "_conditions", entered)
    job, _ = _run_job(cj)
    assert job["status"] == "succeeded" and json.loads(job["result"]) == {"skipped": "run_not_completed"}
    assert _cleanups(env) == [] and r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    monkeypatch.setattr(cleanup, "_conditions", real_conditions)
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    with get_store().transaction():
        job2 = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    seen = {}
    real_step = cleanup._step_worktree

    def contended(plan):
        for domain, extra, kind in (("B", {}, "base_mutation"), ("G", {"group_id": FA}, "source_mutation")):
            ctx = locks.new_context("req")
            o = locks.acquire(domain, PROJECT, holder_kind=kind, ctx=ctx, **extra)
            seen[domain] = o.kind
            if o.ok:
                locks.release(ctx, o.lock_key)
        return real_step(plan)

    monkeypatch.setattr(cleanup, "_step_worktree", contended)
    job, _ = _run_job(job2["job_id"])
    assert job["status"] == "succeeded" and seen == {"B": locks.BUSY, "G": locks.BUSY}
    (rec,) = _cleanups(env)
    assert (rec["result"], rec["basis_kind"], rec["basis_ref"]) == ("deleted", "merged_target", "refs/heads/main")


def test_gate_checkpoints_are_listed_beside_the_group_archives_and_restored_without_being_consumed(git_env):
    """D0007 §7.1: the existing archive list shows every round's checkpoint (told apart from the v0.1 archives); a
    checkpoint restore keeps the checkpoint and its refs and may run again; the v0.1 restore path is unchanged."""
    from flowgate.gate_decisions import checkpoints
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    r = git_env
    env = r["env"]
    keys = _flow(env, ("G", "T", "G"))
    run_id = _start(env)["run_id"]
    sha_a = _work(r, "a.txt")
    _gate(env, keys[0], merge_mode="hold", archive=True)
    first = _confirm(env, run_id, keys[0], "ck1")
    assert _run_job(first["job"]["job_id"])[0]["status"] == "succeeded"
    sha_b = _work(r, "b.txt")
    _finish(env, keys[1], 90)
    if _states(env)[keys[2]] == "pending":
        assert env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h()).status_code == 200
    _gate(env, keys[2], merge_mode="hold", archive=True)
    second = _confirm(env, run_id, keys[2], "ck2")
    assert _run_job(second["job"]["job_id"])[0]["status"] == "succeeded"
    assert _run_cleanup(env)["status"] == "succeeded" and _cleanups(env)[-1]["result"] == "deleted"
    items = [i for i in inbox_routes._project_git_archives(PROJECT) if i.get("kind") == checkpoints.KIND_GATE]
    by_sha = {i["source_sha"]: i for i in items}
    assert set(by_sha) == {sha_a, sha_b} and all(i["consumable"] is False for i in items)
    assert {by_sha[sha_a]["decision_id"], by_sha[sha_b]["decision_id"]} == {
        first["decision"]["decision_id"], second["decision"]["decision_id"]}
    cp_a = by_sha[sha_a]["archive_id"]
    out = inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=cp_a)["result"]
    assert (out["sha"], out["consumed"], out["outcome"]) == (sha_a, False, "restored")
    assert _rev(r["wt"], "HEAD") == sha_a and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    again = inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=cp_a)["result"]
    assert again["outcome"] == "already_at_checkpoint" and again["last_restored_at"]
    for i in items:                                                           # nothing consumed
        assert _rev(r["base"], i["head_ref"]) == i["source_sha"]
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints")) == 2
    with pytest.raises(GitServiceError) as conflict:                          # the slot is never moved
        inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=by_sha[sha_b]["archive_id"])
    assert conflict.value.code == "restore_conflict" and _rev(r["wt"], "HEAD") == sha_a
    with pytest.raises(GitServiceError) as legacy:                            # the v0.1 restore: its own record only
        inbox_routes._restore_group_git_archive(FA, "u_kim")
    assert legacy.value.code == "archive_not_found"


@pytest.mark.parametrize("action", ["retry", "cancel", "resolve"])
def test_two_copies_of_one_recovery_request_answer_the_first_ones_result(git_env, monkeypatch, action):
    """Both copies found no stored key before the guard: the guard serializes them and the second answers the
    first's stored result (not gate_context_changed, not a second row); other content is gate_request_conflict."""
    from flowgate.gate_decisions import recovery
    r = git_env
    if action == "cancel":
        env, g, t, run_id, did, job_id = _parked_without_effect(r)
    else:
        env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    body = {"request_id": "dup1", "expected_decision_version": _version(env, did)}
    if action == "resolve":
        body.update({"resolution": "abandon", "reason": "remote gone", **_evidence(env, g)})
    real, seen = recovery._replay, {"n": 0}

    def racing(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] in (1, 3):                                               # each copy's read before the guard
            return None
        return real(*args, **kwargs)

    monkeypatch.setattr(recovery, "_replay", racing)
    expect = 202 if action == "retry" else 200
    first = _act(env, run_id, g, did, action, body, expect)
    second = _act(env, run_id, g, did, action, body, expect)
    assert second["replayed"] is True and second["decision"]["decision_id"] == did and "replayed" not in first
    assert len([q for q in _requests(env) if q["action"] == action]) == 1
    monkeypatch.setattr(recovery, "_replay", real)
    other = _act(env, run_id, g, did, action, {**body, "expected_decision_version": body[
        "expected_decision_version"] + 7}, 409)
    assert other["error"]["code"] == "gate_request_conflict"


def test_resolve_takes_the_reviewed_evidence_as_required_and_refuses_a_stale_one(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    v = _version(env, did)
    reviewed = _evidence(env, g)
    assert reviewed["evidence_refs"] and all(ref.startswith(did + "/") for ref in reviewed["evidence_refs"])
    base = {"request_id": "ev1", "expected_decision_version": v, "resolution": "abandon", "reason": "gone"}
    missing = _act(env, run_id, g, did, "resolve", base, 422)
    assert {e["field"] for e in missing["error"]["params"]["errors"]} == {"evidence_version", "evidence_refs"}
    bad = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": ["not a ref"]}, 422)
    assert {e["field"] for e in bad["error"]["params"]["errors"]} == {"evidence_refs"}
    stale = _act(env, run_id, g, did, "resolve", {**base, **reviewed,
                                                  "evidence_version": reviewed["evidence_version"] - 1}, 409)
    assert (stale["error"]["code"], stale["error"]["params"]["reason"]) == ("gate_context_changed", "evidence_version")
    moved = [ref.rsplit("@", 1)[0] + "@99" if "/push@" in ref else ref for ref in reviewed["evidence_refs"]]
    assert moved != reviewed["evidence_refs"]
    stale_ref = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": moved}, 409)
    assert stale_ref["error"]["params"]["reason"] == "evidence_refs"
    foreign = [ref.replace(did, "gdc_other", 1) for ref in reviewed["evidence_refs"]]
    assert _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": foreign}, 409)[
        "error"]["params"]["reason"] == "evidence_refs"
    assert _requests(env) == [] and _version(env, did) == v and _job_row(env, job_id)["status"] == "recovery_required"
    out = _act(env, run_id, g, did, "resolve", {**base, **reviewed}, 200)
    assert out["decision"]["status"] == "abandoned"
    (stored,) = _requests(env)
    assert json.loads(stored["response_json"])["reviewed_evidence"] == {
        "evidence_version": reviewed["evidence_version"], "evidence_refs": sorted(reviewed["evidence_refs"])}
    event = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? AND cause = "
                            "'gate_recovery_abandoned'", (g,))
    assert json.loads(event[0]["payload_json"])["reviewed_evidence"]["evidence_refs"] == sorted(
        reviewed["evidence_refs"])
    conflict = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": []}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"


def test_effects_confirmed_never_closes_an_intent_whose_snapshot_was_not_pinned(git_env):
    """commit mode, push and archive OFF, stopped before its snapshot (another commit sits in its pin): nothing is
    proven - no frozen SHA, no pin at it - so effects_confirmed is refused and nothing follows."""
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    (r["base"] / "README.md").write_text("main moved\n", encoding="utf-8")
    _git(["commit", "-am", "main only"], cwd=r["base"])
    foreign = _rev(r["base"], "HEAD")                                        # not a commit of the group branch
    _gate(env, g, merge_mode="commit")
    body = _confirm(env, run_id, g, "u1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    _git(["update-ref", f"refs/flowgate/gate-pin/{did}", foreign], cwd=r["base"])
    job, _ = _run_job(job_id)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "pin_ref_mismatch")
    d = _decision(env, did)
    assert (d["status"], d["frozen_sha"]) == ("recovery_required", None)
    refused = _act(env, run_id, g, did, "resolve", {"request_id": "u2", "expected_decision_version": d[
        "decision_version"], "resolution": "effects_confirmed", "reason": "looks fine", **_evidence(env, g)}, 409)
    assert refused["error"]["code"] == "gate_effects_unverified"
    assert refused["error"]["params"]["missing"] == ["snapshot", "commit"]
    assert _decision(env, did)["status"] == "recovery_required" and _job_row(env, job_id)["status"] == \
        "recovery_required"
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"


def test_an_unwritable_recovery_ref_stays_an_obligation_through_the_round_split(git_env, monkeypatch):
    """abandon goes on when the merge result's recovery ref cannot be written, but that SHA is never recorded as
    kept by another ref (not the pin): it stays an obligation, the end cleanup keeps the slot for it after the round
    split, and writes the ref itself once it can."""
    from types import SimpleNamespace
    from flowgate.gate_decisions import cleanup, git_adapter, repository as repo
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    merged = _rev(r["base"], "refs/heads/main")
    real, broken = git_adapter.create_ref, {"on": True}

    def create_ref(repo_root, ref, target):
        if broken["on"] and ref == f"refs/flowgate/gate-recovery/{did}-merge":
            return "error"
        return real(repo_root, ref, target)

    monkeypatch.setattr(git_adapter, "create_ref", create_ref)
    out = _act(env, run_id, g, did, "resolve", {"request_id": "ob1", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "remote gone",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned"
    effects = _effects(env, did)
    merge_keep = json.loads(effects["merge"]["evidence_json"])["retention"]
    assert effects["merge"]["recovery_ref"] is None and merge_keep == {
        "sha": merged, "ref": f"refs/flowgate/gate-recovery/{did}-merge", "kept": False}
    assert effects["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"      # its own SHA, kept
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") is None
    assert [o["sha"] for o in repo.retention_obligations(FA)] == [merged]
    # The Time Machine's round split (card 4) clears the flag only; the obligation stays.
    d = _decision(env, did)
    assert repo.move(did, d["decision_version"], recovery_reset_required=False)
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    rec = _cleanups(env)[-1]
    assert (rec["result"], rec["result_reason"]) == ("retained", "recovery_ref_unpreserved")
    assert r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    broken["on"] = False                                                      # the ref can be written again
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") == merged
    assert repo.retention_obligations(FA) == []
    assert _effects(env, did)["merge"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}-merge"
    assert _cleanups(env)[-1]["result_reason"] != "recovery_ref_unpreserved"
