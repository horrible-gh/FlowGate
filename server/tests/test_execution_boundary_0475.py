"""T0005: execution-boundary regression locks for worktree admission and CLI cwd."""
from __future__ import annotations

import ast
import inspect
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from modules.flow_gate.api.v1 import ai_invoke_routes
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import process_runner


# Task 1 — invoke every CLI entry point and prove its arguments reach the gate.
def test_task1_all_cli_hop_entrypoints_reach_the_worktree_gate(monkeypatch):
    calls = []

    def blocked_gate(project_id, module, group_id, branch, locale=None):
        calls.append((project_id, module, group_id, branch, locale))
        raise HTTPException(status_code=409, detail={"code": "worktree_unavailable"})

    def boundary_start_run(**kwargs):
        svc._require_group_worktree(
            kwargs["project_id"], kwargs["module"], kwargs["group_id"],
            "group-branch", locale=kwargs.get("continuation_locale"),
        )

    monkeypatch.setattr(svc, "_require_group_worktree", blocked_gate)
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective", lambda _p: {
        "providers": [{"id": "provider"}], "source": "configured", "registered_count": 1,
    })
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _g: None)
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _d: {"branch": "group-branch"})
    with pytest.raises(HTTPException):
        svc.start_run(
            project_id="flowgate", module="default", group_id="flowgate.default.0475",
            doc_ref="flowgate.default.0475.0005-T", action_scope="new", mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale="ja", issued_to="worker",
            api_base_url="http://local", mention_builder=lambda *_a: None,
        )
    monkeypatch.setattr(svc, "start_run", boundary_start_run)
    pending = {
        "doc_ref": "flowgate.default.0475.0005-T", "target_seq": 5,
        "review_mode": False, "instruction_mode": None, "locale": "ja",
        "issued_to": "worker", "api_base_url": "http://local",
    }
    gate = {"slot": {"doc_id": pending["doc_ref"], "item_seq": 5}}

    entrypoints = [
        ("auto_resume", lambda: svc._spawn_auto_resume("flowgate.default.0475", pending)),
        ("review_hop", lambda: svc._spawn_review_hop("flowgate.default.0475", pending, gate)),
        ("rework_hop", lambda: svc._spawn_rework_hop("flowgate.default.0475", pending, gate)),
    ]
    monkeypatch.setattr(svc, "resolve_reviewer", lambda *_a: "provider")
    monkeypatch.setattr(svc, "resolve_step_executor", lambda *_a: "provider")
    for _name, invoke in entrypoints:
        with pytest.raises(HTTPException) as exc:
            invoke()
        assert exc.value.status_code == 409

    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    row = {
        "group_id": "flowgate.default.0475", "doc_ref": pending["doc_ref"],
        "paused_by": "worker", "paused_at": "2026-08-28T00:00:00+09:00",
        "continuation_target_seq": 5,
    }
    monkeypatch.setattr(svc, "_active_run_for_group", lambda _g: None)
    monkeypatch.setattr(svc, "_paused_row_resume_state", lambda *_a, **_k: {
        "resume_available": True, "_resume_target_seq": 5,
    })
    monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda _d: 5)
    monkeypatch.setattr(svc, "resolve_review_gate", lambda _b: {"stage": "work"})
    monkeypatch.setattr(svc, "_resumable_reviewer_overrides", lambda *_a: None)
    monkeypatch.setattr(svc, "_resumable_base_provider", lambda *_a: None)
    monkeypatch.setattr(db_paused, "get_by_group", lambda _g: dict(row))
    monkeypatch.setattr(db_paused, "release_owned", lambda *_a, **_k: dict(row))
    monkeypatch.setattr(db_paused, "upsert", lambda **_k: None)
    with pytest.raises(HTTPException) as exc:
        svc.resume_chain(group_id="flowgate.default.0475", user_id="worker",
                         api_base_url="http://local", locale="ja")
    assert exc.value.status_code == 409

    class Request:
        headers = {"x-locale": "ja"}
        base_url = "http://local/"
    monkeypatch.setattr(ai_invoke_routes, "_require_user",
                        lambda _r: {"issued_to": "worker", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id",
                        lambda _p: {"project_name": "FlowGate"})
    monkeypatch.setitem(sys.modules, "modules.flow_gate.api.token_routes",
                        types.SimpleNamespace(_build_mention_for_token=lambda **_k: "mention",
                                              _build_api_base=lambda _r: "http://local"))
    body = ai_invoke_routes.AiInvokeStartRequest(
        project="flowgate", module="default", group="0475",
        doc_ref=pending["doc_ref"], action_scope="new", mode="single",
    )
    response = ai_invoke_routes.start_ai_invoke(body, Request())
    assert response.status_code == 409

    assert calls == [
        ("flowgate", "default", "flowgate.default.0475", "group-branch", "ja"),
        ("flowgate", "default", "flowgate.default.0475", "group-branch", "ja"),
        ("flowgate", "default", "flowgate.default.0475", "group-branch", "ja"),
        ("flowgate", "default", "flowgate.default.0475", "group-branch", "ja"),
        ("flowgate", "default", "flowgate.default.0475", "group-branch", "ja"),
        ("flowgate", "default", "flowgate.default.0475", "group-branch", None),
    ]


def test_task1_popen_has_one_owner_with_actionable_coordinates():
    source = inspect.getsource(svc)
    tree = ast.parse(source)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "Popen"):
            continue
        owner = next((p.name for p in _ancestors(node, parents) if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))), "<module>")
        sites.append(f"server/modules/flow_gate/services/ai_invoke_service.py:{node.lineno}:{owner}")
    assert len(sites) == 1 and sites[0].endswith(":_cli_execute"), f"unexpected Popen sites: {sites}"


def _ancestors(node, parents):
    while node in parents:
        node = parents[node]
        yield node


# Tasks 2 and 4 — every hop re-resolves; cleanup/self-heal interleaving is last-check-wins.
def _wire_gate(monkeypatch, tmp_path, resolved, ensure_result="ok", ensure_exc=None, provision_error=None, session=None):
    wt = tmp_path / "worktree"
    wt.mkdir(exist_ok=True)
    base = tmp_path / "base"
    base.mkdir(exist_ok=True)
    values = iter(resolved)
    ensure_calls = []
    monkeypatch.setattr(svc.db_git, "get_config", lambda _p: {"enabled": True})
    monkeypatch.setattr(svc.db_git, "get_state", lambda _g: {"branch": "b", "provision_error": provision_error})
    monkeypatch.setattr(svc.git_service, "_project_name", lambda _p: "proj")
    monkeypatch.setattr(svc.git_service, "src_root", lambda *_a: wt)
    monkeypatch.setattr(svc.git_service, "open_merge_session_of_project", lambda _p: session)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root", lambda *_a, **_k: next(values))

    def ensure(*_a, **_k):
        ensure_calls.append(1)
        if ensure_exc:
            raise ensure_exc
        return ensure_result
    monkeypatch.setattr(svc.git_service, "ensure_worktree", ensure)
    return wt, base, ensure_calls


def test_task2_second_hop_rechecks_after_worktree_disappears(monkeypatch, tmp_path):
    wt, base, ensure_calls = _wire_gate(monkeypatch, tmp_path, [])
    roots = iter([wt, wt, base, base])
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root", lambda *_a, **_k: next(roots))
    start_calls = []

    def boundary_start_run(**kwargs):
        start_calls.append((kwargs["group_id"], kwargs["continuation_target_seq"]))
        svc._require_group_worktree(
            kwargs["project_id"], kwargs["module"], kwargs["group_id"],
            "main", locale=kwargs.get("continuation_locale"),
        )

    monkeypatch.setattr(svc, "start_run", boundary_start_run)
    pending = {
        "doc_ref": "p.default.g.0001-T", "target_seq": 1,
        "review_mode": False, "instruction_mode": None, "locale": "ko",
        "issued_to": "worker", "api_base_url": "http://local",
    }
    svc._spawn_auto_resume("p.default.g", pending)
    with pytest.raises(HTTPException) as exc:
        svc._spawn_auto_resume("p.default.g", pending)
    assert exc.value.status_code == 409
    assert exc.value.detail["cause"] == "worktree_missing"
    assert start_calls == [("p.default.g", 1), ("p.default.g", 1)]
    assert len(ensure_calls) == 2


def test_task4_cleanup_interleaving_last_resolution_wins(monkeypatch, tmp_path):
    wt, base, ensure_calls = _wire_gate(monkeypatch, tmp_path, [])
    roots = iter([wt, base])  # initial worktree exists; cleanup wins before final validation
    seen_roots = []
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda *_a, **_k: next(roots))

    def path_sensitive_verdict(_project, _group, root):
        seen_roots.append(root)
        return root == wt

    monkeypatch.setattr(svc, "_is_group_worktree", path_sensitive_verdict)
    with pytest.raises(HTTPException) as exc:
        svc._require_group_worktree("p", "default", "g", "main")
    assert exc.value.status_code == 409
    assert exc.value.detail["cause"] == "worktree_missing"
    assert seen_roots == [wt, base]
    assert len(ensure_calls) == 1


# Tasks 3 and 5 — refusal precedes token/scratch/lease side effects; failures stay structured 409.
def test_task3_gate_precedes_scratch_token_and_run_side_effects(monkeypatch):
    touched = []
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective", lambda _p: {
        "providers": [{"id": "provider"}], "source": "configured", "registered_count": 1,
    })
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _g: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "acquire",
                        lambda **_k: touched.append("lease") or {"state": "acquiring"})
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _d: {"branch": "main"})
    monkeypatch.setattr(svc, "_persist_run_record",
                        lambda _run: touched.append("ai_invoke_run"))
    monkeypatch.setattr(svc, "_require_group_worktree", lambda *_a, **_k: (_ for _ in ()).throw(
        HTTPException(status_code=409, detail={"code": "worktree_unavailable"})))
    monkeypatch.setattr(svc, "_create_scratch", lambda *_a: touched.append("scratch"))
    before = set(svc._runs)
    with pytest.raises(HTTPException):
        svc.start_run(
            project_id="p", module="default", group_id="p.default.g", doc_ref="d",
            action_scope="new", mode="single", continuation_target_seq=None,
            continuation_review_mode=False, continuation_instruction_mode=None,
            continuation_locale="ko", issued_to="u", api_base_url="http://x",
            mention_builder=lambda *_a: touched.append("mention"),
            issue_builder=lambda: touched.append("issue"),
        )
    assert touched == [], "gate refusal must precede lease, durable run, token, and scratch writes"
    assert set(svc._runs) == before


@pytest.mark.parametrize("ensure_result,ensure_exc", [("failed", None), (None, RuntimeError("provision exploded"))])
def test_task5_provision_failures_are_secret_safe_structured_409(monkeypatch, tmp_path, caplog, ensure_result, ensure_exc):
    token = "FLOWGATE_TOKEN_super_secret_0475"
    wt, base, ensure_calls = _wire_gate(
        monkeypatch, tmp_path, [base := tmp_path / "base", base], ensure_result=ensure_result,
        ensure_exc=ensure_exc, provision_error=f"clone failed near {token}", session={"id": 1},
    )
    base.mkdir(exist_ok=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc:
            svc._require_group_worktree("p", "default", "g", "main", locale="en")
    detail = exc.value.detail
    assert exc.value.status_code == 409
    assert (detail["group_id"], detail["cause"]) == ("g", "merge_conflict_open")
    assert detail["provision_error"] == f"clone failed near {token}"
    assert token not in detail["message"]
    assert token not in caplog.text
    assert len(ensure_calls) == 1


def test_task5_provision_error_precedence_without_merge(monkeypatch, tmp_path):
    base = tmp_path / "base"; base.mkdir()
    _wire_gate(monkeypatch, tmp_path, [base, base], ensure_result="failed", provision_error="clone failed")
    with pytest.raises(HTTPException) as exc:
        svc._require_group_worktree("p", "default", "g", "main")
    assert exc.value.detail["cause"] == "provision_failed"


# Tasks 6-8 — _cli_execute's final Popen contract, including managed-scratch fallback and UNC.
def _capture_cli(monkeypatch, run, os_name="posix"):
    seen = {}
    def popen(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        raise OSError("capture only")
    class HostOS:
        name = os_name
        environ = os.environ
    monkeypatch.setattr(process_runner, "os", HostOS)
    monkeypatch.setattr("subprocess.Popen", popen)
    status, _ = svc._cli_execute({"kind": "claude", "cli_command": "claude -p -"}, "prompt", run)
    assert status == "spawn_failed"
    return seen


def _run(scratch, source_marker=True, source_root=None):
    out = {"run_id": "run0475", "scratch_dir": str(scratch), "raw_token": "secret0475", "api_base_url": ""}
    if source_marker:
        out["source_root"] = str(source_root) if source_root is not None else None
    return out


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_task6_existing_local_root_is_absolute_cwd_and_preserves_tree_kill_flags(monkeypatch, tmp_path, os_name):
    root = (tmp_path / "source").resolve(); root.mkdir()
    scratch = (tmp_path / "managed" / "run").resolve(); scratch.mkdir(parents=True)
    seen = _capture_cli(monkeypatch, _run(scratch, source_root=root), os_name)
    assert seen["kwargs"]["cwd"] == str(root)
    assert Path(seen["kwargs"]["cwd"]).is_absolute()
    assert "pushd" not in seen["cmd"]
    assert seen["kwargs"]["shell"] is True
    assert ("creationflags" in seen["kwargs"]) == (os_name == "nt")
    assert ("start_new_session" in seen["kwargs"]) == (os_name != "nt")


@pytest.mark.parametrize("case", ["missing", "file", "none", "absent"])
def test_task7_unavailable_root_uses_managed_run_scratch_and_warns_without_secret(monkeypatch, tmp_path, caplog, case):
    source = None
    if case == "missing":
        source = tmp_path / "does-not-exist"
    if case == "file":
        source = tmp_path / "not-a-directory"
        source.write_text("x")

    managed_storage = (Path(Path.cwd().anchor) / "flowgate-managed-storage-0475").resolve()
    monkeypatch.setattr(svc.db_projects, "get_by_id",
                        lambda _p: {"project_name": "FlowGate"})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root",
                        lambda *_a, **_k: managed_storage)
    made = []
    monkeypatch.setattr(Path, "mkdir",
                        lambda path, **_k: made.append(path.resolve()))
    managed = svc._create_scratch("flowgate", "run0475").resolve()
    expected_root = svc._project_scratch_root("flowgate").resolve()
    assert made == [managed]
    assert managed.parent == expected_root

    run = _run(managed, source_marker=case != "absent", source_root=source)
    with caplog.at_level(logging.WARNING):
        seen = _capture_cli(monkeypatch, run)
    effective_cwd = Path(seen["kwargs"]["cwd"]).resolve()
    os_temp = Path(tempfile.gettempdir()).resolve()
    assert effective_cwd == managed and seen["kwargs"]["cwd"] is not None
    assert expected_root in effective_cwd.parents
    assert os_temp != effective_cwd and os_temp not in effective_cwd.parents
    assert seen["kwargs"]["env"]["FLOWGATE_SCRATCH"] == str(managed)
    assert "running in scratch" in caplog.text
    assert run["raw_token"] not in caplog.text


def test_task8_windows_unc_current_contract_owned_by_wp0004_t2(monkeypatch, tmp_path):
    # WP0004 T#2가 바꿀 대상: cwd=None must intentionally fail when T#2 localizes UNC cwd.
    scratch = (tmp_path / "managed").resolve(); scratch.mkdir()
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if str(path).startswith(r"\\") else original_is_dir(path))
    seen = _capture_cli(monkeypatch, _run(scratch, source_root=r"\\host\share\src"), "nt")
    assert seen["cmd"].startswith(r'pushd "\\host\share\src" &&')
    assert seen["kwargs"]["cwd"] is None