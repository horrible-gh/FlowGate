"""flowgate.default.0152 — embedded test-run runner src_root resolution.

The runner used to pass doc.project_id straight into src_root(project_name,
branch), resolving a nonexistent directory whenever project_id differs from
the projects-table project_name (every embedded run died with
src_root_missing). These tests pin the corrected resolution path WITHOUT
monkeypatching src_root itself, so an id/name mixup cannot hide again.
"""
from __future__ import annotations

from fastapi import HTTPException


def _install_project_row(monkeypatch, *, settings):
    from modules.flow_gate.db import projects as db_projects

    monkeypatch.setattr(
        db_projects,
        "get_by_id",
        lambda pid: (
            {"project_id": pid, "project_name": "FlowGate Live"}
            if pid == "flowgate"
            else None
        ),
    )
    monkeypatch.setattr(db_projects, "get_settings", lambda _pid: settings)


def test_resolver_uses_project_name_and_settings_branch(monkeypatch, tmp_path):
    from modules.flow_gate.storage import paths as storage_paths

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    _install_project_row(monkeypatch, settings={"branch": "dev"})
    mirror = tmp_path / "src" / "FlowGate Live" / "dev"
    mirror.mkdir(parents=True)

    resolved = storage_paths.resolve_project_src_root("flowgate", "main")

    assert resolved == mirror.resolve()
    # The buggy shape (project_id as the directory name) must NOT be produced.
    assert resolved != (tmp_path / "src" / "flowgate" / "main").resolve()


def test_resolver_falls_back_to_given_branch_without_settings(monkeypatch, tmp_path):
    from modules.flow_gate.storage import paths as storage_paths

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    _install_project_row(monkeypatch, settings=None)

    resolved = storage_paths.resolve_project_src_root("flowgate", "topic")

    assert resolved == (tmp_path / "src" / "FlowGate Live" / "topic").resolve()


def test_resolver_returns_none_for_unknown_project(monkeypatch, tmp_path):
    from modules.flow_gate.storage import paths as storage_paths

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    _install_project_row(monkeypatch, settings=None)

    assert storage_paths.resolve_project_src_root("no-such-project") is None
    assert storage_paths.resolve_project_src_root(None) is None


def test_execute_run_goes_through_resolver(monkeypatch):
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "branch": "main",
            "group_id": "flowgate.default.0152",
        },
    )
    finished: dict = {}
    monkeypatch.setattr(
        test_run_service.db_test_runs,
        "finish_run",
        lambda **kw: finished.update(kw),
    )
    monkeypatch.setattr(test_run_service.db_test_runs, "get_run", lambda _rid: None)
    monkeypatch.setattr(test_run_service, "_emit_finished", lambda *_a, **_k: None)

    calls: dict = {}

    def fake_resolver(project_id, fallback_branch="main", group_id=None):
        calls["args"] = (project_id, fallback_branch)
        calls["group_id"] = group_id
        return None

    monkeypatch.setattr(
        test_run_service.storage_paths, "resolve_project_src_root", fake_resolver
    )

    test_run_service.execute_run({"run_id": "trun_0152", "doc_id": "doc-1"})

    assert calls["args"] == ("flowgate", "main")
    # B0001 (0190): the runner must forward group_id so a git-integrated group
    # resolves to its own worktree (work branch) instead of base(main).
    assert calls["group_id"] == "flowgate.default.0152"
    assert finished["status"] == "failed"
    assert finished["error"] == "src_root_missing"


def test_validate_and_create_run_rejects_missing_src_root_at_admission(monkeypatch):
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "branch": "main",
            "group_id": "flowgate.default.0152",
            "type_code": "TS",
            "doc_review_status": "approved",
        },
    )
    monkeypatch.setattr(
        test_run_service.process_service, "is_group_disposed", lambda _g: False
    )
    monkeypatch.setattr(
        test_run_service.storage_paths,
        "resolve_project_src_root",
        lambda *_a, **_k: None,
    )

    try:
        test_run_service.validate_and_create_run(
            doc_id="doc-1", runner_id="usr", triggered_via="token"
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail["error"] == "src_root_missing"
    else:
        raise AssertionError("expected 422 src_root_missing")

def test_validate_and_create_run_allows_revised_with_prior_run(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    src_root = tmp_path / "src"
    src_root.mkdir()
    doc = {
        "doc_id": "doc-1",
        "project_id": "flowgate",
        "branch": "main",
        "group_id": "flowgate.default.0169",
        "type_code": "TS",
        "doc_review_status": "revised",
        "revision_no": 2,
    }
    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(
        test_run_service.process_service, "is_group_disposed", lambda _g: False
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs,
        "list_by_doc",
        lambda _doc_id: [{"run_id": "trun_prior"}],
    )
    monkeypatch.setattr(
        test_run_service.storage_paths,
        "resolve_project_src_root",
        lambda *_a, **_k: src_root,
    )
    monkeypatch.setattr(
        test_run_service,
        "_read_doc_content",
        lambda _doc: "\n".join(
            [
                "## 테스트 케이스",
                "",
                "### TC-1: smoke",
                "- cmd: `python --version`",
                "- 기대: exits 0",
            ]
        ),
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs, "get_running_by_doc", lambda _doc_id: None
    )
    inserted: dict = {}

    def insert_run(**kwargs):
        inserted.update(kwargs)
        return {
            "run_id": "trun_new",
            "doc_id": kwargs["doc_id"],
            "revision_no": kwargs["revision_no"],
            "status": "running",
            "case_total": len(kwargs["cases"]),
            "setup_total": 0,
            "teardown_total": 0,
            "started_at": "2026-07-07T00:00:00+09:00",
        }

    monkeypatch.setattr(test_run_service.db_test_runs, "insert_run", insert_run)
    monkeypatch.setattr(test_run_service, "_emit_started", lambda *_a, **_k: None)

    result = test_run_service.validate_and_create_run(
        doc_id="doc-1", runner_id="usr", triggered_via="ui"
    )

    assert result["ok"] is True
    assert result["run_id"] == "trun_new"
    assert inserted["revision_no"] == 2
    assert inserted["cases"][0]["case_no"] == "TC-1"


def test_validate_and_create_run_rejects_revised_without_prior_run(monkeypatch):
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "branch": "main",
            "group_id": "flowgate.default.0169",
            "type_code": "TS",
            "doc_review_status": "revised",
        },
    )
    monkeypatch.setattr(
        test_run_service.process_service, "is_group_disposed", lambda _g: False
    )
    monkeypatch.setattr(test_run_service.db_test_runs, "list_by_doc", lambda _doc_id: [])

    try:
        test_run_service.validate_and_create_run(
            doc_id="doc-1", runner_id="usr", triggered_via="ui"
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["error"] == "doc_not_approved"
        assert exc.detail["doc_review_status"] == "revised"
    else:
        raise AssertionError("expected 409 doc_not_approved")
