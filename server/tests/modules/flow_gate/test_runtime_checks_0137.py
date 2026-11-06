"""flowgate.v02.0137 T0004 §4 (B): dependency pin, effective pool and connection budget checks.

Pure unit tests plus one isolated churn measurement on the INSTALLED sqloader: the real
PostgreSQLWrapper and psycopg2 pool run with only `psycopg2.connect` replaced by a
counting fake, so no network or database is touched.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

import runtime_checks  # noqa: E402


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def info(self, msg):
        self.lines.append(("info", msg))

    def warning(self, msg):
        self.lines.append(("warning", msg))

    def error(self, msg):
        self.lines.append(("error", msg))


REQ = """\
uvicorn[standard]==0.34.0
sqloader==0.2.17
psycopg2-binary==2.9.12
pytest==8.3.4
jinja2>=3.1.0
# comment==1.0
"""


def test_parse_pins_reads_only_exact_pins():
    pins = runtime_checks.parse_pins(REQ)
    assert pins == {"uvicorn": "0.34.0", "sqloader": "0.2.17", "psycopg2-binary": "2.9.12",
                    "pytest": "8.3.4"}


def test_repo_requirements_pin_the_pool_capable_sqloader():
    pins = runtime_checks.parse_pins(runtime_checks.REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    major_minor_patch = tuple(int(x) for x in pins["sqloader"].split("."))
    assert major_minor_patch >= (0, 2, 16), "0.2.15 ignores DB_POOL_MIN/MAX"


def test_deployed_drift_is_reported_as_critical():
    installed = {"uvicorn": "0.34.0", "sqloader": "0.2.15", "psycopg2-binary": "2.9.12"}
    problems = runtime_checks.pin_mismatches(runtime_checks.parse_pins(REQ), installed.get)
    # pytest missing on a production venv is NOT reported; sqloader drift is, as critical.
    assert problems == [{"package": "sqloader", "required": "0.2.17", "installed": "0.2.15",
                         "critical": True}]


def test_policy_warn_logs_error_and_starts(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(REQ, encoding="utf-8")
    log = _Log()
    problems = runtime_checks.check_dependency_pins(
        requirements=req, policy="warn", logger=log,
        lookup={"sqloader": "0.2.15", "psycopg2-binary": "2.9.12", "uvicorn": "0.34.0"}.get)
    assert len(problems) == 1
    assert log.lines[0][0] == "error" and "sqloader required==0.2.17 installed=0.2.15" in log.lines[0][1]


def test_policy_fail_refuses_critical_drift(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(REQ, encoding="utf-8")
    with pytest.raises(runtime_checks.DependencyPinError):
        runtime_checks.check_dependency_pins(
            requirements=req, policy="fail", logger=_Log(),
            lookup={"sqloader": "0.2.15", "psycopg2-binary": "2.9.12"}.get)
    # a missing critical package is drift too
    with pytest.raises(runtime_checks.DependencyPinError):
        runtime_checks.check_dependency_pins(
            requirements=req, policy="fail", logger=_Log(), lookup={"sqloader": "0.2.17"}.get)
    # non-critical drift never refuses
    assert runtime_checks.check_dependency_pins(
        requirements=req, policy="fail", logger=_Log(),
        lookup={"sqloader": "0.2.17", "psycopg2-binary": "2.9.12", "uvicorn": "0.33.0"}.get)


def test_policy_off_and_unknown_values(monkeypatch):
    assert runtime_checks.check_dependency_pins(policy="off", logger=_Log()) == []
    assert runtime_checks.resolve_policy("bogus") == "warn"
    monkeypatch.setenv(runtime_checks.POLICY_ENV, "FAIL")
    assert runtime_checks.resolve_policy() == "fail"


def test_pool_built_by_0215_is_flagged():
    # What sqloader 0.2.15 builds whatever DB_POOL_MIN/MAX say (NR0003 B §0-8).
    state = {"impl": "SimpleConnectionPool", "minconn": 1, "maxconn": 20, "max_parallel_queries": 20}
    problems = runtime_checks.pool_problems(state, pool_min=20, pool_max=24, max_parallel_queries=20)
    assert any("minconn=1 but DB_POOL_MIN=20" in p for p in problems)
    assert any("maxconn=20 but DB_POOL_MAX=24" in p for p in problems)
    assert any("open and close a connection" in p for p in problems)


def test_pool_matching_config_is_clean():
    state = {"impl": "ThreadedConnectionPool", "minconn": 20, "maxconn": 24, "max_parallel_queries": 20}
    assert runtime_checks.pool_problems(state, pool_min=20, pool_max=24, max_parallel_queries=20) == []
    assert runtime_checks.pool_problems(None, pool_min=20, pool_max=24, max_parallel_queries=20) == []


@pytest.mark.parametrize("processes,pool_max,ok", [(3, 24, True), (4, 24, False), (2, 24, True)])
def test_connection_budget(processes, pool_max, ok):
    # Measured on the shared server (read-only, 2026-11-06): max_connections 100,
    # superuser_reserved_connections 3.
    budget = runtime_checks.connection_budget(max_connections=100, superuser_reserved=3,
                                              reserved_other=20, processes=processes,
                                              pool_max=pool_max)
    assert budget["budget"] == 77 and budget["ok"] is ok


def test_check_database_pool_reports_over_budget_without_raising():
    class _Db:
        pool = SimpleNamespace(minconn=20, maxconn=24)
        max_parallel_queries = 20

        def fetch_one(self, query):
            return {"setting": {"SHOW max_connections": "60",
                                "SHOW superuser_reserved_connections": "3"}[query]}

    settings = SimpleNamespace(DB_POOL_MIN=20, DB_POOL_MAX=24, DB_MAX_PARALLEL_QUERIES=20,
                               DB_RESERVED_CONNECTIONS=20, DB_SERVER_PROCESSES=3)
    log = _Log()
    report = runtime_checks.check_database_pool(settings, _Db(), logger=log)
    assert report["pool_problems"] == []
    assert report["budget"] == {**report["budget"], "budget": 37, "demand": 72, "ok": False}
    assert any(level == "error" and "OVER BUDGET" in msg for level, msg in log.lines)


def test_check_database_pool_is_silent_for_sqlite():
    log = _Log()
    assert runtime_checks.check_database_pool(SimpleNamespace(), object(), logger=log) == {"pool": None}
    assert log.lines == []


def test_shipped_env_sample_is_churn_free_and_in_budget():
    # Settings() defaults are pinned by test_pool_config_and_auth_load_0288 (importing
    # config here would leak its env mirroring into the session); the sample operators
    # copy must carry the same churn-free, in-budget values.
    sample = {}
    for line in (_SERVER_DIR / ".env.sample").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            sample[key.strip()] = value.strip()
    pool_min, parallel = int(sample["DB_POOL_MIN"]), int(sample["DB_MAX_PARALLEL_QUERIES"])
    assert pool_min == parallel
    assert runtime_checks.connection_budget(
        max_connections=100, superuser_reserved=3,
        reserved_other=int(sample["DB_RESERVED_CONNECTIONS"]),
        processes=int(sample["DB_SERVER_PROCESSES"]), pool_max=int(sample["DB_POOL_MAX"]))["ok"]


# ── the installed sqloader, measured (T0004 §4-1/§5-B8) ──────────────────────

def _installed_pool_capable() -> bool:
    version = runtime_checks.installed_version("sqloader")
    if version is None:
        return False
    try:
        return tuple(int(x) for x in version.split(".")[:3]) >= (0, 2, 16)
    except ValueError:
        return False


@pytest.mark.skipif(not _installed_pool_capable(), reason="installed sqloader predates pool_min support")
def test_installed_sqloader_honours_pool_settings_and_stops_churn(monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")
    import psycopg2.extensions
    import psycopg2.pool

    counts = {"opened": 0, "closed": 0}
    lock = threading.Lock()
    idle = psycopg2.extensions.TRANSACTION_STATUS_IDLE

    class _Cursor:
        description = [("x",)]
        rowcount = 1

        def execute(self, *a, **k):
            time.sleep(0.005)   # long enough for a burst's queries to overlap

        def fetchone(self):
            return {"x": 1}

        def fetchall(self):
            return [{"x": 1}]

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        info = SimpleNamespace(transaction_status=idle)

        def __init__(self, *a, **k):
            self.closed = 0
            self.autocommit = False
            with lock:
                counts["opened"] += 1

        def cursor(self, *a, **k):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def get_transaction_status(self):
            return idle

        def close(self):
            if not self.closed:
                self.closed = 1
                with lock:
                    counts["closed"] += 1

    monkeypatch.setattr(psycopg2, "connect", _Conn)
    from sqloader.postgresql import PostgreSQLWrapper

    def burst(db, k, rounds=20):
        for _ in range(rounds):
            gate = threading.Barrier(k)

            def one():
                gate.wait()
                db.fetch_one("SELECT 1")

            threads = [threading.Thread(target=one) for _ in range(k)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

    churny = PostgreSQLWrapper("h", "u", "p", "d", max_parallel_queries=20, pool_min=5, pool_max=24)
    assert (churny.pool.minconn, churny.pool.maxconn) == (5, 24)
    start = dict(counts)
    burst(churny, 20)
    assert counts["closed"] - start["closed"] > 0          # pool_min < parallel still churns

    steady = PostgreSQLWrapper("h", "u", "p", "d", max_parallel_queries=20, pool_min=20, pool_max=24)
    assert runtime_checks.pool_problems(runtime_checks.pool_state(steady), pool_min=20,
                                        pool_max=24, max_parallel_queries=20) == []
    start = dict(counts)
    for k in (1, 5, 10, 20):
        burst(steady, k)
    assert counts["opened"] - start["opened"] == 0
    assert counts["closed"] - start["closed"] == 0
