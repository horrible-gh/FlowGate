<template>
  <!-- flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings): the 용도 line with
       the v7 토론 중 tag, and the Git 반영 card — 반영 방식 (병합 / 커밋만 / 보류), 병합 대상 · 원격, 원격 반영,
       브랜치 보관 and the one-line summary. Built from the common parts. A thin container:
       checkSettingsModel gives the view; nothing is stored on the Node yet, so every control is disabled
       with the reason (the Git choice is asked by the final approval in the header). `view` may be
       handed in for a fixture preview. -->
  <div class="inspector-check-settings" data-test="inspector-check-settings" :data-connected="shown.connected ? 'true' : 'false'">
    <div class="inspector-check-settings-usage" data-test="check-settings-usage">
      <span class="inspector-check-settings-usage-label">{{ t(k('usage.label')) }}</span>
      <span class="inspector-check-settings-usage-value">
        <span class="inspector-check-settings-usage-pill" data-test="check-settings-usage-value">{{ shown.usage ? t(k(`usage.${shown.usage}`)) : t(k('usage.unknown')) }}</span>
        <InspectorTbdNote kind="tag" :label="t(k('usage.tbd'))" :title="t(k('usage.tbd_title'))" />
      </span>
    </div>

    <InspectorBlock :title="t(k('git.title'))" data-test="check-settings-git">
      <InspectorControlRow :label="t(k('git.scope'))">
        <InspectorSegment
          :model-value="shown.scope"
          :options="CHECK_SCOPES.map((scope) => ({ value: scope, label: t(k(`git.${scope}`)), title: shown.connected ? null : t(k('git.unconnected')) }))"
          :disabled="!shown.connected"
          :aria-label="t(k('git.scope'))"
          data-test="check-settings-scope"
        />
      </InspectorControlRow>
      <InspectorControlRow :label="t(k('git.target'))">
        <Select class="inspector-check-settings-target" :model-value="shown.target ?? ''" :options="shown.target ? [{ value: shown.target, label: shown.target }] : []" :placeholder="shown.target ? undefined : '—'" :disabled="!shown.connected" data-test="check-settings-target" />
        <label class="inspector-check-settings-toggle">
          <span>{{ t(k('git.push')) }}</span>
          <Toggle :model-value="shown.push ?? false" :disabled="!shown.connected" :aria-label="t(k('git.push'))" data-test="check-settings-push" />
        </label>
        <label class="inspector-check-settings-toggle">
          <span>{{ t(k('git.keep_branch')) }}</span>
          <Toggle :model-value="shown.keepBranch ?? false" :disabled="!shown.connected" :aria-label="t(k('git.keep_branch'))" data-test="check-settings-keep-branch" />
        </label>
      </InspectorControlRow>
      <p class="inspector-check-settings-summary" data-test="check-settings-summary">{{ summary }}</p>
    </InspectorBlock>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Select from '../../../../../components/ui/Select.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import { CHECK_SCOPES, checkSettingsView, type CheckSettingsView } from './checkSettingsModel'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the target. */
    view?: CheckSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.checkSettings.${key}`

const shown = computed(() => props.view ?? checkSettingsView(props.target))

/** v7 scopeSummary: one line for the chosen method; the reason nothing is chosen otherwise. */
const summary = computed(() => (shown.value.scope ? t(k(`git.summary_${shown.value.scope}`)) : t(k('git.unconnected'))))
</script>

<style scoped>
.inspector-check-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

/* v7 `.usage-row`: the dashed "under discussion" line. */
.inspector-check-settings-usage {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 6px 11px;
  border: 1px dashed #6b5a2a;
  border-radius: 8px;
  background: #141208;
}

.inspector-check-settings-usage-label {
  color: #d7c9a0;
  font-weight: 700;
}

.inspector-check-settings-usage-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #d9e5f0;
}

.inspector-check-settings-usage-pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border: 1px solid #31577f;
  border-radius: 6px;
  background: #102b51;
  color: #cfe6ff;
  font-weight: 700;
}

.inspector-check-settings-target {
  width: 150px;
}

.inspector-check-settings-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #94a8bd;
  font-size: 11.5px;
  white-space: nowrap;
}

.inspector-check-settings-summary {
  margin: 8px 0 0;
  color: #7f96af;
  font-size: 11.5px;
}
</style>
