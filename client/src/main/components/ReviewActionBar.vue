<template>
  <div class="sticky-footer-bar">
    <div class="sfb-inner">
      <!-- Labels section (1 row normally; 2 rows when viewing a past doc) -->
      <div class="sfb-labels">
        <!-- Row 1 -->
        <span class="sfb-label">
          <!-- Head-format label is an *orientation* cue shown only while viewing a PAST doc
               (the workflow head lives elsewhere). When viewing the head doc itself, fall
               through to the normal branch so the status pills (review pending / AI review arrived) render. -->
          <template v-if="isViewingPastDoc">
            <AppIcon
              :name="statusIconClass"
              :style="statusIconStyle"
            />
            <span class="sfb-mono">{{ headDocId }}</span>
            <span class="sfb-title">— [{{ headTypeLabel }}]: {{ headDocTitle || '(pending)' }}</span>
          </template>
          <template v-else-if="currentMode !== 'sequence-complete'">
            <AppIcon
              :name="statusIconClass"
              :style="statusIconStyle"
            />
            <span class="sfb-mono">{{ docRef }}</span>
            <span v-if="docTitle" class="sfb-title">— {{ docTitle }}</span>
            <span
              v-if="showStatusPill"
              :class="['sfb-status-pill', statusPillClass]"
            >{{ statusLabel }}</span>
            <span
              v-else-if="currentMode === 'q'"
              class="sfb-status-pill q-answering"
            >{{ t('main.doc_info_panel.status_q_answering') }}</span>
            <span
              v-if="showAiArrivedPill"
              class="sfb-status-pill ai-arrived"
            ><AppIcon name="robot" /> {{ t('main.review_action_bar.ai_arrived') }}</span>
          </template>
          <template v-else>
            <span class="sfb-mono">{{ docRef }}</span>
            <span v-if="docTitle" class="sfb-title">— {{ docTitle }}</span>
            <span class="sfb-status-pill wf-done">{{ t('main.doc_info_panel.status_wf_done') }}</span>
          </template>
        </span>

      </div>

      <div v-if="isViewingPastDoc" class="sfb-actions">
        <button class="btn btn-primary btn-sm" type="button" @click="onOpenHeadDocClick">
          <AppIcon name="arrow-right" />
          {{ t('main.review_action_bar.btn_go_to_head', { doc: headDocShort }) }}
        </button>
      </div>

      <template v-else>
        <div v-if="currentMode === 'workflow'" class="sfb-actions">
          <!-- R0001 ③-a (rework): one button + trailing chevron that toggles a drop-up,
               mirroring the NextActionModal proceed dropdown (the proceed caret). Clicking it
               no longer opens a dialog; it expands the dropdown of workflow actions. -->
          <div class="ab-dd-wrap">
            <button class="btn btn-primary btn-sm ab-dd-toggle" type="button" @click.stop="toggleDropdown">
              <AppIcon name="tree-structure" /> {{ t('main.review_action_bar.btn_decide_workflow') }}
              <AppIcon class="ab-dd-chevron" :name="dropdownOpen ? 'caret-down' : 'caret-up'" />
            </button>
            <div v-if="dropdownOpen" class="ab-split-dd">
              <!-- R0001 rev4: reviewer-specified order — Copy mention → Run command → Manual decision. -->
              <button class="ab-split-item" type="button" @click="onWorkflowMentionCopyClick">
                <AppIcon name="copy" /> {{ t('main.review_action_bar.btn_copy_mention') }}
              </button>
              <button class="ab-split-item" type="button" @click="onWorkflowCommandClick">
                <AppIcon name="terminal" /> {{ t('main.review_action_bar.btn_invoke_command') }}
              </button>
              <button class="ab-split-item" type="button" @click="onWorkflowAiClick">
                <AppIcon name="robot" /> {{ t('main.review_action_bar.btn_invoke_ai') }}
              </button>
              <button class="ab-split-item" type="button" @click="onWorkflowManualClick">
                <AppIcon name="sliders-horizontal" /> {{ t('main.review_action_bar.btn_manual_decision') }}
              </button>
              <!-- R0001 (0086): continuous (unmanned) work entry — runs the sequence from the
                   current head to a chosen step without a human re-issuing tokens each step. -->
              <button class="ab-split-item ab-split-item--continuous" type="button" @click="onContinuousWorkClick">
                <AppIcon name="fast-forward" /> {{ t('main.review_action_bar.btn_continuous_work') }}
              </button>
            </div>
          </div>
        </div>

        <div v-else-if="currentMode === 'next'" class="sfb-actions">
          <!-- R0001 (0078): when the NEXT workflow step is the final-approval gate (AC),
               collapse to a single [Final approval] button — no list, no dropdown. Clicking it
               emits next-action → MainPanel.onProceedNextStep → onOpenFinalApproval, which
               opens the group final-approval (AC) screen directly. Mirrors the CH carve-out
               and takes precedence over it (an AC step is never also a CH step). -->
          <button
            v-if="isNextFinalApproval"
            class="btn btn-primary btn-sm"
            type="button"
            :disabled="canNextAction === false"
            @click="$emit('next-action')"
          >
            <AppIcon name="clipboard-text" /> {{ t('main.review_action_bar.final_approval') }}
          </button>
          <!-- TR0044.0010 rev3: when the NEXT workflow step is a conversation (CH),
               creating it is a single one-click action — no [Create empty doc]/[Create approved doc]
               split, no dialog. The CH doc is auto-created (L-AUTO approved) and opened.
               (reviewer: "only when the next document is a conversation, show just a single
               [Create conversation doc] / don't create via dialog, auto-create the conversation doc") -->
          <button
            v-else-if="isNextConversation"
            class="btn btn-primary btn-sm"
            type="button"
            :disabled="canNextAction === false"
            @click="$emit('create-conversation')"
          >
            <AppIcon name="chats" /> {{ t('main.review_action_bar.btn_create_conversation') }}
          </button>
          <div v-else-if="isNextTestReportPending" class="ab-split-wrap">
            <button class="btn btn-primary btn-sm ab-split-main" type="button" :disabled="canNextAction === false" @click="onRunTestClick">
              <AppIcon name="play" /> {{ t('main.test_run_strip.run') }}
            </button>
            <button class="btn btn-primary btn-sm ab-split-caret" type="button" @click.stop="toggleDropdown">
              <AppIcon name="caret-up" />
            </button>
            <div v-if="dropdownOpen" class="ab-split-dd">
              <!-- TS -> TSR is auto-assembled by a test run. Keep escape hatches, but do not offer a manual empty TSR. -->
              <button class="ab-split-item" type="button" @click="onNextMentionCopyClick">
                <AppIcon name="copy" /> {{ t('main.review_action_bar.btn_copy_mention') }}
              </button>
              <button class="ab-split-item" type="button" :disabled="canNextAction === false" @click="onNextProceedClick">
                <AppIcon name="arrow-right" /> {{ t('main.review_action_bar.btn_proceed_next') }}
              </button>
              <button class="ab-split-item ab-split-item--continuous" type="button" @click="onContinuousWorkClick">
                <AppIcon name="fast-forward" /> {{ t('main.review_action_bar.btn_continuous_work') }}
              </button>
            </div>
          </div>
          <div v-else class="ab-dd-wrap">
            <!-- R0001 ③-a (rework): one button + trailing chevron that toggles a drop-up,
                 mirroring the NextActionModal proceed dropdown (the proceed caret). Clicking
                 the button opens the dropdown (no dialog). The proceed/copy/create actions
                 all live as dropdown items; order per reviewer (rev3, reversed):
                 Create approved doc → Create empty doc → Copy mention → Proceed to next step. -->
            <button class="btn btn-primary btn-sm ab-dd-toggle" type="button" @click.stop="toggleDropdown">
              <AppIcon name="arrow-right" />
              {{ t('main.review_action_bar.btn_next_step', { step: nextStepLabel || t('main.review_action_bar.next_doc') }) }}
              <AppIcon class="ab-dd-chevron" :name="dropdownOpen ? 'caret-down' : 'caret-up'" />
            </button>
            <div v-if="dropdownOpen" class="ab-split-dd">
              <button v-if="canCreateApproved" class="ab-split-item" type="button" @click="onNextCreateApprovedClick">
                <AppIcon name="seal-check" /> {{ t('main.review_action_bar.btn_create_approved') }}
              </button>
              <button class="ab-split-item" type="button" @click="onNextCreateEmptyClick">
                <AppIcon name="file" /> {{ t('main.review_action_bar.btn_create_empty') }}
              </button>
              <!-- R0001 ③-b: copy the "R + previous + 2-previous" next-step mention without
                   opening the proceed dialog. -->
              <button class="ab-split-item" type="button" @click="onNextMentionCopyClick">
                <AppIcon name="copy" /> {{ t('main.review_action_bar.btn_copy_mention') }}
              </button>
              <button class="ab-split-item" type="button" :disabled="canNextAction === false" @click="onNextProceedClick">
                <AppIcon name="arrow-right" /> {{ t('main.review_action_bar.btn_proceed_next') }}
              </button>
              <!-- R0001 (0086): continuous (unmanned) work entry — runs the sequence from the
                   current head to a chosen step without a human re-issuing tokens each step. -->
              <button class="ab-split-item ab-split-item--continuous" type="button" @click="onContinuousWorkClick">
                <AppIcon name="fast-forward" /> {{ t('main.review_action_bar.btn_continuous_work') }}
              </button>
            </div>
          </div>
        </div>

        <div v-else-if="currentMode === 'q'" class="sfb-actions">
          <span class="sfb-hint">
            <AppIcon name="info" />
            {{ t('main.main_panel.q_doc_hint') }}
          </span>
        </div>

        <div v-else-if="currentMode === 'info'" class="sfb-actions"></div>

        <div v-else-if="currentMode === 'sequence-complete'" class="sfb-actions"></div>

        <!-- 0119 B0001 (NR0009 §6.2): decided-but-empty workflow — every step was deleted.
             No forward action; guide the user to the workflow strip's [시퀀스 수정] to re-add steps.
             This replaces the phantom [다음 단계] / [완료] that the empty sequence used to surface. -->
        <div v-else-if="currentMode === 'workflow-recover'" class="sfb-actions">
          <span class="sfb-hint sfb-hint--recover">
            <AppIcon name="warning-circle" />
            {{ t('main.review_action_bar.recover_hint') }}
          </span>
        </div>

        <div v-else-if="currentMode === 'rejected'" class="sfb-actions sfb-actions--rework">
          <button class="btn btn-sm sfb-rework-tool" type="button" @click="onReworkMentionCopyClick">
            <AppIcon name="copy" /> {{ t('main.review_action_bar.btn_copy_mention') }}
          </button>
          <button class="btn btn-sm sfb-rework-tool" type="button" @click="onInvokeCommandClick">
            <AppIcon name="terminal" /> {{ t('main.review_action_bar.btn_invoke_command') }}
          </button>
          <button
            class="btn btn-sm sfb-rework-complete"
            :disabled="markRevising"
            @click="onMarkRevisedClick"
          >
            <AppIcon name="check" /> {{ t('main.review_action_bar.btn_mark_revised') }}
          </button>
        </div>

        <div v-else class="sfb-actions">
          <!-- Approve -->
          <button class="btn btn-success btn-sm" :disabled="approving" @click="onApproveClick">
            <AppIcon name="check" /> {{ t('main.review_action_bar.btn_approve') }}
          </button>

          <!-- Reject -->
          <button class="btn btn-danger btn-sm" :disabled="approving" @click="onRejectClick">
            <AppIcon name="prohibit" /> {{ t('main.review_action_bar.btn_reject') }}
          </button>

          <!-- Review request ▼ split button (excluding R type) -->
          <div v-if="canShowReviewRequestAction" class="ab-split-wrap">
            <button :class="reviewRequestMainClass" @click="onMentionCopyClick">
              <AppIcon :name="reviewRequestIconClass" /> {{ reviewRequestButtonLabel }}
            </button>
            <button :class="reviewRequestCaretClass" @click.stop="toggleDropdown">
              <AppIcon name="caret-down" />
            </button>
            <div v-if="dropdownOpen" class="ab-split-dd">
              <button class="ab-split-item" @click="onMentionCopyClick">
                <AppIcon name="copy" /> {{ t('main.review_action_bar.btn_copy_mention') }}
              </button>
              <button class="ab-split-item" disabled :title="t('main.review_action_bar.tooltip_coming_soon')">
                <AppIcon name="terminal" /> {{ t('main.review_action_bar.btn_invoke_command') }}
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Approve confirm dialog. flowgate.default.0162 §3.1 "본선": when this approval
         completes a git-active group's workflow (an AC doc with a finalizable slot),
         the git finalize choice rides inside the same dialog and is applied right
         after the approval commits (git failure never reverts the approval). -->
    <ConfirmModal
      v-model:visible="showApproveConfirm"
      :title="t('main.review_action_bar.approve_confirm_title')"
      :message="showGitFinalizeBlock ? t('main.git_finalize.approve_message') : t('main.review_action_bar.approve_confirm_message')"
      :confirm-label="showGitFinalizeBlock ? t('main.git_finalize.approve_confirm') : undefined"
      @confirm="doApprove"
    >
      <div v-if="showGitFinalizeBlock && gitFin" class="ab-git-fin">
        <p class="ab-git-fin-hd">
          <AppIcon name="git-branch" />
          {{ t('main.git_finalize.approve_heading', { branch: gitFin.branch || '-' }) }}
        </p>
        <label
          v-for="c in gitFin.choices"
          :key="c"
          class="ab-git-choice"
          :class="{ sel: gitChoice === c }"
        >
          <input type="radio" name="ab-git-fin-action" :value="c" v-model="gitChoice" />
          <span class="ab-git-choice-label">{{ gitActionLabel(c) }}</span>
          <span class="ab-git-choice-desc">{{ gitActionDesc(c) }}</span>
        </label>
        <div v-if="gitFin.aux_choices?.length" class="ab-git-aux">
          <button class="ab-git-aux-toggle" type="button" @click="gitAuxOpen = !gitAuxOpen">
            <AppIcon :name="gitAuxOpen ? 'caret-down' : 'caret-right'" />
            {{ t('main.git_finalize.aux_toggle') }}
          </button>
          <template v-if="gitAuxOpen">
            <label
              v-for="c in gitFin.aux_choices"
              :key="c"
              class="ab-git-choice ab-git-choice--aux"
              :class="{ sel: gitChoice === c }"
            >
              <input type="radio" name="ab-git-fin-action" :value="c" v-model="gitChoice" />
              <span class="ab-git-choice-label">{{ gitActionLabel(c) }}</span>
              <span class="ab-git-choice-desc">{{ gitActionDesc(c) }}</span>
            </label>
          </template>
        </div>
      </div>
    </ConfirmModal>

    <!-- Revision complete confirm dialog -->
    <ConfirmModal
      v-model:visible="showMarkRevisedConfirm"
      :title="t('main.review_action_bar.mark_revised_confirm_title')"
      :message="t('main.review_action_bar.mark_revised_confirm_message')"
      @confirm="doMarkRevised"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import ConfirmModal from './ConfirmModal.vue'
import AppIcon from '@shared/AppIcon.vue'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'

type ActionBarMode = 'workflow' | 'next' | 'review' | 'q' | 'info' | 'sequence-complete' | 'rejected' | 'workflow-recover'

const props = defineProps<{
  mode?: ActionBarMode
  docId: string
  projectId: string
  groupId: string
  docRef: string
  docTitle?: string
  reviewStatus: string | null
  /** Variant C: whether AI review feedback exists; shows an "AI review arrived" pill in the pending-review footer. */
  aiReviewArrived?: boolean
  nextStepLabel?: string
  /** R0001 #2 (0048): next workflow step type code, used to gate the "create approved doc" item to N/T/TS. */
  nextStepCode?: string
  reviewRequestLabel?: string
  docType?: string
  /** D031: whether the "proceed to next step" action is available (false = show button disabled). */
  canNextAction?: boolean
  /** Latest test run status for TS -> TSR first-run action-bar mode. null means never run. */
  testRunStatus?: string | null
  // T813: head doc label fields
  headDocId?: string | null
  /** D031: head step type code (replaces headDocType), sourced from workflowViewState.headDocLabel. */
  headDocLabel?: string | null
  headDocTitle?: string | null
  // T813: viewed doc id for isViewingPastDoc check
  viewedDocId?: string | null
}>()

const emit = defineEmits<{
  'approve': [nextStatus?: string | null]
  'reject': []
  'open-mention-dialog': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'copy-rework-mention': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'invoke-command': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'revision-complete': [nextStatus?: string | null]
  'decide-workflow': []
  'copy-workflow-mention': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'invoke-workflow-command': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'invoke-workflow-ai': [payload: { docId: string; projectId: string; groupId: string; docRef: string }]
  'next-action': []
  'copy-next-mention': []
  'create-empty': []
  'create-approved': []
  'create-conversation': []
  'run-test': [] // test contract marker for shell-based TS: run-test: []
  'continuous-work': []
  'open-head-doc': [payload: { docId: string; title: string; typeCode: string | null }]
}>()

const { t } = useI18n()
const approving = ref(false)
const showApproveConfirm = ref(false)
const markRevising = ref(false)
const showMarkRevisedConfirm = ref(false)
const dropdownOpen = ref(false)
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()
const gitAuxOpen = ref(false)

// flowgate.default.0162 §3.1 "본선": git finalize state for an AC final-approval doc.
// Fetched from the same per-group endpoint the GitFinalizePanel uses, so the choice
// block, its default, and the pre-check on the server all read one source of truth.
interface GitFinState {
  branch: string | null
  status: string
  default_action: string | null
  choices: string[]
  aux_choices?: string[]
}
const gitFin = ref<GitFinState | null>(null)
const gitChoice = ref<string>('')
const isAcDoc = computed(() => (props.docType ?? '').toUpperCase() === 'AC')
// Show the choice only for an AC doc whose group slot is still actionable —
// awaiting_choice / waiting with real choices offered. Terminal (merged/pushed),
// merging, and conflict slots are excluded so a re-viewed AC never surfaces a
// stale choice; this mirrors the GitFinalizePanel radio gating exactly.
const showGitFinalizeBlock = computed(
  () =>
    isAcDoc.value &&
    !!gitFin.value &&
    (gitFin.value.status === 'awaiting_choice' || gitFin.value.status === 'waiting') &&
    (gitFin.value.choices?.length ?? 0) > 0,
)

async function fetchGitFin() {
  if (!isAcDoc.value || !props.groupId) {
    gitFin.value = null
    return
  }
  try {
    // context=approval → the server returns a display-only preliminary
    // awaiting_choice so the choice block renders in THIS confirm dialog, before
    // the approval has flipped the root to wf_done (0197 T0004 §B). Without it the
    // slot is still 'none' at approval time and the block never shows — R0001's
    // "선택할 수 없다". The persisted git status is unaffected by this read.
    const { data } = await getRequest<{ ok: boolean; state: GitFinState }>(
      `/api/v1/groups/${props.groupId}/git/finalize?context=approval`,
    )
    gitFin.value = data.state
    gitChoice.value = data.state.default_action || 'wait'
    gitAuxOpen.value = !!data.state.aux_choices?.includes(gitChoice.value)
  } catch {
    gitFin.value = null // 403/404/500 — no git block, plain approve
  }
}

// Dynamic keys are resolved with template literals in script rather than string
// concatenation in the template, so the i18n locale spec's static-reference scan
// does not extract the bare prefix as a missing key. Mirrors GitFinalizePanel.
function gitActionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function gitActionDesc(c: string): string {
  return t(`main.git_finalize.action_desc.${c}`)
}

function dispatchGitStatusEvent(git: any) {
  if (!git || typeof window === 'undefined') return
  const status = git?.result?.status ?? null
  const eventName =
    git.ok === false || status === 'conflict' || status === 'waiting'
      ? 'fg:git_status_open'
      : 'fg:git_status_refresh'
  window.dispatchEvent(new CustomEvent(eventName, {
    detail: {
      project: props.projectId || null,
      group_id: props.groupId || null,
      status,
    },
  }))
}

watch([() => props.docId, () => props.groupId, isAcDoc], fetchGitFin, { immediate: true })

const currentMode = computed(() => props.mode ?? 'review')
const normalizedStatus = computed(() => props.reviewStatus || 'pending_review')
const isRootDecided = computed(() =>
  ['R', 'B'].includes(props.docType ?? '') && (props.reviewStatus?.startsWith('wf_') ?? false)
)
const showHeadLabel = computed(() => !!props.headDocId && currentMode.value !== 'sequence-complete')
const isViewingPastDoc = computed(
  () => showHeadLabel.value && props.viewedDocId !== props.headDocId
)
const headDocShort = computed(
  () => props.headDocId ? (props.headDocId.split('.').pop() ?? props.headDocId) : ''
)
const headTypeLabel = computed(() => props.headDocLabel ? docTypeStore.getLabel(props.headDocLabel) : '')
const showStatusPill = computed(() =>
  (currentMode.value === 'review' || currentMode.value === 'info') && props.reviewStatus != null,
)
const showAiArrivedPill = computed(() =>
  !!props.aiReviewArrived && normalizedStatus.value === 'pending_review',
)
const statusLabel = computed(() => {
  if (normalizedStatus.value === 'approved') return t('main.doc_header.status_approved')
  if (normalizedStatus.value === 'rejected') return t('main.doc_header.status_rejected')
  if (normalizedStatus.value === 'revised') return t('main.review_action_bar.status_revised')
  if (normalizedStatus.value === 'wf_in_progress') return t('main.doc_info_panel.status_wf_in_progress')
  if (normalizedStatus.value === 'wf_done') return t('main.doc_info_panel.status_wf_done')
  return t('main.review_action_bar.status_pending_review')
})
const statusPillClass = computed(() => {
  if (normalizedStatus.value === 'approved') return 'approved'
  if (normalizedStatus.value === 'rejected') return 'rejected'
  if (normalizedStatus.value === 'revised') return 'revised'
  if (normalizedStatus.value === 'wf_in_progress') return 'wf-in-progress'
  if (normalizedStatus.value === 'wf_done') return 'wf-done'
  return 'review-pending'
})
const statusIconClass = computed(() => {
  if (isRootDecided.value) return 'check-circle'
  if (normalizedStatus.value === 'approved' || normalizedStatus.value === 'wf_done') return 'check-circle'
  if (normalizedStatus.value === 'rejected') return 'prohibit'
  if (normalizedStatus.value === 'revised') return 'arrows-clockwise'
  if (normalizedStatus.value === 'wf_in_progress') return 'play'
  return 'hourglass-medium'
})
const statusIconStyle = computed(() => ({
  color:
    isRootDecided.value
      ? '#16a34a'
      : normalizedStatus.value === 'approved' || normalizedStatus.value === 'wf_done'
      ? '#16a34a'
      : normalizedStatus.value === 'rejected'
        ? '#dc2626'
        : normalizedStatus.value === 'revised' || normalizedStatus.value === 'wf_in_progress'
          ? '#0284c7'
          : '#d97706',
}))
const defaultReviewRequestLabel = computed(() => t('main.review_action_bar.btn_review_request'))
// Before approval (pending review or revised), requesting a review is the primary action.
// Always force the review-request label so a workflow "next step" label cannot take over
// this slot and hide the button. The mode='next' branch handles advancement after approval.
const isPreApprovalReview = computed(() =>
  normalizedStatus.value === 'pending_review' || normalizedStatus.value === 'revised',
)
const reviewRequestButtonLabel = computed(() =>
  isPreApprovalReview.value
    ? defaultReviewRequestLabel.value
    : (props.reviewRequestLabel || defaultReviewRequestLabel.value),
)
const isNextStageRequest = computed(() =>
  reviewRequestButtonLabel.value !== defaultReviewRequestLabel.value,
)
const reviewRequestVariantClass = computed(() =>
  // Review request (default) uses amber for AI worker actions; next-step advancement uses primary emphasis.
  isNextStageRequest.value ? 'btn-primary' : 'btn-soft-amber',
)
const reviewRequestMainClass = computed(() => [
  'btn',
  reviewRequestVariantClass.value,
  'btn-sm',
  'ab-split-main',
  isNextStageRequest.value ? 'ab-split-main--next' : '',
])
const reviewRequestCaretClass = computed(() => [
  'btn',
  reviewRequestVariantClass.value,
  'btn-sm',
  'ab-split-caret',
  isNextStageRequest.value ? 'ab-split-caret--next' : '',
])
const reviewRequestIconClass = computed(() =>
  isNextStageRequest.value ? 'arrow-right' : 'paper-plane-tilt',
)
const canShowReviewRequestAction = computed(() =>
  // R (workflow root) and AC (final approval gate) are not AI review targets. They are
  // synthetic/gate types rather than content documents, so exclude them from this slot.
  !['R', 'AC'].includes(props.docType ?? '') &&
  normalizedStatus.value !== 'rejected' &&
  (!isNextStageRequest.value || normalizedStatus.value === 'approved'),
)

// R0001 #2 (0048): "create approved doc" is offered only when the next step is an
// instruction type (N/T). TS is excluded (group 0121 R0001 — a test-scenario directive
// is token-issued/AI-authored, never auto-approved; it falls back to the normal
// next-action/copy-next-mention token path). approve-permission gating is enforced by the
// server (next-approved → 403); the FE does not hold the granular permission set.
const canCreateApproved = computed(() =>
  ['N', 'T'].includes((props.nextStepCode ?? '').toUpperCase()),
)

// TR0044.0010 rev3: the next workflow step is a conversation (CH) → the 'next' action
// collapses to a single [Create conversation doc] auto-create button (no split / no dialog).
const isNextConversation = computed(() =>
  (props.nextStepCode ?? '').toUpperCase() === 'CH',
)

// R0001 (0078): the next workflow step is the final-approval gate (AC) → the 'next'
// action collapses to a single [Final approval] button (no list / no dropdown). AC is an
// approval gate, not a document-creation target, so the create-approved / create-empty
// / copy-mention items are meaningless here. Mirrors the CH carve-out and takes
// precedence over it. next-action → MainPanel.onProceedNextStep → onOpenFinalApproval.
const isNextFinalApproval = computed(() =>
  (props.nextStepCode ?? '').toUpperCase() === 'AC',
)

const isNextTestReportPending = computed(() =>
  (props.docType ?? '').toUpperCase() === 'TS' &&
  (props.nextStepCode ?? '').toUpperCase() === 'TSR' &&
  props.testRunStatus == null,
)

function onNextCreateEmptyClick() {
  dropdownOpen.value = false
  emit('create-empty')
}

function onNextCreateApprovedClick() {
  dropdownOpen.value = false
  emit('create-approved')
}

function onNextProceedClick() {
  dropdownOpen.value = false
  emit('next-action')
}

function onRunTestClick() {
  dropdownOpen.value = false
  emit('run-test')
}

// R0001 ③-b: copy the next-step mention directly from the action bar, without
// going through the proceed dialog. MainPanel reuses the existing token/mention
// plumbing; the backend auto-merges "R + previous + 2-previous" predecessors.
function onNextMentionCopyClick() {
  dropdownOpen.value = false
  emit('copy-next-mention')
}

function onOpenHeadDocClick() {
  if (!props.headDocId) return
  emit('open-head-doc', {
    docId: props.headDocId,
    title: props.headDocTitle ?? '',
    typeCode: props.headDocLabel ?? null,
  })
}

function onApproveClick() {
  showApproveConfirm.value = true
}

async function doApprove() {
  if (approving.value) return
  approving.value = true
  try {
    // §3.1: the git finalize choice rides on the approve request only when the
    // choice block is showing (AC + git-active + finalizable). The server pre-checks
    // it before approving and runs the finalize after — a git failure surfaces as
    // { git: { ok: false } } at HTTP 200 without reverting the approval.
    const body: Record<string, unknown> = { doc_id: props.docId, comment: null }
    if (showGitFinalizeBlock.value && gitChoice.value) {
      body.git_action = gitChoice.value
    }
    const res = await postRequest<any>(
      `/api/v1/documents/review_transitions/approve`,
      body,
    )
    const git = (res.data as any)?.git
    if (git && git.ok === false) {
      // Approval stood; only the git post-step failed — say so, don't block.
      // base_dirty (E3) is actionable: name the guidance and let dispatchGitStatusEvent
      // open the header Git panel where the files are listed (T0010 §b).
      if (git.error?.code === 'base_dirty') {
        showToast(t('main.git_finalize.base_dirty_toast'), 'warning')
      } else {
        showToast(git.error?.message || t('main.git_finalize.failed'), 'warning')
      }
    } else if (git?.result?.status === 'conflict') {
      showToast(t('main.git_finalize.conflict_toast', { n: (git.result.conflict_files || []).length }), 'warning')
    } else if (git?.result?.status === 'merged') {
      const key = git.result?.pushed === false ? 'main.git_finalize.merged_local_toast' : 'main.git_finalize.merged_toast'
      showToast(t(key, { commit: git.result.merge_commit || '' }), 'success')
    } else if (git?.result?.status === 'pushed') {
      showToast(t('main.git_finalize.pushed_toast'), 'success')
    } else if (git?.result?.status === 'waiting') {
      showToast(t('main.git_finalize.waiting_toast'), 'success')
    }
    if (git) {
      dispatchGitStatusEvent(git)
    } else if (isAcDoc.value && typeof window !== 'undefined') {
      // 0177 NR0016 §3 (client fallback): a plain final approval carries no git
      // payload — the choice block is hidden while the slot is still 'none', so
      // no git_action rode along — yet the approval just made this group
      // finalize-pending. Poke the header menu; its fetchStatus hits
      // project_git_status, which realizes the lazy none→awaiting_choice
      // transition and broadcasts git_pending_changed for everyone else.
      window.dispatchEvent(new CustomEvent('fg:git_status_refresh', {
        detail: {
          project: props.projectId || null,
          group_id: props.groupId || null,
          status: null,
        },
      }))
    }
    // Pass the server-confirmed status up so DocHeader can optimistically flip the
    // strip/action bar before the refetch round-trip (gap D, NR0003 §6 item 2).
    const updated = (res.data as any)?.document ?? (res.data as any)?.data ?? res.data
    emit('approve', updated?.doc_review_status ?? 'approved')
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? e
    console.error(t('main.review_action_bar.error_approve_failed_log'), detail)
    showToast(t('main.review_action_bar.toast_approve_failed', { detail }), 'danger')
  } finally {
    approving.value = false
  }
}

function onRejectClick() {
  emit('reject')
}

function onMarkRevisedClick() {
  showMarkRevisedConfirm.value = true
}

async function doMarkRevised() {
  if (markRevising.value) return
  markRevising.value = true
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/review_transitions/mark_revised`,
      { doc_id: props.docId, comment: null },
    )
    showToast(t('main.review_action_bar.toast_mark_revised_success'), 'success')
    // Pass the server-confirmed status up for the optimistic flip (gap D, NR0003 §6 item 2).
    const updated = (res.data as any)?.document ?? (res.data as any)?.data ?? res.data
    emit('revision-complete', updated?.doc_review_status ?? null)
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? e
    showToast(t('main.review_action_bar.toast_mark_revised_failed', { detail }), 'danger')
  } finally {
    markRevising.value = false
  }
}

function reworkPayload() {
  return {
    docId: props.docId,
    projectId: props.projectId,
    groupId: props.groupId,
    docRef: props.docRef,
  }
}

function onReworkMentionCopyClick() {
  emit('copy-rework-mention', reworkPayload())
}

function onInvokeCommandClick() {
  emit('invoke-command', reworkPayload())
}

function toggleDropdown() {
  dropdownOpen.value = !dropdownOpen.value
}

function onWorkflowManualClick() {
  dropdownOpen.value = false
  emit('decide-workflow')
}

function onWorkflowMentionCopyClick() {
  dropdownOpen.value = false
  emit('copy-workflow-mention', reworkPayload())
}

function onWorkflowCommandClick() {
  dropdownOpen.value = false
  emit('invoke-workflow-command', reworkPayload())
}

function onWorkflowAiClick() {
  dropdownOpen.value = false
  emit('invoke-workflow-ai', reworkPayload())
}

// R0001 (0086): open the continuous (unmanned) work dialog. Shared by the 'workflow' and
// 'next' dropdowns; MainPanel owns the sequence dialog + warning gate + token issuance.
function onContinuousWorkClick() {
  dropdownOpen.value = false
  emit('continuous-work')
}

function onMentionCopyClick() {
  dropdownOpen.value = false
  emit('open-mention-dialog', {
    docId: props.docId,
    projectId: props.projectId,
    groupId: props.groupId,
    docRef: props.docRef,
  })
}

function onOutsideClick() {
  if (dropdownOpen.value) dropdownOpen.value = false
}

onMounted(() => {
  window.addEventListener('click', onOutsideClick)
})

onBeforeUnmount(() => {
  window.removeEventListener('click', onOutsideClick)
})
</script>

<style scoped>
/* T813: two-line layout (head + viewed doc) */
.sfb--two-line {
  height: auto;
  min-height: 60px;
}
.sfb-labels {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  justify-content: center;
}
.sfb-row-prefix {
  flex-shrink: 0;
  font-weight: 700;
  color: #60748a;
  font-size: 0.75rem;
}
.sfb-label--past {
  opacity: 0.72;
  font-size: 0.78rem;
}

.ab-split-wrap {
  position: relative;
  display: inline-flex;
}

/* R0001 ③-a (rework): single button + trailing chevron drop-up
   (mirrors NextActionModal .nad-proceed-wrap). */
.ab-dd-wrap {
  position: relative;
  display: inline-flex;
}

.ab-dd-toggle {
  border-radius: 6px;
}

.ab-dd-chevron {
  font-size: 0.6rem;
  margin-left: 4px;
}

.sfb-status-pill {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: .72rem;
  margin-left: 4px;
  font-weight: 700;
}

.sfb-status-pill.review-pending {
  background: #fef3c7;
  color: #d97706;
  border: 1px solid #fde68a;
}

.sfb-status-pill.revised {
  background: #e0f2fe;
  color: #0284c7;
  border: 1px solid #bae6fd;
}

.sfb-status-pill.approved,
.sfb-status-pill.wf-done {
  background: #dcfce7;
  color: #16a34a;
  border: 1px solid #bbf7d0;
}

.sfb-status-pill.wf-in-progress {
  background: #e0f2fe;
  color: #0284c7;
  border: 1px solid #bae6fd;
}

.sfb-status-pill.rejected {
  background: #fee2e2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.sfb-status-pill.q-answering {
  background: #e0f2fe;
  color: #0284c7;
  border: 1px solid #bae6fd;
}

.sfb-status-pill.ai-arrived {
  background: #fff7d6;
  color: #6f4e00;
  border: 1px solid #f6d98b;
}

.sfb-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-m);
  font-size: .82rem;
  font-weight: 600;
}

/* 0119 B0001: decided-but-empty recovery hint — amber, mirrors DocWorkflow's .wf-empty-recover */
.sfb-hint--recover {
  color: #92400e;
}
.sfb-hint--recover i {
  color: var(--warning, #d97706);
}

.sfb-rework-tool {
  background: #fff7d6;
  border: 1px solid #f6d98b;
  color: #6f4e00;
}

.sfb-rework-tool:hover {
  background: #ffefb0;
  border-color: #eecb6a;
}

.sfb-rework-complete {
  margin-left: 4px;
  background: #fff;
  border: 1px solid #cbd5e1;
  color: var(--text, #1e293b);
  font-weight: 700;
}

.sfb-rework-complete:hover:not(:disabled) {
  background: #f8fafc;
  border-color: #94a3b8;
}

.ab-split-main {
  border-radius: 6px 0 0 6px;
  border-right: none;
}

.ab-split-main--next {
  font-weight: 600;
}

.ab-split-caret {
  border-radius: 0 6px 6px 0;
  padding-inline: 8px;
  min-width: 0;
}

.ab-split-caret--next {
  border-left: 1px solid rgba(255, 255, 255, .22);
}

.ab-split-dd {
  position: absolute;
  bottom: calc(100% + 6px);
  top: auto;
  /* R0001 (0087): right-anchor so a narrow toggle (short label e.g. "로직")
     opens the menu leftward and never overflows the viewport's right edge. */
  right: 0;
  left: auto;
  min-width: 140px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 200;
  overflow: hidden;
}

.ab-split-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  font-size: 0.8125rem;
  color: var(--text, #1e293b);
  cursor: pointer;
  text-align: left;
  transition: background 0.1s;
}

.ab-split-item:hover:not(:disabled) {
  background: var(--bg-hover, #f1f5f9);
}

.ab-split-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* R0001 (0086): the continuous-work entry is set apart from the per-step actions above it. */
.ab-split-item--continuous {
  border-top: 1px solid var(--border, #e2e8f0);
  color: var(--primary, #2563eb);
  font-weight: 600;
}

/* flowgate.default.0162 §3.1 — git finalize choice inside the AC approve confirm. */
.ab-git-fin {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ab-git-fin-hd {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text, #1e293b);
}
.ab-git-choice {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  cursor: pointer;
}
.ab-git-choice.sel {
  border-color: var(--primary);
  background: var(--primary-l, #eff6ff);
}
.ab-git-choice input {
  display: none;
}
.ab-git-choice-label {
  font-weight: 700;
  font-size: 0.8rem;
}
.ab-git-choice-desc {
  font-size: 0.72rem;
  color: var(--text-m);
}
.ab-git-choice--aux {
  margin-top: 8px;
}
.ab-git-aux {
  margin-top: 2px;
}
.ab-git-aux-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--text-m);
  font-size: 0.76rem;
  cursor: pointer;
  padding: 2px 0;
}
.ab-git-aux-toggle:hover {
  color: var(--primary);
}
</style>


