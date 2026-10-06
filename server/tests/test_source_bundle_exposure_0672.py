"""0672 T0004: Source Bundle leaves the general path; the TS/TSR preserved set is unchanged.

One judgment -- ``source_bundle_exposure`` over ``(action_scope, doc_ref type, head type)``
(NR0003 §2.1/§7) -- now decides every Bundle advertisement (API tool list, CLI prompt/env,
mention, help/notices) and every Bundle entry point (API tool handlers, CLI routes).

The TS/TSR expectations below were captured from the code *before* this change (the
golden of the T0004 "1단계 불변 조건") and must stay byte-identical. The general-path
expectations are the intended removals. Permission (``kind_for_step``) is pinned too:
this group narrows Bundle exposure only and never touches the live source authority.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.api.v1 import source_bundle_routes as routes  # noqa: E402
from modules.flow_gate.services import api_server_tools as tools  # noqa: E402
from modules.flow_gate.services import help_catalog, mention_service, remote_tool_service  # noqa: E402
from modules.flow_gate.services import source_bundle_exposure as exposure  # noqa: E402
from modules.flow_gate.services import source_common, tool_registry  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_cli  # noqa: E402

ACCESS = ["access_source_bundle"]
RUN = ["run_source_bundle", "run_test"]
READ_SOURCE = [name for name, op in tools.SOURCE_OPS.items() if op in tool_registry.READ_TOOLS]
ALL_SOURCE = list(tools.SOURCE_OPS)

# (scope, doc_ref type, head type) -> exposure. NR0003 §7 regression table.
EXPOSURE_TABLE = [
    # preserved set (golden: unchanged)
    ("new", "R", "TS", exposure.ACCESS),
    ("new", "B", "TS", exposure.ACCESS),
    ("new", "TR", "TS", exposure.RUN),
    ("edit", "TS", "TS", exposure.RUN),
    ("review", "TS", "TS", exposure.ACCESS),
    ("edit", "TSR", "TSR", exposure.RUN),
    ("review", "TSR", "TSR", exposure.ACCESS),
    ("test_run", "TS", "TSR", exposure.NONE),
    # TSR(new) never reaches a worker (refused or moved to test_run at issuance)
    ("new", "R", "TSR", exposure.NONE),
    # removed: general path
    ("new", "R", "N", exposure.NONE),
    ("new", "N", "NR", exposure.NONE),
    ("new", "R", "T", exposure.NONE),
    ("new", "T", "TR", exposure.NONE),
    ("new", "TR", "TR", exposure.NONE),
    ("edit", "N", "N", exposure.NONE),
    ("edit", "NR", "NR", exposure.NONE),
    ("edit", "T", "T", exposure.NONE),
    ("edit", "TR", "TR", exposure.NONE),
    ("review", "D", "D", exposure.NONE),
    ("review", "NR", "NR", exposure.NONE),
    ("review", "T", "T", exposure.NONE),
    ("review", "TR", "TR", exposure.NONE),
    ("chat", "CH", "CH", exposure.NONE),
    ("workflow_decide", "R", "T", exposure.NONE),
    ("resolve_conflict", "R", "T", exposure.NONE),
]


@pytest.fixture
def world(monkeypatch):
    """Pin the doc_ref document type and the workflow head type a lookup resolves to."""
    state = {"doc": None, "head": None}
    monkeypatch.setattr(exposure.db_documents, "get_by_id",
                        lambda doc_id: {"doc_id": doc_id, "type_code": state["doc"]} if doc_id and state["doc"] else None)
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: (state["head"], False))

    def set_types(doc, head):
        state.update(doc=doc, head=head)
    return set_types


def _run(scope, tmp_path="."):
    return {"project_id": "p", "group_id": "g", "doc_ref": "d", "action_scope": scope, "source_root": str(tmp_path)}


# ── the judgment ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope, doc_type, head_type, expected", EXPOSURE_TABLE)
def test_exposure_table(scope, doc_type, head_type, expected):
    assert exposure.exposure(scope, doc_type, head_type) == expected


@pytest.mark.parametrize("scope, doc_type, head_type, expected", EXPOSURE_TABLE)
def test_for_doc_ref_resolves_doc_ref_and_head_like_permission_does(world, scope, doc_type, head_type, expected):
    world(doc_type, head_type)
    assert exposure.for_doc_ref(scope, "d") == expected
    assert exposure.for_token({"action_scope": scope, "doc_ref": "d"}) == expected


def test_new_judges_ts_from_the_head_not_the_spine(world):
    world("R", "TS")  # TS(new): the spine R is the doc_ref, the head is TS
    assert exposure.for_doc_ref("new", "d") == exposure.ACCESS
    world("TS", "T")  # a TS doc_ref under a T head is a T(new) step
    assert exposure.for_doc_ref("new", "d") == exposure.NONE


def test_lookup_failure_exposes_nothing(monkeypatch):
    monkeypatch.setattr(exposure.db_documents, "get_by_id",
                        lambda _id: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    assert exposure.for_doc_ref("edit", "d") == exposure.NONE
    assert exposure.for_doc_ref("edit", None) == exposure.NONE


# ── advertisement: API tool list (golden for the preserved set) ──────────────

@pytest.mark.parametrize("scope, doc_type, head_type, expected", [
    ("new", "R", "TS", list(tools.BASE_NAMES) + ACCESS + READ_SOURCE),
    ("new", "B", "TS", list(tools.BASE_NAMES) + ACCESS + READ_SOURCE),
    ("new", "TR", "TS", list(tools.BASE_NAMES) + ACCESS + ALL_SOURCE + RUN),
    ("edit", "TS", "TS", list(tools.BASE_NAMES) + ACCESS + ALL_SOURCE + RUN),
    ("review", "TS", "TS", list(tools.BASE_NAMES) + ACCESS + READ_SOURCE),
    ("edit", "TSR", "TSR", list(tools.BASE_NAMES) + ACCESS + ALL_SOURCE + RUN),
    ("review", "TSR", "TSR", list(tools.BASE_NAMES) + ACCESS + READ_SOURCE),
    ("test_run", "TS", "TSR", list(tools.BASE_NAMES)),
])
def test_ts_tsr_tool_lists_are_unchanged(world, scope, doc_type, head_type, expected):
    world(doc_type, head_type)
    assert [d["name"] for d in tools.definitions_for_run(_run(scope))] == expected


@pytest.mark.parametrize("scope, doc_type, head_type, expected", [
    ("new", "R", "N", list(tools.BASE_NAMES) + READ_SOURCE),
    ("new", "N", "NR", list(tools.BASE_NAMES) + READ_SOURCE),
    ("new", "R", "T", list(tools.BASE_NAMES) + READ_SOURCE),
    ("new", "T", "TR", list(tools.BASE_NAMES) + READ_SOURCE),
    ("new", "TR", "TR", list(tools.BASE_NAMES) + ALL_SOURCE),
    ("new", "R", "TSR", list(tools.BASE_NAMES) + READ_SOURCE),
    ("edit", "N", "N", list(tools.BASE_NAMES) + READ_SOURCE),
    ("edit", "T", "T", list(tools.BASE_NAMES) + READ_SOURCE),
    ("edit", "TR", "TR", list(tools.BASE_NAMES) + ALL_SOURCE + list(tools.SELF_CHECK_NAMES)),
    ("review", "D", "D", list(tools.BASE_NAMES) + READ_SOURCE),
    ("review", "TR", "TR", list(tools.BASE_NAMES) + READ_SOURCE + ["read_self_check"]),
])
def test_general_steps_lose_bundle_tools_and_keep_live_source(world, scope, doc_type, head_type, expected):
    world(doc_type, head_type)
    assert [d["name"] for d in tools.definitions_for_run(_run(scope))] == expected


@pytest.mark.parametrize("scope", ["new", "edit", "review", "workflow_decide", "chat", "resolve_conflict", "resolve_base_dirty", "test_run"])
@pytest.mark.parametrize("step_type", ["R", "N", "NR", "T", "TR", "TS", "TSR", "D", "CH", None])
def test_permission_judgment_is_untouched(scope, step_type):
    """kind_for_step answers exactly as before 0672 for every (scope, type)."""
    if scope in {"review", "workflow_decide", "chat", "resolve_conflict"}:
        expected = ("read", None)
    elif scope == "resolve_base_dirty":
        expected = ("read_write", None)
    elif scope not in {"new", "edit"}:
        expected = ("none", "token_scope_none")
    elif step_type in {"TR", "TSR", "TS"}:
        expected = ("read_write", None)
    else:
        expected = ("read", None)
    assert tool_registry.kind_for_step(scope, step_type) == expected


# ── entry points: API tool handlers ──────────────────────────────────────────

@pytest.mark.parametrize("scope, doc_type, head_type", [
    ("new", "R", "N"), ("edit", "NR", "NR"), ("new", "R", "T"), ("review", "D", "D"),
    ("review", "TR", "TR"), ("new", "TR", "TR"), ("new", "R", "TSR"),
])
def test_bundle_handlers_refuse_general_runs(world, monkeypatch, scope, doc_type, head_type):
    world(doc_type, head_type)
    monkeypatch.setattr(tools.source_bundle_access_service, "access", lambda *_a: pytest.fail("Bundle core reached"))
    monkeypatch.setattr(tools.source_bundle_access_service, "execute", lambda *_a: pytest.fail("Bundle core reached"))
    run = _run(scope)
    for call in (lambda: tools.access_source_bundle(run, {"operation": "status"}),
                 lambda: tools.run_source_bundle(run, {"task_kind": "test", "command": "x"}, 9),
                 lambda: tools.run_test(run, {"command": "pytest -q"}, 9)):
        with pytest.raises(tools.ToolError) as caught:
            call()
        assert (caught.value.status, caught.value.reason) == (403, "source_bundle_not_available")


def test_access_level_steps_cannot_run(world, monkeypatch):
    world("TS", "TS")
    monkeypatch.setattr(tools.source_bundle_access_service, "access", lambda run, data: (200, {"ok": True}))
    monkeypatch.setattr(tools.source_bundle_access_service, "execute", lambda *_a: pytest.fail("Bundle run reached"))
    run = _run("review")
    assert tools.access_source_bundle(run, {"operation": "status"}) == (200, {"ok": True})
    with pytest.raises(tools.ToolError) as caught:
        tools.run_source_bundle(run, {"task_kind": "test", "command": "x"}, 9)
    assert caught.value.reason == "source_bundle_not_available"


def test_ts_edit_still_reaches_the_bundle_core(world, monkeypatch):
    world("TS", "TS")
    seen = []
    monkeypatch.setattr(tools.source_bundle_access_service, "access", lambda run, data: seen.append("access") or (200, {"ok": True}))
    monkeypatch.setattr(tools.source_bundle_access_service, "execute", lambda run, data, remain: seen.append("execute") or (200, {"ok": True}))
    run = _run("edit")
    assert tools.access_source_bundle(run, {"operation": "status"})[0] == 200
    assert tools.run_source_bundle(run, {"task_kind": "test", "command": "x"}, 9)[0] == 200
    assert seen == ["access", "execute"]


def test_tr_edit_keeps_the_self_check_refusal_first(world, monkeypatch):
    world("TR", "TR")
    monkeypatch.setattr(tools.tr_self_check_service, "is_canonical_run", lambda _run: True)
    for call in (lambda: tools.access_source_bundle(_run("edit"), {"operation": "status"}),
                 lambda: tools.run_test(_run("edit"), {"command": "pytest -q"}, 9)):
        with pytest.raises(tools.ToolError) as caught:
            call()
        assert (caught.value.status, caught.value.reason) == (409, "self_check_required")


def test_source_call_guard_no_longer_reaches_bundle_or_snapshot(monkeypatch):
    monkeypatch.setattr(tools.source_bundle_access_service, "guard_promotion", lambda *_a: pytest.fail("Bundle guard"))
    monkeypatch.setattr(tools.snapshot_access_service, "guard_promotion", lambda *_a: pytest.fail("Snapshot guard"))
    monkeypatch.setattr(tools.remote_tool_service, "handle", lambda op, token, body: (200, {"ok": True, "op": op}))
    run = _run("edit")
    assert tools.source_call(run, "live", "write_source_file", {"path": "app.py", "content": "x"}) == (200, {"ok": True, "op": "write"})
    status, payload = tools.source_call(run, "live", "write_source_file",
                                        {"path": "source-bundles/sb_" + "0" * 32 + "/source/app.py", "content": "x"})
    assert (status, payload["error"]["code"]) == (403, "bundle_promotion_blocked")
    status, payload = tools.source_call(run, "live", "patch_source_file",
                                        {"path": "x/source-snapshots/snap_a1/source/app.py", "old_string": "a", "new_string": "b"})
    assert (status, payload["error"]["code"]) == (403, "snapshot_promotion_blocked")


# ── entry points: CLI routes ─────────────────────────────────────────────────

def _cli(monkeypatch, world, scope, doc_type, head_type):
    world(doc_type, head_type)
    token = {"action_scope": scope, "doc_ref": "d"}
    run = {"project_id": "p", "group_id": "g", "run_id": "r", "doc_ref": "d", "action_scope": scope}
    monkeypatch.setattr(routes, "_cli_context", lambda _request: ("raw", token, run))
    monkeypatch.setattr(routes.core, "access", lambda _run, data: (200, {"ok": True, "op": data["operation"]}))
    monkeypatch.setattr(routes.core, "execute", lambda _run, data, remain: (200, {"ok": True, "op": "execute"}))


def _route_calls():
    return {
        "ensure": lambda: routes.ensure_bundle(object()),
        "status": lambda: routes.bundle_status("sb_x", object()),
        "access": lambda: routes.access_bundle("sb_x", {"operation": "read", "path": "x"}, object()),
        "run": lambda: routes.run_bundle("sb_x", {"task_kind": "test", "command": "x"}, object()),
    }


@pytest.mark.parametrize("scope, doc_type, head_type", [
    ("edit", "TR", "TR"), ("review", "TR", "TR"), ("new", "R", "N"), ("edit", "NR", "NR"),
    ("new", "R", "T"), ("review", "D", "D"), ("chat", "CH", "CH"), ("workflow_decide", "R", "T"),
    ("resolve_conflict", "R", "T"), ("test_run", "TS", "TSR"), ("new", "TR", "TR"),
])
def test_cli_routes_refuse_tokens_outside_the_preserved_set(world, monkeypatch, scope, doc_type, head_type):
    _cli(monkeypatch, world, scope, doc_type, head_type)
    for name, call in _route_calls().items():
        with pytest.raises(HTTPException) as caught:
            call()
        assert caught.value.status_code == 403, name
        assert caught.value.detail["error"]["code"] == "source_bundle_not_available"


PRESERVED_CLI = [
    ("edit", "TS", "TS"), ("new", "TR", "TS"), ("edit", "TSR", "TSR"),
    ("review", "TS", "TS"), ("new", "R", "TS"), ("new", "B", "TS"), ("review", "TSR", "TSR"),
]


@pytest.mark.parametrize("scope, doc_type, head_type", PRESERVED_CLI)
def test_cli_routes_keep_the_preserved_set(world, monkeypatch, scope, doc_type, head_type):
    _cli(monkeypatch, world, scope, doc_type, head_type)
    for name, call in _route_calls().items():
        assert call()["ok"] is True, name


@pytest.mark.parametrize("scope, doc_type, head_type", PRESERVED_CLI)
def test_cli_run_matches_the_pre_change_route_for_the_preserved_set(world, monkeypatch, scope, doc_type, head_type):
    """Before 0672 the CLI run route went straight from ``_cli_context`` to ``core.execute``
    for every valid token, whatever tool level was advertised. Access-level TS steps
    (TS review, TS new with doc_ref=R/B, TSR review) must still run through the CLI."""
    _cli(monkeypatch, world, scope, doc_type, head_type)
    seen = []
    monkeypatch.setattr(routes.core, "execute",
                        lambda run, data, remain: seen.append((run, data, remain)) or (200, {"ok": True, "op": "execute"}))
    body = {"task_kind": "test", "command": "x"}
    assert routes.run_bundle("sb_x", body, object()) == {"ok": True, "op": "execute"}
    assert seen == [({"project_id": "p", "group_id": "g", "run_id": "r", "doc_ref": "d", "action_scope": scope},
                     dict(body, bundle_id="sb_x"), 300.0)]
    assert body == {"task_kind": "test", "command": "x"}


class _Request:
    def __init__(self, token="raw"):
        self.headers = {"Authorization": f"Bearer {token}"}


def test_cli_context_is_bundle_owned_and_checks_every_axis(monkeypatch):
    from modules.flow_gate.services import ai_invoke_service
    assert not hasattr(routes, "snapshot_routes")
    token ={"ai_run_id": "r", "project": "p", "group_id": "g", "token_id": "t", "action_scope": "edit", "doc_ref": "d"}
    run = {"run_id": "r", "project_id": "p", "group_id": "g", "current_token_id": "t"}
    monkeypatch.setattr(routes.token_service, "verify", lambda raw: dict(token) if raw == "raw" else (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda _rid: dict(run))
    assert routes._cli_context(_Request())[1:] == (token, run)
    with pytest.raises(HTTPException) as caught:
        routes._cli_context(_Request("bad"))
    assert caught.value.status_code == 403
    for key, value in (("group_id", "other"), ("current_token_id", "stale")):
        monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda _rid, k=key, v=value: dict(run, **{k: v}))
        with pytest.raises(HTTPException) as caught:
            routes._cli_context(_Request())
        assert caught.value.detail["error"]["code"] == "bundle_forbidden"


# ── advertisement: CLI prompt / env ──────────────────────────────────────────

LEGACY_BOUNDARY = (
    "\n\n## Source Bundle CLI boundary\n"
    "Use FLOWGATE_BUNDLE_API with Authorization: Bearer $FLOWGATE_TOKEN for "
    "ensure, status, access (read/search/glob/stat), and allowed run calls. "
    "No human approval is required. Never consume an internal Bundle or Scratch path directly.\n"
)


class _Stop(BaseException):
    """Escapes _cli_execute right after the prompt is handed to the child."""


def _launch(monkeypatch, tmp_path, run_fields):
    captured = {}

    class Owner:
        active = False

        def __init__(self, *_a):
            pass

        def creationflags(self, flags):
            return flags

        def attach(self, _proc):
            pass

        def close(self):
            pass

    class Proc:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def communicate(_proc, _owner, input=None, timeout=None):
        captured["prompt"] = input.decode("utf-8")
        raise _Stop()

    monkeypatch.setattr(provider_cli, "_canonicalize_cli_prompt", lambda prompt, base: (prompt, "http://h/flowgate/api/v1"))
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda *_a: (
        {"effective_command": "agent", "agent_cwd": str(tmp_path), "spawn_cwd": str(tmp_path)}, "valid"))
    monkeypatch.setattr(provider_cli, "_audit_cli_launch", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_start_progress_watchdog", lambda *_a: (None, None))
    monkeypatch.setattr(provider_cli, "_stop_progress_watchdog", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_absolute_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(provider_cli.process_runner, "WindowsProcessOwner", Owner)
    monkeypatch.setattr(provider_cli.process_runner, "popen_kwargs", lambda _cwd, env: captured.update(env=dict(env)) or {})
    monkeypatch.setattr(provider_cli.process_runner, "communicate_with_cleanup", communicate)
    monkeypatch.setattr(provider_cli.process_runner, "kill_process_tree", lambda *_a: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: Proc())
    run = {"run_id": "r", "raw_token": "raw", "scratch_dir": str(tmp_path), "token_scratch_dir": str(tmp_path),
           "api_base_url": "http://h/flowgate/api/v1", "cancel_event": type("E", (), {"is_set": lambda self: False})(),
           "doc_ref": "d", **run_fields}
    with pytest.raises(_Stop):
        provider_cli._cli_execute({"cli_command": "claude", "kind": "claude"}, "PROMPT", run)
    return captured


@pytest.mark.parametrize("scope, doc_type, head_type, expected", [
    ("edit", "TS", "TS", True), ("review", "TS", "TS", True), ("new", "R", "TS", True), ("edit", "TSR", "TSR", True),
    ("edit", "TR", "TR", False), ("review", "TR", "TR", False), ("new", "R", "N", False), ("new", "R", "T", False),
    ("review", "D", "D", False), ("chat", "CH", "CH", False), ("test_run", "TS", "TSR", False),
])
def test_cli_prompt_and_env_follow_the_exposure(world, monkeypatch, tmp_path, scope, doc_type, head_type, expected):
    world(doc_type, head_type)
    captured = _launch(monkeypatch, tmp_path, {"action_scope": scope})
    if expected:
        assert captured["prompt"] == "PROMPT" + LEGACY_BOUNDARY
        assert captured["env"]["FLOWGATE_BUNDLE_API"] == "http://h/flowgate/api/v1/source-bundles/cli"
    else:
        assert captured["prompt"] == "PROMPT"
        assert "FLOWGATE_BUNDLE_API" not in captured["env"]
    assert captured["env"]["FLOWGATE_TOKEN"] == "raw"


# ── advertisement: mention ───────────────────────────────────────────────────

def _mention(monkeypatch, scope, doc_type, head_type):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)
    return mention_service.build_mention(
        project="p", module="default", group="0672", parent_type=doc_type, parent_doc_number="0001",
        parent_title="t", parent_doc_id="p.default.0672.0001-" + doc_type, head_type=head_type,
        head_status="pending", scratch_dir="S", raw_token="RAW", api_base_url="http://h/flowgate/api/v1",
        action_scope=scope,
    )


@pytest.mark.parametrize("scope, doc_type, head_type, expected", [
    ("new", "R", "TS", True), ("new", "TR", "TS", True), ("edit", "TS", "TS", True), ("edit", "TSR", "TSR", True),
    ("new", "R", "N", False), ("new", "N", "NR", False), ("new", "R", "T", False), ("edit", "T", "T", False),
    ("edit", "NR", "NR", False), ("new", "R", "TSR", False),
])
def test_mention_prints_the_bundle_policy_only_for_the_preserved_set(monkeypatch, scope, doc_type, head_type, expected):
    text = _mention(monkeypatch, scope, doc_type, head_type)
    assert ("## Source Bundle policy" in text) is expected
    assert ("/help/items/source_bundles" in text) is expected


@pytest.mark.parametrize("scope, doc_type, head_type", [("new", "T", "TR"), ("new", "TR", "TR"), ("edit", "TR", "TR")])
def test_tr_mention_points_at_self_check_not_bundle(monkeypatch, scope, doc_type, head_type):
    text = _mention(monkeypatch, scope, doc_type, head_type)
    assert "## TR test responsibility and Self-check" in text
    assert "TR test responsibility and Source Bundle" not in text
    assert "automatically prepared Source Bundle" not in text
    assert "## Source Bundle policy" not in text
    assert "run_self_check" in text


# ── advertisement: help / notices ────────────────────────────────────────────

def test_tr_self_check_help_drops_the_bundle_ensure_line():
    ctx = {"principal_kind": "worker", "action_scope": "edit", "doc_type": "TR", "locale": "en",
           "base_url": "/flowgate/api/v1", "source_mode": "remote", "tool_kind": "read_write", "registry": None}
    text = " ".join(help_catalog.build_item("tr_self_check", ctx)["content"]["no_fallback"])
    assert "Bundle ensure" not in text and "Bundle error" not in text
    assert "self_check_required" in text


def test_resolve_context_carries_the_exposure(world, monkeypatch):
    monkeypatch.setattr(help_catalog.tool_registry, "resolve_registry",
                        lambda *_a: {"kind": "read", "source_mode": "remote", "reason": None})
    world("TS", "TS")
    rec = {"project": "p", "group_id": "g", "doc_ref": "d", "action_scope": "review"}
    assert help_catalog.resolve_context(rec, "en", "/b")["bundle_exposure"] == exposure.ACCESS
    world("D", "D")
    assert help_catalog.resolve_context(rec, "en", "/b")["bundle_exposure"] == exposure.NONE


# ── stage 3: isolation ───────────────────────────────────────────────────────

def test_bundle_no_longer_imports_the_snapshot_module():
    from modules.flow_gate.services import source_bundle_access_service, source_bundle_materializer
    from modules.flow_gate.services import snapshot_materialization_service as legacy
    import inspect
    for module in (source_bundle_access_service, source_bundle_materializer, routes):
        assert "snapshot_materialization_service" not in inspect.getsource(module)
        assert "snapshot_routes" not in inspect.getsource(module)
    # The legacy module re-exports the moved utilities unchanged.
    assert legacy.locator_roots is source_common.locator_roots
    assert legacy.redact_error_text is source_common.redact_error_text
    assert legacy.EXCLUDED_DIR_NAMES is source_common.EXCLUDED_DIR_NAMES
    assert "source-snapshots" in source_common.EXCLUDED_DIR_NAMES


def test_tr_submission_no_longer_carries_bundle_provenance():
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import source_bundle_access_service
    import inspect
    assert "source_bundle_access_service" not in inspect.getsource(inbox_routes)
    for name in ("inject_tr_provenance", "attach_tr", "provenance_for_run"):
        assert not hasattr(source_bundle_access_service, name)
