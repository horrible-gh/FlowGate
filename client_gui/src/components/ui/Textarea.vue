<template>
  <textarea
    class="ui-textarea"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :readonly="readonly"
    :rows="rows"
    :aria-invalid="invalid || undefined"
    @input="onInput"
  />
</template>

<script setup lang="ts">
// Domain-free multi-line input (T0004 §5.3). Same visual/v-model contract as Input.
withDefaults(
  defineProps<{
    modelValue?: string
    placeholder?: string
    disabled?: boolean
    readonly?: boolean
    invalid?: boolean
    rows?: number
  }>(),
  { modelValue: '', disabled: false, readonly: false, invalid: false, rows: 4 },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}
</script>

<style scoped>
.ui-textarea {
  width: 100%;
  min-width: 0;
  padding: 10px var(--fg2-control-pad-x);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font-size: var(--fg2-control-font-size);
  line-height: 1.6;
  resize: vertical;
}

.ui-textarea::placeholder {
  color: var(--fg2-muted);
  opacity: 1;
}

.ui-textarea:disabled {
  color: var(--fg2-muted);
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
  resize: none;
}

.ui-textarea:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.ui-textarea.is-invalid {
  border-color: var(--fg2-control-invalid-border);
}
</style>
