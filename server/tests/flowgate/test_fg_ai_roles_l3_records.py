"""L-3 K/A and DB-1 S-1~S-3 with fake provider boundaries and real SQLite storage."""
import copy
import json
import threading
from types import SimpleNamespace as NS
import urllib.error
import pytest
from fastapi import HTTPException
from flowgate.ai_roles import invocations as inv, record_store as store, invocation_view as view
from test_fg_card_settings import db_path, env, _h, FA, _sql  # noqa: F401

CLI = {"id": "aip_card", "name": "Display is not a model", "exec_type": "cli", "kind": "copilot",
       "cli_command": "copilot --model x --no-ask-user"}
API = dict(CLI, exec_type="api", kind="openai", api_model="x", api_base_url="https://fake.invalid")


def origin(purpose="result_write", stage="result", seq=0, pid="aip_card"):
    return {"origin_kind": "card_assigned", "purpose": purpose, "requested_ai_id": pid,
            "role_source": "card_explicit", "target": {"flow_id": FA, "graph_run_id": "old-run", "node_key": "removed",
                "attempt_id": "old-attempt", "stage": stage, "seq": seq, "doc_id": "doc", "doc_revision_no": 3}}


def run(rid="r", o=None):
    return {"run_id": rid, "invocation_origin": o or origin(), "graph_exec_id": "e",
            "provider_id": "aip_card", "requested_provider_id": "aip_card", "fallback_history": [],
            "cancel_event": threading.Event(), "timed_out": False}


def launch(r, provider=CLI, code=0, output='{"type":"assistant.message","data":{"model":"x"}}', classification="started_ok"):
    inv.begin(r, provider)
    if provider["exec_type"] == "cli":
        inv.cli_prepared(r, provider, "posix_sh")
        inv.cli_outcome(r, stdout_full=output, exit_code=code, timed_out=False, process_exited_itself=True)
    else:
        inv.api_response(r, inv.ModelResponse(None, [], {}, {"model": "x"}))
    inv.finish(r, classification)
    return r["invocations"][-1]


def test_k2_each_launch_keeps_its_own_outcome_and_final_note(env, monkeypatch):
    monkeypatch.setattr(store, "_fingerprint_key", lambda: b"fake-test-key")
    r = run()
    first = copy.deepcopy(launch(r, code=0, output=""))
    second = launch(r, code=1)
    r.update(end_reason="exited", stop_code="question_pending", exit_code=99)
    inv.finalize(r)
    rows = store.by_run("r")
    assert [x["result"] for x in rows] == ["succeeded", "call_failed"]
    assert [x["exit_code"] for x in rows] == [0, 1]
    assert rows[0]["run_stop_code"] is None and rows[1]["run_stop_code"] == "question_pending"
    assert second["link_kind"] == "same_provider_restart" and second["prev_inv_id"] == first["inv_id"]
    assert rows[0]["chain_root_id"] == rows[1]["chain_root_id"] == first["inv_id"]
    assert rows[0]["ended_at"] == first["ended_at"]
    inv.finish(r, "spawn_failed")
    inv.flush(r)
    assert store.by_run("r") == rows  # no late save or finalize reclassification


def test_k1_k3_k7_links_cross_units_but_roots_do_not(env, monkeypatch):
    monkeypatch.setattr(store, "_fingerprint_key", lambda: b"fake-test-key")
    w = run("w")
    w1 = launch(w)
    inv.finalize(w)
    w2r = run("w2", origin("result_write_retry"))
    w2 = launch(w2r)
    inv.finalize(w2r)
    assert (w2["link_kind"], w2["prev_inv_id"], w2["chain_root_id"]) == ("stage_retry", w1["inv_id"], w1["inv_id"])
    o = origin("result_review_round", "result_review", 1)
    o["author"] = "ai"
    r = run("review", o)
    r["hop_kind"] = "review"
    r1 = launch(r)
    r["hop_kind"] = "rework"
    r2 = launch(r)
    r["hop_kind"] = "review"
    r3 = launch(r)
    inv.finalize(r)
    assert [v["hop_no"] for v in r["invocations"]] == [1, 2, 3]
    assert [(v["link_kind"], v["prev_inv_id"]) for v in (r1, r2, r3)] == [
        ("review_of", w2["inv_id"]), ("rework_of", r1["inv_id"]), ("review_of", r2["inv_id"])]
    assert all(v["chain_root_id"] == r1["inv_id"] for v in (r1, r2, r3))
    assert len(store.by_run("review")) == 3


@pytest.mark.parametrize("flag,result,detail", [("timed_out", "call_failed", "timeout"),
    ("cancelled", "user_stopped", "cancelled"), ("user_paused", "user_stopped", "user_paused")])
def test_a_current_flags_never_change_prior_launch(env, flag, result, detail):
    r = run()
    launch(r)
    inv.begin(r, API)
    if flag == "cancelled":
        r["cancel_event"].set()
    else:
        r[flag] = True
    inv.finish(r, "started_ok")
    rows = store.by_run("r")
    assert rows[0]["result"] == "succeeded"
    assert (rows[1]["result"], rows[1]["result_detail"]) == (result, detail)


def test_k4_refusal_and_s1_failed_prefix_no_late_save(env, monkeypatch):
    inv.refused(origin(), HTTPException(409, {"code": "inactive", "message": "fake"}), "e")
    row = store.for_node(FA, "removed", 10)[0]
    assert row["result"] == "admission_refused" and row["actual_ai_id"] is None
    assert view.item(row)["compare"]["status"] == "not_called"
    real, calls = store.write_record, []
    def fail_first(record):
        calls.append(record["inv_id"])
        return False if record["inv_id"] == "r#1" else real(record)
    monkeypatch.setattr(store, "write_record", fail_first)
    r = run()
    launch(r)
    launch(r)
    inv.finalize(r)
    assert set(calls) == {"r#1"} and store.by_run("r") == []
    assert r["invocations"] == []
    monkeypatch.setattr(store, "write_record", real)
    inv.flush(r)
    assert store.by_run("r") == []


def test_s1_retry_prefix_and_missing_outcome_and_orphan(env, monkeypatch):
    real, calls = store.write_record, []
    def once(record):
        calls.append(record["inv_id"])
        return False if len(calls) == 1 else real(record)
    monkeypatch.setattr(store, "write_record", once)
    r = run()
    launch(r, API)
    inv.begin(r, API)
    inv.begin(r, API)  # missing T-3 is closed before the next launch
    assert r["invocations"][-2]["result_detail"] == "launch_outcome_missing"
    inv.orphan("r")
    rows = store.by_run("r")
    assert rows[0]["result"] == "succeeded"
    assert rows[-1]["result_detail"] == "orphaned_by_restart"
    assert [r["launch_no"] for r in rows] == [1, 2, 3]


def test_p8_permissions_cursor_config_secrets_and_failure(env, monkeypatch):
    monkeypatch.setattr(store, "_fingerprint_key", lambda: b"fake-test-key")
    _sql(env["db"], "UPDATE ai_providers SET kind='copilot', cli_command=? WHERE provider_id='aip_card'", (CLI["cli_command"],))
    r = run()
    first = launch(r)
    launch(r)
    inv.finalize(r)
    client, url = env["client"], f"/api/v1/flows/{FA}/nodes/removed/ai-invocations"
    response = client.get(url+'?limit=1', headers=_h())
    assert response.status_code == 200
    body = response.json()
    item = body["invocations"][0]
    assert item["provider_config"]["config_state"] == "unchanged"
    assert item["compare"] == {"status": "same", "mismatch_found": False, "reconciled": True}
    assert body["next_cursor"]
    second = client.get(url, params=body["next_cursor"], headers=_h()).json()
    assert [x["inv_id"] for x in second["invocations"]] == [first["inv_id"]] and second["next_cursor"] is None
    assert all(key not in json.dumps(body) for key in ("provider_fingerprint", "unit_key_hash", "cli_command", "api_key"))
    _sql(env["db"], "UPDATE ai_providers SET cli_command='copilot --model y' WHERE provider_id='aip_card'")
    assert client.get(url, headers=_h()).json()["invocations"][0]["provider_config"]["config_state"] == "changed"
    bad = client.get(url+'?limit=0&before_seq=17&extra=x', headers=_h())
    assert bad.status_code == 422
    assert {e["reason"] for e in bad.json()["error"]["params"]["errors"]} == {"out_of_range", "required", "unknown_field"}
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer worker"}).status_code == 401
    assert client.get(url.replace(FA, 'flowgate.v99.9999'), headers=_h()).status_code == 404
    from modules.flow_gate.graph_workflow.api import envelope
    monkeypatch.setattr(envelope, "has_permission", lambda *a: False)
    assert client.get(url, headers=_h()).status_code == 403
    monkeypatch.setattr(envelope, "has_permission", lambda *a: True)
    monkeypatch.setattr(store, "available", lambda: False)
    assert client.get(url, headers=_h()).json()["available"] is False
    monkeypatch.setattr(store, "available", lambda: True)
    monkeypatch.setattr(store, "page_for_node", lambda *a: (_ for _ in ()).throw(RuntimeError("private-test-value")))
    fail = client.get(url, headers=_h())
    assert fail.status_code == 500 and 'private-test-value' not in fail.text


@pytest.mark.parametrize("failure", ["http", "transport", "timeout"])
def test_fake_api_second_turn_failure_is_partial_and_call_failed(env, monkeypatch, failure):
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import worker
    r = run()
    r.update(project_id="flowgate", group_id=FA, docs_target=1, raw_token="fake-token-only", token_id="fake-token-id",
             doc_ref=None, action_scope="new", mode="single")
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *a: "fake-key-only")
    monkeypatch.setattr(svc, "_remaining_sec", lambda *a: 60)
    monkeypatch.setattr(worker, "_provider_prompt", lambda *a: "fake prompt")
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a, **kw: None)
    count = []
    def post(*args, **kwargs):
        count.append(1)
        if len(count) == 1:
            return {"model": "x", "choices": [{"message": {"role": "assistant", "content": "first response"}}]}
        if failure == "http":
            raise urllib.error.HTTPError("https://fake.invalid", 529, "fake", {}, None)
        if failure == "timeout":
            raise urllib.error.URLError(TimeoutError("fake timeout"))
        raise ConnectionError("fake transport")
    monkeypatch.setattr(svc, "_http_post_json", post)
    assert worker._execute_provider_chain(r, [API], "fake prompt") is True
    row = store.by_run("r")[0]
    assert row["result"] == "call_failed" and row["result_detail"] == {"http": "model_http_error",
        "transport": "model_transport_error", "timeout": "model_call_timeout"}[failure]
    assert row["calls_total"] == 2 and row["calls_failed"] == 1 and row["confirmed_coverage"] == "partial"
    assert row["model_call_no"] == 2 and row["model_turn_no"] == 2
    assert json.loads(row["confirmed_model_values_json"]) == ["x"]


def test_fake_cli_uses_full_output_and_normalized_operator_command(env, monkeypatch, tmp_path):
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import worker, provider_cli
    r = run()
    r.update(scratch_dir=str(tmp_path), token_scratch_dir=str(tmp_path), raw_token="fake-token-only", api_base_url="")
    output = ('{"type":"assistant.message","data":{"model":"x"}}\n' + 'irrelevant\n' * 2000).encode()
    decision = {"effective_command": 'pushd "fake" && copilot --model x --output-last-message "fake"',
                "shell_kind": "posix_sh", "spawn_cwd": str(tmp_path)}
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda *a: (dict(decision), None))
    import time
    handle = NS(proc=NS(returncode=0), launched_mono=time.monotonic())
    monkeypatch.setattr(provider_cli, "_execute_local", lambda *a, **kw: (handle, (output, b"", False, None)))
    monkeypatch.setattr(provider_cli, "_recover_cli_last_message", lambda *a: None)
    monkeypatch.setattr(worker, "_provider_prompt", lambda *a: "fake prompt")
    assert worker._execute_provider_chain(r, [CLI], "fake prompt") is True
    row = store.by_run("r")[0]
    assert json.loads(row["confirmed_model_values_json"]) == ["x"] and row["confirmed_coverage"] == "full"
    assert row["requested_model_value"] == "x" and row["result"] == "succeeded"
    serialized = json.dumps(row)
    assert all(secret not in serialized for secret in ('fake-token-only', 'pushd', 'fake prompt', 'irrelevant'))


def test_s3_final_prefix_precedes_graph_and_question_handoffs(env, monkeypatch):
    from modules.flow_gate.services.ai_invoke import finalize
    r = run()
    launch(r)
    r.update(group_id=FA, end_reason="exited", stop_code="question_pending", graph_end_owned=True)
    checkpoints = []
    def successor(*a, **kw):
        assert r["_invocations_closed"] is True
        assert store.by_run("r")[0]["run_stop_code"] == "question_pending"
        checkpoints.append(True)
    monkeypatch.setattr(finalize._svc(), "peek_auto_resume", lambda *a: None)
    monkeypatch.setattr(finalize.terminal, "attempt", lambda *a: None)
    monkeypatch.setattr(finalize.terminal, "cleanup", successor)
    monkeypatch.setattr(finalize.ai_run_end_hooks, "run_ending", successor)
    monkeypatch.setattr(finalize.ai_run_end_hooks, "run_ended", successor)
    for name in ("_auto_resume_answered_question_chain", "_auto_resume_test_gate_chain",
                 "_continue_question_responder_chain", "_dispatch_question_responder"):
        monkeypatch.setattr(finalize, name, successor)
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a, **kw: None)
    finalize._finalize_run(r)
    assert len(checkpoints) == 7


def test_e21_missing_fingerprint_key_does_not_drop_evidence(env, monkeypatch):
    monkeypatch.setattr(store, "_fingerprint_key", lambda: (_ for _ in ()).throw(RuntimeError("fake unavailable")))
    r = run()
    launch(r)
    assert store.by_run("r")[0]["provider_fingerprint"] is None


def test_e30_root_read_failure_keeps_memory_links_and_w14_fills_only_null(env, monkeypatch):
    r = run()
    launch(r)
    original = store.first
    monkeypatch.setattr(store, "first", lambda *a: (_ for _ in ()).throw(RuntimeError("fake read failure")))
    second = launch(r)
    assert second["chain_root_id"] is None
    assert (second["link_kind"], second["prev_inv_id"]) == ("same_provider_restart", "r#1")
    monkeypatch.setattr(store, "first", original)
    inv.finalize(r)
    rows = store.by_run("r")
    assert rows[1]["chain_root_id"] == "r#1"
    assert view.item(rows[1])["chain_root_id"] == "r#1"


def test_disabled_journal_and_origin_missing_do_not_infer_a_card(env, monkeypatch):
    r = run()
    monkeypatch.setattr(store, "available", lambda: False)
    inv.begin(r, API)
    assert "invocations" not in r
    monkeypatch.setattr(store, "available", lambda: True)
    r.pop("invocation_origin")
    rec = launch(r, API)
    assert rec["origin_kind"] == "non_card" and rec["target_unresolved_reason"] == "origin_missing"
    assert rec["target_attempt_id"] is None and rec["inv_role"] is None


def test_nested_admission_refusal_is_recorded_once_with_original_code(env):
    @inv.admission
    def inner(**kw):
        raise HTTPException(409, {"code": "provider_unavailable", "message": "fake refusal"})
    @inv.admission
    def outer(**kw):
        return inner(**kw)
    with pytest.raises(HTTPException):
        outer(invocation_origin=origin(), graph_exec_id="e")
    rows = store.for_node(FA, "removed", 10)
    assert len(rows) == 1 and rows[0]["result_detail"] == "provider_unavailable"


def test_cli_execution_lease_refusal_keeps_its_launch_refused_detail(env):
    r = run()
    inv.begin(r, CLI)
    inv.cli_prepared(r, CLI, "posix_sh")
    inv.finish(r, "spawn_failed", "Graph execution lease refused")
    row = store.by_run("r")[0]
    assert row["result"] == "start_failed" and row["result_detail"] == "launch_refused"


@pytest.mark.parametrize("body", [None, [], "invalid body"], ids=["null", "array", "string"])
@pytest.mark.parametrize("kind", ["claude", "openai"])
@pytest.mark.parametrize("failed_turn", [1, 2])
def test_fake_api_non_object_response_keeps_startup_and_later_turn_classification(
        env, monkeypatch, body, kind, failed_turn):
    from modules.flow_gate.services import ai_invoke_service as svc, graph_execution_hooks
    from modules.flow_gate.services.ai_invoke import worker

    r = run()
    r.update(project_id="flowgate", group_id=FA, docs_target=1, raw_token="fake-token-only",
             token_id="fake-token-id", doc_ref=None, action_scope="new", mode="single")
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *a: "fake-key-only")
    monkeypatch.setattr(svc, "_remaining_sec", lambda *a: 60)
    monkeypatch.setattr(svc, "API_MAX_TURNS_PER_DOC", 2)
    monkeypatch.setattr(svc, "API_MAX_TOOL_NUDGES", 2)
    monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_provider_prompt", lambda *a: "fake prompt")
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a, **kw: None)
    counts = {"primary": 0, "fallback": 0}
    def post(url, headers, request, timeout):
        name = "fallback" if "fallback.invalid" in url else "primary"
        counts[name] += 1
        if name == "primary" and counts[name] == failed_turn:
            return body
        if kind == "claude":
            return {"model": "x", "content": [{"type": "text", "text": "first response"}]}
        return {"model": "x", "choices": [{"message": {"role": "assistant", "content": "first response"}}]}
    monkeypatch.setattr(svc, "_http_post_json", post)
    outcomes = []
    def execute(provider, prompt, state):
        result = worker._api_execute(provider, prompt, state)
        outcomes.append((provider["id"], result, state["api_turns_used"], state["turn_limit_exhausted"]))
        return result
    monkeypatch.setattr(svc, "_api_execute", execute)
    primary = dict(API, kind=kind)
    fallback = dict(primary, id="aip_fallback", api_base_url="https://fallback.invalid")
    assert worker._execute_provider_chain(r, [primary, fallback], "fake prompt") is True
    first = store.by_run("r")[0]
    assert outcomes[0][1][0] == ("spawn_failed" if failed_turn == 1 else "started_ok")
    assert outcomes[0][2:] == (failed_turn, False)
    if failed_turn == 1:
        assert "get" in outcomes[0][1][1]
        assert [o[0] for o in outcomes] == ["aip_card", "aip_fallback"]
        assert r["fallback_history"][0]["reason"] == "spawn_failed"
        assert first["result"] == "start_failed" and first["result_detail"] == "spawn_failed"
    else:
        assert outcomes[0][1] == ("started_ok", None)
        assert len(outcomes) == 1 and not r["fallback_history"]
        assert first["result"] == "call_failed" and first["result_detail"] == "model_transport_error"
        assert first["model_call_no"] == first["model_turn_no"] == failed_turn
        assert first["confirmed_coverage"] == "partial"
        assert json.loads(first["confirmed_model_values_json"]) == ["x"]
    assert counts == {"primary": failed_turn, "fallback": 2 if failed_turn == 1 else 0}
    assert not r.get("startup_retry_count")  # Malformed bodies are never transient retries.
    assert first["calls_total"] == failed_turn and first["calls_failed"] == 1
    assert first["calls_reported"] == failed_turn - 1
    assert first["calls_absent"] == first["calls_unreadable"] == 0


@pytest.mark.parametrize("provider", [CLI, API], ids=["cli", "api"])
def test_t6_orphan_unknown_calls_stay_null_in_storage_and_api(env, provider):
    r = run()
    inv.begin(r, provider)
    if provider["exec_type"] == "cli":
        inv.cli_prepared(r, provider, "posix_sh")
    else:
        # A received response lost with process memory must never become 'zero calls'.
        inv.api_response(r, inv.ModelResponse(None, [], {}, {"model": "x"}))
    assert store.by_run("r")[0]["confirmed_model_state"] is None
    inv.orphan("r")
    row = store.by_run("r")[0]
    assert (row["result"], row["result_detail"]) == ("call_failed", "orphaned_by_restart")
    assert row["confirmed_model_state"] == "unverifiable" and row["unverifiable_reason"] == "no_result"
    assert all(row["calls_" + key] is None for key in ("total", "reported", "absent", "unreadable", "failed"))
    response = env["client"].get(f"/api/v1/flows/{FA}/nodes/removed/ai-invocations", headers=_h())
    assert response.status_code == 200
    conf = response.json()["invocations"][0]["confirmed_model"]
    assert conf == dict(state="unverifiable", values=[], truncated=False, coverage=None,
                        calls=None, bad_lines=None, reason="no_result")
    before = copy.deepcopy(row)
    inv.orphan("r")
    assert store.by_run("r")[0] == before


def test_t6_orphan_preserves_evidence_without_copying_it_to_another_launch(env, monkeypatch):
    r = run()
    saved = copy.deepcopy(launch(r, API))
    unknown = dict(saved, inv_id="r#2", result="running", confirmed_model_state=None)
    saved["result"] = "running"
    ended = dict(saved, inv_id="r#3", result="succeeded")
    monkeypatch.setattr(store, "by_run", lambda *a: [saved, unknown, ended])
    writes = []
    monkeypatch.setattr(store, "finish", lambda inv_id, values: writes.append((inv_id, copy.deepcopy(values))))
    inv.orphan("r")
    assert [key for key, _ in writes] == ["r#1", "r#2"]
    for key in store.FINISH_COLUMNS:
        if key.startswith(("confirmed_", "calls_")) or key in ("bad_lines", "unverifiable_reason"):
            assert writes[0][1][key] == saved.get(key)
    assert writes[1][1]["confirmed_model_state"] == "unverifiable"
    assert writes[1][1]["unverifiable_reason"] == "no_result" and writes[1][1]["calls_total"] is None
