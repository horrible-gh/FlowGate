import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
const http = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get }))
const clip = vi.hoisted(() => ({ copy: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => ({ copyToClipboard: clip.copy }))
import { fetchFlowCommandResult, toFlowCommandResult } from '@/features/project/commandResultApi'
import { createRunResultWatch } from '@/features/ai/runResult/runResultWatch'
import RunResultWindow from '@/features/ai/runResult/RunResultWindow.vue'
import RunResultDetailWindow from '@/features/ai/runResult/RunResultDetailWindow.vue'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'

// flowgate.v02.0113 T#5 — the run result windows (0030-M §5, D 개정 §3.14 / §6.5): the command result read, when the 1st
// window opens (completed / failed only, also when a waiting command later completes or fails), its content, and the 2nd
// window's messages (Markdown, many and long, each launch's own output, copy all).
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
beforeEach(() => { vi.clearAllMocks(); i18n.global.locale.value = 'ko' })
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = '' })

function raw(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run_1', flow_id: 'f', command_seq: 5, latest: true, scope: 'full', started_at: '2026-11-01T00:00:00.000+00:00',
    ended_at: '2026-11-01T00:00:01.000+00:00', ended: true, state: 'completed', idle_reason: 'completed', next_state: 'completed',
    counts: { done: 3, failed: 0, waiting: 0 },
    nodes: [1, 2, 3].map((i) => ({ node_key: `gn_${i}`, title: `작업 ${i}`, doc_type: 'T', status: 'done', outcome: 'done', stage: null, reason: null, detail: null })),
    failure: null, failures: [], roles: { instruction: [{ kind: 'person' }], execution: [{ kind: 'ai', ai_id: 'aip_x', ai_name: 'Claude' }], review: [] },
    message_count: 1,
    messages: [{ kind: 'event', event: 'node_state', code: 'review_approved', node_key: 'gn_1', title: '작업 1', from: 'open', to: 'done', at: '2026-11-01T00:00:00.500+00:00', error: false }],
    ...over,
  }
}

const invocation = (i: number, body: string, extra: Record<string, unknown> = {}) => ({
  kind: 'invocation', inv_id: `air_${i}#1`, role: 'execution', purpose: 'result_write', node_key: 'gn_1', title: '작업 1',
  requested: { ai_id: 'aip_x', model: 'claude-opus-5-5' }, confirmed: { ai_id: 'aip_x', ai_name: 'Claude', state: 'confirmed', models: ['claude-sonnet-5-5'] },
  at: '2026-11-01T00:00:00.700+00:00', result: 'succeeded', body, error_text: null, error: false, ...extra,
})

describe('command result read', () => {
  it('GETs the run command result with the canvas client id and maps it', async () => {
    http.get.mockResolvedValue({ data: { command_result: raw() } })
    const result = await fetchFlowCommandResult('run_1')
    expect(http.get.mock.calls[0][0]).toBe('/api/v1/runs/run_1/command-result')
    expect(http.get.mock.calls[0][2].headers['x-canvas-client-id']).toBeTruthy()
    expect(result).toMatchObject({ runId: 'run_1', commandSeq: 5, scope: 'full', state: 'completed', counts: { done: 3, failed: 0 } })
    await fetchFlowCommandResult('run_1', 7)
    expect(http.get.mock.calls[1][0]).toBe('/api/v1/runs/run_1/command-result?command_seq=7')
  })
})

describe('when the 1st window opens', () => {
  it.each([['completed', true], ['failed', true], ['waiting', false], ['stopped', false]])('%s -> shown %s', async (state, shown) => {
    const load = vi.fn(async () => toFlowCommandResult(raw({ state })))
    const watch = createRunResultWatch({ load })
    watch.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    watch.observe('f', { runId: 'run_1', status: 'running', lastEventSeq: 6 })
    expect(load).not.toHaveBeenCalled()                                   // not idle yet
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 9 })
    await flushPromises()
    expect(load).toHaveBeenCalledWith('run_1')
    expect(watch.state.resultOpen).toBe(shown)
    expect(Object.keys(watch.pending)).toEqual([])
  })

  it('keeps watching an older command or one still going, and ignores the run as it was before the command', async () => {
    const answers = [raw({ command_seq: 3 }), raw({ ended: false, state: 'running' }), raw()]
    const load = vi.fn(async () => toFlowCommandResult(answers.shift()))
    const watch = createRunResultWatch({ load })
    watch.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    expect(load).not.toHaveBeenCalled()
    for (const seq of [5, 6, 7]) {
      watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: seq })
      await flushPromises()
    }
    expect(load).toHaveBeenCalledTimes(3)
    expect(watch.state.resultOpen).toBe(true)
  })

  // flowgate.v02.0143 T#1 (NR 0143.0003 §5-4, §7): a read refused for good (the command gone: 404, refused: 401 / 403)
  // drops the watch with no window, and another Project (`clear`) drops every watch; a passing read failure no longer
  // drops it (it is read again: runResultWatch.0143.spec.ts R3 / R3b).
  it('a gone or refused read (404 / 403) and another Project drop the watch with no window', async () => {
    for (const status of [404, 403]) {
      const watch = createRunResultWatch({ load: vi.fn(async () => { throw Object.assign(new Error('x'), { status }) }) })
      watch.arm('f', null)
      watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 1 })
      await flushPromises()
      expect(watch.state.resultOpen).toBe(false)
      expect(Object.keys(watch.pending)).toEqual([])
    }
    const watch = createRunResultWatch({ load: vi.fn(async () => { throw new Error('x') }) })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 1 })
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])                       // a passing failure keeps it
    watch.arm('g', null)
    watch.clear()
    expect(Object.keys(watch.pending)).toEqual([])
  })
})

describe('a command that waits and may still go on (review rework: P1)', () => {
  const waiting = () => raw({ state: 'waiting', continues: true, idle_reason: 'waiting_approval', next_state: 'waiting_approval' })

  it('opens nothing while it waits, keeps watching its own command and opens once when the save completes it', async () => {
    const answers = [waiting(), raw()]
    const load = vi.fn(async () => toFlowCommandResult(answers.shift()))
    const watch = createRunResultWatch({ load })
    watch.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })     // [전체 실행] -> 수동 작성 대기
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })     // nothing new: no read
    expect(load).toHaveBeenCalledTimes(1)
    watch.observe('f', { runId: 'run_1', status: 'running', lastEventSeq: 8 })  // the person's save woke it
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 12 })    // ... and it completed
    await flushPromises()
    expect(load).toHaveBeenLastCalledWith('run_1', 5)                           // its own command, by its seq
    expect(watch.state.resultOpen).toBe(true)
    expect(watch.state.result?.state).toBe('completed')
    expect(Object.keys(watch.pending)).toEqual([])
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 13 })
    expect(load).toHaveBeenCalledTimes(2)                                       // once only
  })

  it('a failure after it went on opens the window once', async () => {
    const failed = raw({
      state: 'failed', idle_reason: 'waiting_approval', next_state: 'waiting_approval', counts: { done: 0, failed: 1, waiting: 0 },
      failure: { node_key: 'gn_1', title: '작업 1', code: 'ai_unavailable', detail: 'inactive' },
    })
    const answers = [waiting(), waiting(), failed]
    const load = vi.fn(async () => toFlowCommandResult(answers.shift()))
    const watch = createRunResultWatch({ load })
    watch.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    for (const seq of [6, 7, 9]) {
      watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: seq })
      await flushPromises()
    }
    expect(load).toHaveBeenCalledTimes(3)
    expect(watch.state.resultOpen).toBe(true)
    expect(watch.state.result?.state).toBe('failed')
    expect(watch.state.seq).toBe(1)
  })

  it('a waiting command that can no longer go on, another run, or the next command ends its watch with no window', async () => {
    const ended = createRunResultWatch({ load: vi.fn(async () => toFlowCommandResult(raw({ state: 'waiting', continues: false }))) })
    ended.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    ended.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })
    await flushPromises()
    expect(Object.keys(ended.pending)).toEqual([])
    expect(ended.state.resultOpen).toBe(false)

    const load = vi.fn(async () => toFlowCommandResult(waiting()))
    const moved = createRunResultWatch({ load })
    moved.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    moved.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })
    await flushPromises()
    moved.observe('f', { runId: 'run_2', status: 'idle', lastEventSeq: 1 })     // a Time Machine took the run away
    expect(Object.keys(moved.pending)).toEqual([])
    expect(load).toHaveBeenCalledTimes(1)

    const next = createRunResultWatch({ load: vi.fn(async () => toFlowCommandResult(waiting())) })
    next.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    next.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })
    await flushPromises()
    next.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 6 })          // the next command replaces the watch
    expect(next.pending.f.commandSeq).toBeNull()
  })
})

describe('the 1st window', () => {
  it.each([
    ['completed', 'full'], ['failed', 'step'], ['stopped', 'full'],
  ])('shows the origin name and ID for %s / %s', (state, scope) => {
    wrapper = mount(RunResultWindow, { props: {
      result: toFlowCommandResult(raw({ state, scope, flow_id: 'flow-a' })), open: true, flowName: 'Same name',
    }, global: { plugins: [i18n] }, attachTo: document.body })
    const metric = document.querySelector('[data-test="run-result-flow"]')!
    expect(metric.querySelector('.run-result-label')!.textContent).toBe('발생 플로우')
    const name = metric.querySelector('[data-test="run-result-flow-name"]')!
    const id = metric.querySelector('[data-test="run-result-flow-id"]')!
    expect(name.textContent).toBe('Same name')
    expect(name.getAttribute('title')).toBe('Same name')
    expect(id.textContent).toBe('flow-a')
    expect(id.getAttribute('title')).toBe('flow-a')
    expect(metric.getAttribute('title')).toBe('Same name (flow-a)')
  })

  it('distinguishes same-name sequential results and falls back only to the origin ID', async () => {
    wrapper = mount(RunResultWindow, { props: {
      result: toFlowCommandResult(raw({ flow_id: 'flow-a' })), open: true, flowName: 'Same name',
    }, global: { plugins: [i18n] }, attachTo: document.body })
    const name = () => document.querySelector('[data-test="run-result-flow-name"]')!.textContent
    const id = () => document.querySelector('[data-test="run-result-flow-id"]')
    expect(name()).toBe('Same name')
    expect(id()!.textContent).toBe('flow-a')
    await wrapper.setProps({ result: toFlowCommandResult(raw({ flow_id: 'flow-b', run_id: 'run_2' })) })
    expect(name()).toBe('Same name')
    expect(id()!.textContent).toBe('flow-b')
    await wrapper.setProps({ flowName: null })
    expect(name()).toBe('flow-b')
    expect(id()).toBeNull()
    await wrapper.setProps({ flowName: 'flow-b' })
    expect(name()).toBe('flow-b')
    expect(id()).toBeNull()
    await wrapper.setProps({ result: toFlowCommandResult(raw({ flow_id: null })), flowName: 'Unverified name' })
    expect(name()).toBe('확인 불가')
    expect(id()).toBeNull()
  })

  it('shows the state, scope, cards, counts and next state — no fixed sentence — with [상세] and [닫기]', async () => {
    wrapper = mount(RunResultWindow, { props: { result: toFlowCommandResult(raw()), open: true }, global: { plugins: [i18n] }, attachTo: document.body })
    const win = document.querySelector('[data-test="run-result"]')!
    expect(win.getAttribute('data-state')).toBe('completed')
    expect(document.querySelector('[data-test="run-result-scope"]')!.textContent).toBe('전체 실행')
    expect(document.querySelector('[data-test="run-result-counts"]')!.textContent).toBe('3 / 0')
    expect(document.querySelector('[data-test="run-result-next"]')!.textContent).toBe('완료')
    expect(document.querySelectorAll('[data-test="run-result-steps"] li')).toHaveLength(3)
    expect(win.textContent).not.toContain('작업 1건 완료')
    expect(document.querySelector('[data-test="run-result-failure"]')).toBeNull()
    ;(document.querySelector('[data-test="run-result-detail"]') as HTMLButtonElement).click()
    expect(wrapper.emitted('detail')).toHaveLength(1)
  })

  it('a failed command tells its one-line reason', () => {
    const failed = raw({
      state: 'failed', counts: { done: 0, failed: 1, waiting: 1 }, next_state: 'waiting_approval',
      nodes: [{ node_key: 'gn_1', title: '작업 1', outcome: 'failed', reason: 'ai_unavailable', detail: 'inactive' },
        { node_key: 'gn_2', title: '작업 2', outcome: 'waiting', reason: 'manual_write_wait' }],
      failure: { node_key: 'gn_1', title: '작업 1', code: 'ai_unavailable', detail: 'inactive' },
    })
    wrapper = mount(RunResultWindow, { props: { result: toFlowCommandResult(failed), open: true }, global: { plugins: [i18n] }, attachTo: document.body })
    expect(document.querySelector('[data-test="run-result"]')!.getAttribute('data-state')).toBe('failed')
    expect(document.querySelector('[data-test="run-result-failure"]')!.textContent).toContain('작업 1: AI를 사용할 수 없습니다 (inactive)')
    expect(document.querySelector('[data-test="run-result-steps"]')!.textContent).toContain('작업 2 · 대기 (수동 작성 대기)')
  })
})

describe('the 2nd window', () => {
  it('renders many long Markdown messages with models apart, the full error, and copies them all', async () => {
    const long = '## 결과\n\n' + Array.from({ length: 3000 }, (_, i) => `- ${i}`).join('\n') + '\n\n```js\nconsole.log(1)\n```\n<script>alert(1)</script>'
    const messages = [invocation(0, long), ...Array.from({ length: 24 }, (_, i) => invocation(i + 1, `### 검수 ${i}\n\n본문`)),
      invocation(99, '', { result: 'call_failed', error: true, error_text: 'result: call_failed (exit_nonzero)\nstderr_tail:\nboom' })]
    wrapper = mount(RunResultDetailWindow, { props: { result: toFlowCommandResult(raw({ messages })), open: true }, global: { plugins: [i18n] }, attachTo: document.body })
    expect(document.querySelector('[data-test="run-result-message-count"]')!.textContent).toBe('메시지 26개')
    const cards = document.querySelectorAll('[data-test="run-result-message"]')
    expect(cards).toHaveLength(26)
    const first = cards[0]
    expect(first.querySelectorAll('li')).toHaveLength(3000)                // nothing cut
    expect(first.querySelector('h2')!.textContent).toBe('결과')
    expect(first.querySelector('pre code')!.textContent).toContain('console.log(1)')
    expect(first.querySelector('script')).toBeNull()                       // raw HTML shown as text
    expect(first.textContent).toContain('<script>alert(1)</script>')
    expect(first.querySelector('[data-test="run-result-model"]')!.textContent).toContain('claude-opus-5-5 → 확인 claude-sonnet-5-5')
    expect(cards[25].querySelector('[data-test="run-result-error"]')!.textContent).toContain('boom')
    expect(document.querySelector('[data-test="run-result-ai-execution"]')!.textContent).toBe('Claude')
    expect(document.querySelector('[data-test="run-result-ai-instruction"]')!.textContent).toBe('사용하지 않음')
    expect(document.querySelector('[data-test="run-result-ai-review"]')!.textContent).toBe('없음')
    ;(document.querySelector('[data-test="run-result-copy"]') as HTMLButtonElement).click()
    await flushPromises()
    const copied = clip.copy.mock.calls[0][0] as string
    expect(copied).toContain(long)
    expect(copied).toContain('boom')
    expect(copied.split('\n-----\n')).toHaveLength(27)                     // the head line and 26 messages
  })

  it('a launch whose own output was not kept says so, and the run final output is labelled — in the window and the copy', async () => {
    const messages = [
      invocation(1, '', {
        inv_id: 'air_old#1', result: 'call_failed', error: true, body_source: 'not_preserved',
        error_text: 'result: call_failed (exit_nonzero)\nlaunch_output: not_preserved',
      }),
      invocation(2, 'OLD FINAL', { inv_id: 'air_old#2', body_source: 'run' }),
      invocation(3, 'own body', { inv_id: 'air_new#1', body_source: 'launch' }),
    ]
    wrapper = mount(RunResultDetailWindow, { props: { result: toFlowCommandResult(raw({ messages })), open: true }, global: { plugins: [i18n] }, attachTo: document.body })
    const cards = document.querySelectorAll('[data-test="run-result-message"]')
    expect(cards[0].querySelector('[data-test="run-result-body-note"]')!.textContent).toContain('보존되지 않았습니다')
    expect(cards[0].querySelector('.run-detail-body')).toBeNull()
    expect(cards[1].querySelector('[data-test="run-result-body-note"]')!.getAttribute('data-source')).toBe('run')
    expect(cards[2].querySelector('[data-test="run-result-body-note"]')).toBeNull()
    ;(document.querySelector('[data-test="run-result-copy"]') as HTMLButtonElement).click()
    await flushPromises()
    const copied = clip.copy.mock.calls[0][0] as string
    expect(copied).toContain('보존되지 않았습니다')
    expect(copied).toContain('AI 실행의 최종 출력')
  })
})
