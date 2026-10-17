<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="560"
    :initial-height="480"
    :min-width="340"
    :min-height="220"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setDocumentHistoryWindowMinimized(win.docId, value)"
    @close="closeDocumentHistoryWindow(win.docId)"
  >
    <template #header>
      <span class="history-window-heading" data-test="history-window-heading">
        <span class="history-window-kind">{{ t(k('title')) }}</span>
        <span class="history-window-title" :title="title" data-test="history-window-title">{{ title }}</span>
      </span>
    </template>

    <div class="history-window" data-test="history-window" :data-doc="win.docId" :data-state="state" :data-filter="filter">
      <div class="history-filters" role="group" :aria-label="t(k('filter_label'))" data-test="history-filters">
        <Button
          v-for="item in DOCUMENT_HISTORY_FILTERS"
          :key="item"
          size="sm"
          :variant="filter === item ? 'primary' : 'secondary'"
          :aria-pressed="filter === item"
          data-test="history-filter"
          :data-filter="item"
          @click="filter = item"
        >{{ t(k(`filter_${item}`)) }} <span class="history-filter-count" data-test="history-filter-count">{{ counts[item] }}</span></Button>
      </div>

      <div v-if="state === 'loading'" class="history-note" data-test="history-loading">{{ t('gui.document_window.viewer.loading') }}</div>
      <div v-else-if="state === 'error'" class="history-note history-note--danger" data-test="history-error">
        <span>{{ t(`gui.document_window.error.${errorKey}`) }}</span>
        <IconButton :label="t('gui.document_window.viewer.retry')" :title="t('gui.document_window.viewer.retry')" variant="secondary" size="sm" data-test="history-retry" @click="reload">↻</IconButton>
      </div>
      <div v-else-if="!shown.length" class="history-note" data-test="history-empty">{{ t(k('empty')) }}</div>

      <ul v-else class="history-list" data-test="history-list">
        <li v-for="entry in shown" :key="entry.key" class="history-entry" data-test="history-entry" :data-kind="entry.kind" :data-key="entry.key">
          <!-- A rejection: the reason is the card; the AI response hangs under it (the v0.1 shape). -->
          <template v-if="entry.kind === 'reject'">
            <div class="history-card history-card--reject">
              <div class="history-head">
                <span class="history-tag history-tag--reject">{{ t(k('tag_reject')) }}</span>
                <span class="history-who" data-test="history-who">{{ rejectedBy(entry.rejection) }}</span>
                <span class="history-when" data-test="history-when">{{ documentHistoryWhen(entry.rejection.rejectedAt) }}</span>
                <button
                  type="button"
                  class="history-fold"
                  :aria-expanded="!folded.has(entry.key)"
                  :aria-label="t(k(folded.has(entry.key) ? 'unfold' : 'fold'))"
                  :title="t(k(folded.has(entry.key) ? 'unfold' : 'fold'))"
                  data-test="history-fold"
                  @click="toggle(entry.key)"
                >{{ folded.has(entry.key) ? '▸' : '▾' }}</button>
              </div>
              <p class="history-text history-reason" :class="{ 'is-folded': folded.has(entry.key) }" data-test="history-reason">{{ entry.rejection.reason }}</p>
            </div>
            <div v-if="entry.rejection.aiResponse" class="history-response" data-test="history-response">
              <div class="history-head">
                <span class="history-response-label">↳ {{ t(k('ai_response')) }} <span class="history-who">{{ aiName(entry.rejection.responseProviderName) }}</span></span>
                <span v-if="entry.rejection.respondedAt" class="history-when">{{ documentHistoryWhen(entry.rejection.respondedAt) }}</span>
                <button
                  type="button"
                  class="history-fold"
                  :aria-expanded="!folded.has(`${entry.key}:response`)"
                  :aria-label="t(k(folded.has(`${entry.key}:response`) ? 'unfold' : 'fold'))"
                  :title="t(k(folded.has(`${entry.key}:response`) ? 'unfold' : 'fold'))"
                  data-test="history-fold-response"
                  @click="toggle(`${entry.key}:response`)"
                >{{ folded.has(`${entry.key}:response`) ? '▸' : '▾' }}</button>
              </div>
              <p class="history-text" :class="{ 'is-folded': folded.has(`${entry.key}:response`) }" data-test="history-response-text">{{ entry.rejection.aiResponse }}</p>
            </div>
          </template>

          <!-- An AI review: verdict · provenance · rev · time; the reviewer, the comment, the findings. -->
          <div v-else class="history-card history-card--review">
            <div class="history-head">
              <span class="history-tag history-tag--review">{{ t(k('tag_ai_review')) }}</span>
              <span class="history-verdict" :class="`history-verdict--${documentHistoryVerdictKey(entry.review.verdict)}`" data-test="history-verdict">
                {{ t(k(`verdict.${documentHistoryVerdictKey(entry.review.verdict)}`), { n: entry.review.findingCount }) }}
              </span>
              <span v-if="entry.review.intent" class="history-intent" :class="`history-intent--${entry.review.intent}`" data-test="history-intent">
                {{ t(k(entry.review.intent === 'rerun' ? 'intent_rerun' : 'intent_normal')) }}
              </span>
              <span v-if="entry.review.revisionNo !== null" class="history-rev" data-test="history-rev">{{ t(k('rev'), { n: entry.review.revisionNo }) }}</span>
              <span class="history-when" data-test="history-when">{{ documentHistoryWhen(entry.review.at) }}</span>
              <button
                v-if="entry.review.comment"
                type="button"
                class="history-fold"
                :aria-expanded="!folded.has(entry.key)"
                :aria-label="t(k(folded.has(entry.key) ? 'unfold' : 'fold'))"
                :title="t(k(folded.has(entry.key) ? 'unfold' : 'fold'))"
                data-test="history-fold"
                @click="toggle(entry.key)"
              >{{ folded.has(entry.key) ? '▸' : '▾' }}</button>
            </div>
            <div v-if="entry.review.reviewerName" class="history-provider" :class="{ 'is-mismatch': mismatch(entry.review) }" data-test="history-reviewer">
              <span>{{ t(k('reviewer'), { name: entry.review.reviewerName }) }}</span>
              <span v-if="mismatch(entry.review)">{{ t(k('requested'), { name: entry.review.requestedProviderId ?? '' }) }}</span>
            </div>
            <p v-if="supersedes(entry.review)" class="history-sub" data-test="history-supersedes">{{ t(k('supersedes'), { reference: supersedes(entry.review) }) }}</p>
            <p v-if="entry.review.comment" class="history-text" :class="{ 'is-folded': folded.has(entry.key) }" data-test="history-comment">{{ entry.review.comment }}</p>
            <ul v-if="entry.review.findings.length" class="history-findings" data-test="history-findings">
              <li v-for="(finding, index) in entry.review.findings" :key="index">
                <strong v-if="finding.locus">{{ finding.locus }}</strong><span v-if="finding.locus"> — </span>{{ finding.note }}
              </li>
            </ul>
          </div>
        </li>
      </ul>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0056 F1 — a document's 검수·반려 이력 in its own modeless window (0056 CH §2): the v0.1
// '검수·반려 전체보기' dialog (QaReviewHistoryDialog.vue — rejections, AI reviews and the AI responses to the
// rejections on one timeline, newest first, with the 전체 / 반려 / AI 검수 filter and the fold of each entry)
// brought to v0.2 on ModelessShell, the document windows' look. Opened from the history button of a
// document line (openDocumentHistoryWindow); one window per document. Read through `@/features/document`
// (documentHistoryRead — the v0.1 document read, no new server API) and read again on the document's
// review events, on a reconnect and on the window's period (useDocumentWindowEvents), so a review or a
// rejection that lands while the window is open shows up.
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentErrorOf, documentHistoryRead, type DocumentHistory, type DocumentHistoryRejection, type DocumentHistoryReview } from '@/features/document'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import {
  DOCUMENT_HISTORY_FILTERS,
  documentHistoryCounts,
  documentHistoryFiltered,
  documentHistorySupersedes,
  documentHistoryTimeline,
  documentHistoryVerdictKey,
  documentHistoryWhen,
  type DocumentHistoryFilter,
} from './documentHistoryModel'
import { documentReadErrorKey } from './documentWindowModel'
import { closeDocumentHistoryWindow, setDocumentHistoryWindowMinimized, type DocumentHistoryWindowState } from './documentWindows'
import { useDocumentWindowEvents } from './useDocumentWindowEvents'

const props = defineProps<{
  win: DocumentHistoryWindowState
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.history.${key}`

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)

// --- The read -------------------------------------------------------------------------------------

const history = ref<DocumentHistory | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorKey = ref('load')
// Request order: only the latest read may set the view.
let generation = 0

async function reload(): Promise<void> {
  const seq = ++generation
  if (history.value === null) state.value = 'loading'
  try {
    const read = await documentHistoryRead(props.win.docId)
    if (seq !== generation) return
    history.value = read
    state.value = 'ready'
  } catch (error) {
    if (seq !== generation) return
    // A failed read again keeps the shown history; the first failure shows the reason.
    if (history.value === null) {
      errorKey.value = documentReadErrorKey(documentErrorOf(error).kind)
      state.value = 'error'
    }
  }
}

const title = computed(() => history.value?.record.title || props.win.docId)

// --- The timeline and the filter --------------------------------------------------------------------

const filter = ref<DocumentHistoryFilter>('all')
const timeline = computed(() => documentHistoryTimeline(history.value))
const counts = computed(() => documentHistoryCounts(timeline.value))
const shown = computed(() => documentHistoryFiltered(timeline.value, filter.value))

/** Entries folded to a short preview (every entry starts open, as the v0.1 dialog did). */
const folded = ref(new Set<string>())

function toggle(key: string): void {
  const next = new Set(folded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  folded.value = next
}

/** Who rejected: the reviewing AI of an automatic rejection, else the person. */
function rejectedBy(rejection: DocumentHistoryRejection): string {
  if (rejection.byAi) return aiName(rejection.rejectionProviderName)
  return t(k('by_user'))
}

function aiName(name: string | null): string {
  return name ? t(k('by_ai'), { name }) : t(k('ai_unknown'))
}

function mismatch(review: DocumentHistoryReview): boolean {
  return !!review.requestedProviderId && !!review.actualProviderId && review.requestedProviderId !== review.actualProviderId
}

function supersedes(review: DocumentHistoryReview): string | null {
  return documentHistorySupersedes(review, history.value?.reviews ?? [])
}

// --- Following the server ---------------------------------------------------------------------------

useDocumentWindowEvents(
  {
    docIds: () => [props.win.docId],
    shownId: () => props.win.docId,
    groupId: () => history.value?.record.groupId ?? null,
    projectId: () => history.value?.record.projectId ?? null,
    paused: () => props.win.minimized,
  },
  {
    content: () => undefined,
    record: () => void reload(),
  },
)

// A second open request brings the window back to the front and reads the history again.
watch(
  () => props.win.raise,
  () => {
    shell.value?.bringToFront()
    void reload()
  },
)

onMounted(() => {
  void reload()
})
</script>

<style scoped>
.history-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.history-window-kind {
  flex: 0 0 auto;
  font-weight: 700;
}

.history-window-title {
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-window {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.55;
}

.history-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.history-filter-count {
  margin-left: 4px;
  opacity: 0.7;
  font-variant-numeric: tabular-nums;
}

.history-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 4px;
  color: var(--fg2-muted);
}

.history-note--danger {
  color: var(--fg2-status-danger-fg);
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.history-entry {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.history-card {
  padding: 8px 11px;
  border: 1px solid var(--fg2-line);
  border-left-width: 3px;
  border-radius: 8px;
  background: #0e2034;
}

.history-card--review {
  border-left-color: #f59e0b;
}

.history-card--reject {
  border-left-color: #ef4444;
}

.history-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  margin-bottom: 4px;
}

.history-tag,
.history-verdict,
.history-intent,
.history-rev {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 7px;
  border: 1px solid #2c4560;
  border-radius: var(--fg2-control-radius-pill);
  background: #14243a;
  color: #8ea4bb;
  font-size: 10.5px;
  font-weight: 700;
  white-space: nowrap;
}

.history-tag--reject {
  border-color: #71313d;
  background: #3a1c24;
  color: #ff9eae;
}

.history-tag--review {
  border-color: #7b5a17;
  background: #3b2a0d;
  color: #ffc75d;
}

.history-verdict--pass {
  border-color: #246c53;
  background: #0d2b22;
  color: #72e4b8;
}

.history-verdict--issues,
.history-verdict--hold {
  border-color: #7b5a17;
  background: #3b2a0d;
  color: #ffc75d;
}

.history-intent--rerun {
  border-color: #7a4a2a;
  background: #3a2414;
  color: #ffb98a;
}

.history-who {
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-when {
  margin-left: auto;
  color: var(--fg2-muted-2);
  font-size: 11px;
  white-space: nowrap;
}

.history-fold {
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--fg2-muted);
  font: inherit;
  cursor: pointer;
}

.history-fold:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.history-provider {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin-bottom: 4px;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.history-provider.is-mismatch {
  color: var(--fg2-status-danger-fg);
  font-weight: 700;
}

.history-sub {
  margin: 0 0 4px;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.history-text {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.history-reason {
  color: #ffb4bf;
}

/* Folded: a two-line preview (the v0.1 clamp). */
.history-text.is-folded {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.history-findings {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--fg2-muted);
  font-size: 12px;
  list-style: disc;
}

.history-findings li {
  margin: 2px 0;
}

.history-response {
  margin: 6px 0 0 14px;
  padding: 6px 10px;
  border: 1px solid #2d5f9b;
  border-left-width: 3px;
  border-radius: 6px;
  background: #10294a;
}

.history-response-label {
  color: #8cc0ff;
  font-size: 11px;
  font-weight: 700;
}
</style>
