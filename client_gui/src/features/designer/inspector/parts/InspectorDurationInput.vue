<template>
  <!-- flowgate.v02test.0081 (0029-T, 시안 v4): one time setting as two boxes — [number][unit ▾ 시간 / 분 / 초] — kept in
       seconds (number × unit). It first shows the largest unit that divides the value (14400 → [4][시간], 150 →
       [150][초]). The number is taken on Enter or when the box loses focus (Esc puts the shown value back); a unit picked
       is taken at once with the number in the box. A value outside [min, max] is moved to the nearest edge and a line
       under the boxes says so. Display only: the value comes in and leaves (`update:modelValue`) as seconds.
       (0030-TR rework) The number is taken when focus leaves the whole pair, not the number box alone: moving from the
       number box to the unit box keeps the typed number, so picking a unit takes typed number × picked unit once
       ([4][시간] → type 90 → pick 분 → 5400 sent once). Esc that puts a typed number back stops there (the ⚙ window it
       sits in stays open); Esc with nothing typed goes on (and closes the window).
       (0030-TR 3rd) Esc is caught on the pair, not the number box alone: after Tab to the unit box the typed number is still
       being edited, and an Esc there puts it back and keeps the window open just the same. -->
  <span class="inspector-duration" :data-test="testId" :data-value="modelValue ?? ''" @focusout="onFocusOut" @keydown.esc="onEsc">
    <span class="inspector-duration-boxes">
      <input
        ref="amountBox"
        class="inspector-duration-amount"
        type="text"
        inputmode="numeric"
        :value="amountText"
        :disabled="disabled"
        :aria-label="t(k('amount'), { label: label ?? '' })"
        :aria-invalid="invalid ? 'true' : undefined"
        :data-test="testId ? `${testId}-amount` : undefined"
        @input="onInput(($event.target as HTMLInputElement).value)"
        @keydown.enter.prevent="commit"
      />
      <Select
        class="inspector-duration-unit"
        :model-value="shownUnit"
        :options="unitOptions"
        :disabled="disabled"
        :aria-label="t(k('unit'), { label: label ?? '' })"
        :data-test="testId ? `${testId}-unit` : undefined"
        @update:model-value="(value: string) => onUnit(value as DurationUnit)"
      />
    </span>
    <span v-if="note" class="inspector-duration-note" role="status" :data-clamped="note.edge" :data-test="testId ? `${testId}-note` : undefined">{{ noteText }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Select from '../../../../components/ui/Select.vue'
import {
  DURATION_UNITS,
  durationAmountOf,
  durationClamp,
  durationParts,
  durationSeconds,
  durationSplit,
  durationUnitOf,
  type DurationUnit,
} from './durationModel'

const props = withDefaults(
  defineProps<{
    /** The value in seconds (null: none yet — the boxes stay empty). */
    modelValue: number | null
    min: number
    max: number
    disabled?: boolean
    /** The setting's name (the boxes' accessible names). */
    label?: string | null
    /** `data-test` of the whole; the boxes get `-amount` / `-unit`, the note `-note`. */
    testId?: string
  }>(),
  { disabled: false, label: null, testId: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [seconds: number] }>()

const { t } = useI18n()
const k = (key: string) => `gui.designer.inspector.run_settings.duration.${key}`

/** The unit picked last (kept while it divides the value), null = the value's own largest unit. */
const unit = ref<DurationUnit | null>(null)
/** The number being typed (null: the box shows the value). */
const typed = ref<string | null>(null)
/** The edge the last taken value was moved to, and that value. */
const note = ref<{ edge: 'min' | 'max'; value: number } | null>(null)

const split = computed(() => (props.modelValue === null ? null : durationSplit(props.modelValue, unit.value)))
const shownUnit = computed<DurationUnit>(() => split.value?.unit ?? unit.value ?? durationUnitOf(props.min))
const amountText = computed(() => typed.value ?? (split.value ? String(split.value.amount) : ''))
const invalid = computed(() => typed.value !== null && typed.value.trim() !== '' && durationAmountOf(typed.value) === undefined)

const unitOptions = computed(() => DURATION_UNITS.map(([name]) => ({ value: name, label: t(k(name)) })))

function formatted(seconds: number): string {
  return durationParts(seconds).map(([n, part]) => t(k(`part_${part}`), { n })).join(' ')
}
const noteText = computed(() => (note.value ? t(k(`clamped_${note.value.edge}`), { value: formatted(note.value.value) }) : ''))

// A value that comes from outside (another card, the server's answer) drops what was being typed.
watch(() => props.modelValue, () => { typed.value = null })

const amountBox = ref<HTMLInputElement | null>(null)

function onInput(text: string) {
  typed.value = text
}

/**
 * The box shows the value again. When the typed text was never drawn (typed and taken in one go) the drawn value has not
 * changed, so nothing would write the box: it is written here once the update is drawn.
 */
function showValue() {
  typed.value = null
  void nextTick(() => {
    if (typed.value === null && amountBox.value && amountBox.value.value !== amountText.value) amountBox.value.value = amountText.value
  })
}

function revert() {
  showValue()
}

/**
 * Esc in either box of the pair (the number box, or the unit box after Tab): puts a typed number back and keeps the key to
 * itself; with nothing typed the key goes on (closes a window).
 */
function onEsc(event: KeyboardEvent) {
  if (typed.value === null) return
  event.preventDefault()
  event.stopPropagation()
  revert()
}

/**
 * Focus left one of the two boxes. Going to the other box of the pair (number → unit) takes nothing: the typed number
 * waits for the unit picked there. Leaving the pair takes the typed number in the shown unit.
 */
function onFocusOut(event: FocusEvent) {
  const to = event.relatedTarget as Node | null
  const root = (event.currentTarget as Element | null) ?? null
  if (to && root?.contains(to)) return
  commit()
}

/** Takes number × unit: moved inside the range (and said so), sent when it differs from the value. */
function take(amount: number, at: DurationUnit) {
  const { value, clamped } = durationClamp(durationSeconds(amount, at), props.min, props.max)
  note.value = clamped ? { edge: clamped, value } : null
  showValue()
  if (value !== props.modelValue) emit('update:modelValue', value)
}

function commit() {
  if (props.disabled || typed.value === null) return
  const amount = durationAmountOf(typed.value)
  if (amount === undefined) return revert()
  take(amount, shownUnit.value)
}

function onUnit(next: DurationUnit) {
  if (props.disabled) return
  const amount = typed.value !== null ? durationAmountOf(typed.value) : split.value?.amount
  unit.value = next
  if (amount === undefined) return revert()
  take(amount, next)
}
</script>

<style scoped>
.inspector-duration {
  display: inline-flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.inspector-duration-boxes {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.inspector-duration-amount {
  box-sizing: border-box;
  flex: none;
  width: 64px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong, #2c4b64);
  border-radius: 6px;
  background: var(--fg2-control-surface, #0c1c2e);
  color: inherit;
  font: inherit;
  font-size: 12px;
  text-align: right;
}

.inspector-duration-amount:focus-visible {
  outline: 2px solid var(--fg2-focus, #4aa3ff);
  outline-offset: 1px;
}

.inspector-duration-amount:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity, 0.55);
}

.inspector-duration-amount[aria-invalid='true'] {
  border-color: #e5484d;
}

/* 0031-T: wide enough for the longest unit name in each language ('hours' / '시간') after the select's own padding
   (12px left, 34px for its ▾), so a unit is never cut ('hour:'). */
.inspector-duration-unit {
  flex: none;
  width: 96px;
}

.inspector-duration-note {
  color: #f0a23a;
  font-size: 11px;
  line-height: 1.3;
}
</style>
