/**
 * flowgate.v02test.0042 — the card merge wire (features/document/api/cardMerge.ts): state, action body and the
 * notification items.
 */
import { describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: http.get, postRequest: http.post }))

import { cardMergeAct, cardMergeItems, cardMergePayload, cardMergeRead, toCardMergeState } from '@/features/document/api/cardMerge'

describe('card merge wire', () => {
  it('reads the state of one card', async () => {
    http.get.mockResolvedValue({ data: { flow_id: 'f', node_key: 'n', attempt_id: 'a', state: 'waiting', integration_status: 'pending', reason: 'session_busy',
      blocked_by: { node_key: 'n4', title: 'Four', why: 'conflict' }, files: [{ path: 'x.ts', resolved: true, kind: 'deleted_card' }, { path: 'y.ts', kind: 'weird' }, { bad: 1 }], session: { merge_id: 3, kind: 'graph_attempt_merge', review_state: 'open' },
      resolving: null, default_provider: { id: 'p1', name: 'P' }, actions: ['ai_resolve', 'unknown', 'merge', 'retry'],
      waiting_cards: [{ node_key: 'n2', title: 'Two' }, { bad: 1 }] } })
    await expect(cardMergeRead('f', 'n')).resolves.toEqual({
      flowId: 'f', nodeKey: 'n', attemptId: 'a', state: 'waiting', integrationStatus: 'pending', reason: 'session_busy',
      blockedBy: { nodeKey: 'n4', title: 'Four', why: 'conflict' },
      files: [{ path: 'x.ts', resolved: true, kind: 'deleted_card' }, { path: 'y.ts', resolved: false, kind: 'both' }],
      session: { mergeId: 3, kind: 'graph_attempt_merge', reviewState: 'open' }, resolving: null,
      defaultProvider: { id: 'p1', name: 'P' }, actions: ['ai_resolve', 'merge', 'retry'],
      waitingCards: [{ nodeKey: 'n2', title: 'Two', why: null }], startedRunId: null,
    })
    expect(http.get).toHaveBeenCalledWith('/api/v1/flows/f/nodes/n/merge')
  })

  it('an unknown state reads as hidden', () => {
    expect(toCardMergeState({ state: 'nope' }, 'f', 'n')).toMatchObject({ state: 'hidden', flowId: 'f', nodeKey: 'n', actions: [] })
  })

  it('posts the action in the server names', async () => {
    expect(cardMergePayload({ action: 'pick', expectedAttemptId: 'a', files: [{ path: 'x', side: 'latest' }], providerId: 'p' }))
      .toEqual({ action: 'pick', expected_attempt_id: 'a', files: [{ path: 'x', side: 'latest' }], provider_id: 'p' })
    expect(cardMergePayload({ action: 'ai_resolve', expectedAttemptId: 'a', providerId: null })).toEqual({ action: 'ai_resolve', expected_attempt_id: 'a' })
    expect(cardMergePayload({ action: 'discard_rerun', expectedAttemptId: 'a' })).toEqual({ action: 'discard_rerun', expected_attempt_id: 'a' })
    http.post.mockResolvedValue({ data: { state: 'catching_up', started_run_id: 'r1' } })
    await expect(cardMergeAct('f', 'n', { action: 'merge', expectedAttemptId: 'a' })).resolves.toMatchObject({ state: 'catching_up', startedRunId: 'r1' })
    expect(http.post).toHaveBeenCalledWith('/api/v1/flows/f/nodes/n/merge/actions', { action: 'merge', expected_attempt_id: 'a' })
  })

  it('reads the merge items, dropping hidden and malformed rows', async () => {
    http.get.mockResolvedValue({ data: { items: [
      { flow_id: 'f', flow_title: 'F', node_key: 'n4', title: 'Four', state: 'needs_choice', blocked_by: null },
      { flow_id: 'f', node_key: 'n2', title: 'Two', state: 'waiting', blocked_by: { node_key: 'n4', title: 'Four' } },
      { flow_id: 'f', node_key: 'n9', state: 'hidden' }, { node_key: 'x', state: 'failed' },
    ] } })
    await expect(cardMergeItems()).resolves.toEqual([
      { flowId: 'f', flowTitle: 'F', nodeKey: 'n4', title: 'Four', state: 'needs_choice', blockedBy: null },
      { flowId: 'f', flowTitle: null, nodeKey: 'n2', title: 'Two', state: 'waiting', blockedBy: { nodeKey: 'n4', title: 'Four', why: null } },
    ])
    expect(http.get).toHaveBeenCalledWith('/api/v1/merge-items')
  })
})
