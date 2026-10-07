"""T#2 manifest, Source Bundle identity, CAS and approved TSR regressions.

flowgate.default.0682 T#1 replaced the compat identity (HEAD + dirty refusal + manifest
overlay on ``git archive HEAD``) with a Basis captured into a Source Bundle; these cases
keep the 0632 contract (manifest membership authorizes edits, CAS, approved TSR
immutability, product_defect lock-out) on top of it.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException

from basis_bundle_support import CASES, BasisEnv
from modules.flow_gate.services import test_asset_service as assets
from modules.flow_gate.services import test_basis_service as basis


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = BasisEnv(tmp_path, monkeypatch)
    environment.repo("g1")
    monkeypatch.setattr(basis, "source_root", lambda doc: environment.roots[doc["group_id"]])
    return environment


@pytest.fixture
def repo(env):
    return env.roots["g1"]


def _approved(env, monkeypatch, paired=None, latest=None):
    doc = env.ts()
    stored = env.approve(doc)
    monkeypatch.setattr(assets, "_load", lambda ts_id: (
        env.doc(ts_id), {"cases": env.cases}, basis.current(env.doc(ts_id))))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda doc: paired)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *args: latest)
    monkeypatch.setattr(assets.db_test_runs, "get_running_by_doc", lambda ts_id: None)
    return doc, stored


def test_manifest_roles_and_product_dirty_no_longer_refuse(env, repo):
    (repo / "server" / "app.py").write_text("VALUE = 2\n")
    first = basis.capture(env.ts(), CASES)
    assert [(a["path"], a["role"]) for a in first["manifest"]] == [
        ("tests/fixture.json", "fixture"), ("tests/test_a.py", "test")]
    assert first["binding"]["source_dirty"] is True
    (repo / "tests" / "fixture.json").write_text('{"v":2}\n')
    second = basis.capture(env.ts(), CASES)
    assert first["basis_id"] != second["basis_id"]
    assert first["test_assets"]["manifest_hash"] != second["test_assets"]["manifest_hash"]


def test_product_path_cannot_enter_manifest(env):
    cases = [{**CASES[0], "test_assets": "server/app.py"}]
    with pytest.raises(ValueError, match="product_source_or_invalid_test_asset"):
        basis.capture(env.ts(), cases)


def test_asset_read_and_cas_rejection_preserve_bytes_and_basis(env, repo, monkeypatch):
    doc, stored = _approved(env, monkeypatch)
    path = "tests/fixture.json"
    before = (repo / path).read_bytes()
    assert assets.content(doc["doc_id"], path)["content_hash"] == hashlib.sha256(before).hexdigest()
    listed = assets.manifest(doc["doc_id"])
    assert listed["basis_valid"] is True and listed["basis_verdict"]["state"] == "valid"
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
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID


def test_symlinked_manifest_asset_cannot_reach_product_source(env, repo, monkeypatch):
    doc, stored = _approved(env, monkeypatch)
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


def test_approved_tsr_put_is_immutable(env, repo, monkeypatch):
    paired = {"doc_id": "flowgate.default.0632.0011-TSR",
              "doc_review_status": "approved", "revision_no": 7}
    doc, stored = _approved(env, monkeypatch, paired=paired)
    before = (repo / "tests" / "fixture.json").read_bytes()
    with pytest.raises(HTTPException) as blocked:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":3}', actor_id="u")
    assert blocked.value.status_code == 409
    assert (repo / "tests" / "fixture.json").read_bytes() == before
    assert paired["revision_no"] == 7
    assert basis.current(env.doc(doc["doc_id"])) == stored


def test_reopened_tsr_edit_creates_successor_and_invalidation(env, repo, monkeypatch):
    paired = {"doc_id": "flowgate.default.0632.0011-TSR",
              "doc_review_status": "pending_review", "revision_no": 7}
    doc, stored = _approved(env, monkeypatch, paired=paired)
    old = (repo / "tests" / "fixture.json").read_bytes()
    response = assets.update(doc["doc_id"], "tests/fixture.json",
                             expected_hash=hashlib.sha256(old).hexdigest(),
                             content='{"v":2}\n', actor_id="u")
    assert response["basis_id"] != stored["basis_id"]
    assert response["tsr_doc_id"] == paired["doc_id"]
    # 0684 T#1: the edit starts no run and writes no zero-result initialization run.
    assert "initialization_run_id" not in response
    events = [event for _doc, event, _note in env.store.events]
    assert "test_spec_results_invalidated" in events
    assert "test_spec_asset_updated" in events


def test_product_defect_cannot_use_test_asset_editor(env, repo, monkeypatch):
    doc, _stored = _approved(env, monkeypatch, latest={"failure_origin": "product_defect"})
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


def test_execution_copy_is_the_captured_bundle_not_head_plus_overlay(env, repo, monkeypatch):
    from modules.flow_gate.services import spec_execution_service as execution
    (repo / "tests" / "fixture.json").write_text('{"v":2}\n')
    (repo / "server" / "untracked.py").write_text("U = 1\n")  # git archive HEAD would miss it
    doc, stored = _approved(env, monkeypatch)
    copied, scratch = execution.ExecutionRootResolver.prepare(
        env.doc(doc["doc_id"]), {"run_id": "run-1"}, stored)
    assert copied == scratch / "source"
    assert (copied / "tests" / "fixture.json").read_text() == '{"v":2}\n'
    assert (copied / "server" / "app.py").read_text() == "VALUE = 1\n"
    assert (copied / "server" / "untracked.py").read_text() == "U = 1\n"
    (repo / "server" / "app.py").write_text("VALUE = 2\n")
    with pytest.raises(ValueError, match="basis_stale_before_execution: source_changed"):
        execution.ExecutionRootResolver.prepare(env.doc(doc["doc_id"]), {"run_id": "run-2"}, stored)


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
