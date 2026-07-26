"""Single-file change view endpoints (flowgate.default.0326 — R0001 / NR0005 §4).

R0001: "파일 익스플로러에 파일이 변경됐다 까지는 나오는데 해당 파일이 어디가 어떻게
바뀌었는지 까지는 나오지 않는다." No endpoint returned the *content* of a change —
``git/status`` and ``git/groups/{gid}/changes`` return paths and status letters only.
``read_base_file_diff`` / ``read_group_file_diff`` add the missing half: the old and
new content of ONE path, which the client turns into a line diff (NR0005 안 b).

Like test_git_manual_fetch_0320, the git plumbing is monkeypatched so the branch logic
runs identically on every host (the real-git E2E suite skips on git-for-Windows). The
working-tree side is exercised against REAL temp directories, because its containment
guard is the part that must never be faked away.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-file-diff-0326-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _blobs(monkeypatch, mapping: dict[tuple[str, str], bytes]) -> None:
    """Serve (commit, path) -> bytes through the ls-tree/cat-file trio."""
    shas = {f"sha-{commit}-{path}": data for (commit, path), data in mapping.items()}

    def fake_ls_tree_entry(base_root, commit, path):
        key = (commit, path)
        return ("blob", f"sha-{commit}-{path}") if key in mapping else None

    monkeypatch.setattr(svc, "_ls_tree_entry", fake_ls_tree_entry)
    monkeypatch.setattr(svc, "_cat_file_size", lambda root, sha: len(shas.get(sha, b"")))
    monkeypatch.setattr(
        svc, "_cat_file_blob_head", lambda root, sha, limit: shas.get(sha, b"")[:limit]
    )


def _base_env(monkeypatch, tmp_path: Path, *, run_git=None) -> Path:
    """Provisioned base checkout at tmp_path/base with git integration enabled."""
    base_root = tmp_path / "base"
    (base_root / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        svc, "_require_enabled_config", lambda pid: {"base_branch": "main", "enabled": True}
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc, "src_root", lambda name, branch: base_root)
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(
        svc, "_run_git",
        run_git or (lambda args, **kw: _Proc(0, "headcommit\n")),
    )
    return base_root


# ── base checkout: HEAD blob vs working tree ─────────────────────────────────

def test_base_diff_returns_both_sides_of_a_modification(monkeypatch, tmp_path):
    base_root = _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {("headcommit", "app/main.py"): b"old line\n"})
    target = base_root / "app" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"new line\n")

    data = svc.read_base_file_diff("p1", "app/main.py")["data"]

    assert data["status"] == "M"
    assert data["commit"] == "headcommit"
    assert data["old"]["content"] == "old line\n"
    assert data["new"]["content"] == "new line\n"
    assert data["old"]["exists"] and data["new"]["exists"]


def test_base_diff_marks_an_untracked_file_as_added(monkeypatch, tmp_path):
    base_root = _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})
    (base_root / "new.txt").write_bytes(b"hello\n")

    data = svc.read_base_file_diff("p1", "new.txt")["data"]

    assert data["status"] == "A"
    assert data["old"] == {
        "exists": False, "binary": False, "truncated": False, "size": 0, "content": None
    }
    assert data["new"]["content"] == "hello\n"


def test_base_diff_marks_a_missing_working_file_as_deleted(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {("headcommit", "gone.txt"): b"bye\n"})

    data = svc.read_base_file_diff("p1", "gone.txt")["data"]

    assert data["status"] == "D"
    assert data["old"]["content"] == "bye\n"
    assert data["new"]["exists"] is False


def test_base_diff_404s_when_neither_side_has_the_path(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})

    with pytest.raises(GitServiceError) as exc:
        svc.read_base_file_diff("p1", "nowhere.txt")
    assert exc.value.status == 404


def test_base_diff_treats_an_empty_repository_as_all_added(monkeypatch, tmp_path):
    # `rev-parse --verify HEAD` fails before the first commit; that is not an error,
    # every working file simply reads as added.
    base_root = _base_env(monkeypatch, tmp_path, run_git=lambda args, **kw: _Proc(1, "", "fatal"))
    _blobs(monkeypatch, {})
    (base_root / "first.txt").write_bytes(b"x\n")

    data = svc.read_base_file_diff("p1", "first.txt")["data"]

    assert data["status"] == "A"
    assert data["commit"] is None


def test_base_diff_hides_the_paths_the_file_tree_hides(monkeypatch, tmp_path):
    base_root = _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})
    (base_root / ".env").write_bytes(b"SECRET=1\n")
    (base_root / "app.db").write_bytes(b"\x00binary")

    for hidden in (".env", "app.db"):
        with pytest.raises(GitServiceError) as exc:
            svc.read_base_file_diff("p1", hidden)
        assert exc.value.status == 404


def test_base_diff_rejects_traversal_and_absolute_paths(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})

    for bad in ("../secret", "/etc/passwd", "C:/Windows/win.ini", ""):
        with pytest.raises(GitServiceError) as exc:
            svc.read_base_file_diff("p1", bad)
        assert exc.value.status == 400


def test_base_diff_flags_binary_and_truncated_sides(monkeypatch, tmp_path):
    base_root = _base_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {("headcommit", "blob.bin"): b"pre\x00post"})
    (base_root / "blob.bin").write_bytes(b"plain text\n")
    data = svc.read_base_file_diff("p1", "blob.bin")["data"]
    assert data["old"]["binary"] is True and data["old"]["content"] is None
    assert data["new"]["binary"] is False

    monkeypatch.setattr(svc, "BLOB_MAX_RETURN_BYTES", 8)
    (base_root / "big.txt").write_bytes(b"0123456789")
    _blobs(monkeypatch, {})
    big = svc.read_base_file_diff("p1", "big.txt")["data"]
    assert big["new"]["truncated"] is True
    assert big["new"]["content"] == "01234567"
    assert big["new"]["size"] == 10


def test_base_diff_requires_a_provisioned_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(
        svc, "_require_enabled_config", lambda pid: {"base_branch": "main", "enabled": True}
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc, "src_root", lambda name, branch: tmp_path / "missing")

    with pytest.raises(GitServiceError) as exc:
        svc.read_base_file_diff("p1", "a.txt")
    assert exc.value.status == 409


# ── group branch: merge-base blob vs worktree ────────────────────────────────

def _group_env(monkeypatch, tmp_path: Path, *, worktree: bool = True) -> tuple[Path, Path | None]:
    base_root = tmp_path / "base"
    base_root.mkdir(parents=True, exist_ok=True)
    wt_path = tmp_path / "wt"
    if worktree:
        wt_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc, "resolve_group_ref", lambda pid, gid: (base_root, "fg_0326", "groupcommit")
    )
    monkeypatch.setattr(svc.db_git, "get_config", lambda pid: {"base_branch": "main"})
    monkeypatch.setattr(svc, "_run_git", lambda args, **kw: _Proc(0, "mergebase\n"))
    monkeypatch.setattr(
        svc, "_group_worktree_path",
        lambda pid, gid, branch: wt_path if worktree else None,
    )
    return base_root, (wt_path if worktree else None)


def test_group_diff_compares_merge_base_against_the_live_worktree(monkeypatch, tmp_path):
    _, wt_path = _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {
        ("mergebase", "a.py"): b"base\n",
        # The committed branch tip is deliberately different from the worktree:
        # read_group_changes measures the WORKTREE, so the diff must too, or an
        # uncommitted edit would open with an empty diff.
        ("groupcommit", "a.py"): b"committed\n",
    })
    (wt_path / "a.py").write_bytes(b"working\n")

    data = svc.read_group_file_diff("p1", "g1", "a.py")["data"]

    assert data["status"] == "M"
    assert data["merge_base"] == "mergebase"
    assert data["old"]["content"] == "base\n"
    assert data["new"]["content"] == "working\n"


def test_group_diff_reads_untracked_new_files_from_the_worktree(monkeypatch, tmp_path):
    _, wt_path = _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})
    (wt_path / "brand_new.txt").write_bytes(b"fresh\n")

    data = svc.read_group_file_diff("p1", "g1", "brand_new.txt")["data"]

    assert data["status"] == "A"
    assert data["new"]["content"] == "fresh\n"


def test_group_diff_falls_back_to_the_commit_when_the_worktree_is_gone(monkeypatch, tmp_path):
    _group_env(monkeypatch, tmp_path, worktree=False)
    _blobs(monkeypatch, {
        ("mergebase", "a.py"): b"base\n",
        ("groupcommit", "a.py"): b"committed\n",
    })

    data = svc.read_group_file_diff("p1", "g1", "a.py")["data"]

    assert data["status"] == "M"
    assert data["new"]["content"] == "committed\n"


def test_group_diff_reports_a_worktree_deletion(monkeypatch, tmp_path):
    _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {("mergebase", "a.py"): b"base\n"})

    data = svc.read_group_file_diff("p1", "g1", "a.py")["data"]

    assert data["status"] == "D"
    assert data["new"]["exists"] is False


def test_group_diff_rejects_a_non_sha_ref_pin(monkeypatch, tmp_path):
    _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})

    with pytest.raises(GitServiceError) as exc:
        svc.read_group_file_diff("p1", "g1", "a.py", "main")
    assert exc.value.status == 400


def test_group_diff_500s_when_no_merge_base_exists(monkeypatch, tmp_path):
    _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})
    monkeypatch.setattr(svc, "_run_git", lambda args, **kw: _Proc(1, "", "no merge base"))

    with pytest.raises(GitServiceError) as exc:
        svc.read_group_file_diff("p1", "g1", "a.py")
    assert exc.value.status == 500


def test_group_diff_cannot_escape_the_worktree(monkeypatch, tmp_path):
    _, wt_path = _group_env(monkeypatch, tmp_path)
    _blobs(monkeypatch, {})
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")

    # '..' is rejected outright; a nested path that resolves outside is caught by the
    # containment check and reads as "no new side" rather than leaking the file.
    with pytest.raises(GitServiceError) as exc:
        svc.read_group_file_diff("p1", "g1", "../outside.txt")
    assert exc.value.status == 400
    assert svc._diff_side_from_disk(wt_path, "sub/../../outside.txt")["exists"] is False
