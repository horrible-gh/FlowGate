<template>
  <!-- 🔔 Notification center (R0001 group 0045 / NR0003 option A + option D).
       Self-contained bell + dropdown: the persistent document-inflow history read from the server,
       with an unread badge so inflow is visible without entering the dashboard. -->
  <div ref="rootEl" class="notif-center">
    <button
      class="hdr-btn notif-bell"
      type="button"
      :class="{ active: open }"
      :aria-label="t('main.notif_center.title')"
      :aria-expanded="open"
      @click="toggle"
    >
      <i class="fa-regular fa-bell"></i>
      <span v-if="store.unreadCount > 0" class="notif-badge">
        {{ store.unreadCount > 99 ? '99+' : store.unreadCount }}
      </span>
    </button>

    <div v-if="open" class="notif-panel">
      <div class="notif-panel-hd">
        <span class="notif-panel-title">{{ t('main.notif_center.title') }}</span>
        <button
          v-if="store.items.length > 0"
          class="notif-mark-read"
          type="button"
          @click="markAllRead"
        >
          {{ t('main.notif_center.mark_all_read') }}
        </button>
      </div>

      <div class="notif-panel-body">
        <div v-if="store.loading && store.items.length === 0" class="notif-empty">
          <i class="fa-solid fa-spinner fa-spin"></i>
          <p>{{ t('main.overview.loading') }}</p>
        </div>
        <div v-else-if="store.error && store.items.length === 0" class="notif-empty">
          <i class="fa-solid fa-triangle-exclamation"></i>
          <p>{{ t('main.notif_center.load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">
            {{ t('main.overview.retry') }}
          </button>
        </div>
        <div v-else-if="store.items.length === 0" class="notif-empty">
          <i class="fa-regular fa-bell-slash"></i>
          <p>{{ t('main.notif_center.empty') }}</p>
        </div>
        <button
          v-for="item in store.items"
          v-else
          :key="item.event_id"
          class="notif-item"
          :class="{
            'notif-item--unread': store.isUnread(item),
            'notif-item--disabled': item.navigation.kind === 'none',
          }"
          type="button"
          :disabled="item.navigation.kind === 'none'"
          @click="onItemClick(item)"
        >
          <span class="notif-dot" :style="{ background: activityColor(item.activity_type) }"></span>
          <span class="notif-content">
            <span v-if="item.document" class="notif-target">
              <span class="doc-tag" :class="`c-${item.document.type_code}`">{{ item.document.type_code }}</span>
              <strong class="notif-doc-id" :title="item.document.doc_id">{{ item.document.doc_id }}</strong>
              <span class="notif-target-title">{{ item.document.title }}</span>
            </span>
            <span v-else-if="item.group" class="notif-target">
              <i class="fa-regular fa-folder notif-group-icon"></i>
              <strong class="notif-doc-id">{{ item.group.group_id }}</strong>
              <span class="notif-target-title">{{ item.group.title }}</span>
            </span>
            <span class="notif-msg">{{ activityActionLabel(item) }}</span>
            <span class="notif-time">
              {{ formatDashboardTime(item.occurred_at) }}
              <template v-if="item.group && item.document"> · {{ item.group.title }}</template>
              <template v-if="item.actor"> · {{ item.actor.username }}</template>
            </span>
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProjectStore } from '../stores/project'
import { useNotificationsStore } from '../stores/notifications'
import { useDashboardNavigation } from '../composables/useDashboardNavigation'
import { useActivityFormat } from '../composables/useActivityFormat'
import type { DashboardActivity } from '../stores/dashboard'

const { t } = useI18n()
const projectStore = useProjectStore()
const store = useNotificationsStore()
const { openDashboardTarget } = useDashboardNavigation()
const { activityColor, activityActionLabel, formatDashboardTime } = useActivityFormat()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)

function refresh() {
  const pid = projectStore.currentProjectId
  if (pid) void store.fetchFeed(pid)
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    refresh()
    void markAllRead()
  }
}

async function markAllRead() {
  const pid = projectStore.currentProjectId
  if (pid) await store.markSeen(pid)
}

function onItemClick(item: DashboardActivity) {
  if (item.navigation.kind === 'none') return
  void openDashboardTarget(item.navigation)
  open.value = false
}

function onClickOutside(e: MouseEvent) {
  if (!open.value) return
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) open.value = false
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape' && open.value) open.value = false
}

// SSE inflow signal: refetch the feed (and thus the unread badge) without entering the dashboard.
// Debounced to coalesce bursts (a single workflow step can fire several events). The server stays
// the single source of truth — we never increment the badge client-side (NR0003 option D).
let refetchTimer: ReturnType<typeof setTimeout> | null = null
function onInflow() {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  refetchTimer = setTimeout(() => {
    refetchTimer = null
    if (projectStore.currentProjectId) void store.fetchFeed(projectStore.currentProjectId)
  }, 300)
}

watch(() => projectStore.currentProjectId, (pid) => {
  open.value = false
  store.reset()
  if (pid) void store.fetchFeed(pid)
})

onMounted(() => {
  refresh()
  window.addEventListener('fg:notification', onInflow)
  window.addEventListener('click', onClickOutside, true)
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  window.removeEventListener('fg:notification', onInflow)
  window.removeEventListener('click', onClickOutside, true)
  window.removeEventListener('keydown', onKeyDown)
})
</script>

<style scoped>
.notif-center { position: relative; display: inline-flex; }

.notif-bell { position: relative; }
.notif-bell i { font-size: .9rem; }

.notif-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #ef4444;
  color: #fff;
  font-size: .62rem;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}

.notif-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 380px;
  max-width: calc(100vw - 32px);
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, .18);
  z-index: 1000;
  overflow: hidden;
}

.notif-panel-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.notif-panel-title { font-size: .85rem; font-weight: 700; color: var(--text, #0f172a); }
.notif-mark-read {
  font-size: .72rem;
  color: var(--primary, #2563eb);
  font-weight: 600;
}
.notif-mark-read:hover { text-decoration: underline; }

.notif-panel-body { max-height: min(70vh, 480px); overflow-y: auto; }

.notif-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 16px;
  color: var(--text-muted, #64748b);
  font-size: .8rem;
}
.notif-empty i { font-size: 1.4rem; opacity: .6; }

.notif-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle, #f1f5f9);
  transition: background var(--tr, .15s);
}
.notif-item:hover { background: var(--hover, #f8fafc); }
.notif-item--unread { background: rgba(37, 99, 235, .06); }
.notif-item--unread:hover { background: rgba(37, 99, 235, .1); }
.notif-item--disabled { cursor: default; opacity: .7; }
.notif-item--disabled:hover { background: transparent; }

.notif-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  flex-shrink: 0;
}

.notif-content { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.notif-target { display: flex; align-items: center; gap: 6px; min-width: 0; }
.notif-doc-id { font-size: .72rem; color: var(--text-muted, #64748b); flex-shrink: 0; }
.notif-group-icon { font-size: .72rem; color: var(--text-muted, #64748b); }
.notif-target-title {
  font-size: .8rem;
  font-weight: 600;
  color: var(--text, #0f172a);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.notif-msg { font-size: .78rem; color: var(--text-secondary, #475569); }
.notif-time { font-size: .7rem; color: var(--text-muted, #94a3b8); }
</style>
