"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0098.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0098 import (
    FA,
    Path,
    _0098_target,
    _act,
    _confirm,
    _decision,
    _effects,
    _expire_and_reconcile,
    _gate,
    _git,
    _merges,
    _rev,
    _run_job,
    _sql,
    _start_linear,
    _version,
    _work,
    db_path,
    env,
    git_env,
    json,
    product,
    pytest,
    pytestmark,
    shutil,
    sys,
)

def test_0098_rejected_push_keeps_local_merge_and_retries_only_the_same_applied_sha(git_env):
    """0132 T#2 (NR §5.3-11): a non-fast-forward because only the remote went ahead is no longer parked. The push
    phase merges the fetched remote tip into the local target once (``remote_sync``) and pushes that; the approved
    merge is kept as it was (one attempt, the applied SHA unchanged)."""
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    _work(r, "ours.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch, push=True)
    body = _confirm(env, run_id, g, "rejected-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    _git(["checkout", "-b", branch], cwd=r["seed"])
    (r["seed"] / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    _git(["add", "."], cwd=r["seed"])
    _git(["commit", "-m", "remote advance"], cwd=r["seed"])
    _git(["push", "origin", branch], cwd=r["seed"])
    remote = _rev(r["seed"], "HEAD")
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    applied = effects["merge"]["sha_after"]
    assert _decision(env, did)["status"] == "succeeded" and _decision(env, did)["target_sha"] == applied
    sync = json.loads(effects["remote_sync"]["evidence_json"])
    assert effects["remote_sync"]["status"] == "succeeded"
    assert (sync["pre_head"], sync["remote_head"], sync["local_applied"]) == (applied, remote, applied)
    evidence = json.loads(effects["push"]["evidence_json"])
    assert evidence["rejections"][0]["code"] == "push_remote_ahead" and evidence["rejections"][0]["exit_code"] == 1
    assert evidence["rejections"][0]["diagnostic_tail"] and evidence["rejections"][0]["sent_sha"] == applied
    assert evidence["sent_sha"] == sync["sync_commit"] == _rev(r["base"], branch) == _rev(r["bare"], "refs/heads/" + branch)
    assert evidence["applied_sha"] == applied and effects["merge"]["attempt_count"] == 1
    assert _merges(r["base"], branch) == 1  # the sync commit only: the approved fast-forward is kept
