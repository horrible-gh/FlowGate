<template>
  <div class="ui-alert" :class="`ui-alert--${tone}`" role="note">
    <span v-if="$slots.icon" class="ui-alert-icon" aria-hidden="true"><slot name="icon" /></span>
    <div class="ui-alert-body">
      <p v-if="title" class="ui-alert-title">{{ title }}</p>
      <div class="ui-alert-message"><slot /></div>
    </div>
    <div v-if="$slots.action" class="ui-alert-action"><slot name="action" /></div>
    <IconButton v-if="dismissible" class="ui-alert-close" variant="ghost" size="sm" :label="dismissLabel" @click="emit('dismiss')">×</IconButton>
  </div>
</template>

<script setup lang="ts">
// Inline/banner status message that stays on screen until the caller removes it or the
// person dismisses it (T0008 §6) -- distinct from AlertDialog's blocking confirmation
// (T0006) and from Toast's auto-expiring pop-up (T0008 §5). Never runs API/retry logic
// itself: `action` is whatever Button the caller wires up (T0008 §6 example), this
// component only renders the slot.
import IconButton from './IconButton.vue'

export type AlertTone = 'info' | 'success' | 'warning' | 'danger'

withDefaults(
  defineProps<{
    tone?: AlertTone
    title?: string
    dismissible?: boolean
    dismissLabel?: string
  }>(),
  { tone: 'info', title: undefined, dismissible: false, dismissLabel: 'Dismiss' },
)

const emit = defineEmits<{ dismiss: [] }>()
</script>

<style scoped>
.ui-alert {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid var(--fg2-line-strong);
  border-left-width: 3px;
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-text);
  font-size: 13px;
  line-height: 1.6;
}

.ui-alert-icon {
  display: inline-flex;
  flex: none;
  margin-top: 1px;
}

.ui-alert-body {
  flex: 1;
  min-width: 0;
}

.ui-alert-title {
  margin: 0 0 2px;
  font-weight: 650;
  color: var(--fg2-text);
}

.ui-alert-message {
  color: var(--fg2-muted);
}

.ui-alert-action {
  flex: none;
}

.ui-alert-close {
  flex: none;
}

.ui-alert--info {
  border-left-color: var(--fg2-status-info-border);
}

.ui-alert--success {
  border-left-color: var(--fg2-status-success-border);
}

.ui-alert--warning {
  border-left-color: var(--fg2-status-warning-border);
}

.ui-alert--danger {
  border-left-color: var(--fg2-status-danger-border);
}
</style>
