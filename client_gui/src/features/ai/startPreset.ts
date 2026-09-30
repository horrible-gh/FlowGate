import type { AiGroup, ProviderItem } from './types'

export interface StartSelection {
  requestedAiGroupId: string
  providerId: string
  reviewerId: string
  reworkerId: string
}

export function groupIsAvailable(group: AiGroup): boolean {
  return !!group.work_executor?.is_available &&
    (!group.reviewer || group.reviewer.is_available) &&
    (!group.reworker || group.reworker.is_available)
}

export function selectionFromGroup(group: AiGroup): StartSelection {
  return {
    requestedAiGroupId: group.ai_group_id,
    providerId: group.work_executor?.provider_id ?? '',
    reviewerId: group.reviewer?.provider_id ?? '',
    reworkerId: group.reworker?.provider_id ?? '',
  }
}

export function effectiveProviders(providers: ProviderItem[]): ProviderItem[] {
  return providers.filter(provider => provider.enabled !== false)
}

export interface ContinuousStartInput extends StartSelection {
  project: string
  module: string
  group: string
  docRef: string
  actionScope: 'new' | 'edit' | 'workflow_decide'
  targetSeq: number
  instructionMode: 'ai_direct' | 'auto_approved'
  // Runnable steps up to the target; the Group/concrete reviewer is applied to each of them.
  reviewerStepSeqs: number[]
  // The existing continuous review mode toggle. Independent of any reviewer selection.
  reviewMode?: boolean
}

export const REVIEWER_DEFAULT_KEY = '*'

// Steps this run actually executes: not done, within the target, and not N/T authoring
// steps the server auto-handles under auto_approved.
export function reviewerStepSeqs(
  steps: { item_seq: number; type?: string | null; status?: string | null }[],
  targetSeq: number,
  instructionMode: 'ai_direct' | 'auto_approved',
): number[] {
  const autoHandled = instructionMode === 'auto_approved' ? ['N', 'T', 'T2'] : []
  return steps
    .filter(step => step.status !== 'done' && step.item_seq > 0 && step.item_seq <= targetSeq &&
      !autoHandled.includes(String(step.type ?? '').toUpperCase()))
    .map(step => step.item_seq)
}

// Group id is provenance. Only these concrete selections control runtime execution.
export function buildContinuousStartBody(input: ContinuousStartInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    project: input.project,
    module: input.module || null,
    group: input.group,
    doc_ref: input.docRef,
    action_scope: input.actionScope,
    mode: 'continuous',
    continuation_target_seq: input.targetSeq,
    continuation_instruction_mode: input.instructionMode,
    continuation_review_mode: !!input.reviewMode,
    provider_id: input.providerId,
    provider_pinned: true,
  }
  if (input.requestedAiGroupId) body.requested_ai_group_id = input.requestedAiGroupId
  if (input.reworkerId) body.continuation_reworker_provider_id = input.reworkerId
  if (input.reviewerId) {
    // workflow_decide has no item_seq yet: the server keeps the reviewer as a run-level default
    // and applies it to the steps of the freshly decided sequence.
    if (input.actionScope === 'workflow_decide') {
      body.continuation_reviewer_overrides = { [REVIEWER_DEFAULT_KEY]: input.reviewerId }
    } else if (input.reviewerStepSeqs.length) {
      body.continuation_reviewer_overrides = Object.fromEntries(input.reviewerStepSeqs.map(seq => [seq, input.reviewerId]))
    }
  }
  return body
}
