"""flowgate.v02gui.0012 (0004-CH turn 60) — an R↔B number swap repoints the references with one UPDATE per table.

A number swap gives the root a new doc_id, so every column that stores a doc_id (``_DOC_REFERENCE_COLUMNS``) moves
from the old id to the new one. It used to send one UPDATE per column (22): ``documents`` scanned six times,
``events`` three times, each column without an index a whole-table scan. One UPDATE per table now sets each column
only where it held the old id. These tests pin that the rows and values are the ones the per-column UPDATEs gave.
"""
from __future__ import annotations

import sqlite3

from test_root_conversion_number_swap_0064 import (  # noqa: F401 — fixtures (env, the 0127 ports) and helpers
    FLOW, ROOT, ROOT_B, _sql, convert, dedicated_document_write_ports_0127, env, make_doc)

OTHER = "flowgate.v02.0092.0001-R"


def _insert(env, table, values):
    """One row of ``table`` with ``values``; the other NOT NULL columns get placeholders (FK off). Its rowid or None."""
    conn = sqlite3.connect(env["db"])
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            return None
        row = {}
        for _cid, name, ctype, notnull, default, pk in info:
            if name in values:
                row[name] = values[name]
            elif pk and "INT" in (ctype or "").upper():
                continue
            elif notnull and default is None:
                # A document id column gets a real document (OTHER): a dangling one makes SQLite refuse a table statement.
                row[name] = (1 if "INT" in (ctype or "").upper() else
                             OTHER if name.endswith(("doc_id", "target_id")) else f"x_{name}_{len(values)}")
        cur = conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           list(row.values()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def _by_rowid(env, table, rowid):
    return _sql(env["db"], f"SELECT * FROM {table} WHERE rowid = ?", (rowid,))[0]


def test_every_reference_column_moves_in_one_statement_per_table_and_nothing_else_changes(env, monkeypatch):
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.documents.document_service import _DOC_REFERENCE_COLUMNS

    make_doc(env)
    make_doc(env, doc_id=OTHER, rel="documents/flowgate/main/v02/0092/0001-R_requirement.md")
    # documents.target_id / triggered_by of the Flow make children (a succession), superseded_by a superseded root,
    # a workflow sequence a decided root: those columns are seeded on a row of another Flow, which the number swap
    # rewrites all the same. Each table also gets a row holding the old id in one column only.
    skip = {("workflow_sequences", "doc_id")}
    seeded = {}
    for table, column in _DOC_REFERENCE_COLUMNS:
        if (table, column) in skip:
            continue
        values = {column: ROOT}
        if table == "documents":
            values.update(doc_id=f"flowgate.v02.0092.{len(seeded) + 2:04d}-N", project_id="flowgate", module="v02",
                          group_id="flowgate.v02.0092", type_code="N", title="t", status="draft")
        rowid = _insert(env, table, values)
        if rowid is not None:
            seeded[(table, column)] = rowid
    assert len({t for t, _ in seeded}) >= 7, seeded
    # A row holding the old id in two columns of one table and another document's id in the third. (A dangling id
    # there makes SQLite refuse the table statement, which checks every foreign key it sets; the columns one by one
    # give the same rows, see the next test.)
    both = _insert(env, "events", {"doc_id": ROOT, "related_doc_id": ROOT, "related_target_id": OTHER})
    before = {key: _by_rowid(env, key[0], rowid) for key, rowid in seeded.items()}

    statements = []
    store = conn_mod.get_store()
    real = store._execute
    monkeypatch.setattr(store, "_execute", lambda sql, params=None, *a, **k: statements.append(sql) or real(sql, params, *a, **k))
    assert convert()["data"]["doc_id"] == ROOT_B

    for (table, column), rowid in seeded.items():
        after = _by_rowid(env, table, rowid)
        expected = {**before[(table, column)], column: ROOT_B}
        assert after == expected, (table, column)
    assert {k: _by_rowid(env, "events", both)[k] for k in ("doc_id", "related_doc_id", "related_target_id")} == {
        "doc_id": ROOT_B, "related_doc_id": ROOT_B, "related_target_id": OTHER}
    for table, column in _DOC_REFERENCE_COLUMNS:
        try:
            left = _sql(env["db"], f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (ROOT,))[0]["n"]
        except sqlite3.DatabaseError:
            continue
        assert left == 0, (table, column)

    tables = {t for t, _ in _DOC_REFERENCE_COLUMNS}
    repoints = [s for s in statements if s.startswith("UPDATE ") and s.split()[1] in tables
                and " WHERE " in s and "revision_no" not in s and "SET doc_id = doc_id" not in s]   # not the row lock
    per_table = {}
    for s in repoints:
        per_table[s.split()[1]] = per_table.get(s.split()[1], 0) + 1
    assert per_table.get("documents") == 1 and per_table.get("events") == 1, repoints
    assert all(n == 1 for n in per_table.values()), per_table


def test_a_table_whose_statement_fails_falls_back_to_its_columns_one_by_one(env):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents import document_service

    make_doc(env)
    make_doc(env, doc_id=ROOT_B, rel="documents/flowgate/main/v02/0091/0001-B_requirement.md")
    make_doc(env, doc_id=OTHER, rel="documents/flowgate/main/v02/0092/0001-R_requirement.md")
    first = _insert(env, "events", {"doc_id": ROOT, "related_doc_id": OTHER})
    second = _insert(env, "events", {"doc_id": OTHER, "related_doc_id": ROOT})
    store = get_store()
    with store.transaction():
        document_service._repoint_references(
            store, (("events", ("doc_id", "no_such_column", "related_doc_id")), ("no_such_table", ("a", "b"))),
            ROOT, ROOT_B)
    assert (_by_rowid(env, "events", first)["doc_id"], _by_rowid(env, "events", first)["related_doc_id"]) == (ROOT_B, OTHER)
    assert (_by_rowid(env, "events", second)["doc_id"], _by_rowid(env, "events", second)["related_doc_id"]) == (OTHER, ROOT_B)
