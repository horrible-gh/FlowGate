"""PostgreSQL document_types sequence realign (046a) + CH/WP re-seed (189) -- flowgate.v02gui.0017.

NR 0003 A: 023/025 rebuild document_types with explicit ids, which leaves the PostgreSQL SERIAL
sequence at 1. On a fresh database 047 (CH) and 078b (WP) then collide silently (ON CONFLICT DO
NOTHING drops the rows) and 113 (T2) aborts with duplicate key (id)=(3).

The fix keeps the historical 023/025/113 untouched and adds two PostgreSQL-only, hand-authored
files. These offline tests pin their shape and their position; ``--verify`` cannot, because the
generator never iterates a file that has no SQLite source. The live class (opt-in, needs
FLOWGATE_PG_TEST_DSN like test_pg_fk_rebuild_rename_idiom_0542.py) replays the real migration
set inside a throwaway schema.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[3]
_TOOLS_DIR = _SERVER_DIR / "tools"
for _p in (_SERVER_DIR, _TOOLS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import regen_dialect_migrations as R  # noqa: E402

MIG = _SERVER_DIR / "sql" / "migrations"
PG_DIR = MIG / "postgres"
REALIGN = "046a_pg_document_types_sequence_realign.sql"
REPAIR = "189_repair_pg_conversation_work_plan_doctypes.sql"


def _pg_names() -> list[str]:
    # sqloader 0.2.17 DatabaseMigrator.get_migration_files(): sorted(glob(<dir>/*.sql)).
    return sorted(p.name for p in PG_DIR.glob("*.sql"))


def _read(name: str) -> str:
    return (PG_DIR / name).read_text(encoding="utf-8")


def _code(sql: str) -> str:
    """SQL without -- comments, so assertions look at statements, not prose."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


# ── offline ────────────────────────────────────────────────────────────────────


def test_realign_runs_before_the_first_id_less_document_types_seed():
    names = _pg_names()
    assert REALIGN in names
    pos = names.index(REALIGN)
    assert names[pos - 1].startswith("046_") and names[pos + 1].startswith("047_")
    assert names.index("025_document_type_names.sql") < pos
    assert pos < names.index("047_seed_conversation_doctype.sql")
    assert pos < names.index("078b_seed_work_plan_doctype.sql")
    assert pos < names.index("113_seed_t2_tr2_doctypes.sql")


def test_realign_looks_up_the_sequence_and_only_moves_it_forward():
    code = _code(_read(REALIGN))
    assert "pg_get_serial_sequence('document_types', 'id')" in code
    assert "document_types_new_id_seq" not in code  # never hardcode the rebuilt sequence name
    assert re.search(r"GREATEST\(\s*next_now\s*,\s*max_id\s*\+\s*1\s*\)", code)
    assert "IF max_id IS NULL THEN" in code  # empty table: untouched
    assert "IF next_target > next_now THEN" in code  # never rewinds
    assert re.search(r"setval\(\s*seq_name\s*,\s*next_target\s*,\s*false\s*\)", code)
    assert "to_regclass('document_types')" in code
    # data is never touched
    assert not re.search(r"(?i)\b(INSERT|UPDATE|DELETE)\s", code)


def test_repair_reseeds_ch_and_wp_only_where_missing():
    sql = _read(REPAIR)
    code = _code(sql)
    # the realign is repeated first, verbatim, so the inserts cannot collide
    assert code.index("pg_get_serial_sequence") < code.index("INSERT INTO document_types")
    assert "$fg_seq_realign$" in code
    for type_code in ("CH", "WP"):
        assert re.search(
            r"WHERE NOT EXISTS \(SELECT 1 FROM document_types WHERE project_id IS NULL "
            rf"AND type_code = '{type_code}'\)",
            code,
        )
    inserts = [s for s in code.split(";") if "INSERT INTO" in s]
    assert len(inserts) == 2 + 2 * 6  # 2 rows, then 3 names + 3 descriptions each
    for stmt in inserts:
        if "INSERT INTO document_types " in stmt:
            assert "WHERE NOT EXISTS" in stmt
        else:
            assert "ON CONFLICT DO NOTHING" in stmt
    assert not re.search(r"(?i)\b(UPDATE|DELETE)\s", code)


def test_repair_values_match_the_original_seeds():
    """189 must restore exactly what 047 / 078b / 074c would have written."""
    repair = _read(REPAIR)
    originals = {
        "047_seed_conversation_doctype.sql": ["'CH', '대화', 'general', 1, 1, 25", "'会話'", "'Conversation'"],
        "078b_seed_work_plan_doctype.sql": ["'WP', '작업계획', 'general', 1, 1, 30", "'作業計画'", "'Work Plan'"],
    }
    for fname, needles in originals.items():
        source = _read(fname)
        for needle in needles:
            assert needle in source and needle in repair, (fname, needle)
    descriptions = re.findall(r"SELECT id, '(?:ko|ja|en)', '([^']+)'", _read("078b_seed_work_plan_doctype.sql"))
    ch_desc = re.findall(
        r"SELECT id, '(?:ko|ja|en)', '([^']+)'\nFROM   document_types WHERE series = 'general' AND type_code = 'CH'",
        _read("074c_document_type_descriptions.sql"),
    )
    assert len(ch_desc) == 3
    for text in [d for d in descriptions if len(d) > 20] + ch_desc:
        assert f"'{text}'" in repair, text


def test_both_files_are_postgres_only_and_exempt_from_regeneration():
    for name in (REALIGN, REPAIR):
        assert name in R._PG_HAND_AUTHORED_FILES
        assert not (MIG / "sqlite" / name).exists()
        assert not (MIG / "mysql" / name).exists()


def test_historical_rebuild_and_seed_files_are_unchanged():
    """023/025/113 stay the committed historical text (immutable applied migrations)."""
    import subprocess

    paths = [str(PG_DIR / n) for n in (
        "023_series_requirements_to_general.sql", "025_document_type_names.sql",
        "047_seed_conversation_doctype.sql", "078b_seed_work_plan_doctype.sql",
        "113_seed_t2_tr2_doctypes.sql")]
    try:
        res = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=_SERVER_DIR,
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover - environment
        pytest.skip(f"git unavailable: {exc}")
    if res.returncode not in (0, 1):
        pytest.skip(f"not a git checkout: {res.stderr!r}")
    assert res.returncode == 0


# ── live PostgreSQL (opt-in) ───────────────────────────────────────────────────

DSN_ENV = "FLOWGATE_PG_TEST_DSN"
DSN = os.environ.get(DSN_ENV) or os.environ.get("FLOWGATE_POSTGRES_TEST_DSN")
try:
    import psycopg2
except ImportError:  # pragma: no cover - environment
    psycopg2 = None
try:
    from sqloader.migrator import _split_sql_statements
except Exception:  # pragma: no cover - environment
    _split_sql_statements = None

if psycopg2 is None:
    _SKIP_LIVE = "psycopg2 is not installed; the live PostgreSQL check cannot run here"
elif _split_sql_statements is None:
    _SKIP_LIVE = "sqloader.migrator._split_sql_statements is unavailable"
elif not DSN:
    _SKIP_LIVE = f"{DSN_ENV} is not set -- opt-in live PostgreSQL replay"
else:
    _SKIP_LIVE = None
live_postgres = pytest.mark.skipif(_SKIP_LIVE is not None, reason=_SKIP_LIVE or "")


@live_postgres
class TestLiveFreshReplay:
    @pytest.fixture
    def pg(self):
        conn = psycopg2.connect(DSN)
        conn.autocommit = False
        cur = conn.cursor()
        schema = f"fg_test_0017_{uuid.uuid4().hex[:10]}"
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET LOCAL search_path TO {schema}")
        try:
            yield cur, schema
        finally:
            conn.rollback()
            conn.close()

    @staticmethod
    def _apply(cur, schema, name):
        """One file = one savepoint, like the migrator's one transaction per file."""
        cur.execute("SAVEPOINT fg_file")
        try:
            for stmt in _split_sql_statements(_read(name)):
                cur.execute(stmt)
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT fg_file")
            cur.execute(f"SET LOCAL search_path TO {schema}")
            raise
        cur.execute("RELEASE SAVEPOINT fg_file")

    @staticmethod
    def _seq_state(cur):
        cur.execute("SELECT pg_get_serial_sequence('document_types', 'id')")
        seq = cur.fetchone()[0]
        cur.execute(f"SELECT last_value, is_called FROM {seq}")
        last, called = cur.fetchone()
        cur.execute("SELECT MAX(id) FROM document_types")
        return (last + 1 if called else last), cur.fetchone()[0]

    @staticmethod
    def _global_codes(cur):
        cur.execute("SELECT type_code FROM document_types WHERE project_id IS NULL")
        return {r[0] for r in cur.fetchall()}

    def test_fresh_database_applies_everything_and_keeps_ch_and_wp(self, pg):
        cur, schema = pg
        for name in _pg_names():
            self._apply(cur, schema, name)
        next_val, max_id = self._seq_state(cur)
        assert next_val > max_id
        assert {"CH", "WP", "T2", "TR2"} <= self._global_codes(cur)
        cur.execute(
            "SELECT t.type_code, COUNT(n.locale) FROM document_types t "
            "JOIN document_type_names n ON n.document_type_id = t.id "
            "WHERE t.project_id IS NULL AND t.type_code IN ('CH', 'WP') GROUP BY t.type_code")
        assert dict(cur.fetchall()) == {"CH": 3, "WP": 3}
        # a brand-new type can still be inserted without an id
        cur.execute(
            "INSERT INTO document_types (project_id, type_code, type_name, series, is_system, is_active, "
            "sort_order, created_at, updated_at) VALUES (NULL, 'ZZ', 'zz', 'general', 1, 1, 99, 'x', 'x')")

    def test_database_stopped_at_113_recovers_through_046a_and_189(self, pg):
        cur, schema = pg
        names = _pg_names()
        stop = names.index("113_seed_t2_tr2_doctypes.sql")
        # the pre-fix world: everything before 113_seed except the new file
        for name in names[:stop]:
            if name != REALIGN:
                self._apply(cur, schema, name)
        assert not ({"CH", "WP"} & self._global_codes(cur))  # silently lost (the NR 0003 finding)
        with pytest.raises(Exception, match="duplicate key"):
            self._apply(cur, schema, "113_seed_t2_tr2_doctypes.sql")
        # next boot: pending files in name order -> 046a first, then 113, ..., 189
        self._apply(cur, schema, REALIGN)
        for name in names[stop:]:
            self._apply(cur, schema, name)
        next_val, max_id = self._seq_state(cur)
        assert next_val > max_id
        assert {"CH", "WP", "T2", "TR2"} <= self._global_codes(cur)

    def test_realign_and_repair_are_idempotent(self, pg):
        cur, schema = pg
        for name in _pg_names():
            self._apply(cur, schema, name)
        before = self._seq_state(cur)
        cur.execute("SELECT COUNT(*) FROM document_types")
        rows = cur.fetchone()[0]
        self._apply(cur, schema, REALIGN)
        self._apply(cur, schema, REPAIR)
        assert self._seq_state(cur) == before
        cur.execute("SELECT COUNT(*) FROM document_types")
        assert cur.fetchone()[0] == rows
