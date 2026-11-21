"""Independently selectable scenarios; shared fixtures remain in test_group_worktree_commit_revert_0066.py."""
from __future__ import annotations

from tests.regression.group_worktree.test_group_worktree_commit_revert_0066 import (
    G,
    GitServiceError,
    MutationPathAliasError,
    REAL_ACQUIRE,
    REAL_RELEASE,
    REAL_STILL_HELD,
    SECRET,
    URL,
    db_path,
    deepcopy,
    git,
    git_routes,
    gs,
    index,
    json,
    lock_store,
    m,
    make_unsupported,
    os,
    prepare,
    put,
    pytest,
    pytestmark,
    repo,
    shutil,
    snapshot,
    stage_repo,
    status,
    subprocess,
    tr2,
    write,
)

def test_index_token_allows_later_working_edit_and_matching_tokens_commit(repo):
    prepare(repo, "commit")
    view = status(repo)
    put(repo.root, "b.py", "later\n")
    result = write(repo, "commit", {"message": "fix: same index", "index_token": view["index_token"]})
    assert result["committed_paths"] == ["a.py"]
    assert (repo.root / "b.py").read_text() == "later\n"
    prepare(repo, "commit")
    put(repo.root, "a.py", "second\n")
    git(repo.root, "add", "a.py")
    view = status(repo)
    write(repo, "commit", {"message": "fix: second", "index_token": view["index_token"],
                           "worktree_token": view["worktree_token"]})
def test_revert_restores_index_deleted_files_and_existing_excluded(repo):
    for path in ("a.py", "b.py", "old.log"):
        put(repo.root, path, "staged\n")
        git(repo.root, "add", path)
    put(repo.root, "a.py", "working\n")
    (repo.root / "b.py").unlink()
    put(repo.root, "untouched.py", "keep\n")
    result = write(repo, "revert", {"paths": ["a.py", "b.py", "old.log"]})
    assert [row["result"] for row in result["results"]] == ["reverted"] * 3
    for path in ("a.py", "b.py", "old.log"):
        assert (repo.root / path).read_text() == "original\n"
    assert not git(repo.root, "diff", "--cached", "--name-only")
    assert (repo.root / "untouched.py").read_text() == "keep\n"
    assert git(repo.root, "rev-parse", "HEAD").strip() == repo.floor
    assert repo.state["status"] == "none"
    assert write(repo, "revert", {"paths": ["a.py"]})["results"] == [{"path": "a.py", "result": "not_changed"}]
@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("new_path", ["new.py", "new.log"])
def test_revert_rejects_new_path_before_restoring_anything(repo, staged, new_path):
    put(repo.root, "a.py", "keep\n")
    put(repo.root, new_path, "new\n")
    if staged:
        git(repo.root, "add", new_path)
    before = snapshot(repo)
    assert write(repo, "revert", {"paths": ["a.py", new_path]}, 422)["code"] == "new_path_use_remove"
    assert snapshot(repo) == before
@pytest.mark.parametrize("staged", [False, True])
def test_remove_new_file_from_index_and_disk_is_idempotent(repo, staged):
    put(repo.root, "new.py", "new\n")
    if staged:
        git(repo.root, "add", "new.py")
        put(repo.root, "new.py", "later\n")
    put(repo.root, "a.py", "keep\n")
    result = write(repo, "remove", {"paths": ["new.py"]})
    assert result["results"] == [{"path": "new.py", "result": "removed"}]
    assert not (repo.root / "new.py").exists()
    assert "new.py" not in git(repo.root, "ls-files").splitlines()
    assert (repo.root / "a.py").read_text() == "keep\n"
    assert write(repo, "remove", {"paths": ["new.py"]})["results"][0]["result"] == "not_changed"
def test_remove_staged_file_missing_on_disk(repo):
    prepare(repo, "remove")
    (repo.root / "new.py").unlink()
    assert write(repo, "remove", {"paths": ["new.py"]})["results"][0]["result"] == "removed"
    assert "new.py" not in git(repo.root, "ls-files").splitlines()
def test_remove_named_ignored_new_file(repo):
    (repo.root / ".git" / "info" / "exclude").write_text("ignored.tmp\n", encoding="utf-8")
    put(repo.root, "ignored.tmp", "ignored\n")
    assert "ignored.tmp" not in {row["path"] for row in status(repo)["files"]}
    assert write(repo, "remove", {"paths": ["ignored.tmp"]})["results"] == [
        {"path": "ignored.tmp", "result": "removed"}]
    assert not (repo.root / "ignored.tmp").exists()
    assert write(repo, "remove", {"paths": ["ignored.tmp"]})["results"][0]["result"] == "not_changed"
def test_remove_rejects_mixed_existing_request_without_partial_deletion(repo):
    prepare(repo, "remove")
    before = snapshot(repo)
    assert write(repo, "remove", {"paths": ["new.py", "a.py"]}, 422)["code"] == "existing_path_use_revert"
    assert snapshot(repo) == before
@pytest.mark.parametrize("staged", [False, True])
def test_new_excluded_remove_is_protected_even_when_absent(repo, staged):
    put(repo.root, "new.log", "keep\n")
    if staged:
        git(repo.root, "add", "new.log")
    before = snapshot(repo)
    error = write(repo, "remove", {"paths": ["new.log"]}, 409)
    assert error["code"] == "path_protected"
    assert snapshot(repo) == before
    (repo.root / "new.log").unlink()
    assert write(repo, "remove", {"paths": ["new.log"]}, 409)["code"] == "path_protected"
@pytest.mark.parametrize("operation", ["revert", "remove"])
@pytest.mark.parametrize("path", [SECRET, "not-present/.env"])
def test_hidden_precedes_lock_existence_and_ownership(repo, operation, path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Hidden requests must not read the slot or ownership or acquire G")
    monkeypatch.setattr(m, "_context", forbidden)
    error = write(repo, operation, {"paths": [path]}, 409)
    assert error["code"] == "path_protected" and error["details"] == {"reason": "hidden"}
    assert path not in json.dumps(error) and "TR2_MANAGED_FILE" not in json.dumps(error)
    assert not repo.calls
