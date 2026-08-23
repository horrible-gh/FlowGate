"""flowgate.default.0332 TS gate for the TR-commit-point + cancel-on-revert pair.

Run from ``server/`` as::

    python tools/run_0332_tests.py                    # the whole gate
    python tools/run_0332_tests.py --check-capability  # just the git probe

R0001 has two sections and this group built both: T#1 (``0012-TR``) makes TR
approval leave a commit, T#2 (``0014-TR``) makes a timeline revert cancel it.
Neither TR is a full answer alone — a TS that only re-runs ``test_tr_commit_
point_0332.py`` never proves cancel honors the ``already_merged``/conflict
gates T#2 added, and one that only re-runs ``test_tr_commit_cancel_0332.py``
never proves the commits it cancels are the ones T#1 actually creates. This
gate runs both suites together, plus 0332 T0018's third section (``test_tr_
commit_reapply_0332.py`` — a forward restore puts the canceled source back,
D0005 K11) and the 0382 finalize-absorb gate the design (D0005 §2) requires to
keep sharing its exclusion rule with TR commits, so "the three sections work as
one set, and finalize is unaffected" is one process exit code instead of four
separate green runs a reader has to trust line up. The three are one set on
purpose: a cancel proved alone says nothing about whether the source can come
back, and a restore proved alone says nothing about what it is restoring.

Modelled on ``tools/run_0350_tests.py`` (same host-portability and skip-is-
not-a-pass reasons — see [[pytest-skip-is-a-false-green]] in project memory).
The real-git classes in all four suites are gated behind a local
``needs_git = pytest.mark.skipif(not shutil.which("git"), ...)``; when git is
missing every one of them skips and plain pytest still exits 0. Since a TS
verdict is the exit code and nothing else, this runner fails the run when
anything is skipped, and additionally requires fifteen named cases (two each for
commit-on-approval, cancel-on-revert, already-merged, conflict fallback and
finalize coexistence; five for the forward restore) to have reported ``passed``
— a rename, a silent deselect, or a capability-driven skip cannot pass as
coverage.

Environment: ``config.Settings`` declares ALLOWED_ORIGIN / SECRET_KEY /
CONTEXT / DB_TYPE as required and the repo ships no ``.env``, so importing
anything under ``modules/`` raises a pydantic ValidationError unless they are
set. They are seeded with ``setdefault`` so a host that exports real values
keeps them. The working directory is pinned to ``server/`` because
``config.py`` resolves ``sql/queries`` and ``sql/migrations`` relative to the
CWD.
"""

from __future__ import annotations

import os
import pathlib
import sys

_SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]

_ENV_DEFAULTS = {
    "TESTING": "1",
    "SECRET_KEY": "test-secret-key-for-testing-only-32c",
    "ALLOWED_ORIGIN": "*",
    "CONTEXT": "/flowgate",
    "DB_TYPE": "sqlite3",
}

for _key, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

os.chdir(_SERVER_DIR)

if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

try:
    import pytest
except ImportError:  # pragma: no cover - surfaced as a plain sentence, not a traceback
    print(
        "MISSING_DEPS interpreter={} missing=pytest "
        "(install server/requirements.txt into it first)".format(sys.executable),
        file=sys.stderr,
    )
    raise SystemExit(1)


_SUITES = [
    "tests/test_tr_commit_point_0332.py",
    "tests/test_tr_commit_cancel_0332.py",
    "tests/test_tr_commit_reapply_0332.py",
    "tests/test_tr_conflict_session_0332.py",
    "tests/test_finalize_artifact_gate_0382.py",
]


def _check_capability() -> int:
    """``--check-capability``: assert the host can run the real-git classes.

    Every suite gates its end-to-end tests behind the same
    ``shutil.which("git") is not None`` probe. Kept as its own mode (and its
    own TS case) because it turns "the whole real-git axis silently skipped"
    into a named, one-second failure instead of a multi-minute run that
    reports nothing useful.
    """
    import shutil

    has_git = shutil.which("git") is not None
    print("git binary: {}".format(has_git))
    if not has_git:
        _fail("git is not on PATH — every real-git 0332 case would skip")
    print("0332 capability check: OK")
    return 0


# The three sections (0012-TR commit-on-approval / 0014-TR cancel-on-rewind / T0018
# forward-restore reapply) plus already_merged, the conflict fallback and finalize
# coexistence, pinned BY NAME. A rename, a silent deselect or a skip does not count as
# coverage.
_REQUIRED = [
    # A TR approval leaves a commit (0012-TR §1-1~§1-2)
    "test_a_tr_approval_commits_and_leaves_one_live_row",
    "test_the_commit_really_lands_and_debris_stays_out",
    # A rewind cancels it (0014-TR §1-2~§1-3)
    "test_a_cancel_writes_onto_the_row_it_cancels_and_never_deletes_it",
    "test_the_cancel_lays_one_revert_commit_per_tr_newest_first",
    # Already merged - no cancel is made, fail-closed (0014-TR §1-3, D0005 K7-①)
    "test_the_preview_calls_a_merged_group_merged_not_worktree_less",
    "test_the_group_state_gates_answer_in_the_documented_order",
    # Conflict fallback - stop there, leave the rest not_attempted (0014-TR §1-3), and
    # since TR0019 keep the conflict as a session instead of wiping it, with the old
    # destroy still reachable behind the give-up button.
    "test_a_conflict_is_parked_as_a_session_and_leaves_the_rest_untried",
    "test_giving_up_on_a_parked_conflict_restores_the_worktree_exactly_as_before",
    # finalize coexistence - TR commit/cancel do not break the 0382 absorb gate (D0005 §2)
    "test_the_finalize_absorb_still_behaves_exactly_as_before",
    "test_tool_debris_does_not_block_a_cancel",
    "test_absorb_commits_real_work_but_not_tool_debris",
    # The forward restore puts the source back (T0018 K11) - round trip and order / no
    # double application / blocked-and-retried / the stale live row left by a re-approval.
    # This is the third section R0001 asked for; the first two green on their own are the
    # half that follows a rewind backwards but not forwards.
    "test_a_forward_restore_reapplies_the_canceled_commits_newest_cancel_first",
    "test_a_step_redone_by_hand_is_never_double_applied",
    "test_a_blocked_reapply_leaves_the_rows_canceled_and_says_why",
    "test_the_reapply_retry_works_after_the_return_point_is_gone",
    "test_cancel_retry_still_targets_the_old_live_row_after_a_reapproval",
    # A conflict is no longer a dead end (TR0019). The three that matter are: the
    # conflict survives as a session both directions can resolve, the resolution stops
    # short of a commit until a person presses, and an abandoned session is reclaimed.
    # The mention case is here because "resolve it like a merge" is the one AI answer
    # that looks clean and silently re-applies the commit the person asked to cancel.
    "test_resolving_every_file_stops_at_review_and_makes_no_commit",
    "test_the_commit_button_finishes_the_cancel_and_writes_the_ledger",
    "test_the_reapply_direction_takes_the_same_road_and_ends_in_a_new_live_row",
    "test_the_sweep_closes_a_session_whose_revert_is_no_longer_in_flight",
    "test_committing_the_resolution_carries_on_with_the_rest_of_the_run",
    "test_a_resolution_that_changes_nothing_is_recorded_not_treated_as_a_git_error",
    "test_the_ai_mention_tells_a_revert_apart_from_a_merge",
]


class _Gate:
    """Records the per-test outcome pytest's own exit code throws away."""

    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {}
        self.skip_reasons: dict[str, str] = {}

    def pytest_runtest_logreport(self, report) -> None:  # noqa: D401 - pytest hook
        if report.when == "call":
            self.outcomes[report.nodeid] = report.outcome
        elif report.outcome in ("skipped", "failed"):
            # setup-phase skips (that is what `skipif` produces) and
            # setup/teardown errors never emit a "call" report at all.
            self.outcomes.setdefault(report.nodeid, report.outcome)
        if report.outcome == "skipped":
            reason = ""
            if isinstance(getattr(report, "longrepr", None), tuple):
                reason = report.longrepr[2]
            self.skip_reasons.setdefault(report.nodeid, reason)


def _fail(reason: str) -> None:
    print("GATE_FAILED {}".format(reason), file=sys.stderr)
    raise SystemExit(1)


if "--check-capability" in sys.argv:
    raise SystemExit(_check_capability())


gate = _Gate()
# -p no:cacheprovider keeps .pytest_cache out of the checked-out source root.
exit_code = pytest.main([*_SUITES, *sys.argv[1:], "-q", "-p", "no:cacheprovider"], plugins=[gate])

tally: dict[str, int] = {}
for outcome in gate.outcomes.values():
    tally[outcome] = tally.get(outcome, 0) + 1

print(
    "0332 server gate: {} passed / {} failed / {} skipped (pytest exit {})".format(
        tally.get("passed", 0), tally.get("failed", 0), tally.get("skipped", 0), int(exit_code)
    )
)

if not gate.outcomes:
    _fail("the suites collected nothing — {} did not run".format(", ".join(_SUITES)))

skipped = sorted(node for node, outcome in gate.outcomes.items() if outcome == "skipped")
if skipped:
    for node in skipped[:10]:
        print("  SKIPPED {} — {}".format(node, gate.skip_reasons.get(node, "")), file=sys.stderr)
    _fail("{} test(s) were skipped — a skip is not a pass".format(len(skipped)))

def _base_name(nodeid: str) -> str:
    """Strip ``::`` prefix and ``[params]`` suffix so a parametrized case still matches."""
    return nodeid.rsplit("::", 1)[-1].split("[", 1)[0]


missing = [
    name
    for name in _REQUIRED
    if not any(_base_name(node) == name and outcome == "passed" for node, outcome in gate.outcomes.items())
]
if missing:
    for name in missing:
        print("  NOT PASSED {}".format(name), file=sys.stderr)
    _fail("{} required 0332 case(s) did not pass".format(len(missing)))

if int(exit_code) != 0:
    _fail("pytest exited {}".format(int(exit_code)))

print("0332 server gate: OK ({} required cases passed, {} suites, {} total)".format(
    len(_REQUIRED), len(_SUITES), len(gate.outcomes)
))
raise SystemExit(0)
