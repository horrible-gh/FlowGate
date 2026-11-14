"""Group (module) key rename — the database half (flowgate.v02gui.0012 0001-B item B).

A module's name is part of every key of its groups: ``groups.module``, ``documents.module``,
``id_counter.module``, ``project_modules.name`` / ``module_id`` (``{project}:{module}``), every
group / sub-group / document id (``{project}.{module}.{seq}...``), the storage paths
(``documents/{project}/{branch}/{module}/{group}/...``) and the group branch / worktree names.
Renaming a module rewrites all of them to the new name, so the old name is free afterwards and
nothing keeps resolving it (there is no alias table any more; migration 188 drops the one 187 made).

This module only does the database part, inside the caller's transaction:

- :class:`KeyRewriter` is the one text rule. It is pure, so files (front matter, bodies) and the
  database go through the same replacement.
- :func:`rewrite_database` walks every base table and every text column of the live schema (SQLite,
  MySQL/MariaDB, PostgreSQL) instead of a hand-kept table list, so a table a later migration adds is
  covered without touching this file. Values are rewritten by exact old value -> new value, so no
  primary key is needed and rows are never matched by position.

The walk keeps the number of statements small, because every statement is a round trip inside the
transaction that holds the module's Flow locks (0004-CH: ~2,150 statements made a rename take 8 s
even with no group). It reads the schema with one query, searches each table with one query that
covers all of its text columns, and writes each changed column with one ``CASE`` UPDATE per chunk of
values instead of one UPDATE (one table scan) per value.

The orchestration (guards, refusal while work is running, file moves, Git) lives in
``modules.flow_gate.services.module_rename``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import dialect as _dialect

_log = logging.getLogger(__name__)

# Tables the walk never rewrites: migration bookkeeping (file names, checksums) and the retired alias
# table (migration 188 drops it; a database that still has it must not carry stale rows forward).
_SKIP_TABLES = frozenset({
    "schema_migrations", "migrations", "_migrations", "flowgate_migrations", "sqlite_sequence",
    "project_module_identifiers",
})

_SQLITE_TEXT = ("CHAR", "CLOB", "TEXT", "JSON", "VARCHAR")
_MYSQL_TEXT = frozenset({"char", "varchar", "tinytext", "text", "mediumtext", "longtext", "json", "enum", "set"})
_PG_TEXT = frozenset({"text", "character varying", "character", "json", "jsonb", "name", "citext"})

_ID_EDGE_BEFORE = r"(?<![A-Za-z0-9_\-.])"
_NAME_EDGE_BEFORE = r"(?<![A-Za-z0-9_\-.])"
_NAME_EDGE_AFTER = r"(?![A-Za-z0-9_\-])"
_SEP = r"(?:/|\\+)"


@dataclass
class KeyRewriter:
    """Rewrites every textual form of ``project_id``/``old`` keys to ``new``.

    Forms (all anchored so a longer module name or another project never matches):

    1. ids — ``{project}.{old}.<digit>`` -> ``{project}.{new}.<digit>`` (groups, sub-groups, documents,
       conversation documents, ``flowgate.v02gui.0012.0004-CH``);
    2. module id — ``{project}:{old}`` (``project_modules.module_id``, tree node ids ``module:{p}:{old}``);
    3. storage paths — ``documents/{project_dir}/[{branch}/]{old}/<digit>`` with ``/`` or ``\\``
       separators (storage-relative, legacy absolute and JSON-escaped forms);
    4. ``exact`` — whole-name pairs the caller derives from the real naming functions
       (group branch and worktree names such as ``flowgate_v02gui_0012``);
    5. module fields — ``"module": "{old}"`` (JSON) and ``module: {old}`` (front matter) — only in a
       text that is evidently about this project (it carries a rewritten id or ``project`` = project),
       because a bare module name is ambiguous across projects.
    """

    project_id: str
    old: str
    new: str
    project_dir: str
    exact: dict = field(default_factory=dict)

    def __post_init__(self):
        p, o = re.escape(self.project_id), re.escape(self.old)
        pd = re.escape(self.project_dir)
        self._id_re = re.compile(_ID_EDGE_BEFORE + p + r"\." + o + r"\.(?=\d)")
        self._id_new = f"{self.project_id}.{self.new}."
        self._module_id_re = re.compile(_ID_EDGE_BEFORE + p + ":" + o + _NAME_EDGE_AFTER)
        self._module_id_new = f"{self.project_id}:{self.new}"
        self._path_re = re.compile(
            r"(documents" + _SEP + pd + _SEP + r"(?:[^/\\\s\"']+" + _SEP + r")?)" + o + r"(?=" + _SEP + r"\d)")
        pairs = sorted(((k, v) for k, v in (self.exact or {}).items() if k and k != v), key=lambda kv: -len(kv[0]))
        self._exact = dict(pairs)
        self._exact_re = (re.compile(_NAME_EDGE_BEFORE + "(" + "|".join(re.escape(k) for k, _ in pairs) + ")"
                                     + _NAME_EDGE_AFTER) if pairs else None)
        self._json_module_re = re.compile(r'("module"\s*:\s*")' + o + r'(")')
        self._yaml_module_re = re.compile(r"(?m)^([ \t]*module[ \t]*:[ \t]*)" + o + r"([ \t]*\r?)$")
        self._project_marker_re = re.compile(
            r'(?m)"project(?:_id)?"\s*:\s*"' + p + r'"|^[ \t]*project(?:_id)?[ \t]*:[ \t]*' + p + r"[ \t]*\r?$")
        self.coarse = self.old  # every form above contains the old module name

    def touches(self, text) -> bool:
        return isinstance(text, str) and self.old in text

    def text(self, text):
        """The rewritten text (the same object when nothing matched)."""
        if not self.touches(text):
            return text
        out = self._id_re.sub(self._id_new, text)
        out = self._module_id_re.sub(self._module_id_new, out)
        out = self._path_re.sub(lambda m: m.group(1) + self.new, out)
        if self._exact_re is not None:
            out = self._exact_re.sub(lambda m: self._exact[m.group(1)], out)
        if out != text or self._project_marker_re.search(text):
            out = self._json_module_re.sub(lambda m: m.group(1) + self.new + m.group(2), out)
            out = self._yaml_module_re.sub(lambda m: m.group(1) + self.new + m.group(2), out)
        return out if out != text else text

    def doc_id(self, value: Optional[str]) -> Optional[str]:
        return self.text(value) if value else value


class ModuleRenameRefused(Exception):
    """A refused rename. ``code`` is stable for clients; ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 409, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# ── schema introspection ───────────────────────────────────────────────────────────────────────

@dataclass
class _Column:
    name: str
    json_type: Optional[str] = None  # 'json' / 'jsonb' on PostgreSQL (needs casts)


def _quote(store, name: str) -> str:
    if store.dialect == _dialect.MYSQL:
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _row_value(row: dict, *keys: str):
    for key in keys:
        if key in row:
            return row[key]
        if key.upper() in row:
            return row[key.upper()]
    return None


def _schema(store) -> list[tuple[str, list[str], list[_Column]]]:
    """``[(table, all column names, text columns)]`` of every base table, read with one query."""
    if store.dialect == _dialect.SQLITE:
        rows = store._fetch_all(
            "SELECT m.name AS tbl, p.name AS name, p.type AS type"
            " FROM sqlite_master AS m JOIN pragma_table_info(m.name) AS p"
            " WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%' ORDER BY m.name, p.cid")
    else:
        schema = "DATABASE()" if store.dialect == _dialect.MYSQL else "current_schema()"
        rows = store._fetch_all(
            "SELECT c.table_name AS tbl, c.column_name AS name, c.data_type AS type"
            " FROM information_schema.columns c JOIN information_schema.tables t"
            " ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
            f" WHERE c.table_schema = {schema} AND t.table_type = 'BASE TABLE'"
            " ORDER BY c.table_name, c.ordinal_position")
    tables: dict[str, tuple[list[str], list[_Column]]] = {}
    for r in rows:
        table = _row_value(r, "tbl", "table_name")
        name = _row_value(r, "name", "column_name")
        if not table or not name or str(table).lower() in _SKIP_TABLES:
            continue
        columns, text = tables.setdefault(table, ([], []))
        columns.append(name)
        declared = str(_row_value(r, "type", "data_type") or "")
        if store.dialect == _dialect.SQLITE:
            # No declared type means TEXT affinity is possible (BLOB/none); include it, values decide.
            if not declared or any(t in declared.upper() for t in _SQLITE_TEXT):
                text.append(_Column(name))
        elif store.dialect == _dialect.MYSQL:
            if declared.lower() in _MYSQL_TEXT:
                text.append(_Column(name))
        elif declared.lower() in _PG_TEXT:
            text.append(_Column(name, declared.lower() if declared.lower() in ("json", "jsonb") else None))
    return [(table, columns, text) for table, (columns, text) in sorted(tables.items())]


# ── foreign keys: renamed keys are referenced (groups.group_id, documents.doc_id, ...) ──────────

def defer_foreign_keys(store) -> None:
    """Let parent and child keys change in any order inside this transaction; checked at commit."""
    if store.dialect == _dialect.SQLITE:
        store._execute("PRAGMA defer_foreign_keys = ON")  # resets itself at COMMIT / ROLLBACK
    elif store.dialect == _dialect.MYSQL:
        store._execute("SET FOREIGN_KEY_CHECKS = 0")
    else:
        # PostgreSQL defers only DEFERRABLE constraints. Make the non-deferrable ones
        # DEFERRABLE INITIALLY IMMEDIATE (every other statement still checks them immediately), then
        # defer all of them for this transaction. DDL is transactional, so a rollback undoes it too.
        rows = store._fetch_all(
            "SELECT c.conname AS name, t.relname AS tbl FROM pg_constraint c"
            " JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace"
            " WHERE c.contype = 'f' AND NOT c.condeferrable AND n.nspname = current_schema()")
        for r in rows:
            store._execute(
                f"ALTER TABLE {_quote(store, r['tbl'])} ALTER CONSTRAINT {_quote(store, r['name'])}"
                " DEFERRABLE INITIALLY IMMEDIATE")
        store._execute("SET CONSTRAINTS ALL DEFERRED")


def restore_foreign_keys(store) -> None:
    """MySQL keeps FOREIGN_KEY_CHECKS per session: switch it back on before the connection is reused."""
    if store.dialect == _dialect.MYSQL:
        store._execute("SET FOREIGN_KEY_CHECKS = 1")


# ── the walk ───────────────────────────────────────────────────────────────────────────────────

def _project_column(columns: Iterable[str]) -> Optional[str]:
    names = set(columns)
    for candidate in ("project_id", "project"):
        if candidate in names:
            return candidate
    return None


def _hash_partner(column: str, columns: set[str]) -> Optional[str]:
    for suffix in ("_hash", "_sha256"):
        if column + suffix in columns:
            return column + suffix
    return None


# One CASE UPDATE carries at most this many values (5 parameters each with a hash partner, under
# SQLite's historic 999-parameter limit) and roughly this many characters of parameters (well under
# MySQL's max_allowed_packet).
_UPDATE_CHUNK_VALUES = 150
_UPDATE_CHUNK_CHARS = 1_000_000


def _chunks(pairs: list[tuple[str, str]]) -> Iterable[list[tuple[str, str]]]:
    chunk: list[tuple[str, str]] = []
    size = 0
    for old_value, new_value in pairs:
        weight = len(old_value) + len(new_value)
        if chunk and (len(chunk) >= _UPDATE_CHUNK_VALUES or size + weight > _UPDATE_CHUNK_CHARS):
            yield chunk
            chunk, size = [], 0
        chunk.append((old_value, new_value))
        size += weight
    if chunk:
        yield chunk


def _decoded(value):
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value


def rewrite_database(store, rw: KeyRewriter) -> dict:
    """Rewrite every stored form of the module key. Runs inside the caller's transaction.

    Returns ``{"tables": {table: rows_changed}, "values": n}`` for the report.
    """
    q = lambda name: _quote(store, name)  # noqa: E731
    binary = "BINARY " if store.dialect == _dialect.MYSQL else ""
    changed: dict[str, int] = {}
    total = 0
    # Module names are validated slugs ([a-z0-9_-]): no '%'; a '_' only widens the coarse filter.
    like = "%" + rw.coarse + "%"
    for table, columns, text_columns in _schema(store):
        colset = set(columns)
        n_table = 0
        # (1) bare module columns: only rows of this project (the same name may exist elsewhere).
        project_col = _project_column(columns)
        for module_col in ("module", "module_key"):
            if module_col in colset and project_col:
                store._execute(
                    f"UPDATE {q(table)} SET {q(module_col)} = ? WHERE {q(project_col)} = ? AND {q(module_col)} = ?",
                    [rw.new, rw.project_id, rw.old])
        if table == "project_modules" and "name" in colset:
            store._execute("UPDATE project_modules SET name = ? WHERE project_id = ? AND name = ?",
                           [rw.new, rw.project_id, rw.old])
        # (2) every text column: exact value -> rewritten value.
        cols = [c for c in text_columns if not (c.name in ("module", "module_key") and project_col)]
        if not cols:
            continue  # (1) did the bare module columns; a bare name needs the project filter
        exprs = [f"CAST({q(c.name)} AS TEXT)" if c.json_type else q(c.name) for c in cols]
        # One search per table: only the columns that match come back, the others are NULL.
        picks = ", ".join(f"CASE WHEN {e} LIKE ? THEN {e} END AS v{i}" for i, e in enumerate(exprs))
        where = " OR ".join(f"{e} LIKE ?" for e in exprs)
        rows = store._fetch_all(f"SELECT {picks} FROM {q(table)} WHERE {where}", [like] * (2 * len(exprs)))
        if not rows:
            continue
        for i, (col, cast) in enumerate(zip(cols, exprs)):
            rewrites: dict[str, str] = {}
            for r in rows:
                old_value = _decoded(_row_value(r, f"v{i}"))
                if not isinstance(old_value, str) or old_value in rewrites:
                    continue
                new_value = rw.text(old_value)
                if new_value != old_value:
                    rewrites[old_value] = new_value
            if not rewrites:
                continue
            partner = _hash_partner(col.name, colset)
            target = f"CAST(? AS {col.json_type})" if col.json_type else "?"
            for chunk in _chunks(list(rewrites.items())):
                sets, params = [], []
                if partner:
                    # First: MySQL evaluates single-table SET assignments left to right, so the
                    # partner must be computed while the column still holds the old value.
                    sets.append(f"{q(partner)} = CASE {binary}{cast}"
                                + " WHEN ? THEN ?" * len(chunk) + f" ELSE {q(partner)} END")
                    for old_value, new_value in chunk:
                        params += [old_value, hashlib.sha256(new_value.encode("utf-8")).hexdigest()]
                sets.append(f"{q(col.name)} = CASE {binary}{cast}"
                            + f" WHEN ? THEN {target}" * len(chunk) + f" ELSE {q(col.name)} END")
                for old_value, new_value in chunk:
                    params += [old_value, new_value]
                params += [old_value for old_value, _ in chunk]
                count = store._execute_affected(
                    f"UPDATE {q(table)} SET {', '.join(sets)}"
                    f" WHERE {binary}{cast} IN ({', '.join('?' for _ in chunk)})", params)
                n_table += count
                total += len(chunk)
        if n_table:
            changed[table] = n_table
    return {"tables": changed, "values": total}


def module_in_use(store, project_id: str, name: str) -> Optional[dict]:
    """Who uses ``name`` as a module key of ``project_id`` now (None when it is free).

    ``{"group_id": ..., "title": ...}`` names the group the client should show; a name only a
    ``project_modules`` row (an empty group) or stray documents/ids hold reports that instead.
    """
    row = store._fetch_one(
        "SELECT group_id, title FROM groups WHERE project_id = ? AND module = ? ORDER BY group_id LIMIT 1",
        [project_id, name])
    if row:
        title = None
        if store.table_exists("project_modules"):
            pm = store._fetch_one("SELECT title FROM project_modules WHERE project_id = ? AND name = ?",
                                  [project_id, name])
            title = (pm or {}).get("title")
        return {"kind": "group", "module": name, "title": title or name, "group_id": row.get("group_id")}
    if store.table_exists("project_modules"):
        pm = store._fetch_one("SELECT title FROM project_modules WHERE project_id = ? AND name = ?",
                              [project_id, name])
        if pm:
            return {"kind": "module", "module": name, "title": pm.get("title") or name}
    prefix = f"{project_id}.{name}."
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    doc = store._fetch_one(
        "SELECT doc_id FROM documents WHERE (project_id = ? AND module = ?) OR doc_id LIKE ? ESCAPE '!' LIMIT 1",
        [project_id, name, escaped + "%"])
    if doc:
        return {"kind": "documents", "module": name, "title": name, "doc_id": doc.get("doc_id")}
    counter = store._fetch_one("SELECT 1 AS hit FROM id_counter WHERE project_id = ? AND module = ? LIMIT 1",
                               [project_id, name])
    if counter:
        return {"kind": "numbering", "module": name, "title": name}
    return None

