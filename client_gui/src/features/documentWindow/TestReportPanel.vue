<template>
  <div class="tsr" data-test="test-report-panel">
    <div v-if="flags.length" class="tsr-flags" data-test="test-report-summary">
      <TestMarkBadge v-for="flag in flags" :key="flag.mark.key" :mark="flag.mark" :text="flag.text" :tooltip="t(k(flag.mark.key), flag.params)" />
    </div>
    <TestScenarioList :rows="rows" :counts="doc.view.scenario?.counts ?? null" :overall-comment="doc.view.scenario?.overallComment ?? null" :pending-case-ids="doc.pendingCaseIds" />
  </div>
</template>

<script setup lang="ts">
// F81: the report is not a text of its own. It is the scenario list with the recorded results tallied, and the
// AI's few lines under it — the same component as the scenario (TestScenarioList), in the inspector and here.
// Nothing is read from the report document's text to build it.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestSpecDocument } from '@/features/document'
import TestMarkBadge from './TestMarkBadge.vue'
import TestScenarioList from './TestScenarioList.vue'
import { testDocumentFlags, testScenarioRows } from './specialDocumentModel'

const props = defineProps<{ doc: TestSpecDocument }>()
const { t } = useI18n()
const k = (key: string) => `gui.document_window.${key}`
const flags = computed(() => testDocumentFlags(props.doc))
const rows = computed(() => testScenarioRows(props.doc))
</script>

<style scoped>
.tsr { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; }
.tsr-flags { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
</style>
