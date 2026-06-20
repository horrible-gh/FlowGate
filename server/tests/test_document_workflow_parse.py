"""Tests for _parse_doc_workflow enrichment logic (T818).

T818: Head-doc resolution now uses direct group SQL lookup (list_documents),
NOT workflow_sequence_items.result_doc_id. Sequence is kept only for
workflow_steps recovery and next_step_exists ordering.
"""
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_workflow_sequences
from modules.flow_gate.documents.routers import documents as document_routes

# ── helpers ───────────────────────────────────────────────────────────────────

# R doc fixture WITHOUT project_id/group_id (tests that don't need group lookup)
_R_DOC = {
    "doc_id": "R001",
    "type_code": "R",
    "workflow_steps": None,
    "rejection_history": None,
}


def _seq_patch(monkeypatch, items, seq_id=7, r_doc_id="R001"):
    monkeypatch.setattr(
        db_workflow_sequences,
        "get_sequence_by_doc_id",
        lambda doc_id: {"id": seq_id} if doc_id == r_doc_id else None,
    )
    monkeypatch.setattr(
        db_workflow_sequences,
        "get_sequence_items",
        lambda sid: items if sid == seq_id else [],
    )


def _list_docs_patch(monkeypatch, docs_by_group: dict):
    """Patch db_docs.list_documents. docs_by_group: {group_id: [doc, ...]}"""
    def _fake_list(project_id, group_id=None, type_code=None, limit=100, **kw):
        result = docs_by_group.get(group_id, []) if group_id else []
        if type_code is not None:
            result = [d for d in result if d.get("type_code") == type_code]
        return result
    monkeypatch.setattr(db_docs, "list_documents", _fake_list)


# ── T818 #1: PM scenario ──────────────────────────────────────────────────────

def test_t818_pm_scenario_group_head_is_d_doc(monkeypatch):
    """PM scenario: group test.test2.0001 — D doc with review_status=NULL is the head.
    Viewing any sibling (DS, M, R, Q) must surface D as workflow_head_doc_id.
    """
    GROUP_ID = "test.test2.0001"
    GROUP_DOCS = [
        {"doc_id": "test.test2.0001.0001-R",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 1},
        {"doc_id": "test.test2.0001.0002-M",  "type_code": "M",  "doc_review_status": "approved",       "seq": 2},
        {"doc_id": "test.test2.0001.0003-Q",  "type_code": "Q",  "doc_review_status": None,             "seq": 3},
        {"doc_id": "test.test2.0001.0004-DS", "type_code": "DS", "doc_review_status": "approved",       "seq": 4},
        {"doc_id": "test.test2.0001.0005-D",  "type_code": "D",  "doc_review_status": None,             "seq": 5,
         "title": "R0001 테스트용 기본 설계서"},
    ]
    _list_docs_patch(monkeypatch, {GROUP_ID: GROUP_DOCS})

    # Viewing the DS sibling
    parsed = document_routes._parse_doc_workflow({
        "doc_id": "test.test2.0001.0004-DS",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_doc_id"] == "test.test2.0001.0005-D"
    assert parsed["workflow_head_doc_number"] == "test.test2.0001.0005-D"
    assert parsed["workflow_head_doc_title"] == "R0001 테스트용 기본 설계서"
    assert parsed["workflow_head_status"] == "in_progress"
    assert parsed["workflow_head_type"] == "D"
    assert parsed.get("workflow_head_doc_review_status") is None


# ── T818 #2: all approved in group → done ────────────────────────────────────

def test_all_workflow_step_docs_approved_emits_done(monkeypatch):
    """All DS+D docs approved → workflow_head_status='done', no head doc fields."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": "approved", "seq": 2},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_doc_id") is None


# ── T818 #3: no in-progress doc exists (no D created yet) → done ─────────────

def test_no_existing_inprogress_doc_emits_done(monkeypatch):
    """Only DS(approved) in group, D not yet created → head_status='done'.
    PM: group head = a doc that exists, not the next pending slot.
    """
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_doc_id") is None


# ── T818 #4: viewing the head doc itself ──────────────────────────────────────

def test_viewing_head_doc_resolves_itself_as_head(monkeypatch):
    """When viewing the D doc (the head), group_head still resolves to D itself."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": None,       "seq": 2,
         "title": "Design v1"},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "D001",
        "type_code": "D",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_doc_id"] == "D001"
    assert parsed["workflow_head_status"] == "in_progress"
    assert parsed["workflow_head_type"] == "D"


# ── T818 #5: no group info → workflow_head_* absent ──────────────────────────

def test_r_no_sequence_workflow_head_status_absent(monkeypatch):
    """R doc with no project_id/group_id and no sequence → workflow_head_status not set."""
    monkeypatch.setattr(
        db_workflow_sequences,
        "get_sequence_by_doc_id",
        lambda doc_id: None,
    )

    parsed = document_routes._parse_doc_workflow({**_R_DOC})

    assert parsed.get("workflow_head_status") is None
    assert parsed.get("workflow_steps") is None


def test_no_group_info_head_fields_absent(monkeypatch):
    """Doc with no project_id/group_id → workflow_head_* not set."""
    monkeypatch.setattr(db_workflow_sequences, "get_sequence_by_doc_id", lambda doc_id: None)

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed.get("workflow_head_status") is None
    assert parsed.get("workflow_head_doc_id") is None


# ── T818 #6: M doc excluded from head candidates (NON_HEAD_TYPES) ─────────────

def test_m_doc_never_selected_as_head_d_is_head(monkeypatch):
    """M doc is in NON_HEAD_TYPES → filtered out; D with NULL review is the head."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "M001", "type_code": "M", "doc_review_status": None, "seq": 1},
        {"doc_id": "D001", "type_code": "D", "doc_review_status": None, "seq": 2,
         "title": "Design"},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "M001",
        "type_code": "M",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_doc_id"] == "D001"
    assert parsed["workflow_head_type"] == "D"
    assert parsed["workflow_head_status"] == "in_progress"


# ── T818 #7: workflow_head_status always set when group present ───────────────

def test_workflow_head_status_always_set_for_group_doc(monkeypatch):
    """D030 §2: workflow_head_status always set (in_progress or done) when project+group."""
    GROUP_ID = "G001"

    # Case 1: in_progress (D doc with null review_status)
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "D001", "type_code": "D", "doc_review_status": None, "seq": 1},
    ]})
    parsed = document_routes._parse_doc_workflow({
        "doc_id": "D001", "type_code": "D",
        "project_id": "P001", "group_id": GROUP_ID,
        "workflow_steps": None, "rejection_history": None,
    })
    assert parsed["workflow_head_status"] == "in_progress"

    # Case 2: done (D doc approved)
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "D001", "type_code": "D", "doc_review_status": "approved", "seq": 1},
    ]})
    parsed2 = document_routes._parse_doc_workflow({
        "doc_id": "D001", "type_code": "D",
        "project_id": "P001", "group_id": GROUP_ID,
        "workflow_steps": None, "rejection_history": None,
    })
    assert parsed2["workflow_head_status"] == "done"


# ── T818 #8: wf_done treated as approved ─────────────────────────────────────

def test_wf_done_review_status_treated_as_approved(monkeypatch):
    """doc_review_status='wf_done' is in APPROVED_STATUSES → not selected as head."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "D001", "type_code": "D", "doc_review_status": "wf_done", "seq": 1},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "D001", "type_code": "D",
        "project_id": "P001", "group_id": GROUP_ID,
        "workflow_steps": None, "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_doc_id") is None


# ── workflow_steps recovery from sequence items ───────────────────────────────

def test_recovers_workflow_steps_from_sequence_items(monkeypatch):
    """workflow_steps is populated from sequence items when the R doc's field is null.
    (Sequence still used for workflow_steps recovery; no project_id/group_id needed.)
    """
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": None},
        {"type": "T", "result_doc_id": None},
    ])

    parsed = document_routes._parse_doc_workflow({**_R_DOC})

    assert parsed["workflow_steps"] == ["D", "T"]
    # No group info → head fields absent
    assert parsed.get("workflow_head_status") is None


# ── next_step_exists from sequence + group head ───────────────────────────────

def test_next_step_exists_true_when_head_not_last(monkeypatch):
    """next_step_exists=True when group_head's type_code is not the last sequence item."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": None, "seq": 1},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": None},
        {"type": "T",  "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_type"] == "DS"
    assert parsed["next_step_exists"] is True


def test_next_step_exists_true_when_head_is_last_slot(monkeypatch):
    """next_step_exists reflects "a head step exists to advance to", NOT "a step exists
    after the head". When the head is the LAST sequence slot (here T, with the AC final
    approval synthesized off-sequence), it is still advanceable — the action bar must
    surface the next step. b39f6b8 wrongly reported False here (head_idx < step_count-1),
    blanking the action bar on the doc whose next step is the last real slot before AC
    (group test.test.0007 / 0104 regression). Restored to True."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "T001",  "type_code": "T",  "doc_review_status": None,             "seq": 2},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": "DS001"},
        {"type": "T",  "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_type"] == "T"
    assert parsed["next_step_exists"] is True


# ── non-R doc: T doc in-progress with group lookup ───────────────────────────

def test_non_r_viewing_sibling_shows_t_doc_as_head(monkeypatch):
    """Viewing DS (approved) while T is in-progress → T is the group head."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress",  "seq": 0,
         "workflow_steps": '["DS","T"]'},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",         "seq": 1},
        {"doc_id": "T001",  "type_code": "T",  "doc_review_status": "pending_review",   "seq": 2,
         "title": "Test Report v1"},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": "DS001"},
        {"type": "T",  "result_doc_id": "T001"},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "in_progress"
    assert parsed["workflow_head_type"] == "T"
    assert parsed["workflow_head_doc_id"] == "T001"
    assert parsed["workflow_head_doc_title"] == "Test Report v1"
    assert parsed["workflow_head_doc_review_status"] == "pending_review"
    # The in-progress T head is the last slot but still an advanceable step (the action
    # bar points the viewer to it); next_step_exists=True. (Was False under b39f6b8's
    # `head_idx < step_count-1`; group 0104 restore.)
    assert parsed["next_step_exists"] is True


# ── T829: 'pending' derivation ───────────────────────────────────────────────

def test_head_status_pending_when_next_step_not_realized(monkeypatch):
    """T829 primary fix: R decided, sequence has D as next step, D not yet created
    → workflow_head_status='pending' → FE can show next-stage button."""
    GROUP_ID = "G001"
    # Only the R itself exists; D not yet created.
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": None},
        {"type": "T", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed.get("workflow_head_doc_id") is None


def test_head_status_in_progress_when_group_head_exists(monkeypatch):
    """T829 regression #3: R decided, D001 exists with pending_review → 'in_progress'."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "D001", "type_code": "D",  "doc_review_status": "pending_review", "seq": 1,
         "title": "Design v1"},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": "D001"},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "in_progress"
    assert parsed["workflow_head_doc_id"] == "D001"
    assert parsed["workflow_head_type"] == "D"


def test_head_status_done_when_sequence_complete(monkeypatch):
    """T829 regression #2: R wf_done, all sequence items realized and approved → 'done'.
    Must NOT regress to 'pending'."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R",  "doc_review_status": "wf_done",  "seq": 0},
        {"doc_id": "D001", "type_code": "D",  "doc_review_status": "approved", "seq": 1},
        {"doc_id": "T001", "type_code": "T",  "doc_review_status": "approved", "seq": 2},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": "D001"},
        {"type": "T", "result_doc_id": "T001"},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_done",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_doc_id") is None


def test_head_status_unaffected_for_non_r_group_doc(monkeypatch):
    """T829 regression #5 (revised NR158): D-type doc in a group where group_head=None
    with unrealized posterior steps must emit 'pending', not 'done'.
    NR158 revision: T829's 'non-R never pending' rule is replaced — sequence progress
    is now computed uniformly for all doc types.
    """
    GROUP_ID = "G001"
    # All non-R/M/Q docs are approved → group_head=None for the D doc viewer.
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "D001", "type_code": "D",  "doc_review_status": "approved", "seq": 1},
        {"doc_id": "T001", "type_code": "T",  "doc_review_status": "approved", "seq": 2},
    ]})
    # Sequence has further types beyond what exists — must cause 'pending' for non-R too.
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": "D001"},
        {"type": "T", "result_doc_id": "T001"},
        {"type": "P", "result_doc_id": None},
    ], r_doc_id="R001")
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "D001", "type_code": "D",  "doc_review_status": "approved",       "seq": 1},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "D001",
        "type_code": "D",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # NR158 revision: D approved with unrealized T/P posterior steps → pending, not done.
    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "T"


# -- T831: workflow_head_type in pending branch -----------------------------------------------

def test_head_type_filled_for_pending_with_unrealized_step(monkeypatch):
    """T831 primary fix: R decided, seq=["M","DS","D","T","TR"], M approved but DS not yet
    created -> workflow_head_status='pending', workflow_head_type='DS'."""
    GROUP_ID = "G001"
    # M is auto-approved; no DS/D/T/TR docs exist yet.
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "M001", "type_code": "M", "doc_review_status": "approved",       "seq": 1},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "M",  "result_doc_id": "M001"},
        {"type": "DS", "result_doc_id": None},
        {"type": "D",  "result_doc_id": None},
        {"type": "T",  "result_doc_id": None},
        {"type": "TR", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "DS"
    assert parsed.get("workflow_head_doc_id") is None


def test_head_type_pending_m_is_head_not_skipped(monkeypatch):
    """B0001 (group 0105): a PENDING (not-yet-created) auto-approve slot (M) is an
    actionable 'create next document' step and must surface as the head, matching the
    SSOT workflow_sequences.get_effective_head (which does not type-filter unrealized
    slots). Previously (T831) the pending M was skipped to DS, diverging the action-bar
    head from the slot create_next_empty / worker inbox actually advance to."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "M",  "result_doc_id": None},
        {"type": "DS", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "M"


def test_head_type_pending_memo_tail_offers_create_not_final_approval(monkeypatch):
    """B0001 (group 0105) core repro: seq=[R(realized), M(pending)]. The only remaining
    slot is a memo that has not been created yet -> head must be M ([create document]),
    NOT the synthetic AC final-approval gate. Was head_type='AC' before the fix."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "R", "result_doc_id": "R001",
         "result_doc_review_status": "wf_in_progress", "sort_order": 0},
        {"type": "M", "result_doc_id": None, "sort_order": 1},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "M"
    assert parsed["next_step_exists"] is True


def test_head_final_approval_gate_when_only_m_approved(monkeypatch):
    """T831 / M042: seq=["M"], M approved -> no actionable document step remains, but final
    approval (AC) is not yet done -> head = AC/pending so the action bar shows [final approval]
    (group 0104 restore; was status='done' / head_type unset under b39f6b8's auto-finalize)."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "M001", "type_code": "M", "doc_review_status": "approved",       "seq": 1},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "M", "result_doc_id": "M001"},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed.get("workflow_head_type") == "AC"
    # Synthetic AC (no AC slot in seq_items, head_idx == len(steps)) must still report
    # an advanceable next step, so the action bar surfaces [final approval] rather than
    # blanking to 'info' (group 0104 regression part 2).
    assert parsed["next_step_exists"] is True


def test_head_type_first_design_step_when_no_m_in_sequence(monkeypatch):
    """T831: seq=["D","T"], neither created -> head_type='D', status='pending'."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R", "doc_review_status": "wf_in_progress", "seq": 0},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "D", "result_doc_id": None},
        {"type": "T", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "D"
    assert parsed.get("workflow_head_doc_id") is None


# ── NR158: non-R partial-progress cases ───────────────────────────────────────

def test_n158_non_r_partial_progress_emits_pending(monkeypatch):
    """NR158 primary: DS approved, D not created, seq=[DS,D,AC]
    → head_status='pending', head_type='D', next_step_exists=True."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",        "seq": 1},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": "DS001"},
        {"type": "D",  "result_doc_id": None},
        {"type": "AC", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "D"
    assert parsed.get("workflow_head_doc_id") is None
    assert parsed["next_step_exists"] is True


def test_n158_non_r_two_unrealized_steps(monkeypatch):
    """NR158: DS+D approved, AC not created, seq=[DS,D,AC]
    → head_status='pending', head_type='AC', next_step_exists=True.

    The final-approval gate (AC) is itself the advanceable action — viewing the last
    approved doc must surface [final approval]. b39f6b8 had reported False here (AC is
    the last slot), blanking that action bar; restored to True (group 0104)."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",        "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": "approved",        "seq": 2},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": "DS001"},
        {"type": "D",  "result_doc_id": "D001"},
        {"type": "AC", "result_doc_id": None},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "AC"
    assert parsed["next_step_exists"] is True


def test_group_0104_last_real_step_before_synthetic_ac_advanceable(monkeypatch):
    """Group 0104 regression (real data: group test.test.0007). steps=[N,NR,T,TR] with
    N/NR/T approved and TR not yet created; AC is synthesized off-sequence. Viewing the
    approved T doc, the head is TR (the LAST sequence slot, head_idx=3, step_count=4).
    next_step_exists must be True so the action bar shows [next step: TR] — b39f6b8's
    `head_idx < step_count-1` (3 < 3) blanked it."""
    GROUP_ID = "test.test.0007"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "test.test.0007.0001-R",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 1},
        {"doc_id": "test.test.0007.0002-N",  "type_code": "N",  "doc_review_status": "approved",        "seq": 2},
        {"doc_id": "test.test.0007.0003-NR", "type_code": "NR", "doc_review_status": "approved",        "seq": 3},
        {"doc_id": "test.test.0007.0004-T",  "type_code": "T",  "doc_review_status": "approved",        "seq": 4},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "N",  "result_doc_id": "test.test.0007.0002-N"},
        {"type": "NR", "result_doc_id": "test.test.0007.0003-NR"},
        {"type": "T",  "result_doc_id": "test.test.0007.0004-T"},
        {"type": "TR", "result_doc_id": None},
    ], r_doc_id="test.test.0007.0001-R")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "test.test.0007.0004-T",
        "type_code": "T",
        "project_id": "test",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_type"] == "TR"
    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_index"] == 3
    assert parsed["next_step_exists"] is True


def test_n158_non_r_truly_complete_still_done(monkeypatch):
    """NR158: DS+D+AC all approved → head_status='done', next_step_exists=False."""
    GROUP_ID = "G001"
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 0},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",        "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": "approved",        "seq": 2},
        {"doc_id": "AC001", "type_code": "AC", "doc_review_status": "approved",        "seq": 3},
    ]})
    _seq_patch(monkeypatch, [
        {"type": "DS", "result_doc_id": "DS001"},
        {"type": "D",  "result_doc_id": "D001"},
        {"type": "AC", "result_doc_id": "AC001"},
    ], r_doc_id="R001")

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_type") is None
    assert parsed["next_step_exists"] is False


def test_n158_non_r_no_seq_items_fallback_done(monkeypatch):
    """NR158: seq_items missing (no R parent in group) → head_status='done' (safe fallback)."""
    GROUP_ID = "G001"
    # No R doc in group → no parent_r_doc_id → no seq lookup → seq_items=[]
    _list_docs_patch(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
    ]})

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "P001",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "done"
    assert parsed.get("workflow_head_doc_id") is None
