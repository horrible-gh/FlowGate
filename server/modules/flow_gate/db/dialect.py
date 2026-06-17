"""Runtime SQL dialect translation for FlowGate's hand-written queries.

Group 0088 — multi-DB (SQLite / MariaDB(MySQL) / PostgreSQL) support.

Background
----------
The db submodules embed SQL as inline string literals authored in SQLite
dialect: ``?`` placeholders, ``ON CONFLICT ... DO UPDATE SET col = excluded.col``
upserts, ``INSERT OR IGNORE/REPLACE`` and SQLite-only ``%`` LIKE literals.

sqloader translates placeholders only for *file-loaded* queries (sql/queries via
``placeholder`` config). Inline queries bypass that, so they reach the driver
verbatim. pymysql and psycopg2 both use the ``%s`` (pyformat) paramstyle, so the
raw ``?`` placeholders fail outright on MariaDB/PostgreSQL, and ``ON CONFLICT`` /
``INSERT OR ...`` are not MySQL syntax.

This module is the single locus that rewrites a SQLite-dialect query string to
the target dialect at execution time. It is a **no-op for SQLite**, so the
existing SQLite-backed test suite and production behaviour are unchanged; the
rewrites only activate when the live backend is MySQL or PostgreSQL.

Canonical input forms
----------------------
After the call-site cleanups (T0010), every upsert in the db submodules is
written in one of two SQLite/PostgreSQL-native canonical forms, which keeps the
MySQL rewrite below bounded and testable:

* ``INSERT INTO t (...) VALUES (...) ON CONFLICT (cols) DO UPDATE SET <set> [WHERE <c>]``
* ``INSERT INTO t (...) VALUES (...) ON CONFLICT DO NOTHING``

``INSERT OR REPLACE`` / ``INSERT OR IGNORE`` are still handled defensively here.
"""
from __future__ import annotations

import re

# Mirrors sqloader._prototype dialect codes (DatabasePrototype.db_type).
SQLITE = 1
MYSQL = 2
POSTGRESQL = 3


# --- SQLite-only now-expressions embedded in queries.json (group 0088) -------
# Hand-written runtime queries fill created_at/updated_at with SQLite functions:
#   strftime('%Y-%m-%dT%H:%M:%fZ', 'now')  -> UTC ISO8601 with millis + 'Z'
#   datetime('now')                        -> UTC 'YYYY-MM-DD HH:MM:SS'
# SQLite executes them natively (translate is a no-op there); MySQL/PostgreSQL
# have neither function, so they reach the driver verbatim and raise
# UndefinedFunction. Rewrite them to the dialect-native expression that yields
# the same text. The substitution runs *before* _escape_percent in both dialect
# paths, so the '%' inside the MySQL DATE_FORMAT masks get doubled to '%%' for
# the pyformat driver exactly like any other literal '%'.
_STRFTIME_NOW_RE = re.compile(
    r"strftime\s*\(\s*'%Y-%m-%dT%H:%M:%fZ'\s*,\s*'now'\s*\)",
    re.IGNORECASE,
)
_DATETIME_NOW_RE = re.compile(r"datetime\s*\(\s*'now'\s*\)", re.IGNORECASE)

# PostgreSQL: to_char masks use double-quotes to escape the literal T / Z; .MS
# is milliseconds (3 digits), matching SQLite's %f.
_PG_STRFTIME = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"
_PG_DATETIME = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

# MySQL: single '%' here — _escape_percent doubles them downstream. Note %f on
# MySQL is 6-digit microseconds vs SQLite's 3-digit millis; harmless for the
# created_at/updated_at columns (lexical ordering unaffected).
_MY_STRFTIME = "DATE_FORMAT(UTC_TIMESTAMP(3), '%Y-%m-%dT%H:%i:%S.%fZ')"
_MY_DATETIME = "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%S')"


def _rewrite_now(sql: str, strftime_repl: str, datetime_repl: str) -> str:
    """Replace the SQLite now-expressions with dialect-native equivalents."""
    sql = _STRFTIME_NOW_RE.sub(lambda _m: strftime_repl, sql)
    sql = _DATETIME_NOW_RE.sub(lambda _m: datetime_repl, sql)
    return sql


def _convert_placeholders(sql: str) -> str:
    """Replace ``?`` with ``%s`` outside of single-quoted string literals.

    Run *after* literal ``%`` has been doubled, so the ``%s`` introduced here is
    not itself escaped.
    """
    out: list[str] = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def _escape_percent(sql: str) -> str:
    """Double every literal ``%`` so pyformat drivers don't treat it as a marker.

    Safe to apply globally because at this stage placeholders are still ``?``
    (no ``%s`` exists yet) — see _convert_placeholders ordering.
    """
    return sql.replace("%", "%%")


def _insert_target(sql: str) -> str | None:
    """Return the table name from ``INSERT [OR ...|IGNORE] INTO <table>``."""
    m = re.search(
        r"\binsert\b(?:\s+or\s+\w+|\s+ignore)?\s+into\s+[`\"\[]?(\w+)",
        sql,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def _to_mysql(sql: str) -> str:
    sql = _rewrite_now(sql, _MY_STRFTIME, _MY_DATETIME)
    # INSERT OR REPLACE/IGNORE → MySQL prefixes (defensive; call sites use ON CONFLICT).
    sql = re.sub(r"\binsert\s+or\s+replace\s+into\b", "REPLACE INTO", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\binsert\s+or\s+ignore\s+into\b", "INSERT IGNORE INTO", sql, flags=re.IGNORECASE)

    # ON CONFLICT [ (cols) ] DO NOTHING  →  INSERT IGNORE + drop the clause.
    if re.search(r"\bon\s+conflict\b", sql, re.IGNORECASE) and re.search(
        r"\bdo\s+nothing\b", sql, re.IGNORECASE
    ):
        sql = re.sub(
            r"\bon\s+conflict\b\s*(?:\([^)]*\))?\s*do\s+nothing\b",
            "",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        ).rstrip()
        sql = re.sub(r"\binsert\s+into\b", "INSERT IGNORE INTO", sql, count=1, flags=re.IGNORECASE)
        return _convert_placeholders(_escape_percent(sql))

    # ON CONFLICT (cols) DO UPDATE SET <set> [WHERE ...]  →  ON DUPLICATE KEY UPDATE <set'>
    m = re.search(
        r"\bon\s+conflict\b\s*(?:\([^)]*\))?\s*do\s+update\s+set\b(.*)$",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        set_clause = m.group(1)
        # MySQL's ON DUPLICATE KEY UPDATE has no conditional WHERE; drop it.
        set_clause = re.split(r"\bwhere\b", set_clause, maxsplit=1, flags=re.IGNORECASE)[0]
        # excluded.col → VALUES(col)  (the would-be-inserted value)
        set_clause = re.sub(r"\bexcluded\.(\w+)", r"VALUES(\1)", set_clause, flags=re.IGNORECASE)
        # table.col → col  (the existing-row value reference)
        table = _insert_target(sql)
        if table:
            set_clause = re.sub(
                rf"\b{re.escape(table)}\.(\w+)", r"\1", set_clause, flags=re.IGNORECASE
            )
        head = sql[: m.start()].rstrip()
        sql = f"{head} ON DUPLICATE KEY UPDATE {set_clause.strip()}"

    return _convert_placeholders(_escape_percent(sql))


def _to_postgres(sql: str) -> str:
    sql = _rewrite_now(sql, _PG_STRFTIME, _PG_DATETIME)
    # ON CONFLICT / excluded are PostgreSQL-native; only handle SQLite-only prefixes.
    if re.search(r"\binsert\s+or\s+ignore\s+into\b", sql, re.IGNORECASE):
        sql = re.sub(r"\binsert\s+or\s+ignore\s+into\b", "INSERT INTO", sql, flags=re.IGNORECASE)
        if not re.search(r"\bon\s+conflict\b", sql, re.IGNORECASE):
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # INSERT OR REPLACE has no generic PostgreSQL form; call sites are migrated
    # to ON CONFLICT ... DO UPDATE, so it should not reach here. Left as-is.
    return _convert_placeholders(_escape_percent(sql))


def translate(sql: str, dialect: int) -> str:
    """Rewrite a SQLite-dialect query for the target backend.

    No-op for SQLite (dialect is None/SQLITE), so existing behaviour is preserved.
    """
    if not sql or dialect in (None, SQLITE):
        return sql
    if dialect == MYSQL:
        return _to_mysql(sql)
    if dialect == POSTGRESQL:
        return _to_postgres(sql)
    return sql
