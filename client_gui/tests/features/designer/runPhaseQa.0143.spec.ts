/**
 * flowgate.v02.0143 T#1 (NR flowgate.v02.0143.0003 §4.2, T §2 W1~W2, §3 R8~R10) — the run commands while an automatic Q&A
 * responder answers the open Flow's idle run:
 *
 * - the server's `qa_responding` reaches the run the header reads (the run read `toFlowRun`, the Canvas read's run block
 *   and the Center session's choice between them), absent when an older server does not send it;
 * - `runCommandHold`: only an idle run the server says a responder answers holds 다음 단계 / 전체 실행 (`runHold`); the
 *   phase stays the server's, so 일시정지 / 중지 are never offered for it; an idle reason or a card's question mark alone
 *   never holds (a person's wait stays open); the next run read without it releases at once;
 * - the real Shell (HTTP mocked only, as DesignerTop.0038): the Toolbar's and the Run menu's 다음 단계 / 전체 실행.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { DesignerShell } from '@/features/designer'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import {
  ALL_MENU_TARGETS,
  isMenuCommandEnabled,
  runBandTone,
  runBandView,
  runCommandHold,
  runPhaseOf,
  type DesignerMenuTargets,
} from '@/features/designer/top/designerTop'
import { useProjectStore, type Project } from '@/features/project'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { toFlowRun } from '@/features/project/flowRunApi'
import { createPhaseRunServer, type PhaseRunServer } from './flowRunPhaseServer'
import { flowCanvasResponse } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const IDLE_WAITS = ['needs_intervention', 'waiting_confirm', 'waiting_approval'] as const
const held = (run: Parameters<typeof runCommandHold>[0]): DesignerMenuTargets => ({ ...ALL_MENU_TARGETS, runHold: runCommandHold(run) })

describe('W1 — the server\'s qa_responding reaches the run the header reads', () => {
  const block = (extra: Record<string, unknown> = {}) => ({
    run_id: 'run_1', flow_id: 'f', revision_no: 1, status: 'idle', idle_reason: 'needs_intervention', mode: 'full',
    open_node_keys: ['gn_1'], last_event_seq: 17, ...extra,
  })

  it('the run read keeps it only when sent', () => {
    expect(toFlowRun(block({ qa_responding: true, qa_responding_node_keys: ['gn_1'] }))?.qaResponding).toBe(true)
    expect(toFlowRun(block({ qa_responding: false }))?.qaResponding).toBe(false)
    const older = toFlowRun(block())!
    expect('qaResponding' in older).toBe(false)                              // an older server: not told
    expect(toFlowRun(block({ qa_responding: 'yes' }))?.qaResponding).toBeUndefined()
  })

  it('the Canvas read\'s run block keeps it only when sent, through to the Graph run', () => {
    const canvas = toFlowCanvas({ flow_id: 'f', run: block({ qa_responding: true }) })
    expect(canvas.run?.qaResponding).toBe(true)
    expect(toFlowGraph(canvas).run?.qaResponding).toBe(true)
    const older = toFlowGraph(toFlowCanvas({ flow_id: 'f', run: block() })).run!
    expect('qaResponding' in older).toBe(false)
  })
})

describe('W2 — runCommandHold and the run commands (R8 / R9 / R10)', () => {
  it('R8: idle + qaResponding holds 다음 단계 / 전체 실행 only; no 일시정지 / 중지 is offered; the phase stays idle', () => {
    for (const reason of IDLE_WAITS) {
      const run = { status: 'idle', idleReason: reason, qaResponding: true }
      expect(runCommandHold(run)).toBe(true)
      expect(runPhaseOf(run.status)).toBe('idle')
      const targets = held(run)
      expect(isMenuCommandEnabled('run.next', 'idle', targets)).toBe(false)
      expect(isMenuCommandEnabled('run.all', 'idle', targets)).toBe(false)
      for (const command of ['run.pause', 'run.resume', 'run.stop', 'run.rerun'] as const) {
        expect(isMenuCommandEnabled(command, 'idle', targets)).toBe(false)     // not faked into running
      }
      expect(isMenuCommandEnabled('run.validate', 'idle', targets)).toBe(true) // Flow 검증 starts nothing
      expect(isMenuCommandEnabled('view.fit', 'idle', targets)).toBe(true)
      // The band's own facts and colour are unchanged (the server's status and idle reason only).
      const band = runBandView({ status: 'idle', idleReason: reason })!
      expect(band.phase).toBe('idle')
      expect(runBandTone(band)).toBe(reason === 'waiting_confirm' ? 'neutral' : 'warning')
    }
  })

  it('R9: a person\'s wait with a question mark and no responder (false / not told) is never held', () => {
    for (const reason of IDLE_WAITS) {
      for (const qaResponding of [false, undefined]) {
        const run = { status: 'idle', idleReason: reason, qaResponding, nodeRuns: [{ nodeKey: 'gn_1', status: 'open', question: true }] }
        expect(runCommandHold(run)).toBe(false)
        expect(isMenuCommandEnabled('run.next', 'idle', held(run))).toBe(true)
        expect(isMenuCommandEnabled('run.all', 'idle', held(run))).toBe(true)
      }
    }
    expect(runCommandHold(null)).toBe(false)
    expect(runCommandHold(undefined)).toBe(false)
  })

  it('R8: a running / paused / stopping run is never held by it (the phase decides as before)', () => {
    for (const status of ['running', 'paused', 'stopping']) {
      expect(runCommandHold({ status, qaResponding: true })).toBe(false)
    }
  })

  it('R10: the run read after the responder ended, failed or handed its question to a person releases at once', () => {
    const answering = { status: 'idle', idleReason: 'needs_intervention', qaResponding: true }
    expect(isMenuCommandEnabled('run.all', 'idle', held(answering))).toBe(false)
    for (const after of [
      { status: 'idle', idleReason: 'needs_intervention', qaResponding: false },   // failed / user_decision: a person's
      { status: 'running', idleReason: null, qaResponding: false },                // answered: the card goes on
      { status: 'idle', idleReason: 'completed', qaResponding: false },
    ]) {
      expect(runCommandHold(after)).toBe(false)
      expect(isMenuCommandEnabled(after.status === 'idle' ? 'run.all' : 'run.pause', runPhaseOf(after.status), held(after))).toBe(true)
    }
  })
})

describe('W1 / W2 — the Center session follows the newer read (R10)', () => {
  const F = 'p.ui.0143'
  const canvasBlock = (seq: number, extra: Record<string, unknown> = {}) => ({
    run_id: 'run_1', status: 'idle', revision_no: 1, open_node_keys: [], last_event_seq: seq, mode: 'full',
    idle_reason: 'needs_intervention', running_execution_count: 0, requested_execution_count: 0, execution_limit: 4,
    pending_propagation_count: 0, pending_node_keys: [], ...extra,
  })
  const runRead = (seq: number, fields: Partial<FlowRunInfo> = {}): FlowRunInfo => ({
    runId: 'run_1', status: 'idle', openNodeKeys: [], mode: 'full', idleReason: 'needs_intervention', runningExecutionCount: 0,
    requestedExecutionCount: 0, executionLimit: 4, pendingPropagationCount: 0, pendingNodeKeys: [], revisionNo: 1,
    lastEventSeq: seq, ...fields,
  })

  interface QaSource extends FlowGraphSource {
    block: ReturnType<typeof canvasBlock>
    runReads: Array<(run: FlowRunInfo | null) => void>
  }

  function qaSource(block: ReturnType<typeof canvasBlock>): QaSource {
    const source: QaSource = {
      isSample: false,
      block,
      runReads: [],
      loadGraph: async () => ({
        ...toFlowGraph(toFlowCanvas({ flow_id: F, run: source.block })),
        ownership: 'graph', viewMode: 'revision', draft: null, edges: [],
        nodes: [{ id: 'gn_1', kind: 'task', title: 't', x: 0, y: 0, movable: true, structureEditable: false, nodeRun: null }],
      }) as FlowGraph,
      loadRun: () => new Promise<FlowRunInfo | null>((resolve) => source.runReads.push(resolve)),
      startRun: async () => runRead(1, { status: 'running' }),
      stopRun: async (_p, runId) => ({ runId, status: 'stopping', openNodeKeys: [] }),
      resumeRun: async (_p, runId) => ({ runId, status: 'running', openNodeKeys: [] }),
    }
    return source
  }

  let center: DesignerCenterSession | null = null
  afterEach(() => {
    center?.dispose()
    center = null
  })
  const settle = async () => {
    for (let i = 0; i < 4; i += 1) await flushPromises()
  }
  const hold = (session: DesignerCenterSession) => runCommandHold(session.activeRunStatus.value.run)

  it('held while the server says so; the next read without it (Canvas first, then the run read) releases', async () => {
    const source = qaSource(canvasBlock(17, { qa_responding: true }))
    center = createDesignerCenterSession({ source })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: F, flowName: 'F' })
    await settle()
    expect(hold(center)).toBe(true)                                          // the Canvas read alone
    source.runReads[source.runReads.length - 1](runRead(17, { qaResponding: true }))
    await settle()
    expect(center.activeRunStatus.value.run?.qaResponding).toBe(true)
    expect(hold(center)).toBe(true)

    // The responder failed and the question went to a person: the next Canvas read (newer) says false at once.
    source.block = canvasBlock(18, { qa_responding: false })
    center.refreshFlow(F)
    await settle()
    expect(hold(center)).toBe(false)
    source.runReads[source.runReads.length - 1](runRead(18, { qaResponding: false }))
    await settle()
    expect(hold(center)).toBe(false)
  })

  it('a newer Canvas read of an older server (no qa_responding) does not keep an older run read\'s hold', async () => {
    const source = qaSource(canvasBlock(17, { qa_responding: true }))
    center = createDesignerCenterSession({ source })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: F, flowName: 'F' })
    await settle()
    source.runReads[source.runReads.length - 1](runRead(17, { qaResponding: true }))
    await settle()
    source.block = canvasBlock(19)
    center.refreshFlow(F)
    await settle()
    expect(hold(center)).toBe(false)
  })
})

// --- the real Shell (as DesignerTop.0038): HTTP mocked only -------------------------------------------------------------

const TREE = [
  { id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', type_code: null, number: '0001', filename: null, has_md: false, md_path: null },
]

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
})

let wrapper: VueWrapper<any> | null = null
let runServer: PhaseRunServer = createPhaseRunServer()

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

describe('the real Shell — the Toolbar and the Run menu (R8 / R9)', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    i18n.global.locale.value = 'ko'
    setCurrentUser({ username: 'test', email: null, isAdmin: false })
    runServer = createPhaseRunServer()
    postRequest.mockReset()
    postRequest.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      const answer = runServer.post(url, body)
      if (answer) return answer
      throw new Error(`unexpected request ${url}`)
    })
    getRequest.mockReset()
    getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
      const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
      if (canvas) {
        const id = decodeURIComponent(canvas[1])
        return { data: { ...flowCanvasResponse(id, 'graph'), run: runServer.runs.get(id) ?? null } }
      }
      if (url === '/api/v1/me/ai-roles') return { data: { setup_completed: true, selectable_count: 0, roles: {} } }
      if (url.endsWith('/archive')) return { data: { archived: false, eligible: false, reason: 'run_not_completed' } }
      const run = runServer.get(url)
      if (run) return run
      if (/^\/api\/v1\/projects\/[^/]+\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
      if (/^\/api\/v1\/projects\/[^/]+\/git\/status$/.test(url)) {
        return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
      }
      if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
      throw new Error(`unexpected request ${url}`)
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    setCurrentUser(null)
  })

  async function settle() {
    for (let i = 0; i < 6; i += 1) await flushPromises()
  }

  async function doubleClickRow(w: VueWrapper<any>, label: string) {
    const row = w
      .findAll('[data-explorer-row]')
      .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)!
      .find('.explorer-row')
    for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
      row.element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
      await nextTick()
    }
    await settle()
  }

  /** The Shell with the Flow open on an idle run the server tells as it is given. */
  async function mountShell(run: Record<string, unknown>) {
    runServer.runs.set('p.ui.0001', {
      run_id: 'run_1', flow_id: 'p.ui.0001', revision_no: 1, status: 'idle', mode: 'full', open_node_keys: ['gn_1'],
      last_event_seq: 17, idle_reason: 'needs_intervention', ...run,
    } as never)
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()
    await doubleClickRow(wrapper, 'UI 개선')
    await doubleClickRow(wrapper, 'Canvas')
    return wrapper
  }

  const runButtons = (w: VueWrapper<any>) =>
    w.findAll('[data-test="designer-tool-run"]').map((b) => [b.attributes('data-command'), b.attributes('disabled') === undefined])

  async function runMenu(w: VueWrapper<any>) {
    await w.get('[data-test="designer-menu"][data-menu="run"]').trigger('click')
    const pop = w.get('[data-test="designer-menu-pop"][data-menu="run"]')
    return Object.fromEntries(pop.findAll('[data-command]').map((item) => [item.attributes('data-command'), item.attributes('disabled') === undefined]))
  }

  it('R8: while the server says a responder answers, 다음 단계 / 전체 실행 wait and no 일시정지 / 중지 appears; a click sends nothing', async () => {
    const w = await mountShell({ qa_responding: true, qa_responding_node_keys: ['gn_1'] })
    expect(runButtons(w)).toEqual([['run.next', false], ['run.all', false]])
    const menu = await runMenu(w)
    expect(menu['run.next']).toBe(false)
    expect(menu['run.all']).toBe(false)
    expect(menu['run.stop']).toBe(false)
    const before = runServer.calls.length
    await w.get('[data-test="designer-tool-run"][data-command="run.all"]').trigger('click')
    await settle()
    expect(runServer.calls.slice(before).filter((call) => call.startsWith('POST'))).toEqual([])
  })

  it('R9: a person\'s wait with no responder (false, or not told by an older server) keeps 다음 단계 / 전체 실행', async () => {
    for (const run of [{ qa_responding: false }, {}]) {
      sessionStorage.clear()                                                 // a fresh browser tab: no F5 restore
      const w = await mountShell(run)
      expect(runButtons(w)).toEqual([['run.next', true], ['run.all', true]])
      wrapper?.unmount()
      wrapper = null
    }
  })
})
