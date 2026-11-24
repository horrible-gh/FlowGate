/** flowgate.v02test.0068: the Git tab with an open 받아오기 conflict session that git/status gives no conflict row for —
 *  the screen that showed only 「열린 Git 세션 · 받아오기 충돌 #553」 and no card to resolve it in. */
import { afterEach, describe, expect, it } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { conflicts, createFakeGitSource, G, P, status } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus } from './fakeWorktreeSource'

registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null })

const OPS = ['stage', 'unstage', 'commit', 'revert', 'remove']
const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-test="${id}"]`)

async function open(kind: string) {
  i18n.global.locale.value = 'ko'
  const wt = createFakeWorktreeSource({ status: worktreeStatus({ files: [], blockers: [{ code: 'session_open', operations: OPS, sessionKind: kind, mergeId: 553 }] }) })
  const fake = createFakeGitSource({ status: status({ pending: [] }), conflicts: { ...conflicts(), mergeId: 553 } }, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  await flushPromises()
  return { wt, fake }
}

describe('an open 받아오기 conflict session with no git/status row', () => {
  it('is shown in the conflict card with its actions, not as a bare open-session line; its end is told to the Flow tab', async () => {
    const { wt, fake } = await open('group_update')
    expect(q('git-conflict-card')).not.toBeNull()
    expect(q('git-open-session')).toBeNull()
    expect(fake.source.fetchConflicts).toHaveBeenLastCalledWith(G, 553)
    for (const id of ['git-conflict-ai', 'git-conflict-manual', 'git-conflict-abort']) expect(q(id)?.hasAttribute('disabled'), id).toBe(false)
    expect(w!.emitted('git-conflict-change')).toEqual([[G]])
    // The session ended (세션 중단 / resolved / swept): worktree status no longer names it.
    wt.source.loadStatus.mockImplementation(async () => worktreeStatus({ files: [] }))
    await (w!.vm as unknown as { refresh(): Promise<void> }).refresh()
    await flushPromises()
    await flushPromises()
    expect(q('git-conflict-card')).toBeNull()
    expect(q('git-open-session')).toBeNull()
    expect(w!.emitted('git-conflict-change')).toEqual([[G], [G]])
  })

  it('a session the conflict card does not handle keeps the open-session line with its abort', async () => {
    await open('branch_merge')
    expect(q('git-conflict-card')).toBeNull()
    expect(q('git-open-session')?.getAttribute('data-session-kind')).toBe('branch_merge')
    expect(q('git-session-abort')).not.toBeNull()
    expect(w!.emitted('git-conflict-change')).toBeUndefined()
  })
})
