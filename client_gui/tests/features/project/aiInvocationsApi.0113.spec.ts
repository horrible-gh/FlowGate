import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, shallowRef } from 'vue'
const get = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: get }))
import { getAiInvocations, toAiInvocationsPage } from '@/features/project/aiInvocationsApi'
import { useAiInvocations } from '@/features/designer/inspector/features/ai/useAiInvocations'
import AiInvocations from '@/features/designer/inspector/features/ai/AiInvocations.vue'
import { deferred, target } from '../ai/rolesFixtures.0113'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
beforeEach(() => { vi.clearAllMocks(); i18n.global.locale.value = 'ko' })
afterEach(() => { wrapper?.unmount(); wrapper = null })
function row(id = 'one', extra: Record<string,unknown> = {}) {
  return { inv_id: id, create_seq: 9, role: 'execution', purpose: 'result_write', role_source: 'card_explicit', requested_ai_id: 'requested-a', actual_ai: { id: 'actual-a', name: 'Historical AI' }, provider_match: 'different',
    provider_config: { exec_type: 'cli', kind: 'copilot', config_state: 'changed' },
    requested_model: { state: 'value', value: 'requested-model', reason: null },
    confirmed_model: { state: 'confirmed', values: ['reported-model'], coverage: 'partial', truncated: true, calls: { total: 3, reported: 1, absent: 1, unreadable: 0, failed: 1 }, bad_lines: 1, reason: null },
    compare: { status: 'partial', mismatch_found: true, reconciled: false }, result: 'succeeded', result_detail: null,
    link: { kind: 'review_of', prev_inv_id: 'earlier' }, started_at: 'start', ended_at: 'end', created_at: 'created', launch_no: 2, hop_no: 1, round_no: 3,
    ...extra }
}
function page(rows = [row()], next: any = null, available = true) { return { ok: true, available, invocations: rows, next_cursor: next } }
describe('P-1 invocation read and minimal historical view', () => {
  it('encodes both path segments and sends the default 50 plus the exact cursor pair', async () => {
    get.mockResolvedValue({ data: page() }); await getAiInvocations('f/x','n #')
    expect(get).toHaveBeenLastCalledWith('/api/v1/flows/f%2Fx/nodes/n%20%23/ai-invocations', { limit: 50 })
    await getAiInvocations('f/x','n #',{ beforeSeq: 0, beforeInvId: 'last/id' })
    expect(get).toHaveBeenLastCalledWith('/api/v1/flows/f%2Fx/nodes/n%20%23/ai-invocations', { limit: 50, before_seq: 0, before_inv_id: 'last/id' })
  })
  it('keeps server order and distinct evidence without exposing unlisted fields', () => {
    const normalized = toAiInvocationsPage(page([row('z'),row('a',{ create_seq: 8, provider_fingerprint: 'excluded', unit_key_hash: 'excluded', cli_command: 'excluded' })], { before_seq: 8, before_inv_id: 'a' }))
    expect(normalized.invocations.map(item => item.invId)).toEqual(['z','a'])
    expect(normalized.nextCursor).toEqual({ beforeSeq: 8, beforeInvId: 'a' })
    expect(normalized.invocations[0]).toMatchObject({ requestedAiId: 'requested-a', actualAi: { id: 'actual-a', name: 'Historical AI' }, requestedModel: { value: 'requested-model' }, confirmedModel: { values: ['reported-model'], coverage: 'partial', truncated: true }, compare: { status: 'partial', reconciled: false }, configState: 'changed' })
    expect(JSON.stringify(normalized)).not.toContain('excluded')
  })
  it('renders request/actual and request/confirmed separately; codes stay in tooltips', async () => {
    get.mockResolvedValue({ data: page([row('one',{ result: 'call_failed', result_detail: 'orphaned_by_restart' })]) })
    wrapper = mount(AiInvocations,{ props: { target: target() }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.findAll('th')).toHaveLength(6)
    const text = wrapper.text()
    for (const word of ['requested-a','Historical AI','requested-model','reported-model','일부 확인','설정 변경됨','호출 실패']) expect(text).toContain(word)
    expect(text).not.toContain('orphaned_by_restart'); expect(text).not.toContain('result_write'); expect(text).not.toContain('review_of')
    expect(wrapper.html()).toContain('orphaned_by_restart'); expect(wrapper.html()).toContain('review_of')
    expect(get.mock.calls.every(([url]) => url.endsWith('/ai-invocations'))).toBe(true)
  })
  it.each([
    ['running', { confirmed_model: { state: null, values: [], calls: null }, result: 'running', compare: { status: 'requested_only' } }, '실행 중'],
    ['requested only', { confirmed_model: { state: 'unverifiable', reason: 'report_missing', values: [], calls: null }, compare: { status: 'requested_only' } }, '요청만 있음'],
    ['unverifiable', { requested_model: { state: 'none', value: null, reason: 'cli_no_model_arg' }, confirmed_model: { state: 'unverifiable', reason: 'kind_reports_no_model', values: [], calls: null }, compare: { status: 'unidentifiable' } }, '확인 불가'],
    ['admission refused', { actual_ai: null, requested_model: { state: 'not_parsed', value: null, reason: 'not_launched' }, confirmed_model: { state: 'unverifiable', reason: 'no_result', values: [], calls: null }, compare: { status: 'not_called' }, result: 'admission_refused', result_detail: 'designated_not_enabled' }, '호출 안 됨'],
  ] as const)('%s is not treated as reconciled', async (_name, extra, expected) => {
    get.mockResolvedValue({ data: page([row('one',extra)]) }); wrapper = mount(AiInvocations,{ props: { target: target() }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.text()).toContain(expected)
    expect(wrapper.text()).not.toContain('report_missing'); expect(wrapper.text()).not.toContain('not_launched'); expect(wrapper.text()).not.toContain('designated_not_enabled')
  })
  it('feature-off disables the view, does not show a normal empty table or invent records', async () => {
    get.mockResolvedValue({ data: page([],null,false) }); wrapper = mount(AiInvocations,{ props: { target: target() }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.get('[data-test="ai-invocations"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('table').exists()).toBe(false); expect(wrapper.find('p').exists()).toBe(false)
  })
  it('a refused read uses the existing failure and retry UI, never an empty success', async () => {
    get.mockRejectedValue({ response: { status: 403, data: { error: { code: 'flow_access_denied', message: 'Denied' } } } })
    wrapper = mount(AiInvocations,{ props: { target: target() }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.find('[data-test="inspector-read-failure"]').exists()).toBe(true); expect(wrapper.find('table').exists()).toBe(false)
    get.mockResolvedValue({ data: page([]) }); await wrapper.get('[data-test="inspector-read-retry"]').trigger('click'); await flushPromises()
    expect(wrapper.find('[data-test="inspector-read-failure"]').exists()).toBe(false); expect(wrapper.find('table').exists()).toBe(true)
  })
  it('paginates without client sorting, ignores late pages after a card switch, and drops closed reads', async () => {
    const current = shallowRef<InspectorTarget | null>(target())
    let read!: ReturnType<typeof useAiInvocations>
    const Host = defineComponent({ setup() { read = useAiInvocations(() => current.value); return () => h('div') } })
    get.mockResolvedValueOnce({ data: page([row('new')],{ before_seq: 9, before_inv_id: 'new' }) })
    wrapper = mount(Host); await flushPromises()
    get.mockResolvedValueOnce({ data: page([row('old',{ create_seq: 3 })],{ before_seq: 3, before_inv_id: 'old' }) })
    await read.next(); expect(read.page.value?.invocations.map(item => item.invId)).toEqual(['new','old'])
    const late = deferred<any>(); get.mockReturnValueOnce(late.promise)
    const next = read.next(); get.mockResolvedValueOnce({ data: page([row('other')]) })
    current.value = { ...target(), nodeId: 'other' }; await flushPromises(); late.resolve({ data: page([row('late')]) }); await next
    expect(read.page.value?.invocations.map(item => item.invId)).toEqual(['other'])
    const last = deferred<any>(); get.mockReturnValueOnce(last.promise); const reload = read.reload(); wrapper.unmount(); wrapper = null; last.resolve({ data: page([row('closed')]) }); await reload
    expect(read.page.value?.invocations.map(item => item.invId)).toEqual(['other'])
  })
})
