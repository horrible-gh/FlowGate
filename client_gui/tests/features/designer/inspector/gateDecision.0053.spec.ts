/** D0007 §8-10: the confirmed mockup and the live gate/settings contracts. */
import { defineComponent, h } from 'vue'
import { createPinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ run: vi.fn(), settings: vi.fn(), write: vi.fn(), decide: vi.fn(), memos: vi.fn(), update: vi.fn(), create: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(() => Promise.reject({ request: {} })) }))
vi.mock('@/features/document', async original => ({
  ...await original<typeof import('@/features/document')>(),
  runCurrentRead: (...a: unknown[]) => mocks.run(...a), settingsNodeRead: (...a: unknown[]) => mocks.settings(...a),
  settingsNodeWrite: (...a: unknown[]) => mocks.write(...a), runGateDecide: (...a: unknown[]) => mocks.decide(...a),
  cardMemoList: (...a: unknown[]) => mocks.memos(...a), cardMemoUpdate: (...a: unknown[]) => mocks.update(...a), cardMemoCreate: (...a: unknown[]) => mocks.create(...a),
}))
import GateSlots from '@/features/designer/inspector/features/gateCard/GateSlots.vue'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import CardMemoButton from '@/features/designer/inspector/features/cardMemo/CardMemoButton.vue'
import GateMemoPanel from '@/features/designer/inspector/features/cardMemo/GateMemoPanel.vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { gateFailureKey, gateResult } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import gateMessages from '@/features/designer/inspector/features/gateCard/messages'
import registration from '@/features/designer/inspector/features/gateCard/register'
import { SELECTIONS, prepareInspectorI18n, targetOf } from './support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from './support/projectEventStream'
const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
let stream: ProjectEventStream
let id = 0
let target: ReturnType<typeof targetOf>
let current: any
let saved: any
const field = (value: unknown) => ({ value, stored: value, default: null, choices: null })
const deferred = <T,>() => { let resolve!: (v: T) => void; let reject!: (e: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const error = (code: string, status = 409) => ({ isAxiosError: true, response: { status, data: { ok: false, error: { code } } } })
function gate(status = 'open', runStatus = 'idle', extra = {}) {
  return { runId: `run_${id}`, status: runStatus, nodes: [{ nodeKey: target.nodeId, status, attemptId: `attempt_${id}`, gate: {
    can_decide: status === 'open' && ['running', 'idle'].includes(runStatus), decision_in_progress: false, settings_locked: false,
    archived: false, recovery_reset_required: false, decision: null, job: null, recovery: null, ...extra,
  } }] }
}
function result(archived = true) { return { decision_id: 'd1', attempt_id: `attempt_${id}`, result: 'confirmed', status: 'succeeded', archived, archive_id: archived ? 'a1' : null, actor_user_id: 'kim', completed_at: '2026-10-24T12:00:00Z', comment: 'reviewed', effects: [] } }
async function show(component: any = GateSlots, props: any = { target }) {
  const w = mount(component, { props, global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
  wrappers.push(w)
  await flushPromises()
  return w
}
beforeEach(() => {
  Object.values(mocks).forEach(m => m.mockReset())
  target = { ...targetOf(SELECTIONS.graphGate), nodeId: `gate_${++id}`, readOnly: null }
  saved = { cardKind: 'check', locked: false, settingsVersion: 3, fields: { archive: field(false), merge_mode: field(null), merge_target: field(null), push: field(false), keep_branch: field(false) } }
  current = gate()
  mocks.run.mockImplementation(async () => current)
  mocks.settings.mockImplementation(async () => saved)
  mocks.write.mockImplementation(async (_flow, _node, name, value) => saved = { ...saved, settingsVersion: saved.settingsVersion + 1, fields: { ...saved.fields, [name]: field(value) } })
  mocks.memos.mockResolvedValue([])
  stream = installProjectEventStream()
  i18n.global.locale.value = 'ko'
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); removeProjectEventStream() })

describe('gate Inspector — D §8-10', () => {
  it.each(['running', 'idle'])('open + can_decide in %s enables the document actions', async status => {
    current = gate('open', status)
    const w = await show()
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('확인 전')
    expect(w.get('[data-test="gate-confirm"]').attributes('disabled')).toBeUndefined()
    expect(registration).not.toHaveProperty('headerAction')
  })
  it('rejects immediately with a request id and confirmed attempt/settings version; no reason modal or memo copy', async () => {
    mocks.decide.mockImplementation(async () => current = gate('rejected', 'idle', { decision: { ...result(false), result: 'rejected', status: 'rejected', comment: null } }))
    const w = await show()
    await w.get('[data-test="gate-reject"]').trigger('click'); await flushPromises()
    expect(mocks.decide).toHaveBeenCalledTimes(1)
    expect(mocks.decide).toHaveBeenCalledWith(`run_${id}`, target.nodeId, 'reject', undefined, {
      request_id: expect.any(String), expected_attempt_id: `attempt_${id}`, expected_settings_version: 3,
    })
    expect(w.find('[data-test="gate-reject-dialog"]').exists()).toBe(false)
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('반려')
    expect(w.find('[data-test="gate-confirm"]').exists()).toBe(false)
  })
  it('keeps 확인 전 and no archive chip while the intent is accepted; completion comes from GET', async () => {
    const answer = deferred<any>()
    mocks.decide.mockReturnValue(answer.promise)
    const w = await show()
    await w.get('[data-test="gate-confirm"]').trigger('click')
    await w.get('[data-test="gate-confirm"]').trigger('click')
    expect(mocks.decide).toHaveBeenCalledTimes(1)
    expect(w.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="gate-archived"]').exists()).toBe(false)
    current = gate('open', 'idle', { decision_in_progress: true, settings_locked: true, decision: { ...result(), result: 'in_progress', status: 'accepted' } })
    answer.resolve(current); await flushPromises()
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('확인 전')
    expect(w.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="gate-archived"]').exists()).toBe(false)
    current = gate('done', 'idle', { archived: true, settings_locked: true, decision: result() })
    stream.open(); stream.emit('gate_publish_job_changed', { groupId: target.flowId })
    await new Promise(r => setTimeout(r, 240)); await flushPromises()
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('확인 완료')
    expect(w.get('[data-test="gate-result-value"]').attributes('aria-label')).toContain('kim · 2026-10-24T12:00:00Z · reviewed')
    expect(w.find('[data-test="gate-archived"]').exists()).toBe(true)
    expect(w.find('[data-test="gate-confirm"]').exists()).toBe(false)
  })
  it.each([['pending', 'idle', {}], ['pending', 'idle', { recovery_reset_required: true }], ['open', 'paused', {}], ['open', 'stopping', {}]])('disables %s/%s without a decision request', async (status, run, flags) => {
    current = gate(status as string, run as string, { can_decide: false, ...flags })
    const w = await show()
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('확인 전')
    expect(w.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="gate-confirm"]').trigger('click')
    expect(mocks.decide).not.toHaveBeenCalled()
  })
  it('requires the current successful receipt for the archive chip, including after Time Machine', () => {
    const node = gate('done', 'idle', { archived: true, decision: result() }).nodes[0]
    expect(gateResult(node).archived).toBe(true)
    for (const change of [{ status: 'open' }, { gate: { ...node.gate, decision_in_progress: true } }, { gate: { ...node.gate, decision: { ...result(), attempt_id: 'previous' } } }, { gate: { ...node.gate, decision: { ...result(), archive_id: null } } }, { gate: { ...node.gate, decision: { ...result(), status: 'abandoned' } } }]) expect(gateResult({ ...node, ...change }).archived).toBe(false)
  })
  it('shows only the mockup settings, saves archive immediately and restores confirmed values after failure', async () => {
    const w = await show(CheckSettings)
    expect(w.findAll('[data-test="check-settings-target"] option').map(x => x.text())).toEqual(['—', 'origin'])
    expect(w.text()).toContain('아카이브'); expect(w.text()).toContain('Git 반영'); expect(w.text()).toContain('원격 병합')
    for (const name of ['usage', 'summary', 'hold-block']) expect(w.find(`[data-test="check-settings-${name}"]`).exists()).toBe(false)
    await w.get('[data-test="check-settings-archive-value"]').trigger('click'); await flushPromises()
    expect(mocks.write).toHaveBeenCalledWith(target.flowId, target.nodeId, 'archive', true, 3)
    mocks.write.mockRejectedValueOnce(error('gate_settings_locked'))
    await w.get('[data-test="check-settings-archive-value"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('true')
    expect(w.find('[data-field="archive"]').exists()).toBe(true)
    expect(saved.fields.archive.value).toBe(true)
  })
  it('keeps confirmed settings and the field failure when a network save and both subsequent reads fail', async () => {
    const w = await show(CheckSettings)
    const actions = await show()
    const network = { request: {} }
    mocks.write.mockRejectedValueOnce(network)
    mocks.settings.mockRejectedValue(network)
    mocks.run.mockRejectedValue(network)
    await w.get('[data-test="check-settings-archive-value"]').trigger('click'); await flushPromises()
    expect(mocks.write).toHaveBeenCalledWith(target.flowId, target.nodeId, 'archive', true, 3)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('false')
    expect(w.find('[data-test="check-settings-scope"]').exists()).toBe(true)
    expect(w.find('[data-field="archive"]').exists()).toBe(true)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
    expect(actions.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="check-settings-archive-value"]').trigger('click')
    expect(mocks.write).toHaveBeenCalledTimes(1)
    w.unmount()
    const back = await show(CheckSettings)
    expect(back.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('false')
    expect(back.find('[data-field="archive"]').exists()).toBe(true)
    expect(back.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
  })
  it('keeps independently read settings visible and locked when only the run read fails', async () => {
    mocks.run.mockRejectedValue({ request: {} })
    const w = await show(CheckSettings)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('false')
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="check-settings-scope"]').exists()).toBe(true)
    expect(mocks.write).not.toHaveBeenCalled()
  })
  it('reports a stale gate settings PATCH, refreshes the server values and uses the refreshed version on retry', async () => {
    const w = await show(CheckSettings)
    mocks.write.mockImplementationOnce(async () => {
      saved = { ...saved, settingsVersion: 4, fields: { ...saved.fields, merge_target: field('origin') } }
      throw error('gate_context_changed')
    })
    await w.get('[data-test="check-settings-archive-value"]').trigger('click'); await flushPromises()
    expect(mocks.write).toHaveBeenCalledWith(target.flowId, target.nodeId, 'archive', true, 3)
    expect(mocks.settings).toHaveBeenCalledTimes(2)
    expect(mocks.run).toHaveBeenCalledTimes(2)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('false')
    expect((w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value).toBe('origin')
    expect(w.find('[data-field="archive"]').exists()).toBe(true)
    await w.get('[data-test="check-settings-archive-value"]').trigger('click'); await flushPromises()
    expect(mocks.write).toHaveBeenLastCalledWith(target.flowId, target.nodeId, 'archive', true, 4)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('true')
  })
  it.each([
    ['gate_settings_invalid', '설정 탭에서 병합 대상 원격으로 origin을 선택해 주세요'],
    ['gate_git_unavailable', '사용 가능한 Git 작업 슬롯이 없어 확인하지 못했습니다'],
    ['gate_context_changed', '설정 또는 확인 회차가 변경되었습니다. 최신 값을 확인한 뒤 다시 시도해 주세요'],
    ['gate_decision_in_progress', '다른 확인 요청을 처리 중입니다. 처리가 끝난 뒤 다시 시도해 주세요'],
    ['gate_request_conflict', '같은 요청 ID의 내용이 달라 확인하지 못했습니다. 최신 상태를 확인한 뒤 다시 시도해 주세요'],
  ] as const)('explains %s through the existing failure tooltip with ko/en/ja messages', async (code, message) => {
    expect(gateFailureKey({ kind: 'other', status: 409, code })).toBe(`failure.${code}`)
    mocks.decide.mockRejectedValueOnce(error(code))
    const w = await show()
    await w.get('[data-test="gate-confirm"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="inspector-gate-actions"]').classes()).toContain('is-failed')
    expect(w.get('[data-test="gate-confirm"]').attributes('title')).toBe(message)
    for (const locale of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = locale
      await flushPromises()
      expect(gateMessages[locale].failure[code]).toBeTruthy()
      expect(w.get('[data-test="gate-confirm"]').attributes('title')).toBe(gateMessages[locale].failure[code])
    }
  })
  it('locks confirmation during a settings write across tabs, then uses the returned settings version', async () => {
    const a = await show()
    const settings = await show(CheckSettings)
    const write = deferred<any>(); mocks.write.mockReturnValueOnce(write.promise)
    await settings.get('[data-test="check-settings-archive-value"]').trigger('click')
    settings.unmount()
    expect(a.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    saved = { ...saved, settingsVersion: 4, fields: { ...saved.fields, archive: field(true) } }
    write.resolve(saved); await flushPromises()
    mocks.decide.mockImplementation(async () => current = gate('done', 'idle', { decision: result(), archived: true }))
    await a.get('[data-test="gate-confirm"]').trigger('click'); await flushPromises()
    expect(mocks.decide.mock.calls[0][4].expected_settings_version).toBe(4)
    const back = await show(CheckSettings)
    expect(back.get('[data-test="check-settings-archive-value"]').attributes('aria-checked')).toBe('true')
  })
  it('locks settings on the server settings_locked flag even if settings GET is not yet locked', async () => {
    current = gate('done', 'idle', { settings_locked: true, decision: result() })
    const w = await show(CheckSettings)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="check-settings-archive-value"]').trigger('click')
    expect(mocks.write).not.toHaveBeenCalled()
  })
  it('reads run + gate and settings again on reentry, SSE and reconnect', async () => {
    const w = await show()
    stream.open()
    const reads = mocks.run.mock.calls.length
    stream.emit('graph_node_state_changed', { groupId: target.flowId })
    await new Promise(r => setTimeout(r, 240)); await flushPromises()
    expect(mocks.run).toHaveBeenCalledTimes(reads + 1)
    expect(mocks.settings).toHaveBeenCalledTimes(reads + 1)
    stream.reconnect(); await new Promise(r => setTimeout(r, 240)); await flushPromises()
    expect(mocks.run).toHaveBeenCalledTimes(reads + 2)
    expect(mocks.settings).toHaveBeenCalledTimes(reads + 2)
    w.unmount(); await show()
    expect(mocks.settings).toHaveBeenCalledTimes(reads + 3)
  })
  it('GETs after a lost response before resending the identical request', async () => {
    const order: string[] = []
    mocks.run.mockImplementation(async () => { order.push('get'); return current })
    mocks.decide.mockImplementationOnce(async () => { order.push('post'); throw { request: {} } }).mockImplementationOnce(async () => { order.push('replay'); return current = gate('done', 'idle', { decision: result(false) }) })
    const w = await show()
    order.length = 0
    await w.get('[data-test="gate-confirm"]').trigger('click'); await flushPromises()
    expect(order.slice(0, 3)).toEqual(['post', 'get', 'replay'])
    expect(mocks.decide.mock.calls[1]).toEqual(mocks.decide.mock.calls[0])
    expect(w.get('[data-test="gate-result-value"]').text()).toBe('확인 완료')
  })
  it('shows no guessed values on failed GET and retries both contexts', async () => {
    mocks.settings.mockRejectedValueOnce(error('forbidden', 403))
    const w = await show(CheckSettings)
    expect(w.find('[data-test="check-settings-scope"]').exists()).toBe(false)
    const retry = w.find('button')
    await retry.trigger('click'); await flushPromises()
    expect(w.find('[data-test="check-settings-scope"]').exists()).toBe(true)
    expect(mocks.run).toHaveBeenCalledTimes(2)
  })
  it('the header opens/closes/saves the existing memo without changing its metadata', async () => {
    const stored = { memoId: 'm1', title: 'existing', body: 'saved memo', aiRef: true }
    mocks.memos.mockResolvedValue([stored])
    mocks.update.mockImplementation(async (_f, _n, _id, input) => { const next = { ...stored, ...input }; mocks.memos.mockResolvedValue([next]); return next })
    const w = await show(defineComponent({ setup: () => () => h('div', [h(CardMemoButton, { target }), h(GateMemoPanel, { target })]) }), {})
    await w.get('[data-test="designer-inspector-memo"]').trigger('click'); await flushPromises()
    expect(w.get('textarea').element.value).toBe('saved memo')
    await w.get('textarea').setValue('discard')
    await w.get('[data-test="gate-memo-close"]').trigger('click')
    await w.get('[data-test="designer-inspector-memo"]').trigger('click'); await flushPromises()
    expect(w.get('textarea').element.value).toBe('saved memo')
    await w.get('textarea').setValue('new memo')
    await w.get('[data-test="gate-memo-save"]').trigger('click'); await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith(target.flowId, target.nodeId, 'm1', { body: 'new memo' })
    expect(w.find('[data-test="gate-memo-panel"]').exists()).toBe(false)
    expect(mocks.decide).not.toHaveBeenCalled()
  })
  it('real Frame keeps header kind/title/memo/status/more and Q&A/attachments; actions are only in documents', async () => {
    const selection = SELECTIONS.graphGate
    const w = await show(DesignerInspector, { selection })
    const header = w.get('[data-test="designer-inspector-header"]')
    expect(header.find('[data-test="gate-confirm"]').exists()).toBe(false)
    expect(header.find('[data-test="designer-inspector-kind"]').exists()).toBe(true)
    expect(header.find('[data-test="designer-inspector-memo"]').exists()).toBe(true)
    expect(header.find('[data-test="designer-inspector-title"]').exists()).toBe(true)
    expect(header.find('[data-test="designer-inspector-more"]').exists()).toBe(true)
    expect(header.text()).not.toContain('시안 상태 초기화')
    expect(w.text()).toContain('Q&A'); expect(w.text()).toContain('첨부')
    expect(w.find('[data-test="gate-result"]').exists()).toBe(true)
  })
})
