import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, ref } from 'vue'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, patchRequest: http.patch }))
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import { hasAiTab } from '@/features/designer/inspector/inspectorTarget'
import { cardRoleRows, SETTINGS_FIELDS, AI_FIELDS } from '@/features/designer/inspector/features/settings/cardRolesModel'
import aiRegistration from '@/features/designer/inspector/features/ai/register'
import settingsRegistration from '@/features/designer/inspector/features/settings/register'
import { createInspectorRegistry, registrationApplies } from '@/features/designer/inspector/inspectorRegistry'
import { inspectorTabsWith, inspectorTabStripMinWidth, resolveInspectorTab } from '@/features/designer/inspector/inspectorTabs'
import { inspectorCardState } from '@/features/designer/inspector/inspectorTarget'
import { toSettingsNode } from '@/features/document/api/settings'
import { cardStateView, CARD_STATE_KEYS, cardNodeRun } from '@/features/designer/center/graph/cardStateView'
import { toFlowRun } from '@/features/project/flowRunApi'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { target, cardRoles } from '../../ai/rolesFixtures.0113'
import { prepareInspectorI18n } from './support/inspectorSupport'
import aiMessages from '@/features/designer/inspector/features/ai/messages'
import roleMessages from '@/features/ai/roles/messages'
import ko from '@/app/i18n/ko'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
function settingsRaw(kind = 'run'): any {
  const fields: Record<string,any> = {}
  const values: Record<string,any> = { instruction: 'use', instruction_ai: 'old', run_ai: 'old', review_ai: 'old-review', review_count: 0, review_basis: 'last', instruction_review_ai: 'gone', instruction_review_count: 2, restart: 2, time_limit: 60, result_cause: 'human', follow_scope: 'full', pre_instruction_auto: true, hold: true }
  for (const [name,value] of Object.entries(values)) fields[name] = { value, stored: value, default: null, choices: name.includes('count') || name === 'restart' ? [-1,0,1,2,3] : name === 'review_basis' ? ['type','last'] : name === 'instruction' ? ['use','omit'] : name === 'time_limit' ? [60,120] : null }
  return { card_kind: kind, settings_version: 7, locked: false, fields, ai_roles: { options_state: 'ok', options: [{ id: 'a', name: 'None' }], card_init_state: 'legacy', has_legacy_roles: true,
    preserved: { run_ai: 'old', review_ai: 'old-review', review_count: 0, review_basis: 'last' }, roles: {
      ...(kind !== 'plan' ? { instruction: { selected: null, state: 'assigned_ok', outcome: 'ai', display_name: 'Old AI', provider_id: 'old', legacy_flag: true, source: 'legacy_fields' } } : {}),
      execution: { selected: { kind: 'assigned', provider_id: 'gone' }, state: 'assigned_unavailable', outcome: 'unavailable', display_name: 'Gone AI', provider_id: 'gone', cause: 'deleted', legacy_flag: false, source: 'card_explicit' },
      review: { selected: { kind: 'not_used' }, state: 'not_used', outcome: 'no_review', legacy_flag: false, source: 'card_explicit' },
    } } }
}
let raw: any
beforeEach(() => {
  vi.clearAllMocks(); raw = settingsRaw(); i18n.global.locale.value = 'ko'
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/settings')) return { data: structuredClone(raw) }
    if (url.endsWith('/providers')) return { data: { providers: [{ id: 'a', name: 'None' }] } }
    if (url.endsWith('/ai-invocations')) return { data: { ok: true, available: false, invocations: [], next_cursor: null } }
    throw new Error('offline')
  })
  http.patch.mockImplementation(async (_url: string, body: any) => {
    if (body.field.endsWith('_role')) raw.ai_roles.roles[body.field.replace('_role','')].selected = body.value
    else raw.fields[body.field].value = body.value
    raw.settings_version++
    return { data: structuredClone(raw) }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })
async function tab(panel: 'ai' | 'settings', kind = 'run') {
  raw = settingsRaw(kind)
  wrapper = mount(SettingsTab, { props: { target: target(kind === 'plan' ? 'WP' : kind === 'test' ? 'TS' : 'T'), panel }, global: { plugins: [i18n] } })
  await flushPromises(); return wrapper
}
describe('D §8 UI conditions, settings remainder and preserved values', () => {
  it.each([['T','document',true],['N','document',true],['TS','document',true],['WP','document',true],['R','document',false],['B','document',false],['AC','document',false],['T','gate',false],['T','unknown',false],['T','custom',true]] as const)('%s/%s AI visibility=%s', (type, nodeType, shown) => {
    expect(hasAiTab(target(type,nodeType))).toBe(shown)
    expect(registrationApplies(aiRegistration,target(type,nodeType))).toBe(shown)
    expect(hasAiTab({ ...target(type,nodeType), nodeType: null })).toBe(false)
    expect(registrationApplies(aiRegistration,{ ...target(type,nodeType), nodeType: null })).toBe(false)
  })
  it('the real Frame shows AI right after 문서 and commits fallback on a role-free selection', async () => {
    const registry = createInspectorRegistry({ './features/ai/register.ts': { default: aiRegistration }, './features/settings/register.ts': { default: settingsRegistration } })
    const node = { id: 'gn_1', kind: 'task', title: 'card', x: 0, y: 0, docType: 'T', definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '', assignee: null, rootBound: false } }
    const graph = { flowId: 'f', nodes: [node], edges: [], ownership: 'graph', viewMode: 'draft' }
    wrapper = mount(DesignerInspector, { props: { selection: { projectId: 'p', graph, node }, registry }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.findAll('[role="tab"]').map(item => item.text())).toEqual(['문서','AI','Q&A','첨부','설정'])
    await wrapper.findAll('[role="tab"]').find(item => item.text() === 'AI')!.trigger('click'); await flushPromises()
    expect(wrapper.get('[role="tabpanel"]').attributes('data-tab')).toBe('ai')
    const root = { ...node, definition: { ...node.definition, docType: 'R' } }
    await wrapper.setProps({ selection: { projectId: 'p', graph, node: root } }); await flushPromises()
    expect(wrapper.findAll('[role="tab"]').map(item => item.text())).toEqual(['문서','Q&A','첨부','설정'])
    expect(wrapper.get('[role="tabpanel"]').attributes('data-tab')).toBe('documents')
    expect(http.patch).not.toHaveBeenCalled()
    expect(http.get.mock.calls.some(([url]) => url === '/api/v1/me/ai-roles')).toBe(false)
  })
  it('keeps tab order, 52px geometry and fallback when AI disappears', () => {
    expect(inspectorTabsWith(['ai','settings','attachments','qna','documents'])).toEqual(['documents','ai','qna','attachments','settings'])
    expect(aiRegistration.order).toBeGreaterThan(500); expect(aiRegistration.order).toBeLessThan(700)
    expect(inspectorTabStripMinWidth(5)).toBe(292)
    expect(resolveInspectorTab('ai',['documents','qna','attachments','settings'])).toBe('documents')
  })
  it('uses selected only, never copies outcome/preserved into the control, and holds an omitted instruction', () => {
    const roles = cardRoles(); const before = structuredClone(roles)
    expect(cardRoleRows(roles,'use')[0]).toMatchObject({ role: 'instruction', selected: null, legacyFlag: true, providerId: 'old', held: false })
    expect(cardRoleRows(roles,'omit').map(row => [row.role, row.held])).toEqual([['instruction',true],['execution',false],['review',false]])
    expect(roles).toEqual(before)
    expect(SETTINGS_FIELDS).toEqual({ run: ['instruction'], test: ['instruction','result_cause','follow_scope'], plan: ['pre_instruction_auto'] })
    expect(AI_FIELDS.test).toEqual(['review_count'])
  })
  it.each(['run','test','plan'])('%s settings keeps procedure fields only', async kind => {
    const w = await tab('settings',kind)
    expect(w.find('[data-test="inspector-run-settings"]').exists()).toBe(false)
    expect(w.find('[data-role]').exists()).toBe(false)
    expect(w.find('[data-test="settings-instruction"]').exists()).toBe(kind !== 'plan')
    expect(w.find('[data-test="settings-result-check"]').exists()).toBe(kind === 'test')
    expect(w.find('[data-test="settings-pre-instruction-switch"]').exists()).toBe(kind === 'plan')
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('AI shows four rows (지시 · 실행 · 검수 AI, 검수 횟수), no notice or warning, and saved disabled review values', async () => {
    const w = await tab('ai')
    expect(w.findAll('[data-role]').map(row => row.attributes('data-role'))).toEqual(['instruction','execution','review'])
    expect(w.findAll('[data-test="inspector-control-row"]').map(row => row.attributes('data-label'))).toEqual(['지시 AI','실행 AI','검수 AI','검수 횟수'])
    const unset = w.get('[data-role="instruction"] select')
    expect((unset.element as HTMLSelectElement).value).toBe(''); expect(unset.get('option[value=""]').text()).toBe('미설정')
    expect(w.get('[data-role="execution"] select').findAll('option').map(option => option.text())).toContain('사용하지 않음')
    expect(w.text()).not.toContain('이전 설정'); expect(w.find('[data-test="ai-legacy"]').exists()).toBe(false)
    expect(w.find('[role="img"]').exists()).toBe(false); expect(w.find('[data-test="ai-invocations"]').exists()).toBe(false)
    expect((w.get('[data-role="execution"] select').element as HTMLSelectElement).value).toBe('assigned:gone')
    expect(w.get('[data-role="execution"]').text()).toContain('Gone AI')
    const count = w.get('[data-test="inspector-run-settings-review_count"]')
    expect(count.attributes('disabled')).toBeDefined(); expect((count.element as HTMLSelectElement).value).toBe('0')
    expect(count.findAll('option').map(option => option.text())).toEqual(['안 함','1회','2회','3회'])
    expect(w.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
    await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
    const fold = w.get('[data-test="inspector-ai-advanced"]')
    expect(fold.findAll('[data-test="inspector-control-row"]').map(row => row.attributes('data-label'))).toEqual(['재시작','실행 제한 시간','검수 기준','지시서 검수 AI','지시서 검수 횟수'])
    expect(fold.text()).toContain('실행 설정'); expect(fold.text()).toContain('지시서 검수')
    expect(w.find('[data-test="ai-review-count"] [data-test="inspector-run-settings-review_count"]').exists()).toBe(true)
    const basis = w.get('[data-test="inspector-run-settings-review_basis"]')
    expect(basis.attributes('disabled')).toBeDefined(); expect((basis.element as HTMLSelectElement).value).toBe('last')
    expect(w.get('[data-field="instruction_review_ai"] option[value=""]').text()).toBe('카드 검수 역할 값')
    expect((w.get('[data-field="instruction_review_ai"]').element as HTMLSelectElement).value).toBe('gone')
    expect(w.find('[data-test="settings-instruction"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-run-settings-run_ai"]').exists()).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('a running card locks the roles and [고급]; a waiting card keeps them', async () => {
    raw = settingsRaw(); raw.locked = true
    wrapper = mount(SettingsTab, { props: { target: target(), panel: 'ai' }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.findAll('[data-role] select').every(select => select.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.get('[data-test="inspector-run-settings-advanced"]').attributes('disabled')).toBeDefined()
    wrapper.unmount(); wrapper = null
    const w = await tab('ai')
    expect(w.get('[data-role="execution"] select').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="inspector-run-settings-advanced"]').attributes('disabled')).toBeUndefined()
  })
  it('a 테스트 card has the four rows and no [고급] (no advanced item)', async () => {
    const w = await tab('ai','test')
    expect(w.findAll('[data-role]')).toHaveLength(3)
    expect(w.find('[data-test="inspector-run-settings-review_count"]').exists()).toBe(true)
    expect(w.find('[data-test="inspector-run-settings-advanced"]').exists()).toBe(false)
  })
  it('writes one role with the confirmed version and preserves other roles/legacy/count/basis', async () => {
    const w = await tab('ai'), fields = structuredClone(raw.fields), otherRoles = structuredClone(raw.ai_roles.roles)
    await w.get('[data-role="instruction"] select').setValue('assigned:a'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(1)
    expect(http.patch).toHaveBeenCalledWith('/api/v1/flows/f/nodes/gn_1/settings', { field: 'instruction_role', value: { kind: 'assigned', provider_id: 'a' }, expected_settings_version: 7, expected_context: { run_id: null, graph_revision_no: null, draft_version: null } })
    expect(raw.fields).toEqual(fields); expect(raw.ai_roles.roles.execution).toEqual(otherRoles.execution); expect(raw.ai_roles.roles.review).toEqual(otherRoles.review)
    await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
    await w.get('[data-test="inspector-run-settings-restart"]').setValue('1'); await flushPromises()
    expect(http.patch.mock.calls[1][1]).toEqual({ field: 'restart', value: 1, expected_settings_version: 8, expected_context: { run_id: null, graph_revision_no: null, draft_version: null } })
  })
  it('PLAN has no 지시 AI; an omitted instruction keeps its 지시 AI and 지시서 검수 off with their values, without saving', async () => {
    const w = await tab('ai','plan'); expect(w.findAll('[data-role]').map(row => row.attributes('data-role'))).toEqual(['execution','review'])
    await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
    expect(w.get('[data-test="inspector-ai-advanced"]').findAll('[data-test="inspector-control-row"]').map(row => row.attributes('data-label'))).toEqual(['재시작','실행 제한 시간','검수 기준'])
    w.unmount(); wrapper = null
    raw = settingsRaw(); raw.fields.instruction.value = 'omit'; raw.ai_roles.roles.instruction.selected = { kind: 'assigned', provider_id: 'a' }
    wrapper = mount(SettingsTab, { props: { target: target(), panel: 'ai' }, global: { plugins: [i18n] } }); await flushPromises()
    const held = wrapper.get('[data-role="instruction"]')
    expect(held.attributes('data-held')).toBe('true'); expect(held.get('select').attributes('disabled')).toBeDefined()
    expect((held.get('select').element as HTMLSelectElement).value).toBe('assigned:a')
    await wrapper.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
    for (const field of ['instruction_review_ai','instruction_review_count']) expect(wrapper.get(`[data-field="${field}"]`).attributes('disabled')).toBeDefined()
    expect((wrapper.get('[data-field="instruction_review_count"]').element as HTMLSelectElement).value).toBe('2')
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('card_roles_unavailable is a failure icon and reread, not a successful replacement', async () => {
    const w = await tab('ai'); http.patch.mockRejectedValue({ response: { status: 409, data: { error: { code: 'card_roles_unavailable' } } } })
    await w.get('[data-role="instruction"] select').setValue('assigned:a'); await flushPromises()
    expect(w.get('[data-test="inspector-failure-icon"]').attributes('title')).toContain('카드 AI 역할 설정을 사용할 수 없습니다')
    expect((w.get('[data-role="instruction"] select').element as HTMLSelectElement).value).toBe('')
  })
  it.each([null,undefined])('feature-off/older response (%s) keeps legacy fields with no role writes', async block => {
    if (block === undefined) delete raw.ai_roles
    else raw.ai_roles = block
    wrapper = mount(SettingsTab, { props: { target: target(), panel: 'ai' }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.find('[data-role]').exists()).toBe(false); expect(wrapper.find('[data-test="inspector-run-settings-run_ai"]').exists()).toBe(true)
    expect(http.patch).not.toHaveBeenCalled()
    const normalized = toSettingsNode(block === undefined ? { fields: {} } : { fields: {}, ai_roles: null })
    expect('aiRoles' in normalized).toBe(block !== undefined)
  })
  it('tab changes use the app/card settings session and cause no writes or setup GET', async () => {
    const current = ref(target()), panel = ref<'ai'|'settings'>('settings')
    const Host = defineComponent({ setup() { return () => h(SettingsTab,{ target: current.value, panel: panel.value, key: panel.value }) } })
    wrapper = mount(Host,{ global: { plugins: [i18n] } }); await flushPromises()
    panel.value = 'ai'; await flushPromises(); panel.value = 'settings'; await flushPromises()
    expect(http.patch).not.toHaveBeenCalled(); expect(http.get.mock.calls.some(([url]) => url === '/api/v1/me/ai-roles')).toBe(false)
  })
})
describe('three run keys, shared chip/pill and ko/en/ja', () => {
  it.each([{}, { wait_reason: null, stage_assignments: null, approval_wait_submission: null }, { wait_reason: { code: 'ai_unavailable', role: 'execution', stage: 'result', seq: 0, detail: 'deleted' }, stage_assignments: [], approval_wait_submission: { doc_id: 'd', revision_no: 3, rejection_count: 1 } }])('keeps omitted/null/value through run and Canvas APIs', extra => {
    const runNode = toFlowRun({ run_id: 'r', node_runs: [{ node_key: 'n', status: 'open', ...extra }] }).nodeRuns![0]
    const canvas = toFlowCanvas({ flow_id: 'f', nodes: [{ node_key: 'n', position: {}, editable: {}, node_run: { status: 'open', ...extra } }] })
    const canvasNode = toFlowGraph(canvas).nodes[0].nodeRun!
    for (const key of ['waitReason','stageAssignments','approvalWaitSubmission'] as const) { expect(key in runNode).toBe('wait_reason' in extra); expect(canvasNode[key]).toEqual(runNode[key]) }
  })
  it.each([['manual_write_wait','neutral'],['ai_setup_required','warning'],['ai_unavailable','danger']] as const)('%s: chip and pill share label/tone/raw detail with question priority', (code,tone) => {
    const facts = { status: 'review', awaitingApproval: true, approvalWaitReason: 'issuer_missing', waitReason: { code, role: 'execution', stage: 'result', seq: 0, detail: 'raw_reason' } }
    const expected = cardStateView(facts)!
    expect(expected).toMatchObject({ key: code, tone, reason: 'raw_reason', approvable: false })
    expect(cardStateView({ ...facts, question: true })?.key).toBe('waiting_answer')
    const nodeRun = cardNodeRun(null, { nodeKey: 'n', ...facts })!
    expect(inspectorCardState({ ...target(), nodeId: 'n', nodeRun })).toEqual(expected)
    const graph = { flowId: 'f', nodes: [{ id: 'n', kind: 'task', title: '', x: 0, y: 0, nodeRun }], edges: [] }
    expect(toVueFlowNodes(graph, null)[0].data.cardState).toEqual(expected)
    expect(cardStateView({ ...facts, status: 'done' })?.key).toBe('approved')
  })
  it.each([undefined, null])('absent/null wait reason (%s) preserves 0083/0105 question precedence', waitReason => {
    expect(cardStateView({ status: 'open', stage: 'result', runningExecutionCount: 1, question: true, waitReason })?.key).toBe('writing_result')
    expect(cardStateView({ status: 'open', dispatchErrorCode: 'no_result', question: true, waitReason })?.key).toBe('no_result')
    expect(cardStateView({ status: 'review', awaitingApproval: true, approvalWaitReason: 'instruction_not_approved', question: true, waitReason })).toMatchObject({ key: 'needs_intervention', approvable: false, reason: 'instruction_not_approved' })
  })
  it('all state keys and newly registered message structures match across three languages', () => {
    for (const language of [ko,en,ja]) for (const key of CARD_STATE_KEYS) expect(typeof language.designer.center.card_states[key]).toBe('string')
    for (const messages of [aiMessages,roleMessages]) {
      expect(Object.keys(messages.ko).sort()).toEqual(Object.keys(messages.en).sort())
      expect(Object.keys(messages.ko).sort()).toEqual(Object.keys(messages.ja).sort())
    }
    expect(ko.designer.center.card_states).toMatchObject({ manual_write_wait: '수동 작성 대기', ai_setup_required: 'AI 설정 필요', ai_unavailable: 'AI 사용 불가' })
  })
})
