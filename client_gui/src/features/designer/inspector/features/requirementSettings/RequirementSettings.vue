<template>
  <!-- flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's settings tab (0035 v7 reqSettings): the AI 위임
       block with its 끄기 / 켜기 switch; while ON, 범위 · AI Team and the 안전 한도 block. Built from the common
       parts. A thin container: requirementSettingsModel gives the view. `view` may be handed in for a fixture
       preview of the ON shape.
       flowgate.v02.0052 T#2 — no prose block: OFF is one short line (the v7 explanation is its tooltip).
       flowgate.v02.0053 T#7 — a Graph requirement card keeps these values (T#2): every change is saved at once
       (values only — no delegation engine). The Supervisor note is the AI Team label's tooltip; a refused
       change is a red ! beside its control. A v0.1 Flow's requirement Node keeps none: drawn OFF, off, with
       the "not connected" tag. A failed first read is the read failure icon with its retry in place of the form
       (not an OFF form the server may not hold).
       The 범위 | AI Team and 안전 한도 rows sit two to a line only while both fit whole in the block (their own
       width, not the window's): on a narrow Inspector each row has the line to itself, so no control runs
       under the next one. -->
  <div class="inspector-requirement-settings" data-test="inspector-requirement-settings" :data-delegation="shown.delegation" :data-connected="shown.connected ? 'true' : 'false'">
    <InspectorReadFailure v-if="readFailure" :reason="readFailure" @retry="reload" />
    <template v-else>
      <InspectorLockNote v-if="shown.locked" :text="t(k('delegation.locked'))" />
      <InspectorBlock :title="t(k('delegation.title'))" :sub="t(k('delegation.sub'))" :locked="shown.locked" data-test="requirement-delegation">
        <template #head>
          <span v-if="!shown.connected" data-test="requirement-settings-unconnected">
            <InspectorTbdNote kind="tag" :label="t(k('delegation.unconnected_tag'))" :title="t(k('delegation.unconnected'))" />
          </span>
          <InspectorSegment
            :model-value="shown.delegation"
            :options="[
              { value: 'off', label: t(k('delegation.off')), title: reasonText },
              { value: 'on', label: t(k('delegation.on')), title: reasonText },
            ]"
            :disabled="off"
            :aria-label="t(k('delegation.title'))"
            data-test="requirement-delegation-switch"
            @update:model-value="(value: string) => onChange('delegation', value)"
          />
          <InspectorFailureIcon v-if="failureOf('delegation')" :name="t(k('failed'))" :reason="failureOf('delegation')!" data-field="delegation" />
        </template>
        <template v-if="shown.delegation === 'on'">
          <div class="inspector-requirement-settings-grid">
            <InspectorControlRow :label="t(k('delegation.scope'))" :hint="reasonText">
              <InspectorSegment
                :model-value="shown.scope"
                :options="[
                  { value: 'end', label: t(k('delegation.scope_end')), title: reasonText },
                  { value: 'confirm', label: t(k('delegation.scope_confirm')), title: reasonText },
                ]"
                :disabled="off"
                :aria-label="t(k('delegation.scope'))"
                data-test="requirement-delegation-scope"
                @update:model-value="(value: string) => onChange('scope', value)"
              />
              <InspectorFailureIcon v-if="failureOf('scope')" :name="t(k('failed'))" :reason="failureOf('scope')!" data-field="scope" />
            </InspectorControlRow>
            <InspectorControlRow :label="t(k('delegation.team'))" :hint="t(k('delegation.supervisor_note'))">
              <Select
                :model-value="shown.team ?? ''"
                :options="teamOptions"
                :disabled="off"
                data-test="requirement-delegation-team"
                @update:model-value="(value: string) => onChange('team', value === '' ? null : value)"
              />
              <InspectorTbdNote kind="tag" :label="t(k('delegation.tbd'))" :title="t(k('delegation.tbd_title'))" />
              <InspectorFailureIcon v-if="failureOf('team')" :name="t(k('failed'))" :reason="failureOf('team')!" data-field="team" />
            </InspectorControlRow>
          </div>
        </template>
        <p v-else class="inspector-requirement-settings-note" data-test="requirement-delegation-off" :title="t(k('delegation.off_note'))">{{ t(k('delegation.off_short')) }}</p>
      </InspectorBlock>

      <InspectorBlock v-if="shown.delegation === 'on'" :title="t(k('limits.title'))" :sub="t(k('limits.sub'))" :locked="shown.locked" data-test="requirement-limits">
        <template #head>
          <InspectorStatusChip v-if="hot" :label="t(k('limits.reached'), { label: t(k(`limits.${hot.key}`)), value: hot.value ?? '' })" tone="danger" />
        </template>
        <div class="inspector-requirement-settings-grid">
          <InspectorControlRow v-for="limit in shown.limits" :key="limit.key" :label="t(k(`limits.${limit.key}`))" :hint="reasonText" :data-hot="limit.hot ? 'true' : 'false'">
            <Input
              class="inspector-requirement-settings-limit"
              :model-value="limit.value === null ? '' : String(limit.value)"
              placeholder="—"
              :disabled="off"
              inputmode="numeric"
              :data-test="`requirement-limit-${limit.key}`"
              @change="(event: Event) => onLimit(limit.key, event)"
            />
            <span v-if="limit.isDefault" class="inspector-requirement-settings-muted" :data-test="`requirement-limit-${limit.key}-default`">{{ t(k('limits.default')) }}</span>
            <InspectorFailureIcon v-if="failureOf(FIELDS[limit.key])" :name="t(k('failed'))" :reason="failureOf(FIELDS[limit.key])!" :data-field="FIELDS[limit.key]" />
          </InspectorControlRow>
        </div>
      </InspectorBlock>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Input from '../../../../../components/ui/Input.vue'
import Select from '../../../../../components/ui/Select.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import {
  REQUIREMENT_LIMIT_FIELDS,
  requirementLimitValue,
  requirementSettingsErrorKey,
  requirementSettingsView,
  type RequirementLimitKey,
  type RequirementSettingsView,
} from './requirementSettingsModel'
import { useRequirementSettings } from './useRequirementSettings'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the card's settings. */
    view?: RequirementSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.requirementSettings.${key}`

const FIELDS = REQUIREMENT_LIMIT_FIELDS

const card = useRequirementSettings(() => (props.view ? null : props.target))

const shown = computed<RequirementSettingsView>(
  () =>
    props.view ??
    requirementSettingsView(
      {
        card: props.target.nodeType !== null,
        node: card.node.value,
        pending: card.pending.value,
        teams: card.teams.value,
        readOnly: props.target.readOnly,
        placing: card.placing.value,
        disposed: card.disposed.value,
        failure: card.failure.value,
      },
      { withSupervisor: (name) => t(k('delegation.team_supervisor'), { name }), without: t(k('delegation.team_no_supervisor')) },
    ),
)

/** Why the card's settings could not be read (the icon's tooltip), while nothing was read; else null. */
const readFailure = computed(() => {
  const kind = props.view ? null : card.errorKind.value
  return kind && card.node.value === null ? t(k(requirementSettingsErrorKey(kind))) : null
})

/** Nothing can change now (no card, read only, running …): the controls are off, the reason their tooltip. */
const off = computed(() => props.view ? !props.view.connected || props.view.locked : shown.value.reason !== null)

const reasonText = computed(() => (shown.value.reason && off.value ? t(k(shown.value.reason)) : null))

/** No team first (`—`, the value taken back), then the teams. */
const teamOptions = computed(() => [{ value: '', label: '—' }, ...shown.value.teamOptions])

const hot = computed(() => shown.value.limits.find((limit) => limit.hot) ?? null)

function failureOf(field: string): string | null {
  const key = shown.value.failures[field]
  return key ? t(k(key)) : null
}

function onChange(field: string, value: unknown) {
  if (off.value) return
  void card.change(field, value)
}

function reload() {
  void card.reload()
}

function onLimit(key: RequirementLimitKey, event: Event) {
  const text = (event.target as HTMLInputElement | null)?.value ?? ''
  onChange(REQUIREMENT_LIMIT_FIELDS[key], requirementLimitValue(text))
}
</script>

<style scoped>
.inspector-requirement-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

/* v7 two columns while two rows fit side by side (never more: each row is at least half the line). A row is
   as wide as its label and controls (up to the block's width — the Inspector's, not the window's), so a row
   that does not fit beside the other takes the next line instead of running under it. */
.inspector-requirement-settings-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 9px 18px;
}

.inspector-requirement-settings-grid > :deep(.inspector-control-row) {
  flex: 1 1 auto;
  width: max-content;
  min-width: calc(50% - 9px);
  max-width: 100%;
  margin: 0;
}

.inspector-requirement-settings-note {
  margin: 6px 0 0;
  color: #7f96af;
  font-size: 11.5px;
  line-height: 1.5;
}

.inspector-requirement-settings-limit {
  width: 64px;
}

.inspector-requirement-settings :deep(.inspector-control-row[data-hot='true'] .inspector-control-label) {
  color: #ff9eae;
  font-weight: 700;
}

.inspector-requirement-settings-muted {
  color: #7f96af;
  font-size: 11px;
}

</style>
