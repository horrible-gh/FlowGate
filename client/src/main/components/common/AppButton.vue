<template>
  <button
    class="btn"
    :class="[`btn-${variant ?? 'primary'}`, size === 'sm' ? 'btn-sm' : '']"
    :disabled="disabled"
    :type="type ?? 'button'"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  disabled?: boolean
  type?: 'button' | 'submit'
}>()
</script>

<style scoped>
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius: var(--r); font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid transparent; transition: var(--tr); }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-primary   { background: var(--primary);   color: #fff; }
.btn-primary:hover { background: var(--primary-h); }
.btn-secondary { background: var(--surface); color: var(--text); border-color: var(--border); }
.btn-secondary:hover { background: var(--surface-h); }
.btn-ghost { background: transparent; color: var(--text-s); border-color: transparent; }
.btn-ghost:hover { background: var(--surface-h); }
.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover { opacity: 0.88; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
