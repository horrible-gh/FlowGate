<script setup lang="ts">
defineProps<{
  title?: string
  noPad?: boolean
}>()
</script>

<template>
  <div class="card">
    <div v-if="title || $slots.header" class="card-hd">
      <slot name="header">
        <span class="card-title">{{ title }}</span>
      </slot>
    </div>
    <div class="card-bd" :class="{ pad: !noPad }">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg); box-shadow: var(--sh-sm); }
.card-hd { padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
.card-title { font-weight: 600; font-size: 14px; color: var(--text); }
.card-bd.pad { padding: 16px; }
</style>
