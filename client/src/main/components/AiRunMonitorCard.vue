<template>
  <!-- Dashboard AI-run monitor (0269 recheck): the floating miniplayer only appears over
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
                <template v-if="targetFor(entry) > 1">
                  · {{ t('main.ai_miniplayer.progress', { reached: reachedFor(entry), target: targetFor(entry) }) }}
                </template>
                <template v-if="entry.workerDocumentType">
                  · {{ t('main.ai_miniplayer.worker_short', { type: entry.workerDocumentType }) }}
                </template>
                <template v-if="entry.endReason">
                  · {{ entry.endReason }}
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
          <!-- 0500 T0004 §7: this X IS the "목록에서 제거" the report is about, and gating
               it on isFinishedCard() alone left the non-resumable system-stop card with no
               remove control at all on this screen -- the card the user "지워도 계속 뜬다".
               It is offered there too now, and for that class it goes through the durable
               server release (doRemove) instead of the local-only dismiss. -->
          <button
            v-if="isFinishedCard(entry) || isNonResumableSystemStop(entry)"
            type="button"
            class="airm-row-remove"
            :title="t('main.ai_miniplayer.btn_remove')"
            :aria-label="t('main.ai_miniplayer.btn_remove')"
            data-test="ai-run-monitor-remove"
            :disabled="busy.has(entry.groupId)"
            :aria-disabled="busy.has(entry.groupId)"
            @click="doRemove(entry)"
          >
            <AppIcon name="x" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'
import { useTabsStore } from '../stores/tabs'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useToast } from './common/useToast'
import {
  compareRunEntries,
  isAwaitingQ,
  isFinishedCard,
  isNonResumableSystemStop,
  openTargetDocId,
  useAiInvokeRunsStore,
  type AiInvokeRunEntry,
} from '../stores/aiInvokeRuns'

const { t } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const tabsStore = useTabsStore()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()

const entries = computed<AiInvokeRunEntry[]>(() =>
  Object.values(store.runsByGroup).slice().sort(compareRunEntries),
)

// Per-group in-flight guard for the durable remove below: the DELETE is a round trip, and
// a second click while it is out would fire a second one against the same row.
const busy = reactive(new Set<string>())

async function doRemove(entry: AiInvokeRunEntry): Promise<void> {
  // 0500 T0004 §7/§8: on a finished/lost card this stays the local dismiss it always was
  // (§19.4). On a non-resumable system stop the durable ai_invoke_paused_chains row has to
  // go first, or the next active-all bootstrap rebuilds the very card just removed
  // (NR0003 §2/§8). store.removeCard() drops the card only on a confirmed release (§16).
  if (!isNonResumableSystemStop(entry)) {
    store.dismiss(entry.groupId)
    return
  }
  if (!window.confirm(t('main.ai_miniplayer.release_confirm_system'))) return
  busy.add(entry.groupId)
  try {
    await store.removeCard(entry.groupId)
    if (!store.runsByGroup[entry.groupId]) {
      showToast(t('main.ai_miniplayer.release_paused_success'), 'success')
    }
  } catch (error: any) {
    const status = error?.response?.status
    const code = error?.response?.data?.code
    if (status === 403) {
      showToast(t('main.ai_miniplayer.error_release_paused_forbidden'), 'danger')
    } else if (status === 409 && code === 'group_lease_active') {
      showToast(t('main.ai_miniplayer.error_release_paused_lease_conflict'), 'danger')
    } else if (status === 409 && code === 'release_conflict') {
      showToast(t('main.ai_miniplayer.error_release_paused_release_conflict'), 'danger')
    } else if (status === 409) {
      showToast(t('main.ai_miniplayer.error_release_paused_resume_conflict'), 'danger')
    } else {
      showToast(t('main.ai_miniplayer.error_release_paused_failed'), 'danger')
    }
  } finally {
    busy.delete(entry.groupId)
  }
}

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

function targetFor(entry: AiInvokeRunEntry): number {
  const chainTarget = Number(entry.chainDocsTarget)
  return Number.isFinite(chainTarget) ? chainTarget : entry.docsTarget
}

function reachedFor(entry: AiInvokeRunEntry): number {
  const chainReached = Number(entry.chainDocsReached)
  if (Number.isFinite(chainReached)) return chainReached
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
    // Reveal + select the opened doc in the document explorer, same as the header
    // miniplayer — the AI-run monitor card carried the identical open-without-reveal
    // gap (0316 T0004 / NR0003 §3-2 · recommendation 2), and the same rev1 rejection: it passed the
    // current project id, so opening a doc from another project never switched to it.
    // Target the document's OWN project (d.project_id) with switchProject. Best-effort
    // and detached from the open.
    void explorerStore.revealDocInGroupTree(
      d.project_id ?? projectStore.currentProjectId,
      d.doc_id,
      { switchProject: true },
    )
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
