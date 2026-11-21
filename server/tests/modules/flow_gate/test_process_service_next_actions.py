from __future__ import annotations



import os

import sys

from pathlib import Path



import pytest

from jinja2 import Environment, FileSystemLoader



os.environ.setdefault("TESTING", "1")

sys.path.insert(0, ".")



from modules.flow_gate import db, process_service  # noqa: E402





@pytest.fixture()

def flowgate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:

    storage_dir = tmp_path / "storage"

    path_map = {

        "STORAGE_DIR": storage_dir,

        "DB_PATH": storage_dir / "flow_gate.db",

        "INBOX_DIR": storage_dir / "inbox",

        "PROCESSED_DIR": storage_dir / "processed",

        "ERROR_DIR": storage_dir / "error",

        "CONFLICT_DIR": storage_dir / "conflict",

        "OUTBOX_DIR": storage_dir / "outbox",

        "ACCEPT_DIR": storage_dir / "accept",

        "REJECT_DIR": storage_dir / "reject",

        "CANCELLED_DIR": storage_dir / "cancelled",

        "TEST_REPORTS_DIR": tmp_path / "test_reports",

        "TEST_REPORTS_ARCHIVE_DIR": tmp_path / "test_reports_archive",

        "DESIGN_REOPEN_DIR": tmp_path / "design_reopen",

    }



    for attr, path_value in path_map.items():

        monkeypatch.setattr(db, attr, str(path_value))



    db.init_db()

    # init_db() only lays down the base schema (001). The product's
    # insert_document and related queries now rely on columns added by later
    # migrations (e.g. documents.filename in 021). Apply the full migration
    # chain so the test DB matches the current schema contract.
    import sqlite3 as _sqlite3

    _schema_dir = Path(__file__).resolve().parents[3] / "sql" / "migrations" / "sqlite"

    _conn = _sqlite3.connect(str(storage_dir / "flow_gate.db"))

    try:

        _conn.execute("PRAGMA foreign_keys = OFF")

        # Mirror db._legacy_schema_sql(): the documents.status CHECK in the
        # migration files (001 / 027) lists only the base statuses, but the
        # runtime widens it to include 'accepted'/'monitoring'/'done', which the
        # product actually writes (process_service accept/monitor flows). Apply
        # the same widening so the migrated test schema matches runtime behavior.
        _status_narrow = (
            "'draft','open','in_review','approved','rejected',\n"
            "                         'cancelled','closed','archived','answered'"
        )
        _status_wide = (
            "'draft','open','in_review','approved','accepted','rejected',\n"
            "                         'cancelled','closed','archived','answered','monitoring','done'"
        )
        for _mig in sorted(_schema_dir.glob("*.sql")):

            try:

                _conn.executescript(_mig.read_text(encoding="utf-8").replace(_status_narrow, _status_wide))

            except Exception:

                pass

        _conn.commit()

    finally:

        _conn.close()

    db.add_allowed_project("FG", "core")

    db.add_allowed_project("FG", "")

    return {key: str(value) for key, value in path_map.items()}





def _write_text(path: str, content: str) -> None:

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:

        f.write(content)





def _register_doc(

    *,

    doc_id: str,

    doc_type: str,

    title: str,

    group_id: str,

    target_id: str | None = None,

    status: str = "open",

    next_action: str | None = None,

    direction: str | None = None,

    memo_file: str | None = None,

) -> int:

    pk = db.insert_document(

        doc_id=doc_id,

        doc_type=doc_type,

        project="FG",

        module="core",

        title=title,

        target_id=target_id,

        group_id=group_id,

        priority="medium",

        next_action=next_action,

        direction=direction,

        status=status,

    )

    if memo_file:

        db.insert_event(doc_id, "created", memo_file=memo_file)

    return pk





def _seed_requirement_chain(

    *,

    group_id: str,

    ds_status: str = "accepted",

    ds_next: str = "D",

    create_ds_file: bool = False,

) -> dict[str, str | int]:

    db.insert_group(group_id, "FG", "core", "Next Actions Test Group", "medium")



    r_doc_id = "FG-core-R001"

    ar_doc_id = "FG-core-AR001"

    q_doc_id = "FG-core-Q001"

    a_doc_id = "FG-core-A001"

    ds_doc_id = "FG-core-DS001"



    r_filename = "R001_requirement.md"

    q_filename = "Q001_question.md"

    a_filename = "A001_answer.md"

    ar_filename = "AR001_request.md"

    ds_filename = "DS001_design_instruction.md"



    _register_doc(

        doc_id=r_doc_id,

        doc_type="R",

        title="Next Step Guidance Requirements",

        group_id=group_id,

        status="accepted",

        memo_file=r_filename,

    )

    _register_doc(

        doc_id=ar_doc_id,

        doc_type="AR",

        title="Requirement Approval Request",

        group_id=group_id,

        target_id=r_doc_id,

        status="accepted",

        memo_file=ar_filename,

    )

    _register_doc(

        doc_id=q_doc_id,

        doc_type="Q",

        title="Requirement Confirmation Question",

        group_id=group_id,

        target_id=r_doc_id,

        status="accepted",

        memo_file=q_filename,

    )

    _register_doc(

        doc_id=a_doc_id,

        doc_type="A",

        title="Requirement Confirmation Response",

        group_id=group_id,

        target_id=q_doc_id,

        status="accepted",

        memo_file=a_filename,

    )

    ds_pk = _register_doc(

        doc_id=ds_doc_id,

        doc_type="DS",

    title="D007 Next Step Guidance Design",

        group_id=group_id,

        target_id=ar_doc_id,

        status=ds_status,

        next_action=ds_next,

        direction="inbox",

        memo_file=ds_filename,

    )



    if create_ds_file:

        _write_text(

            os.path.join(db.INBOX_DIR, ds_filename),

            "---\n"

            f"group_id: {group_id}\n"

            "type: DS\n"

            "project: FG\n"

            "module: core\n"

            "title: D007 Next Step Guidance Design\n"

            "priority: medium\n"

            f"target_id: {ar_doc_id}\n"

            f"next: {ds_next}\n"

            "---\n"

            "\n"

            "Body\n",

        )



    return {

        "group_id": group_id,

        "r_doc_id": r_doc_id,

        "r_filename": r_filename,

        "q_doc_id": q_doc_id,

        "q_filename": q_filename,

        "a_doc_id": a_doc_id,

        "a_filename": a_filename,

        "ar_doc_id": ar_doc_id,

        "ds_doc_id": ds_doc_id,

        "ds_filename": ds_filename,

        "ds_pk": ds_pk,

    }





def test_approve_document_ds_includes_contextual_next_actions(flowgate_env: dict[str, str]) -> None:

    seeded = _seed_requirement_chain(

        group_id="FG-core-9001",

        ds_status="open",

        ds_next="D",

        create_ds_file=True,

    )



    result = process_service.approve_document(int(seeded["ds_pk"]))



    assert result["status"] == "success"

    assert result["source_type"] == "DS"

    assert "next_actions" in result

    assert [item["type"] for item in result["next_actions"]] == ["D"]



    next_action = result["next_actions"][0]

    assert next_action["label"] == "D Basic Design document is required"

    assert next_action["source_doc_id"] == seeded["ds_doc_id"]

    assert next_action["source_title"] == "D007 Next Step Guidance Design"

    # T051: docs_root unset -> use the absolute path in an allowed storage bucket (located in accept/ after approval)

    source_file = next_action["source_file"]

    assert source_file  # Must not be empty

    assert os.path.isabs(source_file)  # Must be an absolute path

    assert seeded["ds_filename"] in source_file  # Original filename must be included in the path

    assert source_file != seeded["ds_filename"]  # Bare filename is not allowed

    assert next_action["requirement_doc_id"] == seeded["r_doc_id"]

    assert next_action["requirement_title"] == "Next Step Guidance Requirements"

    # R/Q/A files are not on disk (no migration) -> empty string

    assert next_action["requirement_file"] == ""

    assert next_action["reference_qa"] == [

        {

            "type": "Q",

            "doc_id": seeded["q_doc_id"],

            "title": "Requirement Confirmation Question",

            "file": "",

        },

        {

            "type": "A",

            "doc_id": seeded["a_doc_id"],

            "title": "Requirement Confirmation Response",

            "file": "",

        },

    ]

    # T051: verify the docs_root / project_root keys exist (empty strings when unset)

    assert "docs_root" in next_action

    assert "project_root" in next_action

    assert next_action["docs_root"] == ""

    assert next_action["project_root"] == ""





def test_collect_next_stage_candidates_for_dc_excludes_existing_types(

    flowgate_env: dict[str, str],

) -> None:

    seeded = _seed_requirement_chain(

        group_id="FG-core-9002",

        ds_status="accepted",

        ds_next="N",

    )

    _register_doc(

        doc_id="FG-core-DC001",

        

        doc_type="DC",

        

        title="Design Completed",

        

        group_id=seeded["group_id"],

        

        target_id=seeded["ds_doc_id"],

        

        status="accepted",

        memo_file="DC001_done.md",

    )



    dc_doc = db.get_document_by_id("FG-core-DC001")

    candidates = process_service._collect_next_stage_candidates(

        dc_doc,

        seeded["group_id"],

        "FG",

        "core",

    )

    assert [item["type"] for item in candidates] == ["N"]

    assert candidates[0]["source_doc_id"] == "FG-core-DC001"

    assert candidates[0]["requirement_doc_id"] == seeded["r_doc_id"]

    # T051: verify the docs_root / project_root keys exist

    assert "docs_root" in candidates[0]

    assert "project_root" in candidates[0]



    _register_doc(

        doc_id="FG-core-N001",

        doc_type="N",

        title="Existing Investigation Instruction",

        group_id=seeded["group_id"],

        target_id="FG-core-DC001",

        status="accepted",

        memo_file="N001_investigation.md",

    )

    assert process_service._collect_next_stage_candidates(

        dc_doc,

        seeded["group_id"],

        "FG",

        "core",

    ) == []





def test_get_group_detail_ds_stage_lists_all_candidates_with_context(

    flowgate_env: dict[str, str],

) -> None:

    seeded = _seed_requirement_chain(

        group_id="FG-core-9003",

        ds_status="accepted",

        ds_next="D",

    )



    detail = process_service.get_group_detail(seeded["group_id"])



    assert detail is not None

    assert detail["current_action_type"] == "DS"

    assert detail["selected_next_action_type"] == "D"

    assert [item["type"] for item in detail["next_action_candidates"]] == [

        "D",

        "P",

        "L",

        "DB",

        "DC",

    ]

    for item in detail["next_action_candidates"]:

        assert item["source_doc_id"] == seeded["ds_doc_id"]

        assert item["requirement_doc_id"] == seeded["r_doc_id"]

        assert item["reference_qa"][0]["doc_id"] == seeded["q_doc_id"]

        # T051: verify the docs_root / project_root keys exist

        assert "docs_root" in item

        assert "project_root" in item





def test_group_detail_template_renders_next_action_panel(flowgate_env: dict[str, str]) -> None:

    seeded = _seed_requirement_chain(

        group_id="FG-core-9004",

        ds_status="accepted",

        ds_next="D",

    )

    detail = process_service.get_group_detail(seeded["group_id"])

    assert detail is not None



    template_root = Path(__file__).resolve().parents[3] / "templates"

    env = Environment(loader=FileSystemLoader(str(template_root)))

    template = env.get_template("flow_gate/group_detail.html")

    html = template.render(

        detail=detail,

        ctx="",

        request=None,

        result=None,

        extra_files=[],

        bundle_files=[],

        confirm_extra_required=False,

        confirm_extra_doc_id=None,

        confirm_extra_message="",

        focus_section_id=None,

    )



    # The group_detail template renders its UI labels in Korean (product UI text
    # is not translated). Assert the current rendered strings.
    assert "다음 예상 액션" in html

    assert "window.flowgateNextActionCandidates" in html

    assert "data-candidate-index=\"0\"" in html

    assert "멘트복사" in html

    # T051: verify the docs_root line is included in the copyNextAction JS

    assert "docs_root" in html





# ── Additional T051 tests ───────────────────────────────────────────────────





def test_next_action_source_files_are_docs_root_relative(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """When docs_root is set, source_file/requirement_file/reference_qa.file are returned as relative paths with allowed prefixes."""

    # Build the docs directory structure — use the allowed prefix (accept)

    docs_root = tmp_path / "docs"

    accept_dir = docs_root / "accept"

    accept_dir.mkdir(parents=True)



    ds_filename = "DS001_design_instruction.md"

    r_filename = "R001_requirement.md"

    q_filename = "Q001_question.md"

    a_filename = "A001_answer.md"



    (accept_dir / ds_filename).write_text("ds", encoding="utf-8")

    (accept_dir / r_filename).write_text("r", encoding="utf-8")

    (accept_dir / q_filename).write_text("q", encoding="utf-8")

    (accept_dir / a_filename).write_text("a", encoding="utf-8")



    # Register docs_root in project settings

    db.upsert_project_settings("FG", str(docs_root), "")



    seeded = _seed_requirement_chain(

        group_id="FG-core-9010",

        ds_status="open",

        ds_next="D",

        create_ds_file=True,

    )



    result = process_service.approve_document(int(seeded["ds_pk"]))

    assert result["status"] == "success"

    next_action = result["next_actions"][0]



    # Paths must be relative to the allowed prefix (accept) — they must not be only a filename or an absolute path

    expected_ds_rel = os.path.join("accept", ds_filename)

    expected_r_rel = os.path.join("accept", r_filename)

    expected_q_rel = os.path.join("accept", q_filename)

    expected_a_rel = os.path.join("accept", a_filename)



    assert next_action["source_file"] == expected_ds_rel

    assert not os.path.isabs(next_action["source_file"])

    assert next_action["requirement_file"] == expected_r_rel

    assert not os.path.isabs(next_action["requirement_file"])



    ref_q = next(item for item in next_action["reference_qa"] if item["type"] == "Q")

    assert ref_q["file"] == expected_q_rel

    ref_a = next(item for item in next_action["reference_qa"] if item["type"] == "A")

    assert ref_a["file"] == expected_a_rel



    # Verify the path starts with one of the 5 allowed prefixes

    allowed_prefixes = {"_rule", "accept", "inbox", "outbox", "reject"}

    for field in [next_action["source_file"], next_action["requirement_file"]]:

        first = field.replace("\\", "/").split("/")[0]

        assert first in allowed_prefixes, f"Path has an unexpected prefix: {field}"



    # Verify the docs_root value

    assert next_action["docs_root"] == str(docs_root)













































# ── Additional T052 tests ───────────────────────────────────────────────────
