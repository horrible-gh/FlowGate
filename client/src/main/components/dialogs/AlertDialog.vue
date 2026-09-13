<template>
  <!-- Host mode: the imperative alert() queue, same shape as ConfirmDialog's host. -->
  <template v-if="host">
    <DialogShell
      v-for="request in hostRequests"
      :key="request.id"
      :ref="(el: unknown) => linkShell(request, el)"
      :open="true"
      variant="alert"
      @request-close="resolveRequest(request, false)"
    >
      <template #header>
        <DialogHeader :title="request.options.title" @close="onHeaderClose(request)" />
      </template>
      <template #default="{ descriptionId }">
        <p
          v-if="request.options.message"
          :id="descriptionId"
          class="fg-dialog-message"
          :class="`fg-dialog-message--${request.options.tone ?? 'info'}`"
        >
          {{ request.options.message }}
        </p>
      </template>
      <template #footer>
        <DialogFooter :actions="requestActions(request)" />
      </template>
    </DialogShell>
  </template>

  <DialogShell
    v-else
    ref="shellRef"
    :open="open"
    variant="alert"
    :close-on-escape="closeOnEscape"
    @request-close="onRequestClose"
    @opened="emit('opened')"
    @closed="emit('closed')"
  >
    <template #header>
      <DialogHeader :title="title" @close="onHeaderCloseDeclarative" />
    </template>
    <template #default="{ descriptionId }">
      <p v-if="message" :id="descriptionId" class="fg-dialog-message" :class="`fg-dialog-message--${tone}`">
        {{ message }}
      </p>
      <slot name="message" />
    </template>
    <template #footer>
      <DialogFooter :actions="declarativeActions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
/**
 * Common alert dialog — declarative wrapper plus the imperative `alert()` host.
 *
 * flowgate.default.0560 T0012 §2.7 / design: D0008 §2 "AlertDialog",
 * logic: L0009 §2 "AlertDialog".
 *
 * Wraps DialogShell the same way ConfirmDialog does, with one difference that matters:
 * every close request — X, ESC, backdrop — becomes `close`, and the imperative form
 * always resolves `undefined`. The true/false the queue uses internally is bookkeeping
 * and is never shown to an alert caller.
 */
import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import DialogFooter from './DialogFooter.vue'
import DialogHeader from './DialogHeader.vue'
import DialogShell from './DialogShell.vue'
import {
  type DialogAction,
  type DialogAlertTone,
  type DialogConfirmRequest,
  type DialogStackEntry,
} from './dialogTypes'
import { displayedDialogRequests, linkRequestEntry, resolveRequest } from '../../composables/useDialogStack'

export interface AlertDialogProps {
  /** Declarative mode only; the feature owns it. Host mode ignores it. */
  open?: boolean
  /** Declarative mode only. Host mode takes the title from each queued request. */
  title?: string
  message?: string
  tone?: DialogAlertTone
  closeLabel?: string
  closeOnEscape?: boolean
  /** Render the imperative `alert()` queue instead of a declarative dialog. */
  host?: boolean
}

const props = withDefaults(defineProps<AlertDialogProps>(), {
  open: false,
  title: '',
  message: undefined,
  tone: 'info',
  closeLabel: undefined,
  // Left undefined so the `alert` variant's own default (true) applies.
  closeOnEscape: undefined,
  host: false,
})

const emit = defineEmits<{
  close: []
  opened: []
  closed: []
}>()

const { t } = useI18n()

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

const closeLabelText = computed(() => props.closeLabel ?? t('common.close'))

/** One button. It is the primary action, so it lands on the right and takes initial
 *  focus through the same tree every other dialog uses. */
const declarativeActions = computed<DialogAction[]>(() => [
  {
    id: 'close',
    label: closeLabelText.value,
    role: 'primary',
    tone: props.tone === 'danger' ? 'danger' : 'default',
    onSelect: () => emit('close'),
  },
])

function onRequestClose(): void {
  emit('close')
}

function onHeaderCloseDeclarative(): void {
  shellRef.value?.requestClose('header')
}

/* ─────────────────────────── imperative host mode ──────────────────────────── */

const hostRequests = computed<DialogConfirmRequest[]>(() =>
  displayedDialogRequests().filter((request) => request.kind === 'alert'),
)

function requestActions(request: DialogConfirmRequest): DialogAction[] {
  return [
    {
      id: `${request.id}-close`,
      label: request.options.closeLabel ?? closeLabelText.value,
      role: 'primary',
      tone: request.options.tone === 'danger' ? 'danger' : 'default',
      onSelect: () => resolveRequest(request, false),
    },
  ]
}

function onHeaderClose(request: DialogConfirmRequest): void {
  resolveRequest(request, false)
}

/** See ConfirmDialog.linkShell — same binding, same reason. */
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
