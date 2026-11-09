// flowgate.v02.0156 T#4 D7 (0011-T W3): a 다음 단계 / 전체 실행 command on a completed run with no new definition is only
// acknowledged by the server. The person is told why nothing happened (ko / en / ja), once per command — the notice is raised
// in the command's own answer (never from a run observation), so a closed notice does not come back by itself.
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import { isCompletedNoop, type AckRun } from '@/features/designer/center/run/completedCommandAck'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunCommand, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'

const done: AckRun = { runId: 'run_1', status: 'idle', idleReason: 'completed' }

describe('0156 D7: which command is only acknowledged', () => {
  it('is told for a taken command that leaves the same completed idle run', () => {
    expect(isCompletedNoop(done, { ...done }, true)).toBe(true)
  })

  it('is told when the run stays on the same revision', () => {
    expect(isCompletedNoop({ ...done, revisionNo: 2 }, { ...done, revisionNo: 2 }, true)).toBe(true)
  })

  it.each([
    ['not taken', done, done, false],
    ['no run before', null, done, true],
    ['run unknown after', done, null, true],
    ['a run that was not completed', { ...done, idleReason: 'error' }, done, true],
    ['a new definition went on (running)', done, { ...done, status: 'running', idleReason: null }, true],
    ['an idle run of another reason after', done, { ...done, idleReason: 'step_done' }, true],
    ['another run', done, { ...done, runId: 'run_2' }, true],
    ['a new revision was adopted (1 -> 2) and the run is completed again', { ...done, revisionNo: 1 }, { ...done, revisionNo: 2 }, true],
    ['the revision is known only after the command', done, { ...done, revisionNo: 2 }, true],
    ['the revision is known only before the command', { ...done, revisionNo: 1 }, done, true],
  ] as const)('is not told when %s', (_name, before, after, taken) => {
    expect(isCompletedNoop(before as AckRun | null, after as AckRun | null, taken)).toBe(false)
  })
})

describe('0156 D7: the notice words exist in ko / en / ja and say why and what to do', () => {
  const words = (messages: any) => messages.designer.top.run_completed_ack as string
  it('has all three, each different', () => {
    const [k, e, j] = [words(ko), words(en), words(ja)]
    expect(new Set([k, e, j]).size).toBe(3)
    expect(k).toContain('이미 완료')
    expect(k).toContain('새 구성을 확정')
    expect(e).toContain('already completed')
    expect(e).toContain('new definition')
    expect(j).toContain('完了')
    expect(j).toContain('新しい構成を確定')
  })
})

describe('0156 D1: the blocked-adoption sentence names the real menu item of a completed card', () => {
  it.each([['ko', ko], ['en', en], ['ja', ja]] as const)('[%s] quotes the Time Machine menu label', (_name, messages: any) => {
    const label = messages.designer.center.card_menu.time_machine as string      // the label flowCardMenu's `time_machine` item shows
    expect(messages.designer.top.run_errors.graph_revision_migration_blocked).toContain(label)
  })
})

// The session's own answer of a command on a completed run: what the Shell reads after `runFlow` resolves.
const P = 'test2'
const F = 'test2.default.0156'
const A = 'gn_a'
const card = (id: string): FlowGraphNode => ({
  id, kind: 'task', title: '작업', x: 0, y: 0, movable: true, structureEditable: true,
  definition: { nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '작업', assignee: null },
} as FlowGraphNode)

function server(answer: FlowRunInfo) {
  const calls: string[] = []
  const completed: FlowRunInfo = { runId: 'run_1', status: 'idle', idleReason: 'completed', openNodeKeys: [], lastEventSeq: 5, revisionNo: 1 }
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => ({
      flowId: F, ownership: 'graph', viewMode: 'revision',
      draft: null, nodes: [card(A)], edges: [],
    } as unknown as FlowGraph)),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async () => ({ version: 1, keyMap: {} })),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => { throw new Error('no draft to confirm') }),
    loadRun: vi.fn(async () => completed),
    startRun: vi.fn(async (_p: string, _f: string, mode?: FlowRunCommand): Promise<FlowRunInfo> => { calls.push(`run ${mode}`); return answer }),
    stopRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'stopping', openNodeKeys: [] })),
    resumeRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'running', openNodeKeys: [] })),
  }
  return { source, calls }
}

describe("0156 D7: the Shell reads the notice from the command's own answer", () => {
  let center: DesignerCenterSession | null = null
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { center?.dispose(); center = null; vi.useRealTimers() })

  async function open(source: FlowGraphSource) {
    center = createDesignerCenterSession({ source, draftSaveDelayMs: 50, layoutSaveDelayMs: 50 })
    center.setProject(P)
    center.openFlow({ projectId: P, flowId: F, flowName: 'done' })
    await vi.runAllTimersAsync()
    return center
  }
  const ack = (session: DesignerCenterSession, before: AckRun | null, taken: boolean) => {
    const after = session.runStatus(F).run
    return isCompletedNoop(before, after ? { runId: after.runId, status: after.status, idleReason: after.idleReason ?? null, revisionNo: after.revisionNo ?? null } : null, taken)
  }

  it.each([['next', 'step'], ['all', 'full']] as const)('[%s] on a completed run the server only acknowledges: the notice is told', async (mode, command) => {
    const { source, calls } = server({ runId: 'run_1', status: 'idle', idleReason: 'completed', openNodeKeys: [], lastEventSeq: 6, revisionNo: 1 })
    const session = await open(source)
    const known = session.runStatus(F).run
    expect(known).toMatchObject({ status: 'idle', idleReason: 'completed' })
    const before: AckRun = { runId: known!.runId, status: known!.status, idleReason: known!.idleReason ?? null, revisionNo: known!.revisionNo ?? null }
    const taken = await session.runFlow(F, mode)
    expect(calls).toContain(`run ${command}`)
    expect(taken).toBe(true)
    expect(ack(session, before, taken)).toBe(true)
  })

  // The server adopts a confirmed new definition (revision_no 1 -> 2) before it handles the command; when every card of it is
  // already done the answer is idle `completed` again — but the command did something, so no "nothing happened" notice.
  it.each([['next', 'step'], ['all', 'full']] as const)('[%s] a command that adopted a new revision but ends completed tells nothing', async (mode, command) => {
    const { source, calls } = server({ runId: 'run_1', status: 'idle', idleReason: 'completed', openNodeKeys: [], lastEventSeq: 6, revisionNo: 2 })
    const session = await open(source)
    const known = session.runStatus(F).run!
    expect(known.revisionNo).toBe(1)
    const before: AckRun = { runId: known.runId, status: known.status, idleReason: known.idleReason ?? null, revisionNo: known.revisionNo ?? null }
    const taken = await session.runFlow(F, mode)
    expect(calls).toContain(`run ${command}`)
    expect(taken).toBe(true)
    expect(session.runStatus(F).run).toMatchObject({ status: 'idle', idleReason: 'completed', revisionNo: 2 })
    expect(ack(session, before, taken)).toBe(false)
  })

  it('a command that really went on (the run is running after it) tells nothing', async () => {
    const { source } = server({ runId: 'run_1', status: 'running', openNodeKeys: [A], lastEventSeq: 6, revisionNo: 1 })
    const session = await open(source)
    const known = session.runStatus(F).run!
    const before: AckRun = { runId: known.runId, status: known.status, idleReason: known.idleReason ?? null, revisionNo: known.revisionNo ?? null }
    const taken = await session.runFlow(F, 'next')
    expect(ack(session, before, taken)).toBe(false)
  })
})

describe('0156 D7: the Shell tells it once per command, from nowhere else', () => {
  const shell = readFileSync('src/features/designer/shell/DesignerShell.vue', 'utf8')
  it('raises the notice only in the run.next / run.all command answer', () => {
    expect(shell.match(/gui\.designer\.top\.run_completed_ack/g)?.length).toBe(1)
    expect(shell.match(/isCompletedNoop\(/g)?.length).toBe(1)
    const at = shell.indexOf('isCompletedNoop(')
    expect(shell.lastIndexOf("case 'run.next':", at)).toBeGreaterThan(shell.lastIndexOf("case 'run.pause':", at))
    expect(shell.slice(shell.lastIndexOf('void center.runFlow(', at), at)).toContain('.then((taken)')
  })
})
