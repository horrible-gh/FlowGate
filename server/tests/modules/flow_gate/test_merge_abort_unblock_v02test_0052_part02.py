"""Independently selectable scenarios; shared fixtures remain in test_merge_abort_unblock_v02test_0052.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_merge_abort_unblock_v02test_0052 import (
    ADMIN,
    MEMBER,
    Path,
    Proj,
    _COUNTER,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _age_session,
    _assert_abort_audited,
    _conflict_in_group_worktree,
    _ctx,
    _git,
    _git_path,
    _group_branch_sha,
    _hold_index_lock,
    _merge_head,
    _resolve_everything,
    _rewrite_same_bytes_later,
    _routes_client,
    _seed_wf_done_root,
    _session,
    _stale_state,
    _stat_dirty,
    _two_file_conflict,
    base64,
    contextmanager,
    itertools,
    os,
    patch_store,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    time,
)

def test_reason_line_is_gits_cause_not_its_advice():
    from modules.flow_gate.services import git_service as svc

    stderr_457 = (
        "error: Entry 'client_gui/src/features/designer/inspector/DesignerInspector.vue' "
        "not uptodate. Cannot merge.\nfatal: Could not reset index file to revision 'HEAD'.\n"
    )
    assert svc._git_reason_line(stderr_457) == (
        "error: Entry 'client_gui/src/features/designer/inspector/DesignerInspector.vue' "
        "not uptodate. Cannot merge."
    )
    lock = ("fatal: Unable to create '/x/.git/index.lock': File exists.\n\nAnother git process "
            "seems to be running in this repository.\nremove the file manually to continue.\n")
    assert svc._git_reason_line(lock).startswith("fatal: Unable to create")
    assert svc._git_reason_line("plain last\nline two\n") == "line two"
    assert svc._git_reason_line("") == "git command failed"
    assert len(svc._git_reason_line("error: " + "x" * 1000)) == svc.GIT_REASON_MAX_CHARS
    assert "secret" not in svc._git_reason_line(
        "fatal: unable to access 'https://user:secret@host/repo.git/'\n")
def test_eol_only_file_is_not_left_stat_stale_by_rerere_restore(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import rerere_checkpoint

    real_run = rerere_checkpoint._run

    def slow_rerere(root):
        real_run(root)
        time.sleep(1.2)       # 457: `git rerere` took seconds between the stage and the restore

    monkeypatch.setattr(rerere_checkpoint, "_run", slow_rerere)
    gid = proj.group(3)
    proj.local_branch("develop")
    # eol.txt: the group only turns it CRLF, develop edits line one → EOL-only conflict.
    (proj.wt(gid) / "eol.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    (proj.wt(gid) / "shared.txt").write_bytes(f"{gid} version\n".encode())
    proj.origin_commit("develop", {"eol.txt": b"A\nb\nc\n", "shared.txt": "develop version\n"})
    proj.ready(gid)
    out = svc.finalize(gid, "merge", target_branch="develop")
    assert out["result"]["status"] == "conflict", out
    merge_id = out["result"]["merge_id"]
    assert _ctx(merge_id)["eol_only_paths"] == ["eol.txt"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    assert "eol.txt" not in _stat_dirty(root)
    # and therefore even a raw abort (no refresh) is no longer refused
    raw = _git(["merge", "--abort"], cwd=root, check=False)
    assert raw.returncode == 0, raw.stderr
    assert svc.abort_merge(gid, merge_id)["result"]["already_cleaned"] is True
def test_restore_skips_identical_bytes(tmp_path):
    from modules.flow_gate.services.git import rerere_checkpoint

    repo = tmp_path / "r"
    _git(["init", "-b", "main", str(repo)])
    target = repo / "same.txt"
    target.write_bytes(b"same\n")
    old = time.time() - 3600
    os.utime(target, (old, old))
    rerere_checkpoint._restore(repo, {"same.txt": b"same\n"}, set())
    assert abs(target.stat().st_mtime - old) < 1
def test_merge_head_missing_abort_closes_and_resolve_is_refused(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError

    gid = proj.group(4)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    _git_path(root, "MERGE_HEAD").unlink()          # the merge state vanished from disk
    before = (root / "shared.txt").read_bytes()

    with pytest.raises(GitServiceError) as caught:
        svc.resolve_conflicts(gid, merge_id, [{
            "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
        }], True)
    assert caught.value.status == 409
    assert caught.value.code == "merge_not_in_progress"
    assert caught.value.params["reason"] == caught.value.details["reason"]
    assert caught.value.details["next_action"] == "abort"
    assert "MERGE_HEAD" in caught.value.details["reason"]
    assert (root / "shared.txt").read_bytes() == before          # nothing written
    assert sorted(db_git.remaining_conflicts(merge_id)) == ["other.txt", "shared.txt"]

    out = svc.abort_merge(gid, merge_id)
    assert out["result"]["already_cleaned"] is True
    assert out["result"]["branch_preserved"] is True
    assert _session(merge_id)["status"] == "aborted"
    assert db_git.get_state(gid)["status"] == "waiting"
