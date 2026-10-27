"""flowgate.v02.0085 T#3 (D0007 §7.2 / §7.3; §8-12, 13, 14 (retry / cancel / resolve), 15, 17, 18): the recovery of
the confirm gate's ``gate_publish`` job on a real Git repository.

flowgate.v02.0098 T#2 (TR0009): moved unchanged from ``test_fg_gate_publish_recovery_0085`` (before that
``test_fg_gate_publish_0085``), whose harness, fixtures and shared helpers it imports. ``--dist loadfile``
gives one file to one worker, and the 16 cases of the one file took about as long as the 1800 s Self-check
limit on a loaded host.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _h, _sql, db_path, env, setting  # noqa: E402,F401
from test_fg_gate_intake_0085 import (  # noqa: E402,F401
    _attempt, _current, _decide, _decisions, _finish, _gate_events, _jobs, _nodes, _start, _states, product, allowed)
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    PROJECT, _git, _rev, _work, _gate, _run_job, _decision, _effects, _job_row, _merges, _confirm, _act, _version,
    _requests, _start_linear, _evidence, _cleanup_jobs, _run_cleanup, _flow, _cleanups, _run_status,
    _parked_after_merge, _parked_without_effect, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


# ── §8-12: retry ─────────────────────────────────────────────────────────────

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
