/** flowgate.v02test.0068: a Flow tab's conflict mark goes when the Git panel saw the conflict end (세션 중단, a resolve,
 *  an abort) — with no server event at all. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, type EffectScope } from 'vue'
import type { FlowAiActivity, ProjectGitStatus } from '@/features/project'
import { refreshFlowTabConflicts, useFlowTabConflicts } from '@/features/designer/center/flowTabConflicts'

const IDLE: FlowAiActivity = { active: false, runId: null, status: null, actionScope: null, conflictProgress: null }

function gitStatus(groups: string[]): ProjectGitStatus {
  return {
    enabled: true, baseBranch: 'main', slots: [],
    pending: groups.map((groupId) => ({ groupId, status: 'conflict', mergeId: 553, acDocId: null, reviewState: null, finalApprovalBound: false, plan: null })),
    baseDirty: { files: [], mergeInProgress: false, aiRun: false },
    baseUntracked: { files: [], truncated: false, total: null },
  }
}

async function settle() {
  for (let i = 0; i < 8; i += 1) { await Promise.resolve(); await nextTick() }
}

let scope: EffectScope | null = null
afterEach(() => { scope?.stop(); scope = null })

describe('refreshFlowTabConflicts', () => {
  it('reads git/status again, so the mark of an aborted 받아오기 conflict goes without any event', async () => {
    const server = { status: gitStatus(['f86']) }
    const source = {
      loadStatus: vi.fn(async () => server.status),
      loadAiActivity: vi.fn(async () => IDLE),
      subscribe: vi.fn(() => () => {}),
    }
    scope = effectScope()
    const marks = scope.run(() => useFlowTabConflicts({ projectId: () => 'p', flows: () => ['f86', 'other'], graphConflicts: () => ({}), source }))!
    await settle()
    expect(marks.value).toEqual({ f86: { state: 'conflict' } })
    // 세션 중단: the server closed the session; no event reaches this screen.
    server.status = gitStatus([])
    await settle()
    expect(marks.value).toEqual({ f86: { state: 'conflict' } })
    const reads = source.loadStatus.mock.calls.length
    refreshFlowTabConflicts('f86')
    await settle()
    expect(source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(marks.value).toEqual({})
  })

  it('after the tabs are gone it reads nothing', async () => {
    const source = { loadStatus: vi.fn(async () => gitStatus([])), loadAiActivity: vi.fn(async () => IDLE), subscribe: vi.fn(() => () => {}) }
    const own = effectScope()
    own.run(() => useFlowTabConflicts({ projectId: () => 'p', flows: () => ['a'], graphConflicts: () => ({}), source }))
    await settle()
    own.stop()
    const reads = source.loadStatus.mock.calls.length
    refreshFlowTabConflicts()
    await settle()
    expect(source.loadStatus.mock.calls.length).toBe(reads)
  })
})
