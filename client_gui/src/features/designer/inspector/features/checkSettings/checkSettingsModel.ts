// flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings): 용도 (중간 승인 /
// 그룹 종료 — under discussion, v7 §5, with the 토론 중 tag) and the Git 반영 card (반영 방식 병합 / 커밋만 /
// 보류 · 병합 대상 · 원격 반영 · 브랜치 보관). Pure functions.
// flowgate.v02.0053 T#7 — a Graph gate (확인) card keeps these values itself (T#2 card settings: usage,
// merge_mode, merge_target, push, keep_branch), each change saved at once. None stored = asked as today at
// the final approval. The merge target choices are the group's Git work base (and the stored one). A v0.1
// Flow's 확인 Node has no card to keep them: it stays drawn and off, its reason a tooltip. The summary line
// reads the values shown (merge target, push), a value not chosen as not set; a failed first read is an
// error icon with a retry, not a form of guessed values.
import type { InspectorReadOnly } from '../../inspectorTarget'

export type CheckUsage = 'mid' | 'end'
export type CheckScope = 'merge' | 'commit' | 'hold'

export interface CheckSettingsView {
  /** The card's usage, or null when not set. */
  readonly usage: CheckUsage | null
  readonly scope: CheckScope | null
  /** Merge target branch, or null. */
  readonly target: string | null
  /** The merge target choices (the stored one first when it is not among them). */
  readonly targetOptions: readonly string[]
  readonly push: boolean | null
  readonly keepBranch: boolean | null
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
  /** Why nothing can change now (message key under `gui.inspector.checkSettings.`), or null. */
  readonly reason?: string | null
  /** Field -> message key of why its last change was refused. */
  readonly failures?: Readonly<Record<string, string>>
}

export const CHECK_SCOPES: readonly CheckScope[] = ['merge', 'commit', 'hold']
export const CHECK_USAGES: readonly CheckUsage[] = ['mid', 'end']

/** The card settings read (the `@/features/document` SettingsNode). */
export interface CheckNodeFacts {
  readonly locked: boolean
  readonly fields: Readonly<Record<string, { readonly value: unknown }>>
}

export type CheckSettingsFailureKind = 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

export interface CheckSettingsInput {
  /** The Node is a Graph card the server keeps settings for. */
  readonly card: boolean
  readonly node: CheckNodeFacts | null
  readonly pending: Readonly<Record<string, unknown>>
  /** Branches the group's Git work names (its work base), known values only. */
  readonly branches: readonly string[]
  readonly readOnly: InspectorReadOnly
  readonly placing: boolean
  readonly disposed: boolean
  readonly failure: { readonly field: string; readonly kind: CheckSettingsFailureKind } | null
}

/** Why the card's settings cannot change now (message key), or null. */
export function checkSettingsReason(input: Pick<CheckSettingsInput, 'card' | 'node' | 'readOnly' | 'placing' | 'disposed'>): string | null {
  if (!input.card) return 'git.unconnected'
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.disposed) return 'reason.disposed'
  if (input.placing) return 'reason.placing'
  if (input.node?.locked) return 'reason.locked'
  if (input.node === null) return 'reason.not_read'
  return null
}

function oneOf<T extends string>(value: unknown, values: readonly T[]): T | null {
  return typeof value === 'string' && (values as readonly string[]).includes(value) ? (value as T) : null
}

const flag = (value: unknown): boolean | null => (typeof value === 'boolean' ? value : null)

/** The view: the shown values (pending over read), the merge target choices, and why nothing / a field cannot change. */
export function checkSettingsView(input: CheckSettingsInput): CheckSettingsView {
  const valueOf = (field: string): unknown => (field in input.pending ? input.pending[field] : (input.node?.fields[field]?.value ?? null))
  const target = typeof valueOf('merge_target') === 'string' && valueOf('merge_target') ? (valueOf('merge_target') as string) : null
  const targetOptions = [...new Set([...(target ? [target] : []), ...input.branches.filter(Boolean)])]
  const failures: Record<string, string> = {}
  if (input.failure) failures[input.failure.field] = `failure.${input.failure.kind}`
  return {
    usage: oneOf(valueOf('usage'), CHECK_USAGES),
    scope: oneOf(valueOf('merge_mode'), CHECK_SCOPES),
    target,
    targetOptions,
    push: flag(valueOf('push')),
    keepBranch: flag(valueOf('keep_branch')),
    connected: input.card,
    reason: checkSettingsReason(input),
    failures,
  }
}

/** One summary line: its message key and params; `end` = followed by `→ 그룹 종료` (`git.summary_end`). */
export interface CheckSettingsSummary {
  readonly key: string
  readonly params: Readonly<Record<string, string>>
  readonly end: boolean
}

/**
 * v7 scopeSummary, of the values shown (the answer, or a value still being sent): 병합 names the chosen merge
 * target and whether it is pushed — v7's "main에 병합하고 원격 반영" only when those are the values — and a
 * target or push not chosen yet reads as not set; `end` when the card ends the group. 보류 on a group-ending
 * card has its own line; nothing chosen is asked at the final approval.
 */
export function checkSettingsSummary(view: Pick<CheckSettingsView, 'scope' | 'usage' | 'target' | 'push'>): CheckSettingsSummary {
  const end = view.usage === 'end'
  if (view.scope === null) return { key: 'git.summary_none', params: {}, end: false }
  if (view.scope === 'hold') return { key: end ? 'git.summary_hold_end' : 'git.summary_hold', params: {}, end: false }
  if (view.scope === 'commit') return { key: 'git.summary_commit', params: {}, end }
  const push = view.push === true ? 'push' : view.push === false ? 'local' : 'push_unset'
  return view.target
    ? { key: `git.summary_merge.${push}`, params: { target: view.target }, end }
    : { key: `git.summary_merge_untargeted.${push}`, params: {}, end }
}

/** Why the card's settings could not be read (message key, the read failure icon's tooltip). */
export function checkSettingsErrorKey(kind: CheckSettingsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' || kind === 'unavailable' ? `error.${kind}` : 'error.other'
}

/** v7: 보류 cannot end the group (⛔ on the choice, and the block line when both are chosen). */
export function checkSettingsHoldBlocked(view: Pick<CheckSettingsView, 'scope' | 'usage'>): boolean {
  return view.usage === 'end' && view.scope === 'hold'
}
