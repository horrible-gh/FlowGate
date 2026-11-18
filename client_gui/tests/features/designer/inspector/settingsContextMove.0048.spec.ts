/**
 * flowgate.v02test.0048 — a card placed in another tab moves the draft version of the Flow this tab shows; the
 * selected card's settings context moves with it. The AI tab keeps its rows (지시 AI · 실행 AI · 검수 AI · 검수 횟수)
 * drawn while the card is read again under the new context, and a change made meanwhile is sent with the context
 * the new read answers (not refused as gate_context_changed). Another card still starts from nothing.
 * Component tests on a settings transport fixture; not browser evidence.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, patchRequest: http.patch }))
import AiTab from '@/features/designer/inspector/features/ai/AiTab.vue'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n } from './support/inspectorSupport'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'

const node: FlowGraphNode = { id: 'gn_a', kind: 'task', title: 'A', docType: 'T', x: 0, y: 0,
  definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: 'A', assignee: null, rootBound: false } }
const graph = (version: number, nodes: FlowGraphNode[] = [node]): FlowGraph => ({ flowId: 'f', nodes, edges: [],
  ownership: 'graph', viewMode: 'draft', draft: { version } } as FlowGraph)
const target = (version: number, selected = node) => toInspectorTarget({ projectId: 'p', graph: graph(version), node: selected })!
const contextOf = (version: number) => ({ run_id: null, graph_revision_no: null, draft_version: version })
const i18n = prepareInspectorI18n()
const field = (value: unknown, choices: unknown[] | null = null) => ({ value, choices, stored: true, default: null })

let raw: any
let held: Array<() => void>
let holdReads: boolean
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  vi.clearAllMocks()
  held = []
  holdReads = false
  raw = { card_kind: 'run', settings_version: 4, context: contextOf(7), locked: false, node_running: false,
    settings_capability: { editable: true, reason: null },
    fields: { instruction: field('use', ['use', 'omit']), review_count: field(1, [0, 1, 2, 3]) },
    ai_roles: { options_state: 'ok', options: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], roles: Object.fromEntries(
      ['instruction', 'execution', 'review'].map(role => [role, { selected: { kind: 'assigned', provider_id: 'a' }, state: 'assigned_ok', outcome: 'ai', provider_id: 'a', display_name: 'A' }])) } }
  http.get.mockImplementation((url: string) => {
    if (url.endsWith('/settings')) {
      if (!holdReads) return Promise.resolve({ data: structuredClone(raw) })
      return new Promise(resolve => held.push(() => resolve({ data: structuredClone(raw) })))
    }
    if (url.endsWith('/providers')) return Promise.resolve({ data: { providers: [] } })
    return Promise.reject(new Error('fixture: no document operation'))
  })
  // The server's own check (card_settings/context.py require_current): the context sent back must be the current one.
  http.patch.mockImplementation(async (_url: string, body: any) => {
    if (JSON.stringify(body.expected_context) !== JSON.stringify(raw.context)) {
      throw Object.assign(new Error('409'), { isAxiosError: true, response: { status: 409, data: { error: { code: 'gate_context_changed' } } } })
    }
    if (body.field.endsWith('_role')) raw.ai_roles.roles[body.field.replace('_role', '')].selected = body.value
    else raw.fields[body.field].value = body.value
    raw.settings_version++
    return { data: structuredClone(raw) }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })

const settingsReads = () => http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings'))
const rows = (w: VueWrapper<any>) => ({
  roles: ['instruction', 'execution', 'review'].filter(role => w.find(`[data-role="${role}"] select`).exists()),
  count: w.find('[data-test="inspector-run-settings-review_count"]').exists(),
})
const ALL = { roles: ['instruction', 'execution', 'review'], count: true }

async function open(selected = target(7)) {
  wrapper = mount(AiTab, { props: { target: selected }, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

/** Another tab placed a card: the server's draft version is up and this tab's Canvas read shows it. */
function placedElsewhere(version: number) {
  raw.context = contextOf(version)
  holdReads = true
}

describe('AI tab — the settings context moved by another tab (0048)', () => {
  it('keeps the AI rows and their values drawn while the card is read again under the new context', async () => {
    const w = await open()
    expect(rows(w)).toEqual(ALL)
    await w.get('[data-role="execution"] select').setValue('assigned:b'); await flushPromises()

    placedElsewhere(8)
    await w.setProps({ target: target(8) }); await flushPromises()
    expect(settingsReads().at(-1)).toEqual(['/api/v1/flows/f/nodes/gn_a/settings', { context: JSON.stringify(contextOf(8)) }])
    expect(held).toHaveLength(1)
    expect(rows(w)).toEqual(ALL)                                   // was: every AI row gone until the read came back
    expect((w.get('[data-role="execution"] select').element as HTMLSelectElement).value).toBe('assigned:b')
    expect((w.get('[data-test="inspector-run-settings-review_count"]').element as HTMLSelectElement).value).toBe('1')

    held.shift()!(); await flushPromises()
    expect(rows(w)).toEqual(ALL)
    expect((w.get('[data-role="execution"] select').element as HTMLSelectElement).value).toBe('assigned:b')
  })

  it('a change made while that read runs waits for it and is sent with the new context', async () => {
    const w = await open()
    placedElsewhere(8)
    await w.setProps({ target: target(8) }); await flushPromises()
    await w.get('[data-test="inspector-run-settings-review_count"]').setValue('3'); await flushPromises()
    expect(http.patch).not.toHaveBeenCalled()                      // not sent with the old draft version
    expect((w.get('[data-test="inspector-run-settings-review_count"]').element as HTMLSelectElement).value).toBe('3')

    held.shift()!(); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(1)
    expect(http.patch.mock.calls[0][1]).toMatchObject({ field: 'review_count', value: 3, expected_context: contextOf(8), expected_settings_version: 4 })
    expect(raw.fields.review_count.value).toBe(3)
    expect(w.find('[data-test="inspector-run-settings-review_count"]').exists()).toBe(true)
    expect((w.get('[data-test="inspector-run-settings-review_count"]').element as HTMLSelectElement).value).toBe('3')
    expect(w.html()).not.toContain('gate_context_changed')
  })

  it('a change already waiting for its answer when the context moves keeps its value and its answer is shown', async () => {
    const w = await open()
    let answer!: () => void
    http.patch.mockImplementationOnce((_url: string, body: any) => new Promise(resolve => { answer = () => {
      raw.ai_roles.roles.review.selected = body.value; raw.settings_version++; resolve({ data: structuredClone(raw) }) } }))
    await w.get('[data-role="review"] select').setValue('assigned:b'); await flushPromises()
    placedElsewhere(8)
    await w.setProps({ target: target(8) }); await flushPromises()
    expect((w.get('[data-role="review"] select').element as HTMLSelectElement).value).toBe('assigned:b')
    answer(); await flushPromises()
    held.shift()?.(); await flushPromises()
    expect(rows(w)).toEqual(ALL)
    expect((w.get('[data-role="review"] select').element as HTMLSelectElement).value).toBe('assigned:b')
  })

  it('another card still starts from nothing (its rows wait for its own answer)', async () => {
    const w = await open()
    holdReads = true
    await w.setProps({ target: target(7, { ...node, id: 'gn_b' }) }); await flushPromises()
    expect(settingsReads().at(-1)?.[0]).toBe('/api/v1/flows/f/nodes/gn_b/settings')
    expect(rows(w)).toEqual({ roles: [], count: false })
    held.shift()!(); await flushPromises()
    expect(rows(w)).toEqual(ALL)
  })
})
