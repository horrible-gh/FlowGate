<template>
  <input
    :type="type"
    class="ui-input"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :readonly="readonly"
    :aria-invalid="invalid || undefined"
    @input="onInput"
  />
</template>

<script setup lang="ts">
// Domain-free text input (T0004 §5.3): visual + v-model contract only. It never calls an
// API or knows what the value means — that belongs to the feature that owns the field.
withDefaults(
  defineProps<{
    modelValue?: string | number
    type?: string
    placeholder?: string
    disabled?: boolean
    readonly?: boolean
    invalid?: boolean
  }>(),
  { modelValue: '', type: 'text', disabled: false, readonly: false, invalid: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<style scoped>
.ui-input {
  height: var(--fg2-control-h-md);
  width: 100%;
  min-width: 0;
  padding: 0 var(--fg2-control-pad-x);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font-size: var(--fg2-control-font-size);
}

.ui-input::placeholder {
  color: var(--fg2-muted);
  opacity: 1;
}

.ui-input:disabled {
  color: var(--fg2-muted);
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.ui-input:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.ui-input.is-invalid {
  border-color: var(--fg2-control-invalid-border);
}
</style>
