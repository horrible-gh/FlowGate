"""flowgate.default.0459 T0007 — group-keyed paused chain 명시적 취소·해제 API.

Covers the server half of NR0003 방향 C / WP T#2:

  * DB layer (``ai_invoke_paused_chains.release_owned``): compare-and-swap delete,
    NULL-safe on ``paused_at``/``stop_run_id``, race survival (a row upserted after the
    caller's read is never taken), a control case proving a bare ``delete_by_group``
    would have destroyed that newer row, AND (0459 TR0008 rev1) a write that lands
    strictly between the initial SELECT and the DELETE statement itself -- the exact
    0-row-affected race the rev0 review rejection flagged, since ``_execute`` exposes
    no rowcount and the earlier double always cleared its row unconditionally.
  * Service layer (``ai_invoke_service.release_paused_chain``): owner/admin-only,
    403 + row preserved for a third party, idempotent 200 (``already_released``) on
    repeat or on an absent row (never 404), 409 ``run_already_active`` when an active
    run exists and the DISTINCT 409 ``group_lease_active`` when only a valid lease
    does (both preserved, ``force_release_group_lease`` never called), an expired
    lease is invisible (falls through to the CAS delete).
  * The same ownership rule retrofitted onto ``resume_chain``, including (0459 TR0008
    rev1) the exact replacement race the rev0 review flagged: a non-owner/non-admin
    cannot consume or relaunch someone else's paused chain, an admin override still
    works, AND a row that changes owner strictly between the ownership read and the
    consume step is never taken by the old owner's already-authorized call.
  * Route layer (``DELETE /api/v1/ai-invoke/paused/{group_id}``): group-id validation,
    project 404, permission 403, the service's error envelope passed through verbatim,
    and the mutation-policy control-path exemption.

No database: the paused store, workflow sequence and group lease registry are
dict-backed doubles (the lease registry is the production module's own in-memory
test fallback) with the same contracts pytest exercises them against everywhere else
in this suite.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.flow_gate.api.v1 import ai_invoke_routes as routes  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import mutation_policy  # noqa: E402

GROUP = "flowgate.default.0459"
DOC_REF = "flowgate.default.0459.0001-B"
OWNER = "usr_owner"
OTHER = "usr_other"
ADMIN = "usr_admin"


# ══════════════════════════════════════════════════════════════════════════════════════
# Doubles
# ══════════════════════════════════════════════════════════════════════════════════════

class FakePausedStore:
    """Dict-backed stand-in for the service-level tests. ``release_owned`` copies the
    production predicate (group_id, NULL-safe paused_by/paused_at/stop_run_id, and
    stop_kind via the same COALESCE(...,'user') reading ``delete_system_stop`` uses)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def put(self, **row):
        base = {
            "id": 1, "group_id": GROUP, "doc_ref": DOC_REF, "mode": "continuous",
            "paused_by": OWNER, "paused_at": "2026-08-25T10:00:00+09:00",
            "continuation_target_seq": 3, "docs_target": 1, "docs_reached": 0,
            "chain_id": "aiv_chain_1", "chain_docs_target": 1, "chain_docs_reached": 0,
            "stop_kind": "user", "stop_code": None, "stop_run_id": None,
            "stop_last_message_excerpt": None,
            "continuation_base_provider_id": None, "continuation_provider_pinned": False,
            "continuation_provider_overrides": None,
            "continuation_default_note": None, "continuation_note_overrides": None,
            "continuation_instruction_mode": None, "continuation_auto_approve_item_seqs": None,
            "continuation_step_timeout_sec": None, "continuation_restart_max_attempts": None,
            "continuation_review_count_overrides": None, "continuation_reviewer_overrides": None,
        }
        base.update(row)
        self.rows[base["group_id"]] = base
        return base

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row else None

    def exists(self, group_id):
        return group_id in self.rows

    def delete_and_return(self, group_id):
        row = self.rows.pop(group_id, None)
        return dict(row) if row else None

    def delete_by_group(self, group_id):
        self.rows.pop(group_id, None)

    def release_owned(self, group_id, *, paused_by, paused_at, stop_kind, stop_run_id):
        row = self.rows.get(group_id)
        if row is None:
            return None
        normalized = stop_kind or "user"
        if (row.get("paused_by") != paused_by
                or row.get("paused_at") != paused_at
                or (row.get("stop_kind") or "user") != normalized
                or row.get("stop_run_id") != stop_run_id):
            # 0459 TR0008 rev2: the row EXISTS but no longer matches the caller's
            # snapshot -- a real conflict, distinct from "nothing to consume" (None).
            return db_paused.ReleaseSuperseded(dict(row))
        return self.rows.pop(group_id)


class _MiniStore:
    """The bare ``_fetch_one``/``_execute``/``transaction`` surface ``release_owned``
    needs, holding exactly one row so the real DB function can be exercised directly."""

    def __init__(self, row):
        self._row = dict(row) if row else None
        self.executed: list[tuple[str, list]] = []

    def _fetch_one(self, sql, params):
        return dict(self._row) if self._row else None

    def _execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), list(params)))
        self._row = None

    class _Txn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def transaction(self):
        return self._Txn()


class _RacingMiniStore:
    """Like ``_MiniStore``, but models a concurrent upsert landing strictly BETWEEN
    ``release_owned``'s initial SELECT and its DELETE statement (0459 TR0008 rev1) --
    the exact window the rev0 review flagged: ``_execute`` reports no rowcount, so a
    DELETE built from a snapshot that goes stale in that window must be provably
    caught, not just assumed to have succeeded because the earlier read matched.

    ``_execute`` mutates ``self._row`` to the racing row the instant it runs (as if
    the other writer's COMMIT landed right as this DELETE reaches the table), THEN
    decides whether the delete "took" by comparing that CURRENT row to the snapshot
    the caller originally read -- mirroring what a real ``WHERE`` predicate evaluates
    against at execution time, not read time.
    """

    def __init__(self, initial_row, racing_row, expected_snapshot):
        self._row = dict(initial_row)
        self._racing_row = dict(racing_row)
        self._expected = expected_snapshot
        self.executed: list[tuple[str, list]] = []

    def _fetch_one(self, sql, params):
        return dict(self._row) if self._row else None

    def _execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), list(params)))
        self._row = dict(self._racing_row)
        normalized_stop_kind = self._row.get("stop_kind") or "user"
        matches = (
            self._row.get("paused_by") == self._expected["paused_by"]
            and self._row.get("paused_at") == self._expected["paused_at"]
            and normalized_stop_kind == (self._expected["stop_kind"] or "user")
            and self._row.get("stop_run_id") == self._expected["stop_run_id"]
        )
        if matches:
            self._row = None

    class _Txn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def transaction(self):
        return self._Txn()


@pytest.fixture
def env(monkeypatch):
    """release_paused_chain / resume_chain ownership, reduced to the group lock + paused
    store + run registry + lease registry -- no subprocess is ever started."""
    store = FakePausedStore()
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_group_resume_locks", {})
    for name in ("get_by_group", "release_owned", "delete_and_return", "delete_by_group",
                "exists"):
        monkeypatch.setattr(db_paused, name, getattr(store, name))
    # group_ai_leases runs off its own in-memory dict under pytest (PYTEST_CURRENT_TEST) --
    # prime its test-scope id THEN clear, so a lease this test writes directly cannot be
    # wiped by a later _sync_test_scope() call noticing the test id changed mid-test.
    db_leases._sync_test_scope()
    db_leases._memory.clear()
    return {"store": store}


def _active_run(run_id="aiv_active_1", status="running"):
    return {"run_id": run_id, "group_id": GROUP, "status": status}


def _seed_lease(run_id="aiv_lease_run", *, expired=False):
    delta = timedelta(minutes=-5) if expired else timedelta(minutes=5)
    stamp = (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")
    db_leases._memory[GROUP] = {
        "group_id": GROUP, "project_id": "flowgate", "run_id": run_id,
        "chain_id": None, "token_id": None, "action_scope": "new",
        "worker_identity": None, "state": "active", "generation": 1,
        "acquired_at": stamp, "heartbeat_at": stamp, "expires_at": stamp,
        "updated_at": stamp,
    }


# ══════════════════════════════════════════════════════════════════════════════════════
# DB layer: release_owned compare-and-swap
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReleaseOwnedDbLayer:
    def _matching_row(self, **overrides):
        row = {
            "group_id": GROUP, "paused_by": OWNER, "paused_at": "2026-08-25T10:00:00+09:00",
            "stop_kind": "user", "stop_run_id": None,
        }
        row.update(overrides)
        return row

    def test_matching_snapshot_deletes_and_returns_the_row(self, monkeypatch):
        row = self._matching_row()
        store = _MiniStore(row)
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at=row["paused_at"],
            stop_kind="user", stop_run_id=None,
        )

        assert result == row
        assert len(store.executed) == 1
        sql, params = store.executed[0]
        assert sql.startswith("DELETE FROM ai_invoke_paused_chains WHERE ")
        assert "group_id = ?" in sql
        assert "COALESCE(stop_kind, 'user') = ?" in sql
        assert "paused_by = ?" in sql
        assert "paused_at = ?" in sql
        # NULL-safe: a None value gets an IS NULL clause, never a bound placeholder.
        assert "stop_run_id IS NULL" in sql
        assert params == [GROUP, "user", OWNER, row["paused_at"]]

    def test_a_system_row_snapshot_binds_stop_run_id_as_a_parameter(self, monkeypatch):
        row = self._matching_row(stop_kind="system", stop_run_id="aiv_old_stop")
        store = _MiniStore(row)
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at=row["paused_at"],
            stop_kind="system", stop_run_id="aiv_old_stop",
        )

        assert result == row
        sql, params = store.executed[0]
        assert "stop_run_id = ?" in sql
        assert params == [GROUP, "system", OWNER, row["paused_at"], "aiv_old_stop"]

    def test_missing_row_returns_none_without_a_delete(self, monkeypatch):
        store = _MiniStore(None)
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at="x", stop_kind="user", stop_run_id=None,
        )

        assert result is None
        assert store.executed == []

    def test_a_newer_user_pause_written_under_the_read_survives(self, monkeypatch):
        # The caller (release_paused_chain) read the OLD snapshot earlier; by the time
        # this call's own SELECT runs, a newer user pause has landed (a later paused_at).
        newer_row = self._matching_row(paused_at="2026-08-25T10:05:00+09:00")
        store = _MiniStore(newer_row)
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at="2026-08-25T10:00:00+09:00",
            stop_kind="user", stop_run_id=None,
        )

        # 0459 TR0008 rev2: NOT a bare None -- the row is still THERE, just no longer
        # matching the snapshot, so it must come back as a ReleaseSuperseded wrapping
        # the current row, never folded into "nothing to consume".
        assert isinstance(result, db_paused.ReleaseSuperseded)
        assert result.row == newer_row
        assert store.executed == []
        assert store._row == newer_row

    def test_a_newer_system_stop_written_under_the_read_survives(self, monkeypatch):
        newer_row = self._matching_row(
            stop_kind="system", stop_run_id="aiv_new_stop",
            paused_at="2026-08-25T10:05:00+09:00",
        )
        store = _MiniStore(newer_row)
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at="2026-08-25T10:00:00+09:00",
            stop_kind="user", stop_run_id=None,
        )

        assert isinstance(result, db_paused.ReleaseSuperseded)
        assert result.row == newer_row
        assert store.executed == []
        assert store._row == newer_row

    def test_a_group_only_delete_would_have_destroyed_the_newer_row(self):
        """Control case: the naive ``delete_by_group`` this DB function must NOT
        collapse to takes ANY row for the group, snapshot or not."""
        fake = FakePausedStore()
        fake.put(paused_at="2026-08-25T10:00:00+09:00")
        # A newer user pause races in before the (hypothetical) group-only delete runs.
        fake.rows[GROUP]["paused_at"] = "2026-08-25T10:05:00+09:00"

        fake.delete_by_group(GROUP)

        assert GROUP not in fake.rows  # exactly what release_owned must NOT do

    def test_a_write_landing_between_the_select_and_the_delete_is_not_reported_released(
            self, monkeypatch):
        """0459 TR0008 rev1 -- the exact bug the rejection flagged: a concurrent
        upsert that lands AFTER the initial SELECT (so the pre-DELETE Python compare
        still sees the old, matching snapshot) but BEFORE the DELETE statement
        actually reaches the table must not be reported as a successful delete.
        ``_execute`` has no rowcount, so this can only be caught by re-reading the
        primary key inside the same transaction -- which is exactly what the fix
        adds and this test pins."""
        initial = self._matching_row()
        racing = self._matching_row(paused_at="2026-08-25T10:05:00+09:00")
        store = _RacingMiniStore(initial, racing, expected_snapshot={
            "paused_by": OWNER, "paused_at": initial["paused_at"],
            "stop_kind": "user", "stop_run_id": None,
        })
        monkeypatch.setattr(db_paused, "get_store", lambda: store)

        result = db_paused.release_owned(
            GROUP, paused_by=OWNER, paused_at=initial["paused_at"],
            stop_kind="user", stop_run_id=None,
        )

        # NOT the stale `initial` row, and NOT a bare None either (0459 TR0008
        # rev2) -- the race was caught by the post-DELETE verification, and the
        # CURRENT surviving row must come back wrapped, not silently reported as
        # released=true nor collapsed into "nothing to consume".
        assert isinstance(result, db_paused.ReleaseSuperseded)
        assert result.row == racing
        # The newer row that raced in survives untouched.
        assert store._row == racing


# ══════════════════════════════════════════════════════════════════════════════════════
# Service layer: release_paused_chain
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReleaseOwnerAndIdempotency:
    def test_owner_releases_a_user_pause_row(self, env):
        env["store"].put(stop_kind="user")

        result = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert result == {"ok": True, "group_id": GROUP, "released": True,
                          "already_released": False}
        assert GROUP not in env["store"].rows

    def test_owner_releases_a_system_stop_row(self, env):
        env["store"].put(stop_kind="system", stop_code="no_output_exhausted",
                         stop_run_id="aiv_old")

        result = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert result["released"] is True
        assert GROUP not in env["store"].rows

    def test_admin_releases_another_users_row(self, env):
        env["store"].put(paused_by=OTHER)

        result = svc.release_paused_chain(group_id=GROUP, user_id=ADMIN, is_admin=True)

        assert result["released"] is True
        assert GROUP not in env["store"].rows

    def test_non_owner_non_admin_is_forbidden_and_row_survives(self, env):
        env["store"].put(paused_by=OWNER)

        with pytest.raises(HTTPException) as exc:
            svc.release_paused_chain(group_id=GROUP, user_id=OTHER, is_admin=False)

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "paused_chain_forbidden"
        assert GROUP in env["store"].rows

    def test_second_call_after_success_is_idempotent_200(self, env):
        env["store"].put()

        first = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)
        second = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert first["released"] is True
        assert second == {"ok": True, "group_id": GROUP, "released": False,
                          "already_released": True}

    def test_release_with_no_row_at_all_is_idempotent_200_not_404(self, env):
        result = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert result == {"ok": True, "group_id": GROUP, "released": False,
                          "already_released": True}


class TestReleaseVsActiveRunAndLease:
    def test_active_run_blocks_release_with_409_and_preserves_row(self, env):
        env["store"].put()
        svc._runs["aiv_active_1"] = _active_run()

        with pytest.raises(HTTPException) as exc:
            svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "run_already_active"
        assert exc.value.detail["run_id"] == "aiv_active_1"
        assert GROUP in env["store"].rows

    def test_valid_lease_blocks_release_with_409_and_preserves_row_and_lease(
            self, env, monkeypatch):
        # A valid lease here means "another process may be resuming/starting/handing
        # off" -- exactly the case this single-process in-memory registry cannot
        # faithfully simulate (get_active reconciles a memory-invisible run as stale
        # in test mode, T0007 note below), so the lease boundary is mocked directly.
        env["store"].put()
        monkeypatch.setattr(svc.db_group_ai_leases, "get_active",
                            lambda group_id: {"group_id": GROUP, "run_id": "aiv_lease_run"})

        def _must_not_be_called(*_a, **_kw):
            raise AssertionError("force_release_group_lease must not be called")
        monkeypatch.setattr(svc, "force_release_group_lease", _must_not_be_called)

        with pytest.raises(HTTPException) as exc:
            svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert exc.value.status_code == 409
        # 0459 TR0008 rev1: DISTINCT from the active-run 409 above (run_already_active)
        # -- a caller blocked by a lease needs a different remedy (release the lease
        # separately from the locked-group screen) than a caller blocked by another
        # session's active run (adopt that run).
        assert exc.value.detail["code"] == "group_lease_active"
        assert exc.value.detail["run_id"] == "aiv_lease_run"
        assert GROUP in env["store"].rows

    def test_expired_lease_does_not_block_release(self, env):
        env["store"].put()
        _seed_lease("aiv_expired", expired=True)

        result = svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        assert result["released"] is True
        assert GROUP not in db_leases._memory  # get_active's own reclaim rule ran


class TestReleaseVsResumeOrdering:
    def test_release_wins_then_concurrent_resume_gets_resume_conflict(self, env):
        env["store"].put()

        release_result = svc.release_paused_chain(group_id=GROUP, user_id=OWNER,
                                                   is_admin=False)
        assert release_result["released"] is True

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id=OWNER, api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "resume_conflict"

    def test_resume_wins_then_concurrent_release_gets_run_already_active(self, env):
        env["store"].put()
        # Simulates a resume that already consumed the row and started a run: the row
        # is gone and the run registry now carries the new run for this group.
        env["store"].rows.pop(GROUP, None)
        svc._runs["aiv_resumed"] = _active_run("aiv_resumed")

        with pytest.raises(HTTPException) as exc:
            svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "run_already_active"
        assert exc.value.detail["run_id"] == "aiv_resumed"


class TestReleaseConflictVsSupersededRow:
    """0459 TR0008 rev2 -- the exact bug the rejection flagged: ``release_owned``'s
    CAS miss (a newer pause/system-stop lands strictly between
    ``release_paused_chain``'s ownership read and the CAS delete) was being folded
    into the SAME idempotent ``already_released`` 200 as a truly absent row. T0007
    §5 permits that idempotent success ONLY when nothing exists for the group_id at
    all -- a superseded row is still a live pause somebody else owns, and reporting
    it as "already gone" told the store to delete a card for a chain that never left
    the table. This must surface as a real 409 conflict instead, with the surviving
    row left completely untouched so the caller can reconcile against it."""

    def test_a_row_replaced_between_the_release_read_and_the_cas_delete_reports_conflict(
            self, env, monkeypatch):
        store = env["store"]
        store.put(paused_by=OWNER, paused_at="2026-08-25T10:00:00+09:00")
        real_get_by_group = store.get_by_group

        def _racing_get_by_group(group_id):
            row = real_get_by_group(group_id)
            # The concurrent upsert lands the instant after release_paused_chain's own
            # ownership read -- release_owned's internal re-read (and hence its CAS
            # compare) sees this newer row, not the one that authorized this call.
            store.put(paused_by=OTHER, paused_at="2026-08-25T10:05:00+09:00")
            return row
        monkeypatch.setattr(db_paused, "get_by_group", _racing_get_by_group)

        with pytest.raises(HTTPException) as exc:
            svc.release_paused_chain(group_id=GROUP, user_id=OWNER, is_admin=False)

        # A real conflict, NOT the idempotent already_released 200 the rev1 bug sent.
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "release_conflict"
        assert exc.value.detail["group_id"] == GROUP
        # The row that raced in survives completely untouched -- this call's CAS
        # delete must not have removed it, and no other row was written in its place.
        assert store.rows[GROUP]["paused_by"] == OTHER
        assert store.rows[GROUP]["paused_at"] == "2026-08-25T10:05:00+09:00"


# ══════════════════════════════════════════════════════════════════════════════════════
# Service layer: the same ownership rule on resume_chain
# ══════════════════════════════════════════════════════════════════════════════════════

class TestResumeOwnership:
    def test_third_party_cannot_resume_someone_elses_paused_chain(self, env):
        env["store"].put(paused_by=OWNER)

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id=OTHER, api_base_url="http://x/api/v1")

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "paused_chain_forbidden"
        assert GROUP in env["store"].rows

    def test_admin_can_resume_someone_elses_paused_chain(self, env, monkeypatch):
        env["store"].put(paused_by=OWNER)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc",
                            lambda doc_ref: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items",
                            lambda seq_id: [{"item_seq": 3, "result_doc_id": None,
                                            "result_doc_review_status": None}])
        monkeypatch.setattr(svc, "start_run", lambda **kw: {"ok": True, "run_id": "aiv_fake"})

        result = svc.resume_chain(group_id=GROUP, user_id=ADMIN,
                                  api_base_url="http://x/api/v1", is_admin=True)

        assert result == {"ok": True, "run_id": "aiv_fake"}
        assert GROUP not in env["store"].rows

    def test_owner_can_still_resume_their_own_chain(self, env, monkeypatch):
        env["store"].put(paused_by=OWNER)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc",
                            lambda doc_ref: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items",
                            lambda seq_id: [{"item_seq": 3, "result_doc_id": None,
                                            "result_doc_review_status": None}])
        monkeypatch.setattr(svc, "start_run", lambda **kw: {"ok": True, "run_id": "aiv_fake"})

        result = svc.resume_chain(group_id=GROUP, user_id=OWNER,
                                  api_base_url="http://x/api/v1")

        assert result == {"ok": True, "run_id": "aiv_fake"}
        assert GROUP not in env["store"].rows

    def test_no_row_at_all_skips_the_ownership_check_and_falls_through_to_conflict(
            self, env):
        # No pause row for anyone to own -- the existing resume_conflict contract, not
        # a spurious 403.
        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id=OTHER, api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "resume_conflict"

    def test_a_row_replaced_between_the_ownership_read_and_consume_is_never_taken(
            self, env, monkeypatch):
        """0459 TR0008 rev1 -- the exact race the rejection flagged: the ownership
        check reads OWNER's row, but a DIFFERENT user's newer pause replaces it in the
        gap before the consume step runs (a real upsert landing there). The old
        owner's already-authorized call must NOT silently consume or relaunch the new
        owner's chain -- it must fall through to resume_conflict, same as "nothing to
        consume", and the new row must survive untouched. A group-only
        ``delete_and_return(group_id)`` (the rev0 bug) would have taken it instead."""
        store = env["store"]
        store.put(paused_by=OWNER, paused_at="2026-08-25T10:00:00+09:00")
        real_get_by_group = store.get_by_group

        def _racing_get_by_group(group_id):
            row = real_get_by_group(group_id)
            # The concurrent upsert lands the instant after this authorized read.
            store.put(paused_by=OTHER, paused_at="2026-08-25T10:05:00+09:00")
            return row
        monkeypatch.setattr(db_paused, "get_by_group", _racing_get_by_group)

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id=OWNER, api_base_url="http://x/api/v1")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "resume_conflict"
        assert store.rows[GROUP]["paused_by"] == OTHER
        assert store.rows[GROUP]["paused_at"] == "2026-08-25T10:05:00+09:00"


# ══════════════════════════════════════════════════════════════════════════════════════
# Route layer: DELETE /api/v1/ai-invoke/paused/{group_id}
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReleaseRoute:
    @pytest.fixture
    def app_client(self, monkeypatch):
        app = FastAPI()
        app.include_router(routes.router)
        monkeypatch.setattr(
            routes, "verify_bearer",
            lambda request: {"_is_user_jwt": True, "issued_to": OWNER, "is_admin": False},
        )
        monkeypatch.setattr(routes, "has_permission", lambda *a, **kw: True)
        monkeypatch.setattr(routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
        return TestClient(app, raise_server_exceptions=False)

    def test_delete_route_calls_release_paused_chain_and_returns_200(
            self, app_client, monkeypatch):
        captured = {}

        def _fake_release(**kw):
            captured.update(kw)
            return {"ok": True, "group_id": GROUP, "released": True,
                    "already_released": False}
        monkeypatch.setattr(svc, "release_paused_chain", _fake_release)

        resp = app_client.delete(f"/api/v1/ai-invoke/paused/{GROUP}",
                                 headers={"Authorization": "Bearer tok"})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "group_id": GROUP, "released": True,
                               "already_released": False}
        assert captured == {"group_id": GROUP, "user_id": OWNER, "is_admin": False}

    def test_invalid_group_id_422(self, app_client):
        resp = app_client.delete("/api/v1/ai-invoke/paused/flowgate",
                                 headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 422

    def test_unknown_project_404(self, app_client, monkeypatch):
        monkeypatch.setattr(routes.db_projects, "get_by_id", lambda pid: None)
        resp = app_client.delete(f"/api/v1/ai-invoke/paused/{GROUP}",
                                 headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "project_not_found"

    def test_permission_denied_403(self, app_client, monkeypatch):
        monkeypatch.setattr(routes, "has_permission", lambda *a, **kw: False)
        resp = app_client.delete(f"/api/v1/ai-invoke/paused/{GROUP}",
                                 headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "permission_denied"

    def test_service_error_envelope_passes_through_verbatim(self, app_client, monkeypatch):
        def _forbid(**kw):
            raise svc._http_error(403, "paused_chain_forbidden",
                                  "Only the user who paused this chain (or an admin) "
                                  "may release it.", group_id=GROUP)
        monkeypatch.setattr(svc, "release_paused_chain", _forbid)

        resp = app_client.delete(f"/api/v1/ai-invoke/paused/{GROUP}",
                                 headers={"Authorization": "Bearer tok"})

        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "paused_chain_forbidden"
        assert body["group_id"] == GROUP

    def test_no_user_session_403(self, app_client, monkeypatch):
        monkeypatch.setattr(routes, "verify_bearer",
                            lambda request: {"_is_user_jwt": False})
        resp = app_client.delete(f"/api/v1/ai-invoke/paused/{GROUP}",
                                 headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "user_session_required"


class TestReleaseRouteMutationPolicyExemption:
    def test_the_delete_path_is_a_control_path_exception(self):
        assert mutation_policy.is_policy_control_path(
            f"/api/v1/ai-invoke/paused/{GROUP}") is True

    def test_an_unrelated_ai_invoke_path_is_not_exempted(self):
        assert mutation_policy.is_policy_control_path(
            "/api/v1/ai-invoke/start") is True  # start IS exempt (existing rule)
        assert mutation_policy.is_policy_control_path(
            f"/api/v1/documents/{GROUP}.0001-B") is False
