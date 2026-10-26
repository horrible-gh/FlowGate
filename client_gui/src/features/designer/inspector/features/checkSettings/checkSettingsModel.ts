// D0004 §6: server-owned local merge targets and immediate, versioned settings.
import { normalizeApiError, isRecord } from '@shared/apiErrors'
import type { RunGateResult, SettingsNode, SettingsMergeTargetState } from '@/features/document'
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
  readonly targetStored?: boolean
  readonly targetEnabled?: boolean
  readonly targetError?: string | null
  readonly lookupError?: string | null
  readonly canRefreshTargets?: boolean
  readonly unmergeWarning?: boolean
  readonly pushCheckRequired?: boolean
  /** Only the branch names admitted by the server. */
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
  readonly fields: Readonly<Record<string, { readonly value: unknown; readonly stored?: unknown }>>
  readonly mergeTargetOptions?: readonly string[]
  readonly defaultTargetBranch?: string | null
  readonly mergeTargetState?: SettingsMergeTargetState | null
  readonly pushRemoteCheckRequired?: boolean
}

export type CheckSettingsFailureKind = 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

export interface CheckSettingsInput {
  /** The Node is a Graph card the server keeps settings for. */
  readonly card: boolean
  readonly node: CheckNodeFacts | null
  readonly pending: Readonly<Record<string, unknown>>
  /** Legacy fixture input; real candidates are read from node.mergeTargetOptions only. */
  readonly branches: readonly string[]
  readonly readOnly: InspectorReadOnly
  readonly placing: boolean
  readonly disposed: boolean
  readonly failure: { readonly field: string; readonly kind: CheckSettingsFailureKind; readonly key?: string | null } | null
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
  const field = input.node?.fields.merge_target_branch
  const targetStored = field?.stored === true && typeof field.value === 'string' && !!field.value
  const state = input.node?.mergeTargetState
  const current = targetStored ? state?.current : state?.default
  const lookupUnavailable = state?.status === 'failed' || state?.status === 'timeout' || state?.status === 'git_unavailable'
  // An unchecked lookup result does not invalidate the saved or inherited name.
  const target = targetStored ? field!.value as string
    : !current?.error && (current?.checked === true || lookupUnavailable) ? input.node?.defaultTargetBranch ?? null : null
  const targetOptions = input.node?.mergeTargetOptions ?? []
  const scope = oneOf(valueOf('merge_mode'), CHECK_SCOPES)
  const lookupError = state?.status === 'git_unavailable' ? 'target.git_unavailable'
    : state?.status === 'failed' || state?.status === 'timeout' ? 'target.lookup_failed'
    : state?.status === 'empty' ? 'target.empty' : null
  const targetError = current?.error ? checkTargetErrorKey(current.error)
    : lookupUnavailable ? null
    : !target || current?.checked !== true ? 'target.unavailable' : null
  const failures: Record<string, string> = {}
  if (input.failure) failures[input.failure.field] = input.failure.key ?? `failure.${input.failure.kind}`
  return {
    usage: null, // legacy fixture shape only; never read or written by this UI
    scope,
    target,
    targetStored,
    targetEnabled: scope === 'merge' && targetOptions.length > 0,
    targetError: scope === 'merge' ? targetError : null,
    lookupError: scope === 'merge' ? lookupError : null,
    canRefreshTargets: state?.status === 'failed' || state?.status === 'timeout',
    unmergeWarning: scope === 'merge' && current?.unmerge_supported === false,
    pushCheckRequired: input.node?.pushRemoteCheckRequired === true,
    targetOptions,
    archive: valueOf('archive') === true,
    push: flag(input.node?.fields.push?.value),
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


/** Only current/default errors are rendered, never excluded-candidate diagnostics. */
export function checkTargetErrorKey(code: string): string {
  const keys: Record<string, string> = {
    gate_git_unavailable: 'git_unavailable', merge_target_busy: 'busy',
    merge_target_owner_mismatch: 'owner_mismatch', merge_target_incoming_work_base: 'incoming',
    merge_target_not_found: 'missing', merge_target_invalid: 'invalid', target_branch_invalid: 'invalid',
    merge_target_remote_only: 'local_required', merge_target_internal_slot: 'ineligible',
    merge_target_same_branch: 'same_branch', merge_target_preview_unavailable: 'check_failed',
  }
  return `target.${keys[code] ?? 'check_failed'}`
}

/** Shared by the control and approval summary; error messages never expose server diagnostics. */
export function checkControlFailure(error: unknown): { field: string; key: string } | null {
  const normalized = normalizeApiError(error)
  const params = isRecord(normalized.source.params) ? normalized.source.params : {}
  const details = isRecord(normalized.source.details) ? normalized.source.details : {}
  const errors = Array.isArray(params.errors) ? params.errors : []
  const first = errors.find(isRecord)
  const reason = typeof params.reason === 'string' ? params.reason : typeof details.reason === 'string' ? details.reason : typeof first?.reason === 'string' ? first.reason : normalized.code
  if (normalized.code === 'invalid_request' && first?.field === 'merge_target_branch') return { field: 'merge_target_branch', key: 'target.invalid' }
  if (reason === 'push_requires_origin') return { field: 'push', key: 'target.push_check' }
  if (reason === 'origin_not_configured') return { field: 'push', key: 'target.push_unavailable' }
  if (reason === 'legacy_merge_target_conflict') return { field: 'push', key: 'target.legacy_conflict' }
  return reason?.startsWith('merge_target_') || reason === 'target_branch_invalid' || normalized.code === 'gate_git_unavailable'
    ? { field: 'merge_target_branch', key: checkTargetErrorKey(normalized.code === 'gate_git_unavailable' ? normalized.code : reason!) } : null
}

/** Once a decision exists, only its receipt may describe the target and warnings. */
export function checkSettingsSummary(node: SettingsNode | null, decision?: RunGateResult | null) {
  if (decision) {
    // Rejection records the shown settings without accepting any Git operation.
    if (decision.result === 'rejected' || decision.status === 'rejected') {
      return { target: null, unmergeWarning: false, unmergeUnknown: false, push: false }
    }
    const saved = decision.settings
    const shown = saved?.shown
    const effective = saved?.effective
    const basis = saved?.target_basis
    const merge = effective?.merge_mode === 'merge'
    const modern = !!shown && 'merge_target_branch' in shown
    const unavailable = decision.settingsUnavailable === true
    return {
      target: merge || unavailable ? (typeof basis?.target_branch === 'string' ? basis.target_branch : decision.target_branch ?? null) : null,
      unmergeWarning: merge && (basis?.unmerge_supported === false || (Array.isArray(basis?.warnings) && basis.warnings.includes('unmerge_unsupported_for_non_base_target'))),
      unmergeUnknown: unavailable || (merge && modern && typeof basis?.unmerge_supported !== 'boolean'),
      push: effective?.push === true,
    }
  }
  const merge = node?.fields.merge_mode?.value === 'merge'
  const current = node?.mergeTargetState?.current
  return { target: merge ? current?.branch ?? null : null,
    unmergeWarning: merge && current?.unmerge_supported === false, unmergeUnknown: false,
    push: node?.fields.push?.value === true }
}

export function checkOperationErrorKey(code: string | null | undefined, decision?: RunGateResult | null): string | null {
  if (code === 'unmerge_unsupported_target') return 'target.unmerge_unsupported'
  if (code === 'unmerge_target_unknown') return 'target.unmerge_unknown'
  if (code === 'push_rejected') {
    const localApplied = decision?.effects?.some(e => ['merge', 'commit'].includes(String(e.phase)) && e.status === 'done' && typeof e.sha_after === 'string')
    return localApplied ? 'target.push_rejected_kept' : 'target.push_rejected'
  }
  return null
}

