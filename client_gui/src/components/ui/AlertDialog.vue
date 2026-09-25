<template>
  <ModalShell
    :open="open"
    size="sm"
    role="alertdialog"
    :close-on-escape="closeOnEscape"
    :close-on-backdrop="false"
    :aria-describedby="ariaDescribedby"
    @close="emit('close')"
  >
    <template #header="{ titleId }">
      <div class="ui-alert-header">
        <h2 :id="titleId" class="ui-alert-title">{{ title }}</h2>
      </div>
    </template>
    <template #default="{ descriptionId }">
      <p v-if="message" :id="descriptionId" class="ui-alert-message" :class="`ui-alert-message--${tone}`">
        {{ message }}
      </p>
      <slot name="message" :descriptionId="descriptionId" />
    </template>
    <template #footer>
      <div class="ui-alert-actions">
        <Button :variant="tone === 'danger' ? 'danger' : 'primary'" @click="emit('close')">
          {{ acknowledgeLabelText }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Short "read and acknowledge" message, built on ModalShell (T0006 §8). One button,
// `role="alertdialog"` (T0006 §8's accessibility requirement), and never a Toast: it
// does not auto-dismiss and does not stack multiple messages (T0006 §8 "AlertDialog와
// Toast 역할 혼합" 금지) — the caller controls `open` and shows one at a time, same as
// every other declarative dialog in this layer.
//
// Every close path — the acknowledge button and ESC — resolves the same `close` event,
// so a caller cannot end up with a different outcome depending on how the dialog was
// dismissed. Backdrop click never closes it (an alert should not vanish by accident).
import { computed } from 'vue'
import Button from './Button.vue'
import ModalShell from './ModalShell.vue'

export type AlertTone = 'info' | 'warning' | 'danger'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    message?: string
    acknowledgeLabel?: string
    tone?: AlertTone
    closeOnEscape?: boolean
    /** Only needed when the `message` slot's content can't attach the scoped
     *  `descriptionId` to itself (e.g. it points at an element outside the slot). */
    ariaDescribedby?: string
  }>(),
  {
    title: '',
    message: undefined,
    acknowledgeLabel: undefined,
    tone: 'info',
    closeOnEscape: true,
    ariaDescribedby: undefined,
  },
)

const emit = defineEmits<{ close: [] }>()

const acknowledgeLabelText = computed(() => props.acknowledgeLabel ?? 'OK')
</script>

<style scoped>
.ui-alert-header {
  padding: 20px 24px 0;
}

.ui-alert-title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--fg2-text);
}

.ui-alert-message {
  margin: 0;
  padding: 12px 0;
  color: var(--fg2-muted);
  font-size: 13px;
  line-height: 1.6;
}

.ui-alert-message--warning {
  color: var(--fg2-yellow);
}

.ui-alert-message--danger {
  color: #ff8fa0;
}

.ui-alert-actions {
  display: flex;
  justify-content: flex-end;
  padding: 16px 24px 20px;
}
</style>
