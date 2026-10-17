<template>
  <!-- flowgate.v02.0053 T#3 — the body spot of the document window: picks the view a document gets
       (documentBodyView). T#4 — the v0.1 DocumentBodyRouter's special branches moved here: a work plan
       gets its plan table (WorkPlanBody, from the v0.1 WorkPlanEditor), a test scenario / test report its
       test view (TestDocumentBody, from the v0.1 one); every other document is the MD Viewer. A special
       view hands a document it cannot show its own way (a legacy test document, a failed read) to the MD
       Viewer passed in as its fallback, the way the v0.1 TestDocumentBody kept the Markdown body. -->
  <div class="document-window-body" data-test="document-window-body" :data-view="view">
    <WorkPlanBody v-if="view === 'work_plan'" ref="special" :doc-id="docId">
      <template #fallback><MdViewer ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" @reenter="emit('reenter')" /></template>
    </WorkPlanBody>
    <TestDocumentBody v-else-if="view === 'test'" ref="special" :doc-id="docId" :title="title">
      <template #fallback><MdViewer ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" @reenter="emit('reenter')" /></template>
    </TestDocumentBody>
    <MdViewer v-else ref="viewer" :doc-id="docId" :copy-target="copyTarget" :can-reenter="canReenter" @reenter="emit('reenter')" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import MdViewer from './MdViewer.vue'
import TestDocumentBody from './TestDocumentBody.vue'
import WorkPlanBody from './WorkPlanBody.vue'
import { documentBodyView } from './documentWindowModel'
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

const emit = defineEmits<{ reenter: [] }>()

const view = computed(() => documentBodyView(props.typeCode))

const viewer = ref<InstanceType<typeof MdViewer> | null>(null)
const special = ref<{ reload: () => Promise<void> } | null>(null)

/** The stored body may have changed (a server event, a re-open, the window's period): the view reads it again. */
function refresh(change: DocumentContentChange): void {
  void special.value?.reload()
  viewer.value?.requestContentRefresh({ doc_id: change.docId, revision_no: change.revisionNo })
}

defineExpose({ refresh })
</script>

<style scoped>
.document-window-body {
  flex: 1;
  min-height: 0;
  background: var(--fg2-bg);
}
</style>
