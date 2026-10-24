/** T#2: the mounted Designer Shell, Canvas and bottom Inspector share one Center selection. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore } from '@/features/project'
import { flowCanvasResponse } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))
registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const graph = flowCanvasResponse('p.ui.graph', 'graph')
graph.projection = 'graph'
graph.nodes[0] = {
  ...graph.nodes[0], node_key: 'gn_task', identity_source: 'graph_node', origin: 'graph_revision',
  kind: 'task', doc_type: 'TR', title: '구현 레포트',
  document: { doc_id: 'p.ui.graph.0001-TR', type_code: 'TR', seq: 1, title: '구현 레포트', doc_review_status: 'pending_review', status: 'open', supersedes: [] },
  graph_node: { node_type: 'document', label: '구현 레포트', palette_item_id: 'doc_tr', assignee: null, root_bound: false },
  node_run: { status: 'waiting_review', opened_at: null, finished_at: null, result_doc_id: 'p.ui.graph.0001-TR' },
} as typeof graph.nodes[number]
graph.nodes[1] = {
  ...graph.nodes[1], node_key: 'gn_gate', identity_source: 'graph_node', origin: 'graph_revision',
  kind: 'approval', doc_type: null, title: '확인', document: null,
  graph_node: { node_type: 'gate', label: '확인', palette_item_id: 'gate', assignee: null, root_bound: false },
} as typeof graph.nodes[number]
graph.edges = []

const legacy = flowCanvasResponse('p.ui.legacy', 'v01')
legacy.nodes[0] = {
  ...legacy.nodes[0], title: '요구사항',
  document: { doc_id: 'p.ui.legacy.0001-R', type_code: 'R', seq: 1, title: '요구사항', doc_review_status: 'approved', status: 'open', supersedes: [] },
} as typeof legacy.nodes[number]
legacy.nodes[1] = {
  ...legacy.nodes[1], kind: 'task', doc_type: 'T', title: '작업지시',
  document: { doc_id: 'p.ui.legacy.0002-T', type_code: 'T', seq: 2, title: '작업지시', doc_review_status: 'rejected', status: 'open', supersedes: [] },
} as typeof legacy.nodes[number]

let wrapper: VueWrapper | null = null
beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [
      { id: 'project:p', parent_id: null, node_type: 'project', label: 'Project' },
      { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' },
      { id: 'p.ui.graph', parent_id: 'module:p:ui', node_type: 'group', label: 'Graph', number: '0001' },
      { id: 'p.ui.legacy', parent_id: 'module:p:ui', node_type: 'group', label: 'Legacy', number: '0002' },
    ] } } }
    if (url === '/api/v1/flows/p.ui.graph/canvas') return { data: graph }
    if (url === '/api/v1/flows/p.ui.graph/runs/current') return { data: { ok: true, run: null } }
    if (url === '/api/v1/flows/p.ui.legacy/canvas') return { data: legacy }
    if (url.endsWith('/git/status')) return { data: { ok: true, status: { enabled: false, slots: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false } }
    // flowgate.v02.0053 T#7: the gate card's own settings (T#2), as kept for a card nothing was chosen on.
    if (url === '/api/v1/flows/p.ui.graph/nodes/gn_gate/settings') {
      return { data: { ok: true, flow_id: 'p.ui.graph', node_key: 'gn_gate', card_kind: 'check', locked: false, fields: {} } }
    }
    throw new Error(`unexpected request ${url}`)
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject({ id: 'p', name: 'Project', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] })
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetHeight
})

async function settle() { for (let i = 0; i < 6; i += 1) await flushPromises() }
function get(test: string) { return wrapper!.get(`[data-test="${test}"]`) }
async function doubleClick(label: string) {
  const rows = wrapper!.findAll('[data-explorer-row]')
  const row = rows.find((r) => r.find('[data-test="project-explorer-label"]').text() === label)?.find('.explorer-row')
  if (!row) throw new Error(`missing ${label}; rows: ${rows.map((r) => r.text()).join(' | ')}`)
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    row.element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}
async function openFlow(name: string) {
  await settle()
  if (!wrapper!.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === 'Graph')) await doubleClick('UI 개선')
  await doubleClick(name)
}
async function selectNode(id: string) {
  await wrapper!.get(`[data-test="designer-canvas-node"][data-node-id="${id}"]`).trigger('click')
  await settle()
}
async function tab(name: string) {
  const match = wrapper!.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((t) => t.text().startsWith(name))!
  await match.trigger('click')
  await settle()
}

describe('Designer Shell x Inspector (T#2)', () => {
  it('keeps Canvas, Inspector, MiniMap and both side docks together through Graph selection and clearing', async () => {
    await openFlow('Graph')
    expect(get('designer-inspector').attributes('data-selected')).toBe('false')
    await selectNode('gn_task')
    expect(get('designer-canvas').attributes('data-selected-node-id')).toBe('gn_task')
    expect(get('designer-inspector-title').text()).toBe('구현 레포트')
    expect(get('designer-inspector-doc-status').text()).toBe('검수 대기')
    // flowgate.v02.0052 묶음 A: a run status without a display name is kept as the raw value only
    // (data-value, tooltip) and never shown as text.
    expect(get('designer-inspector-run-status').attributes('data-value')).toBe('waiting_review')
    expect(get('designer-inspector-pill').text()).not.toContain('waiting_review')
    expect(wrapper!.findAll('[data-test="designer-inspector-tabs"] [role="tab"]')).toHaveLength(4)
    for (const area of ['designer-bottom-minimap', 'designer-left-dock', 'designer-right-dock', 'designer-workspace']) {
      expect(get(area).exists()).toBe(true)
    }
    await selectNode('gn_gate')
    expect(get('designer-inspector-title').text()).toBe('확인')
    expect(wrapper!.find('[data-test="designer-inspector-doc-status"]').exists()).toBe(false)
    await tab('첨부')
    // flowgate.v02.0053 T#6: a Node without a document shows an empty attachments area, no sentence.
    expect(get('designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    expect(get('designer-inspector-body').text()).toBe('')
    await wrapper!.get('.vue-flow__pane').trigger('click')
    await settle()
    expect(get('designer-inspector').attributes('data-selected')).toBe('false')
    expect(get('designer-canvas').attributes('data-selected-node-id')).toBe('')
  })

  it('follows a legacy document Node and preserves the tab on Node and Flow changes', async () => {
    await openFlow('Legacy')
    await selectNode('doc:p.ui.legacy.0002-N')
    expect(get('designer-inspector-title').text()).toBe('작업지시')
    expect(get('designer-inspector-doc-status').text()).toBe('반려됨')
    await tab('설정')
    await selectNode('doc:p.ui.legacy.0001-R')
    expect(get('designer-inspector-title').text()).toBe('요구사항')
    expect(get('designer-inspector-body').attributes('data-tab')).toBe('settings')
    await openFlow('Graph')
    await selectNode('gn_gate')
    expect(get('designer-inspector-body').attributes('data-tab')).toBe('settings')
    // D0007 §6: the gate settings show archive and Git reflection without a usage row.
    expect(get('inspector-check-settings').text()).toContain('아카이브')
    expect(get('inspector-check-settings').text()).not.toContain('용도')
    expect(get('inspector-check-settings').text()).toContain('Git 반영')
  })
})
