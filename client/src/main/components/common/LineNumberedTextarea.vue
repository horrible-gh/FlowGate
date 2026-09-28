<template>
  <div class="line-numbered-textarea" :class="{ 'line-numbered-textarea--wrap': !wrapOff }">
    <pre ref="gutterRef" class="line-numbered-textarea__gutter" aria-hidden="true">{{ lineNumbers }}</pre>
    <textarea
      ref="textareaRef"
      class="line-numbered-textarea__editor"
      :value="modelValue"
      :wrap="wrapOff ? 'off' : 'soft'"
      :readonly="readonly"
      :disabled="disabled"
      :spellcheck="spellcheck"
      :data-dialog-autofocus="dialogAutofocus ? '' : undefined"
      @input="onInput"
      @scroll="syncScroll"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string
  wrapOff?: boolean
  readonly?: boolean
  disabled?: boolean
  spellcheck?: boolean
  dialogAutofocus?: boolean
}>(), {
  wrapOff: true,
  readonly: false,
  disabled: false,
  spellcheck: false,
  dialogAutofocus: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const gutterRef = ref<HTMLElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)

const lineCount = computed(() => {
  let count = 1
  const value = props.modelValue
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code === 10) count += 1
    else if (code === 13 && value.charCodeAt(index + 1) !== 10) count += 1
  }
  return count
})

const lineNumbers = computed(() =>
  Array.from({ length: lineCount.value }, (_, index) => String(index + 1)).join('\n'),
)

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function syncScroll() {
  if (!textareaRef.value || !gutterRef.value) return
  gutterRef.value.scrollTop = textareaRef.value.scrollTop
}

defineExpose({ textareaRef, gutterRef, syncScroll })
</script>

<style scoped>
.line-numbered-textarea {
  --line-editor-bg: #fff;
  --line-editor-color: inherit;
  --line-editor-gutter-bg: #f8fafc;
  --line-editor-gutter-color: #64748b;
  --line-editor-gutter-border: #e2e8f0;
  --line-editor-font-family: var(--mono, ui-monospace, monospace);
  --line-editor-font-size: .86rem;
  --line-editor-line-height: 1.5;
  --line-editor-padding-y: 12px;
  --line-editor-padding-x: 12px;
  --line-editor-tab-size: 2;

  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--line-editor-bg);
  color: var(--line-editor-color);
  font-family: var(--line-editor-font-family);
  font-size: var(--line-editor-font-size);
  line-height: var(--line-editor-line-height);
}

.line-numbered-textarea__gutter {
  box-sizing: border-box;
  min-width: 3.5em;
  height: 100%;
  margin: 0;
  overflow: hidden;
  padding: var(--line-editor-padding-y) 8px;
  border-right: 1px solid var(--line-editor-gutter-border);
  background: var(--line-editor-gutter-bg);
  color: var(--line-editor-gutter-color);
  font: inherit;
  line-height: inherit;
  text-align: right;
  white-space: pre;
  user-select: none;
  pointer-events: none;
}

.line-numbered-textarea__editor {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  margin: 0;
  resize: none;
  border: 0;
  outline: none;
  overflow: auto;
  padding: var(--line-editor-padding-y) var(--line-editor-padding-x);
  background: var(--line-editor-bg);
  color: inherit;
  font: inherit;
  line-height: inherit;
  tab-size: var(--line-editor-tab-size);
  white-space: pre;
  overflow-wrap: normal;
}

.line-numbered-textarea--wrap .line-numbered-textarea__editor {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
