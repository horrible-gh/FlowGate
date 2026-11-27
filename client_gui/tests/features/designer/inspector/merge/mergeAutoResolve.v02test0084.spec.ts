/**
 * flowgate.v02test.0084 (F84-3) — the merge window shows the server's automatic conflict resolution:
 * `auto_resolve` parsing, "충돌 자동 해결 중 (n/3)" with the AI in use, the exhausted box (unresolved files and parts, each try's
 * reason and AI) beside the unchanged manual buttons, the one-line states, and no extra screen / request when it is null.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MergeWindow from '@/features/designer/inspector/features/merge/MergeWindow.vue'
import { mergeWindows } from '@/features/designer/inspector/features/merge/mergeWindows'
import {
  MERGE_REASON_MAX, mergeAutoCurrent, mergeAutoProgress, mergeAutoReason, mergeAutoResultKey, mergeAutoRunning, mergeCountKey, mergeHunkRange, mergeVerdictLine,
} from '@/features/designer/inspector/features/merge/mergeModel'
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
        { no: 1, mode: 'ai', provider: { id: 'aip_x', name: 'Claude' }, result: 'conflicts_left', reason: '충돌 표시가 남았습니다', reasonSummary: null, startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
        { no: 2, mode: 'rebase_ai', provider: { id: 'aip_x', name: 'Claude' }, result: 'check_failed', reason: 'test_upload_queue 실패', reasonSummary: null, startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
        { no: 3, mode: 'switch_ai', provider: { id: 'aip_y', name: 'Codex' }, result: 'ai_error', reason: 'AI 실행 오류', reasonSummary: null, startedAt: '2026-11-25T08:00:00+09:00', endedAt: 'x' },
      ],
      unresolved: [{ path: 'src/queue.ts', hunks: [{ startLine: 12, endLine: 18 }, { startLine: 40, endLine: 40 }] }, { path: 'src/other.ts', hunks: [] }],
      fileCount: null,
    })
    const odd = toCardMergeState(raw({ auto_resolve: { state: 'running', attempts: [{ mode: 'x', result: 'y', provider: null }, 'bad'], unresolved: [{ hunks: [] }, { path: 'p', hunks: [{ start_line: 'a' }, { start_line: 5 }] }] } })).autoResolve!
    expect(odd).toMatchObject({ state: 'running', attempt: 2, maxAttempts: 3 })
    expect(odd.attempts[0]).toMatchObject({ no: 1, mode: null, result: null, provider: null, reason: null, reasonSummary: null })
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

  it('allows a human pick during automatic resolution, stopping only on submit', async () => {
    api.read.mockResolvedValue(await state(running({ actions: ['ai_resolve', 'pick'], integration_status: 'conflict' })))
    api.act.mockResolvedValue(await state({ auto_resolve: { state: 'stopped_by_person' } }))
    const w = await mountTab()
    expect(q(w, 'merge-ai').exists()).toBe(true)
    expect(api.act).not.toHaveBeenCalled()
    await q(w, 'merge-pick-open').trigger('click')
    expect(w.find('[data-test="merge-window"]').attributes('data-screen')).toBe('pick')
    expect(api.act).not.toHaveBeenCalled()
    await q(w, 'merge-pick-latest').trigger('click')
    await q(w, 'merge-pick-submit').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', {
      action: 'pick', expectedAttemptId: 'att_4', files: [{ path: 'a.ts', side: 'latest' }], providerId: null,
    })
    expect(q(w, 'merge-auto-note').text()).toBe(t('gui.inspector.merge.auto.states.stopped_by_person'))
  })

  it('allows an explicit manual AI call during automatic resolution', async () => {
    api.read.mockResolvedValue(await state(running({ actions: ['ai_resolve', 'pick'] })))
    api.act.mockResolvedValue(await state({ state: 'catching_up', actions: [], auto_resolve: { state: 'stopped_by_person' } }))
    const w = await mountTab()
    await q(w, 'merge-ai').trigger('click')
    await flushPromises()
    expect(api.act).toHaveBeenCalledWith(FLOW, 'gn_4', { action: 'ai_resolve', expectedAttemptId: 'att_4', providerId: null })
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
    expect(box.find('h4').text()).toBe('자동 해결 실패 (3회)')
    expect(box.find('[data-test="merge-auto-text"]').exists()).toBe(false)
    expect(box.find('h4').classes()).toContain('is-danger')
    expect(w.findAll('[data-test="merge-auto-file"]').map((row) => row.find('code').text())).toEqual(['src/queue.ts', 'src/other.ts'])
    expect(w.findAll('[data-test="merge-auto-hunks"]').map((row) => row.text())).toEqual(['12–18, 40행'])
    expect(w.findAll('[data-test="merge-auto-no-hunks"]').map((row) => row.text())).toEqual(['충돌 구간 정보 없음'])
    expect(w.findAll('[data-test="merge-auto-reason-raw"]')).toHaveLength(0)
    const rows = w.findAll('[data-test="merge-auto-attempt"]')
    expect(rows.map((row) => row.find('[data-test="merge-auto-ai"]').text())).toEqual(['Claude', 'Claude', 'Codex'])
    expect(rows.map((row) => row.find('[data-test="merge-auto-result"]').text())).toEqual(['충돌이 남음', '검사 실패', 'AI 오류'])
    expect(rows.map((row) => row.find('[data-test="merge-auto-reason"]').text())).toEqual(['충돌 표시가 남았습니다', 'test_upload_queue 실패', 'AI 실행 오류'])
    expect(rows[1].text()).toContain('2번째')
    expect(rows[1].text()).toContain('다시 얹고 AI로 해결')
    expect(rows[2].text()).toContain('다른 AI로 해결')
    // F84-8 (시안 jvj9ff9t §1): the automatic box is the one box (its files and tries); the buttons sit in the pinned footer.
    for (const test of ['merge-ai', 'merge-pick-open', 'merge-discard']) expect(q(w, 'merge-foot').find(`[data-test="${test}"]`).exists()).toBe(true)
    expect(q(w, 'merge-files').exists()).toBe(false)
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
    expect(q(w, 'merge-auto-box').find('h4').text()).toBe('Automatic resolution failed (3 tries)')
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

// flowgate.v02test.0084 (F84-6) — the failure screen's wording and conflict parts: the AI error result, a check failure's
// reason as one readable line (raw JSON only on unfolding), "no conflict part information" for an empty hunk list, and
// `{count}` sentences that drop the counted part instead of showing "( file(s))".
const VERDICT = JSON.stringify({
  status: 'failed', checked: 2, failure_count: 2, warnings: [], preexisting: 0, skipped: 0,
  failures: [
    { path: 'server/app/queue.py', checker: 'python_ast', line: 42, message: 'invalid syntax' },
    { path: 'server/app/other.py', checker: 'python_ast', line: 7, message: 'unexpected indent' },
  ],
})

describe('F84-6 the reason, result and count rules', () => {
  it('reads reason_summary and count from the wire; a bad count reads as none', async () => {
    const { toCardMergeAutoResolve } = await import('@/features/document/api/cardMerge')
    const auto = toCardMergeAutoResolve({ state: 'skipped_paused', count: 3, attempts: [attempt(1, { result: 'check_failed', reason: VERDICT, reason_summary: '구문 검사 · a.py:1 · 오류' })] })!
    expect(auto.fileCount).toBe(3)
    expect(auto.attempts[0]).toMatchObject({ reason: VERDICT, reasonSummary: '구문 검사 · a.py:1 · 오류' })
    for (const count of [0, -1, 'x', null, undefined]) expect(toCardMergeAutoResolve({ state: 'skipped_paused', count })!.fileCount).toBeNull()
  })

  it('a result the screen does not know reads as the plain failure key', () => {
    expect(['ai_error', 'check_failed', 'conflicts_left'].map(mergeAutoResultKey)).toEqual(['ai_error', 'check_failed', 'conflicts_left'])
    expect([null, undefined, '', 'provider_failed'].map(mergeAutoResultKey)).toEqual(['unknown', 'unknown', 'unknown', 'unknown'])
  })

  it('the reason line: the summary, else a line from the JSON verdict, else the cut text', () => {
    expect(mergeAutoReason({ reason: VERDICT, reasonSummary: ' 구문 검사 실패 ' })).toEqual({ line: '구문 검사 실패', raw: VERDICT })
    expect(mergeAutoReason({ reason: VERDICT, reasonSummary: null })).toEqual({ line: 'python_ast · server/app/queue.py:42 · invalid syntax (+1)', raw: VERDICT })
    expect(mergeAutoReason({ reason: `verification_failed: ${VERDICT}` }).line).toBe('python_ast · server/app/queue.py:42 · invalid syntax (+1)')
    expect(mergeAutoReason({ reason: '충돌 표시가 남았습니다' })).toEqual({ line: '충돌 표시가 남았습니다', raw: null })
    const broken = `{"status": "failed", "failures": [{"path": "${'x'.repeat(200)}`
    const cut = mergeAutoReason({ reason: broken })
    expect(cut.line.endsWith('…')).toBe(true)
    expect(cut.line.length).toBe(MERGE_REASON_MAX + 1)
    expect(cut.raw).toBe(broken)
    expect(mergeAutoReason({ reason: null, reasonSummary: null })).toEqual({ line: '', raw: null })
    expect(mergeVerdictLine({ check: 'pytest', file: 'tests/a.py', error: 'boom' })).toBe('pytest · tests/a.py · boom')
    expect(mergeVerdictLine({ status: 'failed', failures: [] })).toBeNull()
  })

  it('a count sentence falls back to its no-count key', () => {
    expect([mergeCountKey('k', 2), mergeCountKey('k', 0), mergeCountKey('k', null), mergeCountKey('k', undefined)]).toEqual(['k', 'k_nocount', 'k_nocount', 'k_nocount'])
  })
})

describe('F84-6 the exhausted screen', () => {
  it.each([['ko', 'AI 오류', '실패'], ['en', 'AI error', 'Failed'], ['ja', 'AI エラー', '失敗']])('%s: AI error and an unknown result in words, never the raw code', async (locale, aiError, unknown) => {
    i18n.global.locale.value = locale
    const base = exhausted()
    api.read.mockResolvedValue(await state({ auto_resolve: { ...base, attempts: [...base.attempts.slice(0, 2), attempt(3, { result: 'provider_failed', reason: 'x', ended_at: 'x' }), attempt(4, { result: 'ai_error', reason: 'exit 1', ended_at: 'x' })] } }))
    const w = await mountTab()
    const results = w.findAll('[data-test="merge-auto-result"]').map((row) => row.text())
    expect(results.slice(2)).toEqual([unknown, aiError])
    expect(w.text()).not.toContain('provider_failed')
    expect(w.find('[data-test="merge-auto-no-hunks"]').text()).toBe(t('gui.inspector.merge.auto.no_hunks'))
  })

  it('a check failure shows one readable line; the raw JSON is only inside the fold', async () => {
    const base = exhausted()
    api.read.mockResolvedValue(await state({ auto_resolve: { ...base, attempts: [attempt(1, { result: 'check_failed', reason: VERDICT, ended_at: 'x' }), attempt(2, { result: 'check_failed', reason: VERDICT, reason_summary: '구문 검사 실패: queue.py 42행', ended_at: 'x' })] } }))
    const w = await mountTab()
    const reasons = w.findAll('[data-test="merge-auto-reason"]').map((row) => row.text())
    expect(reasons).toEqual(['python_ast · server/app/queue.py:42 · invalid syntax (+1)', '구문 검사 실패: queue.py 42행'])
    for (const reason of reasons) expect(reason).not.toContain('{')
    const folds = w.findAll('[data-test="merge-auto-reason-raw"]')
    expect(folds).toHaveLength(2)
    expect(folds[0].element.tagName).toBe('DETAILS')
    expect(folds[0].find('summary').text()).toBe('원문 보기')
    expect(folds[0].find('pre').text()).toBe(VERDICT)
  })

  it('an exhausted answer with every hunk list empty says so per file instead of a blank', async () => {
    api.read.mockResolvedValue(await state({ auto_resolve: exhausted({ unresolved: [{ path: 'a.ts', hunks: [] }, { path: 'b.ts' }] }) }))
    const w = await mountTab()
    expect(w.findAll('[data-test="merge-auto-file"]')).toHaveLength(2)
    expect(w.findAll('[data-test="merge-auto-hunks"]')).toHaveLength(0)
    expect(w.findAll('[data-test="merge-auto-no-hunks"]').map((row) => row.text())).toEqual(['충돌 구간 정보 없음', '충돌 구간 정보 없음'])
  })
})

describe('F84-6 count sentences', () => {
  it.each(['en', 'ja', 'ko'])('%s: paused conflict without a count drops the counted part', async (locale) => {
    i18n.global.locale.value = locale
    api.read.mockResolvedValue(await state({ files: [], auto_resolve: { state: 'skipped_paused', attempts: [] } }))
    const w = await mountTab()
    const text = q(w, 'merge-summary').text()
    expect(text).toBe(t('gui.inspector.merge.needs_choice.both_nocount'))
    expect(text).not.toMatch(/\(\s*(file\(s\)|重なったファイル\s*件)\)/)
    expect(text).not.toContain('{count}')
  })

  it('paused conflict with the server count puts the number in', async () => {
    i18n.global.locale.value = 'en'
    api.read.mockResolvedValue(await state({ files: [], auto_resolve: { state: 'skipped_paused', count: 3, attempts: [] } }))
    const w = await mountTab()
    expect(q(w, 'merge-summary').text()).toContain('(3 file(s))')
    expect(q(w, 'merge-auto-note').text()).toBe('The run is paused, so automatic resolution did not start.')
  })

  it('the progress box with no file list reads without a count', async () => {
    i18n.global.locale.value = 'en'
    api.read.mockResolvedValue(await state({ state: 'catching_up', resolving: { run_id: 'run_1' }, files: [], actions: [] }))
    const w = await mountTab()
    expect(q(w, 'merge-catching-up-text').text()).toBe(t('gui.inspector.merge.catching_up.resolving_nocount'))
    const log = w.findAll('[data-test="merge-log"] > div').map((row) => row.text())
    expect(log[0]).not.toContain('()')
    expect(log[1]).not.toContain('0/0')
  })

  it('every no-count key exists in ko, en and ja', () => {
    for (const locale of ['ko', 'en', 'ja']) {
      for (const key of ['auto.exhausted_title_nocount', 'auto.no_hunks', 'auto.reason_raw', 'catching_up.resolving_nocount',
        'catching_up.steps.found_nocount', 'catching_up.steps.resolve_nocount', 'needs_choice.both_nocount']) {
        expect(i18n.global.te(`gui.inspector.merge.${key}`, locale)).toBe(true)
        expect(i18n.global.t(`gui.inspector.merge.${key}`, {}, { locale })).not.toContain('{count}')
      }
    }
  })
})


describe('undo of an AI-resolved merge', () => {
  it.each(['success', 'failure', 'blocked'])('confirms the impact and handles %s', async outcome => {
    const { default: ConfirmDialog } = await import('@/components/ui/ConfirmDialog.vue')
    api.read.mockResolvedValue(await state({ state: 'hidden', integration_status: 'integrated', actions: [],
      auto_resolve: { state: 'succeeded', attempt: 1, max_attempts: 3,
        attempts: [attempt(1, { result: 'resolved', ended_at: 'x' })] } }))
    const target = toInspectorTarget(selection({ status: 'done', integrationStatus: 'integrated' }))!
    const run = vi.fn().mockResolvedValue(outcome === 'success')
    const refresh = vi.fn()
    const win = { key: 'undo-test', target: () => target, refresh, minimized: false, raise: 0,
      undo: { blocked: () => outcome === 'blocked' ? 'run_not_idle' : null,
        impact: () => ({ back: ['Selected card', 'Join card'], keep: ['Parallel card'] }), run } }
    mergeWindows.push(win)
    const w = mount(MergeWindow, { props: { target, win }, attachTo: document.body,
      global: { plugins: [i18n], stubs: { ModelessShell: { template: '<div><slot /></div>' } } } })
    wrappers.push(w); await flushPromises()
    const undo = q(w, 'merge-undo')
    expect(undo.exists()).toBe(true)
    if (outcome === 'blocked') {
      expect(undo.attributes('disabled')).toBeDefined()
      expect(q(w, 'merge-undo-reason').text()).not.toBe('')
      expect(run).not.toHaveBeenCalled()
      return
    }
    await undo.trigger('click')
    const confirm = w.findAllComponents(ConfirmDialog).find(dialog => dialog.props('open'))!
    expect(confirm).toBeDefined()
    expect(document.body.textContent).toContain('Selected card, Join card')
    expect(document.body.textContent).toContain('Parallel card')
    confirm.vm.$emit('confirm'); await flushPromises()
    expect(run).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalled()
    expect(mergeWindows.some(entry => entry.key === win.key)).toBe(outcome !== 'success')
    expect(q(w, 'merge-undo-failed').exists()).toBe(outcome === 'failure')
  })
})
