// flowgate.v02test.0084 (F84-8, 시안 jvj9ff9t §1): when the server's automatic conflict resolution used up its tries, the
// card's merge window opens by itself — once per conflict (the session's merge id, else the attempt).
//
// It opens only for an exhaustion this page saw happen: the conflict was read here while still being worked on, or its
// last try ended after this page first saw the card (the server's `ended_at`). So a page opened or reloaded on an already
// exhausted conflict, or a Flow tab opened later on one, never opens it; a window the person closed is not opened again
// for that conflict; the notification, the Canvas card and the merge row still open it by hand.
// Reads of one card may overlap: only the newest answer decides, and an older answer that still shows the work going on
// lets an exhaustion read before it open the window. Reading never starts or stops anything on the server.
import { onScopeDispose, watch } from 'vue'
import { cardMergeRead, type CardMergeState } from '@/features/document'
import { mergeAutoEndedAt, mergeAutoExhausted, mergeConflictKey } from './mergeModel'

/** One card the auto-open looks at: a card of an open Flow whose node run is in a merge conflict. */
export interface MergeAutoCandidate {
  readonly flowId: string
  readonly nodeKey: string
  readonly attemptId: string | null
  /** What makes a new read worth it (the run's status / idle reason / event sequence, the integration reason). */
  readonly signature: string
}

export interface MergeAutoOpenOptions {
  /** The conflict cards of the open Flows now. */
  candidates: () => readonly MergeAutoCandidate[]
  /** Opens the card's merge window. */
  open: (flowId: string, nodeKey: string) => void
  /** Reads a card's merge state (tests pass one). */
  read?: (flowId: string, nodeKey: string) => Promise<CardMergeState>
  /** The clock in ms (tests pass one). */
  now?: () => number
}

const cardOf = (flowId: string, nodeKey: string) => `${flowId}\u0000${nodeKey}`

/** An exhaustion read but not opened (not seen happen yet), kept until a newer answer for the card supersedes it. */
interface Held {
  readonly seq: number
  readonly attempt: string
  readonly key: string
  readonly flowId: string
  readonly nodeKey: string
}

/**
 * Watches the conflict cards; a card is read again only when its signature changed, so a steady conflict costs no
 * requests. Returns nothing: the window is opened through `open`.
 */
export function useMergeAutoOpen(options: MergeAutoOpenOptions): void {
  const read = options.read ?? cardMergeRead
  const now = options.now ?? Date.now
  const lastSignature = new Map<string, string>()
  /** When this page first saw each card as a conflict candidate (ms, this page's clock), while it stays one. */
  const firstSeen = new Map<string, number>()
  const lifetimes = new Map<string, object>()
  /** Per card: the sequence of the newest read sent, and of the newest answer applied. */
  const sent = new Map<string, number>()
  const applied = new Map<string, number>()
  /** The attempts (card + attempt) read while their conflict was still being worked on. */
  const live = new Set<string>()
  /** The conflicts whose window already opened by itself in this page. */
  const opened = new Set<string>()
  /** Per card: an exhaustion read but not opened yet. */
  const held = new Map<string, Held>()
  let alive = true

  function openOnce(key: string, flowId: string, nodeKey: string): void {
    if (opened.has(key)) return
    opened.add(key)
    options.open(flowId, nodeKey)
  }

  function take(candidate: MergeAutoCandidate, card: string, seq: number, state: CardMergeState): void {
    const attempt = `${card}\u0000${state.attemptId ?? candidate.attemptId ?? ''}`
    const newest = seq > (applied.get(card) ?? 0)
    if (!mergeAutoExhausted(state)) {
      // Still worked on (any answer, even an older one: the page saw the work going on).
      if (state.state !== 'hidden') live.add(attempt)
      const hold = held.get(card)
      if (hold && hold.attempt === attempt && seq < hold.seq && live.has(attempt)) {
        // An older answer arrived after the exhaustion: the page did see this conflict before it ran out.
        held.delete(card)
        openOnce(hold.key, hold.flowId, hold.nodeKey)
      } else if (hold && seq > hold.seq) {
        held.delete(card)
      }
      if (newest) applied.set(card, seq)
      return
    }
    // An exhaustion older than the answer already applied for this card no longer decides.
    if (!newest) return
    applied.set(card, seq)
    const key = mergeConflictKey(candidate.flowId, candidate.nodeKey, state)
    if (!key || opened.has(key)) return
    const endedAt = mergeAutoEndedAt(state.autoResolve)
    const seenAt = firstSeen.get(card)
    const ranOutHere = endedAt !== null && seenAt !== undefined && endedAt >= seenAt
    if (live.has(attempt) || ranOutHere) {
      held.delete(card)
      openOnce(key, candidate.flowId, candidate.nodeKey)
      return
    }
    // Exhausted before this page saw the card (a reload, a tab opened later): not opened, unless an older answer shows
    // the work still going on.
    held.set(card, { seq, attempt, key, flowId: candidate.flowId, nodeKey: candidate.nodeKey })
  }

  async function check(candidate: MergeAutoCandidate, card: string, seq: number): Promise<void> {
    const lifetime = lifetimes.get(card)
    let state: CardMergeState
    try {
      state = await read(candidate.flowId, candidate.nodeKey)
    } catch {
      if (sent.get(card) === seq) lastSignature.delete(card)
      return
    }
    if (!alive || lifetimes.get(card) !== lifetime || !options.candidates().some(item =>
      cardOf(item.flowId, item.nodeKey) === card && item.attemptId === candidate.attemptId)) return
    if (candidate.attemptId && state.attemptId && candidate.attemptId !== state.attemptId) return
    take(candidate, card, seq, state)
  }

  watch(
    () => options.candidates().map((item) => [item.flowId, item.nodeKey, item.attemptId ?? '', item.signature].join('\u0001')).join('\u0002'),
    () => {
      const seen = new Set<string>()
      for (const candidate of options.candidates()) {
        const card = cardOf(candidate.flowId, candidate.nodeKey)
        seen.add(card)
        if (!firstSeen.has(card)) {
          firstSeen.set(card, now())
          lifetimes.set(card, {})
        }
        const signature = `${candidate.attemptId ?? ''}\u0001${candidate.signature}`
        if (lastSignature.get(card) === signature) continue
        lastSignature.set(card, signature)
        const seq = (sent.get(card) ?? 0) + 1
        sent.set(card, seq)
        void check(candidate, card, seq)
      }
      // A card that left (its conflict ended, its Flow tab closed) is new again when it comes back.
      for (const card of [...firstSeen.keys()]) {
        if (seen.has(card)) continue
        firstSeen.delete(card)
        lifetimes.delete(card)
        lastSignature.delete(card)
        held.delete(card)
        for (const attempt of live) if (attempt.startsWith(`${card}\u0000`)) live.delete(attempt)
      }
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    alive = false
  })
}
