<template>
  <div class="ui-error-state" :class="[`ui-error-state--${tone}`, { 'ui-error-state--compact': compact }]" role="alert">
    <p class="ui-error-state-title">{{ title }}</p>
    <p v-if="message" class="ui-error-state-message">{{ message }}</p>
    <div v-if="$slots.details" class="ui-error-state-details"><slot name="details" /></div>
    <div v-if="$slots.action" class="ui-error-state-action"><slot name="action" /></div>
  </div>
</template>

<script setup lang="ts">
// Domain-free screen/panel-level error state (T0008 §9). Displays only what the caller
// already resolved -- it never parses an axios error, maps an HTTP status, retries a
// request, navigates the router or mutates a store (T0008 §9 금지 list). `action` is
// wherever the caller puts its own retry Button; this component only shows it.
export type ErrorStateTone = 'danger' | 'warning'

withDefaults(
  defineProps<{
    title: string
    message?: string
    tone?: ErrorStateTone
    compact?: boolean
  }>(),
  { message: undefined, tone: 'danger', compact: false },
)
</script>

<style scoped>
.ui-error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 24px;
  text-align: center;
}

.ui-error-state--compact {
  padding: 20px 16px;
  gap: 6px;
}

.ui-error-state-title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--fg2-status-danger-fg);
}

.ui-error-state--warning .ui-error-state-title {
  color: var(--fg2-status-warning-fg);
}

.ui-error-state-message {
  margin: 0;
  max-width: 360px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--fg2-muted);
}

.ui-error-state-details {
  max-width: 480px;
  font-size: 11px;
  color: var(--fg2-muted-2);
}

.ui-error-state-action {
  margin-top: 6px;
}
</style>
