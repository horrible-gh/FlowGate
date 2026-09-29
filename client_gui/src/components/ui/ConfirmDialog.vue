<template>
  <ModalShell
    :open="open"
    size="sm"
    :close-on-escape="closeOnEscape"
    :close-on-backdrop="closeOnBackdrop"
    :aria-describedby="ariaDescribedby"
    @close="emit('cancel')"
  >
    <template #header="{ titleId }">
      <div class="ui-confirm-header">
        <h2 :id="titleId" class="ui-confirm-title">{{ title }}</h2>
      </div>
    </template>
    <template #default="{ descriptionId }">
      <p v-if="message" :id="descriptionId" class="ui-confirm-message">{{ message }}</p>
      <slot name="message" :descriptionId="descriptionId" />
    </template>
    <template #footer>
      <div class="ui-confirm-actions">
        <Button variant="secondary" @click="emit('cancel')">{{ cancelLabelText }}</Button>
        <Button :variant="danger ? 'danger' : 'primary'" @click="emit('confirm')">{{ confirmLabelText }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Short Yes/No confirmation, built on ModalShell (T0006 §7). This is choice A from
// T0006 §7 ("공용 ConfirmDialog component") — plain declarative props, no separate
// confirm() service layer, because the caller here already owns `open` the same way
// every other declarative dialog in this layer does; adding an imperative queue on top
// is speculative machinery this T does not need (T0006 §7 "과도하게 만들 필요는
// 없다").
//
// Business wording (title/message/labels) is entirely the caller's — this component
// carries no domain text of its own (T0006 §7/§15).
//
// Safe default focus: Cancel renders before Confirm/Danger in the DOM, and ModalShell
// focuses the first focusable element on open, so a danger confirm never lands its
// initial focus on the destructive button without this component having to special-
// case `danger` at all (T0006 §7 "무조건 기본 focus가 가지 않도록").
import { computed } from 'vue'
import Button from './Button.vue'
import ModalShell from './ModalShell.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    message?: string
    confirmLabel?: string
    cancelLabel?: string
    danger?: boolean
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
    closeOnEscape: true,
    closeOnBackdrop: false,
    ariaDescribedby: undefined,
  },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

const confirmLabelText = computed(() => props.confirmLabel ?? 'Confirm')
const cancelLabelText = computed(() => props.cancelLabel ?? 'Cancel')
</script>

<style scoped>
.ui-confirm-header {
  padding: 20px 24px 0;
}

.ui-confirm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--fg2-text);
}

.ui-confirm-message {
  margin: 0;
  padding: 12px 0;
  color: var(--fg2-muted);
  font-size: 13px;
  line-height: 1.6;
}

.ui-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
