<template>
  <!-- flowgate.v02.0048 T#9 — settings tab (D#1 §10.2 T#9 row, D#2 §2.2): the run settings of the selected
       Node. A thin container: the composables read and save, the models decide the values, the choices and
       availability.
       flowgate.v02.0053 T#7 — drawn as v7 draws it (0035 insp.js execSettings / testSettings), on every
       조사 · 작업 · 설계 · Custom · 테스트 card, just placed or done alike: the 🔒 실행 중 line of a running
       card, the 지시서 사용 / 생략 row, then the 실행 설정
       block (실행 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ 실행 제한 시간 · 검수 기준) — or, on a 테스트 card,
       the 시나리오 작성 block (작성 AI | 검수 AI · 검수 횟수; its 실행 block is testRunSettings'). A change is
       saved at once (no save button, no "saved" line). Explanations are tooltips; a refused change is a red !
       beside its control. A Graph card keeps its settings itself (T#2, useCardSettings); a v0.1 Flow's Node
       keeps the three of its sequence row (0048, useSettings) and the rest is drawn off. A failed read with
       nothing shown is the read failure icon (why: its tooltip) with a ↻ retry, as on every settings form.
       flowgate.v02.0056 F2-1: the progress-after-review row is gone with its setting (O1 0005-TR §1-1, §4-3);
       a RUN card whose instruction is used has the 지시 AI row under it (O2 0007-TR §4-1: none = the run AI,
       then the assigned AI).
       flowgate.v02.0061 T#14: a Graph card is also locked (the same 🔒 실행 중 line, every control off) while its Flow's
       run is running, paused or stopping (`target.editLock`), without waiting for the settings read to say so.
       TR0031 rev1: also while the run's status is not confirmed (`unknown`; the lock line then says so).
       flowgate.v02.0113 T#1 (0002-D 개정 §3.13) replaces the run lock here: only an actually running card is locked
       (the server's `locked` or the node run's `node_running`); a waiting, done or error card takes its settings.
       flowgate.v02.0072 T#3: a Graph work plan (WP) card — the [switch] AI 사전 지시서 작성 line, then the same 실행 설정
       block with its AI named 작업 AI (작업 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ 실행 제한 시간 · 검수 기준). Each change
       is one card setting saved at once, with the same lock, failure icon and read failure as every card. The switch is
       a card value: nothing writes a pre-instruction from it yet (its tooltip says so) and off deletes no Step's
       pre-instruction; the 작업 / 검수 AI are the card's, not the plan's Steps' AI. -->
  <div class="inspector-settings" data-test="inspector-settings" :data-source="source" :data-kind="kind" :data-status="status">
    <InspectorReadFailure v-if="errorKey" :reason="t(k(errorKey))" @retry="reload" />
    <template v-else>
      <InspectorLockNote v-if="locked" :text="lockText" />
      <InspectorBlock v-if="panel === 'settings' && kind === 'plan'" :locked="locked" class="inspector-settings-line" data-test="settings-pre-instruction">
        <div class="inspector-settings-switch">
          <Toggle
            :model-value="preInstruction.on"
            :disabled="preInstruction.disabled"
            :aria-label="t(k('pre_instruction.label'))"
            data-test="settings-pre-instruction-switch"
            @update:model-value="onPreInstruction"
          />
          <span class="inspector-settings-switch-label" :title="preInstruction.hint" data-test="settings-pre-instruction-label">{{ t(k('pre_instruction.label')) }}</span>
          <InspectorFailureIcon
            v-if="failureOf('pre_instruction_auto')"
            :name="t(k('failed'))"
            :reason="failureOf('pre_instruction_auto')!"
            data-field="pre_instruction_auto"
          />
        </div>
      </InspectorBlock>
      <InspectorBlock v-else-if="panel === 'settings'" :locked="locked" class="inspector-settings-line" data-test="settings-instruction">
        <InspectorControlRow :label="t(k('instruction.label'))" :hint="t(k('instruction.hint'))">
          <template v-if="kind === 'test'">
            <Toggle :model-value="segment('instruction').value === 'use'" :disabled="segment('instruction').disabled" :aria-label="t(k('instruction.label'))" data-test="settings-instruction-switch" @update:model-value="(value: boolean) => onChange('instruction', value ? 'use' : 'omit')" />
            <span>{{ t(k(segment('instruction').value === 'use' ? 'instruction.on' : 'instruction.off')) }}</span>
          </template>
          <InspectorSegment v-else
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
      <InspectorBlock
        v-if="panel === 'ai' && !card.node.value?.aiRoles && instructionAiShown"
        :locked="locked"
        class="inspector-settings-line"
        data-test="settings-instruction-ai"
      >
        <InspectorControlRow :label="t(k('instruction_ai.label'))" :hint="views.instruction_ai.reason ? t(k(views.instruction_ai.reason)) : t(k('instruction_ai.hint'))">
          <Select
            class="inspector-settings-select"
            :model-value="views.instruction_ai.value"
            :options="views.instruction_ai.options.map((option) => ({ value: option.value, label: labelOf(option) }))"
            :disabled="locked || views.instruction_ai.disabled"
            :aria-label="t(k('instruction_ai.label'))"
            data-test="settings-instruction-ai-select"
            @update:model-value="(value: string) => onChange('instruction_ai', value)"
          />
          <InspectorFailureIcon v-if="failureOf('instruction_ai')" :name="t(k('failed'))" :reason="failureOf('instruction_ai')!" data-field="instruction_ai" />
        </InspectorControlRow>
      </InspectorBlock>
      <template v-if="panel === 'ai' && card.node.value?.aiRoles">
        <InspectorBlock v-for="item in roleRows" :key="item.role" :locked="locked" :data-role="item.role">
          <InspectorControlRow :label="roleLabel(item.role)" :hint="item.cause || item.outcome">
            <AiRoleSelect :model-value="item.selected" :providers="card.node.value.aiRoles.options" :label="roleLabel(item.role)" :display-name="item.displayName" :state="item.state" :cause="item.cause" :disabled="roleDisabled(item.field)" @update:model-value="value => changeRole(item.field, value)" />
            <small v-if="item.legacyFlag" :title="item.displayName || item.providerId || item.outcome" data-test="ai-legacy">{{ t('gui.aiRoles.legacy') }} · {{ item.displayName || item.providerId || t(`gui.inspector.ai.outcome.${item.outcome}`) }}</small>
            <InspectorFailureIcon v-if="failureOf(item.field)" :name="t(k('failed'))" :reason="failureOf(item.field)!" />
          </InspectorControlRow>
        </InspectorBlock>
      </template>
      <InspectorRunSettings v-if="panel === 'ai' || source === 'sequence'"
        :layout="kind === 'test' ? 'scenario' : 'run'"
        :fields="runFields"
        :hide-missing="!!card.node.value?.aiRoles"
        :labels="runLabels"
        :locked="locked"
        :advanced-open="advancedOpen"
        @update:advanced-open="advancedOpen = $event"
        @update:field="onChange"
      />
      <InspectorBlock v-if="panel === 'ai' && kind === 'run' && card.node.value?.fields.instruction?.value !== 'omit'" data-test="instruction-review">
        <InspectorControlRow v-for="field in instructionReviewFields" :key="field" :label="t(`gui.inspector.ai.${field}`)">
          <Select :model-value="views[field].value" :options="views[field].options.map(option => ({ value: option.value, label: labelOf(option) }))" :disabled="views[field].disabled" :data-field="field" @update:model-value="value => onChange(field, value)" />
          <InspectorFailureIcon v-if="failureOf(field)" :name="t(k('failed'))" :reason="failureOf(field)!" />
        </InspectorControlRow>
      </InspectorBlock>
      <InspectorBlock v-if="panel === 'settings' && kind === 'test'" :title="t(k('result_check'))" data-test="settings-result-check">
        <InspectorControlRow v-for="field in resultFields" :key="field" :label="t(k(`${field}.label`))" :hint="views[field].reason ? t(k(views[field].reason!)) : undefined">
          <Select :model-value="views[field].value" :options="views[field].options.map((option) => ({ value: option.value, label: labelOf(option) }))" :disabled="views[field].disabled" :aria-label="t(k(`${field}.label`))" :data-test="`settings-${field}`" @update:model-value="(value: string) => onChange(field, value)" />
          <InspectorFailureIcon v-if="failureOf(field)" :name="t(k('failed'))" :reason="failureOf(field)!" :data-field="field" />
        </InspectorControlRow>
      </InspectorBlock>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTestCard } from '../testSpec/useTestCard'
import Select from '../../../../../components/ui/Select.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
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
  cardSettingsFailureText,
  cardSettingsInstructionAiShown,
  cardSettingsIsAiField,
  cardSettingsReviewOff,
  cardSettingsSwitchOn,
  cardSettingsText,
  cardSettingsValue,
  cardSettingsValueOf,
  type CardSettingsField,
} from './cardSettingsModel'
import { settingsRowPhase, type SettingsField, type SettingsOption } from './settingsModel'
import { useSharedCardSettings } from './useCardSettings'
import { AiRoleSelect, installAiRoleMessages } from '@/features/ai'
import type { AiRoleValue } from '@/features/document'
import { cardRoleRows } from './cardRolesModel'
import { useSettings } from './useSettings'

const props = withDefaults(defineProps<{ target: InspectorTarget; panel?: 'settings' | 'ai' }>(), { panel: 'settings' })

const composer = useI18n({ useScope: 'global' })
installAiRoleMessages(composer)
const { t } = composer

const k = (key: string) => `gui.inspector.settings.${key}`

/** card = a Graph card (T#2 card settings); sequence = a v0.1 Flow's Node (its sequence row). */
const source = computed<'card' | 'sequence'>(() => (props.target.nodeType !== null ? 'card' : 'sequence'))
const kind = computed<'run' | 'test' | 'plan'>(() => {
  const settingsKind = inspectorSettingsKind(props.target)
  return settingsKind === 'test' || settingsKind === 'plan' ? settingsKind : 'run'
})

const testCard = useTestCard(() => kind.value === 'test' ? props.target : null)
const ownCard = useSharedCardSettings(() => (source.value === 'card' && kind.value !== 'test' ? props.target : null))
const card = {
  node: computed(() => kind.value === 'test' ? testCard.value?.settings.node.value ?? null : ownCard.node.value),
  choices: computed(() => kind.value === 'test' ? testCard.value?.settings.choices.value ?? null : ownCard.choices.value),
  pending: computed(() => kind.value === 'test' ? testCard.value?.settings.pending.value ?? {} : ownCard.pending.value),
  status: computed(() => kind.value === 'test' ? testCard.value?.settings.status.value ?? 'idle' : ownCard.status.value),
  errorKind: computed(() => kind.value === 'test' ? testCard.value?.settings.errorKind.value ?? null : ownCard.errorKind.value),
  failure: computed(() => kind.value === 'test' ? testCard.value?.settings.failure.value ?? null : ownCard.failure.value),
  blocked: computed(() => kind.value === 'test' ? testCard.value?.settings.blocked.value ?? null : ownCard.blocked.value),
  placing: computed(() => kind.value === 'test' ? testCard.value?.settings.placing.value ?? false : ownCard.placing.value),
  change: (field: CardSettingsField, value: unknown) => kind.value === 'test' ? testCard.value?.changeSetting(field, value) : ownCard.change(field, value),
  reload: () => kind.value === 'test' ? testCard.value?.reload() : ownCard.reload(),
}
const resultFields = ['result_cause', 'follow_scope'] as const
const instructionReviewFields = computed(() => (['instruction_review_ai', 'instruction_review_count'] as const).filter(field => card.node.value?.fields[field]))
const roleRows = computed(() => cardRoleRows(card.node.value?.aiRoles, cardSettingsValue(card.node.value, card.pending.value, 'instruction'), card.pending.value))
function roleLabel(role: string): string {
  if (role === 'execution' && kind.value === 'plan') return t(k('plan.run_ai'))
  if (role === 'execution' && kind.value === 'test') return t('gui.designer.inspector.run_settings.writer_ai')
  return t(`gui.aiRoles.${role}`)
}
function roleDisabled(field: string): boolean {
  return cardReason.value !== null || !!card.pending.value[field] || !card.node.value?.aiRoles || card.node.value.aiRoles.optionsState !== 'ok' || card.node.value.settingsVersion === undefined
}
function changeRole(field: 'instruction_role' | 'execution_role' | 'review_role', value: AiRoleValue) {
  if (!roleDisabled(field)) void card.change(field, value)
}
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

/**
 * flowgate.v02.0113 T#1 (0002-D 개정 §3.13): a Graph card is locked only while it actually runs — the server's card lock
 * (`locked`) or its node run's `node_running` (an AI execution of it requested / running, or its AI review), the same
 * judgement — never because its Flow's run is running or the node is merely open (a person's or a setup wait). Until
 * the run status is confirmed (`editLock` `unknown`) it still waits.
 */
function cardLocked(): boolean {
  return card.node.value?.locked === true || props.target.nodeRun?.nodeRunning === true || props.target.editLock === 'unknown'
}

/** The 🔒 실행 중 line: an actually running card (see `cardLocked`), or a v0.1 step started and not done yet. */
const locked = computed(() => {
  if (source.value === 'card') return cardLocked()
  const row = sequence.row.value
  return row !== null && settingsRowPhase(row) === 'in_progress'
})

/** TR0031 rev1: the lock line while only the unconfirmed run status locks the card (else the 🔒 실행 중 line). */
const lockText = computed(() =>
  source.value === 'card' && card.node.value?.locked !== true && props.target.nodeRun?.nodeRunning !== true && props.target.editLock === 'unknown'
    ? t('gui.designer.center.run_lock_pending')
    : null,
)

/** Why nothing on a Graph card can change now (message key), or null. */
const cardReason = computed(() => {
  if (kind.value === 'test' && testCard.value) {
    const reason = testCard.value.can('settingChange').reason
    if (reason === 'guard.writing') return 'reason.writing'
    if (reason === 'reason.forbidden') return 'reason.forbidden'
  }
  return cardSettingsDisabledReason({
    readOnly: props.target.readOnly,
    placing: card.placing.value,
    read: card.node.value !== null,
    locked: cardLocked(),
    blocked: card.blocked.value,
  })
})

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
  if (field === 'instruction_review_ai') {
    options = [{ value: '', label: t('gui.aiRoles.card_review'), key: null, params: {} }, ...cardSettingsAiOptions('review_ai', node?.aiRoles?.options ?? card.choices.value?.providers ?? [], value).slice(1)]
  } else if (cardSettingsIsAiField(field)) options = cardSettingsAiOptions(field, card.choices.value?.providers ?? [], value)
  else options = cardSettingsChoiceOptions(field, node?.fields[field]?.choices, value)
  let reason = cardReason.value
  if (!reason && field in card.pending.value) reason = 'reason.busy'
  if (reason === null && field === 'review_ai' && cardSettingsReviewOff(cardSettingsValue(node, card.pending.value, 'review_count'))) reason = 'reason.review_off'
  const reviewSelected = 'review_role' in card.pending.value ? card.pending.value.review_role as AiRoleValue : node?.aiRoles?.roles.review?.selected
  if (!reason && (field === 'review_count' || field === 'review_basis') && reviewSelected?.kind === 'not_used') reason = 'reason.review_off'
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
  const fields = new Set<CardSettingsField>([...CARD_SETTINGS_FIELDS.run, ...CARD_SETTINGS_FIELDS.test, ...CARD_SETTINGS_FIELDS.plan, 'instruction_review_ai', 'instruction_review_count'])
  for (const field of fields) out[field] = source.value === 'card' ? cardField(field) : sequenceField(field)
  return out
})

/** flowgate.v02.0072 T#3 — the WP card's AI 사전 지시서 작성 switch: its value, whether it may change, its tooltip. */
const preInstruction = computed(() => {
  const view = views.value.pre_instruction_auto
  const on = cardSettingsSwitchOn(cardSettingsValue(card.node.value, card.pending.value, 'pre_instruction_auto'))
  const hint = t(k('pre_instruction.hint'))
  return { on, disabled: !view || view.disabled, hint: view?.reason ? `${t(k(view.reason))} · ${hint}` : hint }
})

/** The switch sends its one value (true / false) at once; nothing else changes with it (no plan document write). */
function onPreInstruction(on: boolean) {
  if (preInstruction.value.disabled) return
  void card.change('pre_instruction_auto', on)
}

function segment(field: 'instruction') {
  const view = views.value[field]
  return { value: view.value === '' ? null : view.value, disabled: view.disabled }
}

function segmentOptions(field: 'instruction', values: readonly string[]): InspectorSegmentOption[] {
  const reason = views.value[field].reason
  return values.map((value) => ({ value, label: t(k(`${field}.${value}`)), title: reason ? t(k(reason)) : null }))
}

/** flowgate.v02.0056 F2-1: the 지시 AI row — a RUN card (Graph) whose instruction is used (the value shown). */
const instructionAiShown = computed(() => {
  const shownKind = kind.value
  return source.value === 'card' && shownKind !== 'plan' && cardSettingsInstructionAiShown(shownKind, cardSettingsValue(card.node.value, card.pending.value, 'instruction'))
})

/**
 * Why the server refused the field's last change (the ! icon's tooltip), or null. flowgate.v02.0056 F2-1: on the
 * 지시 AI the server's own message (x-locale) follows the code's sentence (cardSettingsFailureText).
 */
function failureOf(field: CardSettingsField): string | null {
  if (source.value === 'card') {
    const last = card.failure.value
    return last && last.field === field ? cardSettingsFailureText(field, t(k(cardSettingsFailureKey(last.kind, last.reason))), last.message) : null
  }
  const last = sequence.failure.value
  return last && SEQUENCE_FIELD[field] === last.field ? t(k(last.key)) : null
}

const RUN_BLOCK_FIELDS: readonly InspectorRunSettingsField[] = ['run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'review_basis']

/** flowgate.v02.0072 T#3 — the WP card names its run AI 작업 AI; the other cards keep the common names. */
const runLabels = computed<Partial<Record<InspectorRunSettingsField, string>>>(() => (kind.value === 'plan' ? { run_ai: t(k('plan.run_ai')) } : {}))

const runFields = computed<InspectorRunSettingsFields>(() => {
  const out: Partial<Record<InspectorRunSettingsField, InspectorRunSettingsFields[InspectorRunSettingsField]>> = {}
  const shown = kind.value === 'test' ? CARD_SETTINGS_FIELDS.test : RUN_BLOCK_FIELDS
  for (const name of RUN_BLOCK_FIELDS) {
    if (!shown.includes(name)) continue
    if (card.node.value?.aiRoles && (name === 'run_ai' || name === 'review_ai')) continue
    const view = views.value[name]
    out[name] = {
      value: view.value,
      options: view.options.map((option) => ({ value: option.value, label: labelOf(option) })),
      disabled: view.disabled,
      reason: view.reason ? t(k(view.reason)) : planHint(name),
      failure: failureOf(name),
    }
  }
  return out
})

/** flowgate.v02.0083 WP-4 — a WP card's 검수 횟수 says how a person's WP is reviewed (once per submission). */
function planHint(name: InspectorRunSettingsField): string | null {
  return kind.value === 'plan' && name === 'review_count' ? t(k('plan.review_count_hint')) : null
}

function onChange(field: CardSettingsField, text: string) {
  if (views.value[field].disabled) return
  if (source.value === 'card') {
    const value = field === 'instruction_review_ai' ? text || null : cardSettingsValueOf(field, text)
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

/* flowgate.v02.0072 T#3: the WP card's switch line — the switch first, its name right of it. */
.inspector-settings-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  color: #94a8bd;
}

.inspector-settings-switch-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: help;
}

/* flowgate.v02.0056 F2-1: the 지시 AI select, as tall as the run settings' selects. */
.inspector-settings-select {
  height: 32px;
  font-size: 12px;
}
</style>
