"""flowgate.default.0641 T2#3 — conflict/history locking and TR2-managed exception."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import threading

import pytest

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import branch_merge
from modules.flow_gate.services.git import conflict
from modules.flow_gate.services import tr2_file_policy
from modules.flow_gate.documents import tr2_approval_service
from modules.flow_gate.documents import tr2_apply_adapter
from modules.flow_gate.documents import tr2_precheck
from modules.flow_gate.documents import tr2_service


class _Db:
    SESSION_KIND_GROUP_UPDATE = db_git.SESSION_KIND_GROUP_UPDATE
    TR_SESSION_KINDS = db_git.TR_SESSION_KINDS

    def __init__(self, kind: str, *, branch_merge: bool = False):
        self.kind = kind
        self.branch_merge = branch_merge
        self.resolved = []
        self.context = {}
        self.closed = []
        self.session = {
            "merge_id": 7,
            "group_id": None if branch_merge else "flowgate.default.0641",
            "project_id": "flowgate" if branch_merge else None,
            "owner_type": "branch_merge" if branch_merge else "group",
            "status": "open",
            "kind": kind,
            "context": "{}",
        }

    def get_session(self, merge_id):
        return dict(self.session)

    def session_kind(self, _session):
        return self.kind

    def touch_session(self, merge_id):
        pass

    def session_context(self, _session):
        return dict(self.context)

    def set_session_context(self, merge_id, context):
        self.context = dict(context)

    def session_files(self, merge_id):
        return [{"path": "owned.txt"}]

    def mark_file_resolved(self, merge_id, path):
        self.resolved.append(path)

    def remaining_conflicts(self, merge_id):
        return []

    def close_session(self, merge_id, status):
        self.closed.append((merge_id, status))


def _install(monkeypatch, tmp_path, kind, *, branch_merge=False, complete=False):
    root = tmp_path / f"root-{kind}-{branch_merge}"
    root.mkdir()
    target = root / "owned.txt"
    target.write_text("original", encoding="utf-8")

    db = _Db(kind, branch_merge=branch_merge)
    state = {"locked": False, "session_context_calls": 0, "writes": [], "git": []}

    def acquire(project_id, holder, wait_sec=None):
        assert project_id == "flowgate"
        assert not state["locked"]
        state["locked"] = True
        return True

    def release(project_id, holder):
        assert state["locked"]
        state["locked"] = False

    def session_context(group_id, merge_id, **kwargs):
        state["session_context_calls"] += 1
        # First call is the semantic pre-pass, second call MUST happen under project lock.
        if state["session_context_calls"] >= 2:
            assert state["locked"] is True
        return db.get_session(merge_id), {}, "flowgate", root

    def write(root_arg, path, target_arg, content):
        assert state["locked"] is True
        state["writes"].append(path)
        target_arg.write_text(content, encoding="utf-8")

    def run_git(args, cwd, **kwargs):
        if args and args[0] == "add":
            assert state["locked"] is True
        if "commit" in args:
            assert state["locked"] is True
        state["git"].append(list(args))
        stdout = "abc123\n" if args[:2] == ["rev-parse", "--short"] else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(git_service, "db_git", db)
    monkeypatch.setattr(git_service, "_session_context", session_context)
    monkeypatch.setattr(git_service, "_acquire_lock", acquire)
    monkeypatch.setattr(git_service.db_git, "release_lock", release, raising=False)
    monkeypatch.setattr(git_service, "_run_git", run_git)
    monkeypatch.setattr(conflict, "_write_resolved_file", write)
    monkeypatch.setattr(conflict, "_conflict_side_violations", lambda *_: [])
    monkeypatch.setattr(conflict, "_classify_conflict_chunks", lambda *_: [])
    monkeypatch.setattr(git_service, "has_conflict_markers", lambda _text: False)

    # The session-bound conflict exception is deliberate: TR2-managed ownership must
    # not route this controlled Git resolution through the ordinary mutation guard.
    monkeypatch.setattr(
        tr2_file_policy,
        "general_source_mutation",
        lambda *_a, **_k: pytest.fail("conflict resolution entered ordinary TR2 guard"),
    )

    if branch_merge:
        monkeypatch.setattr(conflict, "_is_branch_merge_session", lambda _s: True)
        monkeypatch.setattr(
            branch_merge, "note_resolution_submitted",
            lambda *_a, **_k: (
                state["locked"] is True
                or pytest.fail("branch resolution metadata updated outside project lock")
            ),
        )
        monkeypatch.setattr(
            branch_merge, "note_review_pending",
            lambda *_a, **_k: (
                state["locked"] is True
                or pytest.fail("branch review metadata updated outside project lock")
            ),
        )
    else:
        monkeypatch.setattr(conflict, "_is_branch_merge_session", lambda _s: False)

    return root, target, db, state


@pytest.mark.parametrize(
    "kind,branch_merge",
    [
        (db_git.SESSION_KIND_TR_REVERT, False),
        (db_git.SESSION_KIND_TR_REAPPLY, False),
        (db_git.SESSION_KIND_GROUP_UPDATE, False),
        (db_git.SESSION_KIND_MERGE, False),
        (db_git.SESSION_KIND_MERGE, True),
    ],
)
def test_every_supported_conflict_session_writes_and_git_adds_under_project_lock(
    monkeypatch, tmp_path, kind, branch_merge
):
    root, target, db, state = _install(
        monkeypatch, tmp_path, kind, branch_merge=branch_merge
    )
    result = conflict.resolve_conflicts(
        None if branch_merge else "flowgate.default.0641",
        7,
        [{"path": "owned.txt", "content": "resolved"}],
        False,
        **({"project_id": "flowgate"} if branch_merge else {}),
    )

    assert result["result"]["status"] == "conflict"
    assert target.read_text(encoding="utf-8") == "resolved"
    assert state["writes"] == ["owned.txt"]
    assert ["add", "--", "owned.txt"] in state["git"]
    assert db.resolved == ["owned.txt"]
    assert state["locked"] is False
    assert state["session_context_calls"] >= 2


def test_locked_reread_rejects_source_change_before_first_write(monkeypatch, tmp_path):
    root, target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_TR_REVERT
    )
    original_context = git_service._session_context

    calls = {"n": 0}
    def changed_context(group_id, merge_id, **kwargs):
        calls["n"] += 1
        row = original_context(group_id, merge_id, **kwargs)
        if calls["n"] == 2:
            # Simulates an actor that changed bytes after the semantic pre-pass but before
            # this resolver obtained the project mutex.
            target.write_text("raced", encoding="utf-8")
        return row

    monkeypatch.setattr(git_service, "_session_context", changed_context)

    with pytest.raises(conflict.GitServiceError) as exc:
        conflict.resolve_conflicts(
            "flowgate.default.0641",
            7,
            [{"path": "owned.txt", "content": "resolved"}],
            False,
        )
    assert exc.value.code == "conflict_source_changed"
    assert state["writes"] == []
    assert ["add", "--", "owned.txt"] not in state["git"]
    assert target.read_text(encoding="utf-8") == "raced"


def test_group_update_completion_commit_and_session_close_stay_under_lock(
    monkeypatch, tmp_path
):
    _root, _target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_GROUP_UPDATE
    )
    result = conflict.resolve_conflicts(
        "flowgate.default.0641",
        7,
        [{"path": "owned.txt", "content": "resolved"}],
        True,
    )
    assert result["result"]["status"] == "updated"
    assert db.closed == [(7, "done")]
    assert any("commit" in args for args in state["git"])
    assert state["locked"] is False


def test_tr_resolution_updates_review_state_before_unlock(monkeypatch, tmp_path):
    _root, _target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_TR_REVERT
    )
    original = conflict._set_tr_review_state

    def checked(merge_id, review_state):
        assert state["locked"] is True
        return original(merge_id, review_state)

    monkeypatch.setattr(conflict, "_set_tr_review_state", checked)
    result = conflict.resolve_conflicts(
        "flowgate.default.0641",
        7,
        [{"path": "owned.txt", "content": "resolved"}],
        True,
    )
    assert result["result"]["status"] == "resolved_pending_review"
    assert db.context["review_state"] == conflict.TR_CONFLICT_REVIEW_RESOLVED
    assert state["locked"] is False


def test_finalize_freeze_happens_under_same_resolution_lock_and_auto_approve_after_unlock(
    monkeypatch, tmp_path
):
    _root, _target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_MERGE
    )
    db.context = {"auto_authority": True}
    monkeypatch.setattr(
        conflict.merge_target,
        "resolve_session_target",
        lambda _session: SimpleNamespace(target_branch="main"),
    )

    def freeze(root, branch):
        assert state["locked"] is True
        return {
            "review_fingerprint": "sha256:test",
            "review_base_head": "base",
            "review_merge_head": "merge",
        }

    monkeypatch.setattr(git_service, "_freeze_commit_candidate", freeze)

    def approve(group_id, merge_id, **kwargs):
        assert state["locked"] is False
        return {"ok": True, "result": {"status": "approved-after-unlock"}}

    monkeypatch.setattr(git_service, "approve_merge_review", approve)
    result = conflict.resolve_conflicts(
        "flowgate.default.0641",
        7,
        [{"path": "owned.txt", "content": "resolved"}],
        True,
    )
    assert result["result"]["status"] == "approved-after-unlock"


def _install_shared_mutex(monkeypatch, root: Path, state: dict):
    mutex = threading.Lock()
    owner = {"holder": None}

    def acquire(project_id, holder, wait_sec=None):
        timeout = 3.0 if wait_sec is None else max(float(wait_sec), 0.0)
        ok = mutex.acquire(timeout=timeout)
        if ok:
            owner["holder"] = holder
            state["locked"] = True
        return ok

    def release(project_id, holder):
        assert owner["holder"] == holder
        owner["holder"] = None
        state["locked"] = False
        mutex.release()

    monkeypatch.setattr(git_service, "_acquire_lock", acquire)
    monkeypatch.setattr(git_service.db_git, "release_lock", release, raising=False)
    monkeypatch.setattr(tr2_precheck.db_git, "release_lock", release)
    monkeypatch.setattr(tr2_precheck.db_git, "get_lock",
                        lambda _project: (
                            {"holder": owner["holder"]} if owner["holder"] else None
                        ))
    monkeypatch.setattr(tr2_precheck, "_approval_root", lambda *_args: root)
    return mutex


def test_conflict_resolver_serializes_with_tr2_source_lock(monkeypatch, tmp_path):
    root, target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_TR_REVERT
    )
    _install_shared_mutex(monkeypatch, root, state)

    resolver_in_write = threading.Event()
    allow_resolver_finish = threading.Event()
    resolver_done = threading.Event()
    tr2_entered = threading.Event()
    failures = []

    def held_write(root_arg, path, target_arg, content):
        try:
            assert state["locked"] is True
            resolver_in_write.set()
            assert allow_resolver_finish.wait(3)
            target_arg.write_text(content, encoding="utf-8")
        except Exception as exc:
            failures.append(exc)
            raise

    monkeypatch.setattr(conflict, "_write_resolved_file", held_write)

    def run_resolver():
        try:
            conflict.resolve_conflicts(
                "flowgate.default.0641",
                7,
                [{"path": "owned.txt", "content": "resolved"}],
                False,
            )
        except Exception as exc:
            failures.append(exc)
        finally:
            resolver_done.set()

    def run_tr2_lock():
        try:
            with tr2_precheck.source_lock("flowgate", "flowgate.default.0641"):
                tr2_entered.set()
        except Exception as exc:
            failures.append(exc)

    resolver = threading.Thread(target=run_resolver)
    resolver.start()
    assert resolver_in_write.wait(3)

    approval = threading.Thread(target=run_tr2_lock)
    approval.start()

    # The TR2 approval lock must be the very same project mutex: it cannot enter
    # while conflict resolution is between source write and session update.
    assert not tr2_entered.wait(0.15)

    allow_resolver_finish.set()
    assert resolver_done.wait(3)
    assert tr2_entered.wait(3)
    resolver.join(3)
    approval.join(3)

    assert failures == []
    assert target.read_text(encoding="utf-8") == "resolved"
    assert db.resolved == ["owned.txt"]


def test_tr2_source_lock_serializes_before_conflict_resolver(monkeypatch, tmp_path):
    root, target, db, state = _install(
        monkeypatch, tmp_path, db_git.SESSION_KIND_TR_REVERT
    )
    _install_shared_mutex(monkeypatch, root, state)

    tr2_inside = threading.Event()
    release_tr2 = threading.Event()
    resolver_wrote = threading.Event()
    failures = []

    original_write = conflict._write_resolved_file

    def observed_write(root_arg, path, target_arg, content):
        try:
            assert state["locked"] is True
            resolver_wrote.set()
            return original_write(root_arg, path, target_arg, content)
        except Exception as exc:
            failures.append(exc)
            raise

    monkeypatch.setattr(conflict, "_write_resolved_file", observed_write)

    def hold_tr2():
        try:
            with tr2_precheck.source_lock("flowgate", "flowgate.default.0641"):
                tr2_inside.set()
                assert release_tr2.wait(3)
        except Exception as exc:
            failures.append(exc)

    def run_resolver():
        try:
            conflict.resolve_conflicts(
                "flowgate.default.0641",
                7,
                [{"path": "owned.txt", "content": "resolved"}],
                False,
            )
        except Exception as exc:
            failures.append(exc)

    approval = threading.Thread(target=hold_tr2)
    approval.start()
    assert tr2_inside.wait(3)

    resolver = threading.Thread(target=run_resolver)
    resolver.start()
    assert not resolver_wrote.wait(0.15)

    release_tr2.set()
    approval.join(3)
    resolver.join(3)

    assert failures == []
    assert resolver_wrote.is_set()
    assert target.read_text(encoding="utf-8") == "resolved"
    assert db.resolved == ["owned.txt"]


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return (proc.stdout or "").strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_followup_tr2_can_modify_and_commit_already_managed_path(monkeypatch, tmp_path):
    repo = tmp_path / "followup"
    repo.mkdir()
    _git(repo, "init")
    managed = repo / "managed.txt"
    managed.write_text("version one\n", encoding="utf-8")
    _git(repo, "add", "managed.txt")
    _git(
        repo, "-c", "user.name=FlowGate Test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "initial",
    )

    group_id = "flowgate.default.0641"
    succeeded = {
        "attempt_id": "prior-success",
        "state": "succeeded",
        "commit_json": {"paths": ["managed.txt"]},
    }
    monkeypatch.setattr(
        tr2_file_policy.db_attempts,
        "successful_by_group",
        lambda gid: [succeeded] if gid == group_id else [],
    )
    assert tr2_file_policy.is_managed(group_id, "managed.txt") is True

    # A later TR2 approval is NOT an ordinary editor mutation. If it accidentally
    # routes through the ordinary ownership guard this test must fail immediately.
    monkeypatch.setattr(
        tr2_file_policy,
        "general_source_mutation",
        lambda *_a, **_k: pytest.fail(
            "follow-up TR2 must not enter ordinary managed-file mutation guard"
        ),
    )

    owner = {"holder": None}

    def acquire(project_id, holder, wait_sec=None):
        assert owner["holder"] is None
        owner["holder"] = holder
        return True

    def release(project_id, holder):
        assert owner["holder"] == holder
        owner["holder"] = None

    monkeypatch.setattr(git_service, "_acquire_lock", acquire)
    monkeypatch.setattr(tr2_precheck.db_git, "release_lock", release)
    monkeypatch.setattr(
        tr2_precheck.db_git,
        "get_lock",
        lambda _project: (
            {"holder": owner["holder"]} if owner["holder"] else None
        ),
    )
    monkeypatch.setattr(tr2_precheck, "_approval_root", lambda *_args: repo)
    monkeypatch.setattr(tr2_approval_service.db_git, "get_config", lambda _p: {})
    recorded = {}
    monkeypatch.setattr(
        tr2_approval_service.db_attempts,
        "update",
        lambda attempt_id, **kw: recorded.update(kw) or {"attempt_id": attempt_id, **kw},
    )

    spec = {
        "termination": "ready_to_apply",
        "edits": [{
            "id": "e1",
            "kind": "edit",
            "file": "managed.txt",
            "anchor_old": "version one",
            "replacement_new": "version two",
            "rationale": "follow-up TR2 edit",
            "confidence": "high",
        }],
        "deferred": [],
        "gate": {"commands": [], "apply": False},
    }
    baseline = tr2_service.target_fingerprint(spec, repo)
    backup_root = tmp_path / "backup"
    backup_root.mkdir()

    with tr2_precheck.source_lock("flowgate", group_id) as locked:
        bundle = tr2_apply_adapter.adapter.create_backup(
            spec, repo, backup_root, baseline=baseline
        )
        applied = tr2_apply_adapter.adapter.apply_all(
            spec, repo, backup_root, bundle
        )
        assert applied["written"] == ["managed.txt"]
        assert managed.read_text(encoding="utf-8") == "version two\n"

        sha, paths = tr2_approval_service._commit_exact(
            locked,
            {
                "spec": spec,
                "document": {"doc_id": f"{group_id}.0013-TR2"},
            },
            "followup-attempt",
        )

    assert paths == ["managed.txt"]
    assert len(sha) == 40
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "show", "HEAD:managed.txt") == "version two"
    assert recorded["commit_json"]["paths"] == ["managed.txt"]
