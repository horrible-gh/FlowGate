// flowgate.v02.0048 T#9 — the settings tab's rules (D#1 §8, §10.2 T#9 row; D#2 §2.2 / §2.4). Pure functions
// over the target, the Flow's v0.1 sequence rows (`GET /workflow/sequence`, normalized by
// `@/features/document`) and the choices: which row the Node is, whether it can still be set, the
// choices of each field, whether anything changed, and what a running hop took from the row.
//
// Fields (D#2 §2.2): the run AI, the review count and the reviewer — the three settings the sequence row
// stores and a continuous hop reads. Restart, step time limit and review criteria have no row to store
// them (Q 402) and instruction use / after-review are not stored at all: none of them is offered.
//
// The row (D#2 §2.4): a Node's v0.1 slot names its row by `itemId`. A row can be set while it has not
// started (`status` pending) — a protected pending report row included, the server saves it in place; a
// started row is history (the server ignores its echo).
//
// Operations (D#1 §8.3 rule 0): reading is a query; setting a field and saving are changes. They are
// offered on a v0.1 Flow only (rules 2-4: the v0.1 Node settings are hidden elsewhere) and disabled with
// one reason — ownership conflict, revision view, disposed, AI running, then this feature's own: the row
// started, the row not in the sequence read, a save in flight; the reviewer also when the review count is
// 0, and saving when nothing changed.
import type { InspectorReadOnly } from '../../inspectorTarget'

/** The parts of a sequence row the rules read (the `@/features/document` SettingsRow). */
export interface SettingsRowFacts {
  readonly itemId: number
  readonly itemSeq: number | null
  readonly type: string
  readonly label: string
  readonly status: string
  readonly protected: boolean
  readonly providerId: string | null
  readonly providerDisplayName: string | null
  readonly providerRegistered: boolean | null
  readonly reviewCount: number
  readonly reviewerProviderId: string | null
  readonly reviewerProviderDisplayName: string | null
}

/** The Node's row: the one its v0.1 slot names; null without a slot or when the read does not hold it. */
export function settingsRowFor<T extends SettingsRowFacts>(
  items: readonly T[] | null,
  slot: { readonly itemId: number } | null,
): T | null {
  if (!items || !slot) return null
  return items.find((row) => row.itemId === slot.itemId) ?? null
}

/**
 * editable = not started (status pending; a protected pending report row is saved in place);
 * started = the row holds its result document — the server ignores what is sent for it.
 */
export type SettingsRowState = 'editable' | 'started'

export function settingsRowState(row: Pick<SettingsRowFacts, 'status'>): SettingsRowState {
  return row.status === 'pending' ? 'editable' : 'started'
}

/** The settings of one row; the display names follow the chosen AI (null = the project default). */
export interface SettingsDraft {
  readonly providerId: string | null
  readonly providerDisplayName: string | null
  readonly reviewCount: number
  readonly reviewerProviderId: string | null
  readonly reviewerProviderDisplayName: string | null
}

export function settingsDraftOf(row: SettingsRowFacts): SettingsDraft {
  return {
    providerId: row.providerId,
    providerDisplayName: row.providerDisplayName,
    reviewCount: row.reviewCount,
    reviewerProviderId: row.reviewerProviderId,
    reviewerProviderDisplayName: row.reviewerProviderDisplayName,
  }
}

/** Whether the draft differs from the row in a stored setting (names follow the ids). */
export function settingsChanged(row: SettingsRowFacts, draft: SettingsDraft | null): boolean {
  if (!draft) return false
  return (
    draft.providerId !== row.providerId ||
    draft.reviewCount !== row.reviewCount ||
    draft.reviewerProviderId !== row.reviewerProviderId
  )
}

export type SettingsField = 'provider' | 'reviewCount' | 'reviewer'

/** One AI choice (the `@/features/document` SettingsProvider). */
export interface SettingsProviderFacts {
  readonly id: string
  readonly name: string
}

/** The draft with one field set; an AI is named by its choice (or kept as stored when it is no choice). */
export function settingsDraftWith(
  draft: SettingsDraft,
  field: SettingsField,
  value: string,
  providers: readonly SettingsProviderFacts[],
): SettingsDraft {
  if (field === 'reviewCount') {
    const count = Number(value)
    return Number.isInteger(count) ? { ...draft, reviewCount: count } : draft
  }
  const id = value === '' ? null : value
  const current = field === 'provider' ? draft.providerId : draft.reviewerProviderId
  const currentName = field === 'provider' ? draft.providerDisplayName : draft.reviewerProviderDisplayName
  const name = id === null ? null : providers.find((provider) => provider.id === id)?.name ?? (id === current ? currentName : id)
  return field === 'provider'
    ? { ...draft, providerId: id, providerDisplayName: name }
    : { ...draft, reviewerProviderId: id, reviewerProviderDisplayName: name }
}

/** One option of a field; `key` + `params` is its label (under `gui.inspector.settings.`), or `label` as is. */
export interface SettingsOption {
  readonly value: string
  readonly key: string | null
  readonly params: Readonly<Record<string, string | number>>
  readonly label: string | null
}

/**
 * The run AI / reviewer options: the project default (value ''), the project's AI, and — when the row
 * holds an AI that is no longer offered — that AI as stored, marked, so the shown value stays the stored one.
 */
export function settingsProviderOptions(
  providers: readonly SettingsProviderFacts[],
  current: { readonly id: string | null; readonly name: string | null },
): SettingsOption[] {
  const options: SettingsOption[] = [{ value: '', key: 'option.default', params: {}, label: null }]
  for (const provider of providers) options.push({ value: provider.id, key: null, params: {}, label: provider.name })
  if (current.id !== null && !providers.some((provider) => provider.id === current.id)) {
    options.push({ value: current.id, key: 'option.missing', params: { name: current.name ?? current.id }, label: null })
  }
  return options
}

/** Label key of a review count: -1 until it passes, 0 none, n rounds. */
export function settingsReviewCountKey(count: number): string {
  if (count === -1) return 'count.until_pass'
  if (count === 0) return 'count.none'
  return 'count.times'
}

/**
 * The review count options: the server's set (-1, 0, 1..max), and a stored count outside it marked as
 * such (the server treats it as no review when the hop starts).
 */
export function settingsReviewCountOptions(counts: readonly number[], current: number): SettingsOption[] {
  const options: SettingsOption[] = counts.map((count) => ({ value: String(count), key: settingsReviewCountKey(count), params: { n: count }, label: null }))
  if (!counts.includes(current)) options.push({ value: String(current), key: 'count.out_of_range', params: { n: current }, label: null })
  return options
}

export type SettingsOperation = 'read' | SettingsField | 'save'

/** Reading is a query; setting a field and saving change the server state (D#1 §8.3 rule 0). */
export const SETTINGS_OPERATION_CLASS: Readonly<Record<SettingsOperation, 'change' | 'query'>> = {
  read: 'query',
  provider: 'change',
  reviewCount: 'change',
  reviewer: 'change',
  save: 'change',
}

/** What a refusal of the server's guard blocks until the state is read again or the Node changes. */
export type SettingsBlocked = 'disposed' | 'running' | null

export interface SettingsInput {
  readonly ownership: string | null
  readonly readOnly: InspectorReadOnly
  readonly groupDisposed: boolean
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  /** The last save was refused by the group guard (disposed / AI running). */
  readonly blocked: SettingsBlocked
  /** The Node's row state, or null when the read does not hold the row. */
  readonly row: SettingsRowState | null
  /** The review count shown (the draft's, else the row's). */
  readonly reviewCount: number
  /** The draft differs from the row. */
  readonly changed: boolean
  /** A save is waiting for the server or for the read after it. */
  readonly busy: boolean
}

export interface SettingsAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.settings.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type SettingsAvailability = Readonly<Record<SettingsOperation, SettingsAvailabilityItem>>

const HIDDEN: SettingsAvailabilityItem = { visible: false, enabled: false, reason: null }

/** Why the settings cannot be changed: one reason, in the order of D#1 §8.3 rule 6-1, then this feature's own. */
export function settingsDisabledReason(input: SettingsInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed || input.blocked === 'disposed') return 'reason.disposed'
  if (input.aiRunning || input.blocked === 'running') return 'reason.ai_running'
  if (input.row === null) return 'reason.no_row'
  if (input.row === 'started') return 'reason.started'
  if (input.busy) return 'reason.busy'
  return null
}

/**
 * Per operation: shown at all, usable now, and why not. The settings are the v0.1 Node settings — shown
 * on a v0.1 Flow only (D#1 §8.3 rules 2-4, §10.2: graph / unassigned / conflict hide them).
 */
export function settingsAvailability(input: SettingsInput): SettingsAvailability {
  const read: SettingsAvailabilityItem = { visible: true, enabled: true, reason: null }
  if (input.ownership !== 'v01') return { read, provider: HIDDEN, reviewCount: HIDDEN, reviewer: HIDDEN, save: HIDDEN }
  const common = settingsDisabledReason(input)
  const item = (reason: string | null): SettingsAvailabilityItem => ({ visible: true, enabled: reason === null, reason })
  return {
    read,
    provider: item(common),
    reviewCount: item(common),
    reviewer: item(common ?? (input.reviewCount === 0 ? 'reason.review_off' : null)),
    save: item(common ?? (input.changed ? null : 'reason.unchanged')),
  }
}

/** What a continuous hop runs with (the `@/features/document` SettingsHop). */
export interface SettingsHopFacts {
  readonly itemSeq: number | null
  readonly providerId: string | null
  readonly providerName: string | null
  readonly providerSource: string | null
  readonly reviewCount: number | null
  readonly reviewerProviderId: string | null
  readonly reviewerProviderName: string | null
}

/** The server's `selected_provider_source` when the hop took the row's run AI. */
export const SETTINGS_STORED_SOURCE = 'stored_sequence'

/**
 * Whether a hop took a setting from the row: `row` = it did, `other` = it runs with something else (a
 * request pick, a fallback), `default` = the row leaves it to the project default, `unknown` = the answer
 * does not tell.
 */
export type SettingsHopMatch = 'row' | 'other' | 'default' | 'unknown'

export interface SettingsHopView {
  readonly provider: SettingsHopMatch
  readonly providerName: string | null
  readonly reviewCount: SettingsHopMatch
  readonly count: number | null
  readonly reviewer: SettingsHopMatch
  readonly reviewerName: string | null
}

/**
 * What a running hop of this row took from it (D#2 §2.1 "실행 전달"), or null when the hop is another
 * row's. Run AI: the start answer says `selected_provider_source` (stored_sequence = the row's); a run
 * status does not, so the AI ids are compared. Review count: the hop's count against the row's. Reviewer:
 * compared when the row names one (none = the project default reviewer).
 */
export function settingsHopView(row: SettingsRowFacts, hop: SettingsHopFacts | null): SettingsHopView | null {
  if (!hop || hop.itemSeq === null || hop.itemSeq !== row.itemSeq) return null
  let provider: SettingsHopMatch
  if (hop.providerSource !== null) provider = hop.providerSource === SETTINGS_STORED_SOURCE ? 'row' : 'other'
  else if (row.providerId === null) provider = 'default'
  else if (hop.providerId === null) provider = 'unknown'
  else provider = hop.providerId === row.providerId ? 'row' : 'other'
  const reviewCount: SettingsHopMatch = hop.reviewCount === null ? 'unknown' : hop.reviewCount === row.reviewCount ? 'row' : 'other'
  let reviewer: SettingsHopMatch
  if (row.reviewCount === 0 && hop.reviewCount === 0) reviewer = 'row'
  else if (row.reviewerProviderId === null) reviewer = 'default'
  else if (hop.reviewerProviderId === null) reviewer = 'unknown'
  else reviewer = hop.reviewerProviderId === row.reviewerProviderId ? 'row' : 'other'
  return {
    provider,
    providerName: hop.providerName ?? hop.providerId,
    reviewCount,
    count: hop.reviewCount,
    reviewer,
    reviewerName: hop.reviewerProviderName ?? hop.reviewerProviderId,
  }
}

/** Kinds of a refused save or a failed read (the `@/features/document` settingsErrorOf kinds). */
export type SettingsFailureKind =
  | 'disposed'
  | 'running'
  | 'owner'
  | 'stale'
  | 'not_decided'
  | 'invalid'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'other'

/** What a refused save blocks until the state is read again: the group guard's refusals only. */
export function settingsBlockedBy(kind: SettingsFailureKind): SettingsBlocked {
  return kind === 'disposed' || kind === 'running' ? kind : null
}

/** Message key of a refused save; the rows and the guard are read again after it is shown. */
export function settingsFailureKey(kind: SettingsFailureKind): string {
  return `failure.${kind}`
}

/** Message key of a failed read of the rows. */
export function settingsErrorKey(kind: SettingsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' || kind === 'not_decided' ? `error.${kind}` : 'error.other'
}
