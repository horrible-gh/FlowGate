"""flowgate.v02test.0042 regression (real git, real session rows): a card result's merge conflict resolved on the card.

The earlier suite (``test_card_merge_v02test_0042``) stubbed the session layer. This one runs the whole chain the
card's '병합 · 충돌' tab drives, on a real git worktree and a migrated SQLite (the 0332 fixtures):

    deferred conflict (merge undone, group free)  ->  a person's choice asks the merge again keeping the session
    ->  per-file pick (this card's side / the latest code's side) written through the session's own apply
    ->  the tab offers [병합] only once every file is resolved  ->  the session commit lands on the group branch

and a side that deleted the file is refused (``pick_unavailable``) before anything is written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.regression.tr_commit.test_tr_conflict_session_0332 import (  # noqa: F401 — real sqlite + git fixtures
    _GROUP, _commit, _git, git_active, needs_git, real_store, repo)
from flowgate.card_merge import service
from flowgate.graph_run import result_merge
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.services import tr_commit_service as trc


@pytest.fixture
def merge_on_repo(monkeypatch, git_active):
    """``_merge`` on the real group worktree; only the Graph-side reads are stubbed (no node / document / ledger).
    The document write gate (registered at server startup; it guards card documents, not the source files a merge
    session writes) lets the session's own path writes through, so the git side runs for real."""
    from contextlib import nullcontext
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    def source_paths_only(operation, *args, **kwargs):
        if operation == "paths":
            return args[1]()
        if operation == "paths_scope":
            return nullcontext()
        raise AssertionError(f"unexpected document write in a merge session: {operation}")

    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, source_paths_only)
    # 0332's group-lock stand-in makes ``release`` a no-op while the session lock acquires for real, so a second
    # write on the same session would find its own first lock still held: the session lock is a no-op here too.
    from modules.flow_gate.services.git import branch_merge_publish as bmp
    monkeypatch.setattr(bmp, "session_lock", lambda *a, **k: (None, []))
    monkeypatch.setattr(bmp, "session_unlock", lambda ctx, held: None)
    root = Path(git_active)
    monkeypatch.setattr(result_merge, "_group_root", lambda row: ("flowgate", root, "work"))
    monkeypatch.setattr(result_merge, "_gate_snapshot_reserved", lambda flow: False)
    monkeypatch.setattr(result_merge.git, "get_branch_merge_group_claim", lambda flow: None)
    monkeypatch.setattr(result_merge.workspace, "location", lambda row, project: (None, None, "card"))
    monkeypatch.setattr(result_merge.workspace, "attempt_head", lambda row, project: None)
    monkeypatch.setattr(result_merge.documents, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(result_merge.tr_commit_ledger, "by_attempt", lambda attempt_id: None)
    monkeypatch.setattr(result_merge, "_node", lambda row: {"result_doc_id": None})
    yield root
    result_merge._opened.pop("att", None)


def _card_and_latest(repo: Path, files: dict[str, tuple[str | None, str | None]]) -> str:
    """A card branch and the group branch both changing ``files`` from base: {path: (card text, latest text)};
    None deletes the file on that side. Returns the card's result commit (the group branch stays checked out)."""
    added = [path for path in files if not (repo / path).exists()]
    for path in added:
        (repo / path).write_text("base\n", encoding="utf-8")
    if added:
        _commit(repo, "base files")
    _git(["checkout", "-b", "card"], repo)
    for path, (card, _latest) in files.items():
        if card is None:
            (repo / path).unlink()
        else:
            (repo / path).write_text(card, encoding="utf-8")
    result = _commit(repo, "card result")
    _git(["checkout", "work"], repo)
    for path, (_card, latest) in files.items():
        if latest is None:
            (repo / path).unlink()
        else:
            (repo / path).write_text(latest, encoding="utf-8")
    _commit(repo, "latest group code")
    return result


def _row(result: str) -> dict:
    return {"group_id": _GROUP, "result_commit": result, "attempt_id": "att", "node_key": "node", "run_id": "run"}


def _session_files(merge_id: int) -> dict[str, bool]:
    return {f["path"]: bool(f.get("resolved")) for f in db_git.session_files(merge_id)}


def _review_state(merge_id: int) -> str:
    return db_git.session_context(db_git.get_session(merge_id)).get("review_state") or "open"


@needs_git
def test_deferred_then_resolution_opens_a_real_group_session(real_store, merge_on_repo):
    repo = merge_on_repo
    result = _card_and_latest(repo, {"f.txt": ("card\n", "latest\n")})
    head = _git(["rev-parse", "HEAD"], repo).strip()

    # the merge finds the conflict and undoes it: no session row, the group worktree clean and free
    assert result_merge._merge(_row(result)) == (result_merge.DEFERRED, {"files": ["f.txt"],
                                                                        "file_kinds": {"f.txt": "both"}})
    assert db_git.get_open_session_by_group(_GROUP) is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == head
    assert _git(["status", "--porcelain", "--untracked-files=no"], repo).strip() == ""

    # a person's choice asks it again keeping the conflict: a real graph_attempt_merge session on the group
    status, merge_id = result_merge._merge(_row(result), keep_session=True)
    assert status == result_merge.CONFLICT
    session = db_git.get_session(merge_id)
    assert session["status"] == "open" and db_git.session_kind(session) == result_merge.SESSION_KIND
    assert db_git.session_context(session)["node_key"] == "node"
    assert _git(["rev-parse", "MERGE_HEAD"], repo).strip() == result
    assert _session_files(merge_id) == {"f.txt": False}


@needs_git
@pytest.mark.parametrize("side, expected", [("card", "card\n"), ("latest", "latest\n")])
def test_a_whole_side_pick_then_merge_commits_that_side(real_store, merge_on_repo, side, expected):
    repo = merge_on_repo
    result = _card_and_latest(repo, {"f.txt": ("card\n", "latest\n")})
    latest = _git(["rev-parse", "HEAD"], repo).strip()
    _status, merge_id = result_merge._merge(_row(result), keep_session=True)

    service._apply_picks(_GROUP, merge_id, [{"path": "f.txt", "side": side}])

    # every file resolved: the tab's [병합] (view: review_state resolved, no unresolved file)
    assert _session_files(merge_id) == {"f.txt": True}
    assert _review_state(merge_id) == "resolved"
    assert "<<<<<<<" not in (repo / "f.txt").read_text(encoding="utf-8")

    out = trc.commit_conflict_resolution(_GROUP, merge_id)
    assert out["result"]["status"] == "committed"
    sha = _git(["rev-parse", "HEAD"], repo).strip()
    assert _git(["show", f"{sha}:f.txt"], repo) == expected
    assert set(_git(["log", "-1", "--format=%P", sha], repo).split()) == {latest, result}   # a real merge commit
    assert db_git.get_session(merge_id)["status"] == "done"
    assert db_git.get_open_session_by_group(_GROUP) is None
    assert _git(["status", "--porcelain", "--untracked-files=no"], repo).strip() == ""


@needs_git
def test_merge_is_not_offered_until_every_file_is_resolved(real_store, merge_on_repo):
    repo = merge_on_repo
    result = _card_and_latest(repo, {"a.txt": ("card a\n", "latest a\n"), "b.txt": ("card b\n", "latest b\n")})
    _status, merge_id = result_merge._merge(_row(result), keep_session=True)

    service._apply_picks(_GROUP, merge_id, [{"path": "a.txt", "side": "card"}])
    assert _session_files(merge_id) == {"a.txt": True, "b.txt": False}
    assert _review_state(merge_id) != "resolved"
    assert "<<<<<<<" in (repo / "b.txt").read_text(encoding="utf-8")

    service._apply_picks(_GROUP, merge_id, [{"path": "b.txt", "side": "latest"}])
    assert _review_state(merge_id) == "resolved"
    trc.commit_conflict_resolution(_GROUP, merge_id)
    assert _git(["show", "HEAD:a.txt"], repo) == "card a\n"
    assert _git(["show", "HEAD:b.txt"], repo) == "latest b\n"


@needs_git
def test_a_side_that_deleted_the_file_cannot_be_picked(real_store, merge_on_repo):
    repo = merge_on_repo
    result = _card_and_latest(repo, {"gone.txt": ("card edit\n", None)})
    assert result_merge._merge(_row(result))[1]["file_kinds"] == {"gone.txt": "deleted_latest"}
    _status, merge_id = result_merge._merge(_row(result), keep_session=True)
    assert result_merge.conflict_kinds(repo) == {"gone.txt": "deleted_latest"}
    before = (repo / "gone.txt").read_text(encoding="utf-8")

    with pytest.raises(GraphApiError) as exc:
        service._apply_picks(_GROUP, merge_id, [{"path": "gone.txt", "side": "latest"}])
    assert exc.value.code == "pick_unavailable"
    assert (repo / "gone.txt").read_text(encoding="utf-8") == before
    assert db_git.get_session(merge_id)["status"] == "open"


@needs_git
def test_abort_from_discard_leaves_the_group_as_it_was(real_store, merge_on_repo):
    """[이 결과 버리고 최신 코드로 다시 실행]'s first half: the open session is aborted, the group back at its head."""
    repo = merge_on_repo
    result = _card_and_latest(repo, {"f.txt": ("card\n", "latest\n")})
    head = _git(["rev-parse", "HEAD"], repo).strip()
    _status, merge_id = result_merge._merge(_row(result), keep_session=True)

    trc.abort_conflict_resolution(_GROUP, merge_id)
    assert db_git.get_session(merge_id)["status"] != "open"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head
    assert (repo / "f.txt").read_text(encoding="utf-8") == "latest\n"
    assert _git(["status", "--porcelain", "--untracked-files=no"], repo).strip() == ""
