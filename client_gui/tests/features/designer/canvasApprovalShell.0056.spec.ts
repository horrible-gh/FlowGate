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
async function approveOnCanvas() {
  const card = wrapper!.get('[data-test="designer-canvas-node"][data-node-id="gn_task"]')
  expect(card.get('[data-test="designer-canvas-node-card-state"]').text()).toBe(i18n.global.t('gui.designer.center.card_states.awaiting_approval'))
  await card.get('[data-test="designer-canvas-node-approve"]').trigger('click')
  await settle()
}
const toasts = () => [...document.body.querySelectorAll('.ui-toast')].map((toast) => ({
  text: toast.querySelector('.ui-toast-content')?.textContent?.trim() ?? '',
  danger: toast.classList.contains('ui-toast--danger'),
}))

describe('a refused Canvas [승인] in the Designer Shell (O1 4-1)', () => {
  it.each([
    ['ko', '결과 레포트의 TR2 검증을 통과하지 못했습니다', [
      '문서 승인이 거절되었습니다. — 결과 레포트의 TR2 검증을 통과하지 못했습니다',
      `거절된 문서: ${TR}`,
      '사유: tr2_validation_failed',
      `앞서 승인된 문서: ${T}`,
    ]],
    ['en', 'The result report did not pass the TR2 validation', [
      'A document approval was refused. — The result report did not pass the TR2 validation',
      `Refused document: ${TR}`,
      'Reason: tr2_validation_failed',
      `Approved before: ${T}`,
    ]],
    ['ja', '結果レポートが TR2 検証を通過しませんでした', [
      '文書の承認が拒否されました。 — 結果レポートが TR2 検証を通過しませんでした',
      `拒否された文書: ${TR}`,
      '理由: tr2_validation_failed',
      `先に承認された文書: ${T}`,
    ]],
  ])('document_approval_refused (%s): the toast names the refused document, why and the documents approved before it', async (locale, message, parts) => {
    i18n.global.locale.value = locale
    await openGraph()
    refused = refusal(409, 'document_approval_refused', message, { doc_id: TR, reason: 'tr2_validation_failed', approved_doc_ids: [T] })
    await approveOnCanvas()

    expect(approvals).toEqual([['/api/v1/runs/run_s/nodes/gn_task/approval', { result_doc_id: TR }]])
    // The click was the card's [승인], not a selection: the Inspector bar has nothing to show it in.
    expect(wrapper!.get('[data-test="designer-inspector"]').attributes('data-selected')).toBe('false')
    const shown = toasts()
    expect(shown).toHaveLength(1)
    expect(shown[0].text).toBe(parts.join(' · '))
    expect(shown[0].danger).toBe(true)
    expect(shown[0].text).not.toContain('document_approval_refused')
  })

  it('the refused document toast stays until it is closed', async () => {
    await openGraph()
    // Only the toast timers: flushPromises still runs on setImmediate.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      refused = refusal(409, 'document_approval_refused', '거절', { doc_id: TR, reason: 'transition_refused', approved_doc_ids: [] })
      await approveOnCanvas()
      expect(toasts()).toHaveLength(1)
      vi.advanceTimersByTime(10_000)
      await nextTick()
      expect(toasts().map((toast) => toast.text)).toEqual([`문서 승인이 거절되었습니다. — 거절 · 거절된 문서: ${TR} · 사유: transition_refused`])
      ;(document.body.querySelector('.ui-toast-close') as HTMLElement).click()
      await nextTick()
      expect(toasts()).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  it('a busy refusal names nothing more: its sentence and the server’s message, a warning that goes', async () => {
    await openGraph()
    refused = refusal(409, 'group_ai_running', '다른 AI 실행이 그룹을 쓰고 있습니다')
    await approveOnCanvas()
    const shown = toasts()
    expect(shown.map((toast) => toast.text)).toEqual(['이 그룹에서 다른 AI가 실행 중입니다. 끝난 뒤 다시 승인하세요. — 다른 AI 실행이 그룹을 쓰고 있습니다'])
    expect(shown[0].danger).toBe(false)
    expect(document.body.querySelector('.ui-toast--warning')).not.toBeNull()
  })
})
