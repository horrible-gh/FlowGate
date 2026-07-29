"""flowgate.default.0353 B0001 regression coverage for mode-aware continuation hops."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

ROOT_DOC = "flowgate.default.0353.0001-B"


def _provider(pid: str) -> dict:
    return {
        "id": pid,
        "name": pid,
        "exec_type": "cli",
        "kind": "codex",
        "enabled": True,
        "cli_command": "unused",
        "api_base_url": None,
        "api_model": None,
        "api_key_set": False,
        "api_key_hint": None,
    }


class FakeWfseq:
    def __init__(self) -> None:
        self.sequence = {"id": 353}
        self.items: list[dict] = []
        self.head_item_seq = 1

    def get_sequence_for_member_doc(self, _doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, _doc_id):
        return self.sequence

    def get_sequence_items(self, _seq_id):
        return [dict(item) for item in self.items]

    def get_effective_head(self, _seq_id):
        return next(
            (dict(item) for item in self.items if item["item_seq"] == self.head_item_seq),
            None,
        )


@pytest.fixture
def run_env(monkeypatch):
    wfseq = FakeWfseq()
    providers = [_provider("provider1"), _provider("provider2"), _provider("base")]
    effective = {"providers": providers, "source": "system", "registered_count": 3}

    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda _group_id: 1)
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _pid: {"project_name": "flowgate"})
    monkeypatch.setattr(svc.db_git, "get_config", lambda _pid: None)
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective", lambda _pid: effective)
    monkeypatch.setattr(
        svc.token_service,
        "issue",
        lambda **_kw: {
            "raw_token": "tok_0353",
            "token_id": "tok_0353",
            "expires_at": "2026-07-30T00:00:00+00:00",
            "scratch_dir": "C:/tmp",
        },
    )
    source_root = Path("C:/tmp")
    monkeypatch.setattr(
        svc.storage_paths,
        "resolve_project_src_root",
        lambda _pid, _branch, *, group_id: source_root,
    )
    monkeypatch.setattr(svc, "_create_scratch", lambda _pid, _run_id: source_root)
    monkeypatch.setattr(svc, "_git_status_paths", lambda _root: set())
    monkeypatch.setattr(svc, "_cleanup_retained_scratches", lambda _pid: None)
    monkeypatch.setattr(svc, "_worker", lambda _run, _chain, _prompt: None)
    monkeypatch.setattr(svc, "_runs", {})
    return {"wfseq": wfseq, "providers": providers}


def _start(env, *, instruction_mode: str, target: int, overrides: dict, pin: str = "base"):
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0353",
        doc_ref=ROOT_DOC,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target,
        continuation_review_mode=False,
        continuation_instruction_mode=instruction_mode,
        continuation_locale="ko",
        issued_to="worker",
        api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        mention_builder=lambda _raw, _scratch: "work",
        provider_id=pin,
        continuation_provider_overrides=overrides,
    )


@pytest.mark.parametrize("head_type,report_type", [("N", "NR"), ("T", "TR")])
def test_ai_direct_instruction_start_run_uses_instruction_row_override_with_explicit_pin(
    run_env, head_type, report_type
):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": head_type, "result_doc_id": None},
        {"item_seq": 2, "type": report_type, "result_doc_id": None},
    ]
    result = _start(
        run_env,
        instruction_mode="ai_direct",
        target=2,
        overrides={"1": "provider1", "2": "provider2"},
    )
    assert result["provider"]["id"] == "provider1"


def test_ai_direct_single_n_target_passes_admission_instead_of_422(run_env):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
    ]
    result = _start(
        run_env,
        instruction_mode="ai_direct",
        target=1,
        overrides={"1": "provider1"},
    )
    assert result["provider"]["id"] == "provider1"
    assert svc.get_run_record(result["run_id"])["docs_target"] == 1

def test_ai_direct_restarted_report_hop_uses_its_own_override_and_preserves_session_state(run_env):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
    ]
    overrides = {"1": "provider1", "2": "provider2"}
    first = _start(run_env, instruction_mode="ai_direct", target=2, overrides=overrides)
    first_run = svc.get_run_record(first["run_id"])
    assert first_run["continuation_instruction_mode"] == "ai_direct"
    assert first_run["continuation_provider_overrides"] == overrides
    assert first_run["continuation_base_provider_id"] == "base"

    first_run["status"] = "finished"
    run_env["wfseq"].items[0]["result_doc_id"] = "flowgate.default.0353.0005-N"
    run_env["wfseq"].head_item_seq = 2
    second = _start(run_env, instruction_mode="ai_direct", target=2, overrides=overrides)
    assert second["provider"]["id"] == "provider2"


def test_auto_approved_instruction_keeps_legacy_report_row_fold(run_env):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "T", "result_doc_id": None},
        {"item_seq": 2, "type": "TR", "result_doc_id": None},
    ]
    result = _start(
        run_env,
        instruction_mode="auto_approved",
        target=2,
        overrides={"1": "provider1", "2": "provider2"},
    )
    assert result["provider"]["id"] == "provider2"
    assert svc.get_run_record(result["run_id"])["docs_target"] == 1


@pytest.mark.parametrize("instruction_mode", ["auto_approved", "ai_direct"])
def test_ts_never_folds_to_tsr_for_provider_or_note(run_env, instruction_mode):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "TS", "result_doc_id": None},
        {"item_seq": 2, "type": "TSR", "result_doc_id": None},
    ]
    chain = run_env["providers"]
    provider = svc._resolve_continuation_hop_override(
        ROOT_DOC,
        {"1": "provider1", "2": "provider2"},
        chain,
        continuation_instruction_mode=instruction_mode,
    )
    note = svc._resolve_continuation_hop_note(
        ROOT_DOC,
        {"1": "TS note", "2": "TSR note"},
        continuation_instruction_mode=instruction_mode,
    )
    assert provider == "provider1"
    assert note == "TS note"


@pytest.mark.parametrize(
    "head_type,instruction_mode,expected_type",
    [
        ("N", "ai_direct", "N"),
        ("T", "ai_direct", "T"),
        ("N", "auto_approved", "NR"),
        ("T", "auto_approved", "TR"),
        ("TS", "ai_direct", "TS"),
        ("TS", "auto_approved", "TS"),
    ],
)
def test_unpinned_doctype_assignment_uses_mode_aware_worker_type_latent_non_b0001_path(
    run_env, monkeypatch, head_type, instruction_mode, expected_type
):
    """Latent no-explicit-pin defect coverage; this is not the direct B0001 UI path."""
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": head_type, "result_doc_id": None},
    ]
    seen = []
    monkeypatch.setattr(
        svc.ai_settings_service,
        "resolve_doctype_provider",
        lambda _project_id, doc_type: seen.append(doc_type) or f"assigned-{doc_type}",
    )
    result = svc._resolve_continuation_hop_provider(
        "flowgate",
        ROOT_DOC,
        continuation_instruction_mode=instruction_mode,
    )
    assert result == f"assigned-{expected_type}"
    assert seen == [expected_type]


@pytest.mark.parametrize("head_type,report_type", [("N", "NR"), ("T", "TR")])
def test_ai_direct_single_instruction_target_counts_one_and_passes_start_run(
    run_env, head_type, report_type
):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": head_type, "result_doc_id": None},
        {"item_seq": 2, "type": report_type, "result_doc_id": None},
    ]
    result = _start(
        run_env,
        instruction_mode="ai_direct",
        target=1,
        overrides={"1": "provider1"},
    )
    assert result["provider"]["id"] == "provider1"
    assert svc.get_run_record(result["run_id"])["docs_target"] == 1


def test_docs_target_counts_modes_mixed_items_and_ts(run_env):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
        {"item_seq": 3, "type": "T", "result_doc_id": None},
        {"item_seq": 4, "type": "TR", "result_doc_id": None},
        {"item_seq": 5, "type": "TS", "result_doc_id": None},
    ]
    assert svc._continuation_docs_target(
        ROOT_DOC, 5, continuation_instruction_mode="ai_direct"
    ) == 5
    assert svc._continuation_docs_target(
        ROOT_DOC, 5, continuation_instruction_mode="auto_approved"
    ) == 3
    assert svc._continuation_docs_target(
        ROOT_DOC, 1, continuation_instruction_mode="auto_approved"
    ) == 0
    assert svc._continuation_docs_target(
        ROOT_DOC, 5, continuation_instruction_mode="auto_approved"
    ) >= 1  # TS remains a worker document in both modes.


@pytest.mark.parametrize(
    "instruction_mode,item_seq",
    [("ai_direct", 1), ("auto_approved", 2)],
)
@pytest.mark.parametrize("key_kind", ["string", "integer"])
def test_override_key_formats_and_disabled_provider_fallback(
    run_env, instruction_mode, item_seq, key_kind
):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
    ]
    key = str(item_seq) if key_kind == "string" else item_seq
    assert svc._resolve_continuation_hop_override(
        ROOT_DOC,
        {key: "provider1"},
        run_env["providers"],
        continuation_instruction_mode=instruction_mode,
    ) == "provider1"
    assert svc._resolve_continuation_hop_override(
        ROOT_DOC,
        {key: "deleted-provider"},
        run_env["providers"],
        continuation_instruction_mode=instruction_mode,
    ) is None


@pytest.mark.parametrize(
    "instruction_mode,item_seq",
    [("ai_direct", 1), ("auto_approved", 2)],
)
def test_provider_override_and_note_resolve_the_same_item_seq(run_env, instruction_mode, item_seq):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
    ]
    provider = svc._resolve_continuation_hop_override(
        ROOT_DOC,
        {str(item_seq): "provider1"},
        run_env["providers"],
        continuation_instruction_mode=instruction_mode,
    )
    note = svc._resolve_continuation_hop_note(
        ROOT_DOC,
        {str(item_seq): f"note-{item_seq}"},
        continuation_instruction_mode=instruction_mode,
    )
    assert (provider, note) == ("provider1", f"note-{item_seq}")


def test_missing_and_unknown_modes_preserve_legacy_auto_approved_fold(run_env):
    run_env["wfseq"].items = [
        {"item_seq": 1, "type": "N", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "result_doc_id": None},
    ]
    chain = run_env["providers"]
    overrides = {"1": "provider1", "2": "provider2"}
    legacy = svc._resolve_continuation_hop_override(ROOT_DOC, overrides, chain)
    unknown = svc._resolve_continuation_hop_override(
        ROOT_DOC,
        overrides,
        chain,
        continuation_instruction_mode="future-mode",
    )
    assert legacy == unknown == "provider2"
    assert svc._continuation_docs_target(ROOT_DOC, 2) == 1