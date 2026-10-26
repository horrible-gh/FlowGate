from __future__ import annotations
from unittest.mock import patch

from modules.flow_gate import process_service


def test_create_requirement_rejects_second_r():
    """When a group already contains an 'R', create_requirement must return a
    structured error with code 'group_r_already_exists'.

    The guard lives in create_workflow_root after project/module and existing-group
    validation, so the data dependencies on that path (get_allowed_projects, get_group,
    get_documents_by_group_id) must all be stubbed.
    """
    existing = [{"doc_id": "proj.mod.0001-R", "group_id": "proj.mod.1", "type": "R"}]
    existing_group = {"group_id": "proj.mod.1", "project_id": "proj", "module": "mod"}

    with (
        patch("modules.flow_gate.process_service.db.get_allowed_projects", return_value=[]),
        patch("modules.flow_gate.process_service.db.get_group", return_value=existing_group),
        patch("modules.flow_gate.process_service.db.get_documents_by_group_id", return_value=existing),
    ):
        res = process_service.create_requirement(
            project="proj",
            module="mod",
            title="Example",
            slug="",
            priority="medium",
            body="",
            owner="admin",
            group_id="proj.mod.1",
            new_group_name="",
        )

    assert res.get("status") == "error", "Expected service to return error when R exists"
    assert isinstance(res.get("errors"), list) and isinstance(res["errors"][0], dict), "Expected structured error object"
    assert res["errors"][0].get("code") == "group_r_already_exists"
