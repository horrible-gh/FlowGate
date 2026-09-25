<template>
  <button :type="type" class="ui-btn" :class="[`ui-btn--${variant}`, `ui-btn--${size}`, { 'is-loading': loading }]" :disabled="isDisabled" :aria-busy="loading || undefined">
    <span v-if="loading" class="ui-btn-spinner" aria-hidden="true" />
    <span class="ui-btn-content"><slot /></span>
  </button>
</template>

<script setup lang="ts">
// Domain-free button primitive (T0004 §5.1): visual variant/size/loading only, no feature
// meaning. A screen picks primary/secondary/ghost/danger — never a domain name like
// "git-merge" or "approval" (T0004 §5.1/§11).
import { computed } from 'vue'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: ButtonVariant
    size?: ButtonSize
    type?: 'button' | 'submit' | 'reset'
    disabled?: boolean
    loading?: boolean
  }>(),
  { variant: 'secondary', size: 'md', type: 'button', disabled: false, loading: false },
)

const isDisabled = computed(() => props.disabled || props.loading)
</script>

<style scoped>
.ui-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid transparent;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
  transition: 0.16s ease;
}

.ui-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity-strong);
}

.ui-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.ui-btn--sm {
  height: var(--fg2-control-h-sm);
  padding: 0 12px;
  font-size: 12px;
  border-radius: var(--fg2-control-radius-sm);
}

.ui-btn--md {
  height: var(--fg2-control-h-md);
  padding: 0 16px;
  font-size: var(--fg2-control-font-size);
}

.ui-btn--lg {
  height: var(--fg2-control-h-lg);
  padding: 0 22px;
  font-size: 14px;
  border-radius: var(--fg2-control-radius-lg);
}

.ui-btn--primary {
  border-color: rgba(75, 163, 255, 0.5);
  background: linear-gradient(180deg, #3d8cff, #2a6fd6);
  color: #f4f9ff;
}

.ui-btn--primary:hover:not(:disabled) {
  border-color: rgba(120, 180, 255, 0.7);
  filter: brightness(1.06);
}

.ui-btn--secondary {
  border-color: var(--fg2-line-strong);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-control-fg);
}

.ui-btn--secondary:hover:not(:disabled) {
  border-color: var(--fg2-control-hover-border);
  color: var(--fg2-text);
}

.ui-btn--ghost {
  border-color: transparent;
  background: none;
  color: var(--fg2-control-fg);
}

.ui-btn--ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.06);
  color: var(--fg2-text);
}

.ui-btn--danger {
  border-color: rgba(255, 100, 120, 0.5);
  background: linear-gradient(180deg, #e14b60, #b6324a);
  color: #fff3f4;
}

.ui-btn--danger:hover:not(:disabled) {
  filter: brightness(1.08);
}

.ui-btn-content {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.ui-btn.is-loading .ui-btn-content {
  opacity: 0.7;
}

.ui-btn-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: currentColor;
  animation: ui-btn-spin 0.7s linear infinite;
}

@keyframes ui-btn-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
