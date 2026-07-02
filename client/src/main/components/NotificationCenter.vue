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
        <!-- 시안 3: live indicator — the feed refreshes in place as workflow inflow arrives over SSE. -->
        <span class="notif-live"><span class="notif-live-dot"></span> {{ t('main.notif_center.live') }}</span>
        <button
          v-if="store.items.length > 0"
          class="notif-mark-read"
          type="button"
          @click="markAllRead"
        >
          {{ t('main.notif_center.mark_all_read') }}
        </button>
      </div>

      <!-- 시안 3: filter tabs (전체 / 확인 필요 / 미확인) with live counts. -->
      <div v-if="store.items.length > 0" class="notif-tabs" role="tablist">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="notif-tab"
          :class="{ active: activeFilter === tab.key }"
          type="button"
          role="tab"
          :aria-selected="activeFilter === tab.key"
          @click="activeFilter = tab.key"
        >
          {{ tab.label }} <span class="notif-tab-n">{{ tab.count }}</span>
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
        <div v-else-if="visibleItems.length === 0" class="notif-empty">
          <i class="fa-regular fa-circle-check"></i>
          <p>{{ t('main.notif_center.filter_empty') }}</p>
        </div>
        <button
          v-for="item in visibleItems"
          v-else
          :key="item.event_id"
          class="notif-item"
          :class="[
            reviewTone(item) ? `notif-item--${reviewTone(item)}` : '',
            {
              'notif-item--unread': store.isUnread(item),
              'notif-item--fresh': store.isUnread(item),
              'notif-item--disabled': item.navigation.kind === 'none',
            },
          ]"
          type="button"
          :disabled="item.navigation.kind === 'none'"
          @click="onItemClick(item)"
        >
          <span class="notif-dot" :style="{ background: dotColor(item) }"></span>
          <span class="notif-content">
            <span v-if="item.document" class="notif-target">
              <span class="doc-tag" :class="`c-${item.document.type_code}`">{{ item.document.type_code }}</span>
              <strong class="notif-doc-id" :title="item.document.doc_id">{{ item.document.doc_id }}</strong>
              <span class="notif-target-title">{{ item.document.title }}</span>
              <span
                v-if="reviewBadge(item)"
                class="notif-ai-badge"
                :class="`notif-ai-badge--${reviewTone(item)}`"
              >{{ reviewBadge(item) }}</span>
            </span>
            <span v-else-if="item.group" class="notif-target">
              <i class="fa-regular fa-folder notif-group-icon"></i>
              <strong class="notif-doc-id">{{ item.group.group_id }}</strong>
              <span class="notif-target-title">{{ item.group.title }}</span>
            </span>
            <span class="notif-msg">{{ activityActionLabel(item) }}</span>
            <!-- 시안 3: "됐다는데 사실 확인 필요" — completed row whose AI verdict is issues. -->
            <span v-if="showRiskWarning(item)" class="notif-warn">
              <i class="fa-solid fa-triangle-exclamation"></i> {{ t('main.notif_center.completed_but_issues') }}
            </span>
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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const { activityColor, activityActionLabel, formatDashboardTime, reviewTone, reviewBadge } =
  useActivityFormat()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)

// 시안 3 filter tabs. 전체 = everything; 확인 필요 = rows whose AI verdict flags attention
// (issues/hold — the "됐다는데 사실 반쪽" cases the mockup surfaces); 미확인 = unread since last open.
type NotifFilter = 'all' | 'attention' | 'unread'
const activeFilter = ref<NotifFilter>('all')

function needsAttention(item: DashboardActivity): boolean {
  const tone = reviewTone(item)
  return tone === 'danger' || tone === 'caution'
}

const attentionCount = computed(() => store.items.filter(needsAttention).length)
const unreadItemsCount = computed(() => store.items.filter((i) => store.isUnread(i)).length)

const tabs = computed(() => [
  { key: 'all' as const, label: t('main.notif_center.filter_all'), count: store.items.length },
  { key: 'attention' as const, label: t('main.notif_center.filter_attention'), count: attentionCount.value },
  { key: 'unread' as const, label: t('main.notif_center.filter_unread'), count: unreadItemsCount.value },
])

const visibleItems = computed(() => {
  if (activeFilter.value === 'attention') return store.items.filter(needsAttention)
  if (activeFilter.value === 'unread') return store.items.filter((i) => store.isUnread(i))
  return store.items
})

// A completed inflow whose AI verdict is `issues` = "떴지만 사실 확인 필요" (mockup's red warning row).
function showRiskWarning(item: DashboardActivity): boolean {
  return item.document?.review?.verdict === 'issues'
}

// Row dot: the trust colour when the document is reviewed, else the per-activity inflow colour.
function dotColor(item: DashboardActivity): string {
  const tone = reviewTone(item)
  if (tone === 'ok') return '#16a34a'
  if (tone === 'caution') return '#d97706'
  if (tone === 'danger') return '#dc2626'
  return activityColor(item.activity_type)
}

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

/* ── 시안 3 (live feed) ── */
.notif-live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  padding: 2px 9px;
  border: 1px solid #fca5a5;
  border-radius: 999px;
  color: #b91c1c;
  background: #fef2f2;
  font-size: .58rem;
  font-weight: 800;
  letter-spacing: .08em;
}
.notif-mark-read { margin-left: 10px; }
.notif-live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #ef4444;
  animation: notifPulse 1.4s ease-in-out infinite;
}
@keyframes notifPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, .55); }
  50% { box-shadow: 0 0 0 5px rgba(239, 68, 68, 0); }
}

.notif-tabs {
  display: flex;
  gap: 5px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.notif-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 999px;
  background: var(--surface, #fff);
  color: var(--text-secondary, #475569);
  font-size: .72rem;
  font-weight: 600;
}
.notif-tab:hover { border-color: var(--primary, #2563eb); color: var(--primary, #2563eb); }
.notif-tab.active {
  color: #fff;
  background: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
}
.notif-tab-n {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  border-radius: 999px;
  background: rgba(15, 23, 42, .1);
  font-size: .62rem;
  font-weight: 800;
}
.notif-tab.active .notif-tab-n { background: rgba(255, 255, 255, .28); }

/* Trust-tone rows: left accent + faint tint from the document's AI verdict. */
.notif-item--ok { border-left: 3px solid #22c55e; }
.notif-item--caution { border-left: 3px solid #f59e0b; background: rgba(245, 158, 11, .05); }
.notif-item--danger { border-left: 3px solid #ef4444; background: rgba(239, 68, 68, .05); }

.notif-ai-badge {
  flex-shrink: 0;
  padding: 1px 7px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 999px;
  font-size: .6rem;
  font-weight: 800;
}
.notif-ai-badge--ok { color: #166534; background: #dcfce7; border-color: #86efac; }
.notif-ai-badge--caution { color: #b45309; background: #fef3c7; border-color: #fde68a; }
.notif-ai-badge--danger { color: #b91c1c; background: #fee2e2; border-color: #fca5a5; }

.notif-warn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: .7rem;
  font-weight: 600;
  color: #dc2626;
}

/* Newly arrived (unread) rows slide in — the mockup's "완료가 리스트로 흘러 들어온다". */
.notif-item--fresh { animation: notifFreshIn .45s ease-out; }
@keyframes notifFreshIn {
  from { transform: translateY(-10px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
</style>
