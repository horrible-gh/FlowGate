/**
 * flowgate.v02gui.0002 B (v02test.0024.0006-B B-1~B-3): a confirm card's own [승인] on the mounted Designer Shell.
 *
 * Before: the card's [승인] was tied to the removed general card approval (`runControl.canApprove` false since 0119 T-3,
 * `viewer.can_approve` no longer sent), so the mounted Shell never drew it — the mapper/card tests passed only because
 * they handed `canApprove: true` in by hand. Here only the HTTP layer (`@shared/api`) is mocked; the Canvas read, the run
 * read, the gate settings read and the decision go through the real server Graph source, run control and the
 * Inspector's own approveGateTarget.
 *
 * - waiting for its confirm (server `gate.can_decide`): the card shows [승인]; a click (twice) sends ONE gate-decision
 *   confirm with the Inspector's request key fields, and does not select the card;
 * - not decidable (pending, a decision in progress, done): no [승인], no request.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore } from '@/features/project'
import { answerFlowCanvas, flowCanvasResponse } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', async original => ({
  ...await original<typeof import('@shared/api')>(),
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
const DECIDE = '/api/v1/runs/run_g/nodes/gn_gate/gate-decision'

const graph = flowCanvasResponse(FLOW, 'graph')
graph.projection = 'graph'
graph.nodes[0] = {
  ...graph.nodes[0], node_key: 'gn_gate', identity_source: 'graph_node', origin: 'graph_revision',
  kind: 'approval', doc_type: null, title: '확인', document: null,
  graph_node: { node_type: 'gate', label: '확인', palette_item_id: 'gate', assignee: null, root_bound: false },
  node_run: { status: 'open', opened_at: null, finished_at: null, result_doc_id: null },
} as unknown as typeof graph.nodes[number]
graph.nodes = [graph.nodes[0]]
graph.edges = []

const gate = (extra: Record<string, unknown> = {}) => ({
  can_decide: true, decision_in_progress: false, settings_locked: false, archived: false,
  recovery_reset_required: false, decision: null, job: null, recovery: null, ...extra,
})
const runOf = (status: string, gateState: Record<string, unknown>) => ({
  run_id: 'run_g', flow_id: FLOW, revision_no: 1, status: 'idle', idle_reason: 'waiting_confirm',
  open_node_keys: status === 'open' ? ['gn_gate'] : [], last_event_seq: 3, pending_propagation_count: 0,
  pending_node_keys: [],
  node_runs: [{ node_key: 'gn_gate', status, attempt_id: 'att_1', node_running: false, gate: gateState }],
})
const field = (value: unknown) => ({ value, stored: value !== null, default: null, choices: null })
const SETTINGS = {
  ok: true, flow_id: FLOW, node_key: 'gn_gate', card_kind: 'check', locked: false, settings_version: 3,
  fields: { archive: field(false), merge_mode: field(null), merge_target: field(null), merge_target_branch: field(null),
    push: field(false), keep_branch: field(false) },
  merge_target_options: ['main'], default_target_branch: 'main',
}

let run: ReturnType<typeof runOf>
const decisions: unknown[] = []
const unexpected: string[] = []
let wrapper: VueWrapper | null = null

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  decisions.length = 0
  unexpected.length = 0
  run = runOf('open', gate())
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [
      { id: 'project:p', parent_id: null, node_type: 'project', label: 'Project' },
      { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' },
      { id: FLOW, parent_id: 'module:p:ui', node_type: 'group', label: 'Graph', number: '0001' },
    ] } } }
    if (url === `/api/v1/flows/${FLOW}/canvas`) return { data: graph }
    if (url === `/api/v1/flows/${FLOW}/runs/current`) return { data: { ok: true, run } }
    if (url === `/api/v1/flows/${FLOW}/nodes/gn_gate/settings`) return { data: SETTINGS }
    if (url.endsWith('/git/status')) return { data: { ok: true, status: { enabled: false, slots: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false } }
    const canvas = answerFlowCanvas(url)
    if (canvas) return canvas
    unexpected.push(url)
    throw Object.assign(new Error(`unexpected request ${url}`), { request: {} })
  })
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string, body: unknown) => {
    if (url === DECIDE) {
      decisions.push(body)
      await new Promise(resolve => setTimeout(resolve, 20))
      run = runOf('done', gate({ can_decide: false, settings_locked: true, decision: {
        decision_id: 'd1', attempt_id: 'att_1', result: 'confirmed', status: 'succeeded', archived: false,
        archive_id: null, actor_user_id: 'kim', completed_at: '2026-11-12T09:00:00Z', comment: null, effects: [] } }))
      return { data: { ok: true, run, decision: { decision_id: 'd1', status: 'succeeded', result: 'confirmed' }, job: null } }
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
})

async function settle() { for (let i = 0; i < 8; i += 1) await flushPromises() }
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
const card = () => wrapper!.get('[data-test="designer-canvas-node"][data-node-id="gn_gate"]')

describe('v02gui.0002 B: the confirm card approves on the mounted Shell', () => {
  it('shows [승인] while the server says can_decide and sends one confirm through the Inspector path', async () => {
    await openGraph()
    const button = card().find('[data-test="designer-canvas-node-approve"]')
    expect(button.exists(), `unexpected: ${unexpected.join(', ')}`).toBe(true)
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await button.trigger('click')
    await new Promise(resolve => setTimeout(resolve, 60))
    await settle()
    expect(decisions).toHaveLength(1)
    expect(decisions[0]).toMatchObject({ decision: 'confirm', expected_attempt_id: 'att_1', expected_settings_version: 3 })
    expect(typeof (decisions[0] as { request_id?: unknown }).request_id).toBe('string')
    expect(card().attributes('aria-selected') ?? card().classes().join(' ')).not.toMatch(/true|selected/)
    expect(card().find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
  })

  it.each([
    ['pending', 'pending', gate({ can_decide: false })],
    ['a decision in progress', 'open', gate({ can_decide: false, decision_in_progress: true })],
    ['open but not decidable', 'open', gate({ can_decide: false })],
  ])('offers no [승인] when %s', async (_name, status, gateState) => {
    run = runOf(status as string, gateState as Record<string, unknown>)
    await openGraph()
    expect(card().find('[data-test="designer-canvas-node-approve"]').exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })
})
