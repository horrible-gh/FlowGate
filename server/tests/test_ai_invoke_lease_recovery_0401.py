"""AI-run lease recovery after a server restart, and the manual unlock path (0401 T0004).

B0001 reported a group stuck for 4h05m with no way out: an AI run's group lease lives in
the durable `group_ai_leases` table, but the only code that ever releases one runs inside
the SAME process that acquired it. A restart mid-run kills the process and leaves the
lease behind; NR0003 read the live database and found exactly this. This file pins the
two fixes NR0003 §6 asked for:

  * `group_ai_leases.reclaim_orphaned` / `ai_invoke_service.startup_recover_leases` --
    every lease still on the table when a process boots is definitionally dead (that
    process's own `_runs` registry starts empty), so it is reclaimed and given an
    `orphaned_by_restart` end record.
  * `ai_invoke_service.is_run_live` / `force_release_group_lease` / the `/leases` routes
    -- a human-usable escape hatch that never needs a restart, and shares its "is this
    run actually alive" answer with the 423 rejection body (`mutation_policy._locked`)
    so the screen and the server never again tell two different stories.

Also pinned: the run_id continuity fix (T0004 작업 7) -- the in-memory run-id counter
used to reset to 1 every restart, so a fresh process could reissue a serial an earlier
process already used that day and silently overwrite its `ai_invoke_runs` row at the
next finalize.

All of this runs through group_ai_leases' `_memory` fallback (PYTEST_CURRENT_TEST forces
`_using_memory()` True, same as every other direct-service test in this suite) and a
dict-backed fake for `ai_invoke_runs` (mirrors test_ai_invoke_run_lookup_0359's harness).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402

GROUP = "flowgate.default.0401"
OTHER_GROUP = "flowgate.default.9999"
PROJECT = "flowgate"
RUN_ID = "aiv_20260810_000099"
PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


def _seed_lease(group_id: str = GROUP, run_id: str = RUN_ID, acquired_at: str | None = None) -> dict:
    """Create a real lease through the public API (so `_sync_test_scope` runs the normal
    way), then optionally back-date it -- `acquired_at`/`FUTURE` are chosen far enough
    from "now" that second-precision `now_iso()` timing can never make a test flaky."""
    db_leases.acquire(
        group_id=group_id, project_id=PROJECT, run_id=run_id,
        chain_id=run_id, action_scope="new", worker_identity="usr_ai",
    )
    if acquired_at is not None:
        db_leases._memory[group_id]["acquired_at"] = acquired_at
    return db_leases._memory[group_id]


class FakeRunsStore:
    """Dict-backed stand-in for db/ai_invoke_runs.py (same contract as
    test_ai_invoke_run_lookup_0359's FakeRunsStore -- upsert/max_serial_for_date added
    for what this file needs)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get(self, run_id):
        row = self.rows.get(run_id)
        return dict(row) if row else None

    def upsert(self, row):
        self.rows[row["run_id"]] = dict(row)

    def max_serial_for_date(self, date_str):
        highest = 0
        prefix = f"aiv_{date_str}_"
        for run_id in self.rows:
            if run_id.startswith(prefix):
                try:
                    highest = max(highest, int(run_id.rsplit("_", 1)[-1]))
                except ValueError:
                    continue
        return highest


@pytest.fixture
def fake_runs(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    store = FakeRunsStore()
    monkeypatch.setattr(db_runs, "get", store.get)
    monkeypatch.setattr(db_runs, "upsert", store.upsert)
    monkeypatch.setattr(db_runs, "max_serial_for_date", store.max_serial_for_date)
    return store


@pytest.fixture(autouse=True)
def clean_state():
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()
    # Reset BOTH: leaving _run_counter at whatever a previous test (in this file or
    # anywhere earlier in the session) left it would make the exact-serial assertions
    # below depend on execution order instead of on what this test itself seeds.
    svc._run_counter = 0
    svc._run_counter_floor_date = None
    yield
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()
    svc._run_counter = 0
    svc._run_counter_floor_date = None


# ── group_ai_leases.reclaim_orphaned ───────────────────────────────────────────────────

def test_reclaim_orphaned_clears_leases_before_cutoff():
    _seed_lease(acquired_at=PAST)
    victims = db_leases.reclaim_orphaned("2026-08-10T00:00:00+00:00")
    assert [v["group_id"] for v in victims] == [GROUP]
    assert GROUP not in db_leases._memory


def test_reclaim_orphaned_leaves_leases_acquired_after_cutoff():
    """Scenario 2 of the report's own list: a lease acquired AFTER the cutoff (another
    process, still admitting) must not be touched."""
    _seed_lease(acquired_at=FUTURE)
    victims = db_leases.reclaim_orphaned("2026-08-10T00:00:00+00:00")
    assert victims == []
    assert GROUP in db_leases._memory


def test_reclaim_orphaned_ignores_expiry_unlike_recover_expired():
    """The whole point of this function: it must reclaim a lease LONG before its own
    4h05m TTL would ever let recover_expired touch it."""
    _seed_lease(acquired_at=PAST)
    assert db_leases._memory[GROUP]["expires_at"] > "2026-08-10T00:00:00+00:00"  # not expired
    victims = db_leases.reclaim_orphaned("2026-08-10T00:00:00+00:00")
    assert len(victims) == 1


# ── ai_invoke_service.startup_recover_leases (작업 1) ──────────────────────────────────

def test_startup_recover_leases_zeroes_active_leases_and_records_end_reason(fake_runs):
    _seed_lease(acquired_at=PAST)
    count = svc.startup_recover_leases()
    assert count == 1
    assert GROUP not in db_leases._memory
    row = fake_runs.rows[RUN_ID]
    assert row["end_reason"] == "orphaned_by_restart"
    assert row["group_id"] == GROUP
    assert row["project_id"] == PROJECT


def test_startup_recover_leases_does_not_touch_a_lease_acquired_after_it_started(fake_runs):
    _seed_lease(acquired_at=FUTURE)
    count = svc.startup_recover_leases()
    assert count == 0
    assert GROUP in db_leases._memory
    assert RUN_ID not in fake_runs.rows


def test_startup_recover_leases_skips_a_run_that_already_has_an_end_record(fake_runs):
    """A run that DID finalize normally right before the restart must keep its real
    end reason -- the recovery sweep must never relabel a clean finish."""
    _seed_lease(acquired_at=PAST)
    fake_runs.rows[RUN_ID] = {"run_id": RUN_ID, "end_reason": "exited"}
    svc.startup_recover_leases()
    assert fake_runs.rows[RUN_ID]["end_reason"] == "exited"


def test_startup_recover_leases_is_best_effort_against_a_broken_runs_table(fake_runs, monkeypatch):
    """The end-record write is a nice-to-have; the lease MUST still clear even if it fails."""
    def _boom(_row):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_record_orphaned_lease_run", _boom)
    _seed_lease(acquired_at=PAST)
    count = svc.startup_recover_leases()
    assert count == 1
    assert GROUP not in db_leases._memory


def test_startup_hook_is_wired_into_run_all():
    """server/startup.py must actually call the recovery, guarded so a failure there
    cannot block server boot (mirrors preload_singletons / recover_git_sessions)."""
    source = (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    assert "recover_ai_invoke_leases" in source
    assert "ai_invoke_service.startup_recover_leases()" in source
    assert "def run_all" in source
    run_all_source = source[source.index("def run_all"):]
    assert "recover_ai_invoke_leases()" in run_all_source


# ── ai_invoke_service.is_run_live (작업 2) ──────────────────────────────────────────────

def test_is_run_live_true_for_a_tracked_unfinished_run():
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "running"}
    assert svc.is_run_live(RUN_ID) is True


def test_is_run_live_false_for_a_run_this_process_marked_finished():
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "finished"}
    assert svc.is_run_live(RUN_ID) is False


def test_is_run_live_false_for_an_untracked_run():
    assert svc.is_run_live("aiv_ghost_not_here") is False


# ── ai_invoke_service.force_release_group_lease (작업 2) ───────────────────────────────

def test_force_release_rejects_a_live_run(fake_runs):
    _seed_lease()
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "running"}
    with pytest.raises(HTTPException) as exc_info:
        svc.force_release_group_lease(GROUP)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "run_still_live"
    assert GROUP in db_leases._memory  # untouched


def test_force_release_clears_an_orphaned_lease_and_records_end_reason(fake_runs):
    _seed_lease()
    result = svc.force_release_group_lease(GROUP)
    assert result == {"ok": True, "group_id": GROUP, "run_id": RUN_ID, "released": True}
    assert GROUP not in db_leases._memory
    assert fake_runs.rows[RUN_ID]["end_reason"] == "orphaned_by_manual_release"


def test_force_release_404s_when_no_lease_exists(fake_runs):
    with pytest.raises(HTTPException) as exc_info:
        svc.force_release_group_lease(GROUP)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "lease_not_found"


# ── GET/POST /api/v1/ai-invoke/leases (작업 2) ──────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_1", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    return TestClient(app, raise_server_exceptions=False)


def _auth():
    return {"Authorization": "Bearer tok"}


def test_leases_route_lists_locked_groups_with_liveness(client, fake_runs):
    lease = _seed_lease()
    resp = client.get("/api/v1/ai-invoke/leases", params={"project": PROJECT}, headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [{
        "group_id": GROUP, "run_id": RUN_ID, "state": lease["state"],
        "acquired_at": lease["acquired_at"], "heartbeat_at": lease["heartbeat_at"],
        "expires_at": lease["expires_at"], "run_live": False,
    }]


def test_leases_route_reports_run_live_true_for_a_tracked_run(client, fake_runs):
    _seed_lease()
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "running"}
    resp = client.get("/api/v1/ai-invoke/leases", params={"project": PROJECT}, headers=_auth())
    assert resp.json()["items"][0]["run_live"] is True


def test_leases_route_is_declared_ahead_of_the_run_id_route():
    """L0007 §2.10.3's own routing-order requirement, restated for /leases: it must
    resolve to the list handler and never be read as a run_id by GET /{run_id}."""
    source = Path(ai_invoke_routes.__file__).read_text(encoding="utf-8")
    assert source.index('@router.get("/leases")') < source.index('@router.get("/{run_id}")')
    assert source.index('@router.post("/leases/{group_id}/release")') < source.index('@router.get("/{run_id}")')


def test_release_route_releases_an_orphaned_lease(client, fake_runs):
    _seed_lease()
    resp = client.post(f"/api/v1/ai-invoke/leases/{GROUP}/release", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["released"] is True
    assert GROUP not in db_leases._memory


def test_release_route_rejects_a_live_run(client, fake_runs):
    _seed_lease()
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "running"}
    resp = client.post(f"/api/v1/ai-invoke/leases/{GROUP}/release", headers=_auth())
    assert resp.status_code == 409
    assert resp.json()["code"] == "run_still_live"
    assert GROUP in db_leases._memory


def test_release_route_404s_with_no_lease(client, fake_runs):
    resp = client.post(f"/api/v1/ai-invoke/leases/{GROUP}/release", headers=_auth())
    assert resp.status_code == 404


def test_release_route_is_exempt_from_the_group_mutation_gate():
    """A human calling this on a group whose lease is genuinely active must reach the
    route's own 409 -- not the unrelated 423 the global GroupMutationPolicyMiddleware
    would otherwise raise for exactly that same lease (it would see "lease exists,
    caller is not its worker" and lock the release request out with the very lock it
    exists to release)."""
    assert policy.is_policy_control_path(f"/api/v1/ai-invoke/leases/{GROUP}/release") is True
    assert policy.is_policy_control_path(f"/api/v1/ai-invoke/{RUN_ID}/cancel") is True
    assert policy.is_policy_control_path(f"/api/v1/ai-invoke/{RUN_ID}/pause") is True


# ── mutation_policy 423 carries run_live (작업 4) ───────────────────────────────────────

def test_locked_error_reports_run_live_true_for_a_live_run():
    with svc._runs_lock:
        svc._runs[RUN_ID] = {"run_id": RUN_ID, "status": "running"}
    exc = policy._locked({"group_id": GROUP, "run_id": RUN_ID})
    assert exc.error["run_live"] is True


def test_locked_error_reports_run_live_false_for_an_orphaned_lease():
    exc = policy._locked({"group_id": GROUP, "run_id": RUN_ID})
    assert exc.error["run_live"] is False


def test_locked_error_reports_run_live_false_with_no_run_id_on_the_lease():
    exc = policy._locked({"group_id": GROUP})
    assert exc.error["run_live"] is False


def test_lease_run_live_fails_toward_true_when_it_cannot_look(monkeypatch):
    """A lookup failure must never invite a force-unlock of a run that might actually
    still be busy -- the same fail-safe direction is_run_live's own docstring names."""
    def _boom(_request_run_id):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(svc, "is_run_live", _boom)
    assert policy._lease_run_live(RUN_ID) is True


# ── run_id continuity across restarts (작업 7) ──────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def test_next_run_id_floors_against_finished_runs_and_open_leases(fake_runs):
    date_str = _today()
    fake_runs.rows[f"aiv_{date_str}_000005"] = {"run_id": f"aiv_{date_str}_000005"}
    _seed_lease(group_id=OTHER_GROUP, run_id=f"aiv_{date_str}_000009")
    minted = svc._next_run_id()
    assert minted == f"aiv_{date_str}_000010"


def test_next_run_id_does_not_reflor_within_the_same_process(fake_runs):
    date_str = _today()
    fake_runs.rows[f"aiv_{date_str}_000005"] = {"run_id": f"aiv_{date_str}_000005"}
    first = svc._next_run_id()
    # A new row appears afterwards (another process finalizing) -- this process already
    # floored today and must not re-query, so its next id is simply first+1.
    fake_runs.rows[f"aiv_{date_str}_000099"] = {"run_id": f"aiv_{date_str}_000099"}
    second = svc._next_run_id()
    assert int(second.rsplit("_", 1)[-1]) == int(first.rsplit("_", 1)[-1]) + 1


def test_next_run_id_falls_back_to_the_bare_counter_on_lookup_failure(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    def _boom(_date_str):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_runs, "max_serial_for_date", _boom)
    run_id = svc._next_run_id()  # must not raise
    assert run_id.startswith(f"aiv_{_today()}_")


def test_acquire_raises_run_id_collision_when_another_group_holds_that_run_id():
    _seed_lease(group_id=OTHER_GROUP, run_id=RUN_ID)
    with pytest.raises(db_leases.RunIdCollision):
        db_leases.acquire(
            group_id=GROUP, project_id=PROJECT, run_id=RUN_ID,
            chain_id=RUN_ID, action_scope="new", worker_identity="usr_ai",
        )
    # The failed attempt must not have written anything under the new group either.
    assert GROUP not in db_leases._memory


def test_start_run_retries_once_on_run_id_collision_then_raises_cleanly():
    """Pins the retry wrapper's SHAPE: start_run must catch RunIdCollision, retry
    exactly once with a fresh _next_run_id(), and raise a clean 409 run_id_collision on
    a second hit -- never let the raw exception escape as an unhandled 500. Checked
    structurally rather than by driving a real start_run call, which would otherwise
    need its full dependency graph (token/mention/process machinery) stood up just to
    reach two lines of retry logic."""
    import inspect

    source = inspect.getsource(svc.start_run)
    assert source.count("RunIdCollision") == 2
    assert source.count("_next_run_id()") >= 2
    assert '"run_id_collision"' in source
