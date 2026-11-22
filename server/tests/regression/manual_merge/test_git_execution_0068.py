"""Real Git coverage for explicit update and rejected-push reconciliation."""
import pytest
from manual_merge_0066_support import _git, group, work, manual, results, push_result, rev, held_locks, needs_git, GitServiceError
from test_fg_gate_intake_0085 import product, env, db_path, FA
from test_fg_gate_publish_0085 import git_env, _work, _rev, PROJECT
from types import SimpleNamespace

@pytest.fixture
def proj(git_env):
    r=git_env
    return SimpleNamespace(pid=PROJECT, base=r['base'], bare=r['bare'],
        wt=lambda gid:r['wt'], origin_files=lambda branch:_git(['ls-tree','-r','--name-only',branch],cwd=r['bare']).splitlines())

@needs_git
def test_clean_update_refuses_dirty_files_without_committing(proj):
    from modules.flow_gate.services.git import finalize
    gid=FA
    work(proj,gid,'local.txt','local\n')
    before=rev(proj.wt(gid),'HEAD')
    (proj.wt(gid)/'dirty.txt').write_text('keep me',encoding='utf-8')
    with pytest.raises(GitServiceError) as exc:
        finalize.update_from_base(gid,require_clean=True)
    assert exc.value.code == 'uncommitted_changes'
    assert rev(proj.wt(gid),'HEAD') == before
    assert (proj.wt(gid)/'dirty.txt').read_text() == 'keep me'
    assert held_locks(proj.pid) == []

@needs_git
def test_remote_reconciliation_keeps_both_histories_and_pushes(proj):
    from flowgate.gate_decisions.manual_sync import sync
    gid=FA
    work(proj,gid,'local.txt','local\n')
    out=manual(gid,target='main')
    from modules.flow_gate.services.git.manual_merge import merge_view
    assert merge_view(gid,'main')['work_base_behind'] == 0
    # A remote-only commit from a separate checkout races the local merge.
    seed=proj.base.parent/'external'
    _git(['clone',str(proj.bare),str(seed)],cwd=proj.base.parent)
    _git(['config','user.name','External'],cwd=seed)
    _git(['config','user.email','external@example.test'],cwd=seed)
    (seed/'remote.txt').write_text('remote\n',encoding='utf-8')
    _git(['add','remote.txt'],cwd=seed); _git(['commit','-m','remote'],cwd=seed); _git(['push','origin','main'],cwd=seed)
    remote=rev(seed,'HEAD')
    assert sync(gid,'main')['status'] == 'updated'
    _git(['merge-base','--is-ancestor',remote,'main'],cwd=proj.base)
    row=results(gid)['results'][0]
    pushed=push_result(gid,row['merge_id'],row['merge_commit'])
    assert pushed['pushed_head'] == rev(proj.bare,'main')
    assert {'local.txt','remote.txt'} <= set(proj.origin_files('main'))
    assert held_locks(proj.pid) == []


@needs_git
def test_auto_commit_finishes_real_publish_job(git_env, monkeypatch):
    from test_fg_gate_intake_0085 import _linear, setting, _start, _jobs, _current, _nodes
    from test_fg_gate_publish_0085 import _run_job
    from flowgate.gate_decisions import legacy_adapter
    monkeypatch.setattr(legacy_adapter,'notify_job',lambda job:None)
    r=git_env; gate, _ = _linear(r['env'])
    setting(r['env'],FA,gate,'auto_approve',True)
    setting(r['env'],FA,gate,'merge_mode','commit')
    committed = _work(r,'automatic.txt','automatic commit\n')
    _start(r['env'])
    job=_jobs(r['env'])[0]
    _run_job(job['job_id'])
    node = _nodes(_current(r['env']))[gate]
    assert node['status'] == 'done'
    assert node['gate']['decision']['frozen_sha'] == committed
    assert _git(['show','HEAD:automatic.txt'],cwd=r['wt']).strip() == 'automatic commit'

@needs_git
def test_completed_merge_can_push_newer_local_head(proj):
    gid=FA
    work(proj,gid,'local.txt','local\n')
    manual(gid,target='main')
    row=results(gid)['results'][0]
    push_result(gid,row['merge_id'],row['merge_commit'])
    (proj.base/'later.txt').write_text('later\n',encoding='utf-8')
    _git(['add','later.txt'],cwd=proj.base)
    _git(['commit','-m','later local change'],cwd=proj.base)
    newer=rev(proj.base,'HEAD')
    row=results(gid)['results'][0]
    assert row['remote']['contains_merge_commit'] is True
    assert row['push_available'] is True
    assert push_result(gid,row['merge_id'],row['merge_commit'])['pushed_head'] == newer
    assert rev(proj.bare,'main') == newer
