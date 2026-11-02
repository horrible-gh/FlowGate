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
    monkeypatch.setattr(module, "_branches_containing", lambda _root, _sha: set(REFS))
    monkeypatch.setattr(module, "_base_dirty", lambda _root: False)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _pid: [])
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *_a, **_k: "main")
    monkeypatch.setattr(module, "inspect_workspace", lambda _pid, _name: ("free", {}))
    monkeypatch.setattr(module, "_raise_if_base_slot_unavailable", lambda *_a: None)
    module.invalidate_check_targets()
    yield module
    module.invalidate_check_targets()


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
    """H2: the GitServiceError path (``_compute_snapshot`` :1267-1273) writes no other warning."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)

    def containing(_root, _sha):
        raise mt._git_unavailable("probe_failed")

    monkeypatch.setattr(mt, "_branches_containing", containing)
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
                 "failed_read=containing:GitServiceError:gate_git_unavailable/probe_failed", "failed_name=-",
                 "names=0/-", "inspect_workspace=0/"):
        assert part in line, (part, line)
    shared = line.split("shared_ms=")[1].split(" ")[0]
    assert {item.split(":")[0] for item in shared.split(",")} >= {"checkout", "refs", "slots", "floor", "containing"}


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


def test_0119_a_spent_budget_says_where_it_went(mt, monkeypatch, caplog):
    """H1-b: the budget ran out while the names were judged; the line counts ``inspect_workspace`` and its time."""
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)

    def slow(_pid, _name):
        time.sleep(0.4)
        return "free", {}

    monkeypatch.setattr(mt, "inspect_workspace", slow)
    view = _view(mt)
    _contract(view)
    state = view["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("timeout", "merge_target_options_timeout", None)
    assert view["merge_target_options"] == []
    assert not mt._check_cache                               # a timeout is never cached (unchanged)
    lines = _lines(caplog, mt)
    assert len(lines) == 1
    line = lines[0]
    for part in ("check target lookup timeout ", "stage=names", "error=-", "names=1/3", "inspect_workspace=1/",
                 "budget_ms=300", "slowest_ms=develop:", "entries=0 entries_ms=-"):
        assert part in line, (part, line)
    inspect_ms = int(line.split("inspect_workspace=1/")[1].split("ms")[0])
    assert inspect_ms >= 300


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
# they were (a spent budget: ``timeout``, unchecked states, the cached snapshot unchanged).
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
    snap = next(iter(mt._check_cache.values()))                         # the cache is as it was: nothing added
    assert snap["status"] == "ok" and set(snap["verdicts"]) == set(cached)
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
    entered, release = threading.Event(), threading.Event()

    def dirty(_root):
        entered.set()
        release.wait(5)
        return False

    monkeypatch.setattr(mt, "_base_dirty", dirty)
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
