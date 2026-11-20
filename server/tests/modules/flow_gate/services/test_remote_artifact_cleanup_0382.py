"""Remote deletion tooling fixes (0382 B0001 §3 / NR0003 proposal 5).

The worker trying to remove 261 artifacts encountered three obstacles:

* remove handled only one file, returning 404 for directories and requiring 261 calls.
* Read-only Git objects caused os.remove to raise PermissionError.
* The error became a misleading temporary-server-failure 503, prompting three futile retries.

Pin each correction. Allow recursive deletion only for paths recognized as tool artifacts
(the safe option in proposal 5-c), using the same rules as the UI and submission validator.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import remote_tool_service as rts  # noqa: E402

_SCRATCH = session_scratch("remote-cleanup-0382")


@pytest.fixture
def root(monkeypatch):
    # Filesystem unit tests use scratch paths, without a document database.
    from contextlib import nullcontext
    monkeypatch.setattr(rts.document_write_ports, "write_paths", lambda *a, **kw: nullcontext())
    path = _SCRATCH / f"root-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    yield path
    remove_tree(path)


# ── Delete read-only files too (proposal 5-a) ──

def test_remove_clears_the_readonly_bit_and_succeeds(root):
    target = root / "locked.txt"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)

    extra, _ = rts._exec_remove({"path": "locked.txt"}, root)

    assert extra["removed"] is True
    assert not target.exists()


# ── Remove a whole directory (proposal 5-c) ──

def test_nonempty_directory_without_recursive_keeps_the_old_404(root):
    (root / "server" / ".test-tmp-0313").mkdir(parents=True)
    (root / "server" / ".test-tmp-0313" / "keep.txt").write_text("keep")
    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "server/.test-tmp-0313"}, root)
    assert caught.value.status == 404


def test_recursive_removes_a_tool_artifact_tree_and_counts_it(root):
    debris = root / "server" / ".test-tmp-0313" / "case-a" / ".git" / "objects"
    debris.mkdir(parents=True)
    for index in range(3):
        blob = debris / f"obj{index}"
        blob.write_bytes(b"z")
        os.chmod(blob, stat.S_IREAD)  # Read-only like a Git object

    extra, _ = rts._exec_remove(
        {"path": "server/.test-tmp-0313", "recursive": True}, root
    )

    assert extra["removed"] is True
    assert extra["recursive"] is True
    assert extra["removed_file_count"] == 3
    assert not (root / "server" / ".test-tmp-0313").exists()


@pytest.mark.parametrize("recursive", [False, True])
def test_empty_source_directory_can_be_removed(root, recursive):
    target = root / "client_gui" / "src" / "attachments"
    target.mkdir(parents=True)
    result, _ = rts._exec_remove({"path": "client_gui/src/attachments", "recursive": recursive}, root)
    assert result["removed"] and result["removed_file_count"] == 0
    assert not target.exists()
    assert target.parent.exists()


@pytest.mark.parametrize("path", [".", ".git", ".git/objects"])
def test_empty_root_and_git_remain_protected(root, path):
    target = root / path
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(rts._OpError):
        rts._exec_remove({"path": path}, root)
    assert target.exists()


def test_empty_directory_race_preserves_new_file(root, monkeypatch):
    target = root / "src"
    target.mkdir()
    real_rmdir = Path.rmdir
    def raced(path):
        if path == target:
            (path / "new.py").write_text("keep")
        return real_rmdir(path)
    with monkeypatch.context() as m:
        m.setattr(Path, "rmdir", raced)
        with pytest.raises(rts._OpError) as caught:
            rts._exec_remove({"path": "src", "recursive": True}, root)
    assert caught.value.status == 409
    assert (target / "new.py").read_text() == "keep"


def test_empty_directory_still_respects_document_write_guard(root, monkeypatch):
    from fastapi import HTTPException
    target = root / "src"
    target.mkdir()
    def refuse(paths, *, trees=()):
        assert list(trees) == [target]
        raise HTTPException(409, "card_document_edit_required")
    monkeypatch.setattr(rts.document_write_ports, "write_paths", refuse)
    with pytest.raises(HTTPException):
        rts._exec_remove({"path": "src"}, root)
    assert target.exists()


def test_directory_alias_cannot_delete_target(root):
    target = root / "src"
    target.mkdir()
    alias = root / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(rts._OpError):
        rts._exec_remove({"path": "alias"}, root)
    assert target.exists()


def test_recursive_refuses_a_real_source_directory(root):
    (root / "server" / "modules").mkdir(parents=True)
    (root / "server" / "modules" / "app.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "server/modules", "recursive": True}, root)

    assert caught.value.status == 422
    assert caught.value.details["reason"] == "not_tool_artifact"
    # A rejected request deletes nothing.
    assert (root / "server" / "modules" / "app.py").exists()


@pytest.mark.parametrize("path", [".", "./", "./server", ".git", "server/./modules"])
def test_recursive_normalizes_dot_segments_and_protects_root_source_and_git(root, path):
    (root / "server" / "modules").mkdir(parents=True)
    source = root / "server" / "modules" / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    (root / ".git").mkdir()
    git_config = root / ".git" / "config"
    git_config.write_text("[core]\n", encoding="utf-8")

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": path, "recursive": True}, root)

    assert caught.value.status == 422
    assert caught.value.details["reason"] == "not_tool_artifact"
    assert source.exists(), f"{path!r} must not delete source"
    assert git_config.exists(), f"{path!r} must not delete repository metadata"


def test_recursive_must_be_a_boolean_and_string_false_never_recurses(root):
    (root / "server" / ".test-tmp-0313").mkdir(parents=True)
    (root / "server" / ".test-tmp-0313" / "keep.txt").write_text("keep")

    with pytest.raises(rts._OpError) as caught:
        rts._validate_required(
            "remove", {"path": "server/.test-tmp-0313", "recursive": "false"}
        )
    assert caught.value.status == 422

    # Direct internal execution must not treat a string as boolean true.
    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove(
            {"path": "server/.test-tmp-0313", "recursive": "false"}, root
        )
    assert caught.value.status == 404
    assert (root / "server" / ".test-tmp-0313").exists()


# ── Distinguish retryable and permanent failures (proposal 5-b) ──

def test_locked_path_answers_409_not_a_temporary_503(root, monkeypatch):
    target = root / "held.txt"
    target.write_text("x", encoding="utf-8")

    def _always_denied(_path):
        raise PermissionError(13, "held by another process")

    monkeypatch.setattr(rts.os, "remove", _always_denied)
    monkeypatch.setattr(rts.os, "chmod", lambda *_a, **_kw: None)

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "held.txt"}, root)

    assert caught.value.status == 409, "503(일시적)로 답하면 AI 가 헛되이 재시도한다"
    assert caught.value.details["reason"] == "path_locked"


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("reason", ["path_locked", "not_tool_artifact"])
def test_new_reasons_have_a_message_in_every_locale(reason, locale):
    assert rts._CUSTOM_ERROR_MESSAGES[reason][locale].strip()


def test_locked_message_says_retrying_will_not_help():
    exc = rts._OpError(409, details={"reason": "path_locked"})
    assert "재시도해도" in rts._op_error_message(exc, "ko")
    assert "not help" in rts._op_error_message(exc, "en")


def test_not_tool_artifact_message_names_the_path():
    exc = rts._OpError(422, details={"reason": "not_tool_artifact", "path": "server/modules"})
    assert "server/modules" in rts._op_error_message(exc, "ko")
