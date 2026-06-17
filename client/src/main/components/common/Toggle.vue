<script setup lang="ts">
defineProps<{ modelValue: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <label class="toggle" :class="{ disabled }">
    <input type="checkbox" :checked="modelValue" :disabled="disabled"
      @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)" />
    <span class="toggle-track">
      <span class="toggle-thumb" />
    </span>
  </label>
</template>

<style scoped>
.toggle { display: inline-flex; align-items: center; cursor: pointer; }
.toggle input { display: none; }
.toggle-track { width: 36px; height: 20px; background: var(--border-d); border-radius: 99px; transition: var(--tr); position: relative; }
.toggle input:checked ~ .toggle-track { background: var(--primary); }
.toggle-thumb { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; background: #fff; border-radius: 50%; transition: var(--tr); }
.toggle input:checked ~ .toggle-track .toggle-thumb { transform: translateX(16px); }
.toggle.disabled { opacity: 0.5; cursor: not-allowed; }
</style>
