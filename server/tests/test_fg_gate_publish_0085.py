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

flowgate.v02.0098 T#2 (TR0009): ``--dist loadfile`` gives one file to one worker, and this file's serial
time alone exceeded the 1800 s Self-check limit. The tests from §8-7 on were therefore moved unchanged:
§8-7 to ``test_fg_gate_publish_recovery_0085``, §8-12 to §8-18 to ``test_fg_gate_publish_retry_0085``, the T#3
rework block to ``test_fg_gate_publish_rework_0085`` and ``test_fg_gate_publish_rework_recovery_0085``; the 0098
target tests are in ``test_fg_gate_publish_0098`` and ``test_fg_gate_publish_0098_resume``. They use this
harness and the helpers kept at the end of this file.
"""
from __future__ import annotations

import base64
import json
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
def git_env(product, monkeypatch, tmp_path, request):
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
    # flowgate.v02.0098 T#2 (TR0009): the server keeps its instance alive with the startup()
    # heartbeat loop, and the direct Runner / Sweeper calls here need the same loop. Without it
    # a Git step slower than the default FLOWGATE_INSTANCE_DEAD_AFTER_SEC (60 s) on a loaded
    # host failed ensure_lease with LeaseLost. The default lease and death limits stay intact.
    reg.startup()
    request.addfinalizer(reg.shutdown)
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


def _alive():
    """flowgate.v02.0098 T#2 (TR0009): one heartbeat of this instance before a direct Runner / Sweeper claim.

    A confirm runs its preflight Git checks inside the Flow guard's write transaction. On a loaded host that took
    about 45 s, every heartbeat of the startup() loop meanwhile failed with "database is locked", and the job
    claimed right after lost its lease at its first ensure_lease. The guard is gone here, so the heartbeat the
    loop's next tick would make succeeds; the default lease and death limits stay intact."""
    from modules.flow_gate.services.git import instance_registry as reg
    assert reg.heartbeat_once() is True


def _run_job(job_id):
    """One Runner claim + execute of the job (its available time moved into the past first)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_store
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    _alive()
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
    _alive()
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


# ── helpers shared with the split publish files and test_fg_gate_rounds_0085 ──────────────────

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


def _parked_without_effect(r, request_id="n1"):
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, request_id)
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    (r["base"] / "README.md").write_text("dirty base\n", encoding="utf-8")   # changed after acceptance
    job, _ = _run_job(job_id)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_failed",
                                                                          "base_dirty")
    assert _decision(env, did)["status"] == "failed"
    return env, g, t, run_id, did, job_id


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
