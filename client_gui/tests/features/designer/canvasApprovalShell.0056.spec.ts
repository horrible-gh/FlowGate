/**
 * flowgate.v02.0056 F2-1 — a refused Canvas [승인] as the mounted Designer Shell shows it (O1 0005-TR §4-1). The
 * Canvas [승인] does not select the card, so the Inspector approval bar never shows this refusal: the Shell's toast
 * is where the user reads it. Only the HTTP layer (`@shared/api`) is mocked; the Canvas read, the run read and the
 * approval go through the real server Graph source, run transport (FlowGraphApiError) and run control.
 *
 * - 409 document_approval_refused with its params: the code's sentence, the server's message (x-locale), the
 *   refused document, why, and the documents approved before it — in ko / en / ja; that toast stays until closed;
 * - a busy refusal (no params) names nothing more and keeps the 4 s toast.
 */
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
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))
registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const FLOW = 'p.ui.graph'
const T = `${FLOW}.0001-T`
const TR = `${FLOW}.0002-TR`

const graph = flowCanvasResponse(FLOW, 'graph')
graph.projection = 'graph'
graph.nodes[0] = {
  ...graph.nodes[0], node_key: 'gn_task', identity_source: 'graph_node', origin: 'graph_revision',
  kind: 'task', doc_type: 'TR', title: '구현 레포트',
  document: { doc_id: TR, type_code: 'TR', seq: 2, title: '구현 레포트', doc_review_status: 'pending_review', status: 'open', supersedes: [] },
  graph_node: { node_type: 'document', label: '구현 레포트', palette_item_id: 'doc_tr', assignee: null, root_bound: false },
  node_run: { status: 'review', opened_at: null, finished_at: null, result_doc_id: TR, awaiting_approval: true, stage: 'result' },
} as unknown as typeof graph.nodes[number]
graph.nodes = [graph.nodes[0]]
graph.edges = []

const RUN = {
  run_id: 'run_s',
  flow_id: FLOW,
  revision_no: 1,
  status: 'stopped',
  open_node_keys: [],
  last_event_seq: 3,
  pending_propagation_count: 0,
  pending_node_keys: [],
  node_runs: [{ node_key: 'gn_task', status: 'review', result_doc_id: TR, awaiting_approval: true, stage: 'result' }],
}

/** The server's refusal of the approval, as axios rejects it. */
const refusal = (status: number, code: string, message: string, params: Record<string, unknown> = {}) =>
  Object.assign(new Error(message), { isAxiosError: true, response: { status, data: { ok: false, error: { code, message, params } } } })

/** The next approval answer (a refusal). */
let refused: unknown = null
const approvals: [string, unknown][] = []

let wrapper: VueWrapper | null = null
beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  approvals.length = 0
  refused = null
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [
      { id: 'project:p', parent_id: null, node_type: 'project', label: 'Project' },
      { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' },
      { id: FLOW, parent_id: 'module:p:ui', node_type: 'group', label: 'Graph', number: '0001' },
    ] } } }
    if (url === `/api/v1/flows/${FLOW}/canvas`) return { data: graph }
    if (url === `/api/v1/flows/${FLOW}/runs/current`) return { data: { ok: true, run: RUN } }
    if (url.endsWith('/git/status')) return { data: { ok: true, status: { enabled: false, slots: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false } }
    throw new Error(`unexpected request ${url}`)
  })
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string, body: unknown) => {
    if (url === '/api/v1/runs/run_s/nodes/gn_task/approval') {
      approvals.push([url, body])
      if (refused) throw refused
      return { data: { ok: true, run: RUN, approval: { node_key: 'gn_task', target_doc_ids: [T, TR], approved_doc_ids: [T, TR] } } }
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
  document.body.innerHTML = ''
  i18n.global.locale.value = 'ko'
})

async function settle() { for (let i = 0; i < 6; i += 1) await flushPromises() }
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
async function openGraph() {
  await settle()
  await doubleClick('UI 개선')
  await doubleClick('Graph')
}
describe('v0.2 Shell: general cards never ask for human approval', () => {
  it.each(['ko', 'en', 'ja'])('renders an accessible intervention icon without an approval POST (%s)', async locale => {
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
    await openGraph()
    const card = wrapper!.get('[data-test="designer-canvas-node"][data-node-id="gn_task"]')
    const state = card.get('[data-test="designer-canvas-node-card-state"]')
    expect(state.attributes('role')).toBe('img')
    expect(state.attributes('aria-label')).toBe(i18n.global.t('gui.designer.center.card_states.needs_intervention'))
    expect(state.text()).toBe('')
    expect(card.find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })
})
