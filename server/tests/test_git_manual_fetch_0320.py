"""manual_fetch clean-base fast-forward (flowgate.default.0320 — B0001/NR0003/TR0005).

B0001 ("Git 연동 시 뒤쳐짐...... 영원히 안가져올건가?"): the operator-facing "Fetch"
(``manual_fetch``) only moved the remote-tracking ref ``refs/remotes/origin/{base}``
and then *reported* ``behind_count`` — the local base branch (``refs/heads/{base}``)
never advanced, so the base checkout stayed behind upstream forever and the Fetch
action was a no-op recovery. TR0005 makes ``manual_fetch`` fast-forward a CLEAN base
to ``origin/{base}`` after the fetch, reusing finalize's verified
``merge --ff-only origin/{base}`` pattern.

These tests pin the new decision logic WITHOUT a real git repo or a ``file://``
clone. The real-git E2E suite (``test_git_integration_0115.needs_git``) *skips* on
git-for-Windows because it cannot clone ``file:///DRIVE:/...`` via subprocess; by
monkeypatching the git plumbing here the branch runs identically on every OS, so
this file is the host-portable guard for the fix.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-manual-fetch-0320-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service as svc  # noqa: E402


class _Proc:
    """Minimal stand-in for subprocess.CompletedProcess (returncode + stderr)."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _install(monkeypatch, *, dirty, origin_ref, ff_ok, ahead_behind):
    """Patch every plumbing dependency of ``manual_fetch`` for one scenario.

    Returns the list that captures each ``_run_git`` argv so a test can assert
    which git subcommands actually ran (i.e. whether the fast-forward fired).
    """
    calls: list[list[str]] = []

    monkeypatch.setattr(
        svc, "_require_enabled_config",
        lambda pid: {"base_branch": "main", "username": None},
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc, "src_root", lambda name, branch: Path("/base"))
    monkeypatch.setattr(svc, "_judge_base_slot", lambda root, branch: "checkout")
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_ref_exists", lambda repo, ref: origin_ref)
    monkeypatch.setattr(
        svc, "_dirty", lambda repo, include_untracked=True: dirty
    )
    monkeypatch.setattr(
        svc, "_base_ahead_behind", lambda root, branch: ahead_behind
    )

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:1] == ["fetch"]:
            return _Proc(returncode=0)
        if args[:2] == ["merge", "--ff-only"]:
            return _Proc(
                returncode=0 if ff_ok else 1,
                stderr="" if ff_ok else "fatal: Not possible to fast-forward",
            )
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)
    return calls


def _merge_ran(calls) -> bool:
    return any(c[:2] == ["merge", "--ff-only"] for c in calls)


def test_clean_base_fast_forwards_to_origin(monkeypatch):
    # Clean base + origin/main present and ahead -> ff-only runs, base advances,
    # behind drops to 0. This is the core B0001 fix.
    calls = _install(
        monkeypatch, dirty=False, origin_ref=True, ff_ok=True, ahead_behind=(0, 0)
    )
    out = svc.manual_fetch("proj")["result"]
    assert out["fetched"] is True
    assert out["advanced"] is True
    assert out["base_branch"] == "main"
    assert out["behind_count"] == 0 and out["ahead_count"] == 0
    assert _merge_ran(calls), "clean fast-forward must run merge --ff-only"


def test_dirty_base_is_left_untouched(monkeypatch):
    # Dirty base (tracked changes) -> never force the server's own checkout:
    # no merge, advanced=False, behind still reported truthfully.
    calls = _install(
        monkeypatch, dirty=True, origin_ref=True, ff_ok=True, ahead_behind=(0, 3)
    )
    out = svc.manual_fetch("proj")["result"]
    assert out["fetched"] is True
    assert out["advanced"] is False
    assert out["behind_count"] == 3
    assert not _merge_ran(calls), "dirty base must not be fast-forwarded"


def test_missing_origin_ref_does_not_merge(monkeypatch):
    # No refs/remotes/origin/main yet -> nothing to fast-forward onto.
    calls = _install(
        monkeypatch, dirty=False, origin_ref=False, ff_ok=True, ahead_behind=(0, 0)
    )
    out = svc.manual_fetch("proj")["result"]
    assert out["advanced"] is False
    assert not _merge_ran(calls)


def test_diverged_base_reports_without_destructive_reset(monkeypatch):
    # Local-only commits diverge from origin -> ff-only fails; advanced=False,
    # ahead/behind reported, and NO destructive reset is issued (divergence stays
    # finalize's E4 base_diverged, not this recovery path's job).
    calls = _install(
        monkeypatch, dirty=False, origin_ref=True, ff_ok=False, ahead_behind=(2, 1)
    )
    out = svc.manual_fetch("proj")["result"]
    assert out["fetched"] is True
    assert out["advanced"] is False
    assert out["ahead_count"] == 2 and out["behind_count"] == 1
    assert _merge_ran(calls), "diverged case still attempts ff-only"
    # Only the fetch and the failed ff-only ran — no reset/checkout -B/etc.
    assert all(
        c[:1] == ["fetch"] or c[:2] == ["merge", "--ff-only"] for c in calls
    ), f"unexpected destructive git op in {calls}"
