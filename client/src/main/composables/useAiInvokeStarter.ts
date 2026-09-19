import { getRequest, postRequest } from '@shared/api'
import { useAiProviderStore } from '../stores/aiProvider'
import { aiInvokeGroupId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'

// flowgate.default.0585 T0004 §2: the actual execution common part of AiInvokeDialog.start() —
// provider ensure, request body assembly, POST /api/v1/ai-invoke/start, trackStarted, and the
// server error normalization — pulled out so a caller that must not render the dialog at all
// (autoStart) does not have to duplicate it. AiInvokeDialog keeps everything that is genuinely
// about the dialog: the input/settings UI, the review-loop tab state, the capability-warning ACK
// UI, and mapping this module's error results onto its own startError / lockedGroupId /
// capabilityWarning refs.
export type AiInvokeScope =
  | 'new'
  | 'edit'
  | 'workflow_decide'
  | 'chat'
  | 'rework'
  | 'review'
  | 'vr_correction'
  | 'next_step_message'
  | 'design_handoff'

export interface AiInvokeDocumentReviewLoop {
  review_count: number
  reviewer_provider_id: string
  review_criteria: 'document_type_default' | 'last_rejection_only'
  rework_provider_id: string
  rework_timeout_sec: number
  rework_message: string
  failure_restart_max_attempts: number
  total_timeout_sec: number
}

export interface AiInvokeStartOptions {
  project: string
  module?: string | null
  group: string
  docRef: string
  // Distinct from docRef for a pre-decision continuous start, which must act on the sequence
  // ROOT (/workflow/sequence is keyed by the root, not by a member doc) — see resolvedTarget /
  // preDecision below. Defaults to docRef when omitted.
  sequenceDocRef?: string
  actionScope: AiInvokeScope
  mode: 'single' | 'continuous'
  reviewIntent?: 'rerun'
  /** The chain's stop point. `fromDecision` forces action_scope to 'workflow_decide' (T0004 §2). */
  target?: { seq: number; fromDecision: boolean } | null
  continuationReviewMode?: boolean
  continuationInstructionMode?: 'auto_approved' | 'ai_direct'
  continuationAutoApproveItemSeqs?: number[]
  providerOverrides?: Record<number, string>
  defaultMessage?: string
  messageOverrides?: Record<number, string>
  reviewCountOverrides?: Record<number, number>
  reviewerOverrides?: Record<number, string>
  continuationStepTimeoutSec?: number | null
  continuationRestartMaxAttempts?: number | null
  // The single-run rejection-rework budget (T0010 §3-6). Independent of the continuous block
  // above and applied unconditionally when present — only AiInvokeDialog's own stepTimeoutActive
  // path ever supplies it.
  singleRunTimeoutSec?: number | null
  capabilityWarningAck?: boolean
  documentReviewLoop?: AiInvokeDocumentReviewLoop | null
  selectedDocs?: string[] | null
  messages?: string[] | null
  rejectReason?: string | null
  designTypes?: string[] | null
  designMode?: string | null
  designFirstLabel?: string | null
}

export type AiInvokeStartResult =
  | { ok: true; data: any }
  | { ok: false; kind: 'run_in_progress_orphaned'; groupId: string }
  | { ok: false; kind: 'review_already_completed' }
  | { ok: false; kind: 'review_rerun_not_available' }
  | { ok: false; kind: 'no_provider_registered' }
  | { ok: false; kind: 'no_enabled_provider' }
  | { ok: false; kind: 'capability_warning'; warning: any; message?: string }
  | { ok: false; kind: 'validation_error'; message?: string }
  | { ok: false; kind: 'unknown_error'; message?: string }
  | { ok: false; kind: 'cancelled' }

export function buildAiInvokeStartBody(
  options: AiInvokeStartOptions,
  provider: { selectedProviderId: string; pinned: boolean },
): Record<string, unknown> {
  const preDecision = options.mode === 'continuous' && !!options.target?.fromDecision
  const scope = preDecision ? 'workflow_decide' : options.actionScope
  const body: Record<string, unknown> = {
    project: options.project,
    group: options.group,
    doc_ref: preDecision ? (options.sequenceDocRef || options.docRef) : options.docRef,
    action_scope: scope,
    mode: options.mode,
  }
  if (scope === 'review' && options.reviewIntent === 'rerun') body.review_intent = 'rerun'
  if (options.documentReviewLoop) {
    body.provider_id = null
    body.provider_pinned = false
    body.document_review_loop = options.documentReviewLoop
  } else {
    if (provider.selectedProviderId) body.provider_id = provider.selectedProviderId
    if (provider.pinned) body.provider_pinned = true
  }
  if (options.mode === 'single' && options.capabilityWarningAck) body.capability_warning_ack = true
  if (options.module != null) body.module = options.module
  if (options.selectedDocs?.length) body.selected_docs = options.selectedDocs
  if (options.messages?.length) body.messages = options.messages
  if (options.rejectReason) body.reject_reason = options.rejectReason
  if (options.designTypes?.length) body.design_types = options.designTypes
  if (options.designMode) body.design_mode = options.designMode
  if (options.designFirstLabel) body.design_first_label = options.designFirstLabel
  if (options.mode === 'continuous') {
    body.continuation_target_seq = options.target?.seq ?? null
    body.continuation_review_mode = !!options.continuationReviewMode
    body.continuation_instruction_mode = options.continuationInstructionMode
    if (options.providerOverrides && Object.keys(options.providerOverrides).length) {
      body.continuation_provider_overrides = options.providerOverrides
    }
    if (options.defaultMessage) body.continuation_default_note = options.defaultMessage
    if (options.messageOverrides && Object.keys(options.messageOverrides).length) {
      body.continuation_note_overrides = options.messageOverrides
    }
    if (options.continuationStepTimeoutSec) {
      body.continuation_step_timeout_sec = options.continuationStepTimeoutSec
    }
    // 0 and -1 are both meaningful restart-count picks, so this must not use a truthy check.
    if (options.continuationRestartMaxAttempts != null) {
      body.continuation_restart_max_attempts = options.continuationRestartMaxAttempts
    }
    if (!preDecision && options.continuationAutoApproveItemSeqs?.length) {
      body.continuation_auto_approve_item_seqs = options.continuationAutoApproveItemSeqs
    }
    if (!preDecision && options.reviewCountOverrides && Object.keys(options.reviewCountOverrides).length) {
      body.continuation_review_count_overrides = options.reviewCountOverrides
    }
    if (!preDecision && options.reviewerOverrides && Object.keys(options.reviewerOverrides).length) {
      body.continuation_reviewer_overrides = options.reviewerOverrides
    }
  }
  if (options.singleRunTimeoutSec != null) {
    body.continuation_step_timeout_sec = options.singleRunTimeoutSec
  }
  return body
}

async function checkRunLive(runId: string): Promise<boolean> {
  try {
    const res = await getRequest<any>(`/api/v1/ai-invoke/${encodeURIComponent(runId)}`)
    const status = res.data?.status
    return status === 'running' || status === 'pause_requested'
  } catch (e: any) {
    // Unknown run_id ⇒ definitely not live. Any other failure fails toward "live" so a
    // transient lookup error cannot dead-end the caller either.
    return e?.response?.status !== 404
  }
}

export interface StartAiInvokeDeps {
  /** Checked before and after the post-409 checkRunLive await (0560 T0031). */
  isCancelled?: () => boolean
}

/**
 * The common start() body of AiInvokeDialog, usable without mounting the dialog at all
 * (flowgate.default.0585 T0004 §1/§2). Ensures the provider list, builds the wire body, posts
 * /api/v1/ai-invoke/start, tracks the run on success, and normalizes every error branch the
 * dialog used to handle inline into a typed result the caller maps to its own UI.
 */
export async function startAiInvoke(
  options: AiInvokeStartOptions,
  deps: StartAiInvokeDeps = {},
): Promise<AiInvokeStartResult> {
  const aiProviderStore = useAiProviderStore()
  const aiInvokeStore = useAiInvokeRunsStore()
  await aiProviderStore.ensureLoaded(options.project)
  const body = buildAiInvokeStartBody(options, aiProviderStore)
  try {
    const res = await postRequest<any>('/api/v1/ai-invoke/start', body)
    const data = res.data
    const groupId = aiInvokeGroupId(options.project, options.module, options.group)
    aiInvokeStore.trackStarted({
      ...data,
      group_id: data.group_id ?? groupId,
      doc_ref: data.doc_ref ?? options.docRef,
    })
    return { ok: true, data }
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data ?? {}
    if (status === 409 && data.code === 'run_in_progress' && data.run_id) {
      const groupId = data.group_id ?? aiInvokeGroupId(options.project, options.module, options.group)
      // 0401 NR0003 §3 cause 4: the 409 body always names a run_id, live or not — adopting it
      // unconditionally would close onto a run that was already gone. Verify liveness first.
      if (deps.isCancelled?.()) return { ok: false, kind: 'cancelled' }
      const runIsLive = await checkRunLive(data.run_id)
      if (deps.isCancelled?.()) return { ok: false, kind: 'cancelled' }
      if (runIsLive) {
        aiInvokeStore.trackStarted({
          run_id: data.run_id,
          group_id: groupId,
          doc_ref: options.docRef,
          mode: options.mode,
        })
        void aiInvokeStore.refresh(groupId)
        return { ok: true, data: { ...data, group_id: groupId, adopted: true } }
      }
      return { ok: false, kind: 'run_in_progress_orphaned', groupId }
    }
    if (status === 409 && data.code === 'review_already_completed') {
      return { ok: false, kind: 'review_already_completed' }
    }
    if (status === 409 && data.code === 'review_rerun_not_available') {
      return { ok: false, kind: 'review_rerun_not_available' }
    }
    if (status === 409 && data.code === 'no_provider_registered') {
      return { ok: false, kind: 'no_provider_registered' }
    }
    if (status === 409 && data.code === 'no_enabled_provider') {
      return { ok: false, kind: 'no_enabled_provider' }
    }
    if (status === 422 && data.code === 'provider_capability_confirmation_required' && options.mode === 'single') {
      return { ok: false, kind: 'capability_warning', warning: data, message: data.message }
    }
    if (status === 422) {
      const msgs = (data.errors ?? []).map((er: any) => `${er.loc}: ${er.msg}`).join(' / ')
      return { ok: false, kind: 'validation_error', message: msgs }
    }
    return { ok: false, kind: 'unknown_error', message: data.message }
  }
}
