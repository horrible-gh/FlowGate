"""0454 T0006 — `GET /projects/{id}/groups/tree?include_terminal=false` prunes terminal subtrees.

B0001: "releasing the completed-hide toggle is slow". The explorer hides final-approved (AC)
and discarded (DC) groups by default, but the server sent them anyway — every load, SSE
refresh and retry carried the terminal subtrees over the wire and through JSON.parse only for
the client to filter them straight back out.

What is pinned here:

1. **Back-compat.** Omitting the flag and passing `true` return the flat tree byte-for-byte as
   before — same nodes, same order, same fields, same `{"data": {"nodes": [...]}}` envelope.
2. **Pruning.** `false` drops final-approved groups, discarded groups, groups carrying both
   flags, and everything reachable below them through `parent_id` at ARBITRARY depth (nested
   subgroups and their documents included) — while in-progress groups, their documents,
   project / module / orphan nodes and legacy flag-less groups all stay.
3. **Shape invariants.** Original relative order, no nested `children`, no duplicate ids, and
   every surviving non-root node still finds its parent inside the result.
4. **Termination.** Cyclic and duplicated `parent_id` data cannot hang the walk.
5. **The measurement** T0006's completion criteria ask for: one representative load-scale
   fixture serialized under both variants, with node counts and UTF-8 byte counts. No timing
   threshold decides pass/fail here — the numbers are recorded, the invariants are asserted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import tree_routes  # noqa: E402
from modules.flow_gate.api.v1.tree_routes import build_overview_summary, prune_terminal_subtrees  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_raw_tree_cache():
    """`_get_raw_tree_nodes` (T0007 rev4, `force`-aware since rev5) joins concurrent NON-FORCED
    callers for the same project_id onto one in-flight `process_service.get_group_tree()` call,
    but keeps nothing once that call returns (rev3's flat TTL cache was replaced — see the module
    comment above `_get_raw_tree_nodes`). `_raw_tree_inflight` is normally empty between tests
    since every fetch clears its own entry in a `finally`, but a test that raises mid-fetch (or a
    future one added without going through the fixture) could leave an entry behind and silently
    serve a stale join to the next test's stub — cleared before AND after every test in this file
    as a guard against that.
    """
    tree_routes._raw_tree_inflight.clear()
    yield
    tree_routes._raw_tree_inflight.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Synthetic flat-tree fixtures (T0006 §1.3 — the helper is exercised without a DB)
# ══════════════════════════════════════════════════════════════════════════════

def _node(node_id, parent_id, node_type, **extra):
    node = {
        "id": node_id,
        "parent_id": parent_id,
        "node_type": node_type,
        "type_code": None,
        "number": None,
        "filename": None,
        "label": node_id,
        "has_md": False,
        "md_path": None,
    }
    node.update(extra)
    return node


def _group(node_id, parent_id, *, final=False, discarded=False, flags=True):
    extra = {"is_final_approved": final, "is_discarded": discarded} if flags else {}
    return _node(node_id, parent_id, "group", **extra)


def _doc(node_id, parent_id, type_code="T"):
    return _node(node_id, parent_id, "document", type_code=type_code, label=f"[{type_code}]: {node_id}")


PROJECT = _node("project:p", None, "project")
MODULE_A = _node("module:p:default", "project:p", "module")
MODULE_B = _node("module:p:infra", "project:p", "module")
# A module whose only group is terminal — it must survive the pruning empty (registered-module
# display contract), not disappear with its group.
MODULE_EMPTY = _node("module:p:archive", "project:p", "module")

MIXED_TREE = [
    PROJECT,
    MODULE_A,
    # G1: final-approved, with a direct document AND a nested subgroup that has its own
    # document. The whole branch goes, which a single `parent_id != group_id` filter misses.
    _group("p.default.0001", "module:p:default", final=True),
    _doc("p.default.0001.0001-R", "p.default.0001", "R"),
    _group("p.default.0001.sub", "p.default.0001"),
    _doc("p.default.0001.sub.0001-T", "p.default.0001.sub"),
    _group("p.default.0001.sub.deeper", "p.default.0001.sub"),
    _doc("p.default.0001.sub.deeper.0001-T", "p.default.0001.sub.deeper"),
    # G2: in progress — stays, with everything under it.
    _group("p.default.0002", "module:p:default", final=False),
    _doc("p.default.0002.0001-R", "p.default.0002", "R"),
    _group("p.default.0002.sub", "p.default.0002"),
    _doc("p.default.0002.sub.0001-T", "p.default.0002.sub"),
    # G3: legacy payload with NO terminal flags at all — must not be hidden.
    _group("p.default.0003", "module:p:default", flags=False),
    _doc("p.default.0003.0001-T", "p.default.0003"),
    MODULE_B,
    # G4: discarded.
    _group("p.infra.0004", "module:p:infra", discarded=True),
    _doc("p.infra.0004.0003-DC", "p.infra.0004", "DC"),
    # G5: BOTH flags true.
    _group("p.infra.0005", "module:p:infra", final=True, discarded=True),
    _doc("p.infra.0005.0001-R", "p.infra.0005", "R"),
    _doc("p.infra.0005.0002-T", "p.infra.0005"),
    # G6: in progress, and the orphan bucket next to it.
    _group("p.infra.0006", "module:p:infra"),
    _doc("p.infra.0006.0001-R", "p.infra.0006", "R"),
    _node("orphan:module:p:infra:p.infra.9999", "module:p:infra", "orphan"),
    _doc("p.infra.9999.0001-T", "orphan:module:p:infra:p.infra.9999"),
    MODULE_EMPTY,
    _group("p.archive.0007", "module:p:archive", final=True),
    _doc("p.archive.0007.0001-R", "p.archive.0007", "R"),
]

TERMINAL_IDS = {
    "p.default.0001", "p.default.0001.0001-R",
    "p.default.0001.sub", "p.default.0001.sub.0001-T",
    "p.default.0001.sub.deeper", "p.default.0001.sub.deeper.0001-T",
    "p.infra.0004", "p.infra.0004.0003-DC",
    "p.infra.0005", "p.infra.0005.0001-R", "p.infra.0005.0002-T",
    "p.archive.0007", "p.archive.0007.0001-R",
}


class TestPruneHelper:
    def test_terminal_groups_and_their_whole_subtree_are_removed(self):
        kept = prune_terminal_subtrees(MIXED_TREE)
        kept_ids = {node["id"] for node in kept}
        assert kept_ids.isdisjoint(TERMINAL_IDS)
        # Depth is the point: the nested subgroup and the document two levels under the
        # terminal group go too. A `.filter(parent_id != group_id)` pass would keep them.
        assert "p.default.0001.sub.deeper.0001-T" not in kept_ids

    def test_in_progress_groups_documents_and_structural_nodes_stay(self):
        kept_ids = {node["id"] for node in prune_terminal_subtrees(MIXED_TREE)}
        for node_id in (
            "project:p", "module:p:default", "module:p:infra", "module:p:archive",
            "p.default.0002", "p.default.0002.0001-R",
            "p.default.0002.sub", "p.default.0002.sub.0001-T",
            "p.infra.0006", "p.infra.0006.0001-R",
            "orphan:module:p:infra:p.infra.9999", "p.infra.9999.0001-T",
        ):
            assert node_id in kept_ids, node_id
        assert len(kept_ids) == len(MIXED_TREE) - len(TERMINAL_IDS)

    def test_a_module_left_empty_by_the_pruning_still_ships(self):
        kept = prune_terminal_subtrees(MIXED_TREE)
        assert any(node["id"] == "module:p:archive" for node in kept)
        assert not any(node["parent_id"] == "module:p:archive" for node in kept)

    def test_a_group_without_terminal_flags_is_not_hidden(self):
        kept_ids = {node["id"] for node in prune_terminal_subtrees(MIXED_TREE)}
        assert "p.default.0003" in kept_ids
        assert "p.default.0003.0001-T" in kept_ids
        # ...and neither is an explicit False.
        assert "p.default.0002" in kept_ids

    def test_relative_order_and_the_original_objects_are_preserved(self):
        kept = prune_terminal_subtrees(MIXED_TREE)
        expected = [node for node in MIXED_TREE if node["id"] not in TERMINAL_IDS]
        assert [node["id"] for node in kept] == [node["id"] for node in expected]
        # Same objects, not rebuilt copies: no field can have been rewritten.
        for kept_node, source_node in zip(kept, expected):
            assert kept_node is source_node

    def test_the_result_stays_a_well_formed_flat_tree(self):
        kept = prune_terminal_subtrees(MIXED_TREE)
        ids = [node["id"] for node in kept]
        assert len(ids) == len(set(ids))
        by_id = {node["id"]: node for node in kept}
        assert [node["node_type"] for node in kept if node["parent_id"] is None] == ["project"]
        for node in kept:
            if node["parent_id"] is not None:
                assert node["parent_id"] in by_id, node["id"]
        assert [node["id"] for node in kept if "children" in node] == []

    def test_nothing_is_removed_when_no_group_is_terminal(self):
        healthy = [node for node in MIXED_TREE if node["id"] not in TERMINAL_IDS]
        assert [n["id"] for n in prune_terminal_subtrees(healthy)] == [n["id"] for n in healthy]

    def test_a_parent_id_cycle_below_a_terminal_root_terminates(self):
        """The walk has to end even when the data loops back on itself.

        A single-parent-per-node list cannot express a loop on its own, so the loop is closed
        the way malformed real data would close it: a DUPLICATED id. "cycle-a" appears twice —
        once under the terminal group, once under its own grandchild — so following parent->
        children runs a -> b -> c -> a forever without the visited set.
        """
        cyclic = [
            PROJECT,
            MODULE_A,
            _group("g-terminal", "module:p:default", final=True),
            _group("cycle-a", "g-terminal"),
            _group("cycle-b", "cycle-a"),
            _group("cycle-c", "cycle-b"),
            _group("cycle-a", "cycle-c"),
            _doc("cycle-doc", "cycle-c"),
            _group("p.default.0002", "module:p:default"),
        ]
        kept_ids = [node["id"] for node in prune_terminal_subtrees(cyclic)]
        assert kept_ids == ["project:p", "module:p:default", "p.default.0002"]

    def test_a_cycle_that_no_terminal_group_reaches_is_left_alone(self):
        """Termination must not be bought by dropping data the pruning has no claim on."""
        cyclic = [
            PROJECT,
            MODULE_A,
            _group("g-terminal", "module:p:default", final=True),
            _doc("g-terminal.0001-R", "g-terminal", "R"),
            _group("loop-a", "loop-b"),
            _group("loop-b", "loop-a"),
        ]
        kept_ids = [node["id"] for node in prune_terminal_subtrees(cyclic)]
        assert kept_ids == ["project:p", "module:p:default", "loop-a", "loop-b"]

    def test_duplicate_ids_do_not_break_the_walk(self):
        duplicated = [
            PROJECT,
            MODULE_A,
            _group("dup", "module:p:default", final=True),
            _group("dup", "module:p:default", final=True),
            _doc("dup-doc", "dup"),
            _group("p.default.0002", "module:p:default"),
        ]
        kept_ids = [node["id"] for node in prune_terminal_subtrees(duplicated)]
        assert kept_ids == ["project:p", "module:p:default", "p.default.0002"]

    def test_an_empty_tree_is_handled(self):
        assert prune_terminal_subtrees([]) == []


# ══════════════════════════════════════════════════════════════════════════════
# Route contract (T0006 §1.1) — the handler is a plain def, called directly
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def stub_tree(monkeypatch):
    """Serve MIXED_TREE from the route without touching a database."""
    calls: list[str] = []

    def _fake(project_id: str):
        calls.append(project_id)
        return {"nodes": [dict(node) for node in MIXED_TREE]}

    monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _fake)
    return calls


class TestRouteContract:
    def test_omitting_the_flag_returns_the_full_tree(self, stub_tree):
        body = tree_routes.get_groups_tree("p")
        assert [node["id"] for node in body["data"]["nodes"]] == [n["id"] for n in MIXED_TREE]

    def test_include_terminal_true_is_identical_to_omitting_it(self, stub_tree):
        default_body = tree_routes.get_groups_tree("p")
        explicit_body = tree_routes.get_groups_tree("p", include_terminal=True)
        assert default_body == explicit_body
        assert json.dumps(default_body, sort_keys=True) == json.dumps(explicit_body, sort_keys=True)

    def test_include_terminal_false_prunes(self, stub_tree):
        body = tree_routes.get_groups_tree("p", include_terminal=False)
        ids = {node["id"] for node in body["data"]["nodes"]}
        assert ids.isdisjoint(TERMINAL_IDS)
        assert "p.default.0002.sub.0001-T" in ids

    def test_the_envelope_and_the_branch_argument_are_unchanged(self, stub_tree):
        # `include_summary` is passed explicitly (False) here, not omitted: a direct call (this
        # whole TestRouteContract class) hands an omitted parameter FastAPI's `Query(False, ...)`
        # sentinel object rather than the literal `False` it resolves to over real HTTP — and
        # that sentinel is truthy, so omitting it here would wrongly add `overview_summary`. See
        # the class comment above TestRouteOverHttp for the same quirk on `include_terminal`,
        # and TestRouteOverHttp.test_the_tree_route_over_http_omits_the_summary_by_default below
        # for the real-HTTP omission case this class cannot exercise.
        body = tree_routes.get_groups_tree("p", branch="feature-x", include_terminal=False, include_summary=False)
        assert set(body) == {"data"}
        # 0454 T0007 rev3 review fix: rev2 had added `overview_summary` here UNCONDITIONALLY,
        # which changed the `include_terminal=true` (default) response for every pre-existing
        # caller — exactly what T0006 §1.1 ("그대로 반환한다") rules out (review finding on rev2).
        # rev5 folds the aggregate back into this same envelope, but ONLY behind the opt-in
        # `include_summary` flag — see TestOverviewSummary below.
        assert set(body["data"]) == {"nodes"}
        assert isinstance(body["data"]["nodes"], list)
        # The compatibility `branch` argument is still accepted and still changes nothing.
        assert body == tree_routes.get_groups_tree("p", include_terminal=False, include_summary=False)

    def test_include_summary_is_opt_in_and_additive_only(self, stub_tree):
        # The rev2 mistake, precisely: adding `overview_summary` must never change what a caller
        # who does not ask for it receives. Same nodes, same order — `include_summary` only ever
        # ADDS a key, never alters `nodes`. (`include_summary=False` passed explicitly — see the
        # Query-sentinel note in test_the_envelope_and_the_branch_argument_are_unchanged above.)
        without = tree_routes.get_groups_tree("p", include_terminal=True, include_summary=False)
        with_summary = tree_routes.get_groups_tree("p", include_terminal=True, include_summary=True)
        assert set(without["data"]) == {"nodes"}
        assert set(with_summary["data"]) == {"nodes", "overview_summary"}
        assert with_summary["data"]["nodes"] == without["data"]["nodes"]
        assert with_summary["data"]["overview_summary"] == build_overview_summary(MIXED_TREE)

    def test_pruning_issues_no_extra_tree_query(self, stub_tree):
        tree_routes.get_groups_tree("p", include_terminal=False)
        assert stub_tree == ["p"]

    def test_the_pruned_response_carries_no_nested_children(self, stub_tree):
        body = tree_routes.get_groups_tree("p", include_terminal=False)
        assert [node["id"] for node in body["data"]["nodes"] if "children" in node] == []


# ══════════════════════════════════════════════════════════════════════════════
# The same contract over real HTTP — FastAPI's own query parsing
# ══════════════════════════════════════════════════════════════════════════════
#
# The direct-call tests above run the handler body, but they cannot exercise the part of the
# contract that lives in the signature: `include_terminal: bool = Query(True)`. Calling the
# function with the argument omitted hands it the Query default object, not the boolean, so
# "omitting it returns the full tree" would pass there even if the declared default were
# wrong. These go through the router.

class TestRouteOverHttp:
    URL = "/flowgate/api/v1/projects/p/groups/tree"

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app import app

        monkeypatch.setattr(
            tree_routes.process_service,
            "get_group_tree",
            lambda _pid: {"nodes": [dict(node) for node in MIXED_TREE]},
        )
        return TestClient(app)

    def test_omitting_the_parameter_returns_the_full_tree(self, client):
        res = client.get(self.URL)
        assert res.status_code == 200
        nodes = res.json()["data"]["nodes"]
        assert [n["id"] for n in nodes] == [n["id"] for n in MIXED_TREE]

    def test_true_is_byte_identical_to_omitting_it(self, client):
        omitted = client.get(self.URL)
        explicit = client.get(self.URL, params={"include_terminal": "true"})
        assert explicit.status_code == 200
        assert explicit.content == omitted.content

    def test_false_prunes_the_terminal_subtrees(self, client):
        res = client.get(self.URL, params={"include_terminal": "false"})
        assert res.status_code == 200
        ids = {n["id"] for n in res.json()["data"]["nodes"]}
        assert ids.isdisjoint(TERMINAL_IDS)
        # ...and keeps everything else, at every depth.
        assert "p.default.0002.sub.0001-T" in ids
        assert "module:p:archive" in ids
        assert "p.infra.9999.0001-T" in ids

    def test_the_branch_parameter_still_rides_along(self, client):
        res = client.get(self.URL, params={"branch": "feature-x", "include_terminal": "false"})
        assert res.status_code == 200
        assert res.json() == client.get(self.URL, params={"include_terminal": "false"}).json()

    def test_the_usual_boolean_spellings_are_accepted(self, client):
        pruned = client.get(self.URL, params={"include_terminal": "false"}).json()
        full = client.get(self.URL).json()
        for falsey in ("false", "False", "0"):
            assert client.get(self.URL, params={"include_terminal": falsey}).json() == pruned, falsey
        for truthy in ("true", "True", "1"):
            assert client.get(self.URL, params={"include_terminal": truthy}).json() == full, truthy

    def test_include_summary_false_is_byte_identical_to_omitting_it(self, client):
        # Unlike TestRouteContract's direct calls, a real HTTP request that omits
        # `include_summary` resolves FastAPI's `Query(False, ...)` default to the actual
        # boolean, so omission and an explicit `false` are genuinely identical here.
        omitted = client.get(self.URL, params={"include_terminal": "false"})
        explicit = client.get(self.URL, params={"include_terminal": "false", "include_summary": "false"})
        assert explicit.status_code == 200
        assert explicit.content == omitted.content

    def test_force_true_over_real_http_bypasses_an_in_flight_non_forced_request(self, monkeypatch):
        # The end-to-end version of TestRawTreeNodes's
        # test_a_forced_caller_never_joins_an_in_flight_fetch_however_recent — through the actual
        # router, over actual HTTP, using TestClient's own thread pool for concurrency, so the
        # `force` Query parameter's real-HTTP boolean parsing is exercised too, not just the
        # plain-Python default this class exists to cover for `include_terminal`.
        from fastapi.testclient import TestClient
        from app import app

        calls: list[str] = []
        entered_db_call = threading.Event()
        release_db_call = threading.Event()

        def _slow_fake(project_id: str):
            calls.append(project_id)
            if len(calls) == 1:
                entered_db_call.set()
                assert release_db_call.wait(timeout=5), "test deadlocked waiting to be released"
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _slow_fake)
        client = TestClient(app)

        results: dict[str, object] = {}

        def _call_non_forced():
            results["a"] = client.get(self.URL)

        first = threading.Thread(target=_call_non_forced)
        first.start()
        assert entered_db_call.wait(timeout=5), "the non-forced request never reached the DB call"

        results["b"] = client.get(self.URL, params={"force": "true"})
        assert calls == ["p", "p"], "force=true must not have joined the in-flight non-forced request"
        assert results["b"].status_code == 200

        release_db_call.set()
        first.join(timeout=5)
        assert results["a"].status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Overview summary (T0007) — the aggregate that replaced the full-tree preload
# ══════════════════════════════════════════════════════════════════════════════
#
# 0007-TR rev0 kept the MainPanel overview cards (총 문서 수 / 진행 중 / 타입 분포) populated by
# warming the FULL group tree on every default-screen entry, alongside the sidebar's own pruned
# default fetch — inverting the point of the display-state pruning work (rev0 review finding).
# rev1 fixed the transfer cost with a SEPARATE `/groups/tree/overview_summary` route, but that
# route called process_service.get_group_tree() a second time whenever it ran alongside
# `/groups/tree` on the same page — which the default overview screen always does — and its
# client cache was never refetched on an ordinary refresh (both rev1 review findings). rev2 rode
# `overview_summary` inside the `/groups/tree` response itself UNCONDITIONALLY, which fixed both
# of rev1's problems but changed the `include_terminal=true` (default) response for every
# pre-existing `/groups/tree` caller — exactly what T0006 §1.1 rules out (review finding on rev2).
#
# rev3 put `get_groups_tree_overview_summary` back as its own route (rev1's shape), and had both
# it and `get_groups_tree` read the raw node list through `_get_raw_tree_nodes`. rev3's version of
# that function memoized `process_service.get_group_tree()` per project_id for a flat 5-second
# TTL, which the rev3 review rejected on two counts (cache-then-DB-call ordering let two requests
# on an empty cache both miss; nothing invalidated on a write). rev4 replaced the TTL cache with a
# single-flight JOIN — concurrent callers share the ONE call actually in flight, nothing is kept
# once it returns — but the rev4 review found two remaining gaps: the join did not know about
# writes (a post-write caller could still join a pre-write fetch), and because the tree route and
# the summary route were fetched by two INDEPENDENT Vue watchers, nothing guaranteed the two
# requests actually overlapped — a sequential arrival cost two full DB reads every time, which
# this file's own `test_sequential_tree_then_summary_calls_each_cost_their_own_db_call` (removed
# below) had been asserting as the INTENTIONAL behavior.
#
# rev5 removes both gaps at the root:
#
# 1. `get_groups_tree_overview_summary` is GONE. `overview_summary` folds into `/groups/tree`
#    itself behind the OPT-IN `include_summary` query flag (default `False`) — see
#    TestRouteContract.test_include_summary_is_opt_in_and_additive_only above. This is NOT rev2's
#    design: rev2 changed the default envelope; here the key only ever appears on a request that
#    explicitly asks for it, so every pre-existing caller (none of which could pass a flag that
#    did not exist yet) is unaffected. The client's ONE caller that wants the aggregate
#    (GroupExplorer.vue, via explorer.ts's fetchGroupTree) now gets it on the SAME request it
#    already makes for the sidebar tree — there is no second request left to race or duplicate.
# 2. `_get_raw_tree_nodes` gains a write-generation guard (see the module note above it) so a
#    caller arriving after a write can never join a fetch that predates that write.
#
# What is left to pin here: the aggregate's own math, that `include_summary` behaves identically
# regardless of `include_terminal`, and the write-generation guard's actual race behavior — the
# concurrent-join and failure-propagation tests migrate to TestRawTreeNodes below since they
# exercise `_get_raw_tree_nodes` directly and no longer need two different routes to do it.

class TestOverviewSummary:
    def test_the_aggregate_matches_a_hand_counted_fixture(self):
        # MIXED_TREE has 12 document nodes: R x5, T x6, DC x1 (counted by hand against the
        # fixture above). working_groups is 5 -- every parent whose LAST document (by `number`,
        # ties keep the first-seen one) is type T; a plain single-letter R head does not
        # count (xR-family needs length >= 2), and terminal groups' documents never carry
        # is_final_approved/is_discarded (those are group-only fields) so nothing is excluded
        # on that account here either -- same as the MainPanel computation this replaces.
        summary = build_overview_summary(MIXED_TREE)
        assert summary == {
            "total_documents": 12,
            "working_groups": 5,
            "type_distribution": [
                {"type": "R", "count": 5},
                {"type": "T", "count": 6},
                {"type": "DC", "count": 1},
            ],
        }

    def test_the_summary_is_identical_regardless_of_which_tree_variant_asked_for_it(self, stub_tree):
        # Project-wide by construction (built from raw_nodes, before pruning runs) -- a caller
        # that fetched the pruned tree for the sidebar gets the same totals as one that fetched
        # the full tree, not a partial count scoped to whichever subtree it happened to ask for.
        pruned = tree_routes.get_groups_tree("p", include_terminal=False, include_summary=True)
        full = tree_routes.get_groups_tree("p", include_terminal=True, include_summary=True)
        assert (
            pruned["data"]["overview_summary"]
            == full["data"]["overview_summary"]
            == build_overview_summary(MIXED_TREE)
        )

    def test_the_summary_counts_documents_the_pruned_nodes_would_drop(self, stub_tree):
        # This is the property that lets the overview cards stay accurate off the pruned
        # sidebar load alone: the summary (from the unpruned tree) counts MORE documents than
        # the SAME response's pruned `nodes` carries, because it still includes the terminal
        # subtrees pruning drops from `nodes`.
        body = tree_routes.get_groups_tree("p", include_terminal=False, include_summary=True)
        pruned_doc_count = len([n for n in body["data"]["nodes"] if n["node_type"] == "document"])
        assert body["data"]["overview_summary"]["total_documents"] > pruned_doc_count

    def test_one_request_costs_exactly_one_db_call_regardless_of_arrival_order(self, stub_tree):
        # The rev4 review finding, closed at the root: since `overview_summary` now rides the
        # SAME request as `nodes` (rev5), there is no second, independently-timed request left to
        # cost a second DB call — in EITHER arrival order, because there is only one arrival.
        tree_routes.get_groups_tree("p", include_terminal=False, include_summary=True)
        assert stub_tree == ["p"]
        tree_routes.get_groups_tree("p", include_terminal=True, include_summary=True)
        assert stub_tree == ["p", "p"]  # a SECOND screen load still costs its own call, as before

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app import app

        monkeypatch.setattr(
            tree_routes.process_service,
            "get_group_tree",
            lambda _pid: {"nodes": [dict(node) for node in MIXED_TREE]},
        )
        return TestClient(app)

    def test_the_summary_field_over_real_http_matches_the_helper(self, client):
        res = client.get(
            "/flowgate/api/v1/projects/p/groups/tree",
            params={"include_terminal": "false", "include_summary": "true"},
        )
        assert res.status_code == 200
        body = res.json()
        assert set(body["data"]) == {"nodes", "overview_summary"}
        assert body["data"]["overview_summary"] == build_overview_summary(MIXED_TREE)

    def test_the_tree_route_over_http_omits_the_summary_by_default(self, client):
        res = client.get("/flowgate/api/v1/projects/p/groups/tree", params={"include_terminal": "false"})
        assert res.status_code == 200
        assert set(res.json()["data"]) == {"nodes"}

    def test_the_removed_route_is_gone(self, client):
        res = client.get("/flowgate/api/v1/projects/p/groups/tree/overview_summary")
        assert res.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# `_get_raw_tree_nodes` — single-flight join, and the rev5 `force` freshness guard
# ══════════════════════════════════════════════════════════════════════════════
#
# The review on rev4 found the plain single-flight join did not distinguish "any recent-enough
# read will do" from "this read must not predate a write I already know completed": a caller
# whose request arrives strictly AFTER a document was created could still join a raw-tree fetch
# that had started — and, worse, could STILL BE RUNNING — before that write, and be handed nodes
# that do not include it. This is exactly the case `force=true` reveal-after-create calls
# (DashboardView.vue's `handleRequirementCreated` / `handleRelatedDocCreated`) exist to avoid, and
# rev5 makes `force` carry that guarantee all the way to the DB read: a forced fetch NEVER joins
# an already in-flight one, however recent, so it cannot have started before its own caller's
# request arrived — and that caller only issues its request after awaiting the write's own HTTP
# response, so the write is guaranteed to have already committed by then.

class TestRawTreeNodes:
    def test_concurrent_first_loads_for_the_same_project_join_into_one_db_call(self, monkeypatch):
        # The case single-flight exists for: two NON-forced callers landing on an EMPTY join
        # point at nearly the same instant — they must still share one DB call. A slow fake DB
        # call lets the test hold the first caller inside `process_service.get_group_tree` until
        # the second caller has had a chance to register as a joiner, instead of racing two real
        # requests.
        calls: list[str] = []
        entered_db_call = threading.Event()
        release_db_call = threading.Event()

        def _slow_fake(project_id: str):
            calls.append(project_id)
            entered_db_call.set()
            assert release_db_call.wait(timeout=5), "test deadlocked waiting to be released"
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _slow_fake)

        results: dict[str, object] = {}
        errors: list[Exception] = []

        def _call_a():
            try:
                results["a"] = tree_routes._get_raw_tree_nodes("p")
            except Exception as exc:  # pragma: no cover - surfaced via `errors` below
                errors.append(exc)

        def _call_b():
            try:
                results["b"] = tree_routes._get_raw_tree_nodes("p")
            except Exception as exc:  # pragma: no cover - surfaced via `errors` below
                errors.append(exc)

        first = threading.Thread(target=_call_a)
        first.start()
        assert entered_db_call.wait(timeout=5), "first caller never reached the DB call"

        second = threading.Thread(target=_call_b)
        second.start()
        # Give the second caller time to acquire the join lock and start waiting on the first
        # call's completion event before that first call is allowed to finish (established
        # pattern in this suite for pinning a race window — see e.g. test_auth_preamble_0291.py).
        time.sleep(0.05)
        release_db_call.set()

        first.join(timeout=5)
        second.join(timeout=5)

        assert not errors, f"unexpected exception(s) from a joined caller: {errors}"
        assert calls == ["p"], "the second caller should have joined the first instead of starting its own DB call"
        assert results["a"] is results["b"]  # the SAME list instance, not just an equal one

    def test_a_forced_caller_never_joins_an_in_flight_fetch_however_recent(self, monkeypatch):
        # The rev4 review's exact scenario, generalized: caller A's fetch is already running
        # (it may have started a microsecond ago) when caller B arrives with `force=True` —
        # B must NOT join A's fetch, no matter how fresh A's fetch actually is, because B has no
        # way to know whether A started before or after whatever B's caller needs reflected.
        calls: list[str] = []
        entered_db_call = threading.Event()
        release_db_call = threading.Event()

        def _slow_fake(project_id: str):
            calls.append(project_id)
            if len(calls) > 1:
                # B's own DB call (if it happens) must not block on A's release gate — only A's
                # FIRST call is slow; B arrives and reads synchronously on the test's main thread.
                return {"nodes": [dict(node) for node in MIXED_TREE] + [_doc("p.default.0002.new", "p.default.0002")]}
            entered_db_call.set()
            assert release_db_call.wait(timeout=5), "test deadlocked waiting to be released"
            # Distinguish A's read from what B's read would look like: a document "created"
            # between the two calls only appears in a query that ran AFTER B's own call started,
            # never in one already in flight beforehand.
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _slow_fake)

        results: dict[str, list] = {}
        errors: list[Exception] = []

        def _call_a():
            try:
                results["a"] = tree_routes._get_raw_tree_nodes("p")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        first = threading.Thread(target=_call_a)
        first.start()
        assert entered_db_call.wait(timeout=5), "first caller never reached the DB call"

        # B arrives with force=True while A is STILL running. It must not join A — it must start
        # its own fresh read even though that means a second concurrent DB call.
        results["b"] = tree_routes._get_raw_tree_nodes("p", force=True)
        assert calls == ["p", "p"], "the forced caller must not have joined the in-flight fetch"
        assert {n["id"] for n in results["b"]} == {n["id"] for n in MIXED_TREE} | {"p.default.0002.new"}

        release_db_call.set()
        first.join(timeout=5)
        assert not errors, f"unexpected exception from caller A: {errors}"
        # A's own (non-forced) read is unaffected — it started before B and is allowed to finish
        # with whatever it already had in hand. Only B's join eligibility changes.
        assert {n["id"] for n in results["a"]} == {n["id"] for n in MIXED_TREE}

    def test_a_non_forced_caller_may_join_a_forced_fetch(self, monkeypatch):
        # The restriction is one-directional: force=True means "I will not join", not "no one
        # may join me". A plain (non-forced) caller arriving while a forced fetch is running
        # still shares it — sharing is only given up on the side that actually needs the
        # guarantee.
        calls: list[str] = []
        entered_db_call = threading.Event()
        release_db_call = threading.Event()

        def _slow_fake(project_id: str):
            calls.append(project_id)
            entered_db_call.set()
            assert release_db_call.wait(timeout=5), "test deadlocked waiting to be released"
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _slow_fake)

        results: dict[str, object] = {}

        def _call_a():
            results["a"] = tree_routes._get_raw_tree_nodes("p", force=True)

        first = threading.Thread(target=_call_a)
        first.start()
        assert entered_db_call.wait(timeout=5), "the forced caller never reached the DB call"

        second = threading.Thread(target=lambda: results.__setitem__("b", tree_routes._get_raw_tree_nodes("p")))
        second.start()
        time.sleep(0.05)
        release_db_call.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert calls == ["p"], "the non-forced caller should have joined the forced fetch"
        assert results["a"] is results["b"]

    def test_two_concurrently_overlapping_forced_callers_do_not_join_each_other(self, monkeypatch):
        # Each `force=True` caller insists on its own guarantee independently, even against
        # ANOTHER force=True fetch that is already running — two overlapping forced calls cost
        # two DB calls, not one. (The client already collapses two SAME-key force calls into one
        # HTTP request before either reaches the server — see explorerGroupTreeVariants.0454.spec
        # .ts's 0449-contract test — so this is the server-level property that composition relies
        # on, not a case expected to fire often in practice.)
        calls: list[str] = []
        entered_a = threading.Event()
        release_a = threading.Event()

        def _fake(project_id: str):
            calls.append(project_id)
            if len(calls) == 1:
                entered_a.set()
                assert release_a.wait(timeout=5), "A deadlocked waiting to be released"
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _fake)

        results: dict[str, object] = {}

        def _call_a():
            results["a"] = tree_routes._get_raw_tree_nodes("p", force=True)

        first = threading.Thread(target=_call_a)
        first.start()
        assert entered_a.wait(timeout=5), "the first forced caller never reached the DB call"

        # B arrives with force=True WHILE A (also forced) is still running.
        results["b"] = tree_routes._get_raw_tree_nodes("p", force=True)
        assert calls == ["p", "p"], "two overlapping forced callers must each cost their own DB call"

        release_a.set()
        first.join(timeout=5)

    def test_a_failed_fetch_propagates_to_the_joiner_and_leaves_no_entry_behind(self, monkeypatch):
        # The join point must not swallow an error into "silently return nothing", and must not
        # leave a dead entry that would make every later call for this project_id hang forever.
        entered_db_call = threading.Event()
        release_db_call = threading.Event()

        def _failing_fake(project_id: str):
            entered_db_call.set()
            assert release_db_call.wait(timeout=5), "test deadlocked waiting to be released"
            raise RuntimeError("boom")

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _failing_fake)

        errors: list[Exception] = []

        def _call_a():
            try:
                tree_routes._get_raw_tree_nodes("p")
            except Exception as exc:
                errors.append(exc)

        def _call_b():
            try:
                tree_routes._get_raw_tree_nodes("p")
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=_call_a)
        first.start()
        assert entered_db_call.wait(timeout=5), "first caller never reached the DB call"

        second = threading.Thread(target=_call_b)
        second.start()
        time.sleep(0.05)
        release_db_call.set()

        first.join(timeout=5)
        second.join(timeout=5)

        assert len(errors) == 2, f"both the original caller and its joiner should see the error: {errors}"
        assert all(str(e) == "boom" for e in errors)
        assert "p" not in tree_routes._raw_tree_inflight  # no dead entry left for the next caller

    def test_a_failed_fetch_that_lost_its_inflight_slot_to_a_forced_caller_does_not_evict_the_new_entry(self, monkeypatch):
        # If a `force=True` caller (B) arrives while a fetch (A) is running, B installs a NEW
        # `_RawTreeFetch` in `_raw_tree_inflight[project_id]` for future joiners (see the module
        # note above `_get_raw_tree_nodes`). A's own `finally` must not blindly pop that slot when
        # A later finishes (here, by failing) — it must check it still owns it — or A's completion
        # would evict B's live entry and strand a THIRD caller (C), who arrives while B is still
        # running, into starting a redundant fourth fetch instead of joining B.
        entered_a = threading.Event()
        release_a = threading.Event()
        entered_b = threading.Event()
        release_b = threading.Event()
        calls: list[str] = []

        def _fake(project_id: str):
            calls.append(project_id)
            if len(calls) == 1:  # A: slow, then fails
                entered_a.set()
                assert release_a.wait(timeout=5), "A deadlocked waiting to be released"
                raise RuntimeError("boom")
            entered_b.set()  # B: slow, then succeeds
            assert release_b.wait(timeout=5), "B deadlocked waiting to be released"
            return {"nodes": [dict(node) for node in MIXED_TREE]}

        monkeypatch.setattr(tree_routes.process_service, "get_group_tree", _fake)

        results: dict[str, object] = {}

        def _run(fn):
            try:
                fn()
                return None
            except Exception as exc:  # pragma: no cover - collected via `results`
                return exc

        a_thread = threading.Thread(target=lambda: results.__setitem__("a_error", _run(lambda: tree_routes._get_raw_tree_nodes("p"))))
        a_thread.start()
        assert entered_a.wait(timeout=5), "A never reached the DB call"

        # B arrives with force=True while A is still running: B must NOT join A.
        b_thread = threading.Thread(target=lambda: results.__setitem__("b", tree_routes._get_raw_tree_nodes("p", force=True)))
        b_thread.start()
        assert entered_b.wait(timeout=5), "B never reached the DB call (it wrongly joined A instead)"
        assert calls == ["p", "p"]

        # A fails now, WHILE B is still the live inflight entry. The bug this test guards
        # against: A's `finally` unconditionally pops `_raw_tree_inflight["p"]`, deleting B's
        # entry out from under it.
        release_a.set()
        a_thread.join(timeout=5)
        assert isinstance(results["a_error"], RuntimeError) and str(results["a_error"]) == "boom"

        # C arrives (non-forced) while B is still running. If A's failure had evicted B's entry,
        # C would see no in-flight fetch and start a third DB call.
        c_thread = threading.Thread(target=lambda: results.__setitem__("c", tree_routes._get_raw_tree_nodes("p")))
        c_thread.start()
        time.sleep(0.05)
        release_b.set()
        b_thread.join(timeout=5)
        c_thread.join(timeout=5)

        assert calls == ["p", "p"], "C must have joined B, not started its own DB call"
        assert results["b"] is results["c"]
        assert "p" not in tree_routes._raw_tree_inflight


# ══════════════════════════════════════════════════════════════════════════════
# Load-scale measurement (T0006 completion criteria) -- one fixture, both variants
# ══════════════════════════════════════════════════════════════════════════════
#
# Same shape and size as the 0449 load fixture (2 modules x 84 groups x 34 documents +
# 1 orphan bucket + 1 orphan document = 5,885 nodes), built from the real
# process_service.get_group_tree over a seeded sqlite DB so the measured bytes are the
# server's actual payload, not a hand-written stand-in.
#
# What differs from the 0449 fixture is the TERMINAL MIX. That fixture marks exactly one
# group per module final-approved, which is not what an aged project looks like — and the
# reduction the toggle is about depends entirely on that ratio. Here half the groups are
# final-approved and a further seventh discarded, and the ratio is asserted below so the
# recorded numbers can be read against a stated composition rather than an implied one.

LOAD_GROUPS_PER_MODULE = 84
LOAD_DOCS_PER_GROUP = 34
LOAD_MODULES = ("default", "infra")
LOAD_PROJECT = "t0454proj"


def _is_final_approved(group_index: int) -> bool:
    return group_index % 2 == 0


def _is_discarded(group_index: int) -> bool:
    return group_index % 7 == 3


class _ReadOnlyStore:
    """Minimal store shim over a seeded sqlite connection (mirrors test_t506/0449)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()


def _seed_load_db(conn: sqlite3.Connection):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, 'T0454 tree', ?, ?)",
        (LOAD_PROJECT, now, now),
    )
    seq = 0
    for module_index, module in enumerate(LOAD_MODULES):
        for group_index in range(LOAD_GROUPS_PER_MODULE):
            group_id = f"{LOAD_PROJECT}.{module}.{module_index}{group_index:03d}"
            conn.execute(
                "INSERT OR IGNORE INTO groups "
                "(group_id, project_id, module, title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'OPEN', ?, ?)",
                (group_id, LOAD_PROJECT, module, f"Group {group_id}", now, now),
            )
            for doc_index in range(LOAD_DOCS_PER_GROUP):
                seq += 1
                if doc_index == 0:
                    type_code = "R"
                    # The R doc at wf_done is what makes the group final-approved
                    # (process_service.get_group_tree, D0002 §2).
                    review = "wf_done" if _is_final_approved(group_index) else "approved"
                elif doc_index == 1 and _is_discarded(group_index):
                    # A file-less DC record is what makes a group discarded.
                    type_code = "DC"
                    review = "approved"
                else:
                    type_code = "T"
                    review = "approved"
                conn.execute(
                    "INSERT INTO documents "
                    "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
                    " doc_review_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                    (
                        f"{group_id}.{doc_index:04d}-{type_code}",
                        LOAD_PROJECT, module, group_id, type_code, seq,
                        f"{type_code} doc {seq}", review, now, now,
                    ),
                )
    seq += 1
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        " doc_review_status, created_at, updated_at) "
        "VALUES (?, ?, 'default', ?, 'T', ?, 'orphan doc', 'draft', 'approved', ?, ?)",
        (f"{LOAD_PROJECT}.default.9999.0001-T", LOAD_PROJECT, f"{LOAD_PROJECT}.default.9999", seq, now, now),
    )
    conn.commit()


@contextmanager
def _load_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    for migration in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(migration.read_text(encoding="utf-8"))
        except Exception:
            # Some migrations target tables this suite does not use; the three read here are
            # all present. Same handling as test_t506 / the 0449 tree suite.
            pass
    conn.execute("PRAGMA foreign_keys = OFF")
    _seed_load_db(conn)
    try:
        yield conn
    finally:
        conn.close()
        os.unlink(db_path)


@pytest.fixture(scope="module")
def load_variants() -> dict:
    """Build the load tree once, then serialize it through the route under BOTH variants."""
    from unittest.mock import patch

    from modules.flow_gate.db import connection as _conn
    from modules.flow_gate import process_service

    with _load_db() as conn:
        with patch.object(_conn, "STORE", _ReadOnlyStore(conn)):
            tree = process_service.get_group_tree(LOAD_PROJECT)

    nodes = tree["nodes"]
    with patch.object(process_service, "get_group_tree", lambda _pid: {"nodes": list(nodes)}):
        full = tree_routes.get_groups_tree(LOAD_PROJECT, include_terminal=True)
        pruned = tree_routes.get_groups_tree(LOAD_PROJECT, include_terminal=False)

    def _bytes(body: dict) -> int:
        # UTF-8 bytes of the serialized response — what actually travels the wire.
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    return {
        "full_nodes": full["data"]["nodes"],
        "pruned_nodes": pruned["data"]["nodes"],
        "full_bytes": _bytes(full),
        "pruned_bytes": _bytes(pruned),
    }


class TestLoadScaleVariants:
    def test_the_fixture_is_the_load_shape_with_a_stated_terminal_mix(self, load_variants):
        nodes = load_variants["full_nodes"]
        expected_nodes = (
            1                                                        # project
            + len(LOAD_MODULES)                                      # modules
            + len(LOAD_MODULES) * LOAD_GROUPS_PER_MODULE             # groups
            + len(LOAD_MODULES) * LOAD_GROUPS_PER_MODULE * LOAD_DOCS_PER_GROUP
            + 1 + 1                                                  # orphan bucket + its doc
        )
        assert len(nodes) == expected_nodes == 5_885

        groups = [n for n in nodes if n["node_type"] == "group"]
        assert len(groups) == len(LOAD_MODULES) * LOAD_GROUPS_PER_MODULE
        final = [g for g in groups if g["is_final_approved"]]
        discarded = [g for g in groups if g["is_discarded"]]
        terminal = [g for g in groups if g["is_final_approved"] or g["is_discarded"]]
        # 42 of 84 per module final-approved, 12 of 84 discarded, 6 of those overlapping.
        assert len(final) == 84
        assert len(discarded) == 24
        assert len(terminal) == 96
        assert len(terminal) / len(groups) == pytest.approx(96 / 168)

    def test_the_pruned_variant_drops_exactly_the_terminal_subtrees(self, load_variants):
        full = load_variants["full_nodes"]
        pruned = load_variants["pruned_nodes"]
        terminal_group_ids = {
            n["id"] for n in full
            if n["node_type"] == "group" and (n["is_final_approved"] or n["is_discarded"])
        }
        dropped = {n["id"] for n in full} - {n["id"] for n in pruned}
        # Each terminal group plus its own documents; this fixture has no nested subgroups
        # (the real tree has none either — that depth is covered by the synthetic fixtures).
        assert dropped == terminal_group_ids | {
            n["id"] for n in full if n["parent_id"] in terminal_group_ids
        }
        assert len(dropped) == 96 * (1 + LOAD_DOCS_PER_GROUP)

    def test_the_pruned_variant_is_still_a_well_formed_flat_tree(self, load_variants):
        pruned = load_variants["pruned_nodes"]
        ids = [n["id"] for n in pruned]
        assert len(ids) == len(set(ids))
        by_id = {n["id"]: n for n in pruned}
        assert [n["node_type"] for n in pruned if n["parent_id"] is None] == ["project"]
        for node in pruned:
            if node["parent_id"] is not None:
                assert node["parent_id"] in by_id, node["id"]
        assert [n["id"] for n in pruned if "children" in n] == []
        # Both modules and the orphan bucket survive.
        assert len([n for n in pruned if n["node_type"] == "module"]) == len(LOAD_MODULES)
        assert any(n["node_type"] == "orphan" for n in pruned)

    def test_the_full_variant_is_untouched_at_load_scale(self, load_variants):
        full = load_variants["full_nodes"]
        assert len(full) == 5_885
        pruned_ids = [n["id"] for n in load_variants["pruned_nodes"]]
        # Order preservation, checked against the full list rather than restated.
        assert pruned_ids == [n["id"] for n in full if n["id"] in set(pruned_ids)]

    def test_record_the_transfer_measurement(self, load_variants, record_property):
        """The numbers T0006's completion criteria ask to be recorded.

        Deliberately NOT a timing threshold: the assertions are on counts and monotonic
        direction only, and the measured values are printed so the work report can quote
        them from an actual run.
        """
        full_nodes = len(load_variants["full_nodes"])
        pruned_nodes = len(load_variants["pruned_nodes"])
        full_bytes = load_variants["full_bytes"]
        pruned_bytes = load_variants["pruned_bytes"]
        node_drop = full_nodes - pruned_nodes
        byte_drop = full_bytes - pruned_bytes

        measurement = {
            "full_nodes": full_nodes,
            "pruned_nodes": pruned_nodes,
            "node_reduction": node_drop,
            "node_reduction_pct": round(100.0 * node_drop / full_nodes, 2),
            "full_utf8_bytes": full_bytes,
            "pruned_utf8_bytes": pruned_bytes,
            "byte_reduction": byte_drop,
            "byte_reduction_pct": round(100.0 * byte_drop / full_bytes, 2),
        }
        for key, value in measurement.items():
            record_property(key, value)
        print("\n0454 T0006 include_terminal measurement: " + json.dumps(measurement))

        assert pruned_nodes < full_nodes
        assert pruned_bytes < full_bytes
        assert node_drop == 96 * (1 + LOAD_DOCS_PER_GROUP)
