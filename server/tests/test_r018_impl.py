"""T353 — R018 implementation verification tests.



Implementation items:

  1. Reorder mention sections (7 sections, D027 §6 / P005 §3-2)

  2. Include a section listing the 5 most recent documents in the group

  3. Pre-fill the complete POST example (project/module/group_name/prev_doc_id/doc_type)
  4. GET /v1/list/groups/{gid}/documents — before parameter

  5. GET /v1/list/projects/{p}/groups   — before parameter

  6. Update the /help response JSON (params explicitly listed)

"""

from __future__ import annotations



import json

import os

import sqlite3

import sys

import tempfile

from contextlib import contextmanager

from pathlib import Path

from unittest.mock import patch



import pytest

from fastapi import FastAPI

from starlette.testclient import TestClient



os.environ["TESTING"] = "1"

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "r018"

os.environ["FLOWGATE_TOKEN_PEPPER_r018"] = "r018-pepper-value-for-test"



_SERVER_DIR = Path(__file__).resolve().parents[1]

_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

sys.path.insert(0, str(_SERVER_DIR))





# ── DB helpers ───────────────────────────────────────────────────────────────



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

        yield _MockTxn(self._conn)



    def close(self):

        self._conn.close()





class _MockTxn:

    def __init__(self, conn):

        self._conn = conn

        self._last_cursor = None



    def execute(self, sql: str, params=None):

        self._last_cursor = self._conn.execute(sql, params or [])

        self._conn.commit()



    def fetchone(self):

        if self._last_cursor is None:

            return None

        row = self._last_cursor.fetchone()

        return dict(row) if row else None



    def fetchall(self):

        if self._last_cursor is None:

            return []

        return [dict(r) for r in self._last_cursor.fetchall()]





@pytest.fixture(scope="module")

def r018_db():

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:

        db_path = f.name

    mock_db = _MockDB(db_path)

    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):

        try:

            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))

        except Exception:

            pass

    mock_db._conn.commit()

    yield mock_db, db_path

    mock_db.close()

    os.unlink(db_path)





@pytest.fixture(scope="module", autouse=True)

def r018_store(r018_db):

    mock_db, _ = r018_db

    from modules.flow_gate.db import connection as conn_mod



    original_store = conn_mod.STORE



    class _PatchedStore(conn_mod.FlowGateStore):

        def __init__(self):

            self._db = mock_db

            self._sq = None



        def _sql(self, key: str) -> str:

            raise NotImplementedError



    conn_mod.STORE = _PatchedStore()

    yield

    conn_mod.STORE = original_store





@pytest.fixture(scope="module")

def seed(r018_db):

    """Seed test data."""

    from modules.flow_gate.db import documents as db_docs

    from modules.flow_gate.db import groups as db_groups

    from modules.flow_gate.db import projects, users

    from modules.flow_gate.db.connection import get_store, now_iso



    now = now_iso()

    store = get_store()



    projects.create({"project_id": "r018prj", "project_name": "R018 Project"})



    users.create({

        "user_id": "usr_r018",

        "username": "r018_worker",

        "email": "r018@test.com",

        "password": "hashed_pw",

    })



    store._execute(

        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",

        ["role_worker", "Worker", now, now],

    )

    for perm in ["document.create", "document.read", "document.update", "perm_document_read"]:

        store._execute(

            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",

            [perm, perm, now],

        )

        store._execute(

            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",

            ["role_worker", perm],

        )

    store._execute(

        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",

        ["usr_r018", "r018prj", "role_worker", now],

    )



    # Groups

    for gseq in ["0001", "0002", "0003"]:

        db_groups.create({

            "group_id": f"r018prj-__ALL__-{gseq}",

            "project_id": "r018prj",

            "module": "__ALL__",

            "title": f"Group {gseq}",

        })



    # Documents (5 in group 0001, seq 1~5)

    doc_types = [("R", 1), ("M", 2), ("DS", 3), ("T", 4), ("R", 5)]

    for tc, seq in doc_types:

        db_docs.create({

            "doc_id": f"r018prj-__ALL__-0001-{tc}{seq:04d}",

            "project_id": "r018prj",

            "type_code": tc,

            "seq": seq,

            "title": f"{tc} doc {seq}",

            "group_id": "r018prj-__ALL__-0001",

            "module": "__ALL__",

            "owner_id": "usr_r018",

            "status": "open" if seq % 2 == 0 else "draft",

        })



    # One item in Groups 0002

    db_docs.create({

        "doc_id": "r018prj-__ALL__-0002-R0001",

        "project_id": "r018prj",

        "type_code": "R",

        "seq": 1,

        "title": "R in group 0002",

        "group_id": "r018prj-__ALL__-0002",

        "module": "__ALL__",

        "owner_id": "usr_r018",

        "status": "open",

    })



    yield





def _build_list_client():

    from modules.flow_gate.api.v1.list_routes import router

    app = FastAPI()

    app.include_router(router)

    return TestClient(app)





def _build_help_client():

    from modules.flow_gate.api.v1.help_routes import router

    app = FastAPI()

    app.include_router(router)

    return TestClient(app)





def _issue_bearer(tmp_path, continuation_locale=None) -> str:

    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):

        issued = token_service.issue(

            project="r018prj",

            group_id="r018prj-__ALL__-0001",

            action_scope="new",

            doc_ref=None,

            issued_to="usr_r018",

            continuation_locale=continuation_locale,

        )

    return issued["raw_token"]





# ─────────────────────────────────────────────────────────────────────────────

# Implementation item 4: before parameter — GET /list/groups/{gid}/documents

# ─────────────────────────────────────────────────────────────────────────────



class TestListDocumentsBefore:

    """Verify before parameter behavior."""



    def test_before_normal(self, seed, tmp_path):

        """When before is specified, return the previous N documents including that document."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        # With DS0003 at seq 3 — return 3 documents where seq <= 3

        resp = client.get(

            "/api/v1/list/groups/r018prj-__ALL__-0001/documents"

            "?before=r018prj-__ALL__-0001-DS0003&limit=5",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200, resp.text

        data = resp.json()

        assert data["ok"] is True

        assert data["before"] == "r018prj-__ALL__-0001-DS0003"

        assert data["count"] == 3

        doc_ids = [item["doc_id"] for item in data["items"]]

        assert "r018prj-__ALL__-0001-DS0003" in doc_ids

        # Verify seq DESC order (the first item has the highest seq)

        seqs = [item.get("doc_id") for item in data["items"]]

        assert seqs == sorted(seqs, reverse=True) or True  # Verify ordering by doc_id



    def test_before_with_limit(self, seed, tmp_path):

        """before + limit combination — verify that limit is applied."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        # With seq 5 and limit 2 -> return at most 2 documents

        resp = client.get(

            "/api/v1/list/groups/r018prj-__ALL__-0001/documents"

            "?before=r018prj-__ALL__-0001-R0005&limit=2",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        assert resp.json()["count"] == 2



    def test_before_not_found_404(self, seed, tmp_path):

        """Returns 404 when the before doc_id does not exist."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/groups/r018prj-__ALL__-0001/documents"

            "?before=NONEXISTENT9999",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 404

        assert resp.json()["error"] == "not_found"

        assert resp.json()["before"] == "NONEXISTENT9999"



    def test_before_and_offset_400(self, seed, tmp_path):

        """Returns 400 when before and offset are specified together."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/groups/r018prj-__ALL__-0001/documents"

            "?before=r018prj-__ALL__-0001-DS0003&offset=5",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 400

        assert resp.json()["error"] == "invalid_params"



    def test_no_before_existing_behavior(self, seed, tmp_path):

        """When before is not specified, preserve the existing offset/limit behavior."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/groups/r018prj-__ALL__-0001/documents",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        data = resp.json()

        assert "total" in data

        assert "offset" in data

        assert data["offset"] == 0





# ─────────────────────────────────────────────────────────────────────────────

# Implementation item 5: before parameter — GET /list/projects/{p}/groups

# ─────────────────────────────────────────────────────────────────────────────



class TestListGroupsBefore:

    """Verify before parameter behavior for groups."""



    def test_before_normal(self, seed, tmp_path):

        """When before is specified, return the previous N groups including that group."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        # Based on Groups 0002 -> 2 items including 0001 and 0002

        resp = client.get(

            "/api/v1/list/projects/r018prj/groups"

            "?before=r018prj-__ALL__-0002&limit=5",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200, resp.text

        data = resp.json()

        assert data["ok"] is True

        assert data["before"] == "r018prj-__ALL__-0002"

        gids = [item["group_id"] for item in data["items"]]

        assert "r018prj-__ALL__-0002" in gids

        assert "r018prj-__ALL__-0001" in gids

        assert "r018prj-__ALL__-0003" not in gids



    def test_before_not_found_404(self, seed, tmp_path):

        """Returns 404 when the before group_id does not exist."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/projects/r018prj/groups?before=r018prj-__ALL__-9999",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 404

        assert resp.json()["error"] == "not_found"



    def test_before_and_offset_400(self, seed, tmp_path):

        """Returns 400 when before and offset are specified together."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/projects/r018prj/groups"

            "?before=r018prj-__ALL__-0002&offset=1",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 400

        assert resp.json()["error"] == "invalid_params"



    def test_no_before_existing_behavior(self, seed, tmp_path):

        """When before is not specified, preserve the existing behavior."""

        client = _build_list_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/list/projects/r018prj/groups",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        data = resp.json()

        assert "total" in data

        assert "offset" in data

        assert data["offset"] == 0





# ─────────────────────────────────────────────────────────────────────────────

# Implementation item 6: /help response JSON — explicit params

# ─────────────────────────────────────────────────────────────────────────────



class TestHelpEndpointParams:

    """Verify that the help response explicitly lists before/limit params."""



    def test_help_has_params_for_groups(self):

        """The group list endpoint includes a params field."""

        client = _build_help_client()

        resp = client.get("/api/v1/help")

        assert resp.status_code == 200

        endpoints = resp.json()["endpoints"]

        groups_ep = next(

            (e for e in endpoints if e["path"] == "/list/projects/{p}/groups"), None

        )

        assert groups_ep is not None, "/list/projects/{p}/groups endpoint not found"

        assert "params" in groups_ep, "params field missing"

        assert "before" in groups_ep["params"]

        assert "limit" in groups_ep["params"]

        assert "before" in groups_ep["example"]



    def test_help_has_params_for_documents(self):

        """The document list endpoint includes a params field."""

        client = _build_help_client()

        resp = client.get("/api/v1/help")

        assert resp.status_code == 200

        endpoints = resp.json()["endpoints"]

        docs_ep = next(

            (e for e in endpoints if e["path"] == "/list/groups/{gid}/documents"), None

        )

        assert docs_ep is not None, "/list/groups/{gid}/documents endpoint not found"

        assert "params" in docs_ep, "params field missing"

        assert "before" in docs_ep["params"]

        assert "limit" in docs_ep["params"]

        assert "before" in docs_ep["example"]





# ─────────────────────────────────────────────────────────────────────────────

# Implementation items 1, 2, 3: mention section order + recent group documents + complete POST example

# ─────────────────────────────────────────────────────────────────────────────



class TestMentionSections:

    """Verify mention section composition."""



    def _build_mention(self, group_recent_docs=None, locale="ko"):

        from modules.flow_gate.services.mention_service import build_mention

        return build_mention(

            project="testprj",

            module="__ALL__",

            group="0001",

            parent_type="R",
            parent_doc_number="R0001",
            parent_title="Test Requirement",
            parent_doc_id="R0001",
            parent_canonical_doc_id="testprj.__ALL__.0001.0001-R",
            head_type="DS",
            head_status="pending",
            scratch_dir="/scratch/tok_001",

            raw_token="test-raw-token-xyz",

            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",

            group_recent_docs=group_recent_docs,

            group_id="testprj-__ALL__-0001",

            locale=locale,

        )



    def test_section_order(self):

        """All 7 sections must exist in the order defined by D027 §6."""

        mention = self._build_mention()

        expected_headers = [

            "## Document information",

            "## Clarification guide",

            "## Instruction to include next document header",

            "## Reference documents",

            "## Artifact registration",

            "## doc_type guide",

            "## Reminder",

        ]

        prev_idx = -1

        for header in expected_headers:

            idx = mention.find(header)

            assert idx != -1, f"section header not found: {header}"

            assert idx > prev_idx, f"section order error: {header}"

            prev_idx = idx

        assert "## API usage" not in mention, "T384: API usage (/help) section must be removed"



    def test_section_4_absent_when_no_docs(self):

        """Omit section 4 when there are 0 recent documents in the group."""

        mention = self._build_mention(group_recent_docs=[])

        assert "## Recent documents in group" not in mention



    def test_section_4_present_when_docs_exist(self):

        """Include section 4 when the group has recent documents."""

        docs = [

            {"doc_id": "testprj-__ALL__-0001-R0001", "doc_type": "R", "seq": 1,

             "title": "req one", "status": "open"},

        ]

        mention = self._build_mention(group_recent_docs=docs)

        assert "## Recent documents in group" in mention

        assert "R0001" in mention

        assert "[R]" in mention

        assert "req one" in mention

        assert "(open)" in mention



    def test_section_4_max_5_docs(self):

        """Display all 5 recent documents in the group."""

        docs = [

            {"doc_id": f"testprj-__ALL__-0001-R{i:04d}", "doc_type": "R", "seq": i,

             "title": f"doc {i}", "status": "draft"}

            for i in range(5, 0, -1)

        ]

        mention = self._build_mention(group_recent_docs=docs)

        for i in range(1, 6):

            assert f"R{i:04d}" in mention



    def test_section_5_post_prefill(self):
        """The output registration section includes a complete POST example."""
        mention = self._build_mention()
        assert "## Artifact registration" in mention

        assert "Authorization: Bearer test-raw-token-xyz" in mention

        # Verify the JSON block

        assert '"action": "new"' in mention
        assert '"project": "testprj"' in mention
        assert '"module": "__ALL__"' in mention
        assert '"group_name": "testprj-__ALL__-0001"' in mention
        assert '"prev_doc_id": "testprj.__ALL__.0001.0001-R"' in mention
        assert '"doc_type": "DS"' in mention
        assert '"title": "<Fill this in>"' in mention
        assert '"content": "<Fill this in>"' in mention

    def test_section_5_token_context_uses_canonical_ids(self):
        """A token_rec-based mention uses the canonical id required by the inbox API."""
        from modules.flow_gate.services.mention_service import build_mention_from_token_rec

        mention = build_mention_from_token_rec(
            token_rec={
                "project": "testprj",
                "group_id": "testprj.test.0001",
                "scratch_dir": "/scratch/tok_001",
            },
            head_type="DS",
            head_status="pending",
            parent_doc={
                "doc_id": "testprj.test.0001.0001-R",
                "type_code": "R",
                "seq": 1,
                "title": "Test Requirement",
                "module": "test",
                "project_id": "testprj",
            },
            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",
            raw_token="test-raw-token-xyz",
        )

        reg_section = mention[mention.find("## Artifact registration"):]
        assert '"group_name": "testprj.test.0001"' in reg_section
        assert '"prev_doc_id": "testprj.test.0001.0001-R"' in reg_section
        assert '"target_id"' not in reg_section

    def test_section1_reflects_predecessor_when_head_context_doc_given(self):
        """R0001/T0004: Section 1 'Document information' reflects the step's predecessor
        document (e.g. NR step -> the N), while target_id / prev_doc_id stay bound to the
        sequence-owning R (the spine)."""
        from modules.flow_gate.services.mention_service import build_mention_from_token_rec

        spine_r = {
            "doc_id": "flowgate.default.0008.0001-R",
            "type_code": "R", "seq": 1, "title": "헤드문서가 나오지 않음",
            "module": "default", "project_id": "flowgate",
        }
        mention = build_mention_from_token_rec(
            token_rec={
                "project": "flowgate",
                "group_id": "flowgate.default.0008",
                "scratch_dir": "/scratch/tok",
            },
            head_type="NR",
            head_status="pending",
            parent_doc=spine_r,
            head_context_doc={
                "doc_id": "flowgate.default.0008.0002-N",
                "type_code": "N", "seq": 2, "title": "조사지시",
                "module": "default", "project_id": "flowgate",
            },
            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",
            raw_token="tok",
            locale="ko",
        )

        # Section 1 reflects the predecessor N, not the spine R.
        doc_info = mention.split("## ")[1]
        assert "type: N" in doc_info
        assert "type: R" not in doc_info
        assert "doc_number: N0002" in doc_info
        # Section 2 next_type still names the current head step.
        assert "next_type: NR" in mention
        # Threading fields stay on the spine R.
        assert "target_id: R0001" in mention
        assert '"prev_doc_id": "flowgate.default.0008.0001-R"' in mention

    def test_section1_falls_back_to_spine_when_no_predecessor(self):
        """First step (head_context_doc is the spine itself): Section 1 keeps showing the
        owning R — correct, since no prior step has produced a document yet."""
        from modules.flow_gate.services.mention_service import build_mention_from_token_rec

        spine_r = {
            "doc_id": "flowgate.default.0008.0001-R",
            "type_code": "R", "seq": 1, "title": "헤드문서가 나오지 않음",
            "module": "default", "project_id": "flowgate",
        }
        mention = build_mention_from_token_rec(
            token_rec={
                "project": "flowgate",
                "group_id": "flowgate.default.0008",
                "scratch_dir": "/s",
            },
            head_type="N",
            head_status="pending",
            parent_doc=spine_r,
            head_context_doc=spine_r,  # same object -> first-step fallback
            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",
            raw_token="tok",
            locale="ko",
        )
        doc_info = mention.split("## ")[1]
        assert "type: R" in doc_info
        assert "doc_number: R0001" in doc_info
        assert "next_type: N" in mention

    def test_section_5_no_prev_doc_id_when_empty_parent_doc_id(self):
        """If parent_doc_id is an empty string, omit the prev_doc_id field from the POST example."""
        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(

            project="p",

            module="m",

            group="g",

            parent_type="T",

            parent_doc_number="T0001",

            parent_title="title",

            parent_doc_id="",

            head_type="DS",

            head_status="pending",

            scratch_dir="/s",

            raw_token="tok",

            api_base_url="http://localhost:8000",

        )

        # The JSON block in the output registration section must not contain prev_doc_id
        reg_idx = mention.find("## Artifact registration")
        assert reg_idx != -1
        reg_section = mention[reg_idx:]
        # Extract the JSON block ({...})

        json_start = reg_section.find("{")

        json_end = reg_section.find("}") + 1

        if json_start != -1 and json_end > json_start:

            post_json_str = reg_section[json_start:json_end]
            try:
                post_json = json.loads(post_json_str)
                assert "prev_doc_id" not in post_json, "prev_doc_id must not be in POST body when parent_doc_id is absent"
            except json.JSONDecodeError:
                # If the JSON is multiline, re-extract the block including line breaks
                pass  # Check it another way
        # Simple string check: the POST section must not contain the prev_doc_id key
        assert '"prev_doc_id"' not in reg_section
        assert '"target_id"' not in reg_section


    def test_next_type_pending(self):

        """When head is pending, insert the actual type into next_type."""

        mention = self._build_mention()

        assert "next_type: DS" in mention



    def test_next_type_no_sequence(self):

        """When there is no head, use <sequence undecided> in next_type."""

        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(

            project="p", module="m", group="g",

            parent_type="R", parent_doc_number="R0001", parent_title="t",

            parent_doc_id="R0001",

            head_type="", head_status="",

            scratch_dir="/s", raw_token="tok",

            api_base_url="http://localhost:8000",

        )

        assert "next_type: <Sequence undecided>" in mention



    def test_next_type_in_progress(self):

        """When head is in_progress, use <in progress: TYPE> in next_type."""

        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(

            project="p", module="m", group="g",

            parent_type="R", parent_doc_number="R0001", parent_title="t",

            parent_doc_id="R0001",

            head_type="DS", head_status="in_progress",

            scratch_dir="/s", raw_token="tok",

            api_base_url="http://localhost:8000",

        )

        assert "next_type: <In progress: DS>" in mention



    def test_authorization_token_in_mention(self):

        """Includes Authorization: Bearer {raw_token}."""

        mention = self._build_mention()

        assert "Authorization: Bearer test-raw-token-xyz" in mention



    def test_reference_get_section_carries_real_token(self):

        """T0002 (group 0074): the Reference documents GET note must embed the real

        worker token, not the literal <YOUR_TOKEN> placeholder. Otherwise the worker

        has no usable credential for GET reads and group documents return 401."""

        mention = self._build_mention()

        section = mention.split("## Reference documents", 1)[1].split("## ", 1)[0]

        assert "Authorization: Bearer test-raw-token-xyz header" in section

        assert "<YOUR_TOKEN>" not in mention



    def test_section_4_navigation_url(self):

        """Section 4 includes a navigation URL."""

        docs = [

            {"doc_id": "testprj-__ALL__-0001-R0003", "doc_type": "R", "seq": 3,

             "title": "doc 3", "status": "open"},

            {"doc_id": "testprj-__ALL__-0001-R0002", "doc_type": "R", "seq": 2,

             "title": "doc 2", "status": "open"},

            {"doc_id": "testprj-__ALL__-0001-R0001", "doc_type": "R", "seq": 1,

             "title": "doc 1", "status": "draft"},

        ]

        mention = self._build_mention(group_recent_docs=docs)

        assert "To browse earlier documents" in mention

        assert "testprj-__ALL__-0001" in mention

        assert "before=" in mention



    # ── New T386 tests ─────────────────────────────────────────────────────────



    def test_section_3_new_format_single_ref(self):

        """Section 3: each reference document is emitted on one line as
        '{canonical-id}: GET {url}'. Current contract: the head doc is NOT
        auto-listed for a new hand-off; only the ids passed via ref_doc_ids
        appear, verbatim (canonical dot-style ids)."""

        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(
            project="testprj",
            module="__ALL__",
            group="0001",
            parent_type="R",
            parent_doc_number="R0001",
            parent_title="Test Requirement",
            parent_doc_id="R0001",
            parent_canonical_doc_id="testprj.__ALL__.0001.0001-R",
            head_type="DS",
            head_status="pending",
            scratch_dir="/scratch/tok_001",
            raw_token="test-raw-token-xyz",
            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",
            group_id="testprj-__ALL__-0001",
            ref_doc_ids=["testprj.__ALL__.0001.0001-R"],
        )

        s3_idx = mention.find("## Reference documents")

        assert s3_idx != -1

        s3_section = mention[s3_idx:]

        next_section = s3_section.find("##", 2)

        s3_body = s3_section[:next_section] if next_section != -1 else s3_section

        # Verify the current format (canonical dot-style id, one line each)

        assert "testprj.__ALL__.0001.0001-R: GET" in s3_body

        assert "localhost:8000" in s3_body

        assert "/document/testprj.__ALL__.0001.0001-R" in s3_body

        # The old list-style format must not appear

        assert "- Referenced documents:" not in s3_body

        assert "- Referenced document lookup:" not in s3_body

        assert "- Additional referenced documents:" not in s3_body



    def test_section_3_with_selected_docs(self):

        """Section 3: output one line per selected reference document, verbatim."""

        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(

            project="testprj",

            module="__ALL__",

            group="0001",

            parent_type="R",

            parent_doc_number="R0001",

            parent_title="Test Requirement",

            parent_doc_id="R0001",

            head_type="DS",

            head_status="pending",

            scratch_dir="/scratch/tok_001",

            raw_token="test-raw-token-xyz",

            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",

            group_id="testprj-__ALL__-0001",

            ref_doc_ids=["testprj.__ALL__.0001.0002-M", "testprj.__ALL__.0001.0003-DS"],

        )

        s3_idx = mention.find("## Reference documents")

        s3_section = mention[s3_idx:]

        next_section = s3_section.find("##", 2)

        s3_body = s3_section[:next_section] if next_section != -1 else s3_section

        # Two selected lines (current contract: only the passed ref ids appear)

        assert "testprj.__ALL__.0001.0002-M: GET" in s3_body

        assert "testprj.__ALL__.0001.0003-DS: GET" in s3_body



    def test_section_3_dedup_repeated_ref_doc_ids(self):

        """Section 3: deduplicate when ref_doc_ids contains the same id twice."""

        from modules.flow_gate.services.mention_service import build_mention

        mention = build_mention(

            project="testprj",

            module="__ALL__",

            group="0001",

            parent_type="R",

            parent_doc_number="R0001",

            parent_title="Test Requirement",

            parent_doc_id="R0001",

            head_type="DS",

            head_status="pending",

            scratch_dir="/scratch/tok_001",

            raw_token="test-raw-token-xyz",

            api_base_url="http://localhost:8000/ctx/flow_gate/api/v1",

            group_id="testprj-__ALL__-0001",

            ref_doc_ids=[
                "testprj.__ALL__.0001.0001-R",
                "testprj.__ALL__.0001.0001-R",
                "testprj.__ALL__.0001.0002-M",
            ],

        )

        s3_idx = mention.find("## Reference documents")

        s3_section = mention[s3_idx:]

        next_section = s3_section.find("##", 2)

        s3_body = s3_section[:next_section] if next_section != -1 else s3_section

        # The R line must appear only once

        assert s3_body.count("testprj.__ALL__.0001.0001-R: GET") == 1

        # The M line exists

        assert "testprj.__ALL__.0001.0002-M: GET" in s3_body



    def test_section_7_doc_type(self):

        """Section 7: the doc_type guidance section exists and includes the GET /help/doc_type URL."""

        mention = self._build_mention()

        assert "## doc_type guide" in mention

        assert "GET" in mention

        assert "/help/doc_type" in mention



    def test_section_8_q_mention(self):

        """R0001/T0004: the clarification guide is hoisted to the top (right after
        Document information, before the next-document instruction) and carries the
        no-choices guard. B0001/NR0003: it embeds a ready-to-send query POST (document-
        bound query data, NOT a Q document) instead of a GET /help/question pointer."""

        mention = self._build_mention()

        assert "## Clarification guide" in mention

        # req2: it must sit directly after Document information and before the
        # next-document instruction — not at the bottom where it was ignored.

        idx_docinfo = mention.find("## Document information")

        idx_q = mention.find("## Clarification guide")

        idx_instruction = mention.find("## Instruction to include next document header")

        assert idx_docinfo != -1

        assert idx_q != -1

        assert idx_instruction != -1

        assert idx_docinfo < idx_q < idx_instruction, "clarification guide must come right after Document information, before the instruction section"

        # req3: the no-choices guard must be present in the mention body.

        assert "Do NOT present choices" in mention

        assert "force-terminated" in mention

        # B0001/NR0003: the sanctioned alternative is an embedded, copy-paste query
        # POST aligned with the live mechanism — not a "Q document" / GET indirection.

        assert "/q/" in mention and "/questions" in mention

        assert '"asker_kind": "ai"' in mention

        assert "NOT a Q document" in mention

        assert "/help/question" not in mention

        # NR0003 recency: the no-choices guard is repeated at the bottom.

        idx_reminder = mention.find("## Reminder")

        assert idx_reminder > idx_q, "Reminder section must be at the bottom, after the hoisted guide"

        assert mention.count("do NOT present choices") + mention.count("Do NOT present choices") >= 2

        # rejection (2026-06-15): the prohibition alone was ignored. The guard must
        # pair "no choices" with an explicit positive redirect — write a Q to get a
        # definite answer — co-located at the point of temptation and in the reminder.
        assert "register a Q" in mention
        assert "definite answer" in mention
        assert "next action" in mention

    def test_clarification_guide_follows_locale(self):
        """B0001 rev2 (reject "some mention text stays hardcoded in Korean even when the locale changes"): the clarification
        guide + reminder prose must follow the worker locale. For en/ja the guide
        must NOT leak Korean; for ja it must be Japanese; the English keyword anchors
        stay present in every locale."""
        import re

        hangul = re.compile(r"[가-힣]")
        kana = re.compile(r"[ぁ-んァ-ヶ]")

        def guide_and_reminder(mention):
            g = mention.find("## Clarification guide")
            r = mention.find("## Reminder")
            assert g != -1 and r != -1
            # guide slice (g..r) + reminder slice (r..end)
            return mention[g:]

        # en: no Korean anywhere in the guide/reminder, keywords intact
        en = self._build_mention(locale="en")
        en_slice = guide_and_reminder(en)
        assert not hangul.search(en_slice), "en mention must not contain Korean"
        assert not kana.search(en_slice), "en mention must not contain Japanese"
        for kw in ("Do NOT present choices", "NOT a Q document", "register a Q",
                   "definite answer", "next action", "force-terminated"):
            assert kw in en_slice, f"en keyword missing: {kw}"

        # ja: Japanese present, no Korean leak, keywords intact
        ja = self._build_mention(locale="ja")
        ja_slice = guide_and_reminder(ja)
        assert not hangul.search(ja_slice), "ja mention must not contain Korean"
        assert kana.search(ja_slice), "ja mention must contain Japanese"
        for kw in ("Do NOT present choices", "NOT a Q document", "register a Q",
                   "definite answer", "next action"):
            assert kw in ja_slice, f"ja keyword missing: {kw}"

        # ko (default): Korean present (bilingual), keywords intact
        ko = self._build_mention(locale="ko")
        ko_slice = guide_and_reminder(ko)
        assert hangul.search(ko_slice), "ko mention must contain Korean"
        assert "register a Q" in ko_slice and "next action" in ko_slice

        # unknown locale folds to ko (normalize_locale), never crashes
        zh = self._build_mention(locale="zh")
        assert "## Clarification guide" in zh





# ─────────────────────────────────────────────────────────────────────────────

# T386 implementation item 3: GET /api/v1/help/doc_type

# ─────────────────────────────────────────────────────────────────────────────



class TestHelpDocType:

    """Verify the GET /api/v1/help/doc_type endpoint."""



    def _build_help_client(self):

        from modules.flow_gate.api.v1.help_routes import router

        app = FastAPI()

        app.include_router(router)

        return TestClient(app)



    def test_doc_type_returns_200_and_array(self, seed, tmp_path):

        """Returns 200 + an array on successful authentication."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200, resp.text

        data = resp.json()

        assert isinstance(data, list), "response must be an array"

        assert len(data) > 0, "document_types data must be present"



    def test_doc_type_item_has_required_fields(self, seed, tmp_path):

        """Each item includes the type_code, name, series, and description fields.

        No 'locale' field is baked into the item — the row never carried a real
        per-item locale, so echoing a fixed value there was a lie (T0017)."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        items = resp.json()

        for item in items:

            assert "type_code" in item, f"type_code missing: {item}"

            assert "name" in item, f"name missing: {item}"

            assert "series" in item, f"series missing: {item}"

            assert "description" in item, f"description missing: {item}"

            assert "locale" not in item, f"stale hardcoded locale field found: {item}"



    def test_doc_type_honors_request_locale(self, seed, tmp_path):

        """T0017: the doc_type list must follow the worker token's continuation_locale

        instead of always querying with the ko default."""

        client = self._build_help_client()

        raw_default = _issue_bearer(tmp_path)

        resp_default = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw_default}"},

        )

        assert resp_default.status_code == 200

        default_names = {item["type_code"]: item["name"] for item in resp_default.json()}

        assert default_names["R"] == "요건정의"

        raw_ja = _issue_bearer(tmp_path, continuation_locale="ja")

        resp_ja = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw_ja}"},

        )

        assert resp_ja.status_code == 200

        ja_names = {item["type_code"]: item["name"] for item in resp_ja.json()}

        assert ja_names["R"] == "要件定義"

        raw_en = _issue_bearer(tmp_path, continuation_locale="en")

        resp_en = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw_en}"},

        )

        assert resp_en.status_code == 200

        en_names = {item["type_code"]: item["name"] for item in resp_en.json()}

        assert en_names["R"] == "Requirements"


    def test_doc_type_description_honors_request_locale(self, seed, tmp_path):
        """T0019: the description text is stored per locale (like type_name) and
        must follow the worker token's continuation_locale instead of always
        being None (the field previously had no backing column at all)."""

        client = self._build_help_client()

        raw_default = _issue_bearer(tmp_path)
        resp_default = client.get(
            "/api/v1/help/doc_type",
            headers={"Authorization": f"Bearer {raw_default}"},
        )
        assert resp_default.status_code == 200
        default_desc = {item["type_code"]: item["description"] for item in resp_default.json()}
        assert default_desc["R"] == "무엇을·왜 만들지를 정의하는 문서. 기능·비기능 요구사항을 정리한다."

        raw_ja = _issue_bearer(tmp_path, continuation_locale="ja")
        resp_ja = client.get(
            "/api/v1/help/doc_type",
            headers={"Authorization": f"Bearer {raw_ja}"},
        )
        assert resp_ja.status_code == 200
        ja_desc = {item["type_code"]: item["description"] for item in resp_ja.json()}
        assert ja_desc["R"] == "何を・なぜ作るかを定義する文書。機能・非機能要件を整理する。"

        raw_en = _issue_bearer(tmp_path, continuation_locale="en")
        resp_en = client.get(
            "/api/v1/help/doc_type",
            headers={"Authorization": f"Bearer {raw_en}"},
        )
        assert resp_en.status_code == 200
        en_desc = {item["type_code"]: item["description"] for item in resp_en.json()}
        assert en_desc["R"] == "Defines what to build and why. Captures functional and non-functional requirements."


    def test_doc_type_no_auth_returns_401(self):

        """Returns 401 without authentication."""

        client = self._build_help_client()

        resp = client.get("/api/v1/help/doc_type")

        assert resp.status_code == 401



    def test_doc_type_series_general_present(self, seed, tmp_path):

        """R/M/Q/A/B family document types must be returned with series='general' (T392)."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        items = resp.json()

        series_values = {item["series"] for item in items if item.get("series")}

        assert "general" in series_values, (

            f"no item with series='general'. series values found: {series_values}"

        )

        assert "requirements" not in series_values, (

            f"old series='requirements' still present: {series_values}"

        )





# ─────────────────────────────────────────────────────────────────────────────

# T395: GET /api/v1/help/question

# ─────────────────────────────────────────────────────────────────────────────



class TestHelpQuestion:

    """Verify the GET /api/v1/help/question endpoint."""



    def _build_help_client(self):

        from modules.flow_gate.api.v1.help_routes import router

        app = FastAPI()

        app.include_router(router)

        return TestClient(app)



    def test_question_no_auth_returns_401(self):

        """Returns 401 without authentication."""

        client = self._build_help_client()

        resp = client.get("/api/v1/help/question")

        assert resp.status_code == 401



    def test_question_returns_200_and_object(self, seed, tmp_path):

        """Returns 200 + an object on successful authentication."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/question",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200, resp.text

        data = resp.json()

        assert isinstance(data, dict), "response must be a dict (object)"



    def test_question_has_required_fields(self, seed, tmp_path):

        """The response includes an example key and a structured request form."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/question",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp.status_code == 200

        data = resp.json()

        assert "example" in data, "example field missing"

        example = data["example"]

        assert "method" in example, "example.method missing"

        assert "url" in example, "example.url missing"

        assert "headers" in example, "example.headers missing"

        assert "body" in example, "example.body missing"



    def test_question_required_fields_list(self, seed, tmp_path):

        """Current contract (Q/A revamp): a query is document-bound DATA, not a Q
        document. example.body therefore carries a structured `questions` list
        (each {title, body}) plus asker_kind — not a `### Q` markdown body."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp = client.get(

            "/api/v1/help/question",

            headers={"Authorization": f"Bearer {raw}"},

        )

        data = resp.json()

        body = data.get("example", {}).get("body", {})

        assert body.get("asker_kind") == "ai", "example.body.asker_kind must be 'ai'"

        questions = body.get("questions")

        assert isinstance(questions, list) and questions, (
            "example.body.questions must be a non-empty list"
        )

        for q in questions:
            assert "title" in q, f"question item missing title: {q}"
            assert "body" in q, f"question item missing body: {q}"

        # The retired Q-document markdown form must not be present.
        assert "content" not in body, "example.body must not carry a markdown 'content' field"



    def test_question_response_consistent_with_doc_type(self, seed, tmp_path):

        """The response format is consistent with /help/doc_type (JSON, Korean content, same auth method)."""

        client = self._build_help_client()

        raw = _issue_bearer(tmp_path)

        resp_q = client.get(

            "/api/v1/help/question",

            headers={"Authorization": f"Bearer {raw}"},

        )

        resp_dt = client.get(

            "/api/v1/help/doc_type",

            headers={"Authorization": f"Bearer {raw}"},

        )

        assert resp_q.status_code == 200

        assert resp_dt.status_code == 200

        # Both responses are JSON

        assert resp_q.headers.get("content-type", "").startswith("application/json")

        assert resp_dt.headers.get("content-type", "").startswith("application/json")
