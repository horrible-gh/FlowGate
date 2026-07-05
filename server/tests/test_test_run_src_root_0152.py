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

    def fake_resolver(project_id, fallback_branch="main"):
        calls["args"] = (project_id, fallback_branch)
        return None

    monkeypatch.setattr(
        test_run_service.storage_paths, "resolve_project_src_root", fake_resolver
    )

    test_run_service.execute_run({"run_id": "trun_0152", "doc_id": "doc-1"})

    assert calls["args"] == ("flowgate", "main")
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
