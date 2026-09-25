<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="panelRef"
      class="ui-modeless-panel"
      :class="{ 'is-minimized': minimized }"
      :style="panelStyle"
      tabindex="-1"
      @mousedown="bringToFront"
      @keydown.esc="onEscape"
    >
      <div class="ui-modeless-titlebar" @mousedown="onTitlebarMouseDown">
        <span class="ui-modeless-title">
          <slot name="header">{{ title }}</slot>
        </span>
        <span class="ui-modeless-titlebar-actions">
          <IconButton
            :label="minimized ? 'Restore' : 'Minimize'"
            variant="ghost"
            size="sm"
            @mousedown.stop
            @click="minimized = !minimized"
          >
            <span aria-hidden="true">{{ minimized ? '□' : '−' }}</span>
          </IconButton>
          <IconButton label="Close" variant="ghost" size="sm" @mousedown.stop @click="emit('close')">
            <span aria-hidden="true">{{ '×' }}</span>
          </IconButton>
        </span>
      </div>
      <div v-show="!minimized" class="ui-modeless-body">
        <slot />
      </div>
      <div
        v-if="resizable && !minimized"
        class="ui-modeless-resize-handle"
        @mousedown.stop="onResizeMouseDown"
      />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free non-blocking overlay shell (T0006 §6). Coexists with the base screen —
// no backdrop, no focus trap, background stays fully interactive. Owns only position/
// size/minimized/open-close/drag-resize lifecycle; it knows nothing about Git branches,
// AI runs, Projects, Documents or Approvals (T0006 §6/§15).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import IconButton from './IconButton.vue'
import { allocateOverlayZIndex } from '../../core/overlay/zIndex'
import { captureFocusedElement, returnFocusTo } from '../../core/overlay/focusReturn'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    initialX?: number
    initialY?: number
    initialWidth?: number
    initialHeight?: number
    minWidth?: number
    minHeight?: number
    draggable?: boolean
    resizable?: boolean
    closeOnEscape?: boolean
  }>(),
  {
    title: '',
    initialX: 120,
    initialY: 120,
    initialWidth: 420,
    initialHeight: 320,
    minWidth: 240,
    minHeight: 160,
    draggable: true,
    resizable: true,
    closeOnEscape: true,
  },
)

const emit = defineEmits<{ close: [] }>()

const panelRef = ref<HTMLElement | null>(null)
const x = ref(props.initialX)
const y = ref(props.initialY)
const width = ref(props.initialWidth)
const height = ref(props.initialHeight)
const minimized = ref(false)
const zIndex = ref(0)

// Keeps at least this many px of the titlebar reachable so a panel dragged toward an
// edge can never fully leave the viewport (T0006 §6 "viewport 밖으로 완전히 사라지지
// 않도록 최소 제약").
const MIN_VISIBLE = 80

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function clampPosition(): void {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  x.value = clamp(x.value, -(width.value - MIN_VISIBLE), Math.max(0, viewportWidth - MIN_VISIBLE))
  y.value = clamp(y.value, 0, Math.max(0, viewportHeight - MIN_VISIBLE))
}

const panelStyle = computed(() => ({
  left: `${x.value}px`,
  top: `${y.value}px`,
  width: `${width.value}px`,
  height: minimized.value ? 'auto' : `${height.value}px`,
  zIndex: zIndex.value,
}))

function bringToFront(): void {
  zIndex.value = allocateOverlayZIndex()
}

/* ──────────────────────────────── drag ──────────────────────────────────── */

let dragging = false
let dragStartX = 0
let dragStartY = 0
let originX = 0
let originY = 0

function onTitlebarMouseDown(event: MouseEvent): void {
  bringToFront()
  if (!props.draggable) return
  dragging = true
  dragStartX = event.clientX
  dragStartY = event.clientY
  originX = x.value
  originY = y.value
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', onDragEnd)
}

function onDragMove(event: MouseEvent): void {
  if (!dragging) return
  x.value = originX + (event.clientX - dragStartX)
  y.value = originY + (event.clientY - dragStartY)
  clampPosition()
}

function onDragEnd(): void {
  dragging = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
}

/* ─────────────────────────────── resize ─────────────────────────────────── */

let resizing = false
let resizeStartX = 0
let resizeStartY = 0
let originWidth = 0
let originHeight = 0

function onResizeMouseDown(event: MouseEvent): void {
  if (!props.resizable) return
  bringToFront()
  resizing = true
  resizeStartX = event.clientX
  resizeStartY = event.clientY
  originWidth = width.value
  originHeight = height.value
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
}

function onResizeMove(event: MouseEvent): void {
  if (!resizing) return
  width.value = Math.max(props.minWidth, originWidth + (event.clientX - resizeStartX))
  height.value = Math.max(props.minHeight, originHeight + (event.clientY - resizeStartY))
  clampPosition()
}

function onResizeEnd(): void {
  resizing = false
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
}

/* ────────────────────────────────── ESC ─────────────────────────────────── */
// A separate contract from ModalShell (T0006 §6): this listens on the panel itself,
// not on `document`, so ESC only closes the panel while focus is inside it and never
// while the user is typing somewhere else on the still-interactive base screen.
function onEscape(): void {
  if (!props.closeOnEscape) return
  emit('close')
}

/* ───────────────────────────── open / close ─────────────────────────────── */
// No focus trap and no forced initial focus (T0006 §6 "focus trap 금지" /
// "focus 정책 명확화"): opening never steals focus from whatever the user is doing on
// the base screen. Closing returns focus to the trigger only as a courtesy, and only
// if focus was still inside this panel — if the user had already moved on, forcing
// focus back to the trigger would undo their own navigation.
let triggerElement: HTMLElement | null = null

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      triggerElement = captureFocusedElement()
      zIndex.value = allocateOverlayZIndex()
      // `initialX`/`initialY` are the caller's values, unchecked against the current
      // viewport — clamp immediately on open, not just once a drag/resize happens,
      // otherwise a panel opened with an off-screen initial position is invisible from
      // the start.
      clampPosition()
      window.addEventListener('resize', clampPosition)
    } else if (!isOpen && wasOpen) {
      window.removeEventListener('resize', clampPosition)
      const active = document.activeElement
      const focusWasInside = panelRef.value != null && active instanceof Node && panelRef.value.contains(active)
      if (focusWasInside) returnFocusTo(triggerElement)
      triggerElement = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', clampPosition)
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
})
</script>

<style scoped>
.ui-modeless-panel {
  position: fixed;
  display: flex;
  flex-direction: column;
  background: var(--fg2-panel);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-overlay-radius);
  box-shadow: var(--fg2-overlay-shadow);
  overflow: hidden;
  outline: none;
}

.ui-modeless-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 8px 8px 14px;
  border-bottom: 1px solid var(--fg2-line);
  cursor: move;
  user-select: none;
  flex: none;
}

.is-minimized .ui-modeless-titlebar {
  border-bottom: none;
}

.ui-modeless-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fg2-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-modeless-titlebar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.ui-modeless-body {
  flex: 1;
  overflow: auto;
  padding: 12px 14px;
}

.ui-modeless-resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
}
</style>
