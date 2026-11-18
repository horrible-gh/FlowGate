// Gate settings error/receipt presentation shared by controls and approval.
import { normalizeApiError, isRecord } from '@shared/apiErrors'
import type { RunGateResult } from './run'
import type { SettingsNode } from './settings'
const GATE_EFFECT_SUCCEEDED = 'succeeded'

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
