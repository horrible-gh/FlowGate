<template>
  <!-- Host mode: renders whatever the imperative confirm() queue currently has on
       screen. One instance mounted at app level serves every call site, and because a
       request is displayed as a child of whichever dialog was active when it was
       called, nesting comes out of the stack rather than out of the caller's markup
       (L0009 §2 "Imperative async confirm"). -->
  <template v-if="host">
    <DialogShell
      v-for="request in hostRequests"
      :key="request.id"
      :ref="(el: unknown) => linkShell(request, el)"
      :open="true"
      :variant="request.options.danger === true ? 'confirm-danger' : 'confirm'"
      @request-close="resolveRequest(request, false)"
    >
      <template #header>
        <DialogHeader :title="request.options.title" @close="onHeaderClose(request)" />
      </template>
      <template #default="{ descriptionId }">
        <p v-if="request.options.message" :id="descriptionId" class="fg-dialog-message">
          {{ request.options.message }}
        </p>
      </template>
      <template #footer>
        <DialogFooter :actions="requestActions(request)" />
      </template>
    </DialogShell>
  </template>

  <!-- Declarative mode: the feature owns `open`, exactly as ConfirmModal.vue's callers
       do today, so the 2순위 replacement of those call sites is a swap and not a
       rewrite. -->
  <DialogShell
    v-else
    ref="shellRef"
    :open="open"
    :variant="danger ? 'confirm-danger' : 'confirm'"
    :close-on-escape="closeOnEscape"
    :close-on-backdrop="closeOnBackdrop"
    @request-close="onRequestClose"
    @opened="emit('opened')"
    @closed="emit('closed')"
  >
    <template #header>
      <DialogHeader :title="title" @close="onHeaderCloseDeclarative" />
    </template>
    <template #default="{ descriptionId }">
      <p v-if="message" :id="descriptionId" class="fg-dialog-message">{{ message }}</p>
      <slot name="message" />
    </template>
    <template #footer>
      <DialogFooter :actions="declarativeActions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
/**
 * Common confirm dialog — declarative wrapper plus the imperative `confirm()` host.
 *
 * flowgate.default.0560 T0012 §2.6 / design: D0008 §2 "ConfirmDialog",
 * logic: L0009 §2 "ConfirmDialog".
 *
 * A thin wrapper over DialogShell: it passes `open` straight through and never flips it
 * itself. Every close request that is not the confirm button — header X, ESC, backdrop
 * — comes out as `cancel`, so "I dismissed it" and "I pressed 취소" cannot produce
 * different outcomes on different screens.
 */
import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import DialogFooter from './DialogFooter.vue'
import DialogHeader from './DialogHeader.vue'
import DialogShell from './DialogShell.vue'
import {
  type DialogAction,
  type DialogConfirmRequest,
  type DialogStackEntry,
} from './dialogTypes'
import { displayedDialogRequests, linkRequestEntry, resolveRequest } from '../../composables/useDialogStack'

export interface ConfirmDialogProps {
  /** Declarative mode only; the feature owns it. Host mode ignores it. */
  open?: boolean
  /** Declarative mode only. Host mode takes the title from each queued request. */
  title?: string
  message?: string
  danger?: boolean
  confirmLabel?: string
  cancelLabel?: string
  closeOnEscape?: boolean
  closeOnBackdrop?: boolean
  /**
   * Render the imperative `confirm()` queue instead of a declarative dialog. Mount one
   * of these at app level; every `await confirm({...})` call anywhere then has a
   * surface to appear on.
   */
  host?: boolean
}

const props = withDefaults(defineProps<ConfirmDialogProps>(), {
  open: false,
  title: '',
  message: undefined,
  danger: false,
  confirmLabel: undefined,
  cancelLabel: undefined,
  // Left undefined so the variant table decides (L0009 §1): confirm/confirm-danger are
  // ESC-closable and never backdrop-closable.
  closeOnEscape: undefined,
  closeOnBackdrop: undefined,
  host: false,
})

const emit = defineEmits<{
  confirm: []
  cancel: []
  opened: []
  closed: []
}>()

const { t } = useI18n()

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

const confirmLabelText = computed(() => props.confirmLabel ?? t('common.confirm'))
const cancelLabelText = computed(() => props.cancelLabel ?? t('common.cancel'))

/**
 * `[취소] [확인]` — cancel(30) then primary(40). A danger confirm keeps the same two
 * roles and only changes the primary's tone, which is precisely the Danger Confirm
 * layout of D0008 §6 (`[취소] [위험 주버튼]`).
 */
const declarativeActions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: cancelLabelText.value,
    role: 'cancel',
    onSelect: () => emit('cancel'),
  },
  {
    id: 'confirm',
    label: confirmLabelText.value,
    role: 'primary',
    tone: props.danger ? 'danger' : 'default',
    onSelect: () => emit('confirm'),
  },
])

function onRequestClose(): void {
  // header / escape / backdrop — all of them are a cancel here (L0009 §2 ConfirmDialog).
  emit('cancel')
}

function onHeaderCloseDeclarative(): void {
  shellRef.value?.requestClose('header')
}

/* ─────────────────────────── imperative host mode ──────────────────────────── */

const hostRequests = computed<DialogConfirmRequest[]>(() =>
  displayedDialogRequests().filter((request) => request.kind === 'confirm'),
)

function requestActions(request: DialogConfirmRequest): DialogAction[] {
  return [
    {
      id: `${request.id}-cancel`,
      label: request.options.cancelLabel ?? cancelLabelText.value,
      role: 'cancel',
      onSelect: () => resolveRequest(request, false),
    },
    {
      id: `${request.id}-confirm`,
      label: request.options.confirmLabel ?? confirmLabelText.value,
      role: 'primary',
      tone: request.options.danger === true ? 'danger' : 'default',
      onSelect: () => resolveRequest(request, true),
    },
  ]
}

function onHeaderClose(request: DialogConfirmRequest): void {
  resolveRequest(request, false)
}

/**
 * Bind the displayed request to the stack entry that renders it, so that tearing that
 * entry down on its own — not just tearing down its context — still resolves the
 * caller's Promise. The `nextTick` fallback covers the case where the ref callback runs
 * before the shell's own `onMounted` registration.
 */
function linkShell(request: DialogConfirmRequest, el: unknown): void {
  const shell = el as { entry?: DialogStackEntry | null } | null
  if (shell == null) return
  if (shell.entry != null) {
    linkRequestEntry(request, shell.entry)
    return
  }
  void nextTick(() => {
    if (shell.entry != null) linkRequestEntry(request, shell.entry)
  })
}

defineExpose({ requestClose: (reason: 'header' | 'programmatic') => shellRef.value?.requestClose(reason) })
</script>
