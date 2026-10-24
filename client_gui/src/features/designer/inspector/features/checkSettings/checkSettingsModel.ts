// D0007 §6/7.1: current gate settings, no branch selector or group-ending usage.
import type { InspectorReadOnly } from '../../inspectorTarget'

export type CheckUsage = 'mid' | 'end'
export type CheckScope = 'merge' | 'commit' | 'hold'

export interface CheckSettingsView {
  /** The card's usage, or null when not set. */
  readonly usage: CheckUsage | null
  readonly archive?: boolean
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
  const targetOptions = ['origin']
  const failures: Record<string, string> = {}
  if (input.failure) failures[input.failure.field] = `failure.${input.failure.kind}`
  return {
    usage: null, // legacy fixture shape only; never read or written by this UI
    scope: oneOf(valueOf('merge_mode'), CHECK_SCOPES),
    target: target === 'origin' ? target : null,
    targetOptions,
    archive: valueOf('archive') === true,
    push: flag(valueOf('push')),
    keepBranch: flag(valueOf('keep_branch')),
    connected: input.card,
    reason: checkSettingsReason(input),
    failures,
  }
}

/** Why the card's settings could not be read (message key, the read failure icon's tooltip). */
export function checkSettingsErrorKey(kind: CheckSettingsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' || kind === 'unavailable' ? `error.${kind}` : 'error.other'
}

