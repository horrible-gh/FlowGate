// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's settings tab (0035 v7 reqSettings): AI 위임
// 끄기 / 켜기, and while ON its 범위 (끝까지 / 계획 확인 후), AI Team and the 안전 한도 (최대 생성 Node ·
// 총 검수 · 재작업 · 카드별 재시도 · 재계획). Pure functions.
// flowgate.v02.0053 T#7 — a Graph requirement card keeps these values itself (T#2 card settings: delegation,
// scope, team, limit_* — values only, the delegation engine is out of this scope, 0053 CH): each change is
// saved at once and the answer shown. A limit with no value is the system default (`기본`). A v0.1 Flow's
// requirement Node has no card to keep them: it stays drawn OFF and off, its reason a tooltip. A failed first
// read is an error icon with a retry, not an OFF form the server may not hold.
import type { InspectorReadOnly } from '../../inspectorTarget'

export type RequirementDelegation = 'off' | 'on'
export type RequirementScope = 'end' | 'confirm'

export const REQUIREMENT_LIMIT_KEYS = ['max_nodes', 'total_reviews', 'retries_per_card', 'replans'] as const

export type RequirementLimitKey = (typeof REQUIREMENT_LIMIT_KEYS)[number]

/** The card setting (T#2 field) of each limit. */
export const REQUIREMENT_LIMIT_FIELDS: Readonly<Record<RequirementLimitKey, string>> = {
  max_nodes: 'limit_max_nodes',
  total_reviews: 'limit_total_reviews',
  retries_per_card: 'limit_retries_per_card',
  replans: 'limit_replans',
}

export interface RequirementLimit {
  readonly key: RequirementLimitKey
  /** The value, or null when nothing is known (or the default holds). */
  readonly value: number | null
  /** No value is stored: the system default holds (v7 `기본`). */
  readonly isDefault: boolean
  /** The limit the group reached (v7 req.d.limit: shown red). */
  readonly hot: boolean
}

export interface RequirementSettingsView {
  readonly delegation: RequirementDelegation
  readonly scope: RequirementScope
  /** AI Team id, or null. */
  readonly team: string | null
  readonly teamOptions: readonly { readonly value: string; readonly label: string }[]
  readonly limits: readonly RequirementLimit[]
  /** Delegation is running: the settings are locked (v7 🔒 위임 진행 중). */
  readonly locked: boolean
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
  /** Why nothing can change now (message key under `gui.inspector.requirementSettings.`), or null. */
  readonly reason: string | null
  /** Field -> message key of why its last change was refused. */
  readonly failures: Readonly<Record<string, string>>
}

/** The card settings read (the `@/features/document` SettingsNode). */
export interface RequirementNodeFacts {
  readonly locked: boolean
  readonly fields: Readonly<Record<string, { readonly value: unknown; readonly stored: unknown }>>
}

/** One AI Team (the `@/features/document` SettingsTeam). */
export interface RequirementTeamFacts {
  readonly id: string
  readonly name: string
  readonly supervisorName: string | null
}

export type RequirementSettingsFailureKind = 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

export interface RequirementSettingsInput {
  /** The Node is a Graph card the server keeps settings for. */
  readonly card: boolean
  readonly node: RequirementNodeFacts | null
  /** Values sent and not answered yet, by field. */
  readonly pending: Readonly<Record<string, unknown>>
  readonly teams: readonly RequirementTeamFacts[]
  readonly readOnly: InspectorReadOnly
  /** The card was just placed (`tmp_`): its key is not the server's yet. */
  readonly placing: boolean
  /** A refused change found the group disposed. */
  readonly disposed: boolean
  readonly failure: { readonly field: string; readonly kind: RequirementSettingsFailureKind } | null
}

/** A team's choice label: its name and its Supervisor (or that it has none), as v7 lists them. */
export function requirementTeamLabel(team: RequirementTeamFacts, labels: { readonly withSupervisor: (name: string) => string; readonly without: string }): string {
  return `${team.name} · ${team.supervisorName ? labels.withSupervisor(team.supervisorName) : labels.without}`
}

/** Why the card's settings cannot change now (message key), or null. */
export function requirementSettingsReason(input: Omit<RequirementSettingsInput, 'pending' | 'teams' | 'failure'>): string | null {
  if (!input.card) return 'delegation.unconnected'
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.disposed) return 'reason.disposed'
  if (input.placing) return 'reason.placing'
  if (input.node?.locked) return 'reason.locked'
  if (input.node === null) return 'reason.not_read'
  return null
}

const valueOf = (input: RequirementSettingsInput, field: string): unknown =>
  field in input.pending ? input.pending[field] : (input.node?.fields[field]?.value ?? null)

const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null)

/**
 * The view of the card's settings: the shown values (pending over read), the team choices (and a stored team
 * no longer offered, as its id), the limits with their default mark, and why nothing / a field cannot change.
 */
export function requirementSettingsView(
  input: RequirementSettingsInput,
  labels: { readonly withSupervisor: (name: string) => string; readonly without: string } = { withSupervisor: (name) => name, without: '' },
): RequirementSettingsView {
  const delegation = valueOf(input, 'delegation') === 'on' ? 'on' : 'off'
  const scope = valueOf(input, 'scope') === 'confirm' ? 'confirm' : 'end'
  const team = typeof valueOf(input, 'team') === 'string' && valueOf(input, 'team') ? (valueOf(input, 'team') as string) : null
  const teamOptions = input.teams.map((item) => ({ value: item.id, label: requirementTeamLabel(item, labels) }))
  if (team !== null && !teamOptions.some((option) => option.value === team)) teamOptions.push({ value: team, label: team })
  const limits = REQUIREMENT_LIMIT_KEYS.map((key) => {
    const field = REQUIREMENT_LIMIT_FIELDS[key]
    const value = whole(valueOf(input, field))
    const stored = field in input.pending ? input.pending[field] !== null : input.node?.fields[field]?.stored === true
    return { key, value, isDefault: !stored, hot: false }
  })
  const failures: Record<string, string> = {}
  if (input.failure) failures[input.failure.field] = `failure.${input.failure.kind}`
  return {
    delegation,
    scope,
    team,
    teamOptions,
    limits,
    locked: input.node?.locked === true,
    connected: input.card,
    reason: requirementSettingsReason(input),
    failures,
  }
}

/** Why the card's settings could not be read (message key, the read failure icon's tooltip). */
export function requirementSettingsErrorKey(kind: RequirementSettingsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' || kind === 'unavailable' ? `error.${kind}` : 'error.other'
}

/** The limit a text stands for: a whole number, null (empty: the default), or the text itself (refused by the server). */
export function requirementLimitValue(text: string): number | string | null {
  const trimmed = text.trim()
  if (trimmed === '') return null
  const number = Number(trimmed)
  return Number.isInteger(number) ? number : trimmed
}
