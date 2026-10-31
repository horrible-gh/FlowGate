/**
 * flowgate.v02.0113 T#1 (0030-M §4, 0002-D 개정 §3.11 표 교체 · §3.13 신설) — on screen, a card is running only when the
 * server says it actually runs (`node_running`: an AI execution of it requested / running, or its AI review):
 * - the chip: an open card the server says is not running waits (작성 대기 / 응답 대기, or its 0113 wait — 지시서 / 결과
 *   작성 대기 by the writing role, AI 설정 필요, AI 사용 불가); a review card neither running nor awaiting approval waits (대기);
 *   only the running states carry ◌; pending is 준비, done 완료. Without `node_running` the 0083 counts decide as before.
 * - the running line into a node follows `node_running` when sent (a waiting open node is reached, not running).
 * - every new chip label exists in ko / en / ja.
 */
import { describe, expect, it } from 'vitest'
import { CARD_STATE_KEYS, cardStateView, type CardRunFacts } from '@/features/designer/center/graph/cardStateView'
import { activeNodeIds } from '@/features/designer/center/run/flowRunLock'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'

const run = (status: string, fields: Partial<CardRunFacts> = {}): CardRunFacts => ({ status, ...fields })
const row = (facts: CardRunFacts) => {
  const view = cardStateView(facts)!
  return [view.key, view.phase, view.tone, view.labelKey.replace('gui.designer.center.card_states.', '')]
}

describe('the card chip follows node_running (flowgate.v02.0113 T#1)', () => {
  it.each([
    // node run                                                                              key · phase · tone · label
    ['test3.test.0004: open, manual write wait of the instruction, nothing runs',
      run('open', { stage: 'instruction', nodeRunning: false, runningExecutionCount: 0, requestedExecutionCount: 0,
        waitReason: { code: 'manual_write_wait', role: 'instruction' } }),
      ['manual_write_wait', 'awaiting', 'neutral', 'instruction_write_wait']],
    ['open, manual write wait of the result',
      run('open', { stage: 'result', nodeRunning: false, waitReason: { code: 'manual_write_wait', role: 'execution' } }),
      ['manual_write_wait', 'awaiting', 'neutral', 'result_write_wait']],
    ['open, manual write wait of another role keeps 수동 작성 대기',
      run('open', { nodeRunning: false, waitReason: { code: 'manual_write_wait', role: 'review' } }),
      ['manual_write_wait', 'awaiting', 'neutral', 'manual_write_wait']],
    ['open, AI setup required', run('open', { nodeRunning: false, waitReason: { code: 'ai_setup_required', role: 'execution' } }),
      ['ai_setup_required', 'awaiting', 'warning', 'ai_setup_required']],
    ['open, AI unavailable', run('open', { nodeRunning: false, waitReason: { code: 'ai_unavailable', role: 'execution' } }),
      ['ai_unavailable', 'awaiting', 'danger', 'ai_unavailable']],
    ['open, not running though old counts say requested', run('open', { stage: 'result', nodeRunning: false, requestedExecutionCount: 1 }),
      ['waiting_writer', 'awaiting', 'neutral', 'waiting_writer']],
    ['open, its AI runs', run('open', { stage: 'result', nodeRunning: true }), ['writing_result', 'working', 'info', 'writing_result']],
    ['review, its AI review works', run('review', { awaitingApproval: false, nodeRunning: true }), ['working', 'working', 'info', 'working']],
    ['review, nothing works and no approval wait', run('review', { awaitingApproval: false, nodeRunning: false }),
      ['waiting', 'awaiting', 'neutral', 'waiting']],
    ['review, awaiting approval', run('review', { awaitingApproval: true, nodeRunning: false }),
      ['awaiting_approval', 'awaiting', 'warning', 'awaiting_approval']],
    ['done', run('done', { nodeRunning: false }), ['approved', 'approved', 'success', 'approved']],
    ['pending', run('pending'), ['pending', 'other', 'neutral', 'pending']],
    ['an older server (no node_running): the counts decide', run('open', { stage: 'result', runningExecutionCount: 1 }),
      ['writing_result', 'working', 'info', 'writing_result']],
  ] as const)('%s', (_label, facts, expected) => {
    expect(row(facts)).toEqual(expected)
  })

  it('only the running states carry ◌; pending is 준비, done 완료 (ko / en / ja)', () => {
    for (const messages of [ko, en, ja]) {
      const states = messages.designer.center.card_states as Record<string, string>
      expect(Object.keys(states).sort()).toEqual([...CARD_STATE_KEYS].sort())
      for (const key of ['working', 'writing_instruction', 'writing_result']) expect(states[key].startsWith('◌ '), key).toBe(true)
      for (const key of CARD_STATE_KEYS.filter((k) => !['working', 'writing_instruction', 'writing_result'].includes(k))) {
        expect(states[key].includes('◌'), key).toBe(false)
      }
    }
    expect(ko.designer.center.card_states).toMatchObject({
      pending: '준비', instruction_write_wait: '지시서 작성 대기', result_write_wait: '결과 작성 대기', waiting: '대기',
      manual_write_wait: '수동 작성 대기', working: '◌ 실행 중', approved: '완료',
    })
  })
})

describe('the running line follows node_running (flowgate.v02.0113 T#1)', () => {
  const graph = (nodeRunning?: boolean): FlowGraph => ({
    flowId: 'f',
    nodes: [{ id: 'n', kind: 'task', title: '', x: 0, y: 0,
      nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, ...(nodeRunning === undefined ? {} : { nodeRunning }) } }],
    edges: [],
  } as unknown as FlowGraph)

  it('a waiting open node is no running node; a running one is; an older server keeps open = running', () => {
    expect(activeNodeIds(graph(false)).has('n')).toBe(false)
    expect(activeNodeIds(graph(true)).has('n')).toBe(true)
    expect(activeNodeIds(graph()).has('n')).toBe(true)
  })
})
