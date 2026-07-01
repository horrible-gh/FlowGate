import { describe, expect, it } from 'vitest'
import {
  resolveClickedSlot,
  isRollbackTarget,
  type SequenceSlot,
  type StripCell,
} from '@main/workflow/timeMachineSlot'

// 0018 R0001 — slot identity resolution for the workflow-strip time-machine.

const cells = (...codes: string[]): StripCell[] => codes.map((code) => ({ code }))

describe('resolveClickedSlot — 0018 R0001 slot identity', () => {
  it('positional match: aligned strip/sequence resolves by index', () => {
    const strip = cells('R', 'DS', 'D', 'T', 'AC')
    const items: SequenceSlot[] = [
      { type: 'R', result_doc_id: 'r1', result_seq: 1 },
      { type: 'DS', result_doc_id: 'ds1', result_seq: 2 },
      { type: 'D', result_doc_id: 'd1', result_seq: 3 },
      { type: 'T', result_doc_id: 't1', result_seq: 4 },
      { type: 'AC', result_doc_id: null, result_seq: null },
    ]
    const slot = resolveClickedSlot(strip, items, { index: 2, code: 'D' })
    expect(slot?.result_doc_id).toBe('d1')
    expect(slot?.result_seq).toBe(3)
  })

  it('repeated type: second occurrence resolves to the correct (later) slot', () => {
    // D appears at strip index 1 and 3 — clicking index 3 must pick the 2nd D slot.
    const strip = cells('R', 'D', 'T', 'D', 'AC')
    const items: SequenceSlot[] = [
      { type: 'R', result_doc_id: 'r1', result_seq: 1 },
      { type: 'D', result_doc_id: 'd-first', result_seq: 2 },
      { type: 'T', result_doc_id: 't1', result_seq: 3 },
      { type: 'D', result_doc_id: 'd-second', result_seq: 4 },
      { type: 'AC', result_doc_id: null, result_seq: null },
    ]
    expect(resolveClickedSlot(strip, items, { index: 1, code: 'D' })?.result_doc_id).toBe('d-first')
    expect(resolveClickedSlot(strip, items, { index: 3, code: 'D' })?.result_doc_id).toBe('d-second')
  })

  it('divergent ordering: positional type mismatch falls back to type-occurrence', () => {
    // Sequence items in a different order than the strip; index 2 (D) does not line up.
    const strip = cells('R', 'DS', 'D', 'T')
    const items: SequenceSlot[] = [
      { type: 'R', result_doc_id: 'r1', result_seq: 1 },
      { type: 'T', result_doc_id: 't1', result_seq: 4 },
      { type: 'DS', result_doc_id: 'ds1', result_seq: 2 },
      { type: 'D', result_doc_id: 'd1', result_seq: 3 },
    ]
    const slot = resolveClickedSlot(strip, items, { index: 2, code: 'D' })
    expect(slot?.result_doc_id).toBe('d1') // by occurrence, not index 2 (which is DS here)
  })

  it('returns null when the type is absent from the sequence', () => {
    const strip = cells('R', 'DS')
    const items: SequenceSlot[] = [{ type: 'R', result_doc_id: 'r1', result_seq: 1 }]
    expect(resolveClickedSlot(strip, items, { index: 1, code: 'DS' })).toBeNull()
  })
})

describe('isRollbackTarget — reopen eligibility', () => {
  it('realised reviewable slot is a valid target', () => {
    expect(isRollbackTarget({ type: 'T', result_doc_id: 't1', result_seq: 4 })).toBe(true)
  })

  it('structural / auto-complete types are excluded', () => {
    expect(isRollbackTarget({ type: 'R', result_doc_id: 'r1', result_seq: 1 })).toBe(false)
    expect(isRollbackTarget({ type: 'M', result_doc_id: 'm1', result_seq: 2 })).toBe(false)
    expect(isRollbackTarget({ type: 'AC', result_doc_id: 'ac1', result_seq: 3 })).toBe(false)
    expect(isRollbackTarget({ type: 'Q', result_doc_id: 'q1', result_seq: 3 })).toBe(false)
  })

  it('unrealised slot (no result doc / seq) is excluded', () => {
    expect(isRollbackTarget({ type: 'T', result_doc_id: null, result_seq: null })).toBe(false)
    expect(isRollbackTarget({ type: 'T', result_doc_id: 't1', result_seq: null })).toBe(false)
    expect(isRollbackTarget(null)).toBe(false)
  })
})
