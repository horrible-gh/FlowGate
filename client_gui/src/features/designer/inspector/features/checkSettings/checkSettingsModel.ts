// D0004 §6: server-owned local merge targets and immediate, versioned settings.
import { normalizeApiError, isRecord } from '@shared/apiErrors'
import type { RunGateResult, SettingsNode, SettingsMergeTargetState } from '@/features/document'
import type { InspectorReadOnly } from '../../inspectorTarget'

export type CheckUsage = 'mid' | 'end'
export type CheckScope = 'merge' | 'commit' | 'hold'
/** The candidate lookup's own status as the server answered it (`merge_target_state.status`). */
export type CheckLookupStatus = 'ok' | 'empty' | 'timeout' | 'failed' | 'git_unavailable'
/**
 * Where the shown candidates come from: this answer, the kept confirmed list (0006-T §2), the stored / inherited names
 * alone while no confirmed list exists after a timeout / failure (`unverified`, flowgate.v02test.0020), or nowhere.
 */
export type CheckOptionsSource = 'live' | 'cache' | 'unverified' | 'none'
export const CHECK_LOOKUP_STATUSES: readonly CheckLookupStatus[] = ['ok', 'empty', 'timeout', 'failed', 'git_unavailable']
/** Lookups after which the kept confirmed list may stand in for the missing candidates. */
const CACHE_USABLE_LOOKUPS: readonly CheckLookupStatus[] = ['timeout', 'failed']

export interface CheckSettingsView {
  /** The card's usage, or null when not set. */
  readonly usage: CheckUsage | null
  readonly archive?: boolean
  readonly scope: CheckScope | null
  /** Merge target branch, or null. */
  readonly target: string | null
  readonly targetStored?: boolean
  /** The group's work base, the branch an unset target inherits (shown as the target). */
  readonly defaultTarget?: string | null
  readonly targetEnabled?: boolean
  readonly targetError?: string | null
  /** Display only (0006-T §3.2): never a reason the target cannot be chosen. */
  readonly lookupError?: string | null
  readonly lookupStatus?: CheckLookupStatus | null
  readonly optionsSource?: CheckOptionsSource
  readonly canRefreshTargets?: boolean
  readonly pushCheckRequired?: boolean
  /** Only the branch names admitted by the server: this answer's, else the kept confirmed list's — never both. */
  readonly targetOptions: readonly string[]
  readonly push: boolean | null
  readonly keepBranch: boolean | null
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
  /** Why nothing can change now (message key under `gui.inspector.checkSettings.`), or null. */
  readonly reason?: string | null
  /** Field -> message key of why its last change was refused. */
  readonly failures?: Readonly<Record<string, string>>
  /** Field -> its own save state while it has one (flowgate.v02.0148 T#2): `saving`, `saved` (only after the answer confirmed it), `failed`. */
  readonly fieldStates?: Readonly<Record<string, FieldSaveState>>
}

export type FieldSaveState = 'saving' | 'saved' | 'failed'

// Server contract: flowgate/gate_decisions/repository.py DONE (effect success, not node done).
export const GATE_EFFECT_SUCCEEDED = 'succeeded'

export const CHECK_SCOPES: readonly CheckScope[] = ['merge', 'commit', 'hold']

/** The card settings read (the `@/features/document` SettingsNode). */
export interface CheckNodeFacts {
  readonly locked: boolean
  /**
   * flowgate.v02.0132 T#4: why it is locked, as the server tells it (the gate's `settings_lock_reason`, else the settings
   * read's `lock_reason`), and the gate decision's Git state (`git_active`: auto sync going on, `parked`: waiting for a
   * recovery). Absent = not told: the plain `reason.locked`.
   */
  readonly lockReason?: string | null
  readonly gitActive?: boolean
  readonly parked?: boolean
  readonly fields: Readonly<Record<string, { readonly value: unknown; readonly stored?: unknown }>>
  readonly mergeTargetOptions?: readonly string[]
  readonly defaultTargetBranch?: string | null
  readonly mergeTargetState?: SettingsMergeTargetState | null
  readonly pushRemoteCheckRequired?: boolean
}

export type CheckSettingsFailureKind = 'roles_unavailable' | 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

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
  /** flowgate.v02.0148 T#2: the refusal of each field's own last write (a field keeps its refusal until its next input). */
  readonly fieldFailures?: Readonly<Record<string, { readonly kind: CheckSettingsFailureKind; readonly key?: string | null }>>
  /** flowgate.v02.0148 T#2: each field's save state (the context's `fieldStates`). */
  readonly fieldStates?: Readonly<Record<string, FieldSaveState>>
  /**
   * 0006-T §2: the last candidates the server confirmed (lookup ok / empty) for this very project, flow, card and work
   * base, still inside their validity; null when there is none. Used only while the live lookup timed out / failed.
   */
  readonly cachedTargets?: readonly string[] | null
}

/** The server's gate `settings_lock_reason` -> the message key of the lock (flowgate.v02.0132 T#4). */
const LOCK_REASON_KEYS: Readonly<Record<string, string>> = {
  decision_reserved: 'reason.lock_decision_reserved', merge_conflict: 'reason.lock_merge_conflict',
  time_machine: 'reason.lock_time_machine', gate_decided: 'reason.lock_gate_decided',
  recovery_reset_required: 'reason.lock_recovery_reset', run_not_editable: 'reason.locked',
}

/**
 * Why a locked card cannot change: Git work going on now (auto sync) first, then a parked decision (waiting for a
 * recovery), then the server's reason; an unknown or absent reason is the plain "running".
 */
export function checkLockedReason(node: Pick<CheckNodeFacts, 'lockReason' | 'gitActive' | 'parked'>): string {
  if (node.gitActive) return 'reason.lock_git_active'
  if (node.parked && (!node.lockReason || node.lockReason === 'decision_reserved')) return 'reason.lock_parked'
  return (node.lockReason ? LOCK_REASON_KEYS[node.lockReason] : undefined) ?? 'reason.locked'
}

/** Why the card's settings cannot change now (message key), or null. */
export function checkSettingsReason(input: Pick<CheckSettingsInput, 'card' | 'node' | 'readOnly' | 'placing' | 'disposed'>): string | null {
  if (!input.card) return 'git.unconnected'
  if (input.readOnly === 'archived') return 'reason.read_only_archived'
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.disposed) return 'reason.disposed'
  if (input.placing) return 'reason.placing'
  if (input.node?.locked) return checkLockedReason(input.node)
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
  const defaultTarget = input.node?.defaultTargetBranch ?? null
  // T0012: the saved or inherited branch name is always shown; why it cannot be used is its error, never a blank.
  // flowgate.v02.0148 T#2: the branch being saved is shown at once (like every other field); a refusal brings the confirmed one back.
  const pendingTarget = 'merge_target_branch' in input.pending
  const requested = input.pending.merge_target_branch
  const target = pendingTarget ? (typeof requested === 'string' && requested ? requested : defaultTarget) : targetStored ? field!.value as string : defaultTarget
  const lookupStatus = oneOf(state?.status, CHECK_LOOKUP_STATUSES)
  // Only a confirmed lookup (or an answer without lookup facts) lists live candidates; names beside a failure are not.
  const liveOptions = !state || lookupStatus === 'ok' || lookupStatus === 'empty' ? input.node?.mergeTargetOptions ?? [] : []
  // 0006-T §3.3: the live answer's candidates when it has any, else the kept confirmed list after a timeout / failure.
  const usable = lookupStatus !== null && CACHE_USABLE_LOOKUPS.includes(lookupStatus)
  const cached = usable ? input.cachedTargets ?? [] : []
  // flowgate.v02test.0020 (replaces 0006-T §3.5's lock): with no confirmed list at all after a timeout / failure (a first
  // entry, F5, the kept list expired), the stored and the inherited name stay choosable, marked unverified, so the lookup
  // alone never locks the control. The server judges the choice again on PATCH (judge_check_target) and before the
  // confirmation; a refusal is the field's own error, as for any other name.
  const storedName = targetStored ? field!.value as string : null
  const unverified = usable ? [...new Set([storedName, defaultTarget].filter((name): name is string => !!name))] : []
  const optionsSource: CheckOptionsSource = liveOptions.length > 0 ? 'live' : cached.length > 0 ? 'cache'
    : unverified.length > 0 ? 'unverified' : 'none'
  const targetOptions = optionsSource === 'live' ? liveOptions : optionsSource === 'cache' ? cached
    : optionsSource === 'unverified' ? unverified : []
  const reason = checkSettingsReason(input)
  // flowgate.v02gui.0004 (0001-B 관찰 1): no stored mode = the server applies no merge / commit, i.e. 보류. It is shown
  // selected so the default is visible instead of three unselected choices.
  const scope = oneOf(valueOf('merge_mode'), CHECK_SCOPES) ?? (input.node ? 'hold' : null)
  const lookupError = state?.status === 'git_unavailable' ? 'target.git_unavailable'
    : state?.status === 'failed' ? 'target.lookup_failed'
    : state?.status === 'timeout' ? 'target.lookup_timeout'
    : state?.status === 'empty' ? 'target.empty' : null
  // A Git lookup failure is already the lookup state: the entry's copy of it is not shown twice.
  const targetError = current?.error && state?.status !== 'git_unavailable' ? checkTargetErrorKey(current.error) : null
  const failures: Record<string, string> = {}
  if (input.failure) failures[input.failure.field] = input.failure.key ?? `failure.${input.failure.kind}`
  for (const [name, item] of Object.entries(input.fieldFailures ?? {})) failures[name] = item.key ?? `failure.${item.kind}`
  return {
    usage: null, // legacy fixture shape only; never read or written by this UI
    scope,
    target,
    targetStored,
    defaultTarget,
    // 0006-T §3.2: a candidate (live, kept, or the unverified stored / inherited names) and no safety lock; a lookup
    // error alone never locks the choice (flowgate.v02test.0020).
    // flowgate.v02.0148 T#2: a save of another field on its way is no lock (the writes are queued).
    targetEnabled: scope === 'merge' && targetOptions.length > 0 && reason === null,
    targetError: scope === 'merge' && !pendingTarget ? targetError : null,
    lookupError: scope === 'merge' ? lookupError : null,
    lookupStatus,
    optionsSource,
    canRefreshTargets: state?.status === 'failed' || state?.status === 'timeout',
    pushCheckRequired: input.node?.pushRemoteCheckRequired === true,
    targetOptions,
    archive: valueOf('archive') === true,
    push: flag(valueOf('push')),
    keepBranch: flag(valueOf('keep_branch')),
    connected: input.card,
    reason,
    failures,
    fieldStates: input.fieldStates ?? {},
  }
}

/** Why the card's settings could not be read (message key, the read failure icon's tooltip). */
export function checkSettingsErrorKey(kind: CheckSettingsFailureKind): string {
  if (kind === 'roles_unavailable') return 'error.roles_unavailable'
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
      return { target: null, unmergeUnknown: false, push: false }
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
      unmergeUnknown: unavailable || (merge && modern && typeof basis?.unmerge_supported !== 'boolean'),
      push: effective?.push === true,
    }
  }
  const merge = node?.fields.merge_mode?.value === 'merge'
  const current = node?.mergeTargetState?.current
  return { target: merge ? current?.branch ?? null : null, unmergeUnknown: false,
    push: node?.fields.push?.value === true }
}

export function checkOperationErrorKey(code: string | null | undefined, decision?: RunGateResult | null): string | null {
  if (code === 'unmerge_target_unknown') return 'target.unmerge_unknown'
  if (code === 'push_rejected') {
    const localApplied = decision?.effects?.some(e => ['merge', 'commit'].includes(String(e.phase)) && e.status === GATE_EFFECT_SUCCEEDED && typeof e.sha_after === 'string' && e.sha_after.length > 0)
    return localApplied ? 'target.push_rejected_kept' : 'target.push_rejected'
  }
  const keys: Record<string, string> = {
    merge_conflict: 'merge_conflict', merge_conflict_aborted: 'merge_aborted', merge_failed: 'merge_aborted',
    merge_attempt_unsettled: 'target_check', target_moved: 'target_check', target_branch_missing: 'target_check',
    target_ref_unreadable: 'target_check', gate_git_unavailable: 'target_check',
    // flowgate.v02.0132 T#4 (NR §4.2/§4.5): the remote sync and the parks that wait for a recovery
    push_remote_ahead: 'remote_ahead', push_protected: 'push_protected', push_remote_race: 'remote_race',
    target_checkout_mismatch: 'checkout_mismatch', target_diverged: 'diverged', sync_unsettled: 'sync_unsettled',
    push_sync_conflict_aborted: 'sync_conflict_aborted',
    remote_head_unknown: 'remote_unknown', push_result_unknown: 'push_unknown', push_timeout: 'push_unknown',
    push_auth_failed: 'push_auth_failed', group_branch_missing: 'group_branch_missing',
  }
  if (!code) return null
  return `target.${keys[code] ?? (code.startsWith('merge_target_') ? 'target_check' : 'operation_unknown')}`
}
