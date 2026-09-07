"""Reviewer-facing actual/reported scope manifest in build_review_mention (0493 T0005).

T1: a reviewer must not rely only on the TR body's self-reported changed-files list.
The server-computed manifest -- one row per ACTUAL worktree change, tagged reported /
prior-reported / unreported -- must be visible in the review mention, and an unreported
path (TRV-004) must carry an explicit investigate-before-approving instruction rather
than a silent list.
"""
from modules.flow_gate.services import mention_service


_TOKEN_REC = {
    "project": "test",
    "group_id": "test.none.0493",
    "scratch_dir": r"D:\test\storage2\work\test\tok_x",
}


def _target(meta):
    return {
        "doc_id": "test.none.0493.0002-TR",
        "type_code": "TR",
        "seq": 2,
        "title": "TR under review",
        "module": "none",
        "project_id": "test",
        "meta": meta,
    }


def _build(meta):
    return mention_service.build_review_mention(
        token_rec=_TOKEN_REC,
        target_doc=_target(meta),
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        raw_token="RAWTOKEN123",
    )


def test_scope_context_section_lists_actual_status_and_reporting_state():
    meta = {
        "tr_scope": {
            "verdict": "reject",
            "codes": ["TRV-004"],
            "unconfirmed": [],
            "file_manifest": {
                "total": 2,
                "truncated": False,
                "items": [
                    {
                        "path": "server/a.py", "actual_status": "M", "old_path": None,
                        "reported_in_current_tr": True, "reported_in_prior_tr": False,
                        "reporting_state": "reported",
                    },
                    {
                        "path": "server/leftover.py", "actual_status": "A", "old_path": None,
                        "reported_in_current_tr": False, "reported_in_prior_tr": False,
                        "reporting_state": "unreported",
                    },
                ],
            },
        }
    }
    m = _build(meta)

    assert "## Actual changed-files manifest (server-computed)" in m
    assert "server/a.py [M]" in m
    assert "-- reported" in m
    assert "server/leftover.py [A]" in m
    assert "-- unreported" in m
    assert "TRV-004" in m
    # An unreported path must come with an investigate-before-approving instruction,
    # not a bare list -- this is the T1 acceptance criterion, not a display nicety.
    assert "Investigate EACH one" in m
    assert "flag it for removal" in m
    assert "do not approve" in m


def test_scope_context_section_states_empty_explicitly_not_omitted():
    """An empty manifest must say so explicitly -- omitting the section would let a
    reviewer fall back to reading only the TR's self-report (the exact defect T1 fixes)."""
    meta = {
        "tr_scope": {
            "verdict": "pass", "codes": [], "unconfirmed": [],
            "file_manifest": {"total": 0, "truncated": False, "items": []},
        }
    }
    m = _build(meta)

    assert "## Actual changed-files manifest (server-computed)" in m
    assert "(no actual changes detected" in m
    assert "Unreported changes: (none)" in m


def test_scope_context_section_shows_rename_old_path_and_unconfirmed():
    meta = {
        "tr_scope": {
            "verdict": "warn", "codes": ["TRV-003"],
            "unconfirmed": ["server/ghost.py"],
            "file_manifest": {
                "total": 1, "truncated": False,
                "items": [
                    {
                        "path": "server/new_name.py", "actual_status": "R",
                        "old_path": "server/old_name.py",
                        "reported_in_current_tr": True, "reported_in_prior_tr": False,
                        "reporting_state": "reported",
                    },
                ],
            },
        }
    }
    m = _build(meta)

    assert "renamed from server/old_name.py" in m
    assert "server/ghost.py" in m
    assert "unconfirmed" in m.lower()


def test_scope_context_section_absent_when_no_stored_verdict():
    """A document with no stored tr_scope (non-mutating type, or predates the check) has
    nothing authoritative to add, so the section is skipped rather than shown empty."""
    m = mention_service.build_review_mention(
        token_rec=_TOKEN_REC,
        target_doc=_target(meta=None),
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        raw_token="RAWTOKEN123",
    )
    assert "Actual changed-files manifest" not in m
