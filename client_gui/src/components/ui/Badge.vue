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
  border-style: dashed;
  color: #95a8bd;
  background: transparent;
}

.ui-badge--info {
  border-color: rgba(75, 163, 255, 0.4);
  color: #7cc0ff;
  background: rgba(75, 163, 255, 0.1);
}

.ui-badge--success {
  border-color: rgba(73, 223, 208, 0.4);
  color: #4fe0d3;
  background: rgba(73, 223, 208, 0.1);
}

.ui-badge--warning {
  border-color: rgba(255, 174, 87, 0.45);
  color: #ffc072;
  background: rgba(255, 174, 87, 0.1);
}

.ui-badge--danger {
  border-color: rgba(255, 100, 120, 0.45);
  color: #ff96a6;
  background: rgba(255, 100, 120, 0.1);
}
</style>
