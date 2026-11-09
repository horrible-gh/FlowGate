"""flowgate.v02.0140 T0004: an existing scratch path is an explicit, safe start failure.

Isolated only: a migrated SQLite identity DB, pytest basetemp scratch roots, stubbed
lease/token stores. No live server, shared DB, operator scratch or token is touched.
Covers T#3 items 1-6 at source level (real Windows mkdir, real SQLite ownership); T#3-6 runs the
real admission, worker thread, judge, finalize and terminal cleanup with only the provider call stubbed.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.flow_gate.db import ai_run_identity as identity
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import runtime
from modules.flow_gate.storage import filesystem as storage_fs
from tests.integration.ai_run_identity.test_ai_run_identity_0138 import (  # noqa: F401 - fixtures
    identity_db, scratch_case, owner, tree_hash, open_store,
)

PROJECT = "flowgate"
GROUP_B = "flowgate.v02.0138"
OTHER_GROUP = "flowgate.v02test.0005"


def _legacy_folder(run_id: str) -> Path:
    """A retained folder of an earlier execution with the same id (pre-0138 manifest, no nonce)."""
    path = runtime._project_scratch_root(PROJECT) / run_id
    path.mkdir(parents=True)
    (path / "tmp").mkdir()
    (path / "cache").mkdir()
    (path / "cache" / "result.md").write_bytes(b"previous execution output")
    (path / runtime.SCRATCH_MANIFEST_NAME).write_text(json.dumps({
        "schema": 1, "owner": "flowgate.ai-invoke", "project_id": PROJECT, "run_id": run_id,
        "scratch_path": str(path.resolve()), "created_at": "2026-11-03T04:55:47+00:00",
        "completed_at": "2026-11-03T04:56:36+00:00",
        "policy": {"delete_on_complete": True, "retention_source": "system_settings"},
    }), encoding="utf-8")
    return path


def _snapshot(path: Path):
    return tree_hash(path), storage_fs.path_identity(path), sorted(p.name for p in path.iterdir())


# -- T#1 / T#3-1: existing folder -> structured conflict, nothing touched ---------------

def test_existing_legacy_folder_is_a_structured_conflict_and_preserved(scratch_case):
    b = owner()
    identity.claim(b)
    legacy = _legacy_folder(b["run_id"])
    before = _snapshot(legacy)
    with pytest.raises(runtime.ScratchPathConflict) as caught:
        runtime._create_scratch(b["project_id"], b["run_id"], b["execution_nonce"])
    exc = caught.value
    assert isinstance(exc, FileExistsError)                 # bounded whole-id retries keep working
    assert (exc.code, exc.phase, exc.run_id) == ("scratch_path_conflict", "scratch_create", b["run_id"])
    assert exc.os_errno == 17 and exc.errno == 17
    if os.name == "nt":
        assert exc.os_winerror == 183
    assert isinstance(exc.__cause__, FileExistsError)       # original OS error kept for the log
    assert str(exc) == "[Errno 17] scratch_path_conflict"   # no path, nonce or manifest in the text
    assert str(legacy) not in str(exc) and exc.filename is None
    assert _snapshot(legacy) == before                      # SHA256 list, identity, entries unchanged
    assert not storage_fs.journal_exists("ai-run", PROJECT, b["run_id"])
    manifest = json.loads((legacy / runtime.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "execution_nonce" not in manifest                # legacy never adopted by the new owner
    assert runtime._delete_owned_scratch(PROJECT, b["run_id"], legacy, b["execution_nonce"])[0] is False
    assert not runtime._mark_scratch_completed(PROJECT, b["run_id"], legacy, "2026-11-06T00:00:00+00:00",
                                               b["execution_nonce"])
    assert _snapshot(legacy) == before
    print("0140 legacy conflict:", exc.code, exc.os_errno, exc.os_winerror, "files", len(before[0]))


def test_same_nonce_conflicts_and_other_nonce_keeps_identity_conflict(scratch_case):
    row, scratch = scratch_case
    before = _snapshot(scratch)
    with pytest.raises(runtime.ScratchPathConflict):
        runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
    # A different execution of the same id is refused by ownership first, not by the path.
    with pytest.raises(identity.RunIdentityConflict):
        runtime._create_scratch(row["project_id"], row["run_id"], str(uuid4()))
    assert _snapshot(scratch) == before


def test_pending_delete_journal_is_not_reported_as_a_path_conflict(scratch_case, monkeypatch):
    row, scratch = scratch_case
    assert runtime._mark_scratch_completed(PROJECT, row["run_id"], scratch, "2026-11-06T00:01:00+00:00",
                                           row["execution_nonce"])
    monkeypatch.setattr(storage_fs, "remove_managed_tree",
                        lambda *_a: (_ for _ in ()).throw(PermissionError(13, "injected")))
    assert runtime._delete_owned_scratch(PROJECT, row["run_id"], scratch,
                                         row["execution_nonce"]) == (False, "delete_failed")
    assert storage_fs.journal_exists("ai-run", PROJECT, row["run_id"])
    before = _snapshot(scratch)
    with pytest.raises(ValueError, match="pending delete") as caught:
        runtime._create_scratch(PROJECT, row["run_id"], row["execution_nonce"])
    assert not isinstance(caught.value, runtime.ScratchPathConflict)
    assert _snapshot(scratch) == before


# -- T#3-2: concurrent creators, threads and independent OS processes -------------------

def test_two_threads_one_creator_and_the_loser_cleans_nothing(scratch_case):
    b = owner()
    identity.claim(b)
    barrier = threading.Barrier(2, timeout=60)
    results: list = []

    def create():
        barrier.wait()
        try:
            results.append(("created", runtime._create_scratch(PROJECT, b["run_id"], b["execution_nonce"])))
        except runtime.ScratchPathConflict as exc:
            results.append(("conflict", exc))

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
        assert not thread.is_alive()
    assert sorted(kind for kind, _ in results) == ["conflict", "created"]
    path = next(value for kind, value in results if kind == "created")
    manifest, reason = runtime._validate_scratch_manifest(PROJECT, b["run_id"], path, b["execution_nonce"])
    assert reason == "valid" and manifest["execution_nonce"] == b["execution_nonce"]
    assert (path / "tmp").is_dir() and (path / "cache").is_dir()
    assert not storage_fs.journal_exists("ai-run", PROJECT, b["run_id"])
    print("0140 threads:", sorted(kind for kind, _ in results))


def _create_in_process(db_path, storage_root, row, barrier, result_path):
    from contextlib import nullcontext
    from modules.flow_gate.db import connection
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from modules.flow_gate.services.ai_invoke import runtime as child_runtime
    from modules.flow_gate.storage import paths

    root = Path(storage_root)
    connection.STORE = open_store(db_path)
    paths.get_storage_root = lambda *a, **k: root
    child_runtime.db_projects.get_by_id = lambda _: {"project_name": "FlowGate"}

    def gate(operation, candidates, action=None, *, trees=()):
        return nullcontext() if operation == "paths_scope" else action()

    card_document_ports._handlers[card_document_ports.DOCUMENT_WRITE_GATE] = gate
    result = {"pid": os.getpid()}
    try:
        barrier.wait()
        path = child_runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
        result.update(result="created", path=str(path))
    except child_runtime.ScratchPathConflict as exc:
        result.update(result="conflict", code=exc.code, errno=exc.os_errno, winerror=exc.os_winerror,
                      message=str(exc))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        result.update(result="error", error=type(exc).__name__, message=str(exc))
    finally:
        connection.STORE._db.close()
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")


def test_two_os_processes_one_creator_and_the_loser_cleans_nothing(scratch_case, identity_db, tmp_path):
    db_path, _ = identity_db
    b = owner()
    identity.claim(b)
    storage_root = runtime.storage_paths.get_storage_root(PROJECT)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2, timeout=120)
    outputs = [tmp_path / f"proc-{i}.json" for i in range(2)]
    procs = [ctx.Process(target=_create_in_process, args=(db_path, str(storage_root), b, barrier, str(out)))
             for out in outputs]
    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(180)
            assert proc.exitcode == 0
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(10)
    results = [json.loads(out.read_text(encoding="utf-8")) for out in outputs]
    assert sorted(r["result"] for r in results) == ["conflict", "created"], results
    assert results[0]["pid"] != results[1]["pid"]
    loser = next(r for r in results if r["result"] == "conflict")
    assert loser["code"] == "scratch_path_conflict" and loser["errno"] == 17
    assert loser["message"] == "[Errno 17] scratch_path_conflict"
    if os.name == "nt":
        assert loser["winerror"] == 183
    winner = Path(next(r for r in results if r["result"] == "created")["path"])
    assert runtime._validate_scratch_manifest(PROJECT, b["run_id"], winner, b["execution_nonce"])[1] == "valid"
    print("0140 processes:", [(r["pid"], r["result"], r.get("winerror")) for r in results])


# -- T#1-3 / T#2: service boundary, rollback of only what this admission acquired -------

def _service_stubs(monkeypatch, *, revoke_fails=False, release_fails=False):
    leases = {OTHER_GROUP: {"group_id": OTHER_GROUP, "run_id": "aiv_20261103_003418", "state": "active",
                            "action_scope": "review", "worker_identity": "user-a", "token_id": "tok_a"}}
    released, revoked = [], []

    def release(group_id, run_id, reason=None):
        released.append((group_id, run_id, reason))
        if release_fails:
            raise OSError("injected lease release failure")
        if (leases.get(group_id) or {}).get("run_id") == run_id:
            leases.pop(group_id)
        return True

    def revoke(token_id, reason=None):
        revoked.append((token_id, reason))
        if revoke_fails:
            raise OSError("injected token revoke failure")

    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda group_id: leases.get(group_id))
    monkeypatch.setattr(svc.db_group_ai_leases, "release", release)
    monkeypatch.setattr(svc.token_service, "revoke", revoke)
    monkeypatch.setattr(svc._facade, "_runs", {})
    return leases, released, revoked


def _kwargs(group_id=GROUP_B):
    return {"project_id": PROJECT, "module": "v02", "group_id": group_id,
            "doc_ref": "flowgate.v02.0138.0003-NR", "action_scope": "edit", "mode": "single",
            "continuation_target_seq": None, "continuation_review_mode": False,
            "continuation_instruction_mode": None, "continuation_locale": None, "issued_to": "user-b",
            "api_base_url": "http://flowgate.test/flowgate/api/v1", "mention_builder": lambda *_: "mention"}


def _admission_reaching_scratch(leases, group_id, b, spawned, graph_exec_id=None):
    """Admission up to its real scratch step: own claim, own lease, own token, then mkdir."""
    def admit(**kwargs):
        svc.admission_run_id.set(b["run_id"])
        svc.admission_identity.set({**b, "action_scope": kwargs["action_scope"], "graph_exec_id": graph_exec_id,
                                    "graph_attempt_id": None})
        if not graph_exec_id:
            leases[group_id] = {"group_id": group_id, "run_id": b["run_id"], "state": "active",
                                "action_scope": kwargs["action_scope"], "worker_identity": kwargs["issued_to"],
                                "token_id": "tok_b"}
        svc.admission_identity.get()["token_id"] = "tok_b"
        scratch = runtime._create_scratch(b["project_id"], b["run_id"], b["execution_nonce"])
        svc.admission_identity.get()["scratch_dir"] = str(scratch)
        spawned.append(b["run_id"])                       # a worker would start only past this point
        return {"run_id": b["run_id"]}
    return admit


@pytest.mark.parametrize("injected", ["none", "revoke_fails", "release_fails"])
def test_start_run_maps_conflict_to_409_and_rolls_back_only_its_own(scratch_case, monkeypatch, injected):
    leases, released, revoked = _service_stubs(monkeypatch, revoke_fails=injected == "revoke_fails",
                                               release_fails=injected == "release_fails")
    b = owner(group_id=GROUP_B)
    identity.claim(b)
    legacy = _legacy_folder(b["run_id"])
    before = _snapshot(legacy)
    deletes = []
    monkeypatch.setattr(runtime, "_delete_owned_scratch", lambda *a, **k: deletes.append(a) or (False, "x"))
    spawned: list = []
    monkeypatch.setattr(svc, "_admission_start_run", _admission_reaching_scratch(leases, GROUP_B, b, spawned))

    with pytest.raises(HTTPException) as caught:
        svc.start_run(**_kwargs(GROUP_B))

    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail == {"code": "scratch_path_conflict", "message": detail["message"], "phase": "scratch_create",
                      "run_id": b["run_id"], "retryable": True}
    text = " ".join(str(value) for value in detail.values())
    for secret in (str(legacy), str(legacy.parent), legacy.as_posix(), b["execution_nonce"], "WinError",
                   "Traceback", ":\\", ":/"):
        assert secret not in text
    assert isinstance(caught.value.__cause__, runtime.ScratchPathConflict)
    assert spawned == []                                     # no worker/provider
    assert deletes == []                                     # the other run's folder is never cleaned
    assert _snapshot(legacy) == before
    # Only B's token and B's lease; A's lease/token in another group untouched.
    assert {token for token, _ in revoked} == {"tok_b"}
    assert {(group, run) for group, run, _ in released} == {(GROUP_B, b["run_id"])}
    assert leases[OTHER_GROUP]["token_id"] == "tok_a"
    if injected == "revoke_fails":
        assert released, "lease release must continue after a failed token revoke"
    if injected == "none":
        assert GROUP_B not in leases                         # the card's lock is free again
    print("0140 service:", injected, caught.value.status_code, detail["code"],
          "revoked", len(revoked), "released", len(released))


def test_graph_start_conflict_releases_no_group_lease(scratch_case, monkeypatch):
    leases, released, revoked = _service_stubs(monkeypatch)
    existing = {"group_id": GROUP_B, "run_id": "aiv_20261106_009999", "state": "active",
                "action_scope": "edit", "worker_identity": "user-b", "token_id": "tok_live"}
    leases[GROUP_B] = existing
    b = owner(group_id=GROUP_B)
    identity.claim(b)
    _legacy_folder(b["run_id"])
    monkeypatch.setattr(svc, "_admission_start_run",
                        _admission_reaching_scratch(leases, GROUP_B, b, [], graph_exec_id="exe_0140"))
    with pytest.raises(HTTPException) as caught:
        svc.start_run(**_kwargs(GROUP_B))
    assert caught.value.detail["code"] == "scratch_path_conflict"
    assert leases[GROUP_B] is existing                       # a lease that predates the call is never touched
    assert released == []
    assert {token for token, _ in revoked} == {"tok_b"}      # only the token this admission minted


def test_unexpected_os_error_message_omits_the_server_path(monkeypatch):
    _service_stubs(monkeypatch)
    secret = "C:\\storage\\flowgate\\scratch\\FlowGate\\aiv_20261106_000123"
    monkeypatch.setattr(svc, "_admission_start_run",
                        lambda **k: (_ for _ in ()).throw(PermissionError(13, "Access is denied", secret)))
    with pytest.raises(HTTPException) as caught:
        svc.start_run(**_kwargs())
    assert caught.value.status_code == 500
    assert caught.value.detail["message"] == "PermissionError: [Errno 13] Access is denied"
    assert secret not in json.dumps(caught.value.detail)


def test_api_route_returns_the_structured_409(scratch_case, monkeypatch):
    import sys
    import types
    from starlette.requests import Request
    from modules.flow_gate.api.v1 import ai_invoke_routes as routes

    leases, _, _ = _service_stubs(monkeypatch)
    b = owner(group_id=GROUP_B)
    identity.claim(b)
    _legacy_folder(b["run_id"])
    monkeypatch.setattr(svc, "_admission_start_run", _admission_reaching_scratch(leases, GROUP_B, b, []))
    monkeypatch.setattr(routes, "_require_user", lambda _: {"issued_to": "user-b", "is_admin": True})
    monkeypatch.setattr(routes.db_projects, "get_by_id",
                        lambda _: {"project_id": PROJECT, "project_name": "FlowGate"})
    fake = types.ModuleType("modules.flow_gate.api.token_routes")
    fake._build_api_base = lambda _: "http://testserver/flowgate/api/v1"
    monkeypatch.setitem(sys.modules, "modules.flow_gate.api.token_routes", fake)
    request = Request({"type": "http", "scheme": "http", "server": ("testserver", 80),
                       "path": "/flowgate/api/v1/ai-invoke/start", "headers": [], "query_string": b""})
    body = routes.AiInvokeStartRequest(project=PROJECT, module="v02", group="0138",
                                       doc_ref="flowgate.v02.0138.0003-NR", action_scope="new", mode="single")
    response = routes.start_ai_invoke(body, request)
    payload = json.loads(response.body)
    assert response.status_code == 409, payload
    assert payload["code"] == "scratch_path_conflict" and payload["retryable"] is True
    assert payload["phase"] == "scratch_create" and payload["run_id"] == b["run_id"]
    assert "storage" not in payload["message"] and ":\\" not in payload["message"]


# -- Graph: the refusal keeps its code and settles the request (no endless retry) -------

def test_graph_start_failure_keeps_the_conflict_code_and_cancels_the_request(monkeypatch):
    from flowgate.graph_run import execution
    from modules.flow_gate.graph_workflow.services import node_dispatch

    error = HTTPException(status_code=409, detail={"code": "scratch_path_conflict", "message": "m",
                                                   "phase": "scratch_create", "run_id": "aiv_20261106_000001",
                                                   "retryable": True})
    assert node_dispatch._start_failure(error) == "start_refused:scratch_path_conflict"
    cancelled = []
    monkeypatch.setattr(execution.fencing, "recorded", lambda _e: None)
    monkeypatch.setattr(execution.ledger, "get", lambda exec_id: {"exec_id": exec_id, "status": "requested"})
    monkeypatch.setattr(execution.ledger, "cancel", lambda *a: cancelled.append(a))
    # False = not a busy/duplicate refusal: the node records the failure, the request is settled once.
    assert execution.start_failed("exe_0140", error) is False
    assert cancelled == [("exe_0140", None, "requested")]


# -- T#2-5 / T#3-6: a new whole admission (new id/nonce) starts; the old folder stays ---

def test_new_request_with_new_identity_succeeds_and_old_folder_stays(scratch_case):
    b = owner()
    identity.claim(b)
    legacy = _legacy_folder(b["run_id"])
    before = _snapshot(legacy)
    with pytest.raises(runtime.ScratchPathConflict):
        runtime._create_scratch(PROJECT, b["run_id"], b["execution_nonce"])
    retry = owner()                                         # next committed id, new nonce
    identity.claim(retry)
    assert retry["run_id"] != b["run_id"]
    fresh = runtime._create_scratch(PROJECT, retry["run_id"], retry["execution_nonce"])
    assert fresh != legacy and fresh.parent == legacy.parent
    (fresh / "tmp" / "work.txt").write_text("retry output", encoding="utf-8")
    assert runtime._mark_scratch_completed(PROJECT, retry["run_id"], fresh, "2026-11-06T00:02:00+00:00",
                                           retry["execution_nonce"])
    assert runtime._delete_owned_scratch(PROJECT, retry["run_id"], fresh,
                                         retry["execution_nonce"]) == (True, "deleted")
    assert not fresh.exists()
    assert _snapshot(legacy) == before


def test_evaluation_bounded_retry_still_moves_to_a_new_id(identity_db, monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as evaluation

    ids = iter(["aiv_20261106_000201", "aiv_20261106_000202"])
    monkeypatch.setattr(evaluation.probe, "_next_run_id", lambda: next(ids))
    monkeypatch.setattr(evaluation.probe, "_cleanup_retained_scratches", lambda _scope: None)

    def create(_project_id, run_id, _nonce):
        if run_id.endswith("000201"):
            raise runtime.ScratchPathConflict(run_id, os_errno=17, os_winerror=183)
        return Path("/scratch") / run_id

    monkeypatch.setattr(evaluation.probe, "_create_scratch", create)
    run_id, path, nonce = evaluation._create_eval_scratch()
    assert (run_id, path) == ("aiv_20261106_000202", Path("/scratch/aiv_20261106_000202")) and nonce


# -- T#3-6: real admission 409, then a new request through admission, worker and finalize --

RETRY_TIMEOUT_SEC = 60


def _wait_finished(run_id: str) -> dict:
    """The run is ``finished`` (terminal cleanup published it) AND its worker thread has ended: the finalize's own
    scratch decision runs after that publication (persist first, destructive cleanup after)."""
    import time
    deadline = time.monotonic() + RETRY_TIMEOUT_SEC
    run = None
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run.get("status") == "finished":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"run {run_id} did not finish within {RETRY_TIMEOUT_SEC}s")
    workers = [t for t in threading.enumerate() if t.name == f"ai-invoke-{run_id}"]
    for worker_thread in workers:
        worker_thread.join(max(1.0, deadline - time.monotonic()))
        assert not worker_thread.is_alive(), f"worker of {run_id} still running"
    return run


@pytest.mark.parametrize("submission", ["submitted", "last_submission_failed"])
def test_real_admission_conflict_then_a_new_request_runs_the_worker_and_ends(scratch_case, identity_db,
                                                                             monkeypatch, submission):
    """First request: the real admission meets the existing folder -> 409, nothing started. Second request (new
    committed id / nonce / token): the real admission registers it, the real worker thread runs the provider chain
    (only the provider call itself is a stub: ``_cli_execute``), the real judge / finalize / terminal cleanup end it.
    The 0137 rule decides the run's own scratch (``submitted`` -> deleted by owner; ``last_submission_failed`` ->
    retained). The other execution's folder keeps its SHA256 list, NTFS identity and legacy manifest throughout."""
    from tests.modules.flow_gate.test_ai_invoke_pre_run_start_rollback_0550 import _real_admission_stub
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    from modules.flow_gate.services.ai_invoke import submission_ledger

    _, store = identity_db
    admission, _, leases, releases, revokes, kwargs = _real_admission_stub(monkeypatch)
    kwargs.pop("issue_builder")
    kwargs.update(group_id=GROUP_B, module="v02", doc_ref="flowgate.v02.0138.0003-NR")
    # the rows the run record references (ai_invoke_runs FKs: issued_to -> users, provider_id -> ai_providers)
    store._execute("INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                   "VALUES ('user-1', 'user-1', 'user-1@flowgate.test', 'x', 't', 't')", [])
    store._execute("INSERT INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                   "VALUES ('prov-1', 'P1', 'cli', 'claude', 't', 't')", [])
    tokens = iter(["tok_conflict", "tok_retry"])
    issued: list = []

    def issue(**k):
        token = {"token_id": next(tokens), "raw_token": "raw-" + str(len(issued)), "scratch_dir": "work"}
        issued.append(token["token_id"])
        return token

    monkeypatch.setattr(admission.token_service, "issue", issue)
    monkeypatch.setattr(admission.carryover, "carry_into_new_run", lambda **k: (k["mention"], None))
    monkeypatch.setattr(admission.carryover, "bind_run_scratch", lambda *a, **k: None)
    produced = {"ok": False}
    # the review scope's completion judge (0259): satisfied once the provider stub produced its work
    monkeypatch.setattr(admission.oracle, "_scope_oracle", lambda *a, **k: (lambda: produced["ok"]))
    monkeypatch.setattr(admission._svc(), "request_run_scratch_sweep", lambda *_a: None)
    monkeypatch.setattr(admission._svc(), "_require_group_worktree", lambda *a, **k: None)
    monkeypatch.setattr(admission._svc(), "_ensure_initial_source_sync", lambda *a, **k: None)

    def activate(group_id, run_id, token_id, action_scope, issued_to, _cap):
        assert leases["row"] and leases["row"]["run_id"] == run_id
        leases["row"] = {**leases["row"], "state": "active", "token_id": token_id,
                         "action_scope": action_scope, "worker_identity": issued_to}
        return dict(leases["row"])

    monkeypatch.setattr(admission.db_group_ai_leases, "activate", activate)
    monkeypatch.setattr(admission.db_group_ai_leases, "update_token", lambda *a, **k: None)
    runs: dict = {}
    monkeypatch.setattr(svc, "_runs", runs)
    monkeypatch.setattr(svc._facade, "_runs", runs)
    broadcasts: list = []
    monkeypatch.setattr(svc, "_broadcast", lambda run, event, payload: broadcasts.append((event, run["run_id"])))
    provider_calls: list = []

    def provider_stub(provider, prompt, run):
        """The provider: works in ITS OWN scratch (owner/nonce checked), submits, exits 0."""
        scratch = Path(run["scratch_dir"])
        _manifest, reason = runtime._validate_scratch_manifest(PROJECT, run["run_id"], scratch,
                                                               run["execution_nonce"])
        provider_calls.append((run["run_id"], scratch.name, reason, provider["id"]))
        (scratch / "tmp" / "work.txt").write_text("retry output", encoding="utf-8")
        submission_ledger.record(run, ok=True, status=200, via="inbox", action="review", doc_id=run["doc_ref"])
        if submission == "last_submission_failed":
            submission_ledger.record(run, ok=False, status=422, via="inbox", action="review",
                                     doc_id=run["doc_ref"])
        produced["ok"] = True
        run["exit_code"], run["last_message"], run["last_message_received"] = 0, "done", True
        return "started_ok", None

    monkeypatch.setattr(svc, "_cli_execute", provider_stub)
    legacy = _legacy_folder("aiv_20261106_000101")           # the first id the stub allocator returns
    before = _snapshot(legacy)

    # -- first request: 409, nothing started --
    with pytest.raises(HTTPException) as caught:
        svc.start_run(**kwargs)
    assert caught.value.status_code == 409, caught.value.detail
    assert caught.value.detail["code"] == "scratch_path_conflict"
    assert caught.value.detail["run_id"] == "aiv_20261106_000101"
    assert runs == {} and provider_calls == [] and broadcasts == []      # no registration, worker or provider
    assert leases["row"] is None                                         # own lease released, card unlocked
    assert {token for token, _ in revokes} == {"tok_conflict"}           # own token revoked
    assert db_runs.get("aiv_20261106_000101") is None                    # nothing published for the refused run
    assert _snapshot(legacy) == before

    # -- second request: new committed id / nonce / token, through admission, worker and finalize --
    started = svc.start_run(**kwargs)
    retry_id = started["run_id"]
    assert retry_id == "aiv_20261106_000102" and started["status"] == "running"
    run = _wait_finished(retry_id)

    owners = {r["run_id"]: r for r in store._fetch_all(
        "SELECT run_id, execution_nonce FROM ai_run_owners WHERE run_id IN (?, ?)",
        ["aiv_20261106_000101", retry_id])}
    assert set(owners) == {"aiv_20261106_000101", retry_id}              # both committed; the refused id is burned
    assert owners[retry_id]["execution_nonce"] == run["execution_nonce"]
    assert owners["aiv_20261106_000101"]["execution_nonce"] != run["execution_nonce"]
    # the provider ran once, in the retry's own fresh folder, with a valid owned manifest
    assert provider_calls == [(retry_id, retry_id, "valid", "prov-1")]
    # terminal state: finished / exited / complete, persisted under the retry's owner
    assert (run["status"], run["end_reason"], run["outcome"]) == ("finished", "exited", "complete")
    durable = db_runs.get(retry_id)
    assert durable and durable["status"] == "finished" and durable["outcome"] == "complete"
    assert [e for e, rid in broadcasts if rid == retry_id].count("ai_invoke_started") == 1
    assert all(rid == retry_id for _, rid in broadcasts)
    # own lease released at the end (the card is free again), own credential dropped; the other run untouched
    assert leases["row"] is None
    assert (retry_id, "normal_finish") in releases
    assert not run.get("lease_cleanup_pending")
    assert "raw_token" not in run and run["token_id"] == "tok_retry"
    assert "raw-1" not in json.dumps(durable, default=str)
    assert issued == ["tok_conflict", "tok_retry"]
    assert {token for token, _ in revokes} == {"tok_conflict"}           # nobody revoked the retry's token for it
    # 0137: the retry's own scratch, judged by its own ordered submissions
    fresh = legacy.parent / retry_id
    cleanup = run["scratch_cleanup"]
    if submission == "submitted":
        # 0157: settled -> no carry-over source, but the folder waits for the system TTL.
        assert cleanup["action"] == "retained" and cleanup["reason"] == "settled_retention_active"
        assert run["scratch_retained"] is None
        assert (fresh / "tmp" / "work.txt").read_text(encoding="utf-8") == "retry output"
        manifest, reason = runtime._validate_scratch_manifest(PROJECT, retry_id, fresh, run["execution_nonce"])
        assert reason == "valid" and manifest.get("completed_at")         # stamped for the retained sweep
    else:
        assert cleanup["action"] == "retained" and cleanup["reason"] == "last_submission_failed"
        assert (fresh / "tmp" / "work.txt").read_text(encoding="utf-8") == "retry output"
        manifest, reason = runtime._validate_scratch_manifest(PROJECT, retry_id, fresh, run["execution_nonce"])
        assert reason == "valid" and manifest.get("completed_at")         # stamped for the retained sweep
    # the other execution's folder: SHA256 list, path identity, entries and legacy manifest unchanged
    assert _snapshot(legacy) == before
    assert "execution_nonce" not in json.loads((legacy / runtime.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
    print("0140 retry E2E:", submission, "conflict 409 ->", retry_id, run["status"], run["end_reason"],
          run["outcome"], "scratch", cleanup["action"], cleanup.get("reason"), "legacy files", len(before[0]))
