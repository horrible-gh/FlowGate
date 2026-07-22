<template>
  <aside class="doc-info-panel">
    <!-- Collapsed rail (vertical label) -->
    <button class="doc-panel-rail" @click="$emit('toggle')" :title="t('main.doc_info_panel.expand')">
      <AppIcon name="info" />
      {{ t('main.doc_info_panel.title') }}
    </button>

    <!-- Expanded body -->
    <div class="doc-info-body">
      <!-- Section 1: document status -->
      <div class="dip-section" :class="{ collapsed: sectionCollapsed.status }">
        <div class="dip-section-head">
          <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.status" @click="toggleSection('status')">
            <AppIcon name="caret-down" class="dip-acc-caret" />
            <AppIcon name="radio-button" />
            {{ t('main.doc_info_panel.section_status') }}
          </button>
          <button class="dip-panel-close btn-icon" @click="$emit('toggle')" :title="t('main.doc_info_panel.collapse')">
            <AppIcon name="caret-right" />
          </button>
        </div>
        <div class="dip-sec-body">
          <div :class="['dip-status-badge', statusClass, (nextStep?.visual === 'highlight' || nextStep?.visual === 'current') ? 'dip-badge-clickable' : '']" @click="(nextStep?.visual === 'highlight' || nextStep?.visual === 'current') ? emit('next-action') : undefined">
            <AppIcon :name="statusIcon" />
            {{ statusLabel }}
          </div>
          <p class="dip-status-desc">{{ statusDesc }}</p>
        </div>
      </div>

      <!-- Section 2: Q&A (group 0126). The side panel keeps each query compact and only
           exposes the answer action per card; the full text/answer form opens in the
           dialog so long queries never stretch the panel. -->
      <div class="dip-section" :class="{ collapsed: sectionCollapsed.qa }">
        <div class="dip-qa-headline">
          <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.qa" @click="toggleSection('qa')">
            <AppIcon name="caret-down" class="dip-acc-caret" />
            <AppIcon name="chats" />
            {{ t('main.doc_info_panel.section_qa') }}
          </button>
          <div class="dip-qa-head-actions">
            <span v-if="qaUnansweredCount > 0" class="dip-qa-count">{{ t('main.doc_info_panel.qa_unanswered_count', { n: qaUnansweredCount }) }}</span>
            <button v-if="qaItems.length > 0" class="dip-qa-act dip-qa-fullview" type="button" @click="openQaFull(null, false)" :title="t('main.doc_info_panel.qa_view_full')">
              <AppIcon name="corners-out" />
              {{ t('main.doc_info_panel.qa_view_full') }}
            </button>
            <button class="dip-qa-act dip-qa-act--icon dip-qa-add" type="button" @click="toggleNewQ" :title="t('main.doc_info_panel.qa_add')">
              <AppIcon name="plus" />
            </button>
          </div>
        </div>

        <div class="dip-sec-body">
        <!-- new question inline form ([+ Query]) -->
        <div v-if="newQOpen" class="dip-qa-form">
          <input v-model="newQTitle" class="dip-qa-input" :placeholder="t('main.doc_info_panel.qa_title_ph')" />
          <textarea v-model="newQBody" class="dip-qa-textarea" rows="3" :placeholder="t('main.doc_info_panel.qa_body_ph')"></textarea>
          <!-- group 0243 R0001: optional option editor. Add none and the query is the
               pre-extension one. -->
          <div class="dip-qa-opt-edit">
            <div v-for="(_, idx) in newQOptions" :key="idx" class="dip-qa-opt-row">
              <input
                v-model="newQOptions[idx]"
                class="dip-qa-input"
                :placeholder="t('main.doc_info_panel.qa_option_ph', { n: idx + 1 })"
                :maxlength="200"
              />
              <button
                class="dip-qa-opt-del"
                type="button"
                :title="t('main.doc_info_panel.qa_option_remove')"
                @click="removeQOption(idx)"
              >
                <AppIcon name="x" />
              </button>
            </div>
            <button
              v-if="newQOptions.length < QA_MAX_OPTIONS"
              class="dip-qa-opt-add"
              type="button"
              @click="addQOption"
            >
              <AppIcon name="plus" /> {{ t('main.doc_info_panel.qa_option_add') }}
            </button>
          </div>
          <div class="dip-qa-form-actions">
            <button class="btn btn-sm btn-outline" type="button" @click="toggleNewQ">{{ t('common.cancel') }}</button>
            <button class="btn btn-sm btn-primary" type="button" :disabled="!newQBody.trim() || qaBusy" @click="submitNewQ">{{ t('main.doc_info_panel.qa_register') }}</button>
          </div>
        </div>

        <div v-if="qaLoading" class="dip-qa-hint">{{ t('common.loading') }}</div>
        <div v-else-if="qaError" class="dip-qa-error">{{ qaError }}</div>
        <template v-else>
          <div v-if="qaItems.length === 0" class="dip-reject-empty">
            <AppIcon name="question" />
            <span>{{ t('main.doc_info_panel.qa_empty') }}</span>
          </div>
          <div
            v-for="item in qaItems"
            :key="item.id"
            class="dip-qa-card"
            :class="{ 'answered-card': itemAnswered(item) }"
          >
            <strong class="dip-qa-card-title">Q{{ item.seq }} · {{ item.title || item.body }}</strong>
            <p class="dip-qa-card-body">{{ item.body }}</p>
            <!-- group 0243 R0001: the card previews the options; picking one happens in the
                 full view, which [답변] opens. -->
            <ul v-if="(item.options?.length ?? 0) > 0" class="dip-qa-opt-list">
              <li v-for="opt in item.options" :key="opt.id" class="dip-qa-opt">{{ opt.label }}</li>
            </ul>
            <div class="dip-qa-card-actions">
              <button class="mini-action primary" type="button" @click="openQaFull(item.id, true)">
                <AppIcon name="arrow-bend-up-left" />
                {{ t('main.doc_info_panel.qa_answer') }}
              </button>
            </div>
          </div>
        </template>
        </div>
      </div>

      <!-- Section 2.5: AI review feedback (latest review plus full history) -->
      <div v-if="canShowReviewSection" class="dip-section" :class="{ collapsed: sectionCollapsed.ai_review }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.ai_review" @click="toggleSection('ai_review')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="robot" />
          {{ t('main.doc_info_panel.section_ai_review') }}
        </button>
        <div class="dip-sec-body">
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
                <AppIcon name="caret-down" class="dip-ai-chevron" :class="{ open: findingsExpanded }" />
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
                  <AppIcon name="robot" /> {{ t('main.doc_info_panel.ai_comment_label') }}
                </span>
                <AppIcon name="caret-down" class="dip-ai-comment-chevron" />
              </button>
              <div class="dip-ai-comment-body">{{ aiReview.comment }}</div>
            </div>
          </div>
          <!-- R0001: a "show-all" entry is always offered when there is any review on
               record (not only when prior reviews exist), so the most common single-review
               case can still reach the full-content history view. -->
          <button v-if="(aiReviewHistory?.length ?? 0) > 0" class="dip-ai-history-link" type="button" @click="emit('open-review-history')">
            <AppIcon name="clock-counter-clockwise" />
            {{ priorReviewCount > 0
                ? t('main.doc_info_panel.ai_review_view_history_count', { n: priorReviewCount })
                : t('main.doc_info_panel.view_full') }}
          </button>
        </template>
        <template v-else>
          <div class="dip-reject-empty">
            <AppIcon name="chat-circle-dots" />
            <span>{{ t('main.doc_info_panel.ai_review_empty') }}</span>
            <span class="dip-reject-hint">{{ t('main.doc_info_panel.ai_review_hint') }}</span>
          </div>
        </template>
        </div>
      </div>

      <!-- Section 2.5: TR 작업범위 검증 (0299 D0004 §6).
           결과가 통과이고 사유가 없으면 접어 둔다 — 정상 제출에서 이 카드가 펼쳐져
           있으면 매 문서마다 읽을 것 없는 목록이 자리를 차지한다. 경고·거부일 때만
           펼쳐서 보여준다. -->
      <div v-if="trScope" class="dip-section" :class="{ collapsed: sectionCollapsed.tr_scope }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.tr_scope" @click="toggleSection('tr_scope')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="git-branch" />
          {{ t('main.doc_info_panel.section_tr_scope') }}
        </button>
        <div class="dip-sec-body">
          <div class="dip-trs-head">
            <span class="dip-trs-verdict" :class="`dip-trs-${trScope.verdict}`">
              {{ t(`main.doc_info_panel.tr_scope_verdict_${trScope.verdict}`) }}
            </span>
            <span v-if="trScope.stage" class="dip-trs-stage">
              {{ t(`main.doc_info_panel.tr_scope_stage_${trScope.stage}`) }}
            </span>
          </div>
          <p v-if="trScope.branch" class="dip-trs-assign">
            {{ t('main.doc_info_panel.tr_scope_branch') }}: <code>{{ trScope.branch }}</code>
          </p>

          <ul v-if="trScope.codes?.length" class="dip-trs-codes">
            <li v-for="code in trScope.codes" :key="code">
              <strong>{{ code }}</strong> — {{ t(`main.doc_info_panel.tr_scope_code_${code.replace('-', '_').toLowerCase()}`) }}
            </li>
          </ul>

          <!-- 어긋난 항목이 먼저다. 신고/감지 전체 목록은 그다음이고, 눈으로 대조할
               수 있게 같은 모양으로 나란히 둔다. -->
          <div v-for="key in trScopeDiffKeys" :key="key">
            <template v-if="trScope[key]?.count">
              <p class="dip-trs-list-label dip-trs-mismatch">
                {{ t(`main.doc_info_panel.tr_scope_${key}`) }} ({{ trScope[key].count }})
              </p>
              <ul class="dip-trs-list dip-trs-mismatch">
                <li v-for="p in trScope[key].items" :key="p"><code>{{ p }}</code></li>
                <li v-if="trScope[key].count > trScope[key].items.length" class="dip-trs-more">
                  {{ t('main.doc_info_panel.tr_scope_more', { n: trScope[key].count - trScope[key].items.length }) }}
                </li>
              </ul>
            </template>
          </div>

          <div v-for="key in ['reported', 'detected']" :key="key">
            <p class="dip-trs-list-label">
              {{ t(`main.doc_info_panel.tr_scope_${key}`) }} ({{ trScope[key]?.count ?? 0 }})
            </p>
            <ul class="dip-trs-list">
              <li v-for="p in trScope[key]?.items ?? []" :key="p"><code>{{ p }}</code></li>
              <li v-if="!trScope[key]?.count" class="dip-trs-more">
                {{ t('main.doc_info_panel.tr_scope_empty') }}
              </li>
              <li v-else-if="trScope[key].count > trScope[key].items.length" class="dip-trs-more">
                {{ t('main.doc_info_panel.tr_scope_more', { n: trScope[key].count - trScope[key].items.length }) }}
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- Section 3: rejection reason -->
      <div v-if="canShowRejectSection" class="dip-section" :class="{ collapsed: sectionCollapsed.reject }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.reject" @click="toggleSection('reject')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="chat-slash" />
          {{ t('main.doc_info_panel.section_reject') }}
        </button>
        <div class="dip-sec-body">
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
                <AppIcon name="user-gear" />
                <strong>{{ rejectionAuthorLabel }}</strong>
              </span>
              <span v-if="latestRejection" class="dip-reject-date">{{ formatRejectionDate(latestRejection.rejected_at) }}</span>
              <AppIcon name="caret-down" class="dip-reject-chevron" />
            </button>
            <div class="dip-reject-quote-body">
              <span class="dip-reject-reason">{{ rejectionDisplayReason }}</span>

              <!-- R0001: as with the AI review, offer "show-all" whenever there is a
                   rejection on record so a single rejection still has a full-view entry. -->
              <button v-if="rejectionHistoryList.length > 0" class="dip-ai-history-link" type="button" @click="emit('open-review-history')">
                <AppIcon name="clock-counter-clockwise" />
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
                <AppIcon name="arrow-bend-up-left" class="dip-ai-response-thread" />
                <AppIcon name="robot" /> {{ t('main.doc_info_panel.ai_response_label') }}
              </span>
              <span v-if="latestRejection.responded_at" class="dip-ai-response-date">{{ formatRejectionDate(latestRejection.responded_at) }}</span>
              <AppIcon name="caret-down" class="dip-ai-response-chevron" />
            </button>
            <div class="dip-ai-response-body">{{ latestRejection.ai_response }}</div>
          </div>
        </template>
        <template v-else>
          <div class="dip-reject-empty">
            <AppIcon name="chat-circle-dots" />
            <span>{{ t('main.doc_info_panel.reject_empty') }}</span>
            <span class="dip-reject-hint">{{ t('main.doc_info_panel.reject_hint') }}</span>
          </div>
        </template>
        </div>
      </div>
    </div>

    <!-- R0001 (AC3): show-all modal for the full query/answer text. Owned here since
         the QA data already lives in this panel (the AI review history modal lives in
         MainPanel because its data lives in DocHeader). -->
    <QaHistoryDialog
      v-model:visible="qaHistoryVisible"
      :items="qaItems"
      :doc-id="props.docId"
      :busy="qaBusy"
      :focus-id="qaFocusId"
      :start-answer="qaStartAnswer"
      :submit-answer="submitAnswerCore"
      :request-ai-answer="requestAiAnswer"
      :ai-providers="aiProviderStore.providers"
      :selected-provider-id="aiProviderStore.selectedProviderId"
      :provider-loading="aiProviderStore.loading"
      :provider-errored="!!aiProviderStore.error"
      :select-provider="aiProviderStore.selectProvider"
      :copy-answer-mention="copyAnswerMention"
      :ai-run-item-id="aiRunItemId"
    />
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import QaHistoryDialog from './QaHistoryDialog.vue'
import { useQaAnswers } from '../composables/useQaAnswers'
import { useAiProviderStore } from '../stores/aiProvider'
import { useToast } from './common/useToast'
import { useMentionCopy } from '../composables/useMentionCopy'
import { ClipboardAbort, copyToClipboardDeferred } from '../utils/clipboard'
import type { StepState } from '../workflow/workflowViewState'
import type { AiReview, AiReviewFinding } from '../types/aiReview'
import type { RejectionHistoryItem } from '../composables/useFlowGateToken'
import type { TrScopeVerdict } from '../types/trScope'

const { t } = useI18n()
const aiProviderStore = useAiProviderStore()

const props = defineProps<{
  docId: string
  typeCode: string | null
  reviewStatus: string | null
  rejectReason: string | null
  rejectionHistory?: RejectionHistoryItem[]
  aiReview?: AiReview | null
  aiReviewHistory?: AiReview[]
  // TR 작업범위 검증 결과 (0299 D0004 §6). 서버가 documents.meta 에서 펼쳐 준다.
  trScope?: TrScopeVerdict | null
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

// ── R0001 (group 0126 / C안): section-level accordion ──────────────────────────
// Each info-panel section (status · Q&A · AI review · rejection) folds independently
// under its own title caret — the same caret idiom as the left file-tree. This is
// separate from the whole-panel collapse (the `dip-panel-close` chevron / `toggle`
// emit) so the two controls don't fight. Sections start expanded.
type SectionKey = 'status' | 'qa' | 'ai_review' | 'reject' | 'tr_scope'
const sectionCollapsed = reactive<Record<SectionKey, boolean>>({
  status: false,
  qa: false,
  ai_review: false,
  reject: false,
  // 통과이고 사유가 없으면 접어 둔다 (D0004 §6). 아래 watch 가 판정을 보고 연다.
  tr_scope: true,
})

// 어긋난 항목 — 신고/감지 전체보다 먼저, 눈에 띄게 보여준다 (D0004 §6).
const trScopeDiffKeys = ['out_of_scope', 'unconfirmed', 'unreported', 'format_errors'] as const

watch(
  () => props.trScope,
  (v) => {
    // 경고·거부이거나 사유 코드가 하나라도 있으면 펼친다. 관측 단계에서는 통과로
    // 기록되지만 사유는 남으므로, 그때도 펼쳐서 운영자가 단계를 올리기 전에 무엇이
    // 걸릴지 미리 볼 수 있게 한다.
    sectionCollapsed.tr_scope = !(v && (v.verdict !== 'pass' || (v.codes?.length ?? 0) > 0))
  },
  { immediate: true },
)
function toggleSection(key: SectionKey) {
  sectionCollapsed[key] = !sectionCollapsed[key]
}

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
  if (isQDoc.value) return isQDone.value ? 'check-circle' : 'clock'
  switch (effectiveStatus.value) {
    case 'pending_review': return 'hourglass-medium'
    case 'approved':       return 'check-circle'
    case 'rejected':       return 'x-circle'
    case 'revised':        return 'arrows-clockwise'
    case 'wf_in_progress': return 'play'
    case 'wf_done':        return 'check-circle'
    default:               return isRootWorkflowUndecided.value ? 'question' : 'hourglass-medium'
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

// ── group 0022 §3.1/§3.2 · group 0093 R0001: document-bound query/answer panel ──────
// The data + write actions live in the shared useQaAnswers composable so the
// "full view" dialog (QaHistoryDialog) can reuse the SAME qaItems ref and bound
// actions — answering there refetches once and both surfaces stay in sync.
const {
  qaItems,
  qaLoading,
  qaError,
  qaBusy,
  aiRunItemId,
  itemAnswered,
  fetchQa,
  submitQuestion,
  submitAnswer: submitAnswerCore,
  fetchAnswerMention,
  requestAiAnswer,
} = useQaAnswers(toRef(props, 'docId'))

const qaProjectId = computed(() => props.docId.split('.')[0] || '')
watch(qaProjectId, (projectId) => {
  if (projectId) void aiProviderStore.ensureLoaded(projectId)
}, { immediate: true })

const { showToast } = useToast()
const { recordMentionCopy } = useMentionCopy()

// [멘트 복사] for one query item (0248 B0001 rework). The mention is fetched INSIDE the
// deferred producer, not awaited before it: the token round-trip would otherwise outlive the
// click's transient activation and the clipboard write would silently reject (group 0133
// NR0003). ClipboardAbort keeps a failed fetch from also reporting a copy failure — the
// composable has already put the reason in qaError.
async function copyAnswerMention(itemId: number): Promise<boolean> {
  const ok = await copyToClipboardDeferred(async () => {
    const mention = await fetchAnswerMention(itemId)
    if (!mention) throw new ClipboardAbort()
    return mention
  })
  if (ok) {
    showToast(t('main.doc_info_panel.qa_answer_mention_copied'), 'success')
    // 'qa_answer' is the mention kind this exact hand-off was registered as (useMentionCopy);
    // the document-bound panel is simply the second producer of it.
    void recordMentionCopy(props.docId, 'qa_answer')
  } else if (qaError.value) {
    showToast(qaError.value, 'danger')
  } else {
    showToast(t('main.doc_info_panel.qa_answer_mention_copy_failed'), 'danger')
  }
  return ok
}

const newQOpen = ref(false)
const newQTitle = ref('')
const newQBody = ref('')
// group 0243 R0001: optional reference options on a human-written query. Leaving every row
// blank yields exactly the pre-extension query — blank rows are dropped on submit.
const newQOptions = ref<string[]>([])
const QA_MAX_OPTIONS = 10  // mirrors q_service.MAX_OPTIONS (L0008 §1)

function addQOption() {
  if (newQOptions.value.length < QA_MAX_OPTIONS) newQOptions.value.push('')
}
function removeQOption(idx: number) {
  newQOptions.value.splice(idx, 1)
}

// group 0126 / C안: unanswered count shown on the Q&A headline badge (matches the
// prototype's "미응답 N" pill — and the same count the header counter would show).
const qaUnansweredCount = computed(() => qaItems.value.filter((it) => !itemAnswered(it)).length)

// group 0126 / C안 + T0013: full Q&A modal (show-all / 전체보기). The headline
// [전체보기] opens it unfocused; each card's [답변] opens it focused on that query
// with the answer form started (cards expose only [답변] per T0013).
const qaHistoryVisible = ref(false)
const qaFocusId = ref<number | null>(null)
const qaStartAnswer = ref(false)
function openQaFull(focusId: number | null = null, startAnswer = false) {
  qaFocusId.value = focusId
  qaStartAnswer.value = startAnswer
  qaHistoryVisible.value = true
}

function toggleNewQ() {
  newQOpen.value = !newQOpen.value
  if (!newQOpen.value) { newQTitle.value = ''; newQBody.value = ''; newQOptions.value = [] }
}

async function submitNewQ() {
  if (await submitQuestion(newQTitle.value, newQBody.value, newQOptions.value)) {
    newQTitle.value = ''; newQBody.value = ''; newQOptions.value = []; newQOpen.value = false
  }
}

watch(
  () => props.docId,
  () => { newQOpen.value = false; qaHistoryVisible.value = false; fetchQa() },
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
/* R0001 (group 0126 / C안): section-level accordion. The section title becomes a
   caret toggle — a button reset back to the .dip-section-title look (the typography
   is restated here because a bare `font: inherit` would lose the .63rem/upper-case
   title style under scoped-style specificity). The body wrapper hides and the caret
   rotates -90° when the section is collapsed, mirroring the left file-tree caret. The
   whole-panel close chevron stays a separate control. */
.dip-sec-toggle {
  appearance: none;
  background: none;
  border: none;
  width: 100%;
  padding: 0;
  font-family: inherit;
  font-size: .63rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--text-m);
  text-align: left;
  cursor: pointer;
}
.dip-sec-toggle:hover { color: var(--text-s); }
.dip-acc-caret {
  font-size: .6rem;
  color: #9aa3af;
  transition: transform .18s ease;
}
.dip-section.collapsed .dip-acc-caret { transform: rotate(-90deg); }
.dip-section.collapsed .dip-sec-body { display: none; }

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

/* group 0243 R0001: option editor in the new-query form + option preview on a card. The
   preview is inert text — no recommendation accent, nothing preselected (0022 rule). */
.dip-qa-opt-edit { display: flex; flex-direction: column; gap: 4px; }
.dip-qa-opt-row { display: flex; align-items: center; gap: 4px; }
.dip-qa-opt-row .dip-qa-input { flex: 1; }
.dip-qa-opt-del {
  border: none; background: none; cursor: pointer; color: #9ca3af;
  padding: 2px 4px; line-height: 1; font-size: .7rem;
}
.dip-qa-opt-del:hover { color: var(--danger); }
.dip-qa-opt-add {
  align-self: flex-start; border: none; background: none; cursor: pointer;
  color: var(--primary); font-size: .7rem; padding: 2px 0;
  display: inline-flex; align-items: center; gap: 3px;
}
.dip-qa-opt-list { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.dip-qa-opt {
  font-size: .7rem; color: #6b7280;
  padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; background: #f8fafc;
}

/* group 0126: prototype card layout for the Q&A section. The headline carries a
   caret-title toggle plus an unanswered-count pill and a compact [+ add] button; the
   body lists amber cards (title + 2-line preview + Answer only). */
.dip-qa-headline {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-bottom: 10px;
}
.dip-qa-headline .dip-sec-toggle { width: auto; flex: 1; min-width: 0; }
.dip-qa-head-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 6px; }
.dip-qa-count {
  padding: 2px 7px; border: 1px solid #fde68a; border-radius: 999px;
  color: #92400e; background: #fef3c7; font-size: .66rem; font-weight: 800;
  white-space: nowrap;
}
/* group 0126 T0013: the headline keeps BOTH [전체보기] and [+] (the instruction asked
   to align their styling, not remove either). They share one amber button family so
   they read as a matched pair next to the unanswered-count pill — same 24px height,
   border, radius and palette; [전체보기] carries an icon + label, [+] is the square
   icon-only variant. */
.dip-qa-act {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  height: 24px; padding: 0 9px;
  border: 1px solid #fcd34d; border-radius: var(--r, 6px);
  color: #92400e; background: #fff7ed;
  font-family: inherit; font-size: .66rem; font-weight: 700;
  white-space: nowrap; cursor: pointer;
}
.dip-qa-act:hover { background: #fef3c7; }
.dip-qa-act--icon { width: 24px; padding: 0; font-size: .74rem; }
.dip-qa-card {
  padding: 10px 11px; border: 1px solid #fde68a; border-left: 3px solid #f59e0b;
  border-radius: var(--r, 6px); background: #fffbeb;
}
.dip-qa-card + .dip-qa-card { margin-top: 8px; }
.dip-qa-card-title {
  display: block; overflow: hidden; margin-bottom: 4px;
  color: #78350f; font-size: .76rem; text-overflow: ellipsis; white-space: nowrap;
}
.dip-qa-card-body {
  display: -webkit-box; overflow: hidden; margin: 0;
  color: #6b4f1d; font-size: .72rem; line-height: 1.45;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2;
}
.dip-qa-card-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
.dip-qa-card.answered-card { opacity: .6; border-left-color: #86efac; }
.mini-action {
  padding: 3px 7px; border: 1px solid #fcd34d; border-radius: var(--r, 6px);
  color: #92400e; background: #fff7ed; font-size: .66rem; font-weight: 700; cursor: pointer;
}
.mini-action:hover { filter: brightness(.97); }
.mini-action.primary { color: #fff; border-color: #d97706; background: #d97706; }
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

/* TR 작업범위 검증 카드 (0299 D0004 §6). 신고/감지 두 목록을 같은 모양으로 두어
   눈으로 대조할 수 있게 하고, 어긋난 항목만 색으로 구분한다. */
.dip-trs-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dip-trs-verdict {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: .72rem;
  font-weight: 700;
}
.dip-trs-pass { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }
.dip-trs-warn { background: var(--warning-bg, #fef3c7); color: var(--warning, #b45309); }
.dip-trs-reject { background: var(--danger-bg, #fee2e2); color: var(--danger, #b91c1c); }
.dip-trs-skipped { background: var(--muted-bg, #f1f5f9); color: var(--muted, #64748b); }
.dip-trs-stage { font-size: .7rem; color: var(--muted, #64748b); }
.dip-trs-assign { margin: 6px 0 0; font-size: .72rem; color: var(--muted, #64748b); }
.dip-trs-codes { margin: 6px 0 0; padding-left: 16px; font-size: .72rem; line-height: 1.5; }
.dip-trs-list-label { margin: 8px 0 2px; font-size: .72rem; font-weight: 600; }
.dip-trs-list {
  margin: 0;
  padding-left: 16px;
  font-size: .7rem;
  line-height: 1.5;
  max-height: 180px;   /* 긴 목록이 패널 전체를 늘리지 않게 자체 스크롤 */
  overflow-y: auto;
  word-break: break-all;
}
.dip-trs-mismatch { color: var(--danger, #b91c1c); }
.dip-trs-more { color: var(--muted, #64748b); list-style: none; margin-left: -16px; }

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
