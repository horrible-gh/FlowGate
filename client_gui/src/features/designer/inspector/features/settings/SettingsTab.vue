<template>
  <!-- flowgate.v02.0048 T#9 — settings tab (D#1 §10.2 T#9 row, D#2 §2.2): the run AI, the review count and
       the reviewer of the selected Node's v0.1 sequence step, saved on the step row (no server change). A
       thin container: useSettings reads and saves, settingsModel decides the row, the choices and
       availability.
       flowgate.v02.0051 stage 2 — drawn in the v7 settings shape (0035 execSettings): the 🔒 line of a
       started step, the 지시서 사용 / 생략 and 검수 뒤 진행 rows (not stored on a Node yet: disabled with the
       reason — the test card has its own in testRunSettings), and the common 실행 설정 block
       (InspectorRunSettings) whose 실행 AI · 검수 AI · 검수 횟수 are the three 0048 fields and whose 재시작 ·
       실행 제한 시간 · 검수 기준 are not connected yet. Save, reasons and the running hop stay as in 0048.
       flowgate.v02.0052 묶음 A — no prose in the body (v7 execSettings keeps explanations in tooltips):
       the save hint is the save button's hover text, the row state line yields to the 🔒 line once the
       step started, and the running hop folds into one wrapped line.
       flowgate.v02.0052 T#2 — the step's phase is the server's (sequence row `status`): 시작 전 keeps the
       form and the save; 진행·검수 중 keeps the values drawn locked under one 🔒 line (no save — the server
       ignores a started row); 완료 shows one done line with the stored values instead of the controls. -->
  <div class="inspector-settings" data-test="inspector-settings" :data-status="status">
    <InspectorPaneState v-if="noSlot" state="empty" :message="t(k('no_slot'))" />
    <template v-else>
      <!-- The answer to the last save shows while its row is found again (a saved row may take a new id). -->
      <div v-if="failure" data-test="settings-failure">
        <InspectorReason tone="danger" :text="t(k(failure.key))" />
      </div>
      <div v-else-if="saved" data-test="settings-saved">
        <InspectorReason :text="t(k('saved'))" />
      </div>
    </template>
    <InspectorPaneState v-if="!noSlot && status === 'loading' && !row" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !row" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <InspectorPaneState v-else-if="!row && status === 'ready'" state="loading" :message="t(k('no_row'))" />
    <template v-else-if="row && fields && phase === 'done'">
      <div class="inspector-settings-head" data-test="settings-row" :title="t(k('reason.done'))">
        <span class="inspector-settings-title">{{ title }}</span>
        <span data-test="settings-row-state"><InspectorStatusChip :label="t(k('state.done'))" tone="success" /></span>
      </div>
      <p class="inspector-settings-values" data-test="settings-done-values">
        <span v-for="item in doneValues" :key="item.field" :data-test="`settings-done-${item.field}`">
          <span class="inspector-settings-label">{{ t(k(`field.${item.label}`)) }}</span> {{ item.value }}
        </span>
      </p>
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    </template>
    <template v-else-if="row && fields">
      <InspectorLockNote v-if="started" :text="t(k('state.in_progress'))" />
      <div class="inspector-settings-head" data-test="settings-row">
        <span class="inspector-settings-title">{{ title }}</span>
        <!-- Once the step started the 🔒 line above already says so. -->
        <span v-if="!started" class="inspector-settings-sub" data-test="settings-row-state">{{ t(k(stateKey)) }}</span>
      </div>

      <template v-if="policyRows">
        <InspectorBlock :locked="started" data-test="settings-instruction">
          <InspectorControlRow :label="t(k('instruction.label'))" :hint="t(k('instruction.hint'))">
            <InspectorSegment
              :model-value="null"
              :options="[
                { value: 'use', label: t(k('instruction.use')), title: t(k('unconnected')) },
                { value: 'omit', label: t(k('instruction.omit')), title: t(k('unconnected')) },
              ]"
              disabled
              :aria-label="t(k('instruction.label'))"
            />
          </InspectorControlRow>
        </InspectorBlock>
        <InspectorBlock :locked="started" data-test="settings-policy">
          <InspectorControlRow :label="t(k('policy.label'))" :hint="t(k('policy.hint'))">
            <InspectorSegment
              :model-value="null"
              :options="[
                { value: 'follow', label: t(k('policy.follow')), title: t(k('unconnected')) },
                { value: 'human', label: t(k('policy.human')), title: t(k('unconnected')) },
              ]"
              disabled
              :aria-label="t(k('policy.label'))"
            />
          </InspectorControlRow>
        </InspectorBlock>
      </template>

      <InspectorRunSettings :fields="runFields" :locked="started" :advanced-open="advancedOpen" @update:advanced-open="advancedOpen = $event" @update:field="onField" />
      <div v-if="reviewerReason" data-test="settings-reviewer-reason">
        <InspectorReason :text="t(k(reviewerReason))" />
      </div>

      <!-- A started row is never saved (the server ignores its echo): the 🔒 line is its reason. -->
      <template v-if="!started">
        <div class="inspector-settings-bar">
          <Button
            size="sm"
            variant="primary"
            :disabled="!availability.save.enabled"
            :loading="busy"
            :title="t(k('hint'))"
            data-test="settings-save"
            @click="save"
          >{{ t(k('save')) }}</Button>
        </div>
        <div v-if="availability.save.reason" data-test="settings-reason">
          <InspectorReason :text="t(k(availability.save.reason))" />
        </div>
      </template>
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />

      <div v-if="hop" class="inspector-settings-hop" data-test="settings-hop">
        <span class="inspector-settings-label">{{ t(k('hop.title')) }}</span>
        <span class="inspector-settings-hop-values">
          <span data-test="settings-hop-provider">{{ t(k('hop.provider'), { name: hop.providerName ?? '—' }) }} · {{ t(k(`hop.match.${hop.provider}`)) }}</span>
          <span data-test="settings-hop-review-count">{{ t(k('hop.review_count'), { count: hopCount }) }} · {{ t(k(`hop.match.${hop.reviewCount}`)) }}</span>
          <span data-test="settings-hop-reviewer">{{ t(k('hop.reviewer'), { name: hop.reviewerName ?? '—' }) }} · {{ t(k(`hop.match.${hop.reviewer}`)) }}</span>
        </span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorRunSettings, { type InspectorRunSettingsField, type InspectorRunSettingsFields } from '../../parts/InspectorRunSettings.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { settingsReviewCountKey, settingsRowPhase, settingsRowState, type SettingsField } from './settingsModel'
import { useSettings } from './useSettings'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.settings.${key}`

/** The 0048 fields behind the v7 run settings block. */
const FIELD_OF: Readonly<Partial<Record<InspectorRunSettingsField, SettingsField>>> = {
  run_ai: 'provider',
  review_count: 'reviewCount',
  review_ai: 'reviewer',
}

/** v7 items with no place on a v0.1 sequence row yet. */
const UNCONNECTED: readonly InspectorRunSettingsField[] = ['restart', 'time_limit', 'review_basis']

const { row, noSlot, fields, availability, hop, status, errorKey, busy, failure, saved, set, save, reload } = useSettings(() => props.target)

const title = computed(() => {
  const current = row.value
  if (!current) return ''
  const params = { seq: current.itemSeq ?? '', type: current.type, label: current.label }
  return current.itemSeq === null ? t(k('title_no_seq'), params) : t(k('title'), params)
})

const started = computed(() => {
  const current = row.value
  return !current || settingsRowState(current) === 'started'
})

const phase = computed(() => (row.value ? settingsRowPhase(row.value) : null))

const stateKey = computed(() => {
  const current = row.value
  if (!current || started.value) return 'state.in_progress'
  return current.protected ? 'state.protected' : 'state.editable'
})

/** The 지시서 / 검수 뒤 진행 rows: on the cards v7 gives them to; the test card draws its own (testRunSettings). */
const policyRows = computed(() => props.target.cardKind !== 'test')

const advancedOpen = ref(false)

function optionsOf(field: SettingsField) {
  return (fields.value?.[field].options ?? []).map((option) => ({
    value: option.value,
    label: option.label ?? t(k(option.key ?? ''), option.params),
  }))
}

const runFields = computed<InspectorRunSettingsFields>(() => {
  const out: Partial<Record<InspectorRunSettingsField, InspectorRunSettingsFields[InspectorRunSettingsField]>> = {}
  for (const [name, field] of Object.entries(FIELD_OF) as [InspectorRunSettingsField, SettingsField][]) {
    const item = availability.value[field]
    out[name] = {
      value: fields.value?.[field].value ?? '',
      options: optionsOf(field),
      disabled: !item.enabled,
      reason: item.reason && item.reason !== 'reason.busy' ? t(k(item.reason)) : null,
    }
  }
  for (const name of UNCONNECTED) out[name] = { value: '', options: [], disabled: true, reason: t(k('unconnected')) }
  return out
})

/** A done step's stored values (the reviewer only while a review runs), as the fields' choices name them. */
const doneValues = computed(() => {
  const label = (field: SettingsField) => {
    const value = fields.value?.[field].value ?? ''
    return optionsOf(field).find((option) => option.value === value)?.label ?? '—'
  }
  const items = [
    { field: 'provider', label: 'provider', value: label('provider') },
    { field: 'reviewCount', label: 'review_count', value: label('reviewCount') },
  ]
  if (row.value?.reviewCount !== 0) items.push({ field: 'reviewer', label: 'reviewer', value: label('reviewer') })
  return items
})

function onField(name: InspectorRunSettingsField, value: string) {
  const field = FIELD_OF[name]
  if (field) set(field, value)
}

// The reviewer's own reason (review count "none") shows under the fields; a common reason shows once, by save.
const reviewerReason = computed(() => {
  const reason = availability.value.reviewer.reason
  return reason && reason !== availability.value.save.reason ? reason : null
})

const hopCount = computed(() => {
  const count = hop.value?.count
  return count === null || count === undefined ? '—' : t(k(settingsReviewCountKey(count)), { n: count })
})
</script>

<style scoped>
.inspector-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-settings-head,
.inspector-settings-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-settings-title {
  min-width: 0;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-settings-label {
  color: var(--fg2-muted);
}

.inspector-settings-values {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  margin: 0;
  font-size: 11.5px;
}

.inspector-settings-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

/* One wrapped line (v7 keeps no prose block): title, then the three values separated by ·. */
.inspector-settings-hop {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  padding-top: 6px;
  border-top: 1px solid var(--fg2-line);
  font-size: 11.5px;
}

.inspector-settings-hop-values {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 6px;
  min-width: 0;
  color: var(--fg2-muted);
}

.inspector-settings-hop-values > span + span::before {
  content: '·';
  margin-right: 6px;
  opacity: 0.6;
}
</style>
