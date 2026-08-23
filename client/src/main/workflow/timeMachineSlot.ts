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

// 0332 D0005 §6.1 — a strip cell's source commit. The sequence endpoint hangs it on the
// slot (`tr_commit`), and the marker is resolved through the SAME slot resolution the click
// uses: a repeated type must not mark one cell and roll back another.
export interface SlotCommitMark {
  state: 'live' | 'canceled'
  commit: string | null
  subject?: string | null
  cancel_commit?: string | null
  /**
   * 0332 T0018 K11 — a `live` mark that came back through a forward restore rather than
   * through a fresh approval. The marker itself is unchanged (the newest-row-wins rule
   * already brings it back); this only lets the hover line say which of the two it is.
   */
  restored?: boolean
}

/**
 * Per-cell commit marks, aligned to `stripCells`. `null` = draw nothing: either the slot
 * has no realised document, or its TR changed no source (the server sends no mark for a
 * `no_commit` row). A step that touched no source stays quiet — that is the design, not a
 * missing value.
 */
export function slotCommitMarks(
  stripCells: StripCell[],
  items: SequenceSlot[],
): (SlotCommitMark | null)[] {
  return stripCells.map((cell, index) => {
    const slot = resolveClickedSlot(stripCells, items, { index, code: cell.code })
    const mark = slot?.tr_commit as SlotCommitMark | null | undefined
    if (!mark || (mark.state !== 'live' && mark.state !== 'canceled')) return null
    return mark
  })
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

// 0142 R0001 — reverse time-machine (return point). After a rewind the workflow head sits at
// an earlier step; the steps that were rewound past it are the "return targets". They render
// AHEAD of the head in the same strip and — mirroring the backward time-machine — become
// hover-clickable so one click rolls the workflow FORWARD to that step (restoring the
// untouched steps in between without re-approval).
//
// A strip cell is a return target when its resolved slot is a valid rollback target whose
// realised seq sits above the current head (currentMinSeq, exclusive — you are already there)
// and at or below the return-point front (frontSeq, inclusive — the original position). The
// resolution reuses resolveClickedSlot so the highlighted cells and the click that restores
// them can never diverge (the same repeated-type / divergent-ordering handling applies).
export function returnTargetIndices(
  stripCells: StripCell[],
  items: SequenceSlot[],
  currentMinSeq: number | null,
  frontSeq: number | null,
): number[] {
  if (currentMinSeq == null || frontSeq == null || currentMinSeq >= frontSeq) return []
  const out: number[] = []
  for (let i = 0; i < stripCells.length; i++) {
    const slot = resolveClickedSlot(stripCells, items, { index: i, code: stripCells[i].code })
    if (!isRollbackTarget(slot)) continue
    const seq = slot!.result_seq as number
    if (seq > currentMinSeq && seq <= frontSeq) out.push(i)
  }
  return out
}
