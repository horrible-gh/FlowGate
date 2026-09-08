"""flowgate.default.0481 T0010 rev3 — "머지는 되지도 않음" (2026-09-08 10:33).

Approval validated the WHOLE merge candidate with the WRITE PLAN's rule, so any
merge carrying a file type with no registered syntax validator (`.md`, `.txt`,
`.lock`, `.png` and — the reviewer's actual case — `.tsbuildinfo`) could only ever
answer `pre_commit_validation_failed`. Pressing [승인] never merged, and no retry
could change that.

L0007 §2.7 scopes that rule to "변경되거나 생성된 모든 **plan** 대상 파일"
(`syntax_validation_scope`), which is the write-plan path — and the apply gate there
already refuses such a plan, so a plan-written file with an unregistered extension
can never reach a candidate. Approval therefore skips the verdict it has no
validator for; every other check (UTF-8, conflict markers, and every registered
validator) still runs on every path.

These run against a REAL git repository, so the blobs the validator reads are real
git objects and the errors are the ones an approval would actually produce.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_IDENT = ["-c", "user.name=T", "-c", "user.email=t@t"]


@pytest.fixture
def repo(tmp_path_factory):
    """A real repository whose HEAD tree is the candidate under validation."""
    root = Path(tempfile.mkdtemp(prefix="fg-approve-scope-", dir=str(tmp_path_factory.mktemp("r"))))
    subprocess.run(["git", "init", "-b", "main", str(root)], capture_output=True, check=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _commit(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, check=True)
    subprocess.run(
        ["git", *_IDENT, "commit", "-m", "candidate"],
        cwd=str(root), capture_output=True, check=True,
    )


def _context(root: Path, paths: list[str]) -> dict:
    """The two context keys the validator reads, built from the real HEAD tree."""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD"],
        cwd=str(root), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    manifest = []
    for line in listing:
        meta, path = line.split("\t", 1)
        mode, kind, oid = meta.split()
        manifest.append({"path": path, "mode": mode, "kind": kind, "oid": oid})
    return {
        "snapshot_manifest": manifest,
        "changes": [{"path": p, "status": "M", "old_path": None} for p in paths],
    }


@needs_git
@pytest.mark.parametrize("path", [
    "tsconfig.app.tsbuildinfo",   # the reviewer's own merge, 2026-09-08 10:24
    "README.md",
    "notes.txt",
    "package-lock.json.lock",
    "LICENSE",                    # no extension at all
])
def test_approval_does_not_veto_a_file_type_it_has_no_validator_for(repo, path):
    _commit(repo, {path: "anything at all\n"})
    context = _context(repo, [path])

    assert git_service._validate_review_changed_paths(
        repo, context, unregistered_extension="skip",
    ) == []

    # Control: the write-plan rule is unchanged, and IS what used to run here.
    rejected = git_service._validate_review_changed_paths(
        repo, context, unregistered_extension="reject",
    )
    assert [e["validator"] for e in rejected] == ["unsupported"]


@needs_git
def test_skip_still_refuses_leftover_conflict_markers(repo):
    marked = "a\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> group\nb\n"
    _commit(repo, {"README.md": marked})

    errors = git_service._validate_review_changed_paths(
        repo, _context(repo, ["README.md"]), unregistered_extension="skip",
    )

    assert [e["validator"] for e in errors] == ["conflict_marker"]


@needs_git
def test_skip_still_runs_every_registered_validator(repo):
    _commit(repo, {"broken.json": "{ this is not json ", "fine.json": '{"ok": true}\n'})

    errors = git_service._validate_review_changed_paths(
        repo, _context(repo, ["broken.json", "fine.json"]), unregistered_extension="skip",
    )

    assert [(e["path"], e["validator"]) for e in errors] == [("broken.json", "json")]


@needs_git
def test_a_mixed_candidate_is_judged_only_on_the_files_that_have_a_validator(repo):
    # The exact shape of the reviewer's merge: two source files plus two build
    # artefacts. Only a real syntax error may stop it.
    _commit(repo, {
        "src/app.json": '{"ok": true}\n',
        "src/index.css": "a { color: red; }\n",
        "tsconfig.app.tsbuildinfo": '{"program":{}}\n',
        "docs/CHANGELOG.md": "# changes\n",
    })
    paths = ["src/app.json", "src/index.css", "tsconfig.app.tsbuildinfo", "docs/CHANGELOG.md"]

    assert git_service._validate_review_changed_paths(
        repo, _context(repo, paths), unregistered_extension="skip",
    ) == []


def test_the_mode_is_spelled_out_rather_than_silently_defaulted():
    # A typo'd mode must not fall through to "permit everything".
    with pytest.raises(ValueError):
        git_service._validate_review_changed_paths(
            Path("."), {"changes": [], "snapshot_manifest": []},
            unregistered_extension="ignore",
        )


def test_approve_asks_for_the_merge_rule_not_the_write_plan_rule(monkeypatch):
    """The call site is the whole fix — pin which mode approval passes.

    `approve_merge_review` is guarded by a project git lock, a session lookup and a
    fingerprint check before it reaches the validator; this drives it to exactly
    that point and stops there, so the assertion is about the argument and nothing
    else. The end-to-end proof that the merge now commits and pushes is the real
    approval run recorded in 0011-TR §단계별 확인.
    """
    seen: dict[str, str] = {}

    session = {"merge_id": 4, "group_id": "test2.default.0009", "kind": "merge"}
    context = {
        "review_state": "resolved_pending_review",
        "review_fingerprint": "fp-1",
        "base_head": "b" * 40,
        "changes": [],
        "snapshot_manifest": [],
    }

    monkeypatch.setattr(git_service.db_git, "get_session", lambda _m: session)
    monkeypatch.setattr(git_service.db_git, "session_kind", lambda _s: git_service.db_git.SESSION_KIND_MERGE)
    monkeypatch.setattr(git_service.db_git, "session_context", lambda _s: context)
    monkeypatch.setattr(git_service.db_git, "set_session_context", lambda *_a, **_k: None)
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _p: {"base_branch": "main"})
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(git_service, "_project_of_group", lambda _g: "test2")
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_a, **_k: True)
    monkeypatch.setattr(git_service, "_base_root_of", lambda _p: Path("."))
    monkeypatch.setattr(git_service, "_live_candidate_matches_snapshot", lambda *_a: True)

    def _spy(_base_root, _context, *, unregistered_extension="reject"):
        seen["mode"] = unregistered_extension
        # One synthetic error stops the function here, before commit/push.
        return [{"path": "x.py", "validator": "python", "line": 1, "message": "stop here"}]

    monkeypatch.setattr(git_service, "_validate_review_changed_paths", _spy)

    result = git_service.approve_merge_review(
        "test2.default.0009", 4,
        attempt_id="00000000-0000-4000-8000-000000000000",
        review_fingerprint="fp-1", authority="human",
    )

    assert seen["mode"] == "skip"
    assert result["result"]["status"] == "pre_commit_validation_failed"
    # An ordinary parser failure keeps its own code, not the unsupported one.
    assert context["last_error"]["code"] == "syntax_validation_failed"
