"""Independently selectable scenarios; shared fixtures remain in test_fg_card_settings.py."""
from __future__ import annotations

from test_fg_card_settings import (
    AI,
    FA,
    FB,
    FastAPI,
    JSONResponse,
    Path,
    RUN_FIELDS,
    TEST_VALUE,
    TEST_VERSION,
    TestClient,
    _GIT,
    _MIGRATIONS_DIR,
    _POLICY,
    _SERVER_DIR,
    _TARGET_VIEW_KEYS_0148,
    _TestPolicy,
    _card_lines,
    _doc,
    _fake_ai_invoke,
    _gate_key,
    _get_refreshed,
    _git_cmd,
    _h,
    _put_raw,
    _run_one_card,
    _seed,
    _sql,
    _start,
    _start_job,
    base64,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    git_flow,
    json,
    legacy_cards,
    legacy_setting,
    node,
    os,
    passive_person_results,
    patch,
    placed,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    re,
    setting,
    shown,
    shutil,
    sqlite3,
    stored_rows,
    subprocess,
    sys,
)

def test_a_check_card_without_git_answers_its_target_state_and_refuses_a_branch(env):
    g = _gate_key(env)
    body = get_settings(env, FB, g)
    assert body["fields"]["merge_target_branch"] == {"value": None, "stored": False, "default": None, "choices": None}
    assert (body["merge_target_options"], body["default_target_branch"], body["push_remote_check_required"]) == (
        [], None, False)
    state = body["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("git_unavailable", "gate_git_unavailable",
                                                                 "git_disabled")
    assert state["current"]["inherited"] is True and state["current"]["branch"] is None
    for mode in (None, "hold", "commit"):                     # every merge mode checks a new branch the same way
        if mode:
            setting(env, FB, g, "merge_mode", mode)
        version = get_settings(env, FB, g)["settings_version"]
        refused = setting(env, FB, g, "merge_target_branch", "main", expect=409)
        assert refused["error"]["code"] == "gate_git_unavailable"
        assert refused["error"]["params"]["reason"] == "git_disabled"
        assert (refused["error"]["params"]["field"], refused["error"]["params"]["target_branch"]) == (
            "merge_target_branch", "main")
        assert (g, "merge_target_branch") not in stored_rows(env, FB, g)
        assert get_settings(env, FB, g)["settings_version"] == version
    bad = setting(env, FB, g, "merge_target_branch", " main", expect=422)      # the value itself: no Git asked
    assert bad["error"]["params"]["errors"] == [{"field": "value", "reason": "surrounding_whitespace"}]
    # a stored target is shown with its state, and null clears it with no Git read
    _put_raw(env, FB, g, "merge_target_branch", '"feature/gone"')
    held = get_settings(env, FB, g)
    assert held["fields"]["merge_target_branch"]["value"] == "feature/gone"
    assert held["merge_target_state"]["current"] == {
        "branch": "feature/gone", "inherited": False, "checked": True, "error": "gate_git_unavailable",
        "unmerge_supported": None, "warnings": []}
    version = held["settings_version"]
    cleared = patch(env, FB, g, {"field": "merge_target_branch", "value": None, "expected_settings_version": version})
    assert cleared["fields"]["merge_target_branch"]["stored"] is False and cleared["settings_version"] == version + 1
    # the repository is the authenticated flow's own project: a client cannot name another one
    other = patch(env, FB, g, {"field": "merge_target_branch", "value": "main", "project_id": "other"}, expect=422)
    assert other["error"]["params"]["errors"] == [{"field": "project_id", "reason": "unknown_field"}]
def test_push_on_saves_the_origin_remote_with_it_in_one_change(env):
    g = _gate_key(env)
    on = setting(env, FB, g, "push", True)
    assert (shown(on)["push"], shown(on)["merge_target"], on["settings_version"]) == (True, "origin", 1)
    assert on["push_remote_check_required"] is False and on["legacy"] == {}
    assert stored_rows(env, FB, g) == {(g, "merge_target"): '"origin"', (g, "push"): "true"}
    assert setting(env, FB, g, "push", True)["settings_version"] == 1           # both already so: no change
    off = setting(env, FB, g, "push", False)                                    # off keeps the chosen remote
    assert (shown(off)["push"], shown(off)["merge_target"], off["settings_version"]) == (False, "origin", 2)
    assert shown(setting(env, FB, g, "push", None))["merge_target"] == "origin"
    # an old client's single write of the remote keeps its allowed values
    assert shown(setting(env, FB, g, "merge_target", None))["merge_target"] is None
    assert shown(setting(env, FB, g, "merge_target", "origin"))["merge_target"] == "origin"
    setting(env, FB, g, "merge_target", "main", expect=422)
def test_push_on_with_no_remote_is_shown_and_corrected_by_saving_push_again(env):
    g = _gate_key(env)
    _put_raw(env, FB, g, "push", "true")                                         # an old push=true with no remote
    before = get_settings(env, FB, g)
    assert (before["push_remote_check_required"], before["settings_version"]) == (True, 0)
    assert get_settings(env, FB, g)["push_remote_check_required"] is True        # a GET corrects nothing
    assert stored_rows(env, FB, g) == {(g, "push"): "true"}
    fixed = patch(env, FB, g, {"field": "push", "value": True, "expected_settings_version": 0})
    assert (shown(fixed)["merge_target"], fixed["settings_version"], fixed["push_remote_check_required"]) == (
        "origin", 1, False)
def test_push_on_keeps_the_legacy_branch_and_refuses_a_conflicting_one(env):
    g = _gate_key(env)
    _put_raw(env, FB, g, "legacy_merge_target", '"release/old"')
    kept = setting(env, FB, g, "push", True)
    assert kept["legacy"] == {"legacy_merge_target": "release/old"} and shown(kept)["merge_target"] == "origin"
    assert shown(kept)["merge_target_branch"] is None                           # never moved into the new field
    keys = put_draft(env, FA, 0, [node("tmp_a", None, "gate"), node("tmp_b", None, "gate")])["key_map"]
    a, b = keys["tmp_a"], keys["tmp_b"]
    # an old branch name migration 158 has not moved yet: kept as the legacy value in the same change
    _put_raw(env, FA, a, "merge_target", '"release/v0.2"')
    moved = setting(env, FA, a, "push", True)
    assert moved["legacy"] == {"legacy_merge_target": "release/v0.2"} and moved["settings_version"] == 1
    assert stored_rows(env, FA, a) == {(a, "legacy_merge_target"): '"release/v0.2"',
                                       (a, "merge_target"): '"origin"', (a, "push"): "true"}
    # a different legacy value already kept: the whole change is refused and nothing is written
    _put_raw(env, FA, b, "merge_target", '"release/v0.2"')
    _put_raw(env, FA, b, "legacy_merge_target", '"older"')
    rows = stored_rows(env, FA, b)
    refused = setting(env, FA, b, "push", True, expect=409)
    assert refused["error"]["code"] == "gate_settings_invalid"
    assert refused["error"]["params"]["reason"] == "legacy_merge_target_conflict"
    assert stored_rows(env, FA, b) == rows and get_settings(env, FA, b)["settings_version"] == 0
def test_push_on_writes_both_or_nothing_and_a_stale_version_writes_neither(env, monkeypatch):
    from flowgate.card_settings import repository

    g = _gate_key(env)
    real = repository.put

    def failing(flow_id, node_key, field, value, by):
        if field == "push":
            raise RuntimeError("write failed")
        return real(flow_id, node_key, field, value, by)

    monkeypatch.setattr(repository, "put", failing)
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{g}/settings", json={"field": "push", "value": True},
                              headers=_h())
    assert res.status_code == 500
    monkeypatch.setattr(repository, "put", real)
    assert stored_rows(env, FB, g) == {} and get_settings(env, FB, g)["settings_version"] == 0   # remote rolled back
    # two screens drawn from version 0: the first change wins, the other writes neither push nor the remote
    assert patch(env, FB, g, {"field": "archive", "value": True, "expected_settings_version": 0})["settings_version"] == 1
    stale = patch(env, FB, g, {"field": "push", "value": True, "expected_settings_version": 0}, expect=409)
    assert stale["error"]["code"] == "gate_context_changed"
    assert stored_rows(env, FB, g) == {(g, "archive"): "true"} and get_settings(env, FB, g)["settings_version"] == 1
@pytest.mark.parametrize("status, code, details", [
    (422, "merge_target_invalid", {}),
    (409, "merge_target_remote_only", {}),
    (409, "merge_target_internal_slot", {"connected_group_id": "flowgate.v02.0091"}),
    (404, "merge_target_not_found", {}),
    (409, "merge_target_same_branch", {}),
    (409, "merge_target_incoming_work_base", {"incoming_work_base_commits": 3, "work_base_ref": "feature/v0.2.0"}),
    (409, "merge_target_preview_unavailable", {"work_base_ref": "feature/v0.2.0"}),
    (409, "merge_target_busy", {"merge_id": 7, "blocking_group_id": "flowgate.v02.0091",
                                "route": "/api/v1/groups/x/git/merge/7", "project_id": "flowgate"}),
    (409, "merge_target_owner_mismatch", {"reason": "owner_marker_mismatch"}),
    (409, "base_checkout_branch_mismatch", {"base_branch": "main", "checked_out_branch": None,
                                            "base_root": "C:/secret/storage/src/flowgate/main"}),
    (409, "base_dirty", {"files": ["a.txt"]}),
    (409, "merge_conflict_open", {"merge_id": 3}),
])
def test_a_refused_target_keeps_its_code_and_shows_no_path_or_diagnostic(env, monkeypatch, status, code, details):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.git.credentials import GitServiceError

    def judge(group, branch):
        assert (group["group_id"], group["project_id"]) == (FB, "flowgate")      # the authenticated flow's own
        raise GitServiceError(status, code, "fatal: C:/secret/storage/src", details={"target_branch": branch, **details},
                              diagnostic="fatal: C:/secret/storage/src")

    monkeypatch.setattr(legacy, "judge_merge_target", judge)
    g = _gate_key(env)
    body = setting(env, FB, g, "merge_target_branch", "feature/x", expect=status)
    error = body["error"]
    assert (error["code"], error["params"]["reason"]) == (code, code)
    assert (error["params"]["field"], error["params"]["target_branch"]) == ("merge_target_branch", "feature/x")
    for key in ("incoming_work_base_commits", "merge_id", "connected_group_id", "base_branch"):
        if key in details:
            assert error["params"][key] == details[key]
    assert "secret" not in json.dumps(body) and "route" not in error["params"]
    assert stored_rows(env, FB) == {} and get_settings(env, FB, g)["settings_version"] == 0
def test_a_git_failure_of_the_save_is_git_unavailable_never_invalid_request(env, monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.git.credentials import GitServiceError

    g = _gate_key(env)
    for raised, reason in ((GitServiceError(409, "gate_git_unavailable", "x", details={"reason": "ledger_unavailable"}),
                            "ledger_unavailable"),
                           (GitServiceError(500, "git_error", "fatal: C:/secret"), "git_failed")):
        monkeypatch.setattr(legacy, "judge_merge_target", lambda group, branch, exc=raised: (_ for _ in ()).throw(exc))
        body = setting(env, FB, g, "merge_target_branch", "main", expect=409)
        assert (body["error"]["code"], body["error"]["params"]["reason"]) == ("gate_git_unavailable", reason)
        assert "secret" not in json.dumps(body)
    assert stored_rows(env, FB) == {}
@pytest.mark.parametrize("field,value", [("archive", True), ("keep_branch", True), ("merge_mode", "merge"),
                                         ("merge_target", "origin"), ("push", True)])
def test_0148_opted_in_patch_saves_without_reading_targets(env, monkeypatch, field, value):
    from flowgate.card_settings import legacy_adapter as legacy

    g = _gate_key(env)
    before = get_settings(env, FB, g)
    calls = []

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("unrelated PATCH must not read Git candidates")

    monkeypatch.setattr(legacy, "merge_target_view", forbidden)
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{g}/settings?keep_merge_targets=true", headers=_h(),
                              json={"field": field, "value": value,
                                    "expected_settings_version": before["settings_version"],
                                    "expected_context": before["context"]})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert calls == []
    assert saved["merge_targets_kept"] is True
    assert not _TARGET_VIEW_KEYS_0148.intersection(saved)
    assert saved["push_remote_check_required"] is False
    assert saved["fields"][field]["value"] == value
    assert saved["settings_version"] == before["settings_version"] + 1
    assert saved["context"] == before["context"]
    assert set(saved) == (set(before) - _TARGET_VIEW_KEYS_0148) | {"merge_targets_kept"}
    assert (g, field) in stored_rows(env, FB, g)
def test_0148_default_false_and_unknown_query_keep_identical_full_responses(env, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    monkeypatch.setattr(envelope, "server_ts", lambda: "2026-11-07T00:00:00.000+00:00")
    g = _gate_key(env)
    url = f"/api/v1/flows/{FB}/nodes/{g}/settings"
    body = {"field": "archive", "value": True}
    first = env["client"].patch(url, headers=_h(), json=body)
    assert first.status_code == 200
    assert _TARGET_VIEW_KEYS_0148.issubset(first.json())
    assert "merge_targets_kept" not in first.json()
    for query in ("", "?keep_merge_targets=false", "?keep_merge_targets=0", "?keep_merge_targets=invalid",
                  "?future_response_option=true"):
        again = env["client"].patch(url + query, headers=_h(), json=body)
        assert again.status_code == 200 and again.content == first.content
@pytest.mark.parametrize("query", ["true", "1", "yes", "%20TRUE%20"])
def test_0148_query_opt_in_does_not_extend_the_patch_body(env, query):
    g = _gate_key(env)
    url = f"/api/v1/flows/{FB}/nodes/{g}/settings?keep_merge_targets={query}"
    body = {"field": "archive", "value": True}
    saved = env["client"].patch(url, headers=_h(), json=body)
    assert saved.status_code == 200 and saved.json()["merge_targets_kept"] is True
    refused = env["client"].patch(url, headers=_h(), json={**body, "keep_merge_targets": True})
    assert refused.status_code == 422
    assert refused.json()["error"]["params"]["errors"] == [
        {"field": "keep_merge_targets", "reason": "unknown_field"}]
    stale = env["client"].patch(url, headers=_h(), json={**body, "expected_settings_version": 0})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "gate_context_changed"
def test_0148_kept_targets_still_report_missing_push_remote(env):
    g = _gate_key(env)
    _put_raw(env, FB, g, "push", "true")
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{g}/settings?keep_merge_targets=true", headers=_h(),
                              json={"field": "archive", "value": True})
    assert res.status_code == 200 and res.json()["push_remote_check_required"] is True
    assert res.json()["merge_targets_kept"] is True
def test_0148_opt_in_is_only_a_check_card_response_option(env, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    monkeypatch.setattr(envelope, "server_ts", lambda: "2026-11-07T00:00:00.000+00:00")
    key = put_draft(env, FB, 0, [node("tmp_t")])["key_map"]["tmp_t"]
    url = f"/api/v1/flows/{FB}/nodes/{key}/settings"
    body = {"field": "time_limit", "value": None}
    plain = env["client"].patch(url, headers=_h(), json=body)
    opted = env["client"].patch(url + "?keep_merge_targets=true", headers=_h(), json=body)
    assert plain.status_code == opted.status_code == 200 and plain.content == opted.content
    assert "merge_targets_kept" not in opted.json()
def test_0148_opted_in_patch_does_not_wait_for_the_three_second_lookup(env, monkeypatch):
    import time
    from flowgate.card_settings import legacy_adapter as legacy

    g = _gate_key(env)
    real = legacy.merge_target_view
    calls = []

    def slow(*args):
        calls.append(args)
        time.sleep(3.0)
        return real(*args)

    monkeypatch.setattr(legacy, "merge_target_view", slow)
    url = f"/api/v1/flows/{FB}/nodes/{g}/settings"
    body = {"field": "archive", "value": True}
    started = time.perf_counter()
    plain = env["client"].patch(url, headers=_h(), json=body)
    plain_seconds = time.perf_counter() - started
    started = time.perf_counter()
    opted = env["client"].patch(url + "?keep_merge_targets=true", headers=_h(), json=body)
    opted_seconds = time.perf_counter() - started
    assert plain.status_code == opted.status_code == 200
    assert len(calls) == 1 and opted_seconds < 2.0 <= plain_seconds
    assert opted.json()["merge_targets_kept"] is True
    print(f"PATCH0148_EVIDENCE plain_seconds={plain_seconds:.3f} opted_seconds={opted_seconds:.3f} lookup_calls=1")
@pytest.mark.parametrize("action", ["refresh", "target", "clear"])
def test_0148_refresh_and_target_patch_discard_negative_cache(env, git_flow, monkeypatch, action):
    from modules.flow_gate.services.git import merge_target as mt

    g = _gate_key(env)
    setting(env, FB, g, "merge_target_branch", "main")
    mt.reset_check_targets()
    with monkeypatch.context() as m:
        m.setattr(mt, "_compute_snapshot", lambda basis, *_a: mt._failed_snapshot(
            mt.CHECK_STATUS_TIMEOUT, mt.CHECK_OPTIONS_TIMEOUT, default_branch=basis.work_base_ref, basis=None))
        assert get_settings(env, FB, g)["merge_target_state"]["status"] == "timeout"
    assert get_settings(env, FB, g)["merge_target_state"]["status"] == "timeout"
    if action == "refresh":
        saved = _get_refreshed(env, FB, g)
    else:
        value = "release/local" if action == "target" else None
        res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{g}/settings?keep_merge_targets=true", headers=_h(),
                                  json={"field": "merge_target_branch", "value": value})
        assert res.status_code == 200, res.text
        saved = res.json()
        assert saved["fields"]["merge_target_branch"]["value"] == value
    assert "merge_targets_kept" not in saved
    assert _TARGET_VIEW_KEYS_0148.issubset(saved)
    assert saved["merge_target_state"]["status"] == "ok"
    assert "release/local" in saved["merge_target_options"]
    assert "push_remote_check_required" in saved
