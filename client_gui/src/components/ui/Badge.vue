<template>
  <span class="ui-badge" :class="[`ui-badge--${tone}`, `ui-badge--${size}`]">
    <span v-if="$slots.icon" class="ui-badge-icon"><slot name="icon" /></span>
    <slot />
  </span>
</template>

<script setup lang="ts">
// Domain-free label chip (T0004 §5.7): label + size + tone only. Mapping a domain state
// (Git conflict, approval status, document type, ...) to a tone/label is the owning
// feature's job, not this component's (T0004 §11) — it never imports feature/API code.
withDefaults(
  defineProps<{
    tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger'
    size?: 'sm' | 'md'
  }>(),
  { tone: 'neutral', size: 'md' },
)
</script>

<style scoped>
.ui-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: var(--fg2-control-radius-pill);
  border: 1px solid var(--fg2-line-strong);
  white-space: nowrap;
  font-weight: 600;
}

.ui-badge-icon {
  display: inline-flex;
  align-items: center;
}

.ui-badge--sm {
  padding: 2px 8px;
  font-size: 11px;
}

.ui-badge--md {
  padding: 3px 10px;
  font-size: 12px;
}

.ui-badge--neutral {
  /* Own dashed/transparent chip look -- not shared with any other component, so this
     stays a literal instead of a --fg2-status-neutral-* token (T0008 §13 rework). */
  border-style: dashed;
  color: #95a8bd;
  background: transparent;
}

/* info/success/warning/danger reuse the same --fg2-status-* triplet Toast consumes
   (theme.css T0008 §11 rework) -- these are the values this component originally
   established, now shared instead of duplicated. */
.ui-badge--info {
  border-color: var(--fg2-status-info-border);
  color: var(--fg2-status-info-fg);
  background: var(--fg2-status-info-bg);
}

.ui-badge--success {
  border-color: var(--fg2-status-success-border);
  color: var(--fg2-status-success-fg);
  background: var(--fg2-status-success-bg);
}

.ui-badge--warning {
  border-color: var(--fg2-status-warning-border);
  color: var(--fg2-status-warning-fg);
  background: var(--fg2-status-warning-bg);
}

.ui-badge--danger {
  border-color: var(--fg2-status-danger-border);
  color: var(--fg2-status-danger-fg);
  background: var(--fg2-status-danger-bg);
}
</style>
