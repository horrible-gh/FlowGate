<template>
  <!-- L0009 §2 "Teleport": every common dialog leaves its feature's DOM and mounts on
       one host, nested dialogs included — parent/child is the stack's business, not the
       DOM tree's. -->
  <Teleport :to="teleportTarget">
    <div
      v-if="open"
      ref="overlayRef"
      class="fg-dialog-overlay"
      :class="{
        'fg-dialog-overlay--inactive': !isActive,
        'fg-dialog-overlay--below-header': belowHeader,
      }"
      :style="{ zIndex: overlayZIndex }"
      @mousedown="onOverlayMouseDown"
      @mouseup="onOverlayMouseUp"
    >
      <div
        ref="surfaceRef"
        class="fg-dialog-surface"
        :class="[
          `fg-dialog-surface--${resolvedSurface}`,
          `fg-dialog-surface--${resolvedSize}`,
          `fg-dialog-surface--variant-${variant}`,
          surfaceClass,
          { 'is-busy': busy, 'is-blocking': resolvedBlocking },
        ]"
        :style="{ zIndex: surfaceZIndex }"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="ariaLabelledBy"
        :aria-describedby="ariaDescribedBy"
        :aria-label="ariaLabelAttr"
        :inert="isActive ? undefined : true"
        :data-dialog-variant="variant"
        :data-dialog-id="instanceId"
        tabindex="-1"
        @keydown="onSurfaceKeydown"
      >
        <!-- Slot props carry the generated ARIA ids outward: a wrapper rendering the
             message element in its own template cannot `inject` them (slot content is
             created in the wrapper's scope), and an id that only one side knows is how
             `aria-describedby` ends up pointing at nothing. -->
        <slot name="header" :title-id="resolvedTitleId" :description-id="resolvedDescriptionId" />
        <div class="fg-dialog-body">
          <slot :title-id="resolvedTitleId" :description-id="resolvedDescriptionId" />
        </div>
        <slot name="footer" :title-id="resolvedTitleId" :description-id="resolvedDescriptionId" />
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * Common dialog frame — overlay, surface, stack membership, ESC/backdrop, focus, ARIA.
 *
 * flowgate.default.0560 T0012 §2.3 / design: D0008 §2 "DialogShell",
 * logic: L0009 §2 "DialogShell 로직 계약".
 *
 * The one thing this component never does is close itself. ESC, backdrop and the header
 * X are all *requests*: they are emitted as `request-close(reason)` and the owning
 * feature decides (L0009 §1 불변식 4). That is what stops the same dialog from closing
 * one way on one screen and another way on the next — the R0001 symptom.
 */
import { computed, nextTick, onBeforeUnmount, provide, reactive, ref, shallowRef, watch, watchEffect } from 'vue'

import {
  dialogActionRoleAttribute,
  dialogAriaKey,
  dialogAutofocusAttribute,
  dialogDescriptionId,
  dialogTitleId,
  dialogVariantDefaults,
  dialogZOrder,
  type DialogCloseReason,
  type DialogEntryPolicy,
  type DialogSize,
  type DialogStackEntry,
  type DialogSurface,
  type DialogVariant,
} from './dialogTypes'
import {
  closeComplete,
  dialogDevWarn,
  dialogStackEntries,
  dialogTeleportTarget,
  firstFocusableIn,
  focusableElementsIn,
  forceCleanup,
  isTopDialog,
  nextDialogInstanceId,
  registerDialog,
  shouldEmitRequestClose,
} from '../../composables/useDialogStack'

export interface DialogShellProps {
  open: boolean
  variant: DialogVariant
  size?: DialogSize
  surface?: DialogSurface
  closeable?: boolean
  closeOnEscape?: boolean
  closeOnBackdrop?: boolean
  busy?: boolean
  blocking?: boolean
  titleId?: string
  descriptionId?: string
  ariaLabel?: string
  returnFocusTo?: HTMLElement | null
  /**
   * flowgate.default.0560 T0018 §2.4 — one of the two additions the 4순위 migration
   * needed in this layer, kept as narrow as the reason for it.
   *
   * `MainPanel.vue`'s Document Full View is the reading surface 0269 D0002 deliberately
   * dims BELOW the app header, so the run monitor chip stays reachable while a document
   * is being read (`app.css .modal-bg--below-header`). The common overlay is `inset: 0`,
   * so migrating that instance as-is would have covered the header and undone that
   * decision. The behaviour itself — including the container-relative height caps this
   * shorter track requires — lives in `dialog.css`, which already warned that a
   * below-header variant must not measure heights in `vh`.
   */
  belowHeader?: boolean
  /**
   * Extra class(es) for the dialog SURFACE (T0018 §2.4).
   *
   * A feature dialog's scoped CSS cannot reach the surface — it is rendered by this
   * component and teleported out of the feature's subtree — so instances that carry a
   * measured width or a `:has()` layout rule of their own (the document full view's
   * narrow-window chat rule, the edit surface's 1120px track) had nowhere to put it.
   * This hands them a hook and nothing else: it appends to the class list AFTER the
   * variant/size/surface classes, so it can never remove one. Ordering, close policy and
   * the footer contract stay where they are; this is layout only.
   */
  surfaceClass?: string
}

/**
 * L0009 §1 "boolean props 기본값": Vue turns an omitted optional boolean into `false`,
 * which inverts every opt-out prop. `closeable` therefore gets an explicit `true`, and
 * the three variant-driven flags get an explicit `undefined` default so that "not
 * passed" stays distinguishable from "passed false" and can fall through to the variant
 * table below. A caller's explicit value always wins.
 */
const props = withDefaults(defineProps<DialogShellProps>(), {
  closeable: true,
  busy: false,
  size: undefined,
  surface: undefined,
  closeOnEscape: undefined,
  closeOnBackdrop: undefined,
  blocking: undefined,
  titleId: undefined,
  descriptionId: undefined,
  ariaLabel: undefined,
  returnFocusTo: null,
  belowHeader: false,
  surfaceClass: undefined,
})

const emit = defineEmits<{
  'request-close': [reason: DialogCloseReason]
  opened: []
  closed: []
}>()

const instanceId = nextDialogInstanceId()
const overlayRef = ref<HTMLElement | null>(null)
const surfaceRef = ref<HTMLElement | null>(null)
const entryRef = shallowRef<DialogStackEntry | null>(null)
const teleportTarget = ref<HTMLElement>(dialogTeleportTarget())

const variantDefaults = computed(() => dialogVariantDefaults[props.variant])
const resolvedSize = computed<DialogSize>(() => props.size ?? variantDefaults.value.size)
const resolvedSurface = computed<DialogSurface>(() => props.surface ?? variantDefaults.value.surface)
const resolvedCloseOnEscape = computed(() => props.closeOnEscape ?? variantDefaults.value.closeOnEscape)
const resolvedCloseOnBackdrop = computed(() => props.closeOnBackdrop ?? variantDefaults.value.closeOnBackdrop)
const resolvedBlocking = computed(() => props.blocking ?? variantDefaults.value.blocking)

/**
 * The live close policy the stack manager reads. L0009 §2 "ESC" puts the keydown
 * listener in one place, so the active entry — not the component — has to carry the
 * effective policy for the L0009 §4 decision tree.
 */
const policy = reactive<DialogEntryPolicy>({
  variant: props.variant,
  closeable: props.closeable,
  closeOnEscape: resolvedCloseOnEscape.value,
  closeOnBackdrop: resolvedCloseOnBackdrop.value,
  busy: props.busy,
  blocking: resolvedBlocking.value,
})

watchEffect(() => {
  policy.variant = props.variant
  policy.closeable = props.closeable
  policy.closeOnEscape = resolvedCloseOnEscape.value
  policy.closeOnBackdrop = resolvedCloseOnBackdrop.value
  policy.busy = props.busy
  policy.blocking = resolvedBlocking.value
  const entry = entryRef.value
  if (entry != null) entry.closeable = props.closeable
})

const isActive = computed(() => isTopDialog(entryRef.value))

// `undefined` rather than `false`: jsdom (and any DOM that does not implement the
// `inert` property) would otherwise keep the attribute around with the literal string
// "false", which reads as inert to anything checking for its presence.
/** L0009 §2 "Nested dialog / z-order". Reads the reactive stack so a nested dialog that
 *  changes depth restacks without anyone recomputing z-index by hand. */
const stackIndex = computed(() => {
  const entry = entryRef.value
  if (entry == null) return 0
  const index = dialogStackEntries().indexOf(entry)
  return index === -1 ? entry.stackIndex : index
})
const overlayZIndex = computed(() => dialogZOrder.base + stackIndex.value * dialogZOrder.step)
const surfaceZIndex = computed(() => overlayZIndex.value + dialogZOrder.surfaceOffset)

/* ─────────────────────────────────── ARIA ──────────────────────────────────── */

const resolvedTitleId = computed(() => props.titleId ?? dialogTitleId(instanceId))
const resolvedDescriptionId = computed(() => props.descriptionId ?? dialogDescriptionId(instanceId))
const hasTitleElement = ref(false)
const hasDescriptionElement = ref(false)

// DialogHeader (or any caller markup that wants the generated ids) reads them here, so
// the id that `aria-labelledby` points at and the id the title element carries are one
// value by construction.
provide(dialogAriaKey, {
  get titleId() {
    return resolvedTitleId.value
  },
  get descriptionId() {
    return resolvedDescriptionId.value
  },
})

const ariaLabelledBy = computed(() => (hasTitleElement.value ? resolvedTitleId.value : undefined))
const ariaDescribedBy = computed(() => (hasDescriptionElement.value ? resolvedDescriptionId.value : undefined))
// A headerless shell labels itself with `ariaLabel`; pointing `aria-labelledby` at an id
// nothing renders would be worse than no label at all (L0009 §2 "ARIA" 규칙 3).
const ariaLabelAttr = computed(() => (hasTitleElement.value ? undefined : props.ariaLabel))

function refreshAriaTargets(): void {
  const root = surfaceRef.value
  hasTitleElement.value =
    props.titleId != null || (root != null && root.querySelector(`[id="${resolvedTitleId.value}"]`) != null)
  hasDescriptionElement.value =
    props.descriptionId != null ||
    (root != null && root.querySelector(`[id="${resolvedDescriptionId.value}"]`) != null)
  if (!hasTitleElement.value && (props.ariaLabel == null || props.ariaLabel === '')) {
    dialogDevWarn(`dialog ${instanceId} has neither a title element nor ariaLabel; rendering without aria-labelledby`)
  }
}

/* ───────────────────────────── open / close ────────────────────────────────── */

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      startOpen()
    } else if (!isOpen && wasOpen) {
      finishClose()
    }
  },
  { immediate: true },
)

function startOpen(): void {
  teleportTarget.value = dialogTeleportTarget()
  // Steps 1-6 run before the DOM exists on purpose: `triggerElement` is whatever had
  // focus at the moment the dialog was asked for, and rendering moves focus.
  const entry = registerDialog({
    id: instanceId,
    element: null,
    explicitReturnTarget: props.returnFocusTo ?? null,
    policy,
    hooks: {
      requestClose,
      removeFocusListeners,
      emitClosed: () => emit('closed'),
    },
  })
  entryRef.value = entry
  void nextTick(completeOpen)
}

/** Steps 7-11 of `open(dialog)` — everything that needs the rendered surface. */
function completeOpen(): void {
  const entry = entryRef.value
  if (entry == null || entry.cleanupState !== 'active') return
  entry.element = surfaceRef.value
  refreshAriaTargets()
  applyInitialFocus()
  addFocusListeners()
  emit('opened')
}

function finishClose(): void {
  const entry = entryRef.value
  entryRef.value = null
  if (entry == null) return
  closeComplete(entry)
}

onBeforeUnmount(() => {
  const entry = entryRef.value
  entryRef.value = null
  if (entry == null) return
  // Defensive by design: if the dialog already closed normally this is a no-op at the
  // first line of disposeDialog, so no side effect runs twice (L0009 §2 강제 unmount).
  forceCleanup(entry)
})

function requestClose(reason: DialogCloseReason): void {
  const entry = entryRef.value
  if (entry == null) return
  if (!shouldEmitRequestClose(entry, reason)) return
  emit('request-close', reason)
}

/* ──────────────────────────────── backdrop ─────────────────────────────────── */

// L0009 §4 "Backdrop 인정 분기": both press and release must land on the overlay itself.
// A drag that starts inside the dialog (text selection) and ends on the overlay is not
// a backdrop click and must not close anything.
let pressedOnOverlay = false

function onOverlayMouseDown(event: MouseEvent): void {
  pressedOnOverlay = event.target === overlayRef.value
}

function onOverlayMouseUp(event: MouseEvent): void {
  const pressed = pressedOnOverlay
  pressedOnOverlay = false
  if (!pressed) return
  if (event.target !== overlayRef.value) return
  requestClose('backdrop')
}

/* ───────────────────────────────── focus ───────────────────────────────────── */

function queryInSurface(selector: string): HTMLElement | null {
  return surfaceRef.value?.querySelector<HTMLElement>(selector) ?? null
}

/** L0009 §4 "Initial focus 결정 트리". Danger confirms focus cancel first so that a
 *  reflex Enter does not fire the destructive button. */
function resolveInitialFocus(): HTMLElement | null {
  const root = surfaceRef.value
  if (root == null) return null
  const cancel = queryInSurface(`[${dialogActionRoleAttribute}="cancel"]`)
  const primary = queryInSurface(`[${dialogActionRoleAttribute}="primary"]`)
  if (props.variant === 'confirm-danger') {
    return cancel ?? primary ?? firstFocusableIn(root) ?? root
  }
  const autofocus = queryInSurface(`[${dialogAutofocusAttribute}]`)
  return autofocus ?? primary ?? cancel ?? firstFocusableIn(root) ?? root
}

function applyInitialFocus(): void {
  const target = resolveInitialFocus()
  if (target == null) return
  try {
    target.focus()
  } catch {
    // Best effort; a focus failure must never break opening a dialog.
  }
}

function onSurfaceKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  if (!isActive.value) return
  const root = surfaceRef.value
  if (root == null) return
  const focusables = focusableElementsIn(root)
  if (focusables.length === 0) {
    // Nothing to move to — keep focus on the container instead of letting Tab escape.
    event.preventDefault()
    root.focus()
    return
  }
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement
  if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
    return
  }
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  }
}

function onDocumentFocusIn(event: FocusEvent): void {
  // A child dialog owns the trap while it is open (L0009 §2 "Nested dialog"): only the
  // active dialog pulls focus back, so the parent does not fight it.
  if (!isActive.value) return
  const root = surfaceRef.value
  if (root == null) return
  const target = event.target
  if (target instanceof Node && root.contains(target)) return
  const fallback = firstFocusableIn(root) ?? root
  try {
    fallback.focus()
  } catch {
    // Best effort.
  }
}

let focusListenersAttached = false

function addFocusListeners(): void {
  if (focusListenersAttached) return
  document.addEventListener('focusin', onDocumentFocusIn)
  focusListenersAttached = true
}

function removeFocusListeners(): void {
  if (!focusListenersAttached) return
  document.removeEventListener('focusin', onDocumentFocusIn)
  focusListenersAttached = false
}

defineExpose({
  /** The live stack entry, for the imperative confirm/alert host (T0012 §2.6). */
  entry: entryRef,
  requestClose,
})
</script>

<!-- Not scoped, and deliberately a separate file: D0008 §2 puts the semantic action
     visuals of the whole common layer in `dialog.css`, and header/footer render inside
     this shell's subtree. Loading it here means any rendered dialog brings its own
     styles with it, without touching the global `app.css` definitions (T0012 §1). -->
<style src="./dialog.css"></style>
