"""flowgate.v02.0113 T#1 — DB-1 migrations 163~165: shape in three dialects, constraints, rollback, statement lists.

Runs on SQLite only (raw sqlite3 over every migration in name order). PostgreSQL / MySQL files are checked as text
against the SQLite file (same tables, columns, indexes, CHECK count, rollback blocks); their execution, the
concurrent saves of DB-1 §5.4 T-4~T-7 and the rollback run R-6 need live servers and are not run here.

- §3 / §3.3: the same objects in three dialects; SQLite alone carries the cleanup triggers; no backfill, no
  ``ai_providers`` reference, no foreign key at all on ``ai_role_invocations``; hash columns lower-case hex 64.
- §3.5: each file's ``-- ── DOWN`` block is the dialect's rollback (SQLite drops its triggers before the tables,
  MySQL names the table on ``DROP INDEX`` and has no ``IF EXISTS`` there) and ``RE-APPLY PREP`` drops the added
  columns.
- §5.4 C-1~C-18 (and the CK-A / CK-S / CK-I / CK-G basics): what each NULL-safe CHECK refuses and keeps.
- §5.4 R-1~R-5: rollback with data, triggers gone, parent deletes with foreign_keys ON and OFF, the revision-0
  defect reproduced, re-apply after the column drops.
- §5 I-4 / I-6 / I-9 / I-17 / I-19: the only slot DELETEs, the only init / assignment / record UPDATEs, no record
  DELETE anywhere, no write into ``graph_node_settings`` from the role package.
"""
from __future__ import annotations

import ast
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[2]
MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
DIALECTS = ("sqlite", "postgres", "mysql")
M1, M2, M3 = "163_ai_role_defaults_card_slots.sql", "164_graph_stage_assignments.sql", "165_ai_role_invocations.sql"
FILES = (M1, M2, M3)
NEW_TABLES = {M1: ["user_ai_role_defaults", "graph_node_role_slots", "graph_node_role_init"],
              M2: ["graph_stage_assignments"], M3: ["ai_role_invocations"]}
NEW_INDEXES = {M1: set(), M2: {"idx_gsa_run_node", "idx_gsa_intent", "idx_graph_exec_assignment"},
               M3: {"ux_ari_inv", "ux_ari_run_launch", "idx_ari_unit", "idx_ari_exec", "idx_ari_target_node",
                    "idx_air_graph_exec"}}
ADDED_COLUMNS = {M1: set(), M2: {("graph_attempt_runs", "assignment_stage"), ("graph_attempt_runs", "assignment_seq")},
                 M3: {("ai_invoke_runs", "invocation_origin_kind"), ("ai_invoke_runs", "graph_exec_id")}}
TRIGGERS = {M1: {"trg_graph_node_role_slots_group_deleted", "trg_graph_node_role_init_group_deleted"},
            M2: {"trg_graph_stage_assignments_attempt_deleted"}, M3: set()}
T = "2026-10-30T00:00:00.000+00:00"
HEX = "0123456789abcdef" * 4


def _sql(dialect: str, name: str) -> str:
    return (MIGRATIONS / dialect / name).read_text(encoding="utf-8")


def _executable(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _block(sql: str, title: str) -> list[str]:
    """The statements written as ``-- <SQL>`` lines between ``-- ── <title>`` and ``-- ── END <title>``."""
    lines = sql.splitlines()
    start = [i for i, line in enumerate(lines) if line.startswith(f"-- ── {title} ")]
    if not start:
        return []
    out = []
    for line in lines[start[0] + 1:]:
        if line.startswith("-- ── END"):
            break
        body = line[3:] if line.startswith("-- ") else ""
        if re.match(r"(DROP|ALTER)\s", body):
            out.append(body)
    return out


def _down(dialect: str, name: str) -> list[str]:
    return _block(_sql(dialect, name), "DOWN")


def _reapply_prep(dialect: str, name: str) -> list[str]:
    return _block(_sql(dialect, name), "RE-APPLY PREP")


def _index_names(sql: str) -> set[str]:
    body = _executable(sql)
    names = set(re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", body))
    names |= set(re.findall(r"^\s*(?:UNIQUE\s+KEY|INDEX)\s+(\w+)\s*\(", body, flags=re.MULTILINE))
    return names


def _columns(sql: str, table: str) -> list[str]:
    body = _executable(sql)
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", body, flags=re.DOTALL)
    assert match, table
    names = []
    for line in match.group(1).splitlines():
        found = re.match(r"^    (\w+)\s+(?:TEXT|VARCHAR|CHAR|INTEGER|BIGINT|BIGSERIAL|TINYINT)", line)
        if found:
            names.append(found.group(1))
    return names


# ── §3 / §3.3: the same objects in three dialects ────────────────────────────

@pytest.mark.parametrize("name", FILES)
def test_the_three_dialects_create_the_same_tables_columns_indexes_and_checks(name):
    reference = _sql("sqlite", name)
    for dialect in DIALECTS:
        sql = _sql(dialect, name)
        body = _executable(sql)
        assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", body) == NEW_TABLES[name], dialect
        for table in NEW_TABLES[name]:
            assert _columns(sql, table) == _columns(reference, table), (dialect, table)
        assert _index_names(sql) == NEW_INDEXES[name], dialect
        added = set(re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", body))
        assert added == ADDED_COLUMNS[name], dialect
        assert body.count("CHECK (") == _executable(reference).count("CHECK ("), dialect
        assert set(re.findall(r"CREATE TRIGGER IF NOT EXISTS (\w+)", body)) == (
            TRIGGERS[name] if dialect == "sqlite" else set()), dialect
        assert ("BEGIN;" in body.split("CREATE TRIGGER")[0]) == (dialect == "sqlite"), dialect


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("name", FILES)
def test_additive_only_no_backfill_and_no_ai_provider_reference(dialect, name):
    body = _executable(_sql(dialect, name))
    without_triggers = "\n".join(line for line in body.splitlines() if not line.startswith("CREATE TRIGGER"))
    without_triggers = without_triggers.replace("ON DELETE CASCADE", "")
    assert not re.search(r"\b(UPDATE|DELETE|INSERT|DROP|RENAME)\b", without_triggers), "no backfill (§3.4)"
    assert "ai_providers" not in body, "DB-D4: no AI id column references ai_providers"
    if name == M3:
        assert not re.search(r"\b(REFERENCES|FOREIGN\s+KEY)\b", body), "DB-D7: records never cascade"


def test_mysql_names_its_foreign_keys_and_keeps_fk_columns_out_of_checks():
    fk = {M1: {"fk_uard_user", "fk_gnrs_group", "fk_gnri_group"}, M2: {"fk_gsa_attempt", "fk_gsa_run"}, M3: set()}
    for name in FILES:
        body = _executable(_sql("mysql", name))
        assert set(re.findall(r"CONSTRAINT (\w+) FOREIGN KEY", body)) == fk[name]
        assert "REFERENCES" not in re.sub(r"CONSTRAINT \w+ FOREIGN KEY[^\n]*", "", body)
        for check in re.findall(r"CHECK \((.*?)\)(?:,|\n)", body, flags=re.DOTALL):
            assert not re.search(r"\b(user_id|group_id|attempt_id|run_id)\b", check), check


def test_hash_columns_are_lower_case_hex_64_in_every_dialect():
    sqlite_sql, pg_sql, my_sql = (_executable(_sql(d, M3)) for d in DIALECTS)
    for column in ("provider_fingerprint", "unit_key_hash"):
        assert f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'" in sqlite_sql
        assert f"{column} ~ '^[0-9a-f]{{64}}$'" in pg_sql
        assert f"CHAR_LENGTH({column}) = 64 AND {column} REGEXP '^[0-9a-f]{{64}}$'" in my_sql
        assert re.search(rf"{column}\s+CHAR\(64\) CHARACTER SET ascii COLLATE ascii_bin", my_sql)
    assert "record_seq                      BIGSERIAL PRIMARY KEY" in pg_sql
    assert "record_seq                      BIGINT PRIMARY KEY AUTO_INCREMENT" in my_sql
    assert "create_seq                      BIGINT NOT NULL" in pg_sql and "create_seq                      BIGINT NOT NULL" in my_sql


# ── §3.5: rollback blocks ────────────────────────────────────────────────────

ROLLBACK = {
    "sqlite": {
        M3: ["DROP INDEX IF EXISTS idx_air_graph_exec;", "DROP TABLE IF EXISTS ai_role_invocations;"],
        M2: ["DROP TRIGGER IF EXISTS trg_graph_stage_assignments_attempt_deleted;",
             "DROP INDEX IF EXISTS idx_graph_exec_assignment;", "DROP TABLE IF EXISTS graph_stage_assignments;"],
        M1: ["DROP TRIGGER IF EXISTS trg_graph_node_role_slots_group_deleted;",
             "DROP TRIGGER IF EXISTS trg_graph_node_role_init_group_deleted;",
             "DROP TABLE IF EXISTS graph_node_role_init;", "DROP TABLE IF EXISTS graph_node_role_slots;",
             "DROP TABLE IF EXISTS user_ai_role_defaults;"],
    },
    "postgres": {
        M3: ["DROP INDEX IF EXISTS idx_air_graph_exec;", "DROP TABLE IF EXISTS ai_role_invocations;"],
        M2: ["DROP INDEX IF EXISTS idx_graph_exec_assignment;", "DROP TABLE IF EXISTS graph_stage_assignments;"],
        M1: ["DROP TABLE IF EXISTS graph_node_role_init;", "DROP TABLE IF EXISTS graph_node_role_slots;",
             "DROP TABLE IF EXISTS user_ai_role_defaults;"],
    },
    "mysql": {
        M3: ["DROP INDEX idx_air_graph_exec ON ai_invoke_runs;", "DROP TABLE IF EXISTS ai_role_invocations;"],
        M2: ["DROP INDEX idx_graph_exec_assignment ON graph_attempt_runs;",
             "DROP TABLE IF EXISTS graph_stage_assignments;"],
        M1: ["DROP TABLE IF EXISTS graph_node_role_init;", "DROP TABLE IF EXISTS graph_node_role_slots;",
             "DROP TABLE IF EXISTS user_ai_role_defaults;"],
    },
}
REAPPLY_PREP = {
    M1: [],
    M2: ["ALTER TABLE graph_attempt_runs DROP COLUMN assignment_seq;",
         "ALTER TABLE graph_attempt_runs DROP COLUMN assignment_stage;"],
    M3: ["ALTER TABLE ai_invoke_runs DROP COLUMN graph_exec_id;",
         "ALTER TABLE ai_invoke_runs DROP COLUMN invocation_origin_kind;"],
}


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("name", FILES)
def test_each_file_carries_the_db1_rollback_and_reapply_prep_of_its_dialect(dialect, name):
    assert _down(dialect, name) == ROLLBACK[dialect][name]
    assert _reapply_prep(dialect, name) == REAPPLY_PREP[name]
    if dialect == "mysql":
        assert not any(s.startswith("DROP INDEX IF EXISTS") for s in _down(dialect, name))


# ── SQLite database over every migration ─────────────────────────────────────

_TEMPLATES: dict = {}


def _template(upto: str | None) -> str:
    """Every migration applied once per module (in memory, then saved to a file); each test copies the file."""
    if upto not in _TEMPLATES:
        memory = sqlite3.connect(":memory:")
        for mig in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
            if upto is not None and mig.name > upto:
                break
            memory.executescript(mig.read_text(encoding="utf-8"))
        path = Path(tempfile.mkdtemp(prefix="db1_")) / "template.db"
        target = sqlite3.connect(str(path))
        memory.backup(target)
        target.close()
        memory.close()
        _TEMPLATES[upto] = str(path)
    return _TEMPLATES[upto]


def _migrate(path: str, *, upto: str | None = None, foreign_keys: bool = True) -> sqlite3.Connection:
    shutil.copyfile(_template(upto), path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    conn.row_factory = sqlite3.Row
    return conn


def _seed_parents(conn: sqlite3.Connection, group="g1", run="r1", attempt="a1", user="u1") -> None:
    conn.execute("INSERT OR IGNORE INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'x', ?, ?)", [user, user, f"{user}@example.com", T, T])
    conn.execute("INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
                 "VALUES ('p1', 'p1', ?, ?)", [T, T])
    conn.execute("INSERT INTO groups (group_id, project_id, title, created_at, updated_at) VALUES (?, 'p1', ?, ?, ?)",
                 [group, group, T, T])
    conn.execute("INSERT INTO graph_revisions (group_id, revision_no, source_draft_version, confirmed_at) "
                 "VALUES (?, 1, 1, ?)", [group, T])
    conn.execute("INSERT INTO graph_runs (run_id, group_id, revision_no, status, started_at, policy_source, "
                 "policy_set_id, policy_set_version, policy_digest, updated_at) "
                 "VALUES (?, ?, 1, 'running', ?, 'flow', 'ps', '1', ?, ?)", [run, group, T, HEX, T])
    conn.execute("INSERT INTO graph_node_attempts (attempt_id, run_id, node_key, revision_no, status, created_at) "
                 "VALUES (?, ?, 'n1', 1, 'active', ?)", [attempt, run, T])


def _insert(conn, table: str, row: dict) -> None:
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))


def _assignment(**over) -> dict:
    row = {"attempt_id": "a1", "stage": "instruction", "seq": 0, "run_id": "r1", "group_id": "g1",
           "node_key": "n1", "role": "instruction", "actor": "ai", "provider_id": "p", "source": "card_slot",
           "decided_at": T, "stage_state": "active", "updated_at": T}
    row.update(over)
    return row


def _review(**over) -> dict:
    row = _assignment(stage="result_review", seq=1, role="review", review_rounds=1, author="ai",
                      basis_doc_id="d", basis_revision_no=1, basis_rejection_count=0)
    row.update(over)
    return row


_INV_SEQ = iter(range(1, 10_000))


def _invocation(**over) -> dict:
    n = next(_INV_SEQ)
    row = {"inv_id": f"run{n}#1", "create_seq": n, "ai_run_id": f"run{n}", "launch_no": 1, "origin_kind": "non_card",
           "requested_model_state": "value", "requested_model_value": "m", "result": "running",
           "link_kind": "first", "unit_kind": "run", "unit_key_hash": HEX, "created_at": T, "updated_at": T}
    row.update(over)
    return row


def _ended(**over) -> dict:
    row = _invocation(result="succeeded", ended_at=T, confirmed_model_state="unverifiable",
                      unverifiable_reason="no_result")
    row.update(over)
    return row


@pytest.fixture()
def db(tmp_path):
    conn = _migrate(str(tmp_path / "db1.db"))
    _seed_parents(conn)
    yield conn
    conn.close()


def _refused(conn, table, row) -> bool:
    try:
        conn.execute("SAVEPOINT c")
        _insert(conn, table, row)
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK TO SAVEPOINT c")
        conn.execute("RELEASE SAVEPOINT c")
        assert "CHECK constraint failed" in str(exc) or "NOT NULL" in str(exc), exc
        return True
    conn.execute("ROLLBACK TO SAVEPOINT c")
    conn.execute("RELEASE SAVEPOINT c")
    return False


def test_every_migration_applies_and_creates_the_db1_objects(db):
    names = {r["name"] for r in db.execute("SELECT name FROM sqlite_master")}
    for name in FILES:
        assert set(NEW_TABLES[name]) <= names and NEW_INDEXES[name] <= names and TRIGGERS[name] <= names
    for table, column in ADDED_COLUMNS[M2] | ADDED_COLUMNS[M3]:
        assert column in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}


# ── §5.4 C: constraints (plus the CK-A / CK-S / CK-I basics) ─────────────────

@pytest.mark.parametrize("row, refused", [
    ({"instruction_kind": "assigned", "instruction_provider_id": "p"}, False),
    ({"instruction_kind": "assigned", "instruction_provider_id": "  "}, True),             # CK-A1 blank id
    ({"instruction_kind": "not_used", "instruction_provider_name": "n"}, True),            # not_used + name
    ({"instruction_kind": "not_used", "instruction_provider_id": "p"}, True),              # not_used + id
    ({"instruction_kind": "unset"}, True),
])
def test_account_role_checks(db, row, refused):
    base = {"user_id": "u1", "instruction_kind": "not_used", "execution_kind": "not_used", "review_kind": "not_used",
            "setup_completed_at": T, "updated_at": T}
    assert _refused(db, "user_ai_role_defaults", {**base, **row}) is refused


@pytest.mark.parametrize("row, refused", [
    ({"kind": "assigned", "provider_id": "p", "source": "card_explicit", "set_by": "u1"}, False),
    ({"kind": "not_used", "source": "pending_fill"}, False),
    ({"kind": "assigned", "provider_id": "", "source": "card_explicit", "set_by": "u1"}, True),     # CK-S1
    ({"kind": "not_used", "provider_id": "-", "source": "card_explicit", "set_by": "u1"}, True),    # no special id
    ({"kind": "not_used", "source": "pending_fill", "set_by": "u1"}, True),                       # CK-S2
    ({"kind": "not_used", "source": "card_explicit"}, True),                                      # CK-S2
    ({"kind": "not_used", "source": "legacy", "set_by": "u1"}, True),
])
def test_slot_checks(db, row, refused):
    base = {"group_id": "g1", "node_key": "n1", "role": "execution", "set_at": T}
    assert _refused(db, "graph_node_role_slots", {**base, **row}) is refused


@pytest.mark.parametrize("row, refused", [
    ({"state": "pending_init"}, False),
    ({"state": "initialized", "initialized_at": T}, False),
    ({"state": "initialized"}, True),                                                    # CK-I1
    ({"state": "pending_init", "initialized_at": T}, True),
    ({"state": "legacy"}, True),
])
def test_init_checks(db, row, refused):
    base = {"group_id": "g1", "node_key": "n1", "created_at": T}
    assert _refused(db, "graph_node_role_init", {**base, **row}) is refused


@pytest.mark.parametrize("row, refused", [
    # C-1 / C-2 / C-3 (CK-G6)
    (_assignment(role_value_provider_id="p"), True),
    (_assignment(role_value_kind="assigned", role_value_provider_id="  "), True),
    (_assignment(role_value_kind="assigned"), True),
    (_assignment(role_value_kind="not_used", role_value_provider_id="p"), True),
    (_assignment(role_value_kind="assigned", role_value_provider_id="p"), False),
    (_assignment(), False),
    # C-4 / C-5 (CK-G14)
    (_assignment(intent_exec_id="e"), True),
    (_assignment(intent_kind="approval", intent_exec_id="e"), True),
    (_assignment(intent_kind="execution"), True),
    (_assignment(intent_kind="execution", intent_exec_id=" "), True),
    (_assignment(intent_kind="execution", intent_exec_id="e"), False),
    (_assignment(intent_kind="approval"), False),
    # C-6 (CK-G10)
    (_review(author=None, author_provider_id="p"), True),
    (_review(author="person", author_provider_id="p"), True),
    (_review(author="unknown", author_provider_id="p"), True),
    (_review(author="ai", author_provider_id="p"), False),
    # CK-G1 / G2 / G3 / G4 / G5 / G7 / G8 / G9 / G11 / G12 / G13
    (_assignment(seq=1), True),
    (_review(seq=0), True),
    (_assignment(role="execution"), True),
    (_assignment(provider_id=None), True),
    (_assignment(actor="person"), True),
    (_assignment(actor="person", provider_id=None), False),
    (_assignment(actor="wait_unavailable", cause="provider_deleted"), False),
    (_assignment(actor="wait_unavailable", provider_id=None, cause="x"), True),
    (_assignment(actor="wait_unavailable"), True),
    (_review(actor="person", provider_id=None, review_rounds=None), True),
    (_assignment(actor="no_review", provider_id=None), True),
    (_review(actor="no_review", provider_id=None, review_rounds=None), False),
    (_assignment(plan_instruction_type="T"), True),
    (_assignment(plan_result_type="TR"), False),
    (_review(plan_result_type="TR"), True),
    (_review(reused_doc_id="d"), True),
    (_review(review_rounds=None), True),
    (_review(review_rounds=0), True),
    (_review(review_rounds=-1), False),
    (_assignment(review_rounds=1), True),
    (_assignment(rework_provider_id="p"), True),
    (_review(basis_revision_no=None), True),
    (_review(basis_rejection_count=-1), True),
    (_assignment(stage_state="failed"), True),
    (_assignment(stage_state="failed", fail_cause="start_refused:x"), False),
    (_assignment(fail_cause="x"), True),
    (_assignment(verdict="passed"), True),
    (_review(verdict="passed"), False),
])
def test_stage_assignment_checks(db, row, refused):
    assert _refused(db, "graph_stage_assignments", row) is refused


@pytest.mark.parametrize("row, refused", [
    # C-7 / C-8 (CK-V7: a finished record must carry its confirmed-model state)
    (_invocation(result="succeeded", ended_at=T, unverifiable_reason="no_result"), True),
    (_invocation(result="succeeded", ended_at=T), True),
    # C-9: running carries nothing of the end
    (_invocation(unverifiable_reason="x"), True),
    (_invocation(confirmed_model_values_json="[]"), True),
    (_invocation(calls_total=1), True),
    (_invocation(confirmed_model_state="confirmed"), True),
    # C-10: confirmed
    (_ended(confirmed_model_state="confirmed", confirmed_model_values_json='["m"]', confirmed_coverage="full"), True),
    (_ended(confirmed_model_state="confirmed", confirmed_model_values_json='["m"]', unverifiable_reason=None), True),
    (_ended(confirmed_model_state="confirmed", confirmed_model_values_json='["m"]', confirmed_coverage="full",
            unverifiable_reason=None, confirmed_model_truncated=1), True),
    # C-11: unverifiable
    (_ended(confirmed_model_values_json='["m"]'), True),
    (_ended(confirmed_coverage="partial"), True),
    (_ended(unverifiable_reason=None), True),
    (_ended(unverifiable_reason="  "), True),
    (_ended(confirmed_model_truncated=1), True),
    # C-12: what is kept
    (_invocation(), False),
    (_ended(confirmed_model_state="confirmed", confirmed_model_values_json='["m"]', confirmed_coverage="partial",
            unverifiable_reason=None, confirmed_model_truncated=1), False),
    (_ended(calls_total=2, calls_reported=1, calls_absent=1, bad_lines=0), False),
    # C-13 (CK-V4)
    (_ended(origin_kind="card_qa", inv_role="execution", purpose="qa", target_group_id="g1", qa_item_id="q",
            qa_card_class="qa_new", unit_kind="qa"), True),
    (_ended(origin_kind="card_legacy_attempt", inv_role="qa_response", purpose="qa", target_group_id="g1"), True),
    (_ended(origin_kind="card_qa", inv_role="qa_response", purpose="qa", target_group_id="g1", qa_item_id="q",
            qa_card_class="qa_new", unit_kind="qa"), False),
    # C-14 (CK-V5 / CK-V1)
    (_ended(ai_run_id=None, launch_no=None, result="admission_refused", requested_model_state="not_parsed",
            requested_model_value=None, requested_model_reason="not_launched", confirmed_model_state=None,
            unverifiable_reason=None), True),
    (_ended(ai_run_id=None, launch_no=None, result="admission_refused", requested_model_state="none",
            requested_model_value=None, requested_model_reason="no_model"), True),
    (_ended(ai_run_id=None, launch_no=None, result="admission_refused", requested_model_state="not_parsed",
            requested_model_value=None, requested_model_reason="not_launched"), False),
    (_ended(result="admission_refused", requested_model_state="not_parsed", requested_model_value=None,
            requested_model_reason="not_launched"), True),
    (_ended(ai_run_id=None, launch_no=None), True),
    # C-15 / C-16 (CK-V10)
    (_invocation(provider_fingerprint="Z" * 64), True),
    (_invocation(provider_fingerprint=HEX.upper()), True),
    (_invocation(provider_fingerprint=HEX[:63]), True),
    (_invocation(provider_fingerprint=HEX), False),
    # C-17 (CK-V11)
    (_invocation(unit_key_hash="Z" * 64), True),
    (_invocation(unit_key_hash=HEX + "a"), True),
    # C-18
    (_invocation(create_seq=0), True),
    # CK-V2 / V3 / V6 / V8 / V9
    (_invocation(origin_kind="card_assigned", inv_role="execution", purpose="write", target_group_id="g1"), True),
    (_invocation(origin_kind="card_assigned", inv_role="execution", purpose="write", target_group_id="g1",
                 target_attempt_id="a1", target_stage="result", target_seq=0, unit_kind="assignment"), False),
    (_invocation(inv_role="execution"), True),
    (_invocation(requested_model_reason="x"), True),
    (_invocation(requested_model_state="none", requested_model_value=None), True),
    (_invocation(ended_at=T), True),
    (_ended(ended_at=None), True),
    (_ended(model_call_failure_kind="http_error", model_call_no=2), True),
    (_ended(model_http_status=500), True),
    (_ended(model_call_failure_kind="http_error", model_call_no=2, model_turn_no=1, model_http_status=500), False),
])
def test_role_invocation_checks(db, row, refused):
    assert _refused(db, "ai_role_invocations", row) is refused


def test_two_launch_records_of_one_run_and_launch_number_clash(db):
    _insert(db, "ai_role_invocations", _invocation(inv_id="x#1", ai_run_id="x", launch_no=1))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(db, "ai_role_invocations", _invocation(inv_id="x#1b", ai_run_id="x", launch_no=1))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(db, "ai_role_invocations", _invocation(inv_id="x#1", ai_run_id="y", launch_no=1))


# ── deletes before any rollback (DB-1 §5.3-4, I-12, I-17) ────────────────────

def _fill(conn) -> None:
    _insert(conn, "user_ai_role_defaults", {"user_id": "u1", "instruction_kind": "assigned",
            "instruction_provider_id": "gone", "instruction_provider_name": "Gone AI", "execution_kind": "not_used",
            "review_kind": "not_used", "setup_completed_at": T, "updated_at": T})
    _insert(conn, "graph_node_role_slots", {"group_id": "g1", "node_key": "n1", "role": "execution",
            "kind": "assigned", "provider_id": "gone", "source": "card_explicit", "set_by": "u1", "set_at": T})
    _insert(conn, "graph_node_role_init", {"group_id": "g1", "node_key": "n1", "state": "pending_init",
                                           "created_at": T})
    _insert(conn, "graph_stage_assignments", _assignment())
    _insert(conn, "ai_role_invocations", _invocation(inv_id="keep#1", ai_run_id="keep", target_group_id=None))


def _count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("foreign_keys", [True, False])
def test_parent_deletes_take_slots_init_and_assignments_but_never_records(tmp_path, foreign_keys):
    conn = _migrate(str(tmp_path / "d.db"), foreign_keys=foreign_keys)
    _seed_parents(conn)
    _fill(conn)
    conn.execute("UPDATE graph_node_attempts SET status = 'invalidated' WHERE attempt_id = 'a1'")
    assert _count(conn, "graph_stage_assignments") == 1                      # I-12: invalidation keeps it
    conn.execute("DELETE FROM graph_node_attempts WHERE attempt_id = 'a1'")
    assert _count(conn, "graph_stage_assignments") == 0
    conn.execute("DELETE FROM groups WHERE group_id = 'g1'")
    assert _count(conn, "graph_node_role_slots") == 0 and _count(conn, "graph_node_role_init") == 0
    assert _count(conn, "ai_role_invocations") == 1                          # I-17
    if foreign_keys:
        conn.execute("DELETE FROM users WHERE user_id = 'u1'")
        assert _count(conn, "user_ai_role_defaults") == 0
    conn.close()


# ── §5.4 R: rollback (SQLite) ────────────────────────────────────────────────

def _rollback(conn, *, keep_triggers: bool = False) -> None:
    for name in (M3, M2, M1):
        for statement in _down("sqlite", name):
            if keep_triggers and statement.startswith("DROP TRIGGER"):
                continue
            conn.execute(statement)


def _db1_triggers(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND (name LIKE 'trg_graph_node_role%' "
        "OR name LIKE 'trg_graph_stage_assignments%')")]


@pytest.mark.parametrize("foreign_keys", [True, False])
def test_r1_r2_r3_rollback_with_data_leaves_no_trigger_and_parent_deletes_work(tmp_path, foreign_keys):
    conn = _migrate(str(tmp_path / "r.db"), foreign_keys=foreign_keys)
    _seed_parents(conn)
    _fill(conn)
    conn.commit()
    _rollback(conn)
    conn.commit()
    assert _db1_triggers(conn) == []                                                         # R-1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"user_ai_role_defaults", "graph_node_role_slots", "graph_node_role_init",
                         "graph_stage_assignments", "ai_role_invocations"}
    conn.execute("DELETE FROM graph_node_attempts WHERE attempt_id = 'a1'")                  # R-2 / R-3
    conn.execute("DELETE FROM graph_runs WHERE run_id = 'r1'")
    conn.execute("DELETE FROM groups WHERE group_id = 'g1'")
    conn.commit()
    conn.close()


def test_r4_the_revision_0_rollback_without_trigger_drops_breaks_group_delete(tmp_path):
    conn = _migrate(str(tmp_path / "r4.db"))
    _seed_parents(conn)
    _rollback(conn, keep_triggers=True)
    assert len(_db1_triggers(conn)) == 3
    with pytest.raises(sqlite3.OperationalError,
                       match=r"no such table: main\.(graph_node_role_(init|slots)|graph_stage_assignments)"):
        conn.execute("DELETE FROM groups WHERE group_id = 'g1'")
    with pytest.raises(sqlite3.OperationalError, match=r"no such table: main\.graph_stage_assignments"):
        conn.execute("DELETE FROM graph_node_attempts WHERE attempt_id = 'a1'")
    conn.close()


def test_r5_rollback_then_reapply_prep_then_the_three_files_apply_again(tmp_path):
    conn = _migrate(str(tmp_path / "r5.db"))
    _seed_parents(conn)
    _fill(conn)
    conn.commit()
    _rollback(conn)
    for name in (M3, M2, M1):
        for statement in _reapply_prep("sqlite", name):
            conn.execute(statement)
    conn.commit()
    assert "assignment_stage" not in {r["name"] for r in conn.execute("PRAGMA table_info(graph_attempt_runs)")}
    for name in FILES:
        conn.executescript(_sql("sqlite", name))
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    for name in FILES:
        assert set(NEW_TABLES[name]) <= names and NEW_INDEXES[name] <= names and TRIGGERS[name] <= names
    _fill(conn)
    conn.execute("DELETE FROM groups WHERE group_id = 'g1'")
    conn.commit()
    conn.close()


def test_without_reapply_prep_a_second_run_fails_on_the_added_columns(tmp_path):
    conn = _migrate(str(tmp_path / "r5b.db"))
    _rollback(conn)
    with pytest.raises(sqlite3.OperationalError, match="duplicate column name"):
        conn.executescript(_sql("sqlite", M2))
    conn.close()


def test_a_db_before_163_has_none_of_the_objects(tmp_path):
    conn = _migrate(str(tmp_path / "old.db"), upto="162_zzz")
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert not names & {"user_ai_role_defaults", "graph_stage_assignments", "ai_role_invocations"}
    conn.close()


# ── §5 I-4 / I-6 / I-9 / I-17 / I-19: the statements that may touch the tables ──

def _server_sql_literals() -> list[tuple[str, str]]:
    """Every string literal of the server's Python code (tests excluded), whitespace-normalized."""
    found = []
    tables = ("graph_node_role_slots", "graph_node_role_init", "graph_stage_assignments", "ai_role_invocations",
              "user_ai_role_defaults")
    for path in _SERVER_DIR.rglob("*.py"):
        rel = path.relative_to(_SERVER_DIR).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel or ".venv" in rel or "site-packages" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not any(t in text for t in tables):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and any(t in node.value for t in tables):
                found.append((rel, " ".join(node.value.split())))
    return found


def _statements(verb: str, table: str) -> set[str]:
    pattern = re.compile(rf"\b{verb}\s+(FROM\s+|INTO\s+)?{table}\b")
    return {sql for _rel, sql in _server_sql_literals() if pattern.search(sql)}


def test_i4_the_only_slot_deletes_are_the_replace_and_the_wp_plan_removal():
    assert _statements("DELETE", "graph_node_role_slots") == {
        "DELETE FROM graph_node_role_slots WHERE group_id = ? AND node_key = ? AND role = ?",
        "DELETE FROM graph_node_role_slots WHERE group_id = ? AND node_key = ? AND role = ? AND source = 'wp_plan'",
    }
    assert _statements("UPDATE", "graph_node_role_slots") == set()


def test_i6_init_rows_only_move_pending_to_initialized_and_are_never_deleted():
    assert _statements("UPDATE", "graph_node_role_init") == {
        "UPDATE graph_node_role_init SET state = 'initialized', initialized_at = ? "
        "WHERE group_id = ? AND node_key = ? AND state = 'pending_init'",
    }
    assert _statements("DELETE", "graph_node_role_init") == set()


def test_i9_the_only_assignment_updates_set_the_mutable_columns_and_nothing_deletes_one():
    updates = _statements("UPDATE", "graph_stage_assignments")
    assert updates == {
        "UPDATE graph_stage_assignments SET stage_state = ?, fail_cause = ?, verdict = ?, updated_at = ? "
        "WHERE attempt_id = ? AND stage = ? AND seq = ? AND stage_state = ?",
        "UPDATE graph_stage_assignments SET intent_kind = ?, intent_exec_id = ?, updated_at = ? "
        "WHERE attempt_id = ? AND stage = ? AND seq = ? AND intent_kind IS NULL",
    }
    mutable = {"stage_state", "fail_cause", "verdict", "intent_kind", "intent_exec_id", "updated_at"}
    for sql in updates:
        assigned = set(re.findall(r"(\w+) = \?", sql.split(" WHERE ")[0]))
        assert assigned <= mutable, sql
    assert _statements("DELETE", "graph_stage_assignments") == set()


def test_i14_i17_records_are_never_deleted_and_updates_never_touch_their_identity():
    assert _statements("DELETE", "ai_role_invocations") == set()
    assert _statements("DELETE", "user_ai_role_defaults") == set()
    for sql in _statements("UPDATE", "ai_role_invocations"):
        assigned = set(re.findall(r"(\w+) = \?", sql.split(" WHERE ")[0]))
        assert not assigned & {"inv_id", "create_seq", "unit_key_hash", "ai_run_id", "launch_no", "origin_kind",
                               "purpose", "created_at", "prev_inv_id", "link_kind"}, sql
        where = sql.split(" WHERE ")[1]
        assert ("result = 'running'" in where or "IS NULL" in where), sql


def test_i17_the_ai_invoke_runs_retention_sweep_does_not_reach_the_records():
    source = (_SERVER_DIR / "modules" / "flow_gate" / "db" / "ai_invoke_runs.py").read_text(encoding="utf-8")
    assert "ai_role_invocations" not in source


def test_i19_the_role_package_never_touches_graph_node_settings():
    statement = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+graph_node_settings\b")
    for path in (_SERVER_DIR / "flowgate" / "ai_roles").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not statement.search(node.value), (path.name, node.value)
