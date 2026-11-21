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
       where it lands; held near the strip's ends it keeps scrolling the strip (a requestAnimationFrame loop) until
       moved away. A tab whose Flow's run is running / paused / stopping shows that state's mark before its name.
       flowgate.v02gui.0023: the strip never shows a scrollbar (it used to take its height out of the 36px and squash the
       tabs). The wheel scrolls it sideways, and only while the tabs overflow a list button (VS Code style) sits at the
       right end, outside the tablist: it opens every open Flow in tab order (ui/Dropdown); picking one activates it.
       flowgate.v02test.0035: an archived Flow's tab is dimmed like its Explorer row (0.5; 0.75 while active) with the
       archive icon before its name. A run that ended while its tab was not active leaves its result mark where the run
       mark was (the run band's tone: success ✓ / warning ! / danger ! / neutral ✓); a chat answer that ended so keeps the
       bubble with a blue dot (orange: failed). The parent clears those marks when the tab is activated; a tab hidden by
       the overflow that holds one puts a dot on the list button. -->
  <div ref="barRef" class="flow-tab-bar" data-test="designer-flow-tab-bar">
  <div
    ref="stripRef"
    class="flow-tab-strip"
    :class="{ 'is-reordering': drag.active }"
    role="tablist"
    :aria-label="t('gui.designer.center.tabs_label')"
    data-test="designer-flow-tabs"
    @contextmenu="contextMenu?.tab($event, null)"
    @scroll.passive="updateOverflow"
    @wheel="onWheel"
  >
    <div
      v-for="tab in tabs"
      :key="tab.flowId"
      :ref="(el) => setTabRef(tab.flowId, el)"
      class="flow-tab"
      :class="{
        'is-active': tab.flowId === activeFlowId,
        'is-archived': isArchived(tab.flowId),
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
          v-if="isArchived(tab.flowId)"
          class="flow-tab-archive"
          role="img"
          :aria-label="t('gui.designer.center.tab_archived')"
          :title="t('gui.designer.center.tab_archived')"
          data-test="designer-flow-tab-archived"
        ><LineIcon name="archive" /></span>
        <span
          v-if="runStateOf(tab.flowId)"
          class="flow-tab-run"
          :class="`is-${runStateOf(tab.flowId)}`"
          role="img"
          :aria-label="t(`gui.designer.center.tab_run.${runStateOf(tab.flowId)}`)"
          :title="t(`gui.designer.center.tab_run.${runStateOf(tab.flowId)}`)"
          data-test="designer-flow-tab-run"
          :data-run-status="runStateOf(tab.flowId)"
        />
        <span
          v-else-if="runEndOf(tab.flowId)"
          class="flow-tab-end"
          :class="`is-${runEndOf(tab.flowId)!.tone}`"
          role="img"
          :aria-label="runEndLabel(tab.flowId)"
          :title="runEndLabel(tab.flowId)"
          data-test="designer-flow-tab-run-end"
          :data-tone="runEndOf(tab.flowId)!.tone"
        ><svg v-if="isCheckTone(runEndOf(tab.flowId)!.tone)" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5.2" fill="none" stroke="currentColor" stroke-width="1.3" /><path d="M3.6 6.2l1.6 1.6 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg><svg v-else viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5.6" fill="currentColor" /><path d="M6 3.2v3.4" stroke="var(--fg2-panel, #0d1a28)" stroke-width="1.5" stroke-linecap="round" /><circle cx="6" cy="8.7" r="0.85" fill="var(--fg2-panel, #0d1a28)" /></svg></span>
        <span v-if="conflicts?.[tab.flowId]" class="flow-tab-conflict" role="img"
          :aria-label="t('gui.designer.center.integration.conflict')" :title="t('gui.designer.center.integration.conflict')"
          data-test="designer-flow-tab-conflict"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="HEADER_GLYPHS.conflict" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
        <!-- flowgate.v02gui.0020: the right dock's '대화' icon itself, at its 14px size, never rotating. -->
        <span v-if="chatStates?.[tab.flowId]" class="flow-tab-chat" role="img" :aria-label="t('gui.designer.center.tab_chat')"
          :title="t('gui.designer.center.tab_chat')" data-test="designer-flow-tab-chat"><ChatBubbleIcon :size="14" /></span>
        <span v-else-if="chatEndOf(tab.flowId)" class="flow-tab-chat" :class="`is-${chatEndOf(tab.flowId)}`" role="img"
          :aria-label="t(`gui.designer.center.tab_chat_end.${chatEndOf(tab.flowId)}`)" :title="t(`gui.designer.center.tab_chat_end.${chatEndOf(tab.flowId)}`)"
          data-test="designer-flow-tab-chat-end" :data-chat-end="chatEndOf(tab.flowId)"><ChatBubbleIcon :size="14" /></span>{{ tab.name }}
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
  <div v-if="overflowing" class="flow-tab-list-slot">
    <Dropdown ref="listRef">
      <template #trigger="{ toggle, triggerAttrs }">
        <IconButton
          class="flow-tab-list-button"
          :class="{ 'has-mark': hiddenMark }"
          size="sm"
          :label="t(hiddenMark ? 'gui.designer.center.open_tabs_list_marked' : 'gui.designer.center.open_tabs_list')"
          :title="t(hiddenMark ? 'gui.designer.center.open_tabs_list_marked' : 'gui.designer.center.open_tabs_list')"
          v-bind="triggerAttrs"
          data-test="designer-flow-tab-list-button"
          :data-has-mark="hiddenMark ? 'true' : undefined"
          @click="toggle"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M4 6.25l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </IconButton>
      </template>
      <template #default="{ close }">
        <div class="flow-tab-list" data-test="designer-flow-tab-list">
          <div class="flow-tab-list-head" aria-hidden="true">{{ t('gui.designer.center.open_tabs_count', { count: tabs.length }) }}</div>
          <div ref="listItemsRef" class="flow-tab-list-items" @keydown="onListKeydown">
            <button
              v-for="tab in tabs"
              :key="tab.flowId"
              type="button"
              role="menuitemradio"
              class="flow-tab-list-item"
              :class="{ 'is-active': tab.flowId === activeFlowId, 'is-archived': isArchived(tab.flowId) }"
              :aria-checked="tab.flowId === activeFlowId ? 'true' : 'false'"
              :title="tab.name"
              data-test="designer-flow-tab-list-item"
              :data-flow-id="tab.flowId"
              @click="pickFromList(tab.flowId, close)"
            >
              <span class="flow-tab-list-check" aria-hidden="true">
                <svg v-if="tab.flowId === activeFlowId" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </span>
              <span v-if="isArchived(tab.flowId)" class="flow-tab-archive" role="img" :aria-label="t('gui.designer.center.tab_archived')"
                data-test="designer-flow-tab-list-archived"><LineIcon name="archive" /></span>
              <span
                v-if="runStateOf(tab.flowId)"
                class="flow-tab-run"
                :class="`is-${runStateOf(tab.flowId)}`"
                role="img"
                :aria-label="t(`gui.designer.center.tab_run.${runStateOf(tab.flowId)}`)"
              />
              <span
                v-else-if="runEndOf(tab.flowId)"
                class="flow-tab-end"
                :class="`is-${runEndOf(tab.flowId)!.tone}`"
                role="img"
                :aria-label="runEndLabel(tab.flowId)"
                data-test="designer-flow-tab-list-run-end"
                :data-tone="runEndOf(tab.flowId)!.tone"
              ><svg v-if="isCheckTone(runEndOf(tab.flowId)!.tone)" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5.2" fill="none" stroke="currentColor" stroke-width="1.3" /><path d="M3.6 6.2l1.6 1.6 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg><svg v-else viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5.6" fill="currentColor" /><path d="M6 3.2v3.4" stroke="var(--fg2-panel, #0d1a28)" stroke-width="1.5" stroke-linecap="round" /><circle cx="6" cy="8.7" r="0.85" fill="var(--fg2-panel, #0d1a28)" /></svg></span>
              <span v-if="chatStates?.[tab.flowId]" class="flow-tab-chat" role="img" :aria-label="t('gui.designer.center.tab_chat')"><ChatBubbleIcon :size="14" /></span>
              <span v-else-if="chatEndOf(tab.flowId)" class="flow-tab-chat" :class="`is-${chatEndOf(tab.flowId)}`" role="img"
                :aria-label="t(`gui.designer.center.tab_chat_end.${chatEndOf(tab.flowId)}`)" data-test="designer-flow-tab-list-chat-end"
                :data-chat-end="chatEndOf(tab.flowId)"><ChatBubbleIcon :size="14" /></span>
              <span v-if="conflicts?.[tab.flowId]" class="flow-tab-conflict" role="img"
          :aria-label="t('gui.designer.center.integration.conflict')" :title="t('gui.designer.center.integration.conflict')"
          data-test="designer-flow-tab-list-conflict"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="HEADER_GLYPHS.conflict" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
        <span class="flow-tab-list-name">{{ tab.name }}</span>
            </button>
          </div>
        </div>
      </template>
    </Dropdown>
  </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'
import ChatBubbleIcon from '../../../components/ui/ChatBubbleIcon.vue'
import Dropdown from '../../../components/ui/Dropdown.vue'
import LineIcon from '../../../components/ui/LineIcon.vue'
import { HEADER_GLYPHS } from './canvas/flowNodeKinds'
import type { FlowTabChatEnd, FlowTabRunEnd, FlowTabRunEndTone } from './flowTabMarks'

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
    conflicts?: Readonly<Record<string, boolean | undefined>>
    chatStates?: Readonly<Record<string, boolean>>
    runStates?: Readonly<Record<string, FlowTabRunState | undefined>>
    /** flowgate.v02test.0035: Flows shown as archived (dimmed, archive icon); absent / false = not archived. */
    archived?: Readonly<Record<string, boolean | undefined>>
    /** flowgate.v02test.0035: a run that ended while its tab was not active (shown while no run mark is). */
    runEnds?: Readonly<Record<string, FlowTabRunEnd | undefined>>
    /** flowgate.v02test.0035: a chat answer that ended while its tab was not active (shown while it is not answering). */
    chatEnds?: Readonly<Record<string, FlowTabChatEnd | undefined>>
  }>(),
  { runStates: () => ({}), archived: () => ({}), runEnds: () => ({}), chatEnds: () => ({}) },
)

const emit = defineEmits<{
  activate: [flowId: string]
  close: [flowId: string]
  /** flowgate.v02gui.0001: move the tab so that it ends at `toIndex` in screen order. */
  reorder: [flowId: string, toIndex: number]
}>()

const { t, te } = useI18n()

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

/* ───────────── flowgate.v02test.0035: archived tabs, ended run / chat marks ───────────── */

function isArchived(flowId: string): boolean {
  return props.archived[flowId] === true
}

function runEndOf(flowId: string): FlowTabRunEnd | null {
  return props.runEnds[flowId] ?? null
}

function chatEndOf(flowId: string): FlowTabChatEnd | null {
  return props.chatStates?.[flowId] ? null : props.chatEnds[flowId] ?? null
}

/** ✓ for a completed / neutral end, ! for one that needs a look (warning / danger). */
function isCheckTone(tone: FlowTabRunEndTone): boolean {
  return tone === 'success' || tone === 'neutral'
}

/** The result's name: 실행 완료, else the run band's idle reason words (개입 필요, 오류로 멈춤 …), else 실행 끝남. */
function runEndLabel(flowId: string): string {
  const reason = runEndOf(flowId)?.reason ?? ''
  if (reason === 'completed') return t('gui.designer.center.tab_run_end.completed')
  const key = `gui.designer.top.run_idle_reasons.${reason}`
  return reason && te(key) ? t(key) : t('gui.designer.center.tab_run_end.ended')
}

/** A tab with an ended run / chat mark to look at (the active tab never keeps one). */
function hasEndMark(flowId: string): boolean {
  return flowId !== props.activeFlowId && (runEndOf(flowId) !== null || chatEndOf(flowId) !== null)
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
/** Near the strip's ends (within this many px, or past them) a drag keeps scrolling the strip while the pointer rests
 *  there — one requestAnimationFrame loop, not one step per pointermove — faster the closer it is to the end. */
const EDGE_ZONE = 32
/** Edge scroll speed in px/s: at the zone's inner border, and at (or past) the strip's end. */
const EDGE_SPEED_MIN = 120
const EDGE_SPEED_MAX = 900
/** A longer gap between frames (slow frame, hidden page) scrolls only this many ms' worth. */
const EDGE_FRAME_MAX_MS = 50

const drag = reactive({ flowId: null as string | null, pointerId: -1, startX: 0, active: false, toIndex: -1 })
let suppressClick = false
let suppressTimer: ReturnType<typeof setTimeout> | null = null
/** The dragging pointer's last clientX; the edge loop reads it between pointermoves. */
let pointerX = 0
let edgeFrame: number | null = null
let edgeLastTime: number | null = null
/** Sub-pixel scroll not applied yet (scrollLeft may round small steps away on high refresh rates). */
let edgeCarry = 0

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

/** Signed edge scroll speed (px/s) for a pointer at `x`: < 0 near the left end, > 0 near the right end, 0 elsewhere or
 *  when the strip cannot scroll further that way. */
function edgeSpeedAt(x: number): number {
  const strip = stripRef.value
  if (!strip) return 0
  const rect = strip.getBoundingClientRect()
  const zone = Math.min(EDGE_ZONE, rect.width / 3)
  if (zone <= 0) return 0
  const speed = (depth: number) => EDGE_SPEED_MIN + (EDGE_SPEED_MAX - EDGE_SPEED_MIN) * Math.min(1, depth / zone)
  const maxScroll = strip.scrollWidth - strip.clientWidth
  if (x < rect.left + zone && strip.scrollLeft > 0.5) return -speed(rect.left + zone - x)
  if (x > rect.right - zone && strip.scrollLeft < maxScroll - 0.5) return speed(x - (rect.right - zone))
  return 0
}

function edgeTick(time: number) {
  edgeFrame = null
  const strip = stripRef.value
  const speed = drag.active ? edgeSpeedAt(pointerX) : 0
  if (!strip || speed === 0) {
    stopEdgeScroll()
    return
  }
  if (edgeLastTime !== null) {
    edgeCarry += (speed * Math.min(EDGE_FRAME_MAX_MS, Math.max(0, time - edgeLastTime))) / 1000
    const step = Math.trunc(edgeCarry)
    if (step !== 0) {
      edgeCarry -= step
      strip.scrollLeft += step
      // The tabs moved under a resting pointer, so the landing place moves too.
      drag.toIndex = dropIndexAt(pointerX)
    }
  }
  edgeLastTime = time
  edgeFrame = requestAnimationFrame(edgeTick)
}

/** Starts the edge loop when the pointer is in an edge zone; a running loop stops by itself once it leaves. */
function updateEdgeScroll() {
  if (edgeFrame !== null || typeof requestAnimationFrame !== 'function') return
  if (edgeSpeedAt(pointerX) === 0) return
  edgeLastTime = null
  edgeCarry = 0
  edgeFrame = requestAnimationFrame(edgeTick)
}

function stopEdgeScroll() {
  if (edgeFrame !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(edgeFrame)
  edgeFrame = null
  edgeLastTime = null
  edgeCarry = 0
}

function onPointerMove(event: PointerEvent) {
  if (drag.flowId === null || event.pointerId !== drag.pointerId) return
  if (!drag.active) {
    if (Math.abs(event.clientX - drag.startX) < DRAG_THRESHOLD) return
    drag.active = true
  }
  event.preventDefault()
  pointerX = event.clientX
  drag.toIndex = dropIndexAt(pointerX)
  updateEdgeScroll()
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
  stopEdgeScroll()
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
  resizeObserver?.disconnect()
  resizeObserver = null
})

function revealTab(flowId: string | null) {
  if (flowId === null) return
  nextTick(() => buttonRefs.get(flowId)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }))
}

// A newly active tab is scrolled into view inside the strip.
watch(() => props.activeFlowId, revealTab)

/* ─────────────── flowgate.v02gui.0023: hidden scrollbar, wheel, list button ─────────────── */

const barRef = ref<HTMLElement | null>(null)
const listRef = ref<{ close: () => void; isOpen: boolean } | null>(null)
const listItemsRef = ref<HTMLElement | null>(null)
/** The tabs do not fit: the list button shows. Measured against the whole bar (not the strip, which the button itself
 *  narrows), so the button never keeps itself shown once the tabs would fit without it. */
const overflowing = ref(false)
/** flowgate.v02test.0035: a tab scrolled out of the strip's view holds an ended run / chat mark. */
const hiddenMark = ref(false)
let resizeObserver: ResizeObserver | null = null

function updateOverflow() {
  const strip = stripRef.value
  const bar = barRef.value
  if (!strip || !bar) return
  overflowing.value = strip.scrollWidth - bar.clientWidth > 1
  updateHiddenMark()
}

function updateHiddenMark() {
  const strip = stripRef.value
  if (!strip || !overflowing.value) {
    hiddenMark.value = false
    return
  }
  const view = strip.getBoundingClientRect()
  hiddenMark.value = props.tabs.some((tab) => {
    if (!props.conflicts?.[tab.flowId] && !hasEndMark(tab.flowId)) return false
    const rect = tabRefs.get(tab.flowId)?.getBoundingClientRect()
    return !!rect && (rect.left < view.left - 1 || rect.right > view.right + 1)
  })
}

onMounted(() => {
  updateOverflow()
  if (typeof ResizeObserver === 'function') {
    resizeObserver = new ResizeObserver(() => updateOverflow())
    if (barRef.value) resizeObserver.observe(barRef.value)
  }
})

// Tabs opened / closed / renamed, or a run / chat mark that widens a tab, change the content width.
watch(
  () => [props.tabs.map((tab) => `${tab.flowId}\t${tab.name}`).join('\n'), JSON.stringify(props.runStates ?? {}),
    JSON.stringify(props.chatStates ?? {}), JSON.stringify(props.archived ?? {}), JSON.stringify(props.runEnds ?? {}),
    JSON.stringify(props.chatEnds ?? {}), JSON.stringify(props.conflicts ?? {}), props.activeFlowId],
  () => nextTick(updateOverflow),
)

// The button narrows the strip when it appears: keep the active tab in view.
watch(overflowing, () => revealTab(props.activeFlowId))

/** A vertical wheel scrolls the strip sideways while the tabs overflow (there is no scrollbar to grab). */
function onWheel(event: WheelEvent) {
  const strip = stripRef.value
  if (!strip || !overflowing.value) return
  const delta = Math.abs(event.deltaY) > Math.abs(event.deltaX) ? event.deltaY : event.deltaX
  if (delta === 0) return
  event.preventDefault()
  strip.scrollLeft += event.deltaMode === 1 ? delta * 16 : event.deltaMode === 2 ? delta * strip.clientWidth : delta
}

function listItems(): HTMLElement[] {
  return [...(listItemsRef.value?.querySelectorAll<HTMLElement>('[data-test="designer-flow-tab-list-item"]') ?? [])]
}

// The list opens on the active Flow: focus it and scroll it into the list's view (the menu focuses its first item).
watch(
  () => listRef.value?.isOpen === true,
  (open) => {
    if (!open) return
    nextTick(() => {
      const items = listItems()
      const active = items.find((item) => item.dataset.flowId === props.activeFlowId) ?? items[0]
      active?.focus()
      active?.scrollIntoView?.({ block: 'nearest' })
    })
  },
  { flush: 'post' },
)

function onListKeydown(event: KeyboardEvent) {
  const items = listItems()
  if (items.length === 0) return
  const at = items.indexOf(document.activeElement as HTMLElement)
  let next = -1
  if (event.key === 'ArrowDown') next = at < 0 ? 0 : (at + 1) % items.length
  else if (event.key === 'ArrowUp') next = at < 0 ? items.length - 1 : (at - 1 + items.length) % items.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = items.length - 1
  if (next === -1) return
  event.preventDefault()
  items[next].focus()
  items[next].scrollIntoView?.({ block: 'nearest' })
}

function pickFromList(flowId: string, close: () => void) {
  close()
  if (flowId !== props.activeFlowId) emit('activate', flowId)
  else revealTab(flowId)
  focusTab(flowId)
}
</script>

<style scoped>
/* flowgate.v02gui.0023: the bar holds the height; the strip scrolls without a scrollbar, so the tabs keep it. */
.flow-tab-bar {
  display: flex;
  flex: none;
  align-items: stretch;
  min-width: 0;
  height: 36px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-panel);
}

.flow-tab-strip {
  display: flex;
  flex: 1 1 auto;
  align-items: stretch;
  gap: 2px;
  min-width: 0;
  padding: 0 6px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}

.flow-tab-strip::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.flow-tab-list-slot {
  display: flex;
  flex: none;
  align-items: center;
  padding: 0 6px 0 2px;
  border-left: 1px solid var(--fg2-line);
}

.flow-tab-list-button {
  width: 24px;
  height: 24px;
}

.flow-tab-list-button {
  position: relative;
}

/* flowgate.v02test.0035: a hidden tab holds an ended run / chat mark. */
.flow-tab-list-button.has-mark::after {
  content: '';
  position: absolute;
  top: 3px;
  right: 3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg2-blue);
  box-shadow: 0 0 0 1.5px var(--fg2-panel);
  pointer-events: none;
}

.flow-tab-list-button[aria-expanded='true'] {
  color: var(--fg2-text);
  background: rgba(75, 163, 255, 0.12);
}

/* The list is teleported into the ui/Dropdown menu (its 8px corners and strong surface). */
.flow-tab-list {
  display: flex;
  flex-direction: column;
  width: min(320px, 80vw);
  min-width: 220px;
}

.flow-tab-list-head {
  flex: none;
  padding: 4px 8px 6px;
  color: var(--fg2-muted);
  font-size: 11px;
  font-weight: 600;
}

.flow-tab-list-items {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: min(60vh, 360px);
  overflow-y: auto;
  overscroll-behavior: contain;
  /* The app-wide `*` scrollbar-width/color make Chromium ignore ::-webkit-scrollbar (arrowed track): undo them here. */
  scrollbar-width: auto;
  scrollbar-color: auto;
}

.flow-tab-list-items::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.flow-tab-list-items::-webkit-scrollbar-track,
.flow-tab-list-items::-webkit-scrollbar-corner {
  background: transparent;
  border: 0;
}

.flow-tab-list-items::-webkit-scrollbar-button {
  display: none;
  width: 0;
  height: 0;
}

.flow-tab-list-items::-webkit-scrollbar-thumb {
  border: 0;
  border-radius: 999px;
  background: rgba(154, 181, 210, 0.26);
  box-shadow: none;
}

.flow-tab-list-items:hover::-webkit-scrollbar-thumb {
  background: rgba(154, 181, 210, 0.42);
}

@supports (-moz-appearance: none) {
  .flow-tab-list-items {
    scrollbar-width: thin;
    scrollbar-color: rgba(154, 181, 210, 0.26) transparent;
  }
}

.flow-tab-list-item {
  display: flex;
  flex: none;
  align-items: center;
  min-width: 0;
  height: 28px;
  padding: 0 10px 0 4px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: var(--fg2-muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.flow-tab-list-item:hover,
.flow-tab-list-item:focus-visible {
  background: rgba(75, 163, 255, 0.1);
  color: var(--fg2-text);
}

.flow-tab-list-item:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.flow-tab-list-item.is-active {
  color: var(--fg2-text);
}

.flow-tab-list-item.is-archived {
  opacity: 0.5;
}

.flow-tab-list-check {
  display: inline-flex;
  flex: none;
  width: 14px;
  height: 14px;
  margin-right: 6px;
  color: var(--fg2-blue);
}

.flow-tab-list-check svg {
  display: block;
  width: 14px;
  height: 14px;
}

.flow-tab-list-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* flowgate.v02test.0035: an archived Flow's tab is dimmed like its Explorer row; the active one stays readable. */
.flow-tab.is-archived {
  opacity: 0.5;
}

.flow-tab.is-archived.is-active {
  opacity: 0.75;
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
.flow-tab-chat { position: relative; display: inline-flex; width: 14px; height: 14px; margin-right: 6px; vertical-align: -3px; }
.flow-tab-chat :deep(svg) { display: block; }

/* flowgate.v02test.0035: an answer that ended while the tab was not active — blue dot (done), orange dot (failed). */
.flow-tab-chat.is-done::after,
.flow-tab-chat.is-failed::after {
  content: '';
  position: absolute;
  top: -2px;
  right: -3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg2-blue);
  box-shadow: 0 0 0 1.5px var(--fg2-panel);
}

.flow-tab-chat.is-failed::after {
  background: var(--fg2-orange);
}

.flow-tab-conflict {
  display: inline-flex;
  flex: none;
  width: 14px;
  height: 14px;
  margin-right: 6px;
  vertical-align: -3px;
  color: var(--fg2-status-warning-fg, #ffc072);
}
.flow-tab-conflict svg { width: 14px; height: 14px; }

.flow-tab-archive {
  display: inline-flex;
  flex: none;
  width: 12px;
  height: 12px;
  margin-right: 6px;
  color: var(--fg2-muted);
  vertical-align: -2px;
}

.flow-tab-archive :deep(svg) {
  display: block;
  width: 12px;
  height: 12px;
}

/* flowgate.v02test.0035: the ended run's result in the run mark's place and size, coloured like the run band. */
.flow-tab-end {
  display: inline-flex;
  flex: none;
  width: 10px;
  height: 10px;
  margin-right: 6px;
  vertical-align: -1px;
}

.flow-tab-end svg {
  display: block;
  width: 10px;
  height: 10px;
}

.flow-tab-end.is-success { color: var(--fg2-status-success-fg, #4fe0d3); }
.flow-tab-end.is-warning { color: var(--fg2-status-warning-fg, #ffc072); }
.flow-tab-end.is-danger { color: var(--fg2-status-danger-fg, #ff96a6); }
.flow-tab-end.is-neutral { color: var(--fg2-muted); }

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
