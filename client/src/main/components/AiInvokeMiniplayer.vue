<template>
  <!-- 실행 미니플레이어 (group 0252 D0007): bottom-right floating monitor for every run
       the user owns. Always mounted — with no run to monitor it stays as a muted idle
       pill so the monitor is visibly present on the dashboard and on document screens
       instead of vanishing (0269 재점검). -->
  <div
    class="aiv-mini"
    :class="{ 'aiv-mini--collapsed': collapsed, 'aiv-mini--idle': idle }"
    data-test="ai-miniplayer"
  >
    <button
      v-if="collapsed"
      type="button"
      class="aiv-mini__fab"
      :class="{ 'aiv-mini__fab--awaiting': store.awaitingQCount > 0 }"
      :title="fabText"
      @click="setCollapsed(false)"
    >
      <AppIcon name="robot" />
      <span class="aiv-mini__fab-text">{{ fabText }}</span>
    </button>

    <section v-else class="aiv-mini__panel" :aria-label="t('main.ai_miniplayer.title')">
      <header class="aiv-mini__head">
        <span class="aiv-mini__head-title">
          <AppIcon name="robot" />
          {{ t('main.ai_miniplayer.title') }}
        </span>
        <span class="aiv-mini__head-summary">{{ fabText }}</span>
        <button
          type="button"
          class="aiv-mini__iconbtn"
          :title="t('main.ai_miniplayer.collapse')"
          @click="setCollapsed(true)"
        >
          <AppIcon name="caret-down" />
        </button>
      </header>

      <p v-if="idle" class="aiv-mini__empty">{{ t('main.ai_miniplayer.empty') }}</p>

      <TransitionGroup v-else name="aiv-mini-card" tag="ul" class="aiv-mini__list">
        <li
          v-for="entry in entries"
          :key="entry.groupId"
          class="aiv-mini__card"
          :class="[`aiv-mini__card--${entry.phase}`, { 'aiv-mini__card--awaiting': isAwaitingQ(entry) }]"
        >
          <div class="aiv-mini__row">
            <AppIcon
              :name="cardIcon(entry)"
              :spin="entry.phase === 'running' && !entry.cancelling"
            />
            <div class="aiv-mini__title-wrap">
              <div class="aiv-mini__title">{{ titleFor(entry) }}</div>
              <div class="aiv-mini__doc">{{ entry.docRef || entry.groupId }}</div>
            </div>
            <span v-if="isAwaitingQ(entry)" class="aiv-mini__badge aiv-mini__badge--q">
              {{ t('main.ai_miniplayer.awaiting_q_badge', { count: entry.pendingQDocIds.length }) }}
            </span>
            <span v-else class="aiv-mini__badge">{{ modeLabel(entry) }}</span>
          </div>

          <!-- Progress moves ONLY on document/step arrival (D0007 decision 5); the
               spinner above is the sole liveness cue and mimics no data. -->
          <div v-if="entry.mode === 'continuous' || entry.docsTarget > 1" class="aiv-mini__progress">
            <div class="aiv-mini__progress-track">
              <div class="aiv-mini__progress-fill" :style="{ width: progressPercent(entry) }" />
            </div>
            <span class="aiv-mini__progress-text">
              {{ t('main.ai_miniplayer.progress', { reached: reachedFor(entry), target: entry.docsTarget }) }}
            </span>
          </div>

          <div class="aiv-mini__meta">
            <template v-if="entry.phase === 'paused'">
              <span>{{ t('main.ai_miniplayer.state_paused') }}</span>
            </template>
            <template v-else-if="entry.phase === 'finished'">
              <span>{{ outcomeLabel(entry) }}</span>
            </template>
            <template v-else-if="entry.phase === 'lost'">
              <span>{{ t('main.ai_invoke_dialog.error_run_lost') }}</span>
            </template>
            <template v-else>
              <span>{{ t('main.ai_miniplayer.provider', { name: entry.provider?.name || '—' }) }}</span>
              <span>{{ elapsedText(entry) }}</span>
            </template>
          </div>
          <div v-if="isAwaitingQ(entry)" class="aiv-mini__meta aiv-mini__meta--q">
            {{ t('main.ai_miniplayer.awaiting_q_line') }}
          </div>
          <div v-if="entry.phase === 'pause_requested'" class="aiv-mini__meta">
            {{ t('main.ai_miniplayer.pause_scheduled') }}
          </div>

          <div class="aiv-mini__actions">
            <button
              v-if="entry.phase === 'running' && entry.mode === 'continuous'"
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="entry.cancelling || busy.has(entry.groupId)"
              @click="doPause(entry)"
            >
              <AppIcon name="pause" />
              {{ t('main.ai_miniplayer.btn_pause') }}
            </button>
            <button
              v-else-if="entry.phase === 'pause_requested'"
              type="button"
              class="btn btn-ghost btn-sm"
              disabled
            >
              <AppIcon name="pause" />
              {{ t('main.ai_miniplayer.pause_scheduled') }}
            </button>
            <button
              v-if="entry.phase === 'paused'"
              type="button"
              class="btn btn-primary btn-sm"
              :disabled="busy.has(entry.groupId)"
              @click="doResume(entry)"
            >
              <AppIcon name="play" />
              {{ t('main.ai_miniplayer.btn_resume') }}
            </button>
            <button
              v-if="entry.phase === 'running' || entry.phase === 'pause_requested'"
              type="button"
              class="btn btn-danger btn-sm"
              :disabled="entry.cancelling || busy.has(entry.groupId)"
              :title="t('main.ai_invoke_dialog.btn_cancel_run')"
              @click="doCancel(entry)"
            >
              <AppIcon name="prohibit" />
            </button>
            <button
              v-if="entry.phase === 'finished' || entry.phase === 'lost'"
              type="button"
              class="btn btn-ghost btn-sm"
              @click="store.dismiss(entry.groupId)"
            >
              <AppIcon name="x" />
              {{ t('common.close') }}
            </button>
            <button type="button" class="btn btn-ghost btn-sm" @click="openDoc(entry)">
              <AppIcon name="arrow-square-out" />
              {{ t('main.ai_miniplayer.btn_open_doc') }}
            </button>
          </div>
        </li>
      </TransitionGroup>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'
import { useTabsStore } from '../stores/tabs'
import { useToast } from './common/useToast'
import {
  isAwaitingQ,
  useAiInvokeRunsStore,
  type AiInvokeRunEntry,
} from '../stores/aiInvokeRuns'

const COLLAPSE_LS_KEY = 'flowgate.aiMiniplayer.collapsed'

const { t } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const tabsStore = useTabsStore()

const collapsed = ref(readCollapsed())
const busy = reactive(new Set<string>())
const titles = reactive<Record<string, string>>({})

const entries = computed<AiInvokeRunEntry[]>(() =>
  Object.values(store.runsByGroup).sort((a, b) => a.groupId.localeCompare(b.groupId)),
)

const idle = computed(() => entries.value.length === 0)

const fabText = computed(() =>
  idle.value
    ? t('main.ai_miniplayer.idle_summary')
    : t('main.ai_miniplayer.fab_summary', {
        running: store.activeCount,
        waiting: store.awaitingQCount + store.pausedCount,
      }),
)

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_LS_KEY) === '1'
  } catch {
    return false
  }
}

function setCollapsed(value: boolean): void {
  collapsed.value = value
  try {
    localStorage.setItem(COLLAPSE_LS_KEY, value ? '1' : '0')
  } catch { /* ignore quota errors */ }
}

function cardIcon(entry: AiInvokeRunEntry): string {
  if (isAwaitingQ(entry)) return 'question'
  if (entry.phase === 'paused' || entry.phase === 'pause_requested') return 'pause'
  if (entry.phase === 'finished') return entry.outcome === 'complete' ? 'check-circle' : 'warning'
  if (entry.phase === 'lost') return 'warning'
  return 'circle-notch'
}

function modeLabel(entry: AiInvokeRunEntry): string {
  return entry.mode === 'continuous'
    ? t('main.ai_miniplayer.mode_continuous')
    : t('main.ai_miniplayer.mode_single')
}

function outcomeLabel(entry: AiInvokeRunEntry): string {
  if (entry.outcome === 'complete') return t('main.ai_invoke_dialog.outcome_complete')
  if (entry.outcome === 'partial') return t('main.ai_invoke_dialog.outcome_partial')
  return t('main.ai_invoke_dialog.outcome_none')
}

function reachedFor(entry: AiInvokeRunEntry): number {
  return entry.phase === 'finished' || entry.phase === 'paused'
    ? Math.max(entry.docsReached, entry.docsReachedSoFar)
    : entry.docsReachedSoFar
}

function progressPercent(entry: AiInvokeRunEntry): string {
  // L0009 §2.9: ratio pinned to 0 when the target is unknown/zero (no divide-by-zero).
  const target = entry.docsTarget
  if (!Number.isFinite(target) || target <= 0) return '0%'
  const ratio = Math.min(1, Math.max(0, reachedFor(entry) / target))
  return `${Math.round(ratio * 100)}%`
}

function elapsedText(entry: AiInvokeRunEntry): string {
  const total = Math.floor(store.elapsedMsFor(entry.groupId) / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function titleFor(entry: AiInvokeRunEntry): string {
  const docId = entry.docRef
  if (docId && titles[docId]) return titles[docId]
  if (docId) void fetchTitle(docId)
  return docId || entry.groupId
}

const titleFetches = new Set<string>()
async function fetchTitle(docId: string): Promise<void> {
  if (titleFetches.has(docId) || titles[docId]) return
  titleFetches.add(docId)
  try {
    const res = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(docId)}`)
    const d = (res.data as any)?.data ?? res.data
    if (d?.title) titles[docId] = String(d.title)
  } catch {
    // Best effort — the card falls back to the doc id.
  }
}

async function doPause(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.pause(entry.groupId)
  } catch {
    showToast(t('main.ai_miniplayer.error_pause_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function doResume(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.resume(entry.groupId)
  } catch {
    showToast(t('main.ai_miniplayer.error_resume_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function doCancel(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.cancel(entry.groupId)
  } catch {
    showToast(t('main.ai_invoke_dialog.error_cancel_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function openDoc(entry: AiInvokeRunEntry): Promise<void> {
  // 질의 대기 card jumps straight to the waiting Q; answers are registered in the
  // document's existing Q&A panel, never in the card itself (D0007 화면 구성).
  const docId = entry.pendingQDocIds[0] ?? entry.docRef
  if (!docId) return
  try {
    const res = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(docId)}`)
    const d = (res.data as any)?.data ?? res.data
    if (!d?.doc_id) return
    tabsStore.openTab({
      id: d.doc_id,
      title: d.title ?? d.doc_id,
      path: d.file_path ?? '',
      type: d.type_code === 'Q' ? 'qtui' : 'md',
      mdPath: d.file_path ?? null,
      typeCode: d.type_code ?? null,
    })
  } catch {
    showToast(t('main.ai_miniplayer.error_open_failed'), 'danger')
  }
}

onMounted(() => {
  // P0008 S1: one bootstrap per app mount restores running + paused + awaiting cards.
  void store.bootstrap()
})

watch(entries, list => {
  for (const entry of list) {
    if (entry.docRef && !titles[entry.docRef]) void fetchTitle(entry.docRef)
  }
})
</script>

<style scoped>
.aiv-mini {
  position: fixed;
  right: 18px;
  bottom: 18px;
  /* Must sit above the shared .modal-bg/.modal-overlay layer (z 1000) — the document
     full view uses it, and the cards must stay visible while a document is being read
     there (0269 D0002 "화면 어디에서든 항상"). Kept below alert-grade dialogs
     (ReviewReject/TimeMachine 1200, GitConflictResolver 1400) and toasts (2000). */
  z-index: 1100;
}

.aiv-mini__fab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid color-mix(in srgb, var(--primary) 45%, var(--border));
  border-radius: 999px;
  background: var(--surface);
  box-shadow: var(--sh-md, 0 8px 24px rgba(15, 23, 42, .18));
  color: var(--text);
  font-size: .78rem;
  font-weight: 600;
  cursor: pointer;
}

/* Idle presence: visible but quiet — no primary tint, no shadow pull. */
.aiv-mini--idle .aiv-mini__fab,
.aiv-mini--idle .aiv-mini__panel {
  border-color: var(--border);
  opacity: .72;
}

.aiv-mini--idle .aiv-mini__fab {
  color: var(--text-m);
  font-weight: 500;
}

.aiv-mini--idle:hover .aiv-mini__fab,
.aiv-mini--idle:hover .aiv-mini__panel {
  opacity: 1;
}

.aiv-mini__empty {
  margin: 0;
  padding: 14px 14px 16px;
  color: var(--text-m);
  font-size: .74rem;
  text-align: center;
}

.aiv-mini__fab--awaiting {
  border-color: var(--warning);
  color: var(--warning);
}

.aiv-mini__panel {
  display: flex;
  flex-direction: column;
  width: min(360px, calc(100vw - 36px));
  max-height: min(70vh, 560px);
  border: 1px solid color-mix(in srgb, var(--primary) 35%, var(--border));
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--sh-md, 0 10px 30px rgba(15, 23, 42, .2));
  overflow: hidden;
}

.aiv-mini__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.aiv-mini__head-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text);
  font-size: .78rem;
  font-weight: 700;
}

.aiv-mini__head-summary {
  flex: 1;
  color: var(--text-m);
  font-size: .72rem;
  text-align: right;
}

.aiv-mini__iconbtn {
  display: inline-flex;
  padding: 4px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-m);
  cursor: pointer;
}

.aiv-mini__iconbtn:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

.aiv-mini__list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
}

.aiv-mini__card {
  display: grid;
  gap: 7px;
  padding: 11px 12px;
  border-bottom: 1px solid var(--border);
}

.aiv-mini__card:last-child {
  border-bottom: none;
}

.aiv-mini__card--awaiting {
  background: var(--warning-l);
  box-shadow: inset 3px 0 0 var(--warning);
}

.aiv-mini__card--paused {
  box-shadow: inset 3px 0 0 color-mix(in srgb, var(--primary) 60%, transparent);
}

.aiv-mini__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.aiv-mini__row > .app-icon {
  margin-top: 2px;
  color: var(--primary);
}

.aiv-mini__card--awaiting .aiv-mini__row > .app-icon {
  color: var(--warning);
}

.aiv-mini__title-wrap {
  flex: 1;
  min-width: 0;
}

.aiv-mini__title {
  overflow: hidden;
  color: var(--text);
  font-size: .8rem;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aiv-mini__doc {
  overflow: hidden;
  margin-top: 1px;
  color: var(--text-m);
  font: 500 .68rem 'JetBrains Mono', monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aiv-mini__badge {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-m);
  font-size: .66rem;
  white-space: nowrap;
}

.aiv-mini__badge--q {
  border-color: var(--warning);
  color: var(--warning);
  font-weight: 700;
}

.aiv-mini__progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.aiv-mini__progress-track {
  flex: 1;
  height: 5px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text) 12%, transparent);
  overflow: hidden;
}

.aiv-mini__progress-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--primary);
  transition: width .3s ease;
}

.aiv-mini__progress-text {
  color: var(--text-m);
  font-size: .68rem;
  white-space: nowrap;
}

.aiv-mini__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--text-m);
  font-size: .7rem;
}

.aiv-mini__meta--q {
  color: var(--warning);
  font-weight: 600;
}

.aiv-mini__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.aiv-mini-card-enter-active,
.aiv-mini-card-leave-active {
  transition: opacity .18s ease;
}

.aiv-mini-card-enter-from,
.aiv-mini-card-leave-to {
  opacity: 0;
}

@media (max-width: 680px) {
  .aiv-mini {
    right: 10px;
    bottom: 10px;
  }
}
</style>
