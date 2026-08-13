<template>
  <teleport to="body">
    <div
      v-if="visible"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-rhd" role="dialog" aria-modal="true" aria-labelledby="rhd-title">
        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="rhd-title">
            <AppIcon name="clock-counter-clockwise" style="color:var(--warning, #d97706); margin-right:6px;" />{{ t('main.review_history.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd rhd-body">
          <p class="rhd-desc">{{ t('main.review_history.desc') }}</p>

          <div v-if="timeline.length === 0" class="rhd-empty">{{ t('main.review_history.empty') }}</div>

          <ul v-else class="rhd-list">
            <li v-for="(item, idx) in timeline" :key="idx" class="rhd-entry">
              <div class="rhd-item" :class="`rhd-item--${item.kind}`">
                <div class="rhd-item-head">
                  <span class="rhd-tag" :class="`rhd-tag--${item.kind}`">
                    <AppIcon :name="item.kind === 'review' ? 'robot' : 'chat-slash'" />
                    {{ item.kind === 'review' ? t('main.review_history.review') : t('main.review_history.reject') }}
                  </span>
                  <span v-if="item.kind === 'review'" class="rhd-verdict" :class="verdictClass(item.review.verdict)">
                    {{ verdictLabel(item.review) }}
                  </span>
                  <span v-if="item.revisionNo != null" class="rhd-rev">rev {{ item.revisionNo }}</span>
                  <span class="rhd-when">{{ formatWhen(item.at) }}</span>
                  <button
                    v-if="item.kind === 'reject'"
                    type="button"
                    class="rhd-fold"
                    :aria-expanded="!rejectCollapsed[idx]"
                    @click="toggleReject(idx)"
                  >
                    <AppIcon name="caret-down" class="rhd-fold-chevron" :class="{ open: !rejectCollapsed[idx] }" />
                  </button>
                </div>
                <div v-if="item.kind === 'review'" class="rhd-body-text">
                  <p v-if="item.review.comment" class="rhd-comment">{{ item.review.comment }}</p>
                  <ul v-if="item.review.findings && item.review.findings.length" class="rhd-findings">
                    <li v-for="(f, fi) in item.review.findings" :key="fi">
                      <strong v-if="f.locus">{{ f.locus }}</strong><span v-if="f.locus"> — </span>{{ f.note }}
                    </li>
                  </ul>
                </div>
                <!-- Reviewer #8: the reason never scrolls — it folds/unfolds instead,
                     unfolded by default when the dialog opens. Folded keeps a 2-line preview. -->
                <p v-else class="rhd-reject-reason" :class="{ collapsed: rejectCollapsed[idx] }">{{ item.reject.reason }}</p>
              </div>
              <!-- Reviewer #8: the AI response lives OUTSIDE the rejection box — a threaded
                   reply below the card, paired by sharing this timeline entry. -->
              <div
                v-if="item.kind === 'reject' && item.reject.ai_response"
                class="rhd-ai-response"
                :class="{ open: !responseCollapsed[idx] }"
              >
                <button
                  type="button"
                  class="rhd-ai-response-head"
                  :aria-expanded="!responseCollapsed[idx]"
                  @click="toggleResponse(idx)"
                >
                  <span class="rhd-ai-response-label">
                    <AppIcon name="arrow-bend-up-left" class="rhd-ai-thread" />
                    <AppIcon name="robot" /> {{ t('main.review_history.ai_response') }}
                  </span>
                  <!-- R0001: the AI response carries its own timestamp here, mirroring
                       the rejection's .rhd-when and DocInfoPanel's response date. -->
                  <span v-if="item.reject.responded_at" class="rhd-ai-response-date">{{ formatWhen(item.reject.responded_at) }}</span>
                  <AppIcon name="caret-down" class="rhd-ai-chevron" />
                </button>
                <p class="rhd-ai-response-text">{{ item.reject.ai_response }}</p>
              </div>
            </li>
          </ul>
        </div>

        <!-- Footer -->
        <div class="modal-ft rhd-footer">
          <button type="button" class="btn btn-outline btn-sm" @click="onClose">{{ t('common.close') }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AiReview } from '../types/aiReview'
import AppIcon from '@shared/AppIcon.vue'

type Rejection = {
  rejection_id?: string
  reason: string
  rejected_at: string
  rejected_by: string | null
  // P0005/T0006: AI response recorded against this rejection (nullable).
  ai_response?: string | null
  responded_at?: string | null
}

const props = defineProps<{
  visible: boolean
  reviews?: AiReview[]
  rejections?: Rejection[]
}>()

const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const { t } = useI18n()

type TimelineItem =
  | { kind: 'review'; at: string; revisionNo: number | null; review: AiReview }
  | { kind: 'reject'; at: string; revisionNo: number | null; reject: Rejection }

// Merge reviews and rejections into one newest-first timeline. Rejections lack revision data, so sort them by time only.
const timeline = computed<TimelineItem[]>(() => {
  const items: TimelineItem[] = []
  for (const r of props.reviews ?? []) {
    items.push({ kind: 'review', at: r.reviewed_at ?? r.created_at ?? '', revisionNo: r.revision_no ?? null, review: r })
  }
  for (const j of props.rejections ?? []) {
    items.push({ kind: 'reject', at: j.rejected_at ?? '', revisionNo: null, reject: j })
  }
  return items.sort((a, b) => String(b.at).localeCompare(String(a.at)))
})

// Reviewer #8: everything is unfolded when the dialog opens ("unfolded by default when opened").
// The records hold the *folded* indices, so a missing key = unfolded; reopening
// the dialog or a list change clears them back to all-unfolded.
const rejectCollapsed = reactive<Record<number, boolean>>({})
const responseCollapsed = reactive<Record<number, boolean>>({})
function toggleReject(idx: number) {
  rejectCollapsed[idx] = !rejectCollapsed[idx]
}
function toggleResponse(idx: number) {
  responseCollapsed[idx] = !responseCollapsed[idx]
}
watch(
  () => [props.visible, timeline.value.length] as const,
  () => {
    for (const k of Object.keys(rejectCollapsed)) delete rejectCollapsed[Number(k)]
    for (const k of Object.keys(responseCollapsed)) delete responseCollapsed[Number(k)]
  },
)

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
.modal-rhd { width: 560px; max-width: 92vw; max-height: 80vh; display: flex; flex-direction: column; }
.rhd-body { overflow-y: auto; }
.rhd-desc { font-size: .78rem; color: var(--text-m); margin-bottom: 12px; }
.rhd-empty { padding: 24px; text-align: center; color: var(--text-m); font-size: .85rem; }
.rhd-list { display: flex; flex-direction: column; gap: 10px; }
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
.rhd-rev { font-size: .62rem; font-weight: 600; color: var(--text-s); background: var(--surface-h); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; }
.rhd-when { margin-left: auto; font-size: .65rem; color: var(--text-m); }
/* R0001 (full-content view): the review comment takes its full height like the
   reason / AI response — no inner scrollbox. The dialog body (.rhd-body, max-height
   80vh) is the single scroll surface, so a long comment scrolls the dialog rather
   than being clipped to 9rem. */
.rhd-comment { font-size: .8rem; color: var(--text); white-space: pre-wrap; line-height: 1.55; margin: 0; overflow-wrap: anywhere; }
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
   date + chevron together at the right edge. A single auto-margin anchored on the
   label keeps the chevron right-aligned whether or not the date renders — two
   competing auto-margins (e.g. on both date and chevron) would split the slack and
   strand the clock mid-row. */
.rhd-ai-response-label { font-size: .64rem; font-weight: 700; color: #1d4ed8; display: inline-flex; align-items: center; gap: 5px; margin-right: auto; }
.rhd-ai-thread { color: #93b4e6; }
.rhd-ai-response-date { font-size: .62rem; color: #6b86ad; white-space: nowrap; }
.rhd-ai-chevron { color: #6b86ad; font-size: .6rem; transition: transform .18s ease; }
.rhd-ai-response.open .rhd-ai-chevron { transform: rotate(180deg); }
.rhd-ai-response-text { margin: 0; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; max-height: 3.5em; overflow: hidden; padding: 6px 10px 8px; font-size: .78rem; color: #1e293b; white-space: pre-wrap; line-height: 1.65; overflow-wrap: anywhere; }
.rhd-ai-response.open .rhd-ai-response-text { display: block; max-height: none; overflow: visible; -webkit-line-clamp: initial; }
.rhd-footer { display: flex; justify-content: flex-end; }

/* Reviewer #6: the dialog's scrollbars were the default gray ("the gray doesn't fit") —
   theme the scroll area to its own tone and make it 14px ("14px thickness, thicker").
   Reviewer #8 removed the reason/response inner scrolls and R0001 removed the
   review-comment inner scroll, so the dialog body is now the only scroll surface. */
.rhd-body { scrollbar-width: thin; scrollbar-color: #b8c4d6 #eef2f8; }
@supports selector(::-webkit-scrollbar) {
  .rhd-body { scrollbar-width: auto; scrollbar-color: auto; }
  .rhd-body::-webkit-scrollbar { width: 14px; }
  .rhd-body::-webkit-scrollbar-track { border-radius: 999px; background: #eef2f8; }
  .rhd-body::-webkit-scrollbar-thumb { border: 3px solid #eef2f8; border-radius: 999px; background: #b8c4d6; }
  .rhd-body::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
}
</style>
