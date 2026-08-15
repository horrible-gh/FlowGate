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

          <!-- 0311 T0004 §3 의 필터. rev3 반려("현재 적용되어있는 스타일을 전혀 사용하지
               않는다")에 따라 새 칩 스타일을 만들지 않고, 이 앱이 이미 쓰고 있는 탭 관용구
               (.tab-nav / .tab-nav-item — client/shared/app.css, ProjectSettingsView 와
               같은 것)를 그대로 쓴다. TR0005 rev6 반려 §3: 질의 필터는 뺐다 — 질의는
               QaHistoryDialog 로 옮겼다. -->
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
            <!-- 반려 · AI 검수 — 기존 ReviewHistoryDialog 의 카드 마크업 그대로.
                 반려 사유는 카드 자체가 유일한 상자이고(이중박스 없음), AI 대응은 카드
                 바깥에 형제로 달리는 답글이다. -->
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
                  <!-- TR0005 rev6 반려 §2 ("AI 검수는 왜 접는거 없냐?"): AI 검수 카드도
                       반려와 같은 접힘 컨트롤을 갖는다 — 코멘트가 있을 때만 접을 것이
                       있다. -->
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
                  <p v-if="entry.review.comment" class="rhd-comment" :class="{ collapsed: collapsed.reason[entry.key] }">{{ entry.review.comment }}</p>
                  <ul v-if="entry.review.findings && entry.review.findings.length" class="rhd-findings">
                    <li v-for="(f, fi) in entry.review.findings" :key="fi">
                      <strong v-if="f.locus">{{ f.locus }}</strong><span v-if="f.locus"> — </span>{{ f.note }}
                    </li>
                  </ul>
                </div>
                <!-- Reviewer #8 (기존): 사유는 스크롤하지 않고 접힘/펼침한다. 열려서 시작. -->
                <p v-else class="rhd-reject-reason" :class="{ collapsed: collapsed.reason[entry.key] }">{{ entry.reject.reason }}</p>
              </div>
              <!-- Reviewer #8 (기존): AI 대응은 반려 상자 바깥 — 카드의 형제로 달린 답글. -->
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

// 0311 T0004 — QaHistoryDialog + ReviewHistoryDialog 통합 전체보기 중 검수·반려 쪽.
// TR0005 rev6 반려 §3 ("질의는 빼라"): 질의는 QaHistoryDialog 로 되돌아갔고, 이
// 다이얼로그는 AI 검수(ai_review)+반려(reject) 전용이 됐다. rev3 반려("현재
// 적용되어있는 스타일을 전혀 사용하지 않는다")가 세운 원칙은 그대로다 — 반려·AI 검수
// 항목은 ReviewHistoryDialog 의 .rhd-item 카드(+ 형제로 달리는 AI 대응) 그대로다.
const props = withDefaults(defineProps<{
  visible: boolean
  reviews?: AiReview[]
  rejections?: RejectionHistoryItem[]
  // rejection_history 항목의 rejected_by(UUID)를 표시용 이름으로 바꾸는 조회 함수.
  // 패널이 이미 갖고 있는 캐시(useUsers 조회)를 그대로 재사용한다.
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

// 최신순 정렬(기존 ReviewHistoryDialog 로직 그대로) — 반려는 rejected_at, AI검수는
// reviewed_at ?? created_at 을 쓴다.
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

// 기존 ReviewHistoryDialog 와 같은 규칙: 열려 있는 상태가 기본이고 레코드는 "접힌" 키만
// 담는다. 다이얼로그를 다시 열거나 목록이 바뀌면 전부 펼침으로 되돌아간다. 인덱스가
// 아니라 항목 키로 잡아 필터를 바꿔도 엉뚱한 카드가 접히지 않는다.
const collapsed = reactive<Record<'reason' | 'response', Record<string, boolean>>>({
  reason: {},
  response: {},
})
function toggleFold(kind: 'reason' | 'response', key: string) {
  collapsed[kind][key] = !collapsed[kind][key]
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
/* 이 다이얼로그의 스타일은 통합 전 ReviewHistoryDialog 의 것을 그대로 옮겨온 것이다.
   새로 쓴 규칙은 필터 줄의 개수 배지(.qrh-count)와 AI 검수 코멘트의 접힘 클램프
   (.rhd-comment.collapsed, TR0005 rev6 반려 §2)뿐이고, 필터 줄 자체는 전역 app.css 의
   .tab-nav / .tab-nav-item 이다. */
.modal-qhd { width: 560px; max-width: 92vw; max-height: 80vh; display: flex; flex-direction: column; }
.qhd-body { overflow-y: auto; }
.qhd-desc { font-size: .78rem; color: var(--text-m); margin-bottom: 12px; }
.qhd-empty { padding: 24px; text-align: center; color: var(--text-m); font-size: .85rem; }
.qhd-list { display: flex; flex-direction: column; gap: 12px; }

/* 필터 줄: 전역 .tab-nav 그대로 쓰되, 개수만 흐리게 덧붙인다. */
.qrh-filters { margin-bottom: 12px; }
.qrh-count { opacity: .6; font-variant-numeric: tabular-nums; }
.qhd-footer { display: flex; justify-content: flex-end; }

/* ── 반려 · AI 검수 항목: 기존 ReviewHistoryDialog 의 카드 그대로 ── */
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
/* 반려한 사람 — 패널의 .dip-reject-quote-author 가 보여주던 것과 같은 정보를 이 카드의
   머리줄에 놓는다(통합 전 ReviewHistoryDialog 는 rejected_by 를 받지 못해 못 보여줬다). */
.rhd-who { font-size: .66rem; color: var(--text-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rhd-rev { font-size: .62rem; font-weight: 600; color: var(--text-s); background: var(--surface-h); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; }
.rhd-when { margin-left: auto; font-size: .65rem; color: var(--text-m); }
/* R0001 (full-content view): the review comment took its full height like the
   reason / AI response — no inner scrollbox. TR0005 rev6 반려 §2: it now folds the
   same way the rejection reason does — 2-line clamp when collapsed. */
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
