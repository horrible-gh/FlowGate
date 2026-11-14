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


def _tables(store) -> list[str]:
    if store.dialect == _dialect.SQLITE:
        rows = store._fetch_all("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    elif store.dialect == _dialect.MYSQL:
        rows = store._fetch_all(
            "SELECT table_name AS name FROM information_schema.tables"
            " WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'")
    else:
        rows = store._fetch_all(
            "SELECT table_name AS name FROM information_schema.tables"
            " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")
    names = sorted({(r.get("name") or r.get("NAME") or r.get("table_name") or r.get("TABLE_NAME")) for r in rows})
    return [n for n in names if n and n.lower() not in _SKIP_TABLES]


def _all_columns(store, table: str) -> list[str]:
    if store.dialect == _dialect.SQLITE:
        return [r["name"] for r in store._fetch_all("SELECT name FROM pragma_table_info(?)", [table])]
    schema = "DATABASE()" if store.dialect == _dialect.MYSQL else "current_schema()"
    rows = store._fetch_all(
        f"SELECT column_name AS name FROM information_schema.columns WHERE table_schema = {schema} AND table_name = ?",
        [table])
    return [r.get("name") or r.get("NAME") or r.get("column_name") or r.get("COLUMN_NAME") for r in rows]


def _text_columns(store, table: str) -> list[_Column]:
    out: list[_Column] = []
    if store.dialect == _dialect.SQLITE:
        for r in store._fetch_all("SELECT name, type FROM pragma_table_info(?)", [table]):
            declared = (r.get("type") or "").upper()
            # No declared type means TEXT affinity is possible (BLOB/none); include it, values decide.
            if not declared or any(t in declared for t in _SQLITE_TEXT):
                out.append(_Column(r["name"]))
        return out
    schema = "DATABASE()" if store.dialect == _dialect.MYSQL else "current_schema()"
    rows = store._fetch_all(
        f"SELECT column_name AS name, data_type AS type FROM information_schema.columns"
        f" WHERE table_schema = {schema} AND table_name = ?", [table])
    for r in rows:
        name = r.get("name") or r.get("NAME") or r.get("column_name") or r.get("COLUMN_NAME")
        dtype = str(r.get("type") or r.get("TYPE") or r.get("data_type") or r.get("DATA_TYPE") or "").lower()
        if store.dialect == _dialect.MYSQL and dtype in _MYSQL_TEXT:
            out.append(_Column(name))
        elif store.dialect == _dialect.POSTGRESQL and dtype in _PG_TEXT:
            out.append(_Column(name, dtype if dtype in ("json", "jsonb") else None))
    return out


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


def rewrite_database(store, rw: KeyRewriter) -> dict:
    """Rewrite every stored form of the module key. Runs inside the caller's transaction.

    Returns ``{"tables": {table: rows_changed}, "values": n}`` for the report.
    """
    q = lambda name: _quote(store, name)  # noqa: E731
    changed: dict[str, int] = {}
    total = 0
    # Module names are validated slugs ([a-z0-9_-]): no '%'; a '_' only widens the coarse filter.
    like = "%" + rw.coarse + "%"
    for table in _tables(store):
        columns = _all_columns(store, table)
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
        for col in _text_columns(store, table):
            if col.name in ("module", "module_key") and project_col:
                continue  # (1) did it; a bare name needs the project filter
            cast = f"CAST({q(col.name)} AS TEXT)" if col.json_type else q(col.name)
            binary = "BINARY " if store.dialect == _dialect.MYSQL else ""
            rows = store._fetch_all(
                f"SELECT DISTINCT {cast} AS v FROM {q(table)} WHERE {cast} LIKE ?", [like])
            partner = _hash_partner(col.name, colset)
            for r in rows:
                old_value = r.get("v")
                if isinstance(old_value, (bytes, bytearray)):
                    try:
                        old_value = old_value.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                new_value = rw.text(old_value)
                if not isinstance(old_value, str) or new_value == old_value:
                    continue
                target = f"CAST(? AS {col.json_type})" if col.json_type else "?"
                sets, params = [f"{q(col.name)} = {target}"], [new_value]
                if partner:
                    sets.append(f"{q(partner)} = ?")
                    params.append(hashlib.sha256(new_value.encode("utf-8")).hexdigest())
                params.append(old_value)
                count = store._execute_affected(
                    f"UPDATE {q(table)} SET {', '.join(sets)} WHERE {binary}{cast} = ?", params)
                n_table += count
                total += 1
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
