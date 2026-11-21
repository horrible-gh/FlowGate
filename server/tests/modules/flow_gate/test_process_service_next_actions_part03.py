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

def test_to_docs_root_relative_storage_fallback_to_inbox(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """When docs_root is unset, return the absolute path under storage inbox/ and never return a bare filename."""

    filename = "T_inbox_only.md"

    abs_path = os.path.join(db.INBOX_DIR, filename)

    _write_text(abs_path, "content")



    result = process_service._to_docs_root_relative(filename, "")



    assert result == abs_path

    assert result != filename  # Bare filename is not allowed
def test_to_docs_root_relative_file_not_found_returns_empty(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """Return an empty string when the file does not exist anywhere (never return a bare filename)."""

    result = process_service._to_docs_root_relative("nonexistent_file.md", "")

    assert result == ""
def test_next_action_no_forbidden_prefix_in_output(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """The full next_action output does not contain processed/cancelled/error/conflict/_legacy."""

    docs_root = tmp_path / "docs"

    # forbidden prefix dirs in docs_root

    for forbidden in ("processed", "cancelled", "error", "conflict", "_legacy"):

        (docs_root / forbidden).mkdir(parents=True)

        (docs_root / forbidden / "DS001_design_instruction.md").write_text(

            "content", encoding="utf-8"

        )

    db.upsert_project_settings("FG", str(docs_root), "")



    seeded = _seed_requirement_chain(

        group_id="FG-core-9016",

        ds_status="open",

        ds_next="D",

        create_ds_file=True,

    )



    result = process_service.approve_document(int(seeded["ds_pk"]))

    assert result["status"] == "success"



    forbidden_names = {"processed", "cancelled", "error", "conflict", "_legacy"}

    for na in result["next_actions"]:

        for field in ("source_file", "requirement_file", "docs_root"):

            path_str = (na.get(field) or "").replace("\\", "/")

            for fn in forbidden_names:

                assert fn not in path_str.split("/"), (

                    f"forbidden prefix '{fn}' found in {field}: {path_str}"

                )

        for qa_item in na.get("reference_qa") or []:

            path_str = (qa_item.get("file") or "").replace("\\", "/")

            for fn in forbidden_names:

                assert fn not in path_str.split("/"), (

                    f"forbidden prefix '{fn}' found in reference_qa.file: {path_str}"

                )
def test_to_docs_root_relative_fallback_under_docs_root_returns_relative(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """T052: if the fallback file is under docs_root (= STORAGE_DIR), return a docs_root-relative path.



    A prefixed filename ("AC004_R_fallback_test.md") has no exact match during the walk,

    so it is found only through suffix matching in _find_in_allowed_storage_buckets().

    Because the fallback absolute path is under docs_root (= STORAGE_DIR), it must be converted to a relative path.

    """

    docs_root = flowgate_env["STORAGE_DIR"]

    basename = "R_fallback_test.md"

    prefixed_name = "AC004_R_fallback_test.md"

    abs_path = os.path.join(db.OUTBOX_DIR, prefixed_name)

    _write_text(abs_path, "content")



    result = process_service._to_docs_root_relative(basename, docs_root)



    assert result, "Must not be an empty string"

    assert not os.path.isabs(result), f"Must be a relative path: {result}"

    assert result == os.path.join("outbox", prefixed_name)

    first = result.replace("\\", "/").split("/")[0]

    assert first in {"_rule", "accept", "inbox", "outbox", "reject"}, (

        f"Path has an unexpected prefix: {result}"

    )

    assert result != basename, "Bare filename is not allowed"
def test_to_docs_root_relative_fallback_not_under_docs_root_returns_absolute(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """T052: if the fallback file is not under docs_root, return the absolute path unchanged (preserve existing behavior)."""

    docs_root = tmp_path / "custom_docs"

    docs_root.mkdir()

    basename = "R_external_fallback.md"

    abs_path = os.path.join(db.ACCEPT_DIR, basename)

    _write_text(abs_path, "content")



    result = process_service._to_docs_root_relative(basename, str(docs_root))



    # ACCEPT_DIR (tmp_path/storage/accept) is not under custom_docs (tmp_path/custom_docs)

    assert os.path.isabs(result), f"Must be an absolute path: {result}"

    assert result == abs_path

    assert result != basename, "Bare filename is not allowed"
def test_next_action_all_doc_files_relative_when_docs_root_is_storage_dir(

    flowgate_env: dict[str, str],

    tmp_path: Path,

) -> None:

    """T052: when docs_root == STORAGE_DIR and R/Q/A files are stored with prefixed names,

    source_file/requirement_file/reference_qa.file are all returned as docs_root-relative paths.

    """

    docs_root = flowgate_env["STORAGE_DIR"]

    db.upsert_project_settings("FG", docs_root, "")



    seeded = _seed_requirement_chain(

        group_id="FG-core-9020",

        ds_status="open",

        ds_next="D",

        create_ds_file=True,

    )



    # Store R, Q, and A files in inbox with prefixed names -> no exact match during the walk, so they are found only through suffix matching

    r_prefixed = f"AC001_{seeded['r_filename']}"

    q_prefixed = f"AC002_{seeded['q_filename']}"

    a_prefixed = f"AC003_{seeded['a_filename']}"

    _write_text(os.path.join(db.INBOX_DIR, r_prefixed), "r")

    _write_text(os.path.join(db.INBOX_DIR, q_prefixed), "q")

    _write_text(os.path.join(db.INBOX_DIR, a_prefixed), "a")



    result = process_service.approve_document(int(seeded["ds_pk"]))

    assert result["status"] == "success"

    next_action = result["next_actions"][0]



    allowed = {"_rule", "accept", "inbox", "outbox", "reject"}



    # source_file: moved to accept/ after approval -> path relative to docs_root (=STORAGE_DIR)

    sf = next_action["source_file"]

    assert sf, "source_file must not be empty"

    assert not os.path.isabs(sf), f"source_file must be a relative path: {sf}"

    first = sf.replace("\\", "/").split("/")[0]

    assert first in allowed, f"source_file has an unexpected prefix: {sf}"

    assert sf != seeded["ds_filename"], "source_file must not be a bare filename"



    # requirement_file: prefixed name -> T052 fallback relative path

    rf = next_action["requirement_file"]

    assert rf, "requirement_file must not be empty"

    assert not os.path.isabs(rf), f"requirement_file must be a relative path: {rf}"

    first = rf.replace("\\", "/").split("/")[0]

    assert first in allowed, f"requirement_file has an unexpected prefix: {rf}"



    # reference_qa.file: both Q and A use prefixed names -> T052 fallback relative path

    for qa_item in next_action["reference_qa"]:

        qf = qa_item["file"]

        assert qf, f"reference_qa[{qa_item['type']}].file must not be empty"

        assert not os.path.isabs(qf), f"reference_qa.file must be a relative path: {qf}"

        first = qf.replace("\\", "/").split("/")[0]

        assert first in allowed, f"reference_qa.file has an unexpected prefix: {qf}"
