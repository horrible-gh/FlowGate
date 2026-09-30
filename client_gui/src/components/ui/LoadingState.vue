<template>
  <div class="ui-loading-state" :class="[`ui-loading-state--${size}`, { 'ui-loading-state--inline': inline }]" role="status" aria-busy="true" aria-live="polite">
    <span class="ui-loading-state-spinner" aria-hidden="true" />
    <span class="ui-loading-state-label">{{ label ?? 'Loading' }}</span>
  </div>
</template>

<script setup lang="ts">
// Domain-free "waiting for a result" state (T0008 §8). Purely presentational: it never
// calls fetch/store itself, never decides a loading-timeout policy, and never forces
// itself into a blocking overlay by default (T0008 §8 금지) -- `inline` only switches
// padding/layout, it never adds a backdrop or blocks background interaction.
withDefaults(
  defineProps<{
    label?: string
    size?: 'sm' | 'md' | 'lg'
    inline?: boolean
  }>(),
  { label: undefined, size: 'md', inline: false },
)
</script>

<style scoped>
.ui-loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 16px;
  color: var(--fg2-muted);
}

.ui-loading-state--inline {
  display: inline-flex;
  padding: 0;
}

.ui-loading-state-spinner {
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--fg2-line-strong);
  border-top-color: var(--fg2-focus);
  animation: ui-loading-state-spin 0.7s linear infinite;
}

.ui-loading-state--sm .ui-loading-state-spinner {
  width: 14px;
  height: 14px;
}

.ui-loading-state--md .ui-loading-state-spinner {
  width: 20px;
  height: 20px;
}

.ui-loading-state--lg .ui-loading-state-spinner {
  width: 28px;
  height: 28px;
  border-width: 3px;
}

.ui-loading-state-label {
  font-size: 13px;
}

.ui-loading-state--sm .ui-loading-state-label {
  font-size: 12px;
}

@keyframes ui-loading-state-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
