/**
 * flowgate.v02.0125 T#2 (rejection rej_01M6G972AQBVWPW8) — the exhausted-review `needs_intervention` card on the path the
 * user actually sees: the mounted Designer Shell with a Graph Flow whose card `gn_a` really is in the Canvas (ownership
 * `graph`), its run read from the server, the Canvas card chip, the Inspector pill, the top Run menu and the toolbar.
 * Only the HTTP layer (`@shared/api`) is mocked; the Canvas read, the run read / command and the run control are the real
 * ones. The mocked server keeps ONE run (`server.run`) that the Canvas read, the run read and the run command all answer.
 *
 * - the card's chip title (Canvas) and the Inspector pill title carry the ko / en / ja sentence, never the raw code, and
 *   keep it when the same card is selected, cleared, selected again and after F5 (a new Shell); nothing is started;
 * - the Run menu / toolbar `다음 단계` · `전체 실행` are enabled with that card, are the only run entries (no separate
 *   re-review control on the Canvas card, the Inspector header or the top), and one click is exactly one
 *   `POST /flows/{id}/runs` (step / full) — no stop, no resume — after which the run is locked and a repeat sends nothing;
 * - the run moving on (the server's next run read) updates the SAME card: done → no sentence; another exhaustion →
 *   the sentence again; a step → the reviewed card done, the card after it pending.
 */
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import { REQUESTED_REREAD_MS } from '@/features/designer/center/run/flowRunControl'
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

type Locale = 'ko' | 'en' | 'ja'
const MESSAGES = { ko, en, ja } as const
const LOCALES: readonly Locale[] = ['ko', 'en', 'ja']
const sentence = (locale: Locale): string => MESSAGES[locale].designer.center.card_intervention_reasons.review_count_exhausted
const REASON = 'review_not_passed:review_count_exhausted'

const FLOW = 'p.ui.graph'
const A = 'gn_a' // the reviewed card (its review attempts are used up)
const B = 'gn_b' // the card after it
const TR_A = `${FLOW}.0001-TR`
const RUNS_URL = `/api/v1/flows/${FLOW}/runs`

// --- the server's one run, as the Canvas read, the run read and the run command tell it ----------------------------
type Raw = Record<string, unknown>
let seq = 0
const nodeRun = (key: string, status: string, extra: Raw = {}): Raw => ({ node_key: key, status, result_doc_id: key === A ? TR_A : null, ...extra })
function runBody(status: string, node_runs: Raw[], extra: Raw = {}): Raw {
  seq += 1
  return {
    run_id: 'run_1', flow_id: FLOW, revision_no: 1, status, mode: 'full', idle_reason: null,
    running_execution_count: 0, requested_execution_count: 0, execution_limit: 4, open_node_keys: [],
    last_event_seq: seq, pending_propagation_count: 0, pending_node_keys: [], node_runs, ...extra,
  }
}
/** Idle run: the card's review attempts are used up, its last revision is not reviewed (Q #544). */
const exhaustedRun = () => runBody('idle', [nodeRun(A, 'review', { intervention: { kind: 'needs_intervention', reason: REASON } }), nodeRun(B, 'pending')])
/** The command is going: the card is written / reviewed again; a requested execution makes the run control read again. */
const runningRun = (mode: string) =>
  runBody('running', [nodeRun(A, 'open', { stage: 'result' }), nodeRun(B, 'pending')], { mode, open_node_keys: [A], running_execution_count: 1, requested_execution_count: 1 })
/** Full run finished: both cards done. */
const completedRun = () => runBody('idle', [nodeRun(A, 'done'), nodeRun(B, 'done')], { idle_reason: 'completed' })
/** Step finished: the reviewed card is done, the card after it waits for the next command. */
const stepDoneRun = () =>
  runBody('idle', [nodeRun(A, 'done'), nodeRun(B, 'pending')], { mode: 'step', idle_reason: 'step_done', pending_propagation_count: 1, pending_node_keys: [B] })

const server: { run: Raw | null; onStart: (mode: string) => Raw } = { run: null, onStart: () => exhaustedRun() }

const graph = flowCanvasResponse(FLOW, 'graph')
graph.projection = 'graph'
const card = (index: number, key: string, title: string, docId: string | null) => ({
  ...graph.nodes[index], node_key: key, identity_source: 'graph_node', origin: 'graph_revision',
  kind: 'task', doc_type: 'TR', title,
  document: docId ? { doc_id: docId, type_code: 'TR', seq: index + 1, title, doc_review_status: 'pending_review', status: 'open', supersedes: [] } : null,
  graph_node: { node_type: 'document', label: title, palette_item_id: 'doc_tr', assignee: null, root_bound: false },
}) as unknown as typeof graph.nodes[number]
graph.nodes = [card(0, A, '구현 레포트', TR_A), card(1, B, '후속 카드', null)]
graph.edges = []

/** The Canvas read of the server's run now: its `run` block and each card's `node_run` (no intervention: that is the run read's). */
function canvasNow() {
  const nodeRuns = (server.run?.node_runs ?? []) as Raw[]
  return {
    ...graph,
    run: server.run,
    nodes: graph.nodes.map((node) => {
      const found = nodeRuns.find((item) => item.node_key === node.node_key)
      return { ...node, node_run: found ? { status: found.status, opened_at: null, finished_at: null, result_doc_id: found.result_doc_id ?? null } : null }
    }),
  }
}

let wrapper: VueWrapper<any> | null = null
async function settle() { for (let i = 0; i < 6; i += 1) await flushPromises() }

function boot() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject({ id: 'p', name: 'Project', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] })
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  i18n.global.locale.value = 'ko'
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  server.run = exhaustedRun()
  server.onStart = () => exhaustedRun()
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [
      { id: 'project:p', parent_id: null, node_type: 'project', label: 'Project' },
      { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' },
      { id: FLOW, parent_id: 'module:p:ui', node_type: 'group', label: 'Graph', number: '0001' },
    ] } } }
    if (url === `/api/v1/flows/${FLOW}/canvas`) return { data: canvasNow() }
    if (url === `${RUNS_URL}/current`) return { data: { ok: true, run: server.run } }
    if (url.endsWith('/git/status')) return { data: { ok: true, status: { enabled: false, slots: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false } }
    throw new Error(`unexpected request ${url}`)
  })
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string, body: { mode?: string }) => {
    if (url === RUNS_URL) {
      server.run = server.onStart(body.mode ?? 'full')
      return { data: { ok: true, run: server.run } }
    }
    throw new Error(`unexpected request ${url}`)
  })
  boot()
})
afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetHeight
  document.body.innerHTML = ''
  i18n.global.locale.value = 'ko'
})

// --- screen helpers --------------------------------------------------------------------------------------------------
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
  if (!wrapper!.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === 'Graph')) await doubleClick('UI 개선')
  await doubleClick('Graph')
}
const nodeEl = (id: string) => wrapper!.get(`[data-test="designer-canvas-node"][data-node-id="${id}"]`)
const chip = (id: string) => nodeEl(id).get('[data-test="designer-canvas-node-card-state"]')
const pill = () => wrapper!.get('[data-test="designer-inspector-pill"]')
async function selectCard(id: string) {
  await nodeEl(id).trigger('click')
  await settle()
}
async function clearSelection() {
  await wrapper!.get('.vue-flow__pane').trigger('click')
  await settle()
  expect(wrapper!.get('[data-test="designer-inspector"]').attributes('data-selected')).toBe('false')
}

/**
 * The reviewed card `gn_a` as drawn, in every language: with `shown` its chip title (Canvas) and, when the card is
 * selected, the Inspector pill title (tooltip) carry the sentence and never the raw code; without it neither does.
 */
async function expectSentence(shown: boolean, { inspector = true }: { inspector?: boolean } = {}) {
  for (const locale of LOCALES) {
    i18n.global.locale.value = locale
    await settle()
    const label = MESSAGES[locale].designer.center.card_states.needs_intervention
    const cardTitle = chip(A).attributes('title') ?? ''
    const pillTitle = inspector ? pill().attributes('title') ?? '' : null
    if (shown) {
      expect(chip(A).attributes('data-state'), locale).toBe('needs_intervention')
      expect(cardTitle, locale).toBe(`${label} (${sentence(locale)})`)
      expect(cardTitle).not.toContain('review_count_exhausted')
      if (pillTitle !== null) {
        expect(pillTitle.endsWith(`${label} (${sentence(locale)})`), locale).toBe(true)
        expect(pillTitle).not.toContain('review_count_exhausted')
      }
    } else {
      expect(chip(A).attributes('data-state'), locale).not.toBe('needs_intervention')
      expect(cardTitle, locale).not.toContain(sentence(locale))
      expect(cardTitle).not.toContain('review_count_exhausted')
      if (pillTitle !== null) {
        expect(pillTitle, locale).not.toContain(sentence(locale))
        expect(pillTitle).not.toContain('review_count_exhausted')
      }
    }
  }
  i18n.global.locale.value = 'ko'
  await settle()
}

// --- menu / toolbar helpers ------------------------------------------------------------------------------------------
const menuTitle = (key: string) => wrapper!.get(`[data-test="designer-menu"][data-menu="${key}"]`)
const openPop = () => wrapper!.find('[data-test="designer-menu-pop"]')
async function openMenu(key: string) {
  if (openPop().exists() && openPop().attributes('data-menu') === key) return openPop()
  await menuTitle(key).trigger('click')
  return wrapper!.get(`[data-test="designer-menu-pop"][data-menu="${key}"]`)
}
async function closeMenu() {
  document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
  await flushPromises()
}
const enabledIn = (pop: DOMWrapper<Element>, commands: readonly string[]) =>
  commands.filter((command) => pop.get(`[data-command="${command}"]`).attributes('disabled') === undefined)
const RUN_ENTRIES = ['run.next', 'run.all', 'run.stop', 'run.rerun', 'run.validate']
const toolbarRunCommands = () => wrapper!.findAll('[data-test="designer-tool-run"]').map((button) => button.attributes('data-command'))
const topRunCommands = () =>
  wrapper!.get('[data-test="designer-top"]').findAll('[data-command]').map((el) => el.attributes('data-command') ?? '').filter((command) => command.startsWith('run.'))
const sentRuns = () => postRequest.mock.calls.map((call) => [call[0], call[1]])
const REVIEW_WORDS = /재검수|재시도|re-?review|retry|再レビュー|再試行/i
const reviewControls = (root: DOMWrapper<Element>) =>
  root.findAll('button, [role="button"]').filter((el) => REVIEW_WORDS.test(`${el.text()} ${el.attributes('aria-label') ?? ''} ${el.attributes('title') ?? ''}`))

async function command(via: 'menu' | 'toolbar', cmd: 'run.next' | 'run.all') {
  if (via === 'menu') {
    const pop = await openMenu('run')
    await pop.get(`[data-command="${cmd}"]`).trigger('click')
  } else {
    await wrapper!.get(`[data-test="designer-tool-run"][data-command="${cmd}"]`).trigger('click')
  }
  await settle()
}

describe('Designer Shell: the exhausted-review card as the user sees it (0125 T#2)', () => {
  it('Canvas chip and Inspector pill carry the sentence in ko / en / ja, through select -> clear -> select again and after F5; nothing is started', async () => {
    await openGraph()
    expect(nodeEl(A).exists()).toBe(true)
    await expectSentence(true, { inspector: false })   // the card alone, nothing selected yet
    await selectCard(A)
    await expectSentence(true)
    await clearSelection()
    await expectSentence(true, { inspector: false })   // cleared: the Canvas card still tells it
    await selectCard(A)                                // the same card, selected again
    await expectSentence(true)
    expect(postRequest).not.toHaveBeenCalled()

    // F5: a brand new Shell reads the Canvas and the run again — the same sentence, still no command.
    wrapper!.unmount()
    wrapper = null
    document.body.innerHTML = ''
    boot()
    await openGraph()
    await expectSentence(true, { inspector: false })
    await selectCard(A)
    await expectSentence(true)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('the menu and the toolbar offer 다음 단계 / 전체 실행 and no separate re-review control anywhere on screen', async () => {
    await openGraph()
    await selectCard(A)
    await expectSentence(true)
    // Run menu: only the run entries; with the intervention card both commands are enabled, 중지 / 재실행 are not.
    const pop = await openMenu('run')
    expect(pop.findAll('[data-command]').map((el) => el.attributes('data-command'))).toEqual(RUN_ENTRIES)
    expect(enabledIn(pop, ['run.next', 'run.all', 'run.stop', 'run.rerun'])).toEqual(['run.next', 'run.all'])
    await closeMenu()
    // Toolbar and the whole top band: the same two run commands, nothing else about running or re-reviewing.
    expect(toolbarRunCommands()).toEqual(['run.next', 'run.all'])
    expect(topRunCommands()).toEqual(['run.next', 'run.all'])
    // No re-review / retry button on the Canvas card, in the Inspector header row, or in the top band.
    expect(reviewControls(new DOMWrapper(nodeEl(A).element))).toEqual([])
    expect(reviewControls(new DOMWrapper(pill().element.parentElement!))).toEqual([])
    expect(reviewControls(wrapper!.get('[data-test="designer-top"]'))).toEqual([])
    expect(nodeEl(A).findAll('button')).toHaveLength(0)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it.each([
    ['menu', 'run.next', 'step'],
    ['menu', 'run.all', 'full'],
    ['toolbar', 'run.next', 'step'],
    ['toolbar', 'run.all', 'full'],
  ] as const)('%s %s: the Shell sends exactly one run command (%s), locks, and a repeat sends nothing', async (via, cmd, mode) => {
    await openGraph()
    await selectCard(A)
    await expectSentence(true)
    server.onStart = runningRun
    await command(via, cmd)

    // One request: POST /flows/{id}/runs with the step / full mode — no stop, no resume, nothing else.
    expect(sentRuns()).toEqual([[RUNS_URL, { revision_no: null, mode }]])
    // The run is locked: the toolbar shows 일시정지 / 중지, the Run menu's 다음 단계 / 전체 실행 are disabled.
    expect(toolbarRunCommands()).toEqual(['run.pause', 'run.stop'])
    const pop = await openMenu('run')
    expect(enabledIn(pop, ['run.next', 'run.all'])).toEqual([])
    // The same card is being written / reviewed again: its chip and pill no longer show the exhausted sentence.
    await expectSentence(false)
    // A repeat of the command (the disabled menu entry) sends nothing.
    await pop.get(`[data-command="${cmd}"]`).trigger('click')
    await settle()
    await closeMenu()
    expect(sentRuns()).toEqual([[RUNS_URL, { revision_no: null, mode }]])
  })

  it('전체 실행 then the run moves on: done clears the sentence on the same card; the next command that ends exhausted shows it again', async () => {
    await openGraph()
    await selectCard(A)
    await expectSentence(true)

    // 전체 실행: running -> (the server's next run read) completed. The run control re-reads while an execution is requested.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    server.onStart = runningRun
    await command('menu', 'run.all')
    expect(chip(A).attributes('data-state')).toBe('writing_result')
    server.run = completedRun()
    await vi.advanceTimersByTimeAsync(REQUESTED_REREAD_MS)
    await settle()
    expect(chip(A).attributes('data-state')).toBe('approved')
    await expectSentence(false)
    expect(toolbarRunCommands()).toEqual(['run.next', 'run.all'])
    expect(enabledIn(await openMenu('run'), ['run.next', 'run.all'])).toEqual(['run.next', 'run.all'])
    await closeMenu()

    // 다음 단계 again: this time the review is exhausted again -> the sentence is back on the same card, the menu open again.
    server.onStart = runningRun
    await command('menu', 'run.next')
    expect(chip(A).attributes('data-state')).toBe('writing_result')
    server.run = exhaustedRun()
    await vi.advanceTimersByTimeAsync(REQUESTED_REREAD_MS)
    await settle()
    await expectSentence(true)
    expect(toolbarRunCommands()).toEqual(['run.next', 'run.all'])
    expect(enabledIn(await openMenu('run'), ['run.next', 'run.all'])).toEqual(['run.next', 'run.all'])
    await closeMenu()

    expect(sentRuns()).toEqual([
      [RUNS_URL, { revision_no: null, mode: 'full' }],
      [RUNS_URL, { revision_no: null, mode: 'step' }],
    ])
  })

  it('다음 단계 ends the step: the reviewed card is done (no sentence), the card after it is pending, the menu is open again', async () => {
    await openGraph()
    await selectCard(A)
    await expectSentence(true)

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    server.onStart = runningRun
    await command('menu', 'run.next')
    expect(sentRuns()).toEqual([[RUNS_URL, { revision_no: null, mode: 'step' }]])
    server.run = stepDoneRun()
    await vi.advanceTimersByTimeAsync(REQUESTED_REREAD_MS)
    await settle()

    expect(chip(A).attributes('data-state')).toBe('approved')
    await expectSentence(false)
    // The card after it waits for the next command (pending propagation of this run read).
    expect(chip(B).attributes('data-state')).toBe('pending')
    expect(toolbarRunCommands()).toEqual(['run.next', 'run.all'])
    expect(enabledIn(await openMenu('run'), ['run.next', 'run.all'])).toEqual(['run.next', 'run.all'])
    await closeMenu()
    expect(sentRuns()).toHaveLength(1)
  })
})
