"""A merge git really made must never be lost to a timeout or a re-click —
flowgate.default.0607 T0004 (NR0003).

The incident (0600, 2026-09-23): the AC final approval's ``git merge --no-ff``
wrote its merge commit, then Git for Windows ran a foreground ``git gc --auto``
inside the same process; ``_run_git``'s 30s kill reported the successful merge as
``git_error``. The browser had already given up at 30s, the button came back, and
the second approval saw ``main..branch == 0``, called that "no work", discarded the
slot and deleted branch + worktree without ever pushing.

Everything here runs against REAL git (a bare local origin, the real base checkout
and group worktree) and drives the real final-approval route. A lost command result
is produced by letting git really run and then handing the caller the same
``returncode=-1, stderr="timeout_expired"`` that ``_run_git`` returns on a kill —
the git facts are genuine, only the verdict is lost, which is exactly the incident.

Cases (T0004 §5): A normal merge · B merge commit made, command "timed out" ·
C real conflict (even when the command also "timed out") · D already merged locally
but unpushed · E push failure (fresh merge and pre-existing merge) · F real no-work
(including a work-less branch cut from an unpushed local base, which must NOT be
mistaken for merged work of its own). Plus §3.1's auto-gc suppression, measured on a
repository that really does auto-pack without it, and §3.6's server-side
"approval still in flight" signal.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_git_integration_0115 import (  # noqa: F401 — fixtures are used by name
    _git,
    needs_git,
    patch_store,
    tmp_db,
)

USER = {"user_id": "reviewer", "is_admin": True}
# Its own project: FLOWGATE_STORAGE_DIR is shared by the whole session while each
# module builds its own bare origin (see test_final_approval_conflict_intent_0555).
PROJECT = "mtprj"
PROJECT_NAME = "MtProj"


@pytest.fixture(scope="module")
def project(patch_store, tmp_db):
    from modules.flow_gate.db import projects

    projects.create({"project_id": PROJECT, "project_name": PROJECT_NAME})
    yield


@pytest.fixture(scope="module")
def origin_repo(project):
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-mt-origin-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "shared.py").write_text('"line1"\n', encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config(PROJECT, {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config(PROJECT)
    shutil.rmtree(tmp, ignore_errors=True)


# ── harness ──────────────────────────────────────────────────────────────────

def _seed_final_approval_group(group_id: str) -> tuple[str, str]:
    """R root in progress + one pending AC pointing at it (same shape as 0555)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    store = get_store()
    if store._fetch_one("SELECT 1 AS ok FROM users WHERE user_id = ?", [USER["user_id"]]) is None:
        now = now_iso()
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin,"
            " first_login_required, created_at, updated_at)"
            " VALUES (?, ?, ?, 'x', 1, 1, 0, ?, ?)",
            [USER["user_id"], USER["user_id"], f'{USER["user_id"]}@test', now, now],
        )
    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": PROJECT,
            "module": "default", "title": "merge timeout recovery",
        })
    root_id = f"{group_id}.0001-R"
    ac_id = f"{group_id}.0002-AC"
    if db_docs.get_by_id(root_id) is None:
        db_docs.create({
            "doc_id": root_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(root_id, {"doc_review_status": "wf_in_progress"})
    if db_wfseq.get_sequence_by_doc_id(root_id) is None:
        db_wfseq.insert_sequence(root_id)
    if db_docs.get_by_id(ac_id) is None:
        db_docs.create({
            "doc_id": ac_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "AC", "seq": 2,
            "title": "final approval", "target_id": root_id,
        })
    db_docs.update(ac_id, {"doc_review_status": "pending_review", "target_id": root_id})
    return root_id, ac_id


def _final_approve(doc_id: str, git_action: str | None = "merge") -> tuple[int, dict]:
    """Drive the real final-approval orchestrator (the only public entry point)."""
    from modules.flow_gate.workflow.routers import workflow

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve",
        workflow.DocumentBodyRequest(doc_id=doc_id, git_action=git_action),
        USER,
        None,
    ))
    return response.status_code, json.loads(response.body.decode("utf-8"))


def _review_status(doc_id: str) -> str:
    from modules.flow_gate.db import documents as db_docs

    return (db_docs.get_by_id(doc_id) or {}).get("doc_review_status")


def _branch(group_id: str) -> str:
    return group_id.replace(".", "_")


def _base() -> Path:
    from modules.flow_gate.storage.paths import src_root

    return src_root(PROJECT_NAME, "main")


def _worktree(group_id: str) -> Path:
    from modules.flow_gate.storage.paths import src_root

    return src_root(PROJECT_NAME, _branch(group_id))


def _rev(repo: Path, rev: str) -> str:
    return _git(["rev-parse", rev], cwd=repo).strip()


def _origin_head(origin) -> str:
    return _git(["rev-parse", "main"], cwd=origin["bare"]).strip()


def _branch_exists(group_id: str) -> bool:
    proc = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{_branch(group_id)}"],
        cwd=str(_base()), capture_output=True,
    )
    return proc.returncode == 0


def _git_state(group_id: str) -> dict:
    from modules.flow_gate.db import git_integration as db_git

    return db_git.get_state(group_id) or {}


def _group_with_work(group_id: str, path: str, content: str) -> tuple[str, str]:
    """A final-approval group whose worktree carries one committed change."""
    from modules.flow_gate.services import git_service as svc

    root_id, ac_id = _seed_final_approval_group(group_id)
    assert svc.ensure_worktree(PROJECT, "default", group_id) == "ok"
    wt = _worktree(group_id)
    (wt / path).write_text(content, encoding="utf-8")
    _git(["add", "-A"], cwd=wt)
    _git(["commit", "-m", f"work {group_id}"], cwd=wt)
    return root_id, ac_id


def _lost_result(match):
    """Wrap `_run_git`: really run git, then lose the verdict for matching calls
    the way the 30s kill did (returncode -1, stderr "timeout_expired")."""
    from modules.flow_gate.services import git_service as svc

    original = svc._run_git

    def _wrapped(args, **kwargs):
        result = original(args, **kwargs)
        if match(args):
            return subprocess.CompletedProcess(args, -1, "", "timeout_expired")
        return result

    return _wrapped


def _is_merge_no_ff(args) -> bool:
    return "merge" in args and "--no-ff" in args


def _is_push_base(args) -> bool:
    return list(args[:3]) == ["push", "origin", "main"]


def _reject_push(match):
    from modules.flow_gate.services import git_service as svc

    original = svc._run_git

    def _wrapped(args, **kwargs):
        if match(args):
            return subprocess.CompletedProcess(args, 1, "", "! [rejected] main -> main (fetch first)")
        return original(args, **kwargs)

    return _wrapped


# ── §3.1 auto gc / maintenance never runs inside a request-path git call ──────

def _loose_object_count(repo: Path) -> int:
    objects = repo / ".git" / "objects"
    return sum(
        1 for d in objects.iterdir()
        if len(d.name) == 2 and d.is_dir() for _ in d.iterdir()
    )


def _repo_that_auto_packs(root: Path) -> Path:
    """A repository whose next commit REALLY runs `git gc --auto` (gc.auto=1 plus
    enough loose objects that git's sampling of objects/17 crosses it)."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-b", "main", str(repo)])
    _git(["config", "gc.auto", "1"], cwd=repo)
    _git(["config", "gc.autoDetach", "false"], cwd=repo)
    blobs = root / "blobs"
    blobs.mkdir()
    paths = []
    for i in range(1500):
        p = blobs / f"b{i}.txt"
        p.write_text(f"blob {i}\n", encoding="utf-8")
        paths.append(str(p))
    subprocess.run(
        ["git", "hash-object", "-w", "--stdin-paths"], cwd=str(repo),
        input="\n".join(paths) + "\n", text=True, capture_output=True, check=True,
    )
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], cwd=repo)
    return repo


@needs_git
def test_request_path_git_suppresses_auto_gc_on_a_repo_that_really_auto_packs():
    from modules.flow_gate.services import git_service as svc

    root = Path(tempfile.mkdtemp(prefix="fg-mt-gc-"))
    try:
        # Control: the fixture is real — plain git auto-packs on commit.
        control = _repo_that_auto_packs(root / "control")
        before = _loose_object_count(control)
        proc = subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "c"],
            cwd=str(control), capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert _loose_object_count(control) < before, "fixture did not trigger auto gc"

        # Same repository shape through FlowGate's runner: no auto gc.
        repo = _repo_that_auto_packs(root / "flowgate")
        before = _loose_object_count(repo)
        config_before = (repo / ".git" / "config").read_bytes()
        proc = svc._run_git(
            ["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "c"], cwd=repo,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Auto packing" not in (proc.stderr or "")
        assert _loose_object_count(repo) >= before
        # Per-invocation only: the repository's own config is never written.
        assert (repo / ".git" / "config").read_bytes() == config_before
        assert _git(["config", "--get", "gc.auto"], cwd=repo).strip() == "1"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@needs_git
def test_request_path_overrides_win_over_repo_config_and_keep_caller_env_config():
    from modules.flow_gate.services import git_service as svc

    root = Path(tempfile.mkdtemp(prefix="fg-mt-gccfg-"))
    try:
        repo = root / "repo"
        _git(["init", "-b", "main", str(repo)])
        _git(["config", "gc.auto", "6700"], cwd=repo)
        _git(["config", "maintenance.auto", "true"], cwd=repo)

        assert svc._run_git(["config", "--get", "gc.auto"], cwd=repo).stdout.strip() == "0"
        assert svc._run_git(["config", "--get", "maintenance.auto"], cwd=repo).stdout.strip() == "false"
        # A caller's own env config is appended to, not replaced.
        caller = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "flowgate.probe",
            "GIT_CONFIG_VALUE_0": "kept",
        }
        out = svc._run_git(["config", "--get", "flowgate.probe"], cwd=repo, extra_env=caller)
        assert out.stdout.strip() == "kept"
        out = svc._run_git(["config", "--get", "gc.auto"], cwd=repo, extra_env=caller)
        assert out.stdout.strip() == "0"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── §3.2 judging a lost merge result by git facts ─────────────────────────────

@needs_git
def test_merge_commit_landed_needs_both_parents_not_just_a_moved_head():
    from modules.flow_gate.services import git_service as svc

    root = Path(tempfile.mkdtemp(prefix="fg-mt-landed-"))
    try:
        repo = root / "repo"
        _git(["init", "-b", "main", str(repo)])
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-m", "base"], cwd=repo)
        for name in ("one", "two"):
            _git(["checkout", "-q", "-b", name, "main"], cwd=repo)
            (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
            _git(["add", "-A"], cwd=repo)
            _git(["commit", "-m", name], cwd=repo)
        _git(["checkout", "-q", "main"], cwd=repo)
        pre = _rev(repo, "HEAD")
        one, two = _rev(repo, "one"), _rev(repo, "two")

        # Nothing happened yet: HEAD did not move.
        assert svc._merge_commit_landed(repo, pre, one) is False
        _git(["merge", "--no-ff", "-m", "merge one", "one"], cwd=repo)
        assert svc._merge_commit_landed(repo, pre, one) is True
        # HEAD moved, but by a merge of something else.
        assert svc._merge_commit_landed(repo, pre, two) is False
        # HEAD moved, but not from the base this merge started on.
        assert svc._merge_commit_landed(repo, one, one) is False
        # Unknown inputs are never a success.
        assert svc._merge_commit_landed(repo, None, one) is False
        assert svc._merge_commit_landed(repo, pre, None) is False
        # A plain fast-forward-looking move is not a merge commit of the branch.
        _git(["reset", "-q", "--hard", pre], cwd=repo)
        _git(["merge", "--ff-only", "one"], cwd=repo)
        assert svc._merge_commit_landed(repo, pre, one) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


@needs_git
class TestMergeTimeoutRecovery0607:
    """Cases A–F through the real final-approval route on one real origin.

    Order matters only for the last test (it leaves a conflict it then aborts)."""

    # ── Case A — normal merge ────────────────────────────────────────────────
    def test_a_normal_merge_pushes_approves_and_cleans_up(self, origin_repo):
        group = f"{PROJECT}.default.0701"
        root_id, ac_id = _group_with_work(group, "a.py", "a = 1\n")

        status, payload = _final_approve(ac_id)

        assert status == 200, payload
        result = payload["git"]["result"]
        assert result["status"] == "merged" and result["pushed"] is True
        assert payload["approval"]["approved"] is True
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        head = _rev(_base(), "HEAD")
        assert _origin_head(origin_repo) == head
        assert head.startswith(result["merge_commit"])
        assert _git_state(group)["status"] == "merged"
        assert not _worktree(group).exists()
        assert not _branch_exists(group)

    # ── Case B — the merge commit exists, the command result was lost ────────
    def test_b_merge_commit_made_but_command_timed_out_is_a_successful_merge(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0702"
        root_id, ac_id = _group_with_work(group, "b.py", "b = 1\n")
        base = _base()
        pre = _rev(base, "HEAD")
        tip = _rev(base, f"refs/heads/{_branch(group)}")
        original = svc._run_git

        def _killed_after_commit(args, **kwargs):
            result = original(args, **kwargs)
            if _is_merge_no_ff(args):
                # NR0003 §8: a merge killed after its commit still had MERGE_HEAD.
                (base / ".git" / "MERGE_HEAD").write_text(tip + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(args, -1, "", "timeout_expired")
            return result

        monkeypatch.setattr(svc, "_run_git", _killed_after_commit)
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        assert status == 200, payload
        result = payload["git"]["result"]
        assert result["status"] == "merged" and result["pushed"] is True
        assert payload["approval"]["approved"] is True
        head = _rev(base, "HEAD")
        assert _rev(base, "HEAD^1") == pre and _rev(base, "HEAD^2") == tip
        # pushed — not stranded in local main like 0600's 2a766c9d
        assert _origin_head(origin_repo) == head
        assert not (base / ".git" / "MERGE_HEAD").exists()
        assert _review_status(root_id) == "wf_done"
        assert _git_state(group)["status"] == "merged"
        assert not _worktree(group).exists()
        assert not _branch_exists(group)

    def test_b_a_timed_out_merge_that_made_no_commit_is_still_a_failure(
        self, origin_repo, monkeypatch,
    ):
        """The other half of §3.2: a timeout is never assumed to be a success."""
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0703"
        _root_id, ac_id = _group_with_work(group, "b2.py", "b2 = 1\n")
        base = _base()
        pre = _rev(base, "HEAD")
        original = svc._run_git

        def _killed_before_commit(args, **kwargs):
            if _is_merge_no_ff(args):
                return subprocess.CompletedProcess(args, -1, "", "timeout_expired")
            return original(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", _killed_before_commit)
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        assert status == 500, payload
        assert payload["error"]["code"] == "git_error"
        assert _review_status(ac_id) == "pending_review"
        assert _rev(base, "HEAD") == pre
        assert _origin_head(origin_repo) == pre
        # slot intact for a retry
        assert _worktree(group).is_dir() and _branch_exists(group)
        assert _git_state(group)["worktree_registered"]

        status, payload = _final_approve(ac_id)
        assert status == 200, payload
        assert payload["git"]["result"]["status"] == "merged"
        assert _origin_head(origin_repo) == _rev(base, "HEAD")

    # ── Case D — merged locally, never pushed ────────────────────────────────
    def test_d_already_merged_but_unpushed_is_pushed_not_discarded(self, origin_repo):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0704"
        root_id, ac_id = _group_with_work(group, "d.py", "d = 1\n")
        base = _base()
        origin_before = _origin_head(origin_repo)
        # Request A of the incident: the merge landed locally, its result was lost
        # and the slot was left `waiting`.
        _git(["-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-m",
              "lost merge", _branch(group)], cwd=base)
        stranded = _rev(base, "HEAD")
        svc._set_status(group, "waiting")
        assert int(_git(["rev-list", "--count", f"main..{_branch(group)}"], cwd=base)) == 0
        assert int(_git(["rev-list", "--count", "origin/main..main"], cwd=base)) > 0

        # The one "is there work" judgment no longer calls this empty, so neither
        # the auto-discard nor the no-work quiet path can tear it down.
        cfg = svc.db_git.get_config(PROJECT)
        assert svc._group_has_changes(cfg, _git_state(group), PROJECT_NAME) is True
        assert svc.group_finalize_is_noop(group) is False

        status, payload = _final_approve(ac_id)

        assert status == 200, payload
        result = payload["git"]["result"]
        assert result["status"] == "merged", result
        assert result["pushed"] is True
        assert stranded.startswith(result["merge_commit"])
        # the existing merge was pushed — no second merge commit was stamped
        assert _rev(base, "HEAD") == stranded
        assert _origin_head(origin_repo) == stranded != origin_before
        assert payload["approval"]["approved"] is True
        assert _review_status(root_id) == "wf_done"
        assert _git_state(group)["status"] == "merged"
        assert not _worktree(group).exists()
        assert not _branch_exists(group)

    # ── Case E — push failure keeps everything retryable ─────────────────────
    def test_e_push_failure_keeps_branch_worktree_and_stays_retryable(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0705"
        _root_id, ac_id = _group_with_work(group, "e.py", "e = 1\n")
        base = _base()
        pre = _rev(base, "HEAD")
        origin_before = _origin_head(origin_repo)

        monkeypatch.setattr(svc, "_run_git", _reject_push(_is_push_base))
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        assert status == 500, payload
        assert payload["error"]["code"] == "push_rejected"
        assert payload["approval"]["approved"] is False
        assert _review_status(ac_id) == "pending_review"
        assert _origin_head(origin_repo) == origin_before
        assert _rev(base, "HEAD") == pre  # only this request's merge was rewound
        assert _worktree(group).is_dir() and _branch_exists(group)
        state = _git_state(group)
        assert state["status"] == "waiting" and state["worktree_registered"]

        status, payload = _final_approve(ac_id)
        assert status == 200, payload
        assert payload["git"]["result"]["status"] == "merged"
        assert _origin_head(origin_repo) == _rev(base, "HEAD")
        assert not _worktree(group).exists()

    def test_e_push_failure_of_an_already_merged_branch_never_rewinds_it(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0706"
        _root_id, ac_id = _group_with_work(group, "e2.py", "e2 = 1\n")
        base = _base()
        origin_before = _origin_head(origin_repo)
        _git(["-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-m",
              "lost merge", _branch(group)], cwd=base)
        stranded = _rev(base, "HEAD")
        svc._set_status(group, "waiting")

        monkeypatch.setattr(svc, "_run_git", _reject_push(_is_push_base))
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        assert status == 500, payload
        assert payload["error"]["code"] == "push_rejected"
        assert _review_status(ac_id) == "pending_review"
        # the pre-existing merge is the only copy of the work: it stays
        assert _rev(base, "HEAD") == stranded
        assert _origin_head(origin_repo) == origin_before
        assert _worktree(group).is_dir() and _branch_exists(group)
        assert _git_state(group)["worktree_registered"]

        status, payload = _final_approve(ac_id)
        assert status == 200, payload
        assert payload["git"]["result"]["status"] == "merged"
        assert _origin_head(origin_repo) == stranded
        assert not _worktree(group).exists()

    # ── Case F — real no-work keeps its discarded meaning ────────────────────
    def test_f_real_no_work_is_still_discarded_without_touching_origin(self, origin_repo):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0707"
        root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        base = _base()
        base_before = _rev(base, "HEAD")
        origin_before = _origin_head(origin_repo)
        assert base_before == origin_before

        status, payload = _final_approve(ac_id)

        assert status == 200, payload
        assert payload["git"]["result"]["status"] == "discarded"
        assert payload["git"]["result"]["pushed"] is False
        assert payload["approval"]["approved"] is True
        assert _review_status(root_id) == "wf_done"
        assert _rev(base, "HEAD") == base_before
        assert _origin_head(origin_repo) == origin_before
        assert not _worktree(group).exists()
        assert not _branch_exists(group)

    def test_f_a_work_less_branch_cut_from_an_unpushed_base_is_not_merged_work(
        self, origin_repo,
    ):
        """Safety of the §3.3 detector: another group's unpushed merge sitting in
        local main (0600's 2a766c9d today) must never make an EMPTY branch cut from
        it look "merged but unpushed" — that would push someone else's commit."""
        from modules.flow_gate.services import git_service as svc

        other = f"{PROJECT}.default.0708"
        _group_with_work(other, "f_other.py", "other = 1\n")
        base = _base()
        _git(["-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-m",
              "other group's unpushed merge", _branch(other)], cwd=base)
        unpushed = _rev(base, "HEAD")
        origin_before = _origin_head(origin_repo)
        try:
            group = f"{PROJECT}.default.0709"
            root_id, ac_id = _seed_final_approval_group(group)
            assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
            assert _rev(base, f"refs/heads/{_branch(group)}") == unpushed
            assert svc._unpushed_local_merge_of(base, "main", _branch(group)) is None
            assert svc._unpushed_local_merge_of(base, "main", _branch(other)) == unpushed

            status, payload = _final_approve(ac_id)

            assert status == 200, payload
            assert payload["git"]["result"]["status"] == "discarded"
            assert _origin_head(origin_repo) == origin_before  # nothing was pushed
            assert _rev(base, "HEAD") == unpushed
            assert _review_status(root_id) == "wf_done"
        finally:
            # leave the shared base as the other tests expect it: equal to origin
            _git(["reset", "-q", "--hard", origin_before], cwd=base)

    # ── §3.6 server truth: is this group's approval still running Git? ───────
    def test_finalize_state_reports_an_approval_still_in_flight(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0710"
        _group_with_work(group, "g.py", "g = 1\n")
        assert svc.get_finalize_state(group)["state"]["approval_in_flight"] is False

        holder = f"approval:{group}.0002-AC:probe"
        assert db_git.try_acquire_lock(PROJECT, holder)
        try:
            assert svc.get_finalize_state(group)["state"]["approval_in_flight"] is True
            assert svc.get_finalize_state(group, preview_ac=True)["state"]["approval_in_flight"] is True
            # another group's approval, or a plain git op, is not THIS approval
            assert svc.get_finalize_state(f"{PROJECT}.default.0701")["state"]["approval_in_flight"] is False
        finally:
            db_git.release_lock(PROJECT, holder)
        other = f"approval:{PROJECT}.default.07100.0002-AC:probe"
        assert db_git.try_acquire_lock(PROJECT, other)
        try:
            assert svc.get_finalize_state(group)["state"]["approval_in_flight"] is False
        finally:
            db_git.release_lock(PROJECT, other)

    def test_finalize_state_reports_unknown_when_the_lock_probe_itself_fails(
        self, origin_repo, monkeypatch,
    ):
        """rev1 (human rejection): a failed lock probe must never masquerade as
        `approval_in_flight: False` — the caller cannot tell "confirmed no approval
        running" from "couldn't ask", so a probe failure has to say `None`/`null`,
        distinct from both `True` and a genuinely confirmed `False`."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0711"
        _group_with_work(group, "g2.py", "g = 2\n")

        def _boom(project_id):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(db_git, "get_lock", _boom)
        try:
            assert svc.approval_git_in_flight(group) is None
            assert svc.get_finalize_state(group)["state"]["approval_in_flight"] is None
        finally:
            monkeypatch.undo()
        # once the probe recovers, a real "no approval running" still reports False
        assert svc.get_finalize_state(group)["state"]["approval_in_flight"] is False

    # ── Case C — a real conflict stays a conflict (keep LAST: aborts itself) ──
    def test_c_real_conflict_is_never_judged_a_success_even_when_timed_out(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0711"
        root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        (_worktree(group) / "shared.py").write_text('"group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"mainline version"\n', encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline shared"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)
        origin_before = _origin_head(origin_repo)

        monkeypatch.setattr(svc, "_run_git", _lost_result(_is_merge_no_ff))
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        assert status == 200, payload
        result = payload["git"]["result"]
        assert result["status"] == "conflict"
        assert result["conflict_files"] == ["shared.py"]
        assert payload["approval"]["approved"] is False
        assert payload["approval"]["deferred"] is True
        assert _review_status(ac_id) == "pending_review"
        assert _git_state(group)["status"] == "conflict"
        assert _origin_head(origin_repo) == origin_before
        assert (_base() / ".git" / "MERGE_HEAD").exists()
        assert _worktree(group).is_dir()

        svc.abort_merge(group, result["merge_id"])
        assert not (_base() / ".git" / "MERGE_HEAD").exists()
