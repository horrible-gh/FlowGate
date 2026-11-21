"""Independently selectable scenarios; shared fixtures remain in test_process_service_next_actions.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_process_service_next_actions import (
    Environment,
    FileSystemLoader,
    Path,
    _register_doc,
    _seed_requirement_chain,
    _write_text,
    db,
    flowgate_env,
    os,
    process_service,
    pytest,
    sys,
)

def test_next_action_docs_root_always_in_candidate(flowgate_env: dict[str, str]) -> None:

    """Even when docs_root is unset, the candidate dictionary always contains docs_root / project_root keys."""

    seeded = _seed_requirement_chain(

        group_id="FG-core-9011",

        ds_status="accepted",

        ds_next="N",

    )

    _register_doc(

        doc_id="FG-core-DC002",

        

        doc_type="DC",

        

        title="Design Completed",

        

        group_id=seeded["group_id"],

        

        target_id=seeded["ds_doc_id"],

        

        status="accepted",

        memo_file="DC002_done.md",

    )

    dc_doc = db.get_document_by_id("FG-core-DC002")

    candidates = process_service._collect_next_stage_candidates(

        dc_doc,

        seeded["group_id"],

        "FG",

        "core",

    )

    assert candidates

    for c in candidates:

        assert "docs_root" in c

        assert "project_root" in c

        assert c["docs_root"] == ""

        assert c["project_root"] == ""
def test_next_action_project_root_only_for_T_in_template(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """The T-candidate copy-mention JS includes the project_root line, and non-T candidates do not."""

    docs_root = tmp_path / "docs"

    docs_root.mkdir()

    project_root = tmp_path / "project"

    project_root.mkdir()

    db.upsert_project_settings("FG", str(docs_root), str(project_root))



    # Produce a T candidate via the R stage: GROUP_NEXT_ACTION_FLOW["R"] yields
    # ("DS", "N", "T"). (The former DC-based path is no longer valid — DC is
    # excluded from group history for current-stage computation, see
    # GROUP_HISTORY_EXCLUDED_TYPES, so a DC doc never produces a T candidate.)
    group_id = "FG-core-9012"

    db.insert_group(group_id, "FG", "core", "Next Actions Test Group", "medium")

    _register_doc(

        doc_id="FG-core-R001",

        doc_type="R",

        title="Next Step Guidance Requirements",

        group_id=group_id,

        status="accepted",

        memo_file="R001_requirement.md",

    )

    _register_doc(

        doc_id="FG-core-AR001",

        doc_type="AR",

        title="Requirement Approval Request",

        group_id=group_id,

        target_id="FG-core-R001",

        status="accepted",

        memo_file="AR001_request.md",

    )



    detail = process_service.get_group_detail(group_id)

    assert detail is not None

    t_candidates = [c for c in detail["next_action_candidates"] if c["type"] == "T"]

    assert t_candidates, "No T candidates found"

    t_candidate = t_candidates[0]

    assert t_candidate["project_root"] == str(project_root)



    non_t_candidates = [c for c in detail["next_action_candidates"] if c["type"] != "T"]

    if non_t_candidates:

        # The project_root key exists, but the JS outputs it only when type==T

        # At the data level, verify that every candidate has a project_root key

        for c in non_t_candidates:

            assert "project_root" in c



    # Verify that the HTML rendering includes the project_root conditional logic

    from jinja2 import Environment, FileSystemLoader

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

    assert "project_root" in html

    assert "candidate.type" in html or "=== 'T'" in html
def test_next_action_docs_root_fallback_to_absolute_path(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """When docs_root is set and the file is missing, use the absolute-path fallback (not a bare filename)."""

    docs_root = tmp_path / "docs"

    docs_root.mkdir()

    db.upsert_project_settings("FG", str(docs_root), "")



    seeded = _seed_requirement_chain(

        group_id="FG-core-9013",

        ds_status="open",

        ds_next="D",

        create_ds_file=True,

    )



    result = process_service.approve_document(int(seeded["ds_pk"]))

    assert result["status"] == "success"

    next_action = result["next_actions"][0]



    # T051: docs_root is set but the file is missing -> absolute-path fallback to an allowed storage bucket (accept/)

    # Approval processing moves the file from INBOX_DIR to ACCEPT_DIR/...

    ds_filename = seeded["ds_filename"]

    source_file = next_action["source_file"]

    assert source_file  # Must not be empty

    assert os.path.isabs(source_file)  # Must be an absolute path

    assert ds_filename in source_file  # Original filename must be included in the path

    assert source_file != ds_filename  # Bare filename is not allowed
def test_to_docs_root_relative_forbidden_prefix_not_exposed(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """For forbidden prefixes inside docs_root (such as processed), do not expose the path and return an empty string."""

    docs_root = tmp_path / "docs"

    (docs_root / "processed").mkdir(parents=True)

    filename = "DS_forbidden.md"

    (docs_root / "processed" / filename).write_text("content", encoding="utf-8")



    result = process_service._to_docs_root_relative(filename, str(docs_root))



    # A processed path must never be exposed

    assert "processed" not in result.replace("\\", "/")

    # The file is not present in an allowed storage bucket either, so return an empty string

    assert result == ""
def test_to_docs_root_relative_storage_fallback_to_accept(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """When docs_root is unset, return the absolute path under storage accept/."""

    filename = "R_storage_test.md"

    abs_path = os.path.join(db.ACCEPT_DIR, filename)

    _write_text(abs_path, "content")



    result = process_service._to_docs_root_relative(filename, "")



    assert result == abs_path

    assert os.path.isabs(result)

    # Verify that the path includes the allowed prefix (accept)

    assert "accept" in result.replace("\\", "/")
