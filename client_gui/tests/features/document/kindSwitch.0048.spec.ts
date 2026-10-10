/**
 * flowgate.v02.0048 T#10 — the kind switch's call in features/document/api/kindSwitch.ts (D#1 §6.3, §12.1;
 * D#2 §3.3 / §3.4): the request sent to the existing v0.1 converter (PATCH /documents/{doc_id}/root-type),
 * its answer -> camelCase (the new identity, or the same row when the type was already asked), and how
 * a refusal is read — the converter's / guards' detail strings, the mutation policy envelope and the
 * engine router contact's flat 409 (`flow_owned_by_graph` / `flow_ownership_conflict`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

import { kindSwitchConvert, kindSwitchErrorOf, toKindSwitchResult, type KindSwitchErrorKind } from '@/features/document'

const FLOW = 'flowgate.v02.0050'
const ROOT_R = `${FLOW}.0001-R`
const ROOT_B = `${FLOW}.0001-B`

/** The converter's answer row (the documents row), shortened. */
const row = (docId: string, typeCode: string) => ({
  id: 77,
  doc_id: docId,
  project_id: 'flowgate',
  group_id: FLOW,
  type_code: typeCode,
  seq: 1,
  title: '새 요구사항',
  status: 'draft',
  doc_review_status: null,
  filename: `0001-${typeCode}_document.md`,
  updated_at: '2026-10-09T21:00:00+09:00',
})

beforeEach(() => {
  patchRequest.mockReset()
})

describe('kindSwitchConvert — PATCH /documents/{doc_id}/root-type', () => {
  it('sends the new type to the existing converter and reads the new identity', async () => {
    patchRequest.mockResolvedValue({ data: { data: row(ROOT_B, 'B') } })
    const result = await kindSwitchConvert(ROOT_R, 'B')
    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(patchRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(ROOT_R)}/root-type`, { new_type: 'B' })
    expect(result.previousDocId).toBe(ROOT_R)
    expect(result.renamed).toBe(true)
    expect(result.document).toMatchObject({ docId: ROOT_B, typeCode: 'B', groupId: FLOW, projectId: 'flowgate', seq: 1, title: '새 요구사항' })
  })

  it('B -> R the same way', async () => {
    patchRequest.mockResolvedValue({ data: { data: row(ROOT_R, 'R') } })
    const result = await kindSwitchConvert(ROOT_B, 'R')
    expect(patchRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(ROOT_B)}/root-type`, { new_type: 'R' })
    expect(result).toMatchObject({ previousDocId: ROOT_B, renamed: true, document: { docId: ROOT_R, typeCode: 'R' } })
  })

  it('a root that already had the asked type comes back as it is (not renamed)', () => {
    expect(toKindSwitchResult({ data: row(ROOT_R, 'R') }, ROOT_R)).toMatchObject({ renamed: false, document: { docId: ROOT_R } })
  })

  it('an answer without a document is an error, a transport error is thrown as it is', async () => {
    expect(() => toKindSwitchResult({ data: {} }, ROOT_R)).toThrow()
    const refusal = { isAxiosError: true, response: { status: 409, data: { error: 'flow_owned_by_graph' } } }
    patchRequest.mockRejectedValue(refusal)
    await expect(kindSwitchConvert(ROOT_R, 'B')).rejects.toBe(refusal)
  })
})

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

describe('kindSwitchErrorOf — what a refused switch means', () => {
  const rows: readonly [string, unknown, KindSwitchErrorKind, number | null, string | null][] = [
    // the engine router contact (D#2 §3.3 C): flat v0.1 body
    ['409 flow_owned_by_graph (Graph Flow)', axiosError(409, { error: 'flow_owned_by_graph', doc_id: ROOT_R, group_id: FLOW, graph_revision_no: 1 }), 'graph', 409, 'flow_owned_by_graph'],
    ['409 flow_ownership_conflict', axiosError(409, { error: 'flow_ownership_conflict', doc_id: ROOT_R, group_id: FLOW, graph_revision_no: 1 }), 'ownership_conflict', 409, 'flow_ownership_conflict'],
    // the converter (document_service.convert_root_document_type)
    ['409 decision taken (a v0.1 sequence)', axiosError(409, { detail: 'Cannot convert root type after the workflow decision was taken.' }), 'decided', 409, null],
    ['409 child documents', axiosError(409, { detail: 'Cannot convert root type: child documents already reference it.' }), 'children', 409, null],
    ['409 target id exists', axiosError(409, { detail: `Target doc_id already exists: ${ROOT_B}.` }), 'collision', 409, null],
    ['409 conversion failed', axiosError(409, { detail: 'Conversion failed: boom' }), 'conflict', 409, null],
    ['422 not a root', axiosError(422, { detail: `Only workflow root documents (R/B) can be converted; ${FLOW}.0002-N is N.` }), 'invalid', 422, null],
    ['422 body validation', axiosError(422, { detail: [{ loc: ['body', 'new_type'], msg: 'bad' }] }), 'invalid', 422, null],
    ['404', axiosError(404, { detail: `Document not found: ${ROOT_R}` }), 'not_found', 404, null],
    // the route's guards
    ['409 group disposed', axiosError(409, { detail: 'Modification not allowed: the group has been disposed.' }), 'disposed', 409, null],
    ['423 mutation policy (AI run owns the group)', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'locked', run_live: true } }), 'running', 423, 'GROUP_AI_RUN_LOCKED'],
    ['409 run_in_progress', axiosError(409, { code: 'run_in_progress' }), 'running', 409, 'run_in_progress'],
    ['403', axiosError(403, { detail: 'Forbidden' }), 'forbidden', 403, null],
    ['400', axiosError(400, { detail: 'bad' }), 'invalid', 400, null],
    ['500', axiosError(500, { detail: 'oops' }), 'other', 500, null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null],
    ['not an http error', new Error('boom'), 'other', null, null],
  ]

  it.each(rows)('%s', (_label, error, kind, status, code) => {
    expect(kindSwitchErrorOf(error)).toEqual({ kind, status, code })
  })
})
