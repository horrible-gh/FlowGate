<template>
  <teleport to="body">
    <div
      v-if="visible"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-qhd" role="dialog" aria-modal="true" aria-labelledby="qrh-title">
        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="qrh-title">
            <AppIcon name="chat-slash" style="color:var(--primary, #2563eb); margin-right:6px;" />{{ t('main.qa_review_history.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd qhd-body">
          <p class="qhd-desc">{{ t('main.qa_review_history.desc') }}</p>

          <!-- Filter for 0311 T0004 §3. Per rev3 rejection ("현재 적용되어있는 스타일을 전혀 사용하지
               않는다"), no new chip style was invented — this reuses the tab idiom the app
               already has (.tab-nav / .tab-nav-item — client/shared/app.css, same as
               ProjectSettingsView). TR0005 rev6 rejection §3: the question filter was dropped —
               questions moved to QaHistoryDialog. -->
          <div class="tab-nav qrh-filters">
            <button
              v-for="f in FILTERS"
              :key="f"
              type="button"
              class="tab-nav-item"
              :class="{ active: activeFilter === f }"
              @click="activeFilter = f"
            >
              <AppIcon v-if="f !== 'all'" :name="FILTER_ICON[f]" />
              {{ t(`main.qa_review_history.filter_${f}`) }}
              <span class="qrh-count">{{ filterCounts[f] }}</span>
            </button>
          </div>

          <div v-if="filteredTimeline.length === 0" class="qhd-empty">{{ t('main.qa_review_history.empty') }}</div>

          <ul v-else class="qhd-list">
            <!-- Rejection · AI review — card markup copied as-is from the pre-merge
                 ReviewHistoryDialog. The rejection reason IS the card itself, the only box
                 (no double-boxing); the AI response is a sibling reply hung outside the card. -->
            <li
              v-for="entry in filteredTimeline"
              :key="entry.key"
              class="rhd-entry"
            >
              <div class="rhd-item" :class="`rhd-item--${entry.kind === 'reject' ? 'reject' : 'review'}`">
                <div class="rhd-item-head">
                  <span class="rhd-tag" :class="`rhd-tag--${entry.kind === 'reject' ? 'reject' : 'review'}`">
                    <AppIcon :name="entry.kind === 'reject' ? 'chat-slash' : 'robot'" />
                    {{ entry.kind === 'reject'
                        ? t('main.qa_review_history.filter_reject')
                        : t('main.qa_review_history.filter_ai_review') }}
                  </span>
                  <span v-if="entry.kind === 'ai_review'" class="rhd-verdict" :class="verdictClass(entry.review.verdict)">
                    {{ verdictLabel(entry.review) }}
                  </span>
                  <span v-if="entry.kind === 'reject'" class="rhd-who">
                    {{ rejectedByDisplay?.(entry.reject.rejected_by) || t('main.doc_info_panel.rejection_review_author') }}
                  </span>
                  <span v-if="entry.kind === 'ai_review' && entry.review.revision_no != null" class="rhd-rev">rev {{ entry.review.revision_no }}</span>
                  <span class="rhd-when">{{ formatWhen(entry.at) }}</span>
                  <!-- TR0005 rev6 rejection §2 ("AI 검수는 왜 접는거 없냐?"): the AI-review card
                       also gets the same fold control as a rejection — there is only
                       something to fold when there is a comment. -->
                  <button
                    v-if="entry.kind === 'reject' || (entry.kind === 'ai_review' && entry.review.comment)"
                    type="button"
                    class="rhd-fold"
                    :aria-expanded="!collapsed.reason[entry.key]"
                    @click="toggleFold('reason', entry.key)"
                  >
                    <AppIcon name="caret-down" class="rhd-fold-chevron" :class="{ open: !collapsed.reason[entry.key] }" />
                  </button>
                </div>
                <div v-if="entry.kind === 'ai_review'" class="rhd-body-text">
                  <div
                    v-if="entry.review.review_provider?.actual_provider_name || entry.review.review_provider?.actual_provider_id"
                    class="rhd-provider"
                    :class="{ 'rhd-provider--mismatch': providerMismatch(entry.review) }"
                  >
                    <span>{{ t('main.qa_review_history.actual_provider') }}: {{ actualProviderLabel(entry.review) }}</span>
                    <span v-if="providerMismatch(entry.review)">
                      {{ t('main.qa_review_history.requested_provider') }}: {{ entry.review.review_provider?.requested_provider_id }}
                    </span>
                  </div>
                  <p v-if="entry.review.comment" class="rhd-comment" :class="{ collapsed: collapsed.reason[entry.key] }">{{ entry.review.comment }}</p>
                  <ul v-if="entry.review.findings && entry.review.findings.length" class="rhd-findings">
                    <li v-for="(f, fi) in entry.review.findings" :key="fi">
                      <strong v-if="f.locus">{{ f.locus }}</strong><span v-if="f.locus"> — </span>{{ f.note }}
                    </li>
                  </ul>
                </div>
                <!-- Reviewer #8 (pre-existing): the reason folds/unfolds instead of scrolling. Starts open. -->
                <p v-else class="rhd-reject-reason" :class="{ collapsed: collapsed.reason[entry.key] }">{{ entry.reject.reason }}</p>
              </div>
              <!-- Reviewer #8 (pre-existing): the AI response sits outside the rejection box — a reply hung as a sibling of the card. -->
              <div
                v-if="entry.kind === 'reject' && entry.reject.ai_response"
                class="rhd-ai-response"
                :class="{ open: !collapsed.response[entry.key] }"
              >
                <button
                  type="button"
                  class="rhd-ai-response-head"
                  :aria-expanded="!collapsed.response[entry.key]"
                  @click="toggleFold('response', entry.key)"
                >
                  <span class="rhd-ai-response-label">
                    <AppIcon name="arrow-bend-up-left" class="rhd-ai-thread" />
                    <AppIcon name="robot" /> {{ t('main.qa_review_history.ai_response') }}
                  </span>
                  <span v-if="entry.reject.responded_at" class="rhd-ai-response-date">{{ formatWhen(entry.reject.responded_at) }}</span>
                  <AppIcon name="caret-down" class="rhd-ai-chevron" />
                </button>
                <p class="rhd-ai-response-text">{{ entry.reject.ai_response }}</p>
              </div>
            </li>
          </ul>
        </div>

        <!-- Footer -->
        <div class="modal-ft qhd-footer">
          <button type="button" class="btn btn-outline btn-sm" @click="onClose">{{ t('common.close') }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AiReview } from '../types/aiReview'
import type { RejectionHistoryItem } from '../composables/useFlowGateToken'
import AppIcon from '@shared/AppIcon.vue'

// 0311 T0004 — the review/rejection side of the QaHistoryDialog + ReviewHistoryDialog
// merged full-view. TR0005 rev6 rejection §3 ("질의는 빼라"): questions went back to
// QaHistoryDialog, and this dialog became dedicated to AI review (ai_review) + rejection
// (reject). The principle set by the rev3 rejection ("현재
// 적용되어있는 스타일을 전혀 사용하지 않는다") still holds — rejection/AI-review
// entries are the ReviewHistoryDialog's .rhd-item card (+ the AI response hung as a sibling), as-is.
const props = withDefaults(defineProps<{
  visible: boolean
  reviews?: AiReview[]
  rejections?: RejectionHistoryItem[]
  // Lookup function that turns a rejection_history entry's rejected_by (UUID) into a
  // display name. Reuses the cache the panel already holds (useUsers lookup) as-is.
  rejectedByDisplay?: (userId: string | null | undefined) => string
}>(), {
  reviews: () => [],
  rejections: () => [],
  rejectedByDisplay: undefined,
})

const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const { t } = useI18n()

type RejectEntry = { kind: 'reject'; key: string; at: string; reject: RejectionHistoryItem }
type AiReviewEntry = { kind: 'ai_review'; key: string; at: string; review: AiReview }
type TimelineEntry = RejectEntry | AiReviewEntry

type FilterKey = 'all' | 'reject' | 'ai_review'
const FILTERS: FilterKey[] = ['all', 'reject', 'ai_review']
const FILTER_ICON: Record<FilterKey, string> = {
  all: 'chats',
  reject: 'chat-slash',
  ai_review: 'robot',
}
const activeFilter = ref<FilterKey>('all')

// Newest-first sort (same logic as the pre-merge ReviewHistoryDialog) — rejection uses
// rejected_at, AI review uses reviewed_at ?? created_at.
const reviewRejectTimeline = computed<TimelineEntry[]>(() => {
  const items: TimelineEntry[] = []
  for (const r of props.reviews ?? []) {
    items.push({ kind: 'ai_review', key: `ai_review:${r.id ?? r.reviewed_at ?? r.created_at}`, at: r.reviewed_at ?? r.created_at ?? '', review: r })
  }
  for (const j of props.rejections ?? []) {
    items.push({ kind: 'reject', key: `reject:${j.rejection_id ?? j.rejected_at}`, at: j.rejected_at ?? '', reject: j })
  }
  return items.sort((a, b) => String(b.at).localeCompare(String(a.at)))
})

const filterCounts = computed<Record<FilterKey, number>>(() => ({
  all: reviewRejectTimeline.value.length,
  reject: reviewRejectTimeline.value.filter((e) => e.kind === 'reject').length,
  ai_review: reviewRejectTimeline.value.filter((e) => e.kind === 'ai_review').length,
}))

const filteredTimeline = computed<TimelineEntry[]>(() => {
  switch (activeFilter.value) {
    case 'reject': return reviewRejectTimeline.value.filter((e) => e.kind === 'reject')
    case 'ai_review': return reviewRejectTimeline.value.filter((e) => e.kind === 'ai_review')
    default: return reviewRejectTimeline.value
  }
})

// Same rule as the pre-merge ReviewHistoryDialog: open is the default state, and the
// record holds only "collapsed" keys. Reopening the dialog or changing the list resets
// everything back to expanded. Keyed by item key rather than index, so switching the
// filter never collapses the wrong card.
const collapsed = reactive<Record<'reason' | 'response', Record<string, boolean>>>({
  reason: {},
  response: {},
})
function toggleFold(kind: 'reason' | 'response', key: string) {
  collapsed[kind][key] = !collapsed[kind][key]
}

function providerMismatch(review: AiReview): boolean {
  const provider = review.review_provider
  return Boolean(
    provider?.requested_provider_id
    && provider?.actual_provider_id
    && provider.requested_provider_id !== provider.actual_provider_id,
  )
}
function actualProviderLabel(review: AiReview): string {
  return review.review_provider?.actual_provider_name
    || review.review_provider?.actual_provider_id
    || ''
}
function verdictClass(verdict?: string | null): string {
  return verdict === 'pass' ? 'pass' : 'warn'
}
function verdictLabel(r: AiReview): string {
  if (r.verdict === 'pass') return t('main.doc_info_panel.ai_verdict_pass')
  if (r.verdict === 'hold') return t('main.doc_info_panel.ai_verdict_hold')
  return t('main.doc_info_panel.ai_verdict_issues', { n: r.finding_count ?? 0 })
}
function formatWhen(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const y = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${y}-${mm}-${dd} ${hh}:${mi}`
  } catch {
    return iso
  }
}

function onClose() {
  emit('update:visible', false)
}
</script>

<style scoped>
/* This dialog's styles were carried over as-is from the pre-merge ReviewHistoryDialog.
   The only newly written rules are the filter row's count badge (.qrh-count) and the
   AI-review comment's fold clamp (.rhd-comment.collapsed, TR0005 rev6 rejection §2) —
   the filter row itself is the global app.css .tab-nav / .tab-nav-item. */
.modal-qhd { width: 560px; max-width: 92vw; max-height: 80vh; display: flex; flex-direction: column; }
.qhd-body { overflow-y: auto; }
.qhd-desc { font-size: .78rem; color: var(--text-m); margin-bottom: 12px; }
.qhd-empty { padding: 24px; text-align: center; color: var(--text-m); font-size: .85rem; }
.qhd-list { display: flex; flex-direction: column; gap: 12px; }

/* Filter row: uses the global .tab-nav as-is, just appending a faded count. */
.qrh-filters { margin-bottom: 12px; }
.qrh-count { opacity: .6; font-variant-numeric: tabular-nums; }
.qhd-footer { display: flex; justify-content: flex-end; }

/* ── Rejection · AI review entries: card copied as-is from the pre-merge ReviewHistoryDialog ── */
.rhd-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.rhd-item--review { border-left: 3px solid #f59e0b; }
.rhd-item--reject { border-left: 3px solid var(--danger, #dc2626); background: #fef2f2; }
.rhd-item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.rhd-tag { display: inline-flex; align-items: center; gap: 5px; font-size: .68rem; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
.rhd-tag--review { background: #fff7d6; color: #6f4e00; border: 1px solid #f6d98b; }
.rhd-tag--reject { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
.rhd-verdict { font-size: .62rem; font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.rhd-verdict.warn { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
.rhd-verdict.pass { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
/* Who rejected it — puts the same information the panel's .dip-reject-quote-author used
   to show onto this card's header row (the pre-merge ReviewHistoryDialog never received
   rejected_by, so it could not show it). */
.rhd-who { font-size: .66rem; color: var(--text-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rhd-rev { font-size: .62rem; font-weight: 600; color: var(--text-s); background: var(--surface-h); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; }
.rhd-when { margin-left: auto; font-size: .65rem; color: var(--text-m); }
/* R0001 (full-content view): the review comment took its full height like the
   reason / AI response — no inner scrollbox. TR0005 rev6 rejection §2: it now folds the
   same way the rejection reason does — 2-line clamp when collapsed. */
.rhd-provider { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 0 0 7px; font-size: .7rem; color: var(--text-s); }
.rhd-provider--mismatch { color: var(--danger, #dc2626); font-weight: 700; }
.rhd-comment { font-size: .8rem; color: var(--text); white-space: pre-wrap; line-height: 1.55; margin: 0; overflow-wrap: anywhere; }
.rhd-comment.collapsed { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; max-height: 3.2em; overflow: hidden; }
.rhd-findings { list-style: disc; padding-left: 18px; margin: 6px 0 0; font-size: .76rem; color: var(--text-s); }
.rhd-findings li { margin: 2px 0; }
/* Bare reason text inside the reject card — the card is the only rejection box
   (reviewer #7: no double box). Reviewer #8: no scroll — the unfolded reason takes
   its full height; folded keeps a 2-line preview. */
.rhd-reject-reason { font-size: .8rem; color: #991b1b; white-space: pre-wrap; margin: 0; overflow-wrap: anywhere; }
.rhd-reject-reason.collapsed { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; max-height: 3.2em; overflow: hidden; }
.rhd-fold { display: inline-flex; align-items: center; background: none; border: none; cursor: pointer; padding: 2px 4px; color: #991b1b; }
.rhd-fold-chevron { font-size: .6rem; transition: transform .18s ease; }
.rhd-fold-chevron.open { transform: rotate(180deg); }
/* AI response: a threaded reply BELOW the rejection box (outside it, sibling of
   the card). Same fold idiom as the reason: folded = 2-line preview, open = full
   height, no inner scroll. */
.rhd-ai-response { margin: 8px 0 0 14px; background: #f0f7ff; border: 1px solid #cfe2ff; border-left: 3px solid var(--primary, #2563eb); border-radius: 6px; overflow: hidden; }
.rhd-ai-response-head { display: flex; width: 100%; align-items: center; gap: 6px; padding: 6px 10px; background: none; border: none; text-align: left; cursor: pointer; }
.rhd-ai-response-head:hover { background: #e7f1ff; }
/* R0001: margin-right:auto makes the label absorb all the slack, pinning the
   date + chevron together at the right edge. */
.rhd-ai-response-label { font-size: .64rem; font-weight: 700; color: #1d4ed8; display: inline-flex; align-items: center; gap: 5px; margin-right: auto; }
.rhd-ai-thread { color: #93b4e6; }
.rhd-ai-response-date { font-size: .62rem; color: #6b86ad; white-space: nowrap; }
.rhd-ai-chevron { color: #6b86ad; font-size: .6rem; transition: transform .18s ease; }
.rhd-ai-response.open .rhd-ai-chevron { transform: rotate(180deg); }
.rhd-ai-response-text { margin: 0; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; max-height: 3.5em; overflow: hidden; padding: 6px 10px 8px; font-size: .78rem; color: #1e293b; white-space: pre-wrap; line-height: 1.65; overflow-wrap: anywhere; }
.rhd-ai-response.open .rhd-ai-response-text { display: block; max-height: none; overflow: visible; -webkit-line-clamp: initial; }

/* Reviewer #6: the dialog's scrollbars were the default gray ("the gray doesn't fit") —
   theme the single scroll surface to its own tone and make it 14px. */
.qhd-body { scrollbar-width: thin; scrollbar-color: #b8c4d6 #eef2f8; }
@supports selector(::-webkit-scrollbar) {
  .qhd-body { scrollbar-width: auto; scrollbar-color: auto; }
  .qhd-body::-webkit-scrollbar { width: 14px; }
  .qhd-body::-webkit-scrollbar-track { border-radius: 999px; background: #eef2f8; }
  .qhd-body::-webkit-scrollbar-thumb { border: 3px solid #eef2f8; border-radius: 999px; background: #b8c4d6; }
  .qhd-body::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
}
</style>
