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
from test_snapshot_materialization_0517 import _disguise_same_size_same_mtime  # noqa: E402


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

    def refreshed(snapshot_id, actor, *, source=None):
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
        lambda snapshot_id, actor, *, source=None: {
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


# ---------------------------------------------------------------------------
# 0517 T0022: no worker-facing boundary may carry a server-internal absolute path,
# including the live FlowGate worktree path an OSError names on materialize.
# ---------------------------------------------------------------------------


def _path_spellings(path) -> set[str]:
    """Every way a path can be spelled in worker-visible text: native, '/', '\\', the
    doubled backslashes of an OSError/repr() rendering, and the JSON-escaped body form."""
    raw = str(path)
    windows = raw.replace("/", "\\")
    spellings = {raw, raw.replace("\\", "/"), windows, windows.replace("\\", "\\\\")}
    spellings |= {json.dumps(item)[1:-1] for item in list(spellings)}
    return {item for item in spellings if item}


def _assert_no_raw_path(blob, *paths) -> None:
    text = blob if isinstance(blob, str) else json.dumps(blob, ensure_ascii=False)
    haystack = text.casefold() if sys.platform == "win32" else text
    for path in paths:
        for spelling in _path_spellings(path):
            needle = spelling.casefold() if sys.platform == "win32" else spelling
            assert needle not in haystack, f"raw path leaked: {spelling!r} in {text[:600]!r}"


def _cli_client(snapshot_env, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    run = {
        "project_id": "project", "group_id": "project.default.0517",
        "run_id": "run", "token_id": "token", "current_token_id": "token",
        "provider_id": "provider", "action_scope": "edit",
    }
    token = {
        "token_id": "token", "ai_run_id": "run", "project": "project",
        "group_id": "project.default.0517", "action_scope": "edit", "issued_to": "worker",
    }
    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(
        snapshot_routes.service, "validate_request_authority", lambda candidate, active: candidate,
    )
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: run)
    monkeypatch.setattr(access.usage_db, "record", lambda data: dict(data))
    # workflow_events.create stays the fixture's recorder so audit rows can be inspected.
    app = FastAPI()
    app.include_router(snapshot_routes.router)
    from modules.flow_gate.auth.middleware import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "human"}
    return TestClient(app), {"Authorization": "Bearer raw-cli-token"}, run


def test_t0022_live_worktree_oserror_on_materialize_never_reaches_a_worker(
    snapshot_env, monkeypatch,
):
    """Real reproduction: the requested file disappears between the scope scan and the
    copy, so os.open() raises a genuine FileNotFoundError whose str() names the live
    FlowGate worktree path. Before T0022 that text was stored verbatim in failure_reason
    (the old sanitizer only knew the token scratch root) and re-served on every
    status/access/run call; CLI materialize surfaced it as an unhandled exception.
    """
    client, headers, run = _cli_client(snapshot_env, monkeypatch)
    worktree = snapshot_env.worktree
    live_file = worktree / "one.txt"

    real_collect = access.materialization._collect_scope

    def scan_then_lose_file(row, root):
        scanned = real_collect(row, root)
        live_file.unlink()
        return scanned

    raised = []
    real_copy = access.materialization._copy_and_hash

    def spy_copy(source, target, expected):
        try:
            return real_copy(source, target, expected)
        except OSError as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(access.materialization, "_collect_scope", scan_then_lose_file)
    monkeypatch.setattr(access.materialization, "_copy_and_hash", spy_copy)

    materialized = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)

    # Precondition: the OS really produced an error naming the live worktree file.
    assert len(raised) == 1 and isinstance(raised[0], FileNotFoundError)
    assert str(live_file) in str(raised[0]) or str(live_file).replace("\\", "\\\\") in str(raised[0])

    # 1) CLI materialize response: a precise, scrubbed 409 instead of raw exception text.
    assert materialized.status_code == 409, materialized.text
    detail = materialized.json()["detail"]
    assert detail["code"] == "snapshot_create_failed"
    assert detail["message"].startswith("FileNotFoundError:")
    assert access.materialization.WORKTREE_REDACTION in detail["message"]
    assert "one.txt" in detail["message"]  # relative suffix survives for diagnosis
    _assert_no_raw_path(materialized.text, worktree, live_file, snapshot_env.scratch)

    # 2) The durable failure_reason and its audit event are stored already scrubbed.
    assert snapshot_env.row["status"] == "failed"
    stored = snapshot_env.row["failure_reason"]
    assert access.materialization.WORKTREE_REDACTION in stored
    _assert_no_raw_path(stored, worktree, live_file)
    failure_event = [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"][-1]
    _assert_no_raw_path(failure_event["metadata"], worktree, live_file)

    # 3) Every later worker-facing read of the failed snapshot: CLI status/access/run
    #    and the API tool-call functions.
    responses = [
        client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers),
        client.post("/api/v1/snapshots/cli/snap_test/access", headers=headers, json={"operation": "status"}),
        client.post("/api/v1/snapshots/cli/snap_test/run", headers=headers,
                    json={"task_kind": "test", "command": "echo x"}),
    ]
    for response in responses:
        assert response.status_code in (200, 409), response.text
        _assert_no_raw_path(response.text, worktree, live_file, snapshot_env.scratch)
    access_body = responses[1].json()["detail"]
    assert access_body["error"]["code"] == "snapshot_failed"
    assert access.materialization.WORKTREE_REDACTION in access_body["error"]["message"]

    api_access = api_server_tools.access_source_snapshot(run, "raw", {"snapshot_id": "snap_test", "operation": "status"})
    api_run = api_server_tools.run_source_snapshot(
        run, "raw", {"snapshot_id": "snap_test", "task_kind": "test", "command": "echo x"}, 30.0,
    )
    for status, payload in (api_access, api_run):
        assert status == 409
        assert payload["error"]["code"] == "snapshot_failed"
        _assert_no_raw_path(payload, worktree, live_file, snapshot_env.scratch)


def test_t0022_legacy_stored_worktree_path_is_scrubbed_on_read(snapshot_env, monkeypatch):
    """A failure_reason persisted before T0022 (raw OSError text naming the worktree, in
    the doubled-backslash repr spelling) is still scrubbed when it is re-served."""
    client, headers, run = _cli_client(snapshot_env, monkeypatch)
    leaked = snapshot_env.worktree / "dir" / "a.txt"
    snapshot_env.row.update(
        status="failed", failure_code="snapshot_create_failed",
        failure_reason=f"[Errno 13] Permission denied: {str(leaked)!r}",
    )
    response = client.post("/api/v1/snapshots/cli/snap_test/access", headers=headers, json={"operation": "status"})
    assert response.status_code == 409
    message = response.json()["detail"]["error"]["message"]
    assert access.materialization.WORKTREE_REDACTION in message
    _assert_no_raw_path(response.text, snapshot_env.worktree, leaked)
    status, payload = api_server_tools.access_source_snapshot(run, "raw", {"snapshot_id": "snap_test", "operation": "status"})
    assert status == 409
    _assert_no_raw_path(payload, snapshot_env.worktree, leaked)


def test_t0022_real_oserror_inside_run_is_scrubbed_on_cli_and_api(snapshot_env, monkeypatch):
    """execute() creating its TEMP dir hits a real FileExistsError naming the snapshot
    scratch path. Previously that escaped both boundaries as an unhandled exception."""
    client, headers, run = _cli_client(snapshot_env, monkeypatch)
    access.materialization.materialize("snap_test", "human")
    blocker = snapshot_env.final() / ".flowgate-tmp"
    blocker.write_text("not a directory", encoding="utf-8")

    response = client.post("/api/v1/snapshots/cli/snap_test/run", headers=headers,
                           json={"task_kind": "test", "command": "echo x"})
    assert response.status_code == 409, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "snapshot_io_failed"
    assert "FileExistsError" in error["message"]
    assert access.SNAPSHOT_PATH_REDACTION in error["message"]
    _assert_no_raw_path(response.text, snapshot_env.scratch, blocker, snapshot_env.worktree)

    status, payload = api_server_tools.run_source_snapshot(
        run, "raw", {"snapshot_id": "snap_test", "task_kind": "test", "command": "echo x"}, 30.0,
    )
    assert status == 409
    assert payload["error"]["code"] == "snapshot_io_failed"
    _assert_no_raw_path(payload, snapshot_env.scratch, blocker, snapshot_env.worktree)


def test_t0022_success_payloads_redact_the_live_worktree_path_too(snapshot_env, monkeypatch):
    """A snapshot file (or a command's output) that mentions the live worktree path must
    not hand it to the worker; before T0022 only the scratch root was redacted."""
    client, headers, run = _cli_client(snapshot_env, monkeypatch)
    snapshot_env.worktree.joinpath("one.txt").write_text(
        f"root={snapshot_env.worktree}\n", encoding="utf-8", newline="\n",
    )
    access.materialization.materialize("snap_test", "human")
    read = client.post("/api/v1/snapshots/cli/snap_test/access", headers=headers,
                       json={"operation": "read", "path": "one.txt"})
    assert read.status_code == 200, read.text
    assert read.json()["content"] == f"root={access.materialization.WORKTREE_REDACTION}\n"
    _assert_no_raw_path(read.text, snapshot_env.worktree)

    echo = f'"{sys.executable}" -c "print(open(\'one.txt\').read().strip())"'
    status, payload = api_server_tools.run_source_snapshot(
        run, "raw", {"snapshot_id": "snap_test", "task_kind": "test", "command": echo}, 30.0,
    )
    assert status == 200, payload
    assert access.materialization.WORKTREE_REDACTION in payload["stdout"]
    _assert_no_raw_path(payload, snapshot_env.worktree, snapshot_env.scratch)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r"denied: 'D:\\elsewhere\\secret.txt'", "denied: '<redacted-path>'"),
        ("denied: 'D:/elsewhere/secret.txt'", "denied: '<redacted-path>'"),
        (r"at \\fileserver\share\flowgate\x.py", "at <redacted-path>"),
        ("open /opt/flowgate/work/tok/x failed", "open <redacted-path> failed"),
        ("requested path does not exist: dir/sub/b.txt", "requested path does not exist: dir/sub/b.txt"),
        ("see http://127.0.0.1:8089/flowgate/api/v1/help", "see http://127.0.0.1:8089/flowgate/api/v1/help"),
        ("snapshot-to-worktree/main promotion is prohibited", "snapshot-to-worktree/main promotion is prohibited"),
    ],
)
def test_t0022_error_text_drops_unknown_absolute_paths_but_keeps_relative_text(text, expected):
    assert access.materialization.redact_error_text(text, ()) == expected


def test_t0023_cli_status_rechecks_content_freshness_over_http(snapshot_env, monkeypatch):
    """The dedicated CLI status route used to render the stored row: a same-size,
    restored-mtime edit made after creation came back ``active`` with current-worktree
    validation allowed. It must run the same content refresh as access/run."""
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    access.materialization.materialize("snap_test", "human")
    before = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["snapshot"]["status"] == "active"
    assert before.json()["snapshot"]["current_worktree_validation_allowed"] is True

    _disguise_same_size_same_mtime(snapshot_env.worktree / "one.txt", "ONE")
    assert snapshot_env.row["stale"] is False  # nothing has re-checked yet

    after = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert after.status_code == 200, after.text
    snapshot = after.json()["snapshot"]
    assert snapshot["status"] == "stale"
    assert snapshot["stale"] is True
    assert snapshot["current_worktree_validation_allowed"] is False
    assert snapshot["warning"] == access.STALE_WARNING
    assert snapshot_env.row["stale"] is True
    stale_events = [e for e in snapshot_env.state.events if e["event_type"] == "snapshot_stale"]
    assert len(stale_events) == 1
    assert json.loads(stale_events[0]["metadata"])["stale_reason"] == "scope_fingerprint_changed"
    _assert_no_raw_path(after.text, snapshot_env.worktree, snapshot_env.scratch)


def test_t0023_cli_status_of_created_snapshot_with_missing_tree_is_not_validatable(
    snapshot_env, monkeypatch,
):
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    access.materialization.materialize("snap_test", "human")
    import shutil
    shutil.rmtree(snapshot_env.final())
    response = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert response.status_code == 200, response.text
    snapshot = response.json()["snapshot"]
    assert snapshot["available"] is False
    assert snapshot["integrity_error"] == "snapshot_missing"
    assert snapshot["current_worktree_validation_allowed"] is False
    _assert_no_raw_path(response.text, snapshot_env.worktree, snapshot_env.scratch)


def test_t0023_namespace_preparation_failure_is_recorded_as_create_failure(
    snapshot_env, monkeypatch,
):
    """A real OSError: the namespace name is occupied by a regular file, so
    ``mkdir(exist_ok=True)`` fails before the claim. It used to escape the failure
    recorder and leave the request ``approved``."""
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    (snapshot_env.scratch / access.materialization.SNAPSHOT_NAMESPACE).write_text("x", encoding="utf-8")

    response = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "snapshot_scratch_unavailable"
    _assert_no_raw_path(response.text, snapshot_env.worktree, snapshot_env.scratch)
    assert snapshot_env.row["status"] == "failed"
    assert snapshot_env.row["failure_code"] == "snapshot_create_failed"
    failure = [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"][-1]
    metadata = json.loads(failure["metadata"])
    assert metadata["failure_stage"] == "snapshot_scratch_unavailable"
    assert (failure["from_state"], failure["to_state"]) == ("approved", "failed")

    status = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["snapshot"]["status"] == "failed"


def test_t0023_claim_permission_error_is_recorded_and_scrubbed(snapshot_env, monkeypatch):
    """A non-FileExists OSError creating the claim (here PermissionError naming the
    scratch path) used to skip the failure recorder and reach the CLI as a 500
    ``snapshot_materialize_failed``."""
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    real_mkdir = Path.mkdir

    def deny_claim(self, *args, **kwargs):
        if self.name.endswith(".materializing"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_claim)
    response = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)
    monkeypatch.setattr(Path, "mkdir", real_mkdir)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "snapshot_create_failed"
    assert detail["message"].startswith("PermissionError:")
    _assert_no_raw_path(response.text, snapshot_env.worktree, snapshot_env.scratch)
    assert snapshot_env.row["status"] == "failed"
    assert snapshot_env.row["failure_code"] == "snapshot_create_failed"
    _assert_no_raw_path(snapshot_env.row["failure_reason"], snapshot_env.scratch)
    failure = [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"][-1]
    assert json.loads(failure["metadata"])["failure_stage"] == "PermissionError"
    assert not snapshot_env.final().exists()


def test_t0023_busy_claim_stays_a_conflict_without_failing_the_request(
    snapshot_env, monkeypatch,
):
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    namespace = snapshot_env.scratch / access.materialization.SNAPSHOT_NAMESPACE
    namespace.mkdir()
    (namespace / ".snap_test.materializing").mkdir()

    response = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "snapshot_materialization_busy"
    assert snapshot_env.row["status"] == "approved"
    assert not [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"]
    # The other owner's claim is left untouched.
    assert (namespace / ".snap_test.materializing").is_dir()


def _leave_invalid_final(snapshot_env, damage: str) -> Path:
    """A ``snap_<id>`` directory left under the namespace (e.g. a crash or a manual
    copy) whose manifest is missing or corrupt, while the DB row is still approved."""
    final = snapshot_env.final()
    (final / "source").mkdir(parents=True)
    (final / "README.md").write_text("readme", encoding="utf-8")
    if damage == "corrupt":
        (final / "snapshot.json").write_text("{not json", encoding="utf-8")
    return final


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_t0023_invalid_existing_final_is_recorded_as_create_failure_over_http(
    snapshot_env, monkeypatch, damage,
):
    """The existing-final branch (``_recover_published``) used to raise
    ``snapshot_integrity_error`` outside the failure recorder: the CLI got a 409 but the
    request stayed ``approved`` with no event, and every later materialize hit the same
    wall. An unrecoverable final is now an approved->failed creation failure."""
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    _leave_invalid_final(snapshot_env, damage)

    response = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "snapshot_integrity_error"
    _assert_no_raw_path(response.text, snapshot_env.worktree, snapshot_env.scratch)
    assert snapshot_env.row["status"] == "failed"
    assert snapshot_env.row["failure_code"] == "snapshot_create_failed"
    _assert_no_raw_path(snapshot_env.row["failure_reason"], snapshot_env.worktree, snapshot_env.scratch)
    failures = [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"]
    assert len(failures) == 1
    assert (failures[0]["from_state"], failures[0]["to_state"]) == ("approved", "failed")
    assert json.loads(failures[0]["metadata"])["failure_stage"] == "snapshot_integrity_error"
    assert not [e for e in snapshot_env.state.events if e["event_type"] == "snapshot_created"]

    again = client.post("/api/v1/snapshots/cli/snap_test/materialize", headers=headers)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "snapshot_not_approved"
    status = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["snapshot"]["status"] == "failed"
    assert status.json()["snapshot"]["current_worktree_validation_allowed"] is False
    # Not a destructive cleanup path: the leftover tree stays for the explicit cleanup.
    assert snapshot_env.final().is_dir()


def test_t0023_startup_recovery_of_invalid_final_fails_the_request(snapshot_env):
    _leave_invalid_final(snapshot_env, "missing")
    access.materialization.cleanup_orphans(startup=True)
    assert snapshot_env.row["status"] == "failed"
    assert snapshot_env.row["failure_code"] == "snapshot_create_failed"
    failure = [e for e in snapshot_env.state.events if e["event_type"] == "state_changed"][-1]
    assert json.loads(failure["metadata"])["failure_stage"] == "snapshot_integrity_error"


def _link_directory(link: Path, target: Path) -> None:
    """A real directory link: an NTFS junction on Windows (no privilege needed), a
    symlink elsewhere. Never skipped: a skip here would be a false green."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_t0023_cli_status_closes_a_source_root_swapped_for_a_link(snapshot_env, monkeypatch):
    """``_load_manifest`` checked ``source.is_dir()``, which follows links, so a
    ``source`` replaced by a junction/symlink to another directory still looked healthy:
    the CLI status said ``active`` with current-worktree validation allowed while
    access/run rejected the same snapshot as ``snapshot_locator_invalid``."""
    client, headers, _run = _cli_client(snapshot_env, monkeypatch)
    access.materialization.materialize("snap_test", "human")
    before = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["snapshot"]["available"] is True
    assert before.json()["snapshot"]["current_worktree_validation_allowed"] is True

    source = snapshot_env.final() / "source"
    elsewhere = snapshot_env.scratch.parent / "elsewhere"
    source.rename(elsewhere)
    _link_directory(source, elsewhere)
    assert access.materialization._is_reparse_or_symlink(source)
    assert source.is_dir()  # the link resolves: only a non-following check catches it

    status = client.get("/api/v1/snapshots/cli/snap_test/status", headers=headers)
    assert status.status_code == 200, status.text
    snapshot = status.json()["snapshot"]
    assert snapshot["available"] is False
    assert snapshot["integrity_error"] == "snapshot_integrity_error"
    assert snapshot["current_worktree_validation_allowed"] is False
    assert snapshot["status"] != "active"
    _assert_no_raw_path(status.text, snapshot_env.worktree, snapshot_env.scratch)

    # access/run agree with status: the same snapshot is not usable.
    read = client.post(
        "/api/v1/snapshots/cli/snap_test/access", headers=headers,
        json={"operation": "read", "path": "one.txt"},
    )
    assert read.status_code == 409, read.text
    assert read.json()["detail"]["error"]["code"] == "snapshot_failed"
    assert read.json()["detail"]["error"]["details"]["reason"] == "snapshot_integrity_error"
    ran = client.post(
        "/api/v1/snapshots/cli/snap_test/run", headers=headers,
        json={"task_kind": "test", "command": "echo x"},
    )
    assert ran.status_code == 409, ran.text
    assert ran.json()["detail"]["error"]["code"] == "snapshot_failed"
    # The link target is never touched.
    assert (elsewhere / "one.txt").read_text(encoding="utf-8") == "one"


def test_t0022_known_roots_keep_marker_and_relative_suffix(tmp_path):
    root = tmp_path / "work" / "proj" / "tok_x"
    roots = ((str(root), access.SNAPSHOT_PATH_REDACTION),)
    target = str(root / "source-snapshots" / "snap_1" / "source" / "a.py")
    windows = target.replace("/", "\\")
    for spelling in (target, target.replace("\\", "/"), windows, windows.replace("\\", "\\\\")):
        redacted = access.materialization.redact_error_text(f"x '{spelling}' y", roots)
        assert redacted.startswith(f"x '{access.SNAPSHOT_PATH_REDACTION}")
        assert "a.py' y" in redacted
    # A sibling directory sharing the prefix is not mistaken for the root.
    sibling = str(root) + "-other"
    assert access.materialization.redact_locators(sibling, roots) == sibling
