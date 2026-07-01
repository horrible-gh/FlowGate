// 0018 R0001 — workflow-strip time-machine slot resolution.
//
// A clicked strip cell is identified by its position (index) + type code. To roll the
// workflow back to that exact step we must map it to the sequence slot that realised it
// (its result_doc_id + the realised document's seq = the reopen target_seq).
//
// The strip (stepStates) and the sequence endpoint items are both ordered by sort_order,
// so the positional index normally aligns. But to stay correct when the two orderings ever
// diverge — and, critically, when a type repeats (a design series appearing twice) so a
// naive indexOf(type) would pick the wrong cell (NR0003 §3/§5.2) — we fall back to the Nth
// occurrence of the clicked type. This module is pure (no Vue/DOM) so it is unit-testable.

/** Minimal shape of a workflow sequence item (GET /workflow/{docId}/sequence). */
export interface SequenceSlot {
  type?: string | null
  result_doc_id?: string | null
  result_seq?: number | null
  [k: string]: unknown
}

/** Minimal shape of a strip cell (workflowViewState StepState). */
export interface StripCell {
  code: string
}

export interface ClickPayload {
  index: number
  code: string
}

/**
 * Resolve the sequence slot for a clicked strip cell.
 * Prefers the positional slot when its type matches the clicked type; otherwise falls back
 * to the Nth occurrence of that type (N counted over the strip cells up to and including the
 * clicked index). Returns null when nothing resolves.
 */
export function resolveClickedSlot(
  stripCells: StripCell[],
  items: SequenceSlot[],
  payload: ClickPayload,
): SequenceSlot | null {
  const direct = items[payload.index]
  if (direct && direct.type === payload.code) return direct

  let occurrence = 0
  for (let i = 0; i <= payload.index && i < stripCells.length; i++) {
    if (stripCells[i].code === payload.code) occurrence++
  }
  const sameType = items.filter((it) => it.type === payload.code)
  return sameType[occurrence - 1] ?? direct ?? null
}

// Structural / auto-complete step types that reopen never rolls back and that therefore
// are not valid time-machine targets (mirrors the server reopen exclusion).
export const NON_ROLLBACK_TYPES = ['R', 'B', 'Q', 'AC', 'M']

/** A slot is a valid rollback target when it has a realised reviewable document. */
export function isRollbackTarget(slot: SequenceSlot | null): boolean {
  return !!slot
    && slot.result_doc_id != null
    && slot.result_seq != null
    && !NON_ROLLBACK_TYPES.includes(slot.type ?? '')
}
