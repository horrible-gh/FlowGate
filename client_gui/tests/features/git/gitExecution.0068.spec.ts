import { describe, it, expect, vi } from 'vitest'
import { executeGit, newExecution, type ExecutionInput } from '@/features/git/gitExecution'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus, manualMergeView, mergeResults, mergeResultRow } from './fakeWorktreeSource'
import { gitError } from './fakeGitSource'

const input = (): ExecutionInput => ({ projectId:'p', groupId:'g', message:'change', providerId:'ai', selection:{all:true}, current:()=>true })
describe('scope execution', () => {
  it('does not read or mutate while planning; commit scope never fetches', async () => {
    const f = createFakeWorktreeSource()
    const p = newExecution('commit', null)
    expect(f.source.loadStatus).not.toHaveBeenCalled()
    await executeGit(p, f.source, input())
    expect(p.status).toBe('complete')
    expect(f.source.commit).toHaveBeenCalledTimes(1)
    expect(f.source.fetchRefs).not.toHaveBeenCalled()
    expect(f.source.manualMerge).not.toHaveBeenCalled()
  })
  it('leaves excluded files uncommitted and does not call that complete', async () => {
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[worktreeFile('a'),worktreeFile('b',{staged:'M'})]})})
    const p=newExecution('commit',null)
    await executeGit(p,f.source,{...input(),selection:{paths:['a']}})
    expect(p.status).toBe('remaining')
    expect(f.db.status.files.map(f=>f.path)).toEqual(['b'])
    expect(f.source.unstage).toHaveBeenCalled()
  })
  it('stops before updating dirty work and resumes without repeating the first commit', async () => {
    const update=vi.fn(async()=>({status:'updated',mergeId:null}))
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[worktreeFile('a'),worktreeFile('b')]})},{updateFromBase:update})
    const p=newExecution('merge','v0.2')
    await executeGit(p,f.source,{...input(),selection:{paths:['a']}})
    expect(p.error).toBe('uncommitted_changes'); expect(p.cursor).toBe(1); expect(update).not.toHaveBeenCalled()
    f.db.status=worktreeStatus({files:[]}); f.db.merge=manualMergeView({aheadOfTarget:0})
    await executeGit(p,f.source,input())
    expect(p.status).toBe('complete'); expect(f.source.commit).toHaveBeenCalledTimes(1); expect(update).toHaveBeenCalledTimes(1)
  })
  it('stops on update conflicts and never merges or pushes past them', async()=>{
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[]})},{updateFromBase:async()=>({status:'conflict',mergeId:4})})
    const p=newExecution('push','v0.2'); await executeGit(p,f.source,input())
    expect(p.error).toBe('merge_conflict'); expect(p.cursor).toBe(1)
    expect(f.source.manualMerge).not.toHaveBeenCalled(); expect(f.source.pushMergeResult).not.toHaveBeenCalled()
  })
  it('fences a late status after changing Flow before mutation',async()=>{
    let current=true
    const f=createFakeWorktreeSource({}, {loadStatus:async()=>{current=false;return worktreeStatus()}})
    await executeGit(newExecution('push',null),f.source,{...input(),current:()=>current})
    expect(f.source.stage).not.toHaveBeenCalled()
  })
  it('rejects an AI message generated for a different index',async()=>{
    const f=createFakeWorktreeSource(); f.source.suggestCommitMessage.mockImplementation(async()=>({suggested:'wrong',providerId:'ai',stagedCount:1,inputTruncated:false,head:'h',indexToken:'old'}))
    const p=newExecution('commit',null); await executeGit(p,f.source,{...input(),message:''})
    expect(p.error).toBe('worktree_changed'); expect(f.source.commit).not.toHaveBeenCalled()
  })
  it('reconciles a rejected push only on explicit resume, then verifies the remote',async()=>{
    const sync=vi.fn(async()=>({status:'updated',mergeId:null}))
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[]}),merge:manualMergeView({aheadOfTarget:0}),results:mergeResults({results:[mergeResultRow()]})},{updateFromBase:async()=>({status:'updated',mergeId:null}),syncRemote:sync})
    f.source.pushMergeResult.mockRejectedValueOnce(gitError(409,'push_rejected'))
    const p=newExecution('push','v0.2'); await executeGit(p,f.source,input())
    expect(p.error).toBe('push_rejected'); expect(sync).not.toHaveBeenCalled()
    await executeGit(p,f.source,input())
    expect(sync).toHaveBeenCalledTimes(1); expect(p.status).toBe('remaining')
    expect(p.completed).toEqual(['commit','update','merge','push'])
  })
  it('passes over an update the server refuses for its own reasons and still merges', async()=>{
    for (const code of ['git_error','base_dirty','group_lease_active','group_work_base_unverified']) {
      const f=createFakeWorktreeSource({status:worktreeStatus({files:[]}),merge:manualMergeView({aheadOfTarget:0})},{updateFromBase:async()=>{throw gitError(409,code)}})
      const p=newExecution('merge','v0.2'); await executeGit(p,f.source,input())
      expect(p.status).toBe('complete'); expect(p.error).toBeNull()
      expect(p.skipped).toEqual({update:code}); expect(p.completed).toEqual(['commit','update','merge'])
    }
  })
  it('still stops on update failures that are not the server\'s own state', async()=>{
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[]})},{updateFromBase:async()=>{throw gitError(409,'group_untracked_conflict')}})
    const p=newExecution('push','v0.2'); await executeGit(p,f.source,input())
    expect(p.error).toBe('group_untracked_conflict'); expect(p.cursor).toBe(1); expect(f.source.manualMerge).not.toHaveBeenCalled()
  })
  it('clears an earlier skip when a resumed update succeeds', async()=>{
    let fail=true
    const f=createFakeWorktreeSource({status:worktreeStatus({files:[]}),merge:manualMergeView({aheadOfTarget:0})},{updateFromBase:async()=>{if(fail) throw gitError(500,'git_error'); return {status:'no_change',mergeId:null}}})
    const p=newExecution('merge','v0.2'); await executeGit(p,f.source,input())
    expect(p.skipped).toEqual({update:'git_error'})
    fail=false; p.cursor=1; p.completed=['commit']; await executeGit(p,f.source,input())
    expect(p.skipped).toEqual({})
  })
})
