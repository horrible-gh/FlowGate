/**
 * flowgate.v02test.0002 T-b (0006-T §2), review rej_01M6QXCBVZYSAGRV — the kept merge targets follow the Inspector's real
 * selection. The real DesignerInspector with the real registry draws a check (gate) card's 설정 tab with CheckSettings.
 * Selecting a card of another kind replaces that part (CheckSettings is unmounted, no watch of it sees the switch), and
 * coming back after a failed lookup must offer no list from before the move. A tab change on the same card keeps the
 * card selected, so its list stays. Only `@shared/api` is mocked; the context, the model and the parts stay real. No
 * consumer of the test holds the context (gateContextOf only reads it).
 * Review rej_01M6R14HCES8QJJ5: the Q&A and attachments tabs mount no part of the gate context, yet the card stays
 * selected there - 설정 -> Q&A / 첨부 -> 설정 keeps the list; a move made from those tabs still leaves the card.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(),
  getRequest: (...a: unknown[]) => http.get(...a), patchRequest: (...a: unknown[]) => http.patch(...a), postRequest: (...a: unknown[]) => http.post(...a) }))
import type { FlowGraph, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { gateContextOf } from '@/features/designer/inspector/features/gateCard/useGateContext'
import { GRAPH_GATE_NODE, GRAPH_GRAPH, GRAPH_TEST_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'

const i18n = prepareInspectorI18n()
const LIST = ['feature/v0.2.0', 'main', 'release/local']
const field = (value: unknown, stored = false) => ({ value, stored, default: null, choices: null })
const entry = (branch: string | null, extra = {}) => ({ branch, checked: true, error: null, unmerge_supported: false, warnings: [], ...extra })
const unchecked = { checked: false, error: null, unmerge_supported: null, warnings: [] }
let raw: any
let id = 0
let graph: FlowGraph
let gateId: string
let otherGateId: string
let wrapper: VueWrapper<any> | null = null

function lookupSucceeds() {
  raw.merge_target_options = [...LIST]
  raw.merge_target_state = { status: 'ok', code: null, reason: null, current: entry(raw.default_target_branch), default: entry(raw.default_target_branch) }
}
function lookupFails(status: 'timeout' | 'failed') {
  raw.merge_target_options = []
  raw.merge_target_state = { status, code: status === 'timeout' ? 'merge_target_options_timeout' : 'merge_target_options_unavailable',
    reason: status === 'timeout' ? null : 'probe_failed', current: entry(raw.default_target_branch, unchecked), default: entry(raw.default_target_branch, unchecked) }
}
const notFound = () => ({ isAxiosError: true, response: { status: 404, data: { detail: 'not found' } } })

beforeEach(() => {
  Object.values(http).forEach(m => m.mockReset()); i18n.global.locale.value = 'ko'
  gateId = `gn_gate_0006_${++id}`
  otherGateId = `${gateId}_b`
  graph = { ...GRAPH_GRAPH, nodes: [GRAPH_TEST_NODE, { ...GRAPH_GATE_NODE, id: gateId }, { ...GRAPH_GATE_NODE, id: otherGateId, x: 520 }], edges: [] }
  raw = { card_kind: 'check', settings_version: 6, locked: false,
    fields: { archive: field(false), merge_mode: field('merge'), merge_target: field(null), merge_target_branch: field(null), push: field(false), keep_branch: field(false) },
    merge_target_options: [], default_target_branch: 'feature/v0.2.0', push_remote_check_required: false }
  lookupSucceeds()
  const run = { run_id: 'run0006', status: 'idle', node_runs: [{ node_key: gateId, status: 'open', attempt_id: 'attempt0006', gate: {
    can_decide: true, settings_locked: false, decision_in_progress: false, decision: null, job: null, recovery: null } }] }
  http.get.mockImplementation(async (url: string) => {
    // Only the gate card answers its settings; the other card's own reads are refused (its parts show their error).
    if (url.endsWith(`/nodes/${encodeURIComponent(gateId)}/settings`)) return { data: structuredClone(raw) }
    if (url.endsWith('/runs/current')) return { data: { run: structuredClone(run) } }
    throw notFound()
  })
  http.patch.mockImplementation(async () => { throw notFound() })
  http.post.mockImplementation(async () => { throw notFound() })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })

const gateSelection = (): FlowGraphSelection => selectionOf(graph, gateId)
const otherSelection = (): FlowGraphSelection => selectionOf(graph, GRAPH_TEST_NODE.id)
const otherGateSelection = (): FlowGraphSelection => selectionOf(graph, otherGateId)
const kept = () => gateContextOf(targetOf(gateSelection()))!
const sections = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-section"]').map(section => section.attributes('data-key'))
const control = (w: VueWrapper<any>) => w.get('[data-test="check-settings-target"]')
const optionNames = (w: VueWrapper<any>) => w.findAll('[data-test="check-settings-target"] option').map(o => o.text())
async function openTab(w: VueWrapper<any>, key: 'settings' | 'documents' | 'qna' | 'attachments') {
  const label = i18n.global.t(`gui.designer.inspector.tabs.${key}`)
  await w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find(tab => tab.text().startsWith(label))!.trigger('click')
  await flushPromises()
  expect(w.get('[data-test="designer-inspector-body"]').attributes('data-tab')).toBe(key)
}
/** The Q&A / attachments tab of the card is shown and no part of the gate context is mounted. */
function expectNoGatePart(w: VueWrapper<any>) {
  expect(w.find('[data-test="check-settings-target"]').exists()).toBe(false)
  expect(w.find('[data-test="inspector-gate-card"]').exists()).toBe(false)
  expect(sections(w)).not.toContain('checkSettings')
  expect(sections(w)).not.toContain('gateCard')
}
/** Back on 설정 with a timed-out lookup and no kept list: no candidate is made up. */
async function expectNoListAfterTimeout(w: VueWrapper<any>) {
  lookupFails('timeout')
  await select(w, gateSelection())
  await openTab(w, 'settings')
  expect(kept().settings.value?.mergeTargetState?.status).toBe('timeout')
  expect(kept().cachedTargets()).toBeNull()
  expect(optionNames(w)).toEqual([])
  expect(control(w).attributes('disabled')).toBeDefined()
  expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('feature/v0.2.0')
  expect(w.get('[data-test="check-settings-refresh"]').attributes('disabled')).toBeUndefined()
  expect(http.patch).not.toHaveBeenCalled()
}
async function mountOnGate() {
  wrapper = mount(DesignerInspector, { props: { selection: gateSelection() }, global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
  await flushPromises()
  await openTab(wrapper, 'settings')
  expect(sections(wrapper)).toEqual(['checkSettings'])
  expect(optionNames(wrapper)).toEqual(LIST)
  expect(kept().targetCache.value?.options).toEqual(LIST)
  return wrapper
}
async function select(w: VueWrapper<any>, selection: FlowGraphSelection | null) { await w.setProps({ selection }); await flushPromises() }

describe('0006-T T-b in the real Inspector: the kept list follows the selection (review rej_01M6QXCBVZYSAGRV)', () => {
  it('check card -> a card of another kind (CheckSettings unmounted) -> check card: the list is gone and a failed lookup offers none', async () => {
    const w = await mountOnGate()
    await select(w, otherSelection())
    // The other kind of card has its own settings parts: CheckSettings is gone, and with it the card's selection.
    expect(w.find('[data-test="check-settings-target"]').exists()).toBe(false)
    expect(sections(w)).not.toContain('checkSettings')
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
    expect(kept().cachedTargets()).toBeNull()
    lookupFails('timeout')
    await select(w, gateSelection())
    expect(sections(w)).toEqual(['checkSettings'])
    expect(kept().settings.value?.mergeTargetState?.status).toBe('timeout')
    expect(kept().cachedTargets()).toBeNull()
    expect(optionNames(w)).toEqual([])
    expect(control(w).attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('feature/v0.2.0')
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(true)
    expect(w.get('[data-test="check-settings-refresh"]').attributes('disabled')).toBeUndefined()
    expect(http.patch).not.toHaveBeenCalled()
    // A read sent after the return keeps candidates again.
    lookupSucceeds(); await kept().reload(); await flushPromises()
    expect(kept().targetCache.value?.options).toEqual(LIST)
  })

  it('a GET sent on the check card and answered ok after moving to another kind of card keeps no list', async () => {
    const w = await mountOnGate()
    const answer = http.get.getMockImplementation()!
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    let armed = true
    http.get.mockImplementation(async (url: string, ...rest: unknown[]) => {
      if (!armed || !url.endsWith(`/nodes/${encodeURIComponent(gateId)}/settings`)) return answer(url, ...rest)
      armed = false
      const value = await answer(url, ...rest)
      await held
      return value
    })
    const late = kept().reload()
    await flushPromises()
    await select(w, otherSelection())
    expect(kept().targetCache.value).toBeNull()
    release(); await late; await flushPromises()
    expect(kept().settings.value?.mergeTargetState?.status).toBe('ok')
    expect(kept().targetCache.value).toBeNull()
    lookupFails('timeout')
    await select(w, gateSelection())
    expect(kept().cachedTargets()).toBeNull()
    expect(optionNames(w)).toEqual([])
    expect(control(w).attributes('disabled')).toBeDefined()
  })

  it('a tab change on the same check card keeps the card selected: the kept list stays usable after a failed lookup', async () => {
    const w = await mountOnGate()
    await openTab(w, 'documents')
    expect(w.find('[data-test="check-settings-target"]').exists()).toBe(false)
    // The documents part of the gate card shows the same card, so the card was not left.
    expect(kept().active()).toBe(true)
    expect(kept().targetCache.value?.options).toEqual(LIST)
    lookupFails('timeout')
    await openTab(w, 'settings')
    expect(kept().settings.value?.mergeTargetState?.status).toBe('timeout')
    expect(kept().cachedTargets()).toEqual(LIST)
    expect(optionNames(w)).toEqual(LIST)
    expect(control(w).attributes('disabled')).toBeUndefined()
  })

  it('nothing selected any more leaves the card as well', async () => {
    const w = await mountOnGate()
    await select(w, null)
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
  })

  it.each(['qna', 'attachments'] as const)('설정 -> %s -> 설정 on the same check card keeps the card selected: a failed lookup on the return still offers the kept list (rej_01M6R14HCES8QJJ5)', async tab => {
    const w = await mountOnGate()
    await openTab(w, tab)
    // No part of the gate context is mounted on this tab, and the card is still the Inspector's selection.
    expectNoGatePart(w)
    expect(kept().active()).toBe(true)
    expect(kept().targetCache.value?.options).toEqual(LIST)
    lookupFails('timeout')
    const reads = http.get.mock.calls.length
    await openTab(w, 'settings')
    // The return reads again; the lookup times out and the kept list stays the selectable one.
    expect(http.get.mock.calls.length).toBeGreaterThan(reads)
    expect(kept().settings.value?.mergeTargetState?.status).toBe('timeout')
    expect(kept().cachedTargets()).toEqual(LIST)
    expect(optionNames(w)).toEqual(LIST)
    expect(control(w).attributes('disabled')).toBeUndefined()
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(true)
    expect(w.get('[data-test="check-settings-refresh"]').attributes('disabled')).toBeUndefined()
    // A kept candidate is still chosen through the same PATCH with the shown version; the server judges it again.
    http.patch.mockImplementation(async (_url: string, body: any) => {
      raw.fields[body.field] = field(body.value, true)
      raw.settings_version++
      lookupFails('timeout')
      return { data: structuredClone(raw) }
    })
    await control(w).setValue('release/local'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(1)
    expect(http.patch).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { field: 'merge_target_branch', value: 'release/local', expected_settings_version: 6 })
    expect(kept().settings.value?.settingsVersion).toBe(7)
    expect(kept().cachedTargets()).toEqual(LIST)
    expect(optionNames(w)).toEqual(LIST)
    expect(control(w).attributes('disabled')).toBeUndefined()
  })

  it.each(['qna', 'attachments'] as const)('%s of the check card -> a card of another kind: the card is left there as well', async tab => {
    const w = await mountOnGate()
    await openTab(w, tab)
    expect(kept().active()).toBe(true)
    await select(w, otherSelection())
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
    expect(kept().cachedTargets()).toBeNull()
    await expectNoListAfterTimeout(w)
  })

  it('Q&A of the check card -> another check card: the first card is left', async () => {
    const w = await mountOnGate()
    await openTab(w, 'qna')
    await select(w, otherGateSelection())
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
    await expectNoListAfterTimeout(w)
  })

  it('attachments of the check card -> nothing selected, and Q&A of the check card -> the Inspector closed: the card is left', async () => {
    const w = await mountOnGate()
    await openTab(w, 'attachments')
    await select(w, null)
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
    await expectNoListAfterTimeout(w)
    w.unmount(); wrapper = null
    lookupSucceeds()
    const again = await mountOnGate()
    await openTab(again, 'qna')
    expect(kept().active()).toBe(true)
    again.unmount(); wrapper = null
    await flushPromises()
    expect(kept().active()).toBe(false)
    expect(kept().targetCache.value).toBeNull()
  })
})
