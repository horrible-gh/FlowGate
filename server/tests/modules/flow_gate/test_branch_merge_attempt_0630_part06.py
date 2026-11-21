"""Independently selectable scenarios; shared fixtures remain in test_branch_merge_attempt_0630.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_branch_merge_attempt_0630 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _OTHER_GROUP,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _approve,
    _busy,
    _client,
    _crashed_branch_merge,
    _ctx,
    _end_attempt_job,
    _git,
    _plan_finalize,
    _prepare_route_conflict,
    _resolve_all,
    _review_pending,
    _session,
    _view,
    base64,
    contextmanager,
    copy,
    itertools,
    mock_db,
    os,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    uuid,
)

def test_17_large_conflict_mention_omits_chunk_text_and_is_project_scoped(proj):
    from modules.flow_gate.api import token_routes
    p = proj
    big_target = "".join(f"target line {i} {'x' * 60}\n" for i in range(200))
    big_source = "".join(f"source line {i} {'y' * 60}\n" for i in range(200))
    p.branch("develop")
    p.branch("feature")
    p.commit_on("develop", {"same.txt": big_target})
    p.commit_on("feature", {"same.txt": big_source})
    from modules.flow_gate.services import git_service as svc
    out = svc.merge_branches(p.pid, "feature", "develop", push=False)
    mention = token_routes._build_conflict_mention(
        group_id=None, project_id=p.pid, merge_id=out["merge_id"], scratch_dir="",
        raw_token="tok", api_base_url="http://h/api/v1",
    )
    assert '"chunk_text": "omitted"' in mention
    assert "raw_content" not in mention and "x" * 60 not in mention
    assert f"http://h/api/v1/projects/{p.pid}/git/merge/{out['merge_id']}/resolve-token" in mention
    assert "owner: branch_merge" in mention and "group: None" not in mention
    assert len(mention) < 20_000
def test_18_eol_only_conflict_goes_to_review_without_an_ai_run(proj):
    from modules.flow_gate.services.git import branch_merge
    p = proj
    p.branch("develop")
    p.branch("feature")
    p.commit_on("develop", {"same.txt": b"base\r\n"}, "crlf")
    p.commit_on("feature", {"same.txt": b"base\nadded\n"}, "append")
    from modules.flow_gate.services import git_service as svc
    out = svc.merge_branches(p.pid, "feature", "develop", push=False)
    if out["status"] == "merged":
        pytest.skip("this git merged the EOL change cleanly; no EOL-only conflict to test")
    merge_id = out["merge_id"]
    assert out["eol_only_paths"] == ["same.txt"] and out["remaining_conflicts"] == []
    started = []
    view = branch_merge.settle_new_conflict(
        p.pid, merge_id, start_run=lambda *_a: started.append(1) or "run-x",
    )
    assert started == []                                        # nothing for an AI to do
    assert view["state"] == "resolved_pending_review"
    assert p.sha("refs/heads/develop") == _ctx(merge_id)["base_head"]   # not committed
def test_19_duplicate_approve_is_idempotent(proj):
    p = proj
    out, _before = p.conflict(push=False)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    attempt = str(uuid.uuid4())
    fingerprint = _ctx(merge_id)["review_fingerprint"]
    first = _approve(p, merge_id, attempt_id=attempt, fingerprint=fingerprint)
    head = p.sha("refs/heads/develop")
    second = _approve(p, merge_id, attempt_id=attempt, fingerprint=fingerprint)
    assert first["status"] == "merged" and second["status"] == "already_applied"
    assert p.sha("refs/heads/develop") == head
def test_20_21_open_attempt_pins_source_and_target(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    assert svc.check_branch_delete(p.pid, "feature") == "branch_in_use"
    assert svc.check_branch_delete(p.pid, "develop") == "branch_in_use"
    with pytest.raises(GitServiceError) as caught:
        svc.delete_branch(p.pid, "feature")
    assert caught.value.code == "branch_in_use"
    assert caught.value.details["merge_id"] == out["merge_id"]
    assert caught.value.details["role"] == "source"
    svc.abort_merge(None, out["merge_id"], project_id=p.pid)
    assert svc.check_branch_delete(p.pid, "develop") != "branch_in_use"
