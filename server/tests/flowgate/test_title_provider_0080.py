"""Title policy and real remote queue boundary with a fake Agent transport."""
import threading
from types import SimpleNamespace
import pytest
from modules.flow_gate.settings import ai_title_provider as policy
from modules.flow_gate.settings import system_settings_service as settings
from modules.flow_gate.settings.routers import system as routes
from flowgate.conversation import auto_title, legacy_adapter, title_remote
from modules.flow_gate.services.ai_invoke import provider_remote, runtime, remote_supervisor
from modules.flow_gate.services import agent_jobs, graph_execution_hooks
from flowgate.ai_roles import invocations


@pytest.fixture
def catalog(monkeypatch):
    from modules.flow_gate.db import user_ai_preferences
    monkeypatch.setattr(user_ai_preferences, "get", lambda user: {"title_provider": "auto", "repeat_count_max": None})
    rows = {}
    providers = [dict(id="light", name="Haiku", enabled=True, exec_type="api"),
                 dict(id="remote", name="Agent", enabled=True, exec_type="cli", exec_location="remote",
                      kind="codex", cli_command="codex", target_agent_id="agent")]
    monkeypatch.setattr(policy.system_settings, "get", lambda key: rows.get(key))
    monkeypatch.setattr(policy.system_settings, "list_settings", lambda: list(rows.values()))
    monkeypatch.setattr(policy.system_settings, "get_value", lambda key, default=None: rows.get(key, {}).get("setting_value", default))
    def save(key, value, value_type, **kwargs):
        rows[key] = dict(setting_key=key, setting_value=value, value_type=value_type)
        return rows[key]
    monkeypatch.setattr(policy.system_settings, "set_value", save)
    monkeypatch.setattr(policy.ai_settings_service, "resolve_global", lambda: {"providers": providers})
    monkeypatch.setattr(policy.ai_providers, "get_public_row", lambda pid: next((p for p in providers if p["id"] == pid), None))
    monkeypatch.setattr(policy.ai_settings_service, "selectable_provider", lambda project, pid, user: next((p for p in providers if p["id"] == pid and p["enabled"]), None))
    return rows, providers


def test_default_get_and_save_contract(catalog):
    rows, providers = catalog
    assert settings.get_one(policy.KEY)["setting_value"] == "executor"
    assert next(r for r in settings.get_all() if r["setting_key"] == policy.KEY)["setting_value"] == "executor"
    assert policy.KEY not in rows
    routes.update_settings(routes.SettingsPatch(updates={policy.KEY: "remote"}), user={"user_id": "admin"})
    assert settings.get_one(policy.KEY)["setting_value"] == "remote"
    providers.clear()
    assert policy.value() == "remote"
    rows.clear()
    assert policy.value() == "executor"


@pytest.mark.parametrize("value", [None, "", "missing", " remote", "disabled"])
def test_invalid_setting_is_422_and_atomic(catalog, value):
    from fastapi import HTTPException
    _, providers = catalog
    providers.append(dict(id="disabled", enabled=False))
    with pytest.raises(HTTPException) as exc:
        routes.update_settings(routes.SettingsPatch(updates={policy.KEY: value}), user={"user_id": "admin"})
    assert exc.value.status_code == 422
    assert not catalog[0]


def test_off_executor_and_explicit_dispatch(catalog, monkeypatch):
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda project, pid, *a, **kw: (calls.append(pid) or "Title", None))
    monkeypatch.setattr(title_remote, "complete", lambda project, provider, *a: (calls.append(provider["id"]) or "Remote title", None))
    for setting, executor, expected in [("off", "remote", None), ("executor", "remote", "remote"), ("light", "remote", "light"), ("remote", "light", "remote")]:
        settings.set_values({policy.KEY: setting})
        before = len(calls)
        answer, reason = legacy_adapter.complete_naming("project", executor, "facts", "user", timeout=90, max_tokens=60)
        assert calls[before:] == ([] if expected is None else [expected])
        assert (reason == "title_disabled") if expected is None else bool(answer)


def test_remote_title_enqueues_and_collects_without_parent_lease(catalog, monkeypatch):
    _, providers = catalog
    calls = []
    class Jobs:
        def enqueue_remote_cli(self, **kwargs): calls.append(kwargs)
        def advance_ai_jobs(self): pass
        def get_ai_job(self, *args): return dict(status="DONE", lease_owner_agent_id="agent")
        def collect_ai_result(self, *args):
            return dict(status="DONE", lease_owner_agent_id="agent", collected=True,
                        started_at="now", child_exit_code=0, stdout="Remote title", stderr="")
        def release_ai_retrieval(self, *args): calls.append("released")
    monkeypatch.setattr(agent_jobs, "AgentJobService", Jobs)
    monkeypatch.setattr(provider_remote, "_binding", lambda p: dict(target_agent_id="agent", requester=None))
    monkeypatch.setattr(provider_remote, "agent_failure", lambda *a: None)
    monkeypatch.setattr(provider_remote, "_agent_workdir", lambda *a: "/jobs/${AGENT_JOB_ID}")
    monkeypatch.setattr(remote_supervisor, "prior_remote_unsettled", lambda run: False)
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a: True)
    monkeypatch.setattr(invocations, "cli_prepared", lambda *a: None)
    monkeypatch.setattr(invocations, "cli_outcome", lambda *a, **kw: None)
    monkeypatch.setattr(policy.ai_settings_service, "normalize_cli_command", lambda *a: "codex")
    before = set(runtime._runs)
    answer, reason = title_remote.complete("project", providers[1], "Only name these facts", "user", 90)
    assert answer == "Remote title" and reason is None
    job = calls[0]
    assert job["stdin_text"] == "Only name these facts"
    assert job["graph_exec_id"] is None and job["ai_group_id"] is None and job["ai_token_id"] is None
    assert job["timeout_seconds"] <= 90
    assert calls[-1] == "released" and set(runtime._runs) == before


def test_short_prompt_bounds():
    prompt = auto_title.build_prompt("x" * 3000, 30)
    assert "At most 30 characters on one line" in prompt
    assert "x" * 2001 not in prompt
    assert len(auto_title.clean_title("x" * 40, 30)) == 30


def test_naming_background_does_not_wait(monkeypatch):
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    def blocked(*args):
        started.set()
        release.wait(2)
        done.set()
    monkeypatch.setattr(auto_title, "generate_flow_names", blocked)
    try:
        assert auto_title.schedule_flow_names("flow", {"user_id": "u"}, "remote", "facts")
        assert started.wait(1) and not done.is_set()
    finally:
        release.set()
        assert done.wait(1)

# Reuse the real SQLite ledger/target fixtures to verify the new dispatch is wired
# through durable names, rather than testing only the policy helper in isolation.
from test_flow_first_names_0071 import naming, legacy_cards, db_path, env, FA, USER, TEXT, TITLE, ALL, titles
from modules.flow_gate.graph_workflow.services import first_names_store as ledger


@pytest.fixture
def configured_names(naming, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda project, provider, prompt, user, **kw:
                        legacy_adapter.complete_prompt(project, provider, prompt, **kw))
    return naming


def test_names_are_applied_once_and_human_edit_wins(configured_names):
    from modules.flow_gate.documents import document_service
    n = configured_names
    n["hook"] = lambda: document_service.update_document(n["chat"], {"title": "Human title"}, "u_kim")
    result = auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"], title="Executor supplied text")
    assert result["targets"] == {"flow": "done", "root": "done", "chat": "skipped"}
    assert titles(n) == (ALL[0], ALL[1], "Human title")
    assert ledger.load(FA)["names"]["flow"]["candidate"] == ALL[0]
    auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"])
    assert n["calls"] == 3


def test_old_fallback_marker_does_not_block_configured_provider(configured_names):
    n = configured_names
    ledger.save(FA, {"fallback_requested": True})
    assert auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"])["status"] == "done"
    assert titles(n) == ALL


def test_retry_after_provider_failure(configured_names):
    n = configured_names
    n["answers"]["chat"] = None
    assert auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"], kinds=("chat",))["status"] == "unchanged"
    n["answers"]["chat"] = TITLE
    assert auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"], kinds=("chat",))["status"] == "done"
    assert titles(n)[2] == TITLE
    assert n["calls"] == 2


# 0094: a chat whose first message names nothing still names the flow (from its start
# document), and the three names are made at the same time instead of one after another.
from test_flow_first_names_0071 import ROOT_TEXT, kind_of


def test_flow_prompt_adds_start_document_to_chat_message(configured_names):
    n = configured_names
    n["message"] = "내용 파악하고 이야기 하자"
    assert auto_title.generate_flow_names(FA, USER, "remote", "", n["chat"])["status"] == "done"
    prompts = dict(zip(n["kinds"], n["prompts"]))
    assert "내용 파악하고 이야기 하자" in prompts["flow"] and "로그인 실패 원인을 찾아 고친다" in prompts["flow"]
    assert "Start document:" in prompts["flow"] and "NO_TITLE" in prompts["flow"]
    assert "로그인 실패 원인을 찾아 고친다" not in prompts["chat"]
    assert titles(n) == ALL


def test_flow_prompt_without_chat_or_same_text_has_one_source():
    assert "Start document:" not in auto_title.build_name_prompt("flow", TEXT)
    assert "Start document:" not in auto_title.build_name_prompt("flow", ROOT_TEXT, ROOT_TEXT)
    both = auto_title.build_name_prompt("flow", TEXT, ROOT_TEXT)
    assert kind_of(both) == "flow" and TEXT in both and "Start document:" in both
    assert auto_title.build_name_prompt("root", ROOT_TEXT, "ignored") == auto_title.build_name_prompt("root", ROOT_TEXT)


def test_three_names_are_requested_at_the_same_time(configured_names):
    n = configured_names
    together = threading.Barrier(3, timeout=5)  # broken (-> failed targets) when calls run in a row
    n["hook"] = together.wait
    result = auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"])
    assert result == {"status": "done", "targets": {"chat": "done", "flow": "done", "root": "done"}}
    assert titles(n) == ALL and n["calls"] == 3


def test_names_in_a_row_when_parallel_is_off(configured_names, monkeypatch):
    n = configured_names
    monkeypatch.setattr(auto_title, "PARALLEL_NAMES", False)
    assert auto_title.generate_flow_names(FA, USER, "remote", TEXT, n["chat"])["status"] == "done"
    assert n["kinds"] == ["chat", "flow", "root"] and titles(n) == ALL


def test_chat_endpoint_queues_flow_and_card_before_waiting_for_chat(configured_names, monkeypatch):
    from flowgate.conversation import first_names_triggers as triggers, activity
    from modules.flow_gate.db import documents
    n = configured_names
    order = []
    monkeypatch.setattr(activity, "readable_chat_document", lambda doc_id, user: documents.get_by_id(doc_id))
    monkeypatch.setattr(legacy_adapter, "has_graph_definition", lambda flow: True)
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda flow: False)
    monkeypatch.setattr(triggers, "_schedule", lambda fn, *args: order.append(("queued", args[-1])) or True)
    generate = auto_title.generate_flow_names
    def waited(*args, **kwargs):
        order.append(("waited", kwargs.get("kinds")))
        return generate(*args, **kwargs)
    monkeypatch.setattr(auto_title, "generate_flow_names", waited)
    assert triggers.chat(n["chat"], USER, {"provider_id": "remote"})["title"] == ALL[2]
    assert order == [("queued", ("flow", "root")), ("waited", ("chat",))]
