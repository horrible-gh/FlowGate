/**
 * flowgate.v02.0064 T#4 — the kind switch's judgement read and the new refusals in
 * features/document/api/kindSwitch.ts (P0006 §3.1 / §3.3 / §3.5 / §6.1): GET /documents/{doc_id} with
 * `include=root_conversion` -> camelCase (decision, mode, new / expected number, child count, chain,
 * refusal), the `converted_from` link, the switch answer's `conversion`, and the flat 409 codes.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: vi.fn(),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

import {
  kindSwitchConvert,
  kindSwitchErrorOf,
  kindSwitchOriginRead,
  kindSwitchPreviewRead,
  kindSwitchRefusalKind,
  toKindSwitchPreview,
  type KindSwitchErrorKind,
} from '@/features/document'

const FLOW = 'test2.default.0052'
const ROOT_R = `${FLOW}.0001-R`
const ROOT_B = `${FLOW}.0001-B`

const row = (extra: Record<string, unknown> = {}) => ({
  doc_id: ROOT_R, project_id: 'test2', group_id: FLOW, type_code: 'R', seq: 1, title: '요구사항', status: 'draft',
  group_disposed: false, superseded_by: null, converted_from: null, converted_to: null, live_root_doc_id: ROOT_R, ...extra,
})

beforeEach(() => {
  getRequest.mockReset()
  patchRequest.mockReset()
})

describe('kindSwitchPreviewRead — the document read with the judgement', () => {
  it('asks for include=root_conversion and reads a succession', async () => {
    getRequest.mockResolvedValue({
      data: row({
        root_conversion: {
          target_type: 'B', decision: 'succession', mode: 'succession', refusal: null, new_doc_id: ROOT_B, new_doc_exact: true,
          child_count: 3, chained: false, live_root_doc_id: null, read_at: '2026-10-16T10:04:50.007+09:00',
        },
      }),
    })
    const read = await kindSwitchPreviewRead(ROOT_R)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(ROOT_R)}`, { include: 'root_conversion' })
    expect(read.record).toMatchObject({ docId: ROOT_R, typeCode: 'R' })
    expect(read.groupDisposed).toBe(false)
    expect(read.conversion).toEqual({
      targetType: 'B', decision: 'succession', mode: 'succession', refusal: null, newDocId: ROOT_B, newDocExact: true,
      childCount: 3, chained: false, liveRootDocId: null,
    })
    expect(read.convertedFrom).toBeNull()
    expect(read.liveRootDocId).toBe(ROOT_R)
  })

  it('reads an expected number and the converted_from link', () => {
    const read = toKindSwitchPreview(row({
      doc_id: ROOT_B, type_code: 'B', live_root_doc_id: ROOT_B,
      converted_from: { doc_id: ROOT_R, mode: 'succession', at: '2026-10-16T10:06:30+09:00', by: 'u_hong', children: 3 },
      root_conversion: { target_type: 'R', decision: 'succession', mode: 'succession', refusal: null,
        new_doc_id: `${FLOW}.0005-R`, new_doc_exact: false, child_count: 0, chained: true, live_root_doc_id: null },
    }))
    expect(read.conversion).toMatchObject({ newDocId: `${FLOW}.0005-R`, newDocExact: false, chained: true, childCount: 0 })
    expect(read.convertedFrom).toEqual({ docId: ROOT_R, at: '2026-10-16T10:06:30+09:00', by: 'u_hong' })
  })

  it('kindSwitchOriginRead reads the converted_from link alone, without the judgement', async () => {
    getRequest.mockResolvedValue({
      data: row({ doc_id: ROOT_B, type_code: 'B', converted_from: { doc_id: ROOT_R, mode: 'succession', at: null, by: 'u_hong', children: 3 } }),
    })
    expect(await kindSwitchOriginRead(ROOT_B)).toEqual({ docId: ROOT_R, at: null, by: 'u_hong' })
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(ROOT_B)}`)
    getRequest.mockResolvedValue({ data: row() })
    expect(await kindSwitchOriginRead(ROOT_R)).toBeNull()
  })

  it('reads a refusal with its items', () => {
    const read = toKindSwitchPreview(row({
      superseded_by: ROOT_B, live_root_doc_id: ROOT_B,
      root_conversion: { target_type: 'B', decision: 'refused', mode: null,
        refusal: { code: 'root_superseded', live_root_doc_id: ROOT_B }, new_doc_id: null, new_doc_exact: false,
        child_count: 3, chained: false, live_root_doc_id: ROOT_B },
    }))
    expect(read.conversion).toMatchObject({ decision: 'refused', mode: null, newDocId: null, liveRootDocId: ROOT_B })
    expect(read.conversion?.refusal).toEqual({ code: 'root_superseded', reason: null, mode: null, targetDocId: null, liveRootDocId: ROOT_B })
    const unreadable = toKindSwitchPreview(row({ root_conversion: { target_type: 'B', decision: 'refused', mode: 'succession',
      refusal: { code: 'root_body_unreadable', reason: 'file_not_found', mode: 'succession' } } }))
    expect(unreadable.conversion?.refusal).toEqual({ code: 'root_body_unreadable', reason: 'file_not_found', mode: 'succession', targetDocId: null, liveRootDocId: null })
  })

  it('no judgement (an older server, a non-root, a failed judgement) is null', () => {
    expect(toKindSwitchPreview(row()).conversion).toBeNull()
    expect(toKindSwitchPreview(row({ root_conversion: null })).conversion).toBeNull()
    expect(toKindSwitchPreview(row({ root_conversion: { decision: 'unknown' } })).conversion).toBeNull()
  })
})

describe('the switch answer and its refusals', () => {
  it('a succession answer carries its conversion', async () => {
    patchRequest.mockResolvedValue({
      data: { data: row({ doc_id: ROOT_B, type_code: 'B' }),
        conversion: { mode: 'succession', old_doc_id: ROOT_R, new_doc_id: ROOT_B, old_type: 'R', new_type: 'B', child_count: 3 } },
    })
    const result = await kindSwitchConvert(ROOT_R, 'B')
    expect(result.conversion).toEqual({ mode: 'succession', oldDocId: ROOT_R, newDocId: ROOT_B, childCount: 3 })
    expect(result.renamed).toBe(true)
  })

  it('a same-kind answer has no conversion', async () => {
    patchRequest.mockResolvedValue({ data: { data: row() } })
    expect((await kindSwitchConvert(ROOT_R, 'R')).conversion).toBeNull()
  })

  const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
  const rows: readonly [string, KindSwitchErrorKind][] = [
    ['root_superseded', 'superseded'],
    ['root_body_unreadable', 'body_unreadable'],
    ['front_matter_identity_unsafe', 'identity_unsafe'],
    ['attachment_target_rows_exist', 'attachment'],
    ['attachment_path_unsafe', 'attachment'],
    ['attachments_changed', 'attachment'],
    ['attachment_target_exists', 'attachment'],
    ['attachment_copy_mismatch', 'attachment'],
  ]
  it.each(rows)('409 %s -> %s', (code, kind) => {
    expect(kindSwitchErrorOf(axiosError(409, { error: code, detail: 'x', doc_id: ROOT_R }))).toEqual({ kind, status: 409, code })
  })

  it('every judged refusal code has the reason P0006 §3.1 names', () => {
    const table: Record<string, KindSwitchErrorKind> = {
      invalid_target_type: 'invalid', not_found: 'not_found', not_root: 'invalid', group_disposed: 'disposed',
      group_ai_running: 'running', flow_owned_by_graph: 'graph', flow_ownership_conflict: 'ownership_conflict',
      decision_taken: 'decided', root_superseded: 'superseded', target_id_exists: 'collision',
      attachment_target_rows_exist: 'attachment', root_body_unreadable: 'body_unreadable',
      front_matter_identity_unsafe: 'identity_unsafe',
    }
    for (const [code, kind] of Object.entries(table)) expect(kindSwitchRefusalKind(code), code).toBe(kind)
    expect(kindSwitchRefusalKind('something_new')).toBe('other')
  })
})
