<template>
  <div class="special-body" data-test="test-document-body" :data-state="state" :data-kind="doc?.view.kind ?? ''">
    <template v-if="state === 'structured' && doc">
      <TestSpecPanel v-if="doc.view.kind === 'TS'" :doc="doc" />
      <!-- F81: the report is the scenario list with its recorded results; its saved text is not read for it. -->
      <TestReportPanel v-else :doc="doc" />
    </template>
    <!-- Until the answer is known the Markdown body is mounted but hidden (as in v0.1): a legacy document
         then appears without a flash or a remount; a specification replaces it before it is ever shown. -->
    <div v-else v-show="state === 'fallback'" class="test-doc-source" data-test="test-doc-markdown">
      <slot name="fallback" />
    </div>
  </div>
</template>

<script setup lang="ts">
// the test scenario / test report view in the document window: the v0.1
// TestDocumentBody (client/src/main/components/documents/TestDocumentBody.vue). A specification (contract 2)
// TS shows its scenario (TestSpecPanel), a TSR its report (TestReportPanel), both read only; a legacy TS / TSR,
// a document not written yet (a scenario or report before its run) and a failed read keep the Markdown body
// (the fallback slot, the MD Viewer). The structured ↔ Markdown tab of v0.1 is not kept: the window's ✎ is the
// way to the document text.
// Read through the v0.1 endpoint (`GET /documents/{doc_id}/test-document`, testSpecDocumentRead). The window
// reads it again on its document's events and on its period (DocumentWindowBody.refresh → reload); a read
// that fails while a view is shown keeps it.
// F81: a report is the scenario list with the recorded results and the AI's comment
// (the answer's `scenario_report`). The report's saved text is not read and no table is built from it.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { testSpecDocumentRead, type TestSpecDocument } from '@/features/document'
import TestReportPanel from './TestReportPanel.vue'
import TestSpecPanel from './TestSpecPanel.vue'
import { testDocumentStructured } from './specialDocumentModel'

const props = defineProps<{
  docId: string
  /** The document's title: the server writes a report's title as the first heading of its text. */
  title?: string | null
}>()

const doc = ref<TestSpecDocument | null>(null)
const state = ref<'loading' | 'structured' | 'fallback'>('loading')
// Request order: only the latest read may set the view.
let generation = 0

async function reload(): Promise<void> {
  const seq = ++generation
  const docId = props.docId
  try {
    const read = await testSpecDocumentRead(docId)
    const structured = testDocumentStructured(read)
    if (seq !== generation) return
    doc.value = read
    state.value = structured ? 'structured' : 'fallback'
  } catch {
    if (seq !== generation) return
    // Fail open to the Markdown body; a view that was already showing stays.
    if (state.value !== 'structured') state.value = 'fallback'
  }
}

watch(
  () => props.docId,
  () => {
    doc.value = null
    state.value = 'loading'
    void reload()
  },
)

onMounted(() => {
  void reload()
})

onBeforeUnmount(() => {
  generation += 1
})

defineExpose({ reload })
</script>

<style scoped>
.special-body {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  box-sizing: border-box;
}

.test-doc-source {
  height: 100%;
  min-height: 0;
}

.test-report-source {
  border-top: 1px solid var(--fg2-line);
}
</style>
