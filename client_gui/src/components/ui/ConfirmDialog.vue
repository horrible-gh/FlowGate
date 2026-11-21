<template>
  <ModalShell
    :open="open"
    size="sm"
    surface-class="ui-confirm-surface"
    :close-on-escape="closeOnEscape && !busy"
    :close-on-backdrop="closeOnBackdrop && !busy"
    :aria-describedby="ariaDescribedby"
    @close="!busy && emit('cancel')"
  >
    <template #header="{ titleId }">
      <div class="ui-confirm-header" :class="{ 'is-danger': danger }">
        <span class="ui-confirm-badge" aria-hidden="true" data-test="ui-confirm-badge" :data-icon="badgeIcon"><LineIcon :name="badgeIcon" /></span>
        <h2 :id="titleId" class="ui-confirm-title">{{ title }}</h2>
      </div>
    </template>
    <template #default="{ descriptionId }">
      <p v-if="message" :id="descriptionId" class="ui-confirm-message">{{ message }}</p>
      <slot name="message" :descriptionId="descriptionId" />
    </template>
    <template #footer>
      <div class="ui-confirm-actions">
        <Button variant="secondary" :disabled="busy" @click="!busy && emit('cancel')">{{ cancelLabelText }}</Button>
        <Button :variant="danger ? 'danger' : 'primary'" :loading="busy" :disabled="busy" @click="!busy && emit('confirm')">{{ confirmLabelText }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// v02gui.0029 (0003-CH, mockup tfi2zm20 "Shared confirmation dialog"): drawn like the Designer's Group archive confirm — a badge above the
// title (`icon`, else ⚠ for a danger confirm and ? for any other), the rounded pop surface and two wide buttons. Only
// the look changed: the caller's words, the events, Escape and the focus order stay as they were.
//
// Short Yes/No confirmation, built on ModalShell. This is choice A from
// ("Shared ConfirmDialog component") — plain declarative props, no separate
// confirm() service layer, because the caller here already owns `open` the same way
// every other declarative dialog in this layer does; adding an imperative queue on top
// is speculative machinery this T does not need (the earlier implementation "No need to
// overengineer it").
//
// Business wording (title/message/labels) is entirely the caller's — this component
// carries no domain text of its own.
//
// Safe default focus: Cancel renders before Confirm/Danger in the DOM, and ModalShell
// focuses the first focusable element on open, so a danger confirm never lands its
// initial focus on the destructive button without this component having to special-
// case `danger` at all ("Do not unconditionally apply default focus").
import { computed } from 'vue'
import Button from './Button.vue'
import LineIcon, { type LineIconName } from './LineIcon.vue'
import ModalShell from './ModalShell.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    message?: string
    confirmLabel?: string
    cancelLabel?: string
    danger?: boolean
    /** The header badge's icon; by default ⚠ (`alert`) for a danger confirm, ? (`help-circle`) for any other. */
    icon?: LineIconName
    busy?: boolean
    closeOnEscape?: boolean
    closeOnBackdrop?: boolean
    /** Only needed when the `message` slot's content can't attach the scoped
     *  `descriptionId` to itself (e.g. it points at an element outside the slot). */
    ariaDescribedby?: string
  }>(),
  {
    title: '',
    message: undefined,
    confirmLabel: undefined,
    cancelLabel: undefined,
    danger: false,
    icon: undefined,
    busy: false,
    closeOnEscape: true,
    closeOnBackdrop: false,
    ariaDescribedby: undefined,
  },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

const confirmLabelText = computed(() => props.confirmLabel ?? 'Confirm')
const cancelLabelText = computed(() => props.cancelLabel ?? 'Cancel')
const badgeIcon = computed<LineIconName>(() => props.icon ?? (props.danger ? 'alert' : 'help-circle'))
</script>

<style scoped>
:global(.ui-modal-surface.ui-modal-surface--sm.ui-confirm-surface) {
  max-width: 420px;
  background: var(--fg2-pop-surface);
  border-radius: var(--fg2-pop-radius);
  box-shadow: var(--fg2-pop-shadow);
}

.ui-confirm-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 14px;
  padding: 24px 24px 0;
}

.ui-confirm-badge {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: rgba(75, 163, 255, 0.14);
  color: #8fc8ff;
}

.ui-confirm-badge :deep(svg) {
  width: 22px;
  height: 22px;
}

.ui-confirm-header.is-danger .ui-confirm-badge {
  background: rgba(255, 100, 120, 0.14);
  color: #ff8c9c;
}

.ui-confirm-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--fg2-text);
}

.ui-confirm-message {
  margin: 0;
  padding: 10px 0 14px;
  color: var(--fg2-text-2);
  font-size: 14px;
  line-height: 1.55;
}

/* Two equal, taller buttons across the confirm's width (Cancel first, as before). */
.ui-confirm-actions {
  display: flex;
  gap: 10px;
  padding: 8px 24px 24px;
}

.ui-confirm-actions > :deep(.ui-btn) {
  flex: 1;
  height: 40px;
  border-radius: var(--fg2-inset-radius);
}
</style>
