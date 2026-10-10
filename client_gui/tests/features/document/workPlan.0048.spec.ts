/**
 * flowgate.v02.0048 T#13 — the WP summary's call in features/document/api/workPlan.ts (D#1 §6.3, §12.1;
 * NR0003 §2.4): the request sent to the existing v0.1 endpoint (GET /documents/{doc_id}/work-plan, nothing
 * else), the server's snake_case read view (body steps, step_execution_status, assignment_summary, totals,
 * editability) -> camelCase, and how a refusal is read — the top-level 409 wp_unreadable, FastAPI `detail`
 * 403 / 404 / 422, a 500 and no answer.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
const putRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
  putRequest: (...args: unknown[]) => putRequest(...args),
  patchRequest: vi.fn(),
}))

import { toWorkPlanView, workPlanErrorOf, workPlanRead, type WorkPlanErrorKind } from '@/features/document'
import { WP_DOC, pairRows, stepRow, workPlanDocument } from '../designer/inspector/workPlan/workPlanFixtures'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
})

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

describe('workPlanRead (flowgate.v02.0048 T#13)', () => {
  it('GET /api/v1/documents/{doc_id}/work-plan once, doc_id encoded; no write call', async () => {
    getRequest.mockResolvedValue({ data: workPlanDocument() })
    const view = await workPlanRead('a b/WP')
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/a%20b%2FWP/work-plan')
    expect(postRequest).not.toHaveBeenCalled()
    expect(putRequest).not.toHaveBeenCalled()
    expect(view.docId).toBe(WP_DOC)
  })

  it('a refusal is thrown as it is', async () => {
    const refusal = axiosError(403, { detail: 'Permission denied' })
    getRequest.mockRejectedValue(refusal)
    await expect(workPlanRead(WP_DOC)).rejects.toBe(refusal)
  })
})

describe('toWorkPlanView (flowgate.v02.0048 T#13)', () => {
  it('the read view: revision, totals, editability, assignment, Steps with card, pair, provider and started', () => {
    const view = toWorkPlanView(workPlanDocument({}, ['D#1', 'T#1']))
    expect(view).toMatchObject({
      docId: WP_DOC,
      title: '작업계획 — 설계 2장 · 작업 2세트',
      groupId: 'flowgate.v02.0048',
      revisionNo: 3,
      docReviewStatus: 'approved',
      editable: true,
      editLockedReason: null,
      totals: { designSheets: 2, workSets: 2, steps: 6 },
      unassignedSteps: 1,
      assignments: [
        { providerId: 'claude', name: 'Claude', stepCount: 3 },
        { providerId: 'codex', name: 'Codex', stepCount: 2 },
      ],
    })
    expect(view.steps.map((step) => [step.key, step.cardId, step.pairRole, step.started])).toEqual([
      ['D#1', 'D#1', 'single', true],
      ['D#2', 'D#2', 'single', false],
      ['T#1', 'T#1', 'instruction', true],
      ['TR#1', 'T#1', 'result', true],
      ['T#2', 'T#2', 'instruction', false],
      ['TR#2', 'T#2', 'result', false],
    ])
    // The current registered name wins over the plan's snapshot name; unassigned has no name.
    expect(view.steps[0]).toMatchObject({ providerId: 'claude', providerName: 'Claude', type: 'D', ordinal: 1, reviewCount: 0, locked: false })
    expect(view.steps[1]).toMatchObject({ providerId: null, providerName: null })
    expect(view.steps[2]).toMatchObject({ providerName: 'Codex', reviewCount: 1, pairKey: 'TR#1' })
  })

  it('provider name falls back to the snapshot, then the id', () => {
    const view = toWorkPlanView(
      workPlanDocument({
        steps: [stepRow('D', 1, { provider_id: 'gone', provider_display_name: 'Old name' }), stepRow('D', 2, { provider_id: 'bare' })],
        assignment_summary: [],
      }),
    )
    expect(view.steps.map((step) => step.providerName)).toEqual(['Old name', 'bare'])
  })

  it('a Step without a stored card_id gets the legacy card id (a result Step joins its instruction)', () => {
    const [instruction, result] = pairRows('N', 'NR', 1)
    delete instruction.card_id
    delete result.card_id
    const view = toWorkPlanView(workPlanDocument({ steps: [instruction, result] }))
    expect(view.steps.map((step) => step.cardId)).toEqual(['N#1', 'N#1'])
  })

  it('a locked plan keeps its reason; an editable one drops a stray reason', () => {
    expect(toWorkPlanView(workPlanDocument({ editable: false, edit_locked_reason: 'final_approved' }))).toMatchObject({
      editable: false,
      editLockedReason: 'final_approved',
    })
    expect(toWorkPlanView(workPlanDocument({ editable: false, edit_locked_reason: 'status' })).editLockedReason).toBe('status')
    expect(toWorkPlanView(workPlanDocument({ editable: true, edit_locked_reason: 'status' })).editLockedReason).toBeNull()
  })

  it('a server-assembled Step is locked; Steps without a key are skipped', () => {
    const view = toWorkPlanView(
      workPlanDocument({ steps: [stepRow('AC', 1, { locked: true, locked_reason: 'server_assembled', origin: 'system' }), { type: 'D' }] }),
    )
    expect(view.steps).toHaveLength(1)
    expect(view.steps[0]).toMatchObject({ key: 'AC#1', locked: true, started: false })
  })

  it('a thin or odd answer: zeros and empty lists, no throw', () => {
    expect(toWorkPlanView({ doc_id: WP_DOC })).toEqual({
      docId: WP_DOC,
      title: null,
      groupId: null,
      revisionNo: 0,
      docReviewStatus: null,
      editable: false,
      editLockedReason: null,
      totals: { designSheets: 0, workSets: 0, steps: 0 },
      steps: [],
      assignments: [],
      unassignedSteps: 0,
    })
    expect(toWorkPlanView(null).docId).toBe('')
    const odd = toWorkPlanView({ doc_id: WP_DOC, revision_no: -1, totals: { steps: 'x' }, body: { steps: 'x' }, step_execution_status: 'x' })
    expect(odd.revisionNo).toBe(0)
    expect(odd.steps).toEqual([])
  })
})

describe('workPlanErrorOf (flowgate.v02.0048 T#13)', () => {
  it.each<[string, unknown, WorkPlanErrorKind, number | null, string | null, number | null]>([
    ['409 wp_unreadable (top-level code)', axiosError(409, { code: 'wp_unreadable', reason: 'not_json', revision_no: 4, revisions: [] }), 'unreadable', 409, 'wp_unreadable', 4],
    ['403 perm_document_read', axiosError(403, { detail: 'Permission denied' }), 'forbidden', 403, null, null],
    ['404 detail', axiosError(404, { detail: `Document not found: ${WP_DOC}` }), 'not_found', 404, null, null],
    ['422 not a WP', axiosError(422, { detail: `${WP_DOC} is not a work plan document.` }), 'invalid', 422, null, null],
    ['409 other', axiosError(409, { detail: { code: 'busy' } }), 'other', 409, 'busy', null],
    ['500', axiosError(500, { detail: 'boom' }), 'other', 500, null, null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null, null],
    ['not an http error', new Error('x'), 'other', null, null, null],
  ])('%s', (_name, error, kind, status, code, revisionNo) => {
    expect(workPlanErrorOf(error)).toEqual({ kind, status, code, revisionNo })
  })
})
