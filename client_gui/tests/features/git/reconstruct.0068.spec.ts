import { afterEach, describe, it, expect, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createGitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession } from '@/features/git/gitWorktreeSession'
import { createFakeGitSource, row, status, G, P, gitError } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus, manualMergeView } from './fakeWorktreeSource'
const disposers: Array<() => void> = []
afterEach(() => { disposers.splice(0).forEach(fn => fn()) })
async function setup() {
  const rebuild = vi.fn(async () => ({status:'updated',mergeId:null}))
  const wt = createFakeWorktreeSource({status:worktreeStatus({files:[]}),merge:manualMergeView({aheadOfTarget:0})}, {reconstruct:rebuild,updateFromBase:async()=>({status:'no_change',mergeId:null})})
  const fake = createFakeGitSource({status:status({pending:[row()]})},{worktree:wt.source})
  const git = createGitSession({source:fake.source})
  const session = createGitWorktreeSession({git,source:fake.source})
  disposers.push(() => {session.dispose();git.dispose()})
  git.setContext(P,{groupId:G,name:'Flow'}); await flushPromises()
  return {wt,fake,git,session,rebuild}
}
describe('conflict reconstruction', () => {
  it('aborts the selected conflict before reconstructing the same group', async () => {
    const t = await setup()
    await t.session.reconstruct()
    expect(t.fake.source.abortMerge).toHaveBeenCalledWith(G,expect.any(Number))
    expect(t.rebuild).toHaveBeenCalledWith(G)
    expect(t.fake.source.abortMerge.mock.invocationCallOrder[0]).toBeLessThan(t.rebuild.mock.invocationCallOrder[0])
    expect(t.wt.source.commit).not.toHaveBeenCalled()
  })
  it('does not reconstruct when the abort fails', async () => {
    const t=await setup()
    t.fake.source.abortMerge.mockRejectedValueOnce(gitError(409,'abort_failed'))
    await t.session.reconstruct()
    expect(t.rebuild).not.toHaveBeenCalled()
  })
  it('fences a context switch while the abort is pending', async () => {
    const t=await setup()
    let finish!: () => void
    t.fake.source.abortMerge.mockImplementationOnce(() => new Promise(resolve => {finish=resolve}))
    const pending=t.session.reconstruct()
    t.git.setContext(P,{groupId:'other',name:'Other'}); await flushPromises()
    finish(); await pending
    expect(t.rebuild).not.toHaveBeenCalled()
  })
})
