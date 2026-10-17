// flowgate.v02.0056 F2-2 — what the [작성] window decides without a screen or a server call (O3 0009-TR §4-2): the
// content it sends and how a refusal is shown. Pure.
//
//   existing  409 slot_has_document · node_token_unavailable — the line has a document now (another tab, the
//             card's own run): the Canvas is read again and that document opens; the window keeps the text.
//   notice    409 node_in_instruction_stage · node_ai_working (wait and try again) · flow_disposed ·
//             card_documents_unavailable, 422 on the slot, 403, 404, no answer, anything else.
//   field     422 on the content (not_a_text · too_long · not_for_this_slot · empty_body): under the text.
//
// The server's message (x-locale) is shown after the code's words, as sent (error.code decides, the message
// tells).
import type { CardLineError } from '@/features/document'

/** Line types whose [작성] sends no content: a work plan line is made as an empty plan only (O3 §4-2). */
export const SLOT_WRITE_CONTENTLESS_TYPES: readonly string[] = ['WP']

/** The longest content the server takes (O3 §4-2 `too_long`), in Unicode code points (Python len): see slotWriteLength. */
export const SLOT_WRITE_CONTENT_MAX_LENGTH = 500000

// A UTF-16 surrogate pair: one code point written as two string units.
const SURROGATE_PAIR = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g

/** A text's length as the server counts it: Unicode code points (string.length counts an emoji as two). */
export function slotWriteLength(text: string): number {
  return text.length - (text.match(SURROGATE_PAIR)?.length ?? 0)
}

/** The line is written without content (an empty work plan). */
export function slotWriteContentless(docType: string | null | undefined): boolean {
  return SLOT_WRITE_CONTENTLESS_TYPES.includes(docType?.trim().toUpperCase() ?? '')
}

/** What is sent as `content`: null for a contentless line or a blank text (an empty draft), else the text as typed. */
export function slotWriteContent(text: string, docType: string | null | undefined): string | null {
  if (slotWriteContentless(docType)) return null
  return text.trim() === '' ? null : text
}

/** A problem the window sees before sending (the server would refuse the same): the content field reason, or null. */
export function slotWriteProblem(text: string, docType: string | null | undefined): 'too_long' | null {
  return !slotWriteContentless(docType) && slotWriteLength(text) > SLOT_WRITE_CONTENT_MAX_LENGTH ? 'too_long' : null
}

export type SlotWriteFailureKind = 'existing' | 'notice' | 'field'

export interface SlotWriteFailure {
  readonly kind: SlotWriteFailureKind
  /** Message key under `gui.document_window.write.failure.` */
  readonly key: string
  /** The server's message (x-locale) when it says more than the code, else ''. */
  readonly message: string
  /** existing: the document the refusal names, when it names one. */
  readonly docId: string | null
}

const EXISTING_CODES: readonly string[] = ['slot_has_document', 'node_token_unavailable']
const NOTICE_CODES: readonly string[] = ['node_in_instruction_stage', 'node_ai_working', 'flow_disposed', 'card_documents_unavailable', 'document_storage_unwritable']
const CONTENT_REASONS: readonly string[] = ['not_a_text', 'too_long', 'not_for_this_slot', 'empty_body']
const SLOT_REASONS: readonly string[] = ['not_a_slot_of_this_card', 'slot_shares_a_document', 'doc_type_not_registered']

/** A refused write as the window shows it. */
export function slotWriteFailure(error: Pick<CardLineError, 'code' | 'status' | 'network' | 'message' | 'field' | 'reason' | 'docId'>): SlotWriteFailure {
  const message = error.message && error.message !== error.code ? error.message : ''
  const notice = (key: string): SlotWriteFailure => ({ kind: 'notice', key, message, docId: null })
  if (error.network) return notice('network')
  const code = error.code ?? ''
  if (EXISTING_CODES.includes(code)) return { kind: 'existing', key: code, message, docId: error.docId }
  if (NOTICE_CODES.includes(code)) return notice(code)
  if (error.status === 422 || error.status === 400) {
    const reason = error.reason ?? ''
    if (error.field === 'content' && CONTENT_REASONS.includes(reason)) return { kind: 'field', key: `content.${reason}`, message, docId: null }
    if (error.field === 'slot' && SLOT_REASONS.includes(reason)) return notice(`slot.${reason}`)
    return notice('invalid')
  }
  if (error.status === 401 || error.status === 403) return notice('forbidden')
  if (error.status === 404) return notice('not_found')
  return notice('other')
}

/** Every failure key the window may show (each has ko · en · ja words). */
export const SLOT_WRITE_FAILURE_KEYS: readonly string[] = [
  ...EXISTING_CODES,
  ...NOTICE_CODES,
  ...CONTENT_REASONS.map((reason) => `content.${reason}`),
  ...SLOT_REASONS.map((reason) => `slot.${reason}`),
  'invalid',
  'forbidden',
  'not_found',
  'network',
  'other',
]
