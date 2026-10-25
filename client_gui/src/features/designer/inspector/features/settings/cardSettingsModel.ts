// flowgate.v02.0053 T#7 — the settings of a Graph card of the run / test kinds (조사 · 작업 · 설계 · Custom ·
// 테스트), as the T#2 card settings keep them (`GET / PATCH /flows/{flow_id}/nodes/{node_key}/settings`, one
// value per change, saved at once — v7 has no save button). Pure functions over the card's settings read
// (value · default · choices of each field), the values sent and not answered yet, and the AI choices.
//
// Fields (T#2 `fields._FIELDS`, in v7 order): run — instruction use / omit, instruction_ai (flowgate.v02.0056
// O2 0007-TR §4-1: the 지시 AI, shown while the instruction is used), run_ai, restart, review_ai, review_count,
// and under 고급 time_limit and review_basis; test — instruction and the 시나리오 작성 block's run_ai (작성 AI),
// review_ai and review_count. A new card and a done card are read and changed the same way; the card cannot
// change while it runs (`locked`). flowgate.v02.0056 F2-1: the progress-after-review choice is no setting any
// more (O1 0005-TR §1-1, §4-3 — the server always goes on as the run mode says).
//
// The values: an AI is an id (none = the node's assignee AI / the project's default reviewer / for the 지시 AI the
// run AI then the assignee AI, sent as null);
// restart and review count are the server's repeat choices (-1 until done / until it passes, 0 none, n);
// the time limit is minutes; the review basis is type / last.
//
// flowgate.v02.0072 T#3 — plan: a Graph work plan (WP) card's settings — the AI 사전 지시서 작성 switch
// (`pre_instruction_auto`, true / false, off by default) and the same run block (run_ai shown as 작업 AI, restart,
// review_ai, review_count, time_limit, review_basis). They are the card's values only: not the plan's Steps' AI /
// review AI inside the WP document, nothing runs from them yet, and the switch deletes no Step's pre-instruction.
import type { InspectorReadOnly } from '../../inspectorTarget'
import { settingsReviewCountKey, type SettingsOption } from './settingsModel'

/** One field of the card settings read (the `@/features/document` SettingsNodeField). */
export interface CardSettingsFieldFacts {
  readonly value: unknown
  readonly choices: readonly unknown[] | null
}

/** The card settings read (the `@/features/document` SettingsNode). */
export interface CardSettingsFacts {
  readonly cardKind: string | null
  readonly locked: boolean
  readonly fields: Readonly<Record<string, CardSettingsFieldFacts>>
}

export type CardSettingsField =
  | 'instruction'
  | 'instruction_ai'
  | 'run_ai'
  | 'restart'
  | 'review_ai'
  | 'review_count'
  | 'time_limit'
  | 'review_basis'
  | 'pre_instruction_auto'
  | 'result_cause'
  | 'follow_scope'

/** The fields of each kind, in v7 order. */
export const CARD_SETTINGS_FIELDS: Readonly<Record<'run' | 'test' | 'plan', readonly CardSettingsField[]>> = {
  run: ['instruction', 'instruction_ai', 'run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'review_basis'],
  test: ['instruction', 'run_ai', 'review_ai', 'review_count', 'result_cause', 'follow_scope'],
  plan: ['pre_instruction_auto', 'run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'review_basis'],
}

/** flowgate.v02.0072 T#3 — a switch's value as shown: on only for a stored (or sent) true. */
export function cardSettingsSwitchOn(value: unknown): boolean {
  return value === true
}

/** Fields whose value is a number (sent as one). */
const NUMBER_FIELDS: readonly CardSettingsField[] = ['restart', 'review_count', 'time_limit']
/** Fields whose value is an AI id; none is null (sent as null, the default). */
const AI_FIELDS: readonly CardSettingsField[] = ['instruction_ai', 'run_ai', 'review_ai']

/** The AI fields (a select of the project's AI with "none" first). */
export type CardSettingsAiField = 'instruction_ai' | 'run_ai' | 'review_ai'

export function cardSettingsIsAiField(field: CardSettingsField): field is CardSettingsAiField {
  return AI_FIELDS.includes(field)
}

/**
 * flowgate.v02.0056 F2-1 (O2 0007-TR §4-1, 0056 CH turn 6 §3): the 지시 AI is a RUN card's, shown only while its
 * instruction is used — an omitted instruction has no 지시서 step, so the server does not read it. Before the
 * card is read (a card just placed, a read on its way) the value is unknown and the row is drawn with the rest of
 * the form (off), as the server default is `use`.
 */
export function cardSettingsInstructionAiShown(kind: 'run' | 'test', instruction: unknown): boolean {
  return kind === 'run' && (instruction === 'use' || instruction === null || instruction === undefined)
}

/** The value a field shows: the one sent and not answered yet, else the read's (stored, else default). */
export function cardSettingsValue(
  node: CardSettingsFacts | null,
  pending: Readonly<Record<string, unknown>>,
  field: CardSettingsField,
): unknown {
  if (field in pending) return pending[field]
  return node?.fields[field]?.value ?? null
}

/** A value as a select / segment holds it: '' for none. */
export function cardSettingsText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

/** The value a select / segment choice stands for, as the server takes it (undefined: not a value). */
export function cardSettingsValueOf(field: CardSettingsField, text: string): unknown {
  if (AI_FIELDS.includes(field)) return text === '' ? null : text
  if (NUMBER_FIELDS.includes(field)) {
    const number = Number(text)
    return text !== '' && Number.isInteger(number) ? number : undefined
  }
  return text === '' ? undefined : text
}

/** One AI the run / review AI may be set to (the `@/features/document` SettingsProvider). */
export interface CardSettingsProviderFacts {
  readonly id: string
  readonly name: string
}

/**
 * The AI options: none first (`option.run_default` = the node's AI, `option.review_default` = the project's
 * reviewer, `option.instruction_default` = the run AI, then the node's AI), the project's AI, and a stored AI no
 * longer offered — marked, so the shown value stays the stored one.
 */
const AI_NONE_KEYS: Readonly<Record<CardSettingsAiField, string>> = {
  instruction_ai: 'option.instruction_default',
  run_ai: 'option.run_default',
  review_ai: 'option.review_default',
}

export function cardSettingsAiOptions(
  field: CardSettingsAiField,
  providers: readonly CardSettingsProviderFacts[],
  current: unknown,
): SettingsOption[] {
  const options: SettingsOption[] = [{ value: '', key: AI_NONE_KEYS[field], params: {}, label: null }]
  for (const provider of providers) options.push({ value: provider.id, key: null, params: {}, label: provider.name })
  if (typeof current === 'string' && current && !providers.some((provider) => provider.id === current)) {
    options.push({ value: current, key: 'option.missing', params: { name: current }, label: null })
  }
  return options
}

/** Label key of a repeat count: restart -1 runs until done, review -1 until it passes. */
export function cardSettingsCountKey(field: 'restart' | 'review_count', count: number): string {
  if (field === 'restart' && count === -1) return 'count.until_done'
  return settingsReviewCountKey(count)
}

const integers = (choices: readonly unknown[] | null | undefined): number[] =>
  (choices ?? []).filter((choice): choice is number => typeof choice === 'number' && Number.isInteger(choice))

/** The options of a choice field, in the server's order; a value outside them stays shown, marked. */
export function cardSettingsChoiceOptions(field: CardSettingsField, choices: readonly unknown[] | null | undefined, current: unknown): SettingsOption[] {
  let options: SettingsOption[]
  if (field === 'restart' || field === 'review_count') {
    options = integers(choices).map((count) => ({ value: String(count), key: cardSettingsCountKey(field, count), params: { n: count }, label: null }))
  } else if (field === 'time_limit') {
    options = integers(choices).map((minutes) => ({ value: String(minutes), key: 'option.minutes', params: { n: minutes }, label: null }))
  } else {
    options = (choices ?? []).filter((choice): choice is string => typeof choice === 'string').map((choice) => ({ value: choice, key: `${field}.${choice}`, params: {}, label: null }))
  }
  const shown = cardSettingsText(current)
  if (shown !== '' && !options.some((option) => option.value === shown)) options.push({ value: shown, key: null, params: {}, label: shown })
  return options
}

/** What a refused change blocks until the card is read again on another Node: the disposed group only. */
export type CardSettingsBlocked = 'disposed' | null

export interface CardSettingsInput {
  readonly readOnly: InspectorReadOnly
  /** The card was just placed: its key is not the server's yet (`tmp_`). */
  readonly placing: boolean
  /** The card's settings were read (a failed or running read leaves the form drawn, off). */
  readonly read: boolean
  /** The card runs (server `locked`): its AI already took the settings. */
  readonly locked: boolean
  readonly blocked: CardSettingsBlocked
}

/** Why the card's settings cannot change now (message key under `gui.inspector.settings.`), or null. */
export function cardSettingsDisabledReason(input: CardSettingsInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.blocked === 'disposed') return 'reason.disposed'
  if (input.placing) return 'reason.placing'
  if (input.locked) return 'reason.locked'
  if (!input.read) return 'reason.not_read'
  return null
}

/** The review AI is not used with no review (review count 0). */
export function cardSettingsReviewOff(reviewCount: unknown): boolean {
  return reviewCount === 0
}

/** Kinds of a refused change or a failed read (the `@/features/document` settingsNodeErrorOf kinds). */
export type CardSettingsFailureKind = 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

/** 422 reasons a refused value has its own sentence for (O2 0007-TR §4-1). */
export const CARD_SETTINGS_INVALID_REASONS: readonly string[] = ['unknown_ai', 'not_a_text', 'invalid_text', 'not_a_setting_of_this_card', 'not_a_boolean']

/**
 * Message key of a refused change (the tooltip of the field's ! icon). flowgate.v02.0056 F2-1: a refused value
 * (422) says why when the server named a known reason (`errors[].reason`).
 */
export function cardSettingsFailureKey(kind: CardSettingsFailureKind, reason: string | null = null): string {
  if (kind === 'invalid' && reason !== null && CARD_SETTINGS_INVALID_REASONS.includes(reason)) return `card_failure.invalid_reason.${reason}`
  return `card_failure.${kind}`
}

/** flowgate.v02.0056 F2-1: the fields whose ! tooltip also carries the server's own message (the 0053 ones keep theirs). */
export const CARD_SETTINGS_SERVER_MESSAGE_FIELDS: readonly string[] = ['instruction_ai']

/**
 * The ! tooltip of a refused change: the code's sentence, then — on a field that shows it — the server's message
 * as it sent it (x-locale) when it says more than the sentence.
 */
export function cardSettingsFailureText(field: string, sentence: string, message: string | null): string {
  if (!CARD_SETTINGS_SERVER_MESSAGE_FIELDS.includes(field)) return sentence
  const more = message?.trim() ?? ''
  return more && more !== sentence ? `${sentence} — ${more}` : sentence
}

/** Message key of a failed read of the card's settings. */
export function cardSettingsErrorKey(kind: CardSettingsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' || kind === 'unavailable' ? `card_error.${kind}` : 'card_error.other'
}

export function cardSettingsBlockedBy(kind: CardSettingsFailureKind): CardSettingsBlocked {
  return kind === 'disposed' ? 'disposed' : null
}
