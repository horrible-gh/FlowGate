<template>
  <!-- flowgate.v02.0026 — Flow Tabs strip (0003-D §3.1.3). Stateless: shows the open tabs and
       raises activate / close requests. ui/Tabs cannot hold a close button per tab, so this
       follows the same tab pattern (tablist, aria-selected, roving tabindex, arrow keys with
       wrap-around and focus following) with [tab button][close button] side by side — never a
       button inside a button. Overflowing tabs scroll sideways inside the strip only.
       flowgate.v02.0060 T#2: inside the Designer a right click on a tab (that tab's id — it is not activated) or on the
       strip's blank part goes to the Designer context menu (닫기 / 다른 탭 닫기 / 전체 닫기).
       flowgate.v02gui.0001: a tab dragged sideways (primary pointer, past a few px — a shorter press stays a click; the
       close button never starts a drag) or moved with Alt+←/→ raises `reorder` (its final index); a drop line shows
       where it lands. A tab whose Flow's run is running / paused / stopping shows that state's mark before its name. -->
  <div
    ref="stripRef"
    class="flow-tab-strip"
    :class="{ 'is-reordering': drag.active }"
    role="tablist"
    :aria-label="t('gui.designer.center.tabs_label')"
    data-test="designer-flow-tabs"
    @contextmenu="contextMenu?.tab($event, null)"
  >
    <div
      v-for="tab in tabs"
      :key="tab.flowId"
      :ref="(el) => setTabRef(tab.flowId, el)"
      class="flow-tab"
      :class="{
        'is-active': tab.flowId === activeFlowId,
        'is-dragging': drag.active && drag.flowId === tab.flowId,
        'is-drop-before': dropMark?.flowId === tab.flowId && dropMark.side === 'before',
        'is-drop-after': dropMark?.flowId === tab.flowId && dropMark.side === 'after',
      }"
      role="presentation"
      data-test="designer-flow-tab"
      :data-flow-id="tab.flowId"
      @pointerdown="onPointerDown($event, tab.flowId)"
      @contextmenu="contextMenu?.tab($event, tab.flowId, buttonRefs.get(tab.flowId) ?? null)"
    >
      <button
        :ref="(el) => setButtonRef(tab.flowId, el)"
        type="button"
        role="tab"
        class="flow-tab-button"
        :aria-selected="tab.flowId === activeFlowId"
        :tabindex="tab.flowId === activeFlowId ? 0 : -1"
        :title="tab.name"
        data-test="designer-flow-tab-button"
        @click="onClick(tab.flowId)"
        @keydown="onKeydown($event, tab.flowId)"
      >
        <span
          v-if="runStateOf(tab.flowId)"
          class="flow-tab-run"
          :class="`is-${runStateOf(tab.flowId)}`"
          role="img"
          :aria-label="t(`gui.designer.center.tab_run.${runStateOf(tab.flowId)}`)"
          :title="t(`gui.designer.center.tab_run.${runStateOf(tab.flowId)}`)"
          data-test="designer-flow-tab-run"
          :data-run-status="runStateOf(tab.flowId)"
        />{{ tab.name }}
      </button>
      <IconButton
        class="flow-tab-close"
        size="sm"
        :label="t('gui.designer.center.close_tab', { name: tab.name })"
        :title="t('gui.designer.center.close_tab', { name: tab.name })"
        data-test="designer-flow-tab-close"
        @click="emit('close', tab.flowId)"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </IconButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'

export interface FlowTabStripItem {
  readonly flowId: string
  readonly name: string
}

/** flowgate.v02gui.0001: the run states a tab marks (the run lock's statuses, each with its own mark). */
export type FlowTabRunState = 'running' | 'paused' | 'stopping'

const props = withDefaults(
  defineProps<{
    tabs: readonly FlowTabStripItem[]
    activeFlowId: string | null
    /** flowgate.v02gui.0001: a tab's run state by flow id; a tab not in it (or unknown) shows no mark. */
    runStates?: Readonly<Record<string, FlowTabRunState | undefined>>
  }>(),
  { runStates: () => ({}) },
)

const emit = defineEmits<{
  activate: [flowId: string]
  close: [flowId: string]
  /** flowgate.v02gui.0001: move the tab so that it ends at `toIndex` in screen order. */
  reorder: [flowId: string, toIndex: number]
}>()

const { t } = useI18n()

/** flowgate.v02.0060 T#2: the Designer context menu's tab side, provided by the Designer Shell; null elsewhere. */
interface TabContextMenu {
  tab(event: MouseEvent, flowId: string | null, fallback?: HTMLElement | null): boolean
}
const contextMenu = inject<TabContextMenu | null>(Symbol.for('flowgate.designer.contextMenu'), null)

const stripRef = ref<HTMLElement | null>(null)
const buttonRefs = new Map<string, HTMLButtonElement>()
const tabRefs = new Map<string, HTMLElement>()

function setButtonRef(flowId: string, el: unknown) {
  if (el) buttonRefs.set(flowId, el as HTMLButtonElement)
  else buttonRefs.delete(flowId)
}

function setTabRef(flowId: string, el: unknown) {
  if (el) tabRefs.set(flowId, el as HTMLElement)
  else tabRefs.delete(flowId)
}

function runStateOf(flowId: string): FlowTabRunState | null {
  return props.runStates[flowId] ?? null
}

function focusTab(flowId: string) {
  nextTick(() => buttonRefs.get(flowId)?.focus())
}

function onKeydown(event: KeyboardEvent, flowId: string) {
  if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
  event.preventDefault()
  const count = props.tabs.length
  const index = props.tabs.findIndex((tab) => tab.flowId === flowId)
  if (index === -1 || count === 0) return
  const step = event.key === 'ArrowRight' ? 1 : -1
  // flowgate.v02gui.0001: Alt+←/→ moves the tab itself one place (no wrap-around); focus stays on it.
  if (event.altKey) {
    const to = index + step
    if (to < 0 || to >= count) return
    emit('reorder', flowId, to)
    focusTab(flowId)
    return
  }
  const next = props.tabs[(index + step + count) % count]
  emit('activate', next.flowId)
  focusTab(next.flowId)
}

/* ─────────────────────── flowgate.v02gui.0001: drag to reorder ─────────────────────── */

/** A press moving less than this stays a click. */
const DRAG_THRESHOLD = 4
/** Near the strip's ends a drag scrolls the strip by this step. */
const EDGE_ZONE = 24
const EDGE_STEP = 12

const drag = reactive({ flowId: null as string | null, pointerId: -1, startX: 0, active: false, toIndex: -1 })
let suppressClick = false
let suppressTimer: ReturnType<typeof setTimeout> | null = null

/** Where the dragged tab would land: a line before / after one of the other tabs; null while it would not move. */
const dropMark = computed<{ flowId: string; side: 'before' | 'after' } | null>(() => {
  if (!drag.active || drag.flowId === null) return null
  const from = props.tabs.findIndex((tab) => tab.flowId === drag.flowId)
  if (from === -1 || drag.toIndex === -1 || drag.toIndex === from) return null
  const others = props.tabs.filter((tab) => tab.flowId !== drag.flowId)
  if (others.length === 0) return null
  return drag.toIndex < others.length
    ? { flowId: others[drag.toIndex].flowId, side: 'before' }
    : { flowId: others[others.length - 1].flowId, side: 'after' }
})

function onPointerDown(event: PointerEvent, flowId: string) {
  if (event.button !== 0 || !event.isPrimary || drag.flowId !== null) return
  // The close button closes; it never starts a drag.
  if ((event.target as Element | null)?.closest?.('.flow-tab-close')) return
  drag.flowId = flowId
  drag.pointerId = event.pointerId
  drag.startX = event.clientX
  drag.active = false
  drag.toIndex = -1
  document.addEventListener('pointermove', onPointerMove)
  document.addEventListener('pointerup', onPointerUp)
  document.addEventListener('pointercancel', onPointerCancel)
}

/** The final index of the dragged tab for a pointer at `x`: the number of other tabs whose middle lies left of it. */
function dropIndexAt(x: number): number {
  let index = 0
  for (const tab of props.tabs) {
    if (tab.flowId === drag.flowId) continue
    const rect = tabRefs.get(tab.flowId)?.getBoundingClientRect()
    if (rect && x > rect.left + rect.width / 2) index += 1
  }
  return index
}

function scrollNearEdge(x: number) {
  const strip = stripRef.value
  if (!strip) return
  const rect = strip.getBoundingClientRect()
  if (x < rect.left + EDGE_ZONE) strip.scrollLeft -= EDGE_STEP
  else if (x > rect.right - EDGE_ZONE) strip.scrollLeft += EDGE_STEP
}

function onPointerMove(event: PointerEvent) {
  if (drag.flowId === null || event.pointerId !== drag.pointerId) return
  if (!drag.active) {
    if (Math.abs(event.clientX - drag.startX) < DRAG_THRESHOLD) return
    drag.active = true
  }
  event.preventDefault()
  scrollNearEdge(event.clientX)
  drag.toIndex = dropIndexAt(event.clientX)
}

function onPointerUp(event: PointerEvent) {
  if (drag.flowId === null || event.pointerId !== drag.pointerId) return
  const { flowId, active, toIndex } = drag
  endDrag()
  if (!active) return
  // The release ends a drag, not a click on the tab under it.
  suppressClick = true
  if (suppressTimer !== null) clearTimeout(suppressTimer)
  suppressTimer = setTimeout(() => { suppressClick = false; suppressTimer = null }, 0)
  const from = props.tabs.findIndex((tab) => tab.flowId === flowId)
  if (flowId !== null && from !== -1 && toIndex !== -1 && toIndex !== from) emit('reorder', flowId, toIndex)
}

function onPointerCancel(event: PointerEvent) {
  if (event.pointerId === drag.pointerId) endDrag()
}

function endDrag() {
  drag.flowId = null
  drag.pointerId = -1
  drag.active = false
  drag.toIndex = -1
  document.removeEventListener('pointermove', onPointerMove)
  document.removeEventListener('pointerup', onPointerUp)
  document.removeEventListener('pointercancel', onPointerCancel)
}

function onClick(flowId: string) {
  if (suppressClick) return
  emit('activate', flowId)
}

// A tab closed while it is being dragged ends the drag.
watch(
  () => props.tabs.map((tab) => tab.flowId).join('\n'),
  () => {
    if (drag.flowId !== null && !props.tabs.some((tab) => tab.flowId === drag.flowId)) endDrag()
  },
)

onBeforeUnmount(() => {
  endDrag()
  if (suppressTimer !== null) clearTimeout(suppressTimer)
})

// A newly active tab is scrolled into view inside the strip.
watch(
  () => props.activeFlowId,
  (flowId) => {
    if (flowId === null) return
    nextTick(() => buttonRefs.get(flowId)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }))
  },
)
</script>

<style scoped>
.flow-tab-strip {
  display: flex;
  flex: none;
  align-items: stretch;
  gap: 2px;
  min-width: 0;
  height: 36px;
  padding: 0 6px;
  overflow-x: auto;
  overflow-y: hidden;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-panel);
  scrollbar-width: thin;
}

.flow-tab-strip.is-reordering {
  cursor: grabbing;
  user-select: none;
}

.flow-tab-strip.is-reordering .flow-tab-button {
  cursor: grabbing;
}

.flow-tab {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
  max-width: 220px;
  padding: 0 4px 0 2px;
  border-bottom: 2px solid transparent;
  color: var(--fg2-muted);
}

.flow-tab:hover {
  color: var(--fg2-text);
}

.flow-tab.is-active {
  border-bottom-color: var(--fg2-blue);
  background: rgba(75, 163, 255, 0.08);
  color: var(--fg2-text);
}

/* flowgate.v02gui.0001: the dragged tab is dimmed; a line marks where it lands. */
.flow-tab.is-dragging {
  opacity: 0.55;
}

.flow-tab.is-drop-before::before,
.flow-tab.is-drop-after::after {
  content: '';
  position: absolute;
  top: 5px;
  bottom: 3px;
  width: 2px;
  border-radius: 1px;
  background: var(--fg2-blue);
  pointer-events: none;
}

.flow-tab.is-drop-before::before {
  left: -2px;
}

.flow-tab.is-drop-after::after {
  right: -2px;
}

.flow-tab-button {
  min-width: 0;
  padding: 0 6px 0 10px;
  overflow: hidden;
  border: 0;
  background: none;
  color: inherit;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 32px;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.flow-tab-button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

/* flowgate.v02gui.0001: the run mark — running spins, stopping spins dimmed, paused shows two bars. */
.flow-tab-run {
  display: inline-block;
  box-sizing: border-box;
  width: 10px;
  height: 10px;
  margin-right: 6px;
  vertical-align: -1px;
}

.flow-tab-run.is-running,
.flow-tab-run.is-stopping {
  border: 1.6px solid rgba(139, 181, 239, 0.25);
  border-top-color: var(--fg2-blue, #8bb5ef);
  border-radius: 50%;
  animation: flow-tab-spin 0.8s linear infinite;
}

.flow-tab-run.is-stopping {
  border-top-color: var(--fg2-muted);
  opacity: 0.6;
  animation-duration: 1.6s;
}

.flow-tab-run.is-paused {
  width: 8px;
  border-left: 2.5px solid var(--fg2-status-warning-fg, #e3b341);
  border-right: 2.5px solid var(--fg2-status-warning-fg, #e3b341);
}

@keyframes flow-tab-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .flow-tab-run.is-running,
  .flow-tab-run.is-stopping {
    animation: none;
  }
}

.flow-tab-close {
  width: 22px;
  height: 22px;
}
</style>
