"""flowgate.default.0350 TS gate for the git integration suite.

Run from ``server/`` as::

    python tools/run_0350_tests.py                    # the whole 0115 suite
    python tools/run_0350_tests.py <extra pytest args>

Modelled on ``tools/run_0295_tests.py`` (same host-portability reasons), plus a
guard that file could not have needed:

``test_git_integration_0115.py``'s real-git classes are gated behind
``needs_git``, a ``skipif`` that also probes whether this platform's git can
clone a local ``file://`` origin.  When that probe says no, every 0350 test
*skips* and pytest still exits 0 — a GREEN verdict for a suite that proved
nothing.  Since a TS verdict is the exit code and nothing else, this runner
fails the run when anything is skipped, and additionally requires the eight
0350 cases to have reported ``passed`` by name.

Environment: ``config.Settings`` declares ALLOWED_ORIGIN / SECRET_KEY / CONTEXT
/ DB_TYPE as required and the repo ships no ``.env``, so importing anything
under ``modules/`` raises a pydantic ValidationError unless they are set.  They
are seeded with ``setdefault`` so a host that exports real values keeps them.
The working directory is pinned to ``server/`` because ``config.py`` resolves
``sql/queries`` and ``sql/migrations`` relative to the CWD.
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


_SUITE = "tests/test_git_integration_0115.py"
_SUITES = [_SUITE, "tests/test_git_base_remove_route_0350.py"]


def _check_capability() -> int:
    """``--check-capability``: assert the host can run the real-git E2E classes.

    Reuses the suite's own probe so this answers the same question ``needs_git``
    asks.  Kept as its own mode (and its own TS case) because it turns "the
    whole 0350 axis silently skipped" into a named, one-second failure instead
    of a 90-second run that reports nothing useful.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_fg0350_suite_probe", _SERVER_DIR / _SUITE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    has_git = bool(getattr(module, "_GIT", False))
    can_clone = bool(getattr(module, "_FILE_CLONE", False))
    print("git binary: {}  local file:// clone: {}".format(has_git, can_clone))
    if not has_git:
        _fail("git is not on PATH — every real-git 0350 case would skip")
    if not can_clone:
        _fail("git cannot clone a local file:// origin — every real-git 0350 case would skip")
    print("0350 capability check: OK")
    return 0

# T0004 §2.4 / NR0003 §7 items 1-6. Named individually so a rename, a silent
# deselect, or an environment-driven skip cannot pass as coverage.
_REQUIRED = [
    "TestBaseUntrackedConflictEndToEnd0350::test_merge_and_merge_only_both_hit_the_same_409",
    "TestBaseUntrackedConflictEndToEnd0350::test_failure_is_side_effect_free",
    "TestBaseUntrackedConflictEndToEnd0350::test_revert_tracked_then_untracked_only_matches_b0001_order",
    "TestBaseUntrackedConflictEndToEnd0350::test_noncolliding_untracked_never_blocks_merge",
    "TestBaseRemoveAndRetry0350::test_remove_deletes_and_validates",
    "TestBaseRemoveAndRetry0350::test_remove_then_retry_merge_pushes",
    "TestBaseRemoveAndRetry0350::test_remove_then_retry_merge_only_stays_local",
    "TestBaseRemoveAndRetry0350::test_selective_commit_then_retry_also_unblocks",
    # The HTTP boundary the client actually talks to (added by this TS).
    "TestBaseRemoveRoute0350::test_registered_at_the_path_the_client_calls",
    "TestBaseRemoveRoute0350::test_delegates_the_files_list_verbatim",
    "TestBaseRemoveRoute0350::test_service_error_becomes_the_git_envelope_never_500[422-path_ignored-details0]",
    "TestBaseRemoveRoute0350::test_requires_the_edit_permission",
    "TestBaseRemoveRoute0350::test_never_folded_into_the_tracked_revert_route",
    "TestBaseRemoveRoute0350::test_handler_is_sync_so_it_runs_off_the_event_loop",
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
    "0350 server gate: {} passed / {} failed / {} skipped (pytest exit {})".format(
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

missing = [
    name
    for name in _REQUIRED
    if not any(node.endswith(name) and outcome == "passed" for node, outcome in gate.outcomes.items())
]
if missing:
    for name in missing:
        print("  NOT PASSED {}".format(name), file=sys.stderr)
    _fail("{} required 0350 case(s) did not pass".format(len(missing)))

if int(exit_code) != 0:
    _fail("pytest exited {}".format(int(exit_code)))

print("0350 server gate: OK ({} required cases passed)".format(len(_REQUIRED)))
raise SystemExit(0)
