"""flowgate.default.0400 T0006 (R0001 / M0005): ContinuousWorkDialog's 기본 설정 탭 gained a
시간 section — the previously-fixed per-hop budget (HOP_TIMEOUT_SEC, always 60 minutes) is now
a user pick from a small fixed list (30/45/60/90/120/180/240 minutes), forwarded as
continuation_step_timeout_sec.

0446 T0010 (NR0003 R5) REWRITES that contract, and this file's docstring with it. The pick is
no longer continuous-only. A rejection rework was pinned to exactly 3600 seconds by the
single-run formula min(RUN_TIMEOUT_BASE_SEC × max(1, docs_target), RUN_TIMEOUT_CAP_SEC) — a
single run's docs_target is 1 and the max(1, …) floor holds it there — so 264 of 264 measured
rework runs got precisely one hour, two of them were cut at the 3603-second boundary and a
third spent 59.6 minutes and gave up. `continuation_step_timeout_sec` is therefore read ABOVE
the mode branch in `_resolve_timeout_sec` now: an in-range explicit pick is THIS run's budget
whatever the mode. Two assertions in this file consequently flipped on purpose and were SPLIT
rather than deleted (TestResolveTimeoutSec and TestStartRunEndToEnd below).

Covers:
  * _resolve_timeout_sec: an in-range pick wins in EITHER mode and outranks target_to_end;
    None / out-of-range falls back to that mode's own default — HOP_TIMEOUT_SEC for continuous,
    and the untouched min(RUN_TIMEOUT_BASE_SEC × max(1, docs_target), RUN_TIMEOUT_CAP_SEC)
    formula for single.
  * POST /api/v1/ai-invoke/start: out-of-range values are rejected 422 (mode- and
    scope-independent), an in-range value reaches ai_invoke_service.start_run unchanged — for
    a continuous 'new' start and for a single 'rework' start alike — and omitting the field
    keeps working exactly as before (backward compatible default).
  * start_run stores the pick on the run dict, the run's timeout_sec/deadline_at reflect it end
    to end, and the value written to the `ai_invoke_runs.timeout_sec` COLUMN is the picked one
    (real subprocess, same shape as test_ai_invoke_continuation_note_0346.py).
  * _retry_eligible names its budget gate: a run with less than RETRY_MIN_REMAINING_SEC left
    blocks with retry_block_reason='budget_exhausted' and that reason reaches the persisted
    stop_reason sentence — the hole T0008 §3-6 / TR0009 §7-4 explicitly left to R5.
  * _maybe_auto_resume_hop / _spawn_auto_resume: the pick rides the run forward across a hop
    re-spawn — the "first hop only" shape is the easiest way to get a session-scoped field
    wrong (0317 T0013 재발 방지 shape, mirrored from the provider-override/note coverage).

Pause/resume persistence (the paused-chain row round trip) is covered in
test_ai_invoke_pause_resume_0252.py, which already owns that fixture and pattern.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

PY = sys.executable
ROOT_DOC = "flowgate.default.0400.0001-R"
GROUP = "flowgate.default.0400"


# ── _resolve_timeout_sec (unit) ───────────────────────────────────────────────

class TestResolveTimeoutSec:
    def test_in_range_pick_wins_for_continuous(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, 14400) == 14400

    @pytest.mark.parametrize("pick", [30 * 60, 45 * 60, 60 * 60, 90 * 60, 120 * 60, 180 * 60, 240 * 60])
    def test_every_dialog_option_is_accepted(self, pick):
        assert svc._resolve_timeout_sec("continuous", 1, False, pick) == pick

    def test_none_falls_back_to_hop_timeout(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, None) == svc.HOP_TIMEOUT_SEC

    def test_out_of_range_falls_back_to_hop_timeout(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, 60) == svc.HOP_TIMEOUT_SEC
        assert svc._resolve_timeout_sec("continuous", 1, False, 999999) == svc.HOP_TIMEOUT_SEC

    # ── 0446 T0010 §4-1: the one deliberate flip ─────────────────────────────
    # This slot used to hold ONE test, `test_single_mode_ignores_the_pick`, asserting that "a
    # stray continuous-only field on a single request must not perturb it". R5 makes that
    # false on purpose: a single rejection rework is exactly the run that needs to be given
    # more than the formula's 3600 seconds. The assertion is SPLIT, not deleted — (a) keeps
    # proving the no-pick formula is untouched, (b) states the new contract.
    def test_single_without_a_pick_keeps_the_old_formula(self):
        # (a) The single-run formula (RUN_TIMEOUT_BASE_SEC × docs_target, capped) is untouched.
        # 3600 was never the defect; "cannot choose" was.
        assert svc._resolve_timeout_sec("single", 2, False, None) == min(
            svc.RUN_TIMEOUT_BASE_SEC * 2, svc.RUN_TIMEOUT_CAP_SEC
        )
        assert svc._resolve_timeout_sec("single", 1, False, None) == 3600
        assert svc._resolve_timeout_sec("single", 99, False, None) == svc.RUN_TIMEOUT_CAP_SEC

    def test_single_with_an_in_range_pick_uses_the_pick(self):
        # (b) The new contract. Formerly `!= 14400`.
        assert svc._resolve_timeout_sec("single", 2, False, 14400) == 14400
        assert svc._resolve_timeout_sec("single", 1, False, 14400) == 14400

    def test_single_out_of_range_pick_falls_back_to_the_formula(self):
        # The bounds are the SAME pair the route validates against, so an out-of-range value
        # can only reach the engine by bypassing the route; when it does, it is ignored.
        assert svc._resolve_timeout_sec("single", 1, False, 60) == 3600
        assert svc._resolve_timeout_sec("single", 1, False, 999999) == 3600

    @pytest.mark.parametrize("pick", [30 * 60, 45 * 60, 60 * 60, 90 * 60, 120 * 60, 180 * 60, 240 * 60])
    def test_every_dialog_option_is_accepted_for_single_too(self, pick):
        # All seven options AiInvokeDialog offers a rework, at docs_target=1 (a single run).
        assert svc._resolve_timeout_sec("single", 1, False, pick) == pick

    def test_pick_outranks_target_to_end(self):
        # T0010 §4-2 #2 — ORDER. The explicit-pick check sits ABOVE the mode branch, so it also
        # sits above the `target_to_end ⇒ RUN_TIMEOUT_CAP_SEC` line.
        #
        # In production a single run can never actually have target_to_end=True: start_run
        # computes it as `mode == "continuous" and continuation_target_seq == -1`
        # (ai_invoke_service.py, the baseline_seq block), so it is structurally False for
        # every single run. This test pins the ORDER anyway rather than leaning on that
        # accident — if the pick were moved back below the mode branch, this is what fails.
        assert svc._resolve_timeout_sec("single", 0, True, 1800) == 1800
        assert svc._resolve_timeout_sec("single", 0, True, None) == svc.RUN_TIMEOUT_CAP_SEC

    def test_target_to_end_still_honours_a_continuous_pick(self):
        # target_to_end only matters for the single-run formula below — a continuous run is
        # always ONE hop (0317 TR0011), pre-decision or not, so its budget is still the
        # per-hop pick, never RUN_TIMEOUT_CAP_SEC.
        assert svc._resolve_timeout_sec("continuous", 0, True, 1800) == 1800
        assert svc._resolve_timeout_sec("continuous", 0, True, None) == svc.HOP_TIMEOUT_SEC


# ── POST /api/v1/ai-invoke/start (route-level) ────────────────────────────────

_ITEMS = [
    {"id": 1, "item_seq": 1, "type": "T", "label": "작업지시",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 2, "item_seq": 2, "type": "TR", "label": "작업레포트",
     "result_doc_id": None, "result_doc_review_status": None},
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: list(_ITEMS))
    return TestClient(app, raise_server_exceptions=False)


def _body(**over):
    body = {
        "project": "flowgate", "module": "default", "group": "0400",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 2,
    }
    body.update(over)
    return body


def _post(client, **over):
    return client.post("/api/v1/ai-invoke/start", json=_body(**over),
                       headers={"Authorization": "Bearer tok"})


class TestStartRouteValidation:
    def test_below_minimum_is_rejected_422(self, client, monkeypatch):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run",
                            lambda **kw: pytest.fail("must not reach start_run"))
        resp = _post(client, continuation_step_timeout_sec=60)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_step_timeout_sec"

    def test_above_maximum_is_rejected_422(self, client, monkeypatch):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run",
                            lambda **kw: pytest.fail("must not reach start_run"))
        resp = _post(client, continuation_step_timeout_sec=999999)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_step_timeout_sec"

    def test_in_range_value_reaches_start_run(self, client, monkeypatch):
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_1", "status": "running"}
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post(client, continuation_step_timeout_sec=14400)
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] == 14400

    def test_omitted_field_defaults_to_none_and_still_starts(self, client, monkeypatch):
        # Backward compatible: an older client (or any non-ContinuousWorkDialog entry point)
        # that never sends this field must keep working exactly as before.
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_1", "status": "running"}
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post(client)
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] is None


class TestStartRouteRework:
    """0446 T0010 §4-2 #3: the same four route behaviours, on the entry point R5 is about.

    The route's range check never looked at `mode` or `action_scope` — ai_invoke_routes has a
    bare `if body.continuation_step_timeout_sec is not None and not (MIN <= … <= MAX)` — and
    the start_run call forwards the field unconditionally. These cases prove that by exercise
    rather than by reading the source, so a future narrowing of either cannot slip past.
    """

    def _rework(self, **over):
        body = {"project": "flowgate", "module": "default", "group": "0400",
                "doc_ref": ROOT_DOC, "action_scope": "rework", "mode": "single"}
        body.update(over)
        return body

    def test_in_range_value_reaches_start_run_for_a_rework(self, client, monkeypatch):
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_1", "status": "running"}
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = client.post("/api/v1/ai-invoke/start", headers={"Authorization": "Bearer tok"},
                           json=self._rework(continuation_step_timeout_sec=14400))
        assert resp.status_code == 200
        # The route TRANSLATES the UI scope before calling the engine: _TOKEN_SCOPE maps
        # "rework" -> "edit" (the inbox only honours new/edit/review/workflow_decide; what
        # differs per scope is the mention). That is why T0010 §4-2 #4 asks for the
        # end-to-end case to be started with action_scope='edit', and why T0008's
        # `_scope_oracle_retry_open` narrows on 'edit'. Asserted, not assumed.
        assert captured["action_scope"] == "edit"
        assert captured["mode"] == "single"
        assert captured["continuation_step_timeout_sec"] == 14400

    @pytest.mark.parametrize("bad", [60, 1799, 14401, 999999])
    def test_out_of_range_is_422_for_a_rework_too(self, client, monkeypatch, bad):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run",
                            lambda **kw: pytest.fail("must not reach start_run"))
        resp = client.post("/api/v1/ai-invoke/start", headers={"Authorization": "Bearer tok"},
                           json=self._rework(continuation_step_timeout_sec=bad))
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_step_timeout_sec"

    @pytest.mark.parametrize("edge", [1800, 14400])
    def test_the_two_edges_are_inclusive(self, client, monkeypatch, edge):
        # 30 min and 240 min — the first and last option the dialog offers — must not be
        # off-by-one rejections. 1799/14401 are covered as 422 above.
        captured = {}
        monkeypatch.setattr(
            ai_invoke_routes.ai_invoke_service, "start_run",
            lambda **kw: captured.update(kw) or {"run_id": "aiv_1", "status": "running"},
        )
        resp = client.post("/api/v1/ai-invoke/start", headers={"Authorization": "Bearer tok"},
                           json=self._rework(continuation_step_timeout_sec=edge))
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] == edge

    def test_omitted_field_still_reaches_start_run_as_none(self, client, monkeypatch):
        # Backward compatible on this scope too: a rework started from an older bundle sends
        # nothing and keeps the server's own default.
        captured = {}
        monkeypatch.setattr(
            ai_invoke_routes.ai_invoke_service, "start_run",
            lambda **kw: captured.update(kw) or {"run_id": "aiv_1", "status": "running"},
        )
        resp = client.post("/api/v1/ai-invoke/start", headers={"Authorization": "Bearer tok"},
                           json=self._rework())
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] is None


# ── start_run end-to-end (real subprocess, same shape as the 0346 note tests) ─

def _provider(pid="aip_test01", cmd=None):
    return {
        "id": pid, "name": "cli-1", "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd,
        "api_base_url": None, "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakeWfseq:
    def __init__(self, head_item_seq=1, items=None):
        self.sequence = {"id": 1}
        self.head_item_seq = head_item_seq
        self.items = items if items is not None else list(_ITEMS)

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return list(self.items)

    def get_effective_head(self, seq_id):
        return next((dict(i) for i in self.items if i["item_seq"] == self.head_item_seq), None)


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    wfseq = FakeWfseq()
    chain_holder = {"providers": [], "source": "system", "registered_count": 0}

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 2)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain_holder}
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {
            "raw_token": "tok_raw_test", "token_id": "tok_20260809_000001",
            "expires_at": "2026-08-10T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        },
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda pid, branch, *, group_id: src_root
    )
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return {"wfseq": wfseq, "chain": chain_holder, "tmp": tmp_path}


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _start(env, *, step_timeout_sec=None, target_seq=2):
    cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
    env["chain"]["providers"] = [_provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=ROOT_DOC,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_review_mode=False,
        continuation_instruction_mode="auto_approved",
        continuation_locale="ko",
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## 지시\n문서를 작성하세요.\n",
        continuation_step_timeout_sec=step_timeout_sec,
    )


def _start_single(env, *, action_scope="rework", step_timeout_sec=None):
    """0446 T0010: the SINGLE-run shape — the rejection rework R5 is actually about.

    Same fixture and same real subprocess as `_start` above; only mode/action_scope differ,
    and no continuation_* chain arguments are supplied because a single run has none. Note
    `target_to_end` is structurally False here: start_run derives it as
    `mode == "continuous" and continuation_target_seq == -1`.
    """
    cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
    env["chain"]["providers"] = [_provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    return svc.start_run(
        project_id="flowgate", module="default", group_id=GROUP,
        doc_ref=ROOT_DOC, action_scope=action_scope, mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## 지시\n문서를 작성하세요.\n",
        continuation_step_timeout_sec=step_timeout_sec,
    )


class TestStartRunEndToEnd:
    def test_picked_budget_becomes_the_runs_timeout_sec(self, run_env):
        res = _start(run_env, step_timeout_sec=10800)
        assert res["timeout_sec"] == 10800
        run = _wait_finished(res["run_id"])
        assert run["timeout_sec"] == 10800
        assert run["continuation_step_timeout_sec"] == 10800

    def test_no_pick_falls_back_to_hop_timeout_sec(self, run_env):
        res = _start(run_env, step_timeout_sec=None)
        assert res["timeout_sec"] == svc.HOP_TIMEOUT_SEC
        run = _wait_finished(res["run_id"])
        assert run["continuation_step_timeout_sec"] is None

    # ── 0446 T0010 §4-1: the second deliberate flip ──────────────────────────
    # This slot used to hold `test_pick_is_ignored_on_a_single_run`, whose two assertions
    # (`res["timeout_sec"] != 14400` and `run["continuation_step_timeout_sec"] is None`) were
    # the end-to-end twin of the unit assertion split above. Both are now false BY DESIGN
    # (§3-2 and §3-3), so the test is split the same way rather than deleted.

    def test_single_run_honours_an_in_range_pick_end_to_end(self, run_env):
        # T0010 §4-2 #4. action_scope='edit' — the scope AiInvokeDialog's sibling entry points
        # use, and one whose token cannot register a document (docs_target is pinned to 0), so
        # the pick is doing all the work here.
        res = _start_single(run_env, action_scope="edit", step_timeout_sec=14400)
        assert res["timeout_sec"] == 14400
        # deadline_at = started_at + timeout_sec, so it must be ~4h out, not ~1h.
        started = datetime.fromisoformat(res["started_at"])
        assert datetime.fromisoformat(res["deadline_at"]) - started == timedelta(seconds=14400)
        run = _wait_finished(res["run_id"])
        assert run["timeout_sec"] == 14400
        # §3-3: a single run no longer forgets where its budget came from.
        assert run["continuation_step_timeout_sec"] == 14400

    def test_single_run_pick_is_written_to_the_ai_invoke_runs_row(self, run_env, monkeypatch):
        # T0010 §4-2 #4, second half: "saved" is the claim, so assert the SAVE. `timeout_sec`
        # is one of db/ai_invoke_runs.py's bound columns and _persist_run_record binds
        # run["timeout_sec"] into it at finalize. The start response alone would not prove it.
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        rows: list[dict] = []
        monkeypatch.setattr(db_runs, "upsert", lambda row: rows.append(dict(row)))

        res = _start_single(run_env, action_scope="rework", step_timeout_sec=10800)
        _wait_finished(res["run_id"])
        assert len(rows) == 1
        assert rows[0]["run_id"] == res["run_id"]
        assert rows[0]["mode"] == "single"
        assert rows[0]["timeout_sec"] == 10800

    def test_single_rework_without_a_pick_is_still_exactly_3600(self, run_env, monkeypatch):
        # T0010 §4-2 #5 — the positive control. Proving the picker works means nothing unless
        # the un-picked path is pinned too: min(3600 × max(1, docs_target=1), 14400) = 3600,
        # which is the number NR0003 measured on 264 of 264 rework runs.
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        rows: list[dict] = []
        monkeypatch.setattr(db_runs, "upsert", lambda row: rows.append(dict(row)))

        res = _start_single(run_env, action_scope="rework", step_timeout_sec=None)
        assert res["timeout_sec"] == 3600 == svc.RUN_TIMEOUT_BASE_SEC
        run = _wait_finished(res["run_id"])
        assert run["timeout_sec"] == 3600
        assert run["continuation_step_timeout_sec"] is None
        assert rows[0]["timeout_sec"] == 3600

    @pytest.mark.parametrize("pick", [1800, 2700, 3600, 5400, 7200, 10800, 14400])
    def test_every_dialog_option_survives_a_real_single_start(self, run_env, pick):
        # The seven values AiInvokeDialog's select can emit (30/45/60/90/120/180/240 minutes),
        # each through the real start path rather than only through _resolve_timeout_sec.
        res = _start_single(run_env, action_scope="rework", step_timeout_sec=pick)
        assert res["timeout_sec"] == pick
        assert _wait_finished(res["run_id"])["continuation_step_timeout_sec"] == pick

    def test_single_run_writes_no_paused_chain_row(self, run_env, monkeypatch):
        # T0010 §4-2 #7 / §3-3: carrying the pick on a single run must NOT open any
        # continuous-chain path. _apply_stop_row returns on `mode != "continuous"` at its first
        # line, so nothing reaches ai_invoke_paused_chains — re-pinned here because §3-3 is
        # exactly the kind of change that could have leaked one.
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        touched: list[str] = []
        monkeypatch.setattr(db_paused, "upsert", lambda **kw: touched.append("upsert"))
        monkeypatch.setattr(db_paused, "delete_by_group", lambda g: touched.append("delete"))
        monkeypatch.setattr(db_paused, "get_by_group", lambda g: touched.append("get") or None)

        res = _start_single(run_env, action_scope="rework", step_timeout_sec=14400)
        _wait_finished(res["run_id"])
        assert touched == []


# ── auto-resume handoff (mirrors test_ai_invoke_continuation_note_0346's shape) ─

class TestStepTimeoutRunDictAndAutoResume:
    @pytest.fixture(autouse=True)
    def _clean_registries(self):
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()
        yield
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()

    def test_maybe_resume_carries_step_timeout_forward(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": GROUP, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": None,
            "continuation_note_overrides": None,
            "continuation_default_note": None,
            "continuation_step_timeout_sec": 14400,
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        _g, pending = calls[0]
        assert pending["step_timeout_sec"] == 14400

    def test_spawn_passes_step_timeout_to_start_run(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
            "step_timeout_sec": 14400,
        })
        assert captured["continuation_step_timeout_sec"] == 14400

    def test_spawn_without_a_pick_forwards_none(self, monkeypatch):
        # A pending hop queued before this feature (or with no pick made) must not crash
        # _spawn_auto_resume — pending.get() degrades cleanly to None.
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        assert captured["continuation_step_timeout_sec"] is None


# ── migration 079 (schema) ─────────────────────────────────────────────────────

def test_migration_079_adds_step_timeout_column(test_db):
    cols = {
        row["name"]: row
        for row in test_db.execute("PRAGMA table_info(ai_invoke_paused_chains)").fetchall()
    }
    assert "continuation_step_timeout_sec" in cols
    assert cols["continuation_step_timeout_sec"]["notnull"] == 0


# ── 0446 T0010 §3-4: the budget gate finally has a name ───────────────────────

def _budget_run(*, timeout_sec: int, elapsed_sec: float) -> dict:
    """A run that has already spent `elapsed_sec` of a `timeout_sec` budget.

    `_remaining_sec` is `run["timeout_sec"] - (time.monotonic() - run["started_mono"])`, so a
    started_mono in the past is exactly how much of the budget is gone. Everything else here is
    the shape `_retry_eligible` walks BEFORE the budget line: a continuous hop that exited
    cleanly with nothing to show and one attempt used.
    """
    return {
        "run_id": "aiv_budget", "group_id": "flowgate.default.0446", "mode": "continuous",
        "doc_ref": ROOT_DOC, "action_scope": "new",
        "end_reason": "exited", "cancel_event": None, "pause_requested": False,
        "completion_oracle": None, "docs_target": 1, "docs_reached": 0,
        "attempts_used": 1, "outcome": "none",
        "continuation_selected_provider_name": "cli-1",
        "timeout_sec": timeout_sec,
        "started_mono": time.monotonic() - elapsed_sec,
    }


@pytest.fixture
def no_pending_question(monkeypatch):
    # The question_pending probe sits ABOVE the budget line on purpose (T0008's "a Q stop is
    # not a failure" contract). Silence it so these cases reach the gate under test; the
    # ordering itself is owned by test_ai_invoke_no_output_retry_0359.py.
    monkeypatch.setattr(svc, "_has_pending_question", lambda doc_ref: False)
    monkeypatch.setattr(svc, "peek_auto_resume", lambda group_id: None)


class TestBudgetExhaustedReason:
    def test_a_spent_budget_blocks_the_retry_and_says_so(self, no_pending_question):
        # T0010 §4-2 #6. 3600-second budget, 3400 spent ⇒ 200 left, under
        # RETRY_MIN_REMAINING_SEC (300). Before this change the gate returned False silently.
        run = _budget_run(timeout_sec=3600, elapsed_sec=3400)
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "budget_exhausted"

    def test_the_reason_reaches_the_persisted_stop_reason_sentence(self, no_pending_question):
        # `retry_block_reason` has no column of its own; `stop_reason` does, so the reason
        # rides the existing no_output_exhausted tail (T0008 §3-6 wrote that comment, this
        # makes it true). One line, exactly as it will appear on the record and the card.
        run = _budget_run(timeout_sec=3600, elapsed_sec=3400)
        svc._retry_eligible(run)
        text = svc._stop_reason_text("no_output_exhausted", run)
        assert "No further attempt was opened: budget_exhausted." in text
        assert text == ('"cli-1" produced no document in 1 attempts. '
                        "The chain stopped without switching to another provider."
                        " No further attempt was opened: budget_exhausted.")

    def test_no_new_stop_code_was_invented(self):
        # §3-4 forbids one. budget_exhausted is a REASON carried inside no_output_exhausted.
        assert "budget_exhausted" not in svc.RESUMABLE_STOP_CODES
        assert "budget_exhausted" not in svc.ENGINE_NOTIFY_STOP_CODES

    def test_the_59_6_minute_run_now_gets_its_retry(self, no_pending_question):
        # NR0003 §2's third casualty: a run that used 3576 seconds (59.6 min) and stopped
        # because that was the whole hour it had. On the 14400-second budget this T makes
        # choosable, the same elapsed time leaves 10824 seconds and the gate does NOT block.
        run = _budget_run(timeout_sec=14400, elapsed_sec=3576)
        assert svc._remaining_sec(run) > svc.RETRY_MIN_REMAINING_SEC
        assert svc._retry_eligible(run) is True
        assert "retry_block_reason" not in run

    def test_the_same_59_6_minutes_on_the_old_fixed_hour_is_blocked(self, no_pending_question):
        # The contrast that makes the case above mean something: identical elapsed time,
        # identical run, only the budget differs — 3600 (what every rework used to get)
        # leaves 24 seconds and blocks.
        run = _budget_run(timeout_sec=3600, elapsed_sec=3576)
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "budget_exhausted"

    def test_question_pending_still_outranks_the_budget_gate(self, monkeypatch):
        # §3-4: the order must not change. A run that is BOTH out of budget and waiting on a
        # human answer keeps the question_pending name — that stop is not a failure.
        monkeypatch.setattr(svc, "_has_pending_question", lambda doc_ref: True)
        monkeypatch.setattr(svc, "peek_auto_resume", lambda group_id: None)
        run = _budget_run(timeout_sec=3600, elapsed_sec=3400)
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "question_pending"
