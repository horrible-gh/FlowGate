"""Pytest configuration for FlowGate tests — shared fixtures and setup."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
# Git for Windows may ask forever on a failed unlink when stdin is a pipe.
# Tests are unattended: fail that operation instead of waiting for a console.
os.environ.setdefault("GIT_ASK_YESNO", "false")
os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
# pymysql calls getpass.getuser() at import time; a Self-check host may export no user name.
os.environ.setdefault("USERNAME", "flowgate-test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_REPO_ROOT = _SERVER_DIR.parent
# Some suites import `modules.*` without adding server/ themselves; make single-file runs work.
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))
# flowgate.v02.0146 T#2: the tests/test_fg_gate_*_0085-style suites import the shared harness of tests/flowgate/
# (``from test_fg_card_settings import ...``) by module name. That only resolved once pytest had already collected a
# file of tests/flowgate, so the same selection errored at collection ("No module named 'test_fg_card_settings'")
# or not depending on the order of its arguments. Appended, never prepended: nothing that resolves today changes.
_FLOWGATE_TESTS_DIR = str(Path(__file__).resolve().parent / "flowgate")
if _FLOWGATE_TESTS_DIR not in sys.path:
    sys.path.append(_FLOWGATE_TESTS_DIR)


# ── Repository pollution guard (0382 B0001 / NR0003 proposal 2-b) ──────────
#
# The incident began when a test created a directory inside the repository.
# Its hidden type made it invisible in the UI. Detect this mechanically: fail
# the session if new artifacts remain and print their names and locations.
# Warnings alone go unnoticed (NR §8-2).

# Match scratch directory names too: .gitignore can hide them from git,
# but writing inside the repository is itself what this guard must detect.
_SCRATCH_GLOB = ".test-tmp*"
# A full rglob scan takes seconds per session because of node_modules.
# Scan the known locations: repository root, server/, server/tests/, client_gui/.
_SCRATCH_SCAN_DIRS = (
    _REPO_ROOT, _SERVER_DIR, _SERVER_DIR / "tests", _REPO_ROOT / "client_gui",
)
_POLLUTION_LIST_MAX = 20


def _repo_pollution_snapshot():
    """Snapshot repository artifacts; return None (skip monitoring) if git is unavailable."""
    if shutil.which("git") is None:
        return None
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    found = {path for path in (proc.stdout or "").split("\0") if path}
    for base in _SCRATCH_SCAN_DIRS:
        if not base.is_dir():
            continue
        for path in base.glob(_SCRATCH_GLOB):
            found.add(str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))
    return found


@pytest.fixture(scope="session", autouse=True)
def repo_pollution_guard():
    """Fail the session if tests leave new artifacts inside the repository."""
    before = _repo_pollution_snapshot()
    yield
    after = _repo_pollution_snapshot()
    if before is None or after is None:
        return
    leaked = sorted(after - before)
    if not leaked:
        return
    shown = "\n".join(f"  - {path}" for path in leaked[:_POLLUTION_LIST_MAX])
    more = "" if len(leaked) <= _POLLUTION_LIST_MAX else f"\n  ... 외 {len(leaked) - _POLLUTION_LIST_MAX}개"
    pytest.fail(
        "테스트가 저장소 안에 파일을 남겼습니다 (0382 B0001 재발 방지).\n"
        f"새로 생긴 항목 {len(leaked)}개:\n{shown}{more}\n"
        "스크래치는 저장소 밖에 만드십시오 — server/tests/scratch_support.py 의 "
        "session_scratch() 를 쓰면 됩니다.",
        pytrace=False,
    )


# ── Host capabilities and what they silently take with them ──────────────────
#
# flowgate.default.0394 T0004 (NR0003 §5.2 / §13-10).
#
# Nine places in this suite skip when the host cannot do something — no git binary, no
# symlinks, no run.bat, an unimportable module. Skipping is the right behaviour; being
# quiet about it is not. `test_git_integration_0115.py` alone is 107 cases, and on a host
# without git all 107 vanish and the run still ends "failed 0" — the same words a full
# green run prints. R0001 §8 calls for a green suite AND for intentional skips to be
# explicit, and a bare skip count cannot tell "we meant to skip this" from "this host
# could not run it".
#
# So the capabilities are named in one place and reported in two directions: the header
# says up front what this host can do, and the summary says afterwards what that cost in
# tests. And because a report nobody reads is not a gate, FLOWGATE_REQUIRE_FULL_SUITE=1
# turns any missing capability into a failure — that is what a release/Full run sets, so
# "green" there means the whole suite actually ran.

def _git_available() -> bool:
    return shutil.which("git") is not None


def _symlinks_available() -> bool:
    """Windows needs Developer Mode or admin rights, so this is a real host difference."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            (Path(tmp) / "link").symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


# name -> (label, probe, required_for_a_full_run)
#
# `run.bat` is reported but not required: setup.ps1 generates it and .gitignore hides it,
# so it is legitimately absent on a fresh clone and demanding it would make the strict
# mode below something everyone turns off. The other three are things a machine either
# has or should get before claiming it ran the whole suite.
CAPABILITIES: dict[str, tuple[str, callable, bool]] = {
    "git": ("git binary on PATH", _git_available, True),
    "symlinks": ("symlink creation", _symlinks_available, True),
    "api_source_tree": (
        "server/modules/flow_gate/api present",
        lambda: (_SERVER_DIR / "modules" / "flow_gate" / "api").is_dir(),
        True,
    ),
    "run_bat": (
        "run.bat (generated by setup.ps1)",
        lambda: (_SERVER_DIR.parent / "run.bat").is_file(),
        False,
    ),
}

# Skip reasons, as written at the skip site, mapped to the capability they stand for.
# A reason that matches nothing is reported under "other" rather than dropped.
_REASON_TO_CAPABILITY = (
    ("git", "git"),
    ("symlink", "symlinks"),
    ("api source tree", "api_source_tree"),
    ("run.bat", "run_bat"),
    ("flowgate_helper", "other"),
    ("sqloader", "other"),
)

_capability_state: dict[str, bool] = {}
_skips_by_capability: dict[str, int] = {}


def _classify_skip(reason: str) -> str:
    lowered = (reason or "").lower()
    # Tier deferrals are not missing host capabilities. Counting them as 'other'
    # would bury unexpected skips under 1,800 planned deferrals in one
    # --tier=fast run (0394 T0014).
    if _TIER_SKIP_MARK in lowered:
        return "tier"
    for needle, capability in _REASON_TO_CAPABILITY:
        if needle in lowered:
            return capability
    return "other"


# ── Execution tiers: Fast / Standard / Full ───────────────────────────────
#
# flowgate.default.0394 T0004 (R0001 §6, NR0003 §10.4 / §13-12).
#
# NR0003 §10.1 measured where the 474 seconds go: the 40 slowest tests together are 15%
# of it, and the other 85% is spread thinly across 3,223 cases. Deleting slow tests
# therefore buys nothing, which is why R0001 §6 asks for tiers instead — and why this is
# a selection mechanism, not a cleanup.
#
# A test's tier is decided in two steps:
#
#   1. An explicit `@pytest.mark.fast` / `.standard` / `.full` (on the test, its class or
#      the module's `pytestmark`) always wins. That is the escape hatch for anything the
#      rule below gets wrong, and the only thing a new test has to know about.
#   2. Otherwise the tier is derived from what the module's source actually does, using
#      the classification NR0003 §10.2 measured:
#        fast      — no TestClient, no subprocess/git, no sleep
#        standard  — everything except git/subprocess work and e2e/dialect/migration files
#        full      — the rest
#
# Deriving rather than keeping a list is the point. NR0003 §10.4 warned that a list of
# files leaves new files in no tier at all; here a new file is classified the moment it
# is written, by the same rule as everything else, and a file that needs to say otherwise
# says so in one line at the top of itself.
#
# `--tier=full` (the default) selects everything, so nothing about an ordinary run changes.

_TIER_ORDER = ("fast", "standard", "full")

# Level 1 excludes anything that starts a server, shells out, or waits on the clock.
_NOT_FAST = ("TestClient", "subprocess", "sleep(", '"git"', "'git'")
# Level 2 additionally excludes the git/worktree/e2e/dialect families.
_NOT_STANDARD_TEXT = ("subprocess", '"git"', "'git'")
_NOT_STANDARD_NAME = ("_e2e", "dialect", "migration", "worktree", "git_")

_tier_cache: dict[str, str] = {}

# Skip-reason tag for deferred tests. The summary counts this exact string,
# so keep it synchronized with the marker text below.
_TIER_SKIP_MARK = "(--tier)"

# Count tier totals from actual reports, not collection hooks.
# With pytest-xdist, collection hooks run in workers; their module globals
# are invisible to the controller printing the summary. In practice,
# `--tier=fast -n auto` skipped 117 cases without printing "이 실행은 전체가 아닙니다".
# Parallel execution had reintroduced the silent skips described in NR0003 §5.2.
# Reports reach the controller, so counting here works in both execution modes.
_items_started = 0
_tier_deferred = 0


def _tier_for_module(path: Path) -> str:
    """Derived tier for a test module — see the note above for the rule."""
    key = str(path)
    cached = _tier_cache.get(key)
    if cached:
        return cached

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    name = path.name

    if not any(needle in text for needle in _NOT_FAST):
        tier = "fast"
    elif (
        not any(needle in text for needle in _NOT_STANDARD_TEXT)
        and not any(needle in name for needle in _NOT_STANDARD_NAME)
    ):
        tier = "standard"
    else:
        tier = "full"

    _tier_cache[key] = tier
    return tier


def _declared_tier(item) -> "str | None":
    """An explicit marker, if the test (or its class/module) carries one."""
    for tier in _TIER_ORDER:
        if item.get_closest_marker(tier) is not None:
            return tier
    return None


# ── Parallel execution must keep each file intact ────────────────────────
#
# flowgate.default.0394 T0014 (NR0003 §10.3 / §13-13).
#
# pytest-xdist's default (`--dist load`) distributes individual tests to workers.
# This repository shares module fixtures (temporary DBs and FLOWGATE_* restoration)
# and storage roots established at import time. Splitting a file across processes
# can run tests without their fixtures. Failures then vary with worker allocation,
# look intermittent, and undermine confidence in parallel execution itself:
# exactly the outcome this validation must prevent.
#
# Reject unsafe distribution before execution starts. Documentation alone
# cannot protect someone who omits an option and sees unexpected failures
# instead of rereading the instructions.
#
# Safe: loadfile (file) and loadscope (module/class), keeping a file on one worker.
# Rejected: load (default), worksteal, loadgroup, each.
_SAFE_DIST_MODES = ("no", "loadfile", "loadscope")


def _dist_mode_problem(numprocesses, dist) -> "str | None":
    """Return None for a safe parallel configuration, otherwise the rejection reason."""
    if not numprocesses:
        return None
    if dist in _SAFE_DIST_MODES:
        return None
    return (
        f"병렬 실행에 --dist {dist} 는 쓸 수 없습니다. 이 저장소의 테스트는 모듈 스코프 "
        "임시 DB 와 import 시점 환경변수를 파일 단위로 공유하므로, 한 파일이 여러 워커로 "
        "찢어지면 픽스처가 세워지지 않은 채 실행됩니다 (NR0003 §10.3). "
        "`-n auto --dist loadfile` 로 실행하십시오."
    )


# ── Per-run worker slot pool (flowgate.v02.0114 T#4, D 0006 3.5 (3)) ───────
#
# Self-check reserves R processors per run and binds the entire process tree
# to them through OS enforcement. This cooperative guard prevents creating
# more than R xdist workers and wasting time sharing those processors.
#
# When Self-check supplies slot-0..slot-<R-1> in the run's temporary directory:
# - Each pytest process running tests (serial session or xdist worker) acquires
#   an OS file lock on one free slot until exit; the OS releases it after a crash.
# - Applied workers = min(requested count, including -n auto, free slots). The
#   controller runs no tests; it hands its reserved slots to workers as they start.
# - With no free slots (nested pytest), run serially without a slot or xdist worker;
#   do not wait or fail. A worker losing its slot handoff race fails without tests.
# With no pool information (local execution outside Self-check), do nothing.
_SLOT_DIR_ENV = "FLOWGATE_SELFCHECK_SLOT_DIR"
_SLOTS_ENV = "FLOWGATE_SELFCHECK_SLOTS"
_slot_handles: list = []      # slot files whose lock this process holds for its whole life
_slot_handoff: list = []      # controller: (index, handle) counted for workers not started yet
_slot_state: dict = {}        # what the result block reports (requested / applied / source)


def _slot_pool() -> "tuple[Path, int] | None":
    raw_dir, raw_size = os.environ.get(_SLOT_DIR_ENV), os.environ.get(_SLOTS_ENV)
    if not raw_dir or not raw_size:
        return None
    try:
        size = int(raw_size)
    except ValueError:
        return None
    pool = Path(raw_dir)
    if size < 1 or not pool.is_dir():
        return None
    return pool, size


def _lock_slot(path: Path):
    """An open handle holding ``path``'s exclusive lock, or None when another process holds it."""
    try:
        handle = open(path, "a+b")
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _take_slots(pool: Path, size: int, wanted: int, first=None) -> list:
    order = list(range(size))
    if isinstance(first, int) and 0 <= first < size:
        order.remove(first)
        order.insert(0, first)
    taken = []
    for index in order:
        if len(taken) >= wanted:
            break
        handle = _lock_slot(pool / f"slot-{index}")
        if handle is not None:
            taken.append((index, handle))
    return taken


def _processor_count() -> "int | None":
    """How many processors this process may run on: the run tree's limit inside Self-check."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                                        ctypes.POINTER(ctypes.c_size_t)]
            mine, system = ctypes.c_size_t(), ctypes.c_size_t()
            if not kernel32.GetProcessAffinityMask(kernel32.GetCurrentProcess(), ctypes.byref(mine),
                                                   ctypes.byref(system)):
                return None
            return bin(mine.value).count("1") or None
        return len(os.sched_getaffinity(0)) or None
    except Exception:
        return None


def _apply_slot_pool(config) -> None:
    pool = _slot_pool()
    if pool is None:
        return
    directory, size = pool
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        taken = _take_slots(directory, size, 1, workerinput.get("flowgate_slot"))
        if not taken:
            raise pytest.UsageError(
                "Self-check worker slot pool: no free slot for this xdist worker (lost a race); "
                "it runs no test and the run fails rather than exceed the reservation.")
        _slot_handles.append(taken[0][1])
        return
    option = config.option
    distributed = (getattr(option, "dist", "no") or "no") != "no"
    requested = len(getattr(option, "tx", None) or []) if distributed else 0
    if requested:
        taken = _take_slots(directory, size, requested)
        if taken:
            option.numprocesses = len(taken)
            option.tx = ["popen"] * len(taken)
            _slot_handoff.extend(taken)
        else:
            option.numprocesses = 0
            option.tx = []
            option.dist = "no"
        applied, source = len(taken), ("pool" if taken else "none")
    else:
        taken = _take_slots(directory, size, 1)
        _slot_handles.extend(handle for _index, handle in taken)
        applied, source = 0, ("pool" if taken else "none")
    _slot_state.update(requested=requested, applied=applied, slot_source=source, pool_size=size,
                       cpu_limit=_processor_count())


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    """Controller, right before an xdist worker starts: hand it one of the counted slots."""
    if _slot_handoff:
        index, handle = _slot_handoff.pop(0)
        node.workerinput["flowgate_slot"] = index
        handle.close()


def pytest_unconfigure(config):
    while _slot_handoff:
        _slot_handoff.pop()[1].close()
    while _slot_handles:
        _slot_handles.pop().close()


def pytest_addoption(parser):
    parser.addoption(
        "--fg-git-mode", action="store", default="template",
        choices=("template", "direct"),
        help="Git seed preparation: worker template copies or original direct commands.",
    )
    parser.addoption(
        "--fg-db-mode", action="store", default="template",
        choices=("template", "direct"),
        help="SQLite test preparation: session template copies or direct migration replay.",
    )
    parser.addoption(
        "--tier",
        action="store",
        default="full",
        choices=list(_TIER_ORDER),
        help="Execution tier: fast (development) / standard (task completion) / full (release).",
    )


def pytest_collection_modifyitems(config, items):
    requested = config.getoption("--tier")
    if requested == "full":
        return   # the default: run everything, exactly as before this option existed

    ceiling = _TIER_ORDER.index(requested)
    for item in items:
        tier = _declared_tier(item) or _tier_for_module(Path(str(item.fspath)))
        if _TIER_ORDER.index(tier) > ceiling:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"tier {tier} > requested {requested} {_TIER_SKIP_MARK}"
                )
            )


def pytest_configure(config):
    # Probe here, not in pytest_report_header: `-q` suppresses the header entirely, and
    # the summary would then have nothing to report — which is the exact silence this
    # whole block exists to remove, and every CI invocation uses -q.
    for name, (_label, probe, _required) in CAPABILITIES.items():
        _capability_state[name] = _safe_probe(probe)

    # flowgate.v02.0114 T#3 (D 0006 7.1): the Self-check result block. Composed at
    # pytest_terminal_summary like the reports below and printed as the very last output,
    # with the capability judgement above as its host evidence and the test definition
    # fingerprints taken when collection finishes (tests/selfcheck_evidence.py). A copy of
    # this conftest in a synthetic tree without that module (test_repo_scratch_guard_0382)
    # prints no block, which Self-check reads as "no structured result".
    try:
        import selfcheck_evidence
    except ImportError:
        selfcheck_evidence = None
    if selfcheck_evidence is not None:
        selfcheck_evidence.register(config, capabilities=lambda: dict(_capability_state))
        # 0114 T#4: the slot pool's requested / applied / source and the run tree's processor
        # limit go into the result block as well (read when the block is composed).
        selfcheck_evidence.register(config, parallel=lambda: dict(_slot_state) or None)

    # Check only the controller; workers run their allocation serially and have no dist setting.
    if getattr(config, "workerinput", None) is None:
        problem = _dist_mode_problem(
            getattr(config.option, "numprocesses", None),
            getattr(config.option, "dist", "no"),
        )
        if problem:
            raise pytest.UsageError(problem)

    # 0114 T#4: the worker slot pool (above). Runs before xdist's own (trylast) configure, so a
    # lowered worker count is what xdist starts.
    _apply_slot_pool(config)

    # flowgate.v02.0146 T#2: a per-test hang limit (tests/hang_guard.py), so one hanging test fails by name
    # instead of consuming the whole Self-check ceiling. Missing in a synthetic tree (test_repo_scratch_guard_0382).
    try:
        import hang_guard
    except ImportError:
        hang_guard = None
    if hang_guard is not None:
        hang_guard.install(config)


def pytest_report_header(config):
    missing = [name for name, ok in _capability_state.items() if not ok]
    line = "host capabilities: " + ", ".join(
        f"{name}={'yes' if ok else 'NO'}" for name, ok in _capability_state.items()
    )
    if missing:
        line += (
            f"\n  ⚠ missing: {', '.join(missing)} — the tests that need them will be skipped,"
            " NOT run. Set FLOWGATE_REQUIRE_FULL_SUITE=1 to make that a failure."
        )
    requested = config.getoption("--tier", default="full")
    if requested != "full":
        line += (
            f"\nexecution tier: {requested} — 이 실행은 전체가 아닙니다. release 전에는 "
            f"--tier 없이(=full) 한 번 돌리십시오."
        )
    return line


def pytest_runtest_logreport(report):
    global _items_started, _tier_deferred

    # Exactly one setup report appears per test, including skipped tests.
    # This is therefore the total number of tests handled by this run.
    if report.when == "setup":
        _items_started += 1

    if report.skipped:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        key = _classify_skip(reason)
        if key == "tier":
            _tier_deferred += 1
        _skips_by_capability[key] = _skips_by_capability.get(key, 0) + 1


def pytest_sessionfinish(session, exitstatus):
    """Send environment leaks collected by this worker to the controller."""
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput["flowgate_env_leaks"] = list(_env_leaks)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Merge a terminating worker's leak report into the controller list.

    optionalhook allows conftest to load even when pytest-xdist is unavailable.
    """
    for line in (getattr(node, "workeroutput", None) or {}).get("flowgate_env_leaks", []):
        if line not in _env_leaks:
            _env_leaks.append(line)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _env_leaks:
        terminalreporter.write_sep("=", "환경변수 누출 (0394 T0004)")
        for line in _env_leaks:
            terminalreporter.write_line(f"  · {line}")
        terminalreporter.write_line(
            "  이 실행에서는 되돌려 놓았지만, 근원은 해당 모듈입니다 — 환경변수를 지우지 "
            "말고 원래 값으로 복원하십시오. 지우면 import 시점에 값을 발행한 다른 스위트의 "
            "것까지 함께 사라집니다."
        )

    requested = config.getoption("--tier", default="full")
    workers = getattr(config.option, "numprocesses", None)
    if _tier_deferred:
        # State explicitly that zero failures in a tier run do not mean all tests passed.
        # NR0003 §5.2 explains this distinction.
        #
        # Subtract ALL skips from the executed count, including missing host capabilities.
        # Previously --tier=fast printed "1614건 실행" while pytest reported
        # 1613 passed / 1654 skipped: the extra case lacked run.bat.
        # The line intended to expose silent skips was itself hiding one,
        # making that single counting error especially significant (0394 T0014).
        other_skipped = sum(_skips_by_capability.values()) - _tier_deferred
        ran = _items_started - _tier_deferred - other_skipped
        extra = f" 그 밖의 사유로 {other_skipped}건 더 건너뜀." if other_skipped else ""
        terminalreporter.write_sep("=", "실행 계층 (0394 T0004)")
        terminalreporter.write_line(
            f"  --tier={requested}: {ran}건 실행, "
            f"{_tier_deferred}건은 상위 계층 몫으로 건너뜀.{extra} "
            f"이 실행의 '실패 0' 은 전체 통과가 아닙니다."
        )
    if workers:
        terminalreporter.write_sep("=", "병렬 실행 (0394 T0014)")
        terminalreporter.write_line(
            f"  워커 {workers}개 / --dist {getattr(config.option, 'dist', 'no')} — "
            f"파일 단위로만 나눕니다. 결과 판정은 순차 실행과 같아야 합니다."
        )

    if not _skips_by_capability and all(_capability_state.values()):
        return

    terminalreporter.write_sep("=", "환경 능력 / skip 보고 (0394 T0004)")
    for name, (label, _probe, required) in CAPABILITIES.items():
        ok = _capability_state.get(name, True)
        skipped = _skips_by_capability.get(name, 0)
        mark = "✓" if ok else "✗"
        note = "" if required else "  (full 실행 요건 아님)"
        terminalreporter.write_line(
            f"  {mark} {name:<16} {label:<45} skipped={skipped}{note}"
        )
    other = _skips_by_capability.get("other", 0)
    if other:
        terminalreporter.write_line(f"    (그 밖의 사유로 건너뛴 테스트: {other})")

    missing = [name for name, ok in _capability_state.items() if not ok]
    if missing:
        terminalreporter.write_line(
            "  ⚠ 위 ✗ 항목 때문에 빠진 테스트는 실행되지 않았습니다 — 실패 0 이 곧 "
            "전부 통과는 아닙니다."
        )


@pytest.fixture(scope="session", autouse=True)
def require_full_suite_when_asked():
    """Fail the run when a Full-level run lands on a host that cannot do everything.

    Off by default so a developer laptop keeps skipping quietly-but-visibly; on for the
    Full tier, where the whole point is that nothing was left out.
    """
    if os.environ.get("FLOWGATE_REQUIRE_FULL_SUITE", "").strip() not in ("1", "true", "TRUE"):
        yield
        return

    missing = [
        name for name, (_label, _probe, required) in CAPABILITIES.items()
        if required and not _capability_state.get(name, False)
    ]
    if missing:
        pytest.fail(
            "FLOWGATE_REQUIRE_FULL_SUITE=1 인데 이 호스트가 갖추지 못한 능력이 있습니다: "
            f"{', '.join(missing)}. 이대로 돌리면 해당 테스트가 조용히 빠진 채 '실패 0' 이 "
            "나옵니다 (NR0003 §5.2). 능력을 갖춘 호스트에서 돌리거나, 이 실행이 전체 "
            "검증이 아님을 인정하고 변수를 끄십시오.",
            pytrace=False,
        )
    yield


def _safe_probe(probe) -> bool:
    try:
        return bool(probe())
    except Exception:
        return False


# ── FLOWGATE_* variables are borrowed: restore them at module boundaries ──
#
# flowgate.default.0394 T0004 (R0001 §5.1: order/environment-dependent tests).
#
# Many suites publish their storage roots at import time using
# `os.environ["FLOWGATE_STORAGE_DIR"] = tempfile.mkdtemp(...)`. Pytest imports
# all modules before running tests, so these values already exist at execution.
# If a test overwrites a value and then removes it with `del` or `pop`,
# it erases another suite's value as well as its own and leaves the process
# without any storage root.
#
# This happened when test_numbering.py and test_outbound_single.py each
# deleted FLOWGATE_STORAGE_DIR during cleanup. The later
# test_worktree_resolution_e2e_0280.py lost its temporary root, and
# ensure_worktree created files in the wrong location and returned "failed":
# seven setup errors, although all eight tests passed in isolation. NR0003 §4.1
# missed this order dependency because errors, unlike failures, did not appear
# in the -rf summary.
#
# Those two files were fixed separately. NR0003 §5.3 calls for a global check
# of the global rule: modules borrow the environment and return it unchanged.
#
# Restore and report instead of failing. Restoration prevents the incident
# without turning existing leaks into new failures. The end-of-run report names
# each leaking module and variable so the cause can be fixed at its source.

_env_leaks: list[str] = []


def _flowgate_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.startswith("FLOWGATE_")}


@pytest.fixture(scope="module", autouse=True)
def flowgate_env_returned_as_found(request):
    """Restore FLOWGATE_* variables changed by a module when the module finishes."""
    before = _flowgate_env()
    yield
    after = _flowgate_env()
    if after == before:
        return

    module = getattr(request.node, "name", "?")
    for key in sorted(set(before) | set(after)):
        old_value, new_value = before.get(key), after.get(key)
        if old_value == new_value:
            continue
        if old_value is None:
            what = f"{key} 를 새로 남겼다"
            os.environ.pop(key, None)
        elif new_value is None:
            what = f"{key} 를 지웠다"
            os.environ[key] = old_value
        else:
            what = f"{key} 를 바꿔 놓았다"
            os.environ[key] = old_value
        _env_leaks.append(f"{module}: {what}")


# ── 0669: one server instance per test ───────────────────────────────────────
# The Group lock registers this process as a server_instance the first time it is used
# outside server startup, and every resource_lock row points at that row (FK). Tests
# swap in a fresh DB per test, so an id registered against the previous test's DB would
# fail the next insert. Drop it around every test; the next lock use registers again.
# Only acts when the registry is already imported, so other suites pay nothing.

def _drop_server_instance() -> None:
    import sys
    registry = sys.modules.get("modules.flow_gate.services.git.instance_registry")
    if registry is not None:
        registry.shutdown()


@pytest.fixture(autouse=True)
def server_instance_per_test():
    _drop_server_instance()
    yield
    _drop_server_instance()


def get_migration_files() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return files


@pytest.fixture(scope="session")
def git_repositories(tmp_path_factory, pytestconfig):
    """Worker/session-local originals; only copies reach product Git operations."""
    from git_template_support import GitRepositoryFactory

    return GitRepositoryFactory(
        tmp_path_factory.mktemp("git-templates"),
        mode=pytestconfig.getoption("--fg-git-mode"),
    )


@pytest.fixture(scope="session")
def migrated_sqlite_db(tmp_path_factory, pytestconfig):
    """Fresh per-call DBs from one worker-local template, or explicit direct replay.

    Build before exposing the factory: pytest caches a failed session setup, so
    every consumer errors if any migration fails. Seeds belong only to copies.
    Each execution uses a fresh basetemp, so templates are never reused across
    runs or sessions; pytest may retain recent temporary directories on disk.

    flowgate.default.0394 T0004: test_auth.py once replayed only migrations
    001/002/005 and missed auth_sessions when 067a arrived. Keep one full-chain
    builder: never freeze a per-suite subset or swallow migration errors, which
    would hide the schema drift this fixture exists to detect.
    """
    from sqlite_db_support import SqliteDbFactory

    factory = SqliteDbFactory(
        tmp_path_factory.mktemp("sqlite-template"), get_migration_files(),
        mode=pytestconfig.getoption("--fg-db-mode"),
    )
    factory.prepare()
    return factory

@pytest.fixture(scope="session")
def all_migrations_db():
    """Create a test database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply all migrations in order
    for migration_file in get_migration_files():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            print(f"✓ Applied: {migration_file.name}")
        except Exception as e:
            print(f"⚠ Error in {migration_file.name}: {e}")

    # Seed required data
    conn.executescript(
        """
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES
                ('role_admin','Administrator',1,datetime('now'),datetime('now')),
                ('role_manager','Manager',1,datetime('now'),datetime('now')),
                ('role_worker','Worker',1,datetime('now'),datetime('now')),
                ('role_viewer','Viewer',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','Manage system settings',datetime('now')),
                ('system.user.read','View users',datetime('now')),
                ('system.user.create','Create user',datetime('now')),
                ('system.user.update','Update user',datetime('now')),
                ('system.user.delete','Delete user',datetime('now')),
                ('system.user.assign_role','Assign role',datetime('now')),
                ('project.settings.read','View project settings',datetime('now')),
                ('project.settings.edit','Edit project settings',datetime('now')),
                ('project.document_type.create','Create document type',datetime('now')),
                ('project.document_type.update','Update document type',datetime('now')),
                ('project.document_type.delete','Delete document type',datetime('now'));

        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_admin','system.settings.manage'),
                ('role_admin','system.user.read'),
                ('role_admin','system.user.create'),
                ('role_admin','system.user.update'),
                ('role_admin','system.user.delete'),
                ('role_admin','system.user.assign_role'),
                ('role_admin','project.settings.read'),
                ('role_admin','project.settings.edit'),
                ('role_admin','project.document_type.create'),
                ('role_admin','project.document_type.update'),
                ('role_admin','project.document_type.delete'),
                ('role_manager','project.settings.read'),
                ('role_manager','project.settings.edit'),
                ('role_manager','project.document_type.create'),
                ('role_manager','project.document_type.update'),
                ('role_manager','project.document_type.delete');

        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES
                ('__SYSTEM__','[System]',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES
                ('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
            VALUES('usr_admin','__SYSTEM__','role_admin',datetime('now'));

        INSERT OR IGNORE INTO system_settings(setting_key,setting_value,value_type,updated_at)
            VALUES('storage_root','/data/flowgate','string',datetime('now'));
    """
    )

    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def test_db(all_migrations_db):
    """Lend the shared connection to one test, with no open transaction; roll back its writes.

    flowgate.default.0394 T0014 (NR0003 §13-13: parallel safety validation).

    Four files share the session-scoped `all_migrations_db` connection.
    `test_tokens_resolve_conflict_scope_0233.py` inserts into tokens without
    committing or cleaning up. Python's sqlite3 opens a write transaction and
    leaves it open, so the next file inherits that transaction.

    SQLite silently ignores `PRAGMA foreign_keys` inside a transaction. Thus
    the round-trip INSERT in `test_auto_approved_locale_0099.py`, intended to run
    with FKs disabled, instead raises IntegrityError because of the earlier file.

    Alphabetical serial execution placed these files in the opposite order,
    hiding the issue and supporting NR0003 §4.1's conclusion of no order dependency.
    Distributing files to workers changes the order: this case failed under
    `--tier=standard -n auto --dist loadfile`. Parallel execution exposed existing
    cross-file shared state rather than introducing the defect.

    Close transactions both when lending and when reclaiming the connection.
    Rollback preserves seed data committed at session start and removes only
    test writes; leaving rows in another test's tables has the same root cause.
    """
    all_migrations_db.rollback()
    try:
        yield all_migrations_db
    finally:
        all_migrations_db.rollback()



# ── flowgate.v02.0119 T#2 (T-1): the completion stand-in of the Graph engine suites ──────────────────────────────────
#
# Production has no completion fallback any more: with no handler registered the Graph's ``card_completion_ports``
# answers "no completion" whatever the document's review status, and the server's start fails when it cannot register
# ``flowgate.card_completion``. The Graph engine suites written before 0119 (runs, gates, attempts, time machine, card
# settings ...) drive their nodes to done by approving the result document; for them this TEST-ONLY stand-in answers as
# the document's approval did (approved / wf_done -> complete). It is registered only when no handler is registered
# (a suite that registers ``flowgate.card_completion`` replaces it for its own test) and removed after each test.
# What a suite proves through it is the engine's behaviour, NOT the completion contract: the completion contract (records
# as the only source, no completion when unregistered) is verified with the real registration in
# ``tests/flowgate/test_fg_card_completion_0119.py``.

def _completion_stand_in_for_engine_suites(flow_id, run, node_key, doc):
    return {"completion_id": None, "legacy": True} if (doc or {}).get("doc_review_status") in ("approved", "wf_done") \
        else None


@pytest.fixture(autouse=True)
def _graph_completion_stand_in():
    try:
        from modules.flow_gate.graph_workflow.services import card_completion_ports as ports
    except Exception:  # noqa: BLE001 — a suite without the Graph package
        yield
        return
    if ports.registered(ports.COMPLETION_OF):
        yield
        return
    ports.register(ports.COMPLETION_OF, _completion_stand_in_for_engine_suites)
    try:
        yield
    finally:
        if ports._handler(ports.COMPLETION_OF) is _completion_stand_in_for_engine_suites:
            ports.unregister(ports.COMPLETION_OF)
