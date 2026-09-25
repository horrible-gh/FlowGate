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
      <AppIcon name="bell" />
      <!-- flowgate.default.0517 T0026 §1: Snapshot approvals waiting on a human count here
           too, since their list moved into this panel ([승인 대기]). Unread inflow clears on
           open; a pending approval keeps the badge up until someone decides it. -->
      <span v-if="badgeCount > 0" class="notif-badge" :title="badgeTitle" data-test="notif-badge">
        {{ badgeCount > 99 ? '99+' : badgeCount }}
      </span>
    </button>

    <div v-if="open" class="notif-panel">
      <div class="notif-panel-hd">
        <span class="notif-panel-title">{{ t('main.notif_center.title') }}</span>
        <!-- Mockup 3: live indicator — the feed refreshes in place as workflow inflow arrives over SSE. -->
        <span class="notif-live"><span class="notif-live-dot"></span> {{ t('main.notif_center.live') }}</span>
        <button
          v-if="activeSection === 'general' && store.items.length > 0"
          class="notif-mark-read"
          type="button"
          @click="markAllRead"
        >
          {{ t('main.notif_center.mark_all_read') }}
        </button>
      </div>

      <div
        class="notif-section-tabs"
        role="tablist"
        :aria-label="t('main.notif_center.sections_label')"
        :style="{ gridTemplateColumns: `repeat(${sections.length}, 1fr)` }"
      >
        <button
          v-for="section in sections"
          :key="section.key"
          class="notif-section-tab"
          :class="{ active: activeSection === section.key }"
          type="button"
          role="tab"
          :aria-selected="activeSection === section.key"
          :data-test="`notif-section-${section.key}`"
          @click="activeSection = section.key"
        >
          {{ section.label }}
        </button>
      </div>

      <div v-if="activeSection === 'general'" class="notif-section-body notif-section-body--general">
      <!-- Mockup 3: filter tabs (all / needs attention / unread) with live counts. -->
      <div v-if="store.items.length > 0" class="notif-tabs" role="tablist" :aria-label="t('main.notif_center.filters_label')">
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
          <AppIcon name="spinner" spin />
          <p>{{ t('main.overview.loading') }}</p>
        </div>
        <div v-else-if="store.error && store.items.length === 0" class="notif-empty">
          <AppIcon name="warning" />
          <p>{{ t('main.notif_center.load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">
            {{ t('main.overview.retry') }}
          </button>
        </div>
        <div v-else-if="store.items.length === 0" class="notif-empty">
          <AppIcon name="bell-slash" />
          <p>{{ t('main.notif_center.empty') }}</p>
        </div>
        <div v-else-if="visibleItems.length === 0" class="notif-empty">
          <AppIcon name="check-circle" />
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
              <AppIcon name="folder" class="notif-group-icon" />
              <strong class="notif-doc-id">{{ item.group.group_id }}</strong>
              <span class="notif-target-title">{{ item.group.title }}</span>
            </span>
            <span class="notif-msg">{{ activityActionLabel(item) }}</span>
            <!-- Mockup 3: "됐다는데 사실 확인 필요" — completed row whose AI verdict is issues. -->
            <span v-if="showRiskWarning(item)" class="notif-warn">
              <AppIcon name="warning" /> {{ t('main.notif_center.completed_but_issues') }}
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

      <div v-else-if="activeSection === 'ai'" class="notif-section-body notif-ai-section">
        <div v-if="store.loading" class="notif-loading"><span class="spinner"></span></div>
        <div v-else-if="store.error" class="notif-empty">
          <AppIcon name="warning" /><p>{{ store.error }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">{{ t('main.overview.retry') }}</button>
        </div>
        <div v-else-if="store.degradedSections.includes('ai_runs')" class="notif-empty">
          <AppIcon name="warning" /><p>{{ t('main.notif_center.ai_load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">{{ t('main.overview.retry') }}</button>
        </div>
        <div v-else-if="store.aiItems.length === 0" class="notif-empty">
          <AppIcon name="bell-slash" /><p>{{ t('main.notif_center.ai_empty') }}</p>
        </div>
        <article v-for="item in store.aiItems" v-else :key="item.run_id" class="notif-ai-row" :class="item.succeeded ? 'notif-ai-row--success' : 'notif-ai-row--failure'">
          <AppIcon :name="item.succeeded ? 'check-circle' : 'warning'" class="notif-ai-status-icon" />
          <div class="notif-ai-content">
            <strong class="notif-ai-status">{{ item.succeeded ? t('main.notif_center.ai_success') : t('main.notif_center.ai_failure') }}</strong>
            <div class="notif-target">
              <span v-if="item.doc_type_code" class="doc-tag" :class="'c-' + item.doc_type_code">{{ item.doc_type_code }}</span>
              <span v-if="item.doc_ref" class="notif-doc-id">{{ item.doc_ref }}</span>
              <span v-if="item.doc_title" class="notif-target-title">{{ item.doc_title }}</span>
            </div>
            <p v-if="aiSummary(item)" class="notif-msg">{{ aiSummary(item) }}</p>
            <span class="notif-time">{{ [item.provider_name, formatDashboardTime(item.finished_at)].filter(Boolean).join(' · ') }}</span>
          </div>
          <button class="notif-ai-detail-btn" type="button" @click="openAiDetail(item.run_id, $event)">{{ t('main.notif_center.ai_detail') }}</button>
        </article>
      </div>
      <!-- flowgate.default.0517 T0026 §1 — the durable Snapshot Pending list (T0012 §9), moved
           here from its own header icon. Rows keep exactly [거절]/[자세히]; approval lives in
           the detail dialog only, and [거절] asks for a reason first. -->
      <div v-else-if="activeSection === 'snapshot'" class="notif-section-body snap-pending-section" data-test="snap-pending-section">
        <div class="snap-pending-hd">
          <strong class="snap-pending-title">{{ t('main.snapshot_approval.pending_panel_title', { n: snapshotStore.pendingCount }) }}</strong>
          <p class="snap-pending-hint">{{ t('main.snapshot_approval.pending_panel_hint') }}</p>
        </div>
        <div class="notif-panel-body">
          <div v-if="snapshotStore.loading && snapshotStore.pending.length === 0" class="notif-empty">
            <AppIcon name="spinner" spin />
          </div>
          <div v-else-if="snapshotStore.error && snapshotStore.pending.length === 0" class="notif-empty">
            <AppIcon name="warning" />
            <p>{{ t('main.snapshot_approval.pending_load_failed') }}</p>
            <button class="btn btn-outline btn-sm" type="button" @click="refreshSnapshots">
              {{ t('main.snapshot_approval.pending_retry') }}
            </button>
          </div>
          <div v-else-if="snapshotStore.pending.length === 0" class="notif-empty">
            <AppIcon name="check" />
            <p>{{ t('main.snapshot_approval.pending_empty') }}</p>
          </div>
          <article
            v-for="row in snapshotStore.pending"
            v-else
            :key="row.snapshot_id"
            class="snap-pending-item"
            :class="{ 'snap-pending-item--whole': row.scope === 'whole_source' }"
            data-test="snap-pending-item"
          >
            <div class="snap-pending-row1">
              <span class="snap-pending-provider"><AppIcon name="robot" /> {{ snapshotProviderLabel(row.provider_id) }}</span>
              <span class="badge" :class="row.scope === 'whole_source' ? 'badge-yellow' : 'badge-gray'">{{ snapshotScopeLabel(row.scope) }}</span>
            </div>
            <p class="snap-pending-meta">
              {{ t('main.snapshot_approval.pending_item_meta', { group: row.group_id, time: formatDashboardTime(row.requested_at) }) }}
            </p>
            <p class="snap-pending-paths">{{ snapshotPathSummary(row) }}</p>
            <p class="snap-pending-reason">{{ row.reason }}</p>
            <p v-if="row.scope === 'whole_source'" class="snap-pending-warn">
              <AppIcon name="warning" /> {{ t('main.snapshot_approval.pending_whole_source_warning') }}
            </p>
            <div class="snap-pending-actions">
              <button type="button" class="btn btn-danger btn-sm" data-test="snap-pending-reject" @click="snapshotStore.openReject(row)">
                {{ t('main.snapshot_approval.pending_reject') }}
              </button>
              <button type="button" class="btn btn-primary btn-sm" data-test="snap-pending-details" @click="openSnapshotDetails(row)">
                {{ t('main.snapshot_approval.pending_details') }}
              </button>
            </div>
          </article>
        </div>
      </div>
      <div v-else class="notif-section-body notif-qa-section">
        <div v-if="store.loading" class="notif-loading"><span class="spinner"></span></div>
        <div v-else-if="store.error" class="notif-empty">
          <AppIcon name="warning" /><p>{{ t('main.notif_center.load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">{{ t('main.overview.retry') }}</button>
        </div>
        <div v-else-if="store.degradedSections.includes('open_questions')" class="notif-empty">
          <AppIcon name="warning" /><p>{{ t('main.notif_center.qa_load_failed') }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="refresh">{{ t('main.overview.retry') }}</button>
        </div>
        <div v-else-if="store.qaItems.length === 0" class="notif-empty">
          <AppIcon name="bell-slash" /><p>{{ t('main.notif_center.qa_empty') }}</p>
        </div>
        <article v-for="item in store.qaItems" v-else :key="item.doc_id" class="notif-qa-row">
          <div class="notif-target notif-qa-target">
            <span v-if="item.type_code" class="doc-tag" :class="'c-' + item.type_code">{{ item.type_code }}</span>
            <strong class="notif-doc-id">{{ item.doc_id }}</strong>
            <span v-if="item.title?.trim()" class="notif-target-title">{{ item.title.trim() }}</span>
          </div>
          <button class="notif-qa-open" type="button" @click="openQaDocument(item.doc_id)">{{ t('main.notif_center.qa_open') }} →</button>
        </article>
      </div>
    </div>
    <!-- AI 호출 상세 (NR0011 원장 ID 44). T0018 put it on the common dialog layer; 0560
         T0020 (4.5순위) moved the shell block into its own component — D0008 §4's other
         half. This file keeps the open flag, the fetched detail and the trigger element,
         and nothing about the dialog's own contract moved with it. -->
    <NotificationAiDetailDialog
      :open="detailOpen"
      :detail="detail"
      :loading="detailLoading"
      :errored="detailError"
      :return-focus-to="detailReturnFocus"
      @close="closeAiDetail"
      @open-document="openDetailDocument"
    />
    <!-- Snapshot approval (T0012) and its reject-reason prompt (T0026). Mounted with the bell,
         not the panel: a NEW request auto-opens the detail dialog with the panel closed.
         Both are driven by the snapshot store's targets, so each exists at most once. -->
    <SnapshotApprovalDialog />
    <SnapshotRejectDialog />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useProjectStore } from '../stores/project'
import { useNotificationsStore } from '../stores/notifications'
import { useDashboardNavigation } from '../composables/useDashboardNavigation'
import { useQaOpenIntent } from '../composables/useQaOpenIntent'
import { useActivityFormat } from '../composables/useActivityFormat'
import type { DashboardActivity } from '../stores/dashboard'; import type { AiInvokeDetail, AiInvokeNotification } from '../stores/notifications'; import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import NotificationAiDetailDialog from './NotificationAiDetailDialog.vue'
import SnapshotApprovalDialog from './SnapshotApprovalDialog.vue'
import SnapshotRejectDialog from './SnapshotRejectDialog.vue'
import { useSnapshotRequestsStore, type SnapshotRequestRow } from '../stores/snapshotRequests'
import { useAiProviderStore } from '../stores/aiProvider'
import { useSnapshotPendingSync } from '../composables/useSnapshotPendingSync'

const { t } = useI18n()
const router = useRouter()
const projectStore = useProjectStore()
const store = useNotificationsStore()
const { openDashboardTarget } = useDashboardNavigation()
const { requestQaOpen } = useQaOpenIntent()
const { activityColor, activityActionLabel, formatDashboardTime, reviewTone, reviewBadge } =
  useActivityFormat()
const snapshotStore = useSnapshotRequestsStore()
const aiProviderStore = useAiProviderStore()
const { refresh: refreshSnapshots } = useSnapshotPendingSync()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)
const detailOpen = ref(false)
const detail = ref<AiInvokeDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref(false)
let detailVersion = 0
// A ref, not a plain `let`: the dialog reads it as a prop at open time and the shell hands
// focus back to it from the common teardown path (T0018 §2.3-6), so the value has to be
// reactive rather than read once by a hand-written `nextTick` after close.
const detailReturnFocus = ref<HTMLElement | null>(null)

function aiSummary(item: AiInvokeNotification): string {
  const parts: string[] = []
  if (item.stop_code || item.end_reason) parts.push(item.stop_code || item.end_reason || '')
  if (item.docs_reached != null && item.docs_target != null) {
    parts.push(t('main.notif_center.ai_docs', { reached: item.docs_reached, target: item.docs_target }))
  }
  const excerpt = item.last_message_excerpt?.trim()
  if (excerpt) parts.push(excerpt.length > 240 ? excerpt.slice(0, 240) + '…' : excerpt)
  return parts.filter(Boolean).join(' · ')
}

async function openAiDetail(runId: string, event: Event) {
  detailReturnFocus.value = event.currentTarget as HTMLElement
  const version = ++detailVersion
  detailOpen.value = true
  detail.value = null
  detailError.value = false
  detailLoading.value = true
  await nextTick()
  try {
    const response = await getRequest<AiInvokeDetail>('/api/v1/ai-invoke/' + encodeURIComponent(runId))
    if (version === detailVersion) detail.value = response.data
  } catch {
    if (version === detailVersion) detailError.value = true
  } finally {
    if (version === detailVersion) detailLoading.value = false
  }
}

function closeAiDetail() {
  detailVersion++
  detailOpen.value = false
  detail.value = null
  // No hand-rolled focus return: `return-focus-to` on the shell puts focus back on the
  // trigger from the one teardown procedure (L0009 §2 "Open / Close lifecycle").
}

async function openDetailDocument() {
  const docRef = detail.value?.doc_ref
  if (!docRef) return
  closeAiDetail()
  open.value = false
  await openDashboardTarget({ kind: 'document', doc_id: docRef })
}

type NotifSection = 'general' | 'ai' | 'qa' | 'snapshot'
const activeSection = ref<NotifSection>('general')
// [승인 대기] only appears while there is something to decide (or while it is the section
// being looked at, so deciding the last request shows the empty state instead of yanking
// the view away). With nothing pending the panel is exactly the three sections it was.
const showSnapshotSection = computed(() => snapshotStore.pendingCount > 0 || activeSection.value === 'snapshot')
const sections = computed(() => [
  { key: 'general' as const, label: t('main.notif_center.section_general') },
  { key: 'ai' as const, label: t('main.notif_center.section_ai') + ' ' + store.aiItems.length },
  { key: 'qa' as const, label: t('main.notif_center.section_qa') + ' ' + store.qaTotal },
  ...(showSnapshotSection.value
    ? [{ key: 'snapshot' as const, label: t('main.notif_center.section_snapshot') + ' ' + snapshotStore.pendingCount }]
    : []),
])

const badgeCount = computed(() => store.unreadCount + snapshotStore.pendingCount)
const badgeTitle = computed(() =>
  snapshotStore.pendingCount > 0
    ? t('main.snapshot_approval.bell_pending_hint', { n: snapshotStore.pendingCount })
    : undefined,
)

const SNAPSHOT_SCOPE_LABEL_KEYS: Record<string, string> = {
  single_file: 'scope_single_file',
  selected_files: 'scope_selected_files',
  directory: 'scope_directory',
  whole_source: 'scope_whole_source',
}
function snapshotScopeLabel(scope: string): string {
  const key = SNAPSHOT_SCOPE_LABEL_KEYS[scope]
  return key ? t(`main.snapshot_approval.${key}`) : scope
}

function snapshotProviderLabel(providerId: string): string {
  const found = aiProviderStore.providers.find((p) => p.id === providerId)
  return found?.name || providerId || t('main.snapshot_approval.unknown_provider')
}

// Summary only (T0012 §9/§15) — the full list lives in the detail dialog only.
// Mockup ② phrasing: "<first path> 외 N건".
function snapshotPathSummary(row: SnapshotRequestRow): string {
  if (row.scope === 'whole_source') return t('main.snapshot_approval.scope_whole_source')
  const [first, ...rest] = row.requested_paths
  if (!first) return ''
  return rest.length
    ? t('main.snapshot_approval.pending_path_more', { first, n: rest.length })
    : first
}

// [자세히] hands over to the detail dialog and folds the panel away (T0012 behaviour).
function openSnapshotDetails(row: SnapshotRequestRow) {
  snapshotStore.openDetail(row)
  open.value = false
}

// The detail dialog's "Pending 목록에서 함께 확인 →" link (fg:snapshot_open_pending_panel).
function onOpenPendingPanelRequest() {
  open.value = true
  activeSection.value = 'snapshot'
  activeFilter.value = 'all'
  refresh()
  refreshSnapshots()
}

// Snapshot dialogs teleport out of this subtree like the AI detail dialog does, so the
// panel must neither read their clicks as "outside" nor take their ESC.
function anySnapshotDialogOpen(): boolean {
  return snapshotStore.detailOpen || snapshotStore.rejectOpen
}

// Mockup 3 filter tabs. All = everything; needs attention = rows whose AI verdict flags attention
// (issues/hold — the "됐다는데 사실 반쪽" cases the mockup surfaces); unread = unread since last open.
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

function isOverviewRoute(): boolean {
  return router?.currentRoute?.value?.path === '/'
}

function refresh() {
  const pid = projectStore.currentProjectId
  if (pid) void store.fetchFeed(pid)
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    // Waiting approvals are the one thing here that blocks an AI run, so the panel opens on
    // them when there are any — one click to the list, same as the old dedicated icon.
    activeSection.value = snapshotStore.pendingCount > 0 ? 'snapshot' : 'general'
    activeFilter.value = 'all'
    refresh()
    refreshSnapshots()
    void markAllRead()
  }
}

async function markAllRead() {
  const pid = projectStore.currentProjectId
  if (pid) await store.markSeen(pid)
}

async function openQaDocument(docId: string) {
  requestQaOpen(docId)
  open.value = false
  if (!isOverviewRoute()) await router.push('/')
  await openDashboardTarget({ kind: 'document', doc_id: docId })
}

function onItemClick(item: DashboardActivity) {
  if (item.navigation.kind === 'none') return
  void openDashboardTarget(item.navigation)
  open.value = false
}

function onClickOutside(e: MouseEvent) {
  if (!open.value) return
  // The detail dialog left this component's subtree when it moved onto the common layer
  // (DialogShell teleports to `#dialog-root`), so every click inside it now reads as
  // "outside the notification centre" and would close the panel underneath. Ignoring
  // clicks while the dialog is up keeps the pre-migration behaviour, where the dialog was
  // still a descendant of `rootEl` (T0018 §2.3-6).
  if (detailOpen.value) return
  if (anySnapshotDialogOpen()) return
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) open.value = false
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  // The detail dialog's ESC belongs to the common stack's single document listener now
  // (L0009 §2 "ESC"). Keeping a branch for it here would close it twice.
  if (detailOpen.value) return
  if (anySnapshotDialogOpen()) return
  if (open.value) open.value = false
}

// SSE inflow signal: refetch the feed (and thus the unread badge) without entering the dashboard.
// Debounced to coalesce bursts (a single workflow step can fire several events). The server stays
// the single source of truth — we never increment the badge client-side (NR0003 option D).
let refetchTimer: ReturnType<typeof setTimeout> | null = null
function onInflow() {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  refetchTimer = setTimeout(() => {
    refetchTimer = null
    if (projectStore.currentProjectId && !isOverviewRoute()) {
      void store.fetchFeed(projectStore.currentProjectId)
    }
  }, 300)
}

watch(() => projectStore.currentProjectId, (pid) => {
  open.value = false
  activeSection.value = 'general'
  activeFilter.value = 'all'
  store.reset()
  if (pid && !isOverviewRoute()) void store.fetchFeed(pid)
})

onMounted(() => {
  if (!isOverviewRoute()) refresh()
  window.addEventListener('fg:notification', onInflow)
  window.addEventListener('fg:snapshot_open_pending_panel', onOpenPendingPanelRequest)
  window.addEventListener('click', onClickOutside, true)
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  if (refetchTimer !== null) clearTimeout(refetchTimer)
  window.removeEventListener('fg:notification', onInflow)
  window.removeEventListener('fg:snapshot_open_pending_panel', onOpenPendingPanelRequest)
  window.removeEventListener('click', onClickOutside, true)
  window.removeEventListener('keydown', onKeyDown)
})

// Exposed so tests can observe panel-open state synchronously (ordering assertions);
// no runtime behaviour depends on this.
defineExpose({ open })
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
  left: 0;
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
.notif-msg { min-width: 0; overflow: hidden; font-size: .78rem; color: var(--text-secondary, #475569); text-overflow: ellipsis; white-space: nowrap; }
.notif-time { font-size: .7rem; color: var(--text-muted, #94a3b8); }

/* ── Mockup 3 (live feed) ── */
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

.notif-section-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  padding: 0 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.notif-section-tab {
  padding: 10px 4px 8px;
  border-bottom: 2px solid transparent;
  color: var(--text-muted, #64748b);
  font-size: .76rem;
  font-weight: 700;
}
.notif-section-tab:hover { color: var(--primary, #2563eb); }
.notif-section-tab.active {
  border-bottom-color: var(--primary, #2563eb);
  color: var(--primary, #2563eb);
}
.notif-section-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  padding: 32px 16px;
  color: var(--text-muted, #64748b);
  font-size: .8rem;
  text-align: center;
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

.notif-ai-section { max-height: min(70vh, 480px); overflow-x: hidden; overflow-y: auto; }
.notif-ai-row { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-bottom: 1px solid var(--border-subtle, #f1f5f9); border-left: 3px solid; }
.notif-ai-row--success { border-left-color: #22c55e; }
.notif-ai-row--failure { border-left-color: #ef4444; background: rgba(239, 68, 68, .04); }
.notif-ai-status-icon { margin-top: 2px; }
/* The `.detail-*` half of these two rules left with the dialog (T0020) — a scoped rule
   here could not reach markup this component no longer renders. */
.notif-ai-row--success .notif-ai-status-icon { color: #15803d; }
.notif-ai-row--failure .notif-ai-status-icon { color: #b91c1c; }
.notif-ai-content { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 4px; }
.notif-ai-status { font-size: .78rem; }
.notif-ai-detail-btn { align-self: center; color: var(--primary, #2563eb); font-size: .72rem; font-weight: 700; white-space: nowrap; }
/* Newly arrived (unread) rows slide in — the mockup's "완료가 리스트로 흘러 들어온다". */
.notif-item--fresh { animation: notifFreshIn .45s ease-out; }
@keyframes notifFreshIn {
  from { transform: translateY(-10px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
.notif-qa-row {
  min-height: 58px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--border-subtle, #f1f5f9);
  transition: background var(--tr, .15s);
}
.notif-qa-row:hover { background: var(--hover, #f8fafc); }
.notif-qa-target { min-width: 0; flex: 1; }
.notif-qa-open { flex: none; white-space: nowrap; color: var(--primary, #2563eb); font-size: .72rem; font-weight: 700; }
.notif-qa-open:hover { text-decoration: underline; }

/* ── Snapshot Pending section (T0012 §9 rows, moved here by T0026) ── */
.snap-pending-hd {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  background: var(--bg, #f0f4f8);
}
.snap-pending-title { font-size: .76rem; color: var(--text, #0f172a); }
.snap-pending-hint { margin: 0; font-size: .72rem; color: var(--text-m, #64748b); }
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
