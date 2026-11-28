"""Conservative marker scan filtering for F110."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_supervisor as supervisor


@pytest.fixture
def proc_table(monkeypatch):
    data = {"ticks": 1000, "boot": "btime 1000\n", "hz": 100, "uid": 42,
            "environ": PermissionError(), "stat_error": None}
    monkeypatch.setattr(proof.executor, "_proc_usable", lambda: True)
    monkeypatch.setattr(proof.os, "getpid", lambda: 999)
    monkeypatch.setattr(proof.os, "getuid", lambda: 42, raising=False)
    monkeypatch.setattr(proof.os, "listdir", lambda path: ["123", "999", "self"])
    monkeypatch.setattr(proof.os, "stat", lambda path: SimpleNamespace(st_uid=data["uid"]))
    monkeypatch.setattr(proof.os, "sysconf", lambda name: data["hz"], raising=False)

    def read_bytes(path):
        value = data["environ"]
        if isinstance(value, Exception):
            raise value
        return value

    def read_text(path):
        if str(path) == "/proc/stat":
            return data["boot"]
        if data["stat_error"]:
            raise data["stat_error"]
        return "123 (worker ) name) " + " ".join(["S"] + ["0"] * 18 + [str(data["ticks"])])

    monkeypatch.setattr(proof.Path, "read_bytes", read_bytes)
    monkeypatch.setattr(proof.Path, "read_text", read_text)
    return data


@pytest.mark.skipif(proof.os.name == "nt", reason="Linux marker scan")
@pytest.mark.parametrize("start,state", [
    (1013, proof.ABSENT), (1012, proof.UNKNOWN), (1011, proof.UNKNOWN),
    (1009, proof.UNKNOWN), (None, proof.UNKNOWN), ("invalid", proof.UNKNOWN),
    ("1970-01-01T00:16:53+00:00", proof.ABSENT),
    ("1970-01-01T09:16:53+09:00", proof.ABSENT),
    ("1970-01-01T00:16:53", proof.UNKNOWN),
    (float("nan"), proof.UNKNOWN), (float("inf"), proof.UNKNOWN),
])
def test_unreadable_process_age(proc_table, start, state):
    assert proof.marker_scan("run", start) == (state, ())


@pytest.mark.skipif(proof.os.name == "nt", reason="Linux marker scan")
@pytest.mark.parametrize("change", [
    {"stat_error": PermissionError()}, {"stat_error": FileNotFoundError()},
    {"ticks": "bad"}, {"ticks": -1}, {"boot": "cpu 0 0\n"},
    {"boot": "btime invalid\n"}, {"hz": 0},
])
def test_unknown_process_start_remains_unknown(proc_table, change):
    proc_table.update(change)
    assert proof.marker_scan("run", 1013) == (proof.UNKNOWN, ())


@pytest.mark.skipif(proof.os.name == "nt", reason="Linux marker scan")
@pytest.mark.parametrize("environ,state,pids", [
    (b"FG_SELFCHECK_RUN_ID=run\0", proof.MATCH, (123,)),
    (b"FG_SELFCHECK_RUN_ID=other\0", proof.ABSENT, ()),
    (FileNotFoundError(), proof.ABSENT, ()),
    (OSError("unreadable"), proof.UNKNOWN, ()),
])
def test_existing_scan_results(proc_table, environ, state, pids):
    proc_table["environ"] = environ
    assert proof.marker_scan("run", 1013) == (state, pids)


@pytest.mark.skipif(proof.os.name == "nt", reason="Linux marker scan")
def test_other_uid_still_skipped(proc_table):
    proc_table["uid"] = 43
    assert proof.marker_scan("run") == (proof.ABSENT, ())


@pytest.mark.skipif(proof.os.name == "nt", reason="POSIX callers")
def test_all_callers_forward_start_time(monkeypatch):
    seen = []
    def scan(run_id, started_at=None):
        seen.append((run_id, started_at))
        return proof.ABSENT, ()
    monkeypatch.setattr(proof, "marker_scan", scan)
    row = {"self_check_run_id": "run", "started_at": "2026-11-28T10:00:00+00:00",
           "owner_marker": proof.owner_marker("run")}
    assert proof.tree_state(row).state == proof.ABSENT
    proof._terminate_marked(row)
    monkeypatch.setattr(proof, "posix_group_members", lambda *a: (proof.ABSENT, (), True))
    assert supervisor._widened(SimpleNamespace(pid=123), None, frozenset(), "run", 1013) is False
    assert seen == [("run", row["started_at"]), ("run", row["started_at"]), ("run", 1013)]
