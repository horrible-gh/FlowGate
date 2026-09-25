<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="overlayRef"
      class="ui-modal-overlay"
      :style="{ zIndex: overlayZIndex }"
      @mousedown="onOverlayMouseDown"
      @mouseup="onOverlayMouseUp"
    >
      <div
        ref="surfaceRef"
        class="ui-modal-surface"
        :class="`ui-modal-surface--${size}`"
        :style="{ zIndex: surfaceZIndex }"
        :data-modal-focus-scope="instanceId"
        :role="role"
        aria-modal="true"
        :aria-label="ariaLabelAttr"
        :aria-labelledby="ariaLabelledbyAttr"
        :aria-describedby="ariaDescribedbyAttr"
        tabindex="-1"
        @keydown="onSurfaceKeydown"
      >
        <slot name="header" :titleId="titleId" :descriptionId="descriptionId" />
        <div class="ui-modal-body">
          <slot :titleId="titleId" :descriptionId="descriptionId" />
        </div>
        <slot name="footer" :titleId="titleId" :descriptionId="descriptionId" />
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free blocking overlay shell (T0006 §5). Owns the overlay lifecycle only —
// backdrop, ESC, focus trap/return, scroll lock, z-index — and never any Git/AI/
// Project/Approval meaning. Feature dialogs compose this via slots (T0006 §4); this T
// also builds ConfirmDialog/AlertDialog on top of it, and does not build Feature
// content itself (T0006 §14).
//
// This never closes itself: ESC and a qualifying backdrop click only emit `close`, and
// the caller decides whether to actually flip `open`. That keeps "how does this modal
// close" answerable in one place instead of drifting per call site.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { allocateOverlayZIndex } from '../../core/overlay/zIndex'
import { captureFocusedElement, returnFocusTo } from '../../core/overlay/focusReturn'
import { lockBodyScroll, unlockBodyScroll } from '../../core/overlay/scrollLock'
import { isTopmostModal, nextModalInstanceId, popOpenModal, pushOpenModal } from '../../core/overlay/modalStack'

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl'
export type ModalCloseReason = 'escape' | 'backdrop'

const props = withDefaults(
  defineProps<{
    open: boolean
    size?: ModalSize
    closeOnEscape?: boolean
    closeOnBackdrop?: boolean
    /** `alertdialog` for AlertDialog (T0006 §8); every other caller keeps `dialog`. */
    role?: 'dialog' | 'alertdialog'
    ariaLabel?: string
    ariaLabelledby?: string
    ariaDescribedby?: string
  }>(),
  {
    size: 'md',
    closeOnEscape: true,
    closeOnBackdrop: false,
    role: 'dialog',
    ariaLabel: undefined,
    ariaLabelledby: undefined,
    ariaDescribedby: undefined,
  },
)

const emit = defineEmits<{ close: [reason: ModalCloseReason] }>()

const instanceId = nextModalInstanceId()
const titleId = `${instanceId}-title`
const descriptionId = `${instanceId}-description`

const overlayRef = ref<HTMLElement | null>(null)
const surfaceRef = ref<HTMLElement | null>(null)
const overlayZIndex = ref(0)
// A real second counter tick, not `overlayZIndex + 1`: the latter only reserves a value
// one-above without ever telling the shared counter it was used, so the very next
// unrelated allocation (e.g. a ContextMenu opened from inside this modal, T0010 §10)
// could land on that same "+1" value and tie with this surface instead of clearing it.
const surfaceZIndex = ref(0)

/* ───────────────────────────────── ARIA ─────────────────────────────────── */
// Detected rather than assumed: a caller that skips the header slot (or renders a
// title without this generated id) must not end up with aria-labelledby pointing at
// nothing, which is worse than no label at all.
const hasTitleEl = ref(false)
const hasDescriptionEl = ref(false)

function refreshAriaTargets(): void {
  const root = surfaceRef.value
  hasTitleEl.value = root != null && root.querySelector(`#${titleId}`) != null
  hasDescriptionEl.value = root != null && root.querySelector(`#${descriptionId}`) != null
}

const ariaLabelledbyAttr = computed(() => props.ariaLabelledby ?? (hasTitleEl.value ? titleId : undefined))
const ariaDescribedbyAttr = computed(() => props.ariaDescribedby ?? (hasDescriptionEl.value ? descriptionId : undefined))
const ariaLabelAttr = computed(() => (hasTitleEl.value ? undefined : props.ariaLabel))

/* ──────────────────────────────── focus ─────────────────────────────────── */

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusableElementsIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

let triggerElement: HTMLElement | null = null

function applyInitialFocus(): void {
  const root = surfaceRef.value
  if (root == null) return
  const explicit = root.querySelector<HTMLElement>('[data-modal-autofocus]')
  const target = explicit ?? focusableElementsIn(root)[0] ?? root
  try {
    target.focus()
  } catch {
    // Best effort; a focus failure must never break opening a modal.
  }
}

function onSurfaceKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  const root = surfaceRef.value
  if (root == null) return
  const focusables = focusableElementsIn(root)
  if (focusables.length === 0) {
    event.preventDefault()
    root.focus()
    return
  }
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  }
}

/** Teleported child overlays are outside the modal surface in the DOM, so containment
 *  alone cannot identify them. They must explicitly carry the focus-scope id of the modal
 *  that opened them. A later, higher-z unrelated overlay (for example ToastHost) has no
 *  such ownership marker and therefore cannot bypass this blocking modal's focus trap. */
function isOwnedChildOverlay(node: Node): boolean {
  let el: HTMLElement | null = node instanceof HTMLElement ? node : node.parentElement
  while (el) {
    if (el.dataset.overlayFocusOwner === instanceId) return true
    el = el.parentElement
  }
  return false
}

/** Pulls focus back inside if it escapes the trap by any means other than Tab (e.g. a
 *  caller's own `.focus()` call on background content). Only the topmost modal in the
 *  open stack does this: with two modals open, a lower one's root never contains focus
 *  (it lives in the topmost one), so without this guard every open modal would fight to
 *  pull focus into itself and ping-pong forever. */
function onDocumentFocusIn(event: FocusEvent): void {
  if (!isTopmostModal(instanceId)) return
  const root = surfaceRef.value
  if (root == null) return
  const target = event.target
  if (target instanceof Node && root.contains(target)) return
  if (target instanceof Node && isOwnedChildOverlay(target)) return
  const fallback = focusableElementsIn(root)[0] ?? root
  try {
    fallback.focus()
  } catch {
    // Best effort.
  }
}

/* ──────────────────────────────── ESC ───────────────────────────────────── */

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (!props.closeOnEscape) return
  // With two modals open, Escape must only close the topmost one — otherwise a single
  // press would emit `close` from every open ModalShell at once.
  if (!isTopmostModal(instanceId)) return
  emit('close', 'escape')
}

/* ─────────────────────────────── backdrop ───────────────────────────────── */
// Press and release must both land on the overlay itself — a text selection that
// starts inside the surface and is released over the backdrop must not close it.
let pressedOnOverlay = false

function onOverlayMouseDown(event: MouseEvent): void {
  pressedOnOverlay = event.target === overlayRef.value
}

function onOverlayMouseUp(event: MouseEvent): void {
  const pressed = pressedOnOverlay
  pressedOnOverlay = false
  if (!pressed) return
  if (event.target !== overlayRef.value) return
  if (!props.closeOnBackdrop) return
  emit('close', 'backdrop')
}

/* ──────────────────────────── open / close lifecycle ────────────────────── */
// Scroll lock is refcounted in core/overlay/scrollLock.ts (real module scope, see the
// import above): more than one ModalShell can be open at once (T0006 §5 "중첩
// 가능성"), and the lock must only lift once the last one closes, regardless of which
// one opened or closed first.

let listenersAttached = false

function attachListeners(): void {
  if (listenersAttached) return
  document.addEventListener('keydown', onDocumentKeydown)
  document.addEventListener('focusin', onDocumentFocusIn)
  listenersAttached = true
}

function detachListeners(): void {
  if (!listenersAttached) return
  document.removeEventListener('keydown', onDocumentKeydown)
  document.removeEventListener('focusin', onDocumentFocusIn)
  listenersAttached = false
}

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      triggerElement = captureFocusedElement()
      overlayZIndex.value = allocateOverlayZIndex()
      surfaceZIndex.value = allocateOverlayZIndex()
      pushOpenModal(instanceId)
      lockBodyScroll()
      attachListeners()
      void nextTick(() => {
        refreshAriaTargets()
        applyInitialFocus()
      })
    } else if (!isOpen && wasOpen) {
      detachListeners()
      popOpenModal(instanceId)
      unlockBodyScroll()
      returnFocusTo(triggerElement)
      triggerElement = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (!props.open) return
  detachListeners()
  popOpenModal(instanceId)
  unlockBodyScroll()
})
</script>

<style scoped>
.ui-modal-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(4, 10, 18, 0.6);
}

.ui-modal-surface {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 48px);
  width: 100%;
  overflow: auto;
  background: var(--fg2-panel);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-overlay-radius);
  box-shadow: var(--fg2-overlay-shadow);
  outline: none;
}

.ui-modal-surface--sm {
  max-width: 400px;
}

.ui-modal-surface--md {
  max-width: 520px;
}

.ui-modal-surface--lg {
  max-width: 720px;
}

.ui-modal-surface--xl {
  max-width: min(1100px, 92vw);
}

.ui-modal-body {
  padding: 4px 24px;
  overflow: auto;
}
</style>
