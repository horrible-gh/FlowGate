/**
 * flowgate.v02.0127 T#5 — the TS card's v2 revisions (0009-L §2.15.3 / §3.1): a draft's pinned base, the own-ACK base,
 * 401 / 403 / key-reuse handling, the one unresolved request per document (shared with uploads and windows through the
 * real registry), owner-only release, the original-context resend and the submitted bytes carried by an ACK.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const postRequest = vi.fn()
vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: (...a: unknown[]) => postRequest(...a) }))
// The real document registry (no Vue components): the TS session and the upload / window locks are one place.
vi.mock('@/features/documentWindow', async () => {
  const registry = await import('@/features/documentWindow/documentEditRegistry')
  return {
    documentIoMarkUnresolved: registry.documentIoMarkUnresolved,
    documentIoTsBegin: registry.documentIoTsBegin,
    documentIoTsEnd: registry.documentIoTsEnd,
  }
})

import type { CardDocumentContext } from '@/features/document'
import {
  documentIoConflict, documentIoHasUnresolved, documentIoMarkUnresolved, documentIoUnresolvedOwner, resetDocumentEditRegistry,
} from '@/features/documentWindow/documentEditRegistry'
import {
  resetTsEditSessions, reviseTs, tsConfigCorrected, tsHasUnresolved, tsOwnerOf, tsStartNewIntent, type TsBase,
} from '@/features/designer/inspector/features/testSpec/tsEditSession'

const DOC = 'flowgate.v02.0127.0030-TS'
const CTX: CardDocumentContext = { projectId: 'flowgate', flowId: 'flowgate.v02.0127', nodeKey: 'ts', ownerSlot: 'instruction', openedDocId: DOC }
const CTX_B: CardDocumentContext = { ...CTX, nodeKey: 'ts-b' }
const base = (revisionNo: number, ctx: CardDocumentContext = CTX): TsBase => ({ docId: DOC, revisionNo, ctx })
const ownerRaw = (ctx: CardDocumentContext = CTX) => tsOwnerOf('raw', ctx, DOC)
const ok = (revision: number, replayed = false) => ({ data: { ok: true, doc_id: DOC, revision_no: revision, updated_at: 'u', replayed, line: {} } })
const v2 = (status: number, code: string, params: Record<string, unknown> = {}) => ({ isAxiosError: true, response: { status, data: { ok: false, error: { code, params } } } })
const sent = (n: number) => ({ url: postRequest.mock.calls[n][0] as string, body: postRequest.mock.calls[n][1] as Record<string, unknown> })

beforeEach(() => {
  postRequest.mockReset()
  resetDocumentEditRegistry()
  resetTsEditSessions()
})

describe('a request is frozen from the draft base and judged by its own answer', () => {
  it('raw text goes to /revisions with the pinned revision; the own ACK is the confirmed N+1 and carries the submitted text', async () => {
    postRequest.mockResolvedValueOnce(ok(5))
    const result = await reviseTs(ownerRaw(), base(4), { raw: '---\na: 1\n---\n전체 원문' })
    expect(result).toMatchObject({ ok: true, confirmed: { docId: DOC, revisionNo: 5, replayed: false }, resent: false, submitted: { text: '---\na: 1\n---\n전체 원문', command: null } })
    expect(sent(0).url).toMatch(/\/document\/revisions$/)
    expect(sent(0).body).toMatchObject({ expected_doc_id: DOC, expected_revision: 4, content: '---\na: 1\n---\n전체 원문' })
    expect(String(sent(0).body.idempotency_key)).toMatch(/^[0-9a-f-]{36}$/)
    expect(tsHasUnresolved(DOC)).toBe(false)
    expect(documentIoHasUnresolved(DOC)).toBe(false)
  })

  it('cases and brief use their structured routes on the draft base, never the raw content field', async () => {
    postRequest.mockResolvedValue(ok(5))
    const spec = await reviseTs(tsOwnerOf('cases', CTX, DOC), base(4), { command: { kind: 'ts_spec', cases: [{ case_id: 'TC-1' }], title: 'H1' } })
    expect(spec).toMatchObject({ ok: true, submitted: { text: null, command: { kind: 'ts_spec', title: 'H1' } } })
    expect(sent(0).url).toMatch(/\/document\/test-spec\/revisions$/)
    expect(sent(0).body).toMatchObject({ expected_revision: 4, cases: [{ case_id: 'TC-1' }], title: 'H1' })
    expect(sent(0).body).not.toHaveProperty('content')
    await reviseTs(tsOwnerOf('brief', CTX, DOC), base(5), { command: { kind: 'ts_brief', text: '지시' } })
    expect(sent(1).url).toMatch(/\/document\/test-brief\/revisions$/)
    expect(sent(1).body).toMatchObject({ expected_revision: 5, text: '지시' })
  })

  it('a replay answer is the same ACK; a damaged or mismatching success is uncertain, never a success', async () => {
    postRequest.mockResolvedValueOnce(ok(5, true))
    expect(await reviseTs(ownerRaw(), base(4), { raw: 'x' })).toMatchObject({ ok: true, confirmed: { replayed: true } })
    for (const data of [{}, { ok: true, doc_id: DOC, revision_no: 9, updated_at: 'u', replayed: false, line: {} }]) {
      postRequest.mockResolvedValueOnce({ data })
      const result = await reviseTs(ownerRaw(), base(4), { raw: 'x' })
      expect(result).toMatchObject({ ok: false, kind: 'uncertain' })
      resetTsEditSessions()
      resetDocumentEditRegistry()
    }
  })

  it('no card connection sends nothing', async () => {
    expect(await reviseTs(ownerRaw(), null, { raw: 'x' })).toMatchObject({ ok: false, kind: 'no_context' })
    expect(postRequest).not.toHaveBeenCalled()
  })
})

describe('the document\'s one request is shared with uploads and windows (single_inflight)', () => {
  it('INFLIGHT is visible to the registry while the request is sent', async () => {
    let release: (value: unknown) => void = () => undefined
    postRequest.mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
    const pending = reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(documentIoHasUnresolved(DOC)).toBe(true)
    expect(documentIoConflict(DOC)).toBe(true)
    // a second TS request of another draft is refused while it flies
    expect(await reviseTs(tsOwnerOf('brief', CTX, DOC), base(4), { command: { kind: 'ts_brief', text: 'y' } })).toMatchObject({ ok: false, kind: 'busy' })
    release(ok(5))
    expect(await pending).toMatchObject({ ok: true })
    expect(documentIoHasUnresolved(DOC)).toBe(false)
    expect(postRequest).toHaveBeenCalledTimes(1)
  })

  it('an upload\'s uncertain lock stops the TS request and is not cleared by it', async () => {
    documentIoMarkUnresolved(DOC, true) // the upload's own lock
    expect(await reviseTs(ownerRaw(), base(4), { raw: 'x' })).toMatchObject({ ok: false, kind: 'busy' })
    expect(postRequest).not.toHaveBeenCalled()
    expect(documentIoUnresolvedOwner(DOC)).toBe('upload')
    // even a TS rejection of another document state never clears the upload's lock
    documentIoMarkUnresolved(DOC, false, ownerRaw())
    expect(documentIoUnresolvedOwner(DOC)).toBe('upload')
  })

  it('a TS success / rejection clears only its own lock', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true })
    await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(documentIoUnresolvedOwner(DOC)).toBe(ownerRaw())
    documentIoMarkUnresolved(DOC, false) // the upload tries to clear: not its lock
    expect(documentIoUnresolvedOwner(DOC)).toBe(ownerRaw())
  })
})

describe('REJECTED answers', () => {
  it.each([[409, 'revision_conflict'], [422, 'invalid_content'], [409, 'slot_document_changed']])(
    '%s %s is rejected: nothing is held, the next explicit save is a new intent with a new key', async (status, code) => {
      postRequest.mockRejectedValueOnce(v2(status, code))
      const result = await reviseTs(ownerRaw(), base(4), { raw: 'x' })
      expect(result).toMatchObject({ ok: false, kind: 'rejected', failure: { code } })
      expect(tsHasUnresolved(DOC)).toBe(false)
      expect(documentIoHasUnresolved(DOC)).toBe(false)
      postRequest.mockResolvedValueOnce(ok(5))
      await reviseTs(ownerRaw(), base(4), { raw: 'x' })
      expect(sent(1).body.idempotency_key).not.toBe(sent(0).body.idempotency_key)
    })

  it('the base never moves on a rejection: the same pinned revision is sent again', async () => {
    postRequest.mockRejectedValueOnce(v2(409, 'revision_conflict')).mockResolvedValueOnce(ok(5))
    await reviseTs(tsOwnerOf('cases', CTX, DOC), base(4), { command: { kind: 'ts_spec', cases: [] } })
    await reviseTs(tsOwnerOf('cases', CTX, DOC), base(4), { command: { kind: 'ts_spec', cases: [] } })
    expect(sent(0).body.expected_revision).toBe(4)
    expect(sent(1).body.expected_revision).toBe(4)
  })

  it('401 keeps the ORIGINAL request: the same actor resends the same key and bytes', async () => {
    postRequest.mockRejectedValueOnce(v2(401, 'unauthorized')).mockResolvedValueOnce(ok(5))
    const first = await reviseTs(ownerRaw(), base(4), { raw: '첫 원문' })
    expect(first).toMatchObject({ ok: false, kind: 'rejected', failure: { code: 'unauthorized' } })
    expect(tsHasUnresolved(DOC)).toBe(true)
    const again = await reviseTs(ownerRaw(), base(4), { raw: '다른 원문' })
    expect(again).toMatchObject({ ok: true, resent: true, submitted: { text: '첫 원문' } })
    expect(sent(1).body).toEqual(sent(0).body)
  })

  it('403 token_kind_not_allowed sends nothing more until the setting is corrected, then the same request goes out', async () => {
    postRequest.mockRejectedValueOnce(v2(403, 'token_kind_not_allowed')).mockResolvedValueOnce(ok(5))
    await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    const blocked = await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(blocked).toMatchObject({ ok: false, kind: 'blocked', failure: { code: 'token_kind_not_allowed' } })
    expect(postRequest).toHaveBeenCalledTimes(1)
    tsConfigCorrected(DOC, ownerRaw())
    expect(await reviseTs(ownerRaw(), base(4), { raw: 'x' })).toMatchObject({ ok: true, resent: true })
    expect(sent(1).body.idempotency_key).toBe(sent(0).body.idempotency_key)
  })

  it('idempotency_key_reused is not repeated with a new key: a new intent must be started explicitly', async () => {
    postRequest.mockRejectedValueOnce(v2(409, 'idempotency_key_reused')).mockResolvedValueOnce(ok(5))
    await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(await reviseTs(ownerRaw(), base(4), { raw: 'x' })).toMatchObject({ ok: false, kind: 'blocked' })
    expect(postRequest).toHaveBeenCalledTimes(1)
    tsStartNewIntent(DOC, ownerRaw())
    expect(documentIoHasUnresolved(DOC)).toBe(false)
    expect(await reviseTs(ownerRaw(), base(4), { raw: 'x' })).toMatchObject({ ok: true, resent: false })
    expect(sent(1).body.idempotency_key).not.toBe(sent(0).body.idempotency_key)
  })
})

describe('UNCERTAIN: one frozen request per document', () => {
  it('a network failure holds the request; only its owner resends it, as it is (same key, same bytes), with the submitted text in the ACK', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true })
    const first = await reviseTs(ownerRaw(), base(4), { raw: '첫 원문' })
    expect(first).toMatchObject({ ok: false, kind: 'uncertain' })
    expect(tsHasUnresolved(DOC)).toBe(true)
    expect(documentIoUnresolvedOwner(DOC)).toBe(ownerRaw())

    // another draft of the same document may not send anything meanwhile (raw / cases / brief / upload share it)
    const other = await reviseTs(tsOwnerOf('brief', CTX, DOC), base(4), { command: { kind: 'ts_brief', text: 'x' } })
    expect(other).toMatchObject({ ok: false, kind: 'busy' })
    expect(postRequest).toHaveBeenCalledTimes(1)

    // the owner presses save again, even with a different text: the held request goes out unchanged and the ACK says
    // that it is the FIRST text that was stored (the caller must not adopt the second one as saved)
    postRequest.mockResolvedValueOnce(ok(5))
    const again = await reviseTs(ownerRaw(), base(4), { raw: '다른 원문' })
    expect(again).toMatchObject({ ok: true, confirmed: { revisionNo: 5 }, resent: true, submitted: { text: '첫 원문' } })
    expect(sent(1).body).toEqual(sent(0).body)
    expect(tsHasUnresolved(DOC)).toBe(false)
    expect(documentIoHasUnresolved(DOC)).toBe(false)
  })

  it('a retry is sent with the original card coordinates and another card cannot take the request over', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true })
    await reviseTs(ownerRaw(CTX), base(4, CTX), { raw: 'A' })
    // card B opens the same document: a different owner (coordinates are part of it) is busy, nothing is sent
    expect(await reviseTs(ownerRaw(CTX_B), base(4, CTX_B), { raw: 'B' })).toMatchObject({ ok: false, kind: 'busy' })
    expect(postRequest).toHaveBeenCalledTimes(1)
    // the owner's retry goes to card A's route even when the caller passes a different base context
    postRequest.mockResolvedValueOnce(ok(5))
    await reviseTs(ownerRaw(CTX), base(4, CTX_B), { raw: 'A2' })
    expect(sent(1).url).toBe(sent(0).url)
    expect(sent(1).url).toContain('/nodes/ts/')
    expect(sent(1).url).not.toContain('/nodes/ts-b/')
  })

  it('a resend that is rejected before commit releases the document', async () => {
    postRequest.mockRejectedValueOnce(v2(500, 'internal_error')).mockRejectedValueOnce(v2(409, 'revision_conflict'))
    await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(tsHasUnresolved(DOC)).toBe(true)
    const result = await reviseTs(ownerRaw(), base(4), { raw: 'x' })
    expect(result).toMatchObject({ ok: false, kind: 'rejected', failure: { code: 'revision_conflict' } })
    expect(tsHasUnresolved(DOC)).toBe(false)
    expect(documentIoHasUnresolved(DOC)).toBe(false)
    expect(sent(1).body.idempotency_key).toBe(sent(0).body.idempotency_key)
  })
})
