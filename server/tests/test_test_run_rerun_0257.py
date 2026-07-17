"""Characterization guard for group 0257 review-reopen test reruns.

These two pass against unmodified main, and are meant to: they pin admission behaviour main
already has, so they verify none of this group's changes. They exist because revision 1–2 of
TR0005 added a "pending_review + 직전 passed → 409" gate here that broke the normal reopen
rerun; the gate is gone and these fail if anyone re-adds it. The tests that actually pin the
B0001 fix are in test_tsr_slot_0257.py, and those are red without it.
"""
from __future__ import annotations


def test_pending_review_with_passed_prior_run_creates_fresh_run(monkeypatch, tmp_path):
    """A review reopen must not turn the previous passed run into a 409 gate."""
    from modules.flow_gate.services import test_run_service

    src_root = tmp_path / "src"
    src_root.mkdir()
    doc = {
        "doc_id": "flowgate.default.0257.0099-TS",
        "project_id": "flowgate",
        "branch": "main",
        "group_id": "flowgate.default.0257",
        "type_code": "TS",
        "doc_review_status": "pending_review",
        "revision_no": 3,
    }
    prior_passed = {
        "run_id": "trun_passed_before_reopen",
        "doc_id": doc["doc_id"],
        "status": "passed",
        "tsr_doc_id": "flowgate.default.0257.0100-TSR",
    }

    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(
        test_run_service.process_service, "is_group_disposed", lambda _group_id: False
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs, "list_by_doc", lambda _doc_id: [prior_passed]
    )
    monkeypatch.setattr(
        test_run_service.storage_paths,
        "resolve_project_src_root",
        lambda *_args, **_kwargs: src_root,
    )
    monkeypatch.setattr(
        test_run_service,
        "_read_doc_content",
        lambda _doc: "\n".join(
            [
                "## 테스트 케이스",
                "",
                "### TC-1: review reopen rerun",
                "- cmd: python --version",
                "- 기대: exits 0",
            ]
        ),
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs, "get_running_by_doc", lambda _doc_id: None
    )

    inserted = {}

    def insert_run(**kwargs):
        inserted.update(kwargs)
        return {
            "run_id": "trun_after_reopen",
            "doc_id": kwargs["doc_id"],
            "revision_no": kwargs["revision_no"],
            "status": "running",
            "case_total": len(kwargs["cases"]),
            "setup_total": len(kwargs["setup"]),
            "teardown_total": len(kwargs["teardown"]),
            "started_at": "2026-07-17T05:30:00+09:00",
        }

    monkeypatch.setattr(test_run_service.db_test_runs, "insert_run", insert_run)
    monkeypatch.setattr(test_run_service, "_emit_started", lambda *_args, **_kwargs: None)

    result = test_run_service.validate_and_create_run(
        doc_id=doc["doc_id"], runner_id="reviewer", triggered_via="ui"
    )

    assert result["ok"] is True
    assert result["run_id"] == "trun_after_reopen"
    assert result["status"] == "running"
    assert inserted["doc_id"] == doc["doc_id"]
    assert inserted["revision_no"] == 3


def test_pending_review_rerun_still_rejects_concurrent_start(monkeypatch, tmp_path):
    """The reopen fix permits a new attempt, but never a second active attempt."""
    from fastapi import HTTPException
    from modules.flow_gate.services import test_run_service

    src_root = tmp_path / "src"
    src_root.mkdir()
    doc = {
        "doc_id": "flowgate.default.0257.0099-TS",
        "project_id": "flowgate",
        "branch": "main",
        "group_id": "flowgate.default.0257",
        "type_code": "TS",
        "doc_review_status": "pending_review",
        "revision_no": 3,
    }

    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(
        test_run_service.process_service, "is_group_disposed", lambda _group_id: False
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs,
        "list_by_doc",
        lambda _doc_id: [{"run_id": "trun_passed", "status": "passed"}],
    )
    monkeypatch.setattr(
        test_run_service.storage_paths,
        "resolve_project_src_root",
        lambda *_args, **_kwargs: src_root,
    )
    monkeypatch.setattr(
        test_run_service,
        "_read_doc_content",
        lambda _doc: "\n".join(
            [
                "## 테스트 케이스",
                "### TC-1: smoke",
                "- cmd: python --version",
                "- 기대: exits 0",
            ]
        ),
    )
    monkeypatch.setattr(
        test_run_service.db_test_runs,
        "get_running_by_doc",
        lambda _doc_id: {"run_id": "trun_active"},
    )

    try:
        test_run_service.validate_and_create_run(
            doc_id=doc["doc_id"], runner_id="reviewer", triggered_via="ui"
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["error"] == "run_in_progress"
        assert exc.detail["run_id"] == "trun_active"
    else:
        raise AssertionError("expected 409 run_in_progress")
