"""0449 T0004 — server contracts behind the "reopened group loses its tree / can't proceed" bug.

Three independent things NR0003 established by execution, pinned here:

1. **Flat group tree (item 3 / E2).** ``get_group_tree`` used to put every module, group,
   orphan and document node in the top-level ``nodes`` list AND inside its parent's
   ``children``. Measured on the live project: 4,210 unique ids serialized as 12,169 entries
   in a 4.8 MB response, and no consumer ever read the nested copy (GroupTreeNode.vue derives
   children from ``parent_id``). The duplication is what made a failed refresh's retry so
   expensive. ``nodes`` is now the single representation: each id exactly once, no ``children``.

2. **Return-point lifecycle (item 5 / E4).** A rewind snapshots the approved baseline. When
   the worker redoes that work FORWARD — re-approving each snapshot document through the
   normal pipeline instead of pressing restore — the ledger no longer describes anything, but
   it used to survive until the next rewind, so the document API kept answering
   ``exists=true / current_min_seq=null``. It is now cleared at the approval that exhausts it,
   and only then: a still-pending snapshot (live or nested rewind) keeps its baseline and
   ``root_prev_status`` untouched.

3. **Advance head contract + observability (items 4/6).** ``/workflow/advance`` answers 201 on
   an N head and 409 ``sequence_exhausted`` on an AC head where every sequence row is
   realised — the incident's own shape — and a lingering return point changes neither. Both
   outcomes are now written to the operational log with their code, and no raw token,
   mention or document body goes with them.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
# config.Settings has no defaults for these, and conftest only sets SECRET_KEY. Without them
# this module passes only when some EARLIER test module in the same session happened to set
# them — i.e. green in a full run, red on its own. Set every field Settings requires here.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Flat group tree
# ══════════════════════════════════════════════════════════════════════════════

class _ReadOnlyStore:
    """Minimal store shim over a seeded sqlite connection (mirrors test_t506's)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()


TREE_PROJECT = "t0449proj"


def _seed_tree_db(conn: sqlite3.Connection, *, groups_per_module: int, docs_per_group: int):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, 'T0449 tree', ?, ?)",
        (TREE_PROJECT, now, now),
    )
    seq = 0
    for module_index, module in enumerate(("default", "infra")):
        for group_index in range(groups_per_module):
            group_id = f"{TREE_PROJECT}.{module}.{module_index}{group_index:03d}"
            conn.execute(
                "INSERT OR IGNORE INTO groups "
                "(group_id, project_id, module, title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'OPEN', ?, ?)",
                (group_id, TREE_PROJECT, module, f"Group {group_id}", now, now),
            )
            for doc_index in range(docs_per_group):
                seq += 1
                type_code = "R" if doc_index == 0 else "T"
                review = "wf_done" if (doc_index == 0 and group_index == 0) else "approved"
                conn.execute(
                    "INSERT INTO documents "
                    "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
                    " doc_review_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                    (
                        f"{group_id}.{doc_index:04d}-{type_code}",
                        TREE_PROJECT, module, group_id, type_code, seq,
                        f"{type_code} doc {seq}", review, now, now,
                    ),
                )
    # One orphan document: its group_id is not in the groups table, so the tree builds an
    # "Uncategorized" node for it. That node was duplicated by the old code too.
    seq += 1
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        " doc_review_status, created_at, updated_at) "
        "VALUES (?, ?, 'default', ?, 'T', ?, 'orphan doc', 'draft', 'approved', ?, ?)",
        (f"{TREE_PROJECT}.default.9999.0001-T", TREE_PROJECT, f"{TREE_PROJECT}.default.9999", seq, now, now),
    )
    conn.commit()


@contextmanager
def _tree_db(*, groups_per_module: int, docs_per_group: int):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    for migration in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(migration.read_text(encoding="utf-8"))
        except Exception:
            # Some migrations target tables this suite does not use and fail on re-apply
            # order; the three tables read here are all present. Same handling as test_t506.
            pass
    # Re-assert after the migrations: executescript commits, and a migration file may flip the
    # pragma back on. The orphan document below deliberately names a group that does not exist.
    conn.execute("PRAGMA foreign_keys = OFF")
    _seed_tree_db(conn, groups_per_module=groups_per_module, docs_per_group=docs_per_group)
    try:
        yield conn
    finally:
        conn.close()
        os.unlink(db_path)


def _group_tree(conn):
    from unittest.mock import patch

    from modules.flow_gate.db import connection as _conn
    from modules.flow_gate.process_service import get_group_tree

    with patch.object(_conn, "STORE", _ReadOnlyStore(conn)):
        return get_group_tree(TREE_PROJECT)


def _count_entries(value) -> int:
    """Every dict in the payload, however deeply nested — the serialized item count."""
    if isinstance(value, dict):
        return 1 + sum(_count_entries(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_entries(v) for v in value)
    return 0


class TestFlatGroupTree:
    def test_every_id_is_serialized_exactly_once(self):
        with _tree_db(groups_per_module=3, docs_per_group=4) as conn:
            tree = _group_tree(conn)

        ids = [node["id"] for node in tree["nodes"]]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert duplicates == []
        assert len(ids) == len(set(ids))
        # The live measurement was 12,169 entries for 4,210 ids. Entries and nodes now agree:
        # the only dicts in the payload ARE the nodes.
        assert _count_entries(tree["nodes"]) == len(tree["nodes"])

    def test_no_node_carries_a_nested_children_list(self):
        with _tree_db(groups_per_module=3, docs_per_group=4) as conn:
            tree = _group_tree(conn)

        with_children = [n["id"] for n in tree["nodes"] if "children" in n]
        assert with_children == []

    def test_parent_id_still_reconstructs_the_whole_hierarchy(self):
        with _tree_db(groups_per_module=2, docs_per_group=3) as conn:
            tree = _group_tree(conn)

        by_id = {n["id"]: n for n in tree["nodes"]}
        roots = [n for n in tree["nodes"] if n["parent_id"] is None]
        assert [n["node_type"] for n in roots] == ["project"]
        # Every non-root names a parent that exists in the same flat list — the client's
        # GroupTreeNode computes its children from exactly this.
        for node in tree["nodes"]:
            if node["parent_id"] is not None:
                assert node["parent_id"] in by_id, node["id"]
        modules = [n for n in tree["nodes"] if n["node_type"] == "module"]
        assert modules and all(m["parent_id"] == roots[0]["id"] for m in modules)
        groups = [n for n in tree["nodes"] if n["node_type"] == "group"]
        assert groups and all(by_id[g["parent_id"]]["node_type"] == "module" for g in groups)
        documents = [n for n in tree["nodes"] if n["node_type"] == "document"]
        assert documents
        assert all(by_id[d["parent_id"]]["node_type"] in ("group", "orphan") for d in documents)
        # The orphan bucket is built and linked the same way.
        orphans = [n for n in tree["nodes"] if n["node_type"] == "orphan"]
        assert orphans and all(by_id[o["parent_id"]]["node_type"] == "module" for o in orphans)

    def test_the_fields_the_ui_reads_are_preserved(self):
        with _tree_db(groups_per_module=2, docs_per_group=3) as conn:
            tree = _group_tree(conn)

        groups = {n["id"]: n for n in tree["nodes"] if n["node_type"] == "group"}
        assert any(g["is_final_approved"] is True for g in groups.values())
        assert any(g["is_final_approved"] is False for g in groups.values())
        assert all("is_discarded" in g for g in groups.values())
        assert all(g["label"] for g in groups.values())
        documents = [n for n in tree["nodes"] if n["node_type"] == "document"]
        for doc in documents:
            assert doc["label"].startswith("[")
            assert "type_code" in doc and "has_md" in doc and "md_path" in doc
            # 0410's snapshot fields ride on document nodes and must survive the flattening.
            assert "origin_provider_name" in doc and "origin_ai_run_id" in doc

    def test_serialized_size_grows_linearly_with_the_node_count(self):
        """The 4.8 MB response's real problem: entries grew faster than ids.

        Doubling the documents must roughly double the payload. Under the old shape every
        document was written twice and the growth carried that constant with it; the check
        that actually distinguishes the two is entries-per-node staying at 1.
        """
        with _tree_db(groups_per_module=3, docs_per_group=4) as conn:
            small = _group_tree(conn)
        with _tree_db(groups_per_module=3, docs_per_group=8) as conn:
            large = _group_tree(conn)

        assert _count_entries(small["nodes"]) == len(small["nodes"])
        assert _count_entries(large["nodes"]) == len(large["nodes"])
        # Bytes per UNIQUE id is the metric that separates the two shapes: with a duplicated
        # nested copy it climbs as the tree deepens, because each added document is written
        # once in `nodes` and again inside its group. Flat, it stays put.
        small_per_node = len(json.dumps(small)) / len(small["nodes"])
        large_per_node = len(json.dumps(large)) / len(large["nodes"])
        assert large_per_node <= small_per_node * 1.15, (small_per_node, large_per_node)


# ══════════════════════════════════════════════════════════════════════════════
# 1b. The same contracts at the incident's own scale (item 3.4 / criterion 3)
# ══════════════════════════════════════════════════════════════════════════════
#
# 0449 TR0005 rev1. rev0 proved the flattening on 3x4 and 3x8 fixtures — 35 and 59 nodes —
# and reported the load numbers from a scratch probe (``tree_probe.py``) that no test runs.
# T0004 item 3.4 asks for the opposite: the load shape pinned as a PRODUCT regression test,
# explicitly "조사용 scratch probe만 통과한 것을 제품 회귀 테스트 완료로 대신하지 마라".
# A 35-node fixture cannot distinguish "linear" from "nearly linear", and it cannot fail the
# way the live project failed. So the fixture below is built at the size NR0003 measured.
#
# NR0003 on the live project: 4,210 unique ids serialized as 12,169 entries, 4.8 MB.
# This fixture: 2 modules x 84 groups x 34 documents = 5,885 unique ids, which the pre-0449
# shape would have serialized as 17,480 entries / 4.86 MB (asserted below, not assumed).
# Seeding and building it costs about 1.2 s, so it runs in the ordinary suite.

LOAD_GROUPS_PER_MODULE = 84
LOAD_DOCS_PER_GROUP = 34

# What NR0003 measured on the live project, and what this fixture has to be at least as big as.
INCIDENT_UNIQUE_IDS = 4_210
INCIDENT_ENTRIES = 12_169
INCIDENT_BYTES = 4.8 * 1024 * 1024


def _pre_0449_shape(nodes: list[dict]) -> list[dict]:
    """Reconstruct the payload ``get_group_tree`` used to return, from the flat one.

    The old code appended the SAME dict to ``nodes`` and to its parent's ``children``:
    groups and orphans into their module's list, documents into their group's. Because the
    objects were aliased rather than copied, ``json.dumps`` wrote each one out again at every
    place it appeared — a document once in ``nodes`` and twice more through its group (which
    itself appears in ``nodes`` and in its module's ``children``).

    This exists so the "4.8 MB" in item 3.4 is a measured property of THIS fixture rather than
    a number quoted from the investigation. It models the old shape; it is never asserted to
    be the current output.
    """
    clone = {node["id"]: dict(node) for node in nodes}
    for node in nodes:
        if node["node_type"] in ("project", "module", "group", "orphan"):
            clone[node["id"]]["children"] = []
    for node in nodes:
        parent = clone.get(node["parent_id"]) if node["parent_id"] else None
        if parent is not None and node["node_type"] in ("group", "orphan", "document"):
            parent["children"].append(clone[node["id"]])
    return [clone[node["id"]] for node in nodes]


@pytest.fixture(scope="module")
def load_tree() -> dict:
    """Built once for the whole class — the seed is the only slow part."""
    with _tree_db(
        groups_per_module=LOAD_GROUPS_PER_MODULE, docs_per_group=LOAD_DOCS_PER_GROUP
    ) as conn:
        return _group_tree(conn)


@pytest.fixture(scope="module")
def small_tree() -> dict:
    with _tree_db(groups_per_module=3, docs_per_group=4) as conn:
        return _group_tree(conn)


class TestFlatGroupTreeAtIncidentScale:
    def test_the_fixture_is_at_least_the_size_the_incident_ran_at(self, load_tree):
        """Guards the guard: if this fixture ever shrinks, the tests below stop meaning much."""
        nodes = load_tree["nodes"]
        assert len(nodes) >= INCIDENT_UNIQUE_IDS, len(nodes)

        old = _pre_0449_shape(nodes)
        old_entries = _count_entries(old)
        old_bytes = len(json.dumps({"nodes": old}))
        assert old_entries >= INCIDENT_ENTRIES, old_entries
        assert old_bytes >= INCIDENT_BYTES, old_bytes
        # And the duplication factor is the live one (12,169 / 4,210 = 2.89x), not an
        # artefact of a fixture shaped differently from a real project.
        assert 2.7 <= old_entries / len(nodes) <= 3.1, old_entries / len(nodes)

    def test_every_id_is_serialized_exactly_once_at_load_scale(self, load_tree):
        nodes = load_tree["nodes"]
        ids = [node["id"] for node in nodes]
        assert len(ids) == len(set(ids))
        # The whole payload contains exactly as many dicts as there are nodes: no second copy
        # of anything, anywhere, at 5,885 nodes.
        assert _count_entries(load_tree["nodes"]) == len(nodes)

    def test_no_node_carries_children_at_load_scale(self, load_tree):
        assert [node["id"] for node in load_tree["nodes"] if "children" in node] == []

    def test_the_payload_shrinks_to_a_third_of_the_old_shape(self, load_tree):
        nodes = load_tree["nodes"]
        new_bytes = len(json.dumps(load_tree))
        old_bytes = len(json.dumps({"nodes": _pre_0449_shape(nodes)}))
        # 4.86 MB -> 1.71 MB measured. The refresh retry that NR0003 found so expensive is
        # paying for one of these now, not three.
        assert new_bytes < old_bytes / 2.5, (new_bytes, old_bytes)

    def test_bytes_per_node_does_not_climb_with_the_tree(self, small_tree, load_tree):
        """Linearity, measured across a 168x size step rather than a 1.7x one.

        Under the old shape this ratio climbed with depth, because each added document was
        written once in ``nodes`` and again inside its group. Flat, it is a constant.
        """
        small_per_node = len(json.dumps(small_tree)) / len(small_tree["nodes"])
        load_per_node = len(json.dumps(load_tree)) / len(load_tree["nodes"])
        assert load_per_node <= small_per_node * 1.15, (small_per_node, load_per_node)

    def test_the_hierarchy_still_reconstructs_and_has_no_cycles(self, load_tree):
        nodes = load_tree["nodes"]
        by_id = {node["id"]: node for node in nodes}
        roots = [node for node in nodes if node["parent_id"] is None]
        assert [node["node_type"] for node in roots] == ["project"]

        # Every node reaches the single root by walking parent_id, in bounded steps. That is
        # both "the hierarchy survives" and "there is no cycle" — item 3.4 asks for both.
        depth_of: dict[str, int] = {roots[0]["id"]: 0}

        def depth(node_id: str) -> int:
            chain: list[str] = []
            cursor = node_id
            while cursor not in depth_of:
                assert cursor not in chain, f"cycle through {cursor}"
                chain.append(cursor)
                parent = by_id[cursor]["parent_id"]
                assert parent in by_id, (cursor, parent)
                cursor = parent
            base = depth_of[cursor]
            for offset, item in enumerate(reversed(chain), start=1):
                depth_of[item] = base + offset
            return depth_of[node_id]

        assert max(depth(node["id"]) for node in nodes) == 3  # project > module > group > doc
        assert len(depth_of) == len(nodes)

    def test_the_ui_flags_the_toggle_reads_survive_at_load_scale(self, load_tree):
        """Criterion 3's other half: the final-approved / discarded display still works.

        These are the flags GroupExplorer's hide toggle acts on, so losing them at scale would
        reproduce the incident's "the group is gone" from the other direction.
        """
        groups = [node for node in load_tree["nodes"] if node["node_type"] == "group"]
        assert len(groups) == LOAD_GROUPS_PER_MODULE * 2
        assert all("is_final_approved" in group and "is_discarded" in group for group in groups)
        assert any(group["is_final_approved"] is True for group in groups)
        assert any(group["is_final_approved"] is False for group in groups)

        documents = [node for node in load_tree["nodes"] if node["node_type"] == "document"]
        assert len(documents) == LOAD_GROUPS_PER_MODULE * 2 * LOAD_DOCS_PER_GROUP + 1
        assert all(
            "type_code" in doc and "has_md" in doc and "md_path" in doc
            and "origin_provider_name" in doc and "origin_ai_run_id" in doc
            for doc in documents
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2/3. Return point lifecycle + advance head contract (real DB, real routes)
# ══════════════════════════════════════════════════════════════════════════════

class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        self._conn.execute("BEGIN")
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture
def rp_store(tmp_path, monkeypatch):
    db_path = tmp_path / "rp0449.db"
    mock_db = _MockDB(str(db_path))
    for migration in sorted(_SCHEMA_DIR.glob("*.sql")):
        mock_db._conn.executescript(migration.read_text(encoding="utf-8"))
    mock_db._conn.commit()

    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))
    # Token issuance is exercised for real on the N-head case, so it needs a real pepper.
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "t0449")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_t0449", "0449-test-pepper-value")

    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield storage_root
    conn_mod.STORE = original_store
    mock_db.close()


def _seed_workflow_group(storage_root: Path, suffix: str, *, realise_all: bool):
    """A B-rooted group whose sequence is D → L → T (→ N when not fully realised).

    ``realise_all=True`` reproduces the incident's own shape (Q240: 0444's sequence held only
    realised rows, every one approved), which makes the effective head None and the group head
    AC. ``realise_all=False`` leaves a trailing unrealised N row, so the head is that N.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = f"rp0449{suffix}"
    group_id = f"rp0449.default.{suffix}"
    user_id = f"usr_rp_{suffix}"
    projects.create({"project_id": project_id, "project_name": f"RP {suffix}"})
    users.create({
        "user_id": user_id,
        "username": f"rp{suffix}",
        "email": f"rp{suffix}@test.com",
        "password": "hashed",
    })
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "default",
        "title": f"Return point {suffix}",
    })

    ids = {"project_id": project_id, "group_id": group_id, "user_id": user_id}
    for seq, type_code, title in ((1, "B", "Root"), (4, "D", "Design"), (5, "L", "Logic"), (6, "T", "Task")):
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        path = storage_paths.document_path(
            project_id=project_id, group_code=group_id, doc_code=doc_code,
            filename="document.md", module="default",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {title}\n---\n# {title}\n", encoding="utf-8")
        db_docs.create({
            "doc_id": doc_id,
            "project_id": project_id,
            "module": "default",
            "group_id": group_id,
            "type_code": type_code,
            "seq": seq,
            "title": title,
            "owner_id": user_id,
            "file_path": storage_paths.to_storage_relative(path, project_id),
        })
        db_docs.update(
            doc_id,
            {"doc_review_status": "wf_in_progress" if type_code == "B" else "approved"},
        )
        ids[type_code] = doc_id

    db_wfseq.insert_sequence(ids["B"])
    sequence = db_wfseq.get_sequence_by_doc_id(ids["B"])
    step_types = ["D", "L", "T"] if realise_all else ["D", "L", "T", "N"]
    for order, type_code in enumerate(step_types, start=1):
        db_wfseq.insert_sequence_item(sequence["id"], order, type_code, type_code, "doc", order)
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        result_doc_id = ids.get(item["type"])
        if result_doc_id:
            db_wfseq.set_item_result_doc_id(item["id"], result_doc_id)
    ids["sequence_id"] = sequence["id"]
    return ids


def _approve(doc_id: str, actor: str):
    """Approve through the ordinary pipeline — the forward way home."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    return transition_document_review(
        doc_id=doc_id, action="approve", actor_user_id=actor,
        user_permissions={"document.approve"},
    )


class TestReturnPointHelpersHaveOneDefinition:
    """Item 5.1 — the router no longer carries its own copy of the rewind helpers.

    ``documents.py`` used to define ``_record_return_point`` / ``_return_point_payload`` /
    ``_group_workflow_root_doc`` verbatim alongside the service's, so a fix could land in one
    and miss the other. The names it still exposes must BE the service's functions.
    """

    def test_the_router_names_resolve_to_the_service_functions(self):
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service as service

        assert doc_routes._return_point_payload is service.return_point_payload
        assert doc_routes._group_workflow_root_doc is service.group_workflow_root_doc
        assert doc_routes._content_fingerprint is service.content_fingerprint

    def test_the_router_module_defines_none_of_them_itself(self):
        import ast

        source = (
            _SERVER_DIR / "modules" / "flow_gate" / "documents" / "routers" / "documents.py"
        ).read_text(encoding="utf-8")
        defined = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
        }
        assert "_record_return_point" not in defined
        assert "_return_point_payload" not in defined
        assert "_group_workflow_root_doc" not in defined
        assert "_content_fingerprint" not in defined
        assert "_normalise_markdown_for_fingerprint" not in defined
        # Positive control: the parse really did find this module's functions.
        assert "_parse_doc_workflow" in defined


class TestReturnPointClearedByForwardReapproval:
    def test_reapproving_the_last_snapshot_document_clears_the_return_point(self, rp_store):
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "fwd", realise_all=True)
        reopened = doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        assert reopened["return_point"]["exists"] is True
        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None
        snapshot_count = db_rp.count_docs(point["id"])
        assert snapshot_count == 3

        # Redo the work forward: re-approve each rewound step through the pipeline.
        _approve(ids["D"], ids["user_id"])
        assert db_rp.get_by_group(ids["group_id"]) is not None, "still one pending → keep"
        _approve(ids["L"], ids["user_id"])
        assert db_rp.get_by_group(ids["group_id"]) is not None, "still one pending → keep"

        _approve(ids["T"], ids["user_id"])

        # The ledger AND its snapshot rows are gone the moment nothing is pending.
        assert db_rp.get_by_group(ids["group_id"]) is None
        assert workflow_rework_service.return_point_payload(ids["group_id"]) == {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }
        # The snapshot rows go with it — db_rp.delete() removes them explicitly, because a
        # bare _execute does not run under the PRAGMA that would honour ON DELETE CASCADE.
        assert db_rp.count_docs(point["id"]) == 0

    def test_control_without_the_cleanup_hook_the_ledger_survives(self, rp_store, monkeypatch):
        """Positive control for the case above.

        Disable the hook pipeline_service calls and run the identical steps: the ledger stays,
        with ``exists=true / current_min_seq=null`` — exactly the E4 state NR0003 measured on
        the live group. So the assertion above is driven by the new cleanup, not by the
        fixture happening to leave no return point behind.
        """
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "ctrl", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        monkeypatch.setattr(
            workflow_rework_service, "clear_return_point_if_complete", lambda *_a, **_k: False
        )
        for type_code in ("D", "L", "T"):
            _approve(ids[type_code], ids["user_id"])

        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None, "the row survives — the deletion above came from the hook"
        assert db_rp.current_pending_min_seq(point["id"]) is None
        assert db_rp.count_docs(point["id"]) == 3

    def test_the_read_api_never_reports_an_exhausted_ledger_as_active(self, rp_store):
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "read", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        for type_code in ("D", "L", "T"):
            _approve(ids[type_code], ids["user_id"])

        payload = doc_routes.get_workflow_return_point(ids["B"], {"user_id": ids["user_id"]})
        assert payload["return_point"]["exists"] is False
        assert payload["return_point"]["current_min_seq"] is None

    def test_a_ledger_exhausted_before_the_hook_shipped_still_reads_as_absent(
        self, rp_store, monkeypatch
    ):
        """Item 5.4 — the read is defensive on its own, not just downstream of the cleanup.

        The write-boundary cleanup only fires on approvals that happen AFTER it ships. A group
        that was already sitting on an exhausted ledger — the incident group's own state — has
        its last approval in the past, so nothing will ever come along to clear it until the
        next rewind. Disabling the hook reproduces exactly that history: the row is still
        there, and the payload must still refuse to call it an active return point. It must
        also not delete it: a GET does not rewrite the ledger (item 5.4).
        """
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "legacy", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        monkeypatch.setattr(
            workflow_rework_service, "clear_return_point_if_complete", lambda *_a, **_k: False
        )
        for type_code in ("D", "L", "T"):
            _approve(ids[type_code], ids["user_id"])
        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None

        payload = doc_routes.get_workflow_return_point(ids["B"], {"user_id": ids["user_id"]})
        assert payload["return_point"] == {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }
        # Read-only: the ledger and its snapshot rows are untouched by having been looked at.
        assert db_rp.get_by_group(ids["group_id"]) is not None
        assert db_rp.count_docs(point["id"]) == 3

    def test_a_live_rewind_is_still_reported_as_active(self, rp_store):
        """Negative control for the defence above: it keys on EXHAUSTED, not on "any ledger"."""
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "live", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        _approve(ids["D"], ids["user_id"])  # one down, two still pending

        payload = doc_routes.get_workflow_return_point(ids["B"], {"user_id": ids["user_id"]})
        assert payload["return_point"]["exists"] is True
        assert payload["return_point"]["current_min_seq"] == 5
        assert payload["return_point"]["front_seq"] == 6

    def test_a_never_populated_ledger_keeps_its_front(self, rp_store):
        """The defence is scoped to a WALKED-OUT-OF ledger.

        An empty snapshot has ``current_min_seq is None`` too, but for the opposite reason: it
        was never filled rather than exhausted, and restore still has its front_seq to aim at.
        0291's ``front_label`` contract reads exactly this shape, so the two must stay apart.
        """
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "empty", realise_all=True)
        db_rp.ensure(ids["group_id"], 6, "wf_done")
        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None and db_rp.count_docs(point["id"]) == 0

        payload = workflow_rework_service.return_point_payload(ids["group_id"])
        assert payload["exists"] is True
        assert payload["front_seq"] == 6
        assert payload["restorable_count"] == 0

    def test_a_partially_reapproved_snapshot_keeps_its_baseline(self, rp_store):
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "part", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        _approve(ids["D"], ids["user_id"])

        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None
        assert point["front_seq"] == 6
        assert db_rp.current_pending_min_seq(point["id"]) == 5

    def test_a_nested_rewind_preserves_the_first_baseline_and_root_prev_status(self, rp_store):
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "nest", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=6),
            {"user_id": ids["user_id"]},
        )
        first = db_rp.get_by_group(ids["group_id"])
        assert first is not None
        first_root_prev = first["root_prev_status"]

        # A nested rewind, reaching further back while the first is still live.
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        nested = db_rp.get_by_group(ids["group_id"])
        assert nested is not None
        assert nested["id"] == first["id"]
        assert nested["root_prev_status"] == first_root_prev

        # Approving one of the nested range's documents must NOT collapse the capture while
        # others are still pending.
        _approve(ids["D"], ids["user_id"])
        assert db_rp.get_by_group(ids["group_id"]) is not None
        assert db_rp.get_by_group(ids["group_id"])["root_prev_status"] == first_root_prev

    def test_approving_a_never_snapshotted_type_does_not_decide_the_rewind_is_over(self, rp_store):
        """Item 5.3 — M/AC/Q/root approvals are on their own schedule.

        The M below is not in the snapshot (it is an auto-complete type), so approving it must
        not be the event that clears a live ledger.
        """
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "memo", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        assert db_rp.get_by_group(ids["group_id"]) is not None

        memo_id = f"{ids['group_id']}.0007-M"
        db_docs.create({
            "doc_id": memo_id,
            "project_id": ids["project_id"],
            "module": "default",
            "group_id": ids["group_id"],
            "type_code": "M",
            "seq": 7,
            "title": "Memo",
            "owner_id": ids["user_id"],
            "file_path": None,
        })
        memo = db_docs.get_by_id(memo_id)
        assert workflow_rework_service.clear_return_point_if_complete(ids["group_id"], memo) is False
        assert db_rp.get_by_group(ids["group_id"]) is not None

    def test_an_approved_A_answer_does_not_decide_the_rewind_is_over(self, rp_store):
        """0449 TR0005 rev1 — the same item 5.3 rule, for the type that was left out.

        rev0 spelled the non-restore set out by hand as ``{"Q", "AC"} | AUTO_COMPLETE_TYPES``
        and so covered Q, AC, M and CH but not **A**, even though the shared
        ``documents.constants.NON_SLOT_WORKFLOW_TYPES`` has always listed A alongside Q. An A
        is the answer to a Q: it sits outside ``workflow_sequence_items`` exactly as its Q
        does, and it is approved on the answerer's schedule, so it must not be the approval
        that decides a live rewind is finished. rev0's test covered M only.
        """
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "answer", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None

        # Drive the ledger to EXHAUSTED without going through the pipeline, so the type check
        # is the only thing left that can stop a clear. Approving through _approve() would fire
        # the cleanup hook itself and delete the ledger before the A is ever considered — the
        # test would then pass for the wrong reason, which is how rev0's M-only version could
        # not have caught this either way.
        for type_code in ("D", "L", "T"):
            db_docs.update(ids[type_code], {"doc_review_status": "approved"})
        assert db_rp.current_pending_min_seq(point["id"]) is None

        answer_id = f"{ids['group_id']}.0008-A"
        db_docs.create({
            "doc_id": answer_id,
            "project_id": ids["project_id"],
            "module": "default",
            "group_id": ids["group_id"],
            "type_code": "A",
            "seq": 8,
            "title": "Answer",
            "owner_id": ids["user_id"],
            "file_path": None,
        })
        answer = db_docs.get_by_id(answer_id)
        assert workflow_rework_service.clear_return_point_if_complete(ids["group_id"], answer) is False
        assert db_rp.get_by_group(ids["group_id"]) is not None

        # Positive control: the ledger really was clearable — a restorable type does clear it,
        # so the False above is the A being refused, not an inert ledger.
        step = db_docs.get_by_id(ids["T"])
        assert workflow_rework_service.clear_return_point_if_complete(ids["group_id"], step) is True
        assert db_rp.get_by_group(ids["group_id"]) is None

    def test_the_non_restore_set_is_derived_from_the_shared_constant(self):
        """Pins the two together so they cannot drift apart again.

        The omission was possible only because the set was re-spelled here instead of taken
        from the one place that already answers "is this type outside the slots".
        """
        from modules.flow_gate.documents.constants import NON_SLOT_WORKFLOW_TYPES
        from modules.flow_gate.services import workflow_rework_service

        non_restore = set(workflow_rework_service._RETURN_POINT_NON_RESTORE_TYPES)
        assert NON_SLOT_WORKFLOW_TYPES <= non_restore
        assert "A" in non_restore
        # The root types are the only thing this set adds on top.
        assert non_restore - set(NON_SLOT_WORKFLOW_TYPES) == workflow_rework_service.WORKFLOW_ROOT_TYPES

    def test_a_rewind_without_a_sequence_does_not_sweep_an_A_into_the_snapshot(self, rp_store):
        """The legacy fallback, which is where the omission actually bit.

        When the group has no decided sequence, ``workflow_step_doc_ids`` returns None and the
        rewind falls back to a raw ``seq >= target`` filter over the group's documents. With A
        missing from the non-restore set that filter swept an approved answer into the
        snapshot and reopened it to ``pending_review`` — a document that occupies no slot,
        re-opened as though it were a workflow step.
        """
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.documents.routers import documents as doc_routes
        from modules.flow_gate.services import workflow_rework_service

        ids = _seed_workflow_group(rp_store, "nosequence", realise_all=True)

        # Drop the sequence so the legacy branch is the one under test, and prove it.
        conn_mod.STORE._execute(
            "DELETE FROM workflow_sequence_items WHERE sequence_id = ?", [ids["sequence_id"]]
        )
        conn_mod.STORE._execute(
            "DELETE FROM workflow_sequences WHERE id = ?", [ids["sequence_id"]]
        )
        root = db_docs.get_by_id(ids["B"])
        assert workflow_rework_service.workflow_step_doc_ids(root) is None

        answer_id = f"{ids['group_id']}.0005-A"
        db_docs.create({
            "doc_id": answer_id,
            "project_id": ids["project_id"],
            "module": "default",
            "group_id": ids["group_id"],
            "type_code": "A",
            "seq": 5,  # inside the rewound range: target_seq=4 and the T sits at 6
            "title": "Answer",
            "owner_id": ids["user_id"],
            "file_path": None,
        })
        db_docs.update(answer_id, {"doc_review_status": "approved"})

        result = doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )

        # Not reopened…
        assert answer_id not in result["reopened"]
        assert db_docs.get_by_id(answer_id)["doc_review_status"] == "approved"
        # …and not in the snapshot the ledger would later restore.
        point = db_rp.get_by_group(ids["group_id"])
        assert point is not None
        snapshot_ids = {
            row["doc_id"]
            for row in conn_mod.STORE._fetch_all(
                "SELECT doc_id FROM workflow_return_point_docs WHERE return_point_id = ?",
                [point["id"]],
            )
        }
        assert answer_id not in snapshot_ids
        # Positive control: the real steps in the same range DID go back, so the assertions
        # above are about A being excluded, not about the rewind doing nothing.
        assert ids["D"] in result["reopened"] and ids["T"] in result["reopened"]

    def test_restore_to_front_still_works_when_nothing_was_reapproved(self, rp_store):
        """The restore path's own delete/root-status contract is untouched (item 5.3)."""
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import workflow_return_points as db_rp
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "restore", realise_all=True)
        doc_routes.reopen_workflow(
            doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
            {"user_id": ids["user_id"]},
        )
        restored = doc_routes.restore_workflow(
            doc_routes._RestoreBody(doc_id=ids["B"], destination_seq=None),
            {"user_id": ids["user_id"]},
        )
        assert restored["reached_front"] is True
        assert restored["return_point_cleared"] is True
        assert restored["restored"] == [ids["D"], ids["L"], ids["T"]]
        assert db_rp.get_by_group(ids["group_id"]) is None
        assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"


class _FakeRequest:
    def __init__(self, locale: str = "ko", correlation: str | None = None):
        self.headers = {"x-locale": locale}
        if correlation:
            self.headers["x-request-id"] = correlation
        self.base_url = "http://localhost/"


def _advance(ids: dict, monkeypatch, correlation: str | None = None):
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

    monkeypatch.setattr(wdr, "verify_bearer", lambda _r: {"issued_to": ids["user_id"], "_is_user_jwt": True})
    return wdr.post_workflow_advance_rpc(
        wdr.AdvanceBodyRequest(doc_id=ids["B"]),
        _FakeRequest(correlation=correlation),
    )


def _token_issued_events(group_id: str) -> int:
    from modules.flow_gate.db.connection import get_store

    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM workflow_events WHERE event_type = 'token_issued' AND group_id = ?",
        [group_id],
    )
    return int((row or {}).get("n") or 0)


def _leave_a_spent_return_point(ids: dict):
    """A ledger whose snapshot is fully re-approved — the E4 leftover, planted directly.

    Written through the db layer rather than by running a rewind, so the group's documents
    stay approved and the ONLY difference between the two arms of the cross below is the
    presence of the ledger row.
    """
    from modules.flow_gate.db import workflow_return_points as db_rp

    point = db_rp.ensure(ids["group_id"], 6, "wf_in_progress")
    db_rp.add_doc_if_absent(
        return_point_id=point["id"], doc_id=ids["T"], seq=6,
        prev_status="approved", fingerprint="!" * 64,
    )
    return point


class TestAdvanceHeadContract:
    def test_an_unrealised_N_head_still_advances_and_issues_a_token(self, rp_store, monkeypatch):
        ids = _seed_workflow_group(rp_store, "nhead", realise_all=False)

        response = _advance(ids, monkeypatch)

        assert response.status_code == 201, response.body
        payload = json.loads(response.body)
        assert payload["token"]
        assert payload["action_scope"] == "new"
        assert _token_issued_events(ids["group_id"]) == 1

    def test_a_lingering_return_point_does_not_change_the_N_head_result(self, rp_store, monkeypatch):
        ids = _seed_workflow_group(rp_store, "nheadrp", realise_all=False)
        _leave_a_spent_return_point(ids)

        response = _advance(ids, monkeypatch)

        assert response.status_code == 201, response.body
        assert _token_issued_events(ids["group_id"]) == 1

    def test_an_AC_head_refuses_advance_with_sequence_exhausted(self, rp_store, monkeypatch):
        """The incident's own shape (Q240): every sequence row realised and approved.

        409 here is the CONTRACT, not the defect — advance is not the endpoint that
        progresses an AC head. The client branch that never calls it is pinned on the
        client side (MainPanel.acFinalApprovalRouting.0449.spec.ts).
        """
        from modules.flow_gate.db import workflow_sequences as db_wfseq

        ids = _seed_workflow_group(rp_store, "achead", realise_all=True)
        assert db_wfseq.get_effective_head(ids["sequence_id"]) is None

        response = _advance(ids, monkeypatch)

        assert response.status_code == 409
        assert json.loads(response.body)["error"] == "sequence_exhausted"
        assert _token_issued_events(ids["group_id"]) == 0

    def test_a_lingering_return_point_does_not_change_the_AC_head_result_either(self, rp_store, monkeypatch):
        from modules.flow_gate.db import workflow_sequences as db_wfseq

        ids = _seed_workflow_group(rp_store, "acheadrp", realise_all=True)
        _leave_a_spent_return_point(ids)
        assert db_wfseq.get_effective_head(ids["sequence_id"]) is None

        response = _advance(ids, monkeypatch)

        assert response.status_code == 409
        assert json.loads(response.body)["error"] == "sequence_exhausted"
        assert _token_issued_events(ids["group_id"]) == 0

    def test_final_approval_is_the_endpoint_that_does_progress_an_AC_head(self, rp_store):
        from modules.flow_gate.documents.routers import documents as doc_routes

        ids = _seed_workflow_group(rp_store, "acfinal", realise_all=True)

        created = doc_routes.open_final_approval(
            doc_routes._GroupDocRef(doc_id=ids["B"]),
            {"user_id": ids["user_id"]},
        )

        assert created["doc_id"].endswith("-AC")
        # Idempotent: asking again reuses the same un-approved AC rather than minting a new one.
        again = doc_routes.open_final_approval(
            doc_routes._GroupDocRef(doc_id=ids["B"]),
            {"user_id": ids["user_id"]},
        )
        assert again["doc_id"] == created["doc_id"]
        assert _token_issued_events(ids["group_id"]) == 0


class TestAdvanceRouteLogging:
    def test_a_refusal_is_recorded_with_its_code_and_a_correlation_id(self, rp_store, monkeypatch, caplog):
        ids = _seed_workflow_group(rp_store, "log409", realise_all=True)

        with caplog.at_level(logging.INFO, logger="modules.flow_gate.api.v1.workflow_decision_routes"):
            response = _advance(ids, monkeypatch, correlation="cid-0449-a")

        assert response.status_code == 409
        lines = [r.getMessage() for r in caplog.records]
        assert any("event=received" in line and "cid=cid-0449-a" in line for line in lines), lines
        refusal = [line for line in lines if "event=refused" in line]
        assert refusal, lines
        assert "code=sequence_exhausted" in refusal[0]
        assert "status=409" in refusal[0]
        assert f"doc_id={ids['B']}" in refusal[0]
        assert f"group_id={ids['group_id']}" in refusal[0]

    def test_a_success_is_recorded_and_carries_no_token_material(self, rp_store, monkeypatch, caplog):
        ids = _seed_workflow_group(rp_store, "log201", realise_all=False)

        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-b")

        assert response.status_code == 201
        payload = json.loads(response.body)
        lines = [r.getMessage() for r in caplog.records]
        assert any("event=advanced" in line and "status=201" in line for line in lines), lines
        # The raw token and the worker mention are the two things that must never be logged.
        joined = "\n".join(lines)
        assert payload["token"] not in joined
        assert payload["mention"][:60] not in joined
        assert "0449-test-pepper-value" not in joined


class TestAdvanceUnexpected500IsRecorded:
    """0449 TR0005 rev1 — item 6.1's fourth case, at every point it can happen.

    rev0 opened the ``failed`` guard around the ``advance_workflow()`` call alone. Everything
    before it — normalising the instruction mode, the ``_db_documents.get_by_id()`` lookup, the
    disposed-group and active-run checks — ran outside any guard, and the auto-approve
    validation caught only ``ValueError``. An exception from any of those ended the request as
    a 500 with no ``received``/``failed`` pair, which is precisely the gap the incident
    investigation ran into. The whole handler is inside one guard now, and ``received`` is
    written before the first of those statements rather than after them.

    Each case below breaks ONE of those points and asserts the same three things: the response
    is a structured 500, a ``received`` line exists, and a ``failed`` line exists carrying the
    same correlation id.
    """

    @staticmethod
    def _route_lines(caplog):
        return [r.getMessage() for r in caplog.records if r.getMessage().startswith("[route]")]

    def _assert_pair(self, caplog, response, cid: str):
        assert response.status_code == 500, response.body
        body = json.loads(response.body)
        assert body["error"] == "internal_error"

        lines = self._route_lines(caplog)
        received = [ln for ln in lines if "event=received" in ln and f"cid={cid}" in ln]
        failed = [ln for ln in lines if "event=failed" in ln and f"cid={cid}" in ln]
        assert received, lines
        assert failed, lines
        assert "status=500" in failed[0] and "code=internal_error" in failed[0]
        # The refusal vocabulary is not reused for a fault, so the two stay searchable apart.
        assert not [ln for ln in lines if "event=refused" in ln and f"cid={cid}" in ln]

    def test_a_failure_in_the_document_lookup_still_leaves_the_pair(
        self, rp_store, monkeypatch, caplog
    ):
        """The lookup rev0 ran BEFORE it wrote anything at all."""
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500lookup", realise_all=False)

        def _boom(_doc_id):
            raise RuntimeError("documents table unavailable")

        monkeypatch.setattr(wdr._db_documents, "get_by_id", _boom)
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500a")

        self._assert_pair(caplog, response, "cid-0449-500a")

    def test_a_failure_in_the_instruction_mode_normalisation_still_leaves_the_pair(
        self, rp_store, monkeypatch, caplog
    ):
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500mode", realise_all=False)
        monkeypatch.setattr(
            wdr, "normalize_continuation_instruction_mode",
            lambda _v: (_ for _ in ()).throw(RuntimeError("mode registry unavailable")),
        )
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500b")

        self._assert_pair(caplog, response, "cid-0449-500b")

    def test_a_failure_in_the_disposed_group_check_still_leaves_the_pair(
        self, rp_store, monkeypatch, caplog
    ):
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500disposed", realise_all=False)
        monkeypatch.setattr(
            wdr, "_disposed_group_response",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("group service unavailable")),
        )
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500c")

        self._assert_pair(caplog, response, "cid-0449-500c")

    def test_a_non_ValueError_from_the_auto_approve_validation_still_leaves_the_pair(
        self, rp_store, monkeypatch, caplog
    ):
        """rev0's ``except ValueError`` let anything else past it unrecorded."""
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500approve", realise_all=False)
        monkeypatch.setattr(
            wdr, "normalize_continuation_auto_approve_item_seqs",
            lambda _v: (_ for _ in ()).throw(TypeError("not a sequence")),
        )
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500d")

        self._assert_pair(caplog, response, "cid-0449-500d")

    def test_a_failure_inside_advance_workflow_is_recorded_the_same_way(
        self, rp_store, monkeypatch, caplog
    ):
        """The one case rev0 did cover — kept, so the widening did not lose it."""
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500core", realise_all=False)
        monkeypatch.setattr(
            wdr, "advance_workflow",
            lambda **_k: (_ for _ in ()).throw(RuntimeError("token mint unavailable")),
        )
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500e")

        self._assert_pair(caplog, response, "cid-0449-500e")

    def test_a_500_carries_no_token_or_document_material(self, rp_store, monkeypatch, caplog):
        """Item 6.2 holds on the failure path too, not just the happy one."""
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500leak", realise_all=False)
        monkeypatch.setattr(
            wdr, "advance_workflow",
            lambda **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with caplog.at_level(logging.INFO):
            _advance(ids, monkeypatch, correlation="cid-0449-500f")

        for line in self._route_lines(caplog):
            assert "token=" not in line
            assert "mention" not in line.lower()
            assert "Root" not in line and "Design" not in line  # document titles

    def test_a_refusal_is_still_a_refusal_after_the_widening(self, rp_store, monkeypatch, caplog):
        """Negative control: the guard must not relabel ordinary 409s as faults."""
        ids = _seed_workflow_group(rp_store, "log500ctl", realise_all=True)

        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-500g")

        assert response.status_code == 409
        lines = self._route_lines(caplog)
        assert [ln for ln in lines if "event=refused" in ln and "code=sequence_exhausted" in ln]
        assert not [ln for ln in lines if "event=failed" in ln]


class TestTokenIssueRouteLogging:
    def test_route_logging_only_accepts_the_declared_fields(self):
        """The formatter has no parameter a secret could arrive through (item 6.2)."""
        import inspect

        from modules.flow_gate.services import route_logging

        params = inspect.signature(route_logging.log_route_event).parameters
        assert set(params) == {
            "logger", "endpoint", "event", "status", "code",
            "doc_id", "group_id", "token_id", "correlation_id", "fault", "level",
        }
        # `fault` is the one field a 500 adds (rev2). It is only ever fed
        # exception_signature()'s output, and that function is what keeps a message out —
        # see TestNoTracebackStepsAroundTheDeclaredFields.
        assert "fault" in params

    def test_token_issue_records_arrival_and_final_status(self, monkeypatch, caplog):
        from modules.flow_gate.api import token_routes as tr
        from modules.flow_gate.services import route_logging

        body = tr.TokenIssueRequest(
            project="p0449", module="default", group="0449",
            action_scope="edit", doc_ref="p0449.default.0449.0001-B",
        )
        response = tr.TokenIssueResponse(
            ok=True, raw_token="RAW-SECRET-TOKEN", token_id="tok_0449",
            expires_at="2026-08-22T00:00:00+09:00", scratch_dir="C:/scratch/tok_0449",
            action_scope="edit", doc_ref=body.doc_ref, group_id="p0449.default.0449",
            mention="## Mention body that must not be logged",
        )
        monkeypatch.setattr(tr, "_issue_token", lambda *_a, **_k: response)

        with caplog.at_level(logging.INFO, logger="modules.flow_gate.api.token_routes"):
            out = tr.issue_token(body, _FakeRequest(correlation="cid-0449-c"), {"user_id": "u"})

        assert out is response
        lines = [r.getMessage() for r in caplog.records]
        assert any("event=received" in line and route_logging.TOKEN_ISSUE_ENDPOINT in line for line in lines), lines
        issued = [line for line in lines if "event=issued" in line]
        assert issued, lines
        assert "status=200" in issued[0]
        assert "token_id=tok_0449" in issued[0]
        assert "cid=cid-0449-c" in issued[0]
        joined = "\n".join(lines)
        assert "RAW-SECRET-TOKEN" not in joined
        assert "Mention body" not in joined

    def test_token_issue_records_a_refusal_without_reshaping_it(self, monkeypatch, caplog):
        from fastapi import HTTPException

        from modules.flow_gate.api import token_routes as tr

        body = tr.TokenIssueRequest(project="p0449", module="default", group="0449")

        def _boom(*_a, **_k):
            raise HTTPException(status_code=403, detail="Token issuance permission denied")

        monkeypatch.setattr(tr, "_issue_token", _boom)

        with caplog.at_level(logging.INFO, logger="modules.flow_gate.api.token_routes"):
            with pytest.raises(HTTPException) as excinfo:
                tr.issue_token(body, _FakeRequest(), {"user_id": "u"})

        # Re-raised untouched: the wire contract did not change, only the record did.
        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Token issuance permission denied"
        refusal = [r.getMessage() for r in caplog.records if "event=refused" in r.getMessage()]
        assert refusal
        assert "status=403" in refusal[0]


class TestNoTracebackStepsAroundTheDeclaredFields:
    """0449 TR0005 rev2 — the 500 paths must not write a record the field list cannot see.

    rev1 wrote the restricted ``failed`` line and then called ``logger.exception(...)`` right
    after it. That second call emits a SEPARATE record whose ``exc_info`` the formatter expands
    into the full traceback plus ``str(exc)``. So a helper that interpolates a secret into its
    own error message — a raw token it was minting, a mention body it was rendering — had that
    secret copied verbatim into ``logs/default.log``, *around* :func:`log_route_event`'s closed
    parameter list rather than through it. rev1's leak test could not see it either: it read
    ``record.getMessage()``, which never contains the traceback.

    These tests read what the log FILE would receive — ``Formatter().format(record)``, which
    expands ``exc_info`` — for every record, not just the ``[route]`` ones.
    """

    SECRET = "fgt_live_RAW-TOKEN-0449-DO-NOT-LOG"

    @staticmethod
    def _formatted(caplog):
        """Every record as the file handler would write it, traceback included."""
        formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
        return [formatter.format(r) for r in caplog.records]

    def test_an_advance_500_whose_cause_carries_a_secret_logs_no_traceback(
        self, rp_store, monkeypatch, caplog
    ):
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500tb", realise_all=False)

        def _boom(**_k):
            raise RuntimeError(f"token mint rejected credential {self.SECRET}")

        monkeypatch.setattr(wdr, "advance_workflow", _boom)
        with caplog.at_level(logging.INFO):
            response = _advance(ids, monkeypatch, correlation="cid-0449-tb1")

        assert response.status_code == 500
        blob = "\n".join(self._formatted(caplog))
        assert self.SECRET not in blob, blob
        assert "Traceback (most recent call last)" not in blob, blob
        assert not [r for r in caplog.records if r.exc_info], "no record may carry exc_info"

    def test_a_token_issue_500_whose_cause_carries_a_secret_logs_no_traceback(
        self, monkeypatch, caplog
    ):
        from modules.flow_gate.api import token_routes as tr

        body = tr.TokenIssueRequest(project="p0449", module="default", group="0449")

        def _boom(*_a, **_k):
            raise RuntimeError(f"token mint rejected credential {self.SECRET}")

        monkeypatch.setattr(tr, "_issue_token", _boom)

        with caplog.at_level(logging.INFO, logger="modules.flow_gate.api.token_routes"):
            with pytest.raises(RuntimeError):
                tr.issue_token(body, _FakeRequest(correlation="cid-0449-tb2"), {"user_id": "u"})

        blob = "\n".join(self._formatted(caplog))
        assert self.SECRET not in blob, blob
        assert "Traceback (most recent call last)" not in blob, blob
        assert not [r for r in caplog.records if r.exc_info], "no record may carry exc_info"

    def test_a_token_issue_500_reaching_the_asgi_boundary_carries_no_secret(self, monkeypatch):
        """rev3's test above calls ``issue_token()`` directly, so it never crosses the boundary
        a real 500 actually crosses: Starlette's ``ServerErrorMiddleware`` sends the generic
        response and then unconditionally re-raises ("We always continue to raise the
        exception... allows servers to log the error"), and Uvicorn's ``run_asgi`` catches
        that at the ASGI-server edge with ``self.logger.error(msg, exc_info=exc)`` — a logger
        this module does not own and cannot route through
        :func:`route_logging.log_route_event`'s closed field list.

        This drives the request through a real ASGI app (the same minimal-app-plus-real-router
        pattern as ``test_git_service_error_envelope_0233.py``) with
        ``TestClient(raise_server_exceptions=True)``, whose transport re-raises whatever
        escaped the app — the exact object ``run_asgi`` would hand to ``exc_info=exc`` in
        production. It is then formatted the same way ``logging``'s ``exc_info`` handling
        formats it (``traceback.format_exception``), and the secret must not be in that text.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from modules.flow_gate.api import token_routes as tr
        from modules.flow_gate.auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(tr.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u"}

        def _boom(*_a, **_k):
            raise RuntimeError(f"token mint rejected credential {self.SECRET}")

        monkeypatch.setattr(tr, "_issue_token", _boom)
        client = TestClient(app, raise_server_exceptions=True)

        with pytest.raises(Exception) as excinfo:
            client.post(
                "/api/v1/token/issue",
                json={"project": "p0449", "module": "default", "group": "0449"},
            )

        escaped = excinfo.value
        # The original RuntimeError (carrying the secret) must not be what escapes the app.
        assert type(escaped) is tr.TokenIssueInternalError, escaped
        assert self.SECRET not in str(escaped)

        # What Uvicorn's `run_asgi` would actually hand to the global server log.
        formatted = "".join(
            traceback.format_exception(type(escaped), escaped, escaped.__traceback__)
        )
        assert self.SECRET not in formatted, formatted
        assert "token mint rejected credential" not in formatted, formatted
        # `from None` in the route: no chained cause/context to print the original into view.
        assert escaped.__cause__ is None
        assert escaped.__suppress_context__ is True

    def test_the_failed_line_still_says_what_broke_and_where(
        self, rp_store, monkeypatch, caplog
    ):
        """Leak-proof is not the same as blind: the type and the code coordinates survive.

        Positive control for the two tests above — without this one, deleting the diagnostic
        entirely would also make them green.
        """
        from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

        ids = _seed_workflow_group(rp_store, "log500sig", realise_all=False)

        def _boom(**_k):
            raise RuntimeError(f"token mint rejected credential {self.SECRET}")

        monkeypatch.setattr(wdr, "advance_workflow", _boom)
        with caplog.at_level(logging.INFO):
            _advance(ids, monkeypatch, correlation="cid-0449-tb3")

        failed = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("[route]") and "event=failed" in r.getMessage()
        ]
        assert failed, [r.getMessage() for r in caplog.records]
        assert "fault=" in failed[0]
        assert "RuntimeError" in failed[0]
        # The frame that raised is named, so a triager still lands on the code.
        assert "test_explorer_tree_and_return_point_0449.py:" in failed[0]
        assert self.SECRET not in failed[0]

    def test_the_signature_helper_reports_types_and_coordinates_only(self):
        from modules.flow_gate.services import route_logging

        try:
            try:
                raise ValueError(f"inner secret {self.SECRET}")
            except ValueError as inner:
                raise RuntimeError(f"outer secret {self.SECRET}") from inner
        except RuntimeError as exc:
            signature = route_logging.exception_signature(exc)

        assert "RuntimeError" in signature
        assert "ValueError" in signature          # the chained cause is named too
        assert self.SECRET not in signature
        assert "secret" not in signature
        assert " " not in signature               # one space-joined field, never two
        assert "\n" not in signature
        assert "test_explorer_tree_and_return_point_0449.py:" in signature
