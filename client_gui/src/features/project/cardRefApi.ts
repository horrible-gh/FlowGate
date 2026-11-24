// flowgate.v02test.0077 (0005-T §4): which card a card display id opens (`F77-12`, `v02test.F77-12`,
// `flowgate.v02test.F77-12`, or `#12` read in a context Flow). It reads the exact id match the server puts at the top
// of GET /api/v1/search/documents (0004-TR §3.4): only items with `match: "card"` and a node key are kept — the
// title hits of the same answer are not this reader's business. The server resolves the id; nothing is guessed here.

import { getRequest } from '@shared/api'

/** One card a display id named, with what the Designer needs to open its Flow and select it. */
export interface CardRefMatch {
  /** The Flow (v0.1 group id) the card is on. */
  flowId: string
  /** The card's Graph node key (`gn_…`). */
  nodeKey: string
  cardNo: number
  /** `F77-12` — what a row shows. */
  short: string
  /** `flowgate.v02test.F77-12` — the full display id. */
  full: string
  /** `F77`, when the server sent the Flow's display id. */
  flowLabel: string | null
  /** The card's representative document (its instruction first) and that document's title. */
  docId: string | null
  title: string | null
}

interface RawCardRef {
  card_no?: number | null
  short?: string | null
  full?: string | null
}

interface RawSearchItem {
  doc_id?: string | null
  title?: string | null
  group_id?: string | null
  match?: string | null
  card?: {
    group_id?: string | null
    node_key?: string | null
    flow_ref?: { label?: string | null } | null
    card_ref?: RawCardRef | null
  } | null
}

interface RawSearchResponse {
  items?: RawSearchItem[] | null
}

/**
 * The cards `query` names in `projectId`. `contextFlowId` is the Flow a `#12` is read in (the server reads a
 * module-less `F77-12` in that Flow's module when it is given, so the caller sends it only for `#12`).
 */
export async function findCardRefs(projectId: string, query: string, contextFlowId?: string | null): Promise<CardRefMatch[]> {
  const params: Record<string, unknown> = { q: query, project: projectId, limit: 20 }
  if (contextFlowId) params.flow = contextFlowId
  const response = await getRequest<RawSearchResponse>('/api/v1/search/documents', params)
  const matches: CardRefMatch[] = []
  const seen = new Set<string>()
  for (const item of response.data?.items ?? []) {
    if (item.match !== 'card') continue
    const card = item.card
    const ref = card?.card_ref
    const flowId = card?.group_id || item.group_id
    const nodeKey = card?.node_key
    if (!flowId || !nodeKey || !ref || typeof ref.card_no !== 'number' || !ref.short || !ref.full) continue
    const key = `${flowId}\n${nodeKey}`
    if (seen.has(key)) continue
    seen.add(key)
    matches.push({
      flowId,
      nodeKey,
      cardNo: ref.card_no,
      short: ref.short,
      full: ref.full,
      flowLabel: card?.flow_ref?.label ?? null,
      docId: item.doc_id ?? null,
      title: item.title ?? null,
    })
  }
  return matches
}
