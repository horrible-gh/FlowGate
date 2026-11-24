/** flowgate.v02test.0068: an open 받아오기 conflict session is always resolvable in the conflict card — also when git/status
 *  has no conflict row for it (only worktree status names the session). */
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { G, P, createFakeGitSource, manualSleep, row, status } from './fakeGitSource'

let session: GitSession | null = null
afterEach(() => { session?.dispose(); session = null })

async function open(fake: ReturnType<typeof createFakeGitSource>) {
  session = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
  session.setContext(P, { groupId: G, name: 'f' })
  await flushPromises()
  return session
}

describe('conflict card of an open 받아오기 session git/status has no row for', () => {
  it('shows the conflict card with the session id, reads its files and aborts that session', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBeNull()
    s.adoptOpenSession({ groupId: G, mergeId: 553, kind: 'group_update' })
    expect(s.groupCard.value).toBe('conflict')
    expect(s.pendingRow.value).toMatchObject({ groupId: G, status: 'conflict', mergeId: 553, reviewState: null })
    await flushPromises()
    expect(fake.source.fetchConflicts).toHaveBeenLastCalledWith(G, 553)
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
    // The fake's conflicts answer is for merge 7: until the answer is this session's, no action is offered.
    expect(s.conflictActionable.value).toBe(false)
  })

  it('the card goes when worktree status no longer names the session', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [] }) })
    const s = await open(fake)
    s.adoptOpenSession({ groupId: G, mergeId: 553, kind: 'group_update' })
    await flushPromises()
    const reads = fake.source.loadStatus.mock.calls.length
    s.adoptOpenSession(null)
    expect(s.groupCard.value).toBeNull()
    await flushPromises()
    // git/status is read again for what is true now, once.
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(s.state.conflict).toBeNull()
    // The same answer again changes nothing and reads nothing.
    s.adoptOpenSession(null)
    await flushPromises()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })

  it('a session of another Flow or of another kind makes no card', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [] }) })
    const s = await open(fake)
    s.adoptOpenSession({ groupId: 'p.m.0002', mergeId: 553, kind: 'group_update' })
    expect(s.groupCard.value).toBeNull()
    for (const kind of ['branch_merge', 'graph_attempt_merge', 'tr_revert', 'merge']) {
      s.adoptOpenSession({ groupId: G, mergeId: 553, kind })
      expect(s.groupCard.value, kind).toBeNull()
    }
  })

  it('a conflict row git/status does give stays the one shown', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    s.adoptOpenSession({ groupId: G, mergeId: 7, kind: 'group_update' })
    await flushPromises()
    expect(s.pendingRow.value?.mergeId).toBe(7)
    expect(s.groupCard.value).toBe('conflict')
    expect(s.conflictActionable.value).toBe(true)
    // Nothing changed on the card: no extra read.
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads)
  })

  it('a held (waiting) row gives way to the open 받아오기 conflict, as the server names it', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ status: 'waiting', mergeId: null })] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('hold')
    s.adoptOpenSession({ groupId: G, mergeId: 553, kind: 'group_update' })
    expect(s.groupCard.value).toBe('conflict')
    expect(s.pendingRow.value?.mergeId).toBe(553)
  })
})
