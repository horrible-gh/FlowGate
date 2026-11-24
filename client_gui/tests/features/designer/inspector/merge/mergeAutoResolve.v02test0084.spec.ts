/**
 * flowgate.v02test.0084 (F84-3) — the merge window shows the server's automatic conflict resolution:
 * `auto_resolve` parsing, "충돌 자동 해결 중 (n/3)" with the AI in use, the exhausted box (unresolved files and parts, each try's
 * reason and AI) beside the unchanged manual buttons, the one-line states, and no extra screen / request when it is null.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MergeWindow from '@/features/designer/inspector/features/merge/MergeWindow.vue'
import { mergeWindows } from '@/features/designer/inspector/features/merge/mergeWindows'
import { mergeAutoCurrent, mergeAutoProgress, mergeAutoRunning, mergeHunkRange } from '@/features/designer/inspector/features/merge/mergeModel'
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
const FLOW = 'flowgate.v02test.0084'

function selection(run: Partial<FlowGraphNodeRun> | null): FlowGraphSelection {
  const node: FlowGraphNode = {
    id: 'gn_4', kind: 'task', title: '업로드 대기열 이전', state: 'in_progress', docType: 'T', x: 0, y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'doc_t', label: '업로드 대기열 이전', assignee: null, rootBound: false },
    document: null, slot: null,
    nodeRun: run ? { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, attemptId: 'att_4', ...run } : null,
  }
  const graph: FlowGraph = { flowId: FLOW, nodes: [node], edges: [], ownership: 'graph', draft: null, viewMode: 'revision' }
  return { projectId: 'flowgate', graph, node }
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
const attempt = (no: number, over: Record<string, unknown> = {}) => ({
  no, mode: 'ai', provider: { id: 'aip_x', name: 'Claude' }, result: 'running', reason: null,
  started_at: '2026-11-25T08:00:00+09:00', ended_at: null, ...over,
})
const exhausted = (over: Record<string, unknown> = {}) => ({
  state: 'exhausted', attempt: 3, max_attempts: 3,
  attempts: [
    attempt(1, { result: 'conflicts_left', reason: '충돌 표시가 남았습니다', ended_at: 'x' }),
    attempt(2, { mode: 'rebase_ai', result: 'check_failed', reason: 'test_upload_queue 실패', ended_at: 'x' }),
    attempt(3, { mode: 'switch_ai', provider: { id: 'aip_y', name: 'Codex' }, result: 'ai_error', reason: 'AI 실행 오류', ended_at: 'x' }),
  ],
  unresolved: [{ path: 'src/queue.ts', hunks: [{ start_line: 12, end_line: 18 }, { start_line: 40, end_line: 40 }] }, { path: 'src/other.ts', hunks: [] }],
  ...over,
})

const wrappers: VueWrapper<any>[] = []
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); mergeWindows.splice(0); document.body.innerHTML = '' })
beforeEach(() => {
  vi.clearAllMocks()
  i18n.global.locale.value = 'ko'
  api.choices.mockResolvedValue({ providers: [{ id: 'aip_x', name: 'Claude' }, { id: 'aip_y', name: 'Codex' }], defaultProviderId: null, reviewCounts: [] })
})

async function mountTab(run: Partial<FlowGraphNodeRun> | null = { integrationStatus: 'conflict', integrationReason: 'conflict_deferred' }) {
  const target = toInspectorTarget(selection(run))!
  const w = mount(MergeWindow, { props: { target }, attachTo: document.body, global: { plugins: [i18n], stubs: { ModelessShell: { template: '<div><slot /></div>' } } } })
  wrappers.push(w)
  await flushPromises()
  return w
}
const q = (w: VueWrapper<any>, test: string) => w.find(`[data-test="${test}"]`)

describe('auto_resolve on the wire', () => {
  it('reads the contract: tries, unresolved parts, unknown words kept harmless', async () => {
    const { toCardMergeState } = await import('@/features/document/api/cardMerge')
    expect(toCardMergeState(raw({ auto_resolve: exhausted() })).autoResolve).toEqual({
      state: 'exhausted', attempt: 3, maxAttempts: 3,
      attempts: [
        { no: 1, mode: 'ai', provider: { id: 'aip_x', name: 'Claude' }, result: 'conflicts_left', reason: '충돌 표시가 남았습니다', startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
        { no: 2, mode: 'rebase_ai', provider: { id: 'aip_x', name: 'Claude' }, result: 'check_failed', reason: 'test_upload_queue 실패', startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
        { no: 3, mode: 'switch_ai', provider: { id: 'aip_y', name: 'Codex' }, result: 'ai_error', reason: 'AI 실행 오류', startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
      ],
      unresolved: [{ path: 'src/queue.ts', hunks: [{ startLine: 12, endLine: 18 }, { startLine: 40, endLine: 40 }] }, { path: 'src/other.ts', hunks: [] }],
    })
    const odd = toCardMergeState(raw({ auto_resolve: { state: 'running', attempts: [{ mode: 'x', result: 'y', provider: null }, 'bad'], unresolved: [{ hunks: [] }, { path: 'p', hunks: [{ start_line: 'a' }, { start_line: 5 }] }] } })).autoResolve!
    expect(odd).toMatchObject({ state: 'running', attempt: 2, maxAttempts: 3 })
    expect(odd.attempts[0]).toMatchObject({ no: 1, mode: null, result: null, provider: null, reason: null })
    expect(odd.unresolved).toEqual([{ path: 'p', hunks: [{ startLine: 5, endLine: 5 }] }])
  })

  it.each([undefined, null, 'x', [], { state: 'nope' }, {}])('auto_resolve %j reads as none', async (value) => {
    const { toCardMergeState } = await import('@/features/document/api/cardMerge')
    expect(toCardMergeState(raw(value === undefined ? {} : { auto_resolve: value })).autoResolve).toBeNull()
  })

  it('the model: running, current try, n/max, hunk range', async () => {
    const { toCardMergeAutoResolve } = await import('@/features/document/api/cardMerge')
    const running = toCardMergeAutoResolve({ state: 'running', attempt: 9, max_attempts: 3, attempts: [attempt(1, { result: 'conflicts_left' }), attempt(2)] })!
    expect(mergeAutoRunning(running)).toBe(true)
    expect(mergeAutoRunning(null)).toBe(false)
    expect(mergeAutoCurrent(running)?.no).toBe(2)
    expect(mergeAutoCurrent(toCardMergeAutoResolve({ state: 'exhausted', attempt: 1, attempts: [attempt(1, { result: 'resolved' }), attempt(2, { result: 'timeout' })] }))?.no).toBe(1)
    expect(mergeAutoCurrent(toCardMergeAutoResolve({ state: 'running', attempts: [] }))).toBeNull()
    expect(mergeAutoProgress(running)).toEqual({ n: 3, max: 3 })
    expect(mergeAutoProgress(toCardMergeAutoResolve({ state: 'running', attempt: 0, max_attempts: 3 })!)).toEqual({ n: 1, max: 3 })
    expect([mergeHunkRange({ startLine: 12, endLine: 18 }), mergeHunkRange({ startLine: 7, endLine: 7 })]).toEqual(['12–18', '7'])
  })
})

describe('running', () => {
  const running = (over: Record<string, unknown> = {}) => raw({
    state: 'catching_up', integration_status: 'integrating', actions: [], files: [{ path: 'a.ts', resolved: false }],
    auto_resolve: { state: 'running', attempt: 2, max_attempts: 3, attempts: [attempt(1, { result: 'conflicts_left', reason: '충돌 표시가 남았습니다', ended_at: 'x' }), attempt(2, { mode: 'rebase_ai', provider: { id: 'aip_y', name: 'Codex' } })], unresolved: [] },
    ...over,
  })

  it('shows "충돌 자동 해결 중 (2/3)", the AI in use and the tries so far — and sends nothing by being open', async () => {
    api.read.mockResolvedValue((await import('@/features/document/api/cardMerge')).toCardMergeState(running()))
    const w = await mountTab({ integrationStatus: 'integrating' })
    expect(w.find('[data-test="merge-window"]').attributes('data-screen')).toBe('catching_up')
    expect(q(w, 'merge-box').classes()).toContain('is-info')
    expect(q(w, 'merge-box').find('h4').text()).toBe('충돌 자동 해결 중 (2/3)')
    expect(q(w, 'merge-catching-up-text').text()).toBe(t('gui.inspector.merge.auto.running_text', { ai: 'Codex' }))
    expect(q(w, 'merge-catching-up-text').text()).toContain('Codex')
    expect(q(w, 'merge-log').exists()).toBe(false)
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
    const rows = w.findAll('[data-test="merge-auto-attempt"]')
    expect(rows.map((row) => row.attributes('data-result'))).toEqual(['conflicts_left', 'running'])
    expect(rows[0].text()).toContain('충돌이 남음')
    expect(rows[0].text()).toContain('충돌 표시가 남았습니다')
    expect(rows[1].text()).toContain('다시 얹고 AI로 해결')
    expect(rows[1].text()).toContain('Codex')
    expect(w.findAll('button')).toHaveLength(0)
    expect(api.act).not.toHaveBeenCalled()
    expect(api.read).toHaveBeenCalledTimes(1)
  })

  it('falls back to the card AI while the first try has no AI yet', async () => {
    api.read.mockResolvedValue((await import('@/features/document/api/cardMerge')).toCardMergeState(running({
      auto_resolve: { state: 'running', attempt: 1, max_attempts: 3, attempts: [] },
    })))
    const w = await mountTab({ integrationStatus: 'integrating' })
    expect(q(w, 'merge-box').find('h4').text()).toBe('충돌 자동 해결 중 (1/3)')
    expect(q(w, 'merge-catching-up-text').text()).toContain('Claude')
    expect(w.find('[data-test="merge-auto-history"]').exists()).toBe(false)
  })

  it('without auto_resolve the progress box is as before', async () => {
    api.read.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_1' }, files: [{ path: 'a.ts', resolved: true }, { path: 'b.ts', resolved: false }], actions: [] }))
    const w = await mountTab()
    expect(q(w, 'merge-catching-up-text').text()).toBe(t('gui.inspector.merge.catching_up.resolving', { count: 2 }))
    expect(w.findAll('[data-test="merge-log"] > div')).toHaveLength(4)
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
    expect(q(w, 'merge-auto-note').exists()).toBe(false)
  })
})

describe('exhausted', () => {
  it('lists the unresolved files and parts, each try\'s reason and AI — the manual buttons stay', async () => {
    api.read.mockResolvedValue(await state({ auto_resolve: exhausted() }))
    const w = await mountTab()
    expect(w.find('[data-test="merge-window"]').attributes('data-screen')).toBe('needs_choice')
    const box = q(w, 'merge-auto-box')
    expect(box.attributes('data-auto-state')).toBe('exhausted')
    expect(box.find('h4').text()).toBe('자동 해결 실패 · 3번 시도해도 풀지 못했습니다')
    expect(box.find('h4').classes()).toContain('is-danger')
    expect(w.findAll('[data-test="merge-auto-file"]').map((row) => row.find('code').text())).toEqual(['src/queue.ts', 'src/other.ts'])
    expect(w.findAll('[data-test="merge-auto-hunks"]').map((row) => row.text())).toEqual(['12–18, 40행'])
    const rows = w.findAll('[data-test="merge-auto-attempt"]')
    expect(rows.map((row) => row.find('[data-test="merge-auto-ai"]').text())).toEqual(['Claude', 'Claude', 'Codex'])
    expect(rows.map((row) => row.find('[data-test="merge-auto-result"]').text())).toEqual(['충돌이 남음', '검사 실패', 'AI 오류'])
    expect(rows.map((row) => row.find('[data-test="merge-auto-reason"]').text())).toEqual(['충돌 표시가 남았습니다', 'test_upload_queue 실패', 'AI 실행 오류'])
    expect(rows[1].text()).toContain('2번째')
    expect(rows[1].text()).toContain('다시 얹고 AI로 해결')
    expect(rows[2].text()).toContain('다른 AI로 해결')
    for (const test of ['merge-ai', 'merge-pick-open', 'merge-discard']) expect(q(w, test).exists()).toBe(true)
    expect(q(w, 'merge-files').exists()).toBe(true)
    expect(api.act).not.toHaveBeenCalled()
  })

  it('[AI로 해결] still starts the AI by hand', async () => {
    api.read.mockResolvedValue(await state({ auto_resolve: exhausted() }))
    api.act.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'r2' }, actions: [], auto_resolve: null }))
    const w = await mountTab()
    await q(w, 'merge-ai').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', { action: 'ai_resolve', expectedAttemptId: 'att_4', providerId: null })
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
  })

  it('reads in English too', async () => {
    i18n.global.locale.value = 'en'
    api.read.mockResolvedValue(await state({ auto_resolve: exhausted() }))
    const w = await mountTab()
    expect(q(w, 'merge-auto-box').find('h4').text()).toBe('Automatic resolution failed · not solved in 3 tries')
    expect(w.find('[data-test="merge-auto-hunks"]').text()).toBe('lines 12–18, 40')
  })

  it('the picking screen is left alone', async () => {
    api.read.mockResolvedValue(await state({ auto_resolve: exhausted() }))
    const w = await mountTab()
    await q(w, 'merge-pick-open').trigger('click')
    expect(w.find('[data-test="merge-window"]').attributes('data-screen')).toBe('pick')
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
  })
})

describe('the short states and none', () => {
  it.each([
    ['succeeded', '충돌을 자동으로 풀었습니다.'],
    ['stopped_by_person', '직접 풀기를 시작해 자동 해결을 멈췄습니다.'],
    ['skipped_paused', '실행이 정지 중이라 자동 해결을 시작하지 않았습니다.'],
  ])('%s: one line, no box', async (key, text) => {
    api.read.mockResolvedValue(await state({ auto_resolve: { state: key, attempt: 1, max_attempts: 3, attempts: [attempt(1)], unresolved: [] } }))
    const w = await mountTab()
    expect(q(w, 'merge-auto-note').text()).toBe(text)
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
    expect(w.find('[data-test="merge-auto-history"]').exists()).toBe(false)
    expect(w.find('[data-test="merge-window"]').attributes('data-screen')).toBe('needs_choice')
    expect(q(w, 'merge-ai').exists()).toBe(true)
  })

  it.each([{}, { auto_resolve: null }])('no auto_resolve (%j): the screen is as it was', async (over) => {
    api.read.mockResolvedValue(await state(over))
    const w = await mountTab()
    expect(q(w, 'merge-auto-box').exists()).toBe(false)
    expect(q(w, 'merge-auto-note').exists()).toBe(false)
    expect(q(w, 'merge-summary').text()).toBe(t('gui.inspector.merge.needs_choice.both'))
    expect(w.findAll('[data-test="merge-files"] .mz-fr')).toHaveLength(2)
  })
})
