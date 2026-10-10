<template>
  <!-- flowgate.v02.0051 — the v7 segmented choice (0035 insp-v4 `.seg`): two or three exclusive
       options side by side (사용 / 생략 · 실행 방식 따름 / 사람 확인 필요 · 끄기 / 켜기 · 끝까지 /
       계획 확인 후). A radio group: one option is on; `disabled` keeps the look and refuses changes. -->
  <span class="inspector-segment" :class="`inspector-segment--${size}`" role="radiogroup" :aria-label="ariaLabel" data-test="inspector-segment">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="radio"
      class="inspector-segment-option"
      :class="{ 'is-on': option.value === modelValue }"
      :aria-checked="option.value === modelValue"
      :disabled="disabled || option.disabled"
      :title="option.title ?? undefined"
      data-test="inspector-segment-option"
      :data-value="option.value"
      @click="pick(option.value)"
    >{{ option.label }}</button>
  </span>
</template>

<script setup lang="ts">
export interface InspectorSegmentOption {
  readonly value: string
  readonly label: string
  readonly disabled?: boolean
  readonly title?: string | null
}

const props = withDefaults(
  defineProps<{
    options: readonly InspectorSegmentOption[]
    modelValue: string | null
    disabled?: boolean
    size?: 'sm' | 'md'
    ariaLabel?: string
  }>(),
  { disabled: false, size: 'sm', ariaLabel: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function pick(value: string) {
  if (props.disabled || value === props.modelValue) return
  emit('update:modelValue', value)
}
</script>

<style scoped>
.inspector-segment {
  display: inline-flex;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid #2d4d6d;
  border-radius: 7px;
  background: #0b1b2d;
}

.inspector-segment-option {
  height: 28px;
  padding: 0 11px;
  border: 0;
  background: transparent;
  color: #9fb5ca;
  font-size: 12px;
  cursor: pointer;
}

.inspector-segment--md .inspector-segment-option {
  height: 30px;
  padding: 0 14px;
}

.inspector-segment-option + .inspector-segment-option {
  border-left: 1px solid #2d4d6d;
}

.inspector-segment-option.is-on {
  background: #12355c;
  color: #d8ecff;
}

.inspector-segment-option:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.inspector-segment-option:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}
</style>
