<template>
  <!-- settings tab (row, the earlier implementation): the run settings of the selected
       Node. A thin container: the composables read and save, the models decide the values, the choices and
       availability.
       drawn as v7 draws it (insp.js execSettings / testSettings), on every
       조사 · 작업 · 설계 · Custom · 테스트 card, just placed or done alike: the 🔒 실행 중 line of a running
       card, the 지시서 사용 / 생략 row, then the 실행 설정
       block (실행 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ 실행 제한 시간 · 검수 기준) — or, on a 테스트 card,
       the 시나리오 작성 block (작성 AI | 검수 AI · 검수 횟수; its 실행 block is testRunSettings'). A change is
       saved at once (no save button, no "saved" line). Explanations are tooltips; a refused change is a red !
       beside its control. A Graph card keeps its settings itself (useCardSettings); a v0.1 Flow's Node
       keeps the three of its sequence row (0048, useSettings) and the rest is drawn off. A failed read with
       nothing shown is the read failure icon (why: its tooltip) with a ↻ retry, as on every settings form.
       the progress-after-review row is gone with its setting;
       a RUN card whose instruction is used has the 지시 AI row under it (O2 0007-TR §4-1: none = the run AI,
       then the assigned AI).
       flowgate.v02.0061 T#14: a Graph card is also locked (the same 🔒 실행 중 line, every control off) while its Flow's
       run is running, paused or stopping (`target.editLock`), without waiting for the settings read to say so.
       also while the run's status is not confirmed (`unknown`; the lock line then says so).
       flowgate.v02.0113 T#1 (0002-D 개정 §3.13) replaces the run lock here: only an actually running card is locked
       (the server's `locked` or the node run's `node_running`); a waiting, done or error card takes its settings.
       flowgate.v02.0072 T#3: a Graph work plan (WP) card — the [switch] AI 사전 지시서 작성 line, then the same 실행 설정
       block with its AI named 작업 AI (작업 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ 실행 제한 시간 · 검수 기준). Each change
       is one card setting saved at once, with the same lock, failure icon and read failure as every card. The switch is
       a card value: nothing writes a pre-instruction from it yet (its tooltip says so) and off deletes no Step's
       pre-instruction; the 작업 / 검수 AI are the card's, not the plan's Steps' AI.
       flowgate.v02.0113 T#4 (0030-M §1, 0029-TR 시안, D 개정 §6.3): the AI tab of a card with AI roles shows four rows —
       지시 AI · 실행 AI · 검수 AI (each its own select with 사용하지 않음; none chosen shows 미설정) and 검수 횟수 (안 함 · 1회 ·
       2회 · 3회) — then [고급 ▾], which opens 실행 설정 (재시작 · 실행 제한 시간 · 검수 기준) and 지시서 검수 (지시서 검수 AI ·
       지시서 검수 횟수). flowgate.v02gui.0029 (0003-CH C안): the fold is gone — each AI row has a ⚙ on its right that opens
       that AI's own advanced settings in a small window under the row (지시 AI: 지시서 검수 AI · 횟수, 실행 AI: 재시작 ·
       실행 제한 시간, 검수 AI: 검수 기준 · 검수 횟수 소진 시); a click outside or Esc closes it, one at a time, closed when a
       card opens; a value picked in it keeps it open. It opens above its row when the Inspector has no room under it
       (else the body scrolls to show it, capped to the body). A ⚙ whose settings differ from their defaults carries a yellow dot. A WP card has no 지시 AI; a card that omits its instruction keeps its 지시 AI and 지시서 검수 off with
       their values kept; 검수 횟수 and 검수 기준 are off while the review AI is 사용하지 않음. No notice, warning, status or
       explanation line is drawn, in the ⚙ window either (0003-CH: what 검수 횟수 소진 시 does is its label's tooltip). Values are saved through the same card settings change as before. -->
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
        v-if="panel === 'ai' && legacyAiShown && instructionAiShown"
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
        <InspectorBlock v-for="item in roleRows" :key="item.role" :locked="locked" class="inspector-ai-role" :class="{ 'is-gear-open': gearOpen === item.role }" :data-role="item.role" :data-held="item.held ? 'true' : undefined" :data-written="item.written ? 'true' : undefined">
          <div class="inspector-ai-role-line">
            <InspectorControlRow :label="roleLabel(item.role)">
              <AiRoleSelect plain :model-value="item.selected" :providers="card.node.value.aiRoles.options" :label="roleLabel(item.role)" :display-name="item.displayName" :state="item.state" :cause="item.cause" :disabled="item.held || roleDisabled(item.field)" @update:model-value="value => changeRole(item.field, value)" />
              <InspectorFailureIcon v-if="failureOf(item.field)" :name="t(k('failed'))" :reason="failureOf(item.field)!" />
              <button
                v-if="gearFields(item.role).length"
                type="button"
                class="inspector-ai-gear"
                :class="{ 'is-open': gearOpen === item.role, 'is-modified': gearModified(item.role) }"
                :aria-label="t('gui.inspector.ai.advanced_open', { role: roleLabel(item.role) })"
                :aria-expanded="gearOpen === item.role ? 'true' : 'false'"
                :title="gearTitle(item.role)"
                :data-modified="gearModified(item.role) ? 'true' : undefined"
                :data-gear="item.role"
                data-test="inspector-ai-gear"
                @click="toggleGear(item.role)"
              ><LineIcon name="settings" /></button>
            </InspectorControlRow>
            <div
              v-if="gearOpen === item.role"
              :ref="setGearPop"
              class="inspector-ai-pop"
              :class="{ 'is-above': gearPlace === 'above' }"
              :style="gearMaxHeight !== null ? { maxHeight: `${gearMaxHeight}px` } : undefined"
              :data-place="gearPlace"
              role="dialog"
              :aria-label="t('gui.inspector.ai.advanced_title', { role: roleLabel(item.role) })"
              :data-for="item.role"
              data-test="inspector-ai-advanced"
            >
              <div class="inspector-ai-pop-title">{{ t('gui.inspector.ai.advanced_title', { role: roleLabel(item.role) }) }}</div>
              <InspectorControlRow v-for="field in gearFields(item.role)" :key="field" :label="advancedLabel(field)" :hint="hintOf(field)">
                <Select
                  :class="{ 'is-modified': fieldModified(field) }"
                  :model-value="selectValue(field)"
                  :options="optionsOf(field)"
                  :disabled="locked || (isInstructionReview(field) && instructionHeld) || views[field].disabled"
                  :aria-label="advancedLabel(field)"
                  :data-test="isInstructionReview(field) ? undefined : `inspector-run-settings-${field}`"
                  :data-field="field"
                  :data-modified="fieldModified(field) ? 'true' : undefined"
                  @update:model-value="(value: string) => onAiChange(field, value)"
                />
                <input
                  v-if="verifyCustomShown(field)"
                  class="inspector-ai-custom"
                  type="number"
                  inputmode="numeric"
                  step="1"
                  :min="verifyCustomRange(field)[0]"
                  :max="verifyCustomRange(field)[1] ?? undefined"
                  :value="verifyCustomText(field)"
                  :disabled="locked || views[field].disabled"
                  :aria-label="advancedLabel(field)"
                  :aria-invalid="verifyCustomInvalid(field) ? 'true' : undefined"
                  :data-field="field"
                  :data-test="`inspector-run-settings-${field}-custom`"
                  @input="(event: Event) => onVerifyCustomInput(field, (event.target as HTMLInputElement).value)"
                  @keydown.enter.prevent="commitVerifyCustom(field)"
                  @change="commitVerifyCustom(field)"
                />
                <InspectorFailureIcon v-if="failureOf(field)" :name="t(k('failed'))" :reason="failureOf(field)!" />
              </InspectorControlRow>
            </div>
            <div v-if="gearOpen === item.role && gearRoom !== null" class="inspector-ai-pop-room" aria-hidden="true" :style="{ top: `${gearRoom}px` }" />
          </div>
        </InspectorBlock>
        <InspectorBlock v-if="card.node.value.fields.review_count" :locked="locked" data-test="ai-review-count">
          <InspectorControlRow :label="t(runKey('review_count'))" :hint="hintOf('review_count')">
            <Select
              :model-value="views.review_count.value"
              :options="optionsOf('review_count')"
              :disabled="locked || views.review_count.disabled"
              :aria-label="t(runKey('review_count'))"
              data-test="inspector-run-settings-review_count"
              @update:model-value="(value: string) => onAiChange('review_count', value)"
            />
            <InspectorFailureIcon v-if="failureOf('review_count')" :name="t(k('failed'))" :reason="failureOf('review_count')!" data-field="review_count" />
          </InspectorControlRow>
        </InspectorBlock>
      </template>
      <InspectorRunSettings v-if="(panel === 'ai' && legacyAiShown) || source === 'sequence'"
        :layout="kind === 'test' ? 'scenario' : 'run'"
        :fields="runFields"
        :hide-missing="!!card.node.value?.aiRoles"
        :labels="runLabels"
        :locked="locked"
        :advanced-open="advancedOpen"
        @update:advanced-open="advancedOpen = $event"
        @update:field="onChange"
      />
      <InspectorBlock v-if="panel === 'ai' && legacyAiShown && kind === 'run' && card.node.value?.fields.instruction?.value !== 'omit'" data-test="instruction-review">
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
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTestCard } from '../testSpec/useTestCard'
import Select from '../../../../../components/ui/Select.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import LineIcon from '../../../../../components/ui/LineIcon.vue'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import InspectorRunSettings, { type InspectorRunSettingsField, type InspectorRunSettingsFields } from '../../parts/InspectorRunSettings.vue'
import InspectorSegment, { type InspectorSegmentOption } from '../../parts/InspectorSegment.vue'
import { inspectorSettingsKind, type InspectorTarget } from '../../inspectorTarget'
import { INSPECTOR_BODY } from '../../inspectorHost'
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
  cardSettingsIsVerifyField,
  cardSettingsReviewOff,
  cardSettingsSwitchOn,
  cardSettingsText,
  cardSettingsValue,
  cardSettingsValueOf,
  cardSettingsVerifyCustomValue,
  VERIFY_CUSTOM,
  VERIFY_RANGE,
  type CardSettingsField,
} from './cardSettingsModel'
import { settingsRowPhase, type SettingsField, type SettingsOption } from './settingsModel'
import { useSharedCardSettings } from './useCardSettings'
import { AiRoleSelect, installAiRoleMessages } from '@/features/ai'
import type { AiRoleValue } from '@/features/document'
import { visibleReviewCountOptions } from './reviewCountModel'
import { AI_ADVANCED_FIELDS, AI_FIELDS, AI_GEAR_FIELDS, cardRoleRows, instructionWritten } from './cardRolesModel'
import { useSettings } from './useSettings'

const props = withDefaults(defineProps<{ target: InspectorTarget; panel?: 'settings' | 'ai' }>(), { panel: 'settings' })

const composer = useI18n({ useScope: 'global' })
installAiRoleMessages(composer)
const { t } = composer

const k = (key: string) => `gui.inspector.settings.${key}`

/** card = a Graph card (card settings); sequence = a v0.1 Flow's Node (its sequence row). */
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
const instructionReviewFields = computed(() => AI_ADVANCED_FIELDS.instructionReview.filter(field => aiFieldOf(field)))
/** flowgate.v02gui.0032: a written instruction shows its 지시 AI as 사용하지 않음 (held). */
const instructionHasBody = computed(() => instructionWritten(props.target.cardDocuments))
const roleRows = computed(() => cardRoleRows(card.node.value?.aiRoles, cardSettingsValue(card.node.value, card.pending.value, 'instruction'), card.pending.value, instructionHasBody.value))
function roleLabel(role: string): string {
  if (role === 'execution' && kind.value === 'plan') return t(k('plan.run_ai'))
  if (role === 'execution' && kind.value === 'test') return t('gui.designer.inspector.run_settings.writer_ai')
  return t(`gui.aiRoles.${role}`)
}
function roleDisabled(field: string): boolean {
  return cardReason.value !== null || !!card.pending.value[field] || !card.node.value?.aiRoles || card.node.value.aiRoles.optionsState !== 'ok' || card.node.value.settingsVersion === undefined
}
function changeRole(field: 'instruction_role' | 'execution_role' | 'review_role', value: AiRoleValue) {
  if (field === 'instruction_role' && (instructionHeld.value || instructionHasBody.value)) return
  if (!roleDisabled(field)) void card.change(field, value)
}
/** flowgate.v02.0113 T#4: an omitted instruction keeps its 지시 AI and instruction review values, off. */
const instructionHeld = computed(() => cardSettingsValue(card.node.value, card.pending.value, 'instruction') === 'omit')
/** a field the card's kind puts on the AI tab and the card has. */
function aiFieldOf(field: CardSettingsField): boolean {
  return (AI_FIELDS[kind.value] as readonly string[]).includes(field) && !!card.node.value?.fields[field]
}
type GearRole = keyof typeof AI_GEAR_FIELDS
/**
 * flowgate.v02gui.0029 (0003-CH Coff): the advanced items each AI row's ⚙ opens, for the fields the card has (a 테스트
 * card has none, so no ⚙). An item whose AI row the server does not give stays reachable under the 실행 AI's ⚙ (else
 * the last row's), so no setting drops off the tab.
 */
const gearGroups = computed(() => {
  const rows = roleRows.value.map(row => row.role)
  const own = (role: GearRole) => (AI_GEAR_FIELDS[role] as readonly CardSettingsField[]).filter(field => aiFieldOf(field))
  const out: Partial<Record<GearRole, CardSettingsField[]>> = {}
  for (const role of rows) out[role] = own(role)
  const orphans = (Object.keys(AI_GEAR_FIELDS) as GearRole[]).filter(role => !rows.includes(role)).flatMap(own)
  const host = rows.includes('execution') ? 'execution' : rows[rows.length - 1]
  if (orphans.length && host) out[host] = [...(out[host] ?? []), ...orphans]
  return out
})
function gearFields(role: string): readonly CardSettingsField[] {
  return gearGroups.value[role as GearRole] ?? []
}
const isInstructionReview = (field: CardSettingsField) => (AI_ADVANCED_FIELDS.instructionReview as readonly string[]).includes(field)
function advancedLabel(field: CardSettingsField): string {
  return isInstructionReview(field) ? t(`gui.inspector.ai.${field}`) : fieldLabel(field)
}
/** A field whose shown value (a sent one included) is not the server's default for it. */
function fieldModified(field: CardSettingsField): boolean {
  const item = card.node.value?.fields[field]
  return !!item && cardSettingsText(cardSettingsValue(card.node.value, card.pending.value, field)) !== cardSettingsText(item.default)
}
const gearModified = (role: string) => gearFields(role).some(fieldModified)
function gearTitle(role: string): string {
  const name = t('gui.inspector.ai.advanced_open', { role: roleLabel(role) })
  return gearModified(role) ? `${name} · ${t('gui.inspector.ai.advanced_changed')}` : name
}
/** The one ⚙ window open now (its AI role), or null; a card opens with none. */
const gearOpen = ref<string | null>(null)
function toggleGear(role: string) {
  gearOpen.value = gearOpen.value === role ? null : role
}
// (CH QA): only another card or tab closes the window. Each source is compared on its own:
// a value saved from the window re-reads the Flow and hands down a new target object for the same card, which the
// former single getter (a new array every time) took for a card change and so closed the window on every pick.
watch([() => props.target.nodeId, () => props.panel], () => { gearOpen.value = null; verifyCustom.value = {} })
function onOutside(event: Event) {
  if (gearOpen.value === null) return
  const at = event.target as Element | null
  if (at?.closest?.('.inspector-ai-pop, .inspector-ai-gear')) return
  gearOpen.value = null
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') gearOpen.value = null
}

/**
 * (CH QA): where the open window goes inside the Inspector's scrolling body. Under its row
 * when it fits there; else above the row when it fits there; else under the row with the body scrolled just enough to
 * show it whole — its height capped (scrolling inside) only when even the whole body is too low for it. Under the row
 * an invisible 1px mark is kept GEAR_EDGE past the window, so the body can scroll the window clear of its bottom edge
 * (a margin does not count to a scroll area). Placed when it opens and when the window is resized; with no body
 * measured (outside the Frame, no layout) it stays under the row.
 */
const inspectorBody = inject(INSPECTOR_BODY, null)
const GEAR_GAP = 6
const GEAR_EDGE = 8
const GEAR_MIN_HEIGHT = 96
const gearPlace = ref<'below' | 'above'>('below')
const gearMaxHeight = ref<number | null>(null)
/** Top of the 1px scroll mark under the open window, from its row's top (px), or null for none. */
const gearRoom = ref<number | null>(null)
let gearPop: HTMLElement | null = null
let gearPlacing = 0
function setGearPop(element: Element | ComponentPublicInstance | null) {
  gearPop = element instanceof HTMLElement ? element : null
}
function scrollBoxOf(element: HTMLElement): HTMLElement | null {
  const body = inspectorBody?.element.value
  if (body?.contains(element)) return body
  for (let at = element.parentElement; at; at = at.parentElement) {
    const overflow = getComputedStyle(at).overflowY
    if (overflow === 'auto' || overflow === 'scroll') return at
  }
  return null
}
async function placeGear() {
  const turn = ++gearPlacing
  gearPlace.value = 'below'
  gearMaxHeight.value = null
  gearRoom.value = null
  await nextTick()
  const pop = gearPop
  const line = pop?.parentElement
  const box = pop ? scrollBoxOf(pop) : null
  if (turn !== gearPlacing || !pop || !line || !box) return
  const view = box.getBoundingClientRect()
  if (view.height <= 0) return
  const row = line.getBoundingClientRect()
  const full = pop.offsetHeight
  if (full + GEAR_GAP + GEAR_EDGE > view.bottom - row.bottom && full + GEAR_GAP + GEAR_EDGE <= row.top - view.top) {
    gearPlace.value = 'above'
    return
  }
  const fit = Math.floor(view.height - row.height - GEAR_GAP - 2 * GEAR_EDGE)
  const height = full > fit ? Math.max(GEAR_MIN_HEIGHT, fit) : full
  if (height < full) gearMaxHeight.value = height
  gearRoom.value = Math.ceil(row.height + GEAR_GAP + height + GEAR_EDGE - 1)
  const shift = Math.min(row.bottom + GEAR_GAP + height + GEAR_EDGE - view.bottom, row.top - view.top - GEAR_EDGE)
  if (shift <= 0) return
  await nextTick()
  if (turn === gearPlacing) box.scrollTop += shift
}
watch(gearOpen, (role) => { if (role !== null) void placeGear() }, { flush: 'post' })
function onResize() {
  if (gearOpen.value !== null) void placeGear()
}
onMounted(() => { document.addEventListener('pointerdown', onOutside, true); document.addEventListener('keydown', onKey); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { document.removeEventListener('pointerdown', onOutside, true); document.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize) })
const runKey = (key: string) => `gui.designer.inspector.run_settings.${key}`
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
 * a Graph card is locked only while it actually runs — the server's card lock
 * (`locked`) or its node run's `node_running` (an AI execution of it requested / running, or its AI review), the same
 * judgement — never because its Flow's run is running or the node is merely open (a person's or a setup wait). Until
 * the run status is confirmed (`editLock` `unknown`) it still waits.
 */
function cardLocked(): boolean {
  // the unconfirmed run status only waits until the card's own settings are read — after that the
  // server's answer (`locked`, which already holds `node_running`) decides.
  return card.node.value?.locked === true || props.target.nodeRun?.nodeRunning === true || (props.target.editLock === 'unknown' && card.node.value === null)
}

/** The 🔒 실행 중 line: an actually running card (see `cardLocked`), or a v0.1 step started and not done yet. */
const locked = computed(() => {
  if (source.value === 'card') return cardLocked()
  const row = sequence.row.value
  return row !== null && settingsRowPhase(row) === 'in_progress'
})

/** TR0031 rev1: the lock line while only the unconfirmed run status locks the card (else the 🔒 실행 중 line). */
const lockText = computed(() =>
  source.value === 'card' && card.node.value === null && props.target.nodeRun?.nodeRunning !== true && props.target.editLock === 'unknown'
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
    settingsReadOnly: props.target.settingsReadOnly,
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
  if (!reason && field === 'after_review_exhausted') {
    const resultOff = reviewSelected?.kind === 'not_used' || cardSettingsReviewOff(cardSettingsValue(node, card.pending.value, 'review_count'))
    const instructionOff = kind.value !== 'run' || instructionHeld.value || cardSettingsReviewOff(cardSettingsValue(node, card.pending.value, 'instruction_review_count'))
    if (resultOff && instructionOff) reason = 'reason.review_off'
  }
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

/** the WP card's AI pre-instruction writing switch: its value, whether it may change, its tooltip. */
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
 * flowgate.v02.0144 T-B (0003-NR §4.2a(6)(7)): the older AI form (지시 AI row, 실행 설정 block, instruction review block) is
 * drawn only for a Graph card whose settings answer has come without AI roles (`ai_roles` null or missing). Before
 * the answer (loading, a card just placed) none of it is drawn. the earlier change: a re-read after the settings
 * context moved keeps the last answer drawn until the new one comes (useCardSettings `identity`).
 */
const legacyAiShown = computed(() => source.value === 'card' && card.node.value !== null && !card.node.value.aiRoles)

/**
 * Why the server refused the field's last change (the ! icon's tooltip), or null. the earlier change F2-1: on the
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

const RUN_BLOCK_FIELDS: readonly InspectorRunSettingsField[] = ['run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'verify_max_runs', 'verify_run_minutes', 'review_basis', 'after_review_exhausted']

function fieldLabel(field: CardSettingsField): string {
  return field === 'after_review_exhausted' ? t(k('after_review_exhausted.label')) : t(runKey(field))
}

/** the WP card names its run AI task AI; the other cards keep the common names. */
const runLabels = computed<Partial<Record<InspectorRunSettingsField, string>>>(() => ({ after_review_exhausted: t(k('after_review_exhausted.label')), ...(kind.value === 'plan' ? { run_ai: t(k('plan.run_ai')) } : {}) }))

const runFields = computed<InspectorRunSettingsFields>(() => {
  const out: Partial<Record<InspectorRunSettingsField, InspectorRunSettingsFields[InspectorRunSettingsField]>> = {}
  const shown = kind.value === 'test' ? CARD_SETTINGS_FIELDS.test : RUN_BLOCK_FIELDS
  for (const name of RUN_BLOCK_FIELDS) {
    if (!shown.includes(name)) continue
    if (name === 'after_review_exhausted' && !card.node.value?.fields.after_review_exhausted) continue
    if (card.node.value?.aiRoles && (name === 'run_ai' || name === 'review_ai')) continue
    // flowgate.v02test.0053 0003-T: a card the server gives no 검증 option (a plan card, a v0.1 sequence row) draws none.
    if (cardSettingsIsVerifyField(name) && (kind.value !== 'run' || source.value !== 'card' || !card.node.value?.fields[name])) continue
    const view = views.value[name]
    out[name] = {
      value: view.value,
      // the legacy block offers the presets only: the number box of 사용자 정의 lives in the AI tab's ⚙ window
      options: view.options.filter((option) => option.value !== VERIFY_CUSTOM).map((option) => ({ value: option.value, label: labelOf(option) })),
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

/**
 * a select's options on the AI tab. the earlier change B: the two review counts offer the
 * server's choices up to its effective maximum (안 함 · 1 time … n times), without "until it passes" (-1); a stored value
 * outside them stays shown so nothing is changed by opening the card.
 */
function optionsOf(field: CardSettingsField) {
  const view = views.value[field]
  const counted = field === 'review_count' || field === 'instruction_review_count'
  return (counted ? visibleReviewCountOptions(view.options, view.value) : view.options)
    .map(option => ({ value: option.value, label: labelOf(option) }))
}

/**
 * Why the field is off (its label's tooltip), or the WP card's 검수 횟수 tooltip. flowgate.v02gui.0029 (0003-CH): what
 * when the review limit is exhausted does (for the chosen value) and that instruction review follows it are their labels' tooltips — never a line
 * drawn in the ⚙ window.
 */
function hintOf(field: CardSettingsField): string | null {
  const reason = views.value[field].reason
  if (reason) return t(k(reason))
  if (field === 'after_review_exhausted') return t(k(`after_review_exhausted.${views.value.after_review_exhausted.value === 'stop' ? 'stop' : 'continue'}_hint`))
  if (field === 'instruction_review_count') return t(k('after_review_exhausted.instruction_hint'))
  if (field === 'verify_max_runs') return t(k('verify.max_runs_hint'))
  if (field === 'verify_run_minutes') return t(k('verify.run_minutes_hint'))
  return field === 'review_count' ? planHint('review_count') : null
}

/**
 * flowgate.v02test.0053 0003-T (1-A): a 검증 option's 사용자 정의 — picking that entry of the combo opens a number box beside it
 * (the typed text per field, closed once a value is saved or another entry is picked). Only a whole number inside the
 * option's range is sent (count 1 or more, minutes 1..30); anything else keeps the box open, marked, and sends nothing.
 */
const verifyCustom = ref<Partial<Record<CardSettingsField, string>>>({})
const verifyCustomShown = (field: CardSettingsField) => cardSettingsIsVerifyField(field) && field in verifyCustom.value
const verifyCustomText = (field: CardSettingsField) => verifyCustom.value[field] ?? ''
const verifyCustomRange = (field: CardSettingsField): readonly [number, number | null] => (cardSettingsIsVerifyField(field) ? VERIFY_RANGE[field] : [0, 0])
const verifyCustomInvalid = (field: CardSettingsField) => cardSettingsIsVerifyField(field) && verifyCustomText(field) !== '' && cardSettingsVerifyCustomValue(field, verifyCustomText(field)) === undefined
/** The combo shows 사용자 정의 while its box is open, else the field's value. */
const selectValue = (field: CardSettingsField) => (verifyCustomShown(field) ? VERIFY_CUSTOM : views.value[field].value)
function closeVerifyCustom(field: CardSettingsField) {
  const next = { ...verifyCustom.value }
  delete next[field]
  verifyCustom.value = next
}
function onVerifyPick(field: CardSettingsField, text: string) {
  if (text === VERIFY_CUSTOM) {
    verifyCustom.value = { ...verifyCustom.value, [field]: views.value[field].value }
    return
  }
  closeVerifyCustom(field)
  onChange(field, text)
}
function onVerifyCustomInput(field: CardSettingsField, text: string) {
  verifyCustom.value = { ...verifyCustom.value, [field]: text }
}
function commitVerifyCustom(field: CardSettingsField) {
  if (!cardSettingsIsVerifyField(field) || !verifyCustomShown(field)) return
  const value = cardSettingsVerifyCustomValue(field, verifyCustomText(field))
  if (value === undefined) return
  closeVerifyCustom(field)
  onChange(field, String(value))
}

function onAiChange(field: CardSettingsField, text: string) {
  if (cardSettingsIsVerifyField(field)) return onVerifyPick(field, text)
  if (instructionHeld.value && (field === 'instruction_review_ai' || field === 'instruction_review_count')) return
  onChange(field, text)
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

/* the WP card's switch line — the switch first, its name right of it. */
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

/* flowgate.v02test.0053 0003-T: the 사용자 정의 number box of a 검증 option, as tall as the selects. */
.inspector-ai-custom {
  flex: none;
  width: 72px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: #0c1c2e;
  color: inherit;
  font-size: 12px;
}

.inspector-ai-custom[aria-invalid='true'] {
  border-color: #e5484d;
}

/* flowgate.v02.0056 F2-1: the 지시 AI select, as tall as the run settings' selects. */
.inspector-settings-select {
  height: 32px;
  font-size: 12px;
}

/* (TR mockup): the AI tab — selects as tall as the run settings'. */
.inspector-settings :deep(.ui-select) {
  height: 32px;
  font-size: 12px;
}

/* (CH Coff): each AI row's ⚙ on its right, and that AI's advanced settings in a small window
   under the row. The block of an open ⚙ is lifted over the blocks below it (each block is its own stacking context);
   the ⚙ and its window still take a click on a locked card (its values are seen, its selects stay off). */
.inspector-ai-role.is-gear-open {
  position: relative;
  z-index: 5;
}

.inspector-ai-role-line {
  position: relative;
}

.inspector-ai-role-line > :deep(.inspector-control-row) {
  margin: 0;
}

.inspector-ai-gear {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 32px;
  padding: 0;
  border: 1px solid #2c4b64;
  border-radius: 6px;
  background: #0d1924;
  color: #94a8bd;
  cursor: pointer;
  pointer-events: auto;
}

.inspector-ai-gear :deep(svg) {
  width: 15px;
  height: 15px;
}

.inspector-ai-gear:hover,
.inspector-ai-gear:focus-visible {
  border-color: #4aa3ff;
  color: #dce7f2;
  outline: none;
}

.inspector-ai-gear.is-open {
  border-color: #4aa3ff;
  background: #1c3247;
  color: #fff;
}

.inspector-ai-gear.is-modified::after {
  content: "";
  position: absolute;
  top: 3px;
  right: 3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c9a14a;
}

.inspector-ai-pop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  box-sizing: border-box;
  width: min(360px, 100%);
  padding: 10px 12px;
  border: 1px solid #4aa3ff;
  border-radius: 8px;
  background: #16293c;
  box-shadow: 0 8px 24px rgb(0 0 0 / 56%);
  pointer-events: auto;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* The scroll mark kept just past an open window under its row (see placeGear): seen by the body's scroll area only. */
.inspector-ai-pop-room {
  position: absolute;
  left: 0;
  width: 1px;
  height: 1px;
  pointer-events: none;
  visibility: hidden;
}

/* Not enough room under the row in the Inspector body: the window opens above it (see placeGear). */
.inspector-ai-pop.is-above {
  top: auto;
  bottom: calc(100% + 6px);
}

/* A long label (English 'Instruction review AI') goes on to a second line instead of being cut; the window is 360px
   wide so a long English value ('Card review role value') fits its select. */
.inspector-ai-pop :deep(.inspector-control-label) {
  overflow: visible;
  line-height: 1.3;
  white-space: normal;
  overflow-wrap: anywhere;
}

.inspector-ai-pop-title {
  margin: 0 0 8px;
  color: #dce7f2;
  font-size: 12px;
  font-weight: 750;
}

.inspector-ai-pop :deep(.ui-select.is-modified) {
  border-color: #c9a14a;
}
</style>
