/**
 * flowgate.v02test.0051 C — the card of a review loop that waits for an AI's question: 질문 답변 대기 (not 실행 중 / 반려됨),
 * on the server's word only (`node_running_cause = question_wait:<run>`, or the question's `loop_wait` phase), and the [Q]
 * mark's tooltip tells the phase and the responder's failure reason. The wire reads (run read, Canvas) keep both fields.
 */
import { shallowMount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: vi.fn(), extractApiErrorMessage: (_e: unknown, fallback: string) => fallback }))

import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { cardStateView, type CardRunFacts } from '@/features/designer/center/graph/cardStateView'
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
import { toQuestionWait } from '@/features/project/flowCanvasApi'
import { toFlowRun } from '@/features/project/flowRunApi'

const run = (status: string | null, fields: Partial<CardRunFacts> = {}): CardRunFacts => ({ status, ...fields })
const row = (facts: CardRunFacts, reviewStatus?: string) => {
  const view = cardStateView(facts, reviewStatus)
  return view ? [view.key, view.phase, view.tone] : null
}
const PARKED = { question: true, nodeRunning: true, nodeRunningCause: 'question_wait:air_1', runningExecutionCount: 1, requestedExecutionCount: 0 }
const WAITING = ['waiting_answer', 'awaiting', 'neutral']

describe('a parked review loop reads 질문 답변 대기', () => {
  it.each([
    ['open: the cause says the loop waits (the running lock stays)', run('open', { stage: 'result', ...PARKED }), WAITING],
    ['review: not awaiting approval, still locked', run('review', { awaitingApproval: false, ...PARKED }), WAITING],
    ['rejected by the review, then parked for the question', run('rejected', PARKED), WAITING],
    ['the loop_wait phase of a question of the card', run('open', { stage: 'result', question: true, nodeRunning: true, questionWait: [{ phase: 'loop_wait' }] }), WAITING],
  ] as const)('%s', (_label, facts, expected) => {
    expect(row(facts)).toEqual(expected)
  })

  it('it outranks the document review\'s 반려됨 / 수정됨 chip', () => {
    expect(row(run('open', { stage: 'result', ...PARKED }), 'rejected')).toEqual(WAITING)
    expect(row(run('review', { awaitingApproval: false, ...PARKED }), 'revised')).toEqual(WAITING)
  })

  it('it is not approvable', () => {
    expect(cardStateView(run('open', PARKED))!.approvable).toBe(false)
  })

  it.each([
    ['a running review loop (another cause) stays 작업 중', run('open', { stage: 'result', question: true, nodeRunning: true, nodeRunningCause: 'review_loop:air_1', runningExecutionCount: 1 }), ['writing_result', 'working', 'info']],
    ['a running AI stays 결과 작성 중', run('open', { stage: 'result', nodeRunning: true, nodeRunningCause: 'graph_exec:e1', runningExecutionCount: 1 }), ['writing_result', 'working', 'info']],
    ['a plain pending question phase is no loop wait', run('open', { stage: 'result', question: true, nodeRunning: true, runningExecutionCount: 1, questionWait: [{ phase: null }] }), ['writing_result', 'working', 'info']],
    ['a loop_wait phase of a card no longer asking is no wait', run('open', { stage: 'result', question: false, nodeRunning: true, runningExecutionCount: 1, questionWait: [{ phase: 'loop_wait' }] }), ['writing_result', 'working', 'info']],
    ['a system error outranks the wait', run('open', { ...PARKED, intervention: { kind: 'system_error', reason: 'x' } }), ['system_error', 'other', 'danger']],
    ['an approved card is never turned back to a wait', run('done', PARKED), ['approved', 'approved', 'success']],
    ['no cause sent (an older server): as before', run('open', { stage: 'result', question: true, nodeRunning: true, runningExecutionCount: 1 }), ['writing_result', 'working', 'info']],
  ] as const)('%s', (_label, facts, expected) => {
    expect(row(facts)).toEqual(expected)
  })

  it('without the wait, the review chip still shows for a reviewed card', () => {
    expect(row(run('open', { stage: 'result', nodeRunning: false, runningExecutionCount: 0 }), 'rejected')).toEqual(['rejected', 'other', 'warning'])
  })

  it('the label is 질문 답변 대기 in ko and has its own word in en / ja', () => {
    expect(ko.designer.center.card_states.waiting_answer).toBe('질문 답변 대기')
    expect(en.designer.center.card_states.waiting_answer).toMatch(/question/i)
    expect(ja.designer.center.card_states.waiting_answer).toContain('質問')
  })
})

describe('the wire keeps the loop wait', () => {
  it('toQuestionWait keeps phase / wait status / resume stage / waited_at only when the server sent a phase', () => {
    expect(toQuestionWait([
      { seq: 3, state: 'pending', code: null, message: null, phase: 'loop_wait', wait_status: 'open', resume_stage: 'rework', waited_at: '2026-11-20T01:00:00Z' },
      { seq: 4, state: 'failed', code: 'no_independent_responder', message: 'none' },
    ])).toEqual([
      { seq: 3, state: 'pending', code: null, message: null, phase: 'loop_wait', waitStatus: 'open', resumeStage: 'rework', waitedAt: '2026-11-20T01:00:00Z' },
      { seq: 4, state: 'failed', code: 'no_independent_responder', message: 'none' },
    ])
  })

  it('a run node keeps node_running_cause only when sent', () => {
    const raw = { run_id: 'r1', flow_id: 'f', status: 'running', node_runs: [
      { node_key: 'a', status: 'open', node_running: true, node_running_cause: 'question_wait:air_1' },
      { node_key: 'b', status: 'open', node_running: true },
      { node_key: 'c', status: 'open', node_running: false, node_running_cause: null },
    ] }
    const nodes = toFlowRun(raw)!.nodeRuns!
    expect(nodes[0]).toMatchObject({ nodeRunning: true, nodeRunningCause: 'question_wait:air_1' })
    expect('nodeRunningCause' in nodes[1]).toBe(false)
    expect(nodes[2]).toMatchObject({ nodeRunning: false, nodeRunningCause: null })
  })

  it('the running lock itself is the server\'s: a parked card is still nodeRunning (editing stays closed)', () => {
    const [node] = toFlowRun({ run_id: 'r1', flow_id: 'f', status: 'running', node_runs: [{ node_key: 'a', status: 'open', node_running: true, node_running_cause: 'question_wait:air_1' }] })!.nodeRuns!
    expect(node.nodeRunning).toBe(true)
  })
})

describe('the [Q] mark tells the phase and the failure reason', () => {
  const mountCard = (questionWait: unknown[]) => {
    const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko: { gui: { inspector: { qna: qnaMessages.ko } } } } })
    return shallowMount(FlowNodeCard, {
      props: { id: 'b', data: { kind: 'task', title: 'B', state: null, selected: false, question: true, questionWait } as never },
      global: { plugins: [i18n], stubs: { Handle: true } },
    })
  }

  it('a loop parked for an answer says 질문 답변 대기; a responder failure keeps its code and message', () => {
    const wrapper = mountCard([
      { seq: 3, state: 'pending', code: null, message: null, phase: 'loop_wait' },
      { seq: 4, state: 'failed', code: 'no_independent_responder', message: '독립 답변 AI 없음', phase: 'loop_wait' },
    ])
    const title = wrapper.get('[data-test="designer-canvas-node-question-icon"]').attributes('title')!
    const [first, second] = title.split('\n')
    expect(first).toBe('Q3 · 질문 답변 대기')
    expect(second).toBe('Q4 · 질문 답변 대기 · 자동 답변 실패 · no_independent_responder · 독립 답변 AI 없음')
    wrapper.unmount()
  })

  it('without a loop wait the tooltip is as before (state, code, message)', () => {
    const wrapper = mountCard([{ seq: 5, state: 'dispatched', code: null, message: null }])
    expect(wrapper.get('[data-test="designer-canvas-node-question-icon"]').attributes('title')).toBe('Q5 · AI 답변 중')
    wrapper.unmount()
  })
})
