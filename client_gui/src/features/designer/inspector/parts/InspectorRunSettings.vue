<template>
  <!-- flowgate.v02.0051 — the v7 run settings block (0035 insp.js execSettings `실행 설정`), common to
       the 조사 · 작업 cards and the base of the 테스트 settings: a 2 × 2 grid of selects — 실행 AI ·
       재시작 | 검수 AI · 검수 횟수 — and a "고급 ▾" fold with 실행 제한 시간 · 검수 기준. Display only:
       each field comes in as its value, choices and whether it may change (the feature decides from
       the server; a field with no connection yet is disabled with an empty list and shows `—`), and a
       change leaves as `update:field`. `locked` is the running card (v7 `.set-locked`).
       flowgate.v02.0053 T#7 — `layout="scenario"` is the 테스트 card's 시나리오 작성 block (v7 testSettings
       "케이스 작성", its TSC mark left out): 작성 AI | 검수 AI · 검수 횟수, no fold. A field whose change the
       server refused carries its reason: a red ! icon beside the control, the reason its tooltip.
       flowgate.v02.0072 T#3 — `labels`: the caller may name an item its own way (the WP card's 작업 AI); the layout,
       the controls and the fold stay the same. -->
  <InspectorBlock
    :title="t(k(layout === 'scenario' ? 'scenario_title' : 'title'))"
    :locked="locked"
    class="inspector-run-settings"
    data-test="inspector-run-settings"
    :data-layout="layout"
    :data-advanced="advancedOpen ? 'open' : 'closed'"
  >
    <template v-if="layout === 'run'" #head>
      <Button size="sm" variant="secondary" :disabled="locked" :aria-expanded="advancedOpen" data-test="inspector-run-settings-advanced" @click="emit('update:advancedOpen', !advancedOpen)">
        {{ t(k('advanced')) }} {{ advancedOpen ? '▴' : '▾' }}
      </Button>
    </template>
    <div class="inspector-run-settings-grid">
      <div v-for="(column, index) in columns" :key="index">
        <InspectorControlRow v-for="name in column" :key="name" :label="labelOf(name)" :hint="fields[name]?.reason ?? null">
          <Select
            :model-value="fields[name]?.value ?? ''"
            :options="optionsOf(name)"
            :placeholder="placeholderOf(name)"
            :disabled="locked || !fields[name] || fields[name]!.disabled === true"
            :data-test="`inspector-run-settings-${name}`"
            @update:model-value="(value: string) => emit('update:field', name, value)"
          />
          <InspectorFailureIcon v-if="fields[name]?.failure" :name="t(k('failed'))" :reason="fields[name]!.failure!" :data-field="name" />
        </InspectorControlRow>
      </div>
    </div>
    <div v-if="layout === 'run' && advancedOpen" class="inspector-run-settings-advanced" data-test="inspector-run-settings-advanced-panel">
      <div v-for="name in ADVANCED" :key="name" class="inspector-run-settings-card">
        <InspectorControlRow :label="labelOf(name)" :hint="fields[name]?.reason ?? null">
          <Select
            :model-value="fields[name]?.value ?? ''"
            :options="optionsOf(name)"
            :placeholder="placeholderOf(name)"
            :disabled="locked || !fields[name] || fields[name]!.disabled === true"
            :data-test="`inspector-run-settings-${name}`"
            @update:model-value="(value: string) => emit('update:field', name, value)"
          />
          <InspectorFailureIcon v-if="fields[name]?.failure" :name="t(k('failed'))" :reason="fields[name]!.failure!" :data-field="name" />
        </InspectorControlRow>
      </div>
    </div>
  </InspectorBlock>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import Select, { type SelectOption } from '../../../../components/ui/Select.vue'
import InspectorBlock from './InspectorBlock.vue'
import InspectorControlRow from './InspectorControlRow.vue'
import InspectorFailureIcon from './InspectorFailureIcon.vue'

export type InspectorRunSettingsField = 'run_ai' | 'restart' | 'review_ai' | 'review_count' | 'time_limit' | 'review_basis'

export interface InspectorRunSettingsValue {
  /** The shown value ('' = none / the placeholder). */
  readonly value: string
  readonly options: readonly SelectOption[]
  /** The field exists but may not change now (not connected, read only, started …). */
  readonly disabled?: boolean
  /** Why it may not change (hover hint on the label). */
  readonly reason?: string | null
  /** Why the server refused the last change of the field (the tooltip of the ! icon), or null. */
  readonly failure?: string | null
}

/** A field absent from `fields` is shown disabled and empty (the v7 item stays on screen). */
export type InspectorRunSettingsFields = Partial<Readonly<Record<InspectorRunSettingsField, InspectorRunSettingsValue>>>

/** v7 execSettings: 실행 AI · 재시작 | 검수 AI · 검수 횟수. */
const RUN_COLUMNS: readonly (readonly InspectorRunSettingsField[])[] = [
  ['run_ai', 'restart'],
  ['review_ai', 'review_count'],
]
/** v7 testSettings: 작성 AI | 검수 AI · 검수 횟수. */
const SCENARIO_COLUMNS: readonly (readonly InspectorRunSettingsField[])[] = [['run_ai'], ['review_ai', 'review_count']]
const ADVANCED: readonly InspectorRunSettingsField[] = ['time_limit', 'review_basis']

const props = withDefaults(
  defineProps<{
    fields: InspectorRunSettingsFields
    locked?: boolean
    advancedOpen?: boolean
    layout?: 'run' | 'scenario'
    /** Item names given by the caller instead of the common ones (absent = the common name). */
    labels?: Partial<Readonly<Record<InspectorRunSettingsField, string>>>
  }>(),
  { locked: false, advancedOpen: false, layout: 'run', labels: () => ({}) },
)

const emit = defineEmits<{ 'update:field': [field: InspectorRunSettingsField, value: string]; 'update:advancedOpen': [open: boolean] }>()

const { t } = useI18n()

const k = (key: string) => `gui.designer.inspector.run_settings.${key}`

const columns = computed(() => (props.layout === 'scenario' ? SCENARIO_COLUMNS : RUN_COLUMNS))

/** The scenario block's AI writes the scenario (작성 AI); a name the caller gives wins. */
function labelOf(name: InspectorRunSettingsField): string {
  const own = props.labels[name]
  if (own) return own
  return t(k(props.layout === 'scenario' && name === 'run_ai' ? 'writer_ai' : name))
}

function optionsOf(name: InspectorRunSettingsField): SelectOption[] {
  return [...(props.fields[name]?.options ?? [])]
}

/**
 * A field without a value shows `—`: the item is there, nothing is chosen (or connected) yet. A field whose
 * '' is a choice of its own (an AI's 담당 AI / 기본 검수 AI) shows that choice instead.
 */
function placeholderOf(name: InspectorRunSettingsField): string | undefined {
  const field = props.fields[name]
  if (!field) return '—'
  return field.value === '' && !field.options.some((option) => option.value === '') ? '—' : undefined
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
