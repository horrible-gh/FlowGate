<template>
  <button :type="type" class="ui-icon-btn" :class="[`ui-icon-btn--${variant}`, `ui-icon-btn--${size}`]" :disabled="disabled" :aria-label="label" :title="title">
    <slot />
  </button>
</template>

<script setup lang="ts">
// Icon-only control (T0004 §5.2). `label` is mandatory — it is the only accessible name an
// icon-only button has. `title` is opt-in (native tooltip); leave it unset when a caller
// wraps this in the Tooltip primitive instead, so the two affordances never stack.
withDefaults(
  defineProps<{
    label: string
    title?: string
    variant?: 'ghost' | 'secondary'
    size?: 'sm' | 'md' | 'lg'
    type?: 'button' | 'submit' | 'reset'
    disabled?: boolean
  }>(),
  { variant: 'ghost', size: 'md', type: 'button', disabled: false },
)
</script>

<style scoped>
.ui-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  border-radius: var(--fg2-control-radius-sm);
  border: 1px solid transparent;
  padding: 0;
  cursor: pointer;
  transition: 0.16s ease;
}

.ui-icon-btn :deep(svg) {
  width: 60%;
  height: 60%;
}

.ui-icon-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity-strong);
}

.ui-icon-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.ui-icon-btn--sm {
  width: 28px;
  height: 28px;
}

.ui-icon-btn--md {
  width: 34px;
  height: 34px;
}

.ui-icon-btn--lg {
  width: 42px;
  height: 42px;
}

.ui-icon-btn--ghost {
  background: none;
  color: var(--fg2-control-fg);
}

.ui-icon-btn--ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.08);
  color: var(--fg2-text);
}

.ui-icon-btn--secondary {
  border-color: var(--fg2-line-strong);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-control-fg);
}

.ui-icon-btn--secondary:hover:not(:disabled) {
  border-color: var(--fg2-control-hover-border);
  color: var(--fg2-text);
}
</style>
