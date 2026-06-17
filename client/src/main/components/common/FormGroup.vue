<template>
  <div class="form-group">
    <label v-if="label" class="form-label" :class="{ req: required }">{{ label }}</label>
    <slot />
    <p v-if="hint" class="form-hint">{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label?: string
  hint?: string
  required?: boolean
}>()
</script>

<style scoped>
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 13px; font-weight: 500; color: var(--text-s); }
.form-label.req::after { content: ' *'; color: var(--danger); }
.form-hint { font-size: 12px; color: var(--text-m); margin: 0; }
</style>
