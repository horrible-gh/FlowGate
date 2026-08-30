"""zdiff3 base 청크 → 서버 멘트 전달 경로 (flowgate.default.0478 WP0003 T#3).

T0008 항목 3: `_split_conflict_chunks()`와 `_build_conflict_mention()`이 zdiff3 merge conflict style로 실제 관측된 base 마커를 손실 없이 실어 나르는지 실측한다. 아래 zdiff3 marker 문자열은
test_git_integration_0115.py::TestGitEndToEnd::test_conflict_resolve_flow가 실제 git merge 출력에서 관측한 그대로다(임의 작문 아님) — `<<<<<<< HEAD` / `mainline version` / `||||||| line1` / `line1` /
`=======` / `group version` / `>>>>>>> group/branch`.
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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
    """T0008 item 3, integration half: feed the same real zdiff3 content that
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
