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
/** Blank line between the prepended message section and the rest of the mention (mirrors _section join). */
export const SECTION_SEPARATOR = '\n\n'

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
 * Prepend the chosen project message as a labeled section to the TOP of the mention
 * (R0001 group 0081 "버려져있는 사용자 메세지"). The user message specifies macros the AI
 * must obey, so it must lead the prompt instead of being dumped below the Reminder (the
 * previous append-at-the-end behavior, L0007 §2.4). This mirrors two existing precedents:
 * the rejection-section prepend in useFlowGateToken.copyMentToClipboard, and the server's
 * deliberate hoisting of the clarification guide ("was last → ignored", build_mention).
 *
 * The section uses the server _section() format ('## header\n---\nbody', P005 §3-1) so it
 * reads as a first-class section. Empty message → mention unchanged; empty mention → the
 * section alone (no trailing separator).
 */
export function prependMessageSection(mentionText: string, message: string, header: string): string {
  const body = message.trim()
  if (!body) return mentionText
  const section = `## ${header}\n---\n${body}`
  if (!mentionText) return section
  return section + SECTION_SEPARATOR + mentionText
}
