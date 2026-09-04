"""flowgate.default.0520 T0004 -- Test Report locale propagation regression.

NR0003 root cause: the UI's chosen locale never reached test_runs (route -> service -> DB),
so the background TSR-assembly worker -- which runs outside any request context -- always
fell back to assemble_tsr()'s "ko" default. test_d_point_locale_0430.py already pins the
*string* tables (_tsr_content per locale, zero-Korean-leakage). These tests instead pin the
*propagation* path itself: the route's effective-locale choice, validate_and_create_run's
DB write, and -- the part a same-call-stack unit test cannot catch -- the async worker
reading the locale back off the DB row it refetches after the run finishes, not off
whatever object was in scope when the run was admitted.
"""
from __future__ import annotations

import pytest


TS_DOC = {
    "doc_id": "flowgate.default.0520.0099-TS",
    "project_id": "flowgate",
    "branch": "main",
    "module": "default",
    "group_id": "flowgate.default.0520",
    "type_code": "TS",
    "title": "locale propagation TS",
    "owner_id": "owner-1",
    "doc_review_status": "approved",
    "revision_no": 1,
}

CASES = [
    {
        "kind": "case", "case_no": "TC-1", "case_title": "smoke", "result": "pass",
        "exit_code": 0, "duration_ms": 100, "output_tail": "ok",
    }
]


# -- Sec.15 migration parity: test_runs.locale exists and round-trips --------------

def test_migration_099_adds_test_runs_locale_column_and_round_trips(test_db):
    cols = {r["name"] for r in test_db.execute("PRAGMA table_info(test_runs)").fetchall()}
    assert "locale" in cols

    test_db.execute("PRAGMA foreign_keys = OFF")
    test_db.execute(
        "INSERT INTO test_runs (run_id, doc_id, revision_no, status, triggered_via, "
        "runner_id, created_at, locale) "
        "VALUES ('trun_loc', 'd1', 1, 'running', 'ui', 'u1', 'c', 'ja')"
    )
    row = test_db.execute(
        "SELECT locale FROM test_runs WHERE run_id = 'trun_loc'"
    ).fetchone()
    assert row["locale"] == "ja"
    test_db.execute("DELETE FROM test_runs WHERE run_id = 'trun_loc'")
    test_db.execute("PRAGMA foreign_keys = ON")


# -- Sec.5/Sec.18 validate_and_create_run -> insert_run: the run admits with its locale --

def _wire_validate_and_create_run(monkeypatch, tmp_path, *, inserted):
    from modules.flow_gate.services import test_run_service

    src_root = tmp_path / "src"
    src_root.mkdir()
    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(test_run_service.process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(
        test_run_service.storage_paths, "resolve_project_src_root",
        lambda *_a, **_kw: src_root,
    )
    monkeypatch.setattr(
        test_run_service, "_read_doc_content",
        lambda _doc: "\n".join([
            "## Test Cases", "", "### TC-1: smoke", "- cmd: echo hi", "- expect: exits 0",
        ]),
    )
    monkeypatch.setattr(test_run_service.db_test_runs, "get_running_by_doc", lambda _id: None)

    def insert_run(**kwargs):
        inserted.update(kwargs)
        return {
            "run_id": "trun_new", "doc_id": kwargs["doc_id"], "revision_no": kwargs["revision_no"],
            "status": "running", "case_total": len(kwargs["cases"]),
            "setup_total": len(kwargs["setup"]), "teardown_total": len(kwargs["teardown"]),
            "started_at": "2026-09-03T00:00:00+09:00",
        }

    monkeypatch.setattr(test_run_service.db_test_runs, "insert_run", insert_run)
    monkeypatch.setattr(test_run_service, "_emit_started", lambda *_a, **_kw: None)
    return test_run_service


def test_validate_and_create_run_persists_the_effective_locale(monkeypatch, tmp_path):
    inserted = {}
    svc = _wire_validate_and_create_run(monkeypatch, tmp_path, inserted=inserted)

    result = svc.validate_and_create_run(
        doc_id=TS_DOC["doc_id"], runner_id="user-1", triggered_via="ui", locale="en",
    )

    assert result["ok"] is True
    assert inserted["locale"] == "en"


def test_validate_and_create_run_defaults_to_ko_when_no_locale_given(monkeypatch, tmp_path):
    """Legacy/direct unit callers (T0004 Sec.9) still get the prior default."""
    inserted = {}
    svc = _wire_validate_and_create_run(monkeypatch, tmp_path, inserted=inserted)

    svc.validate_and_create_run(doc_id=TS_DOC["doc_id"], runner_id="user-1", triggered_via="ui")

    assert inserted["locale"] == "ko"


# -- Sec.4/Sec.10 route: which locale source wins for each auth path ---------------

class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_ui_session_route_uses_the_x_locale_header(monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {"_is_user_jwt": True, "issued_to": "user-1", "is_admin": False},
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(routes.test_run_service, "user_can_run_tests", lambda *_a, **_kw: True)

    captured = {}
    monkeypatch.setattr(
        routes.test_run_service, "validate_and_create_run",
        lambda **kwargs: captured.update(kwargs) or {"ok": True, "run_id": "trun_x"},
    )

    resp = routes.post_test_run(
        routes.TestRunBody(doc_id=TS_DOC["doc_id"]), _FakeRequest({"x-locale": "en"})
    )

    assert resp.status_code == 202
    assert captured["locale"] == "en"


def test_ui_session_route_falls_back_to_ko_without_the_header(monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {"_is_user_jwt": True, "issued_to": "user-1", "is_admin": False},
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(routes.test_run_service, "user_can_run_tests", lambda *_a, **_kw: True)

    captured = {}
    monkeypatch.setattr(
        routes.test_run_service, "validate_and_create_run",
        lambda **kwargs: captured.update(kwargs) or {"ok": True, "run_id": "trun_x"},
    )

    resp = routes.post_test_run(routes.TestRunBody(doc_id=TS_DOC["doc_id"]), _FakeRequest())

    assert resp.status_code == 202
    assert captured["locale"] == "ko"


def test_repair_token_route_prefers_continuation_locale_over_the_header(monkeypatch):
    """0520 T0004 Sec.4.2/Sec.11: the token's own continuation_locale -- inherited
    hop-to-hop by engine_recipe_service._emit_repair for an unmanned chain -- outranks
    the request header, the same priority every other continuation-token consumer in
    this codebase already uses (inbox_routes.py).
    """
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {
            "action_scope": "test_run", "doc_ref": TS_DOC["doc_id"], "token_id": "tok_1",
            "issued_to": "worker", "continuation_locale": "ja",
        },
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(routes.token_service, "consume", lambda *_a, **_kw: None)

    captured = {}
    monkeypatch.setattr(
        routes.test_run_service, "validate_and_create_run",
        lambda **kwargs: captured.update(kwargs) or {"ok": True, "run_id": "trun_x"},
    )

    resp = routes.post_test_run(
        routes.TestRunBody(doc_id=TS_DOC["doc_id"]), _FakeRequest({"x-locale": "en"})
    )

    assert resp.status_code == 202
    assert captured["locale"] == "ja"
    assert captured["triggered_via"] == "repair_token"


def test_repair_token_route_falls_back_to_the_header_without_a_stored_locale(monkeypatch):
    """Legacy repair tokens (NULL continuation_locale) fall back to the request header."""
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {
            "action_scope": "test_run", "doc_ref": TS_DOC["doc_id"], "token_id": "tok_1",
            "issued_to": "worker", "continuation_locale": None,
        },
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(routes.token_service, "consume", lambda *_a, **_kw: None)

    captured = {}
    monkeypatch.setattr(
        routes.test_run_service, "validate_and_create_run",
        lambda **kwargs: captured.update(kwargs) or {"ok": True, "run_id": "trun_x"},
    )

    resp = routes.post_test_run(
        routes.TestRunBody(doc_id=TS_DOC["doc_id"]), _FakeRequest({"x-locale": "en"})
    )

    assert resp.status_code == 202
    assert captured["locale"] == "en"


# -- Sec.14.6 async boundary: the worker must read locale off the refetched DB row --

def _run(run_id="trun_x", locale=None):
    run = {
        "run_id": run_id, "doc_id": TS_DOC["doc_id"], "revision_no": 1,
        "status": "running", "started_at": "2026-09-03T00:00:00+09:00", "port": 8123,
        "tsr_doc_id": None,
    }
    if locale is not None:
        run["locale"] = locale
    return run


@pytest.fixture
def exec_env(monkeypatch, tmp_path):
    """Drive _execute_run_inner with every case green (mirrors test_tsr_slot_0257.exec_env)."""
    from modules.flow_gate.services import test_run_service as svc

    calls = {"finish": []}
    src_root = tmp_path / "src"
    src_root.mkdir()
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root", lambda *_a, **_kw: src_root)
    monkeypatch.setattr(svc, "_allocate_port", lambda: 8123)
    monkeypatch.setattr(svc, "_scratch_dir", lambda _doc, _run_id: tmp_path / "scratch")
    monkeypatch.setattr(svc.db_test_runs, "set_run_port", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.db_test_runs, "list_cases", lambda _run_id: CASES)
    monkeypatch.setattr(svc, "_execute_setup", lambda *_a, **_kw: (False, None))
    monkeypatch.setattr(svc, "_execute_case", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc, "_execute_teardown", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc, "_finalize_services", lambda _services, _active=None: None)
    monkeypatch.setattr(svc, "_remove_scratch", lambda _path: None)
    monkeypatch.setattr(svc, "_emit_finished", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(svc.test_command_service, "reflect_from_passed_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.engine_recipe_service, "reflect_from_passed_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.db_test_runs, "finish_run", lambda **kwargs: calls["finish"].append(kwargs))
    return {"svc": svc, "calls": calls, "monkeypatch": monkeypatch}


def test_worker_uses_the_locale_refetched_from_the_db_row_not_the_admission_time_object(exec_env):
    """The object handed to _execute_run_inner at admission carries no locale -- only the
    fresh db_test_runs.get_run() read after the run finishes does. A test that only checks
    a locale variable still in scope on the same call stack cannot catch a regression here
    (T0004 Sec.14.6); this one forces the read through the DB refetch.
    """
    captured = {}

    def fake_get_run(_run_id):
        return _run(locale="en")

    def fake_assemble_tsr(doc, run, cases, locale="ko"):
        captured["locale"] = locale
        return "flowgate.default.0520.0100-TSR"

    exec_env["monkeypatch"].setattr(exec_env["svc"].db_test_runs, "get_run", fake_get_run)
    exec_env["monkeypatch"].setattr(exec_env["svc"], "assemble_tsr", fake_assemble_tsr)

    admission_run = _run()
    assert "locale" not in admission_run

    exec_env["svc"]._execute_run_inner(admission_run)

    assert captured["locale"] == "en"
    assert exec_env["calls"]["finish"][0]["status"] == "passed"


def test_worker_falls_back_to_ko_for_a_run_row_with_no_locale(exec_env):
    """Runs created before this column existed keep the prior silent-ko default rather
    than crashing on a missing key (T0004 Sec.9: legacy/direct callers keep the default)."""
    captured = {}

    def fake_assemble_tsr(doc, run, cases, locale="ko"):
        captured["locale"] = locale
        return "flowgate.default.0520.0100-TSR"

    exec_env["monkeypatch"].setattr(exec_env["svc"].db_test_runs, "get_run", lambda _rid: _run())
    exec_env["monkeypatch"].setattr(exec_env["svc"], "assemble_tsr", fake_assemble_tsr)

    exec_env["svc"]._execute_run_inner(_run())

    assert captured["locale"] == "ko"


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_worker_forwards_each_supported_locale_unchanged(exec_env, locale):
    captured = {}

    exec_env["monkeypatch"].setattr(
        exec_env["svc"].db_test_runs, "get_run", lambda _rid, _l=locale: _run(locale=_l)
    )
    exec_env["monkeypatch"].setattr(
        exec_env["svc"], "assemble_tsr",
        lambda doc, run, cases, locale="ko": captured.setdefault("locale", locale),
    )

    exec_env["svc"]._execute_run_inner(_run())

    assert captured["locale"] == locale


# -- Sec.10 inbox worker-token route: rework closing the automated-review rejection -
#
# The rejection on revision 0 (rej_01M1KPPRQ2ZREWWV): inbox_routes.py's _handle_test_run
# is the *other* test_run entrance -- the unmanned continuous-chain hand-off itself (see
# the continuation envelope built right after validate_and_create_run in that function) --
# and it called validate_and_create_run() with no locale argument at all, so every such
# run silently persisted the service's "ko" default regardless of what the token or the
# request carried. These three pin the same effective-locale priority order the repair
# token route above already uses -- token continuation_locale, then X-Locale, then "ko" --
# this time through the real HTTP door (tests/inbox_client.py), not a hand-called function.

def _worker_token_rec(continuation_locale=None):
    return {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "worker-1",
        # register_binding.group_of_doc() derives the expected group straight from the doc
        # id string, the same way test_test_run_chain_0150.py's _chain_token_rec does --
        # this must match TS_DOC's group, not an arbitrary value.
        "group_id": "flowgate.default.0520",
        "action_scope": "test_run",
        "doc_ref": TS_DOC["doc_id"],
        "dry_run_count": 0,
        "continuation_target_seq": None,
        "continuation_review_mode": 0,
        "continuation_locale": continuation_locale,
    }


def _wire_inbox_test_run(monkeypatch, *, continuation_locale=None):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        inbox_routes.token_service, "verify",
        lambda _raw: _worker_token_rec(continuation_locale),
    )
    monkeypatch.setattr(test_run_service, "token_can_run_tests", lambda *_a, **_k: True)
    # _check_context_binding's group axis is read straight from this row (register_binding.
    # group_of_doc), never parsed out of the doc id -- omitting it 403s before locale is
    # ever computed.
    monkeypatch.setattr(
        inbox_routes.db_docs, "get_by_id",
        lambda _id: {"doc_id": _id, "project_id": "flowgate", "group_id": "flowgate.default.0520"},
    )
    captured = {}
    monkeypatch.setattr(
        test_run_service, "validate_and_create_run",
        lambda **kwargs: captured.update(kwargs) or {
            "ok": True, "run_id": "trun_x", "doc_id": TS_DOC["doc_id"], "status": "running",
        },
    )
    monkeypatch.setattr(inbox_routes.token_service, "consume", lambda **_kw: None)
    return captured


def test_inbox_worker_token_prefers_continuation_locale_over_the_header(monkeypatch):
    from inbox_client import post_inbox

    captured = _wire_inbox_test_run(monkeypatch, continuation_locale="ja")

    resp = post_inbox(
        {"action": "test_run", "project": "flowgate", "doc_id": TS_DOC["doc_id"]},
        headers={"x-locale": "en"},
    )

    assert resp.status_code == 202
    assert captured["locale"] == "ja"


def test_inbox_worker_token_falls_back_to_the_header_without_a_stored_locale(monkeypatch):
    from inbox_client import post_inbox

    captured = _wire_inbox_test_run(monkeypatch, continuation_locale=None)

    resp = post_inbox(
        {"action": "test_run", "project": "flowgate", "doc_id": TS_DOC["doc_id"]},
        headers={"x-locale": "en"},
    )

    assert resp.status_code == 202
    assert captured["locale"] == "en"


def test_inbox_worker_token_defaults_to_ko_with_no_locale_source_at_all(monkeypatch):
    from inbox_client import post_inbox

    captured = _wire_inbox_test_run(monkeypatch, continuation_locale=None)

    resp = post_inbox(
        {"action": "test_run", "project": "flowgate", "doc_id": TS_DOC["doc_id"]},
    )

    assert resp.status_code == 202
    assert captured["locale"] == "ko"
