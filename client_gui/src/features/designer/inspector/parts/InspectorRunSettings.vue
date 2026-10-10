<template>
  <!-- flowgate.v02.0051 — the v7 run settings block (0035 insp.js execSettings `실행 설정`), common to
       the 조사 · 작업 cards and the base of the 테스트 settings: a 2 × 2 grid of selects — 실행 AI ·
       재시작 | 검수 AI · 검수 횟수 — and a "고급 ▾" fold with 실행 제한 시간 · 검수 기준. Display only:
       each field comes in as its value, choices and whether it may change (the feature decides from
       the server; a field with no connection yet is disabled with an empty list and shows `—`), and a
       change leaves as `update:field`. `locked` is the running card (v7 `.set-locked`). -->
  <InspectorBlock :title="t(k('title'))" :locked="locked" class="inspector-run-settings" data-test="inspector-run-settings" :data-advanced="advancedOpen ? 'open' : 'closed'">
    <template #head>
      <Button size="sm" variant="secondary" :disabled="locked" :aria-expanded="advancedOpen" data-test="inspector-run-settings-advanced" @click="emit('update:advancedOpen', !advancedOpen)">
        {{ t(k('advanced')) }} {{ advancedOpen ? '▴' : '▾' }}
      </Button>
    </template>
    <div class="inspector-run-settings-grid">
      <div>
        <InspectorControlRow v-for="name in LEFT" :key="name" :label="t(k(name))" :hint="fields[name]?.reason ?? null">
          <Select
            :model-value="fields[name]?.value ?? ''"
            :options="optionsOf(name)"
            :placeholder="placeholderOf(name)"
            :disabled="locked || !fields[name] || fields[name]!.disabled === true"
            :data-test="`inspector-run-settings-${name}`"
            @update:model-value="(value: string) => emit('update:field', name, value)"
          />
        </InspectorControlRow>
      </div>
      <div>
        <InspectorControlRow v-for="name in RIGHT" :key="name" :label="t(k(name))" :hint="fields[name]?.reason ?? null">
          <Select
            :model-value="fields[name]?.value ?? ''"
            :options="optionsOf(name)"
            :placeholder="placeholderOf(name)"
            :disabled="locked || !fields[name] || fields[name]!.disabled === true"
            :data-test="`inspector-run-settings-${name}`"
            @update:model-value="(value: string) => emit('update:field', name, value)"
          />
        </InspectorControlRow>
      </div>
    </div>
    <div v-if="advancedOpen" class="inspector-run-settings-advanced" data-test="inspector-run-settings-advanced-panel">
      <div v-for="name in ADVANCED" :key="name" class="inspector-run-settings-card">
        <InspectorControlRow :label="t(k(name))" :hint="fields[name]?.reason ?? null">
          <Select
            :model-value="fields[name]?.value ?? ''"
            :options="optionsOf(name)"
            :placeholder="placeholderOf(name)"
            :disabled="locked || !fields[name] || fields[name]!.disabled === true"
            :data-test="`inspector-run-settings-${name}`"
            @update:model-value="(value: string) => emit('update:field', name, value)"
          />
        </InspectorControlRow>
      </div>
    </div>
  </InspectorBlock>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import Select, { type SelectOption } from '../../../../components/ui/Select.vue'
import InspectorBlock from './InspectorBlock.vue'
import InspectorControlRow from './InspectorControlRow.vue'

export type InspectorRunSettingsField = 'run_ai' | 'restart' | 'review_ai' | 'review_count' | 'time_limit' | 'review_basis'

export interface InspectorRunSettingsValue {
  /** The shown value ('' = none / the placeholder). */
  readonly value: string
  readonly options: readonly SelectOption[]
  /** The field exists but may not change now (not connected, read only, started …). */
  readonly disabled?: boolean
  /** Why it may not change (hover hint on the label). */
  readonly reason?: string | null
}

/** A field absent from `fields` is shown disabled and empty (the v7 item stays on screen). */
export type InspectorRunSettingsFields = Partial<Readonly<Record<InspectorRunSettingsField, InspectorRunSettingsValue>>>

const LEFT: readonly InspectorRunSettingsField[] = ['run_ai', 'restart']
const RIGHT: readonly InspectorRunSettingsField[] = ['review_ai', 'review_count']
const ADVANCED: readonly InspectorRunSettingsField[] = ['time_limit', 'review_basis']

const props = withDefaults(
  defineProps<{
    fields: InspectorRunSettingsFields
    locked?: boolean
    advancedOpen?: boolean
  }>(),
  { locked: false, advancedOpen: false },
)

const emit = defineEmits<{ 'update:field': [field: InspectorRunSettingsField, value: string]; 'update:advancedOpen': [open: boolean] }>()

const { t } = useI18n()

const k = (key: string) => `gui.designer.inspector.run_settings.${key}`

function optionsOf(name: InspectorRunSettingsField): SelectOption[] {
  return [...(props.fields[name]?.options ?? [])]
}

/** A field without a value shows `—`: the item is there, nothing is chosen (or connected) yet. */
function placeholderOf(name: InspectorRunSettingsField): string | undefined {
  const field = props.fields[name]
  return !field || field.value === '' ? '—' : undefined
}
</script>

<style scoped>
.inspector-run-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 10px;
}

.inspector-run-settings :deep(.ui-select) {
  height: 32px;
  font-size: 12px;
}

.inspector-run-settings-advanced {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #29415d;
}

.inspector-run-settings-card {
  padding: 8px 11px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0c1c2e;
}

@media (max-width: 560px) {
  .inspector-run-settings-grid,
  .inspector-run-settings-advanced {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* flowgate.v02.0052 T#2 — the block is the container (InspectorBlock): one column once it is narrow. */
@container (max-width: 480px) {
  .inspector-run-settings-grid,
  .inspector-run-settings-advanced {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
