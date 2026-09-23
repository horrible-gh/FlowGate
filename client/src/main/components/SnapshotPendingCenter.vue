<template>
  <!--
    flowgate.default.0517 T0012 §8/§9 — header entry point + durable Pending list.
    Reuses NotificationCenter's bell/badge/panel shell classes (`.notif-bell`,
    `.notif-badge`, `.notif-panel*`) verbatim rather than inventing a second badge design
    (T0012 §8 "새 별도 badge 디자인/색 규칙을 만들지 않는다"), scoped under its own root so
    this stays a second, independent entry point next to the notification bell.
  -->
  <div ref="rootEl" class="notif-center snap-pending-center">
    <button
      class="hdr-btn notif-bell"
      type="button"
      :class="{ active: open }"
      :aria-label="t('main.snapshot_approval.pending_entry_label')"
      :aria-expanded="open"
      @click="toggle"
    >
      <AppIcon name="archive" />
      <span v-if="store.pendingCount > 0" class="notif-badge" data-test="snap-pending-badge">
        {{ store.pendingCount > 99 ? '99+' : store.pendingCount }}
      </span>
    </button>

    <div v-if="open" class="notif-panel snap-pending-panel">
      <div class="notif-panel-hd">
        <span class="notif-panel-title">{{ t('main.snapshot_approval.pending_panel_title', { n: store.pendingCount }) }}</span>
      </div>
      <p class="snap-pending-hint">{{ t('main.snapshot_approval.pending_panel_hint') }}</p>

      <div class="notif-panel-body">
        <div v-if="store.loading && store.pending.length === 0" class="notif-empty">
          <AppIcon name="spinner" spin />
        </div>
        <div v-else-if="store.error && store.pending.length === 0" class="notif-empty">
          <AppIcon name="warning" />
          <p>{{ t('main.snapshot_approval.pending_load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">
            {{ t('main.snapshot_approval.pending_retry') }}
          </button>
        </div>
        <div v-else-if="store.pending.length === 0" class="notif-empty">
          <AppIcon name="check" />
          <p>{{ t('main.snapshot_approval.pending_empty') }}</p>
        </div>
        <article
          v-for="row in store.pending"
          v-else
          :key="row.snapshot_id"
          class="snap-pending-item"
          :class="{ 'snap-pending-item--whole': row.scope === 'whole_source' }"
          data-test="snap-pending-item"
        >
          <div class="snap-pending-row1">
            <span class="snap-pending-provider"><AppIcon name="robot" /> {{ providerLabel(row.provider_id) }}</span>
            <span class="badge" :class="row.scope === 'whole_source' ? 'badge-yellow' : 'badge-gray'">{{ scopeLabel(row.scope) }}</span>
          </div>
          <p class="snap-pending-meta">
            {{ t('main.snapshot_approval.pending_item_meta', { group: row.group_id, time: formatDashboardTime(row.requested_at) }) }}
          </p>
          <p class="snap-pending-paths">{{ pathSummary(row) }}</p>
          <p class="snap-pending-reason">{{ row.reason }}</p>
          <p v-if="row.scope === 'whole_source'" class="snap-pending-warn">
            <AppIcon name="warning" /> {{ t('main.snapshot_approval.pending_whole_source_warning') }}
          </p>
          <div class="snap-pending-actions">
            <button type="button" class="btn btn-danger btn-sm" data-test="snap-pending-reject" @click="reject(row.snapshot_id)">
              {{ t('main.snapshot_approval.pending_reject') }}
            </button>
            <button type="button" class="btn btn-primary btn-sm" data-test="snap-pending-details" @click="openDetails(row)">
              {{ t('main.snapshot_approval.pending_details') }}
            </button>
          </div>
        </article>
      </div>
    </div>

    <!-- Single instance, driven entirely by the store's `detailTarget` — both the
         auto-open path (store.maybeAutoOpen) and this panel's [자세히] target the same
         dialog, so more than one can never be on screen (T0012 §11). -->
    <SnapshotApprovalDialog />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { useProjectStore } from '../stores/project'
import { useSnapshotRequestsStore, type SnapshotRequestRow } from '../stores/snapshotRequests'
import { useAiProviderStore } from '../stores/aiProvider'
import { useActivityFormat } from '../composables/useActivityFormat'
import SnapshotApprovalDialog from './SnapshotApprovalDialog.vue'

const { t } = useI18n()
const projectStore = useProjectStore()
const store = useSnapshotRequestsStore()
const aiProviderStore = useAiProviderStore()
const { formatDashboardTime } = useActivityFormat()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)

const SCOPE_LABEL_KEYS: Record<string, string> = {
  single_file: 'scope_single_file',
  selected_files: 'scope_selected_files',
  directory: 'scope_directory',
  whole_source: 'scope_whole_source',
}
function scopeLabel(scope: string): string {
  const key = SCOPE_LABEL_KEYS[scope]
  return key ? t(`main.snapshot_approval.${key}`) : scope
}

function providerLabel(providerId: string): string {
  const found = aiProviderStore.providers.find((p) => p.id === providerId)
  return found?.name || providerId || t('main.snapshot_approval.unknown_provider')
}

// Summary only (T0012 §9/§15) — the full list lives in the detail dialog only.
// Mockup ② phrasing: "<first path> 외 N건".
function pathSummary(row: SnapshotRequestRow): string {
  if (row.scope === 'whole_source') return t('main.snapshot_approval.scope_whole_source')
  const [first, ...rest] = row.requested_paths
  if (!first) return ''
  return rest.length
    ? t('main.snapshot_approval.pending_path_more', { first, n: rest.length })
    : first
}

function refresh() {
  const pid = projectStore.currentProjectId
  if (pid) void store.fetchPending(pid)
}

function toggle() {
  open.value = !open.value
  if (open.value) refresh()
}

// Mockup ①/③ footer note's "Pending 목록에서 함께 확인 →" link.
function onOpenPendingPanelRequest() {
  open.value = true
  refresh()
}

function openDetails(row: SnapshotRequestRow) {
  store.openDetail(row)
  open.value = false
}

async function reject(snapshotId: string) {
  try {
    await store.reject(snapshotId)
  } catch {
    // best-effort — the row stays in the list and the next refresh reconciles.
  }
}

function onClickOutside(e: MouseEvent) {
  if (!open.value) return
  // The approval dialog teleports out of this subtree (DialogShell → #dialog-root), so a
  // click inside it would otherwise read as "outside" and close this panel underneath —
  // same guard NotificationCenter uses for its own teleported detail dialog.
  if (store.detailOpen) return
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) open.value = false
}

let refetchTimer: ReturnType<typeof setTimeout> | null = null
function onRefreshSignal() {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  refetchTimer = setTimeout(() => {
    refetchTimer = null
    refresh()
  }, 300)
}

watch(() => projectStore.currentProjectId, (pid) => {
  open.value = false
  store.reset()
  if (pid) void store.fetchPending(pid)
})

onMounted(() => {
  refresh()
  // T0012 §12 durable restore: SSE reconnect and the server's snapshot-lifecycle refresh
  // signal both just mean "go re-read the pending list" (D0007 §4.1) — never trusted as
  // the list itself.
  window.addEventListener('fg:sse_reconnected', onRefreshSignal)
  window.addEventListener('fg:snapshot_refresh', onRefreshSignal)
  window.addEventListener('fg:snapshot_open_pending_panel', onOpenPendingPanelRequest)
  window.addEventListener('click', onClickOutside, true)
})

onBeforeUnmount(() => {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  window.removeEventListener('fg:sse_reconnected', onRefreshSignal)
  window.removeEventListener('fg:snapshot_refresh', onRefreshSignal)
  window.removeEventListener('fg:snapshot_open_pending_panel', onOpenPendingPanelRequest)
  window.removeEventListener('click', onClickOutside, true)
})

defineExpose({ open })
</script>

<style scoped>
.snap-pending-center { position: relative; display: inline-flex; }
.snap-pending-panel { width: 380px; }
.snap-pending-hint {
  margin: 0;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  font-size: .72rem;
  color: var(--text-m, #64748b);
  background: var(--bg, #f0f4f8);
}
.snap-pending-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-subtle, #f1f5f9);
}
.snap-pending-item--whole { background: var(--warning-l, #fef3c7); }
.snap-pending-row1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.snap-pending-provider { display: inline-flex; align-items: center; gap: 5px; font-size: .78rem; font-weight: 600; color: var(--text); }
.snap-pending-meta { margin: 0; font-size: .68rem; color: var(--text-m); }
.snap-pending-paths { margin: 0; font-family: 'JetBrains Mono', monospace; font-size: .72rem; color: var(--text-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.snap-pending-reason { margin: 0; font-size: .76rem; color: var(--text-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.snap-pending-warn { margin: 0; display: flex; align-items: center; gap: 5px; font-size: .7rem; font-weight: 600; color: var(--warning, #d97706); }
.snap-pending-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 2px; }
</style>
