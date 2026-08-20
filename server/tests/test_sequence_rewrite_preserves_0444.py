"""0444 T0007 — NR0003 §4-2 · §4-6: the values a sequence rewrite must not lose.

Two defects with one shape: a row is rebuilt from somebody's payload and a value that was
already stored quietly disappears.

* §4-2 (NR0003 §2-7) — ``expand_steps_with_reports`` copied the instruction row's note onto
  every report row it inserted, ``TSR`` included. ``attach_auto_rows`` (the pour path) has
  refused exactly that for ``TSR`` since commit ``0009e926`` ("TSR is assembled by the
  server: no provider and no note may be written for it"); commit ``178b21b2``
  (0434 T0004 F1) added the note copy on the decision/edit path and put the
  ``if report_type != "TSR"`` guard on the ``provider_id`` line only. The two sibling
  functions ended up disagreeing about the same row — a regression, not a design.

* §4-6 (NR0003 §2-4) — ``edit_workflow_pending`` deletes the pending rows and re-inserts the
  caller's items verbatim. The AI sequence-edit worker is a real partial-payload caller: the
  contract it is handed (mention payload, mention rules, help example and guidance) never
  named ``provider_id``, so its PATCH cleared a provider the server never showed it.

NR0003 §6 recorded that the suites which should have caught the second defect monkeypatch the
DB away. So every rewrite test here runs against a **real migrated sqlite** (the
``real_sequence`` pattern of test_workflow_sequence_provider_0408.py, as TR0006 used it in
test_work_plan_apply_0444.py) and reads the rows back through the production query.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import work_plan_sequence_service as pour_svc  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wf_svc  # noqa: E402

PROJECT = "flowgate"
GROUP = "flowgate.default.0444"
ROOT = "flowgate.default.0444.0001-B"
SOURCE_WP = "flowgate.default.0444.0004-WP"
PROVIDER = "stored-provider"
PROVIDER_NAME = "Stored Provider"
OTHER = "other-provider"
OTHER_NAME = "Other Provider"
API_BASE = "http://127.0.0.1:8089/flowgate/api/v1"
HANDOFF = "Take the previous step's answer and write it up"

SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
 VALUES('{PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
 VALUES('{GROUP}', '{PROJECT}', 'default', 'sequence rewrite', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
 doc_id, project_id, module, group_id, type_code, seq, title, status,
 doc_review_status, created_at, updated_at)
 VALUES('{ROOT}', '{PROJECT}', 'default', '{GROUP}', 'B', 1, 'sequence rewrite', 'open', NULL, datetime('now'), datetime('now')),
       ('{SOURCE_WP}', '{PROJECT}', 'default', '{GROUP}', 'WP', 4, 'plan', 'open', 'approved', datetime('now'), datetime('now'));
"""


# ── A — TSR must not inherit the instruction note (NR0003 §4-2) ────────────────────

def expanded(items: list[dict]) -> list[dict]:
    return wf_svc.expand_steps_with_reports([dict(item) for item in items], locale="ko")


def ts_step() -> dict:
    return {
        "type": "TS", "label": "Test", "note": HANDOFF,
        "source_doc_id": SOURCE_WP, "source_revision_no": 3,
        "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME,
    }


def test_the_server_assembled_tsr_row_is_not_given_the_instruction_note():
    """§7 server 1 — the defect itself: TSR arrived carrying the TS row's note."""
    rows = expanded([ts_step()])
    assert [row["type"] for row in rows] == ["TS", "TSR"]
    assert rows[1]["note"] == ""
    assert rows[1]["provider_id"] is None
    assert rows[1]["provider_display_name"] is None


def test_the_tsr_row_still_carries_its_instructions_plan_source():
    """§7 server 2 — only the note is cleared. source_doc_id/source_revision_no keep
    riding along, because 0434 T0004 F1/F2 put the automatic report in the same plan
    revision as its instruction so freshness checks see them together."""
    rows = expanded([ts_step()])
    assert rows[1]["source_doc_id"] == SOURCE_WP
    assert rows[1]["source_revision_no"] == 3


@pytest.mark.parametrize("code,report", [("T", "TR"), ("N", "NR")])
def test_instruction_report_pairs_still_inherit_note_source_and_provider(code, report):
    """§7 server 3 — the control group. The handoff 0434 T0004 F1 was fixing is the
    N→NR / T→TR one, and it survives untouched."""
    rows = expanded([{
        "type": code, "label": code, "note": HANDOFF,
        "source_doc_id": SOURCE_WP, "source_revision_no": 3,
        "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME,
    }])
    assert [row["type"] for row in rows] == [code, report]
    assert rows[1]["note"] == HANDOFF
    assert rows[1]["source_doc_id"] == SOURCE_WP
    assert rows[1]["source_revision_no"] == 3
    assert rows[1]["provider_id"] == PROVIDER
    assert rows[1]["provider_display_name"] == PROVIDER_NAME


def test_both_sibling_functions_now_build_the_same_tsr_note():
    """§7 server 4 — the exact comparison NR0003 §2-7 ran by hand and found different."""
    decision_rows = expanded([ts_step()])
    poured, _uid = pour_svc.attach_auto_rows(
        [pour_svc._new_row(1, "TS", "ko", note=HANDOFF, pair_note=HANDOFF)], "ko", 1,
    )
    assert [row["type"] for row in poured] == ["TS", "TSR"]
    assert decision_rows[1]["note"] == poured[1]["note"] == ""
    assert decision_rows[1]["provider_id"] == poured[1]["provider_id"] is None


# ── B — the pending-row rewrite must not drop a stored provider (NR0003 §4-6) ──────

class SqliteStore:
    """Minimal real-SQL store: the query text still comes from the production registry."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_sequence(migrated_sqlite_db):
    path = migrated_sqlite_db("sequence_rewrite_preserves_0444.db", seed_sql=SEED_SQL)
    store = SqliteStore(path)
    previous = db_connection.STORE
    db_connection.STORE = store
    db_wfseq.insert_sequence(ROOT)
    seq = db_wfseq.get_sequence_by_doc_id(ROOT)
    try:
        yield seq
    finally:
        db_connection.STORE = previous
        store._conn.close()


def add_row(seq, item_seq, type_, label, *, provider=None, provider_name=None, note=""):
    """Insert one real pending row. ``status`` is derived by the production query."""
    db_wfseq.insert_sequence_item(
        sequence_id=seq["id"], item_seq=item_seq, type_=type_, label=label,
        doc_class="B", sort_order=item_seq, note=note,
        source_doc_id=None, source_revision_no=None,
        provider_id=provider, provider_display_name=provider_name,
    )


def providers_of(seq) -> list[tuple]:
    return [
        (row["type"], row["provider_id"], row["provider_display_name"])
        for row in db_wfseq.get_sequence_items(seq["id"])
    ]


def test_an_item_without_the_provider_key_keeps_the_stored_provider(real_sequence):
    """§7 server 5 — the AI worker's PATCH. It never received a provider_id, so it sends
    none back; the row it did not touch must not lose one."""
    add_row(real_sequence, 1, "M", "Memo", provider=PROVIDER, provider_name=PROVIDER_NAME)
    wf_svc.edit_workflow_pending(ROOT, [{"type": "M", "label": "Memo", "note": ""}])
    assert providers_of(real_sequence) == [("M", PROVIDER, PROVIDER_NAME)]


def test_an_explicitly_null_provider_still_clears_it(real_sequence):
    """§7 server 6 — the fallback keys off the ABSENCE of the key, never off falsiness.
    A caller that means "empty this" says so and is obeyed."""
    add_row(real_sequence, 1, "M", "Memo", provider=PROVIDER, provider_name=PROVIDER_NAME)
    wf_svc.edit_workflow_pending(
        ROOT, [{"type": "M", "label": "Memo", "note": "", "provider_id": None}],
    )
    assert providers_of(real_sequence) == [("M", None, None)]


def test_two_pending_rows_with_the_same_key_get_no_guessed_restore(real_sequence):
    """§7 server 7 — with two candidates for one (type, label) there is no way to know
    which is which, so the whole key drops out of the candidate map."""
    add_row(real_sequence, 1, "M", "Memo", provider=PROVIDER, provider_name=PROVIDER_NAME)
    add_row(real_sequence, 2, "M", "Memo", provider=OTHER, provider_name=OTHER_NAME)
    wf_svc.edit_workflow_pending(ROOT, [
        {"type": "M", "label": "Memo", "note": ""},
        {"type": "M", "label": "Memo", "note": ""},
    ])
    assert providers_of(real_sequence) == [("M", None, None), ("M", None, None)]


def test_a_restored_provider_reaches_the_automatic_report_row(real_sequence):
    """§7 server 8 — proof the merge runs BEFORE expand_steps_with_reports: the TR row
    the server attaches is built from the restored value, not from the empty payload."""
    add_row(real_sequence, 1, "T", "Task", provider=PROVIDER, provider_name=PROVIDER_NAME)
    wf_svc.edit_workflow_pending(ROOT, [{"type": "T", "label": "Task", "note": ""}])
    assert providers_of(real_sequence) == [
        ("T", PROVIDER, PROVIDER_NAME),
        ("TR", PROVIDER, PROVIDER_NAME),
    ]


# ── B — the contract the worker is handed has to name the provider ────────────────

def extract_pending_json(mention: str) -> list[dict]:
    fence = chr(96) * 3
    match = re.search(re.escape(fence) + r"json\n(\[.*?\])\n" + re.escape(fence), mention, re.S)
    assert match, mention
    return json.loads(match.group(1))


def test_request_sequence_edit_hands_the_worker_the_stored_provider(real_sequence, monkeypatch):
    """§7 server 9 — the first of the five coordinates: what the token issuer builds."""
    add_row(real_sequence, 1, "M", "Memo", provider=PROVIDER, provider_name=PROVIDER_NAME)
    monkeypatch.setattr(
        wf_svc.token_service, "issue",
        lambda **kwargs: {
            "raw_token": "tok_raw", "token_id": "tok_id", "expires_at": None,
            "scratch_dir": "C:/scratch",
        },
    )
    mention = wf_svc.request_sequence_edit(ROOT, "worker", API_BASE, locale="en")["mention"]
    payload = extract_pending_json(mention)
    assert payload[0]["provider_id"] == PROVIDER
    assert payload[0]["provider_display_name"] == PROVIDER_NAME


def test_a_provider_only_row_still_gets_a_returnable_json_block():
    """§7 server 10 — ``has_pending_metadata`` used to look at note/source only, so a row
    whose ONLY stored value was the provider got no JSON block at all to return."""
    mention = mention_service.build_sequence_edit_mention(
        token_rec={"project": PROJECT, "group_id": GROUP},
        target_doc={"doc_id": ROOT, "type_code": "B", "seq": 1, "title": "Root"},
        api_base_url=API_BASE, raw_token="tok", locale="en",
        sequence_items=[{
            "item_seq": 1, "type": "M", "label": "Memo", "status": "pending",
            "note": "", "source_doc_id": None, "source_revision_no": None,
            "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME,
        }],
    )
    payload = extract_pending_json(mention)
    assert payload[0]["provider_id"] == PROVIDER
    assert payload[0]["provider_display_name"] == PROVIDER_NAME


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_every_locale_tells_the_worker_how_to_return_the_provider(locale):
    """§7 server 11 — a rule only one locale states is a rule two thirds of the workers
    never read. Both halves have to be there: omit the key ⇒ kept, explicit null ⇒ cleared."""
    rules = mention_service._SEQUENCE_EDIT_METADATA_COPY[locale]["rules"]
    assert "provider_id" in rules
    assert "provider_display_name" in rules
    assert "null" in rules

    guidance = help_catalog._content_submit({
        "base_url": "/flowgate/api/v1", "action_scope": "workflow_sequence_edit",
        "doc_id": ROOT, "locale": locale,
    })["guidance"]
    assert "provider_id" in guidance
    assert "null" in guidance
