<template>
  <FlowTerminalWindow v-for="(win, i) in windows" :key="win.key" :ref="el => setWindow(win.id, el)"
    :flow-id="win.id" :title="titleOf(win.id)" :session="win.session" :position="i"
    :visible="win.id === flowId" :focused="win.id === focusedId" :z-index="win.z"
    :designer="designerNow(win.id)" :fill-target="fillTarget ?? null"
    @focus="focus(win.id)" @close="close(win.id)" @reopen="reopen(win.id)" @designer="on => setDesigner(win.id, on)" />
  <ToastHost v-if="toasts.length" :toasts="toasts" :dismiss-label="w.dismiss" @dismiss="dismissToast" />
</template>

<script setup lang="ts">
// The Flow terminal windows (0078 · 0001-R §3.1, 0005-T 할 일 2·3): one window = one server session per Flow, opened by
// the toolbar's [터미널] button. Only the open Flow's window is shown; moving to another Flow tab hides it and its
// shell keeps running, opening that Flow again shows it. Closing a Flow tab closes its terminal (the user could no
// longer reach it, and it would hold one of the three sessions until the idle timeout).
// The button is decided by GET /terminal/status of the open Flow. A login token that is not an admin's never asks
// (the server answers not_admin anyway): the button is not drawn. There is no on/off setting (F85): an administrator
// always gets the button.
// 0015-T A: 모달리스 ↔ 디자이너. A terminal can fill the whole Designer area under the Flow tab strip (`fillTarget`,
// given by the Center column) and go back to its floating window, without ending the shell. Which Flow's terminal fills
// the Designer is kept here per Flow (dock.ts); the Center column asks `designerActive` to show the fill place.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { allocateOverlayZIndex } from '@/core/overlay/zIndex'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { isAdminFromToken } from '@shared/auth'
import FlowTerminalWindow from './FlowTerminalWindow.vue'
import { openFailureOf, openTerminalSession, readTerminalStatus, type OpenFailure, type TerminalSession } from './api'
import { flowTerminalUi } from './state'
import { createTerminalPlaceState, forgetFlow, isDesignerNow, toDesigner, toModeless } from './dock'
import { wordsFor } from './words'

/** The first size the server starts the shell with; the window sends its real size as soon as it is drawn. */
const FIRST_COLS = 100
const FIRST_ROWS = 28

const props = defineProps<{
  flowId: string | null; tabs: readonly { flowId: string; name: string }[]
  /** The Designer area under the Flow tab strip (0015-T A); null / absent = 디자이너 mode is unavailable. */
  fillTarget?: HTMLElement | null
}>()
const { locale } = useI18n()
const w = computed(() => wordsFor(locale.value))

interface TerminalWindowItem { id: string; key: string; session: TerminalSession; z: number }
const windows = ref<TerminalWindowItem[]>([])
const focusedId = ref<string | null>(null)
const refs = new Map<string, InstanceType<typeof FlowTerminalWindow>>()
function setWindow(id: string, el: unknown) { if (el) refs.set(id, el as InstanceType<typeof FlowTerminalWindow>); else refs.delete(id) }
const titleOf = (id: string) => props.tabs.find(tab => tab.flowId === id)?.name || id
let alive = true

function focus(id: string) {
  const win = windows.value.find(x => x.id === id)
  if (!win) return
  focusedId.value = id
  win.z = allocateOverlayZIndex()
}

/* ── 모달리스 ↔ 디자이너 (0015-T A) ── */
const place = createTerminalPlaceState()
const hasWindow = (id: string) => windows.value.some(x => x.id === id)
const designerNow = (id: string) => isDesignerNow(place, id, hasWindow(id), !!props.fillTarget)
/** The open Flow's terminal fills the Designer area: the Center column shows the fill place over the Canvas,
 *  the Inspector and the Minimap (they stay mounted under it and come back as they were). */
function designerActive(flowId: string | null): boolean {
  return !!flowId && designerNow(flowId)
}
/** [⇲ 디자이너로] (true) / [⇱ 모달리스로] (false) of a Flow's terminal. */
async function setDesigner(id: string, on: boolean) {
  if (on && !props.fillTarget) return
  if (on) toDesigner(place, id); else toModeless(place, id)
  await bringUp(id)
}

/* ── status of the open Flow (the toolbar button) ── */
let statusSeq = 0
async function refreshStatus() {
  const id = props.flowId
  const seq = ++statusSeq
  if (!id || !isAdminFromToken()) { flowTerminalUi.status = null; return }
  try {
    const status = await readTerminalStatus(id)
    if (!alive || seq !== statusSeq) return
    flowTerminalUi.status = status
  } catch {
    if (alive && seq === statusSeq) flowTerminalUi.status = null
  }
}

/* ── open / close ── */
const opening = new Set<string>()
/** Opens the open Flow's terminal, or brings it up: the floating window to the front (and back from minimized), the
 *  Designer-filling one gets the keyboard (0015-T A-6). */
async function open() {
  const id = props.flowId
  if (!id) return
  if (windows.value.some(x => x.id === id)) { await bringUp(id); return }
  await openFor(id)
}
async function bringUp(id: string) {
  focus(id)
  await nextTick()
  refs.get(id)?.reveal()
}
async function openFor(id: string) {
  if (opening.has(id)) return
  opening.add(id)
  try {
    const session = await openTerminalSession(id, FIRST_COLS, FIRST_ROWS)
    if (!alive) return
    windows.value.push({ id, key: session.session_id, session, z: allocateOverlayZIndex() })
    await bringUp(id)
  } catch (error) {
    const failure = openFailureOf(error)
    if (alive) notify(failure)
  } finally {
    opening.delete(id)
    if (alive) void refreshStatus()
  }
}
function drop(id: string) {
  windows.value = windows.value.filter(x => x.id !== id)
  forgetFlow(place, id)
  if (focusedId.value === id) focusedId.value = windows.value.at(-1)?.id ?? null
}
/** [✕] of either mode (the shell has ended): the Canvas · Inspector · Minimap come back; the next one opens floating. */
function close(id: string) {
  drop(id)
  void refreshStatus()
}
/** [새로 열기] of an ended terminal: a new session for the same Flow, in the same place (floating / Designer). */
async function reopen(id: string) {
  windows.value = windows.value.filter(x => x.id !== id)
  await nextTick()
  await openFor(id)
}

watch(() => props.flowId, (id, before) => {
  if (id === before) return
  void refreshStatus()
  if (id && windows.value.some(x => x.id === id)) focus(id)
}, { immediate: true })
// A closed Flow tab takes its terminal with it.
watch(() => props.tabs.map(tab => tab.flowId).join('\n'), () => {
  const open = new Set(props.tabs.map(tab => tab.flowId))
  for (const win of windows.value.filter(x => !open.has(x.id))) close(win.id)
  for (const id of Object.keys(place.designer)) if (!open.has(id)) forgetFlow(place, id)
})
watch(() => windows.value.map(x => x.id), ids => {
  flowTerminalUi.openFlows = Object.fromEntries(ids.map(id => [id, true]))
}, { immediate: true })
watch(() => !!props.flowId && windows.value.some(x => x.id === props.flowId), on => { flowTerminalUi.currentOpen = on }, { immediate: true })

/* ── notices ── */
const toasts = ref<ToastHostItem[]>([])
let toastSeq = 0
const FAILURE_WORD: Record<OpenFailure, 'limit' | 'exists' | 'notAdmin' | 'noCwd' | 'failed'> = {
  limit: 'limit', exists: 'exists', not_admin: 'notAdmin', no_cwd: 'noCwd', failed: 'failed',
}
function notify(failure: OpenFailure) {
  const tone = failure === 'failed' ? 'danger' : 'warning'
  toasts.value = [...toasts.value, { id: ++toastSeq, tone, message: w.value[FAILURE_WORD[failure]] }]
}
function dismissToast(id: string | number) { toasts.value = toasts.value.filter(item => item.id !== id) }

onMounted(() => { flowTerminalUi.open = () => { void open() } })
onBeforeUnmount(() => {
  alive = false
  flowTerminalUi.open = null
  flowTerminalUi.status = null
  flowTerminalUi.openFlows = {}
  flowTerminalUi.currentOpen = false
})
defineExpose({ open, close, refreshStatus, designerActive, setDesigner })
</script>
