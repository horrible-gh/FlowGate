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
      <span v-if="store.unreadCount > 0" class="notif-badge">
        {{ store.unreadCount > 99 ? '99+' : store.unreadCount }}
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

      <div class="notif-section-tabs" role="tablist" :aria-label="t('main.notif_center.sections_label')">
        <button
          v-for="section in sections"
          :key="section.key"
          class="notif-section-tab"
          :class="{ active: activeSection === section.key }"
          type="button"
          role="tab"
          :aria-selected="activeSection === section.key"
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
            <strong class="notif-doc-id">{{ item.doc_id }}<template v-if="item.title?.trim()"> — {{ item.title.trim() }}</template></strong>
          </div>
          <button class="notif-qa-open" type="button" @click="openQaDocument(item.doc_id)">{{ t('main.notif_center.qa_open') }} →</button>
        </article>
      </div>
    </div>
    <div v-if="detailOpen" class="notif-dialog-backdrop" @click.stop>
      <section ref="detailDialogEl" class="notif-dialog" role="dialog" aria-modal="true" aria-labelledby="notif-ai-detail-title" tabindex="-1">
        <header class="notif-dialog-hd">
          <div>
            <strong id="notif-ai-detail-title">{{ t('main.notif_center.ai_detail_title') }}</strong>
            <span v-if="detail" :class="detail.succeeded ? 'detail-success' : 'detail-failure'">{{ detail.succeeded ? t('main.notif_center.ai_success') : t('main.notif_center.ai_failure') }}</span>
          </div>
          <button type="button" :aria-label="t('main.notif_center.close')" @click="closeAiDetail">×</button>
        </header>
        <div class="notif-dialog-meta">
          <p v-if="detail?.doc_ref"><strong>{{ detail.doc_ref }}</strong><template v-if="detail.doc_title"> · {{ detail.doc_title }}</template></p>
          <p v-if="detail">{{ [detail.stop_code || detail.end_reason, detail.finished_at ? formatDashboardTime(detail.finished_at) : null, detail.provider_name].filter(Boolean).join(' · ') }}</p>
        </div>
        <pre class="notif-dialog-message">{{ detailMessage }}</pre>
        <footer class="notif-dialog-actions">
          <button v-if="detail?.doc_ref" class="btn btn-outline btn-sm" type="button" @click="openDetailDocument">{{ t('main.notif_center.open_document') }}</button>
          <button class="btn btn-primary btn-sm" type="button" @click="closeAiDetail">{{ t('main.notif_center.close') }}</button>
        </footer>
      </section>
    </div>
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

const { t } = useI18n()
const router = useRouter()
const projectStore = useProjectStore()
const store = useNotificationsStore()
const { openDashboardTarget } = useDashboardNavigation()
const { requestQaOpen } = useQaOpenIntent()
const { activityColor, activityActionLabel, formatDashboardTime, reviewTone, reviewBadge } =
  useActivityFormat()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)
const detailOpen = ref(false)
const detail = ref<AiInvokeDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref(false)
const detailDialogEl = ref<HTMLElement | null>(null)
let detailVersion = 0
let detailReturnFocus: HTMLElement | null = null

const detailMessage = computed(() => {
  if (detailLoading.value) return t('main.notif_center.ai_detail_loading')
  if (detailError.value) return t('main.notif_center.ai_detail_failed')
  const message = detail.value?.last_message?.trim()
  if (message) return detail.value!.last_message!
  const reason = detail.value?.stop_reason?.trim()
  return reason || t('main.notif_center.ai_no_message')
})

function aiSummary(item: AiInvokeNotification): string {
  const parts: string[] = []
  if (item.stop_code || item.end_reason) parts.push(item.stop_code || item.end_reason || '')
  if (item.docs_reached != null && item.docs_target != null) {
    parts.push(t('main.notif_center.ai_docs', { reached: item.docs_reached, target: item.docs_target }))
  }
  const excerpt = item.last_message_excerpt?.trim()
  if (excerpt) parts.push(excerpt)
  return parts.filter(Boolean).join(' · ')
}

async function openAiDetail(runId: string, event: Event) {
  detailReturnFocus = event.currentTarget as HTMLElement
  const version = ++detailVersion
  detailOpen.value = true
  detail.value = null
  detailError.value = false
  detailLoading.value = true
  await nextTick()
  detailDialogEl.value?.focus()
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
  nextTick(() => detailReturnFocus?.focus())
}

async function openDetailDocument() {
  const docRef = detail.value?.doc_ref
  if (!docRef) return
  closeAiDetail()
  open.value = false
  await openDashboardTarget({ kind: 'document', doc_id: docRef })
}

type NotifSection = 'general' | 'ai' | 'qa'
const activeSection = ref<NotifSection>('general')
const sections = computed(() => [
  { key: 'general' as const, label: t('main.notif_center.section_general') },
  { key: 'ai' as const, label: t('main.notif_center.section_ai') + ' ' + store.aiItems.length },
  { key: 'qa' as const, label: t('main.notif_center.section_qa') + ' ' + store.qaTotal },
])

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

function refresh() {
  const pid = projectStore.currentProjectId
  if (pid) void store.fetchFeed(pid)
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    activeSection.value = 'general'
    activeFilter.value = 'all'
    refresh()
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
  if (router.currentRoute.value.path !== '/') await router.push('/')
  await openDashboardTarget({ kind: 'document', doc_id: docId })
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
  if (e.key !== 'Escape') return
  if (detailOpen.value) {
    closeAiDetail()
    return
  }
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
    if (projectStore.currentProjectId) void store.fetchFeed(projectStore.currentProjectId)
  }, 300)
}

watch(() => projectStore.currentProjectId, (pid) => {
  open.value = false
  activeSection.value = 'general'
  activeFilter.value = 'all'
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
.notif-msg { font-size: .78rem; color: var(--text-secondary, #475569); }
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

.notif-ai-section { max-height: min(70vh, 480px); overflow-y: auto; }
.notif-ai-row { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-bottom: 1px solid var(--border-subtle, #f1f5f9); border-left: 3px solid; }
.notif-ai-row--success { border-left-color: #22c55e; }
.notif-ai-row--failure { border-left-color: #ef4444; background: rgba(239, 68, 68, .04); }
.notif-ai-status-icon { margin-top: 2px; }
.notif-ai-row--success .notif-ai-status-icon, .detail-success { color: #15803d; }
.notif-ai-row--failure .notif-ai-status-icon, .detail-failure { color: #b91c1c; }
.notif-ai-content { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 4px; }
.notif-ai-status { font-size: .78rem; }
.notif-ai-detail-btn { align-self: center; color: var(--primary, #2563eb); font-size: .72rem; font-weight: 700; white-space: nowrap; }
.notif-dialog-backdrop { position: fixed; inset: 0; z-index: 2000; display: grid; place-items: center; padding: 24px; background: rgba(15, 23, 42, .42); }
.notif-dialog { width: min(640px, calc(100vw - 48px)); max-height: min(720px, calc(100vh - 48px)); overflow: auto; border-radius: 12px; background: var(--surface, #fff); box-shadow: 0 24px 64px rgba(0,0,0,.28); outline: none; }
.notif-dialog-hd, .notif-dialog-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--border, #e2e8f0); }
.notif-dialog-hd div { display: flex; gap: 10px; align-items: center; }
.notif-dialog-meta { padding: 12px 18px 0; color: var(--text-secondary, #475569); font-size: .78rem; }
.notif-dialog-message { min-height: 180px; margin: 12px 18px; padding: 14px; overflow: auto; border: 1px solid var(--border, #e2e8f0); border-radius: 8px; background: #f8fafc; color: var(--text, #0f172a); font: .8rem/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.notif-dialog-actions { justify-content: flex-end; border-top: 1px solid var(--border, #e2e8f0); border-bottom: 0; }

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
}
.notif-qa-target { min-width: 0; flex: 1; }
.notif-qa-open { flex: none; color: var(--primary, #2563eb); font-size: .72rem; font-weight: 700; }
.notif-qa-open:hover { text-decoration: underline; }
</style>
