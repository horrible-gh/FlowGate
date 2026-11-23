<template>
  <FlowTerminalWindow v-for="(win, i) in windows" :key="win.key" :ref="el => setWindow(win.id, el)"
    :flow-id="win.id" :title="titleOf(win.id)" :session="win.session" :position="i"
    :visible="win.id === flowId" :focused="win.id === focusedId" :z-index="win.z"
    :docked="dockedNow(win.id)" :dock-target="dockTarget ?? null"
    @focus="focus(win.id)" @close="close(win.id)" @reopen="reopen(win.id)" @dock="on => setDocked(win.id, on)" />
  <ToastHost v-if="toasts.length" :toasts="toasts" :dismiss-label="w.dismiss" @dismiss="dismissToast" />
</template>

<script setup lang="ts">
// The Flow terminal windows (0078 · 0001-R §3.1, 0005-T 할 일 2·3): one window = one server session per Flow, opened by
// the toolbar's [터미널] button. Only the open Flow's window is shown; moving to another Flow tab hides it and its
// shell keeps running, opening that Flow again shows it. Closing a Flow tab closes its terminal (the user could no
// longer reach it, and it would hold one of the three sessions until the idle timeout).
// The button is decided by GET /terminal/status of the open Flow. A login token that is not an admin's never asks
// (the server answers not_admin anyway): the button is not drawn.
// 0009-T: a window can be docked into the Designer's bottom Dock (`dockTarget`, the Dock's terminal place) and back.
// Where each Flow's terminal is (floating / docked) and what the bottom Dock shows for it (Inspector | 터미널) is
// kept here per Flow (dock.ts) for as long as this Host lives; the bottom Dock reads it through `bottomPane`.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { allocateOverlayZIndex } from '@/core/overlay/zIndex'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { isAdminFromToken } from '@shared/auth'
import FlowTerminalWindow from './FlowTerminalWindow.vue'
import { openFailureOf, openTerminalSession, readTerminalStatus, type OpenFailure, type TerminalSession } from './api'
import { flowTerminalUi } from './state'
import {
  bottomPaneOf, createTerminalDockState, dockFlow, forgetFlow, isDockedNow, selectBottomPane as choosePane, undockFlow,
  type BottomPane,
} from './dock'
import { wordsFor } from './words'

/** The first size the server starts the shell with; the window sends its real size as soon as it is drawn. */
const FIRST_COLS = 100
const FIRST_ROWS = 28

const props = defineProps<{
  flowId: string | null; tabs: readonly { flowId: string; name: string }[]
  /** The bottom Dock's terminal place (0009-T); null / absent = docking is unavailable. */
  dockTarget?: HTMLElement | null
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

/* ── docking into the bottom Dock (0009-T) ── */
const dock = createTerminalDockState()
const hasWindow = (id: string) => windows.value.some(x => x.id === id)
const dockedNow = (id: string) => isDockedNow(dock, id, hasWindow(id), !!props.dockTarget)
/** What the bottom Dock's left part shows for `flowId`: null = the Inspector alone (no Inspector | 터미널 switch). */
function bottomPane(flowId: string | null): BottomPane | null {
  if (!props.dockTarget || !flowId) return null
  return bottomPaneOf(dock, flowId, hasWindow(flowId))
}
function selectBottomPane(flowId: string, pane: BottomPane) {
  choosePane(dock, flowId, pane)
  if (pane === 'terminal') void nextTick(() => refs.get(flowId)?.reveal())
}
/** [아래에 붙이기] (true) / [떼기] (false) of a Flow's terminal. */
async function setDocked(id: string, on: boolean) {
  if (on && !props.dockTarget) return
  if (on) dockFlow(dock, id); else undockFlow(dock, id)
  await bringUp(id)
}
/** The Inspector | 터미널 labels of the bottom Dock's switch. */
const dockLabels = () => ({ inspector: w.value.inspectorTab, terminal: w.value.terminalTab, switchLabel: w.value.dockSwitch })

/* ── status of the open Flow (the toolbar button) ── */
let statusSeq = 0
async function refreshStatus() {
  const id = props.flowId
  const seq = ++statusSeq
  if (!id || !isAdminFromToken()) { flowTerminalUi.status = null; return }
  try {
    const status = await readTerminalStatus(id)
    if (alive && seq === statusSeq) flowTerminalUi.status = status
  } catch {
    if (alive && seq === statusSeq) flowTerminalUi.status = null
  }
}

/* ── open / close ── */
const opening = new Set<string>()
/** Opens the open Flow's terminal, or brings its window to the front (and back from minimized). */
async function open() {
  const id = props.flowId
  if (!id) return
  if (windows.value.some(x => x.id === id)) { await bringUp(id); return }
  await openFor(id)
}
async function bringUp(id: string) {
  // Docked: the bottom Dock's 터미널 tab is chosen (0009-T 할 일 7); floating: the window comes to the front.
  if (dockedNow(id)) choosePane(dock, id, 'terminal')
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
function close(id: string) {
  windows.value = windows.value.filter(x => x.id !== id)
  if (focusedId.value === id) focusedId.value = windows.value.at(-1)?.id ?? null
  void refreshStatus()
}
/** [새로 열기] of an ended window: a new session for the same Flow in a new window. */
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
  for (const id of Object.keys(dock.docked)) if (!open.has(id)) forgetFlow(dock, id)
})
watch(() => windows.value.map(x => x.id), ids => {
  flowTerminalUi.openFlows = Object.fromEntries(ids.map(id => [id, true]))
}, { immediate: true })
watch(() => !!props.flowId && windows.value.some(x => x.id === props.flowId), on => { flowTerminalUi.currentOpen = on }, { immediate: true })

/* ── notices ── */
const toasts = ref<ToastHostItem[]>([])
let toastSeq = 0
const FAILURE_WORD: Record<OpenFailure, 'limit' | 'exists' | 'notAdmin' | 'disabled' | 'noCwd' | 'failed'> = {
  limit: 'limit', exists: 'exists', not_admin: 'notAdmin', disabled: 'disabled', no_cwd: 'noCwd', failed: 'failed',
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
defineExpose({ open, close, refreshStatus, bottomPane, selectBottomPane, setDocked, dockLabels })
</script>
