"""Empty-remote base provisioning bootstrap (flowgate.default.0313 — B0001).

Bug: connecting a brand-new EMPTY remote (no commits, no base branch) to a fresh
project left the operator unable to do anything. `_provision_base_locked` judged
the pristine base slot "empty" and ran `git clone --branch <base> <url>`, which
cannot succeed against a remote that has no <base> branch — so the base checkout
was never created and every downstream op (worktree, base-commit, first push) was
blocked. The adopt path already handled an empty remote (unborn HEAD + snapshot),
but the empty-slot clone path did not — this pins the fix that closes that gap.

These tests exercise the two new helpers directly (no DB needed):
  - `_remote_is_empty`      — reachable AND no refs at all → True; errors → False
  - `_bootstrap_empty_remote` — init + seed README commit so the base branch is BORN
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_TEST_SCRATCH = Path(
    os.environ.get("FLOWGATE_TEST_SCRATCH", _SERVER_DIR / ".test-tmp-0313")
)
_TEST_SCRATCH.mkdir(parents=True, exist_ok=True)

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
    """Use FlowGate's per-run scratch instead of pytest's mode-0700 tmp_path.

    Python 3.14 applies POSIX-style mode 0700 to Windows directories. On the
    FlowGate host's managed workspace that can produce an unreadable ACL, so the
    executable spec keeps every case under the already-authorized run scratch.
    """
    path = _TEST_SCRATCH / f"case-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    # FlowGate owns and removes FLOWGATE_TEST_SCRATCH after the run. Keeping the
    # case directory avoids Windows cleanup failures on read-only git objects.
    return path


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


def _empty_bare(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "main", str(bare)])
    return bare


@needs_git
class TestRemoteIsEmpty:
    def test_true_for_branchless_bare(self, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        assert svc._remote_is_empty("https://example.test/empty.git", None, "") is True

    def test_false_when_refs_present(self, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args, 0, "abc123\trefs/heads/main\n", ""
            ),
        )
        assert svc._remote_is_empty("https://example.test/nonempty.git", None, "") is False

    def test_false_on_unreachable(self, monkeypatch):
        # An unreachable/errored remote must NOT read as empty, or a real failure
        # would be silently "bootstrapped" over its true cause.
        from modules.flow_gate.services import git_service as svc
        monkeypatch.setattr(
            svc, "_run_git",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 128, "", "fatal"),
        )
        assert svc._remote_is_empty("https://example.test/missing.git", None, "") is False


@needs_git
class TestBootstrapEmptyRemote:
    def test_creates_born_base_branch_with_seed_commit(self, scratch_path):
        from modules.flow_gate.services import git_service as svc
        bare = _empty_bare(scratch_path)
        base_root = scratch_path / "base"

        result = svc._bootstrap_empty_remote(
            base_root, "main", str(bare), None, "", "seedprj"
        )

        assert result["status"] == "ok", result
        assert result["snapshot_commit"]                       # a real commit exists
        assert (base_root / ".git").is_dir()
        assert (base_root / "README.md").is_file()             # the seed file
        # HEAD resolves to a commit on the base branch — the branch is BORN, which
        # is exactly what worktree creation needs to fork from.
        head = svc._run_git(
            ["rev-parse", "--verify", "refs/heads/main"], cwd=base_root
        )
        assert head.returncode == 0 and head.stdout.strip()
        # origin is wired but nothing was pushed (the seed rides the next finalize).
        url = svc._run_git(["remote", "get-url", "origin"], cwd=base_root)
        assert url.stdout.strip() == str(bare)
        remote_refs = _git([
            "--git-dir", str(bare), "for-each-ref", "--format=%(refname)", "refs/heads",
        ])
        assert "refs/heads/main" not in remote_refs

    def test_born_base_can_spawn_a_group_worktree(self, scratch_path):
        """The exact 0313 B0001 state: empty slot + empty remote must end in a base
        checkout a group worktree can fork from (previously impossible)."""
        from modules.flow_gate.services import git_service as svc
        bare = _empty_bare(scratch_path)
        base_root = scratch_path / "base2"

        svc._bootstrap_empty_remote(base_root, "main", str(bare), None, "", "wtprj")

        wt = scratch_path / "wt"
        add = svc._run_git(
            ["worktree", "add", "-b", "grp", str(wt), "main"], cwd=base_root
        )
        assert add.returncode == 0, add.stderr
        assert (wt / "README.md").is_file()
