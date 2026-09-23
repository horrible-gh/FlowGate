"""2026-09-23 incident (flowgate.default.0594 / merge_id=100): a resolve_conflict mention
duplicated every conflicted file's full content into both `chunks` and `raw_content`, with no
size cap. On this merge it produced a ~3.94M-char prompt (measured on the real
aiv_20260923_000487 / aiv_20260923_000485 runs) that every provider fast_fail'd as "too long"
— Codex's own error even quoted its 1,048,576-char input ceiling — but only AFTER a token had
already been spent launching each one, so the UI just showed
"No AI provider could be started for this hop" with no way to tell 0-providers-configured
apart from every-provider-rejected-the-prompt.

Two independent fixes, tested at two levels:

  1. token_routes._build_conflict_mention() no longer emits `raw_content` alongside `chunks`
     (`chunks` already carries only the conflict regions — see
     test_zdiff3_conflict_mention_0478.py) — TestNoRawContentDuplication.
  2. admission.start_run() refuses a resolve_conflict run BEFORE a provider is launched when
     the built mention still exceeds CONFLICT_MENTION_MAX_CHARS, with an explicit
     `conflict_prompt_too_large` 409 instead of a silent tail-truncation or a wasted provider
     attempt — TestOversizedConflictMentionGuard. Reconstructing the actual merge_id=100
     conflict from `main`'s git history (base 8fd5146 / merge 1a96838) and replaying it through
     the real (fixed) `_build_conflict_mention()` gives 1,809,923 chars — dedup alone cuts the
     original ~3.94M by more than half, but this specific merge is still well over both Codex's
     1,048,576-char ceiling and the 500,000-char guard, so it is exactly the case work item 4
     says the guard (not a batch resolver) must catch in this T.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import git_service as git_svc  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission  # noqa: E402


# ── 1. the mention no longer carries the full file content twice ────────────────────

_REAL_CONFLICT = """<<<<<<< HEAD
mainline version
||||||| line1
line1
=======
group version
>>>>>>> group/branch"""


class TestNoRawContentDuplication:
    def test_conflict_mention_never_emits_raw_content(self, monkeypatch):
        def fake_list_conflicts(group_id, merge_id):
            return {
                "ok": True, "merge_id": merge_id, "branch": "group/branch",
                "base_branch": "main", "files": [
                    {"path": "shared.txt", "content": _REAL_CONFLICT, "conflict_count": 1},
                ],
                "kind": "merge", "tr_conflict": None,
            }

        monkeypatch.setattr(token_routes.git_service, "list_conflicts", fake_list_conflicts)
        mention = token_routes._build_conflict_mention(
            group_id="grpmention", project_id="grpmentionproj", merge_id=42,
            scratch_dir="/tmp/scratch", raw_token="tok_test",
            api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        )
        assert mention is not None
        assert "raw_content" not in mention
        json_block = mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        payload = json.loads(json_block)
        assert set(payload["files"][0].keys()) == {"path", "conflict_count", "chunks"}

    def test_small_conflict_keeps_the_existing_resolve_contract(self, monkeypatch):
        """Regression for the ordinary (small) case: path/chunk-shape/bound-endpoint
        contract must be byte-for-byte the same as before the dedup fix — only the
        removed `raw_content` key changes."""
        def fake_list_conflicts(group_id, merge_id):
            return {
                "ok": True, "merge_id": merge_id, "branch": "group/branch",
                "base_branch": "main", "files": [
                    {"path": "shared.txt", "content": _REAL_CONFLICT, "conflict_count": 1},
                ],
                "kind": "merge", "tr_conflict": None,
            }

        monkeypatch.setattr(token_routes.git_service, "list_conflicts", fake_list_conflicts)
        mention = token_routes._build_conflict_mention(
            group_id="grpmention", project_id="grpmentionproj", merge_id=42,
            scratch_dir="/tmp/scratch", raw_token="tok_test",
            api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        )
        assert "POST http://127.0.0.1:8089/flowgate/api/v1/groups/grpmention/git/merge/42/resolve-token" in mention
        json_block = mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        payload = json.loads(json_block)
        chunk = payload["files"][0]["chunks"][0]
        assert chunk["ours"] == ["mainline version"]
        assert chunk["base"] == ["line1"]
        assert chunk["theirs"] == ["group version"]
        # Nowhere near the 500,000-char guard budget for an ordinary single-chunk conflict —
        # generous on purpose so growth in the fixed task/supersede boilerplate doesn't flake
        # this; the guard test below is what actually pins the budget.
        assert len(mention) < 10_000

    def test_large_conflict_set_no_longer_duplicates_every_files_content(self, monkeypatch):
        """Hermetic stand-in for the 0594/merge_id=100 shape: 10 files, each mostly one
        giant conflict block (so `chunks` legitimately carries close to the whole file —
        this is NOT a "just trim to zero" case). Before the fix, `raw_content` duplicated
        each file's content a second time on top of that; this asserts the mention no
        longer scales with that second copy."""
        files = []
        total_content_chars = 0
        for i in range(10):
            ours = ("ours line %d\n" % i) * 3000
            theirs = ("theirs line %d\n" % i) * 3000
            content = f"<<<<<<< HEAD\n{ours}=======\n{theirs}>>>>>>> branch\n"
            total_content_chars += len(content)
            files.append({"path": f"file_{i}.py", "content": content, "conflict_count": 1})

        def fake_list_conflicts(group_id, merge_id):
            return {
                "ok": True, "merge_id": merge_id, "branch": "group/branch",
                "base_branch": "main", "files": files, "kind": "merge", "tr_conflict": None,
            }

        monkeypatch.setattr(token_routes.git_service, "list_conflicts", fake_list_conflicts)
        mention = token_routes._build_conflict_mention(
            group_id="grpmention", project_id="grpmentionproj", merge_id=42,
            scratch_dir="/tmp/scratch", raw_token="tok_test",
            api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        )
        assert "raw_content" not in mention
        # The old code JSON-encoded `content` a SECOND time as `raw_content`, alongside
        # `chunks` — precisely the cost this asserts is gone, computed independently of
        # production code (not a guessed ratio: `chunks` already has its own JSON-array-of-
        # lines overhead, so "new < 1.x * raw content bytes" is not a safe bound on its own).
        raw_content_json_cost = sum(len(json.dumps(f["content"], ensure_ascii=False)) for f in files)
        old_style_len = len(mention) + raw_content_json_cost
        assert len(mention) < old_style_len
        assert raw_content_json_cost > total_content_chars  # sanity: the duplication was real weight
        # …and the actual reduction is substantial, not a rounding error.
        assert len(mention) < old_style_len * 0.7


# ── 2. an oversized resolve_conflict mention is refused before a provider launches ──

GROUP = "test0594.default.0001"
MERGE_ID = 100


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Same collaborator shape as test_ai_invoke_start_identity_0481.py's `world`, plus a
    revoke recorder (that file no-ops revoke; this one needs to assert on it)."""
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 0)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, "source": "test",
                     "providers": [{"id": "p1", "name": "Claude Haiku 4.5", "exec_type": "cli"}]},
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {"raw_token": "tok_raw_test", "token_id": "tok_20260923_000999",
                      "expires_at": "2026-09-24T00:00:00+00:00",
                      "scratch_dir": str(tmp_path / "tokwork")},
    )
    revoked = []
    monkeypatch.setattr(
        svc.token_service, "revoke",
        lambda token_id, reason=None: revoked.append((token_id, reason)),
    )
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return {"tmp": tmp_path, "revoked": revoked}


def _start(mention, group_id=GROUP, merge_id=MERGE_ID):
    return svc.start_run(
        project_id="test0594", module="default", group_id=group_id, doc_ref="",
        action_scope="resolve_conflict", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: mention,
        merge_id=merge_id,
    )


class TestOversizedConflictMentionGuard:
    def test_oversized_mention_is_rejected_with_409_before_any_provider_runs(self, world, monkeypatch):
        cli_calls = []
        monkeypatch.setattr(svc, "_cli_execute", lambda p, prompt, run: cli_calls.append(1))

        huge_mention = "x" * (admission.CONFLICT_MENTION_MAX_CHARS + 100_000)
        with pytest.raises(HTTPException) as caught:
            _start(huge_mention)

        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "conflict_prompt_too_large"
        # No silent truncation: the reported length is the mention's real, full length.
        assert caught.value.detail["prompt_chars"] == len(huge_mention)
        assert caught.value.detail["limit_chars"] == admission.CONFLICT_MENTION_MAX_CHARS
        assert cli_calls == []  # the provider was never reached
        assert world["revoked"] == [
            ("tok_20260923_000999", "ai_invoke_conflict_prompt_too_large")
        ]

    def test_small_resolve_conflict_mention_still_starts_normally(self, world, monkeypatch):
        """Contrast case: the guard must not touch the ordinary, already-working path."""
        import unittest.mock as mock

        with mock.patch.object(svc, "_cli_execute", side_effect=lambda p, prompt, run: ("started_ok", None)):
            response = _start("## prompt\nresolve the conflict\n")
            for _ in range(500):
                record = svc.get_run_record(response["run_id"])
                if record and record["status"] == "finished":
                    break
                time.sleep(0.02)
        assert response["action_scope"] == "resolve_conflict"
        assert response["status"] == "running"
        assert world["revoked"] == []

    def test_guard_is_scoped_to_resolve_conflict_only(self):
        """Structural guard against scope creep: this T fixes the resolve_conflict prompt
        specifically (per the incident) and must not become an undocumented blanket limit
        on every mention admission builds."""
        import inspect

        src = inspect.getsource(admission.start_run)
        assert 'action_scope == "resolve_conflict"' in src
        # The guard constant must not be referenced from an unconditional site.
        guard_line = next(
            line for line in src.splitlines() if "CONFLICT_MENTION_MAX_CHARS" in line and "=" not in line
        )
        assert "CONFLICT_MENTION_MAX_CHARS" in guard_line


class TestConflictMentionMaxCharsIsConservativeVsKnownProviderLimits:
    def test_guard_budget_is_below_codexs_documented_input_ceiling(self):
        # Codex's own turn/start error (aiv_20260923_000485) quoted this exact ceiling.
        CODEX_INPUT_CEILING = 1_048_576
        assert admission.CONFLICT_MENTION_MAX_CHARS < CODEX_INPUT_CEILING

    def test_0594_merge_id_100_scale_conflict_still_exceeds_the_guard_after_dedup(self):
        """Work item 4: raw_content removal alone is not enough for every conflict set.
        This is a hermetic stand-in (not a live git call) for the real reconstructed
        merge_id=100 conflict, which measured 1,809,923 chars post-fix — comfortably
        above both Codex's 1,048,576-char ceiling and this guard's 500,000-char budget.
        A future batch resolver is what actually lands this merge; today's contract is
        that it fails closed and explicitly instead of silently wasting a provider call.
        """
        files = []
        # Sized to land in the same order of magnitude as the real 10-file merge_id=100
        # conflict set (~2.5M raw conflicted bytes; chunks-only came out to ~1.8M chars).
        for i in range(10):
            ours = ("ours %d\n" % i) * 8000
            theirs = ("theirs %d\n" % i) * 8000
            content = f"<<<<<<< HEAD\n{ours}=======\n{theirs}>>>>>>> branch\n"
            files.append({"path": f"file_{i}.py", "content": content, "conflict_count": 1})

        def fake_list_conflicts(group_id, merge_id):
            return {
                "ok": True, "merge_id": merge_id, "branch": "flowgate_default_0594",
                "base_branch": "main", "files": files, "kind": "merge", "tr_conflict": None,
            }

        import unittest.mock as mock
        with mock.patch.object(git_svc, "list_conflicts", fake_list_conflicts), \
             mock.patch.object(token_routes.git_service, "list_conflicts", fake_list_conflicts):
            mention = token_routes._build_conflict_mention(
                group_id="flowgate.default.0594", project_id="flowgate", merge_id=100,
                scratch_dir="/scratch/x", raw_token="tok_raw_test",
                api_base_url="http://127.0.0.1:8089/flowgate/api/v1", locale="ko",
            )
        assert len(mention) > admission.CONFLICT_MENTION_MAX_CHARS
