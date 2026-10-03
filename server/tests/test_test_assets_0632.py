"""T#2 manifest, compat identity, CAS and approved TSR regressions."""
from __future__ import annotations

import hashlib
import subprocess
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import test_asset_service as assets
from modules.flow_gate.services import test_basis_service as basis


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "server").mkdir()
    (root / "tests" / "test_a.py").write_text("def test_a(): assert True\n")
    (root / "tests" / "fixture.json").write_text('{"v":1}\n')
    (root / "server" / "app.py").write_text("VALUE = 1\n")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    monkeypatch.setattr(basis, "source_root", lambda doc: root)
    return root


def _doc():
    return {"doc_id": "flowgate.default.0632.0010-TS", "revision_no": 1,
            "doc_review_status": "approved"}


def _cases():
    return [{"case_id": "TC-001", "execution_mode": "automated",
             "automation_ref": "tests/test_a.py::test_a",
             "test_assets": "tests/fixture.json"}]


def test_manifest_and_compat_test_dirty_are_separate_from_product_source(repo):
    first = basis.resolve(_doc(), _cases())
    assert [(a["path"], a["role"]) for a in first["manifest"]] == [
        ("tests/fixture.json", "fixture"), ("tests/test_a.py", "test")]
    (repo / "tests" / "fixture.json").write_text('{"v":2}\n')
    second = basis.resolve(_doc(), _cases())
    assert first["source"] == second["source"]
    assert first["basis_id"] != second["basis_id"]
    assert second["compat_dirty_paths"] == ["tests/fixture.json"]
    (repo / "server" / "app.py").write_text("VALUE = 2\n")
    with pytest.raises(ValueError, match="source_worktree_dirty"):
        basis.resolve(_doc(), _cases())


def test_product_path_cannot_enter_manifest(repo):
    cases = _cases()
    cases[0]["test_assets"] = "server/app.py"
    with pytest.raises(ValueError, match="product_source_or_invalid_test_asset"):
        basis.resolve(_doc(), cases)


def test_asset_read_and_cas_rejection_preserve_bytes_and_basis(repo, monkeypatch):
    doc = _doc()
    stored = basis.resolve(doc, _cases())
    monkeypatch.setattr(assets, "_load", lambda ts_id: (doc, {"cases": _cases()}, stored))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *args: None)
    path = "tests/fixture.json"
    before = (repo / path).read_bytes()
    assert assets.content(doc["doc_id"], path)["content_hash"] == hashlib.sha256(before).hexdigest()
    with pytest.raises(HTTPException) as bad_hash:
        assets.update(doc["doc_id"], path, expected_hash="0" * 64,
                      content='{"v":3}', actor_id="u")
    assert bad_hash.value.status_code == 409
    with pytest.raises(HTTPException) as outside:
        assets.update(doc["doc_id"], "server/app.py", expected_hash="0" * 64,
                      content="VALUE=3", actor_id="u")
    assert outside.value.status_code == 403
    assert (repo / path).read_bytes() == before
    assert (repo / "server/app.py").read_text() == "VALUE = 1\n"
    assert stored["basis_id"] == basis.resolve(doc, _cases())["basis_id"]


def test_symlinked_manifest_asset_cannot_reach_product_source(repo, monkeypatch):
    doc = _doc()
    stored = basis.resolve(doc, _cases())
    monkeypatch.setattr(assets, "_load", lambda ts_id: (doc, {"cases": _cases()}, stored))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *args: None)
    fixture = repo / "tests" / "fixture.json"
    fixture.unlink()
    try:
        fixture.symlink_to(repo / "server" / "app.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    product_before = (repo / "server" / "app.py").read_bytes()
    with pytest.raises(HTTPException) as blocked:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=stored["manifest"][0]["content_hash"],
                      content="VALUE = 2\n", actor_id="u")
    assert blocked.value.status_code == 409
    assert (repo / "server" / "app.py").read_bytes() == product_before
    assert fixture.is_symlink()


def test_approved_tsr_put_is_immutable(repo, monkeypatch):
    doc = _doc()
    stored = basis.resolve(doc, _cases())
    paired = {"doc_id": "flowgate.default.0632.0011-TSR",
              "doc_review_status": "approved", "revision_no": 7}
    monkeypatch.setattr(assets, "_load", lambda ts_id: (doc, {"cases": _cases()}, stored))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: paired)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *args: None)
    before = (repo / "tests" / "fixture.json").read_bytes()
    with pytest.raises(HTTPException) as blocked:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":3}', actor_id="u")
    assert blocked.value.status_code == 409
    assert (repo / "tests" / "fixture.json").read_bytes() == before
    assert paired["revision_no"] == 7
    assert basis.resolve(doc, _cases())["basis_id"] == stored["basis_id"]


def test_reopened_tsr_edit_creates_successor_and_invalidation(repo, monkeypatch):
    doc = _doc()
    stored = basis.resolve(doc, _cases())
    paired = {"doc_id": "flowgate.default.0632.0011-TSR",
              "doc_review_status": "pending_review", "revision_no": 7}
    state = {"basis": stored, "events": []}
    monkeypatch.setattr(assets, "_load", lambda ts_id: (doc, {"cases": _cases()}, state["basis"]))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: paired)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *args: None)
    monkeypatch.setattr(assets.db_docs, "get_by_id", lambda ts_id: doc)
    monkeypatch.setattr(assets.db_docs, "update", lambda ts_id, fields: {**doc, **fields})
    monkeypatch.setattr(assets.test_basis_service, "initialize",
                        lambda updated, parsed, successor, locale="ko":
                        {"run_id": "initial-B", "tsr_doc_id": paired["doc_id"]})
    monkeypatch.setattr(assets.db_events, "insert_event",
                        lambda *args, **kwargs: state["events"].append(args[1]))

    class Store:
        @contextmanager
        def transaction(self):
            yield
    monkeypatch.setattr(assets, "get_store", lambda: Store())
    old = (repo / "tests" / "fixture.json").read_bytes()
    response = assets.update(doc["doc_id"], "tests/fixture.json",
                             expected_hash=hashlib.sha256(old).hexdigest(),
                             content='{"v":2}\n', actor_id="u")
    assert response["basis_id"] != stored["basis_id"]
    assert response["tsr_doc_id"] == paired["doc_id"]
    assert "test_spec_results_invalidated" in state["events"]
    assert "test_spec_asset_updated" in state["events"]


def test_product_defect_cannot_use_test_asset_editor(repo, monkeypatch):
    doc = _doc()
    stored = basis.resolve(doc, _cases())
    monkeypatch.setattr(assets, "_load", lambda ts_id: (doc, {"cases": _cases()}, stored))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result",
                        lambda *args: {"failure_origin": "product_defect"})
    before = (repo / "tests" / "fixture.json").read_bytes()
    with pytest.raises(HTTPException) as error:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":2}', actor_id="u")
    assert error.value.status_code == 409
    assert (repo / "tests" / "fixture.json").read_bytes() == before


def test_tr_completion_has_no_formal_test_gate():
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.services import help_catalog
    pipeline_service._require_test_gate_for_approval({"type_code": "TR"})
    guide = help_catalog._authoring_guide_body("TR", "en")
    assert "no formal PASS" in guide
    assert "TS/TSR" in guide


def test_compat_execution_copy_overlays_only_manifest_assets(repo, tmp_path, monkeypatch):
    from modules.flow_gate.services import spec_execution_service as execution
    from modules.flow_gate.services import test_run_service as runner
    from modules.flow_gate.services import test_spec_service as spec
    doc = _doc()
    (repo / "tests" / "fixture.json").write_text('{"v":2}\n')
    current = basis.resolve(doc, _cases())
    monkeypatch.setattr(spec, "parse_spec", lambda content: {"cases": _cases()})
    monkeypatch.setattr(runner, "_read_doc_content_or_empty", lambda doc: "spec")
    monkeypatch.setattr(runner, "_scratch_dir", lambda doc, run_id: tmp_path / "scratch")
    copied, scratch = execution.ExecutionRootResolver.prepare(doc, {"run_id": "run-1"}, current)
    assert copied == scratch / "source"
    assert (copied / "tests" / "fixture.json").read_text() == '{"v":2}\n'
    assert (copied / "server" / "app.py").read_text() == "VALUE = 1\n"
    (repo / "server" / "app.py").write_text("VALUE = 2\n")
    with pytest.raises(ValueError, match="source_worktree_dirty"):
        execution.ExecutionRootResolver.prepare(doc, {"run_id": "run-2"}, current)


def test_asset_http_routes_bind_manifest_content_and_cas(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import test_run_routes as routes

    ts_id = "flowgate.default.0632.0010-TS"
    calls = []
    monkeypatch.setattr(routes, "verify_bearer", lambda request: {
        "_is_user_jwt": True, "issued_to": "editor", "is_admin": False,
    })
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": ts_id, "project_id": "flowgate",
    })
    monkeypatch.setattr(routes.test_run_service, "user_can_run_tests", lambda *a: True)
    monkeypatch.setattr(assets, "manifest", lambda doc_id: {"doc_id": doc_id, "assets": []})
    monkeypatch.setattr(assets, "content", lambda doc_id, path: {
        "doc_id": doc_id, "path": path, "content": "before",
    })
    def update(doc_id, path, **kwargs):
        calls.append((doc_id, path, kwargs))
        return {"path": path, "content_hash": "new-hash"}
    monkeypatch.setattr(assets, "update", update)

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    root = f"/api/v1/documents/{ts_id}/test-spec/assets"
    assert client.get(root).json() == {"doc_id": ts_id, "assets": []}
    assert client.get(root + "/tests/fixture.json").json()["content"] == "before"
    response = client.put(root + "/tests/fixture.json", headers={"x-locale": "en"},
                          json={"expected_hash": "old-hash", "content": "after"})
    assert response.status_code == 200 and response.json()["content_hash"] == "new-hash"
    assert calls == [(ts_id, "tests/fixture.json", {
        "expected_hash": "old-hash", "content": "after", "actor_id": "editor", "locale": "en",
    })]
    monkeypatch.setattr(assets, "update", lambda *a, **kw: (_ for _ in ()).throw(
        HTTPException(status_code=409, detail={"error": "expected_hash_mismatch"})))
    rejected = client.put(root + "/tests/fixture.json",
                          json={"expected_hash": "stale-hash", "content": "after"})
    assert rejected.status_code == 409
    assert rejected.json() == {"error": "expected_hash_mismatch"}
