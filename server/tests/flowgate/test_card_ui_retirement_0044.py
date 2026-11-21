"""retire new WP structure while retaining stored cards and documents."""
import pytest
from tests.flowgate.test_fg_card_documents import (db_path, env, FB, _sql, _h, node, put_draft, canvas, line_rows)

def test_old_wp_card_and_document_survive_draft_save_and_missing_lines_are_not_recreated(env):
    from modules.flow_gate.db import groups, documents
    from flowgate.card_documents import repository, slots, legacy_adapter
    key = put_draft(env, FB, 0, [node("tmp_old", "T")])["key_map"]["tmp_old"]
    # Seed the stored pre-retirement shape, never relax the new-card policy in the fixture.
    _sql(env["db"], "UPDATE graph_draft_nodes SET doc_type='WP' WHERE group_id=? AND node_key=?", (FB, key))
    spec = slots.card_slots("document", "WP")[0]
    doc_id = legacy_adapter.create_document(groups.get_by_id(FB), {"node_key": key, "label": "Old WP"}, spec, "u_kim")
    repository.insert(FB, key, "plan", "WP", doc_id, True, 0, "u_kim")
    before = documents.get_by_id(doc_id)
    saved = put_draft(env, FB, 1, [node(key, "WP")])
    assert saved["card_documents"][key][0]["doc_id"] == doc_id
    assert documents.get_by_id(doc_id) == before
    assert not any(r["slot"] == "deploy" for r in line_rows(env, FB, key))
    assert canvas(env, FB)

def test_retyping_existing_work_card_to_wp_is_refused_atomically(env):
    key = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    before = line_rows(env, FB)
    response = env["client"].put(f"/api/v1/flows/{FB}/graph/draft", headers=_h(),
        json={"draft_version": 1, "nodes": [node(key, "WP")], "edges": []})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "work_plan_retired"
    assert line_rows(env, FB) == before
    assert _sql(env["db"], "SELECT draft_version FROM graph_drafts WHERE group_id=?", (FB,))[0]["draft_version"] == 1

@pytest.mark.parametrize("operation", ["preview", "execute"])
def test_retired_deployment_never_enters_mutating_path(monkeypatch, operation):
    from flowgate.work_plan_deploy import service
    monkeypatch.setattr(service, "_read_state", lambda *_: pytest.fail("retired deployment read/mutation"))
    with pytest.raises(service.DeployError) as caught:
        if operation == "preview": service.preview("old-WP", "first", 1, "en")
        else: service.execute("old-WP", {}, "u", "en")
    assert caught.value.code == "work_plan_retired"
    assert caught.value.status == 410
