<template>
  <!-- flowgate.v02.0053 T#3 — the body spot of the document window: picks the view a document gets
       (documentBodyView). T#4 — the v0.1 DocumentBodyRouter's special branches moved here: a work plan
       gets its plan table (WorkPlanBody, from the v0.1 WorkPlanEditor), a test scenario / test report its
       test view (TestDocumentBody, from the v0.1 one); every other document is the MD Viewer. A special
       view hands a document it cannot show its own way (a legacy test document, a failed read) to the MD
       Viewer passed in as its fallback, the way the v0.1 TestDocumentBody kept the Markdown body. -->
  <div class="document-window-body" data-test="document-window-body" :data-view="view">
    <WorkPlanBody v-if="view === 'work_plan'" ref="special" :doc-id="docId">
      <template #fallback><MdViewer ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" /></template>
    </WorkPlanBody>
    <TestDocumentBody v-else-if="view === 'test'" ref="special" :doc-id="docId" :title="title">
      <template #fallback><MdViewer ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" /></template>
    </TestDocumentBody>
    <MdViewer v-else ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import MdViewer from './MdViewer.vue'
import TestDocumentBody from './TestDocumentBody.vue'
import WorkPlanBody from './WorkPlanBody.vue'
import { documentBodyView, type DocumentBodyStatus } from './documentWindowModel'
import type { DocumentContentChange } from './useDocumentWindowEvents'

const props = defineProps<{
  docId: string
  typeCode: string | null
  /** The shown document's title (a test report's text starts with it). */
  title?: string | null
  /** The document window's titlebar hosts the Markdown copy action. */
  copyTarget?: HTMLElement | null
  canReenter?: boolean
}>()

const view = computed(() => documentBodyView(props.typeCode))

const viewer = ref<InstanceType<typeof MdViewer> | null>(null)
const special = ref<{ reload: () => Promise<void> } | null>(null)

/** The stored body may have changed (a server event, a re-open, the window's period): the view reads it again. */
function refresh(change: DocumentContentChange): void {
  void special.value?.reload()
  viewer.value?.requestContentRefresh({ doc_id: change.docId, revision_no: change.revisionNo })
}

/** The MD Viewer's body state for the window footer (flowgate.v02.0070 T#3); null while a special view shows its own. */
const status = computed<DocumentBodyStatus | null>(() => viewer.value?.status ?? null)

/** [복구] of the window footer: the MD Viewer recovers with its diagnosed method (only on the person's click). */
async function recover(): Promise<void> {
  await viewer.value?.recover()
}

defineExpose({ refresh, status, recover })
</script>

<style scoped>
.document-window-body {
  flex: 1;
  min-height: 0;
  background: var(--fg2-bg);
}
</style>
