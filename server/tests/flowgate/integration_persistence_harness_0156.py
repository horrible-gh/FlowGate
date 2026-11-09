"""flowgate.v02.0156 T#2 (T0007 W6) — the S4 / S5 observation: several isolated Flows in one process, each left in a
reproduced condition, the runtime triggers made inside one observation window, and the state sampled every 15 s until
every Flow stayed unchanged for 10 minutes (real time) after its last change (NR §6.1: the window restarts at each
progress; no single query decides). No server restart happens in the window; the server start recovery is never called.

Run on its own (it is not collected by the test pattern):

    python -m pytest tests/flowgate/integration_persistence_harness_0156.py -q -s --basetemp <scratch>/harness

It writes ``fg0156_harness/series.json`` (every sample) and ``fg0156_harness/summary.json`` (per scenario: the first
failure state, each trigger, the last change, the final state and its judgement) under the basetemp.

The scenarios (T0007 W6), on the same real stack as ``test_fg_integration_persistence_0156`` (its fixtures/helpers):

- S4-1: D2-a under an idle run's hold (h1)          - S4-2: D2-a under a command's hold (h2)
- S4-3: D2-b (its session open), then a person's abort  - S4-4: W4 ⓐ — a reservation canceled without a pin
- S4-5: an outside index.lock in the group worktree (L1), removed in the window, then a command
- S5-1: D2-a, then a Stop while the failure lasts, then the failure ends (lock release, the Stop again)
- S5-2: a Stop while a merge is really in progress (held), then the merge goes on
Triggers (s from the start): 15 the Stop (S5-2: its merge held, ``integrating`` confirmed) · 30 the abort (S4-3), the
Stop (S5-1) · 45 the failure ends (S5-1) · 59 S5-2's state before its merge goes on (still ``stopping`` /
``integrating``, no merge, never reclaimed) · 60 F2 (a G release), the lock file removed (S4-5), the held merge goes on
(S5-2) · 90 the Stop again (S5-1) · 120 F3 (a command) · 180 F4 (Stop, then a G release).

S5-2 is asserted (the observation is invalid otherwise): the Stop answered 200 and left the run ``stopping`` with the
attempt ``integrating`` and no merge commit, ``_recover_one`` never took its attempt, and after the merge went on the run
is idle (``stopped``), the node done, the attempt ``integrated``, one merge commit.

``test_s4_s5_trigger_order`` runs the same scenarios and triggers with a 30 s quiet window: a short check of the trigger
order and of S5-2's assertions, not the W6 observation (``test_s4_s5_observation``, the 600 s window).
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

import test_fg_integration_persistence_0156 as p
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, passive_person_results, pre_feature_attempts, db_path, decided, env,
)
from test_fg_card_approval_0056 import card_outputs_port  # noqa: F401 — autouse
from test_fg_integration_persistence_0156 import (  # noqa: F401 — fixtures used by name (two autouse)
    fresh_drain_state, no_document_gates, repo, tap,
)

pytestmark = pytest.mark.usefixtures("legacy_cards", "passive_person_results", "pre_feature_attempts")

WINDOW_SEC = 600            # NR §6.1 T_obs
SHORT_WINDOW_SEC = 30       # the trigger order check only (not an observation)
SAMPLE_SEC = 15
LIMIT_SEC = 1500            # the whole observation, whatever happens
FLOWS = {name: f"flowgate.v02.95{60 + i}" for i, name in enumerate(
    ("S4-1", "S4-2", "S4-3", "S4-4", "S4-5", "S5-1", "S5-2"))}


def _key(row):
    """What counts as a change of a scenario's state (progress evidence)."""
    return (row["run"], row["idle_reason"], row["attempt"], row["integration"], row["reason"], row["node"],
            row["hold"], tuple(map(tuple, row["sessions"])), row["merges"], row["merge_head"])


def _judge(name, row, held_merge_open):
    """The final state's judgement: resolved, a legitimate wait (NR §6.1: a person's conflict session, the blocking lock /
    session still kept, a LIVE gate job, a merge really in progress), or stuck."""
    if (row["attempt"] == "active" and row["integration"] in (p.PENDING, p.INTEGRATING)) or row["run"] == "stopping":
        if held_merge_open:
            return "legit_wait"
        return "stuck"
    if row["run"] == "running" and not row["working"] and row["hold"] is None and row["integration"] in (
            p.CONFLICT, p.FAILED):
        return "stuck"                   # running with nothing working, no command can take it
    if row["integration"] == p.CONFLICT and any(s[1] == "open" for s in row["sessions"]):
        return "legit_wait"
    return "resolved"


@pytest.mark.timeout(LIMIT_SEC + 300)
def test_s4_s5_observation(env, decided, repo, tap, monkeypatch, tmp_path_factory):
    _observe(monkeypatch, repo, env, tmp_path_factory, WINDOW_SEC, "fg0156_harness")


@pytest.mark.timeout(LIMIT_SEC + 300)
def test_s4_s5_trigger_order(env, decided, repo, tap, monkeypatch, tmp_path_factory):
    _observe(monkeypatch, repo, env, tmp_path_factory, SHORT_WINDOW_SEC, "fg0156_harness_short")


def _observe(monkeypatch, repo, env, tmp_path_factory, window, folder):
    from flowgate.gate_decisions import repository as gate_repo
    from flowgate.graph_run import result_merge
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.services.git import branches

    out = tmp_path_factory.getbasetemp() / folder
    out.mkdir(parents=True, exist_ok=True)
    scenes: dict[str, SimpleNamespace] = {}
    marks: dict[str, list] = {name: [] for name in FLOWS}
    first: dict[str, dict] = {}
    t0 = time.monotonic()

    def now():
        return round(time.monotonic() - t0, 1)

    def mark(name, what, **extra):
        marks[name].append({"t": now(), "trigger": what, **extra})

    def fired(name):
        return lambda: first.setdefault(name, p.state(scenes[name]))

    # S5-2's merge is held (only that Flow's merge) until 60 s
    held = {"root": None, "gate": threading.Event()}
    real_merge = branches.merge_no_ff

    def merge(merge_root, *args, **kwargs):
        if held["root"] is not None and str(merge_root) == str(held["root"]):
            held["gate"].wait(LIMIT_SEC)
        return real_merge(merge_root, *args, **kwargs)

    monkeypatch.setattr(branches, "merge_no_ff", merge)
    reclaimed: list[str] = []                               # every attempt the recovery rule took (S5-2: none)
    real_recover = result_merge._recover_one
    monkeypatch.setattr(result_merge, "_recover_one", lambda row: reclaimed.append(row["attempt_id"]) or real_recover(row))

    for i, (name, flow) in enumerate(FLOWS.items()):
        sc = p.scene(env, repo, flow, conflict=name == "S4-3", seq=81 + i)
        scenes[name] = sc
    injections = {}
    # S4-1: D2-a (h1)
    injections["S4-1"] = p.Inject(monkeypatch, p.TARGETS["a"], flow=FLOWS["S4-1"], on_fire=fired("S4-1"))
    p.drive(scenes["S4-1"], "h1", monkeypatch)
    mark("S4-1", "F1")
    # S4-2: D2-a (h2)
    p.prefail(scenes["S4-2"])
    injections["S4-2"] = p.Inject(monkeypatch, p.TARGETS["a"], flow=FLOWS["S4-2"], on_fire=fired("S4-2"))
    p.drive(scenes["S4-2"], "h2", monkeypatch)
    mark("S4-2", "F1")
    # S4-3: D2-b (h3), the session open
    injections["S4-3"] = p.Inject(monkeypatch, p.TARGETS["b"], flow=FLOWS["S4-3"], on_fire=fired("S4-3"))
    p.drive(scenes["S4-3"], "h3", monkeypatch)
    mark("S4-3", "F1")
    # S4-4: ⓐ — a reservation, then its cancel (in the Flow guard), then the post-commit after_terminal
    decision = p.reserve(FLOWS["S4-4"])
    p.drive(scenes["S4-4"], "h3", monkeypatch)
    first["S4-4"] = p.state(scenes["S4-4"])
    p._gate_end(scenes["S4-4"], decision, "cancel")
    mark("S4-4", "cancel")
    # S4-5: index.lock L1 (h1)
    lock = p._lock_file(repo, scenes["S4-5"], "L1")
    lock.write_text("", encoding="utf-8")
    p.drive(scenes["S4-5"], "h1", monkeypatch)
    first["S4-5"] = p.state(scenes["S4-5"])
    mark("S4-5", "F1")
    # S5-1: D2-a (h3), lasting until 45 s
    injections["S5-1"] = p.Inject(monkeypatch, p.TARGETS["a"], flow=FLOWS["S5-1"], persistent=True,
                                  on_fire=fired("S5-1"))
    p.drive(scenes["S5-1"], "h3", monkeypatch)
    mark("S5-1", "F1")
    # S5-2: a merge really in progress (held)
    held["root"] = repo.wt(FLOWS["S5-2"])
    worker = threading.Thread(target=p.approve, args=(scenes["S5-2"],), daemon=True)
    worker.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and p.state(scenes["S5-2"])["integration"] != p.INTEGRATING:
        time.sleep(0.2)
    first["S5-2"] = p.state(scenes["S5-2"])
    mark("S5-2", "merge_held")
    s52: dict = {}

    def s52_view(row):
        return {k: row[k] for k in ("run", "idle_reason", "attempt", "integration", "node", "merges", "sessions")}

    def s52_stop():
        s52["code"] = p.stop(scenes["S5-2"])
        s52["at_stop"] = s52_view(p.state(scenes["S5-2"]))
        s52["reclaimed_at_stop"] = scenes["S5-2"].attempt in reclaimed
        return json.dumps(s52, ensure_ascii=False)

    def s52_before_gate():
        s52["before_gate"] = s52_view(p.state(scenes["S5-2"]))
        s52["reclaimed_before_gate"] = scenes["S5-2"].attempt in reclaimed
        return json.dumps(s52["before_gate"], ensure_ascii=False)

    t0 = time.monotonic()                                   # the window's triggers count from here
    schedule = [
        (15, "S5-2", "stop", s52_stop),                     # the Stop while its merge is held (integrating)
        (30, "S4-3", "person_abort", lambda: p.person_aborts(scenes["S4-3"])),
        (30, "S5-1", "stop", lambda: p.stop(scenes["S5-1"])),
        (45, "S5-1", "failure_ends", lambda: injections["S5-1"].release()),
        (59, "S5-2", "before_gate", s52_before_gate),
        (60, "S5-2", "merge_goes_on", lambda: held["gate"].set()),
        (60, "S4-5", "lock_removed", lambda: lock.unlink()),
        *[(60, name, "F2", (lambda f=FLOWS[name]: p.lock_release(f))) for name in
          ("S4-1", "S4-2", "S4-3", "S4-4", "S4-5", "S5-1")],
        (90, "S5-1", "stop_again", lambda: p.stop(scenes["S5-1"])),
        *[(120, name, "F3", (lambda sc=scenes[name]: p.command(sc))) for name in
          ("S4-1", "S4-2", "S4-3", "S4-4", "S4-5")],
        *[(180, name, "F4", (lambda sc=scenes[name]: (p.stop(sc), p.lock_release(sc.flow))[0])) for name in
          ("S4-1", "S4-2", "S4-3", "S4-4")],
    ]
    series: list[dict] = []
    last_key: dict[str, tuple] = {}
    last_change: dict[str, float] = {}
    next_sample = 0.0
    done_triggers = 0
    while True:
        t = now()
        while done_triggers < len(schedule) and schedule[done_triggers][0] <= t:
            at, name, what, call = schedule[done_triggers]
            try:
                result = call()
            except Exception as exc:  # noqa: BLE001 — recorded, the observation goes on
                result = f"{type(exc).__name__}: {exc}"
            mark(name, what, result=result if isinstance(result, (int, str, type(None))) else str(result))
            done_triggers += 1
            t = now()
        if t >= next_sample:
            sample = {"t": t}
            for name, sc in scenes.items():
                row = p.state(sc)
                sample[name] = {k: row[k] for k in ("run", "idle_reason", "attempt", "integration", "reason", "node",
                                                    "next", "hold", "working", "merges", "merge_head", "sessions",
                                                    "result_in_branch")}
                key = _key(row)
                if last_key.get(name) != key:
                    last_key[name], last_change[name] = key, t
            series.append(sample)
            (out / "series.json").write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
            next_sample = t + SAMPLE_SEC
        triggers_over = done_triggers >= len(schedule)
        # judged on the recorded samples (the window is what the series shows, not the loop's clock between samples)
        quiet = triggers_over and all(series[-1]["t"] - last_change[name] >= window for name in scenes)
        if quiet or t >= LIMIT_SEC:
            break
        time.sleep(1)
    worker.join(30)

    summary = {}
    for name, sc in scenes.items():
        final = p.state(sc)
        summary[name] = {
            "flow": sc.flow, "first_failure": first.get(name), "triggers": marks[name],
            "last_change_t": last_change.get(name), "observed_until_t": series[-1]["t"],
            "quiet_for": round(series[-1]["t"] - last_change.get(name, 0), 1),
            "final": {k: final[k] for k in ("run", "idle_reason", "attempt", "integration", "reason", "node", "next",
                                            "hold", "working", "merges", "merge_head", "sessions", "result_in_branch")},
            "judgement": _judge(name, final, name == "S5-2" and not held["gate"].is_set()),
            # before F4 (Stop) at 180 s: a Stop ends a pending / integrating attempt by invalidating it (its result is
            # not merged), so the state the other triggers left is judged too
            "before_stop": next((s[name] for s in reversed(series) if s["t"] < 180), None),
            "before_stop_judgement": (lambda r: _judge(name, {**r, "logs": []}, False) if r else None)(
                next((s[name] for s in reversed(series) if s["t"] < 180), None)),
            "logs": final["logs"],
        }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({name: (s["judgement"], s["final"]["run"], s["final"]["integration"], s["last_change_t"])
                      for name, s in summary.items()}, ensure_ascii=False))
    # the observation records; it asserts only what it needs to be valid (every trigger made, the window observed)
    assert done_triggers == len(schedule)
    assert all(series[-1]["t"] - last_change[name] >= window or series[-1]["t"] >= LIMIT_SEC for name in scenes)
    # S5-2: the Stop waited for the merge in progress (never reclaimed), then ended the run with done kept
    s52_final = s52_view(p.state(scenes["S5-2"]))
    (out / "s5_2.json").write_text(json.dumps({**s52, "final": s52_final, "reclaimed": reclaimed},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    assert first["S5-2"]["integration"] == p.INTEGRATING, first["S5-2"]
    assert s52["code"] == 200, s52
    for at in ("at_stop", "before_gate"):
        assert (s52[at]["run"], s52[at]["integration"], s52[at]["merges"]) == ("stopping", p.INTEGRATING, 0), s52
    assert not s52["reclaimed_at_stop"] and not s52["reclaimed_before_gate"], s52
    assert scenes["S5-2"].attempt not in reclaimed, reclaimed
    assert (s52_final["run"], s52_final["idle_reason"], s52_final["integration"], s52_final["node"],
            s52_final["merges"]) == ("idle", "stopped", p.INTEGRATED, "done", 1), s52_final
