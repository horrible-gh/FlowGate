<template>
  <div
    class="ui-tabs"
    :class="{ 'is-vertical': orientation === 'vertical' }"
    role="tablist"
    :aria-label="ariaLabel"
    :aria-orientation="orientation"
    :data-orientation="orientation"
  >
    <button
      v-for="tab in tabs"
      :key="tab.key"
      :ref="(el) => setButtonRef(tab.key, el)"
      type="button"
      role="tab"
      class="ui-tab"
      :class="{ 'is-active': tab.key === modelValue }"
      :data-tone="tab.tone || undefined"
      :data-tab="tab.key"
      :aria-selected="tab.key === modelValue"
      :disabled="tab.disabled"
      :tabindex="tab.key === modelValue ? 0 : -1"
      @click="select(tab.key)"
      @keydown="onKeydown($event, tab.key)"
    >
      <slot name="tab" :tab="tab">{{ tab.label }}</slot>
    </button>
  </div>
</template>

<script setup lang="ts">
// Domain-free tab strip: visual selection + interaction only. The screen owns
// the tab list, the active value and what changing tabs does — this component never
// hardcodes a feature's tab set or reads router/feature state itself.
// `orientation` (flowgate.v02test.0096 ④): 'horizontal' (default, a row with ← →) or 'vertical' (a column with ↑ ↓),
// so a screen can switch its tab placement with one prop.
import { nextTick } from 'vue'

export interface TabItem {
  key: string
  label: string
  disabled?: boolean
  /** A colour the screen gives the tab (e.g. `info`, `danger`); the screen styles `[data-tone]`. */
  tone?: string | null
}

export type TabOrientation = 'horizontal' | 'vertical'

const props = withDefaults(
  defineProps<{
    tabs: TabItem[]
    modelValue: string
    ariaLabel?: string
    orientation?: TabOrientation
  }>(),
  { ariaLabel: undefined, orientation: 'horizontal' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string]; change: [value: string] }>()

const buttonRefs = new Map<string, HTMLButtonElement>()

function setButtonRef(key: string, el: unknown) {
  if (el) buttonRefs.set(key, el as HTMLButtonElement)
  else buttonRefs.delete(key)
}

function select(key: string) {
  const tab = props.tabs.find((t) => t.key === key)
  if (!tab || tab.disabled || key === props.modelValue) return
  emit('update:modelValue', key)
  emit('change', key)
}

function onKeydown(event: KeyboardEvent, key: string) {
  const vertical = props.orientation === 'vertical'
  const next = vertical ? 'ArrowDown' : 'ArrowRight'
  const prev = vertical ? 'ArrowUp' : 'ArrowLeft'
  if (event.key !== next && event.key !== prev) return
  event.preventDefault()
  const enabled = props.tabs.filter((t) => !t.disabled)
  const idx = enabled.findIndex((t) => t.key === key)
  if (idx === -1 || enabled.length === 0) return
  const delta = event.key === next ? 1 : -1
  const target = enabled[(idx + delta + enabled.length) % enabled.length]
  select(target.key)
  // Keyboard focus must follow selection to the newly active tab (WAI-ARIA APG tabs
  // pattern) regardless of whether the controlling parent has re-applied modelValue yet.
  nextTick(() => buttonRefs.get(target.key)?.focus())
}
</script>

<style scoped>
.ui-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--fg2-line);
}

.ui-tab {
  padding: 9px 14px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--fg2-muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: 0.16s ease;
}

.ui-tab:hover:not(:disabled) {
  color: var(--fg2-text);
}

.ui-tab:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.ui-tab.is-active {
  color: var(--fg2-text);
  border-bottom-color: var(--fg2-blue);
}

.ui-tab:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

/* Vertical: a column of tabs with the selection mark on the right edge instead of the bottom. */
.ui-tabs.is-vertical {
  flex-direction: column;
  gap: 2px;
  border-bottom: 0;
  border-right: 1px solid var(--fg2-line);
}

.ui-tabs.is-vertical .ui-tab {
  text-align: left;
  border-bottom: 0;
  border-right: 2px solid transparent;
}

.ui-tabs.is-vertical .ui-tab.is-active {
  border-right-color: var(--fg2-blue);
}
</style>
