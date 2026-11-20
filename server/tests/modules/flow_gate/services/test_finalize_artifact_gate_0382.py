"""Prevent final commits from absorbing test artifacts (0382 B0001 / NR0003 proposals 1 and 3).

Commit 0f502ce changed 266 files, including 261 under server/.test-tmp-*.
Finalize used unfiltered git add -A to absorb a dirty worktree while the UI hid those
paths, leaving users no opportunity to notice them.

Pin three contracts:

1. Exclude newly created artifacts from absorption commits.
2. Include deletion of tracked artifacts so the previously committed 261 files can
   still be removed. Filtering deletions would preserve them permanently.
3. Return the excluded list for display instead of dropping it silently.

The UI and validation now share exclusion rules, resolving their earlier contradiction:
paths hidden in the UI could previously block submission.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-artifact-gate-0382-")
)

_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import path_exclusion_rules as rules  # noqa: E402
from modules.flow_gate.services import tr_scope_service as trs  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("artifact-gate-0382")


def _git(args, cwd):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def repo():
    """A real Git worktree outside the repository, following the 0382 prevention rule itself."""
    path = _SCRATCH / f"repo-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], path)
    (path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "base"], path)
    yield path
    remove_tree(path)


def _committed_paths(repo: Path) -> set[str]:
    return {line for line in _git(["ls-files"], repo).splitlines() if line}


# ── Contract 1: exclude new artifacts from the final absorption commit ──

@needs_git
def test_absorb_commits_real_work_but_not_tool_debris(repo):
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0313" / "storage"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: real work", None)

    tracked = _committed_paths(repo)
    assert "server/real.py" in tracked
    assert not [p for p in tracked if ".test-tmp-0313" in p]
    assert excluded == ["server/.test-tmp-0313/storage/junk.txt"]
    # Exclusion does not delete files: retain them in the worktree (time-machine principle).
    assert (debris / "junk.txt").exists()


# ── Contract 2: preserve deletion of tracked files in the commit ──

@needs_git
def test_absorb_still_commits_deletions_of_tracked_files(repo):
    """The cleanup of the previously committed 261 artifacts must use this commit path.

    Filtering all of add -A would also filter deletions and retain those files forever.
    Block only newly added artifacts.
    """
    junk_dir = repo / "server" / ".test-tmp-0313"
    junk_dir.mkdir(parents=True)
    (junk_dir / "old.txt").write_text("committed junk\n", encoding="utf-8")
    _git(["add", "-A", "-f"], repo)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "junk"], repo)
    assert "server/.test-tmp-0313/old.txt" in _committed_paths(repo)

    remove_tree(junk_dir)
    excluded = svc._absorb_worker_edits(repo, "chore: drop test debris", None)

    assert excluded == []
    assert "server/.test-tmp-0313/old.txt" not in _committed_paths(repo)


# ── Contract 3: artifacts alone produce neither an empty commit nor a 500 ──

@needs_git
def test_debris_only_worktree_produces_no_commit_and_no_error(repo):
    before = _git(["rev-parse", "HEAD"], repo).strip()
    debris = repo / "server" / ".test-tmp-0318"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: nothing real", None)

    assert excluded == ["server/.test-tmp-0318/junk.txt"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == before


# ── Contract 3 extension: return the excluded-path list ──

def test_submission_untracked_summary_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        svc.db_git, "get_config", lambda _project: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    assert svc.worktree_untracked_summary("flowgate", "flowgate.default.0468") is None


def test_artifact_payload_reports_count_and_list():
    payload = svc._artifact_payload(["a/.test-tmp-1/x", "b/.test-tmp-1/y"])
    assert payload["excluded_artifact_count"] == 2
    assert payload["excluded_artifacts"] == ["a/.test-tmp-1/x", "b/.test-tmp-1/y"]


def test_artifact_payload_caps_the_list_but_not_the_count():
    many = [f"server/.test-tmp-0313/f{i}" for i in range(svc.FINALIZE_ARTIFACT_LIST_MAX + 50)]
    payload = svc._artifact_payload(many)
    assert payload["excluded_artifact_count"] == len(many)
    assert len(payload["excluded_artifacts"]) == svc.FINALIZE_ARTIFACT_LIST_MAX


# ── Shared exclusion rules for the UI and validation (proposal 3) ──

@pytest.mark.parametrize("path", [
    "server/.test-tmp-0313/storage/x.json",
    "server/.test-tmp-0318/case-abc/.git/objects/aa/bb",
    "server/.test-tmp-0313",
    ".git/config",
    "client/node_modules/pkg/index.js",
    "server/app.db",
])
def test_shared_rule_excludes_tool_debris(path):
    assert rules.is_excluded_path(path) is True
    # Keep the canonical names identified by NR and their identical decisions.
    assert trs.is_excluded_path(path) is True


@pytest.mark.parametrize("path", [
    "server/modules/flow_gate/services/git_service.py",
    "client/src/main/components/MainPanel.vue",
    # Preserve the 0299 rule: genuine configuration changes must not silently disappear.
    "client/src/.eslintrc.json",
])
def test_shared_rule_keeps_real_work(path):
    assert rules.is_excluded_path(path) is False
    assert trs.is_excluded_path(path) is False


def test_the_two_rules_no_longer_disagree_about_the_bug_path():
    """The incident path: hidden by the UI but rejected by validation."""
    path = "server/.test-tmp-0313/storage/x.json"
    assert svc._is_hidden_source_path(path) is True
    assert trs.is_excluded_path(path) is True


def test_exposure_rule_still_hides_a_nested_dotfile():
    """Unified rules must not expose nested secret files in the explorer."""
    assert svc._is_hidden_source_path("server/.env.local") is True


def test_exclusion_reason_names_why():
    assert rules.exclusion_reason("server/.test-tmp-0313/x") == rules.REASON_DOT_DIRECTORY
    assert rules.exclusion_reason(".env") == rules.REASON_DOT_TOPLEVEL
    assert rules.exclusion_reason("client/node_modules/a.js") == rules.REASON_TOOL_DIRECTORY
    assert rules.exclusion_reason("server/app.db") == rules.REASON_GENERATED_FILE
    assert rules.exclusion_reason("server/app.py") is None


def test_partition_keeps_order_and_separates_debris():
    kept, artifacts = rules.partition_paths([
        "server/a.py", "server/.test-tmp-0313/x", "client/b.vue", ".env",
    ])
    assert kept == ["server/a.py", "client/b.vue"]
    assert artifacts == ["server/.test-tmp-0313/x", ".env"]


@needs_git
def test_plain_untracked_file_is_visible_when_absorbed(repo):
    """Unknown shapes are legitimate work, but must no longer be silently staged."""
    note = repo / "server" / "scratch_debug_note.md"
    note.parent.mkdir()
    note.write_text("debug\n", encoding="utf-8")

    before = svc._worktree_untracked_summary_for_path(repo)
    assert before == {
        "total_count": 1,
        "excluded_artifact_count": 0,
        "staged_new_file_count": 1,
    }

    excluded = svc._absorb_worker_edits(repo, "test: visible new file", None)
    payload = svc._artifact_payload(
        excluded, before["staged_new_file_count"]
    )
    assert excluded == []
    assert "server/scratch_debug_note.md" in _committed_paths(repo)
    assert payload["staged_new_file_count"] == 1


@needs_git
def test_submission_and_finalize_untracked_counts_share_one_classifier(repo):
    real = repo / "server" / "feature_note.md"
    real.parent.mkdir()
    real.write_text("work\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0468" / "dump.txt"
    debris.parent.mkdir()
    debris.write_text("dump\n", encoding="utf-8")

    submission = svc._worktree_untracked_summary_for_path(repo)
    excluded = svc._absorb_worker_edits(repo, "test: shared classifier", None)
    finalize = svc._artifact_payload(
        excluded, submission["staged_new_file_count"]
    )

    assert submission == {
        "total_count": 2,
        "excluded_artifact_count": 1,
        "staged_new_file_count": 1,
    }
    assert finalize["excluded_artifact_count"] == submission["excluded_artifact_count"]
    assert finalize["staged_new_file_count"] == submission["staged_new_file_count"]
