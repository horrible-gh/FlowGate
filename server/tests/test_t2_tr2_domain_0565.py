"""T2/TR2 type, pair, permission, canonical spec and source fingerprint contracts."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import pytest

from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.documents.constants import WORK_PLAN_PAIR_MAP, WORK_PLAN_SET_TYPES
from modules.flow_gate.documents.type_code import (
    doc_code_seq_text, doc_id_type_code, is_valid_type_code, parse_doc_code,
    parse_step_key,
)
from modules.flow_gate.numbering.id_formatter import extract_numeric_suffix
from modules.flow_gate.services import workflow_decision_service as decisions
from modules.flow_gate.services.tool_registry import MUTATING_STEP_TYPES, kind_for_step


def _body():
    return {
        "tr2_version": 1,
        "source_t2_doc_id": "flowgate.default.0565.0005-T2",
        "edit_spec": {
            "termination": "ready_to_apply",
            "edits": [{"id": "e1", "file": "src/a.txt", "anchor_old": "a",
                       "replacement_new": "b", "rationale": "change a",
                       "confidence": "high"}],
            "deferred": [],
            "gate": {"commands": [], "apply": False},
        },
    }


@pytest.mark.parametrize("code", ["R", "TR", "TSR", "WP", "T2", "TR2", "A1", "AB12"])
def test_type_code_accepts_canonical_codes(code):
    assert is_valid_type_code(code)


@pytest.mark.parametrize("code", ["2T", "tr2", "T-2", "T_2", "ABCDE", ""])
def test_type_code_rejects_invalid_codes(code):
    assert not is_valid_type_code(code)


def test_document_code_and_pair_contract():
    assert parse_doc_code("0005-T2") == ("T2", 5)
    assert parse_doc_code("0012-DS") == ("DS", 12)
    assert doc_code_seq_text("0005-T2") == "0005"
    assert extract_numeric_suffix("0005-T2") == "0005"
    assert doc_id_type_code("flowgate.default.0565.0016-TR2") == "TR2"
    assert parse_step_key("TR2#1") == ("TR2", 1)
    assert parse_step_key("T#1") == ("T", 1)
    assert parse_step_key("TR2001") is None
    assert WORK_PLAN_PAIR_MAP["T2"] == decisions.AUTO_REPORT_MAP["T2"] == "TR2"
    assert set(WORK_PLAN_PAIR_MAP) == set(decisions.AUTO_REPORT_MAP)
    assert "T2" in WORK_PLAN_SET_TYPES
    assert "T2" in decisions.INSTRUCTION_AUTO_TYPES


def test_source_permission_remains_read_only():
    assert kind_for_step("new", "T2") == ("read", None)
    assert kind_for_step("new", "TR2") == ("read", None)
    assert kind_for_step("new", "TR") == ("read_write", None)
    assert "TR2" not in MUTATING_STEP_TYPES


def test_schema_rejects_unsafe_and_invalid_edits():
    body = _body()
    assert tr2.validate(body, doc={})
    body["edit_spec"]["gate"]["apply"] = True
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.validate(body, doc={})
    body["edit_spec"]["gate"]["apply"] = False
    body["edit_spec"]["edits"] = []
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.validate(body, doc={})
    body = _body()
    body["edit_spec"]["edits"][0]["file"] = "../escape"
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        tr2.validate(body, doc={})


def test_canonical_body_drops_untrusted_roots_and_baseline():
    body = _body()
    body.update({"codebase_root": "wrong", "source_honey": "wrong",
                 "baseline_fingerprint": "wrong"})
    validated = tr2.validate(body, doc={})
    canonical = tr2.canonicalize(validated)
    assert set(canonical) == {"tr2_version", "source_t2_doc_id", "edit_spec"}
    assert canonical["edit_spec"]["edits"][0]["kind"] == "edit"
    assert tr2.dumps(canonical).endswith("\n")


def test_target_fingerprint_raw_bytes_and_absence(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    target = root / "src" / "a.txt"
    target.write_bytes(b"a\r\n")
    spec = tr2.canonicalize(_body())["edit_spec"]
    before = tr2.target_fingerprint(spec, root)
    assert before.startswith("sha256:") and len(before) == 71
    assert len(tr2.spec_fingerprint(spec)) == 64
    (root / "unrelated").write_bytes(b"changed")
    assert tr2.target_fingerprint(spec, root) == before
    target.write_bytes(b"a\n")
    assert tr2.target_fingerprint(spec, root) != before
    target.unlink()
    assert tr2.target_fingerprint(spec, root) != before
    assert tr2.target_fingerprint({"edits": []}, root) == (
        "sha256:" + hashlib.sha256(b"").hexdigest())



def test_single_writer_revision_cas_never_touches_file_on_stale_request(tmp_path, monkeypatch):
    doc_id = "flowgate.default.0565.0006-TR2"
    path = tmp_path / "0006-TR2_document.json"
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_bytes(b"a")
    row = {"doc_id": doc_id, "type_code": "TR2", "project_id": "flowgate",
           "group_id": "flowgate.default.0565", "module": "default",
           "branch": "main", "revision_no": 0, "updated_at": "old",
           "file_path": None, "filename": None, "doc_review_status": "pending_review"}

    class Store:
        @contextmanager
        def transaction(self):
            yield self

        def _execute(self, sql, params):
            if "revision_no = revision_no + 1" in sql:
                if row["revision_no"] == params[-1]:
                    row["revision_no"] += 1
                    row["file_path"] = params[1]
                    row["filename"] = params[2]
            elif "SET revision_no = ?" in sql:
                if row["revision_no"] == params[-1]:
                    row["revision_no"] = params[0]

        def _execute_affected(self, sql, params):
            # The writer's CAS authority is the driver's affected-row count (T0030 §8).
            before = row["revision_no"]
            self._execute(sql, params)
            return int(row["revision_no"] != before)

    monkeypatch.setattr(tr2.db_docs, "get_by_id", lambda _doc_id: row.copy())
    monkeypatch.setattr(tr2, "get_store", lambda: Store())
    monkeypatch.setattr(tr2, "verify_pair", lambda *_: None)
    monkeypatch.setattr(tr2, "mutation_block", lambda _doc: None)
    monkeypatch.setattr(tr2, "resolve_source_root", lambda *_: root)
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda _: path)
    monkeypatch.setattr(tr2.storage_paths, "to_storage_relative", lambda *_: str(path))
    saved = tr2.save(doc_id, _body(), actor="tester", expected_revision=0)
    first_bytes = path.read_bytes()
    assert saved["new_revision"] == row["revision_no"] == 1
    assert json.loads(first_bytes)["baseline_fingerprint"].startswith("sha256:")
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.save(doc_id, _body(), actor="tester", expected_revision=0)
    assert error.value.code == "tr2_spec_changed"
    assert row["revision_no"] == 1
    assert path.read_bytes() == first_bytes
