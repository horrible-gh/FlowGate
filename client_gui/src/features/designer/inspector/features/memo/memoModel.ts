// flowgate.v02.0053 T#9 — the header ✎ 메모's rules. Pure functions: which memo the button opens, the title a
// new memo gets and the reason a refused write shows.
//   - A document's memos come newest change first (the server's order); the button opens the newest.
//   - With none, the button writes one: a new memo titled after the card, opened straight in its editor.
//   - A refusal shows as the button's tooltip only (409: the Flow's AI is working, 403: no permission).

/** The longest memo title the server takes (documents.py create_related_document). */
export const MEMO_TITLE_LIMIT = 100

export interface MemoFacts {
  readonly docId: string
}

/** The memo the button opens: the newest, or null (none yet). */
export function memoLatest<T extends MemoFacts>(memos: readonly T[] | null | undefined): T | null {
  return memos && memos.length ? memos[0] : null
}

/** A new memo's title (the localized pattern's output, else `fallback`), within the server's limit. */
export function memoTitleWithin(title: string, fallback: string): string {
  const text = title.trim() || fallback.trim()
  return text.length > MEMO_TITLE_LIMIT ? `${text.slice(0, MEMO_TITLE_LIMIT - 1)}…` : text
}

export type MemoFailure = 'running' | 'forbidden' | 'network' | 'other'

/** What a refused write shows (a documentErrorOf reading). */
export function memoFailureOf(error: { readonly kind: string; readonly status: number | null }): MemoFailure {
  if (error.kind === 'network') return 'network'
  if (error.status === 409 || error.status === 423) return 'running'
  if (error.status === 403) return 'forbidden'
  return 'other'
}
