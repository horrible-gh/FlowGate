<template>
  <div class="ts-spec" data-test="test-spec-panel">
    <div v-if="flags.length" class="ts-flags" data-test="test-spec-summary">
      <TestMarkBadge v-for="flag in flags" :key="flag.mark.key" :mark="flag.mark" :text="flag.text" :tooltip="t(k(flag.mark.key), flag.params)" />
    </div>
    <TestScenarioList :rows="rows" :counts="doc.view.scenario?.counts ?? null" :overall-comment="doc.view.scenario?.overallComment ?? null" :pending-case-ids="doc.pendingCaseIds" />
  </div>
</template>

<script setup lang="ts">
// F81: the scenario in the document window is the scenario list — the same component the inspector's scenario
// window draws (TestScenarioList), read only here. A stale / unchecked result, a live run and format errors
// stay marks with their words in the tooltips.
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
.ts-spec { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; }
.ts-flags { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
</style>
