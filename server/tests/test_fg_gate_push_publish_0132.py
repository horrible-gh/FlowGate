"""0132 NR §5.3-4,5: rejected pushes park without target changes or remote sync."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_fg_card_settings import FA, db_path, env  # noqa: E402,F401
from test_fg_gate_intake_0085 import product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    _git, _rev, _work, _gate, _run_job, _decision, _effects, _confirm, _start_linear, git_env)


@pytest.mark.parametrize("diverged", [False, True])
@pytest.mark.parametrize("reason,code", [
    ("authentication failed", "push_auth_failed"),
    ("remote: GH006: Protected branch update failed\n! [remote rejected] hook declined", "push_protected"),
    ("! [rejected] sent -> main (unspecified)", "push_rejected"),
])
def test_rejection_parks_and_persists_scrubbed_evidence_without_sync(git_env, monkeypatch, reason, code, diverged):
    from flowgate.gate_decisions import git_adapter
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, _, run_id = _start_linear(r)
    frozen = _work(r, "local.txt")
    _gate(env, g, merge_mode="commit", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "classification-0132")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    branch = r["branch"]  # commit mode pushes the group branch, not the project's main.
    _git(["checkout", "-b", branch], cwd=r["seed"])
    if diverged:
        (r["seed"] / "remote.txt").write_text("remote\n", encoding="utf-8")
        _git(["add", "."], cwd=r["seed"])
        _git(["commit", "-m", "isolated remote advance"], cwd=r["seed"])
    _git(["push", "origin", branch], cwd=r["seed"])
    remote = _rev(r["bare"], "refs/heads/" + branch)
    before = (_rev(r["base"], "refs/heads/" + branch), _rev(r["base"], "HEAD"), _rev(r["wt"], "HEAD"))
    if diverged:
        assert remote != frozen
    stderr = (reason + ("\n! [rejected] sent -> main (non-fast-forward)" if diverged and code != "push_rejected" else "")
              + "\nhttps://who:password123@example.test/repo\nAuthorization: Bearer header-secret\ntoken=query-secret")
    real, calls = svc._run_git, []

    def refused(args, **kwargs):
        if args and args[0] == "push":
            calls.append(args)
            assert kwargs["extra_env"]["LC_ALL"] == "C"
            return subprocess.CompletedProcess(["git", *args], 1, "", stderr)
        return real(args, **kwargs)

    def no_sync(*args, **kwargs):
        pytest.fail("a rejection must not sync the target")

    monkeypatch.setattr(svc, "_run_git", refused)
    monkeypatch.setattr(git_adapter, "sync_merge", no_sync, raising=False)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", code)
    assert _decision(env, did)["status"] == "failed"  # commit mode has no external effect before push.
    assert len(calls) == 1
    assert (_rev(r["base"], "refs/heads/" + branch), _rev(r["base"], "HEAD"), _rev(r["wt"], "HEAD")) == before
    assert _rev(r["bare"], "refs/heads/" + branch) == remote
    effects = _effects(env, did)
    assert "remote_sync" not in effects
    assert effects["push"]["status"] == "failed" and effects["push"]["error_code"] == code
    evidence = json.loads(effects["push"]["evidence_json"])
    assert evidence["exit_code"] == 1 and evidence["diagnostic_tail"]
    assert evidence["sent_sha"] == frozen
    for secret in ("password123", "header-secret", "query-secret"):
        assert secret not in json.dumps(evidence)
