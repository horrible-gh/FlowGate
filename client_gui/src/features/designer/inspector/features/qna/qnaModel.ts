// flowgate.v02.0048 T#7 — the Q&A tab's rules (D#1 §8, §10.2 T#7 row, §10.4). Pure functions over
// the questions of the target document (`GET /q/{doc_id}`, normalized by `@/features/document`):
// which questions wait for the user, the tab number, the display order, who asked and who answered,
// and whether answering is possible now.
//
// Waiting for the user (D#1 §10.4, fixed here from the response fields): a question an AI asked
// (`asker_kind = 'ai'`) that has no answer yet (`answer_count = 0` and no `answers`). A question a
// person asked waits for the AI or the team, not for the user, and an answered question waits for
// nobody. The tab number is that count, shown only when it is at least one.
//
// Operations (D#1 §8.3 rule 0): reading the questions is a query; answering is a change, so it is
// disabled with one reason — ownership conflict, revision view, disposed group, AI running (the
// server refuses a person's write with 423 while an AI run owns the group), then an answer in flight.
// Not offered (D#1 §8.4): team handling, resuming after an answer, the AI answer request.
//
// flowgate.v02.0053 T#6 (0053 CH §2) — a person asks too: the question line at the top of the tab
// registers a question (asker 'human'). Asking is a change with the same reasons as answering, then a
// question already on its way. AI-asked questions are answered by the user as before.
// flowgate.v02.0056 F1 (0056 CH §3) — registering and asking an AI are two steps: an open question's
// [AI 답변 요청] asks the chosen AI to answer it (`ai_request`), a change with the same reasons, then a
// request already on its way.
import type { InspectorReadOnly } from '../../inspectorTarget'

/** The parts of a question the rules read (the `@/features/document` QaItem). */
export interface QnaItemFacts {
  readonly id: number
  readonly seq: number | null
  readonly title: string | null
  readonly body: string
  readonly askerKind: string
  readonly options: readonly { readonly id: string; readonly label: string }[]
  readonly answerCount: number
  readonly answers: readonly QnaAnswerFacts[]
  readonly provider: QnaProviderFacts | null
}

export interface QnaAnswerFacts {
  readonly id: number | null
  readonly body: string
  readonly authorKind: string
  readonly selectedOptionIds: readonly string[]
  readonly provider: QnaProviderFacts | null
}

export interface QnaProviderFacts {
  readonly providerName: string | null
}

export type QnaOperation = 'read' | 'answer' | 'ask' | 'ai_request'

/** Reading is a query; answering, asking and asking an AI change the server state (D#1 §8.3 rule 0). */
export const QNA_OPERATION_CLASS: Readonly<Record<QnaOperation, 'change' | 'query'>> = {
  read: 'query',
  answer: 'change',
  ask: 'change',
  ai_request: 'change',
}

/** A question has an answer (the counter or the answers the server sent). */
export function qnaAnswered(item: Pick<QnaItemFacts, 'answerCount' | 'answers'>): boolean {
  return item.answerCount > 0 || item.answers.length > 0
}

/** A question waits for the user: an AI asked it and nobody answered yet. */
export function qnaWaitingForUser(item: Pick<QnaItemFacts, 'askerKind' | 'answerCount' | 'answers'>): boolean {
  return item.askerKind === 'ai' && !qnaAnswered(item)
}

/** How many questions wait for the user; null before the questions are read. */
export function qnaWaitingCount(items: readonly Pick<QnaItemFacts, 'askerKind' | 'answerCount' | 'answers'>[] | null): number | null {
  return items === null ? null : items.filter(qnaWaitingForUser).length
}

/** The Q&A tab number: the waiting count when it is at least one; none otherwise (or not read). */
export function qnaTabCount(items: readonly Pick<QnaItemFacts, 'askerKind' | 'answerCount' | 'answers'>[] | null): number | null {
  const count = qnaWaitingCount(items)
  return count !== null && count > 0 ? count : null
}

/** Where a question stands: waiting for the user, open (a person asked, no answer), answered. */
export type QnaItemState = 'waiting' | 'open' | 'answered'

export function qnaItemState(item: Pick<QnaItemFacts, 'askerKind' | 'answerCount' | 'answers'>): QnaItemState {
  if (qnaAnswered(item)) return 'answered'
  return qnaWaitingForUser(item) ? 'waiting' : 'open'
}

const STATE_RANK: Readonly<Record<QnaItemState, number>> = { waiting: 0, open: 1, answered: 2 }

/** Display order: waiting first, then open, then answered; within each, by seq (then id). */
export function qnaSorted<T extends Pick<QnaItemFacts, 'id' | 'seq' | 'askerKind' | 'answerCount' | 'answers'>>(items: readonly T[]): T[] {
  return [...items].sort(
    (a, b) =>
      STATE_RANK[qnaItemState(a)] - STATE_RANK[qnaItemState(b)] ||
      (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER) ||
      a.id - b.id,
  )
}

/** Who asked or answered, as a message key (under `gui.inspector.qna.`) and its parameters. */
export interface QnaSide {
  readonly kind: 'ai' | 'human' | 'unknown'
  readonly key: string
  readonly params: Readonly<Record<string, string>>
}

/**
 * The side of an asker / author kind: a person, an AI with its provider when the server knows the
 * run, or an AI whose run is not known (an external worker) — never a guessed provider name.
 */
export function qnaSide(kind: string, provider: QnaProviderFacts | null): QnaSide {
  if (kind === 'human') return { kind: 'human', key: 'side.human', params: {} }
  if (kind === 'ai') {
    const name = provider?.providerName ?? null
    return name ? { kind: 'ai', key: 'side.ai_named', params: { name } } : { kind: 'ai', key: 'side.ai', params: {} }
  }
  return { kind: 'unknown', key: 'side.unknown', params: { kind } }
}

export interface QnaAnswerView {
  readonly key: string
  readonly body: string
  readonly side: QnaSide
  /** Labels of the options this answer picked. */
  readonly picked: readonly string[]
}

export interface QnaItemView {
  readonly id: number
  readonly seq: number | null
  readonly title: string | null
  readonly body: string
  readonly state: QnaItemState
  readonly asker: QnaSide
  readonly options: readonly { readonly id: string; readonly label: string }[]
  readonly answers: readonly QnaAnswerView[]
  /** An answer form is offered (the question has no answer yet). */
  readonly answerable: boolean
}

export function qnaItemView(item: QnaItemFacts): QnaItemView {
  const labels = new Map(item.options.map((option) => [option.id, option.label]))
  const state = qnaItemState(item)
  return {
    id: item.id,
    seq: item.seq,
    title: item.title,
    body: item.body,
    state,
    asker: qnaSide(item.askerKind, item.provider),
    options: item.options,
    answers: item.answers.map((answer, index) => ({
      key: answer.id === null ? `i${index}` : `a${answer.id}`,
      body: answer.body,
      side: qnaSide(answer.authorKind, answer.provider),
      picked: answer.selectedOptionIds.flatMap((id) => labels.get(id) ?? []),
    })),
    answerable: state !== 'answered',
  }
}

/** The tab's list: every question in display order. */
export function qnaView(items: readonly QnaItemFacts[]): QnaItemView[] {
  return qnaSorted(items).map(qnaItemView)
}

export interface QnaInput {
  readonly readOnly: InspectorReadOnly
  readonly groupDisposed: boolean
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  /** An answer is waiting for the server. */
  readonly busy: boolean
  /** A question is waiting for the server. */
  readonly asking?: boolean
  /** An AI answer request is waiting for the server. */
  readonly requesting?: boolean
}

export interface QnaAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.qna.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type QnaAvailability = Readonly<Record<QnaOperation, QnaAvailabilityItem>>

/** Why answering is disabled: one reason, in the order of D#1 §8.3 rule 6-1. */
export function qnaDisabledReason(input: QnaInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed) return 'reason.disposed'
  if (input.aiRunning) return 'reason.ai_running'
  if (input.busy) return 'reason.busy'
  return null
}

/**
 * Why asking is disabled, as a key under `ask_reason.` (worded for a question): the answering reasons
 * up to the AI run, then a question on its way, then an answer on its way.
 */
export function qnaAskDisabledReason(input: QnaInput): string | null {
  const reason = qnaDisabledReason({ ...input, busy: false })
  if (reason !== null) return reason.replace(/^reason\./, 'ask_reason.')
  if (input.asking) return 'ask_reason.asking'
  return input.busy ? 'ask_reason.busy' : null
}

/**
 * Why asking an AI to answer is disabled, as a key under `ai_reason.`: the answering reasons up to the
 * AI run, then a request on its way, then a question on its way, then an answer on its way.
 */
export function qnaAiDisabledReason(input: QnaInput): string | null {
  const reason = qnaDisabledReason({ ...input, busy: false })
  if (reason !== null) return reason.replace(/^reason\./, 'ai_reason.')
  if (input.requesting) return 'ai_reason.requesting'
  if (input.asking) return 'ai_reason.asking'
  return input.busy ? 'ai_reason.busy' : null
}

/**
 * Reading is always possible; answering, asking and asking an AI are offered in every ownership (a
 * document feature, D#1 §8.3 rules 2-4) and disabled with the first reason that holds.
 */
export function qnaAvailability(input: QnaInput): QnaAvailability {
  const reason = qnaDisabledReason(input)
  const ask = qnaAskDisabledReason(input)
  const ai = qnaAiDisabledReason(input)
  return {
    read: { visible: true, enabled: true, reason: null },
    answer: { visible: true, enabled: reason === null, reason },
    ask: { visible: true, enabled: ask === null, reason: ask },
    ai_request: { visible: true, enabled: ai === null, reason: ai },
  }
}

/** A question may be sent: text that is not only blanks. */
export function qnaAskReady(body: string): boolean {
  return body.trim() !== ''
}

/**
 * An answer may be sent: prose that is not only blanks, or one picked option of the question (the
 * server stores a pick alone with the option's label as the body).
 */
export function qnaAnswerReady(item: Pick<QnaItemFacts, 'options'>, body: string, picked: readonly string[]): boolean {
  if (picked.length > 1) return false
  if (picked.length === 1 && !item.options.some((option) => option.id === picked[0])) return false
  return body.trim() !== '' || picked.length === 1
}

/** Kinds of a refused call or a failed read (the `@/features/document` qaErrorOf kinds). */
export type QnaFailureKind = 'running' | 'no_provider' | 'disposed' | 'forbidden' | 'not_found' | 'invalid' | 'network' | 'other'

/** Message key of a refused answer; the questions are read again after it is shown. */
export function qnaFailureKey(kind: QnaFailureKind): string {
  return `failure.${kind}`
}

/** Message key of a refused question (nothing stored). */
export function qnaAskFailureKey(kind: QnaFailureKind): string {
  return kind === 'no_provider' ? 'ask_failure.other' : `ask_failure.${kind}`
}

/** Message key of a refused AI answer request (the question stays without an answer). */
export function qnaAiFailureKey(kind: QnaFailureKind): string {
  return kind === 'invalid' || kind === 'other' ? 'ai_failure.other' : `ai_failure.${kind}`
}

/** Message key of a failed read of the questions. */
export function qnaErrorKey(kind: QnaFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' ? `error.${kind}` : 'error.other'
}
