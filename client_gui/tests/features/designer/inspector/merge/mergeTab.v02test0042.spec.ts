/**
 * flowgate.v02test.0042 — the card's '병합 · 충돌' Inspector tab (시안 v7, 0005-CH turns 38-40): shown only with merge work,
 * always last, blue while it only shows progress and red while a person must choose; ① catching up, ② could not
 * reconcile (the mockup's framed box: files with how each overlaps, [AI로 해결] beside its AI select, the two buttons, the
 * waiting cards), ③ per-file pick (segmented) and [이대로 맞추고 병합], ④ merged, failed ([다시 시도]) and the waiting
 * card's screen; every button's API call
 * and its refusal; a tab request (the Canvas card's 병합 충돌 / a notification / the Git panel) opens the tab.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import MergeTab from '@/features/designer/inspector/features/merge/MergeTab.vue'
import mergeRegistration from '@/features/designer/inspector/features/merge/register'
import {
  mergeReasonKey,
  mergeScreenOf,
  mergeToneOfState,
  mergeToneOfStatus,
  mergeWorkOf,
} from '@/features/designer/inspector/features/merge/mergeModel'
import { createInspectorRegistry, inspectorRegistry, registrationViolations } from '@/features/designer/inspector/inspectorRegistry'
import { INSPECTOR_CONDITIONAL_TABS, INSPECTOR_TABS } from '@/features/designer/inspector/inspectorTabs'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import { prepareInspectorI18n } from '../support/inspectorSupport'

const api = vi.hoisted(() => ({ read: vi.fn(), act: vi.fn(), choices: vi.fn() }))
vi.mock('@/features/document/api/cardMerge', async (original) => ({
  ...await original<typeof import('@/features/document/api/cardMerge')>(),
  cardMergeRead: api.read,
  cardMergeAct: api.act,
}))
vi.mock('@/features/document/api/settings', async (original) => ({
  ...await original<typeof import('@/features/document/api/settings')>(),
  settingsChoicesRead: api.choices,
}))

const i18n = prepareInspectorI18n()
const t = (key: string, params: Record<string, unknown> = {}) => i18n.global.t(key, params)
const FLOW = 'flowgate.v02test.0036'

function node(id: string, title: string, run: Partial<FlowGraphNodeRun> | null): FlowGraphNode {
  return {
    id, kind: 'task', title, state: 'in_progress', docType: 'T', x: 0, y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'doc_t', label: title, assignee: null, rootBound: false },
    document: null, slot: null,
    nodeRun: run ? { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, attemptId: 'att_4', ...run } : null,
  }
}
function selection(run: Partial<FlowGraphNodeRun> | null, id = 'gn_4', title = '업로드 대기열 이전'): FlowGraphSelection {
  const graph: FlowGraph = { flowId: FLOW, nodes: [node(id, title, run)], edges: [], ownership: 'graph', draft: null, viewMode: 'revision' }
  return { projectId: 'flowgate', graph, node: graph.nodes[0] }
}
const raw = (over: Record<string, unknown> = {}) => ({
  flow_id: FLOW, node_key: 'gn_4', attempt_id: 'att_4', state: 'needs_choice', integration_status: 'conflict',
  reason: 'conflict_deferred', blocked_by: null, files: [{ path: 'a.ts', resolved: false }, { path: 'b.ts', resolved: false }],
  session: null, resolving: null, default_provider: { id: 'aip_x', name: 'Claude' },
  actions: ['ai_resolve', 'pick', 'discard_rerun'], ...over,
})
async function state(over: Record<string, unknown> = {}) {
  const { toCardMergeState } = await import('@/features/document/api/cardMerge')
  return toCardMergeState(raw(over))
}

const wrappers: VueWrapper<any>[] = []
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); document.body.innerHTML = '' })
beforeEach(() => {
  vi.clearAllMocks()
  i18n.global.locale.value = 'ko'
  api.choices.mockResolvedValue({ providers: [{ id: 'aip_x', name: 'Claude' }, { id: 'aip_y', name: 'Codex' }], defaultProviderId: null, reviewCounts: [] })
})

async function mountTab(run: Partial<FlowGraphNodeRun> | null = { integrationStatus: 'conflict', integrationReason: 'conflict_deferred' }) {
  const target = toInspectorTarget(selection(run))!
  const w = mount(MergeTab, { props: { target }, attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w)
  await flushPromises()
  return w
}
const q = (w: VueWrapper<any>, test: string) => w.find(`[data-test="${test}"]`)

describe('registry contract and tab rules', () => {
  it('merge is the last tab and the only conditional one', () => {
    expect(INSPECTOR_TABS.at(-1)).toBe('merge')
    expect(INSPECTOR_CONDITIONAL_TABS).toEqual(['merge'])
  })

  it('a tab visible / tone that is no function is a contract violation', () => {
    const modules = { './features/a/register.ts': { default: { key: 'a', order: 1, requiresDocument: false, messages: { ko: {}, en: {}, ja: {} },
      tab: { key: 'merge', component: {}, visible: true, tone: 'red' } } } }
    expect(registrationViolations(modules)).toEqual(['features/a: tab visible is not a function', 'features/a: tab tone is not a function'])
  })

  it('the real registry holds the merge registration with its suppliers', () => {
    const entry = inspectorRegistry.entries.find((item) => item.key === 'merge')!
    expect(entry.tab?.key).toBe('merge')
    expect(typeof entry.tab?.visible).toBe('function')
    expect(typeof entry.tab?.tone).toBe('function')
    expect(entry.requiresDocument).toBe(false)
  })

  it('the model: work, tone, screen and reason keys', () => {
    expect(mergeWorkOf({ nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, integrationStatus: 'pending' } })).toBe(true)
    expect(mergeWorkOf({ nodeRun: { status: 'done', openedAt: null, finishedAt: null, resultDocId: null, integrationStatus: 'integrated' } })).toBe(false)
    expect(mergeWorkOf({ nodeRun: null })).toBe(false)
    expect([mergeToneOfState('waiting'), mergeToneOfState('catching_up'), mergeToneOfState('needs_choice'), mergeToneOfState('failed'), mergeToneOfState('hidden')])
      .toEqual(['info', 'info', 'danger', 'danger', null])
    expect([mergeToneOfStatus('pending'), mergeToneOfStatus('integrating'), mergeToneOfStatus('conflict'), mergeToneOfStatus('failed'), mergeToneOfStatus(null)])
      .toEqual(['info', 'info', 'danger', 'danger', null])
    expect(mergeScreenOf(null, false, false)).toBe('loading')
    expect(mergeScreenOf('hidden', true, false)).toBe('merged')
    expect(mergeScreenOf('hidden', false, false)).toBe('none')
    expect(mergeScreenOf('needs_choice', true, true)).toBe('pick')
    expect(mergeReasonKey('merge_session:12')).toBe('merge_session')
    expect(mergeReasonKey('weird')).toBe('other')
  })
})

// --- The Frame: show / hide, colour, position, tab request ------------------------------------------

const DocumentsStub = defineComponent({ props: ['target'], setup: () => () => h('p', { 'data-test': 'documents-stub' }, 'docs') })
const fixture = createInspectorRegistry({
  './features/documents/register.ts': { default: { key: 'documents', order: 100, requiresDocument: false, tab: { key: 'documents', component: DocumentsStub }, messages: { ko: {}, en: {}, ja: {} } } },
  './features/merge/register.ts': { default: mergeRegistration },
})

async function mountFrame(run: Partial<FlowGraphNodeRun> | null, extra: Record<string, unknown> = {}) {
  const w = mount(DesignerInspector, { props: { selection: selection(run), registry: fixture, ...extra }, attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w)
  await flushPromises()
  return w
}
const tabButtons = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]')
const mergeLabel = () => t('gui.designer.inspector.tabs.merge')

describe('the Frame shows the merge tab only with merge work', () => {
  it.each([null, { integrationStatus: null }, { integrationStatus: 'integrated' }])('no work (%j): no merge tab', async (run) => {
    api.read.mockResolvedValue(await state({ state: 'hidden' }))
    const w = await mountFrame(run as Partial<FlowGraphNodeRun> | null)
    expect(tabButtons(w).map((tab) => tab.text())).not.toContain(mergeLabel())
  })

  it.each([
    ['pending', 'info'], ['integrating', 'info'], ['conflict', 'danger'], ['failed', 'danger'],
  ])('%s: the merge tab is shown last, %s coloured', async (status, tone) => {
    api.read.mockResolvedValue(await state({ state: 'hidden' }))
    const w = await mountFrame({ integrationStatus: status })
    const tabs = tabButtons(w)
    expect(tabs.at(-1)!.text()).toBe(mergeLabel())
    expect(tabs.at(-1)!.attributes('data-tone')).toBe(tone)
    expect(tabs.slice(0, -1).every((tab) => tab.attributes('data-tone') === undefined)).toBe(true)
  })

  it('the colour follows the read state: an AI resolving a conflict is blue', async () => {
    api.read.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_1' }, actions: [] }))
    const w = await mountFrame({ integrationStatus: 'conflict' }, { tabRequest: { flowId: FLOW, nodeId: 'gn_4', tab: 'merge', seq: 1 } })
    await flushPromises()
    expect(tabButtons(w).at(-1)!.attributes('data-tone')).toBe('info')
  })

  it('a tab request for the selected card opens its merge tab', async () => {
    api.read.mockResolvedValue(await state())
    const w = await mountFrame({ integrationStatus: 'conflict' })
    expect(w.get('[data-test="designer-inspector-body"]').attributes('data-tab')).toBe('documents')
    await w.setProps({ tabRequest: { flowId: FLOW, nodeId: 'gn_4', tab: 'merge', seq: 1 } })
    await flushPromises()
    expect(w.get('[data-test="designer-inspector-body"]').attributes('data-tab')).toBe('merge')
    expect(w.find('[data-test="merge-tab"]').exists()).toBe(true)
  })

  it('work that ends while the tab is open keeps the tab with ④ 병합 완료; leaving the card drops it', async () => {
    api.read.mockResolvedValueOnce(await state()).mockResolvedValue(await state({ state: 'hidden', integration_status: 'integrated', actions: [] }))
    const w = await mountFrame({ integrationStatus: 'conflict' }, { tabRequest: { flowId: FLOW, nodeId: 'gn_4', tab: 'merge', seq: 1 } })
    await flushPromises()
    expect(w.find('[data-test="merge-tab"]').attributes('data-screen')).toBe('needs_choice')
    await w.setProps({ selection: selection({ integrationStatus: 'integrated', status: 'done' }) })
    await flushPromises()
    expect(tabButtons(w).at(-1)!.text()).toBe(mergeLabel())
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('merged')
    expect(w.get('[data-test="merge-merged-text"]').text()).toBe(t('gui.inspector.merge.merged.body'))
    await w.setProps({ selection: null })
    await flushPromises()
    await w.setProps({ selection: selection({ integrationStatus: 'integrated', status: 'done' }) })
    await flushPromises()
    expect(tabButtons(w).map((tab) => tab.text())).not.toContain(mergeLabel())
  })
})

// --- The tab's screens and buttons -------------------------------------------------------------------

describe('screens', () => {
  it('waiting: names the card it waits for (its conflict) and goes there', async () => {
    api.read.mockResolvedValue(await state({ state: 'waiting', integration_status: 'pending', reason: 'session_busy', blocked_by: { node_key: 'gn_2', title: '승인 화면 정리', why: 'conflict' }, files: [], actions: [] }))
    const w = await mountTab({ integrationStatus: 'pending' })
    expect(q(w, 'merge-box').classes()).toContain('is-info')
    expect(q(w, 'merge-waiting-text').text()).toBe(t('gui.inspector.merge.waiting.before') + t('gui.inspector.merge.waiting.blocked_conflict', { title: '승인 화면 정리' }) + t('gui.inspector.merge.waiting.after'))
    expect(q(w, 'merge-waiting-text').find('b').text()).toBe(t('gui.inspector.merge.waiting.blocked_conflict', { title: '승인 화면 정리' }))
    const heard: unknown[] = []
    const listener = (event: Event) => heard.push((event as CustomEvent).detail)
    window.addEventListener('fg:open_card_merge', listener)
    expect(q(w, 'merge-go-blocked').text()).toBe(t('gui.inspector.merge.waiting.go', { title: '승인 화면 정리' }))
    expect(q(w, 'merge-go-blocked').find('svg').exists()).toBe(true)
    await q(w, 'merge-go-blocked').trigger('click')
    window.removeEventListener('fg:open_card_merge', listener)
    expect(heard).toEqual([{ flowId: FLOW, nodeKey: 'gn_2' }])
  })

  it('waiting with no named card: its turn, no button', async () => {
    api.read.mockResolvedValue(await state({ state: 'waiting', integration_status: 'pending', reason: 'approved', files: [], actions: [] }))
    const w = await mountTab({ integrationStatus: 'pending' })
    expect(q(w, 'merge-waiting-text').text()).toBe(t('gui.inspector.merge.waiting.turn'))
    expect(q(w, 'merge-go-blocked').exists()).toBe(false)
  })

  it('① catching up: progress bar and step log, no buttons', async () => {
    api.read.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_1' }, files: [{ path: 'a.ts', resolved: true }, { path: 'b.ts', resolved: false }], actions: [] }))
    const w = await mountTab()
    expect(q(w, 'merge-box').classes()).toContain('is-info')
    expect(q(w, 'merge-catching-up-text').text()).toBe(t('gui.inspector.merge.catching_up.resolving', { count: 2 }))
    expect(w.find('.mz-prog i').attributes('style')).toContain('width: 50%')
    expect(w.findAll('[data-test="merge-log"] > div').map((row) => row.attributes('data-step'))).toEqual(['ok', 'run', 'todo'])
    expect(w.findAll('[data-test="merge-log"] > div')[1].text()).toContain('1/2')
    expect(w.findAll('button')).toHaveLength(0)
  })

  it('② could not reconcile: the file box with how each overlaps, [AI로 해결] beside its AI select, then the two buttons', async () => {
    api.read.mockResolvedValue(await state({ files: [{ path: 'src/a.ts', resolved: false, kind: 'both' }, { path: 'server/attachments/routes.py', resolved: false, kind: 'deleted_latest' }] }))
    const w = await mountTab()
    expect(q(w, 'merge-box').classes()).not.toContain('is-info')
    const rows = w.findAll('[data-test="merge-files"] .mz-fr')
    expect(rows.map((row) => row.find('code').text())).toEqual(['src/a.ts', 'server/attachments/routes.py'])
    expect(rows.map((row) => row.find('.mz-why').text())).toEqual([t('gui.inspector.merge.kinds.both'), t('gui.inspector.merge.kinds.deleted_latest')])
    // a file one side deleted is named in the sentence (the mockup's "attachments/routes.py")
    expect(q(w, 'merge-summary').find('b').text()).toBe('attachments/routes.py')
    const [first, second] = w.findAll('.mz-btns')
    expect(first.findAll('[data-test]').map((el) => el.attributes('data-test'))).toEqual(['merge-ai', 'merge-ai-provider'])
    expect(second.findAll('button').map((b) => b.attributes('data-test'))).toEqual(['merge-pick-open', 'merge-discard'])
    expect(q(w, 'merge-merge').exists()).toBe(false)
    expect(q(w, 'merge-meta').text()).toBe(t('gui.inspector.merge.needs_choice.free'))
  })

  it('② with no deleted file says how many files overlap', async () => {
    api.read.mockResolvedValue(await state())
    const w = await mountTab()
    expect(q(w, 'merge-summary').text()).toBe(t('gui.inspector.merge.needs_choice.both', { count: 2 }))
  })

  it('② lists the cards waiting behind it and goes to one', async () => {
    api.read.mockResolvedValue(await state({ waiting_cards: [{ node_key: 'gn_2', title: '카드 첨부 탭 제거' }] }))
    const w = await mountTab()
    expect(q(w, 'merge-meta').text()).toContain(t('gui.inspector.merge.waiting_cards'))
    const heard: unknown[] = []
    const listener = (event: Event) => heard.push((event as CustomEvent).detail)
    window.addEventListener('fg:open_card_merge', listener)
    await q(w, 'merge-waiting-card').trigger('click')
    window.removeEventListener('fg:open_card_merge', listener)
    expect(heard).toEqual([{ flowId: FLOW, nodeKey: 'gn_2' }])
  })

  it('[AI로 해결] starts with the card AI by default, or the AI picked in the select beside it', async () => {
    api.read.mockResolvedValue(await state())
    api.act.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_9' }, actions: [], started_run_id: 'run_9' }))
    const w = await mountTab()
    expect(w.findAll('[data-test="merge-ai-provider"] option').map((o) => o.text())).toEqual([t('gui.inspector.merge.needs_choice.ai_default_named', { name: 'Claude' }), 'Codex'])
    await q(w, 'merge-ai').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', { action: 'ai_resolve', expectedAttemptId: 'att_4', providerId: null })
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('catching_up')
  })

  it('[AI로 해결] with another AI picked sends it', async () => {
    api.read.mockResolvedValue(await state())
    api.act.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_9' }, actions: [] }))
    const w = await mountTab()
    await q(w, 'merge-ai-provider').setValue('aip_y')
    await q(w, 'merge-ai').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', { action: 'ai_resolve', expectedAttemptId: 'att_4', providerId: 'aip_y' })
  })

  it('③ per-file pick: a segmented AI에게 / 이 카드 쪽 / 최신 코드 쪽 per file, then [이대로 맞추고 병합]', async () => {
    api.read.mockResolvedValue(await state())
    api.act.mockResolvedValueOnce(await state({ state: 'hidden', integration_status: 'integrated', actions: [] }))
    const w = await mountTab()
    await q(w, 'merge-pick-open').trigger('click')
    const rows = w.findAll('[data-test="merge-pick-list"] .mz-fr')
    expect(rows.map((row) => row.attributes('data-path'))).toEqual(['a.ts', 'b.ts'])
    expect(rows[0].findAll('.mz-pick button').map((b) => b.text())).toEqual([t('gui.inspector.merge.pick.ai'), t('gui.inspector.merge.pick.card'), t('gui.inspector.merge.pick.latest')])
    expect(rows[0].get('[data-test="merge-pick-ai"]').classes()).toContain('on')
    expect(w.find('textarea').exists()).toBe(false)
    await rows[0].get('[data-test="merge-pick-card"]').trigger('click')
    await rows[1].get('[data-test="merge-pick-latest"]').trigger('click')
    expect(rows[0].get('[data-test="merge-pick-card"]').attributes('aria-checked')).toBe('true')
    expect(q(w, 'merge-pick-submit').text()).toBe(t('gui.inspector.merge.pick.submit'))
    await q(w, 'merge-pick-submit').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', { action: 'pick', expectedAttemptId: 'att_4', files: [{ path: 'a.ts', side: 'card' }, { path: 'b.ts', side: 'latest' }], providerId: null })
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('merged')
    expect(q(w, 'merge-box').classes()).toContain('is-ok')
  })

  it('③ a side that deleted the file cannot be picked', async () => {
    api.read.mockResolvedValue(await state({ files: [{ path: 'gone.py', resolved: false, kind: 'deleted_latest' }] }))
    const w = await mountTab()
    await q(w, 'merge-pick-open').trigger('click')
    expect(q(w, 'merge-pick-latest').attributes('disabled')).toBeDefined()
    expect(q(w, 'merge-pick-card').attributes('disabled')).toBeUndefined()
    await q(w, 'merge-pick-cancel').trigger('click')
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('needs_choice')
  })

  it('② after the AI reconciled every file: [병합] and the discard button', async () => {
    api.read.mockResolvedValue(await state({ files: [{ path: 'a.ts', resolved: true }], session: { merge_id: 31, kind: 'graph_attempt_merge', review_state: 'resolved' }, actions: ['ai_resolve', 'pick', 'merge', 'discard_rerun'] }))
    api.act.mockResolvedValueOnce(await state({ state: 'hidden', integration_status: 'integrated', actions: [] }))
    const w = await mountTab()
    expect(q(w, 'merge-summary').text()).toBe(t('gui.inspector.merge.needs_choice.resolved_all'))
    expect(w.findAll('.mz-btns button').map((b) => b.attributes('data-test'))).toEqual(['merge-merge', 'merge-discard'])
    expect(w.find('.mz-why').text()).toBe(t('gui.inspector.merge.file_resolved'))
    await q(w, 'merge-merge').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', { action: 'merge', expectedAttemptId: 'att_4' })
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('merged')
  })

  it('[이 결과 버리고 …] asks once, then discards and reruns', async () => {
    api.read.mockResolvedValue(await state())
    api.act.mockResolvedValue(await state({ state: 'hidden', actions: [] }))
    const w = await mountTab()
    await q(w, 'merge-discard').trigger('click')
    await flushPromises()
    expect(api.act).not.toHaveBeenCalled()
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')].at(-1)!
    expect(confirm.textContent?.trim()).toBe(t('gui.inspector.merge.confirm_discard.ok'))
    confirm.click()
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', { action: 'discard_rerun', expectedAttemptId: 'att_4' })
  })

  it('failed: the reason, [다시 시도] and the discard button', async () => {
    api.read.mockResolvedValue(await state({ state: 'failed', integration_status: 'failed', reason: 'verification_failed', files: [], actions: ['retry', 'discard_rerun'] }))
    api.act.mockResolvedValue(await state({ state: 'waiting', integration_status: 'pending', reason: 'retry', files: [], actions: [] }))
    const w = await mountTab({ integrationStatus: 'failed' })
    expect(q(w, 'merge-failed-text').text()).toBe(t('gui.inspector.merge.reasons.verification_failed'))
    expect(w.findAll('button').map((b) => b.attributes('data-test'))).toEqual(['merge-retry', 'merge-discard'])
    await q(w, 'merge-retry').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenLastCalledWith(FLOW, 'gn_4', { action: 'retry', expectedAttemptId: 'att_4' })
  })

  it('a refusal is shown with its code; a moved state is read again', async () => {
    api.read.mockResolvedValue(await state())
    api.act.mockRejectedValueOnce({ response: { status: 409, data: { ok: false, error: { code: 'group_busy', message: 'busy' } } } })
    const w = await mountTab()
    await q(w, 'merge-ai').trigger('click')
    await flushPromises()
    expect(q(w, 'merge-error').text()).toBe(t('gui.inspector.merge.error', { code: 'group_busy', message: t('gui.inspector.merge.error_codes.group_busy') }))
    api.act.mockRejectedValueOnce({ response: { status: 409, data: { ok: false, error: { code: 'merge_state_changed', message: 'moved' } } } })
    const reads = api.read.mock.calls.length
    await q(w, 'merge-pick-open').trigger('click')
    await q(w, 'merge-pick-submit').trigger('click')
    await flushPromises()
    expect(api.read.mock.calls.length).toBe(reads + 1)
    expect(q(w, 'merge-error').text()).toContain('merge_state_changed')
  })

  it('a failed read says so and reads again on request', async () => {
    api.read.mockRejectedValueOnce(new Error('offline')).mockResolvedValue(await state())
    const w = await mountTab()
    expect(q(w, 'merge-read-failed').exists()).toBe(true)
    await w.get('[data-test="merge-read-failed"] button').trigger('click')
    await flushPromises()
    expect(q(w, 'merge-read-failed').exists()).toBe(false)
    expect(w.get('[data-test="merge-tab"]').attributes('data-screen')).toBe('needs_choice')
  })
})
