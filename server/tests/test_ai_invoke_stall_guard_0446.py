"""flowgate.default.0446 T0014 — the AI-invoke no-progress watchdog.

`_cli_execute` used to wait for its worker with one `communicate(timeout=...)`, so the only
question it could ask was "has the clock run out?". NR0003 measured that failing in both
directions on a fixed hour: a 74-minute hop that was still registering documents was cut
off, and a worker that died in its first minutes still held its group for the remaining 59.

T0014 splits the one number in two and adds a watchdog thread beside the wait:

  * the budget `_resolve_timeout_sec` returns (unchanged formula, unchanged 1800..14400
    bounds, unchanged 30/45/60/90/120/180/240 list) is now the NO-PROGRESS threshold —
    how long a run may show nothing new;
  * the run's hard ceiling is four hours from `started_mono`, reached with or without
    progress, and it is RUN_TIMEOUT_CAP_SEC itself rather than a second 14400 literal.

Covers, in the order T0014 §5 asks for them:
  1. no-progress positive — neither signal moves, the tree is killed once, well before 4h;
  2. document control — a max-seq that keeps rising outlives the first deadline, and dies
     once it stops rising ("progress once" is not a permanent exemption);
  3. source control — a `git status` set that keeps changing does the same, and the SAME
     dirty set returned again is not counted twice;
  4. the ceiling — a run that progresses on every single tick still ends at 14400s, and a
     retry attempt re-anchors the threshold without resetting the ceiling;
  5. unreadable samples — a DB error or a `None` from git is "unknown", not "no progress",
     and the guard recovers on the next readable sample;
  6. termination races — natural exit, user cancel, the pre-existing timeout, a broken
     stdin pipe and a fast-fail each stop and join the watchdog with no second kill, no
     overwritten cause and no leaked process reference;
  7. the contracts this T must NOT move — `_resolve_timeout_sec`, `_remaining_sec`,
     the constants, and the retry budget gate 0400 named.

The 30-minute and 4-hour edges are exercised through a virtual clock (`svc._now_mono`) and
a poll event that advances it by exactly one interval per tick, so nothing here sleeps for
its assertions.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import process_runner  # noqa: E402

PY = sys.executable
GROUP = "flowgate.default.0446"
INTERVAL = svc.STALL_POLL_INTERVAL_SEC
THRESHOLD = 1800                      # STEP_TIMEOUT_MIN_SEC: the shortest budget offered
TICKS_TO_STALL = THRESHOLD // INTERVAL


# ── virtual clock / poll harness ─────────────────────────────────────────────

class Clock:
    """Monotonic time the test moves by hand."""

    def __init__(self, start: float = 10_000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class TickEvent:
    """A stop event whose `wait(interval)` advances the clock by one interval.

    This is the watchdog's only sleep, so replacing it makes every tick instantaneous and
    every deadline exact: tick N happens at started_mono + N × interval, always.
    """

    def __init__(self, clock: Clock, max_ticks: int = 400):
        self.clock = clock
        self.max_ticks = max_ticks
        self.ticks = 0
        self._flag = False

    def wait(self, interval: float) -> bool:
        if self._flag:
            return True
        if self.ticks >= self.max_ticks:
            self._flag = True           # the run thread would have finished by now
            return True
        self.ticks += 1
        self.clock.advance(interval)
        return False

    def set(self) -> None:
        self._flag = True

    def is_set(self) -> bool:
        return self._flag


class FakeProc:
    """Just enough Popen for the watchdog: `poll()` and a recorded death."""

    def __init__(self, *, dies_when_killed: bool = True):
        self.returncode = None
        self.dies_when_killed = dies_when_killed

    def poll(self):
        return self.returncode


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(svc, "_now_mono", c)
    return c


@pytest.fixture
def kills(monkeypatch) -> list:
    """Every `kill_process_tree` the module makes, in order."""
    seen: list = []

    def _kill(proc):
        seen.append(proc)
        if getattr(proc, "dies_when_killed", False):
            proc.returncode = -9
    monkeypatch.setattr(process_runner, "kill_process_tree", _kill)
    return seen


def _run(clock: Clock, *, timeout_sec: int = THRESHOLD, source_root=None,
         baseline_seq: int = 4, dirty_baseline=None) -> dict:
    """The subset of the run dict the watchdog reads, at the shape start_run builds."""
    return {
        "run_id": "aiv_stall", "group_id": GROUP, "baseline_seq": baseline_seq,
        "timeout_sec": timeout_sec, "started_mono": clock.now,
        "stall_anchor_mono": clock.now, "cancel_event": threading.Event(),
        "source_root": str(source_root) if source_root else None,
        "dirty_baseline": dirty_baseline, "attempt_no": 1, "attempts_used": 1,
        "timed_out": False, "watchdog_kill": None, "progress_observations": 0,
        "last_progress_mono": None, "last_progress_at": None, "proc": None,
    }


def _docs(monkeypatch, fn) -> None:
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", fn)


def _git(monkeypatch, fn) -> None:
    monkeypatch.setattr(svc, "_git_status_paths", fn)


def _loop(run, proc, clock, *, max_ticks=400):
    event = TickEvent(clock, max_ticks=max_ticks)
    verdict = svc._progress_watchdog_loop(run, proc, event, INTERVAL)
    return verdict, event


# ── 1. no-progress positive (§5-1) ───────────────────────────────────────────

class TestStalledRunIsStopped:
    def test_neither_signal_moving_is_killed_at_the_threshold(
            self, clock, kills, monkeypatch, tmp_path):
        # The incident shape: the worker is alive, the group's documents are exactly where
        # the hop started, and the worktree looks exactly as it did at admission.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: {"server/x.py"})
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/x.py"})
        proc = FakeProc()
        started = clock.now

        verdict, event = _loop(run, proc, clock)

        assert verdict == "no_progress"
        assert len(kills) == 1                       # exactly one kill of the tree
        assert kills[0] is proc
        assert event.ticks == TICKS_TO_STALL         # 1800s / 15s — not one tick early
        assert clock.now - started == THRESHOLD
        # ...and it is a TIMEOUT, which is what the existing classification reads.
        assert run["timed_out"] is True
        svc._classify_end_reason(run, True)
        assert run["end_reason"] == "timeout"
        assert svc._resolve_stop_code(run, False) == "timeout"

    def test_it_stopped_far_short_of_the_four_hour_ceiling(
            self, clock, kills, monkeypatch, tmp_path):
        # §5-1's other half: the guard is what ended it, not the ceiling.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        _loop(run, FakeProc(), clock)

        mark = run["watchdog_kill"]
        assert mark["kind"] == "no_progress"
        assert mark["stalled_sec"] == THRESHOLD
        assert mark["elapsed_sec"] == THRESHOLD
        assert mark["elapsed_sec"] < svc._absolute_cap_sec()
        assert mark["progress_observations"] == 0
        assert mark["last_progress_at"] is None

    def test_a_later_tick_cannot_kill_the_same_run_twice(
            self, clock, kills, monkeypatch, tmp_path):
        # A worker that survives the kill (orphaned grandchild, slow reaper) must not be
        # killed again by the next tick, and must not overwrite the recorded reason.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        proc = FakeProc(dies_when_killed=False)

        assert _loop(run, proc, clock)[0] == "no_progress"
        first = dict(run["watchdog_kill"])
        assert _loop(run, proc, clock)[0] is None    # the claim is already taken

        assert len(kills) == 1
        assert run["watchdog_kill"] == first


# ── 2. document-progress control group (§5-2) ────────────────────────────────

class TestDocumentProgressKeepsItAlive:
    def test_a_rising_max_seq_outlives_the_first_deadline_then_stops(
            self, clock, kills, monkeypatch, tmp_path):
        # Documents land every 1500s — inside the 1800s threshold — for three rounds, then
        # the run goes quiet. git never moves at all, so the document signal alone is
        # carrying it.
        started = clock.now
        last_gain = started + 4500

        def seq(gid):
            if clock.now > last_gain:
                return 7
            return 4 + int((clock.now - started) // 1500)
        _docs(monkeypatch, seq)
        _git(monkeypatch, lambda root: {"server/x.py"})
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/x.py"})

        verdict, _ = _loop(run, FakeProc(), clock, max_ticks=800)

        # It was NOT killed on the first deadline...
        assert clock.now - started > THRESHOLD
        assert run["progress_observations"] == 3
        assert run["last_progress_signal"] == "document"
        # ...but a run that once progressed is not exempt forever: 1800s after the LAST
        # document, the same guard ends it.
        assert verdict == "no_progress"
        assert len(kills) == 1
        assert run["watchdog_kill"]["stalled_sec"] == THRESHOLD
        assert run["watchdog_kill"]["elapsed_sec"] == pytest.approx(4500 + THRESHOLD, abs=INTERVAL)

    def test_the_same_run_without_the_documents_dies_at_the_first_deadline(
            self, clock, kills, monkeypatch, tmp_path):
        # The control group's control group: identical fixture, frozen max-seq. If this
        # died at the same time as the case above, the case above would prove nothing.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: {"server/x.py"})
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/x.py"})
        started = clock.now

        assert _loop(run, FakeProc(), clock, max_ticks=800)[0] == "no_progress"
        assert clock.now - started == THRESHOLD
        assert run["progress_observations"] == 0

    def test_a_falling_or_equal_seq_is_not_progress(self, clock, kills, monkeypatch, tmp_path):
        # Only an INCREASE past the watermark counts; a re-read of the same number (or a
        # smaller one, e.g. a purged draft) is the absence of news.
        values = iter([4, 4, 3, 4] + [4] * 500)
        _docs(monkeypatch, lambda gid: next(values))
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())

        assert _loop(run, FakeProc(), clock)[0] == "no_progress"
        assert run["progress_observations"] == 0


# ── 3. source-progress control group (§5-3) ──────────────────────────────────

class TestSourceProgressKeepsItAlive:
    def test_a_changing_dirty_set_outlives_the_first_deadline(
            self, clock, kills, monkeypatch, tmp_path):
        # No documents at all — this is the shape of a T hop editing source for an hour.
        started = clock.now
        last_gain = started + 4500

        def paths(root):
            step = min(int((clock.now - started) // 1500), 3)
            return {f"server/f{i}.py" for i in range(step + 1)}
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, paths)
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/f0.py"})

        verdict, _ = _loop(run, FakeProc(), clock, max_ticks=800)

        assert run["progress_observations"] == 3
        assert run["last_progress_signal"] == "source"
        assert clock.now - started > THRESHOLD
        assert verdict == "no_progress"
        assert run["watchdog_kill"]["elapsed_sec"] == pytest.approx(4500 + THRESHOLD, abs=INTERVAL)
        assert last_gain < clock.now

    def test_a_removed_path_is_a_change_too(self, clock, kills, monkeypatch, tmp_path):
        # A worker that reverts a file, or whose edit gets committed, changes the set
        # downward. That is state moving, and §3-3 counts it.
        sets = iter([{"a", "b"}, {"a"}, set()] + [set()] * 500)
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: next(sets))
        run = _run(clock, source_root=tmp_path, dirty_baseline={"a", "b"})

        _loop(run, FakeProc(), clock, max_ticks=400)
        assert run["progress_observations"] == 2

    def test_the_same_dirty_set_repeated_is_not_counted_again(
            self, clock, kills, monkeypatch, tmp_path):
        # The trap §3-3 names: comparing every tick against the BASELINE instead of the
        # previous reading would score one early edit as progress forever, and a worker
        # that wrote one file and hung would never be stopped.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: {"server/x.py", "server/y.py"})
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        started = clock.now

        verdict, _ = _loop(run, FakeProc(), clock, max_ticks=800)

        # The first tick genuinely differs from the run's own start snapshot, so it counts
        # ONCE (§3-3's "the baseline is the first comparison point"). Every tick after it
        # returns the same set and adds nothing.
        assert run["progress_observations"] == 1
        assert verdict == "no_progress"
        assert clock.now - started == INTERVAL + THRESHOLD
        assert run["watchdog_kill"]["stalled_sec"] == THRESHOLD

    def test_a_run_without_a_source_tree_is_still_guarded(self, clock, kills, monkeypatch):
        # source_root=None is not an unreadable git — there is no worktree to read. The
        # document signal alone must still be able to end the run.
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: pytest.fail("git must not be polled without a tree"))
        run = _run(clock, source_root=None)

        assert _loop(run, FakeProc(), clock)[0] == "no_progress"
        assert len(kills) == 1


# ── 4. the absolute ceiling (§5-4) ───────────────────────────────────────────

class TestAbsoluteCeiling:
    def test_progress_on_every_tick_still_ends_at_four_hours(
            self, clock, kills, monkeypatch, tmp_path):
        # The runaway shape: something moves constantly, so the no-progress clock is reset
        # on every single tick and would never fire on its own.
        counter = {"n": 4}

        def seq(gid):
            counter["n"] += 1
            return counter["n"]
        _docs(monkeypatch, seq)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        started = clock.now

        verdict, event = _loop(run, FakeProc(), clock, max_ticks=2000)

        assert verdict == "absolute_cap"
        assert len(kills) == 1
        assert clock.now - started == svc._absolute_cap_sec() == 14400
        assert event.ticks == 14400 // INTERVAL
        assert run["watchdog_kill"]["kind"] == "absolute_cap"
        assert run["watchdog_kill"]["elapsed_sec"] == 14400
        assert run["progress_observations"] > 900    # it really was progressing throughout
        assert run["timed_out"] is True

    def test_a_retry_attempt_re_anchors_the_threshold_but_not_the_ceiling(self, clock, tmp_path):
        # §2-4 / §4-1: `_reset_attempt_state` keeps started_mono, and the watchdog re-arms
        # only the no-progress window. Otherwise every retry would hand out a fresh 4h.
        run = _run(clock, timeout_sec=3600)
        run.update({"scratch_dir": str(tmp_path), "attempt_started_mono": clock.now})
        started_mono = run["started_mono"]
        clock.advance(10_000)

        svc._reset_attempt_state(run)
        assert run["started_mono"] == started_mono
        assert run["watchdog_kill"] is None

        run["stall_anchor_mono"] = clock.now         # what _start_progress_watchdog does
        assert svc._stall_remaining_sec(run) == 3600
        assert svc._absolute_remaining_sec(run) == pytest.approx(4400)

    def test_the_ceiling_counts_from_the_hop_not_from_this_attempt(
            self, clock, kills, monkeypatch, tmp_path):
        # Same loop, but the hop already burned 10_000 seconds in an earlier attempt: the
        # ceiling must land 4400 seconds in, not 14400.
        counter = {"n": 4}

        def seq(gid):
            counter["n"] += 1
            return counter["n"]
        _docs(monkeypatch, seq)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        run["started_mono"] = clock.now - 10_000
        attempt_started = clock.now

        assert _loop(run, FakeProc(), clock, max_ticks=2000)[0] == "absolute_cap"
        assert clock.now - attempt_started == pytest.approx(4400, abs=INTERVAL)


# ── 5. unreadable samples (§5-5) ─────────────────────────────────────────────

class TestUnreadableSamplesAreNotProgress:
    def test_a_document_read_error_never_kills_on_its_own(
            self, clock, kills, monkeypatch, tmp_path):
        def boom(gid):
            raise RuntimeError("db is down")
        _docs(monkeypatch, boom)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        started = clock.now

        verdict, _ = _loop(run, FakeProc(), clock, max_ticks=400)

        assert verdict is None                       # ran far past the threshold...
        assert clock.now - started == 400 * INTERVAL > THRESHOLD
        assert kills == []                           # ...and killed nothing

    def test_git_returning_none_never_kills_on_its_own(
            self, clock, kills, monkeypatch, tmp_path):
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: None)         # git absent / command failed
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())

        assert _loop(run, FakeProc(), clock, max_ticks=400)[0] is None
        assert kills == []

    def test_the_guard_recovers_on_the_next_readable_sample(
            self, clock, kills, monkeypatch, tmp_path):
        # The failure is transient: git is unreadable for the first 2400 seconds (past the
        # threshold), then answers again. Nothing moved while it was blind, so the stall
        # clock was already over the line and the first readable tick decides.
        started = clock.now
        blind_until = started + 2400

        def paths(root):
            return None if clock.now <= blind_until else {"server/x.py"}
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, paths)
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/x.py"})

        verdict, _ = _loop(run, FakeProc(), clock, max_ticks=400)

        assert verdict == "no_progress"
        assert len(kills) == 1
        # It waited out the blind window rather than killing at 1800...
        assert run["watchdog_kill"]["elapsed_sec"] > THRESHOLD
        # ...and acted on the first sample it could actually read.
        assert run["watchdog_kill"]["elapsed_sec"] == pytest.approx(2400, abs=INTERVAL)

    def test_an_unreadable_sample_does_not_count_as_progress_either(
            self, clock, kills, monkeypatch, tmp_path):
        # The mirror of the case above: being blind must not RESET the stall clock, or a
        # permanently flaky git would keep a dead worker alive forever.
        _docs(monkeypatch, lambda gid: 4)
        every_other = {"n": 0}

        def paths(root):
            every_other["n"] += 1
            return None if every_other["n"] % 2 else {"server/x.py"}
        _git(monkeypatch, paths)
        run = _run(clock, source_root=tmp_path, dirty_baseline={"server/x.py"})
        started = clock.now

        assert _loop(run, FakeProc(), clock, max_ticks=400)[0] == "no_progress"
        assert run["progress_observations"] == 0
        # The readable ticks are half of them, so it lands on the first readable tick at
        # or after the threshold — never before it.
        assert clock.now - started >= THRESHOLD
        assert clock.now - started <= THRESHOLD + INTERVAL

    def test_the_ceiling_still_fires_while_blind(self, clock, kills, monkeypatch, tmp_path):
        # §3-6: the ceiling needs no readable sample to be true, so a permanently blind
        # run is bounded even though the no-progress guard can never decide.
        def boom(gid):
            raise RuntimeError("db is down")
        _docs(monkeypatch, boom)
        _git(monkeypatch, lambda root: None)
        run = _run(clock, source_root=tmp_path)

        assert _loop(run, FakeProc(), clock, max_ticks=2000)[0] == "absolute_cap"
        assert len(kills) == 1


# ── 6. termination races (§5-6) ──────────────────────────────────────────────

class TestCancelOutranksTheWatchdog:
    def test_a_cancelled_run_is_never_claimed_by_the_watchdog(
            self, clock, kills, monkeypatch, tmp_path):
        _docs(monkeypatch, lambda gid: 4)
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        run["cancel_event"].set()

        assert _loop(run, FakeProc(), clock, max_ticks=400)[0] is None
        assert kills == []
        assert run["timed_out"] is False
        svc._classify_end_reason(run, True)
        assert run["end_reason"] == "cancelled"      # not "timeout"
        assert svc._resolve_stop_code(run, False) == "cancelled"

    def test_a_cancel_that_lands_between_poll_and_claim_wins(self, clock, kills):
        # The claim is the last gate, and it re-reads the cancel flag under the lock.
        run = _run(clock, source_root=None)
        run["cancel_event"].set()
        event = TickEvent(clock)

        assert svc._claim_watchdog_kill(run, FakeProc(), event, "no_progress", clock.now) is False
        assert kills == []
        assert run["watchdog_kill"] is None
        assert run["timed_out"] is False

    def test_a_run_thread_already_past_communicate_wins(self, clock, kills):
        # `_stop_progress_watchdog` sets the event; a tick already in flight must not kill
        # a process the next attempt may be about to replace.
        run = _run(clock, source_root=None)
        event = TickEvent(clock)
        event.set()

        assert svc._claim_watchdog_kill(run, FakeProc(), event, "no_progress", clock.now) is False
        assert kills == []
        assert run["timed_out"] is False

    def test_a_child_that_exited_on_its_own_is_not_killed(self, clock, kills):
        run = _run(clock, source_root=None)
        proc = FakeProc()
        proc.returncode = 0

        assert svc._claim_watchdog_kill(run, proc, TickEvent(clock), "no_progress", clock.now) is False
        assert kills == []
        assert run["timed_out"] is False

    def test_a_natural_exit_ends_the_loop_without_a_kill(self, clock, kills, monkeypatch, tmp_path):
        # The worker finishes on its own well inside the threshold: the next tick sees a
        # reaped child and leaves without touching anything.
        _git(monkeypatch, lambda root: set())
        run = _run(clock, source_root=tmp_path, dirty_baseline=set())
        proc = FakeProc()

        def seq(gid):
            if clock.now - run["started_mono"] >= 600:
                proc.returncode = 0
            return 4
        _docs(monkeypatch, seq)

        assert _loop(run, proc, clock, max_ticks=400)[0] is None
        assert clock.now - run["started_mono"] < THRESHOLD
        assert kills == []
        assert run["timed_out"] is False
        assert run["watchdog_kill"] is None


# ── 6b. the same races through the real `_cli_execute` ───────────────────────

def _cli_run(tmp_path, *, timeout_sec=3600) -> dict:
    return {
        "run_id": "aiv_cli", "group_id": GROUP, "baseline_seq": 4,
        "scratch_dir": str(tmp_path), "source_root": str(tmp_path),
        "raw_token": "tok", "api_base_url": "", "cancel_event": threading.Event(),
        "timeout_sec": timeout_sec, "started_mono": time.monotonic(),
        "attempt_started_mono": time.monotonic(), "dirty_baseline": set(),
        "timed_out": False, "watchdog_kill": None, "progress_observations": 0,
        "proc": None, "attempt_no": 1,
    }


def _no_watchdog_threads() -> list:
    return [t for t in threading.enumerate() if t.name.startswith("ai-invoke-watchdog")]


@pytest.fixture
def quiet_signals(monkeypatch):
    """No DB and no git for the end-to-end cases — they are about the exits, not the poll."""
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda gid: 4)
    monkeypatch.setattr(svc, "_git_status_paths", lambda root: set())


class TestCliExecuteExits:
    def test_a_normal_exit_stops_and_joins_the_watchdog(self, tmp_path, quiet_signals):
        provider = {"kind": "claude", "cli_command": f'"{PY}" -c "import sys; sys.stdin.read()"'}
        run = _cli_run(tmp_path)

        status, detail = svc._cli_execute(provider, "hello", run)

        assert (status, detail) == ("started_ok", None)
        assert run["watchdog_kill"] is None
        assert run["timed_out"] is False
        assert run["exit_code"] == 0
        assert run["proc"] is None                   # no leaked process reference
        assert _no_watchdog_threads() == []

    def test_the_wait_is_bounded_by_the_CEILING_not_the_threshold(self, tmp_path, monkeypatch,
                                                                  quiet_signals):
        # §3-1: the threshold belongs to the watchdog now. If `communicate` kept it, a
        # working run would still be cut off at its budget with no one asking why.
        seen = {}

        class _Proc:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                seen["timeout"] = timeout
                return b"", b""

            def poll(self):
                return 0
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _Proc())
        run = _cli_run(tmp_path, timeout_sec=1800)

        svc._cli_execute({"kind": "claude", "cli_command": "x"}, "p", run)

        assert seen["timeout"] == pytest.approx(svc._absolute_cap_sec(), abs=5)
        assert seen["timeout"] != run["timeout_sec"]
        assert _no_watchdog_threads() == []

    def test_the_pre_existing_timeout_path_is_untouched(self, tmp_path, monkeypatch,
                                                        quiet_signals, ):
        # The ceiling is RUN_TIMEOUT_CAP_SEC read at call time, so the one existing test
        # that shortens it (test_ai_invoke_0187 TestForcedKill) still shortens this wait.
        monkeypatch.setattr(svc, "RUN_TIMEOUT_CAP_SEC", 1)
        killed: list = []
        monkeypatch.setattr(process_runner, "kill_process_tree",
                            lambda proc: (killed.append(proc), proc.kill()))
        provider = {"kind": "claude", "cli_command": f'"{PY}" -c "import time; time.sleep(60)"'}
        run = _cli_run(tmp_path)

        status, _ = svc._cli_execute(provider, "hello", run)

        assert status == "started_ok"                # a timeout is not a startup failure
        assert run["timed_out"] is True
        assert run["exit_code"] is None
        assert run["watchdog_kill"] is None          # the WAIT expired, not the watchdog
        assert len(killed) == 1
        assert run["proc"] is None
        assert _no_watchdog_threads() == []

    def test_a_user_cancel_keeps_its_own_reason(self, tmp_path, quiet_signals):
        run = _cli_run(tmp_path)
        run["cancel_event"].set()                    # cancel landed before the spawn
        provider = {"kind": "claude", "cli_command": f'"{PY}" -c "import time; time.sleep(30)"'}

        svc._cli_execute(provider, "hello", run)

        assert run["watchdog_kill"] is None
        assert run["timed_out"] is False
        svc._classify_end_reason(run, True)
        assert run["end_reason"] == "cancelled"
        assert run["proc"] is None
        assert _no_watchdog_threads() == []

    def test_a_broken_stdin_pipe_still_stops_the_watchdog(self, tmp_path, monkeypatch,
                                                          quiet_signals):
        class _Proc:
            returncode = None

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    raise OSError("pipe closed")
                return b"", b""

            def poll(self):
                return self.returncode
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _Proc())
        monkeypatch.setattr(process_runner, "kill_process_tree", lambda proc: None)
        run = _cli_run(tmp_path)

        status, detail = svc._cli_execute({"kind": "claude", "cli_command": "x"}, "p", run)

        assert status == "spawn_failed"              # unchanged: a startup failure
        assert "pipe closed" in detail
        assert run["proc"] is None
        assert _no_watchdog_threads() == []

    def test_a_fast_fail_is_still_a_fast_fail(self, tmp_path, quiet_signals):
        provider = {"kind": "claude", "cli_command": f'"{PY}" -c "import sys; sys.exit(7)"'}
        run = _cli_run(tmp_path)

        status, detail = svc._cli_execute(provider, "hello", run)

        assert status == "fast_fail"                 # the provider chain still falls through
        assert run["timed_out"] is False
        assert run["watchdog_kill"] is None
        assert run["proc"] is None
        assert _no_watchdog_threads() == []

    def test_a_spawn_failure_never_starts_a_watchdog(self, tmp_path, monkeypatch, quiet_signals):
        def boom(*a, **kw):
            raise OSError("not spawned by design")
        monkeypatch.setattr(subprocess, "Popen", boom)
        run = _cli_run(tmp_path)

        status, _ = svc._cli_execute({"kind": "claude", "cli_command": "x"}, "p", run)

        assert status == "spawn_failed"
        assert _no_watchdog_threads() == []


class TestWatchdogThreadLifecycle:
    def test_start_and_stop_leave_no_thread_behind(self, tmp_path, monkeypatch, quiet_signals):
        proc = FakeProc()
        stop, thread = svc._start_progress_watchdog(_cli_run(tmp_path), proc, interval=0.01)
        assert thread.is_alive()
        svc._stop_progress_watchdog(stop, thread, "aiv_cli")
        assert not thread.is_alive()
        assert _no_watchdog_threads() == []

    def test_stopping_an_already_finished_watchdog_is_safe(self, tmp_path, quiet_signals):
        proc = FakeProc()
        proc.returncode = 0                          # the loop returns on its first tick
        run = _cli_run(tmp_path)
        stop, thread = svc._start_progress_watchdog(run, proc, interval=0.01)
        thread.join(timeout=5)
        svc._stop_progress_watchdog(stop, thread, run["run_id"])
        assert not thread.is_alive()


# ── 7. contracts this T must not move (§2, §5-7) ─────────────────────────────

class TestUnchangedTimeContracts:
    def test_the_constants_are_where_T0010_left_them(self):
        assert svc.RUN_TIMEOUT_BASE_SEC == 3600
        assert svc.HOP_TIMEOUT_SEC == 3600
        assert svc.STEP_TIMEOUT_MIN_SEC == 1800
        assert svc.STEP_TIMEOUT_MAX_SEC == 14400
        assert svc.RUN_TIMEOUT_CAP_SEC == 14400
        # The ceiling REUSES the existing four-hour constant — no second literal (§2-4).
        assert svc._absolute_cap_sec() == svc.RUN_TIMEOUT_CAP_SEC == 14400

    def test_resolve_timeout_sec_still_answers_exactly_as_before(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, None) == svc.HOP_TIMEOUT_SEC
        assert svc._resolve_timeout_sec("single", 1, False, None) == 3600
        assert svc._resolve_timeout_sec("single", 2, False, None) == 7200
        assert svc._resolve_timeout_sec("single", 99, False, None) == svc.RUN_TIMEOUT_CAP_SEC
        assert svc._resolve_timeout_sec("single", 1, True, None) == svc.RUN_TIMEOUT_CAP_SEC
        for pick in (1800, 2700, 3600, 5400, 7200, 10800, 14400):
            assert svc._resolve_timeout_sec("single", 1, False, pick) == pick
            assert svc._resolve_timeout_sec("continuous", 1, False, pick) == pick
        # out of range ⇒ ignored, the mode's own default stands
        assert svc._resolve_timeout_sec("single", 1, False, 60) == 3600
        assert svc._resolve_timeout_sec("continuous", 1, False, 999999) == svc.HOP_TIMEOUT_SEC

    def test_remaining_sec_still_measures_from_hop_start(self, clock):
        run = _run(clock, timeout_sec=3600)
        clock.advance(600)
        run["started_mono"] = time.monotonic() - 600
        assert svc._remaining_sec(run) == pytest.approx(3000, abs=2)


class TestRetryBudgetSeparation:
    def test_without_an_anchor_the_retry_budget_is_the_old_reading(self, clock):
        # This is why every 0400 budget_exhausted case still decides the same way: a run
        # with no watchdog anchor gets exactly `_remaining_sec`.
        run = _run(clock, timeout_sec=3600)
        run.pop("stall_anchor_mono")
        run["started_mono"] = clock.now - 3400
        assert svc._stall_remaining_sec(run) == pytest.approx(200)
        assert svc._retry_remaining_sec(run) == pytest.approx(200)
        assert svc._retry_remaining_sec(run) < svc.RETRY_MIN_REMAINING_SEC

    def test_a_run_that_outlived_its_threshold_by_working_is_not_budget_exhausted(self, clock):
        # §4-4: 90 productive minutes on a 60-minute threshold. `_remaining_sec` is
        # NEGATIVE here; reading that as the retry budget would report an exhausted budget
        # for a run with two and a half hours of ceiling left.
        run = _run(clock, timeout_sec=3600)
        run["started_mono"] = clock.now - 5400
        run["stall_anchor_mono"] = clock.now - 60     # it was moving a minute ago
        assert svc._remaining_sec(run) < 0
        assert svc._stall_remaining_sec(run) == pytest.approx(3540)
        assert svc._absolute_remaining_sec(run) == pytest.approx(9000)
        assert svc._retry_remaining_sec(run) == pytest.approx(3540)
        assert svc._retry_remaining_sec(run) > svc.RETRY_MIN_REMAINING_SEC

    def test_the_ceiling_still_caps_the_retry_budget(self, clock):
        # ...and the separation does not become a licence: 100 seconds before the ceiling,
        # a freshly anchored run still has no room for another attempt.
        run = _run(clock, timeout_sec=3600)
        run["started_mono"] = clock.now - (14400 - 100)
        run["stall_anchor_mono"] = clock.now
        assert svc._stall_remaining_sec(run) == pytest.approx(3600)
        assert svc._retry_remaining_sec(run) == pytest.approx(100)
        assert svc._retry_remaining_sec(run) < svc.RETRY_MIN_REMAINING_SEC

    def test_the_gate_reads_the_separated_budget(self, clock, monkeypatch):
        # End to end through `_retry_eligible`, in the shape 0400 built for it.
        monkeypatch.setattr(svc, "_has_pending_question", lambda doc_ref: False)
        monkeypatch.setattr(svc, "peek_auto_resume", lambda group_id: None)

        def _judged(**over):
            run = {
                "mode": "continuous", "cancel_event": threading.Event(), "end_reason": "exited",
                "pause_requested": False, "completion_oracle": None, "action_scope": "new",
                "docs_target": 1, "docs_reached": 0, "attempts_used": 1, "group_id": GROUP,
                "doc_ref": f"{GROUP}.0014-T", "outcome": "none", "timeout_sec": 3600,
                "started_mono": clock.now,
            }
            run.update(over)
            return run

        spent = _judged(started_mono=clock.now - 3400)
        assert svc._retry_eligible(spent) is False
        assert spent["retry_block_reason"] == "budget_exhausted"

        working = _judged(started_mono=clock.now - 5400, stall_anchor_mono=clock.now - 60)
        assert svc._retry_eligible(working) is True
        assert "retry_block_reason" not in working
