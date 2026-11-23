<template>
  <Teleport to="body">
    <div
      v-if="open"
      v-show="!(isMinimized && modelessTaskbarHeight > 0)"
      ref="panelRef"
      :data-taskbar-style="modelessTaskbarHeight > 0 ? modelessStyle : undefined"
      class="ui-modeless-panel"
      :class="{ 'is-minimized': isMinimized }"
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
          <slot name="actions" />
          <IconButton
            :label="isMinimized ? restoreName : minimizeName"
            variant="ghost"
            size="sm"
            :data-modeless-action="isMinimized ? 'restore' : 'minimize'"
            @mousedown.stop
            @click="setMinimized(!isMinimized)"
          >
            <span aria-hidden="true">{{ isMinimized ? '□' : '−' }}</span>
          </IconButton>
          <IconButton :label="closeName" variant="ghost" size="sm" data-modeless-action="close" @mousedown.stop @click="emit('close')">
            <span aria-hidden="true">{{ '×' }}</span>
          </IconButton>
        </span>
      </div>
      <div v-show="!isMinimized" class="ui-modeless-body">
        <slot />
      </div>
      <div
        v-if="resizable && !isMinimized"
        class="ui-modeless-resize-handle"
        @mousedown.stop="onResizeMouseDown"
      />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free non-blocking overlay shell. Coexists with the base screen —
// no backdrop, no focus trap, background stays fully interactive. Owns only position/
// size/minimized/open-close/drag-resize lifecycle; it knows nothing about Git branches,
// AI runs, Projects, Documents or Approvals.
import { computed, nextTick, onBeforeUnmount, ref, watch, watchEffect } from 'vue'
import i18n from '@shared/i18n'
import IconButton from './IconButton.vue'
import { modelessStyle, modelessTaskbarHeight, registerModeless, unregisterModeless } from '../../core/overlay/modelessRegistry'
import { allocateOverlayZIndex } from '../../core/overlay/zIndex'
import { captureFocusedElement, returnFocusTo } from '../../core/overlay/focusReturn'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    kindColor?: string
    initialX?: number
    initialY?: number
    initialWidth?: number
    initialHeight?: number
    minWidth?: number
    minHeight?: number
    draggable?: boolean
    resizable?: boolean
    closeOnEscape?: boolean
    // Optional `v-model:minimized`: a caller that owns the minimized
    // state (e.g. a window list shown elsewhere) passes it in and hears every toggle. Left
    // unset, the shell keeps minimized state to itself exactly as before.
    minimized?: boolean
    // explicit accessible names for the shell's own controls. Each one
    // given wins over the default, which follows the app's current language (gui.common.*).
    minimizeLabel?: string
    restoreLabel?: string
    closeLabel?: string
  }>(),
  {
    title: '',
    kindColor: 'var(--fg2-accent, #6478dc)',
    initialX: 120,
    initialY: 120,
    initialWidth: 420,
    initialHeight: 320,
    minWidth: 240,
    minHeight: 160,
    draggable: true,
    resizable: true,
    closeOnEscape: true,
    minimized: undefined,
    minimizeLabel: undefined,
    restoreLabel: undefined,
    closeLabel: undefined,
  },
)

const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()

// the default accessible names read the shared i18n instance directly
// (this shell is domain-free and is mounted with or without the app's i18n plugin), so they
// follow a language switch while the window stays open. The English words remain only as the
// last resort when the app catalog is not registered at all.
function controlName(key: string, last: string): string {
  return i18n.global.te(key) ? String(i18n.global.t(key)) : last
}
const minimizeName = computed(() => props.minimizeLabel ?? controlName('gui.common.minimize', 'Minimize'))
const restoreName = computed(() => props.restoreLabel ?? controlName('gui.common.restore', 'Restore'))
const closeName = computed(() => props.closeLabel ?? controlName('gui.common.close', 'Close'))

const panelRef = ref<HTMLElement | null>(null)
const registryId = Symbol('modeless')
const headerTitle = ref('')
let titleObserver: MutationObserver | null = null
watch(panelRef, (panel) => {
  titleObserver?.disconnect()
  const title = panel?.querySelector('.ui-modeless-title')
  const update = () => { headerTitle.value = title?.textContent?.trim() ?? '' }
  update()
  if (title && typeof MutationObserver !== 'undefined') {
    titleObserver = new MutationObserver(update)
    titleObserver.observe(title, { childList: true, subtree: true, characterData: true })
  }
}, { flush: 'post' })
const x = ref(props.initialX)
const y = ref(props.initialY)
const width = ref(props.initialWidth)
const height = ref(props.initialHeight)
const localMinimized = ref(props.minimized ?? false)
const zIndex = ref(0)

// A controlled `minimized` wins; the local ref only follows it so an uncontrolled shell
// behaves exactly as it always did.
const isMinimized = computed(() => (props.minimized === undefined ? localMinimized.value : props.minimized))

watch(
  () => props.minimized,
  (value) => {
    if (value !== undefined) localMinimized.value = value
  },
)

function setMinimized(value: boolean): void {
  localMinimized.value = value
  emit('update:minimized', value)
}

watchEffect(() => {
  if (!props.open) { unregisterModeless(registryId); return }
  registerModeless({ id: registryId, title: props.title || headerTitle.value || 'Window',
    color: props.kindColor, minimized: isMinimized.value, restore: () => {
      setMinimized(false)
      bringToFront()
      void nextTick(() => panelRef.value?.focus({ preventScroll: true }))
    } })
})
watch(modelessTaskbarHeight, () => { if (props.open) clampPosition() })
watch(isMinimized, (value, old) => { if (old && !value) bringToFront() })

// Keeps at least this many px of the titlebar reachable so a panel dragged toward an
// edge can never fully leave the viewport (the earlier implementation "Minimum constraints to prevent disappearing
// completely outside the viewport").
const MIN_VISIBLE = 80

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function clampPosition(): void {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight - modelessTaskbarHeight.value
  x.value = clamp(x.value, -(width.value - MIN_VISIBLE), Math.max(0, viewportWidth - MIN_VISIBLE))
  y.value = clamp(y.value, 0, Math.max(0, viewportHeight - MIN_VISIBLE))
}

const panelStyle = computed(() => ({
  '--modeless-kind-color': props.kindColor,
  maxHeight: modelessTaskbarHeight.value > 0 ? `calc(100vh - ${y.value + modelessTaskbarHeight.value}px)` : undefined,
  left: `${x.value}px`,
  top: `${y.value}px`,
  width: `${width.value}px`,
  height: isMinimized.value ? 'auto' : `${height.value}px`,
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
// A separate contract from ModalShell: this listens on the panel itself,
// not on `document`, so ESC only closes the panel while focus is inside it and never
// while the user is typing somewhere else on the still-interactive base screen.
function onEscape(): void {
  if (!props.closeOnEscape) return
  emit('close')
}

/* ───────────────────────────── open / close ─────────────────────────────── */
// No focus trap and no forced initial focus (the earlier implementation "No focus trap" /
// "Explicit focus policy"): opening never steals focus from whatever the user is doing on
// the base screen. Closing returns focus to the trigger only as a courtesy, and only
// if focus was still inside this panel — if the user had already moved on, forcing
// focus back to the trigger would undo their own navigation.
let triggerElement: HTMLElement | null = null

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      if (wasOpen === false) setMinimized(false)
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
  unregisterModeless(registryId)
  titleObserver?.disconnect()
  window.removeEventListener('resize', clampPosition)
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
})

// `geometry()` / `setGeometry()` let a caller read and set the panel's place
// and size after it was created (the initial props are read once) — the Git window fits itself to a
// smaller viewport this way without re-creating its body. `setGeometry` takes the size as given, so
// a caller may go below `minWidth` / `minHeight` to stay on a small screen; the position is clamped
// as a drag would be.
function geometry(): { x: number; y: number; width: number; height: number } {
  return { x: x.value, y: y.value, width: width.value, height: height.value }
}

function setGeometry(next: { x: number; y: number; width: number; height: number }): void {
  x.value = next.x
  y.value = next.y
  width.value = next.width
  height.value = next.height
  clampPosition()
}

// the optional `actions` slot puts a caller's own titlebar buttons right before
// minimize / close (the document window's ✎), and `bringToFront()` lets a caller raise a panel that is
// already open (a second request for the same document). Both stay domain-free.
defineExpose({ bringToFront, geometry, setGeometry })
</script>

<style scoped>
.ui-modeless-panel {
  position: fixed;
  box-sizing: border-box;
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

[data-taskbar-style="flowgate"] .ui-modeless-titlebar {
  box-shadow: inset 0 3px 0 var(--modeless-kind-color);
  background: color-mix(in srgb, var(--modeless-kind-color) 8%, var(--fg2-panel));
}

/* Modern: flat title bar, rounder window, no kind stripe; − and × stay on the right. */
.ui-modeless-panel[data-taskbar-style="modern"] {
  border-radius: 10px;
  border-color: var(--fg2-line);
}

[data-taskbar-style="modern"] .ui-modeless-titlebar {
  padding: 6px 6px 6px 14px;
  border-bottom-color: transparent;
  background: color-mix(in srgb, var(--fg2-text) 4%, var(--fg2-panel));
}

[data-taskbar-style="modern"] .ui-modeless-titlebar-actions :deep(button) {
  border-radius: 6px;
}

[data-taskbar-style="modern"] .ui-modeless-titlebar-actions :deep([data-modeless-action="close"]:hover) {
  background: #c42b1c;
  color: #fff;
}

/* Dock: rounder window, centred title, round close / minimize buttons on the left (close first). */
.ui-modeless-panel[data-taskbar-style="dock"] {
  border-radius: 11px;
  box-shadow: 0 22px 50px rgba(0, 0, 0, 0.45);
}

[data-taskbar-style="dock"] .ui-modeless-titlebar {
  flex-direction: row-reverse;
  padding: 6px 10px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--fg2-text) 6%, var(--fg2-panel)), color-mix(in srgb, var(--fg2-text) 2%, var(--fg2-panel)));
}

[data-taskbar-style="dock"] .ui-modeless-title {
  flex: 1;
  min-width: 0;
  text-align: center;
}

[data-taskbar-style="dock"] .ui-modeless-titlebar-actions {
  min-width: 44px;
}

.ui-modeless-panel[data-taskbar-style="dock"] .ui-modeless-titlebar-actions :deep([data-modeless-action]) {
  order: -1;
  width: 13px;
  min-width: 0;
  height: 13px;
  min-height: 0;
  padding: 0;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.3);
  background: #febc2e;
  color: transparent;
  font-size: 9px;
  line-height: 1;
}

.ui-modeless-panel[data-taskbar-style="dock"] .ui-modeless-titlebar-actions :deep([data-modeless-action="close"]) {
  order: -2;
  background: #ff5f57;
}

.ui-modeless-panel[data-taskbar-style="dock"] .ui-modeless-titlebar-actions :deep([data-modeless-action]:hover),
.ui-modeless-panel[data-taskbar-style="dock"] .ui-modeless-titlebar-actions :deep([data-modeless-action]:focus-visible) {
  color: rgba(0, 0, 0, 0.65);
  filter: brightness(1.05);
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

