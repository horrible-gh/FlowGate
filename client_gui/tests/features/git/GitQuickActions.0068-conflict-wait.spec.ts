// flowgate.v02test.0068 — a server run parked on a conflict session (받아오기 or 병합) says so and hosts the one
// conflict card; an aborted session reads as words, not 「알 수 없는 이유」.
import { afterEach, describe, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { newExecution, type GitExecution } from '@/features/git/gitExecution'
import { createFakeGitSource, G, P, row, status } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus, manualMergeView, mergeResults, mergeResultRow } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null; vi.useRealTimers() })

function run(overrides: Partial<GitExecution>): GitExecution {
  return { ...newExecution('push', 'v0.2'), jobId: 'job-1', completed: ['commit'], cursor: 1, ...overrides }
}

async function setup(execution: GitExecution, conflictRow: boolean) {
  i18n.global.locale.value = 'ko'
  const loadExecution = vi.fn(async () => execution)
  const wt = createFakeWorktreeSource({
    status: worktreeStatus({ files: [] }),
    merge: manualMergeView({ aheadOfTarget: 0 }),
    results: mergeResults({ results: [mergeResultRow()] }),
  }, { loadExecution, startExecution: vi.fn(async () => execution) })
  const fake = createFakeGitSource({ status: status({ pending: conflictRow ? [row()] : [] }) }, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return { loadExecution }
}

describe('0068 run waiting on a conflict session', () => {
  it('says the run waits on the 받아오기 conflict and shows its conflict card once, not 「멈춤」', async () => {
    await setup(run({ status: 'running', mergeId: 7 }), true)
    const waiting = w!.find('[data-test=git-execution-waiting]')
    expect(waiting.exists()).toBe(true)
    expect(waiting.text()).toContain('받아오기 충돌')
    expect(w!.text()).not.toContain('받아오기에서 멈춤')
    expect(w!.findAll('[data-test=git-conflict-card]')).toHaveLength(1)
    expect(w!.find('[data-test=git-quick-actions] [data-test=git-conflict-card]').exists()).toBe(true)
  })
  it('says the session closed while the server has not answered yet, still without 「멈춤」', async () => {
    await setup(run({ status: 'running', mergeId: 7 }), false)
    expect(w!.find('[data-test=git-execution-waiting]').text()).toContain('세션 닫힘')
    expect(w!.text()).not.toContain('받아오기에서 멈춤')
    expect(w!.find('[data-test=git-conflict-card]').exists()).toBe(false)
  })
  it('names an aborted conflict session as the stop reason', async () => {
    await setup(run({ status: 'stopped', error: 'merge_aborted' }), false)
    const reason = w!.find('[data-test=git-stop-reason]')
    expect(reason.text()).toContain('받아오기에서 멈춤')
    expect(reason.text()).toContain('충돌 세션 중단됨')
    expect(reason.text()).not.toContain('알 수 없는 이유')
  })
  it('a finished run leaves a later conflict card to the exception list, still exactly once', async () => {
    await setup(run({ status: 'complete', completed: ['commit', 'update', 'merge', 'push'], cursor: 4 }), true)
    expect(w!.findAll('[data-test=git-conflict-card]')).toHaveLength(1)
    expect(w!.find('[data-test=git-quick-actions] [data-test=git-conflict-card]').exists()).toBe(false)
  })
})
