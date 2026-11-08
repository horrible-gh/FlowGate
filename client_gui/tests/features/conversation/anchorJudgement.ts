// flowgate.v02.0151 T-A2 (NR Rev.4 §2.5, §2.7, §2.8 V6d5 / V6e / V6f) — the read-only check procedure for a reported
// case, written down as pure functions so its decision table is tested. It is a test-side tool: the product neither
// imports it nor re-places a card from it (§2.6-3 ⑥ — the server anchor decides, the GUI only reads it sooner).
// Inputs are values read without writing: the change row (run_id, doc_id, run_start_seq, anchor_*), the AI turns of
// the same doc whose source_run_id is the row's run (their seqs = E), and the turn the stored anchor points at.

export type AnchorStateValue = 'reply' | 'run_start' | 'ambiguous' | 'unresolved' | null

export interface StoredAnchor {
  seq: number | null
  position: 'before' | 'after' | null
  state: AnchorStateValue
}

export interface AnchorCase {
  runId: string
  /** The row's doc_id and the chat being checked. */
  docId: string
  chatId: string
  /** ai_run_source_changes.run_start_seq: a number (0 included), null, or undefined when it could not be read. */
  startSeq: number | null | undefined
  /** E: seqs of the doc's AI turns whose source_run_id is this run, as read now. */
  replySeqs: number[]
  stored: StoredAnchor
  /** source_run_id / speaker of the turn at stored.seq, when the anchor has a seq. */
  anchorTurn?: { sourceRunId: string | null; speaker: string } | null
}

/**
 * consistent — stored = expected, placed; unplaced — stored = expected and the expected value is no placement
 * (ambiguous / unresolved): normal, kept; delayed — the server re-resolution has not caught up (forward-only ranks);
 * candidate — a combination the server rule cannot produce or a wrong link: an attribution / anchor error candidate,
 * never a confirmation; undetermined — the start seq was not read, the table is not applied.
 */
export type AnchorVerdict = 'consistent' | 'unplaced' | 'delayed' | 'candidate' | 'undetermined'

/** The server's compute() for a change row (chat_activity_anchor.py): start seq presence, then the answer count. */
export function expectedAnchor(startSeq: number | null, replySeqs: readonly number[]): StoredAnchor {
  const last = replySeqs.length ? Math.max(...replySeqs) : null
  if (startSeq !== null) {
    return last !== null ? { seq: last, position: 'after', state: 'reply' } : { seq: startSeq, position: 'after', state: 'run_start' }
  }
  if (replySeqs.length === 1) return { seq: replySeqs[0], position: 'after', state: 'reply' }
  return { seq: null, position: null, state: replySeqs.length === 0 ? 'unresolved' : 'ambiguous' }
}

const same = (a: StoredAnchor, b: StoredAnchor) => a.seq === b.seq && a.position === b.position && a.state === b.state

export function judgeAnchor(input: AnchorCase): { verdict: AnchorVerdict; expected: StoredAnchor | null } {
  if (input.startSeq === undefined) return { verdict: 'undetermined', expected: null }
  const expected = expectedAnchor(input.startSeq, input.replySeqs)
  const { stored, replySeqs } = input
  const last = replySeqs.length ? Math.max(...replySeqs) : null
  const candidate = { verdict: 'candidate' as const, expected }
  const delayed = { verdict: 'delayed' as const, expected }
  if (input.docId !== input.chatId) return candidate
  // ambiguous never carries a seq; a reply anchor must point at this run's own AI turn.
  if (stored.state === 'ambiguous' && stored.seq !== null) return candidate
  if (stored.state === 'reply' && stored.seq !== null && input.anchorTurn &&
    (input.anchorTurn.sourceRunId !== input.runId || input.anchorTurn.speaker !== 'ai')) return candidate
  if (same(stored, expected)) return { verdict: expected.seq === null ? 'unplaced' : 'consistent', expected }

  if (input.startSeq !== null) {
    if (last !== null) {
      // Expected: after the last answer.
      if (stored.state === null || stored.state === 'unresolved' || stored.state === 'run_start') {
        return stored.state === 'unresolved' ? candidate : delayed
      }
      if (stored.state === 'reply' && stored.seq !== null && replySeqs.includes(stored.seq) && stored.seq < last) return delayed
      return candidate
    }
    // Expected: (S, after, run_start); only a row not resolved yet is late.
    return stored.state === null ? delayed : candidate
  }
  if (replySeqs.length === 1) return stored.state === null || stored.state === 'unresolved' ? delayed : candidate
  if (replySeqs.length === 0) return stored.state === null ? delayed : candidate
  // No start seq, two or more answers: expected ambiguous. NULL / unresolved / an earlier single-answer reply are late.
  if (stored.state === null || stored.state === 'unresolved') return delayed
  if (stored.state === 'reply' && stored.seq !== null && replySeqs.includes(stored.seq) && stored.seq < (last as number)) return delayed
  return candidate
}

/** §2.5 condition 3: clues (never a confirmation, alone or together) and confirmation evidence (any one confirms). */
export interface AttributionEvidence {
  sameFileNames?: boolean
  treeMismatch?: boolean
  sameBlobPairs?: boolean
  overlappingIntervals?: boolean
  sameChangeInBothSummaries?: boolean
  reEdit?: boolean
  /** (가) the row's doc_id is another chat, or the anchored turn's source_run_id stays different after a re-read. */
  wrongLinkPersisted?: boolean
  /** (나) the row's times / trees contradict the run's own record, or a full copy of another run with no change. */
  rowContradictsRun?: boolean
  /** (다) a server log / event shows the save or notice for a wrong run / doc. */
  wrongTargetLogged?: boolean
}

export const ATTRIBUTION_CLUES = ['sameFileNames', 'treeMismatch', 'sameBlobPairs', 'overlappingIntervals', 'sameChangeInBothSummaries', 'reEdit'] as const
export const ATTRIBUTION_CONFIRMATIONS = ['wrongLinkPersisted', 'rowContradictsRun', 'wrongTargetLogged'] as const

export type AttributionVerdict = 'confirmed_to_server_investigation' | 'unconfirmed_possibly_normal' | 'no_clue'

export function judgeAttribution(evidence: AttributionEvidence): AttributionVerdict {
  if (ATTRIBUTION_CONFIRMATIONS.some((key) => evidence[key])) return 'confirmed_to_server_investigation'
  return ATTRIBUTION_CLUES.some((key) => evidence[key]) ? 'unconfirmed_possibly_normal' : 'no_clue'
}
