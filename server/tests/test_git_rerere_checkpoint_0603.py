"""0603: rerere/checkpoint behavior on real Git conflicts and hold failure ordering."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from modules.flow_gate.services.git import rerere_checkpoint as rr  # noqa: E402
from modules.flow_gate.services.git.credentials import GitServiceError  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc


def conflict_repo(tmp_path: Path, count: int = 6) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "FlowGate Test")
    git(repo, "config", "user.email", "flowgate@example.invalid")
    for i in range(count):
        (repo / f"f{i}.txt").write_text("original\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "seed")
    git(repo, "checkout", "-b", "group")
    for i in range(count):
        group_text = "group unmatched\n" if i == 5 else "group\n"
        (repo / f"f{i}.txt").write_text(group_text, encoding="utf-8")
    git(repo, "commit", "-am", "group")
    git(repo, "checkout", "main")
    for i in range(count):
        (repo / f"f{i}.txt").write_text("base\n", encoding="utf-8")
    git(repo, "commit", "-am", "base")
    merge(repo)
    return repo


def merge(repo: Path) -> None:
    proc = git(repo, "-c", "rerere.enabled=false", "-c", "rerere.autoupdate=false",
               "merge", "--no-ff", "group", check=False)
    assert proc.returncode != 0
    assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()


def record_five(repo: Path) -> dict[str, dict]:
    rr._run(repo)
    ids = rr._ids(repo)
    assert len(ids) == 6
    stages = {f"f{i}.txt": rr._unmerged_stages(repo, f"f{i}.txt") for i in range(6)}
    saved = {}
    for i in range(5):
        path = f"f{i}.txt"
        (repo / path).write_text("base and group\n", encoding="utf-8")
        rr._run(repo)
        git(repo, "add", "--", path)
        digest = rr._postimage_hash(repo, ids[path])
        assert digest
        saved[path] = {
            "rerere_id": ids[path], "postimage_sha256": digest,
            "stages": stages[path],
            "resolution_sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest(),
        }
    return saved


def test_partial_record_abort_and_same_input_replay_is_unstaged(tmp_path):
    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    assert len(git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()) == 1
    git(repo, "merge", "--abort")
    assert not git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).stdout.strip()
    merge(repo)
    originals = {path: (repo / path).read_bytes() for path in saved}
    rr._run(repo)
    current = rr._ids(repo)
    for path, provenance in saved.items():
        # Git drops MERGE_RR entries as soon as a cached resolution is applied.
        assert current.get(path) in (None, provenance["rerere_id"])
        assert rr._postimage_hash(repo, provenance["rerere_id"]) == provenance["postimage_sha256"]
        assert (repo / path).read_text(encoding="utf-8") == "base and group\n"
        assert git(repo, "ls-files", "-u", "--", path).stdout  # autoupdate=false
        # A rejected candidate can be restored to the raw conflict without staging.
        (repo / path).write_bytes(originals[path])
        assert b"<<<<<<<" in (repo / path).read_bytes()


def test_unrelated_base_change_replays_but_changed_conflict_hunk_does_not(tmp_path):
    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    git(repo, "merge", "--abort")
    git(repo, "checkout", "-b", "other_group")
    (repo / "unrelated.txt").write_text("other group\n", encoding="utf-8")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-m", "other group")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "other_group", "-m", "merge other group")
    merge(repo)
    rr._run(repo)
    assert all((repo / path).read_text(encoding="utf-8") == "base and group\n"
               for path in saved)
    git(repo, "merge", "--abort")
    for path in ("f0.txt", "f1.txt"):
        (repo / path).write_text("other edit to conflict\n", encoding="utf-8")
    git(repo, "commit", "-am", "change two conflict hunks")
    merge(repo)
    rr._run(repo)
    for path in ("f0.txt", "f1.txt"):
        assert rr._ids(repo)[path] != saved[path]["rerere_id"]
        assert "<<<<<<<" in (repo / path).read_text(encoding="utf-8")
    assert all((repo / path).read_text(encoding="utf-8") == "base and group\n"
               for path in ("f2.txt", "f3.txt", "f4.txt"))
    assert "<<<<<<<" in (repo / "f5.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["group_update", "tr_revert", "tr_reapply"])
def test_hold_rejects_non_finalize_kind_before_git_or_persistence(monkeypatch, tmp_path, kind):
    from modules.flow_gate.services import git_service as gs
    repo = conflict_repo(tmp_path, count=1)
    session = {"merge_id": 1, "kind": kind, "group_id": "demo.default.1"}
    monkeypatch.setattr(gs, "_session_context", lambda *a, **k: (session, {}, "demo", repo))
    with pytest.raises(GitServiceError) as caught:
        rr.hold("demo.default.1", 1)
    assert caught.value.code == "hold_not_supported"
    assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()


def test_checkpoint_persist_failure_keeps_merge_open(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import merge_target
    repo = conflict_repo(tmp_path, count=1)
    rr._run(repo)
    ids = rr._ids(repo)
    stages = rr._unmerged_stages(repo, "f0.txt")
    (repo / "f0.txt").write_text("base and group\n", encoding="utf-8")
    rr._run(repo)
    git(repo, "add", "f0.txt")
    session = {"merge_id": 1, "kind": "merge", "group_id": "demo.default.1"}
    context = {"rerere_ids": ids, "rerere_stages": {
        "f0.txt": stages,
    }, "resolver_baseline": {
        "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
        "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
    }}
    db = gs.db_git
    monkeypatch.setattr(gs, "_session_context", lambda *a, **k: (session, {}, "demo", repo))
    monkeypatch.setattr(gs, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(db, "release_lock", lambda *a, **k: None)
    monkeypatch.setattr(db, "session_kind", lambda _s: "merge")
    monkeypatch.setattr(db, "session_files", lambda _m: [{"path": "f0.txt", "resolved": 1}])
    monkeypatch.setattr(db, "session_context", lambda _s: context)
    monkeypatch.setattr(db, "get_state", lambda _g: {"branch": "group"})
    monkeypatch.setattr(merge_target, "resolve_session_target", lambda _s: type("Target", (), {"target_branch": "main"})())
    monkeypatch.setattr(merge_target, "raise_if_not_workspace_owner", lambda _t: None)
    monkeypatch.setattr(db, "active_resolution_checkpoint", lambda *_a: None)
    monkeypatch.setattr(db, "create_resolution_checkpoint", lambda _d: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    with pytest.raises(RuntimeError, match="db unavailable"):
        rr.hold("demo.default.1", 1)
    assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()
    assert git(repo, "ls-files", "-u", "--", "f0.txt").stdout == ""


def test_migration_125_sqlite_persists_checkpoint_across_reopen(tmp_path):
    import sqlite3

    db_path = tmp_path / "checkpoint.db"
    migration = (SERVER / "sql/migrations/sqlite/125_git_resolution_checkpoint.sql").read_text(
        encoding="utf-8"
    )
    conn = sqlite3.connect(db_path)
    conn.executescript(migration)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(git_resolution_checkpoint)")}
    assert {"idx_git_resolution_checkpoint_owner", "idx_git_resolution_checkpoint_replay"} <= indexes
    conn.execute(
        "INSERT INTO git_resolution_checkpoint "
        "(checkpoint_id, project_id, group_id, source_merge_id, target_branch, "
        "source_branch, base_head, merge_head, resolved_paths, conflict_origins, "
        "provenance, state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("cp1", "demo", "demo.default.1", 1, "main", "group", "a" * 40, "b" * 40,
         '["f0.txt"]', "[]", '{"f0.txt":{"rerere_id":"abc"}}',
         "active", "2026-09-30", "2026-09-30"),
    )
    conn.commit()
    conn.close()

    restarted = sqlite3.connect(db_path)
    row = restarted.execute(
        "SELECT state, resolved_paths, provenance FROM git_resolution_checkpoint "
        "WHERE project_id = ? AND group_id = ? AND state = 'active'",
        ("demo", "demo.default.1"),
    ).fetchone()
    assert row == ("active", '["f0.txt"]', '{"f0.txt":{"rerere_id":"abc"}}')
    restarted.execute(
        "UPDATE git_resolution_checkpoint SET state = 'consumed', replay_merge_id = 2 "
        "WHERE checkpoint_id = 'cp1'"
    )
    restarted.commit()
    assert restarted.execute(
        "SELECT state, replay_merge_id FROM git_resolution_checkpoint WHERE checkpoint_id = 'cp1'"
    ).fetchone() == ("consumed", 2)
    restarted.close()

def test_start_session_reuses_only_ledger_paths_and_rejects_validator_failure(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import conflict, merge_target

    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "merge", "--abort")
    (repo / "other_group.txt").write_text("base advance\n", encoding="utf-8")
    git(repo, "add", "other_group.txt")
    git(repo, "commit", "-m", "other group base advance")
    merge(repo)
    new_head = git(repo, "rev-parse", "HEAD").stdout.strip()

    class FakeDB:
        def __init__(self):
            self.context = {
                "resolver_baseline": {
                    "base_head": new_head,
                    "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
                },
                "conflict_origins": [],
            }
            self.resolved = set()
            self.replay = None

        def get_session(self, merge_id):
            return {"merge_id": merge_id}

        def session_context(self, session):
            return self.context

        def session_files(self, merge_id):
            return [
                {"path": f"f{i}.txt", "resolved": int(merge_id == 1 and i < 5)}
                for i in range(6)
            ]

        def active_resolution_checkpoint(self, project_id, group_id):
            return {
                "checkpoint_id": "cp1", "project_id": "demo", "group_id": group_id,
                "target_branch": "main", "source_branch": "group",
                "base_head": old_head,
                "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
                "resolved_paths": list(saved), "provenance": saved,
                "conflict_origins": [], "source_merge_id": 1,
            }

        def get_state(self, group_id):
            return {"branch": "group"}

        def mark_file_resolved(self, merge_id, path):
            self.resolved.add(path)

        def remaining_conflicts(self, merge_id):
            return [f"f{i}.txt" for i in range(6) if f"f{i}.txt" not in self.resolved]

        def set_session_context(self, merge_id, context):
            self.context = context

        def set_resolution_checkpoint_replay(self, checkpoint_id, merge_id, result):
            self.replay = result

    db = FakeDB()
    monkeypatch.setattr(gs, "db_git", db)
    monkeypatch.setattr(gs, "_project_of_group", lambda _g: "demo")
    monkeypatch.setattr(merge_target, "resolve_session_target",
                        lambda _s: type("Target", (), {"target_branch": "main"})())
    original_validator = conflict._conflict_side_violations
    # Force one validator rejection without changing the real Git replay result.
    calls = iter([True] + [False] * 8)
    monkeypatch.setattr(
        conflict, "_conflict_side_violations",
        lambda raw, candidate: [{"side": "ours"}] if next(calls) else original_validator(raw, candidate),
    )
    result = rr.start_session("demo.default.1", 2, repo)
    assert result["classification"] == "REVALIDATE"
    assert len(result["reused_paths"]) == 4
    assert len(result["invalid_paths"]) == 1
    assert result["remaining_conflicts"] == 2
    assert len(db.resolved) == 4
    assert db.replay == result
    rejected = result["invalid_paths"][0]
    assert b"<<<<<<<" in (repo / rejected).read_bytes()
    assert git(repo, "ls-files", "-u", "--", rejected).stdout
    assert b"<<<<<<<" in (repo / "f5.txt").read_bytes()
    assert git(repo, "ls-files", "-u", "--", "f5.txt").stdout
    for path in result["reused_paths"]:
        assert (repo / path).read_text(encoding="utf-8") == "base and group\n"
        assert not git(repo, "ls-files", "-u", "--", path).stdout

def test_hold_persists_before_actual_abort_and_abort_does_not_checkpoint(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import approval_intent, conflict, merge_target

    repo = conflict_repo(tmp_path, count=1)
    rr._run(repo)
    ids = rr._ids(repo)
    stages = rr._unmerged_stages(repo, "f0.txt")
    (repo / "f0.txt").write_text("base and group\n", encoding="utf-8")
    rr._run(repo)
    git(repo, "add", "--", "f0.txt")
    session = {"merge_id": 1, "kind": "merge", "group_id": "demo.default.1"}
    context = {
        "rerere_ids": ids,
        "rerere_stages": {"f0.txt": stages},
        "resolver_baseline": {
            "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
        },
    }
    events = []

    class FakeDB:
        SESSION_KIND_MERGE = "merge"
        SESSION_KIND_GROUP_UPDATE = "group_update"
        TR_SESSION_KINDS = ()

        def session_kind(self, _session):
            return "merge"

        def session_files(self, _merge_id):
            return [{"path": "f0.txt", "resolved": 1}]

        def session_context(self, _session):
            return context

        def get_state(self, _group):
            return {"branch": "group"}

        def active_resolution_checkpoint(self, *_args):
            return None

        def create_resolution_checkpoint(self, data):
            assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()
            assert data["resolved_paths"] == ["f0.txt"]
            assert data["provenance"]["f0.txt"]["postimage_sha256"]
            events.append("persist")
            return {"checkpoint_id": "cp1"}

        def release_lock(self, *_args):
            pass

    db = FakeDB()
    monkeypatch.setattr(gs, "db_git", db)
    monkeypatch.setattr(gs, "_session_context",
                        lambda *a, **k: (session, {}, "demo", repo))
    monkeypatch.setattr(gs, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(gs, "_set_status",
                        lambda group, status: events.append(("status", status)))
    monkeypatch.setattr(merge_target, "resolve_session_target",
                        lambda _s: type("Target", (), {"target_branch": "main"})())
    monkeypatch.setattr(merge_target, "raise_if_not_workspace_owner", lambda _t: None)
    monkeypatch.setattr(merge_target, "close_session_attempt",
                        lambda *a, **k: events.append("close"))
    monkeypatch.setattr(approval_intent, "discard_intent", lambda _id: None)

    held = rr.hold("demo.default.1", 1)
    assert held["result"]["checkpoint_id"] == "cp1"
    assert held["result"]["preserved_paths"] == ["f0.txt"]
    assert events[0] == "persist"
    assert ("status", "waiting") in events
    assert not git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).stdout.strip()

    merge(repo)
    monkeypatch.setattr(db, "create_resolution_checkpoint",
                        lambda _data: pytest.fail("ordinary abort created checkpoint"))
    monkeypatch.setattr(db, "invalidate_resolution_checkpoint_for_merge",
                        lambda _id: events.append(("invalidate", _id)), raising=False)
    aborted = conflict.abort_merge("demo.default.1", 1)
    assert ("invalidate", 1) in events
    assert aborted["result"]["status"] == "waiting"
    assert not git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).stdout.strip()

class CheckpointDB:
    SESSION_KIND_MERGE = "merge"

    def __init__(self, repo, checkpoint, merge_id=2):
        self.repo = repo
        self.checkpoint = checkpoint
        self.merge_id = merge_id
        self.context = {
            "resolver_baseline": {
                "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
                "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
            },
            "conflict_origins": [],
        }
        self.resolved = set()
        self.replay = None
        self.created = None

    def get_session(self, merge_id):
        return {"merge_id": merge_id, "kind": "merge", "group_id": "demo.default.1"}

    def session_context(self, session):
        return self.context

    def set_session_context(self, merge_id, context):
        self.context = context

    def session_files(self, merge_id):
        return [
            {"path": f"f{i}.txt", "resolved": int(f"f{i}.txt" in self.resolved)}
            for i in range(6)
        ]

    def active_resolution_checkpoint(self, project_id, group_id):
        return self.checkpoint

    def get_state(self, group_id):
        return {"branch": "group"}

    def mark_file_resolved(self, merge_id, path):
        self.resolved.add(path)

    def remaining_conflicts(self, merge_id):
        return [f"f{i}.txt" for i in range(6) if f"f{i}.txt" not in self.resolved]

    def set_resolution_checkpoint_replay(self, checkpoint_id, merge_id, result):
        self.replay = result

    def invalidate_resolution_checkpoint(self, checkpoint_id):
        self.checkpoint = None

    def session_kind(self, session):
        return "merge"

    def create_resolution_checkpoint(self, data):
        self.created = data
        self.checkpoint = {"checkpoint_id": "cp2", **data}
        return self.checkpoint

    def release_lock(self, *_args):
        pass


def wire_checkpoint(monkeypatch, repo, checkpoint):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import merge_target
    db = CheckpointDB(repo, checkpoint)
    monkeypatch.setattr(gs, "db_git", db)
    monkeypatch.setattr(gs, "_project_of_group", lambda _g: "demo")
    monkeypatch.setattr(gs, "_session_context",
                        lambda *a, **k: (db.get_session(db.merge_id), {}, "demo", repo))
    monkeypatch.setattr(gs, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(gs, "_set_status", lambda *a, **k: None)
    monkeypatch.setattr(merge_target, "resolve_session_target",
                        lambda _s: type("Target", (), {"target_branch": "main"})())
    monkeypatch.setattr(merge_target, "raise_if_not_workspace_owner", lambda _t: None)
    monkeypatch.setattr(merge_target, "close_session_attempt", lambda *a, **k: None)
    return db


def checkpoint_data(repo, saved, base_head):
    return {
        "checkpoint_id": "cp1", "project_id": "demo",
        "group_id": "demo.default.1", "target_branch": "main",
        "source_branch": "group", "base_head": base_head,
        "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
        "resolved_paths": list(saved), "provenance": saved,
        "conflict_origins": [], "source_merge_id": 1,
    }


@pytest.mark.parametrize("move", ["rewind", "diverge"])
def test_base_rewind_or_diverge_never_restores(monkeypatch, tmp_path, move):
    repo = conflict_repo(tmp_path)
    git(repo, "merge", "--abort")
    (repo / "advance.txt").write_text("checkpoint base\\n", encoding="utf-8")
    git(repo, "add", "advance.txt")
    git(repo, "commit", "-m", "checkpoint base")
    merge(repo)
    saved = record_five(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    checkpoint = checkpoint_data(repo, saved, old_head)
    git(repo, "merge", "--abort")
    git(repo, "reset", "--hard", "HEAD~1")
    if move == "diverge":
        (repo / "diverged.txt").write_text("different base\\n", encoding="utf-8")
        git(repo, "add", "diverged.txt")
        git(repo, "commit", "-m", "different base")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, checkpoint)
    result = rr.start_session("demo.default.1", 2, repo)
    assert result["classification"] == "INVALID"
    assert result["reused_paths"] == []
    assert db.resolved == set()
    for path in saved:
        assert b"<<<<<<<" in (repo / path).read_bytes()
        assert git(repo, "ls-files", "-u", "--", path).stdout


def test_changed_repo_wide_cache_postimage_is_rejected(monkeypatch, tmp_path):
    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    checkpoint = checkpoint_data(repo, saved, old_head)
    rerere_id = saved["f0.txt"]["rerere_id"]
    conflict_id, _, variant = rerere_id.partition(".")
    cache = rr._git_path(repo, "rr-cache") / conflict_id
    postimage = cache / ("postimage." + variant if variant else "postimage")
    if not postimage.exists():
        postimage = cache / "postimage"
    postimage.write_text("another repo cache resolution\\n", encoding="utf-8")
    git(repo, "merge", "--abort")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, checkpoint)
    result = rr.start_session("demo.default.1", 2, repo)
    assert "f0.txt" in result["invalid_paths"]
    assert "f0.txt" not in db.resolved
    assert b"<<<<<<<" in (repo / "f0.txt").read_bytes()
    assert git(repo, "ls-files", "-u", "--", "f0.txt").stdout


def test_foreign_repo_cache_candidate_is_not_checkpoint_resolution(monkeypatch, tmp_path):
    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    checkpoint = checkpoint_data(repo, saved, old_head)
    git(repo, "merge", "--abort")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, checkpoint)
    original_run = rr._run

    def foreign_candidate(root):
        original_run(root)
        # MERGE_RR is already gone; a clean working tree alone must not authorize it.
        assert rr._ids(root).get("f0.txt") is None
        (root / "f0.txt").write_text("other cache resolution\\n", encoding="utf-8")

    monkeypatch.setattr(rr, "_run", foreign_candidate)
    result = rr.start_session("demo.default.1", 2, repo)
    assert "f0.txt" in result["invalid_paths"]
    assert "f0.txt" not in db.resolved
    assert b"<<<<<<<" in (repo / "f0.txt").read_bytes()
    assert git(repo, "ls-files", "-u", "--", "f0.txt").stdout


def test_replay_partial_resolution_rehold_preserves_old_and_new(monkeypatch, tmp_path):
    repo = conflict_repo(tmp_path)
    saved = record_five(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    checkpoint = checkpoint_data(repo, saved, old_head)
    git(repo, "merge", "--abort")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, checkpoint)
    first = rr.start_session("demo.default.1", 2, repo)
    assert set(first["reused_paths"]) == set(saved)
    assert "f5.txt" in db.context["rerere_ids"]
    (repo / "f5.txt").write_text("new group resolution\\n", encoding="utf-8")
    rr.record_resolution(2, repo, "f5.txt")
    git(repo, "add", "--", "f5.txt")
    db.mark_file_resolved(2, "f5.txt")
    held = rr.hold("demo.default.1", 2)
    assert set(held["result"]["preserved_paths"]) == {f"f{i}.txt" for i in range(6)}
    assert set(db.created["provenance"]) == {f"f{i}.txt" for i in range(6)}
    for path in saved:
        assert db.created["provenance"][path] == saved[path]
    merge(repo)
    db.merge_id = 3
    db.context = {
        "resolver_baseline": {
            "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
        },
        "conflict_origins": [],
    }
    db.resolved = set()
    second = rr.start_session("demo.default.1", 3, repo)
    assert set(second["reused_paths"]) == {f"f{i}.txt" for i in range(6)}
    assert second["remaining_conflicts"] == 0
    assert (repo / "f5.txt").read_text(encoding="utf-8") == "new group resolution\\n"


def test_existing_cache_candidate_can_be_replaced_and_held(monkeypatch, tmp_path):
    """An old repo-wide answer must not erase a user's new answer for the same input."""
    repo = conflict_repo(tmp_path, count=1)
    rr._run(repo)
    old_id = rr._ids(repo)["f0.txt"]
    (repo / "f0.txt").write_text("old cache answer\n", encoding="utf-8")
    rr._run(repo)
    git(repo, "add", "--", "f0.txt")
    git(repo, "merge", "--abort")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, None)
    first = rr.start_session("demo.default.1", 2, repo)
    assert first is None
    assert db.context["rerere_ids"]["f0.txt"] == old_id
    assert "f0.txt" in db.context["rerere_consumed_ids"]
    assert b"<<<<<<<" in (repo / "f0.txt").read_bytes()
    (repo / "f0.txt").write_text("new user answer\n", encoding="utf-8")
    rr.record_resolution(2, repo, "f0.txt")
    git(repo, "add", "--", "f0.txt")
    db.mark_file_resolved(2, "f0.txt")
    held = rr.hold("demo.default.1", 2)
    assert held["result"]["preserved_paths"] == ["f0.txt"]
    assert rr._postimage_hash(repo, old_id) == db.created["provenance"]["f0.txt"]["postimage_sha256"]
    merge(repo)
    db.merge_id = 3
    db.context = {
        "resolver_baseline": {
            "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
        }, "conflict_origins": [],
    }
    db.resolved = set()
    replay = rr.start_session("demo.default.1", 3, repo)
    assert replay["reused_paths"] == ["f0.txt"]
    assert (repo / "f0.txt").read_text(encoding="utf-8") == "new user answer\n"


def test_abort_failure_restores_previous_checkpoint(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import merge_target

    repo = conflict_repo(tmp_path, count=1)
    rr._run(repo)
    rerere_id = rr._ids(repo)["f0.txt"]
    stages = rr._unmerged_stages(repo, "f0.txt")
    (repo / "f0.txt").write_text("answer\n", encoding="utf-8")
    rr._run(repo)
    git(repo, "add", "--", "f0.txt")
    session = {"merge_id": 2, "kind": "merge", "group_id": "demo.default.1"}
    context = {"rerere_ids": {"f0.txt": rerere_id}, "rerere_stages": {"f0.txt": stages},
               "resolver_baseline": {"base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
                                     "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()}}
    state = {"cp-old": "active"}
    events = []

    class FakeDB:
        SESSION_KIND_MERGE = "merge"
        def session_kind(self, _s): return "merge"
        def session_files(self, _m): return [{"path": "f0.txt", "resolved": 1}]
        def session_context(self, _s): return context
        def get_state(self, _g): return {"branch": "group"}
        def active_resolution_checkpoint(self, *_a):
            return {"checkpoint_id": "cp-old"} if state["cp-old"] == "active" else None
        def create_resolution_checkpoint(self, _d):
            state["cp-old"] = "invalidated"
            state["cp-new"] = "active"
            events.append("persist")
            return {"checkpoint_id": "cp-new"}
        def rollback_resolution_checkpoint(self, new_id, previous_id):
            assert (new_id, previous_id) == ("cp-new", "cp-old")
            state["cp-new"] = "invalidated"
            state["cp-old"] = "active"
            events.append("rollback")
        def release_lock(self, *_a): pass

    db = FakeDB()
    monkeypatch.setattr(gs, "db_git", db)
    monkeypatch.setattr(gs, "_session_context", lambda *a, **k: (session, {}, "demo", repo))
    monkeypatch.setattr(gs, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(merge_target, "resolve_session_target", lambda _s: type("T", (), {"target_branch": "main"})())
    monkeypatch.setattr(merge_target, "raise_if_not_workspace_owner", lambda _s: None)
    original_git = gs._run_git
    def fail_abort(args, **kwargs):
        if args == ["merge", "--abort"]:
            return subprocess.CompletedProcess(args, 1, "", "injected abort failure")
        return original_git(args, **kwargs)
    monkeypatch.setattr(gs, "_run_git", fail_abort)
    with pytest.raises(GitServiceError, match="Merge abort failed"):
        rr.hold("demo.default.1", 2)
    assert events == ["persist", "rollback"]
    assert state == {"cp-old": "active", "cp-new": "invalidated"}
    assert git(repo, "rev-parse", "MERGE_HEAD").stdout.strip()


def test_replay_edit_submit_hold_replays_edited_answer(monkeypatch, tmp_path):
    """A consumed MERGE_RR ID survives an edited checkpoint replay."""
    repo = conflict_repo(tmp_path)
    rr._run(repo)
    ids = rr._ids(repo)
    stages = {path: rr._unmerged_stages(repo, path) for path in ("f0.txt", "f5.txt")}
    saved = {}
    for path, answer in (("f0.txt", "first answer\n"), ("f5.txt", "other answer\n")):
        (repo / path).write_text(answer, encoding="utf-8")
        rr._run(repo)
        git(repo, "add", "--", path)
        saved[path] = {
            "rerere_id": ids[path],
            "postimage_sha256": rr._postimage_hash(repo, ids[path]),
            "stages": stages[path],
            "resolution_sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest(),
        }
    old_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    checkpoint = checkpoint_data(repo, saved, old_head)
    git(repo, "merge", "--abort")
    merge(repo)
    db = wire_checkpoint(monkeypatch, repo, checkpoint)
    first = rr.start_session("demo.default.1", 2, repo)
    assert set(first["reused_paths"]) == set(saved)
    assert db.context["rerere_ids"]["f0.txt"] == saved["f0.txt"]["rerere_id"]
    assert "f0.txt" in db.context["rerere_consumed_ids"]

    (repo / "f0.txt").write_text("edited answer\n", encoding="utf-8")
    rr.record_resolution(2, repo, "f0.txt")
    git(repo, "add", "--", "f0.txt")
    edited_hash = hashlib.sha256((repo / "f0.txt").read_bytes()).hexdigest()
    assert db.context["replayed_provenance"]["f0.txt"]["resolution_sha256"] == edited_hash
    held = rr.hold("demo.default.1", 2)
    assert set(held["result"]["preserved_paths"]) == set(saved)
    assert db.created["provenance"]["f5.txt"] == saved["f5.txt"]
    assert db.created["provenance"]["f0.txt"]["resolution_sha256"] == edited_hash

    merge(repo)
    db.merge_id = 3
    db.context = {
        "resolver_baseline": {
            "base_head": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "merge_head": git(repo, "rev-parse", "MERGE_HEAD").stdout.strip(),
        }, "conflict_origins": [],
    }
    db.resolved = set()
    second = rr.start_session("demo.default.1", 3, repo)
    assert set(second["reused_paths"]) == set(saved)
    assert (repo / "f0.txt").read_text(encoding="utf-8") == "edited answer\n"
    assert (repo / "f5.txt").read_text(encoding="utf-8") == "other answer\n"