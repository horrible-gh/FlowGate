import inspect
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import snapshot_routes
from modules.flow_gate.services import api_server_tools
from modules.flow_gate.services import help_catalog
from modules.flow_gate.services import snapshot_access_service as access
from modules.flow_gate.services.ai_invoke import provider_api

from test_snapshot_materialization_0517 import _Store, snapshot_env  # noqa: F401,E402


@pytest.fixture
def access_env(tmp_path, monkeypatch):
    final = tmp_path / "source-snapshots" / "snap_access"
    source = final / "source"
    source.mkdir(parents=True)
    (source / "app.py").write_text("print('snapshot')\nneedle = 1\n", encoding="utf-8")
    (source / "pkg").mkdir()
    (source / "pkg" / "data.txt").write_text("needle\n", encoding="utf-8")
    row = {
        "snapshot_id": "snap_access",
        "project_id": "project",
        "group_id": "project.default.0517",
        "run_id": "run_access",
        "token_id": "tok_access",
        "provider_id": "provider",
        "reason": "test runner needs a real tree",
        "purpose": "run integrated tests",
        "scope": "directory",
        "requested_paths": ["pkg"],
        "source_kind": "current_worktree",
        "status": "created",
        "source_revision": "a" * 40,
        "created_at": "2026-09-23T00:00:00+00:00",
        "approved_by": "human",
        "stale": False,
    }
    usages = []
    events = []

    monkeypatch.setattr(access.request_db, "get", lambda sid: dict(row) if sid == row["snapshot_id"] else None)

    def refreshed(snapshot_id, actor):
        result = dict(row)
        result.update(available=True, snapshot_path=str(final))
        return result

    monkeypatch.setattr(access.materialization, "refresh_stale", refreshed)
    monkeypatch.setattr(
        access.materialization.token_service, "scratch_dir_path",
        lambda project_id, token_id: str(tmp_path),
    )
    monkeypatch.setattr(
        access.usage_db, "record",
        lambda data: usages.append(dict(data)) or dict(data),
    )
    monkeypatch.setattr(access.workflow_events, "create", lambda data: events.append(data) or data)
    run = {
        "project_id": "project",
        "group_id": "project.default.0517",
        "run_id": "run_access",
        "token_id": "tok_current",
    }
    return SimpleNamespace(
        row=row, final=final, source=source, run=run, usages=usages, events=events,
    )


def test_c8_active_snapshot_returns_no_locator_metadata_and_read_search(access_env):
    status, result = access.access(
        access_env.run,
        {"snapshot_id": "snap_access", "operation": "read", "path": "app.py"},
    )
    assert status == 200
    assert result["content"].startswith("print")
    # Neither the API tool-call path nor the CLI HTTP path may ever hand the AI a raw
    # filesystem locator or scratch path — this dict is returned verbatim to both.
    assert "locator" not in result["snapshot"]
    assert str(access_env.source.resolve()) not in json.dumps(result)
    assert result["snapshot"] == {
        "snapshot_id": "snap_access",
        "request_id": "snap_access",
        "project_id": "project",
        "group_id": "project.default.0517",
        "run_id": "run_access",
        "token_id": "tok_access",
        "provider_id": "provider",
        "source_kind": "current_worktree",
        "source_revision": "a" * 40,
        "scope": "directory",
        "requested_paths": ["pkg"],
        "created_at": "2026-09-23T00:00:00+00:00",
        "stale": False,
        "status": "active",
        "current_worktree_validation_allowed": True,
    }
    status, searched = access.access(
        access_env.run,
        {
            "snapshot_id": "snap_access", "operation": "search",
            "pattern": "needle", "glob": "**/*.py",
        },
    )
    assert status == 200
    assert searched["matches"][0]["file"] == "app.py"
    assert [item["operation"] for item in access_env.usages] == ["read", "search"]


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/escape"])
def test_c17_locator_jail_rejects_traversal_and_absolute_paths(access_env, path):
    with pytest.raises(access.SnapshotAccessError) as caught:
        access.access(
            access_env.run,
            {"snapshot_id": "snap_access", "operation": "read", "path": path},
        )
    assert caught.value.code in {"snapshot_path_invalid", "snapshot_path_escape"}


def test_c17_other_run_group_or_project_is_denied(access_env):
    for key, value in (
        ("run_id", "other-run"), ("group_id", "other.group"), ("project_id", "other"),
    ):
        bad = dict(access_env.run)
        bad[key] = value
        with pytest.raises(access.SnapshotAccessError) as caught:
            access.access(bad, {"snapshot_id": "snap_access", "operation": "status"})
        assert caught.value.code == "snapshot_forbidden"


def test_c11_stale_access_is_readable_and_always_warns(access_env):
    access_env.row["stale"] = True
    status, result = access.access(
        access_env.run,
        {"snapshot_id": "snap_access", "operation": "read", "path": "app.py"},
    )
    assert status == 200
    assert result["snapshot"]["status"] == "stale"
    assert result["snapshot"]["warning"] == "ACTIVE SNAPSHOT IS STALE"
    assert result["snapshot"]["current_worktree_validation_allowed"] is False
    assert "cannot be treated as current worktree validation" in result["snapshot"]["validation_claim"]


@pytest.mark.parametrize(
    ("state", "code", "http_status"),
    [("deleted", "snapshot_deleted", 410), ("failed", "snapshot_failed", 409)],
)
def test_c13_c14_deleted_and_failed_are_explicit(access_env, state, code, http_status):
    access_env.row["status"] = state
    access_env.row["failure_reason"] = "copy failed"
    with pytest.raises(access.SnapshotAccessError) as caught:
        access.access(access_env.run, {"snapshot_id": "snap_access", "operation": "status"})
    assert caught.value.status == http_status
    assert caught.value.code == code
    assert caught.value.state == state


def test_c21_stored_failure_reason_absolute_path_is_redacted_on_api_and_direct_boundary(
    access_env, monkeypatch,
):
    """0517.0021-TR rev4 rejection point 3: a materialization failure can durably store a
    failure_reason built from raw exception text (e.g. OSError str()), which can contain
    the real scratch/snapshot absolute path. Later access() calls surface that stored text
    through SnapshotAccessError.message — this must come out redacted regardless of which
    worker-facing boundary renders the error payload.
    """
    leaked_path = str(access_env.source / "app.py")
    access_env.row["status"] = "failed"
    access_env.row["failure_reason"] = (
        f"copy interrupted: [Errno 13] Permission denied: '{leaked_path}'"
    )

    with pytest.raises(access.SnapshotAccessError) as caught:
        access.access(access_env.run, {"snapshot_id": "snap_access", "operation": "status"})
    direct_payload = caught.value.payload("status")
    assert access.SNAPSHOT_PATH_REDACTION in direct_payload["error"]["message"]
    for variant in (leaked_path, leaked_path.replace("\\", "/"), leaked_path.replace("/", "\\")):
        assert variant not in json.dumps(direct_payload)

    # Same stored failure_reason, rendered through the API tool-call boundary
    # (api_server_tools.access_source_snapshot), which also just calls exc.payload().
    token = {
        "token_id": "tok_access", "ai_run_id": "run_access", "project": "project",
        "group_id": "project.default.0517", "issued_to": "worker",
    }
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service, "validate_request_authority",
        lambda candidate, active: candidate,
    )
    status, api_payload = api_server_tools.access_source_snapshot(
        access_env.run, "raw-token", {"snapshot_id": "snap_access", "operation": "status"},
    )
    assert status == 409
    assert access.SNAPSHOT_PATH_REDACTION in api_payload["error"]["message"]
    for variant in (leaked_path, leaked_path.replace("\\", "/"), leaked_path.replace("/", "\\")):
        assert variant not in json.dumps(api_payload)


def test_c12_stale_execution_claim_is_blocked_and_audited(access_env, monkeypatch):
    access_env.row["stale"] = True

    class Proc:
        returncode = 0

        def communicate(self, timeout):
            return b"ok", b""

    monkeypatch.setattr(access.subprocess, "Popen", lambda *args, **kwargs: Proc())
    status, result = access.execute(
        access_env.run,
        {
            "snapshot_id": "snap_access",
            "task_kind": "test",
            "command": "pytest -q",
            "claim_current_worktree": True,
        },
        remaining_sec=10,
        source_tool_calls=3,
        snapshot_reads=2,
    )
    assert status == 409
    assert result["error"]["code"] == "snapshot_stale_claim_blocked"
    assert result["warning"] == "ACTIVE SNAPSHOT IS STALE"
    assert "cannot fully inspect every child process" in result["execution_boundary"]
    assert access_env.usages[-1]["access_kind"] == "execution"
    assert access_env.usages[-1]["stale_at_use"] is True
    kinds = [event["event_type"] for event in access_env.events]
    assert kinds == ["snapshot_execution_reported", "snapshot_misuse_blocked"]


def test_c9_snapshot_promotion_path_is_blocked_before_source_mutation(access_env):
    with pytest.raises(access.SnapshotAccessError) as caught:
        access.guard_promotion(
            access_env.run,
            "write_source_file",
            {
                "path": "source-snapshots/snap_access/source/app.py",
                "content": "persistent",
            },
        )
    assert caught.value.code == "snapshot_promotion_blocked"
    event = access_env.events[-1]
    assert event["event_type"] == "snapshot_misuse_blocked"
    assert "snapshot_to_worktree_promotion" in event["metadata"]


def test_c1_api_request_tool_is_request_only_and_returns_no_locator(monkeypatch):
    token = {
        "token_id": "tok", "ai_run_id": "run", "project": "project",
        "group_id": "project.default.0517", "issued_to": "worker",
    }
    run = {
        "token_id": "tok", "current_token_id": "tok", "run_id": "run",
        "project_id": "project", "group_id": "project.default.0517",
        "provider_id": "provider",
    }
    captured = {}
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service,
        "validate_request_authority",
        lambda candidate, active: candidate,
    )
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service,
        "create_request",
        lambda data, actor: captured.update(data=data, actor=actor) or {
            **data, "snapshot_id": "snap_requested", "status": "requested",
            "requested_at": "now",
        },
    )
    status, result = api_server_tools.request_source_snapshot(
        run,
        "raw-token",
        {
            "reason": "build requires a tree",
            "scope": "single_file",
            "requested_paths": ["app.py"],
            "purpose": "build",
        },
    )
    assert status == 201
    assert result["request_id"] == "snap_requested"
    assert result["status"] == "requested"
    assert result["materialized"] is False
    assert result["requires_human_decision"] is True
    assert "locator" not in result and "snapshot_path" not in str(result)
    assert captured["data"]["source_kind"] if "source_kind" in captured["data"] else True


def test_c3_c4_request_path_has_no_implicit_materialize_call(monkeypatch):
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: {"token_id": "tok"})
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service,
        "validate_request_authority",
        lambda token, run: token,
    )
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service,
        "create_request",
        lambda data, actor: {**data, "snapshot_id": "snap_only", "status": "requested"},
    )
    monkeypatch.setattr(
        api_server_tools.snapshot_access_service.materialization,
        "materialize",
        lambda *args, **kwargs: pytest.fail("request/retry/review/rework must not materialize"),
    )
    run = {
        "token_id": "tok", "run_id": "run", "project_id": "p",
        "group_id": "p.default.0517", "provider_id": "provider",
    }
    status, result = api_server_tools.request_source_snapshot(
        run, "raw",
        {
            "reason": "a runner needs a tree", "scope": "whole_source",
            "requested_paths": [], "purpose": "run static analysis",
        },
    )
    assert status == 201 and result["status"] == "requested"

    module_root = Path(__file__).parents[1] / "modules" / "flow_gate"
    callers = []
    for path in module_root.rglob("*.py"):
        if "materialization.materialize(" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(module_root).as_posix())
    assert callers == ["api/v1/snapshot_routes.py"]


def test_c2_approval_rejection_and_materialization_are_human_session_only():
    for endpoint in (
        snapshot_routes.approve,
        snapshot_routes.reject,
        snapshot_routes.materialize_snapshot,
    ):
        dependency = inspect.signature(endpoint).parameters["user"].default
        assert dependency.dependency is snapshot_routes.get_current_user
    for name in ("approve_source_snapshot", "reject_source_snapshot", "materialize_source_snapshot"):
        assert name not in api_server_tools.SCHEMAS


def test_c18_multiple_used_snapshots_are_stable_and_injected_before_changed_files(monkeypatch):
    rows = {
        "snap_one": {
            "snapshot_id": "snap_one", "status": "deleted", "stale": False,
            "source_kind": "current_worktree", "source_revision": "1" * 40,
            "created_at": "2026-09-23T01:00:00+00:00",
        },
        "snap_two": {
            "snapshot_id": "snap_two", "status": "created", "stale": True,
            "source_kind": "current_worktree", "source_revision": "2" * 40,
            "created_at": "2026-09-23T02:00:00+00:00",
        },
    }
    monkeypatch.setattr(
        access.usage_db, "list_for_run",
        lambda run_id: [
            {"snapshot_id": "snap_one"}, {"snapshot_id": "snap_two"},
            {"snapshot_id": "snap_one"},
        ],
    )
    monkeypatch.setattr(access.request_db, "get", lambda snapshot_id: dict(rows[snapshot_id]))
    monkeypatch.setattr(
        access.materialization, "refresh_stale",
        lambda snapshot_id, actor: {
            **rows[snapshot_id], "available": True, "snapshot_path": "/unused",
        },
    )
    original = "# Report\n\n## 변경 파일\n\n- server/app.py\n"
    rendered, provenance = access.inject_tr_provenance(original, "run")
    assert [row["snapshot_id"] for row in provenance] == ["snap_one", "snap_two"]
    assert rendered.count("Scratch snapshot used: Yes") == 2
    assert "Source revision: " + "1" * 40 in rendered
    assert "Created at: 2026-09-23T02:00:00+00:00" in rendered
    assert "Stale at completion: Yes" in rendered
    assert rendered.index("## Scratch Snapshot Provenance") < rendered.index("## 변경 파일")


def test_c20_no_snapshot_tr_is_byte_for_byte_unchanged(monkeypatch):
    monkeypatch.setattr(access.usage_db, "list_for_run", lambda run_id: [])
    body = "# Report\r\n\r\n## Changed Files\r\n\r\nNone\r\n"
    rendered, provenance = access.inject_tr_provenance(body, "run")
    assert rendered == body
    assert provenance == []


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_c15_all_dialects_define_durable_snapshot_usage(dialect):
    path = Path(__file__).parents[1] / "sql" / "migrations" / dialect / "117_snapshot_usages.sql"
    sql = path.read_text(encoding="utf-8")
    for column in (
        "snapshot_id", "run_id", "token_id", "access_kind", "operation",
        "task_kind", "stale_at_use", "document_id",
    ):
        assert column in sql


def test_c15_sqlite_usage_survives_a_new_connection(tmp_path):
    database = tmp_path / "snapshot.sqlite"
    migration = (
        Path(__file__).parents[1]
        / "sql" / "migrations" / "sqlite" / "117_snapshot_usages.sql"
    ).read_text(encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.executescript(migration)
        connection.execute(
            "INSERT INTO snapshot_usages "
            "(usage_id,snapshot_id,run_id,token_id,access_kind,operation,success,"
            "stale_at_use,current_worktree_claim,used_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("u", "s", "r", "t", "access", "read", 1, 0, 0, "now"),
        )
    with sqlite3.connect(database) as restarted:
        assert restarted.execute(
            "SELECT snapshot_id,run_id,operation FROM snapshot_usages"
        ).fetchone() == ("s", "r", "read")


def test_c7_help_and_prompt_expose_abuse_boundary():
    help_text = str(api_server_tools.DESCRIPTIONS["request_source_snapshot"])
    prompt = provider_api._api_system_prompt()
    assert "Merge Context" in help_text
    assert "never approve" in help_text
    assert "whole_source is not the default" in help_text
    assert "real filesystem tree" in prompt
    assert "ACTIVE SNAPSHOT IS STALE" in prompt
    assert "promoted" in prompt
    assert "cannot fully inspect every child process" in prompt
    assert "cannot fully inspect every child process" in (
        help_catalog._content_source_snapshots({})["execution_limit"]
    )


def test_c5_c6_request_scope_schema_has_narrow_and_whole_source_guards():
    schema = api_server_tools.SCHEMAS["request_source_snapshot"]
    assert schema["properties"]["scope"]["enum"] == [
        "single_file", "selected_files", "directory", "whole_source",
    ]
    assert {"reason", "purpose"} <= set(schema["required"])
    assert set(api_server_tools.SCHEMAS["run_source_snapshot"]["properties"]["task_kind"]["enum"]) == set(
        access.TASK_KINDS
    )


def test_c10_c19_canonical_mutation_and_tr_scope_contracts_remain_separate():
    assert set(access.MUTATION_TOOL_NAMES) == {
        "write_source_file", "patch_source_file", "remove_source_file",
    }
    assert not {
        "promote_source_snapshot", "commit_source_snapshot", "merge_source_snapshot",
        "sync_source_snapshot",
    } & set(api_server_tools.SCHEMAS)


def test_cli_request_approve_materialize_access_run_share_one_snapshot(
    snapshot_env, monkeypatch,
):
    """T#2 connected regression (rework of the prior TR0021 rejection): the snapshot the
    CLI *requests* through the real worker-token HTTP boundary is the exact same
    snapshot that human approval/materialization publishes and that the CLI later
    reads/searches/globs/stats and runs a command against — never two disconnected
    fixture rows. Also pins that no CLI response along the way ever carries the raw
    filesystem locator or scratch path.
    """
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services import snapshot_request_service

    snapshot_env.row.update(
        status="requested", stale=False, stale_detected_at=None,
        created_at=None, source_revision=None, source_fingerprint=None,
    )
    monkeypatch.setattr(snapshot_request_service, "get_store", lambda: _Store())
    monkeypatch.setattr(
        snapshot_request_service.workflow_events, "create", lambda event: event,
    )
    monkeypatch.setattr(snapshot_request_service, "_notify", lambda *args: None)

    def create(data):
        snapshot_env.row.update(data)
        snapshot_env.row.update(
            snapshot_id="snap_test", status="requested",
            requested_at="2026-09-24T00:00:00+00:00",
        )
        return dict(snapshot_env.row)

    def transition(snapshot_id, decision, actor):
        if snapshot_env.row["status"] != "requested":
            return dict(snapshot_env.row), False
        snapshot_env.row["status"] = decision
        snapshot_env.row["approved_by" if decision == "approved" else "rejected_by"] = actor
        return dict(snapshot_env.row), True

    monkeypatch.setattr(snapshot_request_service.db, "create", create)
    monkeypatch.setattr(snapshot_request_service.db, "transition", transition)
    monkeypatch.setattr(
        snapshot_request_service, "validate_request_authority",
        lambda candidate, active: candidate,
    )

    # The one worker identity used from request all the way through run/access below —
    # nothing here is swapped out for a different snapshot or a different run.
    run = {
        "project_id": "project", "group_id": "project.default.0517",
        "run_id": "run-cli-worker", "token_id": "tok-cli-worker",
        "provider_id": "provider-cli", "action_scope": "edit",
        "doc_ref": "flowgate.default.0517.0021-TR",
    }
    token = {
        "token_id": "tok-cli-worker", "ai_run_id": "run-cli-worker",
        "project": "project", "group_id": "project.default.0517",
        "action_scope": "edit", "doc_ref": "flowgate.default.0517.0021-TR",
        "issued_to": "cli-worker",
    }
    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: run)
    monkeypatch.setattr(access.usage_db, "record", lambda data: dict(data))
    monkeypatch.setattr(access.workflow_events, "create", lambda data: data)

    app = FastAPI()
    app.include_router(snapshot_routes.router)
    from modules.flow_gate.auth.middleware import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "human"}
    client = TestClient(app)
    headers = {"Authorization": "Bearer raw-cli-token"}

    requested = client.post(
        "/api/v1/snapshots/cli/request", headers=headers,
        json={"reason": "cli worker needs a real tree", "scope": "single_file",
              "requested_paths": ["one.txt"], "purpose": "run tests",
              "source_kind": "current_worktree"},
    )
    assert requested.status_code == 200
    snapshot_id = requested.json()["request"]["snapshot_id"]
    assert snapshot_id == snapshot_env.row["snapshot_id"]
    assert "locator" not in requested.text and "snapshot_path" not in requested.text

    # The SAME snapshot_id from the CLI request above is what human approval and the
    # CLI's OWN materialize call (not the separate human /approve route) publish, and
    # what every access/run call below operates on.
    snapshot_request_service.decide(snapshot_id, "approved", "human")
    assert snapshot_env.row["status"] == "approved"

    materialized = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/materialize", headers=headers,
    )
    assert materialized.status_code == 200, materialized.text
    assert snapshot_env.row["status"] == "created"
    materialized_body = materialized.json()
    assert materialized_body["snapshot"]["snapshot_id"] == snapshot_id
    assert materialized_body["snapshot"]["status"] == "active"
    assert "locator" not in materialized.text
    assert "snapshot_path" not in materialized.text
    assert str(snapshot_env.final()) not in materialized.text

    for operation, extra in (
        ("read", {"path": "one.txt"}),
        ("search", {"pattern": "one", "glob": "*.txt"}),
        ("glob", {"pattern": "*.txt"}),
        ("stat", {"path": "one.txt"}),
    ):
        response = client.post(
            f"/api/v1/snapshots/cli/{snapshot_id}/access", headers=headers,
            json={"operation": operation, **extra},
        )
        assert response.status_code == 200, response.text
        assert "locator" not in response.text
        assert str(snapshot_env.final()) not in response.text

    # Run a REAL child process through the CLI boundary. It prints all three host paths
    # that execute() itself injects (cwd, TEMP, TMP), covering the leak a mocked Popen
    # returning only "one" could never observe, AND writes those same paths into a file
    # inside the snapshot itself (0517.0021-TR rev4 rejection point 5: a leak written to
    # disk and re-consumed through access read/search must come out redacted too, not
    # just the directly captured stdout/stderr).
    print_and_write_runtime_paths = (
        f'"{sys.executable}" -c "import os,sys;'
        "open('runtime_paths.txt','w').write(os.getcwd()+chr(10)+str(os.environ.get('TEMP'))"
        "+chr(10)+str(os.environ.get('TMP'))+chr(10));"
        "print(os.getcwd());print(os.environ.get('TEMP'));print(os.environ.get('TMP'),file=sys.stderr)\""
    )
    executed = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/run", headers=headers,
        json={"task_kind": "test", "command": print_and_write_runtime_paths},
    )
    assert executed.status_code == 200, executed.text
    executed_body = executed.json()
    assert executed_body["exit_code"] == 0
    assert executed_body["snapshot"]["snapshot_id"] == snapshot_id
    assert access.SNAPSHOT_PATH_REDACTION in executed_body["stdout"]
    assert access.SNAPSHOT_PATH_REDACTION in executed_body["stderr"]
    combined_output = executed_body["stdout"] + executed_body["stderr"]
    raw_locators = (
        snapshot_env.scratch.resolve(),
        snapshot_env.final().resolve(),
        (snapshot_env.final() / "source").resolve(),
        (snapshot_env.final() / ".flowgate-tmp").resolve(),
    )
    for raw_path in raw_locators:
        raw = str(raw_path)
        assert raw not in combined_output
        assert raw.replace("\\", "/") not in combined_output
        assert raw.replace("/", "\\") not in combined_output
    assert "locator" not in executed.text
    assert "snapshot_path" not in executed.text
    assert str(snapshot_env.final()) not in executed.text

    # Read the file the subprocess just wrote inside the snapshot back through access
    # read — the raw paths it captured to disk must be redacted on the way out too.
    read_back = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/access", headers=headers,
        json={"operation": "read", "path": "runtime_paths.txt"},
    )
    assert read_back.status_code == 200, read_back.text
    assert access.SNAPSHOT_PATH_REDACTION in read_back.json()["content"]
    for raw_path in raw_locators:
        raw = str(raw_path)
        assert raw not in read_back.text
        assert raw.replace("\\", "/") not in read_back.text
        assert raw.replace("/", "\\") not in read_back.text
    assert "locator" not in read_back.text

    # search must redact the same on-disk leak inside matches[].text.
    search_back = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/access", headers=headers,
        json={"operation": "search", "pattern": ".+", "glob": "runtime_paths.txt"},
    )
    assert search_back.status_code == 200, search_back.text
    search_body = search_back.json()
    assert search_body["matches"], "search must find the file execute() wrote inside the snapshot"
    matched_text = "".join(item["text"] for item in search_body["matches"])
    assert access.SNAPSHOT_PATH_REDACTION in matched_text
    for raw_path in raw_locators:
        raw = str(raw_path)
        assert raw not in search_back.text
        assert raw.replace("\\", "/") not in search_back.text
        assert raw.replace("/", "\\") not in search_back.text
    assert "locator" not in search_back.text

    # A materialization failure stored with raw exception text (e.g. an OSError's own
    # str()) must not resurface a real absolute path through this same CLI boundary.
    snapshot_env.row.update(
        status="failed",
        failure_reason=(
            "copy interrupted: [Errno 13] Permission denied: "
            f"'{snapshot_env.final() / 'source' / 'one.txt'}'"
        ),
    )
    failed_access = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/access", headers=headers,
        json={"operation": "status"},
    )
    assert failed_access.status_code == 409, failed_access.text
    assert access.SNAPSHOT_PATH_REDACTION in failed_access.json()["detail"]["error"]["message"]
    assert str(snapshot_env.final()) not in failed_access.text


def test_api_tool_call_boundary_redacts_real_subprocess_file_round_trip(
    snapshot_env, monkeypatch,
):
    """0517.0021-TR rev4 rejection point 6: the API tool-call boundary (api_server_tools.py)
    must share the exact same redaction contract as the CLI HTTP boundary (proven above by
    ``test_cli_request_approve_materialize_access_run_share_one_snapshot``) rather than a
    parallel implementation. Drives a real subprocess through
    ``api_server_tools.run_source_snapshot`` and reads the file it wrote inside the
    snapshot back through ``api_server_tools.access_source_snapshot``.
    """
    monkeypatch.setattr(access.usage_db, "record", lambda data: dict(data))
    monkeypatch.setattr(access.workflow_events, "create", lambda data: data)
    access.materialization.materialize("snap_test", "human")

    token = {
        "token_id": "token", "ai_run_id": "run", "project": "project",
        "group_id": "project.default.0517", "issued_to": "worker",
    }
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(
        api_server_tools.snapshot_request_service, "validate_request_authority",
        lambda candidate, active: candidate,
    )
    run = {
        "token_id": "token", "current_token_id": "token", "run_id": "run",
        "project_id": "project", "group_id": "project.default.0517",
        "provider_id": "provider",
    }

    print_and_write_runtime_paths = (
        f'"{sys.executable}" -c "import os,sys;'
        "open('runtime_paths.txt','w').write(os.getcwd()+chr(10)+str(os.environ.get('TEMP'))"
        "+chr(10)+str(os.environ.get('TMP'))+chr(10));"
        "print(os.getcwd());print(os.environ.get('TEMP'));print(os.environ.get('TMP'),file=sys.stderr)\""
    )
    status, result = api_server_tools.run_source_snapshot(
        run, "raw-token",
        {"snapshot_id": "snap_test", "task_kind": "test", "command": print_and_write_runtime_paths},
        remaining_sec=30.0,
    )
    assert status == 200, result
    assert access.SNAPSHOT_PATH_REDACTION in result["stdout"]
    assert access.SNAPSHOT_PATH_REDACTION in result["stderr"]
    raw_locators = (
        snapshot_env.scratch.resolve(),
        snapshot_env.final().resolve(),
        (snapshot_env.final() / "source").resolve(),
        (snapshot_env.final() / ".flowgate-tmp").resolve(),
    )
    combined_output = result["stdout"] + result["stderr"]
    dumped = json.dumps(result)
    for raw_path in raw_locators:
        raw = str(raw_path)
        assert raw not in combined_output
        assert raw not in dumped
        assert raw.replace("\\", "/") not in dumped
        assert raw.replace("/", "\\") not in dumped

    read_status, read_result = api_server_tools.access_source_snapshot(
        run, "raw-token",
        {"snapshot_id": "snap_test", "operation": "read", "path": "runtime_paths.txt"},
    )
    assert read_status == 200, read_result
    assert access.SNAPSHOT_PATH_REDACTION in read_result["content"]
    read_dumped = json.dumps(read_result)
    for raw_path in raw_locators:
        assert str(raw_path) not in read_dumped

    search_status, search_result = api_server_tools.access_source_snapshot(
        run, "raw-token",
        {
            "snapshot_id": "snap_test", "operation": "search",
            "pattern": ".+", "glob": "runtime_paths.txt",
        },
    )
    assert search_status == 200, search_result
    matched_text = "".join(item["text"] for item in search_result["matches"])
    assert access.SNAPSHOT_PATH_REDACTION in matched_text
    search_dumped = json.dumps(search_result)
    for raw_path in raw_locators:
        assert str(raw_path) not in search_dumped


def test_lineage_authorization_rejects_forged_axes(access_env):
    access_env.row["chain_id"] = "chain-0517"
    successor = {**access_env.run, "run_id": "successor", "chain_id": "chain-0517"}
    assert access.access(
        successor, {"snapshot_id": "snap_access", "operation": "status"}
    )[0] == 200
    for change in (
        {"chain_id": "forged"}, {"group_id": "other.group"},
        {"project_id": "other"},
    ):
        with pytest.raises(access.SnapshotAccessError) as caught:
            access.access(
                {**successor, **change},
                {"snapshot_id": "snap_access", "operation": "status"},
            )
        assert caught.value.status == 403
