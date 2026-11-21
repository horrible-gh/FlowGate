"""zdiff3 base-chunk propagation into server mentions.

verifies _split_conflict_chunks and _build_conflict_mention preserve
base markers actually observed in zdiff3 merge output by
TestGitEndToEnd.test_conflict_resolve_flow: <<<<<<< HEAD / mainline version /
Base marker: ||||||| line1 / line1 / ======= / group version / >>>>>>> group/branch.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"K" * 32).decode()
os.environ["FLOWGATE_STORAGE_DIR"] = tempfile.mkdtemp(prefix="fg-zdiff3-mention-0478-")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))


def test_split_conflict_chunks_with_zdiff3_base():
    """Verify _split_conflict_chunks() correctly extracts base content from zdiff3 markers.
    
    This is a standalone unit test that doesn't depend on git fixtures — it only tests
    the conflict parsing function with actual zdiff3 conflict markers from test_conflict_resolve_flow.
    """
    from modules.flow_gate.api.token_routes import _split_conflict_chunks

    # Real zdiff3 conflict content from test_conflict_resolve_flow
    conflict_content = """<<<<<<< HEAD
mainline version
||||||| line1
line1
=======
group version
>>>>>>> group/branch"""

    chunks = _split_conflict_chunks(conflict_content)
    assert len(chunks) == 1
    chunk = chunks[0]

    # Verify all sections are correctly populated
    assert chunk["ours_label"] == "HEAD"
    assert chunk["theirs_label"] == "group/branch"
    assert chunk["ours"] == ["mainline version"]
    assert chunk["base"] == ["line1"]
    assert chunk["theirs"] == ["group version"]


def test_build_conflict_mention_carries_base_from_zdiff3(monkeypatch):
    """integration half: feed the same real zdiff3 content that
    list_conflicts() returns through _build_conflict_mention() (not just
    _split_conflict_chunks() directly) and assert the emitted mention's JSON
    `files[].chunks[].base` is non-empty and equals the common-ancestor content.
    """
    import json

    from modules.flow_gate.api import token_routes
    from modules.flow_gate.services import git_service as svc

    # Same real zdiff3 content asserted in test_split_conflict_chunks_with_zdiff3_base,
    # observed from test_conflict_resolve_flow's actual git merge output.
    conflict_content = """<<<<<<< HEAD
mainline version
||||||| line1
line1
=======
group version
>>>>>>> group/branch"""

    def fake_list_conflicts(group_id, merge_id):
        assert group_id == "grpmention"
        assert merge_id == 42
        return {
            "ok": True,
            "merge_id": merge_id,
            "branch": "group/branch",
            "base_branch": "main",
            "files": [
                {"path": "shared.txt", "content": conflict_content, "conflict_count": 1},
            ],
            "kind": "merge",
            "tr_conflict": None,
        }

    monkeypatch.setattr(token_routes.git_service, "list_conflicts", fake_list_conflicts)
    monkeypatch.setattr(svc, "list_conflicts", fake_list_conflicts)

    mention = token_routes._build_conflict_mention(
        group_id="grpmention",
        project_id="grpmentionproj",
        merge_id=42,
        scratch_dir="/tmp/scratch",
        raw_token="tok_test",
        api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
    )
    assert mention is not None

    json_block = mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    payload = json.loads(json_block)
    chunks = payload["files"][0]["chunks"]
    assert len(chunks) == 1
    assert chunks[0]["base"] == ["line1"]
    assert chunks[0]["ours"] == ["mainline version"]
    assert chunks[0]["theirs"] == ["group version"]
