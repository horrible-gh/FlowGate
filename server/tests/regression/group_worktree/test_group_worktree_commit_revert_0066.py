"""E4--E6: real Git contents, real TR2 mutation guard and HTTP contracts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy

import pytest

from tests.integration.group_worktree.test_group_worktree_stage_0066 import (
    repo as stage_repo, git, put, index, write, status,
    G, URL, SECRET, m, gs, git_routes, tr2_file_policy as tr2,
    REAL_ACQUIRE, REAL_RELEASE, REAL_STILL_HELD, lock_store, db_path,
)
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.storage.safe_path import MutationPathAliasError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")


@pytest.fixture
def repo(stage_repo, monkeypatch):
    monkeypatch.setattr(tr2.tr2_service, "resolve_source_root", lambda *args: stage_repo.root)
    monkeypatch.setattr(tr2.db_recovery, "has_unresolved", lambda _: False)
    return stage_repo


def prepare(repo, operation):
    if operation == "remove":
        path = "new.py"
    else:
        path = "a.py"
    put(repo.root, path, "staged\n")
    git(repo.root, "add", path)
    return {"message": "fix: staged"} if operation == "commit" else {"paths": [path]}


def snapshot(repo):
    return (git(repo.root, "rev-parse", "HEAD"), index(repo),
            {p.relative_to(repo.root).as_posix(): p.read_bytes()
             for p in repo.root.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo.root).parts})


def test_commit_only_index_preserves_working_and_untracked_author_state(repo, monkeypatch):
    repo.cfg.update(author_name="Configured Author", author_email="configured@example.test")
    before_state = deepcopy(repo.state)
    put(repo.root, "a.py", "index version\n")
    git(repo.root, "add", "a.py")
    put(repo.root, "a.py", "working version\n")
    put(repo.root, "new.py", "new untracked\n")
    put(repo.root, "b.py", "unstaged\n")
    expected = snapshot(repo)[2]
    observed = []
    run = gs._run_git
    def spy(args, **kwargs):
        observed.append(args)
        return run(args, **kwargs)
    monkeypatch.setattr(gs, "_run_git", spy)
    result = write(repo, "commit", {"message": " \tfix: staged\n only  "})
    assert result["subject"] == "fix: staged only"
    assert result["commit_sha"] == git(repo.root, "rev-parse", "HEAD").strip()
    assert result["commit"] == result["commit_sha"][:7]
    assert result["committed_paths"] == ["a.py"] and result["committed_count"] == 1
    assert git(repo.root, "show", "HEAD:a.py") == "index version\n"
    assert git(repo.root, "show", "HEAD:b.py") == "original\n"
    assert snapshot(repo)[2] == expected
    assert git(repo.root, "log", "-1", "--format=%an <%ae>").strip() == "Configured Author <configured@example.test>"
    assert repo.state == before_state
    assert result["status"]["counts"]["staged"] == 0
    assert result["status"]["counts"]["unstaged"] == 2
    assert result["status"]["counts"]["untracked"] == 1
    assert result["status"]["committed"]["commit_count"] == 1
    assert not any(set(args) & {"add", "merge", "push", "fetch", "reset"} for args in observed)
    assert repo.calls[0][1]["holder_kind"] == "source_mutation"
    assert repo.calls[-1][0] == "release"


@pytest.mark.parametrize("message,code", [
    ("", "commit_message_required"), (" \n\t ", "commit_message_required"),
    ("x" * (gs.COMMIT_SUBJECT_MAX + 1), "invalid_request"), ("a\0b", "invalid_request"),
])
def test_bad_message_before_lock_without_side_effects(repo, message, code):
    prepare(repo, "commit")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": message}, 422)["code"] == code
    assert not repo.calls and snapshot(repo) == before


def test_message_exact_limit_and_unicode(repo):
    prepare(repo, "commit")
    message = "가" * gs.COMMIT_SUBJECT_MAX
    assert write(repo, "commit", {"message": message})["subject"] == message


def test_nothing_staged_has_no_absorption(repo):
    put(repo.root, "a.py", "working\n")
    put(repo.root, "new.py", "new\n")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": "fix: none"}, 422)["code"] == "nothing_staged"
    assert snapshot(repo) == before


@pytest.mark.parametrize("path,kind", [(SECRET, "hidden"), ("managed.py", "managed"), ("new.log", "excluded_new")])
def test_external_protected_stage_refuses_entire_commit(repo, path, kind):
    prepare(repo, "commit")
    put(repo.root, path, "protected\n")
    git(repo.root, "add", path)
    before = snapshot(repo)
    error = write(repo, "commit", {"message": "fix: protected"}, 409)
    assert error["code"] == "staged_protected_paths"
    assert error["details"]["counts"][kind] == 1
    assert SECRET not in json.dumps(error)
    if path == SECRET:
        assert error["details"]["counts"]["managed"] == 0 and not error["details"]["paths"]
    assert snapshot(repo) == before


@pytest.mark.parametrize("path", ["managed.py", SECRET])
def test_protected_rename_source_deletion_blocks_commit(repo, path):
    git(repo.root, "mv", path, "renamed.py")
    before = snapshot(repo)
    error = write(repo, "commit", {"message": "fix: rename"}, 409)
    assert error["code"] == "staged_protected_paths" and SECRET not in json.dumps(error)
    assert snapshot(repo) == before


@pytest.mark.parametrize("delete", [False, True])
def test_existing_excluded_changes_are_committable(repo, delete):
    if delete:
        (repo.root / "old.log").unlink()
    else:
        put(repo.root, "old.log", "cleanup\n")
    git(repo.root, "add", "old.log")
    result = write(repo, "commit", {"message": "fix: existing artifact"})
    assert result["committed_paths"] == ["old.log"]
    assert ("old.log" in git(repo.root, "ls-tree", "--name-only", "HEAD").splitlines()) is not delete


def make_unsupported(repo, kind):
    oid = git(repo.root, "rev-parse", "HEAD:a.py").strip()
    if kind == "type":
        git(repo.root, "update-index", "--cacheinfo", "120000", oid, "a.py")
    else:
        payload = f"0 {'0' * 40}\ta.py\0" + "".join(
            f"100644 {oid} {stage}\ta.py\0" for stage in (1, 2, 3))
        result = subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=repo.root,
                                input=payload.encode(), capture_output=True)
        assert result.returncode == 0


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("kind", ["type", "unmerged"])
def test_unsupported_entries_never_mutate(repo, operation, kind):
    make_unsupported(repo, kind)
    before = snapshot(repo)
    body = {"message": "fix: unsupported"} if operation == "commit" else {"paths": ["a.py"]}
    error = write(repo, operation, body, 409)
    assert error["code"] == ("index_entry_unsupported" if operation == "commit" else "path_protected")
    assert snapshot(repo) == before


@pytest.mark.parametrize("token", ["index_token", "worktree_token"])
def test_commit_stale_token_is_side_effect_free(repo, token):
    prepare(repo, "commit")
    view = status(repo)
    put(repo.root, "b.py", "later\n")
    git(repo.root, "add", "b.py")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": "fix: stale", token: view[token]}, 409)["code"] == "worktree_changed"
    assert snapshot(repo) == before
