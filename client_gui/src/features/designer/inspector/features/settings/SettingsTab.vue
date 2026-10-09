<template>
  <!-- flowgate.v02.0048 T#9 — settings tab (D#1 §10.2 T#9 row, D#2 §2.2): the run AI, the review count and
       the reviewer of the selected Node's v0.1 sequence step, saved on the step row (no server change). A
       thin container: useSettings reads and saves, settingsModel decides the row, the choices and
       availability. -->
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
    <template v-else-if="row && fields">
      <div class="inspector-settings-head" data-test="settings-row">
        <span class="inspector-settings-title">{{ title }}</span>
        <span class="inspector-settings-sub" data-test="settings-row-state">{{ t(k(stateKey)) }}</span>
      </div>

      <label v-for="field in FIELDS" :key="field.name" class="inspector-settings-field" :data-test="`settings-field-${field.name}`">
        <span class="inspector-settings-label">{{ t(k(field.label)) }}</span>
        <Select
          :model-value="fields[field.name].value"
          :options="optionsOf(field.name)"
          :disabled="!availability[field.name].enabled"
          :data-test="`settings-${field.name}`"
          @update:model-value="set(field.name, $event)"
        />
      </label>
      <div v-if="reviewerReason" data-test="settings-reviewer-reason">
        <InspectorReason :text="t(k(reviewerReason))" />
      </div>

      <div class="inspector-settings-bar">
        <Button
          size="sm"
          variant="primary"
          :disabled="!availability.save.enabled"
          :loading="busy"
          data-test="settings-save"
          @click="save"
        >{{ t(k('save')) }}</Button>
        <span class="inspector-settings-sub">{{ t(k('hint')) }}</span>
      </div>
      <div v-if="availability.save.reason" data-test="settings-reason">
        <InspectorReason :text="t(k(availability.save.reason))" />
      </div>
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />

      <div v-if="hop" class="inspector-settings-hop" data-test="settings-hop">
        <span class="inspector-settings-label">{{ t(k('hop.title')) }}</span>
        <span data-test="settings-hop-provider">{{ t(k('hop.provider'), { name: hop.providerName ?? '—' }) }} · {{ t(k(`hop.match.${hop.provider}`)) }}</span>
        <span data-test="settings-hop-review-count">{{ t(k('hop.review_count'), { count: hopCount }) }} · {{ t(k(`hop.match.${hop.reviewCount}`)) }}</span>
        <span data-test="settings-hop-reviewer">{{ t(k('hop.reviewer'), { name: hop.reviewerName ?? '—' }) }} · {{ t(k(`hop.match.${hop.reviewer}`)) }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Select from '../../../../../components/ui/Select.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { settingsReviewCountKey, settingsRowState, type SettingsField } from './settingsModel'
import { useSettings } from './useSettings'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.settings.${key}`

const FIELDS: readonly { readonly name: SettingsField; readonly label: string }[] = [
  { name: 'provider', label: 'field.provider' },
  { name: 'reviewCount', label: 'field.review_count' },
  { name: 'reviewer', label: 'field.reviewer' },
]

const { row, noSlot, fields, availability, hop, status, errorKey, busy, failure, saved, set, save, reload } = useSettings(() => props.target)

const title = computed(() => {
  const current = row.value
  if (!current) return ''
  const params = { seq: current.itemSeq ?? '', type: current.type, label: current.label }
  return current.itemSeq === null ? t(k('title_no_seq'), params) : t(k('title'), params)
})

const stateKey = computed(() => {
  const current = row.value
  if (!current || settingsRowState(current) === 'started') return 'state.started'
  return current.protected ? 'state.protected' : 'state.editable'
})

function optionsOf(field: SettingsField) {
  return (fields.value?.[field].options ?? []).map((option) => ({
    value: option.value,
    label: option.label ?? t(k(option.key ?? ''), option.params),
  }))
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
  max-width: 420px;
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

.inspector-settings-field {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-settings-label {
  color: var(--fg2-muted);
}

.inspector-settings-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-settings-hop {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 6px;
  border-top: 1px solid var(--fg2-line);
}
</style>
