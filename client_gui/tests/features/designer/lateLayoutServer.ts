// flowgate.v02.0059 T#2 (0016-TR rev2) — a mocked `@shared/api` server whose layout reads the test can hold
// back, for the tests of a 자동 배치 answer that comes late (autoLayoutLateAnswer.0059 /
// DesignerShellDensityLate.0059). It answers GET /api/v1/flows/{flowId}/canvas (with and without
// collapse=conversation), the v0.1 GET /api/v1/documents/{docId} and the layout save PUT in the server's shapes.
//
// The Flows (positions = the server's auto placement, no saved layout unless the test gives one):
// - documentsFlow: an unassigned Flow, documents R, CH (a conversation, hidden by the collapsed read), D —
//   R (0, 0), CH (260, 0), D (520, 0), as the server answered p.ui.0013 (autoLayoutServerRead.0059). `replaced`:
//   D 0003 is replaced by D 0004 (the same places, D 0004 at (520, 0)).
// - assignedFlow: the same Flow once a v0.1 root R with one slot is assigned (sequence) — R (0, 0), the slot
//   (260, 0), CH (260, 180) hidden, D (520, 180) (the sequence rule of the p.ui.0016 server answer).
// - replacedRootFlow: a v0.1 Flow with no slot whose root was replaced twice (0001-R -> 0002-R -> 0005-R) —
//   0005-R (0, 0), D (260, 180), D (520, 180), as the server answered p.ui.0014; only the documents' supersede
//   chain tells that 0005-R is the root.

type Pos = readonly [number, number]

interface Card {
  readonly key: string
  readonly type: string
  readonly kind: string
  readonly at: Pos
  readonly docId?: string
  readonly seq?: number
  readonly supersedes?: readonly string[]
  readonly slot?: number
  readonly hidden?: boolean
}

export interface ServerFlow {
  readonly collapsed: Record<string, unknown>
  readonly whole: Record<string, unknown>
  readonly documents: ReadonlyArray<Record<string, unknown>>
}

function canvasNode(card: Card, saved: Pos | undefined) {
  const [x, y] = saved ?? card.at
  const title = `${card.type} ${card.seq ?? card.slot}`
  return {
    node_key: card.key,
    identity_source: card.slot ? 'sequence_item' : 'document',
    origin: card.slot ? 'sequence_slot' : 'document',
    kind: card.kind,
    doc_type: card.type,
    title,
    state: 'done',
    editable: { move: true, structure: false },
    document: card.docId
      ? { doc_id: card.docId, type_code: card.type, seq: card.seq, title, doc_review_status: 'approved', status: 'open', supersedes: [...(card.supersedes ?? [])] }
      : null,
    slot: card.slot
      ? { item_id: card.slot, item_seq: 1, order: 1, type: card.type, label: `${card.type}#1`, derived_status: 'pending', protected: false, source_wp_card_id: null }
      : null,
    graph_node: null,
    node_run: null,
    position: { x, y, source: saved ? 'saved' : 'auto' },
  }
}

function serverFlow(
  flowId: string,
  head: { projection: string; state: string; rootDocId: string | null },
  cards: readonly Card[],
  edges: ReadonlyArray<readonly [string, string]>,
  documents: ReadonlyArray<Record<string, unknown>>,
  saved: Readonly<Record<string, Pos>> = {},
): ServerFlow {
  const read = (collapse: boolean) => {
    const hidden = collapse ? cards.filter((card) => card.hidden).length : 0
    return {
      ok: true,
      flow_id: flowId,
      project_id: 'p',
      ownership: { state: head.state, sequence_root_doc_id: head.rootDocId, graph_revision_no: null, assigned_at: null },
      projection: head.projection,
      view: null,
      graph: { draft: null, latest_revision_no: null, root_binding: null },
      nodes: cards.filter((card) => !(collapse && card.hidden)).map((card) => canvasNode(card, saved[card.key])),
      edges: edges.map(([source, target]) => ({
        edge_id: `seq:${source}>${target}`, edge_class: 'execution', origin: 'sequence', source, target,
        relation: null, condition: null, editable: false,
      })),
      external_relations: [],
      viewport: null,
      run: null,
      identity: { sequence_tag: null, rematched: false, retired_node_keys: [] },
      collapsed: {
        applied: collapse ? ['conversation'] : [],
        ignored_keys: [],
        hidden_counts: hidden > 0 ? { conversation: hidden } : {},
      },
    }
  }
  return { collapsed: read(true), whole: read(false), documents }
}

const doc = (flowId: string, seq: number, type: string, extra: Record<string, unknown> = {}) => ({
  doc_id: `${flowId}.000${seq}-${type}`, group_id: flowId, status: 'open', superseded_by: null, type_code: type, seq,
  title: `${type} ${seq}`, ...extra,
})

const docCard = (flowId: string, seq: number, type: string, kind: string, at: Pos, extra: Partial<Card> = {}): Card => ({
  key: `doc:${flowId}.000${seq}-${type}`, type, kind, at, docId: `${flowId}.000${seq}-${type}`, seq, ...extra,
})

export function documentsFlow(flowId: string, options: { replaced?: boolean; saved?: Record<string, Pos> } = {}): ServerFlow {
  const d = options.replaced
    ? docCard(flowId, 4, 'D', 'design', [520, 0], { supersedes: [`${flowId}.0003-D`] })
    : docCard(flowId, 3, 'D', 'design', [520, 0])
  return serverFlow(
    flowId,
    { projection: 'documents', state: 'unassigned', rootDocId: null },
    [docCard(flowId, 1, 'R', 'requirement', [0, 0]), docCard(flowId, 2, 'CH', 'memo', [260, 0], { hidden: true }), d],
    [],
    [
      doc(flowId, 1, 'R'),
      doc(flowId, 2, 'CH'),
      doc(flowId, 3, 'D', options.replaced ? { superseded_by: `${flowId}.0004-D` } : {}),
      ...(options.replaced ? [doc(flowId, 4, 'D')] : []),
    ],
    options.saved,
  )
}

export function assignedFlow(flowId: string): ServerFlow {
  const root = docCard(flowId, 1, 'R', 'requirement', [0, 0])
  const slot: Card = { key: 'slot_31', type: 'T', kind: 'task', at: [260, 0], slot: 31 }
  return serverFlow(
    flowId,
    { projection: 'sequence', state: 'v01', rootDocId: `${flowId}.0001-R` },
    [root, slot, docCard(flowId, 2, 'CH', 'memo', [260, 180], { hidden: true }), docCard(flowId, 3, 'D', 'design', [520, 180])],
    [[root.key, slot.key]],
    [doc(flowId, 1, 'R'), doc(flowId, 2, 'CH'), doc(flowId, 3, 'D')],
  )
}

export function replacedRootFlow(flowId: string, saved: Record<string, Pos> = {}): ServerFlow {
  return serverFlow(
    flowId,
    { projection: 'sequence', state: 'v01', rootDocId: `${flowId}.0001-R` },
    [
      docCard(flowId, 5, 'R', 'requirement', [0, 0], { supersedes: [`${flowId}.0002-R`] }),
      docCard(flowId, 3, 'D', 'design', [260, 180]),
      docCard(flowId, 4, 'D', 'design', [520, 180]),
    ],
    [],
    [
      doc(flowId, 1, 'R', { superseded_by: `${flowId}.0002-R` }),
      doc(flowId, 2, 'R', { superseded_by: `${flowId}.0005-R` }),
      doc(flowId, 3, 'D'),
      doc(flowId, 4, 'D'),
      doc(flowId, 5, 'R'),
    ],
    saved,
  )
}

/**
 * The mocked server. `hold.whole` / `hold.document`: a read without collapse / a document read is answered
 * (with what the server held when it was asked) only on `release()`. `saveAnswer`: how the layout save answers.
 */
export function createLateServer() {
  const flows: Record<string, ServerFlow> = {}
  const reads: Array<{ url: string; collapse: unknown }> = []
  const saves: Array<{ flowId: string; positions: Array<{ node_key: string; x: number; y: number }> }> = []
  const held: Array<() => void> = []
  const hold = { whole: false, document: false }
  const server = {
    flows,
    reads,
    saves,
    hold,
    saveAnswer: 'ok' as 'ok' | 'ignored' | 'fail',
    /** Answers every held read (and stops holding). */
    release() {
      hold.whole = false
      hold.document = false
      while (held.length) held.shift()!()
    },
    heldCount: () => held.length,
    wholeReads: () => reads.filter((read) => /\/canvas$/.test(read.url) && read.collapse === undefined).length,
    collapsedReads: () => reads.filter((read) => /\/canvas$/.test(read.url) && read.collapse === 'conversation').length,
    documentReads: () => reads.filter((read) => /\/documents\//.test(read.url)).length,
    async get(url: string, params: Record<string, unknown> = {}): Promise<{ data: unknown }> {
      const later = (data: unknown, wait: boolean) =>
        wait ? new Promise<{ data: unknown }>((resolve) => held.push(() => resolve({ data }))) : Promise.resolve({ data })
      const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
      if (canvas) {
        reads.push({ url, collapse: params.collapse })
        const flow = flows[decodeURIComponent(canvas[1])]
        if (params.collapse === 'conversation') return later(flow.collapsed, false)
        if (params.collapse === undefined) return later(flow.whole, hold.whole)
      }
      const document = /^\/api\/v1\/documents\/([^/]+)$/.exec(url)
      if (document) {
        reads.push({ url, collapse: undefined })
        const id = decodeURIComponent(document[1])
        const found = Object.values(flows).flatMap((flow) => flow.documents).find((d) => d.doc_id === id)
        if (found) return later(found, hold.document)
      }
      throw new Error(`unexpected request ${url}`)
    },
    async put(url: string, body: { positions?: Array<{ node_key: string; x: number; y: number }> }): Promise<{ data: unknown }> {
      const layout = /^\/api\/v1\/flows\/([^/]+)\/canvas\/layout$/.exec(url)
      if (!layout) throw new Error(`unexpected request ${url}`)
      const positions = body.positions ?? []
      saves.push({ flowId: decodeURIComponent(layout[1]), positions })
      if (server.saveAnswer === 'fail') throw new Error('save failed')
      const ignored = server.saveAnswer === 'ignored' ? positions.map((p) => ({ node_key: p.node_key, reason: 'retired' })) : []
      return { data: { ok: true, saved_positions: positions.length - ignored.length, viewport_saved: false, ignored } }
    },
  }
  return server
}

export type LateServer = ReturnType<typeof createLateServer>
