import { describe, expect, it } from 'vitest'
import {
  ATTRIBUTION_CLUES,
  ATTRIBUTION_CONFIRMATIONS,
  expectedAnchor,
  judgeAnchor,
  judgeAttribution,
  type AnchorCase,
  type AttributionEvidence,
  type StoredAnchor,
} from './anchorJudgement'

// flowgate.v02.0151 T-A2 (NR Rev.4 §2.5 expected-anchor and anchor_state tables, §2.7, §2.8 V6d5 / V6e / V6f): the
// read-only check procedure, synthetic inputs only. The server rule itself (compute / should_replace / needs_resolve)
// stays fixed by server/tests/.../test_chat_activity_anchor_0675.py, which this group runs unchanged.
const CH = 'p.m.0001.0002-CH'
const reply = (seq: number): StoredAnchor => ({ seq, position: 'after', state: 'reply' })
const runStart = (seq: number): StoredAnchor => ({ seq, position: 'after', state: 'run_start' })
const none = (state: StoredAnchor['state']): StoredAnchor => ({ seq: null, position: null, state })
const own = { sourceRunId: 'R_old', speaker: 'ai' }

function input(startSeq: number | null | undefined, replySeqs: number[], stored: StoredAnchor, extra: Partial<AnchorCase> = {}): AnchorCase {
  return { runId: 'R_old', docId: CH, chatId: CH, startSeq, replySeqs, stored,
    anchorTurn: stored.seq !== null && stored.state === 'reply' ? own : null, ...extra }
}

describe('0151 expected anchor = server compute() branches (§2.5 table)', () => {
  it.each([
    ['S present, answers (several)', 5, [6, 11], reply(11)],
    ['S present, no answer', 9, [], runStart(9)],
    ['S = 0 is a start seq, not "none"', 0, [], runStart(0)],
    ['S null, exactly one answer', null, [11], reply(11)],
    ['S null, no answer', null, [], none('unresolved')],
    ['S null, two or more answers', null, [6, 11], none('ambiguous')],
  ])('%s', (_name, startSeq, replies, expected) => {
    expect(expectedAnchor(startSeq as number | null, replies as number[])).toEqual(expected)
  })
})

describe('0151 V6d5: S null and several answers — normal unplaced vs delayed is told by anchor_state', () => {
  it.each([
    ['(가) ambiguous, seq NULL', none('ambiguous'), 'unplaced'],
    ['(나) state NULL (pending), seq NULL', none(null), 'delayed'],
    ['(다) unresolved, seq NULL', none('unresolved'), 'delayed'],
    ['(라) reply at the earlier single answer #6', reply(6), 'delayed'],
    ['(마) reply at max(E) #11 — compute cannot make it', reply(11), 'candidate'],
  ])('%s', (_name, stored, verdict) => {
    const judged = judgeAnchor(input(null, [6, 11], stored as StoredAnchor))
    expect(judged.verdict).toBe(verdict)
    // Whatever the verdict, the expected value is "ambiguous", never "after the last answer".
    expect(judged.expected).toEqual(none('ambiguous'))
    expect(judged.expected).not.toEqual(reply(11))
  })

  it('(가) and (나) both have anchor_seq NULL yet end differently', () => {
    expect(judgeAnchor(input(null, [6, 11], none('ambiguous'))).verdict).not.toBe(judgeAnchor(input(null, [6, 11], none(null))).verdict)
  })
})

describe('0151 V6e: expected-anchor classification of the follow-up cases and of wrong links', () => {
  it.each([
    ['V6a delayed read, #9 before #10', input(5, [9], reply(9)), 'consistent'],
    ['V6a2 / V6b late answer after #10', input(8, [11], reply(11)), 'consistent'],
    ['V6a3 answered #9, finished on the adopted read', input(8, [9], reply(9)), 'consistent'],
    ['V6c before the answer', input(9, [], runStart(9)), 'consistent'],
    ['V6d start seq, answers #6 #11', input(5, [6, 11], reply(11)), 'consistent'],
    ['V6d2 no start seq, answers #6 #11', input(null, [6, 11], none('ambiguous')), 'unplaced'],
    ['V6d3 no start seq, one answer', input(null, [11], reply(11)), 'consistent'],
    ['V6d4 no start seq, no answer', input(null, [], none('unresolved')), 'unplaced'],
    ['V6d4 start seq 0, no answer', input(0, [], runStart(0)), 'consistent'],
  ])('%s is consistent (placed or normally unplaced)', (_name, c, verdict) => {
    expect(judgeAnchor(c).verdict).toBe(verdict)
  })

  it.each([
    ['anchored turn belongs to another run', input(5, [11], reply(11), { anchorTurn: { sourceRunId: 'R_new', speaker: 'ai' } })],
    ['anchored turn is a user turn', input(5, [11], reply(11), { anchorTurn: { sourceRunId: null, speaker: 'user' } })],
    ['start seq present but stored ambiguous', input(5, [6, 11], none('ambiguous'))],
    ['start seq present but stored unresolved', input(5, [6, 11], none('unresolved'))],
    ['reply after the last answer', input(5, [6, 11], reply(12), { anchorTurn: own })],
    ['ambiguous with a seq', input(null, [6, 11], { seq: 11, position: 'after', state: 'ambiguous' })],
    ['run_start without a start seq', input(null, [6, 11], runStart(5))],
    ['row of another chat', input(5, [11], reply(11), { docId: 'p.m.0001.0009-CH' })],
  ])('%s is only a candidate (not a confirmation)', (_name, c) => {
    expect(judgeAnchor(c).verdict).toBe('candidate')
  })

  it.each([
    ['start seq present, still run_start after an answer', input(9, [11], runStart(9)), 'delayed'],
    ['start seq present, state NULL with answers', input(9, [11], none(null)), 'delayed'],
    ['start seq present, reply at the first of two answers', input(5, [6, 11], reply(6)), 'delayed'],
    ['no start seq, single answer still unresolved', input(null, [11], none('unresolved')), 'delayed'],
    ['no start seq, several answers, single-answer reply left', input(null, [6, 11], reply(6)), 'delayed'],
  ])('%s is delayed (server re-resolution range)', (_name, c, verdict) => {
    const judged = judgeAnchor(c)
    expect(judged.verdict).toBe(verdict)
    if (c.startSeq === null && c.replySeqs.length > 1) expect(judged.expected).toEqual(none('ambiguous'))
  })

  it('an unread start seq is not judged by the table', () => {
    for (const stored of [none('ambiguous'), none(null), reply(11), runStart(9)]) {
      expect(judgeAnchor(input(undefined, [6, 11], stored))).toEqual({ verdict: 'undetermined', expected: null })
    }
  })
})

describe('0151 V6f: clues never confirm an attribution error, alone or together', () => {
  const subsets = <T extends string>(keys: readonly T[]): T[][] =>
    Array.from({ length: 2 ** keys.length }, (_, mask) => keys.filter((_, i) => mask & (1 << i)))

  it('every combination of clues (re-application, overlapping snapshots, names, OIDs, blob pairs, re-edit) stays unconfirmed', () => {
    for (const clues of subsets(ATTRIBUTION_CLUES)) {
      const evidence = Object.fromEntries(clues.map((key) => [key, true])) as AttributionEvidence
      expect(judgeAttribution(evidence)).toBe(clues.length ? 'unconfirmed_possibly_normal' : 'no_clue')
    }
  })

  it.each([
    ['(가) normal re-application: same blob pair, no overlap', { sameFileNames: true, sameBlobPairs: true }],
    ['(나) overlapping intervals, same change in both summaries', { overlappingIntervals: true, sameChangeInBothSummaries: true }],
    ['(다) same names, different trees', { sameFileNames: true, treeMismatch: true }],
    ['(라) re-edit: R_new.old == R_old.new', { sameFileNames: true, reEdit: true }],
  ])('%s is "unconfirmed (possibly normal)"', (_name, evidence) => {
    expect(judgeAttribution(evidence as AttributionEvidence)).toBe('unconfirmed_possibly_normal')
  })

  it.each(ATTRIBUTION_CONFIRMATIONS)('(마) confirmation evidence %s alone hands the case to a server investigation', (key) => {
    expect(judgeAttribution({ [key]: true })).toBe('confirmed_to_server_investigation')
    expect(judgeAttribution({ [key]: true, sameBlobPairs: true, overlappingIntervals: true })).toBe('confirmed_to_server_investigation')
  })
})
