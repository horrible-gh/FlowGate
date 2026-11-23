"""flowgate.v02test.0066 #36-1: the Flow tab's 「AI 해결 중 · n/m」 reads its n/m from
``GET /ai-invoke/active?conflict_progress=1`` (read only, opt-in)."""
from modules.flow_gate.services import git_service
from modules.flow_gate.services.ai_invoke import diagnostics


class _Service:
    def __init__(self, run):
        self.run = run

    def get_run_record(self, run_id):
        return self.run if self.run and self.run.get("run_id") == run_id else None


def _resolving(run_id="aiv_1"):
    return {"ok": True, "active": True, "run_id": run_id, "status": "running", "action_scope": "resolve_conflict"}


def _wire(monkeypatch, run, files, calls=None):
    monkeypatch.setattr(diagnostics, "_svc", lambda: _Service(run))
    monkeypatch.setattr(git_service, "merge_session_owner_args", lambda merge_id, group_id: (group_id, None))

    def list_conflicts(group_id, merge_id, **kwargs):
        if calls is not None:
            calls.append((group_id, merge_id, kwargs))
        if isinstance(files, Exception):
            raise files
        return {"ok": True, "merge_id": merge_id, "files": files}

    monkeypatch.setattr(git_service, "list_conflicts", list_conflicts)


def test_progress_counts_files_without_markers_over_all_session_files(monkeypatch):
    calls = []
    files = [{"path": "a.py", "conflict_count": 0}, {"path": "b.py", "conflict_count": 2},
             {"path": "c.py", "conflict_count": 1}, {"path": "d.py"}]
    _wire(monkeypatch, {"run_id": "aiv_1", "group_id": "p.m.1", "merge_id": 46}, files, calls)
    assert diagnostics.conflict_progress(_resolving()) == {"done": 2, "total": 4}
    assert calls == [("p.m.1", 46, {})]


def test_a_branch_merge_session_is_read_by_its_project(monkeypatch):
    calls = []
    _wire(monkeypatch, {"run_id": "aiv_1", "group_id": "p.m.1", "merge_id": 7}, [{"path": "a", "conflict_count": 1}], calls)
    monkeypatch.setattr(git_service, "merge_session_owner_args", lambda merge_id, group_id: (None, "p"))
    assert diagnostics.conflict_progress(_resolving()) == {"done": 0, "total": 1}
    assert calls == [(None, 7, {"project_id": "p"})]


def test_no_progress_for_other_runs_and_no_file_read(monkeypatch):
    calls = []
    _wire(monkeypatch, {"run_id": "aiv_1", "group_id": "p.m.1", "merge_id": 46}, [{"path": "a", "conflict_count": 0}], calls)
    assert diagnostics.conflict_progress({"ok": True, "active": False}) is None
    assert diagnostics.conflict_progress({**_resolving(), "action_scope": "chat"}) is None
    assert diagnostics.conflict_progress({**_resolving(), "active": False}) is None
    assert calls == []


def test_no_progress_when_the_run_or_its_session_cannot_be_read(monkeypatch):
    _wire(monkeypatch, None, [])
    assert diagnostics.conflict_progress(_resolving()) is None
    _wire(monkeypatch, {"run_id": "aiv_1", "group_id": "p.m.1", "merge_id": None}, [])
    assert diagnostics.conflict_progress(_resolving()) is None
    _wire(monkeypatch, {"run_id": "aiv_1", "group_id": "p.m.1", "merge_id": 46}, RuntimeError("session gone"))
    assert diagnostics.conflict_progress(_resolving()) is None


def test_active_route_adds_the_field_only_when_asked(monkeypatch):
    """The route keeps every existing caller's answer unchanged; the count is opt-in."""
    import inspect
    from modules.flow_gate.api.v1 import ai_invoke_routes

    params = inspect.signature(ai_invoke_routes.get_active_ai_invoke).parameters
    assert params["conflict_progress"].default is False
    source = inspect.getsource(ai_invoke_routes.get_active_ai_invoke)
    assert "if conflict_progress:" in source
    assert "ai_invoke_service.get_active_status(group_id)" in source
