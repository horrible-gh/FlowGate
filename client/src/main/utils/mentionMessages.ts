/**
 * Mention-add message logic (L0007 §1, §2.1, §2.4).
 *
 * Pure helpers shared by the mention-copy flow: merge/sort/dedup the candidate list
 * and append a chosen message to the mention text. The API contract is P0006 §4.5.
 */

/** [All]'s wire/judgement representation (P0006 §3.4, L0007 §1). */
export const WILDCARD_DOC_TYPE = '*'
/** Specific-type messages rank above [All] (L0007 §1). */
const RANK_SPECIFIC = 0
const RANK_ALL = 1
/** Joins the mention body and the chosen message (single LF, L0007 §1). */
export const MESSAGE_SEPARATOR = '\n'

export interface MessageEntry {
  id: number
  project_id?: string
  doc_type: string
  message: string
  updated_at?: string
}

/**
 * Merge / sort / dedup the raw P0006 §4.5 union (requested type + [All]) into the
 * final display order (L0007 §2.1).
 *
 * - rank asc  → current-type messages above [All] (more specific first)
 * - updated_at desc → newest within a rank group first
 * - id asc → deterministic final tie-break
 * - dedup by trim(message), keeping the higher-priority (earlier-sorted) one
 *
 * When currentDocType is the wildcard itself (dialog [All] selected), every '*' row
 * becomes RANK_SPECIFIC and non-'*' rows are dropped — only [All] messages show, no
 * double counting.
 */
export function buildCandidateList(
  rawMessages: MessageEntry[],
  currentDocType: string,
): MessageEntry[] {
  const ranked: Array<{ entry: MessageEntry; rank: number; key: string }> = []
  for (const m of rawMessages) {
    let rank: number
    if (m.doc_type === currentDocType) rank = RANK_SPECIFIC
    else if (m.doc_type === WILDCARD_DOC_TYPE) rank = RANK_ALL
    else continue // defensive: unrelated types dropped (not expected in a valid response)
    ranked.push({ entry: m, rank, key: m.message.trim() })
  }

  ranked.sort((a, b) => {
    if (a.rank !== b.rank) return a.rank - b.rank
    const au = a.entry.updated_at ?? ''
    const bu = b.entry.updated_at ?? ''
    if (au !== bu) return au < bu ? 1 : -1 // updated_at desc
    return a.entry.id - b.entry.id // id asc
  })

  const seen = new Set<string>()
  const deduped: MessageEntry[] = []
  for (const c of ranked) {
    if (seen.has(c.key)) continue
    seen.add(c.key)
    deduped.push(c.entry)
  }
  return deduped
}

/**
 * Append a chosen message to the mention text (L0007 §2.4).
 * Empty mention → message alone; otherwise mention + LF + message.
 */
export function appendMessageToMention(mentionText: string, message: string): string {
  const body = message.trim()
  if (!body) return mentionText
  if (!mentionText) return body
  return mentionText + MESSAGE_SEPARATOR + body
}
