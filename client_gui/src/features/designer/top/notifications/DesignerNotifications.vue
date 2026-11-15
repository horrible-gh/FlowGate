<template>
  <section ref="panel" id="designer-notifications" class="designer-notifications" :style="position" role="dialog" :aria-label="t('gui.designer.top.header.notifications')" @keydown.esc.stop.prevent="close(true)">
    <div class="notification-toolbar">
      <button :disabled="loading || !projectId" @click="refresh">{{ t('gui.designer.top.notifications.refresh') }}</button>
      <button @click="close(true)">{{ t('gui.designer.top.notifications.close') }}</button>
    </div>
    <div role="tablist" :aria-label="t('gui.designer.top.header.notifications')" class="notification-tabs" @keydown="tabKey">
      <button v-for="name in tabs" :id="`notification-tab-${name}`" :key="name" role="tab" :aria-selected="tab === name" :tabindex="tab === name ? 0 : -1" :aria-controls="tab === name ? `notification-panel-${name}` : undefined" @click="tab = name">{{ t(`gui.designer.top.notifications.tabs.${name}`) }}</button>
    </div>
    <div :id="`notification-panel-${tab}`" role="tabpanel" :aria-labelledby="`notification-tab-${tab}`" tabindex="0" :aria-busy="loading">
      <p v-if="!projectId">{{ t('gui.designer.top.notifications.no_project') }}</p>
      <template v-else>
        <p v-if="initialLoading" role="status">{{ t('gui.designer.top.notifications.loading') }}</p>
        <p v-if="(tab === 'runs' ? runsError : feedError) || degraded" role="alert">{{ t('gui.designer.top.notifications.load_error') }}</p>
        <p v-if="cancelError" role="alert">{{ t('gui.designer.top.notifications.cancel_error') }}</p>
        <p v-if="tab === 'runs' && !project" role="status">{{ t('gui.designer.top.notifications.project_unknown') }}</p>
        <NotificationRunsTab v-if="tab === 'runs' && project && !runsError && (!initialLoading || rows.length > 0)" :rows="rows" :project-name="project?.name || projectId" :busy="busy" @stop="stop" @open-document="openDocumentWindow" />
        <NotificationAiTab v-if="tab === 'ai' && !initialLoading && !feedError && !degraded" :items="feed?.ai_invoke_runs?.items || []" :total="feed?.ai_invoke_runs?.total" :has-more="feed?.ai_invoke_runs?.has_more" @open-document="openDocumentWindow" />
        <NotificationQuestionsTab v-if="tab === 'questions' && !initialLoading && !feedError && !degraded" :items="feed?.open_questions?.items || []" :total="feed?.open_questions?.total" :has-more="feed?.open_questions?.has_more" @open-document="openDocumentWindow" />
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
const props = defineProps<{ projectId: string | null; project: ExplorerProject | null; anchor: HTMLElement | null }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const tabs = ['runs', 'ai', 'questions'] as const
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
const degraded = computed(() => feed.value?.degraded_sections?.includes(tab.value === 'ai' ? 'ai_runs' : tab.value === 'questions' ? 'open_questions' : '') ?? false)
async function refresh() {
  const pid = props.projectId
  if (!pid || disposed) return
  if (loading.value) { refreshQueued = true; return }
  refreshQueued = false
  const ticket = generation
  loading.value = true
  const [active, notifications] = await Promise.allSettled([fetchActiveRuns(), fetchNotificationFeed(pid)])
  if (disposed || ticket !== generation) return
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
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); close(true) }
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !panel.value?.contains(event.target) && !props.anchor?.contains(event.target)) close()
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
  runs.value = []; feed.value = null; busy.value = []
  initialLoading.value = !!props.projectId
  loading.value = false; runsError.value = false; feedError.value = false; cancelError.value = false
  if (!props.projectId) return
  const reload = () => { void refresh() }
  unsubscribe = subscribeProjectEvents(props.projectId, { events: ['group_view_refresh', 'ai_invoke_started', 'ai_invoke_finished'], event: reload, resync: reload })
  // The feed has no complete event stream; poll only for this mounted, open popover.
  timer = setInterval(reload, 5000)
  reload()
}, { immediate: true })
onMounted(() => { place(); void focusTab(); window.addEventListener('resize', place); document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', escape) })
onBeforeUnmount(() => { disposed = true; generation++; unsubscribe?.(); clearInterval(timer); window.removeEventListener('resize', place); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape) })
</script>
<style scoped>
.designer-notifications { position: fixed; z-index: 100; width: min(480px, calc(100vw - 16px)); max-height: calc(100dvh - 70px); overflow: auto; box-sizing: border-box; padding: 12px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); background: var(--fg2-panel); color: var(--fg2-text); box-shadow: 0 8px 24px #0006; font-size: 13px; overflow-wrap: anywhere; }
.notification-toolbar, .notification-tabs { display: flex; gap: 6px; margin-bottom: 10px; }
.notification-toolbar { justify-content: flex-end; }
.notification-tabs button { flex: 1; }
.designer-notifications :deep(button) { border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); padding: 6px 9px; background: var(--fg2-panel); color: inherit; font: inherit; cursor: pointer; }
.designer-notifications :deep(button:disabled) { opacity: .5; cursor: default; }
.designer-notifications :deep(button:focus-visible), [role="tabpanel"]:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 2px; }
.notification-tabs [aria-selected="true"] { border-color: var(--fg2-focus); font-weight: 700; }
.designer-notifications :deep(.notification-list) { padding: 0; margin: 0; list-style: none; }
.designer-notifications :deep(li) { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 0; border-top: 1px solid var(--fg2-line); }
.designer-notifications :deep(li strong), .designer-notifications :deep(li p) { width: 100%; margin: 0; }
.designer-notifications :deep(.notification-actions) { display: flex; gap: 8px; width: 100%; justify-content: flex-end; }
</style>
