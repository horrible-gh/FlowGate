<template>
  <component
    :is="as ?? 'input'"
    class="form-ctrl"
    :type="as === 'input' || !as ? (type ?? 'text') : undefined"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  >
    <slot v-if="as === 'select'" />
  </component>
</template>

<script setup lang="ts">
defineProps<{
  modelValue?: string | number
  type?: string
  placeholder?: string
  disabled?: boolean
  as?: 'input' | 'select' | 'textarea'
}>()
defineEmits(['update:modelValue'])
</script>

<style scoped>
.form-ctrl { width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--r); font-size: 13px; color: var(--text); background: var(--surface); transition: var(--tr); outline: none; }
.form-ctrl:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-l); }
.form-ctrl:disabled { background: var(--surface-h); opacity: 0.6; cursor: not-allowed; }
</style>
