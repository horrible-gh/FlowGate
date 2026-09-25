<template>
  <span ref="triggerWrapRef" class="ui-contextmenu-trigger" @contextmenu="onContextMenu">
    <slot />
  </span>
  <Teleport to="body">
    <div
      v-if="isOpen"
      ref="menuRef"
      class="ui-contextmenu-menu"
      role="menu"
      :style="menuStyle"
      :data-overlay-focus-owner="focusOwnerId"
      @keydown="onMenuKeydown"
    >
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        role="menuitem"
        class="ui-contextmenu-item"
        :class="{ 'ui-contextmenu-item--danger': item.danger }"
        :disabled="item.disabled"
        @click="selectItem(item)"
      >
        {{ item.label }}
      </button>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free right-click/keyboard menu (T0010 §8): opens at a pointer position rather
// than relative to a trigger element (that's Dropdown's job, T0010 §8.1), and is invoked
// via the native `contextmenu` event on whatever the caller wraps in the default slot.
// It only ever knows `key`/`label`/`disabled`/`danger` (T0010 §8.3) -- the caller maps a
// selected `key` to whatever Designer/Git/Project action it means via the `select` event;
// this component never hardcodes an action name (T0010 §8.3, §16).
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { allocateOverlayZIndex } from '../../core/overlay/zIndex'
import { captureFocusedElement, returnFocusTo } from '../../core/overlay/focusReturn'

export interface ContextMenuItem {
  key: string
  label: string
  disabled?: boolean
  danger?: boolean
}

defineProps<{
  items: ContextMenuItem[]
}>()

const emit = defineEmits<{
  select: [item: ContextMenuItem]
  open: []
  close: []
}>()

const isOpen = ref(false)
const triggerWrapRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const menuPos = ref({ left: 0, top: 0 })
const zIndex = ref(0)
const focusOwnerId = ref<string | undefined>(undefined)

const menuStyle = computed(() => ({
  position: 'fixed' as const,
  left: `${menuPos.value.left}px`,
  top: `${menuPos.value.top}px`,
  zIndex: zIndex.value,
}))

let triggerElement: HTMLElement | null = null

function positionAt(x: number, y: number): void {
  const menu = menuRef.value
  if (!menu) {
    menuPos.value = { left: x, top: y }
    return
  }
  const rect = menu.getBoundingClientRect()
  const left = rect.width > 0 ? Math.max(0, Math.min(x, window.innerWidth - rect.width)) : x
  const top = rect.height > 0 ? Math.max(0, Math.min(y, window.innerHeight - rect.height)) : y
  menuPos.value = { left, top }
}

function focusFirstItem(): void {
  const first = menuRef.value?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')
  first?.focus()
}

function openAt(x: number, y: number): void {
  // Only capture on the *first* open: if the menu is already open and `openAt` is
  // invoked again (e.g. an imperative reposition from a different trigger while it is
  // still showing, T0010 §8.3), `document.activeElement` is already a menu item that
  // is about to be replaced -- capturing it here would overwrite the real pre-menu
  // focus target with a throwaway one, and `close()` could never return focus to
  // where the user actually was.
  if (!isOpen.value) {
    triggerElement = captureFocusedElement()
    const scope = triggerWrapRef.value?.closest<HTMLElement>('[data-modal-focus-scope]')
      ?? triggerElement?.closest<HTMLElement>('[data-modal-focus-scope]')
    focusOwnerId.value = scope?.dataset.modalFocusScope
  }
  menuPos.value = { left: x, top: y }
  zIndex.value = allocateOverlayZIndex()
  isOpen.value = true
  emit('open')
  void nextTick(() => {
    // The menu can already have been closed again by the time this runs (e.g. an
    // outside click landing in the same tick as open) -- `menuRef` would then be null
    // and writing `menuPos` would race the v-if teardown that already unmounted the
    // Teleport content, so bail out instead of positioning/focusing a closed menu.
    if (!isOpen.value) return
    positionAt(x, y)
    focusFirstItem()
  })
}

function close(): void {
  if (!isOpen.value) return
  isOpen.value = false
  returnFocusTo(triggerElement)
  triggerElement = null
  focusOwnerId.value = undefined
  emit('close')
}

function onContextMenu(event: MouseEvent): void {
  event.preventDefault()
  openAt(event.clientX, event.clientY)
}

function selectItem(item: ContextMenuItem): void {
  if (item.disabled) return
  emit('select', item)
  close()
}

function menuItemEls(): HTMLElement[] {
  return Array.from(menuRef.value?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? [])
}

function onMenuKeydown(event: KeyboardEvent): void {
  const items = menuItemEls()
  if (items.length === 0) return
  const currentIndex = items.indexOf(document.activeElement as HTMLElement)
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    items[(currentIndex + 1 + items.length) % items.length].focus()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    items[(currentIndex - 1 + items.length) % items.length].focus()
  } else if (event.key === 'Home') {
    event.preventDefault()
    items[0].focus()
  } else if (event.key === 'End') {
    event.preventDefault()
    items[items.length - 1].focus()
  }
}

// Document-level listeners mirror Dropdown.vue's pattern (T0004 §5.9): attached for the
// component's lifetime and guarded by `isOpen` inside the handler, rather than added and
// removed on every open/close.
function onDocMousedown(event: MouseEvent): void {
  if (!isOpen.value) return
  const target = event.target as Node
  if (menuRef.value?.contains(target)) return
  close()
}

function onDocKeydown(event: KeyboardEvent): void {
  if (!isOpen.value) return
  if (event.key !== 'Escape') return
  // Registered on the *capture* phase and stopped here rather than relying on
  // registration order: a ContextMenu opened inside a ModalShell (or any future
  // overlay) sits above it in z-index but not necessarily later in mount order, so
  // "whichever component's listener happened to attach first" is not a safe way to
  // decide who owns Escape. Capture-phase listeners on `document` always run before
  // any bubble-phase listener on `document` (including ModalShell's own Escape
  // handler), for any event whose real target is a descendant -- which every keydown
  // is, since it originates at `document.activeElement`. Stopping propagation here
  // keeps the event from ever reaching that bubble-phase handler, so only the menu
  // closes and no ancestor overlay also reacts to the same keypress (T0010 §10).
  event.stopPropagation()
  close()
}

document.addEventListener('mousedown', onDocMousedown)
document.addEventListener('keydown', onDocKeydown, true)

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMousedown)
  document.removeEventListener('keydown', onDocKeydown, true)
})

defineExpose({ openAt, close, isOpen })
</script>

<style scoped>
.ui-contextmenu-trigger {
  display: contents;
}

.ui-contextmenu-menu {
  min-width: 180px;
  padding: 6px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid var(--fg2-line-strong);
  background: var(--fg2-control-surface-strong);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.4);
}

.ui-contextmenu-item {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-control-fg);
  font-size: var(--fg2-control-font-size);
  text-align: left;
  cursor: pointer;
}

.ui-contextmenu-item:hover,
.ui-contextmenu-item:focus-visible {
  background: rgba(124, 192, 255, 0.12);
  outline: none;
}

.ui-contextmenu-item:disabled {
  opacity: var(--fg2-control-disabled-opacity);
  cursor: not-allowed;
}

.ui-contextmenu-item--danger {
  color: var(--fg2-status-danger-fg);
}
</style>
