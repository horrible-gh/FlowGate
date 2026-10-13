/**
 * flowgate.v02.0056 F2-2 — the [작성] window rules (documentWindow/slotWriteModel.ts, O3 0009-TR §4-2): the content
 * sent (null for a blank text and for a work plan line), the length the server takes, and every refusal sorted into
 * existing (the line has a document now) · notice · field, with the server's message kept when it says more; every
 * failure key has its ko · en · ja words.
 */
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import {
  SLOT_WRITE_CONTENT_MAX_LENGTH,
  SLOT_WRITE_FAILURE_KEYS,
  slotWriteContent,
  slotWriteContentless,
  slotWriteFailure,
  slotWriteLength,
  slotWriteProblem,
} from '@/features/documentWindow/slotWriteModel'

registerAppMessages(i18n)

const error = (fields: Partial<Parameters<typeof slotWriteFailure>[0]>) => ({
  code: null,
  status: null,
  network: false,
  message: '',
  field: null,
  reason: null,
  docId: null,
  ...fields,
})

describe('slotWriteModel (flowgate.v02.0056 F2-2)', () => {
  it('the content: the text as typed, null when blank, null for a work plan line', () => {
    expect(slotWriteContent('# 제목\n', 'TR')).toBe('# 제목\n')
    expect(slotWriteContent('  \n', 'TR')).toBeNull()
    expect(slotWriteContent('무시됨', 'WP')).toBeNull()
    expect(slotWriteContentless('wp')).toBe(true)
    expect(slotWriteContentless('M')).toBe(false)
    expect(slotWriteContentless(null)).toBe(false)
  })

  it('a text over the server limit is refused before sending', () => {
    expect(SLOT_WRITE_CONTENT_MAX_LENGTH).toBe(500000)
    expect(slotWriteProblem('a'.repeat(500000), 'TR')).toBeNull()
    expect(slotWriteProblem('a'.repeat(500001), 'TR')).toBe('too_long')
    expect(slotWriteProblem('a'.repeat(500001), 'WP')).toBeNull()
  })

  it('the length is Unicode code points as the server counts it (Python len), not UTF-16 units', () => {
    const emoji = '\u{1F600}'
    expect(slotWriteLength(`${emoji}a`)).toBe(2)
    expect(slotWriteLength('\uD800')).toBe(1)
    expect(slotWriteProblem(emoji.repeat(250001), 'TR')).toBeNull()
    expect(slotWriteProblem(emoji.repeat(500000), 'TR')).toBeNull()
    expect(slotWriteProblem(emoji.repeat(500001), 'TR')).toBe('too_long')
  })

  it('409 slot_has_document / node_token_unavailable: existing, with the document the refusal names', () => {
    expect(slotWriteFailure(error({ code: 'slot_has_document', status: 409, message: '이미 있음', docId: 'd9' }))).toEqual({ kind: 'existing', key: 'slot_has_document', message: '이미 있음', docId: 'd9' })
    expect(slotWriteFailure(error({ code: 'node_token_unavailable', status: 409 }))).toEqual({ kind: 'existing', key: 'node_token_unavailable', message: '', docId: null })
  })

  it.each(['node_in_instruction_stage', 'node_ai_working', 'flow_disposed', 'card_documents_unavailable'])('409 %s: a notice', (code) => {
    expect(slotWriteFailure(error({ code, status: 409, message: code }))).toEqual({ kind: 'notice', key: code, message: '', docId: null })
  })

  it('422: the content reasons under the text, the slot reasons and the rest as a notice', () => {
    for (const reason of ['not_a_text', 'too_long', 'not_for_this_slot', 'empty_body']) {
      expect(slotWriteFailure(error({ code: 'invalid_request', status: 422, field: 'content', reason }))).toMatchObject({ kind: 'field', key: `content.${reason}` })
    }
    for (const reason of ['not_a_slot_of_this_card', 'slot_shares_a_document', 'doc_type_not_registered']) {
      expect(slotWriteFailure(error({ code: 'invalid_request', status: 422, field: 'slot', reason }))).toMatchObject({ kind: 'notice', key: `slot.${reason}` })
    }
    expect(slotWriteFailure(error({ code: 'invalid_request', status: 422, field: 'x', reason: 'unknown_field' }))).toMatchObject({ kind: 'notice', key: 'invalid' })
  })

  it('403 / 404 / no answer / anything else', () => {
    expect(slotWriteFailure(error({ code: 'flow_access_denied', status: 403 }))).toMatchObject({ kind: 'notice', key: 'forbidden' })
    expect(slotWriteFailure(error({ code: 'node_not_found', status: 404 }))).toMatchObject({ kind: 'notice', key: 'not_found' })
    expect(slotWriteFailure(error({ network: true }))).toMatchObject({ kind: 'notice', key: 'network' })
    expect(slotWriteFailure(error({ code: 'boom', status: 500, message: '서버 오류' }))).toEqual({ kind: 'notice', key: 'other', message: '서버 오류', docId: null })
  })

  it.each(['ko', 'en', 'ja'] as const)('every failure key has its %s words', (locale) => {
    for (const key of SLOT_WRITE_FAILURE_KEYS) expect(i18n.global.te(`gui.document_window.write.failure.${key}`, locale), key).toBe(true)
  })
})
