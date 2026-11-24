import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession, type GitWorktreeSession } from '@/features/git/gitWorktreeSession'
import { newExecution, type GitExecution } from '@/features/git/gitExecution'
import { G, P, createFakeGitSource, manualSleep } from './fakeGitSource'
import { createFakeWorktreeSource } from './fakeWorktreeSource'

const sessions: Array<{ git: GitSession; wt: GitWorktreeSession }> = []
afterEach(() => {
  for (const s of sessions.splice(0)) { s.wt.dispose(); s.git.dispose() }
  vi.useRealTimers()
})

function setup() {
  const server = new Map<string, GitExecution>()
  let resolve!: (value: GitExecution) => void
  const accepted = new Promise<GitExecution>(r => { resolve = r })
  const startExecution = vi.fn(async (gid: string) => {
    const run = { ...newExecution('push', 'v0.2'), jobId: 'durable-a' }
    server.set(gid, run)
    return accepted
  })
  const loadExecution = vi.fn(async (gid: string) => server.get(gid) ?? null)
  const fake = createFakeWorktreeSource({}, { startExecution, loadExecution })
  const source = createFakeGitSource({}, { worktree: fake.source })
  const git = createGitSession({ source: source.source, sleep: manualSleep().sleep })
  const wt = createGitWorktreeSession({ git, source: source.source })
  sessions.push({ git, wt })
  return { git, wt, fake, server, startExecution, loadExecution, resolve }
}

describe('durable Git execution observer', () => {
  it('submits once, survives a Flow switch and restores the server result without replaying mutations', async () => {
    const t = setup()
    t.git.setContext(P, { groupId: G, name: 'A' })
    await flushPromises()
    const request = t.wt.execute('push')
    expect(t.startExecution).toHaveBeenCalledTimes(1)
    t.git.setContext(P, { groupId: 'p.m.0002', name: 'B' })
    await flushPromises()
    const done: GitExecution = { ...t.server.get(G)!, status: 'complete', cursor: 4,
      completed: ['commit', 'update', 'merge', 'push'] }
    t.server.set(G, done)
    t.resolve(done)
    await request
    expect(t.wt.state.execution).toBeNull()
    t.git.setContext(P, { groupId: G, name: 'A' })
    await flushPromises()
    expect(t.wt.state.execution?.jobId).toBe('durable-a')
    expect(t.wt.state.execution?.status).toBe('complete')
    expect(t.startExecution).toHaveBeenCalledTimes(1)
    expect(t.fake.source.stage).not.toHaveBeenCalled()
    expect(t.fake.source.commit).not.toHaveBeenCalled()
    expect(t.fake.source.manualMerge).not.toHaveBeenCalled()
    expect(t.fake.source.pushMergeResult).not.toHaveBeenCalled()
  })

  it('restores a running job in a new session and stops polling on disposal', async () => {
    vi.useFakeTimers()
    const t = setup()
    t.server.set(G, { ...newExecution('merge', 'v0.2'), jobId: 'before-reload' })
    t.git.setContext(P, { groupId: G, name: 'A' })
    await flushPromises()
    expect(t.wt.state.execution?.status).toBe('running')
    expect(t.startExecution).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2600)
    expect(t.loadExecution.mock.calls.length).toBeGreaterThan(1)
    t.wt.dispose()
    const calls = t.loadExecution.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(t.loadExecution).toHaveBeenCalledTimes(calls)
  })
})
