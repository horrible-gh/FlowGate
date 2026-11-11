"""flowgate.v02.0119 T#1 (T-0, NR §2.6 (c)) — the merge target lookup's one diagnostic line.

``check_target_view`` writes one WARNING line (``check target lookup <status> ...``) when the view ends ``failed`` /
``timeout`` / ``git_unavailable``: status / code / reason, the stage it ended in, the error (including the
GitServiceError path of ``_compute_snapshot``, which logs nothing else), the shared read or the name that raised,
the time of each shared read, how many names were judged and how long, and how many ``inspect_workspace`` calls
were made and how long they took. The response contract does not change and a listing view (``ok`` / ``empty``)
writes nothing.

No Git and no database: the basis, the shared reads and the workspace inspection are replaced in the module, so
only the lookup's own control flow runs.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())

_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

LINE = "check target lookup"
REFS = {"develop": ("b" * 40, "commit"), "main": ("a" * 40, "commit"), "release": ("c" * 40, "commit")}


@pytest.fixture
def mt(monkeypatch):
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import group_work_base
    from modules.flow_gate.services.git import merge_target as module

    basis = module.CheckTargetBasis(project_id="p1", group_id="g1", cfg={}, base_root=Path("."),
                                    base_branch="main", work_base_ref="main", source_branch="g1_branch")
    monkeypatch.setattr(module, "CHECK_TARGET_BUDGET_SEC", 30.0)
    monkeypatch.setattr(module, "check_target_basis", lambda *_a, **_k: basis)
    monkeypatch.setattr(module, "raise_if_base_checkout_off_branch", lambda *_a: None)
    monkeypatch.setattr(group_work_base, "local_branch_refs", lambda _root: dict(REFS))
    monkeypatch.setattr(module, "check_scope_floor", lambda _basis: "f" * 40)
    monkeypatch.setattr(module, "_base_dirty", lambda _root: False)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _pid: [])
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *_a, **_k: "main")
    monkeypatch.setattr(module, "inspect_workspace", lambda _pid, _name: ("free", {}))
    # the real base slot check, for a test that runs it (``_real_base_slot``)
    monkeypatch.setattr(module, "_real_base_slot", module._raise_if_base_slot_unavailable, raising=False)
    monkeypatch.setattr(module, "_raise_if_base_slot_unavailable", lambda *_a, **_k: None)
    module.reset_check_targets()
    yield module
    assert module.settle_check_targets(10)   # flowgate.v02test.0020: no background computation outlives a test
    module.reset_check_targets()


def _view(mt, selected=None, refresh=True) -> dict:
    return mt.check_target_view("p1", "g1", selected, refresh=refresh)


def _lines(caplog, mt) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == mt.__name__ and r.getMessage().startswith(LINE)]


def _contract(view: dict) -> None:
    """The response keys are the existing contract (D0004 §3.1 / §5): nothing of the trace leaks into them."""
    assert set(view) == {"merge_target_options", "default_target_branch", "merge_target_state"}
    assert set(view["merge_target_state"]) == {"status", "code", "reason", "current", "default"}


def test_0119_a_listing_view_writes_no_line_and_keeps_no_trace(mt, caplog):
    caplog.set_level(logging.DEBUG, logger=mt.__name__)
    view = _view(mt)
    _contract(view)
    assert view["merge_target_state"]["status"] == "ok"
    assert view["merge_target_options"] == ["develop", "main", "release"]
    assert _lines(caplog, mt) == []
    assert mt._check_trace.get() is None                    # the view's context variable is reset
    cached = _view(mt, refresh=False)                       # the cached snapshot carries no trace either
    assert cached == view and _lines(caplog, mt) == []
    assert not any(key.startswith("trace") for snap in mt._check_cache.values() for key in snap)


def test_0119_a_shared_read_git_error_is_one_line_naming_the_read(mt, monkeypatch, caplog):
    """H2: the GitServiceError path (``_compute_snapshot`` :1267-1273) writes no other warning.
    flowgate.v02test.0020 rev2: the list's shared reads are the slot ledger and the branch names (``refs``)."""
    from modules.flow_gate.services.git import group_work_base
    caplog.set_level(logging.WARNING, logger=mt.__name__)

    def refs(_root):
        raise mt._git_unavailable("probe_failed")

    monkeypatch.setattr(group_work_base, "local_branch_refs", refs)
    view = _view(mt)
    _contract(view)
    state = view["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("failed", "merge_target_options_unavailable",
                                                                 "probe_failed")
    assert view["merge_target_options"] == []
    warnings = [r.getMessage() for r in caplog.records if r.name == mt.__name__]
    lines = _lines(caplog, mt)
    assert len(lines) == 1 and warnings == lines, warnings
    line = lines[0]
    for part in ("check target lookup failed ", "group=g1", "project=p1", "code=merge_target_options_unavailable",
                 "reason=probe_failed", "source=computed", "stage=shared_reads",
                 "error=GitServiceError:gate_git_unavailable/probe_failed",
                 "failed_read=refs:GitServiceError:gate_git_unavailable/probe_failed", "failed_name=-",
                 "names=0/-", "inspect_workspace=0/"):
        assert part in line, (part, line)
    shared = line.split("shared_ms=")[1].split(" ")[0]
    assert {item.split(":")[0] for item in shared.split(",")} == {"refs", "slots"}


def test_0119_one_names_unclassified_git_error_is_named(mt, monkeypatch, caplog):
    """H3 on the GitServiceError path: the code becomes the reason and the name that raised is in the line."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)

    def inspect(_pid, name):
        if name == "release":
            raise mt.GitServiceError(500, "workspace_probe_failed", "marker read failed")
        return "free", {}

    monkeypatch.setattr(mt, "inspect_workspace", inspect)
    view = _view(mt)
    state = view["merge_target_state"]
    assert (state["status"], state["reason"], view["merge_target_options"]) == (
        "failed", "workspace_probe_failed", [])
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    line = lines[0]
    for part in ("stage=names", "error=GitServiceError:workspace_probe_failed", "failed_read=-",
                 "failed_name=release", "names=3/3", "inspect_workspace=2/"):
        assert part in line, (part, line)
    assert "check target candidates failed" not in "".join(r.getMessage() for r in caplog.records)


def test_0119_one_names_plain_exception_keeps_the_existing_warning_and_adds_the_line(mt, monkeypatch, caplog):
    """H3 on the generic path (:1274-1278): the existing stack warning stays; the line names the name."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)

    def inspect(_pid, name):
        if name == "develop":
            raise RuntimeError("owner marker unreadable")
        return "free", {}

    monkeypatch.setattr(mt, "inspect_workspace", inspect)
    view = _view(mt)
    state = view["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("failed", "merge_target_options_unavailable",
                                                                 "probe_failed")
    messages = [r.getMessage() for r in caplog.records if r.name == mt.__name__]
    assert any(m.startswith("check target candidates failed for g1") for m in messages)
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    for part in ("stage=names", "error=RuntimeError", "failed_name=develop", "names=1/3", "inspect_workspace=1/"):
        assert part in lines[0], (part, lines[0])


def _background(caplog, mt) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == mt.__name__ and r.getMessage().startswith("check target background")]


def test_0119_a_spent_budget_says_where_it_went(mt, monkeypatch, caplog):
    """H1-b: the budget ran out while the names were judged. The view's line says where the computation was when the
    view gave up. flowgate.v02test.0020: the computation goes on in the background; its own line, written when it
    ends, counts every name and ``inspect_workspace`` call with the real total time (the diagnostic the view's budget
    used to cut off), and the next read gets its list."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)

    def slow(_pid, _name):
        time.sleep(0.6)
        return "free", {}

    monkeypatch.setattr(mt, "inspect_workspace", slow)
    view = _view(mt)
    _contract(view)
    state = view["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("timeout", "merge_target_options_timeout", None)
    assert view["merge_target_options"] == []
    # 0148: the timeout is cached briefly; the diagnostic line below keeps the 0119 contract.
    assert mt._check_cache[("p1", "g1")]["status"] == "timeout"
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    line = lines[0]
    # where the background computation had got to when the view gave up depends on thread timing (on an idle host:
    # ``stage=names names=0/3``); the line itself and its budget do not
    for part in ("check target lookup timeout ", "source=computed", "error=-", "budget_ms=300",
                 "entries=0 entries_ms=-"):
        assert part in line, (part, line)
    assert mt.settle_check_targets(10)
    assert _lines(caplog, mt) == lines                                 # still the view's one line
    background = _background(caplog, mt)
    assert len(background) == 1, background
    done = background[0]
    # every name judged; the work base ``main`` takes no workspace inspection, the two others 0.6 s each
    for part in ("check target background ok ", "group=g1", "source=computed", "error=-", "names=3/3",
                 "inspect_workspace=2/", "budget_ms=30000", "slowest_ms=develop:"):
        assert part in done, (part, done)
    assert int(done.split("inspect_workspace=2/")[1].split("ms")[0]) >= 1200
    assert int(done.split("total_ms=")[1].split(" ")[0]) >= 1200
    monkeypatch.setattr(mt, "inspect_workspace", lambda *_a: pytest.fail("the finished list is read, not recomputed"))
    again = _view(mt, refresh=False)
    assert again["merge_target_state"]["status"] == "ok"
    assert again["merge_target_options"] == ["develop", "main", "release"]


def test_0119_a_basis_failure_is_one_line_at_the_basis(mt, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=mt.__name__)

    def basis(*_a, **_k):
        raise mt._git_unavailable("git_disabled")

    monkeypatch.setattr(mt, "check_target_basis", basis)
    view = _view(mt)
    _contract(view)
    state = view["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("git_unavailable", "gate_git_unavailable",
                                                                 "git_disabled")
    assert view["default_target_branch"] == "main"
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    for part in ("check target lookup git_unavailable ", "stage=basis",
                 "error=GitServiceError:gate_git_unavailable/git_disabled", "shared_ms=-"):
        assert part in lines[0], (part, lines[0])


def test_0119_the_single_judgement_is_not_traced(mt, monkeypatch):
    """Saving, confirming and running (``judge_check_target``) never see a trace: ``inspect_workspace`` as is."""
    calls = []
    monkeypatch.setattr(mt, "inspect_workspace", lambda pid, name: calls.append(name) or ("free", {}))
    assert mt._check_trace.get() is None
    assert mt._inspect_traced("p1", "develop") == ("free", {}) and calls == ["develop"]


# ── 0119 T#1 rev1: the judgement ``_entry_of`` adds for a name a cached / joined snapshot did not hold ──────────────
# A cached snapshot (or another request's computation this view joined) holds the verdicts of its own computation; a
# stored target outside them is judged by ``_entry_of`` under what is left of this view's budget. That judgement is
# traced as ``entries`` (its name and ms), its error as ``failed_name`` / ``error``; the response and the cache stay as
# they were for 0119. 0148 adds short negative caching of timeout views, including an added judgement
# on a cached snapshot; a joined request still cannot replace its owner's cached result.
NEW = "new_selected"


def _judge_unlisted(mt, monkeypatch) -> None:
    """``judge_check_target`` for a name the snapshot did not list: no Git, the judgement's own steps after the name
    resolved (``_judge_local_target`` -> ``inspect_workspace`` as a view traces it)."""
    def judge(_pid, _gid, name, *, basis=None, **_k):
        return mt._judge_local_target(basis, name, "d" * 40, lambda _n: (0, None), None)

    monkeypatch.setattr(mt, "judge_check_target", judge)


def _slow_for(name: str, seconds: float, error=None):
    def inspect(_pid, branch):
        if branch == name:
            time.sleep(seconds)
            if error is not None:
                raise error
        return "free", {}
    return inspect


def _timed_out_view(selected: str) -> dict:
    """The response a view whose ``_entry_of`` spent the budget returns (unchanged from before 0119 T#1)."""
    return {
        "merge_target_options": [], "default_target_branch": "main",
        "merge_target_state": {
            "status": "timeout", "code": "merge_target_options_timeout", "reason": None,
            "current": {"branch": selected, "checked": False, "error": None, "unmerge_supported": None,
                        "warnings": [], "inherited": False},
            "default": {"branch": "main", "checked": False, "error": None, "unmerge_supported": None,
                        "warnings": []}}}


def _entries_ms(line: str, name: str) -> int:
    return int(line.split(f"entries_ms={name}:")[1].split(" ")[0].split(",")[0])


def test_0119_a_cached_snapshots_added_judgement_that_spends_the_budget_is_named(mt, monkeypatch, caplog):
    """The review's case: the cache holds ``main``'s verdict, ``new_selected``'s ``inspect_workspace`` takes 0.4 s of
    a 0.3 s budget. The line names it (``entries=1``, ``entries_ms=new_selected:>=300``) instead of ``names=0/-``
    with nothing judged."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    _judge_unlisted(mt, monkeypatch)
    assert _view(mt)["merge_target_state"]["status"] == "ok"           # computed and cached (30 s budget)
    cached = dict(next(iter(mt._check_cache.values()))["verdicts"])
    assert NEW not in cached and "main" in cached
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)
    monkeypatch.setattr(mt, "inspect_workspace", _slow_for(NEW, 0.4))
    view = _view(mt, NEW, refresh=False)
    _contract(view)
    assert view == _timed_out_view(NEW)
    snap = next(iter(mt._check_cache.values()))                         # 0148: no partial verdicts kept
    assert snap["status"] == "timeout" and snap["verdicts"] == {}
    assert snap["options"] == [] and snap["basis"] is None
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    line = lines[0]
    for part in ("check target lookup timeout ", "source=cache", "stage=entries", "error=budget_spent",
                 "failed_name=-", "names=0/-", "inspect_workspace=1/", "extra_ms=0", "entries=1",
                 f"entries_ms={NEW}:", "budget_ms=300"):
        assert part in line, (part, line)
    assert _entries_ms(line, NEW) >= 300


def test_0119_a_joined_snapshots_added_judgement_that_spends_the_budget_is_named(mt, monkeypatch, caplog):
    """The same on a joined snapshot: this view waited for another request's computation (selected None), whose
    verdicts lack ``new_selected``; judging it spends the rest of this view's budget."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    _judge_unlisted(mt, monkeypatch)
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 1.0)
    entered, release = _blocked_refs(mt, monkeypatch)
    monkeypatch.setattr(mt, "inspect_workspace", _slow_for(NEW, 1.1))
    owner: dict = {}
    thread = threading.Thread(target=lambda: owner.update(view=_view(mt, refresh=False)))
    thread.start()
    try:
        assert entered.wait(5)                     # the owner's computation is in flight
        timer = threading.Timer(0.2, release.set)
        timer.start()
        view = _view(mt, NEW, refresh=False)       # joins it, then judges NEW on its own
    finally:
        release.set()
        thread.join(5)
    assert owner["view"]["merge_target_state"]["status"] == "ok"
    _contract(view)
    assert view == _timed_out_view(NEW)
    snap = next(iter(mt._check_cache.values()))                         # the owner's snapshot, nothing added
    assert snap["status"] == "ok" and NEW not in snap["verdicts"]
    lines = _lines(caplog, mt)
    assert len(lines) == 1, lines                                       # the owner (ok) writes none
    line = lines[0]
    for part in ("check target lookup timeout ", "source=joined", "stage=entries", "error=budget_spent",
                 "names=0/-", "inspect_workspace=1/", "entries=1", f"entries_ms={NEW}:", "budget_ms=1000"):
        assert part in line, (part, line)
    assert _entries_ms(line, NEW) >= 1000


def test_0119_an_added_judgement_that_raises_past_the_budget_names_its_error(mt, monkeypatch, caplog):
    """The added judgement raised (an unclassified GitServiceError) after the budget ran out: the view times out as
    before and the line names the name and its error, not only ``budget_spent``."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    _judge_unlisted(mt, monkeypatch)
    assert _view(mt)["merge_target_state"]["status"] == "ok"
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)
    monkeypatch.setattr(mt, "inspect_workspace", _slow_for(
        NEW, 0.4, mt.GitServiceError(500, "workspace_probe_failed", "marker read failed")))
    view = _view(mt, NEW, refresh=False)
    assert view == _timed_out_view(NEW)
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    for part in ("source=cache", "stage=entries", "error=GitServiceError:workspace_probe_failed",
                 f"failed_name={NEW}", "inspect_workspace=1/", "entries=1", f"entries_ms={NEW}:"):
        assert part in lines[0], (part, lines[0])


def test_0119_an_added_judgement_inside_the_budget_writes_no_line_and_is_kept(mt, monkeypatch, caplog):
    """Unchanged cache behaviour: a name judged in time is kept in the cached snapshot and a listing view writes
    nothing; the next view reads it from the snapshot (no second ``inspect_workspace``)."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    _judge_unlisted(mt, monkeypatch)
    assert _view(mt)["merge_target_state"]["status"] == "ok"
    calls = []
    monkeypatch.setattr(mt, "inspect_workspace", lambda _pid, name: calls.append(name) or ("free", {}))
    view = _view(mt, NEW, refresh=False)
    current = view["merge_target_state"]["current"]
    assert view["merge_target_state"]["status"] == "ok"
    assert (current["branch"], current["checked"], current["error"]) == (NEW, True, None)
    assert calls == [NEW] and NEW in next(iter(mt._check_cache.values()))["verdicts"]
    assert _view(mt, NEW, refresh=False) == view and calls == [NEW]
    assert _lines(caplog, mt) == []


@pytest.mark.parametrize("phase", ["basis", "candidates", "entry"])
@pytest.mark.parametrize("clear", ["refresh", "invalidate", "expiry"])
def test_0148_timeout_is_cached_briefly_without_extending_its_age(mt, monkeypatch, phase, clear):
    """All timeout exits share a short negative snapshot; each request still describes its own selected name."""
    with monkeypatch.context() as m:
        if phase == "basis":
            def unavailable(*_a, **_k):
                raise mt._git_unavailable("probe_failed")
            m.setattr(mt, "check_target_basis", unavailable)
            m.setattr(mt, "_spent", lambda _budget: True)
        elif phase == "candidates":
            m.setattr(mt, "_spent", lambda _budget: True)
        else:
            def outlived(*_a):
                raise mt._BudgetSpent()
            m.setattr(mt, "_entry_of", outlived)
        first = _view(mt, refresh=False)
    assert first["merge_target_state"]["status"] == "timeout"
    assert first["merge_target_options"] == []
    at = mt._check_cache[("p1", "g1")]["at"]
    with monkeypatch.context() as m:
        def forbidden(*_a, **_k):
            pytest.fail("negative cache should avoid even the basis/Git probe")
        m.setattr(mt, "check_target_basis", forbidden)
        assert _view(mt, refresh=False) == first
        other = _view(mt, selected="release", refresh=False)
        assert other["merge_target_state"]["current"]["branch"] == "release"
        assert other["merge_target_state"]["current"]["checked"] is False
        assert other["merge_target_state"]["current"]["inherited"] is False
    assert mt._check_cache[("p1", "g1")]["at"] == at
    if clear == "invalidate":
        mt.invalidate_check_targets("p1", "g1")
    elif clear == "expiry":
        monkeypatch.setattr(mt, "CHECK_TARGET_TIMEOUT_CACHE_SEC", 0.0)
    refreshed = _view(mt, refresh=clear == "refresh")
    assert refreshed["merge_target_state"]["status"] == "ok"
    assert refreshed["merge_target_options"] == ["develop", "main", "release"]


def test_0148_invalidation_during_timeout_prevents_late_cache_repopulation(mt, monkeypatch):
    real = mt._compute_snapshot

    def late(basis, *_a):
        mt.invalidate_check_targets("p1", "g1")
        return mt._failed_snapshot(mt.CHECK_STATUS_TIMEOUT, mt.CHECK_OPTIONS_TIMEOUT,
                                   default_branch=basis.work_base_ref, basis=None)

    monkeypatch.setattr(mt, "_compute_snapshot", late)
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"
    monkeypatch.setattr(mt, "_compute_snapshot", real)
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "ok"


# ── flowgate.v02test.0020: the computation outlives the view's budget ───────────────────────────────────────────────
def _blocked_refs(mt, monkeypatch, refs=None):
    """The branch name read (a shared read) waits for ``release``: the computation stays in flight as long as a test
    needs. It then answers ``refs`` (default: REFS)."""
    from modules.flow_gate.services.git import group_work_base
    entered, release = threading.Event(), threading.Event()

    def listed(_root):
        entered.set()
        release.wait(5)
        return dict(REFS if refs is None else refs)

    monkeypatch.setattr(group_work_base, "local_branch_refs", listed)
    return entered, release


def test_0020_a_late_result_replaces_the_negative_snapshot_and_is_served(mt, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.2)
    entered, release = _blocked_refs(mt, monkeypatch)
    first = _view(mt, refresh=False)
    assert first["merge_target_state"]["status"] == "timeout" and entered.is_set()
    assert mt._check_cache[("p1", "g1")]["status"] == "timeout"      # 0148: the negative snapshot, for now
    joined = _view(mt, refresh=False)                                 # inside 5 s: the negative snapshot, no wait
    assert joined["merge_target_state"]["status"] == "timeout"
    release.set()
    assert mt.settle_check_targets(5)
    snap = mt._check_cache[("p1", "g1")]
    assert snap["status"] == "ok" and "late" not in snap and "stale" not in snap
    computed = []
    monkeypatch.setattr(mt, "_compute_snapshot", lambda *a: computed.append(1))
    picked = _view(mt, refresh=False)
    assert picked["merge_target_state"]["status"] == "ok"
    assert picked["merge_target_options"] == ["develop", "main", "release"]
    assert computed == [] and len(_background(caplog, mt)) == 1


def test_0020_a_refresh_joins_the_running_computation_instead_of_starting_over(mt, monkeypatch):
    """flowgate.v02test.0020 rev2: pressing refresh while the list is being computed used to cancel that computation
    and start again from nothing (on a slow host: never done). It now waits for the running one."""
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.2)
    entered, release = _blocked_refs(mt, monkeypatch)
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"
    assert entered.wait(1)
    [running] = list(mt._check_inflight.values())
    real = mt._compute_snapshot
    computed = []
    monkeypatch.setattr(mt, "_compute_snapshot", lambda *a: computed.append(1) or real(*a))
    assert _view(mt, refresh=True)["merge_target_state"]["status"] == "timeout"   # joined; its own 0.2 s ran out
    assert list(mt._check_inflight.values()) == [running] and not running.budget.expired and computed == []
    release.set()
    assert mt.settle_check_targets(5)
    assert running.result["status"] == "ok" and mt._check_cache[("p1", "g1")] is running.result
    assert _view(mt, refresh=False)["merge_target_options"] == ["develop", "main", "release"] and computed == []


def test_0020_a_newer_computation_cancels_the_older_one_of_the_same_key(mt, monkeypatch):
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.2)
    entered, release = _blocked_refs(mt, monkeypatch)
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"
    assert entered.wait(1)
    [old] = list(mt._check_inflight.values())
    mt.invalidate_check_targets("p1", "g1")                           # a server change: the old one is never cached
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"  # a new one starts
    assert old.budget.expired                                         # ... and spends what the old one had left
    new = [flight for flight in mt._check_inflight.values() if flight is not old]
    assert len(new) == 1 and not new[0].budget.expired
    release.set()
    assert mt.settle_check_targets(5)
    assert old.result["status"] == "timeout"                          # it stopped at its next budget check
    assert new[0].result["status"] == "ok"
    assert mt._check_cache[("p1", "g1")] is new[0].result
    assert not mt._check_inflight


def test_0020_a_result_after_an_invalidation_is_not_cached(mt, monkeypatch):
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.2)
    entered, release = _blocked_refs(mt, monkeypatch)
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"
    assert entered.wait(1)
    mt.invalidate_check_targets("p1", "g1")                           # a server change while it ran
    release.set()
    assert mt.settle_check_targets(5)
    assert ("p1", "g1") not in mt._check_cache


def test_0020_the_background_computation_runs_outside_the_request_context(mt, monkeypatch):
    """The request's DB read scope (``request_cache``) and Git budget never reach the computation's thread."""
    from modules.flow_gate.db import request_cache
    from modules.flow_gate.services.git import command, group_work_base

    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 1.0)
    seen = []
    monkeypatch.setattr(group_work_base, "local_branch_refs", lambda _root: seen.append(
        (request_cache.current(), command._BUDGET.get())) or dict(REFS))
    with request_cache.request_scope("settings"):
        assert _view(mt)["merge_target_state"]["status"] == "ok"
    [(scope, budget)] = seen
    assert scope is None
    assert budget is not None and budget.deadline - time.monotonic() > 1.0        # the computation's own (30 s)


def test_0020_the_diagnostic_line_reaches_the_app_file_log(mt, monkeypatch, tmp_path):
    """The 0119 line was written with a plain module logger, which propagates to a handler-less root: it never reached
    ``logs/default.log``. ``route_logging.forward_to_file_log`` hands this module's WARNING lines to LogAssist's file
    handlers, looked up per record (whenever LogAssist attached them)."""
    app = logging.getLogger("LogAssist.log")
    handler = logging.FileHandler(tmp_path / "default.log", encoding="utf-8")
    app.addHandler(handler)
    try:
        def basis(*_a, **_k):
            raise mt._git_unavailable("git_disabled")

        monkeypatch.setattr(mt, "check_target_basis", basis)
        assert _view(mt)["merge_target_state"]["status"] == "git_unavailable"
    finally:
        app.removeHandler(handler)
        handler.close()
    text = (tmp_path / "default.log").read_text(encoding="utf-8")
    assert text.count("check target lookup git_unavailable group=g1 project=p1") == 1, text


def test_0148_failed_refresh_does_not_leave_an_old_timeout(mt, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(mt, "_spent", lambda _budget: True)
        assert _view(mt, refresh=False)["merge_target_state"]["status"] == "timeout"
    with monkeypatch.context() as m:
        m.setattr(mt, "_compute_snapshot", lambda basis, *_a: mt._failed_snapshot(
            mt.CHECK_STATUS_FAILED, mt.CHECK_OPTIONS_UNAVAILABLE, "probe_failed",
            default_branch=basis.work_base_ref, basis=None))
        assert _view(mt, refresh=True)["merge_target_state"]["status"] == "failed"
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "ok"


# ── flowgate.v02test.0020 rev2: a light candidate list, served stale-while-revalidate ──────────────────────────────
def test_0020_rev2_the_list_reads_no_checkout_floor_or_tracked_changes_but_a_save_still_does(mt, monkeypatch):
    """The list reads the branch names and the slot ledger only. The base checkout's branch, its tracked changes and
    the work base history a target lacks (the scope floor) are left to the single judgement, which refuses them."""
    from modules.flow_gate.services.git import lock

    monkeypatch.setattr(mt, "_raise_if_base_slot_unavailable", mt._real_base_slot)   # the real base slot check
    monkeypatch.setattr(lock, "guard_base_free", lambda _pid: None)
    monkeypatch.setattr(lock, "base_merge_in_progress", lambda _pid: None)
    monkeypatch.setattr(mt, "open_merge_attempts", lambda _pid: [])
    for name in ("raise_if_base_checkout_off_branch", "check_scope_floor", "_base_dirty"):
        monkeypatch.setattr(mt, name, lambda *_a, _name=name: pytest.fail(f"the candidate list read {_name}"))
    view = _view(mt)
    assert view["merge_target_state"]["status"] == "ok"
    assert view["merge_target_options"] == ["develop", "main", "release"]
    assert view["merge_target_state"]["current"]["error"] is None
    # the same names judged one at a time (save / confirm / run) read and refuse them as before
    called = []
    monkeypatch.setattr(mt, "raise_if_base_checkout_off_branch", lambda *_a: called.append("checkout"))
    monkeypatch.setattr(mt, "_base_dirty", lambda _root: called.append("dirty") or True)
    basis = mt.check_target_basis("p1", "g1")
    with pytest.raises(mt.GitServiceError) as dirty:
        mt._judge_local_target(basis, "main", "a" * 40, lambda _n: pytest.fail("no count for the work base"), None)
    assert dirty.value.code == "base_dirty" and called == ["checkout", "dirty"]
    with pytest.raises(mt.GitServiceError) as lacking:
        mt._judge_local_target(basis, "develop", "b" * 40, lambda _n: called.append("floor") or (1, None), None)
    assert lacking.value.code == "merge_target_incoming_work_base" and called[-2:] == ["checkout", "floor"]


def test_0020_rev2_an_invalidated_list_is_served_at_once_and_recomputed_behind_it(mt, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    first = _view(mt, refresh=False)
    snap = mt._check_cache[("p1", "g1")]
    mt.invalidate_check_targets("p1", "g1")                           # e.g. a merge was confirmed
    assert mt._check_cache[("p1", "g1")] is snap and snap["stale"] is True
    entered, release = _blocked_refs(mt, monkeypatch, refs={**REFS, "hotfix": ("e" * 40, "commit")})
    started = time.monotonic()
    served = _view(mt, refresh=False)
    assert served == first and time.monotonic() - started < 1.0       # the stale list, without waiting
    assert entered.wait(5)                                            # ... recomputed behind it
    assert _view(mt, refresh=False) == first and len(mt._check_inflight) == 1    # one computation per key
    release.set()
    assert mt.settle_check_targets(5)
    fresh = mt._check_cache[("p1", "g1")]
    assert fresh is not snap and "stale" not in fresh
    assert _view(mt, refresh=False)["merge_target_options"] == ["develop", "hotfix", "main", "release"]
    assert _lines(caplog, mt) == [] and _background(caplog, mt) == []    # a listing revalidation writes nothing


def test_0020_rev2_an_old_list_is_served_and_revalidated_until_it_expires(mt, monkeypatch):
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "ok"
    snap = mt._check_cache[("p1", "g1")]
    real = mt._compute_snapshot
    computed = []
    monkeypatch.setattr(mt, "_compute_snapshot", lambda *a: computed.append(1) or real(*a))
    snap["at"] -= mt.CHECK_TARGET_REFRESH_SEC - 5                     # not old enough: served as it is
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "ok"
    assert mt.settle_check_targets(5) and computed == []
    snap["at"] -= 10                                                  # older than CHECK_TARGET_REFRESH_SEC
    assert _view(mt, refresh=False)["merge_target_options"] == ["develop", "main", "release"]
    assert mt.settle_check_targets(5) and computed == [1]
    renewed = mt._check_cache[("p1", "g1")]
    assert renewed is not snap and time.monotonic() - renewed["at"] < 5
    renewed["at"] -= mt.CHECK_TARGET_CACHE_SEC + 1                    # expired: not served, the read waits for one
    assert _view(mt, refresh=False)["merge_target_state"]["status"] == "ok" and computed == [1, 1]


def test_0020_rev2_a_failed_revalidation_keeps_the_stale_list_and_writes_its_line(mt, monkeypatch, caplog):
    from modules.flow_gate.services.git import group_work_base
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    first = _view(mt, refresh=False)
    snap = mt._check_cache[("p1", "g1")]
    mt.invalidate_check_targets("p1", "g1")

    def broken(_root):
        raise mt._git_unavailable("probe_failed")

    monkeypatch.setattr(group_work_base, "local_branch_refs", broken)
    assert _view(mt, refresh=False) == first                          # still served
    assert mt.settle_check_targets(5)
    assert mt._check_cache[("p1", "g1")] is snap and snap["stale"] is True
    assert _lines(caplog, mt) == []
    [line] = _background(caplog, mt)
    for part in ("check target background failed ", "source=revalidate", "stage=shared_reads",
                 "failed_read=refs:GitServiceError:gate_git_unavailable/probe_failed", "budget_ms=30000"):
        assert part in line, (part, line)
    # a refresh never serves it: it waits for a computation of its own and says what failed
    refreshed = _view(mt, refresh=True)["merge_target_state"]
    assert (refreshed["status"], refreshed["reason"]) == ("failed", "probe_failed")
