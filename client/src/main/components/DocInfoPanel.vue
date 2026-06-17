<template>
  <aside class="doc-info-panel">
    <!-- Collapsed rail (vertical label) -->
    <button class="doc-panel-rail" @click="$emit('toggle')" :title="t('main.doc_info_panel.expand')">
      <i class="fa-solid fa-circle-info"></i>
      {{ t('main.doc_info_panel.title') }}
    </button>

    <!-- Expanded body -->
    <div class="doc-info-body">
      <!-- Section 1: document status -->
      <div class="dip-section">
        <div class="dip-section-head">
          <span class="dip-section-title">
            <i class="fa-solid fa-circle-dot"></i>
            {{ t('main.doc_info_panel.section_status') }}
          </span>
          <button class="dip-panel-close btn-icon" @click="$emit('toggle')" :title="t('main.doc_info_panel.collapse')">
            <i class="fa-solid fa-chevron-right"></i>
          </button>
        </div>
        <div :class="['dip-status-badge', statusClass, nextStep?.visual === 'current' ? 'dip-badge-clickable' : '']" @click="nextStep?.visual === 'current' ? emit('next-action') : undefined">
          <i class="fa-solid" :class="statusIcon"></i>
          {{ statusLabel }}
        </div>
        <p class="dip-status-desc">{{ statusDesc }}</p>
      </div>

      <!-- Section 2: Q&A (group 0022 D0005 §3.1 — added after removing [Proceed workflow]) -->
      <div class="dip-section">
        <div class="dip-section-head">
          <span class="dip-section-title">
            <i class="fa-solid fa-circle-question"></i>
            {{ t('main.doc_info_panel.section_qa') }}
          </span>
          <button class="btn-icon dip-qa-add" type="button" @click="toggleNewQ" :title="t('main.doc_info_panel.qa_add')">
            <i class="fa-solid fa-plus"></i>
          </button>
        </div>

        <!-- new question inline form ([+ Query]) -->
        <div v-if="newQOpen" class="dip-qa-form">
          <input v-model="newQTitle" class="dip-qa-input" :placeholder="t('main.doc_info_panel.qa_title_ph')" />
          <textarea v-model="newQBody" class="dip-qa-textarea" rows="3" :placeholder="t('main.doc_info_panel.qa_body_ph')"></textarea>
          <div class="dip-qa-form-actions">
            <button class="btn btn-sm btn-outline" type="button" @click="toggleNewQ">{{ t('common.cancel') }}</button>
            <button class="btn btn-sm btn-primary" type="button" :disabled="!newQBody.trim() || qaBusy" @click="submitNewQ">{{ t('main.doc_info_panel.qa_register') }}</button>
          </div>
        </div>

        <div v-if="qaLoading" class="dip-qa-hint">{{ t('common.loading') }}</div>
        <div v-else-if="qaError" class="dip-qa-error">{{ qaError }}</div>
        <template v-else>
          <div v-if="qaItems.length === 0" class="dip-reject-empty">
            <i class="fa-regular fa-circle-question"></i>
            <span>{{ t('main.doc_info_panel.qa_empty') }}</span>
          </div>
          <div v-for="item in qaItems" :key="item.id" class="dip-qa-item" :class="{ open: expandedItemId === item.id }">
            <button type="button" class="dip-qa-item-head" @click="toggleItem(item.id)">
              <span class="q-state-badge" :class="itemAnswered(item) ? 'done' : 'pending'" style="font-size:.6rem; padding:1px 7px;">
                {{ itemAnswered(item) ? t('main.doc_info_panel.qa_answered') : t('main.doc_info_panel.qa_answering') }}
              </span>
              <span class="dip-qa-item-title">Q{{ item.seq }} · {{ item.title || item.body }}</span>
              <i class="fa-solid fa-chevron-down dip-qa-chevron"></i>
            </button>
            <div v-if="expandedItemId === item.id" class="dip-qa-item-body">
              <div class="dip-qa-meta">
                <i :class="item.asker_kind === 'ai' ? 'fa-solid fa-robot' : 'fa-solid fa-user'"></i>
                {{ item.asker_kind === 'ai' ? t('main.doc_info_panel.qa_by_ai') : t('main.doc_info_panel.qa_by_human') }}
              </div>
              <!-- R0001 (rev1): the question body is shown directly when the item is
                   expanded — a single disclosure (the item head) rather than a nested
                   fold, so reading a query no longer needs a second click. The body is
                   still height-capped with an internal scroll so a long question never
                   stretches the side panel (AC1/AC2/AC4). -->
              <div class="dip-qa-fold">
                <div class="dip-qa-fold-head">
                  <span class="dip-qa-fold-label">{{ t('main.doc_info_panel.qa_question') }}</span>
                </div>
                <div class="dip-qa-fold-body">{{ item.body }}</div>
              </div>
              <template v-if="(item.answers?.length ?? 0) > 0">
                <div
                  v-for="(a, ai) in item.answers"
                  :key="ai"
                  class="dip-qa-fold dip-qa-fold--answer"
                >
                  <div class="dip-qa-fold-head">
                    <span class="dip-qa-fold-label">
                      <i :class="a.author_kind === 'ai' ? 'fa-solid fa-robot' : 'fa-solid fa-user'" class="dip-qa-answer-icon"></i>
                      {{ t('main.doc_info_panel.qa_answer') }}
                    </span>
                  </div>
                  <div class="dip-qa-fold-body">{{ a.body }}</div>
                </div>
              </template>
              <div v-if="answerOpenId === item.id" class="dip-qa-form">
                <textarea v-model="answerBody" class="dip-qa-textarea" rows="3" :placeholder="t('main.doc_info_panel.qa_answer_ph')"></textarea>
                <div class="dip-qa-form-actions">
                  <button class="btn btn-sm btn-outline" type="button" @click="answerOpenId = null">{{ t('common.cancel') }}</button>
                  <button class="btn btn-sm btn-primary" type="button" :disabled="!answerBody.trim() || qaBusy" @click="submitAnswer(item.id)">{{ t('main.doc_info_panel.qa_answer_submit') }}</button>
                </div>
              </div>
              <div v-else class="dip-qa-actions">
                <button class="btn btn-sm btn-outline" type="button" @click="openAnswer(item.id)">{{ t('main.doc_info_panel.qa_answer_write') }}</button>
                <button class="btn btn-sm btn-outline" type="button" :disabled="qaBusy" @click="requestAiAnswer(item.id)">
                  <i class="fa-solid fa-robot"></i> {{ t('main.doc_info_panel.qa_answer_ai') }}
                </button>
              </div>
            </div>
          </div>
          <!-- R0001 (AC3): full Q&A in a modal (same dialog form as the AI review/
               rejection show-all) so the entire query/answer text is reachable without
               stretching the side panel. -->
          <button v-if="qaItems.length > 0" class="dip-ai-history-link" type="button" @click="qaHistoryVisible = true">
            <i class="fa-solid fa-up-right-from-square"></i>
            {{ t('main.doc_info_panel.view_full') }}
          </button>
        </template>
      </div>

      <!-- Section 2.5: AI review feedback (latest review plus full history) -->
      <div v-if="canShowReviewSection" class="dip-section">
        <div class="dip-section-title">
          <i class="fa-solid fa-robot"></i>
          {{ t('main.doc_info_panel.section_ai_review') }}
        </div>
        <template v-if="aiReview">
          <div class="dip-ai-entry">
            <div class="dip-ai-entry-head">
              <span class="dip-ai-meta">{{ formatRejectionDate(aiReview.reviewed_at ?? aiReview.created_at ?? '') }} · {{ aiReview.reviewer_name || t('main.doc_info_panel.ai_review_author') }}</span>
              <!-- Findings collapse under the verdict badge (toggle); plain badge when there are none. -->
              <button
                v-if="aiFindings.length"
                type="button"
                class="dip-ai-verdict dip-ai-verdict--toggle"
                :class="aiVerdictClass(aiReview.verdict)"
                :aria-expanded="findingsExpanded"
                @click="toggleFindings"
              >
                {{ aiVerdictLabel(aiReview) }}
                <i class="fa-solid fa-chevron-down dip-ai-chevron" :class="{ open: findingsExpanded }"></i>
              </button>
              <span v-else class="dip-ai-verdict" :class="aiVerdictClass(aiReview.verdict)">{{ aiVerdictLabel(aiReview) }}</span>
            </div>
            <ol v-if="aiFindings.length && findingsExpanded" class="dip-ai-findings">
              <li v-for="(f, i) in aiFindings" :key="i" class="dip-ai-finding">
                <span v-if="f.locus" class="dip-ai-finding-locus">{{ f.locus }}</span>
                <span class="dip-ai-finding-note">{{ f.note }}</span>
              </li>
            </ol>
            <!-- R0001 (rev1): the comment fold now uses the SAME control idiom as the
                 rejection reason and the AI response — a clickable header row carrying a
                 label + chevron (no separate "expand/collapse" text button), with the body
                 clamped to two lines and expanding to a height-capped scroll. Only the
                 accent colour stays amber to match this box's verdict tone. -->
            <div v-if="aiReview.comment" class="dip-ai-comment" :class="{ open: commentExpanded }">
              <button
                type="button"
                class="dip-ai-comment-toggle"
                :aria-expanded="commentExpanded"
                :title="t(commentExpanded ? 'main.doc_info_panel.ai_comment_collapse' : 'main.doc_info_panel.ai_comment_expand')"
                @click="commentExpanded = !commentExpanded"
              >
                <span class="dip-ai-comment-label">
                  <i class="fa-solid fa-robot"></i> {{ t('main.doc_info_panel.ai_comment_label') }}
                </span>
                <i class="fa-solid fa-chevron-down dip-ai-comment-chevron"></i>
              </button>
              <div class="dip-ai-comment-body">{{ aiReview.comment }}</div>
            </div>
          </div>
          <!-- R0001: a "show-all" entry is always offered when there is any review on
               record (not only when prior reviews exist), so the most common single-review
               case can still reach the full-content history view. -->
          <button v-if="(aiReviewHistory?.length ?? 0) > 0" class="dip-ai-history-link" type="button" @click="emit('open-review-history')">
            <i class="fa-solid fa-clock-rotate-left"></i>
            {{ priorReviewCount > 0
                ? t('main.doc_info_panel.ai_review_view_history_count', { n: priorReviewCount })
                : t('main.doc_info_panel.view_full') }}
          </button>
        </template>
        <template v-else>
          <div class="dip-reject-empty">
            <i class="fa-regular fa-comment-dots"></i>
            <span>{{ t('main.doc_info_panel.ai_review_empty') }}</span>
            <span class="dip-reject-hint">{{ t('main.doc_info_panel.ai_review_hint') }}</span>
          </div>
        </template>
      </div>

      <!-- Section 3: rejection reason -->
      <div v-if="canShowRejectSection" class="dip-section">
        <div class="dip-section-title">
          <i class="fa-solid fa-comment-slash"></i>
          {{ t('main.doc_info_panel.section_reject') }}
        </div>
        <template v-if="rejectionDisplayReason">
          <div class="dip-reject-quote" :class="{ open: rejectionExpanded }">
            <button
              class="dip-reject-quote-toggle"
              type="button"
              :aria-expanded="rejectionExpanded"
              :title="t(rejectionExpanded ? 'main.doc_info_panel.rejection_collapse' : 'main.doc_info_panel.rejection_expand')"
              @click="rejectionExpanded = !rejectionExpanded"
            >
              <span class="dip-reject-quote-author">
                <i class="fa-solid fa-user-shield"></i>
                <strong>{{ rejectionAuthorLabel }}</strong>
              </span>
              <span v-if="latestRejection" class="dip-reject-date">{{ formatRejectionDate(latestRejection.rejected_at) }}</span>
              <i class="fa-solid fa-chevron-down dip-reject-chevron"></i>
            </button>
            <div class="dip-reject-quote-body">
              <span class="dip-reject-reason">{{ rejectionDisplayReason }}</span>

              <!-- R0001: as with the AI review, offer "show-all" whenever there is a
                   rejection on record so a single rejection still has a full-view entry. -->
              <button v-if="rejectionHistoryList.length > 0" class="dip-ai-history-link" type="button" @click="emit('open-review-history')">
                <i class="fa-solid fa-clock-rotate-left"></i>
                {{ priorRejectionCount > 0
                    ? t('main.doc_info_panel.rejection_view_history_count', { n: priorRejectionCount })
                    : t('main.doc_info_panel.view_full') }}
              </button>
            </div>
          </div>

          <!-- P0005/T0006: the AI's response to this rejection, threaded as a reply
               directly UNDER the quote (a sibling, not nested inside it). Collapsible
               like the rejection quote (folded by default); the body is height-capped
               so a long response scrolls instead of stretching the whole panel. -->
          <div v-if="latestRejection?.ai_response" class="dip-ai-response" :class="{ open: aiResponseExpanded }">
            <button
              type="button"
              class="dip-ai-response-head"
              :aria-expanded="aiResponseExpanded"
              :title="t(aiResponseExpanded ? 'main.doc_info_panel.rejection_collapse' : 'main.doc_info_panel.rejection_expand')"
              @click="aiResponseExpanded = !aiResponseExpanded"
            >
              <span class="dip-ai-response-label">
                <i class="fa-solid fa-reply dip-ai-response-thread"></i>
                <i class="fa-solid fa-robot"></i> {{ t('main.doc_info_panel.ai_response_label') }}
              </span>
              <span v-if="latestRejection.responded_at" class="dip-ai-response-date">{{ formatRejectionDate(latestRejection.responded_at) }}</span>
              <i class="fa-solid fa-chevron-down dip-ai-response-chevron"></i>
            </button>
            <div class="dip-ai-response-body">{{ latestRejection.ai_response }}</div>
          </div>
        </template>
        <template v-else>
          <div class="dip-reject-empty">
            <i class="fa-regular fa-comment-dots"></i>
            <span>{{ t('main.doc_info_panel.reject_empty') }}</span>
            <span class="dip-reject-hint">{{ t('main.doc_info_panel.reject_hint') }}</span>
          </div>
        </template>
      </div>
    </div>

    <!-- R0001 (AC3): show-all modal for the full query/answer text. Owned here since
         the QA data already lives in this panel (the AI review history modal lives in
         MainPanel because its data lives in DocHeader). -->
    <QaHistoryDialog v-model:visible="qaHistoryVisible" :items="qaItems" />
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import QaHistoryDialog from './QaHistoryDialog.vue'
import type { StepState } from '../workflow/workflowViewState'
import type { AiReview, AiReviewFinding } from '../types/aiReview'
import type { RejectionHistoryItem } from '../composables/useFlowGateToken'

const { t } = useI18n()

const props = defineProps<{
  docId: string
  typeCode: string | null
  reviewStatus: string | null
  rejectReason: string | null
  rejectionHistory?: RejectionHistoryItem[]
  aiReview?: AiReview | null
  aiReviewHistory?: AiReview[]
  qStatus?: string | null
  workflowSteps?: string[] | null
  selfIndex?: number | null
  stepStates: StepState[]
  nextStepIndex: number | null
  collapsed: boolean
}>()

const emit = defineEmits<{
  toggle: []
  'next-action': []
  'open-review-history': []
}>()

function formatRejectionDate(iso: string): string {
  try {
    const d = new Date(iso)
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${mm}-${dd} ${hh}:${min}`
  } catch {
    return iso
  }
}

const rejectionHistoryList = computed(() => [...(props.rejectionHistory ?? [])].reverse())
const latestRejection = computed(() => rejectionHistoryList.value[0] ?? null)
const priorRejectionCount = computed(() => Math.max(0, rejectionHistoryList.value.length - 1))
const rejectionDisplayReason = computed(() => latestRejection.value?.reason ?? props.rejectReason ?? '')
const rejectionExpanded = ref(false)
// The AI response folds independently of the rejection quote (reviewer: "let the response be collapsible too").
const aiResponseExpanded = ref(false)

// rejected_by is a user UUID; resolve it to a display name (same /api/v1/users/{id}
// pattern as DocHeader.fetchOwner) so the rejection line never shows a raw UUID.
// An empty cache entry means "resolved but unknown" -> show nothing rather than the UUID.
const rejectedByNames = ref<Record<string, string>>({})

async function resolveRejectedBy(userId: string) {
  if (!userId || rejectedByNames.value[userId] !== undefined) return
  rejectedByNames.value = { ...rejectedByNames.value, [userId]: '' } // mark in-flight
  try {
    const res = await getRequest<any>(`/api/v1/users/${encodeURIComponent(userId)}`)
    const user = (res.data as any)?.data ?? res.data
    const name = user?.username ?? user?.display_name ?? ''
    rejectedByNames.value = { ...rejectedByNames.value, [userId]: name }
  } catch {
    rejectedByNames.value = { ...rejectedByNames.value, [userId]: '' }
  }
}

function rejectedByDisplay(userId: string | null | undefined): string {
  if (!userId) return ''
  return rejectedByNames.value[userId] || ''
}

const rejectionAuthorLabel = computed(() => (
  rejectedByDisplay(latestRejection.value?.rejected_by)
  || t('main.doc_info_panel.rejection_review_author')
))

watch(
  () => latestRejection.value?.rejected_by,
  (id) => { if (id) resolveRejectedBy(id) },
  { immediate: true },
)
watch(
  () => `${latestRejection.value?.rejected_at ?? ''}:${rejectionDisplayReason.value}`,
  () => { rejectionExpanded.value = false; aiResponseExpanded.value = false },
  { immediate: true },
)

const isRootWorkflowUndecided = computed(
  () =>
    ['R', 'B'].includes(props.typeCode ?? '') &&
    (props.workflowSteps == null || props.workflowSteps.length === 0),
)

const statusClass = computed(() => {
  if (isQDoc.value) return isQDone.value ? 'approved' : 'wf-in-progress'
  switch (effectiveStatus.value) {
    case 'pending_review': return 'review-pending'
    case 'approved':       return 'approved'
    case 'rejected':       return 'rejected'
    case 'revised':        return 'revised'
    case 'wf_in_progress': return 'wf-in-progress'
    case 'wf_done':        return 'wf-done'
    default:               return isRootWorkflowUndecided.value ? 'not-decided' : 'review-pending'
  }
})

const statusIcon = computed(() => {
  if (isQDoc.value) return isQDone.value ? 'fa-circle-check' : 'fa-clock'
  switch (effectiveStatus.value) {
    case 'pending_review': return 'fa-hourglass-half'
    case 'approved':       return 'fa-circle-check'
    case 'rejected':       return 'fa-circle-xmark'
    case 'revised':        return 'fa-rotate'
    case 'wf_in_progress': return 'fa-play'
    case 'wf_done':        return 'fa-circle-check'
    default:               return isRootWorkflowUndecided.value ? 'fa-circle-question' : 'fa-hourglass-half'
  }
})

const statusLabel = computed(() => {
  if (isQDoc.value) {
    return isQDone.value
      ? t('main.doc_info_panel.status_q_done')
      : t('main.doc_info_panel.status_q_answering')
  }
  switch (effectiveStatus.value) {
    case 'pending_review': return t('main.doc_info_panel.status_pending')
    case 'approved':       return t('main.doc_info_panel.status_approved')
    case 'rejected':       return t('main.doc_info_panel.status_rejected')
    case 'revised':        return t('main.doc_info_panel.status_revised')
    case 'wf_in_progress': return t('main.doc_info_panel.status_wf_in_progress')
    case 'wf_done':        return t('main.doc_info_panel.status_wf_done')
    default:
      return isRootWorkflowUndecided.value
        ? t('main.doc_info_panel.status_not_decided')
        : t('main.doc_info_panel.status_pending')
  }
})

const statusDesc = computed(() => {
  if (isQDoc.value) {
    return isQDone.value
      ? t('main.doc_info_panel.status_q_done_desc')
      : t('main.doc_info_panel.status_q_answering_desc')
  }
  switch (props.reviewStatus) {
    case 'pending_review':
      return props.aiReview
        ? t('main.doc_info_panel.status_pending_desc_ai')
        : t('main.doc_info_panel.status_pending_desc')
    case 'approved':       return t('main.doc_info_panel.status_approved_desc')
    case 'rejected':       return t('main.doc_info_panel.status_rejected_desc')
    case 'revised':        return t('main.doc_info_panel.status_revised_desc')
    case 'wf_in_progress': return t('main.doc_info_panel.status_wf_in_progress_desc')
    case 'wf_done':        return t('main.doc_info_panel.status_wf_done_desc')
    default:               return ''
  }
})

const currentTypeCode = computed(() => props.typeCode || 'R')
const isQDoc = computed(() => props.typeCode === 'Q')
const isQDone = computed(() => props.qStatus === 'done')
const canShowRejectSection = computed(() => !['R', 'B', 'Q', 'M'].includes(props.typeCode ?? ''))

// ── AI review feedback (variant C: latest item plus "view full history") ──
const canShowReviewSection = computed(() => !['R', 'B', 'Q', 'M'].includes(props.typeCode ?? ''))
const priorReviewCount = computed(() => Math.max(0, (props.aiReviewHistory?.length ?? 0) - 1))

// Accordion: collapse the findings list under the verdict badge so the side panel
// stays scannable. Default to collapsed (count visible on the badge); reset to
// collapsed whenever the latest review changes.
const aiFindings = computed<AiReviewFinding[]>(() => props.aiReview?.findings ?? [])
const findingsExpanded = ref(false)
// The comment folds independently of the findings (same idiom as the rejection reason).
const commentExpanded = ref(false)
watch(
  () => props.aiReview?.id ?? props.aiReview?.reviewed_at ?? null,
  () => { findingsExpanded.value = false; commentExpanded.value = false },
  { immediate: true },
)
function toggleFindings() {
  findingsExpanded.value = !findingsExpanded.value
}

function aiVerdictClass(verdict?: string | null): string {
  return verdict === 'pass' ? 'pass' : 'warn'
}
function aiVerdictLabel(r: AiReview): string {
  if (r.verdict === 'pass') return t('main.doc_info_panel.ai_verdict_pass')
  if (r.verdict === 'hold') return t('main.doc_info_panel.ai_verdict_hold')
  return t('main.doc_info_panel.ai_verdict_issues', { n: r.finding_count ?? 0 })
}
const isBehindWorkflowHead = computed(() =>
  !['R', 'B'].includes(props.typeCode ?? '') &&
  props.stepStates.find(s => s.code === currentTypeCode.value)?.visual === 'done'
)
const isRootDecided = computed(() =>
  ['R', 'B'].includes(props.typeCode ?? '') && (props.reviewStatus?.startsWith('wf_') ?? false)
)
const isCompletedDoc = computed(() =>
  (isQDoc.value && isQDone.value) ||
  isBehindWorkflowHead.value ||
  isRootDecided.value ||
  props.reviewStatus === 'approved' ||
  props.reviewStatus === 'wf_done'
)

const effectiveStatus = computed(() =>
  isCompletedDoc.value ? 'wf_done' : props.reviewStatus
)

const nextStep = computed(() =>
  props.nextStepIndex != null ? (props.stepStates[props.nextStepIndex] ?? null) : null
)

// ── group 0022 §3.1/§3.2: document-bound query/answer panel ──────────────────────
interface QaAnswer {
  body: string
  author_kind: string
}
interface QaItem {
  id: number
  seq: number
  title: string | null
  body: string
  asker_kind: string
  answer_count?: number
  answers?: QaAnswer[]
}

const qaItems = ref<QaItem[]>([])
const qaLoading = ref(false)
const qaError = ref('')
const qaBusy = ref(false)
const expandedItemId = ref<number | null>(null)
const newQOpen = ref(false)
const newQTitle = ref('')
const newQBody = ref('')
const answerOpenId = ref<number | null>(null)
const answerBody = ref('')

// R0001 (AC3): full Q&A modal (show-all), same dialog form as ReviewHistoryDialog.
const qaHistoryVisible = ref(false)

function itemAnswered(item: QaItem): boolean {
  return (item.answer_count ?? item.answers?.length ?? 0) > 0
}

function toggleItem(id: number) {
  expandedItemId.value = expandedItemId.value === id ? null : id
  answerOpenId.value = null
}
function toggleNewQ() {
  newQOpen.value = !newQOpen.value
  if (!newQOpen.value) { newQTitle.value = ''; newQBody.value = '' }
}
function openAnswer(id: number) {
  answerOpenId.value = id
  answerBody.value = ''
}

async function fetchQa() {
  if (!props.docId) return
  qaLoading.value = true
  qaError.value = ''
  try {
    const res = await getRequest<any>(`/api/v1/q/${encodeURIComponent(props.docId)}`)
    qaItems.value = (res.data as any)?.qa?.items ?? []
  } catch (e: any) {
    qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
  } finally {
    qaLoading.value = false
  }
}

async function submitNewQ() {
  if (!newQBody.value.trim() || qaBusy.value) return
  qaBusy.value = true
  try {
    await postRequest(`/api/v1/q/${encodeURIComponent(props.docId)}/questions`, {
      asker_kind: 'human',
      questions: [{ title: newQTitle.value.trim() || null, body: newQBody.value.trim() }],
    })
    newQTitle.value = ''; newQBody.value = ''; newQOpen.value = false
    await fetchQa()
  } catch (e: any) {
    qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
  } finally {
    qaBusy.value = false
  }
}

async function submitAnswer(itemId: number) {
  if (!answerBody.value.trim() || qaBusy.value) return
  qaBusy.value = true
  try {
    await postRequest(`/api/v1/q/${encodeURIComponent(props.docId)}/items/${itemId}/answers`, {
      body: answerBody.value.trim(),
      author_kind: 'human',
    })
    answerOpenId.value = null; answerBody.value = ''
    await fetchQa()
  } catch (e: any) {
    qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
  } finally {
    qaBusy.value = false
  }
}

// [Request AI answer] — hands the item to an AI worker; the answer lands later as
// author_kind='ai' (D0005 §3.2). Dispatch wiring is server-side; here we mark it requested.
async function requestAiAnswer(itemId: number) {
  if (qaBusy.value) return
  qaBusy.value = true
  try {
    await postRequest(`/api/v1/q/${encodeURIComponent(props.docId)}/items/${itemId}/answers/ai-request`, {})
    await fetchQa()
  } catch (e: any) {
    qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
  } finally {
    qaBusy.value = false
  }
}

watch(
  () => props.docId,
  () => { expandedItemId.value = null; newQOpen.value = false; answerOpenId.value = null; qaHistoryVisible.value = false; fetchQa() },
  { immediate: true },
)

// Live refresh: a worker (or another user) registering a Q on the doc on screen lands
// server-side and fires SSE qna_q_registered, but this panel otherwise only refetches on
// doc switch / mount — so the new Q was invisible until F5 (0059 B0001). Refetch when the
// SSE-bridged window event targets this document. Idempotent GET, so an unrelated event
// for another doc is filtered out by doc_id.
function _onQRegistered(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string } | undefined
  if (detail?.doc_id && detail.doc_id === props.docId) fetchQa()
}
onMounted(() => window.addEventListener('fg:q_registered', _onQRegistered))
onBeforeUnmount(() => window.removeEventListener('fg:q_registered', _onQRegistered))
</script>

<style scoped>
.dip-badge-clickable {
  cursor: pointer;
}
.dip-badge-clickable:hover {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .25);
}
.dip-step-clickable {
  cursor: pointer;
}
.dip-step-clickable:hover {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .25);
}
.dip-step-disabled {
  cursor: default;
  opacity: .72;
}
/* group 0022 §3.1: Q&A panel (reuses the rejection-box idiom) */
.dip-section-head { display: flex; align-items: center; justify-content: space-between; }
.dip-qa-add { color: var(--primary); }
.dip-qa-hint { font-size: .72rem; color: #6b7280; padding: 4px 0; }
.dip-qa-error { font-size: .72rem; color: var(--danger); padding: 4px 0; }
.dip-qa-form {
  display: flex; flex-direction: column; gap: 6px;
  margin: 6px 0; padding: 8px; background: #f8fafc;
  border: 1px solid var(--border); border-radius: 6px;
}
.dip-qa-input, .dip-qa-textarea {
  width: 100%; box-sizing: border-box; font-size: .78rem;
  padding: 5px 7px; border: 1px solid var(--border); border-radius: 4px;
  font-family: inherit;
}
.dip-qa-textarea { resize: vertical; }
.dip-qa-form-actions { display: flex; justify-content: flex-end; gap: 6px; }
.dip-qa-item {
  border: 1px solid var(--border); border-radius: 6px;
  margin-bottom: 6px; overflow: hidden; background: #fff;
}
.dip-qa-item-head {
  display: flex; align-items: center; gap: 7px; width: 100%;
  padding: 7px 9px; background: none; border: none; cursor: pointer; text-align: left;
}
.dip-qa-item-head:hover { background: #f8fafc; }
.dip-qa-item-title { flex: 1; font-size: .76rem; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dip-qa-chevron { font-size: .55rem; color: #9aa3af; transition: transform .18s ease; }
.dip-qa-item.open .dip-qa-chevron { transform: rotate(180deg); }
.dip-qa-item-body { padding: 4px 10px 10px; }
.dip-qa-meta { font-size: .62rem; color: #59606a; margin-bottom: 5px; }
/* R0001 (rev1): question / answer bodies are shown directly when the Q item is
   expanded (no nested fold toggle), so reading a query needs a single disclosure.
   A labelled header row sits over a height-capped, 14px-scrollbar body — long text
   scrolls inside the box instead of stretching the side panel. Only the accent/scroll
   tone differs: neutral grey for the question, green for the answer. */
.dip-qa-fold {
  margin-top: 4px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-left: 3px solid #94a3b8;
  border-radius: 6px;
  overflow: hidden;
}
.dip-qa-fold--answer { border-left-color: #22c55e; }
.dip-qa-fold-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
}
.dip-qa-fold-label {
  font-size: .64rem;
  font-weight: 700;
  color: #475569;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-qa-fold--answer .dip-qa-fold-label { color: #15803d; }
.dip-qa-answer-icon { color: #15803d; }
.dip-qa-fold-body {
  display: block;
  max-height: 8rem;
  overflow-y: auto;
  overflow-wrap: anywhere;
  padding: 0 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
  scrollbar-width: auto;
  scrollbar-color: #94a3b8 #e2e8f0;
}
.dip-qa-fold--answer .dip-qa-fold-body { scrollbar-color: #22c55e #dcfce7; }
@supports selector(::-webkit-scrollbar) {
  .dip-qa-fold-body::-webkit-scrollbar { width: 14px; }
  .dip-qa-fold-body::-webkit-scrollbar-track { border-radius: 999px; background: #e2e8f0; }
  .dip-qa-fold-body::-webkit-scrollbar-thumb {
    border: 3px solid #e2e8f0;
    border-radius: 999px;
    background: #94a3b8;
  }
  .dip-qa-fold-body::-webkit-scrollbar-thumb:hover { background: #64748b; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-track { background: #dcfce7; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-thumb { border-color: #dcfce7; background: #22c55e; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-thumb:hover { background: #16a34a; }
}
.dip-qa-actions { display: flex; gap: 6px; margin-top: 8px; }
.dip-wf-step.current.dip-wf-completed {
  background: #fff;
  border-color: #d1d5db;
  color: #111827;
  font-weight: 400;
}
.dip-reject-history-label {
  font-size: .68rem;
  color: #6b7280;
  margin-left: 6px;
  font-weight: 400;
}
/* AI review feedback section */
.dip-ai-entry-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.dip-ai-meta {
  font-size: .6rem;
  color: #59606a;
}
.dip-ai-verdict {
  margin-left: auto;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: .62rem;
  font-weight: 700;
}
.dip-ai-verdict.warn { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
.dip-ai-verdict.pass { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
.dip-ai-verdict--toggle {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: inherit;
  line-height: 1.2;
}
.dip-ai-verdict--toggle:hover { filter: brightness(0.97); }
.dip-ai-chevron {
  font-size: .55rem;
  transition: transform .15s;
}
.dip-ai-chevron.open { transform: rotate(180deg); }
.dip-ai-findings {
  list-style: decimal;
  margin: 0 0 6px;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  /* R0043: cap a long findings list so it scrolls instead of stretching the panel,
     mirroring the rejection-reason idiom; amber 14px scrollbar to keep the tone. */
  max-height: 9rem;
  overflow-y: auto;
  scrollbar-width: auto;
  scrollbar-color: #f59e0b #fef3c7;
}
@supports selector(::-webkit-scrollbar) {
  .dip-ai-findings::-webkit-scrollbar { width: 14px; }
  .dip-ai-findings::-webkit-scrollbar-track { border-radius: 999px; background: #fef3c7; }
  .dip-ai-findings::-webkit-scrollbar-thumb {
    border: 3px solid #fef3c7;
    border-radius: 999px;
    background: #f59e0b;
  }
  .dip-ai-findings::-webkit-scrollbar-thumb:hover { background: #d97706; }
}
.dip-ai-finding {
  font-size: .76rem;
  color: #1e293b;
  line-height: 1.5;
}
.dip-ai-finding-locus {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: .68rem;
  font-weight: 700;
  color: #b45309;
  background: #fef3c7;
  border: 1px solid #fde68a;
  border-radius: 4px;
  padding: 0 5px;
  margin-right: 5px;
}
.dip-ai-finding-note {
  white-space: pre-wrap;
}
/* R0001 (rev1): the comment box now shares the rejection-reason / AI-response control
   idiom exactly — a clickable header row (label + chevron, no "expand/collapse" text button)
   sitting above a body that is clamped to two lines and expands to a height-capped,
   14px-scrollbar body. Only the accent colour stays amber (vs red/blue) to keep this
   box's verdict tone, so the fold behaves identically across all three sections. */
.dip-ai-comment {
  margin-top: 6px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-left: 3px solid #f59e0b;
  border-radius: 6px;
  overflow: hidden;
}
.dip-ai-comment-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
.dip-ai-comment-toggle:hover { background: #fffdf5; }
.dip-ai-comment-label {
  font-size: .64rem;
  font-weight: 700;
  color: #b45309;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-ai-comment-chevron {
  margin-left: auto;
  color: #d08a2c;
  font-size: .6rem;
  transition: transform .18s ease;
}
.dip-ai-comment.open .dip-ai-comment-chevron { transform: rotate(180deg); }
.dip-ai-comment-body {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  max-height: 3.5em;
  overflow: hidden;
  overflow-wrap: anywhere;
  padding: 0 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
}
.dip-ai-comment.open .dip-ai-comment-body {
  display: block;
  max-height: 8rem;
  overflow-y: auto;
  -webkit-line-clamp: initial;
  scrollbar-width: auto;
  scrollbar-color: #f59e0b #fef3c7;
}
@supports selector(::-webkit-scrollbar) {
  .dip-ai-comment.open .dip-ai-comment-body::-webkit-scrollbar { width: 14px; }
  .dip-ai-comment.open .dip-ai-comment-body::-webkit-scrollbar-track { border-radius: 999px; background: #fef3c7; }
  .dip-ai-comment.open .dip-ai-comment-body::-webkit-scrollbar-thumb {
    border: 3px solid #fef3c7;
    border-radius: 999px;
    background: #f59e0b;
  }
  .dip-ai-comment.open .dip-ai-comment-body::-webkit-scrollbar-thumb:hover { background: #d97706; }
}
.dip-ai-history-link {
  margin-top: 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: .72rem;
  font-weight: 600;
  color: var(--primary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.dip-ai-history-link:hover { text-decoration: underline; }

/* P0005/T0006: the AI's response to a rejection — threaded as a reply directly
   under the rejection quote (a sibling, not nested inside the quote box).
   Collapsible like the quote: folded by default, the header toggles the body. */
.dip-ai-response {
  margin: 7px 0 0 12px; /* indent so it reads as a reply to the quote above it */
  background: #f0f7ff;
  border: 1px solid #cfe2ff;
  border-left: 3px solid var(--primary, #2563eb);
  border-radius: 6px;
  overflow: hidden;
}
.dip-ai-response-head {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
.dip-ai-response-head:hover { background: #e7f1ff; }
.dip-ai-response-label {
  font-size: .64rem;
  font-weight: 700;
  color: #1d4ed8;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-ai-response-thread { color: #93b4e6; }
.dip-ai-response-date {
  margin-left: auto;
  color: #6b86ad;
  font-size: .62rem;
  white-space: nowrap;
}
.dip-ai-response-chevron {
  color: #6b86ad;
  font-size: .6rem;
  transition: transform .18s ease;
}
.dip-ai-response.open .dip-ai-response-chevron { transform: rotate(180deg); }
/* Collapsed still shows a few lines (clamped) exactly like the rejection reason —
   NOT crushed to zero height. Opening lifts the clamp and height-caps the body
   with a scrollbar, so a long response scrolls instead of stretching the panel.
   This is the rejection quote's own idiom ("like the rejection — show only a few lines, scroll when expanded"). */
.dip-ai-response-body {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  max-height: 3.5em;
  overflow: hidden;
  overflow-wrap: anywhere;
  padding: 6px 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
}
.dip-ai-response.open .dip-ai-response-body {
  display: block;
  max-height: 8rem;
  overflow-y: auto;
  -webkit-line-clamp: initial;
  /* Reviewer #6: the scrollbar read as gray and didn't match — give it the box's
     own blue tone and bump it to 14px (was a washed-out #93b4e6 at 10px). */
  scrollbar-width: auto;
  scrollbar-color: #5b8fd6 #e7f1ff;
}
@supports selector(::-webkit-scrollbar) {
  .dip-ai-response.open .dip-ai-response-body::-webkit-scrollbar { width: 14px; }
  .dip-ai-response.open .dip-ai-response-body::-webkit-scrollbar-track { border-radius: 999px; background: #e7f1ff; }
  .dip-ai-response.open .dip-ai-response-body::-webkit-scrollbar-thumb {
    border: 3px solid #e7f1ff;
    border-radius: 999px;
    background: #5b8fd6;
  }
  .dip-ai-response.open .dip-ai-response-body::-webkit-scrollbar-thumb:hover { background: #3b73c4; }
}
</style>
