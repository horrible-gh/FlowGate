<template>
  <!-- 대시보드 AI 실행 모니터 (0269 재점검): the floating miniplayer only appears over
       the current screen, so the dashboard gets its own always-present card — the run
       list is visible there even when nothing is running. -->
  <div class="card" data-test="ai-run-monitor-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="robot" />
        {{ t('main.ai_miniplayer.dash_title') }}
      </span>
      <span v-if="entries.length > 0" class="airm-count">
        {{ t('main.ai_miniplayer.fab_summary', {
          running: store.activeCount,
          waiting: store.awaitingQCount + store.pausedCount,
        }) }}
      </span>
    </div>
    <div class="card-body">
      <div v-if="entries.length === 0" class="empty">
        <AppIcon name="robot" />
        <p>{{ t('main.ai_miniplayer.dash_empty') }}</p>
      </div>
      <div v-else class="airm-list">
        <!-- A row is a container, not a button: the remove action is a button of its own
             and nesting one inside another is invalid markup (0290 NR0003 §5.2). -->
        <div
          v-for="entry in entries"
          :key="entry.groupId"
          class="airm-row"
        >
          <button
            type="button"
            class="airm-row-main"
            @click="openDoc(entry)"
          >
            <AppIcon
              :name="cardIcon(entry)"
              :spin="entry.phase === 'running' && !entry.cancelling"
              class="airm-row-icon"
              :class="{ 'airm-row-icon--awaiting': isAwaitingQ(entry) }"
            />
            <span class="airm-row-body">
              <span class="airm-row-doc">{{ entry.docRef || entry.groupId }}</span>
              <span class="airm-row-meta">
                {{ stateLabel(entry) }}
                <template v-if="entry.provider?.name"> · {{ entry.provider.name }}</template>
                <template v-if="entry.docsTarget > 1">
                  · {{ t('main.ai_miniplayer.progress', { reached: reachedFor(entry), target: entry.docsTarget }) }}
                </template>
              </span>
            </span>
            <span
              class="airm-badge"
              :class="{ 'airm-badge--awaiting': isAwaitingQ(entry) }"
            >
              {{ isAwaitingQ(entry) ? t('main.ai_miniplayer.dash_state_awaiting') : modeLabel(entry) }}
            </span>
          </button>
          <button
            v-if="isFinishedCard(entry)"
            type="button"
            class="airm-row-remove"
            :title="t('main.ai_miniplayer.btn_remove')"
            :aria-label="t('main.ai_miniplayer.btn_remove')"
            data-test="ai-run-monitor-remove"
            @click="store.dismiss(entry.groupId)"
          >
            <AppIcon name="x" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'
import { useTabsStore } from '../stores/tabs'
import { useToast } from './common/useToast'
import {
  compareRunEntries,
  isAwaitingQ,
  isFinishedCard,
  openTargetDocId,
  useAiInvokeRunsStore,
  type AiInvokeRunEntry,
} from '../stores/aiInvokeRuns'

const { t } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const tabsStore = useTabsStore()

const entries = computed<AiInvokeRunEntry[]>(() =>
  Object.values(store.runsByGroup).slice().sort(compareRunEntries),
)

function cardIcon(entry: AiInvokeRunEntry): string {
  if (isAwaitingQ(entry)) return 'question'
  if (entry.phase === 'paused' || entry.phase === 'pause_requested') return 'pause'
  if (entry.phase === 'finished') return entry.outcome === 'complete' ? 'check-circle' : 'warning'
  if (entry.phase === 'lost') return 'warning'
  return 'circle-notch'
}

function stateLabel(entry: AiInvokeRunEntry): string {
  if (isAwaitingQ(entry)) return t('main.ai_miniplayer.dash_state_awaiting')
  if (entry.phase === 'paused') return t('main.ai_miniplayer.dash_state_paused')
  if (entry.phase === 'pause_requested') return t('main.ai_miniplayer.pause_scheduled')
  if (entry.phase === 'finished') return t('main.ai_miniplayer.dash_state_finished')
  if (entry.phase === 'lost') return t('main.ai_miniplayer.dash_state_lost')
  return t('main.ai_miniplayer.dash_state_running')
}

function modeLabel(entry: AiInvokeRunEntry): string {
  return entry.mode === 'continuous'
    ? t('main.ai_miniplayer.mode_continuous')
    : t('main.ai_miniplayer.mode_single')
}

function reachedFor(entry: AiInvokeRunEntry): number {
  return entry.phase === 'finished' || entry.phase === 'paused'
    ? Math.max(entry.docsReached, entry.docsReachedSoFar)
    : entry.docsReachedSoFar
}

async function openDoc(entry: AiInvokeRunEntry): Promise<void> {
  // A waiting Q wins, followed by the latest generated document and the source fallback.
  const docId = openTargetDocId(entry)
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
    // Same acknowledgement rule as the header monitor (0290 R0001 §1).
    store.dismiss(entry.groupId)
  } catch {
    showToast(t('main.ai_miniplayer.error_open_failed'), 'danger')
  }
}

onMounted(() => {
  // The overview can be the first screen after a reload — make sure the registry is
  // filled even if the miniplayer's own bootstrap has not landed yet (it de-dupes).
  void store.bootstrap()
})
</script>

<style scoped>
.airm-count {
  color: var(--text-m);
  font-size: .72rem;
}

.airm-list {
  display: flex;
  flex-direction: column;
}

.airm-row {
  display: flex;
  align-items: center;
  width: 100%;
  border-bottom: 1px solid var(--border);
}

.airm-row:last-child {
  border-bottom: none;
}

.airm-row-main {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 10px 4px 10px 16px;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.airm-row-main:hover {
  background: var(--surface-hover, rgba(148, 163, 184, .08));
}

.airm-row-remove {
  display: inline-flex;
  flex: 0 0 auto;
  margin-right: 10px;
  padding: 4px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-m);
  cursor: pointer;
}

.airm-row-remove:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
  color: var(--text);
}

.airm-row-icon {
  flex: 0 0 auto;
  color: var(--primary);
}

.airm-row-icon--awaiting {
  color: var(--warning);
}

.airm-row-body {
  flex: 1;
  min-width: 0;
}

.airm-row-doc {
  display: block;
  overflow: hidden;
  color: var(--text);
  font: 600 .74rem 'JetBrains Mono', monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.airm-row-meta {
  display: block;
  margin-top: 2px;
  color: var(--text-m);
  font-size: .7rem;
}

.airm-badge {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-m);
  font-size: .66rem;
  white-space: nowrap;
}

.airm-badge--awaiting {
  border-color: var(--warning);
  color: var(--warning);
  font-weight: 700;
}
</style>
