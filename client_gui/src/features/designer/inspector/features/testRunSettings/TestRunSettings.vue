<template>
  <!-- flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings), built from the common
       parts. flowgate.v02.0053 T#7 — the 실행 block only, after the settings feature's 지시서 ·
       시나리오 작성 (v7 order): 실행 방식 케이스별 — 시나리오에서 정함 and the Case counts by mode, with the v7
       mark that the manual · external result flow is not drawn. No code (TSC / TSR) on screen; the
       explanations are tooltips. `view` may be handed in for a fixture preview. -->
  <InspectorBlock v-if="target.cardKind !== 'test'" :title="t(k('run.title'))" class="inspector-test-run-settings" data-test="inspector-test-run-settings">
    <template #head>
      <InspectorTbdNote kind="tag" :label="t(k('run.tbd'))" :title="t(k('run.tbd_title'))" />
    </template>
    <InspectorControlRow :label="t(k('run.mode'))" :hint="t(k('run.mode_hint'))">
      <span class="inspector-test-run-settings-modes" data-test="test-run-modes">
        <b>{{ t(k('run.per_case')) }}</b> — {{ t(k('run.decided_in_cases')) }} ·
        <span v-for="mode in MODES" :key="mode" class="inspector-test-run-settings-mode" :class="`inspector-test-run-settings-mode--${mode}`" :data-test="`test-run-mode-${mode}`">{{
          t(k(`run.${mode}`), { n: shown.modes[mode] ?? t(k('run.unknown')) })
        }}</span>
      </span>
    </InspectorControlRow>
  </InspectorBlock>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import type { TestRunSettingsView } from './testRunSettingsModel'
import { useTestRunSettings } from './useTestRunSettings'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the card's TS. */
    view?: TestRunSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.testRunSettings.${key}`

const MODES = ['automated', 'manual', 'external'] as const

const read = useTestRunSettings(() => (props.view || props.target.cardKind === 'test' ? null : props.target))

const shown = computed(() => props.view ?? read.view.value)
</script>

<style scoped>
/* v7 `.case-modes`. */
.inspector-test-run-settings-modes {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: #9fb3c9;
  font-size: 11.5px;
}

.inspector-test-run-settings-modes b {
  color: #e3eef9;
}

.inspector-test-run-settings-mode {
  padding: 0 7px;
  border: 1px solid #2c4560;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
}

.inspector-test-run-settings-mode--automated {
  border-color: #2d5f9b;
  color: #8cc0ff;
}

.inspector-test-run-settings-mode--manual {
  border-color: #7b5a17;
  color: #ffc75d;
}

.inspector-test-run-settings-mode--external {
  border-color: #315271;
  color: #9fc4e8;
}
</style>
