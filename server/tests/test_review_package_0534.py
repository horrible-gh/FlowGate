from __future__ import annotations

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from modules.flow_gate.services import git_service
from modules.flow_gate.services import review_package_service as rps
from modules.flow_gate.services.git_service import GitServiceError


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "flowgate_default_0534")
    return root, base


def patch_context(monkeypatch, root: Path, marker: str | None, *, registered=True):
    state = {
        "project_id": "flowgate", "branch": "flowgate_default_0534",
        "worktree_registered": 1 if registered else 0,
        "initial_source_sync_sha": marker, "initial_source_sync_at": "2026-01-01T00:00:00Z" if marker else None,
    }
    monkeypatch.setattr(rps.db_git, "get_state", lambda group_id: state)
    monkeypatch.setattr(rps.db_git, "get_config", lambda project_id: {"enabled": 1, "base_branch": "main"})
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda project_id, group_id: (root, "worktree"))
    monkeypatch.setattr(rps.tr_scope_service, "resolve_stage", lambda project_id: "warn")
    monkeypatch.setattr(rps.tr_scope_service, "group_declared_paths", lambda group_id, exclude_doc_id=None: [])
    monkeypatch.setattr(rps.db_documents, "get_by_id", lambda doc_id: {
        "doc_id": doc_id, "group_id": "flowgate.default.0534", "meta": {"tr_scope": {"reported": {"items": []}}},
    })


def unzip(package: rps.ReviewPackage):
    return zipfile.ZipFile(io.BytesIO(package.content))


def test_legacy_marker_is_metadata_only_and_full_effective_delta_is_exported(tmp_path, monkeypatch):
    root, base = make_repo(tmp_path)
    (root / "committed.txt").write_text("prior TR\n", encoding="utf-8")
    git(root, "add", "committed.txt")
    git(root, "commit", "-m", "prior TR")
    legacy_backfill = git(root, "rev-parse", "HEAD")
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(root, "add", "staged.txt")
    (root / "base.txt").write_text("unstaged\n", encoding="utf-8")
    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    patch_context(monkeypatch, root, legacy_backfill)

    package = rps.build_review_package("flowgate", "flowgate.default.0534", "flowgate.default.0534.0001-TR")
    with unzip(package) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        patch = archive.read("diff.patch").decode()
        names = archive.namelist()
        assert metadata["base_sha"] == base
        assert metadata["initial_source_sync_sha"] == legacy_backfill
        assert "committed.txt" in patch
        assert "staged.txt" in patch
        assert "base.txt" in patch
        assert archive.read("untracked/untracked.txt").replace(b"\r\n", b"\n") == b"untracked\n"
        assert "untracked.txt" in archive.read("changed-files.txt").decode()
        assert not any(name.startswith("/") or ".." in Path(name).parts for name in names)


@pytest.mark.parametrize("marker", [None, "different-provenance-marker"])
def test_empty_delta_is_a_valid_evidence_package(tmp_path, monkeypatch, marker):
    root, base = make_repo(tmp_path)
    patch_context(monkeypatch, root, marker)
    package = rps.build_review_package("flowgate", "flowgate.default.0534")
    with unzip(package) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["base_sha"] == base
        assert metadata["has_changes"] is False
        assert metadata["changed_files"] == []
        assert archive.read("diff.patch") == b""
        assert archive.read("changed-files.txt") == b"No changes.\n"
        assert b"No worktree changes detected" in archive.read("report.md")
        assert {"metadata.json", "changed-files.txt", "diff.patch", "report.md"} <= set(archive.namelist())


def test_binary_large_and_policy_files_are_reported_not_packed(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    (root / "binary.bin").write_bytes(b"x\x00y")
    (root / "large.txt").write_bytes(b"x" * (rps.MAX_INCLUDED_FILE_BYTES + 1))
    (root / "debug.log").write_text("generated", encoding="utf-8")
    patch_context(monkeypatch, root, None)
    package = rps.build_review_package("flowgate", "flowgate.default.0534")
    with unzip(package) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        reasons = {item["path"]: item["reason"] for item in metadata["excluded_files"]}
        assert reasons == {"binary.bin": "binary", "debug.log": "excluded_by_policy", "large.txt": "too_large"}
        assert not any(name.startswith("untracked/") for name in archive.namelist())
        report = archive.read("report.md").decode()
        assert "binary" in report and "too_large" in report and "excluded_by_policy" in report


def test_symlink_outside_root_is_excluded(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    patch_context(monkeypatch, root, None)
    package = rps.build_review_package("flowgate", "flowgate.default.0534")
    with unzip(package) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert {"path": "outside-link.txt", "reason": "symlink_outside_root"} in metadata["excluded_files"]
        assert "untracked/outside-link.txt" not in archive.namelist()
        assert b"secret" not in package.content


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/drive", ".git/config", "safe/../escape"])
def test_path_traversal_and_git_paths_are_rejected(path):
    assert rps._safe_relative_path(path) is None


def test_worktree_missing_is_a_structured_error(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    patch_context(monkeypatch, root, None, registered=False)
    with pytest.raises(GitServiceError) as raised:
        rps.build_review_package("flowgate", "flowgate.default.0534")
    assert raised.value.code == "worktree_missing"
    assert raised.value.status == 409