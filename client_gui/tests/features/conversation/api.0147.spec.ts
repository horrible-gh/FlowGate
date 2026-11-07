import { beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), extractApiErrorMessage: vi.fn(() => '') }))
vi.mock('@shared/api', () => http)
import { fetchRun, fetchTurns, fetchChatRuns, postTurn } from '@/features/conversation/api'
import { createHttpConversationSource } from '@/features/conversation/conversationSource'
const A = 'p.m.0001.0002-CH'
beforeEach(() => { for (const fn of Object.values(http)) fn.mockReset() })

describe('0147 real HTTP DTO boundaries', () => {
  it.each(['run-7', null, undefined])('source_run_id=%s survives GET and POST turn adaptation', async (id) => {
    const wire = { seq: 2, speaker: 'ai', body: 'fixture', ...(id === undefined ? {} : { source_run_id: id }) }
    http.getRequest.mockResolvedValue({ data: { turns: [wire], head_seq: 2 } })
    expect((await fetchTurns(A, { afterSeq: 0, limit: 50 })).turns[0].sourceRunId).toBe(id ?? null)
    http.postRequest.mockResolvedValue({ data: { turn: wire, head_seq: 2 } })
    expect((await postTurn(A, { body: 'fixture', idempotencyKey: 'isolated', basedOnSeq: 1 })).turn?.sourceRunId).toBe(id ?? null)
  })

  it.each([
    ['complete', 0], ['none', 3], ['partial', null], ['future-value', 0], [null, null], [undefined, undefined],
  ])('preserves outcome=%s, exit_code=%s, and exact membership', async (outcome, exitCode) => {
    http.getRequest.mockResolvedValue({ data: { run_id: 'r1', status: 'finished', end_reason: 'exited', outcome,
      exit_code: exitCode, doc_ref: A, action_scope: 'chat', group_id: 'p.m.0001' } })
    const dto = await fetchRun('r1')
    expect(dto).toMatchObject({ runId: 'r1', status: 'finished', endReason: 'exited', outcome: outcome ?? null, exitCode: exitCode ?? null,
      docRef: A, actionScope: 'chat', groupId: 'p.m.0001' })
    expect(http.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r1')
  })

  it('does not disguise missing/unknown status or an explicit mismatched run id', async () => {
    http.getRequest.mockResolvedValue({ data: { run_id: 'other', status: 'future', outcome: 'future', exit_code: '0' } })
    expect(await fetchRun('r1')).toMatchObject({ runId: 'other', status: 'unknown', outcome: 'future', exitCode: null })
    http.getRequest.mockResolvedValue({ data: {} })
    expect(await fetchRun('r1')).toMatchObject({ status: 'unknown', outcome: null, exitCode: null, docRef: null })
  })

  it('preserves the real running wire without inventing its missing doc_ref or terminal fields', async () => {
    http.getRequest.mockResolvedValue({ data: { run_id: 'r1', status: 'running', action_scope: 'chat', group_id: 'p.m.0001' } })
    expect(await fetchRun('r1')).toMatchObject({ runId: 'r1', status: 'running', actionScope: 'chat', groupId: 'p.m.0001',
      docRef: null, outcome: null, exitCode: null, endReason: null })
  })

  it('keeps remote diagnostic cause/message/workdir on actual failure', async () => {
    const remote = { cause: 'workdir_missing', workdir: '/isolated/job', message: 'isolated workdir missing' }
    http.getRequest.mockResolvedValue({ data: { run_id: 'r1', status: 'finished', end_reason: 'all_providers_failed',
      doc_ref: A, outcome: 'none', exit_code: null, fallback_history: [{ detail: 'isolated failure', remote_final: remote }] } })
    expect((await fetchRun('r1')).failure).toEqual({ ...remote, detail: 'isolated failure' })
  })

  it('connects the finite existing list API without assuming list action_scope or exit_code', async () => {
    http.getRequest.mockResolvedValue({ data: { items: [{ run_id: 'r1', doc_ref: A }, { run_id: 'foreign', doc_ref: 'other' }], total: 3, has_more: true } })
    const source = createHttpConversationSource()
    expect(await source.listRuns({ projectId: 'p', groupId: 'p.m.0001' }, 500)).toEqual({
      items: [{ runId: 'r1', docRef: A }, { runId: 'foreign', docRef: 'other' }], total: 3, hasMore: true,
    })
    expect(http.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/runs', { group_id: 'p.m.0001', limit: 100 })
  })

  it('incomplete/malformed list fields do not mean history absent', async () => {
    http.getRequest.mockResolvedValue({ data: {} })
    expect(await fetchChatRuns({ projectId: 'p', groupId: 'p.m.0001' }, 20)).toEqual({ items: [], total: null, hasMore: true })
    http.getRequest.mockResolvedValue({ data: { items: [{}, { run_id: 'r1', doc_ref: A }], total: 2, has_more: false } })
    expect((await fetchChatRuns({ projectId: 'p', groupId: 'p.m.0001' }, 20)).hasMore).toBe(true)
  })
})
