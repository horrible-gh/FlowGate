<script setup lang="ts">
defineProps<{
  color?: 'green' | 'red' | 'blue' | 'yellow' | 'gray' | 'info'
}>()
</script>

<template>
  <span class="badge" :class="`badge-${color ?? 'gray'}`">
    <slot />
  </span>
</template>

<style scoped>
.badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; }
.badge-green  { background: var(--success-l); color: var(--success); }
.badge-red    { background: var(--danger-l);  color: var(--danger); }
.badge-blue   { background: var(--primary-l); color: var(--primary); }
.badge-yellow { background: var(--warning-l); color: var(--warning); }
.badge-gray   { background: #f1f5f9; color: var(--text-s); }
.badge-info   { background: var(--info-l); color: var(--info); }
</style>
