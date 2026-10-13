// flowgate.v02.0056 F2-2 — the v0.2 card memo rules (O3 flowgate.v02.0056.0009-TR §4-5): what a memo may be
// written with, what a change sends, and how a refusal is shown. Pure: no server call, no Vue. The memo is the
// card's (its Flow's node), never a v0.1 M document.
import type { InspectorTarget } from '../../inspectorTarget'

/**
 * The server's limits (O3 §4-5): a title of 1..200 characters on one line, a body of at most 20000. The server
 * counts characters as Unicode code points (Python len), so they are counted with cardMemoLength — never with
 * string.length, which counts an emoji or another character beyond the BMP as two.
 */
export const CARD_MEMO_TITLE_MAX = 200
export const CARD_MEMO_BODY_MAX = 20000

// A UTF-16 surrogate pair: one code point written as two string units.
const SURROGATE_PAIR = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g

/** A text's length as the server counts it: Unicode code points. */
export function cardMemoLength(text: string): number {
  return text.length - (text.match(SURROGATE_PAIR)?.length ?? 0)
}

/** How the memo windows read: as written, or in the editor. */
export type CardMemoWindowMode = 'view' | 'edit'

/** A memo as the screen holds it (features/document CardMemo). */
export interface CardMemoFacts {
  readonly memoId: string
  readonly title: string
  readonly body: string
  readonly aiRef: boolean
}

/** What the memo editor holds. */
export interface CardMemoDraft {
  readonly title: string
  readonly body: string
  readonly aiRef: boolean
}

/** The editor's starting values: the memo's, or an empty memo the AI does not refer to (default OFF). */
export function cardMemoDraftOf(memo: CardMemoFacts | null | undefined): CardMemoDraft {
  return memo ? { title: memo.title, body: memo.body, aiRef: memo.aiRef } : { title: '', body: '', aiRef: false }
}

// Control characters (line breaks included) the server refuses in a title (`invalid_text`).
const CONTROL = /[\u0000-\u001f\u007f]/

/** Why a title cannot be saved (the server's reasons), or null. The server trims it first. */
export function cardMemoTitleProblem(title: string): 'required' | 'too_long' | 'invalid_text' | null {
  const trimmed = title.trim()
  if (trimmed === '') return 'required'
  if (CONTROL.test(trimmed)) return 'invalid_text'
  if (cardMemoLength(trimmed) > CARD_MEMO_TITLE_MAX) return 'too_long'
  return null
}

/** Why a body cannot be saved, or null. */
export function cardMemoBodyProblem(body: string): 'too_long' | null {
  return cardMemoLength(body) > CARD_MEMO_BODY_MAX ? 'too_long' : null
}

/** What a change sends: the fields that differ from the memo (the title trimmed); null when nothing changed. */
export function cardMemoChanges(memo: CardMemoFacts, draft: CardMemoDraft): { title?: string; body?: string; aiRef?: boolean } | null {
  const changes: { title?: string; body?: string; aiRef?: boolean } = {}
  const title = draft.title.trim()
  if (title !== memo.title) changes.title = title
  if (draft.body !== memo.body) changes.body = draft.body
  if (draft.aiRef !== memo.aiRef) changes.aiRef = draft.aiRef
  return Object.keys(changes).length ? changes : null
}

/** A refused memo call as the server answers it (features/document CardMemoError). */
export interface CardMemoErrorFacts {
  readonly code: string | null
  readonly status: number | null
  readonly network: boolean
  readonly message: string
  readonly field: string | null
  readonly reason: string | null
}

export interface CardMemoFailure {
  /** The editor field the refusal is about (shown under it), or null (shown in the window). */
  readonly field: 'title' | 'body' | 'ai_ref' | null
  /** Message key under `gui.inspector.cardMemo.failure.` */
  readonly key: string
  /** The server's message (x-locale) when it says more than the code, else ''. */
  readonly message: string
  /** The list is read again (the memo is gone, or the card changed under it). */
  readonly reread: boolean
}

const FIELD_REASONS: Readonly<Record<'title' | 'body' | 'ai_ref', readonly string[]>> = {
  title: ['required', 'not_a_text', 'too_long', 'invalid_text'],
  body: ['not_a_text', 'too_long', 'no_change'],
  ai_ref: ['not_a_bool'],
}

/** How a refused memo call is shown (error.code / the 422 field decide; the server's message tells). */
export function cardMemoFailure(error: CardMemoErrorFacts): CardMemoFailure {
  const message = error.message && error.message !== error.code ? error.message : ''
  const plain = (key: string, reread = false): CardMemoFailure => ({ field: null, key, message, reread })
  if (error.network) return plain('network')
  if (error.status === 422 || error.status === 400) {
    const field = error.field as keyof typeof FIELD_REASONS | null
    if (field && FIELD_REASONS[field]?.includes(error.reason ?? '')) return { field, key: `field.${field}.${error.reason}`, message, reread: false }
    return plain('invalid')
  }
  if (error.code === 'memo_not_found') return plain('memo_not_found', true)
  if (error.code === 'flow_disposed') return plain('flow_disposed')
  if (error.code === 'card_memos_unavailable') return plain('unavailable')
  if (error.status === 404) return plain('not_found', true)
  if (error.status === 401 || error.status === 403) return plain('forbidden')
  return plain('other')
}

/** Every failure key the section may show (each has ko · en · ja words). */
export const CARD_MEMO_FAILURE_KEYS: readonly string[] = [
  ...Object.entries(FIELD_REASONS).flatMap(([field, reasons]) => reasons.map((reason) => `field.${field}.${reason}`)),
  'invalid',
  'memo_not_found',
  'flow_disposed',
  'unavailable',
  'not_found',
  'forbidden',
  'network',
  'other',
]

/** Why the memos cannot be changed here (message key under `gui.inspector.cardMemo.`), or null: read only views. */
export function cardMemoLockedReason(target: Pick<InspectorTarget, 'readOnly'> | null): string | null {
  if (target?.readOnly === 'conflict') return 'locked.conflict'
  if (target?.readOnly === 'revision') return 'locked.revision'
  return null
}

/** A memo time (ISO 8601) as `YYYY-MM-DD HH:mm` in the viewer's local time; other text as sent. */
export function cardMemoWhen(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
