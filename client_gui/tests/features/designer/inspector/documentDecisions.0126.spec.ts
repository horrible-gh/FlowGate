import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { h } from 'vue'
import InspectorSlotList, { type InspectorSlotView } from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import { prepareInspectorI18n } from './support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from './support/projectEventStream'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, postRequest: http.post }))
const context = { run_id: null, graph_revision_no: null, draft_version: 1 }
const own = (slot: string, status: string | null = null) => ({ slot, docType: slot === 'instruction' ? 'T' : 'TR', author: 'run', docId: `doc_${slot}`, title: slot, status: 'open', docReviewStatus: status })
const node: FlowGraphNode = { id: 'gn_a', kind: 'task', title: 'A', docType: 'T', x: 0, y: 0,
  definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: 'A', assignee: null, rootBound: false },
  cardDocuments: [own('instruction'), own('report')] }
const graph: FlowGraph = { flowId: 'f', nodes: [node], edges: [], ownership: 'graph', viewMode: 'draft',
  draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
const target = (key = 'gn_a') => toInspectorTarget({ projectId: 'p', graph, node: { ...node, id: key } })!
const lines: InspectorSlotView[] = ['instruction', 'report'].map(key => ({ key, name: key, id: `doc_${key}`, kind: 'document', open: 'enabled' }))
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
let stream: ProjectEventStream
let raw: any
const refresh = vi.fn()
const view = () => structuredClone(raw)
const buttons = () => wrapper!.findAll('[data-test="inspector-document-decision"]')
async function open(props: Record<string, unknown> = {}) {
  wrapper = mount(InspectorSlotList, { props: { slots: lines, target: target(), ...props },
    slots: { io: () => h('span', { 'data-test': 'io' }, [h('button', { 'data-test': 'upload' }), h('button', { 'data-test': 'download' }), h('span', { role: 'separator' })]) },
    global: { plugins: [i18n, createPinia()], provide: { [INSPECTOR_HOST as symbol]: { request: refresh } } } })
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.useFakeTimers()
  stream = installProjectEventStream()
  vi.clearAllMocks()
  raw = { flow_id: 'f', node_key: 'gn_a', context: { ...context, documents: { instruction: { sha: 'one' }, report: { sha: 'two' } } }, decision_version: 4,
    documents: Object.fromEntries(['instruction', 'report'].map(slot => [slot, { doc_id: `doc_${slot}`, revision_no: 3, doc_review_status: null }])),
    capabilities: Object.fromEntries(['instruction', 'report'].map(slot => [slot, { approve: { allowed: true }, reject: { allowed: true } }])) }
  http.get.mockImplementation(async () => ({ data: view() }))
  http.post.mockImplementation(async () => ({ data: view() }))
})
afterEach(() => { wrapper?.unmount(); wrapper = null; removeProjectEventStream(); vi.useRealTimers() })

describe('0126 document row decisions UI1/B10 (transport fixtures)', () => {
  it('one last icon per row, history/detail order, separators, accessible name and native keyboard button', async () => {
    await open()
    expect(buttons()).toHaveLength(2)
    for (const row of wrapper!.findAll('[data-test="inspector-slot"]')) {
      const order = row.findAll('button').map(b => b.attributes('data-test'))
      expect(order.slice(-3)).toEqual(['inspector-slot-history', 'inspector-slot-open', 'inspector-document-decision'])
      expect(row.findAll('[role="separator"]')).toHaveLength(2)
      const b = row.get('[data-test="inspector-document-decision"]')
      expect(b.element.tagName).toBe('BUTTON')
      expect(b.attributes('type')).toBe('button')
      expect(b.attributes('title')).toBe(b.attributes('aria-label'))
      expect(b.attributes('data-action')).toBe('approve')
    }
  })
  it.each(['approved', 'wf_done', 'pending_review', 'rejected', 'revised', null])('server state %s chooses exactly one action', async state => {
    raw.documents.instruction.doc_review_status = state
    await open()
    expect(buttons()[0].attributes('data-action')).toBe(['approved', 'wf_done'].includes(state ?? '') ? 'reject' : 'approve')
  })
  it('sends exact card/doc/revision/version/context and request key; pending blocks both rows and has no optimistic toggle', async () => {
    let done!: (v: unknown) => void
    http.post.mockImplementation(() => new Promise(resolve => { done = resolve }))
    await open()
    const captured = view()
    await buttons()[1].trigger('click')
    expect(http.post).toHaveBeenCalledTimes(1)
    const [url, body] = http.post.mock.calls[0]
    expect(url).toBe('/api/v1/flows/f/nodes/gn_a/documents/report/decision')
    expect(body).toEqual({ action: 'approve', doc_id: 'doc_report', expected_revision: 3, expected_decision_version: 4,
      expected_context: captured.context, request_key: expect.any(String) })
    expect(body.request_key).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/)
    expect(buttons().every(b => b.attributes('disabled') !== undefined)).toBe(true)
    expect(buttons()[1].attributes('data-action')).toBe('approve')
    await buttons()[0].trigger('click')
    expect(http.post).toHaveBeenCalledTimes(1)
    raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
    done({ data: captured }) // deliberately stale POST view; the follow-up GET is authoritative
    await flushPromises()
    expect(buttons().map(b => b.attributes('data-action'))).toEqual(['reject', 'reject'])
    expect(refresh).toHaveBeenCalledWith({ kind: 'refresh-flow', flowId: 'f' })
  })
  it('instruction rejection paints both rows from the committed cascade', async () => {
    raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
    http.post.mockImplementation(async () => {
      raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'rejected'
      return { data: view() }
    })
    await open()
    await buttons()[0].trigger('click'); await flushPromises()
    expect(http.post.mock.calls[0][1].action).toBe('reject')
    expect(buttons().map(b => b.attributes('data-action'))).toEqual(['approve', 'approve'])
    expect(wrapper!.findAll('[data-test="inspector-slot-state"]').every(b => b.attributes('data-tone') === 'danger')).toBe(true)
  })
  it.each([403, 409, 500])('surfaces server %s and reloads without pretending success', async status => {
    http.post.mockRejectedValue({ response: { status, data: { error: { message: 'decision refused' } } } })
    await open(); await buttons()[1].trigger('click'); await flushPromises()
    expect(wrapper!.get('[data-test="document-decision-failure"]').text()).toContain('decision refused')
    expect(http.get).toHaveBeenCalledTimes(2)
    expect(buttons()[1].attributes('data-action')).toBe('approve')
  })
  it.each([403, 409, 500])('keeps a pending POST %s error across same-card line/context refreshes', async status => {
    let reject!: (error: unknown) => void
    http.post.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
    await open(); await buttons()[1].trigger('click')
    // A Canvas/doc event updates the selected card before the POST's CAS refusal arrives.
    const updated = { ...target(), cardDocuments: [own('instruction', 'approved'), own('report', 'revised')] }
    raw.documents.instruction.doc_review_status = 'approved'
    raw.decision_version++
    raw.context.draft_version++
    await wrapper!.setProps({ target: { ...updated, settingsContext: { ...context, draft_version: raw.context.draft_version } } })
    await flushPromises()
    expect(buttons().every(b => b.attributes('disabled') !== undefined)).toBe(true)
    const reads = http.get.mock.calls.length
    reject({ response: { status, data: { error: { message: `same card refused ${status}` } } } })
    await flushPromises()
    expect(wrapper!.get('[data-test="document-decision-failure"]').text()).toContain(`same card refused ${status}`)
    expect(http.get).toHaveBeenCalledTimes(reads + 1)
    expect(buttons()[1].attributes('disabled')).toBeUndefined()
    expect(buttons()[1].attributes('data-action')).toBe('approve')
    await wrapper!.setProps({ target: { ...updated, settingsContext: { ...context, draft_version: raw.context.draft_version } } })
    await flushPromises()
    expect(wrapper!.get('[data-test="document-decision-failure"]').text()).toContain(`same card refused ${status}`)
  })
  it('a lost response still recovers the committed state by GET', async () => {
    http.post.mockImplementation(async () => {
      raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
      throw new Error('response lost after commit')
    })
    await open(); await buttons()[1].trigger('click'); await flushPromises()
    expect(buttons().map(b => b.attributes('data-action'))).toEqual(['reject', 'reject'])
    expect(wrapper!.find('[data-test="document-decision-failure"]').exists()).toBe(true)
  })
  it('capabilities fail closed for missing body/document, authority, execution and historical selection', async () => {
    raw.capabilities.report.approve = { allowed: false, reason: { code: 'document_decision_locked' } }
    await open()
    expect(buttons()[1].attributes('disabled')).toBeDefined()
    expect(buttons()[1].attributes('title')).toContain('document_decision_locked')
    await wrapper!.setProps({ slots: [{ ...lines[0], id: null, empty: true }, lines[1]], target: { ...target(), settingsContext: { ...context, graph_revision_no: 99 } } })
    await flushPromises()
    expect(buttons().every(b => b.attributes('disabled') !== undefined)).toBe(true)
    expect(http.post).not.toHaveBeenCalled()
  })
  it('keeps the same-card refusal when ownership changes while the POST is pending', async () => {
    let reject!: (error: unknown) => void
    http.post.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
    await open(); await buttons()[1].trigger('click')
    await wrapper!.setProps({ target: { ...target(), ownership: 'conflict' } }); await flushPromises()
    reject({ response: { status: 409, data: { error: { message: 'ownership changed' } } } })
    await flushPromises()
    expect(wrapper!.get('[data-test="document-decision-failure"]').text()).toContain('ownership changed')
    expect(buttons().every(b => b.attributes('disabled') !== undefined)).toBe(true)
  })
  it('late POST cannot paint or report an error onto another selected card', async () => {
    let reject!: (e: unknown) => void
    http.post.mockImplementation(() => new Promise((_r, fail) => { reject = fail }))
    await open(); await buttons()[1].trigger('click')
    raw.node_key = 'gn_b'
    await wrapper!.setProps({ target: target('gn_b') }); await flushPromises()
    reject({ response: { data: { error: { message: 'old card failure' } } } }); await flushPromises()
    expect(wrapper!.find('[data-test="document-decision-failure"]').exists()).toBe(false)
    expect(buttons()[1].attributes('data-action')).toBe('approve')
    expect(refresh).toHaveBeenCalledWith({ kind: 'refresh-flow', flowId: 'f' })
  })
  it('re-mount/re-selection reads durable data without requiring an SSE delivery', async () => {
    await open(); wrapper!.unmount(); wrapper = null
    raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
    await open()
    expect(buttons().map(b => b.attributes('data-action'))).toEqual(['reject', 'reject'])
    expect(http.post).not.toHaveBeenCalled()
  })
  it('reconnect after missing SSE reloads the card', async () => {
    await open()
    stream.open()
    raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
    stream.open()
    await vi.advanceTimersByTimeAsync(200); await flushPromises()
    expect(buttons().map(b => b.attributes('data-action'))).toEqual(['reject', 'reject'])
  })
})

it('unassigned Graph drafts can decide before the first run, while v01 stays on its existing UI', async () => {
  await open({ target: { ...target(), ownership: 'unassigned' } })
  expect(buttons().every(b => b.attributes('disabled') === undefined)).toBe(true)
  await wrapper!.setProps({ target: { ...target(), ownership: 'v01' } }); await flushPromises()
  expect(buttons()).toHaveLength(0)
})
it('a server-invalidated approval replaces an old approved Canvas badge', async () => {
  raw.documents.instruction.doc_review_status = 'pending_review'
  await open({ slots: [{ ...lines[0], state: { label: 'old approved', tone: 'success' } }, lines[1]],
    target: { ...target(), cardDocuments: [own('instruction', 'approved'), own('report')] } })
  expect(buttons()[0].attributes('data-action')).toBe('approve')
  expect(wrapper!.findAll('[data-test="inspector-slot-state"]')[0].attributes('data-tone')).toBe('warning')
})
it('document event refresh covers both rows and ignores a different Flow', async () => {
  await open(); stream.open()
  const reads = http.get.mock.calls.length
  stream.emit('doc_review_status_changed', { groupId: 'elsewhere', docId: 'doc_report' })
  await vi.advanceTimersByTimeAsync(200); await flushPromises()
  expect(http.get).toHaveBeenCalledTimes(reads)
  raw.documents.instruction.doc_review_status = raw.documents.report.doc_review_status = 'approved'
  stream.emit('doc_review_status_changed', { groupId: 'f', docId: 'doc_report' })
  await vi.advanceTimersByTimeAsync(200); await flushPromises()
  expect(buttons().map(b => b.attributes('data-action'))).toEqual(['reject', 'reject'])
})
it.each([['ko', '승인'], ['en', 'Approve'], ['ja', '承認']])('localizes the icon name in %s', async (locale, word) => {
  i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
  await open()
  expect(buttons()[0].attributes('aria-label')).toContain(word)
  i18n.global.locale.value = 'ko'
})
