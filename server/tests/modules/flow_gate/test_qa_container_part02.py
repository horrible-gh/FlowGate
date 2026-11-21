"""Independently selectable scenarios; shared fixtures remain in test_qa_container.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_qa_container import (
    DOC,
    Path,
    _MockDB,
    _MockTxn,
    _QUERIES,
    _QUERIES_JSON,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _json,
    contextmanager,
    os,
    pytest,
    raw,
    sqlite3,
    store,
    sys,
    tempfile,
)

def test_open_q_doc_ids_batch_query_count_is_constant_as_documents_grow(store):
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import ai_invoke_service, q_service

    original_fetch_all = conn_mod.STORE._fetch_all
    pending_reads = []

    def counted_fetch_all(sql, params=None):
        if "JOIN question_items qi" in sql and "d.group_id IN" in sql:
            pending_reads.append(1)
        return original_fetch_all(sql, params)

    conn_mod.STORE._fetch_all = counted_fetch_all
    try:
        assert ai_invoke_service._open_q_doc_ids("p.none.0001") == []
        assert len(pending_reads) == 1
        for seq in range(2, 52):
            doc_id = f"p.none.0001.{seq:04d}-D"
            db_docs.create({
                "doc_id": doc_id, "project_id": "p", "type_code": "D", "seq": seq,
                "title": doc_id, "group_id": "p.none.0001", "module": "none",
                "owner_id": "u1", "file_path": f"{doc_id}.md", "status": "open",
            })
        q_service.add_questions(
            "p.none.0001.0051-D", [{"body": "open"}],
            asker_kind="human", created_by="u1",
        )
        before = len(pending_reads)
        assert ai_invoke_service._open_q_doc_ids("p.none.0001") == ["p.none.0001.0051-D"]
        assert len(pending_reads) - before == 1
    finally:
        conn_mod.STORE._fetch_all = original_fetch_all
def test_requestion_reopens_done_container(store):
    from modules.flow_gate.services import q_service
    r1 = q_service.add_questions(DOC, [{"body": "q1"}], asker_kind="human", created_by="u1")
    q_service.register_answer(DOC, r1["added_item_ids"][0], "a1", author_kind="human", author_id="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "done"
    # re-question = new item → done → pending
    q_service.add_questions(DOC, [{"body": "q2"}], asker_kind="human", created_by="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "pending"
def test_list_open_items(store):
    from modules.flow_gate.services import q_service
    q_service.add_questions(DOC, [{"title": "열린질의", "body": "open?"}],
                            asker_kind="human", created_by="u1")
    items = q_service.list_open_items(project_id="p")
    assert any(it["doc_id"] == DOC and it["title"] == "열린질의" for it in items)
    # TR0005 rework: each row carries the host document's type_code so the dashboard
    # opens the real document (not a Q-tree viewer). DOC is a 'D' document.
    row = next(it for it in items if it["doc_id"] == DOC)
    assert row["type_code"] == "D"
    # once answered, it drops off the list
    bundle = q_service.get_qa_detail(DOC)
    item_id = bundle["items"][0]["id"]
    q_service.register_answer(DOC, item_id, "answered", author_kind="human", author_id="u1")
    items2 = q_service.list_open_items(project_id="p")
    assert not any(it["doc_id"] == DOC for it in items2)
def test_list_open_items_excludes_completed_or_inactive_groups_but_keeps_history(store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import q_service

    def create_doc(doc_id, group_id, title, type_code="D", seq=1) -> None:
        db_docs.create({
            "doc_id": doc_id, "project_id": "p", "type_code": type_code, "seq": seq,
            "title": title, "group_id": group_id, "module": "none",
            "owner_id": "u1", "status": "open",
        })

    # (A) Question on the wf_done document itself → excluded (per-document guard).
    wf_done_doc = "p.none.0001.0002-D"
    create_doc(wf_done_doc, "p.none.0001", "Workflow Done", type_code="D", seq=2)
    q_service.add_questions(wf_done_doc, [{"title": "완료문서질의", "body": "done?"}],
                            asker_kind="human", created_by="u1")
    store.execute("UPDATE documents SET doc_review_status='wf_done' WHERE doc_id=?", [wf_done_doc])

    # (B) Manually closed group → excluded.
    db_groups.create({
        "group_id": "p.none.0002", "project_id": "p", "module": "none",
        "title": "Closed", "status": "CLOSED", "closed_at": "2026-06-13T00:00:00Z",
    })
    closed_doc = "p.none.0002.0001-D"
    create_doc(closed_doc, "p.none.0002", "Closed Group")
    q_service.add_questions(closed_doc, [{"title": "종료그룹질의", "body": "closed?"}],
                            asker_kind="human", created_by="u1")

    # (C) Soft-deleted group → excluded.
    db_groups.create({
        "group_id": "p.none.0003", "project_id": "p", "module": "none",
        "title": "Deleted",
    })
    deleted_doc = "p.none.0003.0001-D"
    create_doc(deleted_doc, "p.none.0003", "Deleted Group")
    q_service.add_questions(deleted_doc, [{"title": "삭제그룹질의", "body": "deleted?"}],
                            asker_kind="human", created_by="u1")
    store.execute("UPDATE groups SET deleted_at='2026-06-13T00:00:00Z' WHERE group_id='p.none.0003'")

    # (D) B0001 core case: a COMPLETED workflow group whose root R is wf_done, but
    #     whose still-open question lives on a NON-root child doc (T, merely
    #     'approved'). The group was never manually closed (status stays 'draft').
    #     Keying the exclusion off the group root — not the child doc — is what
    #     drops this. A per-document wf_done check (rev1) let it leak through.
    db_groups.create({
        "group_id": "p.none.0004", "project_id": "p", "module": "none",
        "title": "Completed Workflow",
    })
    create_doc("p.none.0004.0001-R", "p.none.0004", "Root", type_code="R", seq=1)
    store.execute("UPDATE documents SET doc_review_status='wf_done' WHERE doc_id=?",
                  ["p.none.0004.0001-R"])
    completed_child_doc = "p.none.0004.0002-T"
    create_doc(completed_child_doc, "p.none.0004", "Child Task", type_code="T", seq=2)
    store.execute("UPDATE documents SET doc_review_status='approved' WHERE doc_id=?",
                  [completed_child_doc])
    q_service.add_questions(completed_child_doc, [{"title": "완료그룹자식질의", "body": "child?"}],
                            asker_kind="human", created_by="u1")

    # (E) Active-group guard: identical shape to (D) but the root R is still
    #     wf_in_progress, so the group is NOT complete and its child question must
    #     REMAIN visible. Prevents the group-level filter from over-suppressing.
    db_groups.create({
        "group_id": "p.none.0005", "project_id": "p", "module": "none",
        "title": "Active Workflow",
    })
    create_doc("p.none.0005.0001-R", "p.none.0005", "Root", type_code="R", seq=1)
    store.execute("UPDATE documents SET doc_review_status='wf_in_progress' WHERE doc_id=?",
                  ["p.none.0005.0001-R"])
    active_child_doc = "p.none.0005.0002-T"
    create_doc(active_child_doc, "p.none.0005", "Child Task", type_code="T", seq=2)
    store.execute("UPDATE documents SET doc_review_status='approved' WHERE doc_id=?",
                  [active_child_doc])
    q_service.add_questions(active_child_doc, [{"title": "활성그룹자식질의", "body": "child?"}],
                            asker_kind="human", created_by="u1")

    # (F) Plain active document/group → kept.
    active_doc = "p.none.0001.0003-D"
    create_doc(active_doc, "p.none.0001", "Active", type_code="D", seq=3)
    q_service.add_questions(active_doc, [{"title": "활성질의", "body": "active?"}],
                            asker_kind="human", created_by="u1")

    items = q_service.list_open_items(project_id="p")
    returned_doc_ids = {it["doc_id"] for it in items}
    # kept: genuinely active work
    assert active_doc in returned_doc_ids
    assert active_child_doc in returned_doc_ids
    # excluded: completed / inactive work (incl. the B0001 child-doc leak)
    assert wf_done_doc not in returned_doc_ids
    assert closed_doc not in returned_doc_ids
    assert deleted_doc not in returned_doc_ids
    assert completed_child_doc not in returned_doc_ids

    # History is preserved: the document-scoped Q&A detail still returns the past
    # questions for both the wf_done doc and the completed group's child doc.
    assert q_service.get_qa_detail(wf_done_doc)["items"][0]["title"] == "완료문서질의"
    assert q_service.get_qa_detail(completed_child_doc)["items"][0]["title"] == "완료그룹자식질의"
def test_migration_040_retires_qav(store):
    rows = store.fetch_all(
        "SELECT type_code, is_active FROM document_types WHERE type_code IN ('Q','A','V')")
    assert rows  # rows preserved
    assert all(r["is_active"] == 0 for r in rows)
