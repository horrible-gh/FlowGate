<template>
  <!-- flowgate.v02test.0108 F108-6: the ⚙ window's [숫자][단위▾] time input, shared by 명령 실행 제한 (대화 tab) and
       채팅 최대 시간 (AI tab). The value is seconds, or null for 제한 없음 (the number box is then disabled and shows —).
       A unit pick saves the shown number × the new unit at once (1 when coming from 제한 없음); a typed number saves on
       Enter / blur and Esc puts the shown value back. Only positive whole numbers are kept, and a value outside
       min~max is pulled to the nearest end. A stored value opens in the largest unit that divides it (시간 → 분 → 초). -->
  <span class="cdi" :data-test="dataTest">
    <input
      v-model="text"
      type="text"
      inputmode="numeric"
      autocomplete="off"
      class="cdi-num"
      :placeholder="unit === 'none' ? '—' : ''"
      :disabled="disabled || unit === 'none'"
      :aria-label="label"
      :data-test="`${dataTest}-value`"
      @input="onInput"
      @keydown.enter.prevent="commitText"
      @keydown.esc.stop.prevent="revert"
      @blur="commitText"
    />
    <select
      :value="unit"
      class="cdi-unit"
      :disabled="disabled"
      :aria-label="label"
      :data-test="`${dataTest}-unit`"
      @change="onUnitChange($event.target as HTMLSelectElement)"
    >
      <option v-for="u in UNITS" :key="u" :value="u">{{ t(`gui.conversation.settings.duration_unit_${u}`) }}</option>
    </select>
  </span>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

type DurationUnit = 'none' | 'hour' | 'minute' | 'second'
const UNITS: readonly DurationUnit[] = ['none', 'hour', 'minute', 'second']
const UNIT_SECONDS: Record<Exclude<DurationUnit, 'none'>, number> = { hour: 3600, minute: 60, second: 1 }

const props = withDefaults(defineProps<{
  /** Seconds, or null for 제한 없음. */
  modelValue: number | null
  /** Allowed range in seconds (inclusive). */
  min: number
  max: number
  disabled?: boolean
  label?: string
  dataTest?: string
}>(), { disabled: false, label: '', dataTest: 'duration' })

const emit = defineEmits<{ 'update:modelValue': [seconds: number | null] }>()

const { t } = useI18n()

const unit = ref<DurationUnit>('none')
const text = ref('')

/** The largest unit that divides the value (시간 → 분 → 초); null is 제한 없음. */
function split(seconds: number | null): { unit: DurationUnit; text: string } {
  if (seconds == null) return { unit: 'none', text: '' }
  for (const u of ['hour', 'minute'] as const) {
    if (seconds % UNIT_SECONDS[u] === 0) return { unit: u, text: String(seconds / UNIT_SECONDS[u]) }
  }
  return { unit: 'second', text: String(seconds) }
}

/** The seconds the boxes show now, or undefined while the typed text is not a positive whole number. */
function shownSeconds(): number | null | undefined {
  if (unit.value === 'none') return null
  const n = parsePositive(text.value)
  return n == null ? undefined : n * UNIT_SECONDS[unit.value]
}

/**
 * A positive whole number typed with digits only, or null (0, empty, other characters). A number too big to be exact
 * (over Number.MAX_SAFE_INTEGER, even Infinity) is still kept: min~max is far below it, so the clamp pulls it to max.
 */
function parsePositive(value: string): number | null {
  const s = value.trim()
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n >= 1 ? n : null
}

const clamp = (seconds: number) => Math.min(props.max, Math.max(props.min, seconds))

function revert(): void {
  const shown = split(props.modelValue)
  unit.value = shown.unit
  text.value = shown.text
}

// A new value from outside (reopen, a failed save putting the old one back, a pulled-in value) is redrawn; a value
// that is what the boxes already show keeps the unit the person picked.
watch(() => props.modelValue, (value) => {
  if (shownSeconds() !== value) revert()
}, { immediate: true })

function save(seconds: number | null): void {
  if (seconds === props.modelValue) {
    // Same value (e.g. pulled to the same end): just show it again in the picked unit or the stored form.
    if (shownSeconds() !== seconds) revert()
    return
  }
  emit('update:modelValue', seconds)
}

/** Digits only while typing. */
function onInput(): void {
  const digits = text.value.replace(/\D+/g, '')
  if (digits !== text.value) text.value = digits
}

function commitText(): void {
  if (unit.value === 'none') return
  const n = parsePositive(text.value)
  if (n == null) {
    revert()
    return
  }
  const seconds = clamp(n * UNIT_SECONDS[unit.value])
  if (seconds !== n * UNIT_SECONDS[unit.value]) {
    const pulled = split(seconds)
    unit.value = pulled.unit
    text.value = pulled.text
  }
  save(seconds)
}

function onUnitChange(select: HTMLSelectElement): void {
  const next = UNITS.find((u) => u === select.value) ?? 'none'
  if (next === 'none') {
    unit.value = 'none'
    text.value = ''
    save(null)
    return
  }
  const n = unit.value === 'none' ? 1 : (parsePositive(text.value) ?? 1)
  const raw = n * UNIT_SECONDS[next]
  const seconds = clamp(raw)
  if (seconds === raw) {
    unit.value = next
    text.value = String(n)
  } else {
    const pulled = split(seconds)
    unit.value = pulled.unit
    text.value = pulled.text
  }
  save(seconds)
  // The pick may be pulled back to the unit already shown (e.g. [30][분] → 시간 = 1800초 = [30][분]): `unit` did not
  // change then, so Vue would not touch the select and it would keep showing the picked 시간. Put it back by hand.
  if (select.value !== unit.value) select.value = unit.value
}
</script>

<style scoped>
/* [숫자][단위▾] on one line, the ⚙ window's control height (--csx-*) and the --fg2-* control tokens. */
.cdi {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 4px;
  min-width: 0;
}

.cdi-num,
.cdi-unit {
  height: var(--csx-control-height, 26px);
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: inherit;
  padding: 0 5px;
}

.cdi-num:disabled,
.cdi-unit:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.cdi-num:focus-visible,
.cdi-unit:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
