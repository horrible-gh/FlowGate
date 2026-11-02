"""0119 T-4: exact fast-forward evidence and job wire compatibility (no Git or DB writes)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowgate.gate_decisions import git_adapter, intake


@pytest.mark.parametrize("head,pre,source,ancestor,merging,expected", [
    ("source", "before", "source", True, False, True),
    ("other", "before", "source", True, False, False),
    ("source", "before", "source", False, False, False),
    ("source", "before", "source", None, False, False),
    ("source", "before", "source", True, True, False),
    ("source", "source", "source", True, False, False),
    ("source", None, "source", True, False, False),
    (None, "before", "source", True, False, False),
])
def test_fast_forward_requires_the_exact_recorded_inputs(monkeypatch, head, pre, source, ancestor, merging, expected):
    monkeypatch.setattr(git_adapter, "is_ancestor", lambda root, a, b: ancestor)
    monkeypatch.setattr(git_adapter, "_gs", lambda: SimpleNamespace(_rev_parse=lambda root, ref: head, _merge_in_progress=lambda root: merging))
    assert git_adapter._fast_forward_landed(Path("unused"), pre, source) is expected


def test_job_wire_keeps_old_fields_and_adds_a_nullable_phase_note():
    assert intake._job_wire(None) is None
    job = {"job_id": "j", "kind": "gate_publish", "status": "pending", "phase": "start", "last_error_code": None}
    assert intake._job_wire(job) == {**job, "phase_note": None}
    job.update(phase_note="gate_conflict", last_error_code="merge_conflict")
    assert intake._job_wire(job) == job
