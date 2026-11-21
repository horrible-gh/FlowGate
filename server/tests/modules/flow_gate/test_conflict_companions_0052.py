"""card B: real target-side re-export move, HTTP resolve and merge approval."""
import itertools
import os
import json

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
import uuid

import pytest

from test_git_integration_0115 import (
    tmp_db, patch_store, seed, origin_repo, needs_git, _git,
    _seed_wf_done_root, TestGitEndToEnd as _ExistingE2E,
)
from modules.flow_gate.services import git_service as svc
from modules.flow_gate.services.git import conflict_companions
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.storage.paths import src_root
from modules.flow_gate.api import token_routes
from modules.flow_gate.services.ai_invoke import runtime, worker

BASE = "// original comment\n// second comment\n" + "".join(f"export const v{i} = {i};\n" for i in range(10))
INCOMING = BASE.replace("original comment", "updated group comment").replace("second comment", "group second comment")
REEXPORT = "export * from './resources/F';\n"


_groups = itertools.count(52)


@pytest.fixture
def moved(origin_repo):
    suffix = f"{next(_groups):04d}"
    group = "gitprj.default." + suffix
    seedwt = origin_repo["seedwt"]
    (seedwt / "F.ts").write_text(BASE, encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "base file"], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)
    assert svc.ensure_worktree("gitprj", "default", group) == "ok"
    wt = src_root("GitProj", "gitprj_default_" + suffix)
    (wt / "F.ts").write_text(INCOMING, encoding="utf-8")
    (seedwt / "resources").mkdir()
    (seedwt / "resources/F.ts").write_text(BASE, encoding="utf-8")
    (seedwt / "unrelated.ts").write_text("export const unrelated = true;\n", encoding="utf-8")
    (seedwt / "F.ts").write_text(REEXPORT, encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "move into resources and re-export"], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)
    _seed_wf_done_root(group, project_id="gitprj")
    db_git.set_status(group, "awaiting_choice")
    out = svc.finalize(group, "merge")
    assert out["result"]["status"] == "conflict"
    mid = out["result"]["merge_id"]
    root = svc.resolve_conflict_src_root(group, mid)
    yield group, mid, root
    if db_git.get_session(mid)["status"] == "open":
        svc.abort_merge(group, mid)


def submission(extension="ts"):
    return [{"path": "F." + extension, "content": REEXPORT,
             "supersede": {"side": "ours", "reason": "incoming comments preserved in moved file"},
             "companion_files": [{"path": "resources/F." + extension, "content": INCOMING}]}]


@needs_git
def test_moved_file_http_resolution_and_merge(moved, monkeypatch):
    group, mid, root = moved
    info = svc.list_conflicts(group, mid)
    assert info["companion_candidates"] == {"F.ts": ["resources/F.ts"]}
    mention = token_routes._build_conflict_mention(
        group_id=group, project_id="gitprj", merge_id=mid, scratch_dir="/scratch",
        raw_token="test-token", api_base_url="http://localhost/flowgate/api/v1")
    assert '"companion_candidates"' in mention and "companion_files" in mention
    post = _ExistingE2E._resolve_token_client_0604(monkeypatch, group, mid)
    before = {p: (root / p).read_bytes() for p in ["F.ts", "resources/F.ts", "unrelated.ts"]}
    index = _git(["ls-files", "--stage"], cwd=root)
    invalid = []
    old = submission()
    old[0].pop("companion_files")
    old.append({"path": "resources/F.ts", "content": INCOMING})
    invalid.append(old)  # The old ordinary-files contract remains restricted.
    for path in ["unrelated.ts", "../F.ts", "/F.ts"]:
        row = submission()
        row[0]["companion_files"][0]["path"] = path
        invalid.append(row)
    dropped = submission()
    dropped[0]["companion_files"][0]["content"] = BASE
    invalid.append(dropped)
    markers = submission()
    markers[0]["companion_files"][0]["content"] = "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n"
    invalid.append(markers)
    duplicate = submission()
    duplicate[0]["companion_files"] *= 2
    invalid.append(duplicate)
    for bad in invalid:
        response = post(bad)
        assert response.status_code == 422, response.text
        assert {p: (root / p).read_bytes() for p in before} == before
        assert _git(["ls-files", "--stage"], cwd=root) == index
    accepted = post(submission())
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()["result"]
    assert result["status"] == "resolved_pending_review"
    assert (root / "resources/F.ts").read_text() == INCOMING
    assert (root / "F.ts").read_text() == REEXPORT
    records = [{"path": "resources/F.ts", "source_path": "F.ts"}]
    assert svc.list_conflicts(group, mid)["companion_files"] == records
    review = svc.get_merge_review(group, mid)["result"]
    assert review["companion_files"] == records
    assert review["conflict_supersedes"][0]["chunks"][0]["companion_path"] == "resources/F.ts"
    # Exercise the real pre-commit dispatch with only the external parser stubbed.
    # Parser failure must keep the merge uncommitted; retry checks both TS files.
    checked = []
    def rejecting_parser(ext, text):
        checked.append((ext, text))
        return {"line": 1, "message": "test parser rejection"}
    monkeypatch.setattr(svc, "_run_node_syntax_check", rejecting_parser)
    head_before = _git(["rev-parse", "HEAD"], cwd=root)
    rejected = svc.approve_merge_review(group, mid, attempt_id=str(uuid.uuid4()),
        review_fingerprint=result["review_fingerprint"], authority="human")
    assert rejected["result"]["status"] == "pre_commit_validation_failed", rejected
    assert _git(["rev-parse", "HEAD"], cwd=root) == head_before
    assert ("ts", INCOMING) in checked
    assert all(ext == "ts" for ext, _text in checked)
    checked.clear()
    def accepting_parser(ext, text):
        checked.append((ext, text))
        return None
    monkeypatch.setattr(svc, "_run_node_syntax_check", accepting_parser)
    approved = svc.approve_merge_review(group, mid, attempt_id=str(uuid.uuid4()),
        review_fingerprint=result["review_fingerprint"], authority="human")
    assert approved["result"]["status"] == "merged", approved
    assert ("ts", INCOMING) in checked
    assert all(ext == "ts" for ext, _text in checked)
    assert _git(["show", "HEAD:resources/F.ts"], cwd=root) == INCOMING
    assert len(_git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd=root).split()) == 3


def test_relation_rechecked_under_lock(portable_move, monkeypatch):
    root, _context, _resolved = portable_move
    group, mid = "g", 1
    original = conflict_companions.candidates
    calls = []
    def changing(root, paths):
        calls.append(True)
        return original(root, paths) if len(calls) == 1 else {}
    monkeypatch.setattr(conflict_companions, "candidates", changing)
    before = (root / "F.ts").read_bytes(), (root / "resources/F.ts").read_bytes()
    with pytest.raises(svc.GitServiceError) as exc:
        svc.resolve_conflicts(group, mid, submission(), True)
    assert exc.value.code == "conflict_session_changed"
    assert before == ((root / "F.ts").read_bytes(), (root / "resources/F.ts").read_bytes())


def test_worker_forwards_nested_companions(monkeypatch):
    from modules.flow_gate.services.ai_invoke import provider_api
    sent = []
    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":true}'
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda req, **kw: sent.append(json.loads(req.data)) or Reply())
    monkeypatch.setattr(provider_api, "_resolve_transport_api_base", lambda run: "http://localhost")
    monkeypatch.setattr(svc, "merge_route_prefix", lambda *a: "/merge/1")
    data = {"files": submission(), "complete": True}
    status, _ = worker._resolve_conflict({"group_id": "g", "merge_id": 1, "transport_api_base": "http://localhost"}, "token", data)
    assert status == 200 and sent == [data]
    from modules.flow_gate.api.v1.git_routes import ResolveBody
    assert ResolveBody(**data).model_dump(exclude_none=True) == data
    assert "companion_files" in runtime._RESOLVE_TOOL_SCHEMA["properties"]["files"]["items"]["properties"]

@pytest.fixture
def portable_move(tmp_path, monkeypatch):
    """Real Git, without local file:// cloning (unsupported by Git for Windows).

    Only DB identity and workspace-lock acquisition are mocked; validation,
    working files, index and merge commit all use the production resolver.
    """
    from modules.flow_gate.services.git import branch_merge_publish
    root = tmp_path
    _git(["init", "-b", "main"], cwd=root)
    _git(["config", "merge.conflictStyle", "diff3"], cwd=root)
    _git(["config", "user.name", "Move Test"], cwd=root)
    _git(["config", "user.email", "move@example.test"], cwd=root)
    (root / "F.ts").write_text(BASE, encoding="utf-8")
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "base"], cwd=root)
    _git(["checkout", "-b", "incoming"], cwd=root)
    (root / "F.ts").write_text(INCOMING, encoding="utf-8")
    _git(["commit", "-am", "group comments"], cwd=root)
    _git(["checkout", "main"], cwd=root)
    (root / "resources").mkdir()
    (root / "resources/F.ts").write_text(BASE, encoding="utf-8")
    (root / "unrelated.ts").write_text("unrelated\n", encoding="utf-8")
    (root / "F.ts").write_text(REEXPORT, encoding="utf-8")
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "target move"], cwd=root)
    proc = svc._run_git(["merge", "--no-commit", "incoming"], cwd=root)
    assert proc.returncode == 1 and (root / "F.ts").is_file()
    session = {"id": 1, "group_id": "g", "status": "open", "kind": "group_update"}
    context, resolved = {}, set()
    monkeypatch.setattr(svc, "_session_context", lambda *a, **kw: (session, {}, "p", root))
    monkeypatch.setattr(db_git, "session_kind", lambda s: "group_update")
    monkeypatch.setattr(db_git, "touch_session", lambda *a: None)
    monkeypatch.setattr(db_git, "session_context", lambda s: dict(context))
    monkeypatch.setattr(db_git, "set_session_context", lambda mid, c: context.update(c))
    monkeypatch.setattr(db_git, "get_session", lambda mid: session)
    monkeypatch.setattr(db_git, "session_files", lambda mid: [{"path": "F.ts", "resolved": "F.ts" in resolved}])
    monkeypatch.setattr(db_git, "mark_file_resolved", lambda mid, p: resolved.add(p))
    monkeypatch.setattr(db_git, "remaining_conflicts", lambda mid: [] if resolved else ["F.ts"])
    monkeypatch.setattr(db_git, "close_session", lambda mid, status: session.update(status=status))
    monkeypatch.setattr(branch_merge_publish, "session_lock", lambda *a, **kw: (None, None))
    monkeypatch.setattr(branch_merge_publish, "session_unlock", lambda *a: None)
    return root, context, resolved


def test_portable_move_reaches_real_merge_commit(portable_move):
    root, context, resolved = portable_move
    assert conflict_companions.candidates(root, {"F.ts"}) == {"F.ts": ["resources/F.ts"]}
    out = svc.resolve_conflicts("g", 1, submission(), True)
    assert out["result"]["status"] == "updated"
    assert resolved == {"F.ts"}
    assert context["conflict_companions"] == [{"path": "resources/F.ts", "source_path": "F.ts"}]
    assert _git(["show", "HEAD:resources/F.ts"], cwd=root) == INCOMING
    assert _git(["show", "HEAD:F.ts"], cwd=root) == REEXPORT
    assert len(_git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd=root).split()) == 3


@pytest.mark.parametrize("fault", ["unrelated", "escape", "markers", "drop", "duplicate", "no_declaration"])
def test_portable_bad_companion_writes_nothing(portable_move, fault):
    root, context, resolved = portable_move
    files = submission()
    companion = files[0]["companion_files"][0]
    if fault == "unrelated": companion["path"] = "unrelated.ts"
    if fault == "escape": companion["path"] = "../F.ts"
    if fault == "markers": companion["content"] = "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> incoming\n"
    if fault == "drop": companion["content"] = BASE
    if fault == "duplicate": files[0]["companion_files"] *= 2
    if fault == "no_declaration": files[0].pop("supersede")
    before = {p: (root / p).read_bytes() for p in ["F.ts", "resources/F.ts", "unrelated.ts"]}
    index = _git(["ls-files", "--stage"], cwd=root)
    with pytest.raises(svc.GitServiceError) as exc:
        svc.resolve_conflicts("g", 1, files, True)
    assert exc.value.status == 422
    assert before == {p: (root / p).read_bytes() for p in before}
    assert _git(["ls-files", "--stage"], cwd=root) == index
    assert not resolved and not context


def test_portable_companion_content_rechecked(portable_move, monkeypatch):
    root, context, resolved = portable_move
    from modules.flow_gate.services.git import branch_merge_publish
    def lock(*args, **kwargs):
        (root / "resources/F.ts").write_text("concurrent edit\n", encoding="utf-8")
        return None, None
    monkeypatch.setattr(branch_merge_publish, "session_lock", lock)
    before = (root / "F.ts").read_bytes()
    with pytest.raises(svc.GitServiceError) as exc:
        svc.resolve_conflicts("g", 1, submission(), True)
    assert exc.value.code == "conflict_source_changed"
    assert (root / "F.ts").read_bytes() == before
    assert (root / "resources/F.ts").read_text() == "concurrent edit\n"
    assert not resolved


@pytest.mark.parametrize("lines", [["// TODO"], ["", "  // TODO  ", " "], ["", "  "]])
def test_short_common_chunk_is_not_relocation_evidence(lines):
    chunk = {"theirs": lines}
    companion = {"path": "resources/F.ts", "content": "\n  // TODO  \n \n"}
    assert conflict_companions.relocated_evidence(chunk, "ours", [companion]) is None


def test_relocation_requires_contiguous_ordered_nonblank_context():
    chunk = {"theirs": ["// changed one", "// changed two"]}
    for content in ["// changed two\n// changed one\n",
                    "// changed one\n// unrelated\n// changed two\n"]:
        assert conflict_companions.relocated_evidence(chunk, "ours", [
            {"path": "resources/F.ts", "content": content}]) is None
    evidence = conflict_companions.relocated_evidence(chunk, "ours", [
        {"path": "resources/F.ts", "content": "  // changed one  \n// changed two\n"}])
    assert evidence["companion_path"] == "resources/F.ts"


@pytest.mark.parametrize("path", [[], {}, None, 42])
def test_invalid_resolution_path_rejected_before_companion_lookup(portable_move, path):
    root, context, resolved = portable_move
    files = submission()
    files[0]["path"] = path
    before = (root / "F.ts").read_bytes(), (root / "resources/F.ts").read_bytes()
    index = _git(["ls-files", "--stage"], cwd=root)
    with pytest.raises(svc.GitServiceError) as exc:
        svc.resolve_conflicts("g", 1, files, True)
    assert exc.value.status == 422 and exc.value.code == "invalid_request"
    assert before == ((root / "F.ts").read_bytes(), (root / "resources/F.ts").read_bytes())
    assert _git(["ls-files", "--stage"], cwd=root) == index
    assert not context and not resolved
