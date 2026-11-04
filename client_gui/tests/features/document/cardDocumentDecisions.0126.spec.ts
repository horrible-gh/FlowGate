import { beforeEach, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: http.get, postRequest: http.post }))
import { cardDocumentDecisionsRead, cardDocumentDecisionWrite } from '@/features/document/api/cardDocumentDecisions'
beforeEach(() => vi.clearAllMocks())
it('uses the Graph decision routes with encoded identities and preserves the opaque CAS context', async () => {
  const current = { flow_id: 'f/1', node_key: 'gn a', context: { run_id: null, documents: { report: { body_event_id: 17 } } }, decision_version: 2, documents: {}, capabilities: {} }
  http.get.mockResolvedValue({ data: current })
  expect(await cardDocumentDecisionsRead('f/1', 'gn a')).toBe(current)
  expect(http.get).toHaveBeenCalledWith('/api/v1/flows/f%2F1/nodes/gn%20a/documents/decisions')
  const request = { action: 'approve' as const, doc_id: 'doc', expected_revision: 3, expected_decision_version: 2,
    expected_context: current.context, request_key: 'row:1' }
  http.post.mockResolvedValue({ data: { ...current, decision_version: 3 } })
  expect((await cardDocumentDecisionWrite('f/1', 'gn a', 'report', request)).decision_version).toBe(3)
  expect(http.post).toHaveBeenCalledWith('/api/v1/flows/f%2F1/nodes/gn%20a/documents/report/decision', request)
  expect(http.post.mock.calls[0][1]).not.toHaveProperty('actor_id')
})
it('throws server failures without manufacturing a successful decision', async () => {
  const failure = { response: { status: 409, data: { error: { code: 'document_decision_context_changed' } } } }
  http.post.mockRejectedValue(failure)
  await expect(cardDocumentDecisionWrite('f', 'n', 'report', { action: 'reject', doc_id: 'd', expected_revision: 1,
    expected_decision_version: 2, expected_context: {}, request_key: 'key' })).rejects.toBe(failure)
})
