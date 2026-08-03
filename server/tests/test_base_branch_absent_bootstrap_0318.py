"""Base-branch-absent provisioning bootstrap (flowgate.default.0318 — B0001).

Bug: connecting a remote that DOES advertise refs but NOT the configured base
branch (a default-branch name mismatch — remote `master` vs base `main`, or a new
repository initialized on another branch) left the operator unable to do anything.
`_provision_base_locked` judged the pristine base slot "empty", `_remote_is_empty`
read False (refs are present), so it ran `git clone --branch <base>` — which fatals
with "Remote branch <base> not found in upstream origin". No base checkout, no seed
README, every downstream op (worktree, base-commit, first push) blocked.

The 0313 fix only closed the *fully bare* remote case (zero refs). This pins the
0318 generalization: route ANY remote lacking `refs/heads/<base>` to the seed
bootstrap so the base branch is BORN with a README, exactly like the empty case.

These tests exercise the new detection helper directly (no DB needed):
  - `_remote_lacks_base_branch` — reachable AND no refs/heads/<base> → True; a
    present base → False; errors → False (never mask a real failure as bootstrap).
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

_SERVER_DIR = Path(__file__).resolve().parents[1]
# 0382 B0001: 기본값이 `_SERVER_DIR / ".test-tmp-0318"` 이었다. FLOWGATE_TEST_SCRATCH 는
# FlowGate 가 직접 돌릴 때만 채워지므로, 사람이나 AI 가 손으로 pytest 를 돌리면 저장소
# **안에** 폴더가 생겼고(수집만 해도 생긴다 — 이 줄은 모듈 최상단이다) 마무리 커밋의
# `git add -A` 가 그걸 통째로 삼켰다. 기본값은 이제 저장소 밖이다.
_TEST_SCRATCH = session_scratch("0318")

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"K" * 32).decode()
os.environ.setdefault("FLOWGATE_STORAGE_DIR", str(_TEST_SCRATCH / "storage"))

import sys

sys.path.insert(0, str(_SERVER_DIR))

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")


@pytest.fixture
def scratch_path():
    """Per-case directory under this spec's scratch root — never inside the repo
    (mirrors test_empty_remote_bootstrap_0313 — Python 3.14 / Windows ACL, and the
    0382 제안 7 cleanup that makes keeping the directory unnecessary)."""
    path = _TEST_SCRATCH / f"case-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    yield path
    remove_tree(path)


def _git(args, cwd=None):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def _bare_with_branch(tmp_path: Path, branch: str) -> Path:
    """A non-bare remote that has one commit on `branch` and no others."""
    bare = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", branch, str(bare)])
    seed = tmp_path / "seedwt"
    _git(["init", "-b", branch, str(seed)])
    (seed / "app.txt").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seed)
    _git(["commit", "-m", "seed"], cwd=seed)
    # A raw ``C:\...`` remote is parsed as scp syntax by Git for Windows, which
    # invokes ssh instead of the local transport. A file URI is unambiguous on
    # Windows and remains portable on POSIX hosts.
    _git(["remote", "add", "origin", bare.resolve().as_uri()], cwd=seed)
    _git(["push", "origin", branch], cwd=seed)
    return bare


@needs_git
class TestRemoteLacksBaseBranch:
    def test_true_when_refs_present_but_base_absent(self, monkeypatch):
        # ls-remote for refs/heads/main returns nothing because only `master` exists.
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        assert svc._remote_lacks_base_branch(
            "https://example.test/mismatch.git", None, "", "main"
        ) is True

    def test_false_when_base_present(self, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args, 0, "abc123\trefs/heads/main\n", ""
            ),
        )
        assert svc._remote_lacks_base_branch(
            "https://example.test/hasmain.git", None, "", "main"
        ) is False

    def test_false_on_unreachable(self, monkeypatch):
        # An errored remote must NOT read as "needs bootstrap", or a real auth/network
        # failure would be silently seeded over instead of surfacing its true cause.
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 128, "", "fatal"),
        )
        assert svc._remote_lacks_base_branch(
            "https://example.test/missing.git", None, "", "main"
        ) is False

    def test_real_remote_default_branch_mismatch(self, scratch_path):
        """Integration: a remote whose only branch is `master` lacks base `main`."""
        from modules.flow_gate.services import git_service as svc
        bare = _bare_with_branch(scratch_path, "master")
        remote_url = bare.resolve().as_uri()
        assert svc._remote_lacks_base_branch(remote_url, None, "", "main") is True
        # sanity: the branch that DOES exist is reported present
        assert svc._remote_lacks_base_branch(remote_url, None, "", "master") is False


@needs_git
class TestBootstrapAfterMismatch:
    def test_mismatch_remote_bootstraps_born_base_with_readme(self, scratch_path):
        """The 0318 end state: refs-present-but-base-absent must bootstrap a born
        base checkout (README seed) a group worktree can fork from — previously a
        `git clone --branch main` fatal."""
        from modules.flow_gate.services import git_service as svc
        bare = _bare_with_branch(scratch_path, "master")
        remote_url = bare.resolve().as_uri()
        base_root = scratch_path / "base"

        assert svc._remote_lacks_base_branch(remote_url, None, "", "main") is True
        result = svc._bootstrap_empty_remote(
            base_root, "main", remote_url, None, "", "mismprj"
        )

        assert result["status"] == "ok", result
        assert (base_root / "README.md").is_file()
        head = svc._run_git(
            ["rev-parse", "--verify", "refs/heads/main"], cwd=base_root
        )
        assert head.returncode == 0 and head.stdout.strip()
        wt = scratch_path / "wt"
        add = svc._run_git(
            ["worktree", "add", "-b", "grp", str(wt), "main"], cwd=base_root
        )
        assert add.returncode == 0, add.stderr
        assert (wt / "README.md").is_file()
