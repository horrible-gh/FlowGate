"""flowgate.default.0481 T0010 rev5 (반려 #1) — the start response's run identity.

"충돌해결에서 AI호출 했더니 아무것도 안하고 가만히 있음... 뭔가 표시되는것도 없고."

The browser can only draw a conflict run in the dialog that started it when the run entry
carries `action_scope='resolve_conflict'` (client `isScreenOwnedRun`); an entry without it
is an ordinary group run, which MainPanel answers by covering the document column — taking
the dialog down. Until this revision that scope reached the browser from exactly two places,
both of them AFTER the press:

  * `ai_invoke_started`, broadcast by the worker THREAD, i.e. some time after the POST
    returned (worker._worker), and
  * `GET /ai-invoke/{run_id}`, the 5-second poll (diagnostics.get_status, pinned by
    test_ai_invoke_status_identity_0481.py).

So the only payload that exists at the moment of the press — the start response itself —
was the one that omitted them, and a screen that adopted it produced a scope-less entry.
The window between the press and the first frame therefore had to stay blank. This pins the
third place. See also test_ai_invoke_status_identity_0481.py: ship them from all three or
from none.
"""
from __future__ import annotations

import os
import sys
import time
import unittest.mock as mock
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "test2.default.0005"
MERGE_ID = 6


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The collaborators start_run touches, faked — same shape as 0268 T0004's fixture."""
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 5)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, "source": "test",
                     "providers": [{"id": "p1", "name": "Claude Haiku 4.5", "exec_type": "cli"}]},
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {"raw_token": "tok_raw_test", "token_id": "tok_20260908_000001",
                      "expires_at": "2026-09-09T00:00:00+00:00",
                      "scratch_dir": str(tmp_path / "tokwork")},
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return True


def _start(action_scope="resolve_conflict", merge_id=MERGE_ID, group_id=GROUP):
    """Start a run whose provider does nothing, and return the START RESPONSE."""
    with mock.patch.object(svc, "_cli_execute", side_effect=lambda p, prompt, run: ("started_ok", None)):
        response = svc.start_run(
            project_id="test2",
            module="default",
            group_id=group_id,
            doc_ref="",
            action_scope=action_scope,
            mode="single",
            continuation_target_seq=None,
            continuation_review_mode=False,
            continuation_instruction_mode=None,
            continuation_locale=None,
            issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\nresolve the conflict\n",
            merge_id=merge_id,
        )
        # Let the worker thread land so the fake provider is not still running at teardown.
        for _ in range(500):
            record = svc.get_run_record(response["run_id"])
            if record and record["status"] == "finished":
                break
            time.sleep(0.02)
    return response


def test_start_response_carries_the_scope_the_dialog_gates_on(world):
    response = _start()

    # The three keys the browser needs at press time, none of which the start response
    # used to have. action_scope is the one that decides screen ownership.
    assert response["action_scope"] == "resolve_conflict"
    assert response["project_id"] == "test2"
    assert response["merge_id"] == MERGE_ID
    # …without moving the identity it already shipped.
    assert response["group_id"] == GROUP
    assert response["run_id"] == response["run_id"]
    assert response["status"] == "running"


def test_start_response_matches_the_live_status_for_the_same_run(world):
    response = _start()
    status = svc.get_status(response["run_id"])

    # The screen re-reads this record every 5 seconds. If the two disagreed on the scope,
    # the dialog would appear at the press and then be torn down on the first poll.
    for key in ("group_id", "project_id", "action_scope"):
        assert response[key] == status[key], key


def test_a_project_scoped_run_reports_both_of_its_identities(world):
    # resolve_base_dirty lives under a synthetic `<project>.none.0000` group but its card is
    # filed under `project:<id>` — only derivable when both keys travel together.
    response = _start(action_scope="resolve_base_dirty", merge_id=None,
                      group_id="test2.none.0000")

    assert (response["project_id"], response["action_scope"]) == ("test2", "resolve_base_dirty")


def test_merge_id_is_absent_for_a_scope_that_has_no_merge(world):
    response = _start(action_scope="resolve_base_dirty", merge_id=None,
                      group_id="test2.none.0000")

    assert response["merge_id"] is None
