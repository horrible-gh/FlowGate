// flowgate.v02test.0068 — a server run parked on a conflict session (받아오기 or 병합) says so and hosts the one
// conflict card; an aborted session reads as words, not 「알 수 없는 이유」.
import { afterEach, describe, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { newExecution, type GitExecution } from '@/features/git/gitExecution'
import { conflicts, createFakeGitSource, G, P, row, status } from './fakeGitSource'
import type { GitResolverAttempt } from '@/features/git/types'
import { createFakeWorktreeSource, worktreeStatus, manualMergeView, mergeResults, mergeResultRow } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null; vi.useRealTimers() })

function run(overrides: Partial<GitExecution>): GitExecution {
  return { ...newExecution('push', 'v0.2'), jobId: 'job-1', completed: ['commit'], cursor: 1, ...overrides }
}

async function setup(execution: GitExecution, conflictRow: boolean, attempt: GitResolverAttempt | null = null) {
  i18n.global.locale.value = 'ko'
  const loadExecution = vi.fn(async () => execution)
  const wt = createFakeWorktreeSource({
    status: worktreeStatus({ files: [] }),
    merge: manualMergeView({ aheadOfTarget: 0 }),
    results: mergeResults({ results: [mergeResultRow()] }),
  }, { loadExecution, startExecution: vi.fn(async () => execution) })
  const fake = createFakeGitSource({ status: status({ pending: conflictRow ? [row()] : [] }), conflicts: { ...conflicts(), resolverAttempt: attempt } }, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return { loadExecution, loadStatus: fake.source.loadStatus }
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
  it('reads git/status again on every poll while the card of the waited session is not shown yet', async () => {
    vi.useFakeTimers()
    const { loadExecution, loadStatus } = await setup(run({ status: 'running', mergeId: 7 }), false)
    const before = loadStatus.mock.calls.length
    await vi.advanceTimersByTimeAsync(2600)
    await flushPromises()
    expect(loadExecution.mock.calls.length).toBeGreaterThan(1)
    expect(loadStatus.mock.calls.length).toBeGreaterThan(before)
  })
  it('says the AI is resolving and that it goes on in other tabs', async () => {
    await setup(run({ status: 'running', mergeId: 7, conflictAi: { state: 'running' } }), true)
    const waiting = w!.find('[data-test=git-execution-waiting]')
    expect(waiting.attributes('data-ai')).toBe('running')
    expect(waiting.text()).toContain('AI 해결 중 · 다른 탭에 가도 계속됨')
  })
  it('says the AI failed and why, with the lost lines on hover, instead of waiting silently', async () => {
    await setup(run({ status: 'running', mergeId: 7, conflictAi: { state: 'failed', code: 'conflict_supersede_invalid', path: 'a.py', lines: ['logger.info(1)'], lineCount: 2 } }), true)
    const waiting = w!.find('[data-test=git-execution-waiting]')
    expect(waiting.attributes('data-ai')).toBe('failed')
    expect(waiting.text()).toContain('받아오기 충돌 · AI 해결 실패 · 빠진 줄 2개')
    expect(waiting.find('.why').attributes('title')).toContain('logger.info(1)')
    expect(w!.find('[data-test=git-conflict-manual]').text()).toBe('직접 해결')
    expect(w!.find('[data-test=git-conflict-abort]').text()).toBe('중단')
  })
  it('the conflict card keeps the failed AI attempt of the server record: reason, file, lost lines, what to do next', async () => {
    const attempt: GitResolverAttempt = { runId: 'r1', state: 'failed', code: 'conflict_side_dropped', path: 'server/a.py', lines: ['logger.info(1)'], lineCount: 1, provider: 'opus' }
    await setup(run({ status: 'running', mergeId: 7, conflictAi: { state: 'failed', code: 'conflict_side_dropped' } }), true, attempt)
    const failed = w!.find('[data-test=git-conflict-ai-failed]')
    expect(failed.exists()).toBe(true)
    expect(failed.text()).toContain('AI 해결 실패 · 한쪽 변경이 빠짐')
    expect(failed.text()).toContain('server/a.py')
    expect(failed.text()).toContain('logger.info(1)')
    expect(failed.text()).toContain('다른 AI로 다시 하거나 직접 해결하세요.')
    expect(w!.find('[data-test=git-conflict-ai]').text()).toContain('다른 AI로')
  })
  it('an AI run the person stopped reads as 「중지됨」, in the run box and on the card', async () => {
    await setup(run({ status: 'running', mergeId: 7, conflictAi: { state: 'failed', code: 'cancelled' } }), true,
      { runId: 'r1', state: 'failed', code: 'cancelled', path: null, lines: [], lineCount: 0, provider: 'opus' })
    expect(w!.find('[data-test=git-execution-waiting]').text()).toContain('AI 해결 실패 · 중지됨')
    expect(w!.find('[data-test=git-conflict-ai-failed]').text()).toContain('AI 해결 실패 · 중지됨')
    expect(w!.find('[data-test=git-conflict-ai-outcome]').exists()).toBe(false)
  })
  it('a refusal whose run is still working is not shown as a failure', async () => {
    await setup(run({ status: 'running', mergeId: 7 }), true, { runId: 'r1', state: 'rejected', code: 'conflict_side_dropped', path: 'a.py', lines: [], lineCount: 0, provider: null })
    expect(w!.find('[data-test=git-conflict-ai-failed]').exists()).toBe(false)
    expect(w!.find('[data-test=git-conflict-ai]').text()).toContain('AI 해결')
  })
  it('says it is checking while no card is shown yet, never 「세션 닫힘」 or 「멈춤」', async () => {
    await setup(run({ status: 'running', mergeId: 7 }), false)
    expect(w!.find('[data-test=git-execution-waiting]').text()).toContain('받아오기 충돌 · 상태 확인 중')
    expect(w!.text()).not.toContain('세션 닫힘')
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
