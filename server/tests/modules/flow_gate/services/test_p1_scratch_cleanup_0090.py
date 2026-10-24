"""T#4: minimal spec preparation/finally failure contracts (no live DB or process)."""
from __future__ import annotations

import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.flow_gate.services import spec_execution_service as execution
from modules.flow_gate.services import test_run_service as runner


@pytest.fixture
def spec_run(monkeypatch, tmp_path):
    doc = {"doc_id": "ts-fixture", "revision_no": 1}
    run = {"run_id": "run-fixture", "doc_id": doc["doc_id"], "revision_no": 1}
    scratch = tmp_path / "test_runs" / run["run_id"]
    scratch.mkdir(parents=True)
    sentinel = scratch / "previous.txt"
    sentinel.write_text("previous execution", encoding="utf-8")
    active = SimpleNamespace(cancel_event=threading.Event())
    terminal, metadata, phases, finished, unregistered = [], [], [], [], []

    monkeypatch.setattr(execution.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(execution.test_spec_service, "load_result_meta",
                        lambda _raw: {"spec_cases": [], "selected_case_ids": []})
    monkeypatch.setattr(runner, "_scratch_dir", lambda *_: scratch)
    monkeypatch.setattr(runner, "_register_active_run", lambda _id: active)
    monkeypatch.setattr(runner, "_unregister_active_run", unregistered.append)
    monkeypatch.setattr(runner, "_bail_if_cancelled", lambda *_: False)
    monkeypatch.setattr(runner, "_get_run_lock", lambda _id: contextlib.nullcontext())
    monkeypatch.setattr(execution, "_enter_phase", lambda _d, _r, phase: phases.append(phase))
    monkeypatch.setattr(execution, "merge_run_meta",
                        lambda _id, **kw: metadata.append(kw) or {})
    monkeypatch.setattr(execution.db_test_runs, "finish_run", lambda **kw: terminal.append(kw))
    monkeypatch.setattr(execution.db_test_runs, "get_run", lambda _id: terminal[-1])
    monkeypatch.setattr(runner, "_emit_finished", lambda *_: finished.append(True))
    stop = Mock()
    monkeypatch.setattr(execution, "_stop_chain_without_result", stop)
    measure = Mock(side_effect=AssertionError("source capture must not start"))
    process = Mock(side_effect=AssertionError("pytest must not start"))
    monkeypatch.setattr(execution.test_basis_service, "measure_run", measure)
    monkeypatch.setattr(execution.ExistingRunnerAdapter, "run_pytest", process)
    failure = PermissionError(13, "private path must not be logged", str(sentinel))
    delete = Mock(side_effect=failure)
    monkeypatch.setattr(runner.storage_fs, "remove_managed_tree", delete)
    return SimpleNamespace(**locals())


def test_retry_cleanup_failure_refuses_preparation(spec_run, caplog):
    env = spec_run
    execution.execute(env.run)

    assert env.terminal == [{"run_id": env.run["run_id"], "status": "failed",
                             "error": "basis_capture_failed"}]
    assert env.metadata[-1]["prepare_refused"] == {
        "error": "basis_capture_failed", "reasons": "scratch_cleanup_failed"}
    assert env.phases == [execution.PHASE_PREPARING]
    env.measure.assert_not_called()
    env.process.assert_not_called()
    env.delete.assert_called_once_with(env.scratch, env.scratch.parent, missing_ok=True)
    assert env.sentinel.read_text(encoding="utf-8") == "previous execution"
    assert not (env.scratch / "source").exists()
    assert env.finished == [True] and env.unregistered == [env.run["run_id"]]
    env.stop.assert_called_once()
    assert "errno=13" in caplog.text and "owner=test-run" in caplog.text
    assert str(env.scratch) not in caplog.text
    assert "private path must not be logged" not in caplog.text


class OriginalAbort(BaseException):
    pass


@pytest.mark.parametrize("outcome", ["result", "handled_error", "propagated_error"])
def test_finally_cleanup_failure_preserves_outcome(spec_run, monkeypatch, outcome):
    env = spec_run
    basis = {"basis_id": "fixture"}
    monkeypatch.setattr(execution.ExecutionRootResolver, "prepare",
                        lambda *_: (basis, env.scratch / "source", env.scratch))
    monkeypatch.setattr(execution.test_basis_service, "source_identity", lambda _: {})
    original = OriginalAbort("original") if outcome == "propagated_error" else RuntimeError("original")

    def record(*_):
        if outcome != "result":
            raise original
    monkeypatch.setattr(execution, "_record_basis", record)

    if outcome == "propagated_error":
        with pytest.raises(OriginalAbort) as caught:
            execution.execute(env.run)
        assert caught.value is original
        assert env.terminal == [] and env.finished == []
    else:
        execution.execute(env.run)
        if outcome == "result":
            assert env.terminal == [{"run_id": env.run["run_id"], "status": "passed",
                                     "case_passed": 0, "case_failed": 0}]
            env.stop.assert_not_called()
        else:
            assert env.terminal == [{"run_id": env.run["run_id"], "status": "failed",
                                     "error": "spec_execution_error"}]
            env.stop.assert_called_once()
        assert env.finished == [True]
    env.delete.assert_called_once_with(env.scratch, env.scratch.parent, missing_ok=True)
    assert env.unregistered == [env.run["run_id"]]
    assert env.sentinel.exists()
