// flowgate.v02test.0067 #46: the messages that wait for the chat's next answer ("이어서 답하기").
//
// A message sent with [AI 호출] while the chat's AI is answering is stored at once, but the AI that answers did not
// read it: its call waits for that answer to end and then runs once for every waiting message. Which messages wait is
// part of the chat, not of the screen — F5, another chat or Flow and another tab of this browser must still answer
// them — so the list is kept here in localStorage, next to the chat's draft and its local errors.
//
// Why not on the server: the server stores no send action per turn ([AI 호출] and [저장만] look the same there) and
// this card changes the screen only. What the server does keep decides whether a waiting message was answered after
// all: an AI turn whose `based_on_seq` reaches it read it; `stale_since_seq` says from which message on it did not.
//
// A mark is answered at most once automatically (`auto`): a follow-up that ends without covering it does not call
// again, so a failing AI is never called in a loop.
import type { ConversationTurn, ProviderOrigin } from './types'

export interface FollowUpMark {
  /** The stored message sent with [AI 호출]. */
  seq: number
  providerId: string
  providerOrigin?: ProviderOrigin
  /** When it was queued (ms, this browser's clock). */
  at: number
  /** The follow-up for it has been started once; it is not started again. */
  auto?: boolean
}

/** Why the follow-up waits: the chat's own answer (`run`). Card 4 adds a Flow run (`flow_run`). */
export type FollowUpReason = 'run' | 'flow_run'

/** The waiting line the screen shows and the other cards use (card 3's button, card 4's wait). */
export interface FollowUpView {
  chatId: string
  reason: FollowUpReason
  /** The waiting messages' seqs, oldest first (only the person's messages on screen). */
  waitingSeqs: number[]
  count: number
}

const MAX_AGE_MS = 24 * 60 * 60 * 1000
const STORAGE_LIMIT = 50

export function followUpStorageKey(userId: string, chatId: string): string {
  return `flowgate.user.${userId}.chat.followups.${chatId}`
}

function validMark(value: unknown): value is FollowUpMark {
  const m = value as Partial<FollowUpMark> | null
  return !!m && typeof m.seq === 'number' && Number.isFinite(m.seq) && m.seq >= 0 &&
    typeof m.providerId === 'string' && m.providerId.length > 0 && typeof m.at === 'number' && Number.isFinite(m.at)
}

/** The chat's marks (oldest first); expired / malformed ones are left out. [] when storage is unavailable. */
export function readFollowUps(userId: string, chatId: string, now = Date.now()): FollowUpMark[] {
  try {
    const raw = localStorage.getItem(followUpStorageKey(userId, chatId))
    const list = raw ? JSON.parse(raw) : []
    if (!Array.isArray(list)) return []
    return list.filter(validMark).filter((m) => now - m.at < MAX_AGE_MS).sort((a, b) => a.seq - b.seq)
  } catch {
    return []
  }
}

/** Replaces the chat's marks (none: the key is removed). Best effort: false when storage failed. */
export function writeFollowUps(userId: string, chatId: string, marks: readonly FollowUpMark[]): boolean {
  try {
    const key = followUpStorageKey(userId, chatId)
    if (!marks.length) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(marks.slice(-STORAGE_LIMIT).map((m) => ({
      seq: m.seq, providerId: m.providerId, at: m.at,
      ...(m.providerOrigin ? { providerOrigin: m.providerOrigin } : {}), ...(m.auto ? { auto: true } : {}),
    }))))
    return true
  } catch {
    return false
  }
}

/** Adds (or refreshes the AI of) one mark; a mark already started automatically keeps that. */
export function withFollowUp(marks: readonly FollowUpMark[], mark: FollowUpMark): FollowUpMark[] {
  const old = marks.find((m) => m.seq === mark.seq)
  const next = marks.filter((m) => m.seq !== mark.seq)
  next.push(old?.auto ? { ...mark, auto: true } : mark)
  return next.sort((a, b) => a.seq - b.seq)
}

/**
 * The seq an AI turn read up to: `based_on_seq`; without it, the message before `stale_since_seq` (the server's mark of
 * the first message it did not see). Without either nothing is known (-1): such an answer covers no waiting message.
 */
export function turnReadUpTo(turn: ConversationTurn): number {
  if (turn.basedOnSeq != null) return turn.basedOnSeq
  if (turn.staleSinceSeq != null) return turn.staleSinceSeq - 1
  return -1
}

/** Whether an AI answer stored after `seq` read it. */
export function followUpAnswered(turns: readonly ConversationTurn[], seq: number): boolean {
  return turns.some((t) => t.speaker === 'ai' && !t.deletedAt && t.seq > seq && turnReadUpTo(t) >= seq)
}

/** The marks still waiting for an answer: not answered yet, and not already called for once. */
export function waitingFollowUps(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[]): FollowUpMark[] {
  return marks.filter((m) => !m.auto && !followUpAnswered(turns, m.seq))
}

/** The person's messages among the waiting marks (seqs, oldest first). */
export function waitingMessageSeqs(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[]): number[] {
  const seqs = new Set(waitingFollowUps(turns, marks).map((m) => m.seq))
  return turns.filter((t) => t.speaker === 'user' && !t.deletedAt && seqs.has(t.seq)).map((t) => t.seq)
}

/** How a chat run ended, as far as the follow-up is concerned. */
export type RunEnd = 'done' | 'stopped' | 'failed'
