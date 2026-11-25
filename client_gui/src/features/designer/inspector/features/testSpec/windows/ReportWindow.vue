<template>
  <!-- F81: the report is the scenario list again — the recorded results tallied, and the AI's few lines under it.
       The same component the scenario window and the document window draw (TestScenarioList). -->
  <ModelessShell kind-color="#f0a23a" v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 130)" :initial-y="geometry('y', 100)" :initial-width="geometry('width', 860)" :initial-height="geometry('height', 600)"
    :close-on-escape="false" @update:minimized="state.minimized = $event" @close="close">
    <template #header>{{ t(k('title')) }}</template>
    <div class="report" data-test="report-window">
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <TestScenarioList :rows="card.scenarioRows.value" :overall-comment="card.view.value?.scenario?.overallComment ?? null" :pending-case-ids="pending" interactive
        @open="card.openWindow('case', $event)" @remarks="card.openWindow('remark', $event)" />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { TestScenarioList } from '@/features/documentWindow'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { useScenarioWindow } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.report.${key}`
const { card, shell, state, close, geometry } = useScenarioWindow(() => props.target, 'report')
/** Cases the live run has selected and not reported yet. */
const pending = computed(() => {
  const run = card.value?.view.value?.activeRun
  return run && card.value?.test.runState.value.active ? [...(run.selectedCaseIds ?? [])] : []
})
</script>
<style scoped>
.report { color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
</style>
