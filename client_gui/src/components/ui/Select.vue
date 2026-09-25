<template>
  <select class="ui-select" :class="{ 'is-invalid': invalid }" :value="modelValue" :disabled="disabled" @change="onChange">
    <option v-if="placeholder" value="" disabled hidden>{{ placeholder }}</option>
    <option v-for="opt in options" :key="opt.value" :value="opt.value" :disabled="opt.disabled">{{ opt.label }}</option>
  </select>
</template>

<script setup lang="ts">
// Domain-free native select (T0004 §5.4): a plain label/value list. Provider selector,
// Branch selector and similar domain pickers stay in their owning feature (T0004 §11).
export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

withDefaults(
  defineProps<{
    modelValue?: string
    options: SelectOption[]
    placeholder?: string
    disabled?: boolean
    invalid?: boolean
  }>(),
  { modelValue: '', disabled: false, invalid: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function onChange(event: Event) {
  emit('update:modelValue', (event.target as HTMLSelectElement).value)
}
</script>

<style scoped>
.ui-select {
  height: var(--fg2-control-h-md);
  width: 100%;
  min-width: 0;
  padding: 0 34px 0 var(--fg2-control-pad-x);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background:
    linear-gradient(45deg, transparent 50%, #93a7bd 50%) calc(100% - 16px) center / 6px 6px no-repeat,
    linear-gradient(135deg, #93a7bd 50%, transparent 50%) calc(100% - 12px) center / 6px 6px no-repeat,
    var(--fg2-control-surface);
  color: var(--fg2-text);
  font-size: var(--fg2-control-font-size);
  appearance: none;
}

.ui-select:disabled {
  color: var(--fg2-muted);
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.ui-select:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.ui-select.is-invalid {
  border-color: var(--fg2-control-invalid-border);
}

.ui-select option {
  background: #0d1a28;
  color: var(--fg2-text);
}
</style>
