"""flowgate.v02.0130: a waiting (ToD) card's AI settings change from the screen's own context.

0126 locked the card whenever the context the screen built from the Canvas (its run, its view: revision or draft)
differed from the server's ``context.current`` - and its tests only ever sent the server's own context back. These
tests build the context exactly as ``inspectorTarget.ts`` does, from the actual ``GET /canvas`` answer, then GET /
PATCH / re-GET the card settings through the real routes.
"""
import json

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    FA, X, B, _sql, db_path, decided, env, flow, ledger, line_doc, node_run, roles, start, starts, wired,
)
from test_fg_card_settings import _h, get_settings, patch, node, edge, put_draft


def url(key):
    return f"/api/v1/flows/{FA}/nodes/{key}/settings"


def canvas(env, view=None):
    res = env["client"].get(f"/api/v1/flows/{FA}/canvas", params={"view": view} if view else None, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def screen_context(answer):
    """``inspectorTarget.ts`` settingsContext: the Canvas run id, the viewed revision in a revision view, the draft
    version in a draft view."""
    view = answer.get("view") or {}
    run = answer.get("run") or {}
    mode = view.get("mode")
    return {"run_id": run.get("run_id"),
            "graph_revision_no": view.get("revision_no") if mode == "revision" else None,
            "draft_version": view.get("draft_version") if mode == "draft" else None}


def tod(env, key):
    """The reported state: the card's report reviewed and refused, review count exhausted, an approval wait recorded
    (needs intervention) - nothing running."""
    from flowgate.card_settings import legacy_adapter
    _sql(env["db"], "UPDATE graph_node_runs SET status = ? WHERE node_key = ?", ("review", key))
    report = line_doc(env, key, "report")
    _sql(env["db"], "UPDATE documents SET doc_review_status = ? WHERE doc_id = ?", ("revised", report))
    legacy_adapter.record_approval_wait(FA, key, report, "review_count_exhausted")
    return report


def draft_with_new_card(env):
    from modules.flow_gate.graph_workflow.store import definition_store
    original = definition_store.revision(FA, 1)
    version = (definition_store.draft_header(FA) or {}).get("draft_version", 0)
    kept = [{"edge_id": e["edge_id"], "client_key": None, "source": e["source"], "target": e["target"],
             "condition": e["condition"]} for e in original["edges"]]
    last = original["edges"][-1]["target"]
    return put_draft(env, FA, version,
                     [node(n["node_key"], n["doc_type"]) for n in original["nodes"]] + [node("tmp_new")],
                     kept + [edge("tmp_e_new", last, "tmp_new")])


def edit_and_reread(env, key, screen):
    read = env["client"].get(url(key), params={"context": json.dumps(screen)}, headers=_h())
    assert read.status_code == 200, read.text
    before = read.json()
    assert before["node_running"] is False
    assert before["locked"] is False, before
    assert before["settings_capability"] == {"editable": True, "reason": None}
    version = before["settings_version"]
    current = before
    for field, value in [("execution_role", {"kind": "assigned", "provider_id": B}),
                         ("review_role", {"kind": "assigned", "provider_id": X}),
                         ("review_count", 3)]:
        # useCardSettings sends back the context the read answered.
        current = patch(env, FA, key, {"field": field, "value": value,
                                       "expected_settings_version": current["settings_version"],
                                       "expected_context": current["context"]})
        version += 1
        assert current["settings_version"] == version and current["locked"] is False
    # F5 / reselection: a new read with the screen's context again.
    fresh = env["client"].get(url(key), params={"context": json.dumps(screen)}, headers=_h()).json()
    assert fresh["settings_version"] == version
    assert fresh["locked"] is False
    assert fresh["fields"]["review_count"]["value"] == 3
    assert fresh["ai_roles"]["roles"]["execution"]["selected"]["provider_id"] == B
    assert fresh["ai_roles"]["roles"]["review"]["selected"]["provider_id"] == X
    return fresh


def test_tod_card_editable_from_revision_view_context(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    tod(env, key)
    screen = screen_context(canvas(env, "revision"))
    edit_and_reread(env, key, screen)
    assert ledger(env) == [] and starts == []


def test_tod_card_editable_from_draft_view_context(env, decided, starts):
    """The user edits the Flow's draft after the run started (the Canvas shows the draft): 0126 locked every card."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    tod(env, key)
    draft_with_new_card(env)
    screen = screen_context(canvas(env, "draft"))
    assert screen["draft_version"] is not None and screen["graph_revision_no"] is None
    fresh = edit_and_reread(env, key, screen)
    assert fresh["context_matches"] is False          # the screen's view differs; the card is not locked for it
    assert ledger(env) == [] and starts == []


def test_tod_card_editable_from_other_revision_or_run_context(env, decided, starts):
    """The screen views a revision other than the run's, or names another / no run: still editable."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    run = start(env)
    tod(env, key)
    for screen in ({"run_id": run["run_id"], "graph_revision_no": 0, "draft_version": None},
                   {"run_id": run["run_id"], "graph_revision_no": 2, "draft_version": None},
                   {"run_id": None, "graph_revision_no": 1, "draft_version": None},
                   {"run_id": "another-run", "graph_revision_no": 1, "draft_version": None}):
        answer = env["client"].get(url(key), params={"context": json.dumps(screen)}, headers=_h()).json()
        assert answer["locked"] is False and answer["settings_capability"]["editable"] is True, (screen, answer)
        assert answer["context"] == {"run_id": run["run_id"], "graph_revision_no": 1, "draft_version": None}


def test_running_card_stays_locked_whatever_the_screen_context(env, decided, starts):
    key = flow(env)
    roles(key, X, "not_used", "not_used")
    start(env)
    [execution] = ledger(env)
    _sql(env["db"], "UPDATE graph_attempt_runs SET status = ? WHERE exec_id = ?", ("running", execution["exec_id"]))
    screen = screen_context(canvas(env, "revision"))
    answer = env["client"].get(url(key), params={"context": json.dumps(screen)}, headers=_h()).json()
    assert answer["locked"] is True and answer["node_running"] is True
    assert answer["settings_capability"] == {"editable": False, "reason": "node_settings_locked"}
    refused = patch(env, FA, key, {"field": "review_count", "value": 2, "expected_context": answer["context"],
                                   "expected_settings_version": answer["settings_version"]}, expect=409)
    assert refused["error"]["code"] == "node_settings_locked"


def test_card_only_in_the_draft_takes_settings_during_a_run(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    added = draft_with_new_card(env)["key_map"]["tmp_new"]
    before = get_settings(env, FA, added)
    assert before["locked"] is False
    after = patch(env, FA, added, {"field": "review_count", "value": 2, "expected_context": before["context"],
                                   "expected_settings_version": before["settings_version"]})
    assert after["fields"]["review_count"]["value"] == 2
