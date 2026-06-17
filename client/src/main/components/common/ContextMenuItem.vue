<template>
  <button class="ctx-item" :class="{ 'ctx-item--danger': danger }" type="button" role="menuitem" @click="emit('click')">
    <i v-if="icon" :class="icon" class="ctx-item__icon"></i>
    <slot />
  </button>
</template>

<script setup lang="ts">
defineProps<{
  icon?: string
  danger?: boolean
}>()

const emit = defineEmits<{
  click: []
}>()
</script>

<style scoped>
.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 14px;
  background: transparent;
  border: none;
  border-radius: 0;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: var(--tr, background 0.15s);
}
.ctx-item:hover {
  background: var(--surface-h);
}
.ctx-item__icon {
  width: 14px;
  text-align: center;
  flex-shrink: 0;
  color: var(--text-s);
}
.ctx-item--danger {
  color: var(--danger, #ef4444);
}
.ctx-item--danger .ctx-item__icon {
  color: var(--danger, #ef4444);
}
.ctx-item--danger:hover {
  background: rgba(239, 68, 68, 0.12);
}
</style>
