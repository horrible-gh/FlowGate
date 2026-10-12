// flowgate.v02.0048 T#12 — the check card's state and operations (D#1 §7.2 / §7.3, §8.2, §10.2 T#12 row).
// Three reads through useInspectorResource: the AC's review state (status, group disposed), the group's
// Git reflection state as the final approval sees it (`git/finalize?context=approval`) and whether an
// AI run owns the group (`/ai-invoke/active`). The final approval calls the existing v0.1 approve with
// the chosen Git action through `@/features/document`, then reads its own three resources again; a
// refusal (409 state contention / Git busy, 423 AI running, 422 stale choice, 403 …) is shown as a
// reason and the state is read again too (D#1 §8.1). Git in detail is the existing Git panel, opened
// with the Host intent `show-tool: git` (the Git feature is never imported); what the approval means to
// the rest of the Designer travels as the server's own events (D#1 §7.3).
import { computed, inject, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import {
  checkApprove,
  checkDocumentRead,
  checkErrorOf,
  checkGitRead,
  documentErrorOf,
  reviewActivityRead,
  type CheckApproveResult,
  type CheckGitChoice,
  type CheckGitState,
  type ReviewDocument,
} from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument } from '../../openDocument'
import { useInspectorResource, type InspectorEventMatch } from '../../useInspectorResource'
import {
  checkApprovalRunning,
  checkAvailability,
  checkDefaultChoice,
  checkFailureKey,
  checkGitErrorKey,
  checkGitOffered,
  checkOutcome,
  checkReviewStatus,
  type CheckAvailability,
  type CheckFailureKind,
  type CheckOutcomeLine,
} from './checkModel'

/**
 * Server events after which the AC's review state is read again (D#1 §7.4). A final approval that
 * rides on Git is committed by the approval job, which tells it with the Flow's Git frames
 * (git_finalize_done, git_approval_job_changed), not with doc_review_status_changed.
 */
export const CHECK_DOCUMENT_EVENTS = ['doc_review_status_changed', 'document_explorer_refresh', 'git_finalize_done', 'git_approval_job_changed'] as const

/**
 * Server events after which the group's Git reflection state is read again: the Git ledger's own
 * (status, finalize, approval job, conflict) and the review change that realizes a pending slot.
 */
export const CHECK_GIT_EVENTS = [
  'git_pending_changed',
  'git_finalize_done',
  'git_approval_job_changed',
  'git_merge_conflict',
  'doc_review_status_changed',
] as const

/** Server events after which the group's AI activity is read again. */
export const CHECK_ACTIVITY_EVENTS = ['ai_invoke_started', 'ai_invoke_finished'] as const

/** A frame of the target Flow (Git and AI run frames carry the group_id, no doc_id). */
export const checkFlowMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

export interface CheckOffer {
  /** Choices of the dialog, server order; empty = a plain final approval. */
  readonly choices: readonly CheckGitChoice[]
  /** The choice the dialog starts on. */
  readonly initial: CheckGitChoice | null
  /** Where a merge lands (the group's work base). */
  readonly target: string | null
}

export interface CheckState {
  /** The AC's review status (the read document's, else the Canvas one). */
  readonly reviewStatus: ComputedRef<string | null>
  /** The Git reflection state of the target Flow; null before it is read or when it cannot be. */
  readonly git: ComputedRef<CheckGitState | null>
  readonly gitStatus: Readonly<Ref<'idle' | 'loading' | 'ready' | 'error'>>
  /** Message key of a failed Git state read (with nothing to show), or null. */
  readonly gitErrorKey: ComputedRef<string | null>
  readonly availability: ComputedRef<CheckAvailability>
  /** The Git choice the final approval asks for now. */
  readonly offer: ComputedRef<CheckOffer>
  /** The final approval is waiting for its answer. */
  readonly busy: Readonly<Ref<boolean>>
  /** Message key of the last refusal for this target, or null. */
  readonly failureKey: ComputedRef<string | null>
  /** The line after the last final approval answered for this target, or null. */
  readonly outcome: ComputedRef<CheckOutcomeLine | null>
  /** Final approval with the chosen Git action (null / wait: none rides along). */
  approve(choice: CheckGitChoice | null): Promise<boolean>
  /** Opens the right Tool Panel on Git. */
  openGit(): void
  /** ↗ 열기: the AC in its document window. */
  open(): void
  /** Reads the review and Git state again. */
  reload(): Promise<void>
  /** Forgets the last refusal (the dialog opens again). */
  clearFailure(): void
}

export function useCheck(target: () => InspectorTarget | null): CheckState {
  const host = inject(INSPECTOR_HOST, null)
  const flowRead = <T>(read: (flowId: string) => Promise<T>) => () => {
    const flowId = target()?.flowId
    return flowId ? read(flowId) : Promise.reject(new Error('no target Flow'))
  }

  const docState = useInspectorResource<ReviewDocument>({ target, read: checkDocumentRead, events: CHECK_DOCUMENT_EVENTS })
  const gitState = useInspectorResource<CheckGitState>({ target, read: flowRead(checkGitRead), events: CHECK_GIT_EVENTS, matches: checkFlowMatches })
  const activity = useInspectorResource({ target, read: flowRead(reviewActivityRead), events: CHECK_ACTIVITY_EVENTS, matches: checkFlowMatches })

  const busy = ref(false)
  const failure = ref<CheckFailureKind | null>(null)
  const answered = shallowRef<CheckApproveResult | null>(null)

  // A refusal or an answer belongs to the AC it was given for.
  watch(
    () => target()?.docId ?? null,
    () => {
      failure.value = null
      answered.value = null
    },
  )

  const readDocument = computed(() => {
    const read = docState.data.value
    const current = target()
    return read && current && read.record.docId === current.docId ? read : null
  })

  const git = computed(() => gitState.data.value)

  const reviewStatus = computed(() => {
    const current = target()
    return current ? checkReviewStatus(current, readDocument.value?.record ?? null) : null
  })

  const availability = computed(() => {
    const current = target()
    return checkAvailability({
      cardKind: current?.cardKind ?? 'other',
      readOnly: current?.readOnly ?? null,
      docReviewStatus: reviewStatus.value,
      // An unanswered read does not block: the server refuses while a run or a job owns the group.
      aiRunning: activity.data.value?.active === true,
      groupDisposed: readDocument.value?.groupDisposed === true,
      approvalRunning: checkApprovalRunning(git.value, current?.docId ?? null),
      busy: busy.value,
    })
  })

  const offer = computed<CheckOffer>(() => {
    const state = git.value
    if (!state || !checkGitOffered(state)) return { choices: [], initial: null, target: state?.workBaseRef ?? null }
    return { choices: state.choices, initial: checkDefaultChoice(state), target: state.workBaseRef }
  })

  const gitErrorKey = computed(() =>
    gitState.status.value === 'error' && !git.value ? checkGitErrorKey(documentErrorOf(gitState.error.value).kind) : null,
  )

  const outcome = computed(() => (answered.value ? checkOutcome(answered.value) : null))

  // The line after an answer tells what that answer did. Once the AC moves on from the status read
  // right after it (a queued job approves later, a rewind reopens it), the line is no longer true.
  let answeredStatus: string | null | undefined
  watch(
    () => target()?.docId ?? null,
    () => {
      answeredStatus = undefined
    },
  )
  watch(reviewStatus, (status) => {
    if (answered.value && answeredStatus !== undefined && status !== answeredStatus) answered.value = null
  })

  const openGit = () => host?.request({ kind: 'show-tool', tool: 'git' })

  const reload = async () => {
    await Promise.all([docState.reload(), gitState.reload(), activity.reload()])
  }

  async function approve(choice: CheckGitChoice | null): Promise<boolean> {
    const current = target()
    if (!current?.docId || !availability.value.approve.enabled) return false
    const docId = current.docId
    // Only a choice the server offers now rides along; anything else is a plain final approval.
    const sent = choice !== null && offer.value.choices.includes(choice) ? choice : null
    busy.value = true
    failure.value = null
    answered.value = null
    answeredStatus = undefined
    let ok = false
    try {
      const result = await checkApprove({ docId, choice: sent })
      if (target()?.docId === docId) {
        answered.value = result
        // The v0.1 bar opens the Git status where a conflict or a failed Git step continues.
        if (checkOutcome(result).toGit) openGit()
      }
      ok = true
    } catch (error) {
      if (target()?.docId === docId) failure.value = checkErrorOf(error).kind
    }
    // Success or refusal, the state shown is read again from the server — still busy meanwhile, so
    // the old "under review" state never offers the same final approval a second time.
    try {
      await reload()
    } finally {
      busy.value = false
    }
    if (target()?.docId === docId) answeredStatus = reviewStatus.value
    // The AC and its root moved without a review event (the Git path tells with Git frames only):
    // the Canvas card and the header are read again quietly (D#1 §5.7 refresh-flow).
    if (ok) host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    return ok
  }

  return {
    reviewStatus,
    git,
    gitStatus: gitState.status,
    gitErrorKey,
    availability,
    offer,
    busy,
    failureKey: computed(() => (failure.value ? checkFailureKey(failure.value) : null)),
    outcome,
    approve,
    openGit,
    open: () => {
      const docId = target()?.docId
      if (docId) openDocument(docId)
    },
    reload,
    clearFailure: () => {
      failure.value = null
    },
  }
}
