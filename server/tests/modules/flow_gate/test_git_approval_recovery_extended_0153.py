"""Recovery races, response loss and new remote histories, using real Git/SQLite."""
import copy
import json
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest

from tests.modules.flow_gate.test_git_approval_recovery_0153 import (
    real, env, project, repo, patch_store, tmp_db, ready, request, saved, approve,
    fail_push, names, git, git_rc, ref_value, parents_of, pending, terminal, fa,
)
from tests.modules.flow_gate.test_git_reconcile_0153 import remote_commit
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, branch_merge_publish as bmp
from modules.flow_gate.services.git import session_claim
from modules.flow_gate.db import merge_request_binding as bindings


def test_A2_concurrent_approval_is_serialized_before_git(real):
    mid, fp = ready(real)
    attempt = str(uuid.uuid4())
    entered, resume = threading.Event(), threading.Event()
    def barrier(args):
        if 'commit' in args:
            entered.set()
            assert resume.wait(30), 'approval barrier timeout'
    real.hooks.append(barrier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(approve, real, mid, attempt, fp)
        try:
            assert entered.wait(30)
            before = copy.deepcopy(saved(mid))
            commands = list(real.commands)
            real.clock['instance'] = 'inst_B'
            for competing_id in (attempt, str(uuid.uuid4())):
                with pytest.raises(gs.GitServiceError) as exc:
                    approve(real, mid, competing_id, fp)
                assert exc.value.code == 'git_busy'
            assert real.commands == commands and saved(mid) == before
        finally:
            real.clock['instance'] = 'inst_A'
            resume.set()
        assert first.result(timeout=60)['result']['approval']['approved']
    assert names(real).count('commit') == 1 and names(real).count('push') == 1
    terminal(real, mid)
    real.commands.clear()
    assert approve(real, mid, attempt, fp)['result']['approval']['approved']
    assert not real.commands


def test_A3_approval_commit_response_loss_cannot_resurrect_intent(real, monkeypatch):
    mid, fp = ready(real)
    original = approval_intent.commit_deferred_approval
    consumed = []
    def lose(*args, **kwargs):
        result = original(*args, **kwargs)
        consumed.append(result)
        raise RuntimeError('lost approval response after transaction commit')
    monkeypatch.setattr(approval_intent, 'commit_deferred_approval', lose)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    assert len(consumed) == 1 and consumed[0]['approved']
    assert approval_intent.intent_of_context(saved(mid)) is None
    real.commands.clear()
    out, _ = request(real, mid)
    assert out['result']['approval']['approved']
    assert len(consumed) == 1 and not real.commands
    terminal(real, mid)
