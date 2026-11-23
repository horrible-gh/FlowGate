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
  /**
   * 0067 card 3: [지금 멈추고 다시 답하기] was pressed while this message waited. The run is being stopped and, once its
   * stop is confirmed, ONE call answers the waiting messages. Kept with the mark (not on the screen) so an F5 or another
   * tab between the press and the confirmed stop still ends in the call — and so a plain [중지] (which clears it) does not.
   */
  replyNow?: boolean
  /**
   * 0067 card 5 (#2): the chat's call was refused because a card holds the Flow — its Graph run is running, paused or
   * stopping (409 `graph_run_locked`), or another run holds the group's AI slot (409 `run_in_progress` of a run that is
   * not this chat's). It waits for the Flow, not for an answer of the chat: once the run is over (or the slot is free)
   * ONE call answers every waiting message. Kept with the mark so F5 / another chat / another tab keep waiting.
   */
  flow?: boolean
}

/** Why the follow-up waits: the chat's own answer (`run`), or a card's run of the Flow (`flow_run`, card 5). */
export type FollowUpReason = 'run' | 'flow_run'

/** The waiting line the screen shows and the other cards use (card 3's button, card 4's wait). */
export interface FollowUpView {
  chatId: string
  reason: FollowUpReason
  /** The waiting messages' seqs, oldest first (only the person's messages on screen). */
  waitingSeqs: number[]
  count: number
  /** [지금 멈추고 다시 답하기] was pressed: the run is being stopped, the new call follows (the button is not pressable again). */
  stopping: boolean
  /** The running call may edit files (edit / edit-once): stopping it can leave an edit half done. */
  editsFiles: boolean
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
      ...(m.replyNow ? { replyNow: true } : {}), ...(m.flow ? { flow: true } : {}),
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
  next.push({ ...mark, ...(old?.auto ? { auto: true } : {}), ...(old?.replyNow ? { replyNow: true } : {}) })
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

/** Whether the press of [지금 멈추고 다시 답하기] is still waiting on a stop (a flagged mark nobody has answered). */
export function replyNowRequested(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[]): boolean {
  return waitingFollowUps(turns, marks).some((m) => m.replyNow)
}

/** The same marks without the press of [지금 멈추고 다시 답하기] (a refused stop, or a plain [중지] after it). */
export function withoutReplyNow(marks: readonly FollowUpMark[]): FollowUpMark[] {
  return marks.map((m) => {
    const rest = { ...m }
    delete rest.replyNow
    return rest
  })
}

/** 0067 card 5: the waiting marks that wait for the Flow (a card's run, or the group's AI slot), oldest first. */
export function flowWaitingFollowUps(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[]): FollowUpMark[] {
  return waitingFollowUps(turns, marks).filter((m) => m.flow)
}

/**
 * 0067 card 5: the chat's call met the Flow (409 `graph_run_locked`, or a `run_in_progress` held by a card). The marks
 * this call answered (a follow-up: `auto`, not answered yet) wait again, every waiting mark now waits for the Flow, and
 * `mark` (the message this call was for, or the chat's latest turn for a bare [AI 호출]) joins them.
 */
export function withFlowWait(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[], mark: FollowUpMark | null): FollowUpMark[] {
  const back = marks.map((m) => {
    if (!m.auto || followUpAnswered(turns, m.seq)) return m
    const rest = { ...m }
    delete rest.auto
    return rest
  })
  const next = mark ? withFollowUp(back, mark) : back
  return next.map((m) => (m.auto || followUpAnswered(turns, m.seq) ? m : { ...m, flow: true }))
}

/** 0067 card 5: the Flow let the call through — its flow marks are now called for (`auto`, answered at most once). */
export function flowCalled(marks: readonly FollowUpMark[]): FollowUpMark[] {
  return marks.map((m) => {
    if (!m.flow) return m
    const rest = { ...m, auto: true }
    delete rest.flow
    return rest
  })
}

/** 0067 card 5: the wait for the Flow is given up ([중지] on the waiting line, or a call that failed otherwise). */
export function withoutFlowWait(marks: readonly FollowUpMark[]): FollowUpMark[] {
  return marks.filter((m) => !m.flow)
}

/** The person's messages among the waiting marks (seqs, oldest first). */
export function waitingMessageSeqs(turns: readonly ConversationTurn[], marks: readonly FollowUpMark[]): number[] {
  const seqs = new Set(waitingFollowUps(turns, marks).map((m) => m.seq))
  return turns.filter((t) => t.speaker === 'user' && !t.deletedAt && seqs.has(t.seq)).map((t) => t.seq)
}

/** How a chat run ended, as far as the follow-up is concerned. */
export type RunEnd = 'done' | 'stopped' | 'failed'
