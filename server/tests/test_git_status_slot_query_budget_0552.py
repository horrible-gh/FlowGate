"""git/status slot query budget (flowgate.default.0552 T0013 / 0005-NR Set D).

`project_git_status` already reads every ledger row of the project in ONE
`list_states_of_project_any` scan, and then asked the database for each of those
rows a second time: the per-slot `writable` probe went

    group_worktree_writable -> effective_src_root_ex -> db_git.get_state(group_id)

so a project with S slots paid `SELECT * FROM group_git_state WHERE group_id = ?`
S extra times (0005-NR measured 8 for 8 slots). The request-scope cache cannot
absorb it — the params differ per slot, and the transition loop just above writes,
which discards the whole request cache anyway.

T0013 hands the already-read row down instead. These tests pin the two halves of
that claim:

  A. cost — the number of `group_git_state` reads no longer grows with S, with a
     CONTROL that re-runs the old shape and shows the counter really does see the
     S extra queries it is asserting the absence of;
  B. meaning — `writable`, `branch`, `status` and the resolution *reason* are
     byte-identical to what the re-reading path answers, in every fallback shape
     `effective_src_root_ex` distinguishes, including the ones with no row at all.

Environment mirrors test_screen_load_query_reduction_0282.py: TESTING=1 and a
temporary SQLite built from the real sqlite migrations, patched into
connection.STORE, plus a recorder around it that keeps every executed statement.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

import sys

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

def _pname(project_id: str) -> str:
    """`projects.project_name` is UNIQUE, and src_root() keys off it — one name
    per project keeps each fixture's worktrees in their own storage subtree."""
    return f"QSlot_{project_id}"

SQL_STATEMENTS: list[str] = []


def _record(sql: str) -> None:
    SQL_STATEMENTS.append(" ".join(str(sql).split()))


def group_state_reads() -> list[str]:
    """Every statement that touched group_git_state (reads and writes)."""
    return [s for s in SQL_STATEMENTS if "group_git_state" in s]


def per_group_state_reads() -> list[str]:
    """Only the single-row `WHERE group_id = ?` lookups — the N+1 under test."""
    return [
        s for s in group_state_reads()
        if s.startswith("SELECT") and "WHERE group_id = ?" in s
    ]


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        _record(sql)
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        _record(sql)
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        _record(sql)
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        _record(sql)
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def storage_root(tmp_path_factory):
    return tmp_path_factory.mktemp("qslot-storage")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, storage_root):
    """Production-shaped metadata caching + an empty statement log per test.

    FLOWGATE_META_CACHE_TTL is set explicitly because under TESTING the 0282 TTL
    cache is OFF, and without it `projects` / `project_git_config` would be
    re-read once per slot — a shape that never happens in production and would
    hide which table this test is actually measuring.
    """
    from modules.flow_gate.db import meta_cache

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))
    monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "5")
    meta_cache.clear_all()
    SQL_STATEMENTS.clear()
    yield
    meta_cache.clear_all()
    SQL_STATEMENTS.clear()


def _branch(project_id: str, n: int) -> str:
    return f"{project_id}_default_{n:04d}"


def _make_project(
    project_id: str,
    slots: int,
    storage_root: Path,
    *,
    live: int | None = None,
    wf_done: bool = False,
):
    """A git-enabled project with `slots` registered worktree slots.

    `live` (default: all) is how many of them get a real on-disk worktree — the
    rest stay ledger-only so `writable` has both answers to be wrong about.
    `wf_done` gives every group a final-approved R root, which is what arms
    project_git_status's lazy none -> awaiting_choice transition (a WRITE) in
    the loop that runs just before the writable probe.
    Returns the group ids in slot order.
    """
    from modules.flow_gate.db import documents, git_integration as db_git
    from modules.flow_gate.db import groups, projects

    live = slots if live is None else live
    projects.create({"project_id": project_id, "project_name": _pname(project_id)})
    db_git.upsert_config(project_id, {
        "repo_url": "https://example.invalid/qslot.git",
        "secret_enc": None,
        "base_branch": "main",
        "enabled": 1,
    })
    group_ids = []
    for n in range(1, slots + 1):
        group_id = f"{project_id}.default.{n:04d}"
        groups.create({
            "group_id": group_id,
            "project_id": project_id,
            "module": "default",
            "title": group_id,
        })
        if wf_done:
            doc_id = f"{group_id}.0001-R"
            documents.create({
                "doc_id": doc_id,
                "project_id": project_id,
                "module": "default",
                "group_id": group_id,
                "type_code": "R",
                "seq": 1,
                "title": doc_id,
            })
            documents.update(doc_id, {"doc_review_status": "wf_done"})
        branch = _branch(project_id, n)
        db_git.register_worktree(group_id, project_id, branch)
        if n <= live:
            wt = storage_root / "src" / _pname(project_id) / branch
            wt.mkdir(parents=True, exist_ok=True)
            # 0287 NR0004: a worktree is a directory WITH its .git link.
            (wt / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
        group_ids.append(group_id)
    return group_ids


def _status(project_id: str) -> dict:
    from modules.flow_gate.services import git_service as svc

    return svc.project_git_status(project_id)["status"]


# ── A. cost: group_git_state reads do not grow with slot count ───────────────


class TestSlotQueryBudget:
    def test_one_slot_and_many_slots_read_group_git_state_the_same_number_of_times(
        self, tmp_db, storage_root
    ):
        _make_project("qslot1", 1, storage_root, live=1)
        _make_project("qslot8", 8, storage_root, live=6)

        SQL_STATEMENTS.clear()
        one = _status("qslot1")
        one_state_reads = list(group_state_reads())
        one_total = len(SQL_STATEMENTS)

        SQL_STATEMENTS.clear()
        many = _status("qslot8")
        many_state_reads = list(group_state_reads())
        many_total = len(SQL_STATEMENTS)

        assert len(one["slots"]) == 1 and len(many["slots"]) == 8

        # The point of the set: 8× the slots, the same ledger cost.
        assert len(many_state_reads) == len(one_state_reads) == 2, (
            f"1 slot: {one_state_reads}\n8 slots: {many_state_reads}"
        )
        # No single-row probe survives at either slot count.
        assert per_group_state_reads() == []
        # What remains are the two BATCHED reads — the project-wide ledger scan
        # and the cancel-block IN() lookup — each of which answers for every slot
        # in one statement. Their shape is therefore identical at 1 and at 8
        # slots; only the placeholder count inside the IN list differs.
        def _shape(statements):
            return [st.split(" IN (")[0] for st in statements]

        assert _shape(many_state_reads) == _shape(one_state_reads)
        assert "WHERE project_id = ?" in many_state_reads[0]
        # Nothing else in the response assembly grew either.
        assert many_total == one_total, (
            f"total queries grew with slot count: {one_total} -> {many_total}"
        )

    def test_control_the_old_per_slot_probe_is_visible_to_this_counter(
        self, tmp_db, storage_root
    ):
        """Guard against a green that means "the counter sees nothing".

        Re-runs the pre-T0013 shape — the writable probe without the row — and
        asserts the counter records exactly one `WHERE group_id = ?` read per
        slot. If this fails, the assertions above prove nothing.
        """
        from modules.flow_gate.services import git_service as svc

        groups_ = _make_project("qslotctl", 5, storage_root, live=3)

        SQL_STATEMENTS.clear()
        legacy = [svc.group_worktree_writable("qslotctl", g) for g in groups_]
        assert len(per_group_state_reads()) == 5

        SQL_STATEMENTS.clear()
        from modules.flow_gate.db import git_integration as db_git

        rows = {r["group_id"]: r for r in db_git.list_states_of_project_any("qslotctl")}
        SQL_STATEMENTS.clear()
        current = [
            svc.group_worktree_writable("qslotctl", g, rows[g]) for g in groups_
        ]
        assert per_group_state_reads() == []

        # Same answers, one fewer query each.
        assert current == legacy == [True, True, True, False, False]


    @pytest.mark.parametrize("slots", [1, 4, 8])
    def test_before_and_after_counts_side_by_side(
        self, tmp_db, storage_root, monkeypatch, slots, capsys
    ):
        """The before/after table, measured rather than asserted from memory.

        `_legacy` is the pre-T0013 body of `group_worktree_writable` verbatim (it
        drops the row on the floor and lets the resolver re-read it), so both
        columns come out of the same `project_git_status` on the same fixture —
        the only difference is whether the already-read row is handed down.
        """
        from modules.flow_gate.services import git_service as svc

        project_id = f"qslotcmp{slots}"
        _make_project(project_id, slots, storage_root, live=max(slots - 1, 1))

        def _legacy(project_id_, group_id, state=None):
            return svc.effective_src_root_ex(project_id_, group_id)[0] is not None

        with monkeypatch.context() as m:
            m.setattr(svc, "group_worktree_writable", _legacy)
            SQL_STATEMENTS.clear()
            before_status = _status(project_id)
            before_per_slot = len(per_group_state_reads())
            before_total = len(SQL_STATEMENTS)

        SQL_STATEMENTS.clear()
        after_status = _status(project_id)
        after_per_slot = len(per_group_state_reads())
        after_total = len(SQL_STATEMENTS)

        with capsys.disabled():
            print(
                f"\n  slots={slots:>2}  group_git_state WHERE group_id=?: "
                f"{before_per_slot} -> {after_per_slot}   "
                f"total statements: {before_total} -> {after_total}"
            )

        # Before: exactly one extra single-row read per slot. After: none.
        assert before_per_slot == slots
        assert after_per_slot == 0
        assert after_total == before_total - slots

        # Same answers — every field the panel and the explorer read.
        assert after_status["slots"] == before_status["slots"]
        assert after_status["pending"] == before_status["pending"]
        assert after_status["pending_count"] == before_status["pending_count"]
        assert after_status["cleanable_count"] == before_status["cleanable_count"]
        assert after_status["base_branch"] == before_status["base_branch"]


    def test_the_transition_loop_write_does_not_bring_the_read_back(
        self, tmp_db, storage_root
    ):
        """The N+1 survived 0291's request-scope cache for a reason.

        The slot loop WRITES (the lazy none -> awaiting_choice transition, the
        stale-pending repair), and `_execute` discards the whole request-scope
        cache, so the per-slot `get_state` that follows was a real round trip
        every time — not a cache hit. This is that exact shape: four groups whose
        roots are wf_done, so every slot is written before its writable probe.
        """
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotwrite", 4, storage_root, live=2, wf_done=True)

        SQL_STATEMENTS.clear()
        status = _status("qslotwrite")

        # The transition really did run and really did write.
        assert [s["status"] for s in status["slots"]] == ["awaiting_choice"] * 4
        assert any(
            st.startswith("UPDATE group_git_state") for st in group_state_reads()
        ), group_state_reads()
        # …and still no slot re-read the row it was handed.
        assert per_group_state_reads() == []
        # The verdict a fresh read would have produced, for every slot.
        assert [s["writable"] for s in status["slots"]] == [True, True, False, False]
        for slot in status["slots"]:
            assert slot["writable"] == svc.group_worktree_writable(
                "qslotwrite", slot["group_id"]
            )


# ── B. meaning: same verdict, same reason, same freshness ────────────────────


class TestWritableMeaningIsUnchanged:
    def test_status_response_matches_a_recomputation_through_the_reading_path(
        self, tmp_db, storage_root
    ):
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotsame", 6, storage_root, live=4)

        slots = _status("qslotsame")["slots"]
        assert [s["writable"] for s in slots] == [True] * 4 + [False] * 2

        for slot in slots:
            # The DB-reading form must agree field for field.
            assert slot["writable"] == svc.group_worktree_writable(
                "qslotsame", slot["group_id"]
            )
            state = svc.db_git.get_state(slot["group_id"])
            assert slot["branch"] == state["branch"]
            assert slot["status"] == state["status"]
            assert slot["merge_id"] == state["merge_id"]

    def test_supplied_row_reproduces_every_fallback_reason(self, tmp_db, storage_root):
        """Each SRC_ROOT_* outcome, resolved twice: from the DB, and from the row."""
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotedge", 1, storage_root, live=0)
        group_id = "qslotedge.default.0001"
        branch = _branch("qslotedge", 1)
        wt = storage_root / "src" / _pname("qslotedge") / branch

        def _both():
            row = svc.db_git.get_state(group_id)
            return svc.effective_src_root_ex("qslotedge", group_id), \
                svc.effective_src_root_ex("qslotedge", group_id, row)

        # E1 ledger row present, directory absent
        from_db, from_row = _both()
        assert from_db == from_row == (None, svc.SRC_ROOT_DIR_MISSING)

        # E2 directory present but no .git link (interrupted teardown corpse)
        wt.mkdir(parents=True, exist_ok=True)
        from_db, from_row = _both()
        assert from_db == from_row == (None, svc.SRC_ROOT_DIR_BROKEN)

        # E3 healthy worktree
        (wt / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
        from_db, from_row = _both()
        assert from_db == from_row == (wt.resolve(), svc.SRC_ROOT_WORKTREE)

        # E4 slot released (merge/push cleanup clears the flag, keeps the branch)
        svc.db_git.unregister_worktree(group_id)
        from_db, from_row = _both()
        assert from_db == from_row == (None, svc.SRC_ROOT_UNREGISTERED)

    def test_branch_empty_state_falls_back_via_both_paths(self, tmp_db, storage_root):
        """T0013 §C.12: a registered-but-branchless row is the other ledger-only
        fallback `effective_src_root_ex` distinguishes (SRC_ROOT_NO_BRANCH), and
        it must survive the row hand-off exactly like every other reason in
        `test_supplied_row_reproduces_every_fallback_reason` above."""
        from modules.flow_gate.db.connection import get_store
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotnobranch", 1, storage_root, live=1)
        group_id = "qslotnobranch.default.0001"

        # worktree_registered stays 1 — only the branch itself is blanked, the
        # shape a corrupted or partially-written ledger row can still have.
        get_store()._execute(
            "UPDATE group_git_state SET branch = ? WHERE group_id = ?",
            ["", group_id],
        )

        row = svc.db_git.get_state(group_id)
        assert row["worktree_registered"]
        assert not (row["branch"] or "").strip()

        from_db = svc.effective_src_root_ex("qslotnobranch", group_id)
        from_row = svc.effective_src_root_ex("qslotnobranch", group_id, row)

        assert from_db == from_row == (None, svc.SRC_ROOT_NO_BRANCH)
        assert svc.group_worktree_writable("qslotnobranch", group_id) is False
        assert svc.group_worktree_writable("qslotnobranch", group_id, row) is False

    def test_no_state_row_still_falls_back_to_the_lookup(self, tmp_db, storage_root):
        """`state=None` means "not supplied", never "there is no row"."""
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotnostate", 1, storage_root, live=1)
        group_id = "qslotnostate.default.0001"

        # A group with a row: omitting the argument must still find it.
        assert svc.effective_src_root_ex("qslotnostate", group_id, None)[1] == \
            svc.SRC_ROOT_WORKTREE
        assert svc.group_worktree_writable("qslotnostate", group_id, None) is True

        # A group with no ledger row at all keeps the missing-state reason —
        # the optimisation must not invent a new error state for it.
        ghost = "qslotnostate.default.9999"
        assert svc.effective_src_root_ex("qslotnostate", ghost) == \
            (None, svc.SRC_ROOT_NO_STATE)
        assert svc.effective_src_root_ex("qslotnostate", ghost, None) == \
            (None, svc.SRC_ROOT_NO_STATE)
        assert svc.group_worktree_writable("qslotnostate", ghost) is False

    def test_supplied_row_never_overrides_the_on_disk_check(self, tmp_db, storage_root):
        """The filesystem half of the judgement is untouched by this set.

        A row that claims a registered worktree does NOT make a vanished
        directory writable — reuse removes a query, not a check.
        """
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotdisk", 1, storage_root, live=1)
        group_id = "qslotdisk.default.0001"
        row = svc.db_git.get_state(group_id)
        assert svc.group_worktree_writable("qslotdisk", group_id, row) is True

        (storage_root / "src" / _pname("qslotdisk") / _branch("qslotdisk", 1) / ".git").unlink()
        assert svc.group_worktree_writable("qslotdisk", group_id, row) is False
        assert svc.effective_src_root_ex("qslotdisk", group_id, row)[1] == \
            svc.SRC_ROOT_DIR_BROKEN

    def test_a_stale_status_in_the_supplied_row_cannot_change_the_verdict(
        self, tmp_db, storage_root
    ):
        """`status` is log-only inside the resolver.

        project_git_status hands down a row whose `status` it may have just
        rewritten in memory (the lazy transition / stale-pending repair). That
        must be irrelevant here — only worktree_registered and branch decide.
        """
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotstale", 1, storage_root, live=1)
        group_id = "qslotstale.default.0001"
        row = dict(svc.db_git.get_state(group_id))
        for fake_status in ("none", "awaiting_choice", "merged", "discarded", None):
            row["status"] = fake_status
            assert svc.effective_src_root_ex("qslotstale", group_id, row) == \
                svc.effective_src_root_ex("qslotstale", group_id)

    def test_the_two_row_sources_have_the_same_shape(self, tmp_db, storage_root):
        """`list_states_of_project_any` row ≡ `get_state` row.

        Both are `SELECT *` on group_git_state, which is why the row can be
        handed over with no normalisation. Pinned so a future column added to
        only one of them is caught here instead of silently changing a verdict.
        """
        from modules.flow_gate.db import git_integration as db_git

        _make_project("qslotshape", 2, storage_root, live=1)
        scanned = {r["group_id"]: r for r in db_git.list_states_of_project_any("qslotshape")}
        for group_id, row in scanned.items():
            assert row == db_git.get_state(group_id)


# ── D. the two-argument callers are untouched ────────────────────────────────


class TestExistingCallersUnaffected:
    def test_signatures_keep_their_original_arity(self):
        import inspect

        from modules.flow_gate.services import git_service as svc

        for fn in (svc.effective_src_root_ex, svc.group_worktree_writable):
            params = list(inspect.signature(fn).parameters.values())
            assert [p.name for p in params[:2]] == ["project_id", "group_id"]
            assert params[2].name == "state"
            assert params[2].default is None
            # Positional-or-keyword, so a `lambda *args: ...` stub of the old
            # shape keeps receiving what it always received.
            assert params[2].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

    def test_effective_src_root_wrapper_still_two_arguments(self, tmp_db, storage_root):
        from modules.flow_gate.services import git_service as svc

        _make_project("qslotwrap", 1, storage_root, live=1)
        group_id = "qslotwrap.default.0001"
        expected = storage_root / "src" / _pname("qslotwrap") / _branch("qslotwrap", 1)
        assert svc.effective_src_root("qslotwrap", group_id) == expected.resolve()
        assert svc.effective_src_root("qslotwrap", None) is None
