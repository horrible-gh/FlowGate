<template>
  <section ref="panel" id="designer-notifications" class="designer-notifications" :style="position" role="dialog" :aria-label="t('gui.designer.top.header.notifications')" @keydown.esc.stop.prevent="close(true)">
    <!-- 0016-CH (시안 basodrr4 B안): one head row — the three tabs as a segmented control (icon, label, count; the
         selected one filled), then refresh / close as small icon buttons. -->
    <div class="notification-head">
      <div role="tablist" :aria-label="t('gui.designer.top.header.notifications')" class="notification-tabs" @keydown="tabKey">
        <button v-for="name in tabs" :id="`notification-tab-${name}`" :key="name" type="button" role="tab" class="notification-tab" :aria-selected="tab === name" :tabindex="tab === name ? 0 : -1" :aria-controls="tab === name ? `notification-panel-${name}` : undefined" @click="tab = name"><LineIcon :name="TAB_ICONS[name]" /><span class="notification-tab-label">{{ t(`gui.designer.top.notifications.tabs.${name}`) }}</span><span v-if="name !== 'ai' && countLabel(name === 'runs' ? counts.runs : counts.questions)" class="notification-tab-count" data-test="notification-tab-count">{{ ' ' + countLabel(name === 'runs' ? counts.runs : counts.questions) }}</span></button>
      </div>
      <div class="notification-toolbar">
        <button type="button" class="notification-icon-btn" :disabled="loading || !projectId" :aria-label="t('gui.designer.top.notifications.refresh')" :title="t('gui.designer.top.notifications.refresh')" @click="refresh"><LineIcon name="restore" /></button>
        <button type="button" class="notification-icon-btn" :aria-label="t('gui.designer.top.notifications.close')" :title="t('gui.designer.top.notifications.close')" @click="close(true)"><LineIcon name="x" /></button>
      </div>
    </div>
    <div :id="`notification-panel-${tab}`" class="notification-panel" role="tabpanel" :aria-labelledby="`notification-tab-${tab}`" tabindex="0" :aria-busy="loading">
      <p v-if="!projectId" class="notification-note">{{ t('gui.designer.top.notifications.no_project') }}</p>
      <template v-else>
        <p v-if="initialLoading" class="notification-note" role="status">{{ t('gui.designer.top.notifications.loading') }}</p>
        <p v-if="(tab === 'runs' ? runsError : tab === 'questions' ? questionsError : feedError) || degraded" class="notification-note is-alert" role="alert">{{ t('gui.designer.top.notifications.load_error') }}</p>
        <p v-if="cancelError" class="notification-note is-alert" role="alert">{{ t('gui.designer.top.notifications.cancel_error') }}</p>
        <p v-if="tab === 'runs' && !project" class="notification-note" role="status">{{ t('gui.designer.top.notifications.project_unknown') }}</p>
        <NotificationRunsTab v-if="tab === 'runs' && project && !runsError && (!initialLoading || rows.length > 0)" :rows="rows" :merge-items="mergeRows" :project-name="project?.name || projectId" :busy="busy" @stop="stop" @open-document="openDocumentWindow" @open-merge="openMerge" />
        <NotificationAiTab v-if="tab === 'ai' && !initialLoading && !feedError && !degraded" :items="feed?.ai_invoke_runs?.items || []" :total="feed?.ai_invoke_runs?.total" :has-more="feed?.ai_invoke_runs?.has_more" />
        <NotificationQuestionsTab v-show="tab === 'questions'" v-if="!initialLoading && !questionsError" :items="displayQuestions" :project-id="projectId" :project="project" :refresh-key="refreshKey" />
      </template>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ExplorerProject } from '../../projectExplorer/projectExplorerTree'
import { subscribeProjectEvents } from '@/features/project/projectEvents'
import { openDocumentWindow } from '@/features/documentWindow/documentWindows'
import { cancelInvokeRun, fetchActiveRuns, fetchNotificationFeed, type InvokeRun, type NotificationFeed } from './notificationApi'
import NotificationRunsTab from './NotificationRunsTab.vue'
import NotificationAiTab from './NotificationAiTab.vue'
import NotificationQuestionsTab from './NotificationQuestionsTab.vue'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
import { cardMergeItems, qaOpenItems, qaRead, type CardMergeItem, type QaOpenItem } from '@/features/document'
import { countLabel, notificationCounts } from './notificationCounts'
const displayQuestions = ref<QaOpenItem[]>([])
const questions = ref<QaOpenItem[] | null>(null), questionsError = ref(false), refreshKey = ref(0)
const props = defineProps<{ projectId: string | null; project: ExplorerProject | null; anchor: HTMLElement | null }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const tabs = ['runs', 'ai', 'questions'] as const
const TAB_ICONS: Record<(typeof tabs)[number], LineIconName> = { runs: 'play', ai: 'sparkles', questions: 'message' }
const tab = ref<(typeof tabs)[number]>('runs')
const panel = ref<HTMLElement | null>(null)
const position = ref({ left: '8px', top: '52px' })
const runs = ref<InvokeRun[]>([]), feed = ref<NotificationFeed | null>(null)
const loading = ref(false), runsError = ref(false), feedError = ref(false), cancelError = ref(false)
const initialLoading = ref(false)
const busy = ref<string[]>([])
let refreshQueued = false
let generation = 0, disposed = false, timer: ReturnType<typeof setInterval> | undefined
let unsubscribe: (() => void) | undefined
const rows = computed(() => {
  const flows = new Map(props.project?.groups.flatMap((group) => group.flows.map((flow) => [flow.id, flow.name] as const)) ?? [])
  return runs.value.filter((run) => flows.has(run.groupId)).map((run) => ({ ...run, flowName: flows.get(run.groupId)! }))
})
// flowgate.v02test.0042: card result merges waiting in this project's Flows (the runs tab lists them; a merge that only
// waits keeps the Flow "running" with no AI run, so the list was empty before).
const mergeItems = ref<CardMergeItem[]>([])
const mergeRows = computed(() => {
  const flows = new Map(props.project?.groups.flatMap((group) => group.flows.map((flow) => [flow.id, flow.name] as const)) ?? [])
  return mergeItems.value.filter((item) => flows.has(item.flowId)).map((item) => ({ ...item, flowName: flows.get(item.flowId)! }))
})
const counts = computed(() => {
  const base = notificationCounts(rows.value, questions.value)
  return { ...base, runs: base.runs, bell: base.bell + mergeRows.value.length }
})
/** [열기] of a merge row: the card's '병합 · 충돌' tab (the Designer's one "open this card's merge" event). */
function openMerge(item: CardMergeItem & { flowName: string }) {
  window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: item.flowId, nodeKey: item.nodeKey, flowName: item.flowName } }))
  close()
}
const degraded = computed(() => feed.value?.degraded_sections?.includes(tab.value === 'ai' ? 'ai_runs' : '') ?? false)
async function refresh() {
  const pid = props.projectId
  if (!pid || disposed) return
  if (loading.value) { refreshQueued = true; return }
  refreshQueued = false
  const ticket = generation
  loading.value = true
  const [active, notifications, open, merges] = await Promise.allSettled([fetchActiveRuns(), fetchNotificationFeed(pid), qaOpenItems(pid), cardMergeItems()])
  if (disposed || ticket !== generation) return
  // A merge list that could not be read shows none; the runs above are unaffected.
  mergeItems.value = merges.status === 'fulfilled' ? merges.value : []
  if (open.status === 'fulfilled') {
    const current = new Set(open.value.map(row => `${row.doc_id}:${row.seq}`))
    const missing = (questions.value ?? []).filter(row => !current.has(`${row.doc_id}:${row.seq}`))
    const documents = await Promise.allSettled([...new Set(missing.map(row => row.doc_id))].map(qaRead))
    if (disposed || ticket !== generation) return
    const answered = missing.filter(row => documents.some(result => result.status === 'fulfilled' && result.value.docId === row.doc_id && result.value.items.some(item => item.seq === row.seq && (item.answerCount > 0 || item.answers.length > 0))))
    displayQuestions.value = [...open.value, ...answered]
  } else displayQuestions.value = []
  questionsError.value = open.status === 'rejected'
  questions.value = open.status === 'fulfilled' ? open.value : null
  refreshKey.value++
  runsError.value = active.status === 'rejected'
  feedError.value = notifications.status === 'rejected'
  runs.value = active.status === 'fulfilled' ? active.value : []
  feed.value = notifications.status === 'fulfilled' ? notifications.value : null
  loading.value = false
  initialLoading.value = false
  // Coalesce signals received during this request into one fresh server read.
  if (refreshQueued) await refresh()
}
async function stop(run: InvokeRun) {
  if (!run.runId || busy.value.includes(run.key) || !rows.value.some((row) => row.key === run.key)) return
  const ticket = generation
  busy.value.push(run.key)
  cancelError.value = false
  try {
    await cancelInvokeRun(run.runId)
    // Keep the server phase until a read confirms it. A cancel request is not completion.
    if (!disposed && ticket === generation) await refresh()
  } catch {
    if (!disposed && ticket === generation) cancelError.value = true
  } finally {
    if (!disposed && ticket === generation) busy.value = busy.value.filter((key) => key !== run.key)
  }
}
function close(restoreFocus = false) {
  if (restoreFocus) props.anchor?.focus()
  emit('close')
}
// 0002-CH: a modeless window (AI detail, document windows; teleported to body) is not "outside" the popover.
// Pressing its title bar / resize handle or Escape inside it works on that window only.
function inModeless(target: EventTarget | null): boolean {
  const element = target instanceof Element ? target : target instanceof Node ? target.parentElement : null
  return !!element?.closest('.ui-modeless-panel')
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && !inModeless(event.target)) { event.preventDefault(); close(true) }
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !inModeless(event.target) && !panel.value?.contains(event.target) && !props.anchor?.contains(event.target)) close()
}
function place() {
  const rect = props.anchor?.getBoundingClientRect()
  if (!rect) return
  position.value = { left: `${Math.max(8, Math.min(rect.left, window.innerWidth - Math.min(480, window.innerWidth - 16) - 8))}px`, top: `${rect.bottom + 6}px` }
}
async function focusTab() { await nextTick(); panel.value?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus() }
function tabKey(event: KeyboardEvent) {
  const i = tabs.indexOf(tab.value)
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  tab.value = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (i + (event.key === 'ArrowRight' ? 1 : 2)) % 3]!
  void focusTab()
}
watch(() => props.projectId, () => {
  generation++
  refreshQueued = false
  unsubscribe?.()
  clearInterval(timer)
  displayQuestions.value = []; questions.value = null; questionsError.value = false; runs.value = []; feed.value = null; busy.value = []; mergeItems.value = []
  initialLoading.value = !!props.projectId
  loading.value = false; runsError.value = false; feedError.value = false; cancelError.value = false
  if (!props.projectId) return
  const reload = () => { void refresh() }
  unsubscribe = subscribeProjectEvents(props.projectId, { events: ['group_view_refresh', 'ai_invoke_started', 'ai_invoke_finished', 'qna_q_registered', 'qna_answer_registered'], event: reload, resync: reload })
  // The feed has no complete event stream; poll only for this mounted, open popover.
  timer = setInterval(reload, 5000)
  reload()
}, { immediate: true })
onMounted(() => { place(); void focusTab(); window.addEventListener('resize', place); document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', escape) })
onBeforeUnmount(() => { disposed = true; generation++; unsubscribe?.(); clearInterval(timer); window.removeEventListener('resize', place); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape) })
</script>
<style scoped>
/* 0016-CH (시안 basodrr4 B안): the raised card popover; type sizes and text colours come from theme.css only. */
.designer-notifications { position: fixed; z-index: 100; width: min(480px, calc(100vw - 16px)); max-height: calc(100dvh - 70px); overflow: auto; box-sizing: border-box; padding: 12px 10px 10px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-pop-radius); background: var(--fg2-pop-surface); color: var(--fg2-text); box-shadow: var(--fg2-pop-shadow); font-size: var(--fg2-fs-body); line-height: 1.45; overflow-wrap: anywhere; }
.notification-head { display: flex; align-items: center; gap: 6px; margin: 0 2px 10px; }
.notification-tabs { display: flex; flex: 1; gap: 4px; min-width: 0; padding: 4px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-inset-radius); background: var(--fg2-inset-bg); }
.notification-tab { display: flex; flex: 1; align-items: center; justify-content: center; gap: 6px; min-width: 0; height: 32px; padding: 0 8px; border: 0; border-radius: 7px; background: transparent; color: var(--fg2-text-3); font: inherit; font-weight: 600; cursor: pointer; white-space: nowrap; }
.notification-tab:hover { color: var(--fg2-text); }
.notification-tab :deep(svg) { width: 15px; height: 15px; }
.notification-tab[aria-selected="true"] { background: #1d3653; color: var(--fg2-text); box-shadow: inset 0 0 0 1px rgba(124, 192, 255, 0.35); }
.notification-tab-count { padding: 0 6px; border-radius: 9px; background: rgba(154, 181, 210, 0.18); color: var(--fg2-text-2); font-size: var(--fg2-fs-xs); font-weight: 700; line-height: 16px; }
.notification-tab[aria-selected="true"] .notification-tab-count { background: var(--fg2-orange); color: #1a1205; }
.notification-toolbar { display: flex; flex: none; gap: 2px; }
.notification-panel { display: flex; flex-direction: column; gap: 6px; }
/* :deep — the tabs' own empty / status lines use the same class. */
.designer-notifications :deep(.notification-note) { margin: 6px; color: var(--fg2-text-3); font-size: var(--fg2-fs-sub); }
.designer-notifications :deep(.notification-note.is-alert) { color: var(--fg2-status-danger-fg); }
.designer-notifications :deep(button) { font: inherit; color: inherit; cursor: pointer; }
.designer-notifications :deep(button:disabled) { opacity: .45; cursor: default; }
.designer-notifications :deep(button:focus-visible), .notification-panel:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 2px; }
/* Small icon-only buttons: refresh / close here, the row actions in the tabs. */
.designer-notifications :deep(.notification-icon-btn) { display: inline-grid; place-items: center; flex: none; width: 28px; height: 28px; padding: 0; border: 1px solid transparent; border-radius: 7px; background: transparent; color: var(--fg2-text-3); }
.designer-notifications :deep(.notification-icon-btn:hover:not(:disabled)) { background: rgba(124, 192, 255, 0.1); color: var(--fg2-text); }
.designer-notifications :deep(.notification-icon-btn svg) { width: 16px; height: 16px; }
.designer-notifications :deep(.notification-row-actions .notification-icon-btn) { border-color: var(--fg2-line); background: var(--fg2-control-surface-flat); }
/* Card rows shared by the three tabs: [status box] [title / meta] [time / actions]. */
.designer-notifications :deep(.notification-list) { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.designer-notifications :deep(.notification-card) { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: center; gap: 4px 12px; min-width: 0; padding: 12px; border: 1px solid var(--fg2-line); border-radius: 12px; background: rgba(5, 12, 20, 0.45); }
.designer-notifications :deep(.notification-status) { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; color: var(--fg2-text-3); background: rgba(154, 181, 210, 0.1); }
.designer-notifications :deep(.notification-status svg) { width: 16px; height: 16px; }
.designer-notifications :deep(.notification-status.is-ok) { color: var(--fg2-cyan); background: rgba(73, 223, 208, 0.12); }
.designer-notifications :deep(.notification-status.is-run) { color: var(--fg2-blue); background: rgba(75, 163, 255, 0.12); }
.designer-notifications :deep(.notification-status.is-fail) { color: #ff7a8c; background: rgba(255, 100, 120, 0.12); }
.designer-notifications :deep(.notification-status.is-wait) { color: var(--fg2-orange); background: rgba(255, 174, 87, 0.12); }
.designer-notifications :deep(.notification-main) { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.designer-notifications :deep(.notification-title) { overflow: hidden; color: var(--fg2-text); font-size: var(--fg2-fs-body); font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.designer-notifications :deep(.notification-meta) { overflow: hidden; color: var(--fg2-text-3); font-size: var(--fg2-fs-sub); text-overflow: ellipsis; white-space: nowrap; }
.designer-notifications :deep(.notification-reason) { margin: 0; color: var(--fg2-text-2); font-size: var(--fg2-fs-sub); }
.designer-notifications :deep(.notification-side) { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.designer-notifications :deep(.notification-side time) { color: var(--fg2-text-3); font-size: var(--fg2-fs-xs); white-space: nowrap; }
.designer-notifications :deep(.notification-row-actions) { display: flex; gap: 4px; }
.designer-notifications :deep(.notification-context) { margin: 0 6px 2px; color: var(--fg2-text-3); font-size: var(--fg2-fs-xs); }
</style>
