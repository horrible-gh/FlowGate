<script setup lang="ts">
defineProps<{
  type?: 'info' | 'warning' | 'danger' | 'success'
}>()
</script>

<template>
  <div class="alert" :class="`alert-${type ?? 'info'}`">
    <slot />
  </div>
</template>

<style scoped>
.alert { padding: 10px 14px; border-radius: var(--r); font-size: 13px; border-left: 3px solid; }
.alert-info    { background: var(--info-l);    color: var(--info);    border-color: var(--info); }
.alert-warning { background: var(--warning-l); color: var(--warning); border-color: var(--warning); }
.alert-danger  { background: var(--danger-l);  color: var(--danger);  border-color: var(--danger); }
.alert-success { background: var(--success-l); color: var(--success); border-color: var(--success); }
</style>
