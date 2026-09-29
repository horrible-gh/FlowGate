<template>
  <button
    type="button"
    class="ui-toggle"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="ariaLabel"
    :disabled="disabled"
    :class="{ 'is-on': modelValue }"
    @click="onToggle"
  >
    <span class="ui-toggle-track"><span class="ui-toggle-thumb" /></span>
  </button>
</template>

<script setup lang="ts">
// Domain-free boolean switch (T0004 §5.5). It only reports true/false — it never knows
// whether that boolean means "AI provider enabled" or anything else; the caller owns that
// meaning and any persistence.
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    ariaLabel?: string
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

function onToggle() {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}
</script>

<style scoped>
.ui-toggle {
  display: inline-flex;
  align-items: center;
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}

.ui-toggle:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity-strong);
}

.ui-toggle:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.ui-toggle-track {
  position: relative;
  width: 40px;
  height: 22px;
  border-radius: var(--fg2-control-radius-pill);
  border: 1px solid var(--fg2-line-strong);
  background: rgba(12, 24, 38, 0.9);
  transition: 0.16s ease;
}

.ui-toggle-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--fg2-control-fg);
  transition: 0.16s ease;
}

.ui-toggle.is-on .ui-toggle-track {
  border-color: rgba(75, 163, 255, 0.6);
  background: linear-gradient(180deg, #3d8cff, #2a6fd6);
}

.ui-toggle.is-on .ui-toggle-thumb {
  left: 20px;
  background: #f4f9ff;
}
</style>
