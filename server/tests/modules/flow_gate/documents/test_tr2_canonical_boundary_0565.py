"""Connected guards around the TR2 writer and legacy document routes."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from modules.flow_gate.documents import tr2_service as tr2


def _body():
    return {
        "tr2_version": 1,
        "source_t2_doc_id": "flowgate.default.0565.0005-T2",
        "edit_spec": {
            "termination": "ready_to_apply",
            "edits": [{"id": "e1", "file": "src/a.txt", "anchor_old": "a",
                       "replacement_new": "b", "rationale": "change",
                       "confidence": "high"}],
            "deferred": [], "gate": {"commands": [], "apply": False},
        },
    }


def test_pair_rejects_result_registered_in_a_non_tr2_slot(monkeypatch):
    result_id = "flowgate.default.0565.0006-TR2"
    source_id = "flowgate.default.0565.0005-T2"
    monkeypatch.setattr(tr2.db_wfseq, "get_item_by_result_doc_id",
                        lambda *_: {"type": "TR", "result_doc_id": result_id,
                                    "sequence_id": 7})
    monkeypatch.setattr(tr2.db_wfseq, "get_paired_instruction_item",
                        lambda *_: {"type": "T2", "result_doc_id": source_id,
                                    "sequence_id": 7})
    monkeypatch.setattr(tr2.db_docs, "get_by_id",
                        lambda doc_id: {"doc_id": doc_id,
                                        "type_code": "TR2" if doc_id == result_id else "T2",
                                        "project_id": "p", "group_id": "g"})
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.verify_pair(result_id, {"source_t2_doc_id": source_id})
    assert error.value.code == "tr2_workflow_conflict"


def test_file_and_revision_rollback_when_writer_fails(tmp_path, monkeypatch):
    doc_id = "flowgate.default.0565.0006-TR2"
    path = tmp_path / "document.json"
    path.write_bytes(b"previous canonical bytes")
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_bytes(b"a")
    row = {"doc_id": doc_id, "type_code": "TR2", "project_id": "flowgate",
           "group_id": "flowgate.default.0565", "module": "default",
           "branch": "main", "revision_no": 0, "updated_at": "old",
           "file_path": "old-path", "filename": "document.json"}

    class Store:
        @contextmanager
        def transaction(self):
            before = row.copy()
            try:
                yield self
            except Exception:
                row.clear()
                row.update(before)
                raise

        def _execute(self, sql, params):
            if row["revision_no"] == params[-1]:
                row["revision_no"] += 1
                row["file_path"] = params[1]
                row["filename"] = params[2]

        def _execute_affected(self, sql, params):
            # The writer's CAS authority is the driver's affected-row count (T0030 §8).
            before = row["revision_no"]
            self._execute(sql, params)
            return int(row["revision_no"] != before)

    monkeypatch.setattr(tr2.db_docs, "get_by_id", lambda _: row.copy())
    monkeypatch.setattr(tr2, "get_store", lambda: Store())
    monkeypatch.setattr(tr2, "verify_pair", lambda *_: None)
    monkeypatch.setattr(tr2, "mutation_block", lambda _doc: None)
    monkeypatch.setattr(tr2, "resolve_source_root", lambda *_: root)
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda _: path)
    monkeypatch.setattr(tr2.storage_paths, "to_storage_relative", lambda *_: "new-path")

    def broken_writer(target, body):
        target.write_bytes(b"interrupted replacement")
        raise OSError("storage failure")

    monkeypatch.setattr(tr2, "write_body_atomically", broken_writer)
    with pytest.raises(OSError, match="storage failure"):
        tr2.save(doc_id, _body(), actor="tester", expected_revision=0)
    assert row["revision_no"] == 0
    assert row["file_path"] == "old-path"
    assert path.read_bytes() == b"previous canonical bytes"


def test_generic_markdown_content_route_cannot_overwrite_tr2(monkeypatch):
    from modules.flow_gate.documents.routers import documents as routes
    monkeypatch.setattr(routes.document_service, "get_document",
                        lambda *_: {"doc_id": "d", "type_code": "TR2"})
    monkeypatch.setattr(routes, "_reject_if_group_disposed", lambda *_: None)
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", lambda *_: None)
    with pytest.raises(HTTPException) as error:
        routes.update_document_content(
            "d", routes.DocumentContentUpdate(content="unvalidated"),
            current_user={"user_id": "human"})
    assert error.value.status_code == 409


def test_human_direct_route_passes_expected_revision_to_single_writer(monkeypatch):
    from modules.flow_gate.documents.routers import documents as generic_routes
    from modules.flow_gate.documents.routers import tr2 as routes
    monkeypatch.setattr(routes, "_doc", lambda *_: {"doc_id": "d", "type_code": "TR2"})
    monkeypatch.setattr(generic_routes, "_reject_if_group_disposed", lambda *_: None)
    monkeypatch.setattr(generic_routes, "_reject_if_group_ai_running", lambda *_: None)
    monkeypatch.setattr(routes.document_service, "is_final_approved", lambda *_: False)
    monkeypatch.setattr(routes.document_service, "is_document_editable",
                        lambda *_a, **_k: True)
    seen = []
    monkeypatch.setattr(routes.tr2, "save",
                        lambda *args, **kwargs: seen.append((args, kwargs)) or {"ok": True})
    result = routes.put_tr2(None, "d", routes.Tr2Save(expected_revision=2, body=_body()),
                            current_user={"user_id": "human"})
    assert result == {"ok": True}
    assert seen == [(("d", _body()), {"actor": "human", "expected_revision": 2})]


def test_inbox_pair_preflight_rejects_mismatch_before_registration(monkeypatch):
    source_id = "flowgate.default.0565.0005-T2"
    monkeypatch.setattr(tr2.db_wfseq, "get_pending_head_by_group",
                        lambda *_: {"type": "TR2", "sequence_id": 7,
                                    "sort_order": 2})
    monkeypatch.setattr(tr2.db_wfseq, "get_sequence_items",
                        lambda *_: [{"type": "T2", "result_doc_id": "other",
                                     "sort_order": 1}])
    monkeypatch.setattr(tr2.db_docs, "get_by_id",
                        lambda *_: {"type_code": "T2", "project_id": "p",
                                    "group_id": "g"})
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.verify_pending_pair("p", "g", source_id)
    assert error.value.code == "tr2_workflow_conflict"
