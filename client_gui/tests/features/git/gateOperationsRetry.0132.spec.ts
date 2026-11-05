/** flowgate.v02.0132 T#4 (NR §5.3 client): an accepted retry (202) that fails again, the sync state and the new codes. */
import { defineComponent, h, provide } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ read: vi.fn(), recover: vi.fn() }))
vi.mock('@/features/document', async original => ({ ...await original<typeof import('@/features/document')>(), runCurrentRead: (...a: unknown[]) => mocks.read(...a), runGateRecover: (...a: unknown[]) => mocks.recover(...a) }))
import GitPushErrorWindow from '@/features/git/GitPushErrorWindow.vue'
import GitPushFailureCard from '@/features/git/GitPushFailureCard.vue'
import { retryOutcomeText, useGateOperations, GATE_OPERATIONS_CONTEXT, type GateOperations } from '@/features/git/gateOperations'
import { GIT_PANEL_CONTEXT } from '@/features/git/gitSession'
import { checkOperationErrorKey } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'
let current: any
let jobs: GateOperations
let wrapper: VueWrapper | null = null
const i18n = prepareInspectorI18n()
/**
 * A gate block as the server sends it (`intake.run_wire`): the held decision's `recovery` (`recovery.flags` — its
 * `error_code` is the decision's `last_error_code`, else the JOB's) and the job. `gate` overrides the gate-level facts.
 * Parked (the default): decision failed, job recovery_required, no Git work.
 */
const node = (recovery: Record<string, unknown> = {}, gate: Record<string, unknown> = {}) => ({ nodeKey: 'g1', status: 'open', attemptId: 'a1', gate: {
  decision_in_progress: true, can_decide: false, settings_locked: true, archived: false, recovery_reset_required: false,
  git_active: false, parked: true,
  decision: { decision_id: 'd1', status: 'failed', effects: [{ phase: 'push', status: 'failed' }] },
  job: { job_id: 'j1', status: 'recovery_required', phase: 'push', last_error_code: 'push_remote_race' },
  recovery: { decision_id: 'd1', decision_version: 7, status: 'failed', retryable: true, cancelable: false, resolvable: false,
    evidence_version: 4, evidence_refs: ['d1/push@2'], phase: 'push', error_code: 'push_remote_race', ...recovery }, ...gate } })
/** What the server answers to an accepted retry (202): the decision is `accepted` at version + 1, its job `pending` and live, and the code is still the job's earlier one. */
const accepted = (code = 'push_remote_race', version = 8) => node(
  { decision_version: version, status: 'accepted', retryable: false, error_code: code },
  { git_active: true, parked: false, job: { job_id: 'j1', status: 'pending', phase: 'push', last_error_code: code } })
/** The attempt failed transiently: the job waits for its next try (`retry_wait`, the code of that attempt), still live. */
const waiting = (code = 'remote_head_unknown', version = 8) => node(
  { decision_version: version, status: 'accepted', retryable: false, error_code: code },
  { git_active: true, parked: false, job: { job_id: 'j1', status: 'retry_wait', phase: 'push', last_error_code: code } })
/** The work gave up again: decision failed at a later version with the new code, job recovery_required, parked. */
const parkedAgain = (code: string, version = 9) => node(
  { decision_version: version, status: 'failed', retryable: true, error_code: code },
  { git_active: false, parked: true, job: { job_id: 'j1', status: 'recovery_required', phase: 'push', last_error_code: code } })
async function show() {
  wrapper = mount(defineComponent({ setup() {
    jobs = useGateOperations(() => 'flowgate', () => 'flowgate.v02.0132', () => false)
    provide(GATE_OPERATIONS_CONTEXT, jobs)
    provide(GIT_PANEL_CONTEXT, { session: { state: { pushFailure: null }, closeWindow: vi.fn(), setWindowMinimized: vi.fn() } } as any)
    return () => h('div', [
      h(GitPushErrorWindow, { win: { id: 'gate-job:g', kind: 'push-error', title: 'Git job', minimized: false, groupId: 'flowgate.v02.0132', mergeId: null }, operation: jobs.selected.value }),
      jobs.selected.value ? h(GitPushFailureCard, { operation: jobs.selected.value }) : null,
    ])
  } }), { global: { plugins: [i18n], stubs: { ModelessShell: { template: '<div><slot /></div>' } } } })
  await flushPromises()
  jobs.select(jobs.operations.value[0]); await flushPromises()
  return wrapper
}
const retry = async () => { await jobs.act('retry'); await flushPromises() }
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  mocks.read.mockReset(); mocks.recover.mockReset()
  current = { runId: 'r1', status: 'idle', nodes: [node()] }
  mocks.read.mockImplementation(async () => current)
  // 202, as the server answers it: accepted at version 8, the job pending and live, the earlier code still on it
  mocks.recover.mockImplementation(async () => current = { ...current, nodes: [accepted()] })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })
const settle = async (...nodes: unknown[]) => { current = { ...current, nodes }; await jobs.reload(); await flushPromises() }
const outcome = () => jobs.retryOutcomeOf(jobs.operations.value[0])
const cardText = (name: string) => wrapper!.find(`[data-test="git-push-card"] [data-test="${name}"]`)
describe('retry 202 outcome — 0132 T#4', () => {
  it('says nothing before any retry', async () => {
    await show()
    expect(outcome()).toBeNull()
  })
  it("the accepted retry itself (the server's 202 shape, the earlier code still on it) is no failure", async () => {
    await show(); await retry()
    expect(mocks.recover).toHaveBeenCalledTimes(1)
    const operation = jobs.operations.value[0]
    // the review's repro: version raised, accepted, job pending, git_active, error_code = the earlier one
    expect(operation.gate.recovery).toMatchObject({ decision_version: 8, status: 'accepted', error_code: 'push_remote_race' })
    expect(operation.gate.job?.status).toBe('pending')
    expect(operation.gate.git_active).toBe(true)
    expect(jobs.retryOutcomeOf(operation)).toBeNull()
  })
  it('a transient failure of the retry (retry_wait, another code) is no end either', async () => {
    await show(); await retry()
    await settle(waiting('remote_head_unknown'))
    expect(jobs.operations.value[0].gate.recovery?.error_code).toBe('remote_head_unknown')
    expect(outcome()).toBeNull()
    await settle(waiting('push_timeout', 8))
    expect(outcome()).toBeNull()
  })
  it('waits while the job is live even when the decision already reads failed (a half-moved snapshot)', async () => {
    await show(); await retry()
    await settle(node({ decision_version: 9, status: 'failed', error_code: 'push_remote_race' }, { git_active: false, parked: true, job: { job_id: 'j1', status: 'running', phase: 'push', last_error_code: 'push_remote_race' } }))
    expect(outcome()).toBeNull()
    await settle(node({ decision_version: 9, status: 'failed', error_code: 'push_remote_race' }, { git_active: true, parked: false }))
    expect(outcome()).toBeNull()
  })
  it('tells an accepted retry that was parked again for the same reason, in the card and the window', async () => {
    await show(); await retry()
    await settle(accepted())
    await settle(waiting('remote_head_unknown'))
    await settle(parkedAgain('push_remote_race'))
    jobs.select(jobs.operations.value[0]); await flushPromises()
    expect(jobs.retryOutcomeOf(jobs.selected.value)).toEqual({ kind: 'same', code: 'push_remote_race' })
    const text = '재시도는 수락됐지만 같은 이유로 다시 실패했습니다 · 원격이 계속 갱신되어 동기화 한도 초과 · 복구 대기'
    expect(cardText('git-retry-outcome').text()).toBe(text)
    expect(wrapper!.get('[data-test="git-push-window"] [data-test="git-retry-outcome"]').text()).toBe(text)
  })
  it('tells an accepted retry that was parked again for another reason', async () => {
    await show(); await retry()
    await settle(parkedAgain('target_diverged'))
    jobs.select(jobs.operations.value[0]); await flushPromises()
    expect(jobs.retryOutcomeOf(jobs.selected.value)).toEqual({ kind: 'changed', code: 'target_diverged' })
    expect(cardText('git-retry-outcome').text()).toBe('재시도는 수락됐지만 다른 이유로 실패했습니다 · 병합 대상 이력이 갈라짐 · 복구 대기')
  })
  it('the card shows the sync state while the retry goes on and the outcome only once it is parked again', async () => {
    await show(); await retry()
    await settle(accepted())
    jobs.select(jobs.operations.value[0]); await flushPromises()
    expect(cardText('git-state').text()).toBe('자동 동기화 중')
    expect(cardText('git-retry-outcome').exists()).toBe(false)
    await settle(parkedAgain('push_remote_race'))
    jobs.select(jobs.operations.value[0]); await flushPromises()
    expect(cardText('git-state').text()).toBe('복구 대기')
    expect(cardText('git-retry-outcome').exists()).toBe(true)
  })
  it('does not report a stale read at the version the retry was sent at, and forgets a retry whose decision ended', async () => {
    await show(); await retry()
    await settle(node({ decision_version: 7 }))
    expect(outcome()).toBeNull()
    await settle()
    await settle(parkedAgain('push_remote_race'))
    expect(outcome()).toBeNull()
  })
  it.each(['ko', 'en', 'ja'] as const)('has distinct same / changed texts in %s', locale => {
    i18n.global.locale.value = locale
    const t = i18n.global.t as unknown as (key: string) => string
    const same = retryOutcomeText({ kind: 'same', code: 'sync_unsettled' }, t)
    const changed = retryOutcomeText({ kind: 'changed', code: 'sync_unsettled' }, t)
    expect(same).not.toBe(changed)
    expect(same).not.toContain('gui.git.gate_job')
    expect(same).toContain(t('gui.inspector.checkSettings.target.sync_unsettled'))
  })
})
describe('commit mode parks a remote-ahead push — it promises no automatic sync (0132 T#4)', () => {
  // publish._push, commit mode: one plain push, a refusal parks push_remote_ahead with no remote_sync
  const commitParked = () => node({ error_code: 'push_remote_ahead' }, { job: { job_id: 'j1', status: 'recovery_required', phase: 'push', last_error_code: 'push_remote_ahead' } })
  it.each(['ko', 'en', 'ja'] as const)('the failure card names the waiting, not a sync, in %s', async locale => {
    i18n.global.locale.value = locale
    current.nodes[0] = commitParked()
    await show()
    jobs.notice.value = 'push_remote_ahead'; await flushPromises()
    const t = i18n.global.t as unknown as (key: string) => string
    const notice = cardText('git-notice').text()
    expect(notice).toBe(t('gui.inspector.checkSettings.target.remote_ahead'))
    expect(notice).not.toMatch(/자동|automatic|自動/i)
    expect(cardText('git-state').text()).toBe(t('gui.inspector.checkSettings.target.parked'))
    expect(wrapper!.get('[data-test="git-push-card"]').text()).not.toContain(t('gui.inspector.checkSettings.target.syncing'))
  })
  it('a retry that is refused the same way is reported without any sync promise', async () => {
    current.nodes[0] = commitParked()
    await show(); await retry()
    await settle(parkedAgain('push_remote_ahead'))
    jobs.select(jobs.operations.value[0]); await flushPromises()
    const text = cardText('git-retry-outcome').text()
    expect(text).toContain('같은 이유로 다시 실패')
    expect(text).toContain('원격이 앞서 있어 반영 거절')
    expect(text).not.toMatch(/자동 동기화로/)
  })
})
describe('sync state and new codes in the failure card — 0132 T#4', () => {
  it.each([[{ git_active: true }, '자동 동기화 중'], [{ parked: true }, '복구 대기']])('shows %j as its own line', async (flags, text) => {
    current.nodes[0] = node({}, flags)
    await show()
    expect(wrapper!.get('[data-test="git-push-card"] [data-test="git-state"]').text()).toBe(text)
  })
  it('shows no state line when the server tells none', async () => {
    current.nodes[0] = node({}, { git_active: undefined, parked: undefined })
    await show()
    expect(wrapper!.find('[data-test="git-state"]').exists()).toBe(false)
  })
  it.each(['push_remote_ahead', 'push_protected', 'push_remote_race', 'target_checkout_mismatch', 'target_diverged', 'sync_unsettled', 'push_sync_conflict_aborted'])('maps %s to its own text and never to the unknown one', async code => {
    const key = checkOperationErrorKey(code)
    expect(key).not.toBeNull()
    expect(key).not.toBe('target.operation_unknown')
    expect(key).not.toBe('target.target_check')
    for (const locale of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = locale
      expect(i18n.global.te(`gui.inspector.checkSettings.${key}`)).toBe(true)
    }
    current.nodes[0] = node({ error_code: code })
    i18n.global.locale.value = 'ko'
    await show()
    jobs.notice.value = code; await flushPromises()
    expect(wrapper!.get('[data-test="git-push-card"] [data-test="git-notice"]').text()).toBe(i18n.global.t(`gui.inspector.checkSettings.${key}`))
  })
})
