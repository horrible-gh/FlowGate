"""Shared real-Git harness for the 0066 T#3 suites (manual merge, merge results, fetch-only).

Built on the 0594 harness: every test gets its own project (bare origin, seed clone, base
checkout) on a temporary SQLite with the real migrations, and the real job / lock path runs
(``base_publish`` jobs claimed in the request, ``resource_lock`` rows, G -> B/W -> R).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.modules.flow_gate.test_git_merge_target_0594 import Proj, _ctx, _git, _seed_wf_done_root, _session, needs_git  # noqa: F401
from group_lock_stub import held_locks  # noqa: F401

from modules.flow_gate.services.git.credentials import GitServiceError


def seed_root(gid: str, pid: str, status: str = "wf_in_progress") -> None:
    """A Group whose root is NOT wf_done (no AC, no approval): the manual merge case."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups

    if db_groups.get_by_id(gid) is None:
        db_groups.create({"group_id": gid, "project_id": pid, "module": "default", "title": "t3"})
    doc_id = f"{gid}.0001-R"
    if db_docs.get_by_id(doc_id) is None:
        db_docs.create({"doc_id": doc_id, "project_id": pid, "module": "default", "group_id": gid,
                        "type_code": "R", "seq": 1, "title": "root",
                        "file_path": f"documents/{gid}/0001-R.md"})
    db_docs.update(doc_id, {"doc_review_status": status})


def group(p: Proj, seq: int, *, wf_done: bool = False) -> str:
    gid = p.group(seq)
    if wf_done:
        _seed_wf_done_root(gid, p.pid)
    else:
        seed_root(gid, p.pid)
    return gid


def branch(gid: str) -> str:
    return gid.replace(".", "_")


def work(p: Proj, gid: str, path: str, content: str) -> str:
    wt = p.wt(gid)
    (wt / path).parent.mkdir(parents=True, exist_ok=True)
    (wt / path).write_text(content, encoding="utf-8")
    _git(["add", "--", path], cwd=wt)
    _git(["commit", "-m", f"work {path}"], cwd=wt)
    return rev(wt, "HEAD")


def rev(repo: Path, ref: str) -> str:
    return _git(["rev-parse", ref], cwd=repo).strip()


def try_rev(repo: Path, ref: str):
    out = _git(["rev-parse", "--verify", "--quiet", ref], cwd=repo, check=False).strip()
    return out or None


def files_at(repo: Path, ref: str) -> list[str]:
    return _git(["ls-tree", "-r", "--name-only", ref], cwd=repo).split()


def state(gid: str) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.get_state(gid) or {}


def merge_sessions(gid: str) -> list[dict]:
    from modules.flow_gate.db import git_integration as db_git
    return [s for s in db_git.sessions_by_group(gid)
            if db_git.session_kind(s) == db_git.SESSION_KIND_MERGE]


def manual(gid: str, target: str | None = None, push: bool = False) -> dict:
    from modules.flow_gate.services.git import manual_merge
    return manual_merge.request_merge(gid, target, push)


def results(gid: str) -> dict:
    from modules.flow_gate.services.git import manual_merge
    return manual_merge.merge_results_view(gid)


def push_result(gid: str, merge_id, merge_commit) -> dict:
    from modules.flow_gate.services.git import manual_merge
    return manual_merge.request_result_push(gid, merge_id, merge_commit)


def refused(fn, *args, **kwargs) -> GitServiceError:
    with pytest.raises(GitServiceError) as info:
        fn(*args, **kwargs)
    return info.value


def reject_push(monkeypatch, match=None) -> list:
    """Make matching ``git push`` calls fail the way a non-fast-forward rejection does."""
    from modules.flow_gate.services import git_service as svc
    real = svc._run_git
    seen: list = []

    def run(args, **kwargs):
        if args and args[0] == "push" and (match is None or match(args)):
            seen.append(list(args))
            return subprocess.CompletedProcess(args, 1, "", "! [rejected] (fetch first)")
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    return seen


def spy_git(monkeypatch) -> list:
    from modules.flow_gate.services import git_service as svc
    real = svc._run_git
    seen: list = []

    def run(args, **kwargs):
        seen.append(list(args))
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    return seen


def spy_locks(monkeypatch) -> list:
    """Every lock request (domain, group, target_key, holder_kind) and release, in order."""
    from modules.flow_gate.services.git import lock_manager as locks
    real_acquire, real_release = locks.acquire, locks.release
    seen: list = []

    def acquire(domain, project_id, **kwargs):
        out = real_acquire(domain, project_id, **kwargs)
        seen.append(("acquire", domain, kwargs.get("target_key"), kwargs.get("holder_kind"), out.ok))
        return out

    def release(ctx_or_key, key=None):
        seen.append(("release", key if key is not None else ctx_or_key))
        return real_release(ctx_or_key, key)
    monkeypatch.setattr(locks, "acquire", acquire)
    monkeypatch.setattr(locks, "release", release)
    return seen


def drive_jobs(gid: str, limit: int = 4) -> int:
    """What a Job Runner worker does: claim each waiting base_publish job of the Group."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner

    ran = 0
    for _ in range(limit):
        jobs = db_jobs.jobs_of_group(gid, "base_publish", ("pending", "blocked", "retry_wait"))
        if not jobs:
            break
        for job in jobs:
            with get_store().transaction():
                get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                                     ["2000-01-01T00:00:00+00:00", job["job_id"]])
            claim = job_runner.try_claim(job["job_id"])
            if claim is not None:
                job_runner.execute(claim)
                ran += 1
    return ran


_GIT_C_ESCAPES = {'"': '\\"', "\\": "\\\\", "\a": "\\a", "\b": "\\b", "\t": "\\t",
                  "\n": "\\n", "\v": "\\v", "\f": "\\f", "\r": "\\r"}


def git_quoted(path: str) -> str:
    """``path`` as ``git diff --name-only`` prints it with core.quotePath on: unchanged when
    Git does not quote it, else double-quoted with C escapes and octal UTF-8 bytes."""
    if all(0x20 <= ord(c) < 0x7F and c not in _GIT_C_ESCAPES for c in path):
        return path
    body = "".join(_GIT_C_ESCAPES.get(c) or (c if 0x20 <= ord(c) < 0x7F else
                   "".join(f"\\{b:03o}" for b in c.encode("utf-8"))) for c in path)
    return f'"{body}"'


def _expand_json(value):
    """Stored job rows carry JSON text; read it as values so escaping is checked once."""
    if isinstance(value, dict):
        return {k: _expand_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_expand_json(v) for v in value]
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return _expand_json(json.loads(value))
        except ValueError:
            return value
    return value


def no_hidden(value, *paths) -> None:
    """No spelling of ``paths`` anywhere in ``value``: the path, its Git-quoted line and that
    line's body, with ``/`` or ``\\`` separators, raw or JSON-escaped."""
    text = json.dumps(_expand_json(value), ensure_ascii=False, default=str)
    for path in paths:
        quoted = git_quoted(path)
        for form in {path, quoted, quoted.strip('"')}:
            for spelled in (form, form.replace("/", "\\")):
                assert spelled not in text, spelled
                assert json.dumps(spelled, ensure_ascii=False)[1:-1] not in text, spelled
