<template>
  <!-- flowgate.v02.0048 T#9 — settings tab (D#1 §10.2 T#9 row, D#2 §2.2): the run settings of the selected
       Node. A thin container: the composables read and save, the models decide the values, the choices and
       availability.
       flowgate.v02.0053 T#7 — drawn as v7 draws it (0035 insp.js execSettings / testSettings), on every
       조사 · 작업 · 설계 · Custom · 테스트 card, just placed or done alike: the 🔒 실행 중 line of a running
       card, the 지시서 사용 / 생략 row, the 검수 뒤 진행 row (its short state on the right), then the 실행 설정
       block (실행 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ 실행 제한 시간 · 검수 기준) — or, on a 테스트 card,
       the 시나리오 작성 block (작성 AI | 검수 AI · 검수 횟수; its 실행 block is testRunSettings'). A change is
       saved at once (no save button, no "saved" line). Explanations are tooltips; a refused change is a red !
       beside its control. A Graph card keeps its settings itself (T#2, useCardSettings); a v0.1 Flow's Node
       keeps the three of its sequence row (0048, useSettings) and the rest is drawn off. A failed read with
       nothing shown is the read failure icon (why: its tooltip) with a ↻ retry, as on every settings form. -->
  <div class="inspector-settings" data-test="inspector-settings" :data-source="source" :data-kind="kind" :data-status="status">
    <InspectorReadFailure v-if="errorKey" :reason="t(k(errorKey))" @retry="reload" />
    <template v-else>
      <InspectorLockNote v-if="locked" />
      <InspectorBlock :locked="locked" class="inspector-settings-line" data-test="settings-instruction">
        <InspectorControlRow :label="t(k('instruction.label'))" :hint="t(k('instruction.hint'))">
          <InspectorSegment
            :model-value="segment('instruction').value"
            :options="segmentOptions('instruction', ['use', 'omit'])"
            :disabled="segment('instruction').disabled"
            :aria-label="t(k('instruction.label'))"
            data-test="settings-instruction-switch"
            @update:model-value="(value: string) => onChange('instruction', value)"
          />
          <InspectorFailureIcon v-if="failureOf('instruction')" :name="t(k('failed'))" :reason="failureOf('instruction')!" data-field="instruction" />
        </InspectorControlRow>
      </InspectorBlock>
      <InspectorBlock :locked="locked" class="inspector-settings-line" data-test="settings-policy">
        <InspectorControlRow :label="t(k('after_review.label'))" :hint="t(k('after_review.hint'))" wrap>
          <InspectorSegment
            :model-value="segment('after_review').value"
            :options="segmentOptions('after_review', ['follow', 'human'])"
            :disabled="segment('after_review').disabled"
            :aria-label="t(k('after_review.label'))"
            data-test="settings-policy-switch"
            @update:model-value="(value: string) => onChange('after_review', value)"
          />
          <span v-if="policyNote" class="inspector-settings-muted" data-test="settings-policy-note">{{ policyNote }}</span>
          <InspectorFailureIcon v-if="failureOf('after_review')" :name="t(k('failed'))" :reason="failureOf('after_review')!" data-field="after_review" />
        </InspectorControlRow>
      </InspectorBlock>
      <InspectorRunSettings
        :layout="kind === 'test' ? 'scenario' : 'run'"
        :fields="runFields"
        :locked="locked"
        :advanced-open="advancedOpen"
        @update:advanced-open="advancedOpen = $event"
        @update:field="onChange"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import InspectorRunSettings, { type InspectorRunSettingsField, type InspectorRunSettingsFields } from '../../parts/InspectorRunSettings.vue'
import InspectorSegment, { type InspectorSegmentOption } from '../../parts/InspectorSegment.vue'
import { inspectorSettingsKind, type InspectorTarget } from '../../inspectorTarget'
import {
  CARD_SETTINGS_FIELDS,
  cardSettingsAiOptions,
  cardSettingsChoiceOptions,
  cardSettingsDisabledReason,
  cardSettingsErrorKey,
  cardSettingsFailureKey,
  cardSettingsReviewOff,
  cardSettingsText,
  cardSettingsValue,
  cardSettingsValueOf,
  type CardSettingsField,
} from './cardSettingsModel'
import { settingsRowPhase, type SettingsField, type SettingsOption } from './settingsModel'
import { useCardSettings } from './useCardSettings'
import { useSettings } from './useSettings'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.settings.${key}`

/** card = a Graph card (T#2 card settings); sequence = a v0.1 Flow's Node (its sequence row). */
const source = computed<'card' | 'sequence'>(() => (props.target.nodeType !== null ? 'card' : 'sequence'))
const kind = computed(() => (inspectorSettingsKind(props.target) === 'test' ? 'test' : 'run'))

const card = useCardSettings(() => (source.value === 'card' ? props.target : null))
const sequence = useSettings(() => (source.value === 'sequence' ? props.target : null))

/** The 0048 sequence fields behind the v7 items; the other items have no place on a sequence row. */
const SEQUENCE_FIELD: Readonly<Partial<Record<CardSettingsField, SettingsField>>> = {
  run_ai: 'provider',
  review_count: 'reviewCount',
  review_ai: 'reviewer',
}

const advancedOpen = ref(false)

const status = computed(() => (source.value === 'card' ? card.status.value : sequence.status.value))

const errorKey = computed(() => {
  if (source.value === 'card') {
    const failed = card.errorKind.value
    return failed && !card.node.value ? cardSettingsErrorKey(failed) : null
  }
  return sequence.errorKey.value && !sequence.row.value ? sequence.errorKey.value : null
})

/** The 🔒 실행 중 line: a running card (server lock), or a v0.1 step started and not done yet. */
const locked = computed(() => {
  if (source.value === 'card') return card.node.value?.locked === true
  const row = sequence.row.value
  return row !== null && settingsRowPhase(row) === 'in_progress'
})

/** Why nothing on a Graph card can change now (message key), or null. */
const cardReason = computed(() =>
  cardSettingsDisabledReason({
    readOnly: props.target.readOnly,
    placing: card.placing.value,
    read: card.node.value !== null,
    locked: card.node.value?.locked === true,
    blocked: card.blocked.value,
  }),
)

const labelOf = (option: SettingsOption) => option.label ?? t(k(option.key ?? ''), option.params)

interface FieldView {
  readonly value: string
  readonly options: readonly SettingsOption[]
  readonly disabled: boolean
  /** Why it is off (message key), or null. */
  readonly reason: string | null
}

function cardField(field: CardSettingsField): FieldView {
  const node = card.node.value
  const value = cardSettingsValue(node, card.pending.value, field)
  let options: SettingsOption[]
  if (field === 'run_ai' || field === 'review_ai') options = cardSettingsAiOptions(field, card.choices.value?.providers ?? [], value)
  else options = cardSettingsChoiceOptions(field, node?.fields[field]?.choices, value)
  let reason = cardReason.value
  if (reason === null && field === 'review_ai' && cardSettingsReviewOff(cardSettingsValue(node, card.pending.value, 'review_count'))) reason = 'reason.review_off'
  return { value: cardSettingsText(value), options, disabled: reason !== null, reason }
}

function sequenceField(field: CardSettingsField): FieldView {
  const mapped = SEQUENCE_FIELD[field]
  if (!mapped) return { value: '', options: [], disabled: true, reason: 'reason.no_place' }
  const view = sequence.fields.value?.[mapped]
  const item = sequence.availability.value[mapped]
  const row = sequence.row.value
  let reason = item.reason
  if (row && settingsRowPhase(row) === 'done') reason = 'reason.done'
  return { value: view?.value ?? '', options: view?.options ?? [], disabled: !item.enabled || reason === 'reason.done', reason }
}

const views = computed(() => {
  const out = {} as Record<CardSettingsField, FieldView>
  for (const field of CARD_SETTINGS_FIELDS.run) out[field] = source.value === 'card' ? cardField(field) : sequenceField(field)
  return out
})

function segment(field: 'instruction' | 'after_review') {
  const view = views.value[field]
  return { value: view.value === '' ? null : view.value, disabled: view.disabled }
}

function segmentOptions(field: 'instruction' | 'after_review', values: readonly string[]): InspectorSegmentOption[] {
  const reason = views.value[field].reason
  return values.map((value) => ({ value, label: t(k(`${field}.${value}`)), title: reason ? t(k(reason)) : null }))
}

/** v7 policyRow: the short state of the chosen 검수 뒤 진행. */
const policyNote = computed(() => {
  const value = views.value.after_review.value
  return value === 'follow' || value === 'human' ? t(k(`after_review.note_${value}`)) : null
})

/** Why the server refused the field's last change (the ! icon's tooltip), or null. */
function failureOf(field: CardSettingsField): string | null {
  if (source.value === 'card') {
    const last = card.failure.value
    return last && last.field === field ? t(k(cardSettingsFailureKey(last.kind))) : null
  }
  const last = sequence.failure.value
  return last && SEQUENCE_FIELD[field] === last.field ? t(k(last.key)) : null
}

const RUN_BLOCK_FIELDS: readonly InspectorRunSettingsField[] = ['run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'review_basis']

const runFields = computed<InspectorRunSettingsFields>(() => {
  const out: Partial<Record<InspectorRunSettingsField, InspectorRunSettingsFields[InspectorRunSettingsField]>> = {}
  const shown = kind.value === 'test' ? CARD_SETTINGS_FIELDS.test : RUN_BLOCK_FIELDS
  for (const name of RUN_BLOCK_FIELDS) {
    if (!shown.includes(name)) continue
    const view = views.value[name]
    out[name] = {
      value: view.value,
      options: view.options.map((option) => ({ value: option.value, label: labelOf(option) })),
      disabled: view.disabled,
      reason: view.reason ? t(k(view.reason)) : null,
      failure: failureOf(name),
    }
  }
  return out
})

function onChange(field: CardSettingsField, text: string) {
  if (views.value[field].disabled) return
  if (source.value === 'card') {
    const value = cardSettingsValueOf(field, text)
    if (value !== undefined) void card.change(field, value)
    return
  }
  const mapped = SEQUENCE_FIELD[field]
  if (mapped) void sequence.change(mapped, text)
}

function reload() {
  void (source.value === 'card' ? card.reload() : sequence.reload())
}
</script>

<style scoped>
.inspector-settings {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: 12px;
}

/* v7 instRow / policyRow: one-line blocks (`.blk` with 6px 12px) above the settings block; the blocks keep
   their own 8px apart (InspectorBlock). */
.inspector-settings-line {
  padding: 6px 12px;
}

.inspector-settings-muted {
  color: #7f96af;
  font-size: 11px;
}
</style>
