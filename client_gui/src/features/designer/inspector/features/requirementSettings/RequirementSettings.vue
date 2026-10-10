<template>
  <!-- flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's settings tab (0035 v7 reqSettings): the AI 위임
       block with its 끄기 / 켜기 switch; while ON, 범위 · AI Team, the Supervisor note and the 안전 한도
       block. Built from the common parts. A thin container: requirementSettingsModel gives the view
       (always OFF and not changeable until a delegation API exists — the controls are disabled with the
       reason, 0051-R §2.3). `view` may be handed in for a fixture preview of the ON shape.
       flowgate.v02.0052 T#2 — no prose block: OFF is one short line (the v7 explanation is its tooltip)
       and "not connected" is a tag beside the disabled switch (the reason is its tooltip). -->
  <div class="inspector-requirement-settings" data-test="inspector-requirement-settings" :data-delegation="shown.delegation" :data-connected="shown.connected ? 'true' : 'false'">
    <InspectorLockNote v-if="shown.locked" :text="t(k('delegation.locked'))" />
    <InspectorBlock :title="t(k('delegation.title'))" :sub="t(k('delegation.sub'))" :locked="shown.locked" data-test="requirement-delegation">
      <template #head>
        <span v-if="!shown.connected" data-test="requirement-settings-unconnected">
          <InspectorTbdNote kind="tag" :label="t(k('delegation.unconnected_tag'))" :title="t(k('delegation.unconnected'))" />
        </span>
        <InspectorSegment
          :model-value="shown.delegation"
          :options="[
            { value: 'off', label: t(k('delegation.off')), title: shown.connected ? null : t(k('delegation.unconnected')) },
            { value: 'on', label: t(k('delegation.on')), title: shown.connected ? null : t(k('delegation.unconnected')) },
          ]"
          :disabled="!shown.connected || shown.locked"
          :aria-label="t(k('delegation.title'))"
          data-test="requirement-delegation-switch"
        />
      </template>
      <template v-if="shown.delegation === 'on'">
        <div class="inspector-requirement-settings-grid">
          <InspectorControlRow :label="t(k('delegation.scope'))">
            <InspectorSegment
              :model-value="shown.scope"
              :options="[
                { value: 'end', label: t(k('delegation.scope_end')) },
                { value: 'confirm', label: t(k('delegation.scope_confirm')) },
              ]"
              :disabled="!shown.connected || shown.locked"
              :aria-label="t(k('delegation.scope'))"
              data-test="requirement-delegation-scope"
            />
          </InspectorControlRow>
          <InspectorControlRow :label="t(k('delegation.team'))">
            <Select :model-value="shown.team ?? ''" :options="[...shown.teamOptions]" :placeholder="shown.team ? undefined : '—'" :disabled="!shown.connected || shown.locked" data-test="requirement-delegation-team" />
          </InspectorControlRow>
        </div>
        <p class="inspector-requirement-settings-note">
          {{ t(k('delegation.supervisor_note')) }}
          <InspectorTbdNote kind="tag" :label="t(k('delegation.tbd'))" />
        </p>
      </template>
      <p v-else class="inspector-requirement-settings-note" data-test="requirement-delegation-off" :title="t(k('delegation.off_note'))">{{ t(k('delegation.off_short')) }}</p>
    </InspectorBlock>

    <InspectorBlock v-if="shown.delegation === 'on'" :title="t(k('limits.title'))" :sub="t(k('limits.sub'))" :locked="shown.locked" data-test="requirement-limits">
      <template #head>
        <InspectorStatusChip v-if="hot" :label="t(k('limits.reached'), { label: t(k(`limits.${hot.key}`)), value: hot.value ?? '' })" tone="danger" />
      </template>
      <div class="inspector-requirement-settings-grid">
        <InspectorControlRow v-for="limit in shown.limits" :key="limit.key" :label="t(k(`limits.${limit.key}`))" :data-hot="limit.hot ? 'true' : 'false'">
          <Input class="inspector-requirement-settings-limit" :model-value="limit.value === null ? '' : String(limit.value)" placeholder="—" :disabled="!shown.connected || shown.locked" :data-test="`requirement-limit-${limit.key}`" />
          <span class="inspector-requirement-settings-muted">{{ t(k('limits.default')) }}</span>
        </InspectorControlRow>
      </div>
    </InspectorBlock>

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
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import { requirementSettingsView, type RequirementSettingsView } from './requirementSettingsModel'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the target. */
    view?: RequirementSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.requirementSettings.${key}`

const shown = computed(() => props.view ?? requirementSettingsView(props.target))

const hot = computed(() => shown.value.limits.find((limit) => limit.hot) ?? null)
</script>

<style scoped>
.inspector-requirement-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-requirement-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 18px;
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

@media (max-width: 560px) {
  .inspector-requirement-settings-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
