// flowgate.v02.0124 T#2 — a run left by no-op / waiting for a person's intervention says which card blocks it and why
// (0003-NR §5.3, §6.7~10): the server's command-result `blocking` -> the Shell's existing toast. No new button, window or band.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { toFlowCommandResult } from '@/features/project/commandResultApi'
import { createRunResultWatch, runWaitText } from '@/features/ai/runResult/runResultWatch'
import { failureLine } from '@/features/ai/runResult/runResultView'
import { installRunResultMessages } from '@/features/ai/runResult/messages'
import { ALL_MENU_TARGETS, isMenuCommandEnabled, runBandTone, runBandView } from '@/features/designer/top/designerTop'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
installRunResultMessages(i18n.global)                                           // the result window's own words (failure.*)
const t = (key: string, values?: Record<string, unknown>) => String(values ? i18n.global.t(key, values) : i18n.global.t(key))
const te = (key: string) => i18n.global.te(key)

const HOLD = 'review_not_passed:review_verdict_hold'
const EXHAUSTED = 'review_not_passed:retry_exhausted'
const blockingOf = (title: string | null, reason: string | null, release: string | null, key = 'gn_a') =>
  ({ node_key: key, title, doc_type: 'T', kind: 'needs_intervention', reason, release })

/** A needs_intervention no-op command result, as the server answers it (T#2 S1: `nodes=[]`, `blocking` the card). */
function result(over: Record<string, unknown> = {}) {
  return toFlowCommandResult({
    run_id: 'run_1', flow_id: 'f', command_seq: 5, latest: true, scope: 'step', started_at: '2026-11-01T00:00:00.000+00:00',
    ended_at: '2026-11-01T00:00:00.100+00:00', ended: true, end_seq: 7, continues: true, state: 'waiting',
    idle_reason: 'needs_intervention', next_state: 'needs_intervention', counts: { done: 0, failed: 0, waiting: 0 },
    nodes: [], failure: null, failures: [], blocking: [blockingOf('작업 A', HOLD, 'memo_rereview')],
    roles: { instruction: [], execution: [], review: [] }, message_count: 0, messages: [],
    ...over,
  })
}

const manual = { node_key: 'gn_m', title: '작업 M', doc_type: 'T', status: 'open', outcome: 'waiting', stage: 'instruction', reason: 'manual_write_wait', detail: null }
const review = { node_key: 'gn_a', title: '작업 A', doc_type: 'T', status: 'review', outcome: 'waiting', stage: 'result', reason: 'review', detail: null }

describe('toFlowCommandResult reads end_seq and blocking', () => {
  it('end_seq and each blocking item', () => {
    const r = result({ end_seq: 12, nodes: [review], counts: { done: 1, failed: 0, waiting: 1 } })
    expect(r.endSeq).toBe(12)
    expect(r.blocking).toEqual([{ nodeKey: 'gn_a', title: '작업 A', docType: 'T', kind: 'needs_intervention', reason: HOLD, release: 'memo_rereview' }])
    expect(r.nodes).toHaveLength(1)                                            // the other fields are read as before
    expect(r.counts).toEqual({ done: 1, failed: 0, waiting: 1 })
  })

  it('no end_seq: null; no blocking or not an array: []', () => {
    expect(toFlowCommandResult({ run_id: 'r', ended: false }).endSeq).toBeNull()
    expect(toFlowCommandResult({ run_id: 'r' }).blocking).toEqual([])
    expect(toFlowCommandResult({ run_id: 'r', blocking: 'x' }).blocking).toEqual([])
    expect(toFlowCommandResult({ run_id: 'r', blocking: { node_key: 'a' } }).blocking).toEqual([])
  })
})

describe('runWaitText: the blocking card first (ko, T#2 §4)', () => {
  beforeEach(() => { i18n.global.locale.value = 'ko' })
  const memo = '카드에 개입 필요 상태가 기록되어 있습니다.'

  it.each([
    ['memo_rereview', {}, `개입 필요 — 작업 A: ${HOLD} · ${memo}`],
    ['two cards', { blocking: [blockingOf('작업 A', HOLD, 'memo_rereview'), blockingOf('작업 B', HOLD, 'memo_rereview', 'gn_b')] },
      `개입 필요 — 작업 A: ${HOLD} (외 1개) · ${memo}`],
    ['a person\'s document', { blocking: [blockingOf('작업 A', EXHAUSTED, 'new_submission')] },
      `개입 필요 — 작업 A: ${EXHAUSTED} · 해제: 문서 수정 후 새 제출`],
    ['an AI card the next command requests again', { blocking: [blockingOf('작업 A', EXHAUSTED, null)] },
      `개입 필요 — 작업 A: ${EXHAUSTED}`],
    ['issuer permission', { blocking: [blockingOf('작업 A', 'issuer_lacks_approve', 'issuer_permission')] },
      '개입 필요 — 작업 A: issuer_lacks_approve · 해제: 발행자 승인 권한 보완'],
    ['no title: the node key', { blocking: [blockingOf(null, HOLD, 'memo_rereview', 'gn_a')] }, `개입 필요 — gn_a: ${HOLD} · ${memo}`],
    ['no reason', { blocking: [blockingOf('작업 A', null, 'memo_rereview')] }, `개입 필요 — 작업 A · ${memo}`],
    ['blocking first over a manual write wait', { nodes: [manual, review], counts: { done: 0, failed: 0, waiting: 2 } },
      `개입 필요 — 작업 A: ${HOLD} · ${memo}`],
    ['no blocking: the manual write wait as before', { blocking: [], nodes: [{ ...manual, title: '작업' }] }, '개입 필요 — 작업: 수동 작성 대기'],
    ['waiting_approval stored before T-2, no blocking: as before', { idle_reason: 'waiting_approval', blocking: [], nodes: [{ ...manual, title: '작업' }] },
      '사람 작성·승인 대기 — 작업: 수동 작성 대기'],
    ['waiting_confirm, a gate, no blocking', { idle_reason: 'waiting_confirm', blocking: [], nodes: [{ ...manual, reason: null }] }, '확인 대기'],
  ] as const)('%s', (_name, over, text) => {
    expect(runWaitText(result(over as Record<string, unknown>), t, te)).toBe(text)
  })

  it('en and ja: the head, the others and the release words', () => {
    const two = result({ blocking: [blockingOf('작업 A', HOLD, 'memo_rereview'), blockingOf('작업 B', HOLD, 'memo_rereview', 'gn_b')] })
    i18n.global.locale.value = 'en'
    expect(runWaitText(two, t, te)).toBe(`Intervention needed — 작업 A: ${HOLD} (+1 more) · The card has a recorded intervention state.`)
    expect(runWaitText(result({ blocking: [blockingOf('작업 A', EXHAUSTED, 'new_submission')] }), t, te))
      .toBe(`Intervention needed — 작업 A: ${EXHAUSTED} · Release: edit the document and submit it again`)
    i18n.global.locale.value = 'ja'
    expect(runWaitText(two, t, te)).toBe(`介入が必要 — 작업 A: ${HOLD} (他1件) · カードに介入が必要な状態が記録されています。`)
    expect(runWaitText(result({ blocking: [blockingOf('작업 A', 'issuer_lacks_approve', 'issuer_permission')] }), t, te))
      .toBe('介入が必要 — 작업 A: issuer_lacks_approve · 解除: 発行者の承認権限を補う')
    i18n.global.locale.value = 'ko'
  })

  it('an unknown release code adds no release words', () => {
    expect(runWaitText(result({ blocking: [blockingOf('작업 A', HOLD, 'something_new')] }), t, te)).toBe(`개입 필요 — 작업 A: ${HOLD}`)
  })
})

describe('the no-op flow through the watch', () => {
  beforeEach(() => { i18n.global.locale.value = 'ko' })

  it('arm -> observe idle -> one tell, no window, still watched; the same result read again tells nothing', async () => {
    const load = vi.fn(async () => result())
    const tell = vi.fn()
    const watch = createRunResultWatch({ load, tell })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 7 })
    await flushPromises()
    expect(tell).toHaveBeenCalledTimes(1)
    expect(tell.mock.calls[0][0]).toMatchObject({ idleReason: 'needs_intervention', nodes: [], continues: true })
    expect(tell.mock.calls[0][0].blocking).toHaveLength(1)
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 8 })      // read again: the same command and end
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(2)
    expect(tell).toHaveBeenCalledTimes(1)
  })

  it('F5: a new watch (the same load) with no arm reads nothing and tells nothing', async () => {
    const load = vi.fn(async () => result())
    const tell = vi.fn()
    const watch = createRunResultWatch({ load, tell })
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 7 })
    await flushPromises()
    expect(load).not.toHaveBeenCalled()
    expect(tell).not.toHaveBeenCalled()
  })

  it('a refused re-request start: the 1st window opens with the failure line (the real code and detail), no toast', async () => {
    const failed = result({
      state: 'failed', idle_reason: 'error', next_state: 'error', continues: false, blocking: [],
      failure: { node_key: 'gn_a', title: '작업 A', code: 'stage_failed', detail: 'start_refused:ai_disabled', at: null, role: 'review' },
    })
    const tell = vi.fn()
    const watch = createRunResultWatch({ load: vi.fn(async () => failed), tell })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 7 })
    await flushPromises()
    expect(watch.state.resultOpen).toBe(true)
    expect(tell).not.toHaveBeenCalled()
    expect(failureLine(t, te, failed)).toBe('작업 A: 단계를 진행하지 못했습니다 (start_refused:ai_disabled)')
  })
})

describe('the run band and the menus (F5 included)', () => {
  beforeEach(() => { i18n.global.locale.value = 'ko' })

  it('needs_intervention: the band reason is kept, tone warning; waiting_confirm stays neutral', () => {
    const facts = { status: 'idle', idleReason: 'needs_intervention' }
    expect(runBandView(facts)?.idleReason).toBe('needs_intervention')
    expect(runBandTone(runBandView(facts)!)).toBe('warning')
    expect(runBandTone(runBandView({ status: 'idle', idleReason: 'waiting_confirm' })!)).toBe('neutral')
    expect(runBandTone(runBandView({ ...facts })!)).toBe('warning')               // read again from the same run (F5)
  })

  it.each([['ko', '개입 필요', '확인 대기'], ['en', 'Intervention needed', 'Waiting for confirmation'], ['ja', '介入が必要', '確認待ち']] as const)(
    'the band words (%s)', (locale, intervention, confirm) => {
      i18n.global.locale.value = locale
      expect(t('gui.designer.top.run_idle_reasons.needs_intervention')).toBe(intervention)
      expect(t('gui.designer.top.run_idle_reasons.waiting_confirm')).toBe(confirm)
    })

  it('run.next and run.all stay open on an idle run waiting for intervention', () => {
    expect(isMenuCommandEnabled('run.next', 'idle', ALL_MENU_TARGETS)).toBe(true)
    expect(isMenuCommandEnabled('run.all', 'idle', ALL_MENU_TARGETS)).toBe(true)
  })
})