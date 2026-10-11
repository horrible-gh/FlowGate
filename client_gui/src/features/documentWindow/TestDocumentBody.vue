<template>
  <div class="special-body" data-test="test-document-body" :data-state="state" :data-kind="doc?.view.kind ?? ''">
    <template v-if="state === 'structured' && doc">
      <TestSpecPanel v-if="doc.view.kind === 'TS'" :doc="doc" />
      <template v-else>
        <TestReportPanel :doc="doc" :rows="reportRows" />
        <!-- A report whose saved text is not in the report's shape: the text as written, under the server's verdict. -->
        <div v-if="!reportRows" class="test-report-source" data-test="test-report-source">
          <slot name="fallback" />
        </div>
      </template>
    </template>
    <!-- Until the answer is known the Markdown body is mounted but hidden (as in v0.1): a legacy document
         then appears without a flash or a remount; a specification replaces it before it is ever shown. -->
    <div v-else v-show="state === 'fallback'" class="test-doc-source" data-test="test-doc-markdown">
      <slot name="fallback" />
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#4 — the test scenario / test report view in the document window: the v0.1
// TestDocumentBody (client/src/main/components/documents/TestDocumentBody.vue). A specification (contract 2)
// TS shows its scenario (TestSpecPanel), a TSR its report (TestReportPanel), both read only; a legacy TS / TSR,
// a document not written yet (a scenario or report before its run) and a failed read keep the Markdown body
// (the fallback slot, the MD Viewer). The structured ↔ Markdown tab of v0.1 is not kept: the window's ✎ is the
// way to the document text.
// Read through the v0.1 endpoint (`GET /documents/{doc_id}/test-document`, testSpecDocumentRead). The window
// reads it again on its document's events and on its period (DocumentWindowBody.refresh → reload); a read
// that fails while a view is shown keeps it.
// A report is also read as saved (documentContentRead): the server answers its run record and never reads the
// report file, which ✎ edits — so the report's Case rows come from the saved text (testReportRows), and a text
// with any line or cell not of the server's report shape (a note, a quote, a heading, a verdict of one's own…)
// is shown as written; the verdict, the gate, the counts and each Case's verdict stay the record's (0053 Q #419).
// The text is read against that record too: who checked a Case and when must be what the record says, and a
// cell or evidence item it holds as written is its exact value.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { documentContentRead, testSpecDocumentRead, type TestSpecDocument, type TestSpecResultRow } from '@/features/document'
import TestReportPanel from './TestReportPanel.vue'
import TestSpecPanel from './TestSpecPanel.vue'
import { testDocumentStructured, testReportRows } from './specialDocumentModel'

const props = defineProps<{
  docId: string
  /** The document's title: the server writes a report's title as the first heading of its text. */
  title?: string | null
}>()

const doc = ref<TestSpecDocument | null>(null)
/** A report's Case rows as its saved text says them; null when that text is not in the report's shape. */
const reportRows = ref<readonly TestSpecResultRow[] | null>(null)
const state = ref<'loading' | 'structured' | 'fallback'>('loading')
// Request order: only the latest read may set the view.
let generation = 0

async function reload(): Promise<void> {
  const seq = ++generation
  const docId = props.docId
  try {
    const read = await testSpecDocumentRead(docId)
    const structured = testDocumentStructured(read)
    const saved = structured && read.view.kind === 'TSR' ? (await documentContentRead(docId)).content : null
    if (seq !== generation) return
    doc.value = read
    reportRows.value = saved === null ? null : testReportRows(saved, props.title ?? null, read)
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
    reportRows.value = null
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
